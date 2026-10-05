from __future__ import annotations

import asyncio
from hashlib import sha256

import pytest

from secai_gateway.multilang.chunks import split
from secai_gateway.multilang.config import EXPECTED_MODEL, MultilangSettings, verified_counter
from secai_gateway.multilang.errors import TranslationError
from secai_gateway.multilang.literals import protect, validate_unicode
from secai_gateway.multilang.provider import GenerationResult
from secai_gateway.multilang.registry import canonical, capabilities
from secai_gateway.multilang.service import TranslationRequest, translate


def settings(**overrides):
    values = {"enabled": True, "enable_zh": True, "enable_ar": True, "enable_ru": True,
              "context_tokens": 32768, "chat_template_overhead_tokens": 128}
    values.update(overrides)
    return MultilangSettings(_env_file=None, **values)


def count(text):
    return len(text)  # deterministic fixture; production requires pinned tokenizer JSON.


class FakeLlm:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.calls = []

    async def generate(self, request):
        self.calls.append(request)
        output = self.outputs.pop(0)
        if isinstance(output, Exception):
            raise output
        return GenerationResult(output, "stop", EXPECTED_MODEL)


def test_registry_and_flags():
    cap = capabilities(settings())
    assert [x["code"] for x in cap["languages"]] == [
        "vi", "zh-Hans", "ja", "ko", "fr", "de", "es", "ru", "ar", "en"]
    assert len(cap["pairs"]) == 18
    assert canonical(" ZH-hans \t") == "zh-Hans"
    assert canonical(" zh ") == "zh-Hans"
    assert all(canonical(x) is None for x in ("auto", "zh-Hant", "ar-SA"))
    assert cap["pairs"][0]["enabled"] and cap["pairs"][1]["enabled"]
    assert not cap["pairs"][2]["enabled"]


def test_pinned_local_tokenizer_required(tmp_path):
    from tokenizers import Tokenizer, models, pre_tokenizers

    tokenizer = Tokenizer(models.WordLevel({"[UNK]": 0, "dịch": 1}, unk_token="[UNK]"))
    tokenizer.pre_tokenizer = pre_tokenizers.Whitespace()
    path = tmp_path / "tokenizer.json"
    tokenizer.save(str(path))
    digest = sha256(path.read_bytes()).hexdigest()
    cfg = settings(tokenizer_json=str(path), tokenizer_sha256=digest)
    counter = verified_counter(cfg)
    assert counter is not None and counter("dịch") == 1
    assert verified_counter(settings(tokenizer_json=str(path), tokenizer_sha256="0" * 64)) is None
    assert verified_counter(settings(tokenizer_json=str(path), tokenizer_sha256=digest,
                                     chat_template_overhead_tokens=0)) is None


@pytest.mark.asyncio
async def test_invalid_and_disabled_do_not_generate():
    fake = FakeLlm([])
    for source, target, code in (("en", "vi", "LANGUAGE_DISABLED"),
                                 ("zh", "zh-Hans", "UNSUPPORTED_PAIR"),
                                 ("fr", "de", "UNSUPPORTED_PAIR"),
                                 ("vi", "ja", "LANGUAGE_DISABLED")):
        with pytest.raises(TranslationError, match=code):
            await translate(TranslationRequest("Một câu có nghĩa.", source, target),
                            llm=fake, settings=settings(), token_count=count)
    assert fake.calls == []


def test_literal_occurrences_exact_and_unknown_rejected():
    source = "Dịch `password: abc123` cùng CVE-2024-1234, CVE-2024-1234 và 192.0.2.1."
    p = protect(source)
    assert "abc123" not in p.masked
    assert len(p.tokens) == 4
    assert p.restore(p.masked) == source
    for malformed in (p.masked.replace(p.tokens[0], "", 1),
                      p.masked + p.tokens[0],
                      p.masked.replace(p.tokens[0], p.tokens[1], 1),
                      p.masked + " ⟦ML-foreign-100⟧"):
        with pytest.raises(TranslationError, match="OUTPUT_VALIDATION_FAILED"):
            p.restore(malformed)


def test_literals_adjacent_to_non_latin_and_chunk_tokens_atomic():
    source = "存在CVE-2024-12345漏洞。العربيةCWE-79نص. Найден 192.0.2.1уязвимость."
    p = protect(source)
    assert "CVE-2024-12345" in p.literals
    assert "CWE-79" in p.literals
    assert "192.0.2.1" in p.literals
    parts = split(p, count=len, budget=42, max_chunks=16)
    assert "".join(content + sep for content, sep, _ in parts) == p.masked
    assert [token for _, _, tokens in parts for token in tokens] == list(p.tokens)
    assert p.restore(p.masked) == source


def test_cjk_and_arabic_sentence_boundaries_without_spaces():
    for source in ("第一句。第二句。第三句。", "الجملة الأولى؟الجملة الثانية؟الجملة الثالثة؟"):
        p = protect(source)
        parts = split(p, count=len, budget=8, max_chunks=16)
        assert "".join(content + sep for content, sep, _ in parts) == p.masked
        assert len(parts) >= 2


def test_unicode_policy_and_nfc_prose_only():
    for bad in ("a\x00b", "a\u202eb", "a\u2066b", "a\ud800b"):
        with pytest.raises(TranslationError, match="UNSAFE_UNICODE"):
            validate_unicode(bad)
    valid = "العَرَبِيَّة‌‍ e\u0301 `e\u0301`"
    validate_unicode(valid)
    p = protect(valid)
    assert "é" in p.masked
    assert p.restore(p.masked).endswith("`e\u0301`")


class EchoLiteralLlm:
    def __init__(self, bad_first=False, fail_last=False):
        self.calls = []
        self.bad_first = bad_first
        self.fail_last = fail_last

    async def generate(self, request):
        self.calls.append(request)
        if self.bad_first and len(self.calls) == 1:
            return GenerationResult("missing literal", "stop", EXPECTED_MODEL)
        if self.fail_last and len(self.calls) >= 2:
            return GenerationResult("", "stop", EXPECTED_MODEL)
        import re
        tokens = re.findall(r"⟦ML-[A-Za-z0-9_-]+-\d+⟧", request.user)
        return GenerationResult("检查 " + " ".join(tokens) + "。", "stop", EXPECTED_MODEL)


@pytest.mark.asyncio
async def test_retry_counts_and_restore():
    fake = EchoLiteralLlm(bad_first=True)
    result = await translate(TranslationRequest("Kiểm tra CVE-2024-1234.", "vi", "zh"),
                             llm=fake, settings=settings(), token_count=count)
    assert result.attempts == 2 and result.chunks == 1
    assert "CVE-2024-1234" in result.translated_text
    assert result.warnings == ("SOURCE_LANGUAGE_UNCERTAIN", "TARGET_LANGUAGE_UNCERTAIN")


@pytest.mark.asyncio
async def test_missing_guard_and_output_fail_closed():
    fake = FakeLlm([])
    with pytest.raises(TranslationError, match="MODEL_UNAVAILABLE"):
        await translate(TranslationRequest("Một câu có nghĩa.", "vi", "zh"),
                        llm=fake, settings=settings(), token_count=None)
    assert not fake.calls
    fake = EchoLiteralLlm(bad_first=True, fail_last=True)
    with pytest.raises(TranslationError, match="EMPTY_TRANSLATION"):
        await translate(TranslationRequest("Kiểm tra CVE-2024-1234.", "vi", "zh"),
                        llm=fake, settings=settings(), token_count=count)


@pytest.mark.asyncio
async def test_source_literal_and_context_budgets_before_inference():
    fake = FakeLlm([])
    cases = (
        ("Một câu có nghĩa." * 4, settings(max_source_tokens=20, chunk_input_tokens=20),
         "TOKEN_LIMIT_EXCEEDED"),
        ("Văn bản `" + "x" * 80 + "`.", settings(chunk_input_tokens=40),
         "TOKEN_LIMIT_EXCEEDED"),
        ("Một câu có nghĩa.", settings(context_tokens=200), "TOKEN_LIMIT_EXCEEDED"),
        ("Một câu có nghĩa." * 3, settings(max_text_chars=20), "TEXT_TOO_LONG"),
    )
    for source, config, code in cases:
        with pytest.raises(TranslationError, match=code):
            await translate(TranslationRequest(source, "vi", "zh"), llm=fake,
                            settings=config, token_count=count)
    assert not fake.calls


@pytest.mark.asyncio
async def test_nth_chunk_fails_atomically():
    class FirstOnly:
        def __init__(self):
            self.calls = 0

        async def generate(self, request):
            self.calls += 1
            return GenerationResult("检查。" if self.calls == 1 else "", "stop", EXPECTED_MODEL)

    fake = FirstOnly()
    with pytest.raises(TranslationError, match="EMPTY_TRANSLATION"):
        await translate(TranslationRequest("Câu đầu tiên. Câu thứ hai có nhiều chữ hơn.", "vi", "zh"),
                        llm=fake, settings=settings(chunk_input_tokens=24), token_count=count)
    assert fake.calls >= 2


@pytest.mark.asyncio
async def test_untranslated_copy_and_wrong_script_rejected():
    class Copier:
        async def generate(self, request):
            return GenerationResult(request.user, "stop", EXPECTED_MODEL)

    with pytest.raises(TranslationError, match="OUTPUT_VALIDATION_FAILED"):
        await translate(TranslationRequest("Kiểm tra CVE-2024-1234 ngay hôm nay.", "vi", "zh"),
                        llm=Copier(), settings=settings(), token_count=count)

    class LatinOutput:
        async def generate(self, request):
            return GenerationResult("This is a long English sentence with no Chinese text.",
                                    "stop", EXPECTED_MODEL)

    with pytest.raises(TranslationError, match="OUTPUT_VALIDATION_FAILED"):
        await translate(TranslationRequest("Đây là một câu đầy đủ.", "vi", "zh"),
                        llm=LatinOutput(), settings=settings(), token_count=count)


@pytest.mark.asyncio
async def test_cancellation_releases_model_slot():
    gate = asyncio.Event()

    class Hanging:
        async def generate(self, request):
            await gate.wait()
            return GenerationResult("检查结果。", "stop", EXPECTED_MODEL)

    task = asyncio.create_task(translate(TranslationRequest("Đây là một câu để dịch.", "vi", "zh"),
                                         llm=Hanging(), settings=settings(), token_count=count))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    gate.set()
    result = await translate(TranslationRequest("Đây là một câu để dịch.", "vi", "zh"),
                             llm=Hanging(), settings=settings(), token_count=count)
    assert result.attempts == 1
