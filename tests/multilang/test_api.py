from __future__ import annotations

import json
import asyncio
from contextlib import asynccontextmanager

import httpx
import pytest
from fastapi import FastAPI

from secai_gateway.api.multilang import routes
from secai_gateway.config import Settings
from secai_gateway.multilang.config import EXPECTED_MODEL, MultilangSettings
from secai_gateway.multilang.provider import GenerationRequest, HttpLlm
from secai_gateway.multilang_app import Dispatcher, create_app

from .test_core import EchoLiteralLlm


def configured(**overrides):
    values = {"enabled": True, "enable_zh": True, "context_tokens": 32768,
              "chat_template_overhead_tokens": 128}
    values.update(overrides)
    return MultilangSettings(_env_file=None, **values)


@pytest.fixture
def incumbent(monkeypatch):
    monkeypatch.setenv("QWEN_MODEL", EXPECTED_MODEL)
    monkeypatch.setenv("QWEN_BASE_URL", "http://127.0.0.1:9/v1")
    monkeypatch.setenv("LLM_PROVIDER", "vllm")
    # Pin CORS per test so wildcard or explicit origins from the caller's .env cannot leak in.
    monkeypatch.setenv("ALLOWED_ORIGINS", "https://allowed.example")
    monkeypatch.setattr(routes, "verified_counter", lambda settings: lambda text: len(text))
    return Settings(_env_file=None)


@pytest.mark.asyncio
async def test_api_wire_errors_headers_and_no_tools(incumbent):
    fake = EchoLiteralLlm()
    app = routes.create_v3_app(settings_factory=configured, legacy=incumbent, llm=fake)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://test") as client:
        cap = await client.get("/ai/v3/translate/languages")
        assert cap.status_code == 200
        assert len(cap.json()["pairs"]) == 18
        assert cap.headers["cache-control"] == "no-store"
        invalid = await client.post("/ai/v3/translate", json={"text": "abc", "source_lang": "vi",
                                                              "target_lang": "zh", "model": "bad"})
        assert invalid.status_code == 400
        assert invalid.json()["error"]["code"] == "INVALID_REQUEST"
        assert invalid.headers["x-request-id"] == invalid.json()["error"]["request_id"]
        disabled = await client.post("/ai/v3/translate", json={"text": "Một câu.",
                                                               "source_lang": "vi", "target_lang": "ja"})
        assert disabled.status_code == 403 and not fake.calls
        success = await client.post("/ai/v3/translate", json={"text": "Kiểm tra CVE-2024-1234.",
                                                              "source_lang": " VI ", "target_lang": "zh"})
        assert success.status_code == 200, success.text
        data = success.json()
        assert set(data) == {"translated_text", "source_lang", "target_lang", "warnings", "metadata"}
        assert data["source_lang"] == "vi" and data["target_lang"] == "zh-Hans"
        assert "CVE-2024-1234" in data["translated_text"]
        assert success.headers["x-request-id"] == data["metadata"]["request_id"]
        assert "nosniff" == success.headers["x-content-type-options"]
        assert all("tool" not in call.__dict__ for call in fake.calls)


@pytest.mark.asyncio
async def test_composition_off_on_legacy_and_cors(incumbent):
    off = create_app(settings_factory=lambda: configured(enabled=False), llm=EchoLiteralLlm())
    assert not isinstance(off, Dispatcher)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=off),
                                 base_url="http://test") as client:
        assert (await client.get("/ai/v3/translate/languages")).status_code == 404
        assert (await client.get("/health")).status_code == 200
    on = create_app(settings_factory=configured, llm=EchoLiteralLlm())
    assert isinstance(on, Dispatcher)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=on),
                                 base_url="http://test") as client:
        assert (await client.get("/health")).status_code == 200
        allowed = await client.options("/ai/v3/translate", headers={
            "Origin": "https://allowed.example", "Access-Control-Request-Method": "POST"})
        assert allowed.headers.get("access-control-allow-origin") == "https://allowed.example"
        preflight = await client.options("/ai/v3/translate", headers={
            "Origin": "https://untrusted.example", "Access-Control-Request-Method": "POST"})
        assert preflight.headers.get("access-control-allow-origin") != "*"
        assert (await client.get("/ai/v3/translate/languages")).status_code == 200


@pytest.mark.asyncio
async def test_quota_is_separate_and_has_wire_error(incumbent):
    app = routes.create_v3_app(settings_factory=lambda: configured(rate_limit_per_minute=1),
                               legacy=incumbent, llm=EchoLiteralLlm())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://test") as client:
        assert (await client.get("/ai/v3/translate/languages")).status_code == 200
        payload = {"text": "Dịch văn bản này.", "source_lang": "vi", "target_lang": "zh"}
        assert (await client.post("/ai/v3/translate", json=payload)).status_code == 200
        limited = await client.post("/ai/v3/translate", json=payload,
                                    headers={"X-Forwarded-For": "203.0.113.1"})
        assert limited.status_code == 429
        assert limited.json()["error"]["code"] == "RATE_LIMITED"
        assert int(limited.headers["retry-after"]) >= 1


@pytest.mark.asyncio
async def test_http_port_no_tools_fixed_model_and_reject_tool_call():
    outbound = []

    def handler(request):
        outbound.append((str(request.url), json.loads(request.content)))
        return httpx.Response(200, json={"model": EXPECTED_MODEL, "choices": [{
            "finish_reason": "stop", "message": {"content": "检查。"}}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        port = HttpLlm(client, "http://incumbent.invalid/v1", "dummy")
        result = await port.generate(GenerationRequest("system", "source", 100))
    assert result.model == EXPECTED_MODEL
    url, payload = outbound[0]
    assert url == "http://incumbent.invalid/v1/chat/completions"
    assert payload["model"] == EXPECTED_MODEL
    assert payload["stream"] is False
    assert "tools" not in payload and "parallel_tool_calls" not in payload
    assert [x["role"] for x in payload["messages"]] == ["system", "user"]

    async def bad_response(request):
        return httpx.Response(200, json={"model": EXPECTED_MODEL, "choices": [{
            "finish_reason": "tool_calls", "message": {"content": "", "tool_calls": [{}]}}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(bad_response)) as client:
        with pytest.raises(Exception, match="OUTPUT_VALIDATION_FAILED"):
            await HttpLlm(client, "http://incumbent.invalid/v1", "dummy").generate(
                GenerationRequest("system", "source", 100))


@pytest.mark.asyncio
async def test_body_limit_and_admission_cancellation(incumbent):
    class Hanging:
        def __init__(self):
            self.entered = asyncio.Event()
            self.cancelled = asyncio.Event()

        async def generate(self, request):
            self.entered.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.cancelled.set()
                raise

    fake = Hanging()
    app = routes.create_v3_app(settings_factory=lambda: configured(max_body_bytes=1024,
        max_active_requests=1, request_timeout_seconds=1), legacy=incumbent, llm=fake)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://test") as client:
        too_big = await client.post("/ai/v3/translate", content=b"x" * 1025,
                                    headers={"Content-Type": "application/json"})
        assert too_big.status_code == 413
        payload = {"text": "Dịch câu này đi.", "source_lang": "vi", "target_lang": "zh"}
        first = asyncio.create_task(client.post("/ai/v3/translate", json=payload))
        await fake.entered.wait()
        second = await client.post("/ai/v3/translate", json=payload)
        assert second.status_code == 429
        assert (await first).status_code == 504
        assert fake.cancelled.is_set()


@pytest.mark.asyncio
async def test_malformed_json_and_surrogate_are_safe_errors(incumbent):
    fake = EchoLiteralLlm()
    app = routes.create_v3_app(settings_factory=configured, legacy=incumbent, llm=fake)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://test") as client:
        malformed = await client.post("/ai/v3/translate", content=b'{"text":',
                                      headers={"Content-Type": "application/json"})
        assert malformed.status_code == 400
        assert malformed.json()["error"]["code"] == "INVALID_REQUEST"
        surrogate = await client.post("/ai/v3/translate",
            content=b'{"text":"ab\\ud800cd","source_lang":"vi","target_lang":"zh"}',
            headers={"Content-Type": "application/json"})
        assert surrogate.status_code == 422
        assert surrogate.json()["error"]["code"] == "UNSAFE_UNICODE"
        assert "ab\\ud800cd" not in surrogate.text and "ud800" not in surrogate.text
        assert not fake.calls


@pytest.mark.asyncio
async def test_model_identity_mismatch_no_inference(incumbent):
    fake = EchoLiteralLlm()
    wrong = incumbent.model_copy(update={"qwen_model": "Qwen/Qwen3-32B"})
    app = routes.create_v3_app(settings_factory=configured, legacy=wrong, llm=fake)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://test") as client:
        response = await client.post("/ai/v3/translate", json={"text": "Dịch câu này.",
            "source_lang": "vi", "target_lang": "zh"})
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "MODEL_UNAVAILABLE"
    assert not fake.calls


@pytest.mark.asyncio
async def test_dispatcher_forwards_lifespan_and_legacy_scope():
    events = []

    @asynccontextmanager
    async def lifespan(app):
        events.append("startup")
        yield
        events.append("shutdown")

    legacy = FastAPI(lifespan=lifespan)
    v3 = FastAPI()
    dispatcher = Dispatcher(legacy, v3)
    queue = asyncio.Queue()
    await queue.put({"type": "lifespan.startup"})
    await queue.put({"type": "lifespan.shutdown"})
    sent = []

    async def receive():
        return await queue.get()

    async def send(message):
        sent.append(message["type"])

    await dispatcher({"type": "lifespan"}, receive, send)
    assert sent == ["lifespan.startup.complete", "lifespan.shutdown.complete"]
    assert events == ["startup", "shutdown"]
    assert v3.state.http_client is None
