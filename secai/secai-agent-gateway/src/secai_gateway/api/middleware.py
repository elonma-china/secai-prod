"""CORS + fixed-window rate limiting."""
from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse


def route_limit(path: str) -> int:
    if path.endswith("/stream"):
        path = path[: -len("/stream")]  # rate-limit /ai/x/stream as /ai/x
    if path == "/ai/chat":
        # Raised 2026-08-27 from the shared default of 15: the contracted chatflow perf targets
        # (R-PERF-CCU ~30 concurrent threads, R-PERF-RPS ~0.6 RPS = 36 req/min) structurally exceed
        # 15 req/60s regardless of concurrency — no client-side tuning can satisfy both a 15/60s cap
        # and a 36/min target from one source IP. 90/60s gives ~2.5x headroom over the RPS target
        # while still bounding abuse; other routes are untouched. See KPI-VA-GEN-TEST/KET-QUA/ for
        # the benchmark that surfaced this.
        return 90
    if path == "/ai/exploit" or path == "/ai/exploit/run":
        return 20
    if path == "/admin/refresh-models":
        return 5
    if path in {"/ai/translate", "/ai/generate-report", "/ai/generate-statistical-report"}:
        return 10
    # /projects* and /sessions* (api/projects.py) are plain CRUD over local SQLite — far
    # lighter than an LLM call — so they just inherit the shared default below.
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
        if request.url.path == "/health":
            return await call_next(request)
        limit = route_limit(request.url.path)
        client = request.client.host if request.client else "unknown"
        try:
            state.rate_limiter.check(client, request.url.path, limit)
        except RuntimeError:
            return JSONResponse({"detail": "rate limit exceeded"}, status_code=429)
        return await call_next(request)
