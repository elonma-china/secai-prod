"""Durable projects registry — SQLite-backed, same shape as SqliteLedger.

A "project" groups sessions/conversations under a named workspace owned by a user.
This is the P0 registry only (create/get/list/rename/delete); wiring projects to
sessions/routes is a follow-up scope owned by other agents.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any
from uuid import uuid4


class ProjectStore:
    """Small durable projects registry used locally; production may replace this with Postgres."""

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        with self._connect() as conn:
            conn.executescript(
                """
                create table if not exists projects (
                  project_id text primary key, owner text not null, name text not null,
                  metadata text not null, created_at integer not null, updated_at integer not null);
                """
            )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, check_same_thread=False)

    @staticmethod
    def _row_to_dict(row: tuple[Any, ...]) -> dict[str, Any]:
        project_id, owner, name, metadata, created_at, updated_at = row
        return {
            "project_id": project_id, "owner": owner, "name": name,
            "metadata": json.loads(metadata), "created_at": created_at, "updated_at": updated_at,
        }

    def create(self, project_id: str | None, owner: str, name: str, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        pid = project_id or uuid4().hex
        now = int(time.time())
        payload = json.dumps(metadata or {}, sort_keys=True)
        with self._lock, self._connect() as conn:
            conn.execute(
                "insert into projects values (?, ?, ?, ?, ?, ?)",
                (pid, owner, name, payload, now, now),
            )
        return {"project_id": pid, "owner": owner, "name": name, "metadata": metadata or {}, "created_at": now, "updated_at": now}

    def get(self, project_id: str) -> dict[str, Any] | None:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "select project_id, owner, name, metadata, created_at, updated_at from projects where project_id = ?",
                (project_id,),
            ).fetchone()
        return self._row_to_dict(row) if row else None

    def list(self, owner: str | None = None) -> list[dict[str, Any]]:
        query = "select project_id, owner, name, metadata, created_at, updated_at from projects"
        params: tuple[Any, ...] = ()
        if owner is not None:
            query += " where owner = ?"
            params = (owner,)
        query += " order by updated_at desc"
        with self._lock, self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def rename(self, project_id: str, name: str) -> dict[str, Any] | None:
        now = int(time.time())
        with self._lock, self._connect() as conn:
            cur = conn.execute(
                "update projects set name = ?, updated_at = ? where project_id = ?",
                (name, now, project_id),
            )
            if cur.rowcount == 0:
                return None
            row = conn.execute(
                "select project_id, owner, name, metadata, created_at, updated_at from projects where project_id = ?",
                (project_id,),
            ).fetchone()
        return self._row_to_dict(row) if row else None

    def delete(self, project_id: str) -> bool:
        with self._lock, self._connect() as conn:
            cur = conn.execute("delete from projects where project_id = ?", (project_id,))
        return cur.rowcount > 0
