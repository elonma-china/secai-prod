"""Optional document-agent ASGI composition; legacy app remains the default entrypoint."""
from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI

from .app import app as base_app
from .config import get_settings


def create_document_app(base_app: FastAPI, scope_store: Any, backend: Any, runner: Any, settings: Any) -> FastAPI:
    if not getattr(settings, "document_agent_enabled", False):
        return base_app
    if scope_store is None or backend is None or runner is None:
        raise ValueError("enabled document agent requires scope store, backend and runner")
    from .api.document_agent.routes import build_router

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        async with base_app.router.lifespan_context(base_app):
            await asyncio.to_thread(scope_store.reap, time.time())

            async def maintain():
                while True:
                    await asyncio.sleep(60)
                    await asyncio.to_thread(scope_store.reap, time.time())

            task = asyncio.create_task(maintain())
            try:
                yield
            finally:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

    app = FastAPI(lifespan=lifespan)
    app.include_router(build_router(scope_store, backend, runner))
    app.mount("/", base_app)

    @app.middleware("http")
    async def no_store(request, call_next):
        response = await call_next(request)
        if request.url.path.startswith("/ai/v3/document-agent"):
            response.headers["Cache-Control"] = "no-store"
        return response

    return app


# No production entrypoint switch. Explicitly composed deployments inject an accepted OCR backend.
app = create_document_app(base_app, None, None, None, get_settings())
