"""Additive ASGI composition. Use secai_gateway.multilang_app:app for staged v3."""
from __future__ import annotations

from contextlib import AsyncExitStack

import httpx

from .api.multilang.routes import create_v3_app
from .app import create_app as legacy_create_app
from .config import get_settings
from .multilang.config import MultilangSettings


class Dispatcher:
    def __init__(self, legacy, v3):
        self.legacy = legacy
        self.v3 = v3

    async def __call__(self, scope, receive, send):
        if scope["type"] == "lifespan":
            try:
                async with AsyncExitStack() as stack:
                    message = await receive()
                    if message["type"] != "lifespan.startup":
                        raise RuntimeError("invalid lifespan sequence")
                    await stack.enter_async_context(self.legacy.router.lifespan_context(self.legacy))
                    client = await stack.enter_async_context(httpx.AsyncClient(
                        timeout=httpx.Timeout(120.0),
                        limits=httpx.Limits(max_connections=2, max_keepalive_connections=2)))
                    self.v3.state.http_client = client
                    await send({"type": "lifespan.startup.complete"})
                    message = await receive()
                    if message["type"] != "lifespan.shutdown":
                        raise RuntimeError("invalid lifespan sequence")
                    self.v3.state.http_client = None
                await send({"type": "lifespan.shutdown.complete"})
            except Exception as exc:
                await send({"type": "lifespan.startup.failed", "message": type(exc).__name__})
                raise
        elif scope["type"] in ("http", "websocket") and (
            scope.get("path") == "/ai/v3/translate"
            or scope.get("path", "").startswith("/ai/v3/translate/")):
            await self.v3(scope, receive, send)
        else:
            await self.legacy(scope, receive, send)


def create_app(*, settings_factory=MultilangSettings, llm=None):
    settings = settings_factory()
    legacy = legacy_create_app()
    if not settings.enabled:
        return legacy  # exact original object and middleware graph
    v3 = create_v3_app(settings_factory=settings_factory, legacy=get_settings(), llm=llm)
    return Dispatcher(legacy, v3)


app = create_app()
