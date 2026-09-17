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

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Query, Request

from linch.types import message_to_dict

from .schemas import CreateProjectRequest, CreateSessionRequest, RenameProjectRequest

router = APIRouter(tags=["projects"])


def _not_found(message: str) -> HTTPException:
    return HTTPException(status_code=404, detail={"error": "not_found", "message": message})


def _forbidden(message: str) -> HTTPException:
    return HTTPException(status_code=403, detail={"error": "forbidden", "message": message})


def _bad_request(message: str) -> HTTPException:
    return HTTPException(status_code=400, detail={"error": "bad_request", "message": message})


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
    meta = rec.meta or {}
    return {
        "session_id": rec.id, "title": meta.get("title", ""),
        "created_at": rec.created_at, "updated_at": rec.updated_at,
    }


def _is_project_session(rec: Any, pid: str) -> bool:
    meta = rec.meta or {}
    return meta.get("project_id") == pid and meta.get("kind") == "session"


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
    title = body.title or ""
    await store.create(id=sid, meta={"project_id": pid, "owner": owner, "title": title, "kind": "session"})
    return {"session_id": sid, "project_id": pid, "title": title}


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
    return [_session_out(s) for s in await store.list() if _is_project_session(s, pid)]


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
    return {"messages": [{"seq": r.seq, "appended_at": r.appended_at, "message": message_to_dict(r.message)} for r in rows]}
