from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def stable_hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


class PluginState:
    """Small WAL journal shared by the MCP server, jobs, and actor adapters."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, timeout=30)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS projects (
                id TEXT PRIMARY KEY, relative_path TEXT NOT NULL UNIQUE, revision INTEGER NOT NULL,
                metadata TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS requests (
                request_id TEXT PRIMARY KEY, operation TEXT NOT NULL, input_hash TEXT NOT NULL,
                result TEXT NOT NULL, created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY, project_id TEXT NOT NULL, phase TEXT NOT NULL, status TEXT NOT NULL,
                pid INTEGER, command_hash TEXT NOT NULL, directory TEXT NOT NULL,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS actor_tasks (
                id TEXT PRIMARY KEY, project_id TEXT NOT NULL, role TEXT NOT NULL, input_hash TEXT NOT NULL,
                request TEXT NOT NULL, response TEXT, status TEXT NOT NULL, revision INTEGER NOT NULL,
                rejection_reason TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS actor_tasks_status ON actor_tasks(project_id, status, created_at);
            CREATE INDEX IF NOT EXISTS actor_tasks_replay ON actor_tasks(project_id, role, input_hash, status);
            CREATE UNIQUE INDEX IF NOT EXISTS actor_tasks_active_unique ON actor_tasks(project_id, role, input_hash)
                WHERE status IN ('pending','submitted');
            """
        )
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def create_project(self, project_id: str, relative_path: str, metadata: dict[str, Any]) -> dict[str, Any]:
        now = utcnow()
        self.conn.execute(
            "INSERT INTO projects VALUES (?, ?, 1, ?, ?, ?)",
            (project_id, relative_path, json.dumps(metadata, sort_keys=True), now, now),
        )
        self.conn.commit()
        return self.get_project(project_id)

    def get_project(self, project_id: str) -> dict[str, Any]:
        row = self.conn.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
        if row is None:
            raise ValueError(f"unknown project: {project_id}")
        return {
            "id": row["id"], "relative_path": row["relative_path"], "revision": row["revision"],
            "metadata": json.loads(row["metadata"]), "created_at": row["created_at"], "updated_at": row["updated_at"],
        }

    def list_projects(self) -> list[dict[str, Any]]:
        rows = self.conn.execute("SELECT id FROM projects ORDER BY created_at").fetchall()
        return [self.get_project(str(row["id"])) for row in rows]

    def create_job(self, project_id: str, phase: str, pid: int, command_hash: str, directory: str) -> dict[str, Any]:
        job_id = f"job-{uuid.uuid4().hex}"
        now = utcnow()
        self.conn.execute(
            "INSERT INTO jobs VALUES (?, ?, ?, 'running', ?, ?, ?, ?, ?)",
            (job_id, project_id, phase, pid, command_hash, directory, now, now),
        )
        self.conn.commit()
        return self.get_job(job_id)

    def get_job(self, job_id: str) -> dict[str, Any]:
        row = self.conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if row is None:
            raise ValueError(f"unknown job: {job_id}")
        return dict(row)

    def running_job(self, project_id: str, phase: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM jobs WHERE project_id=? AND phase=? AND status='running' ORDER BY created_at DESC LIMIT 1",
            (project_id, phase),
        ).fetchone()
        return None if row is None else dict(row)

    def update_job_status(self, job_id: str, status: str) -> dict[str, Any]:
        self.conn.execute("UPDATE jobs SET status=?, updated_at=? WHERE id=?", (status, utcnow(), job_id))
        self.conn.commit()
        return self.get_job(job_id)

    def replay(self, request_id: str, operation: str, payload: Any) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM requests WHERE request_id=?", (request_id,)).fetchone()
        if row is None:
            return None
        if row["operation"] != operation or row["input_hash"] != stable_hash(payload):
            raise ValueError("request_id was already used with different input")
        return json.loads(row["result"])

    def reserve_request(self, request_id: str, operation: str, payload: Any) -> dict[str, Any] | None:
        input_hash = stable_hash(payload)
        try:
            self.conn.execute("BEGIN IMMEDIATE")
            row = self.conn.execute("SELECT * FROM requests WHERE request_id=?", (request_id,)).fetchone()
            if row is not None:
                self.conn.rollback()
                if row["operation"] != operation or row["input_hash"] != input_hash:
                    raise ValueError("request_id was already used with different input")
                if row["result"] == "__pending__":
                    raise ValueError("request_id is already in progress")
                return json.loads(row["result"])
            self.conn.execute(
                "INSERT INTO requests VALUES (?, ?, ?, '__pending__', ?)",
                (request_id, operation, input_hash, utcnow()),
            )
            self.conn.commit()
            return None
        except Exception:
            if self.conn.in_transaction:
                self.conn.rollback()
            raise

    def finish_request(self, request_id: str, result: dict[str, Any]) -> dict[str, Any]:
        cursor = self.conn.execute(
            "UPDATE requests SET result=? WHERE request_id=? AND result='__pending__'",
            (json.dumps(result, sort_keys=True), request_id),
        )
        if cursor.rowcount != 1:
            self.conn.rollback()
            raise ValueError("request reservation was lost")
        self.conn.commit()
        return result

    def abandon_request(self, request_id: str) -> None:
        self.conn.execute("DELETE FROM requests WHERE request_id=? AND result='__pending__'", (request_id,))
        self.conn.commit()

    def remember(self, request_id: str, operation: str, payload: Any, result: dict[str, Any]) -> dict[str, Any]:
        try:
            self.conn.execute(
                "INSERT INTO requests VALUES (?, ?, ?, ?, ?)",
                (request_id, operation, stable_hash(payload), json.dumps(result, sort_keys=True), utcnow()),
            )
            self.conn.commit()
            return result
        except sqlite3.IntegrityError:
            self.conn.rollback()
            replay = self.replay(request_id, operation, payload)
            if replay is None:
                raise
            return replay

    def create_actor_task(self, project_id: str, role: str, request: dict[str, Any]) -> dict[str, Any]:
        input_hash = stable_hash(request)
        row = self.conn.execute(
            "SELECT * FROM actor_tasks WHERE project_id=? AND role=? AND input_hash=? AND status IN ('pending','submitted') ORDER BY created_at DESC LIMIT 1",
            (project_id, role, input_hash),
        ).fetchone()
        if row is not None:
            return self._task(row)
        task_id = f"task-{uuid.uuid4().hex}"
        now = utcnow()
        try:
            self.conn.execute(
                "INSERT INTO actor_tasks VALUES (?, ?, ?, ?, ?, NULL, 'pending', 1, NULL, ?, ?)",
                (task_id, project_id, role, input_hash, json.dumps(request, sort_keys=True), now, now),
            )
            self.conn.commit()
        except sqlite3.IntegrityError:
            self.conn.rollback()
            row = self.conn.execute(
                "SELECT * FROM actor_tasks WHERE project_id=? AND role=? AND input_hash=? AND status IN ('pending','submitted') ORDER BY created_at DESC LIMIT 1",
                (project_id, role, input_hash),
            ).fetchone()
            if row is None:
                raise
            return self._task(row)
        return self.get_actor_task(task_id)

    def get_actor_task(self, task_id: str) -> dict[str, Any]:
        row = self.conn.execute("SELECT * FROM actor_tasks WHERE id=?", (task_id,)).fetchone()
        if row is None:
            raise ValueError(f"unknown actor task: {task_id}")
        return self._task(row)

    def list_actor_tasks(self, project_id: str, status: str | None, limit: int) -> list[dict[str, Any]]:
        if status:
            rows = self.conn.execute(
                "SELECT * FROM actor_tasks WHERE project_id=? AND status=? ORDER BY created_at LIMIT ?",
                (project_id, status, limit),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM actor_tasks WHERE project_id=? ORDER BY created_at DESC LIMIT ?", (project_id, limit)
            ).fetchall()
        return [self._task(row) for row in rows]

    @staticmethod
    def _task(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"], "project_id": row["project_id"], "role": row["role"],
            "input_hash": row["input_hash"], "request": json.loads(row["request"]),
            "response": None if row["response"] is None else json.loads(row["response"]),
            "status": row["status"], "revision": row["revision"],
            "rejection_reason": row["rejection_reason"], "created_at": row["created_at"], "updated_at": row["updated_at"],
        }

    def update_actor_task(self, task_id: str, expected_revision: int, *, response: dict[str, Any] | None = None, rejection_reason: str | None = None) -> dict[str, Any]:
        task = self.get_actor_task(task_id)
        if task["revision"] != expected_revision:
            raise ValueError(f"actor task revision conflict: expected {expected_revision}, current {task['revision']}")
        if task["status"] != "pending":
            raise ValueError(f"actor task is already {task['status']}")
        status = "submitted" if response is not None else "rejected"
        cursor = self.conn.execute(
            "UPDATE actor_tasks SET response=?, status=?, revision=revision+1, rejection_reason=?, updated_at=? WHERE id=? AND revision=?",
            (None if response is None else json.dumps(response, sort_keys=True), status, rejection_reason, utcnow(), task_id, expected_revision),
        )
        if cursor.rowcount != 1:
            raise ValueError("actor task update lost a concurrent revision race")
        self.conn.commit()
        return self.get_actor_task(task_id)
