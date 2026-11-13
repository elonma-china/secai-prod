"""The v3 route reuses one keep-alive client for the private specialist."""
from __future__ import annotations

import asyncio

import httpx
import pytest

from secai_gateway.api.multilang import routes
from secai_gateway.config import Settings
from secai_gateway.multilang.config import MultilangSettings
from secai_gateway.multilang.provider import GenerationResult

MODEL = "google/madlad400-3b-mt@fa184c675da0b5c9e1c8694fccd4e12e2d422094"


def configured(**kwargs):
    values = dict(enabled=True, enable_zh=True, backend="specialist",
                  specialist_base_url="http://127.0.0.1:8941", specialist_api_key="secret",
                  specialist_model=MODEL, specialist_context_tokens=512,
                  specialist_max_output_tokens=256, specialist_chunk_input_tokens=256)
    values.update(kwargs)
    return MultilangSettings(_env_file=None, **values)


def make_app():
    return routes.create_v3_app(settings_factory=configured, legacy=Settings(_env_file=None))


async def test_client_is_created_lazily_once_and_shared():
    app = make_app()
    assert app.state.specialist_client is None
    first = routes.shared_specialist_client(app)
    assert routes.shared_specialist_client(app) is first
    assert app.state.specialist_client is first
    await first.aclose()


async def test_shared_client_keeps_the_private_no_proxy_no_redirect_posture():
    app = make_app()
    client = routes.shared_specialist_client(app)
    assert client.trust_env is False and client.follow_redirects is False
    await client.aclose()


async def test_injected_client_is_respected_untouched():
    app = make_app()
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200))) as injected:
        app.state.specialist_client = injected
        assert routes.shared_specialist_client(app) is injected


async def test_concurrent_first_requests_get_the_same_client():
    app = make_app()
    got = await asyncio.gather(*[asyncio.to_thread(lambda: None) for _ in range(1)])  # yield once
    clients = [routes.shared_specialist_client(app) for _ in range(32)]
    assert len({id(c) for c in clients}) == 1 and got == [None]
    await clients[0].aclose()


async def test_every_request_hands_the_same_client_to_the_provider(monkeypatch):
    seen = []

    class Recorder:
        def __init__(self, client, settings):
            seen.append(client)

        async def generate(self, request):
            return GenerationResult("你好。", "stop", MODEL)

    monkeypatch.setattr(routes, "SpecialistLlm", Recorder)
    monkeypatch.setattr(routes, "verified_specialist_counter", lambda settings: len)
    app = make_app()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        for _ in range(3):
            response = await client.post("/ai/v3/translate", json={"text": "Kiểm tra.",
                                                                    "source_lang": "vi", "target_lang": "zh"})
            assert response.status_code == 200, response.text
    assert len(seen) == 3 and len({id(c) for c in seen}) == 1 and seen[0] is app.state.specialist_client
    await app.state.specialist_client.aclose()


async def test_shutdown_handler_closes_the_shared_client():
    app = make_app()
    client = routes.shared_specialist_client(app)
    assert not client.is_closed
    for handler in app.router.on_shutdown:
        await handler()
    assert client.is_closed
