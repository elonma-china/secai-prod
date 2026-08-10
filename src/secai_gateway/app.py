from __future__ import annotations

import time
from contextlib import asynccontextmanager

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


def create_app(runner: CompletionRunner | None = None) -> FastAPI:
    state = GatewayState(runner)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.gateway = state
        yield

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
