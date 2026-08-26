from __future__ import annotations

import time
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI

from .agent import CompletionRunner, LinchRunner
from .api import configure_middleware, register_routes
from .config import Settings, get_settings
from .state import FixedWindowRateLimiter


class GatewayState:
    """Composition root: settings + runner + rate limiter, shared by routes. Conversation
    memory is owned by the runner's linch session store (Redis) — no separate history layer."""

    def __init__(self, runner: CompletionRunner | None = None, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.runner = runner or LinchRunner(self.settings.gateway_db_path)
        self.rate_limiter = FixedWindowRateLimiter()
        self.started_at = time.monotonic()
        # Process-wide HTTP client, set during lifespan (below). Reused so the
        # input-sanitization classifier's embed hop keeps its connection alive across chat
        # requests instead of paying a fresh TCP+TLS handshake each time. None outside a
        # running app (e.g. unit tests using ASGITransport, which does not fire lifespan) —
        # callers fall back to a per-call client.
        self.http_client: httpx.AsyncClient | None = None


def create_app(runner: CompletionRunner | None = None) -> FastAPI:
    state = GatewayState(runner)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.gateway = state
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(30.0),
            limits=httpx.Limits(max_keepalive_connections=32, keepalive_expiry=60.0),
        ) as client:
            state.http_client = client
            yield
            state.http_client = None

    app = FastAPI(
        title="SECAI Agent Gateway",
        description="IAS v1-compatible security AI gateway (chat, translate, report, exploit research).",
        version="2.0.0",
        docs_url="/docs",        # Swagger UI
        redoc_url="/redoc",      # ReDoc
        openapi_url="/openapi.json",
        lifespan=lifespan,
    )
    configure_middleware(app, state)
    register_routes(app, state)
    return app


app = create_app()
