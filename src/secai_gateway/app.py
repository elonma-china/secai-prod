from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI

from .agent import CompletionRunner, LinchRunner
from .api import configure_middleware, register_routes
from .config import Settings, get_settings
from .sanitize.classifier import load_head, startup_head_check
from .state import FixedWindowRateLimiter

logger = logging.getLogger(__name__)


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
        # Boot-time self-check: warn loudly if the input classifier will silently fail open
        # (missing/untrained head, or head trained on a different embedding model than the one
        # this deployment sends). Otherwise the guard is quietly OFF while /health says ok.
        if getattr(state.settings, "input_classifier_enabled", True):
            warn = startup_head_check(load_head(state.settings),
                                      getattr(state.settings, "embedding_model", ""))
            if warn:
                logger.warning(warn)
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
