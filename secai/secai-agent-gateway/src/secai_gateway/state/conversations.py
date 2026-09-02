"""Durable multi-conversation index + transcript store (SQLite).

The *live* working memory an agent reasons over stays in linch's Redis session store
(``RedisSessionStore``) with a 24h window and automatic compaction. THIS store is the
durable system of record layered on top: a per-user index of conversations (for
listing / switching old↔new) plus the full, uncompacted message transcript (for reading
history back long after the Redis live window has expired).

Mirrors ``state/ledger.py::SqliteLedger``: same on-disk file (``gateway_db_path``), a
process-wide ``threading.Lock`` around short write transactions, one fresh connection per
call (``check_same_thread=False``). Everything here is best-effort from the caller's point
of view — the route layer wraps calls fail-open so a durability hiccup never breaks a chat
reply (see api/routes.py).

Ownership: ``owner_hash = sha256(user_id)``. It is also folded into the linch session id
(``chat:conv:{owner_hash}:{conversation_id}``) so cross-user addressing is structurally
impossible; the ``owner_hash`` column here is the second, explicit layer (a mismatched
owner → :meth:`owns` returns ``False`` → the route answers 404 without confirming
existence).
"""
from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Any

# Title auto-derived from the first user message; kept short for a conversation-list UI.
_TITLE_MAX = 60


def _derive_title(text: str) -> str:
    """First non-empty line of the user's message, whitespace-collapsed, truncated."""
    line = " ".join((text or "").strip().split())
    return line[:_TITLE_MAX]


class ConversationStore:
    """SQLite-backed durable conversation index + transcript."""

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        with self._connect() as conn:
            conn.executescript(
                """
                create table if not exists conversations (
                  conversation_id text primary key,
                  owner_hash text not null,
                  session_id text not null,
                  title text not null default '',
                  created_at integer not null,
                  updated_at integer not null,
                  message_count integer not null default 0,
                  deleted integer not null default 0);
                create index if not exists idx_conv_owner
                  on conversations (owner_hash, deleted, updated_at desc);
                create table if not exists conversation_messages (
                  conversation_id text not null,
                  seq integer not null,
                  role text not null,
                  content text not null,
                  created_at integer not null,
                  primary key (conversation_id, seq));
                """
            )

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    # ── index ────────────────────────────────────────────────────────────────
    def create(self, owner_hash: str, conversation_id: str, session_id: str,
               title: str, ts: int) -> None:
        """Insert a conversation row. Idempotent (``insert or ignore``) so a first-turn
        create that races a prior create just no-ops instead of overwriting."""
        with self._lock, self._connect() as conn:
            conn.execute(
                "insert or ignore into conversations "
                "(conversation_id, owner_hash, session_id, title, created_at, updated_at, "
                "message_count, deleted) values (?, ?, ?, ?, ?, ?, 0, 0)",
                (conversation_id, owner_hash, session_id, title or "", ts, ts),
            )

    def get(self, conversation_id: str) -> dict[str, Any] | None:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "select * from conversations where conversation_id = ?", (conversation_id,)
            ).fetchone()
        return dict(row) if row is not None else None

    def owns(self, owner_hash: str, conversation_id: str) -> bool:
        """True only when the conversation exists, is not soft-deleted, and belongs to
        ``owner_hash``. The gate for every conversation-scoped read/write."""
        if not owner_hash:
            return False
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "select 1 from conversations where conversation_id = ? and owner_hash = ? "
                "and deleted = 0",
                (conversation_id, owner_hash),
            ).fetchone()
        return row is not None

    def list_for_owner(self, owner_hash: str, limit: int = 20,
                       offset: int = 0) -> tuple[list[dict[str, Any]], int]:
        """(page, total) of a user's live conversations, newest-updated first."""
        limit = max(1, min(int(limit or 20), 200))
        offset = max(0, int(offset or 0))
        with self._lock, self._connect() as conn:
            total = conn.execute(
                "select count(*) from conversations where owner_hash = ? and deleted = 0",
                (owner_hash,),
            ).fetchone()[0]
            rows = conn.execute(
                "select conversation_id, title, created_at, updated_at, message_count "
                "from conversations where owner_hash = ? and deleted = 0 "
                "order by updated_at desc, created_at desc limit ? offset ?",
                (owner_hash, limit, offset),
            ).fetchall()
        return [dict(r) for r in rows], int(total)

    def soft_delete(self, conversation_id: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                "update conversations set deleted = 1 where conversation_id = ?",
                (conversation_id,),
            )

    # ── transcript ─────────────────────────────────────────────────────────────
    def append_turn(self, conversation_id: str, user_text: str, assistant_text: str,
                    ts: int) -> None:
        """Append one user+assistant turn (2 rows) to the durable transcript and bump the
        conversation's counters. Sets the title from the user message if still empty."""
        with self._lock, self._connect() as conn:
            base = conn.execute(
                "select coalesce(max(seq), 0) from conversation_messages "
                "where conversation_id = ?", (conversation_id,),
            ).fetchone()[0]
            conn.executemany(
                "insert or ignore into conversation_messages "
                "(conversation_id, seq, role, content, created_at) values (?, ?, ?, ?, ?)",
                [
                    (conversation_id, base + 1, "user", user_text or "", ts),
                    (conversation_id, base + 2, "assistant", assistant_text or "", ts),
                ],
            )
            conn.execute(
                "update conversations set message_count = message_count + 2, updated_at = ?, "
                "title = case when title = '' or title is null then ? else title end "
                "where conversation_id = ?",
                (ts, _derive_title(user_text), conversation_id),
            )

    def messages(self, conversation_id: str, limit: int = 200,
                 offset: int = 0) -> tuple[list[dict[str, Any]], int]:
        """(page, total) of the durable transcript, oldest first."""
        limit = max(1, min(int(limit or 200), 1000))
        offset = max(0, int(offset or 0))
        with self._lock, self._connect() as conn:
            total = conn.execute(
                "select count(*) from conversation_messages where conversation_id = ?",
                (conversation_id,),
            ).fetchone()[0]
            rows = conn.execute(
                "select role, content, seq, created_at from conversation_messages "
                "where conversation_id = ? order by seq asc limit ? offset ?",
                (conversation_id, limit, offset),
            ).fetchall()
        return [dict(r) for r in rows], int(total)

    def recent_turns(self, conversation_id: str, n: int) -> list[dict[str, Any]]:
        """The last ``n`` transcript messages, oldest-first — used to rehydrate a linch
        session whose Redis live window has expired (see api/routes.py::_maybe_rehydrate)."""
        n = max(0, int(n or 0))
        if not n:
            return []
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "select role, content from conversation_messages where conversation_id = ? "
                "order by seq desc limit ?",
                (conversation_id, n),
            ).fetchall()
        return [dict(r) for r in reversed(rows)]
