"""OpenRouter contract tests use only MockTransport and invented credentials."""
from copy import deepcopy
from dataclasses import replace
import json
import logging

import httpx
import pytest
from pydantic import BaseModel, ConfigDict, Field

from just_read.models import Report
from just_read.pipeline import Analysis, Pipeline
from just_read.providers import ModelProvider, PipelineError
from just_read.settings import Settings
from test_pipeline import Workflow, RAW


class RouterResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    answer: str = Field(min_length=3, max_length=20)


def config(**overrides):
    return replace(Settings(llm_provider="openrouter", llm_model="test/vendor-fixed-model",
                            llm_api_key="invented-router-key", search_api_key="invented-search-key"), **overrides)


def completion(value=None, *, finish_reason="stop", **message_fields):
    return {"model": "test/actual-model", "choices": [{"index": 0, "finish_reason": finish_reason,
        "message": {"role": "assistant", "content": json.dumps(value or {"answer": "valid"}), **message_fields}}]}


@pytest.mark.parametrize("missing", [None, "llm_model", "llm_api_key", "search_api_key"])
def test_openrouter_settings_validate_and_ready_supports_key_only_search(missing):
    settings = config(**({missing: ""} if missing else {}))
    settings.validate()
    assert settings.ready is (missing in {None, "search_api_key"})


@pytest.mark.asyncio
@pytest.mark.parametrize("base_url,expected_url", [
    ("", "https://openrouter.ai/api/v1/chat/completions"),
    ("https://gateway.invalid/router/v1/", "https://gateway.invalid/router/v1/chat/completions"),
])
async def test_openrouter_wire_uses_nonstream_native_json_schema_and_fixed_model(base_url, expected_url):
    calls = []

    def handler(request):
        assert str(request.url) == expected_url
        assert request.headers["authorization"] == "Bearer invented-router-key"
        assert "x-api-key" not in request.headers and "anthropic-version" not in request.headers
        payload = json.loads(request.content)
        calls.append(payload)
        # Reasoning may coexist with content and must not be parsed as the result.
        return httpx.Response(200, json=completion(reasoning="This is not JSON output."))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await ModelProvider(config(llm_base_url=base_url), client).complete(
            RouterResult, "system rules", "user question", lambda: False, max_tokens=1024)
    assert result.answer == "valid" and len(calls) == 1
    body = calls[0]
    assert body["model"] == "test/vendor-fixed-model"
    assert body["stream"] is False and body["max_tokens"] == 1024
    assert body["messages"] == [{"role": "system", "content": "system rules"}, {"role": "user", "content": "user question"}]
    assert body["provider"]["require_parameters"] is False
    format_ = body["response_format"]
    assert format_["type"] == "json_schema"
    schema_config = format_["json_schema"]
    assert schema_config["name"] == "RouterResult" and schema_config["strict"] is False
    assert schema_config["schema"]["additionalProperties"] is False
    assert schema_config["schema"]["required"] == ["answer"]
    assert "maxLength" not in schema_config["schema"]["properties"]["answer"]
    assert not {"input", "text", "output_config", "max_output_tokens", "tools"} & body.keys()


PROTOCOL_FAILURES = [
    {}, {"choices": []}, {"choices": {}}, {"choices": [None]},
    {"choices": completion()["choices"] * 2},
    {"choices": [{"finish_reason": "stop"}]},
    {"choices": [{"finish_reason": "stop", "message": None}]},
    completion(finish_reason="tool_calls", tool_calls=[{"id": "not-supported"}]),
    completion(tool_calls=[{"id": "not-supported"}]),
    completion(finish_reason=None), completion(finish_reason="error"),
    completion(finish_reason=[]), completion(finish_reason={}),
    completion(content=None), completion(content=[{"type": "text", "text": '{"answer":"valid"}'}]),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", PROTOCOL_FAILURES)
async def test_openrouter_ambiguous_or_incomplete_protocol_fails_without_retry(payload):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=payload)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(PipelineError) as error:
            await ModelProvider(config(), client).complete(RouterResult, "s", "u", lambda: False)
    assert error.value.code == "UPSTREAM_PROTOCOL"
    assert len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("payload,code", [
    (completion(finish_reason="length"), "MODEL_TRUNCATED"),
    (completion(finish_reason="content_filter", content=None), "MODEL_REFUSED"),
    (completion(refusal="Cannot comply with this request."), "MODEL_REFUSED"),
])
async def test_openrouter_refusal_and_truncation_override_parseable_json(payload, code):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=payload)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(PipelineError) as error:
            await ModelProvider(config(), client).complete(RouterResult, "s", "u", lambda: False)
    assert error.value.code == code
    assert len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("sequence", [("temporary", "invalid", "ok"), ("invalid", "temporary", "ok")])
async def test_openrouter_transport_retry_and_schema_repair_share_three_request_budget(sequence):
    calls = []

    def handler(request):
        outcome = sequence[len(calls)]
        calls.append(json.loads(request.content))
        if outcome == "temporary":
            return httpx.Response(503, json={"error": {"message": "not displayed"}})
        return httpx.Response(200, json=completion({"answer": "x" if outcome == "invalid" else "valid"}))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await ModelProvider(config(), client).complete(RouterResult, "system", "question", lambda: False)
    assert result.answer == "valid" and len(calls) == 3
    assert "前次响应" in calls[-1]["messages"][0]["content"]


@pytest.mark.asyncio
@pytest.mark.parametrize("code", ["INVALID_EVIDENCE", "INVALID_CHART"])
@pytest.mark.parametrize("sequence", [
    ("semantic", "ok"), ("temporary", "semantic", "ok"), ("semantic", "temporary", "ok"),
])
async def test_openrouter_semantic_validator_repairs_once_with_bounded_transport_retry(code, sequence):
    calls, validated = [], []

    def handler(request):
        outcome = sequence[len(calls)]
        calls.append(json.loads(request.content))
        if outcome == "temporary":
            return httpx.Response(503, json={"error": {"message": "private upstream body"}})
        return httpx.Response(200, json=completion({"answer": "unsupported" if outcome == "semantic" else "valid"}))

    def validate(value):
        assert isinstance(value, RouterResult)
        validated.append(value.answer)
        if value.answer != "valid":
            raise PipelineError(code, "未通过本地证据校验。")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await ModelProvider(config(), client).complete(
            RouterResult, "system", "question", lambda: False, validator=validate)
    assert result.answer == "valid"
    assert validated == ["unsupported", "valid"]
    assert len(calls) == len(sequence) <= 3
    assert "前次响应" in calls[-1]["messages"][0]["content"]


@pytest.mark.asyncio
@pytest.mark.parametrize("code", ["INVALID_EVIDENCE", "INVALID_CHART"])
async def test_openrouter_persistent_semantic_failure_is_not_accepted_after_repair(code):
    calls, validated = [], []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=completion())

    def reject(value):
        validated.append(value)
        raise PipelineError(code, "未通过本地证据校验。")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(PipelineError) as caught:
            await ModelProvider(config(), client).complete(
                RouterResult, "system", "question", lambda: False, validator=reject)
    assert caught.value.code == code
    assert len(calls) == len(validated) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("sequence", [
    ("schema", "semantic"), ("semantic", "schema"),
    ("temporary", "schema", "semantic"), ("schema", "temporary", "semantic"),
    ("temporary", "semantic", "schema"), ("semantic", "temporary", "schema"),
])
async def test_openrouter_schema_and_semantic_failures_share_one_repair_budget(sequence):
    calls, validated = [], []

    def handler(request):
        outcome = sequence[len(calls)]
        calls.append(request)
        if outcome == "temporary":
            return httpx.Response(503, json={"error": {"message": "private upstream body"}})
        return httpx.Response(200, json=completion({"answer": "x" if outcome == "schema" else "unsupported"}))

    def reject(value):
        validated.append(value.answer)
        raise PipelineError("INVALID_EVIDENCE", "未通过本地证据校验。")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(PipelineError) as caught:
            await ModelProvider(config(), client).complete(
                RouterResult, "system", "question", lambda: False, validator=reject)
    assert caught.value.code == ("INVALID_EVIDENCE" if sequence[-1] == "semantic" else "INVALID_MODEL_OUTPUT")
    # Invalid schema must never reach the semantic validator. Neither error gets
    # its own fresh repair allowance, even when a transport retry is interleaved.
    assert validated == ["unsupported"]
    assert len(calls) == len(sequence) <= 3


@pytest.mark.asyncio
@pytest.mark.parametrize("content", ["", "not-json", '{"answer":"x"}', '{"answer":"valid","extra":true}'])
async def test_openrouter_invalid_content_and_local_constraints_get_only_one_repair(content):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=completion(content=content))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(PipelineError) as error:
            await ModelProvider(config(), client).complete(RouterResult, "s", "u", lambda: False)
    assert error.value.code == "INVALID_MODEL_OUTPUT"
    assert len(calls) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("content", ['```json\n{"answer":"valid"}\n```', '```\n{"answer":"valid"}\n```'])
async def test_openrouter_accepts_fenced_json_without_extra_repair(content):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=completion(content=content))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await ModelProvider(config(), client).complete(RouterResult, "system", "question", lambda: False)
    assert result.answer == "valid" and len(calls) == 1


@pytest.mark.asyncio
async def test_openrouter_fenced_internal_contract_accepts_null_extra_and_numeric_strings_once():
    calls = []
    model_output = {"findings": [{"text": "这是待核实分析。", "supports": None, "extra": "ignored"}],
        "gaps": None, "data": [{"label": 7, "value_text": "0012.50", "period": 2026,
                                "unit": None, "quote": None}], "extra": "ignored"}

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=completion(content="```json\n" + json.dumps(model_output) + "\n```"))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await ModelProvider(config(), client).complete(Analysis, "system", "question", lambda: False)
    assert len(calls) == 1 and result.findings[0].supports == [] and result.gaps == []
    assert result.data[0].label == "7" and result.data[0].period == "2026"
    assert result.data[0].value_text == "0012.50" and result.data[0].unit == ""
    assert "extra" not in result.model_dump_json()


@pytest.mark.asyncio
@pytest.mark.parametrize("location", ["top", "choice"])
@pytest.mark.parametrize("upstream_code,code,attempts", [
    (401, "PROVIDER_CONFIG", 1), (403, "PROVIDER_CONFIG", 1),
    (429, "UPSTREAM_TEMPORARY", 2), (500, "UPSTREAM_TEMPORARY", 2),
    (402, "UPSTREAM_ERROR", 1), (400, "UPSTREAM_ERROR", 1),
    ("malicious-error-code", "UPSTREAM_ERROR", 1),
])
async def test_openrouter_http_200_error_envelope_cannot_succeed_or_leak(location, upstream_code, code, attempts, caplog):
    marker = "private-error-body-marker"
    payload = completion()
    container = payload if location == "top" else payload["choices"][0]
    container["error"] = {"code": upstream_code, "message": marker, "metadata": {"raw_error": marker}}
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=payload)

    with caplog.at_level(logging.INFO, logger="just_read.providers"):
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(PipelineError) as error:
                await ModelProvider(config(), client).complete(RouterResult, "s", "u", lambda: False)
    assert error.value.code == code and len(calls) == attempts
    assert marker not in str(error.value) + caplog.text
    assert "malicious-error-code" not in str(error.value) + caplog.text
    assert "invented-router-key" not in str(error.value) + caplog.text


@pytest.mark.asyncio
async def test_openrouter_http_auth_failure_does_not_echo_error_body():
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(
            401, json={"error": {"message": "private-body invented-router-key"}}))) as client:
        with pytest.raises(PipelineError) as error:
            await ModelProvider(config(), client).complete(RouterResult, "s", "u", lambda: False)
    assert error.value.code == "PROVIDER_CONFIG"
    assert "private-body" not in str(error.value) and "invented-router-key" not in str(error.value)


@pytest.mark.asyncio
async def test_openrouter_full_brief_pipeline_preserves_sources_and_snapshot():
    workflow = Workflow(depth="brief")
    actual_calls = []

    def handler(request):
        actual_calls.append(request)
        if request.url.host == "api.tavily.com":
            return workflow(request)
        assert request.url == "https://openrouter.ai/api/v1/chat/completions"
        body = json.loads(request.content)
        assert body["provider"]["require_parameters"] is False and body["stream"] is False
        # Reuse the provider-independent evidence fixture, adapting only its wire envelope.
        fixture_request = httpx.Request("POST", "https://api.openai.com/v1/responses", json={
            "text": {"format": {"name": body["response_format"]["json_schema"]["name"]}},
            "input": deepcopy(body["messages"]),
        })
        result = workflow(fixture_request).json()
        content = result["output"][0]["content"][0]["text"]
        return httpx.Response(200, json=completion(content=content))

    progress = []

    async def record(step, _message):
        progress.append(step)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await Pipeline(config(), client).run("分析文档阅读能力", "brief", record, lambda: False)
    assert len(actual_calls) == 8 and workflow.search_calls == 2
    assert progress == [0, 1, 2]
    assert result["source"] == "generated" and "[S1]" in result["summary"]
    assert {"overview", "sources"} <= {chapter["id"] for chapter in result["chapters"]}
    snapshot = result.pop("_evidence")
    assert snapshot["sources"][0]["raw_content"] == RAW
    assert len(snapshot["plan"]["subquestions"]) == 2
    assert all(decision["supported"] for decision in snapshot["review"]["decisions"])
    Report.model_validate({**result, "id": "router-report", "createdAt": "2026-10-07T00:00:00Z", "bookmarks": []})
