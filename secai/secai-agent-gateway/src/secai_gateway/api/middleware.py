"""CORS + fixed-window rate limiting."""
from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse


def route_limit(path: str) -> int:
    if path.endswith("/stream"):
        path = path[: -len("/stream")]  # rate-limit /ai/x/stream as /ai/x
    if path == "/ai/chat" or (path.startswith("/sessions/") and path.endswith("/chat")):
        # Raised 2026-08-27 from the shared default of 15: the contracted chatflow perf targets
        # (R-PERF-CCU ~30 concurrent threads, R-PERF-RPS ~0.6 RPS = 36 req/min) structurally exceed
        # 15 req/60s regardless of concurrency — no client-side tuning can satisfy both a 15/60s cap
        # and a 36/min target from one source IP. See KPI-VA-GEN-TEST/KET-QUA/ for the benchmark
        # that surfaced this.
        # Raised again 2026-09-20 (90->150) to give headroom for a real 30-CCU load-test benchmark
        # from a single source IP (30 concurrent x 3 requests/user = 90 requests landing inside one
        # 60s rolling window, see state/ratelimit.py's FixedWindowRateLimiter) — 90 was borderline
        # for that exact scenario. 150 gives comfortable margin above a single 30-CCU test's 90
        # requests while still bounding a real single-IP abuser (permanent change, not test-only).
        # /sessions/{sid}/chat (added by feat/session) is the session-scoped sibling of /ai/chat
        # and carries the same chatflow load — without this match it silently fell through to the
        # 15 req/60s default below, re-introducing the exact cap this fix was raised to remove.
        return 150
    if path in {"/projects", "/sessions"} or path.startswith("/projects/") or path.startswith("/sessions/"):
        # Project/Session CRUD (api/projects.py) is plain local-SQLite/session-store CRUD —
        # far lighter than an LLM call — but it backs the FE's session rail (list on every
        # page load, rename, move between projects) and, like /ai/chat above, all FE users
        # share ONE proxy IP (buckets are keyed by (client_ip, path)). The shared 15/60s
        # default throttles that legitimate multi-user traffic almost immediately, so this
        # whole surface gets the same kind of headroom as the chat routes. This check runs
        # AFTER the chat-route check above so /sessions/{sid}/chat[/stream] still gets 150,
        # not 120.
        return 120
    if path == "/ai/exploit" or path == "/ai/exploit/run":
        return 20
    if path == "/admin/refresh-models":
        return 5
    if path in {"/ai/translate", "/ai/generate-report", "/ai/generate-statistical-report"}:
        return 10
    return 15


def configure_middleware(app: FastAPI, state: Any) -> None:
    app.add_middleware(
        CORSMiddleware, allow_origins=state.settings.cors_origins, allow_credentials=False,
        # DELETE/PATCH added for the Project/Session CRUD surface (api/projects.py) — without
        # them the browser's CORS preflight (OPTIONS) rejects those verbs before the request
        # ever reaches the route.
        allow_methods=["POST", "GET", "DELETE", "PATCH"], allow_headers=["Content-Type", "X-API-Key"],
    )

    @app.middleware("http")
    async def rate_limit(request: Request, call_next):
        if request.url.path.startswith("/ai/v2/translate/"):
            origin = request.headers.get("origin", "")
            if request.method == "OPTIONS" and origin in state.settings.cors_origins:
                return JSONResponse(None, status_code=204, headers={
                    "Access-Control-Allow-Origin": origin,
                    "Access-Control-Allow-Methods": "GET, POST, DELETE, OPTIONS",
                    "Access-Control-Allow-Headers": "Content-Type, Idempotency-Key",
                    "Cache-Control": "no-store",
                })
            response = await call_next(request)
            response.headers["Cache-Control"] = "no-store"
            return response
        if request.url.path == "/health":
            return await call_next(request)
        limit = route_limit(request.url.path)
        client = request.client.host if request.client else "unknown"
        try:
            state.rate_limiter.check(client, request.url.path, limit)
        except RuntimeError:
            return JSONResponse({"detail": "rate limit exceeded"}, status_code=429)
        return await call_next(request)
