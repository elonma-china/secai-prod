"""Hy-MT v3 budget evidence: local tokenizer only, never model generation."""
from __future__ import annotations

import hashlib
from pathlib import Path

import httpx
import pytest

from secai_gateway.api.multilang import routes
from secai_gateway.config import Settings
from secai_gateway.multilang.config import (HYMT_GGUF_TEMPLATE_SHA256, HyMtCounter,
                                            MultilangSettings, verified_specialist_counter)
from secai_gateway.multilang.errors import TranslationError
from secai_gateway.multilang.provider import GenerationResult
from secai_gateway.multilang.service import TranslationRequest, translate


TOKENIZER = Path("/home/ict/secai-multilang-cpu-runtime/hymt2-7b-tokenizer/tokenizer.json")
TOKENIZER_CONFIG = TOKENIZER.with_name("tokenizer_config.json")
TOKENIZER_SHA = "4185389a69284fdd3ebf737c6120bbeee36cbed9763151fa5bac5986a39e05d4"
CONFIG_SHA = "1fffdb809549222938f4a732645dd984c7c0b2973d592c2d01e05425274317da"
BASE_REVISION = "9b0eb4e8f001def3e5ff6469a0ac96fdb39ec223"


def configured(**changes):
    values = dict(enabled=True, enable_zh=True, backend="specialist",
                  specialist_profile="hymt2_7b_gguf",
                  specialist_model="tencent/Hy-MT2-7B",
                  specialist_tokenizer_json=str(TOKENIZER),
                  specialist_tokenizer_sha256=TOKENIZER_SHA,
                  specialist_tokenizer_config_json=str(TOKENIZER_CONFIG),
                  specialist_tokenizer_config_sha256=CONFIG_SHA,
                  specialist_tokenizer_revision=BASE_REVISION,
                  specialist_chat_template_sha256=HYMT_GGUF_TEMPLATE_SHA256,
                  specialist_context_tokens=4096,
                  specialist_chunk_input_tokens=896,
                  specialist_max_output_tokens=512)
    values.update(changes)
    return MultilangSettings(_env_file=None, **values)


@pytest.mark.skipif(not TOKENIZER.is_file() or not TOKENIZER_CONFIG.is_file(),
                    reason="pinned public Hy-MT tokenizer not staged on this host")
def test_real_pinned_tokenizer_counts_exact_rendered_server_prompt():
    assert hashlib.sha256(TOKENIZER.read_bytes()).hexdigest() == TOKENIZER_SHA
    assert hashlib.sha256(TOKENIZER_CONFIG.read_bytes()).hexdigest() == CONFIG_SHA
    counter = verified_specialist_counter(configured())
    assert isinstance(counter, HyMtCounter)
    text = "Xin chào"
    assert counter.prompt_tokens("zh-Hans", text) == 30
    assert counter.render_prompt("zh-Hans", text).startswith("<|startoftext|>Translate ")
    assert counter.render_prompt("zh-Hans", text).endswith("Xin chào<|extra_0|>")
    assert counter.prompt_tokens("ar", text) <= 1024


@pytest.mark.skipif(not TOKENIZER.is_file() or not TOKENIZER_CONFIG.is_file(),
                    reason="pinned public Hy-MT tokenizer not staged on this host")
@pytest.mark.parametrize("changes", [
    {"specialist_model": "tencent/Hy-MT2-1.8B"},
    {"specialist_tokenizer_sha256": "0" * 64},
    {"specialist_tokenizer_config_sha256": "0" * 64},
    {"specialist_tokenizer_revision": "wrong"},
    {"specialist_chat_template_sha256": "0" * 64},
    {"specialist_context_tokens": 8192},
    {"specialist_max_output_tokens": 256},
])
def test_wrong_hymt_profile_metadata_fails_closed(changes):
    try:
        counter = verified_specialist_counter(configured(**changes))
    except ValueError:
        return  # Invalid configuration also fails before an HTTP request.
    assert counter is None


@pytest.mark.asyncio
@pytest.mark.skipif(not TOKENIZER.is_file() or not TOKENIZER_CONFIG.is_file(),
                    reason="pinned public Hy-MT tokenizer not staged on this host")
async def test_hymt_full_prompt_budget_and_no_t5_prefix():
    counter = verified_specialist_counter(configured())
    calls = []

    class Fake:
        async def generate(self, request):
            calls.append(request)
            return GenerationResult("你好。", "stop", "tencent/Hy-MT2-7B")

    result = await translate(TranslationRequest("Xin chào.", "vi", "zh"), llm=Fake(),
                             settings=configured(), token_count=counter)
    assert result.translated_text == "你好。"
    assert len(calls) == 1 and calls[0].user == "Xin chào."
    assert calls[0].max_output_tokens == 512
    assert not calls[0].user.startswith("<2zh>")

    class NoCall:
        async def generate(self, request):
            pytest.fail("full prompt over 1024 tokens reached provider")

    long_text = "Xin chào " * 800
    with pytest.raises(TranslationError, match="TOKEN_LIMIT_EXCEEDED"):
        await translate(TranslationRequest(long_text, "vi", "zh"), llm=NoCall(),
                        settings=configured(specialist_chunk_input_tokens=1024),
                        token_count=counter)


@pytest.mark.asyncio
async def test_hymt_rejects_fake_counter_before_provider():
    class NoCall:
        async def generate(self, request):
            pytest.fail("fake counter reached provider")

    with pytest.raises(TranslationError, match="MODEL_UNAVAILABLE"):
        await translate(TranslationRequest("Xin chào.", "vi", "zh"), llm=NoCall(),
                        settings=configured(), token_count=len)


@pytest.mark.asyncio
@pytest.mark.skipif(not TOKENIZER.is_file() or not TOKENIZER_CONFIG.is_file(),
                    reason="pinned public Hy-MT tokenizer not staged on this host")
async def test_v3_route_uses_hymt_counter_and_bad_metadata_blocks_private_provider():
    calls = []

    class Fake:
        async def generate(self, request):
            calls.append(request)
            return GenerationResult("你好。", "stop", "tencent/Hy-MT2-7B")

    body = {"text": "Xin chào.", "source_lang": "vi", "target_lang": "zh"}
    legacy = Settings(_env_file=None)
    good = routes.create_v3_app(settings_factory=configured, legacy=legacy, llm=Fake())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=good),
                                 base_url="http://127.0.0.1") as client:
        response = await client.post("/ai/v3/translate", json=body)
    assert response.status_code == 200
    assert response.json()["translated_text"] == "你好。"
    assert len(calls) == 1 and calls[0].user == body["text"]

    bad = routes.create_v3_app(
        settings_factory=lambda: configured(specialist_chat_template_sha256="0" * 64),
        legacy=legacy, llm=Fake())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=bad),
                                 base_url="http://127.0.0.1") as client:
        response = await client.post("/ai/v3/translate", json=body)
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "MODEL_UNAVAILABLE"
    assert len(calls) == 1
