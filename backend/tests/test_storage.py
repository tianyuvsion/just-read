from concurrent.futures import ThreadPoolExecutor
import time
import uuid
import sqlite3
import pytest
from just_read.settings import Settings
from just_read.storage import Store


def setup(tmp_path):
    store = Store(Settings(database_path=str(tmp_path / "store.db")))
    owner, _ = store.create_session()
    task, _ = store.create_task(owner, str(uuid.uuid4()), {"question": "研究", "depth": "deep"}, True)
    store.claim_next()
    return store, owner, task["id"]


def report():
    return {"id": str(uuid.uuid4()), "title": "报告", "bookmarks": [], "chapters": []}


def test_cancelled_late_publication_cannot_save(tmp_path):
    store, owner, task_id = setup(tmp_path)
    store.cancel(task_id, owner)
    store.progress(task_id, 2, "late progress")
    store.fail(task_id, "LATE_ERROR", "late error")
    assert store.publish(task_id, owner, report()) is False
    assert store.task(task_id, owner)["status"] == "cancelled"
    assert store.reports(owner) == []


def test_cancel_publish_race_has_one_atomic_winner(tmp_path):
    store, owner, task_id = setup(tmp_path)
    value = report()
    with ThreadPoolExecutor(2) as pool:
        cancel = pool.submit(store.cancel, task_id, owner)
        publish = pool.submit(store.publish, task_id, owner, value)
        cancel.result()
        publish.result()
    task = store.task(task_id, owner)
    if task["status"] == "cancelled":
        assert store.reports(owner) == [] and task["report"] is None
    else:
        assert task["status"] == "succeeded"
        assert len(store.reports(owner)) == 1 and task["report"]["id"] == value["id"]


def test_deadline_includes_queue_and_never_publishes_late(tmp_path):
    store, owner, task_id = setup(tmp_path)
    with store.connect() as db:
        db.execute("UPDATE tasks SET deadline=? WHERE id=?", (time.time() - 1, task_id))
    assert not store.publish(task_id, owner, report())
    assert store.task(task_id, owner)["error"]["code"] == "TASK_TIMEOUT"
    assert store.reports(owner) == []
    queued, _ = store.create_task(owner, str(uuid.uuid4()), {"question": "排队", "depth": "deep"}, True)
    with store.connect() as db:
        db.execute("UPDATE tasks SET deadline=? WHERE id=?", (time.time() - 1, queued["id"]))
    assert store.claim_next() is None
    assert store.task(queued["id"], owner)["error"]["code"] == "TASK_TIMEOUT"


def test_parallel_idempotent_creates_only_one_task(tmp_path):
    store = Store(Settings(database_path=str(tmp_path / "store.db")))
    owner, _ = store.create_session()
    key = str(uuid.uuid4())
    with ThreadPoolExecutor(4) as pool:
        futures = [pool.submit(store.create_task, owner, key, {"question": "研究", "depth": "deep"}, True) for _ in range(4)]
        results = [f.result() for f in futures]
    assert len({r[0]["id"] for r in results}) == 1
    assert sum(created for _, created in results) == 1


def test_artifact_insert_failure_rolls_back_entire_publication(tmp_path):
    store, owner, task_id = setup(tmp_path)
    with store.connect() as db:
        db.execute("CREATE TRIGGER reject_artifact BEFORE INSERT ON report_artifacts BEGIN SELECT RAISE(ABORT, 'test failure'); END")
    with pytest.raises(sqlite3.IntegrityError):
        store.publish(task_id, owner, report(), {"sources": [{"raw_content": "snapshot"}]})
    assert store.task(task_id, owner)["status"] == "running"
    assert store.reports(owner) == []


def test_replay_precedes_provider_readiness_and_expiry_allows_new_task(tmp_path):
    store = Store(Settings(database_path=str(tmp_path / "store.db")))
    owner, _ = store.create_session()
    key = str(uuid.uuid4())
    body = {"question": "研究", "depth": "deep"}
    first, _ = store.create_task(owner, key, body, True)
    store.cancel(first["id"], owner)
    replay, created = store.create_task(owner, key, body, False)
    assert replay["id"] == first["id"] and not created
    with store.connect() as db:
        db.execute("UPDATE tasks SET expires=? WHERE id=?", (time.time() - 1, first["id"]))
    replacement, created = store.create_task(owner, key, body, True)
    assert created and replacement["id"] != first["id"]
