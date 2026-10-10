"""Workspace extensions over the original durable task store.

Versions, research evidence and successful publication share one transaction.
Personal bookmarks may change independently; published version bodies do not.
"""
from __future__ import annotations

from copy import deepcopy
from contextvars import ContextVar
import hashlib
import json
import re
import secrets
import time
import uuid

from .storage import Store, StoreError, TERMINAL, iso
from .publishing import digest, json_bytes, package_report, prepare_report, public_report, render_html, version_diff


_EXECUTION = ContextVar("justread_execution", default=None)


def encoded(value) -> str:
    return json_bytes(value).decode("utf-8")


class WorkspaceStore(Store):
    def __init__(self, settings):
        super().__init__(settings)
        with self.connect() as db:
            columns = {column["name"] for column in db.execute("PRAGMA table_info(tasks)")}
            if "execution_id" not in columns:
                db.execute("ALTER TABLE tasks ADD COLUMN execution_id TEXT")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS materials (
                    id TEXT PRIMARY KEY, owner TEXT NOT NULL, content TEXT NOT NULL,
                    blob BLOB NOT NULL, created REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS materials_owner ON materials(owner, created);
                CREATE TABLE IF NOT EXISTS material_chunks (
                    material_id TEXT NOT NULL REFERENCES materials(id), id TEXT NOT NULL,
                    position INTEGER NOT NULL, content TEXT NOT NULL,
                    PRIMARY KEY(material_id, id)
                );
                CREATE TABLE IF NOT EXISTS task_checkpoints (
                    task_id TEXT NOT NULL, owner TEXT NOT NULL, name TEXT NOT NULL,
                    content TEXT NOT NULL, updated REAL NOT NULL,
                    PRIMARY KEY(task_id, name)
                );
                CREATE TABLE IF NOT EXISTS task_runs (
                    id TEXT PRIMARY KEY, task_id TEXT NOT NULL, owner TEXT NOT NULL,
                    kind TEXT NOT NULL, content TEXT NOT NULL, created REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS runs_task ON task_runs(task_id, created);
                CREATE TABLE IF NOT EXISTS report_versions (
                    report_id TEXT NOT NULL REFERENCES reports(id), version INTEGER NOT NULL,
                    owner TEXT NOT NULL, content TEXT NOT NULL, evidence TEXT NOT NULL,
                    diff TEXT NOT NULL, created REAL NOT NULL,
                    PRIMARY KEY(report_id, version)
                );
                CREATE TABLE IF NOT EXISTS report_reviews (
                    id TEXT PRIMARY KEY, report_id TEXT NOT NULL, version INTEGER NOT NULL,
                    owner TEXT NOT NULL, kind TEXT NOT NULL, content TEXT NOT NULL, created REAL NOT NULL,
                    FOREIGN KEY(report_id, version) REFERENCES report_versions(report_id, version)
                );
                CREATE TABLE IF NOT EXISTS report_shares (
                    token_hash TEXT PRIMARY KEY, report_id TEXT NOT NULL, version INTEGER NOT NULL,
                    owner TEXT NOT NULL, expires REAL NOT NULL, revoked REAL, created REAL NOT NULL, content TEXT,
                    FOREIGN KEY(report_id, version) REFERENCES report_versions(report_id, version)
                );
                CREATE TABLE IF NOT EXISTS offline_exports (
                    report_id TEXT NOT NULL, version INTEGER NOT NULL, owner TEXT NOT NULL,
                    blob BLOB NOT NULL, manifest TEXT NOT NULL, created REAL NOT NULL,
                    PRIMARY KEY(report_id, version),
                    FOREIGN KEY(report_id, version) REFERENCES report_versions(report_id, version)
                );
                CREATE TABLE IF NOT EXISTS offline_export_history (
                    id TEXT PRIMARY KEY, report_id TEXT NOT NULL, version INTEGER NOT NULL,
                    owner TEXT NOT NULL, blob BLOB NOT NULL, manifest TEXT NOT NULL, created REAL NOT NULL,
                    FOREIGN KEY(report_id, version) REFERENCES report_versions(report_id, version)
                );
            """)
            share_columns = {column["name"] for column in db.execute("PRAGMA table_info(report_shares)")}
            if "content" not in share_columns:
                db.execute("ALTER TABLE report_shares ADD COLUMN content TEXT")
            # Additive migration: all legacy reports become immutable version 1.
            for row in db.execute("SELECT id,owner,content,created FROM reports").fetchall():
                if db.execute("SELECT 1 FROM report_versions WHERE report_id=? LIMIT 1", (row["id"],)).fetchone():
                    continue
                report = json.loads(row["content"])
                report["version"] = 1
                artifact = db.execute("SELECT content FROM report_artifacts WHERE report_id=?", (row["id"],)).fetchone()
                evidence = json.loads(artifact["content"]) if artifact else {}
                report, evidence = prepare_report(report, evidence, {})
                db.execute("UPDATE reports SET content=? WHERE id=?", (encoded(report), row["id"]))
                self._insert_version(db, row["owner"], report, evidence, {}, row["created"])

    def begin_execution(self, job: dict):
        execution = (job["id"], job["execution_id"]) if job.get("execution_id") else None
        return _EXECUTION.set(execution)

    def end_execution(self, token) -> None:
        _EXECUTION.reset(token)

    @staticmethod
    def _matches_execution(db, task_id: str) -> bool:
        execution = _EXECUTION.get()
        if execution is None:
            return True  # Administrative calls and original Store test fixtures.
        if execution[0] != task_id:
            return False
        row = db.execute("SELECT execution_id FROM tasks WHERE id=?", (task_id,)).fetchone()
        return bool(row and row["execution_id"] == execution[1])

    def claim_next(self) -> dict | None:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            now = time.time()
            db.execute("UPDATE tasks SET status='failed',message=?,error=?,expires=?,execution_id=NULL WHERE status='queued' AND deadline<=?", (
                "任务超时", encoded({"code": "TASK_TIMEOUT", "message": "任务超过最长执行时间"}), now + self.settings.terminal_retention_seconds, now))
            row = db.execute("SELECT * FROM tasks WHERE status='queued' ORDER BY accepted LIMIT 1").fetchone()
            if not row:
                return None
            execution_id = str(uuid.uuid4())
            db.execute("UPDATE tasks SET status='running',message=?,execution_id=? WHERE id=?", ("正在理解调研问题", execution_id, row["id"]))
            return {"id": row["id"], "owner": row["owner"], "request": json.loads(row["request"]), "deadline": row["deadline"], "execution_id": execution_id}

    def progress(self, task_id: str, step: int, message: str) -> None:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if self._matches_execution(db, task_id):
                db.execute("UPDATE tasks SET step=max(step,?),message=? WHERE id=? AND status='running'", (max(0, min(step, 2)), message[:500], task_id))

    def fail(self, task_id: str, code: str, message: str) -> None:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if self._matches_execution(db, task_id):
                db.execute("UPDATE tasks SET status='failed',message=?,error=?,expires=?,execution_id=NULL WHERE id=? AND status='running'", (
                    message, encoded({"code": code, "message": message}), time.time() + self.settings.terminal_retention_seconds, task_id))

    def is_cancelled(self, task_id: str) -> bool:
        with self.connect() as db:
            row = db.execute("SELECT status FROM tasks WHERE id=?", (task_id,)).fetchone()
            return not row or row["status"] != "running" or not self._matches_execution(db, task_id)

    def cancel(self, task_id: str, owner: str) -> dict:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            task = self._owned_task(db, task_id, owner)
            if task["status"] not in TERMINAL:
                db.execute("UPDATE tasks SET status='cancelled',message=?,expires=?,execution_id=NULL WHERE id=?", ("调研已取消", time.time() + self.settings.terminal_retention_seconds, task_id))
            return self._view(db, self._owned_task(db, task_id, owner))

    @staticmethod
    def _insert_version(db, owner, report, evidence, difference, created=None):
        db.execute("INSERT INTO report_versions VALUES (?,?,?,?,?,?,?)", (
            report["id"], report["version"], owner, encoded(report), encoded(evidence), encoded(difference), created or time.time()))

    @staticmethod
    def _owned_version(db, report_id, owner, version):
        row = db.execute("SELECT * FROM report_versions WHERE report_id=? AND owner=? AND version=?", (report_id, owner, version)).fetchone()
        if not row:
            raise StoreError(404, "VERSION_NOT_FOUND", "报告版本不存在")
        return row

    def put_material(self, owner: str, material: dict, raw: bytes) -> dict:
        value = deepcopy(material)
        value.setdefault("id", str(uuid.uuid4()))
        value.setdefault("revision", 1)
        value.setdefault("createdAt", iso())
        value["hash"] = hashlib.sha256(raw).hexdigest()
        value["size_bytes"] = len(raw)
        chunks = value.get("chunks", [])
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM materials WHERE id=?", (value["id"],)).fetchone():
                raise StoreError(409, "SOURCE_EXISTS", "资料编号已存在")
            db.execute("INSERT INTO materials VALUES (?,?,?,?,?)", (value["id"], owner, encoded(value), raw, time.time()))
            for position, chunk in enumerate(chunks):
                item = dict(chunk) if isinstance(chunk, dict) else {"text": str(chunk)}
                chunk_id = str(item.get("id") or f"chunk-{position + 1}")
                # Stable extraction order disambiguates duplicate external chunk IDs.
                db.execute("INSERT INTO material_chunks VALUES (?,?,?,?)", (value["id"], f"{position}:{chunk_id}", position, encoded(item)))
        return value

    def materials(self, owner: str) -> list[dict]:
        with self.connect() as db:
            rows = db.execute("SELECT content FROM materials WHERE owner=? ORDER BY created DESC", (owner,)).fetchall()
        summaries = []
        for row in rows:
            material = json.loads(row["content"])
            chunks = material.pop("chunks", [])
            pages = material.pop("pages", [])
            material["chunk_count"] = len(chunks)
            material.setdefault("page_count", len(pages) if isinstance(pages, list) else 0)
            if "text_characters" not in material and isinstance(pages, list):
                material["text_characters"] = sum(len(page.get("text", "")) for page in pages if isinstance(page, dict))
            material.pop("text", None)
            summaries.append(material)
        return summaries

    def material(self, source_id: str, owner: str) -> dict:
        with self.connect() as db:
            row = db.execute("SELECT content FROM materials WHERE id=? AND owner=?", (source_id, owner)).fetchone()
        if not row:
            raise StoreError(404, "SOURCE_NOT_FOUND", "资料不存在")
        return json.loads(row["content"])

    def material_blob(self, source_id: str, owner: str) -> tuple[bytes, str, str]:
        with self.connect() as db:
            row = db.execute("SELECT content,blob FROM materials WHERE id=? AND owner=?", (source_id, owner)).fetchone()
        if not row:
            raise StoreError(404, "SOURCE_NOT_FOUND", "资料不存在")
        value = json.loads(row["content"])
        return bytes(row["blob"]), value.get("media_type", "application/octet-stream"), value.get("name", "source")

    def materials_for_task(self, owner: str, source_ids: list[str]) -> list[dict]:
        return [self.material(source_id, owner) for source_id in dict.fromkeys(source_ids)]

    def load_checkpoint(self, task_id: str, owner: str, name: str) -> dict | None:
        with self.connect() as db:
            self._owned_task(db, task_id, owner)
            row = db.execute("SELECT content FROM task_checkpoints WHERE task_id=? AND owner=? AND name=?", (task_id, owner, name)).fetchone()
        return json.loads(row["content"]) if row else None

    @staticmethod
    def _run_event(db, task_id, owner, kind, event):
        db.execute("INSERT INTO task_runs VALUES (?,?,?,?,?,?)", (str(uuid.uuid4()), task_id, owner, kind, encoded(event), time.time()))

    def save_checkpoint(self, task_id: str, owner: str, name: str, data: dict) -> None:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            task = self._owned_task(db, task_id, owner)
            if task["status"] != "running" or not self._matches_execution(db, task_id):
                return  # A paused/cancelled/restarted writer cannot commit late work.
            db.execute("INSERT INTO task_checkpoints VALUES (?,?,?,?,?) ON CONFLICT(task_id,name) DO UPDATE SET content=excluded.content,updated=excluded.updated", (task_id, owner, name, encoded(data), time.time()))
            self._run_event(db, task_id, owner, "checkpoint", {"stage": name, "status": "succeeded", "content_hash": digest(data)})

    def record_usage(self, task_id: str, owner: str, event: dict) -> None:
        with self.connect() as db:
            self._owned_task(db, task_id, owner)
            # Usage contains counts and durations, not provider payloads/prompts.
            safe = {k: v for k, v in event.items() if k.lower() not in {"api_key", "authorization", "cookie", "headers", "prompt", "request", "response"}}
            if _EXECUTION.get():
                safe["execution_id"] = _EXECUTION.get()[1]
            self._run_event(db, task_id, owner, "usage", safe)

    def record_stage(self, task_id: str, owner: str, event: dict) -> None:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            task = self._owned_task(db, task_id, owner)
            if task["status"] == "running" and self._matches_execution(db, task_id):
                self._run_event(db, task_id, owner, "stage", event)

    def runs(self, task_id: str, owner: str) -> list[dict]:
        with self.connect() as db:
            self._owned_task(db, task_id, owner)
            rows = db.execute("SELECT * FROM task_runs WHERE task_id=? AND owner=? ORDER BY created,id", (task_id, owner)).fetchall()
        return [{"id": row["id"], "kind": row["kind"], "createdAt": iso(row["created"]), **json.loads(row["content"])} for row in rows]

    @staticmethod
    def _stage_rank(name: str) -> tuple[int, str]:
        if name in {"A", "B", "C", "D"}:
            return "ABCD".index(name) * 100, name
        match = re.search(r"(?:^|[^A-Z])([ABCD])(\d{2})(?:[^0-9]|$)", name)
        if match:
            return "ABCD".index(match.group(1)) * 100 + int(match.group(2)), name
        if name[:1] in {"A", "B", "C", "D"}:
            return "ABCD".index(name[0]) * 100 + 1, name
        for prefix, stage in {"plan": 1, "sources": 1, "analysis": 101, "review": 101, "layout": 201, "expression": 201, "quality": 301}.items():
            if name.lower().startswith(prefix):
                return stage, name
        return 999, name

    def task_action(self, task_id: str, owner: str, action: str, from_stage: str | None = None) -> dict:
        if from_stage is not None and from_stage not in {"A", "B", "C", "D"}:
            raise StoreError(422, "INVALID_RESTART_STAGE", "重试起始阶段必须为 A、B、C 或 D")
        now = time.time()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            task = self._owned_task(db, task_id, owner)
            if action == "pause":
                if task["status"] == "paused":
                    return self._view(db, task)
                if task["status"] not in {"queued", "running"}:
                    raise StoreError(409, "TASK_STATE_CONFLICT", "只有运行中或排队任务可以暂停")
                db.execute("UPDATE tasks SET status='paused',message=?,expires=NULL,execution_id=NULL WHERE id=?", ("任务已暂停，可从已保存阶段继续", task_id))
                self._run_event(db, task_id, owner, "action", {"action": "pause"})
                return self._view(db, self._owned_task(db, task_id, owner))
            if action not in {"resume", "retry", "regenerate"}:
                raise StoreError(422, "INVALID_TASK_ACTION", "不支持的任务操作")
            if action == "resume" and task["status"] != "paused":
                raise StoreError(409, "TASK_STATE_CONFLICT", "只有已暂停任务可以继续")
            if action != "resume" and task["status"] not in TERMINAL | {"paused"}:
                raise StoreError(409, "TASK_STATE_CONFLICT", "请先暂停或结束当前任务再重新生成")
            if db.execute("SELECT 1 FROM tasks WHERE owner=? AND status IN ('queued','running')", (owner,)).fetchone():
                raise StoreError(409, "TASK_ALREADY_ACTIVE", "当前会话已有正在执行的调研任务")
            count = db.execute("SELECT count(*) FROM tasks WHERE status IN ('queued','running')").fetchone()[0]
            if count >= self.settings.max_active_tasks:
                raise StoreError(429, "QUEUE_FULL", "调研队列已满，请稍后重试")
            if action == "resume":
                db.execute("UPDATE tasks SET status='queued',message=?,deadline=?,expires=NULL,error=NULL,execution_id=NULL WHERE id=?", ("等待继续调研", now + self.settings.task_timeout_seconds, task_id))
                self._run_event(db, task_id, owner, "action", {"action": "resume"})
                return self._view(db, self._owned_task(db, task_id, owner))
            new_id = str(uuid.uuid4())
            db.execute("INSERT INTO tasks (id,owner,idem,request,status,message,accepted,deadline) VALUES (?,?,?,?,'queued',?,?,?)", (new_id, owner, str(uuid.uuid4()), task["request"], "等待重新生成", now, now + self.settings.task_timeout_seconds))
            old = db.execute("SELECT * FROM task_checkpoints WHERE task_id=? AND owner=?", (task_id, owner)).fetchall()
            cutoff = self._stage_rank(from_stage)[0] if from_stage else 999
            for checkpoint in old:
                if not from_stage or self._stage_rank(checkpoint["name"])[0] < cutoff:
                    db.execute("INSERT INTO task_checkpoints VALUES (?,?,?,?,?)", (new_id, owner, checkpoint["name"], checkpoint["content"], now))
            self._run_event(db, new_id, owner, "action", {"action": "retry", "from_task_id": task_id, "from_stage": from_stage, "input_unchanged": True})
            return self._view(db, self._owned_task(db, new_id, owner))

    def publish(self, task_id: str, owner: str, report: dict, evidence: dict | None = None) -> bool:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            task = db.execute("SELECT status,deadline,request FROM tasks WHERE id=? AND owner=?", (task_id, owner)).fetchone()
            if not task or task["status"] != "running" or not self._matches_execution(db, task_id):
                return False
            if task["deadline"] <= time.time():
                db.execute("UPDATE tasks SET status='failed',message=?,error=?,expires=? WHERE id=?", ("任务超时", encoded({"code": "TASK_TIMEOUT", "message": "任务超过最长执行时间"}), time.time() + self.settings.terminal_retention_seconds, task_id))
                return False
            report = {**report, "version": 1}
            report, snapshot = prepare_report(report, evidence, json.loads(task["request"]))
            db.execute("INSERT INTO reports VALUES (?,?,?,?)", (report["id"], owner, encoded(report), time.time()))
            if evidence is not None:
                db.execute("INSERT INTO report_artifacts VALUES (?,?)", (report["id"], json.dumps(snapshot, ensure_ascii=False)))
            self._insert_version(db, owner, report, snapshot, {"created": True})
            db.execute("UPDATE tasks SET status='succeeded',step=3,message=?,report_id=?,expires=? WHERE id=?", ("报告已完成并保存", report["id"], time.time() + self.settings.terminal_retention_seconds, task_id))
            return True

    def import_report(self, owner: str, report: dict) -> dict:
        report = {**deepcopy(report), "id": str(uuid.uuid4()), "source": "import", "createdAt": iso(), "version": 1}
        report, evidence = prepare_report(report, {"mode": "import"}, {})
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("INSERT INTO reports VALUES (?,?,?,?)", (report["id"], owner, encoded(report), time.time()))
            self._insert_version(db, owner, report, evidence, {"created": True})
        return report

    def versions(self, report_id: str, owner: str) -> list[dict]:
        self.report(report_id, owner)
        with self.connect() as db:
            rows = db.execute("SELECT version,created,diff,content FROM report_versions WHERE report_id=? AND owner=? ORDER BY version DESC", (report_id, owner)).fetchall()
        return [{"version": row["version"], "createdAt": iso(row["created"]), "diff": json.loads(row["diff"]),
                 "title": json.loads(row["content"]).get("title", "")} for row in rows]

    def version(self, report_id: str, owner: str, number: int) -> dict:
        with self.connect() as db:
            return json.loads(self._owned_version(db, report_id, owner, number)["content"])

    def revise(self, report_id: str, owner: str, base_version: int, reportdict: dict) -> dict:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT content FROM reports WHERE id=? AND owner=?", (report_id, owner)).fetchone()
            if not row:
                raise StoreError(404, "REPORT_NOT_FOUND", "报告不存在")
            current = json.loads(row["content"])
            if int(current.get("version", 1)) != base_version:
                raise StoreError(409, "VERSION_CONFLICT", "报告已有新版本，请重新加载后合并修改")
            prior = self._owned_version(db, report_id, owner, base_version)
            editable = {"title", "question", "category", "summary", "minutes", "chapters", "chart", "chartMeta", "workflow"}
            updated = {**current, **{k: deepcopy(v) for k, v in reportdict.items() if k in editable}, "version": base_version + 1}
            if isinstance(updated.get("workflow"), dict):
                old_ai = (current.get("workflow") or {}).get("ai_quality_review")
                if isinstance(old_ai, dict):
                    updated["workflow"]["ai_quality_review"] = {**deepcopy(old_ai), "status": "stale_after_edit",
                        "report_version": base_version, "notice": "此审查来自修改前版本；当前版本应重新审查。"}
            chapter_ids = {c["id"] for c in updated["chapters"]}
            updated["bookmarks"] = [b for b in current.get("bookmarks", []) if b in chapter_ids]
            evidence = json.loads(prior["evidence"])
            evidence["revision"] = {"kind": "human_edit", "base_version": base_version,
                "notice": "正文经过人工修改；原研究来源并不自动证明修改后的内容。"}
            evidence.setdefault("validation", {}).setdefault("warnings", []).append("本版本包含人工编辑；自动检查不替代对修改内容的事实审查。")
            updated, evidence = prepare_report(updated, evidence, {"base_version": base_version})
            difference = version_diff(current, updated)
            self._insert_version(db, owner, updated, evidence, difference)
            db.execute("UPDATE reports SET content=? WHERE id=? AND owner=?", (encoded(updated), report_id, owner))
        return updated

    def restore(self, report_id: str, owner: str, base_version: int, version: int) -> dict:
        return self.revise(report_id, owner, base_version, self.version(report_id, owner, version))

    def workflow(self, report_id: str, owner: str, version: int | None = None) -> dict:
        report = self.version(report_id, owner, version) if version is not None else self.report(report_id, owner)
        result = deepcopy(report.get("workflow") or {})
        result["reviews"] = self.reviews(report_id, owner, int(report.get("version", 1)))
        return result

    def review(self, report_id: str, owner: str, version: int, payload: dict) -> dict:
        value = deepcopy(payload)
        kind = value.get("kind", "human")
        if kind not in {"human", "ai", "automatic"}:
            raise StoreError(422, "INVALID_REVIEW_KIND", "审查须标明人工、AI 或自动一致性检查")
        value.update(id=str(uuid.uuid4()), kind=kind, report_id=report_id, version=version, createdAt=iso())
        with self.connect() as db:
            self._owned_version(db, report_id, owner, version)
            db.execute("INSERT INTO report_reviews VALUES (?,?,?,?,?,?,?)", (value["id"], report_id, version, owner, kind, encoded(value), time.time()))
        return value

    def reviews(self, report_id: str, owner: str, version: int | None = None) -> list[dict]:
        self.report(report_id, owner)
        with self.connect() as db:
            sql = "SELECT content FROM report_reviews WHERE report_id=? AND owner=?"
            params = [report_id, owner]
            if version is not None:
                sql += " AND version=?"
                params.append(version)
            rows = db.execute(sql + " ORDER BY created DESC", params).fetchall()
        return [json.loads(row["content"]) for row in rows]

    def create_share(self, report_id: str, owner: str, version: int, ttl_seconds: int) -> dict:
        if not isinstance(ttl_seconds, int) or not 1 <= ttl_seconds <= 365 * 86400:
            raise StoreError(422, "INVALID_SHARE_EXPIRY", "分享有效期须为 1 秒至 365 天")
        token, now = secrets.token_urlsafe(32), time.time()
        with self.connect() as db:
            row = self._owned_version(db, report_id, owner, version)
            snapshot = public_report(json.loads(row["content"]))
            db.execute("INSERT INTO report_shares (token_hash,report_id,version,owner,expires,revoked,created,content) VALUES (?,?,?,?,?,?,?,?)", (hashlib.sha256(token.encode()).hexdigest(), report_id, version, owner, now + ttl_seconds, None, now, encoded(snapshot)))
        return {"token": token, "report_id": report_id, "version": version, "expiresAt": iso(now + ttl_seconds)}

    def share(self, token: str) -> dict:
        if not token or len(token) > 100:
            raise StoreError(404, "SHARE_NOT_FOUND", "分享不存在或已失效")
        with self.connect() as db:
            token_hash = hashlib.sha256(token.encode()).hexdigest()
            row = db.execute("SELECT s.content AS shared_content,v.content AS original_content FROM report_shares s JOIN report_versions v ON v.report_id=s.report_id AND v.version=s.version AND v.owner=s.owner WHERE s.token_hash=? AND s.revoked IS NULL AND s.expires>?", (token_hash, time.time())).fetchone()
            if not row:
                raise StoreError(404, "SHARE_NOT_FOUND", "分享不存在或已失效")
            if row["shared_content"] is None:
                # Existing shares receive their projection once, then remain frozen.
                report = public_report(json.loads(row["original_content"]))
                db.execute("UPDATE report_shares SET content=? WHERE token_hash=? AND content IS NULL", (encoded(report), token_hash))
            else:
                report = json.loads(row["shared_content"])
        return report

    def revoke_share(self, report_id: str, owner: str, token: str) -> None:
        self.report(report_id, owner)
        with self.connect() as db:
            row = db.execute("SELECT 1 FROM report_shares WHERE token_hash=? AND report_id=? AND owner=?", (hashlib.sha256(token.encode()).hexdigest(), report_id, owner)).fetchone()
            if not row:
                raise StoreError(404, "SHARE_NOT_FOUND", "分享不存在")
            db.execute("UPDATE report_shares SET revoked=COALESCE(revoked,?) WHERE token_hash=? AND report_id=? AND owner=?", (time.time(), hashlib.sha256(token.encode()).hexdigest(), report_id, owner))

    def export(self, report_id: str, owner: str, version: int) -> tuple[bytes, str]:
        with self.connect() as db:
            row = self._owned_version(db, report_id, owner, version)
            review_rows = db.execute("SELECT content FROM report_reviews WHERE report_id=? AND version=? AND owner=? ORDER BY created,id", (report_id, version, owner)).fetchall()
            reviews = [json.loads(review["content"]) for review in review_rows]
            saved = db.execute("SELECT blob,manifest FROM offline_exports WHERE report_id=? AND version=? AND owner=?", (report_id, version, owner)).fetchone()
            if saved and json.loads(saved["manifest"]).get("review_snapshot_hash") == digest(reviews):
                return bytes(saved["blob"]), f"justread-{report_id}-v{version}.zip"
            report, evidence = json.loads(row["content"]), json.loads(row["evidence"])
        blob, manifest = package_report(report, evidence, reviews)
        with self.connect() as db:
            # Cache the latest review snapshot and retain every previously issued package.
            db.execute("INSERT OR IGNORE INTO offline_export_history VALUES (?,?,?,?,?,?,?)", (manifest["export_id"], report_id, version, owner, blob, encoded(manifest), time.time()))
            db.execute("INSERT INTO offline_exports VALUES (?,?,?,?,?,?) ON CONFLICT(report_id,version) DO UPDATE SET blob=excluded.blob,manifest=excluded.manifest,created=excluded.created", (report_id, version, owner, blob, encoded(manifest), time.time()))
        return blob, f"justread-{report_id}-v{version}.zip"

    def export_html(self, report_id: str, owner: str, version: int) -> str:
        with self.connect() as db:
            row = self._owned_version(db, report_id, owner, version)
            reviews = [json.loads(review["content"]) for review in db.execute("SELECT content FROM report_reviews WHERE report_id=? AND version=? AND owner=? ORDER BY created,id", (report_id, version, owner)).fetchall()]
        return render_html(json.loads(row["content"]), json.loads(row["evidence"]), reviews)
