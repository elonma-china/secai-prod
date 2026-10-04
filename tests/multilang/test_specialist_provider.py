from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from secai_gateway.api.multilang import routes
from secai_gateway.config import Settings
from secai_gateway.multilang.config import MultilangSettings
from secai_gateway.multilang.errors import TranslationError
from secai_gateway.multilang.provider import GenerationRequest, SpecialistLlm
from secai_gateway.multilang.service import TranslationRequest, translate


MODEL = "google/madlad400-3b-mt@fa184c675da0b5c9e1c8694fccd4e12e2d422094"


def configured(**kwargs):
    values = dict(enabled=True, enable_zh=True, enable_ar=True, backend="specialist",
                  specialist_base_url="http://127.0.0.1:8941", specialist_api_key="secret",
                  specialist_model=MODEL, specialist_context_tokens=512,
                  specialist_max_output_tokens=256, specialist_chunk_input_tokens=256)
    values.update(kwargs)
    return MultilangSettings(_env_file=None, **values)


@pytest.mark.asyncio
async def test_specialist_sends_canonical_pair_as_private_wire_codes():
    seen = []

    def handler(request):
        seen.append((str(request.url), json.loads(request.content), request.headers["authorization"]))
        return httpx.Response(200, json={"translated_text": "检查。", "model": MODEL})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), trust_env=False) as client:
        port = SpecialistLlm(client, configured())
        result = await port.generate(GenerationRequest("system", "Dịch câu này.", 256,
                                                        source_lang="vi", target_lang="zh-Hans"))
    assert result.text == "检查。" and result.model == MODEL and result.stop_reason == "stop"
    assert seen == [("http://127.0.0.1:8941/v1/translate",
                     {"text": "Dịch câu này.", "source_lang": "vi", "target_lang": "zh"},
                     "Bearer secret")]


@pytest.mark.asyncio
async def test_specialist_rejects_mismatch_unavailable_and_unbounded_response():
    for response in (httpx.Response(200, json={"translated_text": "检查。", "model": "wrong"}),
                     httpx.Response(503),
                     httpx.Response(200, content=b"x" * 262145)):
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: response),
                                     trust_env=False) as client:
            with pytest.raises(TranslationError, match="MODEL_UNAVAILABLE"):
                await SpecialistLlm(client, configured()).generate(GenerationRequest(
                    "system", "Dịch câu này.", 256, source_lang="vi", target_lang="zh-Hans"))


def test_specialist_rejects_public_or_unconfigured_endpoint():
    for changes in (dict(specialist_base_url="https://example.com"),
                    dict(specialist_base_url="http://10.0.0.1"),
                    dict(specialist_base_url="https://169.254.169.254:443"),
                    dict(specialist_base_url="https://0.0.0.0:443"),
                    dict(specialist_base_url="http://127.0.0.1:bad"),
                    dict(specialist_api_key="")):
        with pytest.raises(TranslationError, match="MODEL_UNAVAILABLE"):
            SpecialistLlm(httpx.AsyncClient(), configured(**changes))


@pytest.mark.asyncio
async def test_route_uses_structured_pair_concurrently_and_preserves_literals(monkeypatch):
    seen = []

    async def handler(request):
        body = json.loads(request.content)
        seen.append(body)
        markers = __import__("re").findall(r"⟦ML-[A-Za-z0-9_-]+-\d+⟧", body["text"])
        output = ("检查 " if body["target_lang"] == "zh" else "Tiếng Việt ")
        return httpx.Response(200, json={"translated_text": output + " ".join(markers) + ".",
                                         "model": MODEL})

    monkeypatch.setattr(routes, "verified_specialist_counter", lambda settings: len)
    wrong_incumbent = Settings(_env_file=None).model_copy(update={"qwen_model": "wrong"})
    app = routes.create_v3_app(settings_factory=configured, legacy=wrong_incumbent)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), trust_env=False) as provider:
        app.state.specialist_client = provider
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://test") as client:
            a, b = await asyncio.gather(
                client.post("/ai/v3/translate", json={"text": "Kiểm tra CVE-2024-1234.",
                                                       "source_lang": "vi", "target_lang": "zh"}),
                client.post("/ai/v3/translate", json={"text": "检查 CVE-2024-1234。",
                                                       "source_lang": "ZH-HANS", "target_lang": "vi"}))
    assert a.status_code == b.status_code == 200, (a.text, b.text)
    assert "CVE-2024-1234" in a.json()["translated_text"]
    assert "CVE-2024-1234" in b.json()["translated_text"]
    assert {(x["source_lang"], x["target_lang"]) for x in seen} == {("vi", "zh"), ("zh", "vi")}


@pytest.mark.asyncio
async def test_specialist_missing_pinned_tokenizer_fails_before_provider_call():
    calls = []

    def handler(request):
        calls.append(request)
        raise AssertionError("provider must not run without tokenizer evidence")

    app = routes.create_v3_app(settings_factory=configured, legacy=Settings(_env_file=None))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), trust_env=False) as provider:
        app.state.specialist_client = provider
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://test") as client:
            response = await client.post("/ai/v3/translate", json={
                "text": "Dịch câu này.", "source_lang": "vi", "target_lang": "zh"})
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "MODEL_UNAVAILABLE"
    assert calls == []


@pytest.mark.asyncio
async def test_specialist_source_prefix_and_output_bounds_apply_before_acceptance():
    class Fake:
        async def generate(self, request):
            from secai_gateway.multilang.provider import GenerationResult
            return GenerationResult("检查。" * 100, "stop", MODEL)

    with pytest.raises(TranslationError, match="TOKEN_LIMIT_EXCEEDED"):
        await translate(TranslationRequest("Đây là câu cần dịch.", "vi", "zh"), llm=Fake(),
                        settings=configured(specialist_max_output_tokens=10), token_count=len)

    class NoCall:
        async def generate(self, request):
            raise AssertionError("encoder limit must reject before model call")

    with pytest.raises(TranslationError, match="TOKEN_LIMIT_EXCEEDED"):
        await translate(TranslationRequest("đ" * 510, "vi", "zh"), llm=NoCall(),
                        settings=configured(specialist_chunk_input_tokens=512), token_count=len)
