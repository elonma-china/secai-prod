"""Project → Session REST CRUD surface (P0, backend-only).

A "project" (``state.runner.project_store``, ``ProjectStore`` in ``state/project_store.py``) groups
many "sessions". A session is a linch session living in ``state.runner.session_store`` whose
``SessionRecord.meta`` carries ``{"project_id", "owner", "title", "kind": "session"}`` — the
link back to its project is metadata-only, there is no FK enforced by the session store.

Every handler reaches shared state via ``request.app.state.gateway`` (set in ``app.py`` at
app-start — see ``GatewayState`` in ``app.py``), matching the module-level-``APIRouter``
pattern used by ``api/showcase/*``. Nothing here is imported at module scope from either
store, so importing this module never requires ``state.runner.project_store`` to exist yet.

Owner scoping: a missing/blank ``user_id`` is normalized to ``"anonymous"``. A caller may only
see/mutate rows whose owner matches theirs. Reads (GET) return 404 on a mismatch, to avoid
leaking whether a row exists at all; mutations (POST .../sessions, PATCH, DELETE) return 403.

Note: ``ProjectStore`` methods (create/get/list/rename/delete) are synchronous sqlite calls —
no ``await``. ``SessionStore`` methods (create/load/list/delete/load_messages/update_meta) are
the linch async protocol — always ``await``.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Query, Request

from linch.types import Message, message_to_dict

from ..agent.hooks import CHAT_PRE_FETCH_MARKER
from .schemas import (
    CreateProjectRequest,
    CreateSessionRequest,
    RenameProjectRequest,
    UpdateSessionRequest,
)

router = APIRouter(tags=["projects"])


def _not_found(message: str) -> HTTPException:
    return HTTPException(status_code=404, detail={"error": "not_found", "message": message})


def _forbidden(message: str) -> HTTPException:
    return HTTPException(status_code=403, detail={"error": "forbidden", "message": message})


def _bad_request(message: str) -> HTTPException:
    return HTTPException(status_code=400, detail={"error": "bad_request", "message": message})


# linch's own ``build_user_message()`` (linch/loop/request.py) unconditionally prepends this
# EXACT block as a separate content block to EVERY user turn it persists — there is no public
# hook/config on linch.Agent to suppress it at the source (verified: it's a hardcoded literal
# in a free function, not gated by any Agent/RunOptions field). It carries no sensitive data
# (just today's date), but it IS internal-prompt scaffolding the chat UI should not render, so
# it is stripped here — at the API-response serialization layer ONLY. The underlying session
# store row is left untouched (no schema/persisted-data change, just what this endpoint returns).
_ENV_DATE_BLOCK_RE = re.compile(r"^<env>\nToday's date: \d{4}-\d{2}-\d{2}\n</env>$")

# 2026-09-21 (Opus review round 2, MAJOR-2): sessions created BEFORE the ephemeral-context fix
# (agent/hooks.py::EphemeralTurnContextHook) have the KB-grounding block baked directly into
# their ALREADY-PERSISTED user-turn text — the leak this whole fix targets, just already written
# to the store for any session that had a turn before the fix landed. The env-block strip above
# does nothing for those (the contaminated text is the SECOND block, not the env one). This marks
# the split point ``_ground_chat_prompt`` (api/routes.py, pre-fix) always inserted right before
# the real question: ``CHAT_PRE_FETCH_MARKER + " ... ]\n" + kb_dump + "\n\n" + _SPLIT + prompt``.
_CHAT_PRE_FETCH_SPLIT = "[Câu hỏi của người dùng]\n"


def _recover_pre_fetch_question(text: str) -> str | None:
    """For a user-turn text block starting with ``CHAT_PRE_FETCH_MARKER`` (an old,
    pre-fix contaminated turn — see the comment above), recover just the user's
    original question: the substring after the LAST ``_CHAT_PRE_FETCH_SPLIT`` marker
    (``rfind``, in case the KB dump itself happens to contain that literal phrase).
    Returns ``None`` when the split marker isn't present at all — the caller then
    drops the whole block rather than showing the raw marker+KB-dump text."""
    idx = text.rfind(_CHAT_PRE_FETCH_SPLIT)
    if idx == -1:
        return None
    return text[idx + len(_CHAT_PRE_FETCH_SPLIT):]


def _display_message_dict(message: Message) -> dict[str, Any]:
    """``message_to_dict(message)``, cleaned for display:

    - Strips linch's own injected ``<env>...</env>`` date preamble block (see
      ``_ENV_DATE_BLOCK_RE`` above) — any role, harmless either way (only ever appears
      on user turns in practice).
    - For role=``user`` turns ONLY (assistant/tool content is never touched): a text
      block contaminated by the pre-fix grounding leak (starts with
      ``CHAT_PRE_FETCH_MARKER``) is replaced by just the recovered original question
      (``_recover_pre_fetch_question``), or dropped entirely if the question can't be
      recovered — never shown raw.

    Never ends up with empty ``content`` — falls back to the unfiltered blocks if every
    block would otherwise be dropped, so a (theoretical) content-only-contaminated
    message still round-trips with SOMETHING rather than vanishing. This is a response-
    serialization-layer transform only: the underlying session-store row is untouched
    (no schema/persisted-data change)."""
    out = message_to_dict(message)
    content = out.get("content")
    if not isinstance(content, list):
        return out
    is_user = out.get("role") == "user"
    filtered: list[Any] = []
    for block in content:
        if not (isinstance(block, dict) and block.get("type") == "text"):
            filtered.append(block)
            continue
        text = block.get("text") or ""
        if _ENV_DATE_BLOCK_RE.match(text):
            continue
        if is_user and text.startswith(CHAT_PRE_FETCH_MARKER):
            recovered = _recover_pre_fetch_question(text)
            if recovered is None:
                continue  # can't recover the real question — drop the grounding dump
            filtered.append({**block, "text": recovered})
            continue
        filtered.append(block)
    if filtered:
        out["content"] = filtered
    return out


def _norm_owner(user_id: str | None) -> str:
    """Missing/blank user_id → "anonymous", matching the anonymous-caller default used
    throughout the gateway (see LegacyRequest.user_id)."""
    s = str(user_id).strip() if user_id is not None else ""
    return s or "anonymous"


def _iso_ts(ts: Any) -> Any:
    """Project rows store created_at/updated_at as epoch ints (ProjectStore, on disk);
    session rows already emit ISO-8601 strings. Normalize the API output shape to match
    the sibling resource without touching how ProjectStore persists the value."""
    if isinstance(ts, str):
        return ts
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def _project_out(rec: dict[str, Any]) -> dict[str, Any]:
    return {
        "project_id": rec["project_id"], "name": rec["name"], "owner": rec["owner"],
        "created_at": _iso_ts(rec["created_at"]), "updated_at": _iso_ts(rec["updated_at"]),
    }


def _session_out(rec: Any) -> dict[str, Any]:
    """SessionOut: the shape returned by every session create/list/patch endpoint. Only
    ADDS fields to the original 4-key shape ({session_id,title,created_at,updated_at}) —
    ``project_id`` (null for a standalone session) and ``last_activity`` (falls back to
    ``created_at`` for a session that has never been chatted in)."""
    meta = rec.meta or {}
    return {
        "session_id": rec.id, "title": meta.get("title", ""),
        "created_at": rec.created_at, "updated_at": rec.updated_at,
        "project_id": meta.get("project_id"),
        "last_activity": meta.get("last_activity") or rec.created_at,
    }


def _is_project_session(rec: Any, pid: str) -> bool:
    meta = rec.meta or {}
    return meta.get("project_id") == pid and meta.get("kind") == "session"


def _sort_by_last_activity(records: list[Any]) -> list[Any]:
    """Sort session records newest-active-first. NOT ``updated_at`` — a rename bumps
    ``updated_at`` (SessionStore.update_meta always does) but must not reorder the list,
    only a chat turn (which stamps ``meta.last_activity``) should. Falls back to
    ``created_at`` for a session that has never been chatted in."""
    def key(rec: Any) -> str:
        meta = rec.meta or {}
        return meta.get("last_activity") or rec.created_at
    return sorted(records, key=key, reverse=True)


# ── projects ──────────────────────────────────────────────────────────────────────

@router.post("/projects")
async def create_project(body: CreateProjectRequest, request: Request) -> dict[str, Any]:
    if not body.name or not body.name.strip():
        raise _bad_request("name is required")
    state = request.app.state.gateway
    owner = _norm_owner(body.user_id)
    rec = state.runner.project_store.create(None, owner=owner, name=body.name.strip())
    return _project_out(rec)


@router.get("/projects")
async def list_projects(request: Request, user_id: str | None = Query(default=None)) -> list[dict[str, Any]]:
    state = request.app.state.gateway
    owner = _norm_owner(user_id)
    return [_project_out(rec) for rec in state.runner.project_store.list(owner=owner)]


@router.get("/projects/{pid}")
async def get_project(pid: str, request: Request, user_id: str | None = Query(default=None)) -> dict[str, Any]:
    state = request.app.state.gateway
    rec = state.runner.project_store.get(pid)
    if rec is None:
        raise _not_found(f"project not found: {pid}")
    owner = _norm_owner(user_id)
    if rec["owner"] != owner:
        # GET: 404 rather than 403, so a non-owner can't tell the project exists.
        raise _not_found(f"project not found: {pid}")
    return _project_out(rec)


@router.patch("/projects/{pid}")
async def rename_project(pid: str, body: RenameProjectRequest, request: Request) -> dict[str, Any]:
    if not body.name or not body.name.strip():
        raise _bad_request("name is required")
    state = request.app.state.gateway
    rec = state.runner.project_store.get(pid)
    if rec is None:
        raise _not_found(f"project not found: {pid}")
    owner = _norm_owner(body.user_id)
    if rec["owner"] != owner:
        raise _forbidden("not the project owner")
    updated = state.runner.project_store.rename(pid, body.name.strip())
    if updated is None:  # deleted between the get() and rename() above
        raise _not_found(f"project not found: {pid}")
    return _project_out(updated)


@router.delete("/projects/{pid}")
async def delete_project(pid: str, request: Request, user_id: str | None = Query(default=None)) -> dict[str, Any]:
    state = request.app.state.gateway
    rec = state.runner.project_store.get(pid)
    if rec is None:
        raise _not_found(f"project not found: {pid}")
    owner = _norm_owner(user_id)
    if rec["owner"] != owner:
        raise _forbidden("not the project owner")
    store = state.runner.session_store
    targets = [s for s in await store.list() if _is_project_session(s, pid)]
    for s in targets:
        await store.delete(s.id)
    state.runner.project_store.delete(pid)
    return {"deleted": len(targets)}


# ── sessions (scoped under a project, plus flat /sessions/{sid} lookups) ───────────

@router.post("/projects/{pid}/sessions")
async def create_session(pid: str, body: CreateSessionRequest, request: Request) -> dict[str, Any]:
    state = request.app.state.gateway
    proj = state.runner.project_store.get(pid)
    if proj is None:
        raise _not_found(f"project not found: {pid}")
    owner = _norm_owner(body.user_id)
    if proj["owner"] != owner:
        raise _forbidden("not the project owner")
    store = state.runner.session_store
    sid = f"proj:{pid}:sess:{uuid4().hex}"
    title = (body.title or "").strip()
    rec = await store.create(id=sid, meta={"project_id": pid, "owner": owner, "title": title, "kind": "session"})
    return _session_out(rec)


@router.get("/projects/{pid}/sessions")
async def list_project_sessions(pid: str, request: Request, user_id: str | None = Query(default=None)) -> list[dict[str, Any]]:
    state = request.app.state.gateway
    proj = state.runner.project_store.get(pid)
    if proj is None:
        raise _not_found(f"project not found: {pid}")
    owner = _norm_owner(user_id)
    if proj["owner"] != owner:
        # GET: 404 rather than 403, so a non-owner can't tell the project exists.
        raise _not_found(f"project not found: {pid}")
    store = state.runner.session_store
    rows = [s for s in await store.list() if _is_project_session(s, pid)]
    return [_session_out(s) for s in _sort_by_last_activity(rows)]


# ── sessions (standalone — belong to NO project, like a ChatGPT top-level chat) ─────

_MAX_TITLE_LEN = 200


@router.post("/sessions")
async def create_standalone_session(body: CreateSessionRequest, request: Request) -> dict[str, Any]:
    """Create a session that belongs to no project. Provisioning a project-scoped
    session is a different route (``POST /projects/{pid}/sessions``) — reject here
    rather than silently ignoring a caller's intent to attach one."""
    if body.project_id is not None:
        raise _bad_request(
            "project_id is not accepted here; use POST /projects/{pid}/sessions to "
            "create a project-scoped session"
        )
    title = (body.title or "").strip()
    if len(title) > _MAX_TITLE_LEN:
        raise _bad_request(f"title must be at most {_MAX_TITLE_LEN} characters")
    state = request.app.state.gateway
    owner = _norm_owner(body.user_id)
    store = state.runner.session_store
    sid = f"sess:{uuid4().hex}"
    rec = await store.create(id=sid, meta={"project_id": None, "owner": owner, "title": title, "kind": "session"})
    return _session_out(rec)


@router.get("/sessions")
async def list_sessions(
    request: Request,
    user_id: str | None = Query(default=None),
    scope: Literal["standalone", "all"] = Query(default="standalone"),
) -> list[dict[str, Any]]:
    """List the caller's own sessions (``kind == "session"`` only — legacy ``chat:*`` /
    ``exploit:*`` rows, and any row missing ``kind``, never appear here). Default scope
    is ``standalone`` (no ``project_id``) — the ChatGPT-style top-level chat list; pass
    ``scope=all`` to also include project-scoped sessions. Sorted by ``last_activity``
    desc, not ``updated_at`` (see ``_sort_by_last_activity``). No pagination."""
    state = request.app.state.gateway
    owner = _norm_owner(user_id)
    store = state.runner.session_store
    rows = [
        s for s in await store.list()
        if (s.meta or {}).get("kind") == "session"
        and (s.meta or {}).get("owner") == owner
        and (scope == "all" or (s.meta or {}).get("project_id") is None)
    ]
    return [_session_out(s) for s in _sort_by_last_activity(rows)]


@router.patch("/sessions/{sid}")
async def update_session(sid: str, body: UpdateSessionRequest, request: Request) -> dict[str, Any]:
    """Rename a session and/or move it into/out of a project. Moving is metadata-only —
    the session keeps its own id (``sess:...`` or ``proj:{pid}:sess:...``) regardless of
    which project (if any) it currently belongs to; membership is decided purely by
    ``meta.project_id`` (see ``_is_project_session``)."""
    state = request.app.state.gateway
    store = state.runner.session_store
    rec = await store.load(sid)
    if rec is None or (rec.meta or {}).get("kind") != "session":
        # 404 also for a legacy row (chat:*/exploit:*, or anything missing kind) — this
        # route only ever operates on rows this feature itself created.
        raise _not_found(f"session not found: {sid}")
    owner = _norm_owner(body.user_id)
    if (rec.meta or {}).get("owner") != owner:
        raise _forbidden("not the session owner")

    fields_set = body.model_fields_set
    updates: dict[str, Any] = {}

    if "title" in fields_set:
        title = (body.title or "").strip()
        if not title:
            raise _bad_request("title must not be blank")
        if len(title) > _MAX_TITLE_LEN:
            raise _bad_request(f"title must be at most {_MAX_TITLE_LEN} characters")
        updates["title"] = title

    if "project_id" in fields_set:
        new_pid = body.project_id
        current_pid = (rec.meta or {}).get("project_id")
        if new_pid is not None and new_pid != current_pid:
            proj = state.runner.project_store.get(new_pid)
            if proj is None or proj["owner"] != owner:
                raise _not_found(f"project not found: {new_pid}")
        updates["project_id"] = new_pid

    if not updates:
        raise _bad_request("nothing to update")

    updated = await store.update_meta(sid, updates)
    return _session_out(updated)


@router.get("/sessions/{sid}")
async def get_session(sid: str, request: Request, user_id: str | None = Query(default=None)) -> dict[str, Any]:
    state = request.app.state.gateway
    store = state.runner.session_store
    rec = await store.load(sid)
    if rec is None:
        raise _not_found(f"session not found: {sid}")
    owner = _norm_owner(user_id)
    if (rec.meta or {}).get("owner") != owner:
        # GET: 404 rather than 403, so a non-owner can't tell the session exists.
        raise _not_found(f"session not found: {sid}")
    return {"session_id": rec.id, "meta": rec.meta, "created_at": rec.created_at, "updated_at": rec.updated_at}


@router.delete("/sessions/{sid}")
async def delete_session(sid: str, request: Request, user_id: str | None = Query(default=None)) -> dict[str, Any]:
    state = request.app.state.gateway
    store = state.runner.session_store
    rec = await store.load(sid)
    if rec is None:
        raise _not_found(f"session not found: {sid}")
    owner = _norm_owner(user_id)
    if (rec.meta or {}).get("owner") != owner:
        raise _forbidden("not the session owner")
    await store.delete(sid)
    return {"deleted": True}


@router.get("/sessions/{sid}/messages")
async def get_session_messages(sid: str, request: Request, user_id: str | None = Query(default=None)) -> dict[str, Any]:
    state = request.app.state.gateway
    store = state.runner.session_store
    rec = await store.load(sid)
    if rec is None:
        raise _not_found(f"session not found: {sid}")
    owner = _norm_owner(user_id)
    if (rec.meta or {}).get("owner") != owner:
        # GET: 404 rather than 403, so a non-owner can't tell the session exists.
        raise _not_found(f"session not found: {sid}")
    rows = await store.load_messages(sid)
    return {"messages": [{"seq": r.seq, "appended_at": r.appended_at, "message": _display_message_dict(r.message)} for r in rows]}
