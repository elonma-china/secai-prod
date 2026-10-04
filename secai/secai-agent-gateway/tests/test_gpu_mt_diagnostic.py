"""Private Q4 diagnostic adapter checks; all upstream responses are mocked."""
from __future__ import annotations

import httpx
import pytest

from deploy.gpu_stage.mt_diagnostic import (
    GUARD_ALIAS, Q4_CANDIDATE, GuardedBackend, create_diagnostic_app,
    create_gpu_app, diagnostic_settings,
)


def _upstream(*, alias=GUARD_ALIAS, stop_type="eos", truncated=False):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok"})
        if request.url.path == "/apply-template":
            return httpx.Response(200, json={"prompt": "<|startoftext|>Translate now<|extra_0|>"})
        if request.url.path == "/tokenize":
            return httpx.Response(200, json={"tokens": [127958, 100, 127962]})
        if request.url.path == "/completion":
            return httpx.Response(200, json={"content": "Bonjour", "model": alias,
                                             "tokens": [123, 127960], "stop_type": stop_type,
                                             "truncated": truncated})
        raise AssertionError(request.url.path)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler),
                             base_url="http://127.0.0.1:18767", trust_env=False,
                             follow_redirects=False), calls


@pytest.mark.asyncio
async def test_private_envelope_reuses_reviewed_auth_and_exact_eog(tmp_path):
    upstream, calls = _upstream()
    settings = diagnostic_settings(token="private-test-token", token_file=None)
    backend = GuardedBackend(upstream)
    app = create_diagnostic_app(settings, backend)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://127.0.0.1") as client:
        response = await client.post("/v1/translate", json={"text": "Xin chào",
            "source_lang": "vi", "target_lang": "fr"},
            headers={"Authorization": "Bearer private-test-token"})
        assert response.status_code == 200
        assert response.json() == {"translated_text": "Bonjour", "model": Q4_CANDIDATE.model}
        denied = await client.post("/v1/translate", json={"text": "Xin chào",
            "source_lang": "vi", "target_lang": "fr"})
        assert denied.status_code == 401
        invalid = await client.post("/v1/translate", json={"text": "Xin chào",
            "source_lang": "vi", "target_lang": "fr", "extra": 1},
            headers={"Authorization": "Bearer private-test-token"})
        assert invalid.status_code == 400
    assert calls == ["/health", "/apply-template", "/tokenize", "/completion"]
    await upstream.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("alias,stop_type,truncated", [
    ("wrong", "eos", False), (GUARD_ALIAS, "limit", False),
    (GUARD_ALIAS, "eos", True),
])
async def test_wrong_alias_or_incomplete_generation_poison_external_guard_adapter(
        alias, stop_type, truncated):
    upstream, calls = _upstream(alias=alias, stop_type=stop_type, truncated=truncated)
    settings = diagnostic_settings(token="private-test-token", token_file=None)
    backend = GuardedBackend(upstream)
    app = create_diagnostic_app(settings, backend)
    headers = {"Authorization": "Bearer private-test-token"}
    body = {"text": "Xin chào", "source_lang": "vi", "target_lang": "fr"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://127.0.0.1") as client:
        first = await client.post("/v1/translate", json=body, headers=headers)
        second = await client.post("/v1/translate", json=body, headers=headers)
    assert first.status_code in {502, 503}
    assert second.status_code == 503
    assert calls.count("/completion") == 1
    await upstream.aclose()


def test_factory_remains_off_without_explicit_flag(monkeypatch):
    monkeypatch.delenv("GPU_MT_DIAGNOSTIC_ENABLED", raising=False)
    with pytest.raises(RuntimeError, match="disabled"):
        create_gpu_app()


def test_q4_profile_matches_actual_serving_context():
    assert Q4_CANDIDATE.gguf_eos_token_id == 3
    assert Q4_CANDIDATE.terminal_token_ids == frozenset({127960, 127967})
    assert 1024 + 512 <= 2048
