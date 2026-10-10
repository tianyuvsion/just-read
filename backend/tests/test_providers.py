import asyncio
import json
from dataclasses import replace

import httpx
import pytest
from pydantic import BaseModel, ConfigDict, Field

from just_read.providers import ModelProvider, PipelineError, SearchProvider, wire_schema
from just_read.pipeline import Layout
from just_read.settings import Settings


class SmallResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    answer: str = Field(min_length=3, max_length=20)


def settings(**kwargs):
    return replace(Settings(), llm_model="server-fixed-model", llm_api_key="test-key",
                   search_api_key="search-key", **kwargs)


def openai_response(result=None, **kwargs):
    return {"status": "completed", "model": "actual-model", "output": [
        {"type": "reasoning", "summary": []},
        {"type": "message", "content": [{"type": "output_text", "text": json.dumps(result or {"answer": "valid"})}]}], **kwargs}


def test_schema_cleanup_preserves_fields_named_like_schema_keywords():
    schema = wire_schema(Layout)
    assert "title" in schema["properties"]
    assert "title" not in schema.get("required", [])
    assert "title" in schema["$defs"]["Section"]["properties"]
    assert "maxLength" not in schema["properties"]["title"]


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["openai", "anthropic"])
async def test_nonstream_structured_wire_and_actual_text_block(provider):
    calls = []

    def handler(request):
        calls.append(json.loads(request.content))
        if provider == "openai":
            assert request.url.path == "/v1/responses"
            assert calls[-1]["text"]["format"]["strict"] is False
            return httpx.Response(200, json=openai_response())
        assert request.url.path == "/v1/messages"
        assert request.headers["anthropic-version"] == "2023-06-01"
        return httpx.Response(200, json={"stop_reason": "end_turn", "content": [
            {"type": "thinking", "thinking": "not application output"},
            {"type": "text", "text": '{"answer":"valid"}'}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await ModelProvider(settings(llm_provider=provider), client).complete(
            SmallResult, "system", "user", lambda: False)
    assert result.answer == "valid"
    assert calls[0]["stream"] is False
    assert calls[0]["model"] == "server-fixed-model"
    schema = calls[0]["text"]["format"]["schema"] if provider == "openai" else calls[0]["output_config"]["format"]["schema"]
    assert schema["additionalProperties"] is False
    assert "maxLength" not in schema["properties"]["answer"]
    assert "tools" not in calls[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("provider,payload,code", [
    ("openai", openai_response(status="incomplete"), "MODEL_TRUNCATED"),
    ("openai", openai_response(output=[{"content": [{"type": "refusal", "refusal": "no"}]}]), "MODEL_REFUSED"),
    ("anthropic", {"stop_reason": "max_tokens", "content": [{"type": "text", "text": '{"answer":"valid"}'}]}, "MODEL_TRUNCATED"),
    ("anthropic", {"stop_reason": "refusal", "content": []}, "MODEL_REFUSED"),
    ("anthropic", {"stop_reason": "tool_use", "content": []}, "UPSTREAM_PROTOCOL"),
])
async def test_refusal_truncation_never_accepted_or_retried(provider, payload, code):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=payload)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(PipelineError) as error:
            await ModelProvider(settings(llm_provider=provider), client).complete(SmallResult, "s", "u", lambda: False)
    assert error.value.code == code
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_one_transient_retry_plus_one_local_schema_repair_at_most_three_calls():
    calls = []

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(503, json={"secret": "must not leak"})
        if len(calls) == 2:
            return httpx.Response(200, json=openai_response({"answer": "x"}))
        return httpx.Response(200, json=openai_response())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await ModelProvider(settings(), client).complete(SmallResult, "s", "u", lambda: False)
    assert result.answer == "valid"
    assert len(calls) == 3


@pytest.mark.asyncio
async def test_permanent_schema_violation_fails_after_one_repair():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=openai_response({"answer": "x"}))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(PipelineError, match="结构校验"):
            await ModelProvider(settings(), client).complete(SmallResult, "s", "u", lambda: False)
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_tavily_uses_raw_evidence_and_bearer_auth():
    def handler(request):
        payload = json.loads(request.content)
        assert request.url == "https://api.tavily.com/search"
        assert request.headers["authorization"] == "Bearer search-key"
        assert payload["include_raw_content"] is True
        assert payload["include_answer"] is False
        return httpx.Response(200, json={"results": [{"url": "https://example.org", "raw_content": "source"}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await SearchProvider(settings(), client).search("test query", lambda: False)
    assert result[0]["raw_content"] == "source"


@pytest.mark.asyncio
async def test_missing_key_and_cancellation_make_no_request():
    def unexpected(_):
        pytest.fail("must not issue a network request")

    async with httpx.AsyncClient(transport=httpx.MockTransport(unexpected)) as client:
        with pytest.raises(PipelineError) as error:
            await ModelProvider(Settings(), client).complete(SmallResult, "s", "u", lambda: False)
        assert error.value.code == "PROVIDER_CONFIG"
        with pytest.raises(asyncio.CancelledError):
            await ModelProvider(settings(), client).complete(SmallResult, "s", "u", lambda: True)


@pytest.mark.asyncio
async def test_auth_error_does_not_expose_provider_body_or_key():
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(
            401, json={"message": "secret provider body test-key"}))) as client:
        with pytest.raises(PipelineError) as error:
            await ModelProvider(settings(), client).complete(SmallResult, "s", "u", lambda: False)
    assert error.value.code == "PROVIDER_CONFIG"
    assert "test-key" not in str(error.value) and "secret" not in str(error.value)
