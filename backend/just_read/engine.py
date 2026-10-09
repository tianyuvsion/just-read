import asyncio
import logging
import inspect
import time
import uuid
from .models import Report
from .storage import Store, iso
from .settings import Settings

logger = logging.getLogger("justread")


class Engine:
    def __init__(self, store: Store, settings: Settings, pipeline_factory=None):
        self.store, self.settings = store, settings
        if pipeline_factory is None:
            from .pipeline import Pipeline
            pipeline_factory = lambda: Pipeline(settings)
        self.pipeline_factory = pipeline_factory
        self.workers = []
        self.running: dict[str, asyncio.Task] = {}
        self.wake = asyncio.Event()
        self.database_failures = 0
        self.last_runtime_error = None

    def runtime(self):
        alive = sum(not task.done() for task in self.workers)
        return {"workers_expected": self.settings.workers, "workers_alive": alive,
                "running_tasks": len(self.running), "healthy": alive == self.settings.workers and self.last_runtime_error is None,
                "database_failures": self.database_failures, "last_error": self.last_runtime_error}

    async def start(self):
        self.store.recover()
        self.workers = [asyncio.create_task(self.worker()) for _ in range(self.settings.workers)]

    async def stop(self):
        for task in self.workers + list(self.running.values()):
            task.cancel()
        await asyncio.gather(*self.workers, *self.running.values(), return_exceptions=True)

    def notify(self):
        self.wake.set()

    def cancel(self, task_id: str):
        task = self.running.get(task_id)
        if task:
            task.cancel()

    async def worker(self):
        while True:
            try:
                job = self.store.claim_next()
                self.last_runtime_error = None
            except asyncio.CancelledError:
                raise
            except Exception:
                self.database_failures += 1
                self.last_runtime_error = "STORE_UNAVAILABLE"
                logger.warning("Worker store temporarily unavailable")
                await asyncio.sleep(.25)
                continue
            if job is None:
                self.wake.clear()
                try:
                    await asyncio.wait_for(self.wake.wait(), timeout=1)
                except asyncio.TimeoutError:
                    pass
                continue
            task = asyncio.create_task(self.run(job))
            self.running[job["id"]] = task
            try:
                await task
            except asyncio.CancelledError:
                # A cancelled task must not terminate its worker. Shutdown does.
                if asyncio.current_task().cancelling():
                    raise
            except Exception:
                # Store failures while committing a run must not kill the worker.
                self.database_failures += 1
                self.last_runtime_error = "STORE_UNAVAILABLE"
                logger.warning("Worker could not finalize a task")
                token = self.store.begin_execution(job) if hasattr(self.store, "begin_execution") else None
                try:
                    while True:
                        await asyncio.sleep(.25)
                        try:
                            self.store.fail(job["id"], "STORE_UNAVAILABLE", "保存失败，可从已保存阶段重试")
                            break
                        except asyncio.CancelledError:
                            raise
                        except Exception:
                            continue
                finally:
                    if token is not None:
                        self.store.end_execution(token)
            finally:
                # A paused task may already have been resumed by another worker.
                if self.running.get(job["id"]) is task:
                    self.running.pop(job["id"], None)

    async def run(self, job: dict):
        task_id = job["id"]
        token = self.store.begin_execution(job) if hasattr(self.store, "begin_execution") else None
        async def progress(step: int, message: str):
            if self.store.is_cancelled(task_id):
                raise asyncio.CancelledError
            self.store.progress(task_id, step, message)

        try:
            remaining = job["deadline"] - time.time()
            if remaining <= 0:
                raise asyncio.TimeoutError
            async with asyncio.timeout(remaining):
                pipeline = self.pipeline_factory()
                options = {}
                if "context" in inspect.signature(pipeline.run).parameters:
                    context = {"spec": job["request"], "source_materials": []}
                    if hasattr(self.store, "materials_for_task"):
                        context["source_materials"] = self.store.materials_for_task(job["owner"], job["request"].get("source_ids", []))
                    if hasattr(self.store, "load_checkpoint"):
                        async def load_checkpoint(name):
                            return self.store.load_checkpoint(task_id, job["owner"], name)
                        context["load_checkpoint"] = load_checkpoint
                    if hasattr(self.store, "save_checkpoint"):
                        async def save_checkpoint(name, data):
                            self.store.save_checkpoint(task_id, job["owner"], name, data)
                        context["save_checkpoint"] = save_checkpoint
                    if hasattr(self.store, "record_usage"):
                        async def record_usage(event):
                            self.store.record_usage(task_id, job["owner"], event)
                        context["record_usage"] = record_usage
                    options["context"] = context
                result = await pipeline.run(job["request"]["question"], job["request"]["depth"], progress,
                                            lambda: self.store.is_cancelled(task_id), **options)
                evidence = result.pop("_evidence", None)
                # The model cannot choose identity, timestamps, ownership, or bookmarks.
                result.update(id=str(uuid.uuid4()), createdAt=iso(), question=job["request"]["question"], bookmarks=[], version=1)
                result["source"] = "demo" if self.settings.llm_provider == "fixture" else "generated"
                text_length = sum(len(p) for c in result.get("chapters", []) for p in c.get("paragraphs", []))
                result["minutes"] = max(1, round(text_length / 500))
                from .publishing import prepare_report
                result, evidence = prepare_report(result, evidence, job["request"])
                if hasattr(self.store, "save_checkpoint"):
                    self.store.save_checkpoint(task_id, job["owner"], "D.quality", {
                        "data": result.get("workflow", {}).get("quality_review", {})})
                report = Report.model_validate(result).model_dump(mode="json", exclude_none=True)
                self.store.publish(task_id, job["owner"], report, evidence)
        except asyncio.CancelledError:
            # DB cancellation is committed before cancelling this coroutine.
            raise
        except (asyncio.TimeoutError, TimeoutError):
            self.store.fail(task_id, "TASK_TIMEOUT", "任务超过最长执行时间")
        except Exception as exc:
            code = getattr(exc, "code", "GENERATION_FAILED")
            message = getattr(exc, "message", "调研生成失败，请稍后重试")
            # No provider response, request content, credentials, or traceback in API logs.
            logger.warning("Task %s failed (%s)", task_id, code)
            self.store.fail(task_id, code, message)
        finally:
            if token is not None:
                self.store.end_execution(token)
