from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI

from .agent import CompletionRunner, LinchRunner
from .api import configure_middleware, register_routes
from .config import Settings, get_settings
from .sanitize.classifier import load_head, startup_head_check
from .state import FixedWindowRateLimiter

# Without this, the root logger defaults to WARNING and every `logger.info(...)` in the
# codebase (input-sanitize decisions, github lookups, ...) is silently dropped — only
# .warning()/.exception() ever reached the process's stderr via logging's last-resort handler.
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


class GatewayState:
    """Composition root: settings + runner + rate limiter, shared by routes. Conversation
    memory is owned by the runner's linch session store (Redis) — no separate history layer."""

    def __init__(self, runner: CompletionRunner | None = None, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.review_credentials = {}
        self.review_store = None
        if self.settings.review_enabled:
            from .api.review_auth import load_credentials
            from .state.review_store import ReviewStore
            self.review_credentials = load_credentials(self.settings.review_credentials_file)
            self.review_store = ReviewStore(self.settings.review_db_path)
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
        # Boot-time self-check: warn loudly if the input classifier will silently fail open
        # (missing/untrained head, or head trained on a different embedding model than the one
        # this deployment sends). Otherwise the guard is quietly OFF while /health says ok.
        if getattr(state.settings, "input_classifier_enabled", True):
            warn = startup_head_check(load_head(state.settings),
                                      getattr(state.settings, "embedding_model", ""))
            if warn:
                logger.warning(warn)
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
    if state.settings.review_enabled:
        app.state.gateway = state
    configure_middleware(app, state)
    register_routes(app, state)
    from .api import projects as _projects_router
    app.include_router(_projects_router.router)
    if state.settings.review_enabled:
        from .api.reviews import router as review_router
        app.include_router(review_router)
    return app


app = create_app()
