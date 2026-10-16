import asyncio
import json
import logging
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
import httpx
from pydantic import BaseModel, ConfigDict, Field

from just_read.settings import Settings
from .chat_templates import complete_html, get_template, template_context, templates

logger = logging.getLogger("just_read.chat")


class ChatMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: Literal["system", "user", "assistant"]
    content: str = Field(min_length=1, max_length=250000)


class ChatStreamRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    messages: list[ChatMessage] = Field(min_length=1, max_length=100)
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    max_tokens: int | None = Field(default=None, ge=1, le=32000)
    template_id: str | None = Field(default=None, max_length=64)
    template_prompt: str | None = Field(default=None, min_length=1, max_length=12000)
    template_reference: bool = True


async def stream_chat_events(request: Request, settings: Settings, body: ChatStreamRequest):
    # 1. 针对测试环境或 fixture 的离线模拟支持
    if settings.test_mode and settings.llm_provider == "fixture":
        model_name = settings.llm_model or "Kimi-K3"
        tokens = ["你好！", "我是", "基于 ", model_name, " 的 AI 对话助手。", " 请问有什么", "可以协助您的吗？"]
        for token in tokens:
            if await request.is_disconnected():
                break
            await asyncio.sleep(settings.fixture_delay_seconds)
            yield f"data: {json.dumps({'content': token}, ensure_ascii=False)}\n\n"
        yield "data: [DONE]\n\n"
        return

    # 2. 真实 OpenRouter / 兼容 API 流式转发
    base = (settings.llm_base_url or "https://openrouter.ai/api/v1").rstrip("/")
    url = f"{base}/chat/completions"
    model = settings.llm_model or "moonshotai/kimi-k3"

    headers = {
        "Authorization": f"Bearer {settings.llm_api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://justread.local",
        "X-Title": "JustREAD AI Chat",
    }
    template = get_template(body.template_id) if body.template_id else None
    context = template_context(template, body.template_prompt, body.template_reference) if template else []
    payload = {
        "model": model,
        "messages": context + [m.model_dump() for m in body.messages],
        "stream": True,
        "temperature": body.temperature,
        "max_tokens": body.max_tokens or (24000 if template else 4000),
    }
    # Kimi K3 defaults to maximum reasoning effort on OpenRouter. Its thinking
    # shares the completion budget, so a long reference can exhaust it before HTML.
    if settings.llm_provider == "openrouter" and model == "moonshotai/kimi-k3":
        payload["reasoning"] = {"effort": "low"}

    timeout = httpx.Timeout(connect=15.0, read=120.0, write=15.0, pool=15.0)
    combined = ""
    empty_retried = False
    output_rounds = 0
    yield 'event: status\ndata: {"status":"preparing"}\n\n'
    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            async with asyncio.timeout(600):
                for attempt in range(4 if template else 2):
                    if await request.is_disconnected():
                        return
                    part = ""
                    finish_reason = None
                    usage = {}
                    generation_id = None
                    provider = None
                    thinking = False
                    saw_done = False
                    async with client.stream("POST", url, headers=headers, json=payload) as upstream:
                        if upstream.status_code != 200:
                            yield f"event: error\ndata: {json.dumps({'code': 'UPSTREAM_ERROR', 'message': f'模型服务响应异常（HTTP {upstream.status_code}），请稍后重试。'}, ensure_ascii=False)}\n\n"
                            return
                        async for raw_line in upstream.aiter_lines():
                            if await request.is_disconnected():
                                return
                            line = raw_line.strip()
                            if not line.startswith("data:"):
                                continue
                            data_str = line[5:].strip()
                            if data_str == "[DONE]":
                                saw_done = True
                                break
                            try:
                                chunk = json.loads(data_str)
                            except json.JSONDecodeError:
                                continue
                            if "error" in chunk:
                                logger.warning("chat_upstream_error id=%s", generation_id)
                                yield f"event: error\ndata: {json.dumps({'code': 'MODEL_ERROR', 'message': '模型生成中断，请重试。'}, ensure_ascii=False)}\n\n"
                                return
                            choices = chunk.get("choices", [])
                            generation_id = chunk.get("id") or generation_id
                            provider = chunk.get("provider") or provider
                            usage = chunk.get("usage") or usage
                            choice = choices[0] if choices else {}
                            finish_reason = choice.get("finish_reason") or finish_reason
                            delta = choice.get("delta") or {}
                            if not thinking and not part and any(delta.get(key) for key in ("reasoning", "reasoning_content", "reasoning_details")):
                                thinking = True
                                yield 'event: status\ndata: {"status":"thinking"}\n\n'
                            content = delta.get("content")
                            if isinstance(content, str) and content:
                                part += content
                                combined += content
                                yield f"data: {json.dumps({'content': content}, ensure_ascii=False)}\n\n"
                    details = usage.get("completion_tokens_details") or {}
                    log_round = logger.warning if not part.strip() or finish_reason in {"length", "error", "content_filter"} else logger.info
                    log_round(
                        "chat_round id=%s provider=%s model=%s template=%s attempt=%s finish=%s chars=%s completion_tokens=%s reasoning_tokens=%s",
                        generation_id, provider, model, body.template_id, attempt + 1,
                        finish_reason, len(part), usage.get("completion_tokens"), details.get("reasoning_tokens"),
                    )
                    if finish_reason in {"error", "content_filter", "tool_calls"}:
                        yield 'event: error\ndata: {"code":"MODEL_ERROR","message":"模型未正常完成正文输出，请重试。"}\n\n'
                        return
                    if not saw_done and finish_reason is None:
                        yield 'event: error\ndata: {"code":"UPSTREAM_INTERRUPTED","message":"模型数据流在完成前中断，已保留收到的内容，请重试。"}\n\n'
                        return
                    if not part.strip():
                        # Retry an empty completion once, without adding an empty
                        # assistant turn or pretending there is HTML to continue.
                        can_retry = not empty_retried and (finish_reason != "length" or body.max_tokens is None)
                        if can_retry:
                            empty_retried = True
                            if finish_reason == "length":
                                payload["max_tokens"] = min(payload["max_tokens"] * 2, 32000)
                            yield 'event: status\ndata: {"status":"retrying"}\n\n'
                            continue
                        code = "OUTPUT_BUDGET_EXHAUSTED" if finish_reason == "length" else "EMPTY_RESPONSE"
                        message = "模型用完了生成额度，但未产出正文。请减少章节或缩短输入后重试。" if finish_reason == "length" else "模型未返回正文，自动重试后仍未成功，请稍后再试。"
                        yield f"event: error\ndata: {json.dumps({'code': code, 'message': message}, ensure_ascii=False)}\n\n"
                        return
                    output_rounds += 1
                    if not template or complete_html(combined):
                        if not template and finish_reason == "length":
                            yield 'event: error\ndata: {"code":"OUTPUT_INCOMPLETE","message":"回答达到生成长度上限，已保留收到的内容。"}\n\n'
                            return
                        yield "data: [DONE]\n\n"
                        return
                    if output_rounds >= 3:
                        break
                    payload["messages"].extend([
                        {"role": "assistant", "content": part},
                        {"role": "user", "content": "上一次 HTML 尚未完成。严格从上一次最后一个字符继续，补齐剩余正文、脚本和结束标签，不重复已有内容，不加解释或代码围栏。"},
                    ])
                    yield f"event: status\ndata: {json.dumps({'status': 'continuing', 'round': output_rounds + 1})}\n\n"
                yield f"event: error\ndata: {json.dumps({'code': 'OUTPUT_INCOMPLETE', 'message': '已完成三段输出，网页仍未完整。已保留收到的代码，请缩短报告或减少章节后重新生成。'}, ensure_ascii=False)}\n\n"
        except (httpx.TimeoutException, TimeoutError):
            yield f"event: error\ndata: {json.dumps({'code': 'UPSTREAM_TIMEOUT', 'message': '生成超时，已保留收到的内容，请重试。'}, ensure_ascii=False)}\n\n"
        except httpx.HTTPError:
            yield f"event: error\ndata: {json.dumps({'code': 'NETWORK_ERROR', 'message': '与模型服务的连接中断，请重试。'}, ensure_ascii=False)}\n\n"


def register_chat_routes(app: FastAPI, settings: Settings):
    @app.get("/api/v1/chat/templates", tags=["chat"])
    async def list_templates():
        return {"templates": templates()}

    @app.post("/api/v1/chat/stream", tags=["chat"])
    async def chat_stream(request: Request, body: ChatStreamRequest):
        if body.template_id and get_template(body.template_id) is None:
            raise HTTPException(status_code=422, detail="Unknown template")
        return StreamingResponse(
            stream_chat_events(request, settings, body),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )
