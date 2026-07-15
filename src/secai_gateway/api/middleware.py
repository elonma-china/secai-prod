"""CORS + fixed-window rate limiting."""
from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse


def route_limit(path: str) -> int:
    if path.endswith("/stream"):
        path = path[: -len("/stream")]  # rate-limit /ai/x/stream as /ai/x
    if path == "/ai/exploit":
        return 20
    if path == "/admin/refresh-models":
        return 5
    if path in {"/ai/translate", "/ai/generate-report", "/ai/generate-statistical-report"}:
        return 10
    return 15


def configure_middleware(app: FastAPI, state: Any) -> None:
    app.add_middleware(
        CORSMiddleware, allow_origins=state.settings.cors_origins, allow_credentials=False,
        allow_methods=["POST", "GET"], allow_headers=["Content-Type", "X-API-Key"],
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
