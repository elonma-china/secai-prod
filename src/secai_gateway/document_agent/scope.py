"""Durable, synchronous capability scoping for the opt-in document agent.

Call these methods through ``asyncio.to_thread`` from async request handlers. This
store binds opaque handles to internal job IDs and reserves idempotent turns. A
turn reservation fences stale workers after the fixed 120-second lease; it does
not provide application-level exactly-once execution. Callers must still use the
durable OCR queue's admission/idempotency contract before submitting work.

``begin_turn`` returns a new reservation or a bounded completed response. An
active duplicate raises ``TurnInProgress``; a key reused with another exact
payload digest raises ``TurnPayloadConflict``. Call ``abort_turn`` if admission
fails; call ``complete_turn`` only with the safe public response. Both require
the reservation returned by ``begin_turn``. Payload digests must cover the exact
prompt, file bytes, task handle and stream mode; the HTTP layer computes them.
No prompt, file bytes or raw OCR result belongs in scope metadata.
"""

from __future__ import annotations

import hashlib
import json
import math
import secrets
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

from .settings import (
    CONTEXT_TOMBSTONE_SECONDS,
    CONTEXT_TTL_SECONDS,
    MAX_CONTEXTS_PER_PEER,
    MAX_TURN_RESPONSE_BYTES,
    TURN_LEASE_SECONDS,
)


class InvalidContext(LookupError):
    """The credential or internal scope is unknown."""


class ExpiredContext(LookupError):
    """The context reached its fixed expiry."""


class TooManyContexts(ValueError):
    """The trusted peer already has the allowed number of live contexts."""


class TaskNotFound(LookupError):
    """The handle is unknown within this context."""


class TurnInProgress(ValueError):
    """An identical turn still owns its fixed lease."""


class TurnPayloadConflict(ValueError):
    """This idempotency key was already bound to different bytes."""


class TurnReservationLost(ValueError):
    """The reservation expired, was aborted, or was replaced."""


class TurnResponseTooLarge(ValueError):
    """The safe replay response exceeds its storage limit."""


@dataclass(frozen=True, slots=True)
class ContextGrant:
    context_token: str = field(repr=False)
    expires_at: float


@dataclass(frozen=True, slots=True)
class DocumentScope:
    context_id: str
    expires_at: float


@dataclass(frozen=True, slots=True)
class TurnStart:
    reservation: str | None = field(repr=False)
    response: dict | None = field(repr=False)


def _finite_now(now: float) -> float:
    value = float(now)
    if not math.isfinite(value):
        raise ValueError("time must be finite")
    return value


def _hex(value: str, length: int) -> bool:
    return isinstance(value, str) and len(value) == length and all(c in "0123456789abcdef" for c in value)


class DocumentScopeStore:
    """SQLite store with one short transaction per public operation."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection(write=True) as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS document_contexts (
                    context_id TEXT PRIMARY KEY,
                    token_hash TEXT NOT NULL UNIQUE,
                    peer TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    expires_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS document_contexts_peer_expiry
                    ON document_contexts(peer, expires_at);
                CREATE TABLE IF NOT EXISTS document_tasks (
                    task_handle TEXT PRIMARY KEY,
                    context_id TEXT NOT NULL REFERENCES document_contexts(context_id) ON DELETE CASCADE,
                    job_id TEXT NOT NULL,
                    UNIQUE(context_id, job_id)
                );
                CREATE TABLE IF NOT EXISTS document_turns (
                    context_id TEXT NOT NULL REFERENCES document_contexts(context_id) ON DELETE CASCADE,
                    idempotency_key TEXT NOT NULL,
                    payload_sha256 TEXT NOT NULL,
                    state TEXT NOT NULL CHECK(state IN ('running', 'aborted', 'complete')),
                    reservation TEXT,
                    lease_until REAL NOT NULL,
                    response_json TEXT,
                    PRIMARY KEY(context_id, idempotency_key)
                );
                """
            )

    @contextmanager
    def _connection(self, *, write: bool = False) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        try:
            conn.execute("PRAGMA foreign_keys=ON")
            if write:
                conn.execute("BEGIN IMMEDIATE")
            yield conn
            if write:
                conn.commit()
        except BaseException:
            if write:
                conn.rollback()
            raise
        finally:
            conn.close()

    @staticmethod
    def _active_scope(conn: sqlite3.Connection, scope: DocumentScope, now: float) -> None:
        if scope.expires_at <= now:
            raise ExpiredContext("context expired")
        row = conn.execute(
            "SELECT expires_at FROM document_contexts WHERE context_id = ?", (scope.context_id,)
        ).fetchone()
        if row is None or row[0] != scope.expires_at:
            raise InvalidContext("context unknown")
        if row[0] <= now:
            raise ExpiredContext("context expired")

    def create(self, peer: str, now: float) -> ContextGrant:
        """Issue a 32-byte CSPRNG token; persist only its SHA-256 digest."""
        now = _finite_now(now)
        if not isinstance(peer, str) or not peer or len(peer) > 128:
            raise ValueError("trusted peer required")
        token = secrets.token_hex(32)
        token_hash = hashlib.sha256(bytes.fromhex(token)).hexdigest()
        context_id = secrets.token_hex(16)
        expires_at = now + CONTEXT_TTL_SECONDS
        if not math.isfinite(expires_at) or expires_at <= now:
            raise ValueError("context deadline must be finite and later than creation")
        with self._connection(write=True) as conn:
            conn.execute(
                "DELETE FROM document_contexts WHERE expires_at + ? <= ?",
                (CONTEXT_TOMBSTONE_SECONDS, now),
            )
            count, = conn.execute(
                "SELECT count(*) FROM document_contexts WHERE peer = ? AND expires_at > ?", (peer, now)
            ).fetchone()
            if count >= MAX_CONTEXTS_PER_PEER:
                raise TooManyContexts("peer context limit reached")
            conn.execute(
                "INSERT INTO document_contexts VALUES (?, ?, ?, ?, ?)",
                (context_id, token_hash, peer, now, expires_at),
            )
        return ContextGrant(token, expires_at)

    def resolve(self, token: str, now: float) -> DocumentScope:
        """Resolve a credential; expired credentials remain distinguishable for 24h."""
        now = _finite_now(now)
        if not _hex(token, 64):
            raise InvalidContext("context unknown")
        token_hash = hashlib.sha256(bytes.fromhex(token)).hexdigest()
        with self._connection() as conn:
            row = conn.execute(
                "SELECT context_id, expires_at FROM document_contexts WHERE token_hash = ?", (token_hash,)
            ).fetchone()
        if row is None:
            raise InvalidContext("context unknown")
        if row[1] <= now:
            raise ExpiredContext("context expired")
        return DocumentScope(row[0], row[1])

    def bind_task(self, scope: DocumentScope, job_id: str) -> str:
        """Bind a private job ID once; return a context-scoped 16-byte handle."""
        if not isinstance(job_id, str) or not job_id or len(job_id) > 128:
            raise ValueError("internal job ID required")
        now = time.time()
        with self._connection(write=True) as conn:
            self._active_scope(conn, scope, now)
            row = conn.execute(
                "SELECT task_handle FROM document_tasks WHERE context_id = ? AND job_id = ?",
                (scope.context_id, job_id),
            ).fetchone()
            if row:
                return row[0]
            handle = secrets.token_hex(16)
            conn.execute(
                "INSERT INTO document_tasks(task_handle, context_id, job_id) VALUES (?, ?, ?)",
                (handle, scope.context_id, job_id),
            )
            return handle

    def resolve_task(self, scope: DocumentScope, task_handle: str) -> str:
        """Return an internal job ID only to trusted server code for this scope."""
        with self._connection() as conn:
            self._active_scope(conn, scope, time.time())
            if not _hex(task_handle, 32):
                raise TaskNotFound("task not found")
            row = conn.execute(
                "SELECT job_id FROM document_tasks WHERE context_id = ? AND task_handle = ?",
                (scope.context_id, task_handle),
            ).fetchone()
        if row is None:
            raise TaskNotFound("task not found")
        return row[0]

    def begin_turn(self, scope: DocumentScope, key: str, payload_sha256: str, now: float) -> TurnStart:
        """Reserve a turn or replay its completed safe response.

        ``key`` is exactly 32 lowercase hex and ``payload_sha256`` exactly 64.
        Retry the same digest after abort or 120 seconds to get a new fenced
        reservation. A running duplicate never calls the model or OCR again.
        """
        now = _finite_now(now)
        if not _hex(key, 32) or not _hex(payload_sha256, 64):
            raise ValueError("invalid idempotency key or payload digest")
        with self._connection(write=True) as conn:
            self._active_scope(conn, scope, now)
            row = conn.execute(
                "SELECT payload_sha256, state, lease_until, response_json FROM document_turns "
                "WHERE context_id = ? AND idempotency_key = ?",
                (scope.context_id, key),
            ).fetchone()
            if row:
                old_digest, state, lease_until, response_json = row
                if old_digest != payload_sha256:
                    raise TurnPayloadConflict("idempotency key payload changed")
                if state == "complete":
                    return TurnStart(None, json.loads(response_json))
                if state == "running" and lease_until > now:
                    raise TurnInProgress("turn in progress")
            reservation = secrets.token_hex(16)
            lease_until = min(now + TURN_LEASE_SECONDS, scope.expires_at)
            conn.execute(
                "INSERT INTO document_turns(context_id, idempotency_key, payload_sha256, state, "
                "reservation, lease_until, response_json) VALUES (?, ?, ?, 'running', ?, ?, NULL) "
                "ON CONFLICT(context_id, idempotency_key) DO UPDATE SET state='running', "
                "reservation=excluded.reservation, lease_until=excluded.lease_until, response_json=NULL",
                (scope.context_id, key, payload_sha256, reservation, lease_until),
            )
            return TurnStart(reservation, None)

    def complete_turn(
        self, scope: DocumentScope, key: str, reservation: str, response: dict, now: float
    ) -> None:
        """Commit only the current reservation's bounded, public-safe response."""
        now = _finite_now(now)
        if not isinstance(response, dict):
            raise ValueError("response must be a JSON object")
        payload = json.dumps(response, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        if len(payload.encode("utf-8")) > MAX_TURN_RESPONSE_BYTES:
            raise TurnResponseTooLarge("turn response exceeds limit")
        with self._connection(write=True) as conn:
            self._active_scope(conn, scope, now)
            updated = conn.execute(
                "UPDATE document_turns SET state='complete', reservation=NULL, response_json=? "
                "WHERE context_id=? AND idempotency_key=? AND state='running' "
                "AND reservation=? AND lease_until>?",
                (payload, scope.context_id, key, reservation, now),
            ).rowcount
            if not updated:
                raise TurnReservationLost("turn reservation lost")

    def abort_turn(self, scope: DocumentScope, key: str, reservation: str, now: float) -> None:
        """Release a failed turn while retaining its digest for key conflict checks."""
        now = _finite_now(now)
        with self._connection(write=True) as conn:
            self._active_scope(conn, scope, now)
            updated = conn.execute(
                "UPDATE document_turns SET state='aborted', reservation=NULL, lease_until=0 "
                "WHERE context_id=? AND idempotency_key=? AND state='running' "
                "AND reservation=? AND lease_until>?",
                (scope.context_id, key, reservation, now),
            ).rowcount
            if not updated:
                raise TurnReservationLost("turn reservation lost")
