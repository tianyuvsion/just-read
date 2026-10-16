import asyncio
import json
import logging
from typing import Literal

from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse
import httpx
from pydantic import BaseModel, ConfigDict, Field

from just_read.settings import Settings

logger = logging.getLogger("just_read.chat")


class ChatMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: Literal["system", "user", "assistant"]
    content: str = Field(min_length=1, max_length=60000)


class ChatStreamRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    messages: list[ChatMessage] = Field(min_length=1, max_length=100)
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    max_tokens: int = Field(default=4000, ge=1, le=8000)


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
    payload = {
        "model": model,
        "messages": [m.model_dump() for m in body.messages],
        "stream": True,
        "temperature": body.temperature,
        "max_tokens": body.max_tokens,
    }

    timeout = httpx.Timeout(connect=15.0, read=120.0, write=15.0, pool=15.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            async with client.stream("POST", url, headers=headers, json=payload) as upstream:
                if upstream.status_code != 200:
                    err_bytes = await upstream.aread()
                    err_msg = f"上游接口响应异常 (HTTP {upstream.status_code})"
                    try:
                        err_json = json.loads(err_bytes)
                        if "error" in err_json:
                            err_info = err_json["error"]
                            err_msg = err_info.get("message") if isinstance(err_info, dict) else str(err_info)
                    except Exception:
                        pass
                    yield f"event: error\ndata: {json.dumps({'code': 'UPSTREAM_ERROR', 'message': err_msg}, ensure_ascii=False)}\n\n"
                    return

                async for raw_line in upstream.aiter_lines():
                    if await request.is_disconnected():
                        logger.info("客户端连接已断开，停止拉取上游数据流")
                        break

                    line = raw_line.strip()
                    if not line or line.startswith(":"):
                        continue

                    if line.startswith("data: "):
                        data_str = line[6:].strip()
                        if data_str == "[DONE]":
                            yield "data: [DONE]\n\n"
                            break

                        try:
                            chunk = json.loads(data_str)
                            if "error" in chunk:
                                err_info = chunk["error"]
                                err_text = err_info.get("message") if isinstance(err_info, dict) else str(err_info)
                                yield f"event: error\ndata: {json.dumps({'code': 'MODEL_ERROR', 'message': err_text}, ensure_ascii=False)}\n\n"
                                break

                            choices = chunk.get("choices", [])
                            if choices:
                                delta = choices[0].get("delta", {})
                                content = delta.get("content")
                                if content:
                                    yield f"data: {json.dumps({'content': content}, ensure_ascii=False)}\n\n"
                        except json.JSONDecodeError:
                            continue

        except httpx.TimeoutException:
            yield f"event: error\ndata: {json.dumps({'code': 'UPSTREAM_TIMEOUT', 'message': '模型响应超时，请重试'}, ensure_ascii=False)}\n\n"
        except (httpx.NetworkError, httpx.HTTPError) as exc:
            yield f"event: error\ndata: {json.dumps({'code': 'NETWORK_ERROR', 'message': f'与大模型服务通信中断: {str(exc)}'}, ensure_ascii=False)}\n\n"


def register_chat_routes(app: FastAPI, settings: Settings):
    @app.post("/api/v1/chat/stream", tags=["chat"])
    async def chat_stream(request: Request, body: ChatStreamRequest):
        return StreamingResponse(
            stream_chat_events(request, settings, body),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )
