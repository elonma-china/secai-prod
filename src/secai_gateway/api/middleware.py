"""CORS + fixed-window rate limiting."""
from __future__ import annotations

import math
import secrets
import time
from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.responses import Response


def route_limit(path: str) -> int:
    if path.endswith("/stream"):
        path = path[: -len("/stream")]  # rate-limit /ai/x/stream as /ai/x
    if path == "/ai/chat" or path == "/ai/conversations/chat" or (
        path.startswith("/sessions/") and path.endswith("/chat")
    ):
        # The conversation-scoped chat endpoint is the same chatflow as /ai/chat (just
        # multi-conversation), so it inherits the same 90/60s headroom. `/stream` was
        # already normalized above.
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
    if path.startswith("/review-api/") and path != "/review-api/live-pairs" and not path.startswith("/review-api/report-templates/"):
        # Review CRUD shares the Devspace proxy IP and refreshes after each vote.
        # Keep model-backed live/template routes at their existing limit.
        return 120
    if path == "/ai/exploit" or path == "/ai/exploit/run":
        return 20
    if path == "/admin/refresh-models":
        return 5
    if path in {"/ai/translate", "/ai/generate-report", "/ai/generate-statistical-report"}:
        return 10
    return 15


# Literal path segments of the /review-api/* surface; every other segment is an identifier.
_REVIEW_STATIC_SEGMENTS = frozenset({
    "overview", "approvals", "preview", "bulk", "me", "reviewers", "campaigns", "imports", "items",
    "stats", "export", "assignments", "judgment", "judgments", "adjudication", "live-settings",
    "live-pairs", "report-templates", "revise", "render", "reward", "score", "training", "jobs",
    "eligibility", "batches", "retry", "runs", "events",
})


def rate_limit_key(path: str) -> str:
    """Bucket key for the limiter: a route template for /review-api/*, else the exact path.

    Keying by exact path let a client dodge the limit (and grow the bucket dict without bound)
    by changing an {id} segment on every request. Done by segment vocabulary rather than router
    introspection so it does not depend on FastAPI internals.
    """
    if not path.startswith("/review-api/"):
        return path
    segments = [seg for seg in path[len("/review-api/"):].split("/") if seg]
    if len(segments) > 6:
        return "/review-api/*deep"
    return "/review-api/" + "/".join(seg if seg in _REVIEW_STATIC_SEGMENTS else "{id}" for seg in segments)


def retry_after(limiter: Any, client: str, key: str) -> int:
    """Seconds until the oldest request leaves the 60s window (never below 1)."""
    stamps = getattr(limiter, "_buckets", {}).get((client, key)) or []
    if not stamps:
        return 60
    return max(1, min(60, math.ceil(min(stamps) + 60 - time.monotonic())))


def configure_middleware(app: FastAPI, state: Any) -> None:
    app.add_middleware(
        CORSMiddleware, allow_origins=state.settings.cors_origins, allow_credentials=False,
        # DELETE/PATCH added for the Project/Session CRUD surface (api/projects.py) — without
        # them the browser's CORS preflight (OPTIONS) rejects those verbs before the request
        # ever reaches the route.
        allow_methods=["POST", "GET", "PUT", "DELETE", "PATCH"],
        allow_headers=["Content-Type", "X-API-Key", "Authorization"],
    )

    @app.middleware("http")
    async def rate_limit(request: Request, call_next):
        if request.url.path.startswith("/ai/v2/translate/"):
            origin = request.headers.get("origin", "")
            if request.method == "OPTIONS":
                method = request.headers.get("access-control-request-method", "")
                headers = {item.strip().lower() for item in
                           request.headers.get("access-control-request-headers", "").split(",") if item.strip()}
                if (origin in state.settings.cors_origins and method in {"GET", "POST", "DELETE"}
                        and headers <= {"content-type", "idempotency-key"}):
                    return Response(status_code=204, headers={
                        "Access-Control-Allow-Origin": origin,
                        "Access-Control-Allow-Methods": "GET, POST, DELETE, OPTIONS",
                        "Access-Control-Allow-Headers": "Content-Type, Idempotency-Key",
                        "Cache-Control": "no-store", "Vary": "Origin",
                    })
                return JSONResponse({"error": {"code": "INVALID_REQUEST",
                                               "message": "CORS preflight is invalid.",
                                               "request_id": secrets.token_hex(12)}},
                                    status_code=400, headers={"Cache-Control": "no-store"})
            response = await call_next(request)
            if response.status_code in {404, 405}:
                # Starlette's unmatched-route response uses {"detail": ...}; the
                # anonymous v2 surface always returns the same safe error shape.
                missing = response.status_code == 404
                response = JSONResponse({"error": {"code": "JOB_NOT_FOUND" if missing else "INVALID_REQUEST",
                                                   "message": "Job was not found." if missing else "Invalid request.",
                                                   "request_id": secrets.token_hex(12)}},
                                        status_code=response.status_code)
            response.headers["Cache-Control"] = "no-store"
            return response
        if request.url.path == "/health":
            return await call_next(request)
        limit = route_limit(request.url.path)
        client = request.client.host if request.client else "unknown"
        key = rate_limit_key(request.url.path)
        try:
            state.rate_limiter.check(client, key, limit)
        except RuntimeError:
            return JSONResponse({"detail": "rate limit exceeded"}, status_code=429,
                                headers={"Retry-After": str(retry_after(state.rate_limiter, client, key))})
        return await call_next(request)
