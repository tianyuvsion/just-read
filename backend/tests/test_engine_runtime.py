import asyncio
import sqlite3
import uuid

import pytest

from just_read.engine import Engine
from just_read.settings import Settings
from just_read.workspace import WorkspaceStore
from just_read.pipeline import PipelineError
from test_workspace import report


@pytest.mark.asyncio
async def test_worker_survives_transient_database_error_and_reports_liveness(caplog):
    class TemporarilyUnavailableStore:
        def __init__(self): self.calls = 0
        def recover(self): pass
        def claim_next(self):
            self.calls += 1
            if self.calls == 1:
                raise sqlite3.OperationalError("private database detail")
            return None

    store = TemporarilyUnavailableStore()
    engine = Engine(store, Settings(workers=1))
    await engine.start()
    try:
        for _ in range(60):
            if store.calls >= 2: break
            await asyncio.sleep(.01)
        runtime = engine.runtime()
        assert runtime["workers_alive"] == 1 and runtime["healthy"] is True
        assert runtime["database_failures"] == 1
        assert "private database detail" not in caplog.text
    finally:
        await engine.stop()
    assert engine.runtime()["workers_alive"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("late_result", ["report", "failure"])
async def test_engine_old_cancel_suppressing_runner_cannot_write_to_resumed_same_task(tmp_path, late_result):
    settings = Settings(database_path=str(tmp_path / "fenced-engine.sqlite3"), llm_provider="fixture",
                        test_mode=True, workers=2)
    store = WorkspaceStore(settings)
    first_started, second_started = asyncio.Event(), asyncio.Event()
    cancelled, release_old, release_new = asyncio.Event(), asyncio.Event(), asyncio.Event()
    calls = []
    class SlowPipeline:
        async def run(self, question, depth, progress, is_cancelled, context=None):
            calls.append(len(calls) + 1)
            if len(calls) == 1:
                first_started.set()
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    # Simulates an adapter that finishes work despite cancellation.
                    cancelled.set()
                    await release_old.wait()
                await context["save_checkpoint"]("B.late-writer", {"old": True})
                assert is_cancelled() is True
                if late_result == "failure":
                    raise PipelineError("OLD_RUN_FAILED", "obsolete failure")
                value = report()
                value["title"] = "obsolete report"
                return value
            second_started.set()
            await release_new.wait()
            assert is_cancelled() is False
            value = report()
            value["title"] = "fresh resumed report"
            return value
    engine = Engine(store, settings, SlowPipeline)
    await engine.start()
    try:
        task, _ = store.create_task("owner", str(uuid.uuid4()), {"question": "测试恢复", "depth": "brief"}, True)
        task_id = task["id"]
        engine.notify()
        await asyncio.wait_for(first_started.wait(), 2)
        old_runner = engine.running[task_id]
        store.task_action(task_id, "owner", "pause")
        engine.cancel(task_id)
        await asyncio.wait_for(cancelled.wait(), 2)
        store.task_action(task_id, "owner", "resume")
        engine.notify()
        await asyncio.wait_for(second_started.wait(), 2)
        new_runner = engine.running[task_id]
        assert new_runner is not old_runner
        release_old.set()
        await asyncio.wait_for(asyncio.shield(old_runner), 2)
        assert store.task(task_id, "owner")["status"] == "running"
        assert store.load_checkpoint(task_id, "owner", "B.late-writer") is None
        assert engine.running[task_id] is new_runner
        release_new.set()
        await asyncio.wait_for(asyncio.shield(new_runner), 2)
        saved = store.task(task_id, "owner")
        assert saved["status"] == "succeeded" and saved["report"]["title"] == "fresh resumed report"
        with store.connect() as db:
            assert db.execute("SELECT COUNT(*) FROM reports").fetchone()[0] == 1
    finally:
        release_old.set()
        release_new.set()
        await engine.stop()
