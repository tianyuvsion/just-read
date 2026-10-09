import asyncio
from dataclasses import replace
import time
import uuid
import json
import httpx
import pytest
from fastapi.testclient import TestClient
from just_read.app import COOKIE, create_app
from just_read.settings import Settings
from just_read.storage import Store


def config(tmp_path, **kwargs):
    return Settings(database_path=str(tmp_path / "db.sqlite3"), test_mode=True,
                    llm_provider="fixture", fixture_delay_seconds=0.01, **kwargs)


def request(client, question="AI 技术调研", key=None, **kwargs):
    return client.post("/api/v1/research-tasks", json={"question": question, "depth": "deep"},
                       headers={"Idempotency-Key": key or str(uuid.uuid4()), **kwargs})


def terminal(client, task_id, seconds=5):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        response = client.get(f"/api/v1/research-tasks/{task_id}")
        assert response.status_code == 200, response.text
        task = response.json()
        if task["status"] in {"succeeded", "failed", "cancelled"}:
            return task
        time.sleep(0.01)
    pytest.fail("Task did not terminate")


def test_persistent_generation_and_bookmarks(tmp_path):
    settings = config(tmp_path)
    app = create_app(settings)
    with TestClient(app) as client:
        assert request(client).status_code == 428
        health = client.get("/api/v1/health")
        assert health.json()["ready"] is True
        assert "HttpOnly" in health.headers["set-cookie"]
        token = client.cookies.get(COOKIE)
        response = request(client)
        assert response.status_code == 201
        task = terminal(client, response.json()["id"])
        assert task["status"] == "succeeded", task
        assert task["step"] == 3 and task["error"] is None
        report = task["report"]
        assert report["source"] == "demo"
        assert {"overview", "sources"} <= {c["id"] for c in report["chapters"]}
        report_id = report["id"]
        with app.state.store.connect() as db:
            snapshot = db.execute("SELECT content FROM report_artifacts WHERE report_id=?", (report_id,)).fetchone()
        assert snapshot and '"mode": "fixture"' in snapshot[0]
        assert "_evidence" not in report and "raw_content" not in report
        url = f"/api/v1/reports/{report_id}"
        saved = client.put(url + "/bookmarks", json={"bookmarks": ["overview"]})
        assert saved.status_code == 200
        assert saved.json()["bookmarks"] == ["overview"]
        assert client.put(url + "/bookmarks", json={"bookmarks": ["nonexistent"]}).status_code == 422
        assert client.put(url + "/bookmarks", json={"bookmarks": ["overview", "overview"]}).status_code == 422
        assert client.get("/api/v1/reports", params={"q": "AI"}).json()["reports"][0]["id"] == report_id
        assert '"_evidence":' not in client.get("/api/v1/reports").text
        assert "raw_content" not in client.get(url).text
        assert client.get("/api/v1/reports", params={"q": "不存在的内容xyz"}).json() == {"reports": []}
        assert client.post(f"/api/v1/research-tasks/{task['id']}/cancel").json()["status"] == "succeeded"
    with TestClient(create_app(settings)) as reopened:
        reopened.cookies.set(COOKIE, token)
        assert reopened.get(url).json()["bookmarks"] == ["overview"]
        assert reopened.get("/api/v1/reports").json()["reports"][0]["id"] == report_id


def test_idempotency_normalization_conflict_and_owner_isolation(tmp_path):
    with TestClient(create_app(config(tmp_path))) as client:
        client.get("/api/v1/health")
        owner_a = client.cookies.get(COOKIE)
        key = str(uuid.uuid4())
        first = request(client, "  同一问题  ", key)
        replay = request(client, "同一问题", key)
        assert first.status_code == 201 and replay.status_code == 200
        assert first.json()["id"] == replay.json()["id"]
        assert request(client, "另一个问题", key).json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"
        result = terminal(client, first.json()["id"])
        client.cookies.clear()
        client.get("/api/v1/health")
        assert client.get(f"/api/v1/research-tasks/{result['id']}").status_code == 404
        assert client.post(f"/api/v1/research-tasks/{result['id']}/cancel").status_code == 404
        assert client.get(f"/api/v1/reports/{result['report']['id']}").status_code == 404
        assert client.get("/api/v1/reports").json() == {"reports": []}
        # Idempotency keys are scoped to a session, not global.
        second = request(client, "另一个问题", key)
        assert second.status_code == 201
        assert second.json()["id"] != first.json()["id"]
        assert client.get(f"/api/v1/research-tasks/{first.json()['id']}", headers={"Cookie": f"{COOKIE}={owner_a}"}).status_code == 200


class SlowPipeline:
    async def run(self, question, depth, progress, cancelled):
        await progress(0, "正在研究")
        await asyncio.sleep(10)
        return {}


def test_cancel_active_task_releases_worker(tmp_path):
    settings = config(tmp_path, workers=1)
    with TestClient(create_app(settings, pipeline_factory=SlowPipeline)) as client:
        client.get("/api/v1/health")
        first = request(client).json()
        assert request(client, "第二个问题").json()["error"]["code"] == "TASK_ALREADY_ACTIVE"
        cancelled = client.post(f"/api/v1/research-tasks/{first['id']}/cancel").json()
        assert cancelled["status"] == "cancelled" and cancelled["report"] is None
        assert client.post(f"/api/v1/research-tasks/{first['id']}/cancel").json() == cancelled
        second = request(client, "第二个问题")
        assert second.status_code == 201
        time.sleep(0.05)
        assert client.get(f"/api/v1/research-tasks/{second.json()['id']}").json()["status"] == "running"
        client.post(f"/api/v1/research-tasks/{second.json()['id']}/cancel")
        assert client.get("/api/v1/reports").json() == {"reports": []}


def test_timeouts_failure_and_provider_unconfigured(tmp_path):
    settings = config(tmp_path, task_timeout_seconds=0.04)
    with TestClient(create_app(settings, pipeline_factory=SlowPipeline)) as client:
        client.get("/api/v1/health")
        task = terminal(client, request(client).json()["id"])
        assert task["status"] == "failed" and task["error"]["code"] == "TASK_TIMEOUT"
        assert task["report"] is None
    with TestClient(create_app(replace(settings, llm_provider="openai", test_mode=False))) as client:
        assert client.get("/api/v1/health").json()["ready"] is False
        assert request(client).status_code == 503
    with pytest.raises(ValueError, match="Fixture"):
        create_app(replace(settings, test_mode=False))


def test_validation_origin_and_body_limits(tmp_path):
    with TestClient(create_app(config(tmp_path))) as client:
        client.get("/api/v1/health")
        assert request(client, " ").status_code == 422
        assert request(client, "😀" * 501).status_code == 422
        assert request(client, "问题", key="not-a-uuid").status_code == 422
        assert request(client, Origin="https://evil.example").status_code == 403
        assert client.post("/api/v1/research-tasks", json={"question": "问题", "depth": "invalid"}, headers={"Idempotency-Key": str(uuid.uuid4())}).status_code == 422
        assert client.post("/api/v1/reports/import", content=b"x" * (2 * 1024 * 1024 + 1)).status_code == 413
        assert client.get("/api/v1/unknown").status_code == 404


def imported():
    return {"id": "local-import", "source": "import", "title": "测试导入", "question": "本地文档",
            "category": "文档", "summary": "这是一篇导入报告", "createdAt": "2026-10-05T00:00:00Z", "minutes": 1,
            "chapters": [{"id": "overview", "title": "标题", "paragraphs": []}, {"id": "content", "title": "正文", "paragraphs": ["原文内容"]}],
            "chart": [], "bookmarks": []}


def test_report_import_server_identity_and_chart_contract(tmp_path):
    with TestClient(create_app(config(tmp_path))) as client:
        client.get("/api/v1/health")
        body = imported()
        response = client.post("/api/v1/reports/import", json=body)
        assert response.status_code == 200
        result = response.json()
        assert result["id"] != body["id"] and result["source"] == "import"
        assert client.get(f"/api/v1/reports/{result['id']}").status_code == 200
        body["chart"] = [{"label": "不可核验", "value": 12}]
        assert client.post("/api/v1/reports/import", json=body).status_code == 422
        body["chart"] = [{"label": "增长率", "value": -2.5}]
        body["chartMeta"] = {"title": "候选增长率", "unit": "%", "note": "数值与口径待核实"}
        response = client.post("/api/v1/reports/import", json=body)
        assert response.status_code == 200
        assert response.json()["chart"] == [{"label": "增长率", "value": -2.5}]
        for nonfinite in ["NaN", "Infinity", "-Infinity"]:
            body["chart"][0]["value"] = nonfinite
            assert client.post("/api/v1/reports/import", json=body).status_code == 422


def test_restart_recovery_and_expired_tasks_reports_persist(tmp_path):
    settings = config(tmp_path)
    store = Store(settings)
    owner, token = store.create_session()
    body = {"question": "恢复任务", "depth": "deep"}
    interrupted, _ = store.create_task(owner, str(uuid.uuid4()), body, True)
    assert store.claim_next()["id"] == interrupted["id"]
    queued, _ = store.create_task(store.create_session()[0], str(uuid.uuid4()), body, True)
    with TestClient(create_app(settings)) as client:
        client.cookies.set(COOKIE, token)
        old = client.get(f"/api/v1/research-tasks/{interrupted['id']}").json()
        assert old["error"]["code"] == "SERVER_RESTARTED"
        with store.connect() as db:
            queued_owner = db.execute("SELECT owner FROM tasks WHERE id=?", (queued["id"],)).fetchone()[0]
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and store.task(queued["id"], queued_owner)["status"] not in {"succeeded", "failed"}:
            time.sleep(0.01)
        result = store.task(queued["id"], queued_owner)
        assert result["status"] == "succeeded", result
        with store.connect() as db:
            db.execute("UPDATE tasks SET expires=? WHERE id=?", (time.time() - 1, queued["id"]))
        from just_read.storage import StoreError
        with pytest.raises(StoreError):
            store.task(queued["id"], queued_owner)
        assert store.report(result["report"]["id"], queued_owner)["id"] == result["report"]["id"]


def test_unexpected_api_error_is_sanitized(tmp_path, monkeypatch):
    app = create_app(config(tmp_path))
    def broken(_owner):
        raise RuntimeError("sensitive provider detail")
    monkeypatch.setattr(app.state.store, "reports", broken)
    with TestClient(app, raise_server_exceptions=False) as client:
        client.get("/api/v1/health")
        response = client.get("/api/v1/reports")
        assert response.status_code == 500
        assert response.json()["error"]["code"] == "INTERNAL_ERROR"
        assert "sensitive" not in response.text


def test_openapi_matches_runtime_envelopes(tmp_path):
    schema = create_app(config(tmp_path)).openapi()
    paths = schema["paths"]
    report_schema = paths["/api/v1/reports"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]
    assert report_schema["$ref"].endswith("/ReportList")
    task_post = paths["/api/v1/research-tasks"]["post"]["responses"]
    assert "201" in task_post
    assert task_post["422"]["content"]["application/json"]["schema"]["$ref"].endswith("/ErrorEnvelope")


def test_mocked_provider_wire_pipeline_engine_and_snapshot(tmp_path):
    from test_pipeline import Workflow, RAW
    from just_read.pipeline import Pipeline
    settings = Settings(database_path=str(tmp_path / "integrated.db"), llm_provider="openai",
                        llm_model="test-model", llm_api_key="test-key", search_api_key="test-key")
    workflow = Workflow(depth="deep")
    class MockedPipeline:
        async def run(self, *args):
            async with httpx.AsyncClient(transport=httpx.MockTransport(workflow)) as upstream:
                return await Pipeline(settings, upstream).run(*args)
    app = create_app(settings, pipeline_factory=MockedPipeline)
    with TestClient(app) as client:
        client.get("/api/v1/health")
        task = terminal(client, request(client, "分析文档阅读能力").json()["id"])
        assert task["status"] == "succeeded", task
        assert task["report"]["source"] == "generated"
        report_id = task["report"]["id"]
        with app.state.store.connect() as db:
            snapshot = json.loads(db.execute("SELECT content FROM report_artifacts WHERE report_id=?", (report_id,)).fetchone()[0])
        assert snapshot["sources"][0]["raw_content"] == RAW
        assert len(snapshot["plan"]["subquestions"]) == 3
        assert len(workflow.calls) == 10
        for url in [f"/api/v1/reports/{report_id}", "/api/v1/reports", f"/api/v1/research-tasks/{task['id']}"]:
            body = client.get(url).text
            assert "raw_content" not in body and '"_evidence":' not in body


def test_expired_session_requires_explicit_bootstrap_and_keeps_ownership(tmp_path):
    app = create_app(config(tmp_path))
    with TestClient(app) as client:
        client.get("/api/v1/health")
        old_token = client.cookies.get(COOKIE)
        imported_report = client.post("/api/v1/reports/import", json=imported()).json()
        owner = app.state.store.session(old_token)
        with app.state.store.connect() as db:
            db.execute("UPDATE sessions SET expires=? WHERE id=?", (time.time() - 1, owner))
        assert client.get("/api/v1/reports").status_code == 428
        client.get("/api/v1/health")
        assert client.cookies.get(COOKIE) != old_token
        assert client.get(f"/api/v1/reports/{imported_report['id']}").status_code == 404
        assert client.get("/api/v1/reports").json() == {"reports": []}
        assert app.state.store.report(imported_report["id"], owner)["id"] == imported_report["id"]


def test_built_frontend_same_origin_serving(tmp_path):
    from pathlib import Path
    dist = Path(__file__).resolve().parents[2] / "dist"
    if not (dist / "index.html").exists():
        pytest.skip("Run frontend build before static-serving check")
    app = create_app(config(tmp_path, static_dir=str(dist)))
    with TestClient(app) as client:
        index = client.get("/")
        assert index.status_code == 200 and '<div id="app">' in index.text
        client.get("/api/v1/health")
        assert client.get("/api/v1/reports").json() == {"reports": []}
        response = client.get("/api/v1/unknown-route")
        assert response.status_code == 404 and response.json()["error"]["code"] == "NOT_FOUND"
