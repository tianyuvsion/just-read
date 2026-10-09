"""One-key search integration, offline with invented credentials and source excerpts."""
from copy import deepcopy
from dataclasses import replace
import json
import logging
import time
import uuid

import httpx
import pytest
from fastapi.testclient import TestClient

from just_read.models import Report
from just_read.pipeline import Pipeline
from just_read.providers import PipelineError, SearchProvider
from just_read.settings import Settings
from test_openrouter import completion
from test_pipeline import Workflow, RAW, assert_relaxed_report


def config(**overrides):
    return replace(Settings(llm_provider="openrouter", llm_model="test/search-model",
                            llm_api_key="invented-single-key"), **overrides)


def citation(content=RAW, url="https://example.org/doc"):
    return {"type": "url_citation", "url_citation": {"url": url, "title": "公开文档摘录", "content": content}}


def search_response(annotations=None, **message_fields):
    return completion(content="Unverified generated answer; never use this as source evidence.",
                      annotations=[citation()] if annotations is None else annotations, **message_fields)


@pytest.mark.parametrize("provider,search,has_search,expected,search_ready", [
    ("openrouter", "auto", False, "openrouter", True),
    ("openrouter", "auto", True, "tavily", True),
    ("openai", "auto", False, "tavily", False),
    ("anthropic", "auto", True, "tavily", True),
    ("openrouter", "openrouter", False, "openrouter", True),
    ("openrouter", "openrouter", True, "openrouter", True),
    ("openrouter", "tavily", False, "tavily", False),
    ("openrouter", "tavily", True, "tavily", True),
    ("openai", "tavily", True, "tavily", True),
])
def test_search_provider_resolution_and_readiness_matrix(provider, search, has_search, expected, search_ready):
    settings = config(llm_provider=provider, search_provider=search,
                      search_api_key="invented-search-key" if has_search else "")
    settings.validate()
    assert settings.effective_search_provider == expected
    assert settings.search_ready is search_ready
    assert settings.ready is True  # Search availability is advisory for report generation.


@pytest.mark.parametrize("missing", ["llm_api_key", "llm_model"])
def test_openrouter_search_requires_model_and_llm_key(missing):
    settings = config(**{missing: ""})
    assert settings.effective_search_provider == "openrouter"
    assert settings.search_ready is False and settings.ready is False


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
def test_explicit_openrouter_search_rejects_other_llm_adapters(provider):
    with pytest.raises(ValueError):
        config(llm_provider=provider, search_provider="openrouter").validate()


def test_unknown_search_provider_is_rejected():
    with pytest.raises(ValueError):
        config(search_provider="unknown").validate()


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", ["llm_api_key", "llm_model"])
async def test_search_missing_openrouter_credentials_fails_before_network(missing):
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: pytest.fail("must not call network"))) as client:
        with pytest.raises(PipelineError) as error:
            await SearchProvider(config(**{missing: ""}), client).search("research query", lambda: False)
    assert error.value.code == "PROVIDER_CONFIG"


@pytest.mark.asyncio
@pytest.mark.parametrize("base_url,expected_url", [
    ("", "https://openrouter.ai/api/v1/chat/completions"),
    ("https://gateway.invalid/api/v1/", "https://gateway.invalid/api/v1/chat/completions"),
])
async def test_one_key_search_wire_bounds_and_extractive_metadata(base_url, expected_url):
    query = "查询公开文档的阅读能力"
    original = RAW + "原文上下文。" * 1000
    calls = []

    def handler(request):
        assert str(request.url) == expected_url
        assert request.headers["authorization"] == "Bearer invented-single-key"
        body = json.loads(request.content)
        calls.append(body)
        assert body["model"] == "test/search-model"
        assert body["stream"] is False and body["max_tokens"] == 1024
        assert body["max_tool_calls"] == 1
        assert body["tools"] == [{"type": "openrouter:web_search", "parameters": {
            "engine": "exa", "max_results": 4, "max_total_results": 4,
            "max_uses": 1, "max_characters": 4000}}]
        assert "response_format" not in body and "plugins" not in body
        assert any(query in message.get("content", "") for message in body["messages"])
        return httpx.Response(200, json=search_response([citation(original)]))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await SearchProvider(config(llm_base_url=base_url, max_sources=4), client).search(query, lambda: False)
    assert len(calls) == 1 and len(result) == 1
    assert result[0]["url"] == "https://example.org/doc"
    assert result[0]["raw_content"] == original[:4000]
    assert result[0]["content_kind"] == "extractive_excerpt"
    assert result[0]["retrieval_provider"] == "openrouter"
    assert result[0]["retrieval_engine"] == "exa"
    assert result[0]["query"] == query


@pytest.mark.asyncio
@pytest.mark.parametrize("annotations", [
    [], [citation(None)], [citation("")], [citation("  \n\t")],
    [{"type": "url_citation", "url_citation": {"url": "https://example.org/doc", "title": "Only a link"}}],
    [{"type": "not_a_citation", "content": RAW}],
])
async def test_search_does_not_promote_generated_answer_or_url_only_citations_to_evidence(annotations):
    response = search_response(annotations)
    response["choices"][0]["message"]["content"] = RAW  # Tempting, valid-looking generated text.
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=response))) as client:
        results = await SearchProvider(config(), client).search("research query", lambda: False)
    if annotations and annotations[0].get("type") == "url_citation":
        assert len(results) == 1
        assert results[0]["url"] == "https://example.org/doc"
        assert results[0]["raw_content"] == "" and results[0]["content_kind"] == "metadata_only"
    else:
        assert results == []
    assert RAW not in json.dumps(results)


@pytest.mark.asyncio
async def test_search_missing_annotations_returns_no_evidence_and_obeys_result_cap():
    response = search_response()
    response["choices"][0]["message"].pop("annotations")
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=response))) as client:
        assert await SearchProvider(config(), client).search("research query", lambda: False) == []
    response = search_response([citation(url=f"https://example.org/doc/{i}") for i in range(5)])
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=response))) as client:
        assert len(await SearchProvider(config(max_sources=2), client).search("research query", lambda: False)) <= 2


@pytest.mark.asyncio
@pytest.mark.parametrize("payload,code", [
    (completion(finish_reason="length", annotations=[citation()]), "MODEL_TRUNCATED"),
    (completion(finish_reason="content_filter", annotations=[citation()]), "MODEL_REFUSED"),
    (completion(refusal="private refusal", annotations=[citation()]), "MODEL_REFUSED"),
    (completion(finish_reason="tool_calls", tool_calls=[{"id": "unfinished"}], annotations=[citation()]), "UPSTREAM_PROTOCOL"),
    ({"choices": search_response()["choices"] * 2}, "UPSTREAM_PROTOCOL"),
])
async def test_search_refusal_truncation_and_unfinished_response_are_not_retried(payload, code):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=payload)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(PipelineError) as error:
            await SearchProvider(config(), client).search("research query", lambda: False)
    assert error.value.code == code and len(calls) == 1
    assert "private refusal" not in str(error.value)


@pytest.mark.asyncio
async def test_search_retries_temporary_transport_failure_once():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(503, json={"error": "private-response"}) if len(calls) == 1 else httpx.Response(200, json=search_response())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        results = await SearchProvider(config(), client).search("research query", lambda: False)
    assert len(calls) == 2 and results[0]["raw_content"] == RAW


@pytest.mark.asyncio
@pytest.mark.parametrize("location", ["top", "choice"])
@pytest.mark.parametrize("upstream_code,code,attempts", [(401, "PROVIDER_CONFIG", 1),
    (429, "UPSTREAM_TEMPORARY", 2), (503, "UPSTREAM_TEMPORARY", 2), (402, "UPSTREAM_ERROR", 1)])
async def test_search_embedded_errors_override_valid_citations_and_are_sanitized(location, upstream_code, code, attempts, caplog):
    payload = search_response()
    where = payload if location == "top" else payload["choices"][0]
    where["error"] = {"code": upstream_code, "message": "secret-body-marker invented-single-key"}
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=payload)

    with caplog.at_level(logging.INFO, logger="just_read.providers"):
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(PipelineError) as error:
                await SearchProvider(config(), client).search("research query", lambda: False)
    assert error.value.code == code and len(calls) == attempts
    assert "secret-body-marker" not in str(error.value) + caplog.text
    assert "invented-single-key" not in str(error.value) + caplog.text


class OneKeyWorkflow:
    """Same research fixtures as other adapters, with web tools instead of Tavily."""
    def __init__(self, *, missing_excerpt=False):
        self.workflow = Workflow(depth="brief")
        self.calls = []
        self.search_calls = 0
        self.missing_excerpt = missing_excerpt

    def __call__(self, request):
        assert request.url == "https://openrouter.ai/api/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer invented-single-key"
        self.calls.append(request)
        body = json.loads(request.content)
        if body.get("tools"):
            self.search_calls += 1
            return httpx.Response(200, json=search_response([] if self.missing_excerpt else [citation()]))
        fixture_request = httpx.Request("POST", "https://api.openai.com/v1/responses", json={
            "text": {"format": {"name": body["response_format"]["json_schema"]["name"]}},
            "input": deepcopy(body["messages"]),
        })
        result = self.workflow(fixture_request).json()
        return httpx.Response(200, json=completion(content=result["output"][0]["content"][0]["text"]))


@pytest.mark.asyncio
async def test_one_key_full_brief_pipeline_retains_excerpt_provenance_in_snapshot():
    workflow = OneKeyWorkflow()
    progress = []

    async def record(step, _message):
        progress.append(step)

    async with httpx.AsyncClient(transport=httpx.MockTransport(workflow)) as client:
        result = await Pipeline(config(), client).run("分析文档阅读能力", "brief", record, lambda: False)
    assert len(workflow.calls) == 8 and workflow.search_calls == 2
    assert progress == [0, 1, 2] and result["source"] == "generated"
    evidence = result.pop("_evidence")
    source = evidence["sources"][0]
    assert source["raw_content"] == RAW and source["url"] == "https://example.org/doc"
    assert source["content_kind"] == "extractive_excerpt"
    assert source["retrieval_provider"] == "openrouter" and source["retrieval_engine"] == "exa"
    assert source["query"] == evidence["plan"]["subquestions"][0]["query"]
    assert source["timestamp"] and source["accessed_at"]
    assert "Unverified generated answer" not in json.dumps(evidence)
    Report.model_validate({**result, "id": "one-key-report", "createdAt": "2026-10-07T00:00:00Z", "bookmarks": []})


@pytest.mark.asyncio
async def test_one_key_pipeline_without_excerpt_publishes_warnings_not_fake_evidence():
    workflow = OneKeyWorkflow(missing_excerpt=True)

    async def noop(*_):
        pass

    async with httpx.AsyncClient(transport=httpx.MockTransport(workflow)) as client:
        report = await Pipeline(config(), client).run("分析文档阅读能力", "brief", noop, lambda: False)
    assert_relaxed_report(report)
    assert report["_evidence"]["sources"] == []
    assert workflow.workflow.analysis_calls == 2
    assert "[S1]" not in report["summary"]
    assert "Unverified generated answer" not in json.dumps(report)


@pytest.mark.parametrize("missing_excerpt", [False, True])
def test_one_key_http_task_publishes_with_explicit_source_quality_notes(tmp_path, monkeypatch, missing_excerpt):
    settings = config(database_path=str(tmp_path / "one-key.sqlite3"))
    # Prevent app.py's module-level app from loading any real .env on first import.
    monkeypatch.setattr(Settings, "from_env", lambda: settings)
    from just_read.app import create_app

    workflow = OneKeyWorkflow(missing_excerpt=missing_excerpt)

    class MockedPipeline:
        async def run(self, *args):
            async with httpx.AsyncClient(transport=httpx.MockTransport(workflow)) as client:
                return await Pipeline(settings, client).run(*args)

    app = create_app(settings, pipeline_factory=MockedPipeline)
    with TestClient(app) as client:
        assert client.get("/api/v1/health").json()["ready"] is True
        response = client.post("/api/v1/research-tasks", json={"question": "分析文档阅读能力", "depth": "brief"},
                               headers={"Idempotency-Key": str(uuid.uuid4())})
        assert response.status_code == 201
        task_id = response.json()["id"]
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            task = client.get(f"/api/v1/research-tasks/{task_id}").json()
            if task["status"] in {"succeeded", "failed", "cancelled"}:
                break
            time.sleep(0.01)
        assert task["status"] == "succeeded", task
        assert task["report"]["source"] == "generated" and task["step"] == 3
        report_id = task["report"]["id"]
        with app.state.store.connect() as db:
            saved = json.loads(db.execute("SELECT content FROM report_artifacts WHERE report_id=?", (report_id,)).fetchone()[0])
        assert saved["validation"]["mode"] == "relaxed"
        assert any(chapter["id"] == "generation-notes" for chapter in task["report"]["chapters"])
        if missing_excerpt:
            assert saved["sources"] == [] and saved["validation"]["warnings"]
            assert "[S1]" not in task["report"]["summary"]
        else:
            assert saved["sources"][0]["raw_content"] == RAW
            assert saved["sources"][0]["retrieval_provider"] == "openrouter"
        assert len(workflow.calls) == 8
        for url in [f"/api/v1/research-tasks/{task_id}", f"/api/v1/reports/{report_id}", "/api/v1/reports"]:
            response = client.get(url)
            assert response.status_code == 200
            assert '"_evidence":' not in response.text and "raw_content" not in response.text
