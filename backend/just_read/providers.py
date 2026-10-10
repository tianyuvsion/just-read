"""Bounded, non-streaming provider adapters. No client-selected models or tools."""
from __future__ import annotations

import asyncio
import json
import logging
import math
import time
from copy import deepcopy
from typing import Callable, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

logger = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)
CancelCheck = Callable[[], bool]
MAX_RESPONSE_BYTES = 2_000_000
MAX_INPUT_CHARS = 160_000


class PipelineError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def checkpoint(cancelled: CancelCheck) -> None:
    if cancelled():
        raise asyncio.CancelledError


def wire_schema(model: type[BaseModel]) -> dict:
    """Keep schema field types without adding required business fields."""
    schema = deepcopy(model.model_json_schema())

    def clean(node):
        if isinstance(node, dict):
            for key in ("minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum",
                        "minLength", "maxLength", "minItems", "maxItems", "pattern", "default", "title"):
                node.pop(key, None)
            for key, value in node.items():
                # Property names such as "title" are not schema keywords.
                if key in {"properties", "$defs", "definitions"} and isinstance(value, dict):
                    for child in value.values():
                        clean(child)
                else:
                    clean(value)
        elif isinstance(node, list):
            for value in node:
                clean(value)

    clean(schema)
    return schema


async def post_json(client: httpx.AsyncClient, url: str, *, headers: dict,
                    payload: dict, timeout: float, cancelled: CancelCheck) -> dict:
    """Read one complete JSON response with a byte cap, never an SSE request."""
    checkpoint(cancelled)
    try:
        async with client.stream("POST", url, headers=headers, json=payload,
                                 timeout=timeout, follow_redirects=False) as response:
            if response.status_code == 429 or response.status_code >= 500:
                raise PipelineError("UPSTREAM_TEMPORARY", "上游服务暂时不可用，请稍后重试。")
            if response.status_code in (401, 403):
                raise PipelineError("PROVIDER_CONFIG", "上游服务凭证或权限配置不正确。")
            if not response.is_success:
                raise PipelineError("UPSTREAM_ERROR", "上游服务拒绝了请求，请检查服务端配置。")
            body = bytearray()
            async for chunk in response.aiter_bytes():
                checkpoint(cancelled)
                body.extend(chunk)
                if len(body) > MAX_RESPONSE_BYTES:
                    raise PipelineError("UPSTREAM_TOO_LARGE", "上游响应超出处理上限。")
    except (httpx.TimeoutException, httpx.NetworkError) as exc:
        raise PipelineError("UPSTREAM_TEMPORARY", "上游请求超时或网络暂时不可用。") from exc
    except httpx.HTTPError as exc:
        raise PipelineError("UPSTREAM_ERROR", "上游请求未能完成。") from exc
    checkpoint(cancelled)
    try:
        data = json.loads(body)
    except (ValueError, UnicodeDecodeError) as exc:
        raise PipelineError("UPSTREAM_PROTOCOL", "上游未返回有效的 JSON。") from exc
    if not isinstance(data, dict):
        raise PipelineError("UPSTREAM_PROTOCOL", "上游响应格式不正确。")
    return data


def openrouter_message(data: dict) -> dict:
    """Validate the completed envelope without trusting generated text or errors."""
    errors = [data.get("error")]
    choices = data.get("choices")
    if isinstance(choices, list):
        errors.extend(choice.get("error") for choice in choices if isinstance(choice, dict))
    for error in errors:
        if error is None:
            continue
        code = error.get("code") if isinstance(error, dict) else None
        if code in (401, 403):
            raise PipelineError("PROVIDER_CONFIG", "上游服务凭证或权限配置不正确。")
        if code == 429 or isinstance(code, int) and 500 <= code <= 599:
            raise PipelineError("UPSTREAM_TEMPORARY", "上游服务暂时不可用，请稍后重试。")
        raise PipelineError("UPSTREAM_ERROR", "上游服务拒绝了请求，请检查服务端配置。")
    if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict):
        raise PipelineError("UPSTREAM_PROTOCOL", "模型响应缺少唯一的输出内容。")
    choice = choices[0]
    message = choice.get("message")
    reason = choice.get("finish_reason")
    if reason in ("content_filter", "refusal") or (isinstance(message, dict) and message.get("refusal")):
        raise PipelineError("MODEL_REFUSED", "模型拒绝处理此研究请求。")
    if reason == "length":
        raise PipelineError("MODEL_TRUNCATED", "模型响应被截断，未生成完整报告。")
    if reason != "stop" or not isinstance(message, dict) or message.get("tool_calls"):
        raise PipelineError("UPSTREAM_PROTOCOL", "模型响应未正常完成。")
    return message


class ModelProvider:
    def __init__(self, settings, client: httpx.AsyncClient, usage_recorder=None):
        self.settings = settings
        self.client = client
        self.usage_recorder = usage_recorder

    async def complete(self, schema: type[T], system: str, user: str,
                       cancelled: CancelCheck, *, max_tokens: int = 7000,
                       validator: Callable[[T], None] | None = None) -> T:
        s = self.settings
        if s.llm_provider not in {"openai", "anthropic", "openrouter"} or not s.llm_api_key or not s.llm_model:
            raise PipelineError("PROVIDER_CONFIG", "请先配置模型供应商、模型名称和 API Key。")
        if len(system) + len(user) > MAX_INPUT_CHARS:
            raise PipelineError("INPUT_TOO_LARGE", "研究材料超出单次模型输入上限。")
        transient_left, repair_left = 1, 1
        repair_message = ""
        # One transport retry and one shared structure/evidence repair: three calls.
        for attempt in range(3):
            checkpoint(cancelled)
            prompt = system + repair_message
            start = time.monotonic()
            usage_recorded = False
            try:
                data = await self._request(schema, prompt, user, cancelled, max_tokens)
                await record_usage(self.usage_recorder, data, s.llm_provider, s.llm_model, schema.__name__, start, attempt + 1)
                usage_recorded = True
                logger.info("model_response", extra={
                    "provider": s.llm_provider, "model": data.get("model", s.llm_model),
                    "elapsed_ms": round((time.monotonic() - start) * 1000),
                    "usage": data.get("usage"), "attempt": attempt + 1,
                })
                text = self._extract(data)
                try:
                    # Common Markdown wrappers do not turn usable JSON into a failed task.
                    text = text.strip()
                    if text.startswith("```") and text.endswith("```"):
                        text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
                    result = schema.model_validate_json(text, strict=False)
                except (ValidationError, ValueError) as exc:
                    raise PipelineError("INVALID_MODEL_OUTPUT", "模型输出未通过结构校验。") from exc
                if validator is not None:
                    validator(result)
                return result
            except PipelineError as exc:
                if not usage_recorded:
                    await record_usage(self.usage_recorder, {}, s.llm_provider, s.llm_model, schema.__name__,
                                       start, attempt + 1, error_code=exc.code)
                logger.warning("model_call_failed", extra={"provider": s.llm_provider,
                    "model": s.llm_model, "error_code": exc.code, "attempt": attempt + 1,
                    "elapsed_ms": round((time.monotonic() - start) * 1000)})
                if exc.code == "UPSTREAM_TEMPORARY" and transient_left:
                    transient_left -= 1
                    await asyncio.sleep(0.15)
                    continue
                if exc.code in {"INVALID_MODEL_OUTPUT", "INVALID_EVIDENCE", "INVALID_CHART"} and repair_left:
                    repair_left -= 1
                    repair_message = ("\n前次响应未能解析，请返回符合字段类型的完整 JSON 对象。"
                                      "不确定或无法提供的可选内容可以省略或使用空数组。")
                    continue
                raise
        raise PipelineError("UPSTREAM_ERROR", "模型请求已达到重试上限。")

    async def _request(self, schema, system, user, cancelled, max_tokens):
        s = self.settings
        if s.llm_provider == "openai":
            base = s.llm_base_url or "https://api.openai.com/v1"
            url = base.rstrip("/") + "/responses"
            headers = {"Authorization": f"Bearer {s.llm_api_key}"}
            payload = {"model": s.llm_model, "stream": False, "store": False,
                "max_output_tokens": max_tokens, "truncation": "disabled",
                "input": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                "text": {"format": {"type": "json_schema", "name": schema.__name__,
                    "strict": False, "schema": wire_schema(schema)}}}
        elif s.llm_provider == "openrouter":
            base = s.llm_base_url or "https://openrouter.ai/api/v1"
            url = base.rstrip("/") + "/chat/completions"
            headers = {"Authorization": f"Bearer {s.llm_api_key}"}
            payload = {"model": s.llm_model, "stream": False, "max_tokens": max_tokens,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                "response_format": {"type": "json_schema", "json_schema": {
                    "name": schema.__name__, "strict": False, "schema": wire_schema(schema)}},
                "provider": {"require_parameters": False}}
        else:
            base = s.llm_base_url or "https://api.anthropic.com/v1"
            url = base.rstrip("/") + "/messages"
            headers = {"x-api-key": s.llm_api_key, "anthropic-version": "2023-06-01"}
            payload = {"model": s.llm_model, "stream": False, "max_tokens": max_tokens,
                "system": system, "messages": [{"role": "user", "content": user}],
                "output_config": {"format": {"type": "json_schema", "schema": wire_schema(schema)}}}
        data = await post_json(self.client, url, headers=headers, payload=payload,
                               timeout=s.provider_timeout_seconds, cancelled=cancelled)
        # Validate HTTP 200 errors/completion before logging response metadata.
        if s.llm_provider == "openrouter":
            openrouter_message(data)
        return data

    def _extract(self, data: dict) -> str:
        if self.settings.llm_provider == "openai":
            output = data.get("output")
            if not isinstance(output, list):
                raise PipelineError("UPSTREAM_PROTOCOL", "模型响应缺少输出内容。")
            blocks = [c for item in output if isinstance(item, dict)
                      and isinstance(item.get("content"), list) for c in item["content"] if isinstance(c, dict)]
            if any(b.get("type") == "refusal" for b in blocks):
                raise PipelineError("MODEL_REFUSED", "模型拒绝处理此研究请求。")
            if data.get("status") == "incomplete":
                raise PipelineError("MODEL_TRUNCATED", "模型响应被截断，未生成完整报告。")
            if data.get("status") != "completed":
                raise PipelineError("UPSTREAM_PROTOCOL", "模型响应未正常完成。")
            texts = [b.get("text") for b in blocks if b.get("type") == "output_text"]
        elif self.settings.llm_provider == "openrouter":
            message = openrouter_message(data)
            if not isinstance(message.get("content"), str):
                raise PipelineError("UPSTREAM_PROTOCOL", "模型响应内容格式不正确。")
            texts = [message["content"]]
        else:
            reason = data.get("stop_reason")
            if reason == "refusal":
                raise PipelineError("MODEL_REFUSED", "模型拒绝处理此研究请求。")
            if reason in {"max_tokens", "model_context_window_exceeded"}:
                raise PipelineError("MODEL_TRUNCATED", "模型响应被截断，未生成完整报告。")
            if reason != "end_turn":
                raise PipelineError("UPSTREAM_PROTOCOL", "模型响应未正常完成。")
            blocks = data.get("content", [])
            if not isinstance(blocks, list):
                raise PipelineError("UPSTREAM_PROTOCOL", "模型响应内容格式不正确。")
            texts = [b.get("text") for b in blocks if isinstance(b, dict) and b.get("type") == "text"]
        if len(texts) != 1 or not isinstance(texts[0], str) or not texts[0].strip():
            raise PipelineError("INVALID_MODEL_OUTPUT", "模型没有返回唯一的结构化结果。")
        return texts[0]


class SearchProvider:
    def __init__(self, settings, client: httpx.AsyncClient, usage_recorder=None):
        self.settings, self.client = settings, client
        self.usage_recorder = usage_recorder

    async def _fetch(self, url, *, headers, payload, timeout, cancelled, attempt):
        started = time.monotonic()
        s = self.settings
        model = s.llm_model if s.effective_search_provider == "openrouter" else "search"
        try:
            result = await post_json(self.client, url, headers=headers, payload=payload, timeout=timeout, cancelled=cancelled)
        except PipelineError as exc:
            await record_usage(self.usage_recorder, {}, s.effective_search_provider, model, "search", started, attempt, error_code=exc.code)
            raise
        await record_usage(self.usage_recorder, result, s.effective_search_provider, model, "search", started, attempt)
        return result

    async def search(self, query: str, cancelled: CancelCheck) -> list[dict]:
        s = self.settings
        if not s.search_ready:
            raise PipelineError("PROVIDER_CONFIG", "请先配置所选搜索服务的凭证。")
        if not query.strip() or len(query) > 600:
            raise PipelineError("INVALID_PLAN", "搜索计划超出处理范围。")
        for attempt in range(2):
            try:
                if s.effective_search_provider == "openrouter":
                    return await self._openrouter(query, cancelled, attempt + 1)
                data = await self._fetch(s.search_base_url.rstrip("/") + "/search",
                    headers={"Authorization": f"Bearer {s.search_api_key}"},
                    payload={"query": query, "search_depth": "advanced", "topic": "general",
                        "max_results": s.max_sources, "include_raw_content": True,
                        "include_answer": False, "include_images": False},
                    timeout=s.provider_timeout_seconds, cancelled=cancelled, attempt=attempt + 1)
                results = data.get("results")
                if not isinstance(results, list):
                    raise PipelineError("UPSTREAM_PROTOCOL", "搜索服务没有返回资料列表。")
                return [r for r in results[:s.max_sources] if isinstance(r, dict)]
            except PipelineError as exc:
                if exc.code != "UPSTREAM_TEMPORARY" or attempt:
                    raise
                await asyncio.sleep(0.15)
        raise PipelineError("UPSTREAM_ERROR", "搜索服务请求失败。")

    async def _openrouter(self, query: str, cancelled: CancelCheck, attempt=1) -> list[dict]:
        s = self.settings
        base = s.llm_base_url or "https://openrouter.ai/api/v1"
        data = await self._fetch(base.rstrip("/") + "/chat/completions",
            headers={"Authorization": f"Bearer {s.llm_api_key}"},
            payload={"model": s.llm_model, "stream": False, "max_tokens": 1024,
                "messages": [
                    {"role": "system", "content": "Use web search exactly once for the supplied query. Cite the returned sources. Keep the response concise; do not answer from memory."},
                    {"role": "user", "content": query}],
                "tools": [{"type": "openrouter:web_search", "parameters": {
                    "engine": "exa", "max_results": s.max_sources,
                    "max_total_results": s.max_sources, "max_uses": 1, "max_characters": 4000}}],
                "max_tool_calls": 1},
            timeout=s.provider_timeout_seconds, cancelled=cancelled, attempt=attempt)
        message = openrouter_message(data)
        usage = data.get("usage")
        tool_usage = usage.get("server_tool_use") if isinstance(usage, dict) else None
        count = tool_usage.get("web_search_requests") if isinstance(tool_usage, dict) else None
        if count is not None:
            if type(count) is not int or not 0 <= count <= 1:
                raise PipelineError("UPSTREAM_PROTOCOL", "检索工具返回的调用次数不符合预算。")
            if count == 0:
                return []
        annotations = message.get("annotations", [])
        if not isinstance(annotations, list):
            raise PipelineError("UPSTREAM_PROTOCOL", "检索工具返回的来源格式不正确。")
        results, seen = [], set()
        for annotation in annotations:
            checkpoint(cancelled)
            if len(results) >= s.max_sources:
                break
            if not isinstance(annotation, dict) or annotation.get("type") != "url_citation":
                continue
            citation = annotation.get("url_citation")
            if not isinstance(citation, dict):
                continue
            url, raw = citation.get("url"), citation.get("content")
            if not isinstance(url, str) or not url or url in seen:
                continue
            seen.add(url)
            # content is provider extractive evidence; assistant content is ignored.
            raw = raw.strip() if isinstance(raw, str) else ""
            results.append({"url": url, "title": citation.get("title") or url,
                "raw_content": raw[:4000], "content_kind": "extractive_excerpt" if raw else "metadata_only",
                "retrieval_provider": "openrouter", "retrieval_engine": "exa", "query": query})
        return results


async def record_usage(recorder, data, provider, model, stage, started, attempt, error_code=None):
    """Persist only allowlisted counters; never credentials, prompts or response text."""
    if recorder is None:
        return
    usage = data.get("usage") if isinstance(data, dict) else None
    usage = usage if isinstance(usage, dict) else {}
    event = {"provider": provider, "model": model, "stage": stage, "attempt": attempt,
             "elapsed_ms": round((time.monotonic() - started) * 1000), "requests": 1}
    if error_code:
        event["error_code"] = error_code
    for target, candidates in {"input_tokens": ["input_tokens", "prompt_tokens"],
                               "output_tokens": ["output_tokens", "completion_tokens"],
                               "total_tokens": ["total_tokens"], "cost": ["cost"]}.items():
        for key in candidates:
            value = usage.get(key)
            if type(value) in (int, float) and value >= 0 and math.isfinite(value):
                event[target] = value
                break
    await recorder(event)
