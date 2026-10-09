"""A-C stages exercised with actual adapters and local source chunks, never a real key."""
from copy import deepcopy
from dataclasses import replace
import json

import httpx
import pytest

from just_read.pipeline import Pipeline, PipelineError
from just_read.providers import SearchProvider
from test_pipeline import Workflow, config, noop


QUOTE = "设备由底座和传感器组成。2026年计数：甲 20 次；乙 30 次。"
MATERIAL = {"id": "document-uuid", "title": "上传设备资料", "kind": "upload", "url": "", "chunks": [
    {"id": "document-uuid:c1", "text": QUOTE, "page": 2, "paragraph": 1, "start": 100, "end": 100+len(QUOTE)}]}


class MaterialWorkflow:
    def __init__(self, fail_second=False):
        self.calls = []
        self.fail_second = fail_second
        self.fixture = Workflow(mutate_analysis=self.analysis)

    @staticmethod
    def analysis(value):
        value["findings"] = [{"text": "设备包含底座与传感器。", "supports": [{"source_id": MATERIAL["id"], "quote": QUOTE}]}]
        value["data"] = [{"label": label, "value_text": number, "unit": "次", "metric": "计数", "period": "2026年",
                           "source_id": MATERIAL["id"], "quote": QUOTE} for label, number in [("甲", "20"), ("乙", "30")]]
        value["calculations"] = [{"operation": "sum", "labels": ["甲", "乙"]}]
        value["relations"] = [{"source": "底座", "target": "传感器", "type": "supports", "label": "连接",
                               "supports": [{"source_id": MATERIAL["id"], "quote": QUOTE}]}]

    def __call__(self, request):
        body = json.loads(request.content)
        name = body["text"]["format"]["name"]
        self.calls.append(name)
        if name == "Analysis":
            user = json.loads(body["input"][1]["content"])
            assert user["sources"][0]["id"] == MATERIAL["id"]
            assert QUOTE in user["sources"][0]["raw_content"]
            if self.fail_second and user["subquestion"].startswith("第1"):
                return httpx.Response(401, json={"error": "private upstream details"})
        if name == "SceneSpec":
            value = {"title": "设备概念结构", "parts": [{"id": "base", "label": "底座", "geometry": "box",
                "position": [0, 0, 0], "size": [2, 1, 1], "evidence_refs": ["E1"]}]}
            result = {"status": "completed", "output": [{"type": "message", "content": [
                {"type": "output_text", "text": json.dumps(value)}]}]}
        else:
            result = self.fixture(request).json()
        result["usage"] = {"input_tokens": 20, "output_tokens": 30, "total_tokens": 50,
                           "private_field": "must not persist"}
        return httpx.Response(200, json=result)


def context():
    values, usage = {}, []
    async def load(name): return deepcopy(values.get(name))
    async def save(name, value): values[name] = deepcopy(value)
    async def record(value): usage.append(value)
    return {"spec": {"question": "分析设备并求总计数", "depth": "brief", "reader": "工程师", "scope": "设备结构",
                     "source_ids": [MATERIAL["id"]], "enable_3d": True},
            "source_materials": [deepcopy(MATERIAL)], "load_checkpoint": load, "save_checkpoint": save,
            "record_usage": record}, values, usage


@pytest.mark.asyncio
async def test_upload_calculation_ir_scene_and_checkpoint_reuse_are_one_real_pipeline():
    ctx, saved, usage = context()
    upstream = MaterialWorkflow()
    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as client:
        pipeline = Pipeline(replace(config(), search_api_key=""), client)
        report = await pipeline.run("分析设备并求总计数", "brief", noop, lambda: False, context=ctx)
        workflow = report["workflow"]
        assert "text" not in workflow["sources"][0]["chunks"][0]
        assert workflow["sources"][0]["chunks"][0]["quotes"][0]["quote"] == QUOTE
        assert report["_evidence"]["sources"][0]["chunks"] == MATERIAL["chunks"]
        assert all(item["verification"]["quote_match"] for item in workflow["evidence"])
        assert workflow["evidence"][0]["locator"]["page"] == 2
        assert workflow["calculations"][0]["result"] == 50
        assert workflow["calculations"][0]["source_refs"] == [MATERIAL["id"]]
        assert any(claim["calculation_refs"] for claim in workflow["research_ir"]["claims"])
        assert all(row["claims"] >= 1 for row in workflow["research_ir"]["coverage"])
        assert {visual["kind"] for visual in workflow["visuals"]} >= {"flow", "relation", "bar"}
        assert workflow["scene"]["parts"][0]["dimensions_known"] is False
        assert workflow["reading_plan"]["audience"] == "工程师"
        assert {"A.plan", "A.sources", "B.analysis.Q1", "B.analysis.Q2", "B.review", "C.layout", "C.scene"} <= saved.keys()
        first_calls = len(upstream.calls)
        repeated = await pipeline.run("分析设备并求总计数", "brief", noop, lambda: False, context=ctx)
        assert len(upstream.calls) == first_calls
        assert repeated["workflow"] == workflow
        assert len(usage) == first_calls and all(event["total_tokens"] == 50 for event in usage)
        assert "private_field" not in json.dumps(usage)
        # A revised scope must not reuse the old task's stage contents.
        ctx["spec"]["scope"] = "修订后的研究范围"
        await pipeline.run("分析设备并求总计数", "brief", noop, lambda: False, context=ctx)
        assert len(upstream.calls) == first_calls * 2


@pytest.mark.asyncio
async def test_resume_keeps_completed_analysis_but_auth_failure_never_publishes():
    ctx, saved, _ = context()
    upstream = MaterialWorkflow(fail_second=True)
    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as client:
        pipeline = Pipeline(replace(config(), search_api_key=""), client)
        with pytest.raises(PipelineError) as caught:
            await pipeline.run("分析设备并求总计数", "brief", noop, lambda: False, context=ctx)
        assert caught.value.code == "PROVIDER_CONFIG"
        assert "B.analysis.Q1" in saved and "B.analysis.Q2" not in saved
        upstream.fail_second = False
        before = len(upstream.calls)
        report = await pipeline.run("分析设备并求总计数", "brief", noop, lambda: False, context=ctx)
    assert upstream.calls[before:] == ["Analysis", "Review", "Layout", "SceneSpec", "FinalQuality"]
    assert len(report["workflow"]["research_ir"]["question_answers"]) == 2


@pytest.mark.asyncio
async def test_public_report_only_contains_cited_excerpt_not_uploaded_unreferenced_text():
    ctx, _, _ = context()
    private_text = "未引用私有段落：不应随公开报告发布的完整上传原文。"
    material = ctx["source_materials"][0]
    material["chunks"].append({"id": "document-uuid:c2", "text": private_text, "page": 3,
                                "paragraph": 2, "start": 500, "end": 500 + len(private_text)})
    material["pages"] = [{"page": 2, "text": QUOTE}, {"page": 3, "text": private_text}]
    material["original_document"] = {"text": private_text}
    async with httpx.AsyncClient(transport=httpx.MockTransport(MaterialWorkflow())) as client:
        report = await Pipeline(replace(config(), search_api_key=""), client).run(
            "分析设备并求总计数", "brief", noop, lambda: False, context=ctx)
    private = report.pop("_evidence")
    assert private_text not in json.dumps(report, ensure_ascii=False)
    assert private["sources"][0]["chunks"] == material["chunks"]
    assert private["sources"][0]["pages"] == material["pages"]
    assert private_text in private["sources"][0]["raw_content"]
    source = report["workflow"]["sources"][0]
    assert not {"pages", "raw_content", "original_document"} & source.keys()
    assert all("text" not in chunk for chunk in source["chunks"])
    assert all(item["quote"] == QUOTE for item in source["excerpts"])
    assert source["chunks"][1] == {"id": "document-uuid:c2", "page": 3, "paragraph": 2,
                                  "start": 500, "end": 500 + len(private_text), "quotes": []}
    assert source["excerpts"][0]["locator"]["page"] == 2
    assert source["excerpts"][0]["verification"]["quote_match"] is True
    assert source["selection"]["total_chunks"] == 2
    assert source["selection"]["quoted_chunk_ids"] == ["document-uuid:c1"]
    assert "不包含未引用正文" in source["selection"]["notice"]


@pytest.mark.asyncio
async def test_search_usage_records_failure_and_retry_without_private_payload():
    events, calls = [], []
    async def record(event): events.append(event)
    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(503, json={"error": "secret provider body"})
        return httpx.Response(200, json={"results": [], "usage": {"total_tokens": 10, "authorization": "secret"}})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert await SearchProvider(config(), client, usage_recorder=record).search("test search", lambda: False) == []
    assert len(events) == 2 and events[0]["error_code"] == "UPSTREAM_TEMPORARY"
    assert [event["attempt"] for event in events] == [1, 2]
    assert events[1]["total_tokens"] == 10 and "secret" not in json.dumps(events)
