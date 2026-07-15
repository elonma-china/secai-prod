from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any


class SqliteLedger:
    """Small durable ledger used locally; production may replace this with Postgres."""

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        with self._connect() as conn:
            conn.executescript(
                """
                create table if not exists tool_audit (
                  idempotency_key text primary key, run_id text not null, tool_name text not null,
                  payload_hash text not null, metadata text not null, created_at integer not null);
                create table if not exists evidence_ledger (
                  evidence_id text primary key, run_id text not null, metadata text not null, created_at integer not null);
                create table if not exists poc_drafts (
                  idempotency_key text primary key, run_id text not null, draft text not null,
                  metadata text not null, created_at integer not null);
                """
            )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, check_same_thread=False)

    def audit_once(self, key: str, run_id: str, tool_name: str, payload_hash: str, metadata: dict[str, Any]) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                "insert or ignore into tool_audit values (?, ?, ?, ?, ?, ?)",
                (key, run_id, tool_name, payload_hash, json.dumps(metadata, sort_keys=True), int(time.time())),
            )

    def save_evidence(self, evidence_id: str, run_id: str, metadata: dict[str, Any]) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                "insert or ignore into evidence_ledger values (?, ?, ?, ?)",
                (evidence_id, run_id, json.dumps(metadata, sort_keys=True), int(time.time())),
            )

    def draft_once(self, key: str, run_id: str, draft: str, metadata: dict[str, Any]) -> dict[str, Any]:
        with self._lock, self._connect() as conn:
            conn.execute(
                "insert or ignore into poc_drafts values (?, ?, ?, ?, ?)",
                (key, run_id, draft, json.dumps(metadata, sort_keys=True), int(time.time())),
            )
            row = conn.execute("select draft, metadata from poc_drafts where idempotency_key = ?", (key,)).fetchone()
        return {"draft": row[0], "metadata": json.loads(row[1])}

    def exploit_metadata(self, run_id: str | None) -> dict[str, Any]:
        if not run_id:
            return {"evidence_ids": [], "edb_ids": [], "provenance": [], "candidate_status": "not_generated"}
        with self._lock, self._connect() as conn:
            evidence_rows = conn.execute(
                "select evidence_id, metadata from evidence_ledger where run_id = ?", (run_id,)
            ).fetchall()
            draft_row = conn.execute(
                "select metadata from poc_drafts where run_id = ? order by created_at desc limit 1", (run_id,)
            ).fetchone()
        evidence = [json.loads(row[1]) for row in evidence_rows]
        edb_ids = sorted({str(row.get("edb_id")) for row in evidence if row.get("edb_id")})
        provenance = [
            {"edb_id": row.get("edb_id"), "sha256": row.get("sha256"), "canonical_url": row.get("canonical_url")}
            for row in evidence
        ]
        draft = json.loads(draft_row[0]) if draft_row else {}
        return {
            "evidence_ids": [row[0] for row in evidence_rows], "edb_ids": edb_ids,
            "provenance": provenance, "candidate_status": draft.get("candidate_status", "not_generated"),
        }
