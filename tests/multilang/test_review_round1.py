"""Regressions reproduced by independent T7 review at bfe4e6b."""
from __future__ import annotations

import re
from hashlib import sha256

import httpx
import pytest
from tokenizers import Tokenizer, models, pre_tokenizers

from secai_gateway.api.multilang import routes
from secai_gateway.config import Settings
from secai_gateway.multilang.config import EXPECTED_MODEL, verified_counter
from secai_gateway.multilang.errors import TranslationError
from secai_gateway.multilang.provider import GenerationRequest, GenerationResult, HttpLlm
from secai_gateway.multilang.service import TranslationRequest, translate

from .test_core import count, settings


def _local_tokenizer(path, *, truncate=False):
    tokenizer = Tokenizer(models.WordLevel({"[UNK]": 0, "hello": 1}, unk_token="[UNK]"))
    tokenizer.pre_tokenizer = pre_tokenizers.Whitespace()
    if truncate:
        tokenizer.enable_truncation(max_length=8)
    tokenizer.save(str(path))
    return sha256(path.read_bytes()).hexdigest()


@pytest.mark.asyncio
async def test_t7_01_serialized_truncation_cannot_bypass_source_budget(tmp_path):
    path = tmp_path / "tokenizer.json"
    digest = _local_tokenizer(path, truncate=True)
    config = settings(tokenizer_json=str(path), tokenizer_sha256=digest,
                      max_source_tokens=8, chunk_input_tokens=8)
    calls = []

    class Llm:
        async def generate(self, request):
            calls.append(request)
            return GenerationResult("检查完成。", "stop", EXPECTED_MODEL)

    counter = verified_counter(config)
    assert counter is None or counter("hello " * 100) > 8
    with pytest.raises(TranslationError, match="MODEL_UNAVAILABLE|TOKEN_LIMIT_EXCEEDED"):
        await translate(TranslationRequest("hello " * 100, "vi", "zh"), llm=Llm(),
                        settings=config, token_count=counter)
    assert not calls


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", ["dịch\u202engược", "dịch\ud800hỏng"])
async def test_t7_02_generated_unicode_retries_then_safe_502(invalid, monkeypatch):
    class Llm:
        def __init__(self):
            self.calls = 0

        async def generate(self, request):
            self.calls += 1
            return GenerationResult(invalid, "stop", EXPECTED_MODEL)

    fake = Llm()
    with pytest.raises(TranslationError, match="OUTPUT_VALIDATION_FAILED"):
        await translate(TranslationRequest("Dịch câu đầy đủ này.", "vi", "zh"),
                        llm=fake, settings=settings(), token_count=count)
    assert fake.calls == 2

    monkeypatch.setattr(routes, "verified_counter", lambda config: count)
    legacy = Settings(_env_file=None).model_copy(update={
        "llm_provider": "vllm", "qwen_model": EXPECTED_MODEL,
        "qwen_base_url": "http://127.0.0.1:9/v1"})
    app = routes.create_v3_app(settings_factory=settings, legacy=legacy, llm=Llm())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://test") as client:
        response = await client.post("/ai/v3/translate", json={
            "text": "Dịch câu đầy đủ này.", "source_lang": "vi", "target_lang": "zh"})
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "OUTPUT_VALIDATION_FAILED"


@pytest.mark.asyncio
@pytest.mark.parametrize("text,source,target,answer", [
    ("افحص إعدادات Apache Kubernetes الآن.", "ar", "vi", "Kiểm tra cấu hình Apache Kubernetes ngay."),
    ("Проверьте настройки Apache Kubernetes сейчас.", "ru", "vi", "Kiểm tra cấu hình Apache Kubernetes ngay."),
    ("Apache Kubernetes 설정을 지금 확인하세요.", "ko", "vi", "Kiểm tra cấu hình Apache Kubernetes ngay."),
])
async def test_t7_03_mixed_technical_source_is_uncertain(text, source, target, answer):
    calls = []

    class Llm:
        async def generate(self, request):
            calls.append(request)
            return GenerationResult(answer, "stop", EXPECTED_MODEL)

    result = await translate(TranslationRequest(text, source, target), llm=Llm(),
                             settings=settings(enable_ko=True), token_count=count)
    assert calls
    assert "SOURCE_LANGUAGE_UNCERTAIN" in result.warnings


@pytest.mark.asyncio
async def test_t7_04_internal_prompt_fragment_and_canary_fail_closed():
    class Leak:
        def __init__(self, canary_only=False):
            self.canary_only = canary_only
            self.calls = 0

        async def generate(self, request):
            self.calls += 1
            if self.canary_only:
                match = re.search(r"[0-9a-f]{24}", request.system)
                assert match
                text = "Traduction " + match.group(0)
            else:
                text = request.system.replace("Preserve every ⟦ML-...⟧ marker exactly once and in order. ", "")
            return GenerationResult(text, "stop", EXPECTED_MODEL)

    for canary_only in (False, True):
        fake = Leak(canary_only)
        with pytest.raises(TranslationError, match="OUTPUT_VALIDATION_FAILED"):
            await translate(TranslationRequest("Đây là một câu cần dịch.", "vi", "fr"),
                            llm=fake, settings=settings(enable_fr=True), token_count=count)
        assert fake.calls == 2


@pytest.mark.asyncio
async def test_t7_05_whitespace_copy_french_to_vietnamese_rejected():
    class Copier:
        async def generate(self, request):
            return GenerationResult("Vérifiez le  système avant de continuer.", "stop", EXPECTED_MODEL)

    with pytest.raises(TranslationError, match="OUTPUT_VALIDATION_FAILED"):
        await translate(TranslationRequest("Vérifiez le système avant de continuer.", "fr", "vi"),
                        llm=Copier(), settings=settings(enable_fr=True), token_count=count)


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [[], 42, {"model": EXPECTED_MODEL, "choices": []},
                                          {"model": EXPECTED_MODEL, "choices": [42]},
                                          {"model": EXPECTED_MODEL, "choices": [{"message": []}]},
                                          {"model": EXPECTED_MODEL, "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}], "usage": []}])
async def test_t7_06_malformed_provider_json_maps_to_503(payload):
    async with httpx.AsyncClient(transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json=payload))) as client:
        with pytest.raises(TranslationError, match="MODEL_UNAVAILABLE"):
            await HttpLlm(client, "http://incumbent.invalid/v1", "dummy").generate(
                GenerationRequest("system", "source", 100))


def test_t7_07_hash_matched_invalid_tokenizer_is_unavailable(tmp_path):
    path = tmp_path / "tokenizer.json"
    path.write_text("{}")
    digest = sha256(path.read_bytes()).hexdigest()
    assert verified_counter(settings(tokenizer_json=str(path), tokenizer_sha256=digest)) is None


@pytest.mark.asyncio
async def test_t7_08_returned_tool_call_uses_single_validation_retry():
    calls = []

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            message = {"content": "", "tool_calls": [{"id": "blocked"}]}
            reason = "tool_calls"
        else:
            message = {"content": "检查完成。"}
            reason = "stop"
        return httpx.Response(200, json={"model": EXPECTED_MODEL, "choices": [
            {"finish_reason": reason, "message": message}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        llm = HttpLlm(client, "http://incumbent.invalid/v1", "dummy")
        result = await translate(TranslationRequest("Dịch câu đầy đủ này.", "vi", "zh"),
                                 llm=llm, settings=settings(), token_count=count)
    assert result.attempts == 2
    assert len(calls) == 2
