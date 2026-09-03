"""HTTP surface for the /logs viewer: the page itself, search, detail, facets, and the
realtime tail.

Everything except ``/logs`` and ``/logs/api/login`` requires a valid session cookie.
Read-only by construction — no endpoint here writes to Elasticsearch.
"""
from __future__ import annotations

import asyncio
import json
import logging
from importlib.resources import files
from typing import Any

from fastapi import Cookie, FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse

from . import auth
from .query import SEARCHABLE_FIELDS, TERM_FILTERS, build_facets_body, build_search_body

logger = logging.getLogger(__name__)

_STREAM_POLL_S = 1.0        # realtime cadence; ES refresh_interval is 1s so this is the floor
_STREAM_MAX_BATCH = 100
_STREAM_IDLE_PING_S = 20.0  # keep proxies from timing out an idle SSE connection


def _page_html() -> str:
    return files("secai_gateway.activity").joinpath("static/logs.html").read_text(encoding="utf-8")


def register_logs_ui(app: FastAPI, state: Any) -> None:
    """Attach the /logs viewer. No-op when disabled so a deployment can turn it off."""
    settings = state.settings
    if not getattr(settings, "logs_ui_enabled", True):
        return

    def _require_session(cookie: str | None) -> str:
        user = auth.verify_cookie(settings, cookie)
        if not user:
            raise HTTPException(401, "Chưa đăng nhập hoặc phiên đã hết hạn.")
        return user

    def _sink() -> Any:
        sink = getattr(state, "logsink", None)
        if sink is None or not getattr(sink, "enabled", False):
            raise HTTPException(503, "Activity log chưa được cấu hình (thiếu ELASTICSEARCH_URL).")
        return sink

    async def _search(body: dict[str, Any]) -> dict[str, Any]:
        try:
            return await _sink().search(body)
        except HTTPException:
            raise
        except Exception as exc:
            logger.warning("logs UI: Elasticsearch query failed: %s", exc)
            raise HTTPException(503, f"Không truy vấn được Elasticsearch: {exc}") from exc

    def _filters_from(request: Request) -> dict[str, Any]:
        qp = request.query_params
        return {k: qp[k] for k in TERM_FILTERS if qp.get(k)}

    def _tribool(raw: str | None) -> bool | None:
        if raw in (None, "", "all"):
            return None
        return raw.lower() in ("1", "true", "yes", "on")

    # ── page ──────────────────────────────────────────────────────────────────────────
    @app.get("/logs", include_in_schema=False)
    async def logs_page() -> HTMLResponse:
        if not auth.is_configured(settings):
            return HTMLResponse(
                "<h1>Logs UI chưa được cấu hình</h1><p>Đặt <code>LOGS_UI_PASSWORD</code> "
                "(hoặc <code>LOGS_UI_PASSWORD_SHA256</code>) rồi khởi động lại gateway.</p>",
                status_code=503)
        return HTMLResponse(_page_html())

    # ── auth ──────────────────────────────────────────────────────────────────────────
    @app.post("/logs/api/login", include_in_schema=False)
    async def logs_login(body: dict[str, Any]) -> Response:
        if not auth.is_configured(settings):
            raise HTTPException(503, "Logs UI chưa cấu hình mật khẩu.")
        username = str(body.get("username") or "")
        password = str(body.get("password") or "")
        if not auth.check_credentials(settings, username, password):
            # Small constant delay: makes trivial online guessing slower without adding a
            # timing signal of its own. The 5/60s route rate limit is the real defence.
            await asyncio.sleep(0.4)
            raise HTTPException(401, "Sai tài khoản hoặc mật khẩu.")
        cookie, max_age = auth.issue_cookie(settings, username)
        resp = JSONResponse({"ok": True, "username": username})
        resp.set_cookie(auth.COOKIE_NAME, cookie, max_age=max_age, httponly=True,
                        samesite="lax", path="/logs",
                        secure=bool(getattr(settings, "logs_ui_cookie_secure", False)))
        return resp

    @app.post("/logs/api/logout", include_in_schema=False)
    async def logs_logout() -> Response:
        resp = JSONResponse({"ok": True})
        resp.delete_cookie(auth.COOKIE_NAME, path="/logs")
        return resp

    @app.get("/logs/api/me", include_in_schema=False)
    async def logs_me(secai_logs_session: str | None = Cookie(default=None)) -> dict[str, Any]:
        return {"username": _require_session(secai_logs_session),
                "fields": sorted(SEARCHABLE_FIELDS)}

    # ── data ──────────────────────────────────────────────────────────────────────────
    @app.get("/logs/api/search", include_in_schema=False)
    async def logs_search(request: Request, date_from: str | None = None,
                          date_to: str | None = None, q: str | None = None,
                          field: str = "all", blocked: str | None = None,
                          error: str | None = None, size: int = 50,
                          after: str | None = None,
                          secai_logs_session: str | None = Cookie(default=None)) -> dict[str, Any]:
        _require_session(secai_logs_session)
        try:
            search_after = json.loads(after) if after else None
        except ValueError:
            search_after = None
        body = build_search_body(date_from=date_from, date_to=date_to, q=q, field=field,
                                 filters=_filters_from(request), blocked=_tribool(blocked),
                                 error=_tribool(error), size=size, search_after=search_after)
        raw = await _search(body)
        hits = raw.get("hits", {}).get("hits", [])
        return {
            "total": (raw.get("hits", {}).get("total") or {}).get("value", 0),
            "rows": [{"id": h.get("_id"), "sort": h.get("sort"), **(h.get("_source") or {})}
                     for h in hits],
            "next": json.dumps(hits[-1]["sort"]) if hits else None,
        }

    @app.get("/logs/api/facets", include_in_schema=False)
    async def logs_facets(request: Request, date_from: str | None = None,
                          date_to: str | None = None, q: str | None = None,
                          field: str = "all", blocked: str | None = None,
                          error: str | None = None,
                          secai_logs_session: str | None = Cookie(default=None)) -> dict[str, Any]:
        _require_session(secai_logs_session)
        body = build_facets_body(date_from=date_from, date_to=date_to, q=q, field=field,
                                 filters=_filters_from(request), blocked=_tribool(blocked),
                                 error=_tribool(error))
        raw = await _search(body)
        aggs = raw.get("aggregations", {}) or {}
        out = {name: {str(b["key"]): b["doc_count"] for b in (agg.get("buckets") or [])}
               for name, agg in aggs.items()}
        out["total"] = (raw.get("hits", {}).get("total") or {}).get("value", 0)
        return out

    @app.get("/logs/api/detail/{doc_id}", include_in_schema=False)
    async def logs_detail(doc_id: str,
                          secai_logs_session: str | None = Cookie(default=None)) -> dict[str, Any]:
        _require_session(secai_logs_session)
        raw = await _search({"size": 1, "query": {"term": {"_id": doc_id}}})
        hits = raw.get("hits", {}).get("hits", [])
        if not hits:
            raise HTTPException(404, "Không tìm thấy bản ghi.")
        return {"id": hits[0].get("_id"), **(hits[0].get("_source") or {})}

    # ── realtime tail ─────────────────────────────────────────────────────────────────
    @app.get("/logs/api/stream", include_in_schema=False)
    async def logs_stream(request: Request, after: str | None = None,
                          q: str | None = None, field: str = "all",
                          blocked: str | None = None, error: str | None = None,
                          secai_logs_session: str | None = Cookie(default=None)) -> StreamingResponse:
        """SSE tail. Walks forward through the total order ``(@timestamp, _id)`` with
        ``search_after``, so each poll picks up exactly the records added since the last
        one — no duplicates, no gaps, and no dependence on wall-clock windows."""
        _require_session(secai_logs_session)
        filters = _filters_from(request)
        blocked_f, error_f = _tribool(blocked), _tribool(error)
        try:
            cursor: list[Any] | None = json.loads(after) if after else None
        except ValueError:
            cursor = None

        async def gen():
            nonlocal cursor
            idle = 0.0
            if cursor is None:
                # No starting point given: begin from the newest record so the tail shows
                # what happens from now on rather than replaying history.
                newest = await _sink().search(build_search_body(size=1))
                hits = newest.get("hits", {}).get("hits", [])
                cursor = hits[0]["sort"] if hits else [0, ""]
            while True:
                if await request.is_disconnected():
                    return
                try:
                    body = build_search_body(q=q, field=field, filters=filters,
                                             blocked=blocked_f, error=error_f,
                                             size=_STREAM_MAX_BATCH, search_after=cursor,
                                             ascending=True)
                    raw = await _sink().search(body)
                    hits = raw.get("hits", {}).get("hits", [])
                except Exception as exc:      # ES hiccup: report once, keep the tail alive
                    yield _sse("error", {"error": str(exc)[:200]})
                    await asyncio.sleep(_STREAM_POLL_S * 3)
                    continue
                for hit in hits:
                    cursor = hit["sort"]
                    yield _sse("log", {"id": hit.get("_id"), "sort": hit.get("sort"),
                                       **(hit.get("_source") or {})})
                idle = 0.0 if hits else idle + _STREAM_POLL_S
                if idle >= _STREAM_IDLE_PING_S:
                    idle = 0.0
                    yield b": keep-alive\n\n"
                await asyncio.sleep(_STREAM_POLL_S)

        return StreamingResponse(gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache",
                                          "X-Accel-Buffering": "no"})


def _sse(event: str, payload: dict[str, Any]) -> bytes:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n".encode()
