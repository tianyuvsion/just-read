import asyncio
from dataclasses import replace
from datetime import datetime
import json

import httpx
import pytest

from just_read.models import Report
from just_read.pipeline import Analysis, Layout, Pipeline, PipelineError, Plan, Review
from just_read.settings import Settings


QUOTE = "公开文档显示，该系统支持按章节阅读，并通过来源链接查看原始资料。"
RAW = QUOTE + "该公开文档描述了产品的阅读界面以及资料整理方式。它没有提供用户规模、准确率或商业收益数据，因此这些指标不能由此推断。"
DATA_QUOTE = "2026年下载量：甲平台 20 次；乙平台 30 次。上述结果来自同一统计口径。"


def config(**kwargs):
    return replace(Settings(), llm_model="fixed-model", llm_api_key="test-key",
                   search_api_key="search-key", **kwargs)


async def noop(*_):
    pass


class Workflow:
    def __init__(self, depth="brief", mutate_analysis=None, mutate_layout=None, reject_review=False,
                 raw=RAW, with_chart=False, mutate_plan=None, mutate_review=None):
        self.depth = depth
        self.count = 2 if depth == "brief" else 3
        self.mutate_analysis = mutate_analysis
        self.mutate_layout = mutate_layout
        self.reject_review = reject_review
        self.raw = raw
        self.with_chart = with_chart
        self.mutate_plan = mutate_plan
        self.mutate_review = mutate_review
        self.calls = []
        self.search_calls = 0
        self.analysis_calls = 0

    def __call__(self, request):
        self.calls.append(request)
        payload = json.loads(request.content)
        if request.url.host == "api.tavily.com":
            self.search_calls += 1
            return httpx.Response(200, json={"results": [{"title": "公开产品文档", "url": "https://example.org/doc",
                        "content": "Search snippet is not evidence", "raw_content": self.raw}]})
        name = payload["text"]["format"]["name"]
        if name == "Plan":
            result = {"subquestions": [{"question": f"第{i}个研究子问题", "query": f"第{i}个搜索查询"} for i in range(self.count)]}
            if self.mutate_plan:
                self.mutate_plan(result)
        elif name == "Analysis":
            self.analysis_calls += 1
            result = {"findings": [{"text": "该系统支持按章节阅读，并可通过来源链接查看原始资料。",
                                  "supports": [{"source_id": "S1", "quote": QUOTE}]}], "gaps": [], "data": []}
            if self.with_chart and self.analysis_calls == 1:
                result["data"] = [{"label": label, "value_text": number, "unit": "次", "metric": "下载量",
                                   "period": "2026年", "source_id": "S1", "quote": DATA_QUOTE}
                                  for label, number in [("甲平台", "20"), ("乙平台", "30")]]
            if self.mutate_analysis:
                self.mutate_analysis(result)
        elif name == "Review":
            items = json.loads(payload["input"][1]["content"])["items"]
            result = {"decisions": [{"id": fid, "supported": not self.reject_review} for fid in items]}
            if self.mutate_review:
                self.mutate_review(result)
        elif name == "Layout":
            result = {"title": "文档阅读能力调研", "category": "产品研究", "summary_ids": ["F1"],
                      "sections": [{"title": f"研究维度{i}", "finding_ids": [f"F{i}"]} for i in range(1, self.count + 1)]}
            if self.mutate_layout:
                self.mutate_layout(result)
        elif name == "FinalQuality":
            result = {"reviewed": True, "issues": [], "suggestions": [], "edits": []}
        else:
            pytest.fail(f"unexpected schema {name}")
        return httpx.Response(200, json={"status": "completed", "output": [{"type": "message", "content": [
            {"type": "output_text", "text": json.dumps(result, ensure_ascii=False)}]}]})


def assert_relaxed_report(report, *, warnings=True):
    snapshot = report["_evidence"]
    assert snapshot["validation"]["mode"] == "relaxed"
    messages = snapshot["validation"]["warnings"]
    assert all(isinstance(message, str) and message for message in messages)
    assert len(messages) == len(set(messages))
    if warnings:
        assert messages
    notes = next(chapter for chapter in report["chapters"] if chapter["id"] == "generation-notes")
    assert notes["paragraphs"]
    if warnings:
        assert all(message in "\n".join(notes["paragraphs"]) for message in messages)
    assert report["source"] == "generated"
    Report.model_validate({**{key: value for key, value in report.items() if key != "_evidence"},
                           "id": "r-relaxed", "createdAt": "2026-10-07T00:00:00Z", "bookmarks": []})


@pytest.mark.asyncio
@pytest.mark.parametrize("depth,chapter_count,llm_count", [("brief", 5, 6), ("deep", 6, 7)])
async def test_real_pipeline_contract_and_source_provenance(depth, chapter_count, llm_count):
    workflow = Workflow(depth)
    progress = []

    async def record(step, message):
        progress.append(step)

    async with httpx.AsyncClient(transport=httpx.MockTransport(workflow)) as client:
        result = await Pipeline(config(), client).run("分析文档阅读能力", depth, record, lambda: False)
    assert result["source"] == "generated"
    assert len(result["chapters"]) == chapter_count
    assert result["chapters"][0]["id"] == "overview"
    sources = next(chapter for chapter in result["chapters"] if chapter["id"] == "sources")
    assert "https://example.org/doc" in "\n".join(sources["paragraphs"])
    assert "[S1]" in result["summary"]
    assert result["chart"] == [] and "chartMeta" not in result
    assert progress == [0, 1, 2]
    assert workflow.search_calls == workflow.count
    assert len(workflow.calls) == llm_count + workflow.count
    assert not {"id", "createdAt", "bookmarks"} & result.keys()
    assert_relaxed_report(result, warnings=False)
    evidence = result.pop("_evidence")
    assert set(evidence) == {"plan", "sources", "findings", "gaps", "data", "review", "layout", "validation"}
    assert len(evidence["plan"]["subquestions"]) == workflow.count
    assert evidence["sources"][0]["raw_content"] == RAW
    assert evidence["sources"][0]["url"] == "https://example.org/doc"
    assert datetime.fromisoformat(evidence["sources"][0]["timestamp"]).tzinfo is not None
    assert evidence["findings"]["F1"]["supports"][0]["quote"] == QUOTE
    assert all(d["supported"] for d in evidence["review"]["decisions"])
    assert evidence["layout"]["summary_ids"] == ["F1"]
    Report.model_validate({**result, "id": "r1", "createdAt": "2026-10-05T00:00:00Z", "bookmarks": []})


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", [
    lambda r: r["findings"][0]["supports"][0].update(source_id="S99"),
    lambda r: r["findings"][0]["supports"][0].update(quote="这是模型编造的、原文不存在的一段引文。"),
    lambda r: r["findings"][0].update(text="该系统的实际准确率达到99%。"),
    lambda r: r.update(findings=[]),
])
async def test_unknown_sources_fabricated_quotes_numbers_and_empty_findings_warn_but_publish(mutation):
    workflow = Workflow(mutate_analysis=mutation)
    async with httpx.AsyncClient(transport=httpx.MockTransport(workflow)) as client:
        report = await Pipeline(config(), client).run("测试研究", "brief", noop, lambda: False)
    assert_relaxed_report(report)
    assert workflow.analysis_calls == 2  # No business-validation repair request.
    public_text = json.dumps({key: value for key, value in report.items() if key != "_evidence"}, ensure_ascii=False)
    assert "[S99]" not in public_text


@pytest.mark.asyncio
@pytest.mark.parametrize("translated", [False, True])
async def test_english_source_and_chinese_finding_preserve_quote_and_flag_translation(translated):
    quote = "TaskGroup cancels remaining tasks when a task fails with an exception other than CancelledError."
    raw = quote + " The exceptions are then combined into an ExceptionGroup after all tasks have finished."
    chinese = "当某个任务抛出非取消异常时，任务组会取消其余任务。"
    def analyze(result):
        current_quote = chinese if translated else quote
        result["findings"] = [{"text": chinese, "supports": [{"source_id": "S1", "quote": current_quote}]}]

    workflow = Workflow(raw=raw, mutate_analysis=analyze)
    async with httpx.AsyncClient(transport=httpx.MockTransport(workflow)) as client:
        report = await Pipeline(config(), client).run("分析任务组的异常处理行为", "brief", noop, lambda: False)
    assert_relaxed_report(report, warnings=translated)
    assert workflow.analysis_calls == 2
    snapshot = report["_evidence"]
    assert snapshot["sources"][0]["raw_content"] == raw
    assert all(finding["text"] == chinese for finding in snapshot["findings"].values())
    assert all(finding["supports"] == [{"source_id": "S1", "quote": chinese if translated else quote}]
               for finding in snapshot["findings"].values())
    assert chinese in report["summary"] and report["source"] == "generated"


@pytest.mark.asyncio
async def test_search_snippet_without_raw_content_stays_metadata_and_warns():
    workflow = Workflow(raw=None)
    async with httpx.AsyncClient(transport=httpx.MockTransport(workflow)) as client:
        report = await Pipeline(config(), client).run("测试研究", "brief", noop, lambda: False)
    assert_relaxed_report(report)
    assert workflow.analysis_calls == 2
    source = report["_evidence"]["sources"][0]
    assert source["raw_content"] == "" and source["content_kind"] == "metadata_only"
    assert "Search snippet is not evidence" not in json.dumps(report["_evidence"])


@pytest.mark.asyncio
async def test_review_disagreement_is_preserved_as_warning():
    workflow = Workflow(reject_review=True)
    async with httpx.AsyncClient(transport=httpx.MockTransport(workflow)) as client:
        report = await Pipeline(config(), client).run("测试研究", "brief", noop, lambda: False)
    assert_relaxed_report(report)
    assert all(not item["supported"] for item in report["_evidence"]["review"]["decisions"])


@pytest.mark.asyncio
async def test_layout_unknown_ids_are_repaired_without_inventing_claims():
    workflow = Workflow(mutate_layout=lambda r: r.update(summary_ids=["F99"],
        sections=[{"title": "引用缺失的章节", "finding_ids": ["F99", "F1", "F1"]}]))
    async with httpx.AsyncClient(transport=httpx.MockTransport(workflow)) as client:
        report = await Pipeline(config(), client).run("测试研究", "brief", noop, lambda: False)
    assert_relaxed_report(report)
    assert "F99" not in report["summary"]
    body = [chapter for chapter in report["chapters"] if chapter["id"] not in {"overview", "sources", "generation-notes"}]
    assert body and any(chapter["paragraphs"] for chapter in body)


@pytest.mark.asyncio
async def test_chart_preserves_values_and_labels_them_as_candidate_data():
    workflow = Workflow(raw=RAW + DATA_QUOTE, with_chart=True)
    async with httpx.AsyncClient(transport=httpx.MockTransport(workflow)) as client:
        result = await Pipeline(config(), client).run("测试研究", "brief", noop, lambda: False)
    assert result["chart"] == [{"label": "甲平台", "value": 20.0}, {"label": "乙平台", "value": 30.0}]
    assert result["chartMeta"]["unit"] == "次"
    assert "2026年" in result["chartMeta"]["note"]
    assert "原文直接值" not in result["chartMeta"]["note"]
    assert_relaxed_report(result, warnings=False)


@pytest.mark.asyncio
async def test_chart_unmatched_finite_value_can_publish_but_is_flagged():
    def change(result):
        result["data"] = [{"label": "甲平台", "value_text": "99", "unit": "次", "metric": "下载量",
                           "period": "2026年", "source_id": "S1", "quote": DATA_QUOTE}]

    workflow = Workflow(raw=RAW + DATA_QUOTE, with_chart=True, mutate_analysis=change)
    async with httpx.AsyncClient(transport=httpx.MockTransport(workflow)) as client:
        report = await Pipeline(config(), client).run("测试研究", "brief", noop, lambda: False)
    assert_relaxed_report(report)
    assert workflow.analysis_calls == 2
    assert report["chart"] == [{"label": "甲平台", "value": 99.0}]
    assert "原文直接值" not in report["chartMeta"]["note"]


@pytest.mark.asyncio
@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity", "not-a-number"])
async def test_nonfinite_or_unparseable_chart_values_remain_text_without_plotting(value):
    def change(result):
        result["data"] = [{"label": "甲平台", "value_text": value, "unit": "次", "metric": "下载量",
                           "period": "2026年", "source_id": "S1", "quote": DATA_QUOTE}]

    workflow = Workflow(raw=RAW + DATA_QUOTE, mutate_analysis=change)
    async with httpx.AsyncClient(transport=httpx.MockTransport(workflow)) as client:
        report = await Pipeline(config(), client).run("测试研究", "brief", noop, lambda: False)
    assert_relaxed_report(report)
    assert workflow.analysis_calls == 2
    assert report["chart"] == [] and "chartMeta" not in report
    assert all(item["value_text"] == value for item in report["_evidence"]["data"].values())
    assert value in json.dumps(report["chapters"], ensure_ascii=False)


def test_chart_groups_units_and_warns_on_conflicting_values_while_keeping_first():
    first = {"label": "甲", "value_text": "20", "unit": "次", "metric": "下载量", "period": "2026", "source_id": "S1"}
    second = {**first, "label": "乙", "unit": "万次"}
    chart, metadata = Pipeline._chart({"D1": first, "D2": second})
    assert chart == [{"label": "甲", "value": 20.0}] and metadata["unit"] == "次"
    warnings = []
    chart, _ = Pipeline._chart({"D1": first, "D2": {**first, "value_text": "30"}}, warnings)
    assert chart == [{"label": "甲", "value": 20.0}] and warnings


@pytest.mark.parametrize("raw,unit,expected,expected_unit", [
    ("+12", "人", 12.0, "人"), ("-2.5", "次", -2.5, "次"),
    ("1,234.5", "次", 1234.5, "次"), ("-1,234.5e-2", "次", -12.345, "次"),
    (".25", "次", 0.25, "次"), ("1.2E+3", "次", 1200.0, "次"),
    ("12%", "", 12.0, "%"), ("12％", "百分比", 12.0, "百分比"),
    ("0e-9999", "次", 0.0, "次"),
])
def test_chart_parses_explicit_number_formats_without_silent_unit_conversion(raw, unit, expected, expected_unit):
    datum = {"label": "指标", "value_text": raw, "unit": unit, "metric": "变化", "period": "2026",
             "source_id": "S1"}
    warnings = []
    chart, metadata = Pipeline._chart({"D1": datum}, warnings)
    assert chart == [{"label": "指标", "value": expected}]
    assert metadata["unit"] == expected_unit
    assert datum["value_text"] == raw and datum["unit"] == unit
    assert warnings == []


@pytest.mark.asyncio
@pytest.mark.parametrize("raw,unit", [
    ("1,2,3", "次"), ("12,5", "次"), ("1,2345", "次"),
    ("12%", "人"), ("1e-9999", "次"), ("-1e-9999", "次"),
])
async def test_ambiguous_units_numbers_and_float_underflow_stay_text_only(raw, unit):
    def change(result):
        result["data"] = [{"label": "观测值", "value_text": raw, "unit": unit, "metric": "计数",
                           "period": "2026", "source_id": "S1", "quote": QUOTE}]

    workflow = Workflow(mutate_analysis=change)
    async with httpx.AsyncClient(transport=httpx.MockTransport(workflow)) as client:
        report = await Pipeline(config(), client).run("测试研究", "brief", noop, lambda: False)
    assert_relaxed_report(report)
    assert report["chart"] == [] and "chartMeta" not in report
    assert all(datum["value_text"] == raw and datum["unit"] == unit
               for datum in report["_evidence"]["data"].values())
    assert raw in json.dumps(report["chapters"], ensure_ascii=False)


@pytest.mark.parametrize("chapters", [
    [{"id": f"c-{i}", "title": "章节", "paragraphs": ["正文"]} for i in range(2001)],
    [{"id": "many-paragraphs", "title": "章节", "paragraphs": ["正文"] * 2001}],
    [{"id": "long-paragraph", "title": "章节", "paragraphs": ["文" * 450001]}],
])
def test_fitting_oversized_chapters_produces_reader_compatible_copy_and_notice(chapters):
    original = json.dumps(chapters, ensure_ascii=False)
    warnings = []
    fitted = Pipeline._fit_chapters(chapters, warnings)
    assert len(fitted) <= 1999
    assert all(len(chapter["paragraphs"]) <= 2000 for chapter in fitted)
    assert sum(len(paragraph) for chapter in fitted for paragraph in chapter["paragraphs"]) <= 450000
    assert warnings and json.dumps(chapters, ensure_ascii=False) == original


def test_fitting_retains_overview_and_source_urls_when_all_content_limits_are_exceeded():
    overview = {"id": "overview", "title": "概览", "paragraphs": ["报告概览"]}
    sources = {"id": "sources", "title": "来源", "paragraphs": [
        f"[S{i}] 资料 https://example.org/source/{i}" for i in range(1, 9)]}
    chapters = [overview, {"id": "comparison", "title": "正文", "paragraphs": ["文" * 600] * 2001},
                *[{"id": f"section-{i}", "title": "章节", "paragraphs": ["尾段"]} for i in range(2001)], sources]
    warnings = []
    fitted = Pipeline._fit_chapters(chapters, warnings)
    assert fitted[0] == overview and fitted[-1] == sources
    assert len(fitted) == 1999
    assert all(len(chapter["paragraphs"]) <= 2000 for chapter in fitted)
    assert sum(len(paragraph) for chapter in fitted for paragraph in chapter["paragraphs"]) <= 450000
    assert warnings


@pytest.mark.asyncio
async def test_large_report_fits_public_limits_without_discarding_private_research():
    # Each upstream response stays below its byte cap, but merged public text
    # exceeds the reader limit and creates more warnings than the notes budget.
    texts = [(f"Research item {i}: " + "Unverified analysis. " * 30).strip() for i in range(1201)]

    def expand(result):
        result["findings"] = [{"text": text, "supports": []} for text in texts]

    workflow = Workflow(mutate_analysis=expand)
    async with httpx.AsyncClient(transport=httpx.MockTransport(workflow)) as client:
        report = await Pipeline(config(), client).run("测试长报告整理", "brief", noop, lambda: False)
    # A shortened warning chapter need not repeat every private diagnostic.
    assert_relaxed_report(report, warnings=False)
    snapshot = report["_evidence"]
    assert len(snapshot["findings"]) == len(texts) * 2
    assert snapshot["findings"]["F2402"]["text"] == texts[-1]
    assert sum(len(finding["text"]) for finding in snapshot["findings"].values()) > 450000
    assert len(snapshot["layout"]["sections"][-1]["finding_ids"]) == len(texts) * 2
    assert report["chapters"][0]["id"] == "overview"
    sources = next(chapter for chapter in report["chapters"] if chapter["id"] == "sources")
    assert "[S1]" in "\n".join(sources["paragraphs"])
    assert "https://example.org/doc" in "\n".join(sources["paragraphs"])
    notes = next(chapter for chapter in report["chapters"] if chapter["id"] == "generation-notes")
    public_body = [chapter for chapter in report["chapters"] if chapter["id"] != "generation-notes"]
    assert len(public_body) <= 1999 and len(notes["paragraphs"]) <= 2000
    assert sum(len(paragraph) for chapter in public_body for paragraph in chapter["paragraphs"]) <= 450000
    assert sum(map(len, notes["paragraphs"])) <= 49900
    assert len(snapshot["validation"]["warnings"]) > len(notes["paragraphs"])
    assert any("完整研究对象" in warning for warning in snapshot["validation"]["warnings"])
    assert any("其余生成提示" in paragraph for paragraph in notes["paragraphs"])


def test_internal_contracts_default_nulls_and_ignore_extra_fields_without_changing_number_text():
    assert Plan.model_validate({"subquestions": None, "extra": "ignored"}).model_dump() == {"subquestions": []}
    analysis = Analysis.model_validate({"findings": [{"text": None, "supports": None, "extra": "ignored"}],
        "gaps": None, "data": [{"label": 42, "value_text": "0012.50", "unit": None, "period": 2026,
                                "quote": None, "extra": "ignored"}], "extra": "ignored"})
    assert analysis.findings[0].model_dump() == {"text": "", "supports": [], "statement_kind": "analysis"}
    assert analysis.gaps == []
    assert analysis.data[0].label == "42" and analysis.data[0].period == "2026"
    assert analysis.data[0].value_text == "0012.50" and analysis.data[0].unit == ""
    assert "extra" not in analysis.model_dump_json()
    layout = Layout.model_validate({"title": None, "summary_ids": [1], "sections": [
        {"title": 7, "finding_ids": None, "extra": "ignored"}], "extra": "ignored"})
    assert layout.title == "" and layout.summary_ids == ["1"]
    assert layout.sections[0].title == "7" and layout.sections[0].finding_ids == []
    review = Review.model_validate({"decisions": [{"id": 1, "supported": "true", "extra": None}]})
    assert review.decisions[0].model_dump() == {"id": "1", "supported": True}


@pytest.mark.asyncio
async def test_negative_single_chart_value_is_renderable_and_not_blocked():
    def change(result):
        result["data"] = [{"label": "增长", "value_text": "-2.5", "unit": "%", "metric": "增长率",
                           "period": "2026年", "source_id": "S1", "quote": "未取得原始数值出处。"}]

    workflow = Workflow(mutate_analysis=change)
    async with httpx.AsyncClient(transport=httpx.MockTransport(workflow)) as client:
        report = await Pipeline(config(), client).run("测试研究", "brief", noop, lambda: False)
    assert_relaxed_report(report)
    assert report["chart"] == [{"label": "增长", "value": -2.5}]


@pytest.mark.asyncio
@pytest.mark.parametrize("depth,count", [("brief", 4), ("deep", 1)])
async def test_plan_and_chapter_counts_are_guidance_not_publish_gates(depth, count):
    workflow = Workflow(depth=depth)
    workflow.count = count
    async with httpx.AsyncClient(transport=httpx.MockTransport(workflow)) as client:
        report = await Pipeline(config(), client).run("测试研究", depth, noop, lambda: False)
    assert_relaxed_report(report, warnings=False)
    assert len(report["_evidence"]["plan"]["subquestions"]) == count
    assert workflow.analysis_calls == count
    assert len(report["chapters"]) == count + 3


@pytest.mark.asyncio
@pytest.mark.parametrize("decisions", [[], [{"id": "F99", "supported": True}],
    [{"id": "F1", "supported": True}, {"id": "F1", "supported": False}]])
async def test_incomplete_or_ambiguous_review_warns_but_preserves_results(decisions):
    workflow = Workflow(mutate_review=lambda result: result.update(decisions=decisions))
    async with httpx.AsyncClient(transport=httpx.MockTransport(workflow)) as client:
        report = await Pipeline(config(), client).run("测试研究", "brief", noop, lambda: False)
    assert_relaxed_report(report)
    assert len(report["_evidence"]["findings"]) == 2


@pytest.mark.asyncio
async def test_missing_internal_fields_and_empty_outputs_generate_honest_readable_report():
    def omit_fields(result):
        result.clear()
        result["unrecognized_model_field"] = "ignored"

    workflow = Workflow(mutate_plan=omit_fields, mutate_analysis=omit_fields,
                        mutate_review=omit_fields, mutate_layout=omit_fields)
    async with httpx.AsyncClient(transport=httpx.MockTransport(workflow)) as client:
        report = await Pipeline(config(), client).run("测试研究", "brief", noop, lambda: False)
    assert_relaxed_report(report)
    assert report["summary"].strip()
    assert report["_evidence"]["findings"] == {}
    assert report["_evidence"]["data"] == {}
    assert "unrecognized_model_field" not in json.dumps(report)


@pytest.mark.asyncio
async def test_no_search_credentials_generate_model_analysis_with_explicit_warning():
    workflow = Workflow()
    async with httpx.AsyncClient(transport=httpx.MockTransport(workflow)) as client:
        report = await Pipeline(replace(config(), search_api_key=""), client).run(
            "测试研究", "brief", noop, lambda: False)
    assert_relaxed_report(report)
    assert workflow.search_calls == 0
    assert report["_evidence"]["sources"] == []
    assert "[S1]" not in report["summary"]


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["Plan", "Analysis", "Review", "Layout", "FinalQuality"])
@pytest.mark.parametrize("failure,code,attempts", [
    ("auth", "PROVIDER_CONFIG", 1), ("temporary", "UPSTREAM_TEMPORARY", 2),
    ("protocol", "UPSTREAM_PROTOCOL", 1), ("refusal", "MODEL_REFUSED", 1),
])
async def test_primary_failures_block_but_supplementary_failures_preserve_analysis(stage, failure, code, attempts):
    workflow = Workflow()
    failed_calls = []

    def handler(request):
        payload = json.loads(request.content)
        if request.url.host != "api.tavily.com" and payload["text"]["format"]["name"] == stage:
            failed_calls.append(request)
            if failure == "auth":
                return httpx.Response(401, json={"error": "private upstream response"})
            if failure == "temporary":
                return httpx.Response(503, json={"error": "private upstream response"})
            if failure == "protocol":
                return httpx.Response(200, json=[])
            return httpx.Response(200, json={"status": "completed", "output": [{"type": "message",
                "content": [{"type": "refusal", "refusal": "private upstream refusal"}]}]})
        return workflow(request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        if stage in {"Plan", "Analysis"} or failure == "auth":
            with pytest.raises(PipelineError) as caught:
                await Pipeline(config(), client).run("测试研究", "brief", noop, lambda: False)
            assert caught.value.code == code
        else:
            report = await Pipeline(config(), client).run("测试研究", "brief", noop, lambda: False)
            assert_relaxed_report(report)
            assert len(report["_evidence"]["findings"]) == 2
            assert code in "\n".join(report["_evidence"]["validation"]["warnings"])
            assert "private upstream" not in json.dumps(report)
    assert len(failed_calls) == attempts


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["Review", "Layout", "FinalQuality"])
async def test_cancellation_never_becomes_an_advisory_warning(stage):
    workflow = Workflow()

    def handler(request):
        payload = json.loads(request.content)
        if request.url.host != "api.tavily.com" and payload["text"]["format"]["name"] == stage:
            raise asyncio.CancelledError
        return workflow(request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(asyncio.CancelledError):
            await Pipeline(config(), client).run("测试研究", "brief", noop, lambda: False)


@pytest.mark.asyncio
async def test_fixture_is_explicit_labeled_and_never_networked():
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: pytest.fail("network in fixture"))) as client:
        with pytest.raises(PipelineError):
            await Pipeline(Settings(llm_provider="fixture"), client).run("测试问题", "deep", noop, lambda: False)
        result = await Pipeline(Settings(llm_provider="fixture", test_mode=True, fixture_delay_seconds=0), client).run(
            "测试问题", "deep", noop, lambda: False)
        with pytest.raises(PipelineError):
            await Pipeline(Settings(), client).run("测试问题", "deep", noop, lambda: False)
    assert result["source"] == "demo" and "模拟" in result["title"]
    assert result["_evidence"] == {"mode": "fixture"}
    assert len(result["chapters"]) == 5
    assert "模拟来源" in result["chapters"][-1]["paragraphs"][0]


@pytest.mark.asyncio
async def test_snapshot_retains_exact_bounded_source_excerpt_used_by_analysis():
    raw = RAW + "证据上下文。" * 3000
    workflow = Workflow(raw=raw)
    async with httpx.AsyncClient(transport=httpx.MockTransport(workflow)) as client:
        result = await Pipeline(config(), client).run("测试研究", "brief", noop, lambda: False)
    saved = result["_evidence"]["sources"][0]["raw_content"]
    expected = raw[:min(10000, 60000 // config().max_sources)]
    assert saved == expected and len(saved) < len(raw)
    analysis_request = next(json.loads(call.content) for call in workflow.calls
                            if call.url.host != "api.tavily.com"
                            and json.loads(call.content)["text"]["format"]["name"] == "Analysis")
    assert json.loads(analysis_request["input"][1]["content"])["sources"][0]["raw_content"] == saved


@pytest.mark.asyncio
async def test_cancel_between_stages_does_not_publish_fixture_report():
    flag = False

    async def cancel_after_progress(*_):
        nonlocal flag
        flag = True

    with pytest.raises(asyncio.CancelledError):
        await Pipeline(Settings(llm_provider="fixture", test_mode=True, fixture_delay_seconds=0)).run(
            "测试问题", "brief", cancel_after_progress, lambda: flag)
