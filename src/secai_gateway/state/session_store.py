"""Redis-backed linch SessionStore — the single, unified conversation memory."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from linch.sessions.store import (
    ProviderViewSnapshot,
    SessionRecord,
    StoredMessage,
    snapshot_from_dict,
    snapshot_to_dict,
)
from linch.sessions.tasks import CreateTaskInput, Task, TaskPatch
from linch.types import Message, message_from_dict, message_to_dict

_PREFIX = "linch:sess:"
_TTL_SECONDS = 86_400  # 24h, matches the legacy conversation retention window


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _rec_key(sid: str) -> str:
    return f"{_PREFIX}{sid}"


def _msgs_key(sid: str) -> str:
    return f"{_PREFIX}{sid}:msgs"


def _snap_key(sid: str) -> str:
    return f"{_PREFIX}{sid}:snap"


def _rec_to_json(rec: SessionRecord) -> str:
    return json.dumps({
        "id": rec.id, "created_at": rec.created_at, "updated_at": rec.updated_at,
        "meta": rec.meta, "invoked_skills": rec.invoked_skills,
    }, ensure_ascii=False)


def _rec_from_json(raw: str) -> SessionRecord:
    d = json.loads(raw)
    return SessionRecord(
        id=d["id"], created_at=d["created_at"], updated_at=d["updated_at"],
        meta=dict(d.get("meta") or {}), invoked_skills=list(d.get("invoked_skills") or []),
    )


class RedisSessionStore:
    """Implements the linch SessionStore protocol over Redis (sessions/messages/snapshots
    persisted; tasks in-memory). Keys expire after 24h and are refreshed on every write."""

    def __init__(self, url: str, *, ttl_seconds: int = _TTL_SECONDS) -> None:
        from redis.asyncio import Redis  # lazy import — only needed when Redis is configured

        self._redis = Redis.from_url(url, decode_responses=True)
        # Live-memory retention window, refreshed on every write. Parametrized (default =
        # the legacy 24h) so a deploy can widen the working window; durable history is a
        # separate SQLite store (state/conversations.py), independent of this TTL.
        self._ttl = int(ttl_seconds or _TTL_SECONDS)
        self._tasks: dict[str, dict[str, Task]] = {}
        self._task_counter: dict[str, int] = {}

    async def ping(self) -> bool:
        try:
            return bool(await self._redis.ping())
        except Exception:
            return False

    async def _save_record(self, rec: SessionRecord) -> None:
        await self._redis.set(_rec_key(rec.id), _rec_to_json(rec), ex=self._ttl)

    # ── sessions ───────────────────────────────────────────────────────────────
    async def create(self, *, id: str | None = None, meta: dict[str, object] | None = None) -> SessionRecord:
        sid = id or str(uuid4())
        existing = await self.load(sid)
        if existing is not None:
            self._tasks.setdefault(sid, {})
            self._task_counter.setdefault(sid, 1)
            return existing
        ts = _now_iso()
        rec = SessionRecord(id=sid, created_at=ts, updated_at=ts, meta=dict(meta or {}))
        await self._save_record(rec)
        self._tasks[sid] = {}
        self._task_counter[sid] = 1
        return rec

    async def load(self, id: str) -> SessionRecord | None:
        raw = await self._redis.get(_rec_key(id))
        return _rec_from_json(raw) if raw is not None else None

    async def _ensure_record(self, id: str) -> SessionRecord:
        """Get-or-create the session record for a write.

        The chat endpoint keys sessions by a stable per-user id (``chat:<hash>``)
        and streams straight into a run. If the record does not yet exist, the
        first turn's write must *create* it rather than raise — otherwise the
        whole reply is lost as ``session not found`` and the user sees an empty
        bubble. Idempotent with ``create()``; a normal turn (record already made
        by ``Agent.session``) still just loads it."""
        rec = await self.load(id)
        return rec if rec is not None else await self.create(id=id)

    async def load_messages(self, id: str) -> list[StoredMessage]:
        rows = await self._redis.lrange(_msgs_key(id), 0, -1)
        out: list[StoredMessage] = []
        for raw in rows:
            d = json.loads(raw)
            out.append(StoredMessage(seq=d["seq"], appended_at=d["appended_at"], message=message_from_dict(d["message"])))
        return out

    async def append_messages(self, id: str, messages: list[Message]) -> list[StoredMessage]:
        rec = await self._ensure_record(id)
        ts = _now_iso()
        base = await self._redis.llen(_msgs_key(id))
        stored: list[StoredMessage] = []
        payloads: list[str] = []
        for i, message in enumerate(messages):
            seq = int(base) + i + 1
            payloads.append(json.dumps({"seq": seq, "appended_at": ts, "message": message_to_dict(message)}, ensure_ascii=False))
            stored.append(StoredMessage(seq=seq, appended_at=ts, message=message))
        if payloads:
            await self._redis.rpush(_msgs_key(id), *payloads)
            await self._redis.expire(_msgs_key(id), self._ttl)
        rec.updated_at = ts
        await self._save_record(rec)
        return stored

    async def save_provider_snapshot(self, id: str, snapshot: ProviderViewSnapshot) -> None:
        await self._redis.set(_snap_key(id), json.dumps(snapshot_to_dict(snapshot), ensure_ascii=False), ex=self._ttl)

    async def load_provider_snapshot(self, id: str) -> ProviderViewSnapshot | None:
        raw = await self._redis.get(_snap_key(id))
        return snapshot_from_dict(json.loads(raw)) if raw is not None else None

    async def update_meta(self, id: str, meta: dict[str, object]) -> SessionRecord:
        rec = await self._ensure_record(id)
        rec.meta.update(meta)
        rec.updated_at = _now_iso()
        await self._save_record(rec)
        return rec

    async def set_invoked_skills(self, id: str, skills: list[dict[str, object]]) -> None:
        rec = await self._ensure_record(id)
        rec.invoked_skills = list(skills)
        rec.updated_at = _now_iso()
        await self._save_record(rec)

    async def list(self, *, limit: int | None = None, offset: int = 0) -> list[SessionRecord]:
        keys: list[str] = []
        async for key in self._redis.scan_iter(match=f"{_PREFIX}*", count=200):
            if key.endswith(":msgs") or key.endswith(":snap"):
                continue
            keys.append(key)
        recs = [rec for raw in (await self._redis.mget(keys) if keys else []) if raw and (rec := _rec_from_json(raw))]
        recs.sort(key=lambda r: r.updated_at, reverse=True)
        if offset:
            recs = recs[offset:]
        if limit is not None:
            recs = recs[:limit]
        return recs

    async def delete(self, id: str) -> None:
        await self._redis.delete(_rec_key(id), _msgs_key(id), _snap_key(id))
        self._tasks.pop(id, None)
        self._task_counter.pop(id, None)

    async def close(self) -> None:
        await self._redis.aclose()

    # ── tasks (unused: subagent/task system is disabled) — kept in-process ───────
    async def create_task(self, session_id: str, input: CreateTaskInput) -> Task:
        task_id = str(self._task_counter.get(session_id, 1))
        self._task_counter[session_id] = int(task_id) + 1
        ts = _now_iso()
        task = Task(
            id=task_id, session_id=session_id, subject=input.subject,
            description=input.description, active_form=input.active_form, status="pending",
            metadata=dict(input.metadata or {}), created_at=ts, updated_at=ts,
        )
        self._tasks.setdefault(session_id, {})[task_id] = task
        return task

    async def get_task(self, session_id: str, task_id: str) -> Task | None:
        return self._tasks.get(session_id, {}).get(task_id)

    async def list_tasks(self, session_id: str) -> list[Task]:
        return sorted(self._tasks.get(session_id, {}).values(), key=lambda t: int(t.id))

    async def update_task(self, session_id: str, task_id: str, patch: TaskPatch) -> Task | None:
        task = self._tasks.get(session_id, {}).get(task_id)
        if task is None:
            return None
        if patch.status == "deleted":
            await self.delete_task(session_id, task_id)
            return None
        for attr in ("subject", "description", "active_form", "owner"):
            val = getattr(patch, attr, None)
            if val is not None:
                setattr(task, attr, val)
        if patch.status is not None and patch.status != "deleted":
            task.status = patch.status
        if patch.metadata is not None:
            for key, value in patch.metadata.items():
                task.metadata.pop(key, None) if value is None else task.metadata.__setitem__(key, value)
        task.updated_at = _now_iso()
        return task

    async def delete_task(self, session_id: str, task_id: str) -> bool:
        return self._tasks.get(session_id, {}).pop(task_id, None) is not None

    async def claim_task(self, session_id: str, task_id: str, owner: str) -> Task | None:
        task = self._tasks.get(session_id, {}).get(task_id)
        if task is None or task.owner is not None or task.status != "pending":
            return None
        task.owner = owner
        task.updated_at = _now_iso()
        return task

    async def ready_tasks(self, session_id: str) -> list[Task]:
        bucket = self._tasks.get(session_id, {})
        return [t for t in sorted(bucket.values(), key=lambda t: int(t.id)) if t.status == "pending" and t.owner is None]

    async def release_task(self, session_id: str, task_id: str) -> Task | None:
        task = self._tasks.get(session_id, {}).get(task_id)
        if task is None or task.status == "completed":
            return task
        task.owner = None
        task.status = "pending"
        task.updated_at = _now_iso()
        return task
