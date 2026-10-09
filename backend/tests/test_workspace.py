import hashlib
import io
import json
import sqlite3
import time
import uuid
from zipfile import ZipFile

import pytest

from just_read.settings import Settings
from just_read.storage import Store, StoreError, iso
from just_read.workspace import WorkspaceStore


def report():
    return {"id": str(uuid.uuid4()), "source": "generated", "title": "版本报告", "question": "调研问题", "category": "测试",
            "summary": "简短结论", "createdAt": iso(), "minutes": 1, "chapters": [
                {"id": "overview", "title": "概览", "paragraphs": ["候选结论 [S1]"]},
                {"id": "sources", "title": "来源", "paragraphs": ["[S1] https://example.org"]}],
            "chart": [], "bookmarks": []}


@pytest.fixture
def store(tmp_path):
    return WorkspaceStore(Settings(database_path=str(tmp_path / "workspace.sqlite3"), test_mode=True, llm_provider="fixture"))


def running(store, owner):
    task, _ = store.create_task(owner, str(uuid.uuid4()), {"question": "问题", "depth": "brief"}, True)
    store.claim_next()
    return task["id"]


def publish(store, owner="owner"):
    task_id = running(store, owner)
    value = report()
    assert store.publish(task_id, owner, value, {"sources": [{"id": "S1", "raw_content": "source text", "url": "https://example.org"}]} )
    return store.report(value["id"], owner)


def test_additive_migration_preserves_original_tasks_and_versions(tmp_path):
    settings = Settings(database_path=str(tmp_path / "legacy.sqlite3"))
    old = Store(settings)
    task = running(old, "owner")
    original = report()
    old.publish(task, "owner", original, {"sources": []})
    migrated = WorkspaceStore(settings)
    assert migrated.task(task, "owner")["status"] == "succeeded"
    assert migrated.version(original["id"], "owner", 1)["title"] == original["title"]
    assert migrated.report(original["id"], "owner")["version"] == 1
    assert len(WorkspaceStore(settings).versions(original["id"], "owner")) == 1


def test_material_bytes_chunks_and_ownership(store):
    raw = b"original bytes"
    value = store.put_material("a", {"id": "source-1", "name": "a.txt", "media_type": "text/plain", "pages": [{"page": 1, "text": "FULL_PAGE_TEXT"}], "chunks": [{"id": "c1", "text": "text", "page": 1}]}, raw)
    assert value["hash"] == hashlib.sha256(raw).hexdigest()
    assert store.materials_for_task("a", ["source-1"])[0]["chunks"][0]["page"] == 1
    assert store.material_blob("source-1", "a") == (raw, "text/plain", "a.txt")
    assert "chunks" not in store.materials("a")[0]
    assert store.materials("a")[0]["chunk_count"] == 1
    assert "pages" not in store.materials("a")[0]
    assert "FULL_PAGE_TEXT" not in json.dumps(store.materials("a"))
    assert store.materials("a")[0]["page_count"] == 1
    assert store.materials("a")[0]["text_characters"] == len("FULL_PAGE_TEXT")
    assert store.materials("b") == []
    for operation in (lambda: store.material("source-1", "b"), lambda: store.material_blob("source-1", "b")):
        with pytest.raises(StoreError) as error:
            operation()
        assert error.value.status == 404


def test_pause_resume_and_retry_checkpoint_prefix(store):
    task = running(store, "owner")
    for name in ("A01-plan", "A04-sources", "B06-analysis", "C08-layout"):
        store.save_checkpoint(task, "owner", name, {"input_hash": "same", "stage": name})
    paused = store.task_action(task, "owner", "pause")
    assert paused["status"] == "paused"
    store.save_checkpoint(task, "owner", "D11-late", {"bad": True})
    assert store.load_checkpoint(task, "owner", "D11-late") is None
    resumed = store.task_action(task, "owner", "resume")
    assert resumed["id"] == task and resumed["status"] == "queued"
    assert store.load_checkpoint(task, "owner", "B06-analysis") is not None
    store.claim_next()
    store.fail(task, "EXAMPLE", "failed")
    retry = store.task_action(task, "owner", "retry", "B")
    assert retry["id"] != task
    assert store.load_checkpoint(retry["id"], "owner", "A04-sources") is not None
    assert store.load_checkpoint(retry["id"], "owner", "B06-analysis") is None
    assert store.load_checkpoint(retry["id"], "owner", "C08-layout") is None
    with pytest.raises(StoreError):
        store.load_checkpoint(task, "other", "A01-plan")


def test_retry_without_stage_retains_successful_same_input_checkpoints(store):
    task = running(store, "owner")
    store.save_checkpoint(task, "owner", "custom-checkpoint", {"hash": "x"})
    store.fail(task, "X", "error")
    retry = store.task_action(task, "owner", "retry")
    assert store.load_checkpoint(retry["id"], "owner", "custom-checkpoint") == {"hash": "x"}


def test_pause_does_not_let_resume_bypass_active_limit(store):
    task = running(store, "owner")
    store.task_action(task, "owner", "pause")
    running(store, "owner")
    with pytest.raises(StoreError) as error:
        store.task_action(task, "owner", "resume")
    assert error.value.code == "TASK_ALREADY_ACTIVE"


def test_usage_and_stage_records_are_owned_and_no_credentials(store):
    task = running(store, "owner")
    store.record_usage(task, "owner", {"stage": "B06", "elapsed_ms": 31, "usage": {"total_tokens": 12}, "api_key": "DO-NOT-STORE"})
    store.record_stage(task, "owner", {"stage": "B06", "status": "running"})
    events = store.runs(task, "owner")
    assert {e["kind"] for e in events} == {"usage", "stage"}
    assert "DO-NOT-STORE" not in json.dumps(events)
    with pytest.raises(StoreError):
        store.runs(task, "another-owner")


def test_publication_version_and_evidence_rollback_together(store, monkeypatch):
    task = running(store, "owner")
    value = report()
    def broken(*args):
        raise sqlite3.OperationalError("snapshot failure")
    monkeypatch.setattr(store, "_insert_version", broken)
    with pytest.raises(sqlite3.OperationalError):
        store.publish(task, "owner", value, {"sources": []})
    with store.connect() as db:
        assert db.execute("SELECT count(*) FROM reports").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM report_artifacts").fetchone()[0] == 0
    assert store.task(task, "owner")["status"] == "running"


def test_cancelled_task_never_publishes_new_version(store):
    task = running(store, "owner")
    store.cancel(task, "owner")
    assert store.publish(task, "owner", report(), {}) is False
    assert store.reports("owner") == []


def test_edit_conflict_diff_restore_and_immutable_evidence(store):
    initial = publish(store)
    rid = initial["id"]
    first = store.version(rid, "owner", 1)
    changed = store.revise(rid, "owner", 1, {"title": "修改标题", "chapters": [
        {"id": "overview", "title": "概览", "paragraphs": ["人工修改"]}]})
    assert changed["version"] == 2 and changed["id"] == rid
    assert store.version(rid, "owner", 1) == first
    assert store.versions(rid, "owner")[0]["diff"]["fields"] == ["title"]
    assert store.versions(rid, "owner")[0]["diff"]["chapters_removed"] == ["sources"]
    with pytest.raises(StoreError) as error:
        store.revise(rid, "owner", 1, {"title": "stale"})
    assert error.value.code == "VERSION_CONFLICT"
    restored = store.restore(rid, "owner", 2, 1)
    assert restored["version"] == 3 and restored["title"] == first["title"]
    assert store.version(rid, "owner", 2)["title"] == "修改标题"
    store.bookmarks("owner", rid, ["overview"])
    assert store.version(rid, "owner", 3)["bookmarks"] == []
    assert store.report(rid, "owner")["bookmarks"] == ["overview"]


def test_review_is_bound_to_version_and_separate_from_ai(store):
    first = publish(store)
    original = store.version(first["id"], "owner", 1)
    human = store.review(first["id"], "owner", 1, {"kind": "human", "status": "approved", "notes": "人工检查"})
    machine = store.review(first["id"], "owner", 1, {"kind": "ai", "status": "warnings"})
    assert human["kind"] != machine["kind"]
    assert len(store.workflow(first["id"], "owner", 1)["reviews"]) == 2
    assert store.version(first["id"], "owner", 1) == original


def test_share_frozen_version_expiry_revocation_and_no_private_evidence(store):
    first = publish(store)
    shared = store.create_share(first["id"], "owner", 1, 3600)
    store.revise(first["id"], "owner", 1, {"title": "version two"})
    public = store.share(shared["token"])
    assert public["version"] == 1 and public["title"] == first["title"]
    assert "raw_content" not in json.dumps(public) and "_evidence" not in public
    with pytest.raises(StoreError):
        store.create_share(first["id"], "other", 1, 30)
    store.revoke_share(first["id"], "owner", shared["token"])
    with pytest.raises(StoreError):
        store.share(shared["token"])
    expired = store.create_share(first["id"], "owner", 1, 10)
    with store.connect() as db:
        db.execute("UPDATE report_shares SET expires=?", (time.time() - 1,))
    with pytest.raises(StoreError):
        store.share(expired["token"])


def test_export_is_bound_to_owner_and_fixed_version(store):
    first = publish(store)
    original_zip, name = store.export(first["id"], "owner", 1)
    store.revise(first["id"], "owner", 1, {"title": "second version"})
    assert store.export(first["id"], "owner", 1)[0] == original_zip
    with ZipFile(io.BytesIO(original_zip)) as archive:
        assert json.loads(archive.read("report.json"))["version"] == 1
        assert b"justread-data" in archive.read("index.html")
    assert name.endswith("-v1.zip")
    with pytest.raises(StoreError):
        store.export(first["id"], "other", 1)


def test_import_receives_canonical_server_identity_and_first_version(store):
    original = report()
    original["version"] = 99
    imported = store.import_report("owner", original)
    assert imported["id"] != original["id"]
    assert imported["version"] == 1 and imported["source"] == "import"
    assert store.version(imported["id"], "owner", 1) == imported


def test_new_human_review_invalidates_export_cache_and_keeps_old_snapshot(store):
    first = publish(store)
    before, _ = store.export(first["id"], "owner", 1)
    store.review(first["id"], "owner", 1, {"kind": "human", "notes": "review arrived later", "decision": "approved"})
    after, _ = store.export(first["id"], "owner", 1)
    assert before != after
    with ZipFile(io.BytesIO(before)) as old, ZipFile(io.BytesIO(after)) as new:
        assert json.loads(old.read("reviews.json")) == []
        assert json.loads(new.read("reviews.json"))[0]["notes"] == "review arrived later"
        assert old.read("report.json") == new.read("report.json")
    assert "review arrived later" in store.export_html(first["id"], "owner", 1)
    with store.connect() as db:
        assert db.execute("SELECT count(*) FROM offline_export_history").fetchone()[0] == 2


def test_old_execution_cannot_write_after_same_id_resume_is_running(store):
    task, _ = store.create_task("owner", str(uuid.uuid4()), {"question": "问题", "depth": "brief"}, True)
    first = store.claim_next()
    old_context = store.begin_execution(first)
    try:
        store.save_checkpoint(task["id"], "owner", "A01", {"value": "accepted checkpoint"})
        store.task_action(task["id"], "owner", "pause")
        store.task_action(task["id"], "owner", "resume")
        second = store.claim_next()
        assert first["execution_id"] != second["execution_id"]
        assert store.task(task["id"], "owner")["status"] == "running"
        assert store.is_cancelled(task["id"])
        store.save_checkpoint(task["id"], "owner", "A01", {"value": "stale overwrite"})
        store.record_stage(task["id"], "owner", {"stage": "D13", "status": "stale"})
        store.progress(task["id"], 2, "stale progress")
        store.fail(task["id"], "STALE_FAILURE", "old execution must not fail the new one")
        assert store.publish(task["id"], "owner", report(), {}) is False
        assert store.task(task["id"], "owner")["status"] == "running"
        assert store.task(task["id"], "owner")["message"] != "stale progress"
        assert store.load_checkpoint(task["id"], "owner", "A01")["value"] == "accepted checkpoint"
        assert store.reports("owner") == []
        assert not any(run.get("status") == "stale" for run in store.runs(task["id"], "owner"))
        fresh_context = store.begin_execution(second)
        try:
            assert not store.is_cancelled(task["id"])
            store.save_checkpoint(task["id"], "owner", "A01", {"value": "fresh"})
            assert store.publish(task["id"], "owner", report(), {})
        finally:
            store.end_execution(fresh_context)
        assert store.task(task["id"], "owner")["status"] == "succeeded"
    finally:
        store.end_execution(old_context)


def test_share_projection_removes_legacy_full_text_but_keeps_bound_quotes(store):
    original = report()
    original["workflow"] = {"sources": [{"id": "S1", "title": "legacy", "raw_content": "PRIVATE_RAW", "pages": [{"text": "PRIVATE_PAGE"}],
        "chunks": [{"id": "c1", "page": 2, "start": 0, "end": 100, "text": "PRIVATE_CHUNK"}]}],
        "evidence": [{"id": "E1", "source_id": "S1", "chunk_id": "c1", "quote": "EXPLICIT_CITED_QUOTE", "locator": {"page": 2}, "verification": {"quote_match": True}}],
        "api_key": "PRIVATE_KEY"}
    task = running(store, "owner")
    store.publish(task, "owner", original, {"sources": [{"raw_content": "PRIVATE_INTERNAL"}]})
    private = store.version(original["id"], "owner", 1)
    share = store.create_share(original["id"], "owner", 1, 3600)
    public = store.share(share["token"])
    body = json.dumps(public)
    for text in ("PRIVATE_RAW", "PRIVATE_PAGE", "PRIVATE_CHUNK", "PRIVATE_KEY", "PRIVATE_INTERNAL", "raw_content"):
        assert text not in body
    source = public["workflow"]["sources"][0]
    assert source["excerpts"][0]["quote"] == "EXPLICIT_CITED_QUOTE"
    assert source["chunks"][0]["quotes"][0]["quote"] == "EXPLICIT_CITED_QUOTE"
    assert source["chunks"][0]["page"] == 2 and "text" not in source["chunks"][0]
    assert public["workflow"]["manifest"]["delivery_scope"] == "public"
    assert store.version(original["id"], "owner", 1) == private
    archive, _ = store.export(original["id"], "owner", 1)
    with ZipFile(io.BytesIO(archive)) as zipped:
        assert b"PRIVATE_INTERNAL" in zipped.read("evidence.json")
