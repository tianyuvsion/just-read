from copy import deepcopy
from dataclasses import replace
import json

import httpx
import pytest

from just_read.pipeline import FinalQuality, Pipeline
from test_pipeline import Workflow, config, noop


@pytest.mark.asyncio
async def test_whole_report_review_applies_one_safe_edit_and_reuses_exact_draft_checkpoint():
    base, calls, saved = Workflow(), [], {}
    async def load(name): return deepcopy(saved.get(name))
    async def save(name, value): saved[name] = deepcopy(value)
    def handler(request):
        body = json.loads(request.content)
        if request.url.host == "api.tavily.com" or body["text"]["format"]["name"] != "FinalQuality":
            return base(request)
        draft = json.loads(body["input"][1]["content"])
        calls.append(draft)
        assert {"overview", "sources", "comparison"} <= {c["id"] for c in draft["chapters"]}
        before = next(c for c in draft["chapters"] if c["id"] == "comparison")["paragraphs"][0]
        result = {"reviewed": True, "issues": ["表达可更直接"], "suggestions": [], "edits": [
            {"chapter_id": "comparison", "paragraph_index": 0, "before": before,
             "after": before.replace("该系统支持", "系统支持"), "reason": "省去冗余指代"}]}
        return httpx.Response(200, json={"status": "completed", "output": [{"type": "message", "content": [
            {"type": "output_text", "text": json.dumps(result, ensure_ascii=False)}]}]})
    context = {"load_checkpoint": load, "save_checkpoint": save}
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        pipeline = Pipeline(config(), client)
        report = await pipeline.run("阅读能力分析", "brief", noop, lambda: False, context=context)
        repeated = await pipeline.run("阅读能力分析", "brief", noop, lambda: False, context=context)
    workflow = report["workflow"]
    assert workflow["ai_quality_review"]["status"] == "completed"
    assert workflow["ai_quality_review"]["report_version"] == 1
    assert workflow["ai_quality_review"]["fact_verified"] is False
    revision = workflow["auto_revision"]
    assert revision["max_rounds"] == revision["rounds"] == len(revision["applied"]) == 1
    assert not revision["rejected"]
    assert next(c for c in report["chapters"] if c["id"] == "comparison")["paragraphs"][0].startswith("系统支持")
    assert report["_evidence"]["findings"]["F1"]["text"].startswith("该系统支持")
    assert workflow["research_ir"]["claims"][0]["text"].startswith("该系统支持")
    assert workflow["narrative_blocks"][0]["original_text"].startswith("该系统支持")
    assert workflow["narrative_blocks"][0]["text"].startswith("系统支持")
    assert len(calls) == 1 and "D.quality_model" in saved
    assert repeated["workflow"] == workflow


@pytest.mark.parametrize("change", [
    {"after": "用户计数为 30，候选来源：[S1]。"},
    {"after": "用户计数为 20，候选来源：[S2]。"},
    {"after": "用户计数为 20，已核实来源：[S1]。"},
    {"before": "不匹配的旧段落"},
    {"paragraph_index": -1},
    {"chapter_id": "sources"},
])
def test_unsafe_expression_edits_become_suggestions_without_replacing_paragraph(change):
    original = "用户计数为 20，候选来源：[S1]。"
    chapters = [{"id": "comparison", "paragraphs": [original]}, {"id": "sources", "paragraphs": [original]}]
    workflow, warnings = {"narrative_blocks": []}, []
    edit = {"chapter_id": "comparison", "paragraph_index": 0, "before": original,
            "after": "候选用户计数为 20，来源：[S1]。", "reason": "修改表达", **change}
    Pipeline._apply_final_quality(chapters, workflow, FinalQuality(reviewed=True, edits=[edit]), warnings)
    assert chapters[0]["paragraphs"] == chapters[1]["paragraphs"] == [original]
    assert not workflow["auto_revision"]["applied"] and len(workflow["auto_revision"]["rejected"]) == 1
    assert warnings and workflow["ai_quality_review"]["fact_verified"] is False


def test_each_paragraph_has_at_most_one_revision_and_audit_preserves_both_proposals():
    before, after = "系统支持阅读。", "系统提供阅读功能。"
    chapters, workflow = [{"id": "comparison", "paragraphs": [before]}], {"narrative_blocks": []}
    review = FinalQuality(reviewed=True, edits=[
        {"chapter_id": "comparison", "paragraph_index": 0, "before": before, "after": after},
        {"chapter_id": "comparison", "paragraph_index": 0, "before": after, "after": "系统还有未证实功能。"}])
    Pipeline._apply_final_quality(chapters, workflow, review, [])
    assert chapters[0]["paragraphs"] == [after]
    assert len(workflow["auto_revision"]["applied"]) == len(workflow["auto_revision"]["rejected"]) == 1


@pytest.mark.asyncio
async def test_empty_unrecognized_quality_result_is_unavailable_not_claimed_complete():
    base, attempts = Workflow(), []
    def handler(request):
        body = json.loads(request.content)
        if request.url.host != "api.tavily.com" and body["text"]["format"]["name"] == "FinalQuality":
            attempts.append(request)
            return httpx.Response(200, json={"status": "completed", "output": [{"type": "message", "content": [
                {"type": "output_text", "text": "{}"}]}]})
        return base(request)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        report = await Pipeline(config(), client).run("测试阅读能力", "brief", noop, lambda: False)
    assert len(attempts) == 2
    assert report["workflow"]["ai_quality_review"]["status"] == "unavailable"
    assert report["workflow"]["ai_quality_review"]["error_code"] == "INVALID_MODEL_OUTPUT"
    assert not report["workflow"]["auto_revision"]["applied"]
    assert any("AI 阅读审查未完成" in w for w in report["_evidence"]["validation"]["warnings"])


@pytest.mark.asyncio
async def test_fixture_quality_review_is_explicitly_simulated():
    settings = replace(config(), llm_provider="fixture", test_mode=True, fixture_delay_seconds=0)
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: pytest.fail("unexpected network"))) as client:
        report = await Pipeline(settings, client).run("测试模拟", "brief", noop, lambda: False)
    review = report["workflow"]["ai_quality_review"]
    assert review["status"] == "simulated" and review["simulated"] is True
    assert review["report_version"] == 1 and review["fact_verified"] is False
