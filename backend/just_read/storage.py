"""Small durable store. State transitions are committed in one SQLite transaction."""
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import secrets
import sqlite3
import time
import uuid

from .settings import Settings


TERMINAL = {"succeeded", "failed", "cancelled"}


def iso(timestamp: float | None = None) -> str:
    return datetime.fromtimestamp(timestamp if timestamp is not None else time.time(), timezone.utc).isoformat()


class StoreError(Exception):
    def __init__(self, status: int, code: str, message: str):
        self.status, self.code, self.message = status, code, message
        super().__init__(message)


class Store:
    def __init__(self, settings: Settings):
        self.settings = settings
        path = Path(settings.database_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = str(path)
        with self.connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY, expires REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY, owner TEXT NOT NULL, idem TEXT NOT NULL,
                    request TEXT NOT NULL, status TEXT NOT NULL, step INTEGER NOT NULL DEFAULT 0,
                    message TEXT NOT NULL, accepted REAL NOT NULL, deadline REAL NOT NULL,
                    expires REAL, report_id TEXT, error TEXT,
                    UNIQUE(owner, idem)
                );
                CREATE INDEX IF NOT EXISTS tasks_status ON tasks(status, accepted);
                CREATE TABLE IF NOT EXISTS reports (
                    id TEXT PRIMARY KEY, owner TEXT NOT NULL, content TEXT NOT NULL, created REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS reports_owner ON reports(owner, created);
                CREATE TABLE IF NOT EXISTS report_artifacts (
                    report_id TEXT PRIMARY KEY REFERENCES reports(id), content TEXT NOT NULL
                );
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA busy_timeout=10000")
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def session(self, token: str | None) -> str | None:
        if not token or len(token) > 100:
            return None
        owner = hashlib.sha256(token.encode()).hexdigest()
        with self.connect() as db:
            row = db.execute("SELECT expires FROM sessions WHERE id=?", (owner,)).fetchone()
        return owner if row and row["expires"] > time.time() else None

    def create_session(self) -> tuple[str, str]:
        token = secrets.token_urlsafe(32)
        owner = hashlib.sha256(token.encode()).hexdigest()
        with self.connect() as db:
            db.execute("INSERT INTO sessions VALUES (?,?)", (owner, time.time() + self.settings.session_ttl_seconds))
        return owner, token

    def renew_session(self, owner: str) -> None:
        with self.connect() as db:
            db.execute("UPDATE sessions SET expires=? WHERE id=?", (time.time() + self.settings.session_ttl_seconds, owner))

    def _view(self, db, row) -> dict:
        report = None
        if row["status"] == "succeeded":
            saved = db.execute("SELECT content FROM reports WHERE id=? AND owner=?", (row["report_id"], row["owner"])).fetchone()
            if saved:
                report = json.loads(saved["content"])
        return {"id": row["id"], "status": row["status"], "step": row["step"],
                "message": row["message"], "report": report,
                "error": json.loads(row["error"]) if row["error"] else None,
                "expiresAt": iso(row["expires"]) if row["expires"] else None}

    def _owned_task(self, db, task_id: str, owner: str):
        row = db.execute("SELECT * FROM tasks WHERE id=? AND owner=?", (task_id, owner)).fetchone()
        if not row or (row["expires"] is not None and row["expires"] <= time.time()):
            raise StoreError(404, "TASK_NOT_FOUND", "任务不存在或已过期")
        return row

    def create_task(self, owner: str, key: str, request: dict, ready: bool) -> tuple[dict, bool]:
        body = json.dumps(request, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        now = time.time()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM tasks WHERE expires IS NOT NULL AND expires<=?", (now,))
            old = db.execute("SELECT * FROM tasks WHERE owner=? AND idem=?", (owner, key)).fetchone()
            if old:
                if old["request"] != body:
                    raise StoreError(409, "IDEMPOTENCY_CONFLICT", "同一请求键不能用于不同的调研问题")
                return self._view(db, old), False
            if not ready:
                raise StoreError(503, "PROVIDER_NOT_CONFIGURED", "请先在服务端配置模型和搜索接口")
            if db.execute("SELECT 1 FROM tasks WHERE owner=? AND status IN ('queued','running')", (owner,)).fetchone():
                raise StoreError(409, "TASK_ALREADY_ACTIVE", "当前会话已有正在执行的调研任务")
            count = db.execute("SELECT count(*) FROM tasks WHERE status IN ('queued','running')").fetchone()[0]
            if count >= self.settings.max_active_tasks:
                raise StoreError(429, "QUEUE_FULL", "调研队列已满，请稍后重试")
            task_id = str(uuid.uuid4())
            db.execute("""INSERT INTO tasks (id,owner,idem,request,status,message,accepted,deadline)
                VALUES (?,?,?,?,'queued',?,?,?)""", (task_id, owner, key, body, "等待开始调研", now, now + self.settings.task_timeout_seconds))
            return self._view(db, self._owned_task(db, task_id, owner)), True

    def task(self, task_id: str, owner: str) -> dict:
        with self.connect() as db:
            return self._view(db, self._owned_task(db, task_id, owner))

    def cancel(self, task_id: str, owner: str) -> dict:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._owned_task(db, task_id, owner)
            if row["status"] not in TERMINAL:
                db.execute("UPDATE tasks SET status='cancelled',message=?,expires=? WHERE id=?", ("调研已取消", time.time() + self.settings.terminal_retention_seconds, task_id))
            return self._view(db, self._owned_task(db, task_id, owner))

    def is_cancelled(self, task_id: str) -> bool:
        with self.connect() as db:
            row = db.execute("SELECT status FROM tasks WHERE id=?", (task_id,)).fetchone()
        return not row or row["status"] != "running"

    def recover(self) -> None:
        with self.connect() as db:
            db.execute("UPDATE tasks SET status='failed',message=?,error=?,expires=? WHERE status='running'",
                       ("服务重启，任务中断", json.dumps({"code": "SERVER_RESTARTED", "message": "服务重启中断了调研，请重新发起任务"}, ensure_ascii=False), time.time() + self.settings.terminal_retention_seconds))

    def claim_next(self) -> dict | None:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            now = time.time()
            db.execute("UPDATE tasks SET status='failed',message=?,error=?,expires=? WHERE status='queued' AND deadline<=?",
                       ("任务超时", json.dumps({"code": "TASK_TIMEOUT", "message": "任务超过最长执行时间"}, ensure_ascii=False), now + self.settings.terminal_retention_seconds, now))
            row = db.execute("SELECT * FROM tasks WHERE status='queued' ORDER BY accepted LIMIT 1").fetchone()
            if not row:
                return None
            db.execute("UPDATE tasks SET status='running',message=? WHERE id=?", ("正在理解调研问题", row["id"]))
            return {"id": row["id"], "owner": row["owner"], "request": json.loads(row["request"]), "deadline": row["deadline"]}

    def progress(self, task_id: str, step: int, message: str) -> None:
        # Final step belongs to the atomic publication operation only.
        with self.connect() as db:
            db.execute("UPDATE tasks SET step=max(step,?),message=? WHERE id=? AND status='running'", (max(0, min(step, 2)), message[:500], task_id))

    def fail(self, task_id: str, code: str, message: str) -> None:
        with self.connect() as db:
            db.execute("UPDATE tasks SET status='failed',message=?,error=?,expires=? WHERE id=? AND status='running'",
                       (message, json.dumps({"code": code, "message": message}, ensure_ascii=False), time.time() + self.settings.terminal_retention_seconds, task_id))

    def publish(self, task_id: str, owner: str, report: dict, evidence: dict | None = None) -> bool:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT status,deadline FROM tasks WHERE id=? AND owner=?", (task_id, owner)).fetchone()
            if not row or row["status"] != "running":
                return False
            if row["deadline"] <= time.time():
                db.execute("UPDATE tasks SET status='failed',message=?,error=?,expires=? WHERE id=?",
                           ("任务超时", json.dumps({"code": "TASK_TIMEOUT", "message": "任务超过最长执行时间"}, ensure_ascii=False), time.time() + self.settings.terminal_retention_seconds, task_id))
                return False
            db.execute("INSERT INTO reports VALUES (?,?,?,?)", (report["id"], owner, json.dumps(report, ensure_ascii=False), time.time()))
            if evidence is not None:
                db.execute("INSERT INTO report_artifacts VALUES (?,?)", (report["id"], json.dumps(evidence, ensure_ascii=False)))
            db.execute("UPDATE tasks SET status='succeeded',step=3,message=?,report_id=?,expires=? WHERE id=?",
                       ("报告已完成并保存", report["id"], time.time() + self.settings.terminal_retention_seconds, task_id))
            return True

    def report(self, report_id: str, owner: str) -> dict:
        with self.connect() as db:
            row = db.execute("SELECT content FROM reports WHERE id=? AND owner=?", (report_id, owner)).fetchone()
        if not row:
            raise StoreError(404, "REPORT_NOT_FOUND", "报告不存在")
        return json.loads(row["content"])

    def reports(self, owner: str) -> list[dict]:
        with self.connect() as db:
            rows = db.execute("SELECT content FROM reports WHERE owner=? ORDER BY created DESC", (owner,)).fetchall()
        return [json.loads(r["content"]) for r in rows]

    def import_report(self, owner: str, report: dict) -> dict:
        report = {**report, "id": str(uuid.uuid4()), "source": "import", "createdAt": iso()}
        with self.connect() as db:
            db.execute("INSERT INTO reports VALUES (?,?,?,?)", (report["id"], owner, json.dumps(report, ensure_ascii=False), time.time()))
        return report

    def bookmarks(self, owner: str, report_id: str, bookmarks: list[str]) -> dict:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT content FROM reports WHERE id=? AND owner=?", (report_id, owner)).fetchone()
            if not row:
                raise StoreError(404, "REPORT_NOT_FOUND", "报告不存在")
            report = json.loads(row["content"])
            ids = {c["id"] for c in report["chapters"]}
            if len(set(bookmarks)) != len(bookmarks) or not set(bookmarks) <= ids:
                raise StoreError(422, "INVALID_BOOKMARKS", "书签必须引用报告中已有的章节")
            report["bookmarks"] = bookmarks
            db.execute("UPDATE reports SET content=? WHERE id=? AND owner=?", (json.dumps(report, ensure_ascii=False), report_id, owner))
            return report
