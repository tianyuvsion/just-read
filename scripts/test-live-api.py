#!/usr/bin/env python3
"""Opt-in live checks. The default only inspects this project's configuration.

Run with backend/.venv/bin/python; --full uses an isolated database and real
upstreams, while its HTTP requests are in-process through FastAPI TestClient.
"""
from __future__ import annotations

import argparse
import asyncio
from contextlib import contextmanager
from dataclasses import replace
import json
import logging
import math
import os
from pathlib import Path
import sys
import time
from typing import Literal
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import httpx
from pydantic import BaseModel, ConfigDict

from just_read.providers import ModelProvider, PipelineError, SearchProvider
from just_read.settings import Settings

DEFAULT_QUESTION = "Python asyncio 中 TaskGroup 与 gather 的异常传播和取消行为有什么区别？"
ERRORS = {
    "INVALID_ARGUMENTS": "命令参数无效，请使用 --help 查看用法。",
    "CONFIG_INVALID": "配置无效，请检查配置字段及取值。",
    "PROVIDER_CONFIG": "模型名称、密钥或供应商配置不完整或不可用。",
    "PROVIDER_NOT_CONFIGURED": "所选检索服务未配置完整，无法执行联网调研。",
    "LIVE_MODE_REQUIRED": "真实联调拒绝 fixture 供应商和 test_mode。",
    "UPSTREAM_TEMPORARY": "上游暂时不可用或超时，有限重试已结束。",
    "UPSTREAM_ERROR": "上游请求失败，请检查协议兼容性与服务配置。",
    "UPSTREAM_PROTOCOL": "上游未按要求返回完整的结构化响应。",
    "UPSTREAM_TOO_LARGE": "上游响应超过处理上限。",
    "INPUT_TOO_LARGE": "研究输入超过处理上限。",
    "MODEL_REFUSED": "模型拒绝了请求。",
    "MODEL_TRUNCATED": "模型输出被截断，未视为成功。",
    "INVALID_MODEL_OUTPUT": "模型输出未通过数据结构校验。",
    "INVALID_REQUEST": "调研问题或请求字段无效。",
    "INVALID_PLAN": "研究计划未满足要求。",
    "INSUFFICIENT_EVIDENCE": "没有取得足够的可核验正文材料。",
    "INVALID_EVIDENCE": "结论引用或数字未通过原文核验。",
    "INVALID_CHART": "图表数据未通过原文及统计口径核验。",
    "EVIDENCE_REVIEW_FAILED": "研究结论或图表未通过证据审查。",
    "INVALID_REPORT": "报告结构或证据快照未通过检查。",
    "TASK_TIMEOUT": "任务达到执行时限，未视为成功。",
    "TASK_CANCELLED": "任务已取消。",
    "HTTP_CHECK_FAILED": "本地业务 HTTP 流程检查失败。",
    "GENERATION_FAILED": "联调未完成，请检查服务配置后重试。",
    "INTERRUPTED": "联调已由用户中断。",
}


class Probe(BaseModel):
    model_config = ConfigDict(extra="forbid")
    result: Literal["ok"]


def emit(event: str, **fields) -> None:
    print(json.dumps({"event": event, **fields}, ensure_ascii=False), flush=True)


def fail(code: str) -> None:
    code = code if code in ERRORS else "GENERATION_FAILED"
    raise PipelineError(code, ERRORS[code])


class SafeArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        # argparse's default error echoes unknown arguments, which may contain
        # a credential pasted into the wrong place.
        fail("INVALID_ARGUMENTS")


def check_settings(settings: Settings, mode: str) -> None:
    settings.validate()
    if settings.test_mode or settings.llm_provider == "fixture":
        fail("LIVE_MODE_REQUIRED")
    if not settings.llm_api_key.strip() or not settings.llm_model.strip():
        fail("PROVIDER_CONFIG")
    if mode == "search" and not settings.search_ready:
        fail("PROVIDER_NOT_CONFIGURED")
    if not math.isfinite(settings.provider_timeout_seconds) or not math.isfinite(settings.task_timeout_seconds):
        fail("CONFIG_INVALID")


async def model_probe(settings: Settings, client: httpx.AsyncClient | None = None) -> dict:
    """Client injection exists for offline tests; CLI uses the real adapter."""
    check_settings(settings, "model")
    if client is None:
        async with httpx.AsyncClient() as owned:
            return await model_probe(settings, owned)
    result = await ModelProvider(settings, client).complete(
        Probe, 'Return the requested JSON object with result equal to "ok".',
        "Check structured JSON response compatibility.", lambda: False, max_tokens=1024)
    return result.model_dump()


async def search_probe(settings: Settings, client: httpx.AsyncClient | None = None) -> dict:
    """Check extractive citations rather than the model's generated answer."""
    check_settings(settings, "search")
    if client is None:
        async with httpx.AsyncClient() as owned:
            return await search_probe(settings, owned)
    from just_read.pipeline import safe_url
    results = await SearchProvider(settings, client).search(
        "Python asyncio TaskGroup gather cancellation exceptions official documentation", lambda: False)
    valid = [r for r in results if safe_url(r.get("url")) and isinstance(r.get("raw_content"), str)
             and len(r["raw_content"].strip()) >= 80]
    if not valid:
        fail("INSUFFICIENT_EVIDENCE")
    return {"search_provider": settings.effective_search_provider,
            "sources": len(valid), "excerpt_characters": sum(len(r["raw_content"]) for r in valid)}


def response_body(response) -> dict:
    try:
        body = response.json()
    except ValueError:
        fail("HTTP_CHECK_FAILED")
    if not isinstance(body, dict):
        fail("HTTP_CHECK_FAILED")
    if not response.is_success:
        error = body.get("error")
        fail(error.get("code", "HTTP_CHECK_FAILED") if isinstance(error, dict) else "HTTP_CHECK_FAILED")
    return body


@contextmanager
def isolated_app_import(database: Path):
    # app.py has a module-level app=create_app(). Keep that import-time Store
    # in the same isolated database rather than touching the user's data/.
    key = "JUSTREAD_DATABASE_PATH"
    previous = os.environ.get(key)
    os.environ[key] = str(database)
    try:
        from just_read.app import create_app
        yield create_app
    finally:
        if previous is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = previous


def full_probe(settings: Settings, question: str) -> None:
    check_settings(settings, "full")
    from fastapi.testclient import TestClient
    from just_read.models import Report, ResearchRequest, Task

    try:
        request = ResearchRequest(question=question, depth="brief")
    except ValueError:
        fail("INVALID_REQUEST")
    directory = ROOT / ".test-data"
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    database = directory / f"live-{uuid.uuid4()}.sqlite3"
    os.close(os.open(database, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600))
    live = replace(settings, database_path=str(database),
                   task_timeout_seconds=min(settings.task_timeout_seconds, 1800), workers=1)
    with isolated_app_import(database) as create_app:
        app = create_app(live)
    # HTTPS base preserves Secure-cookie configurations without opening sockets.
    with TestClient(app, base_url="https://testserver", raise_server_exceptions=False) as client:
        health = response_body(client.get("/api/v1/health"))
        if health.get("ready") is not True or health.get("mode") != "production":
            fail("PROVIDER_NOT_CONFIGURED")
        deadline = time.monotonic() + live.task_timeout_seconds
        task_id, terminal, previous_stage = None, False, None
        try:
            task = Task.model_validate(response_body(client.post("/api/v1/research-tasks",
                json=request.model_dump(), headers={"Idempotency-Key": str(uuid.uuid4())})))
            task_id = task.id
            while True:
                stage = (task.status, task.step)
                if stage != previous_stage:
                    emit("progress", task_id=task_id, status=task.status, step=task.step)
                    previous_stage = stage
                if task.status in {"succeeded", "failed", "cancelled"}:
                    terminal = True
                    break
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    fail("TASK_TIMEOUT")
                time.sleep(min(1, remaining))
                task = Task.model_validate(response_body(client.get(f"/api/v1/research-tasks/{task_id}")))
                if task.id != task_id:
                    fail("HTTP_CHECK_FAILED")
            if task.status == "cancelled":
                fail("TASK_CANCELLED")
            if task.status == "failed":
                fail(task.error.code if task.error else "GENERATION_FAILED")
            if task.report is None or task.step != 3:
                fail("INVALID_REPORT")
            saved = response_body(client.get(f"/api/v1/reports/{task.report.id}"))
            report = Report.model_validate(saved)
            if report.source != "generated" or report.id != task.report.id:
                fail("INVALID_REPORT")
            if not {"overview", "sources"} <= {chapter.id for chapter in report.chapters}:
                fail("INVALID_REPORT")
            if "_evidence" in saved or "raw_content" in saved:
                fail("INVALID_REPORT")
            with app.state.store.connect() as db:
                row = db.execute("SELECT content FROM report_artifacts WHERE report_id=?", (report.id,)).fetchone()
            if row is None:
                fail("INVALID_REPORT")
            evidence = json.loads(row["content"])
            required = {"plan", "sources", "findings", "gaps", "data", "review", "layout"}
            if not isinstance(evidence, dict) or not required <= evidence.keys():
                fail("INVALID_REPORT")
            sources = evidence["sources"]
            if any(not s.get("url") or not s.get("timestamp") for s in sources):
                fail("INVALID_REPORT")
            emit("full_ok", task_id=task.id, report_id=report.id, source=report.source,
                 validation_mode=evidence.get("validation", {}).get("mode"),
                 warnings=len(evidence.get("validation", {}).get("warnings", [])),
                 chapters=len(report.chapters), evidence_counts={
                     "sources": len(sources), "findings": len(evidence["findings"]),
                     "gaps": len(evidence["gaps"]), "data": len(evidence["data"]),
                     "reviews": len(evidence["review"]["decisions"])},
                 database=str(database.relative_to(ROOT)))
        finally:
            if task_id and not terminal:
                try:
                    client.post(f"/api/v1/research-tasks/{task_id}/cancel")
                except Exception:
                    pass  # TestClient lifespan still stops the engine.


def main(argv: list[str] | None = None) -> int:
    parser = SafeArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check-config", dest="mode", action="store_const", const="check",
                      help="only inspect configuration (default; no network)")
    mode.add_argument("--model-only", dest="mode", action="store_const", const="model",
                      help="opt in to a small real structured model request")
    mode.add_argument("--search-only", dest="mode", action="store_const", const="search",
                      help="opt in to one real search and verify extractive source content")
    mode.add_argument("--full", dest="mode", action="store_const", const="full",
                      help="opt in to one real brief research task with an isolated database")
    parser.set_defaults(mode="check")
    parser.add_argument("--question", default=DEFAULT_QUESTION, help="research question for --full")
    previous_logging = logging.root.manager.disable
    logging.disable(logging.CRITICAL)
    try:
        args = parser.parse_args(argv)
        try:
            settings = Settings.from_env()
        except (ValueError, OSError):
            fail("CONFIG_INVALID")
        if args.mode == "check":
            emit("config", provider=settings.llm_provider,
                 model_configured=bool(settings.llm_model.strip()),
                 llm_key_configured=bool(settings.llm_api_key.strip()),
                 search_key_configured=bool(settings.search_api_key.strip()),
                 search_provider=settings.effective_search_provider, search_ready=settings.search_ready,
                 ready=settings.ready)
        check_settings(settings, args.mode)
        if args.mode == "model":
            asyncio.run(model_probe(settings))
            emit("model_ok", provider=settings.llm_provider, model_configured=True)
        elif args.mode == "search":
            emit("search_ok", **asyncio.run(search_probe(settings)))
        elif args.mode == "full":
            full_probe(settings, args.question)
        return 0
    except KeyboardInterrupt:
        emit("error", code="INTERRUPTED", message=ERRORS["INTERRUPTED"])
        return 130
    except PipelineError as exc:
        code = exc.code if exc.code in ERRORS else "GENERATION_FAILED"
        emit("error", code=code, message=ERRORS[code])
        return 2 if code in {"INVALID_ARGUMENTS", "CONFIG_INVALID", "PROVIDER_CONFIG", "PROVIDER_NOT_CONFIGURED", "LIVE_MODE_REQUIRED"} else 1
    except Exception:
        emit("error", code="GENERATION_FAILED", message=ERRORS["GENERATION_FAILED"])
        return 1
    finally:
        logging.disable(previous_logging)


if __name__ == "__main__":
    raise SystemExit(main())
