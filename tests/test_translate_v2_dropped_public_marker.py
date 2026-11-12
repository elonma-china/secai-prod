"""A model that silently drops the placeholder of a public identifier (product version,
CVE/CWE id) must not fail the whole document: that identifier alone is retried visibly,
verified intact and in order, then re-masked. Sensitive literals are never shown."""
import asyncio
import re

import pytest

from secai_gateway.translation.service import _is_public_identifier, translate_blocks
from secai_gateway.translation.types import DocumentBlock, TranslationError

MARKER = re.compile(r"⟦T[0-9a-f]{24}:\d+⟧")
SOURCE = "Rated critical with a CVSS v3.1 base score on host 10.0.0.5 today."


def block(text):
    return DocumentBlock(id="b1", kind="paragraph", order=0, text=text, metadata={})


class Ctx:
    def __init__(self, fn):
        self.fn, self.calls = fn, []

    async def checkpoint(self, *_a, **_k):
        pass

    async def translate(self, text, _s, _t):
        self.calls.append(text)
        return self.fn(text)


def run(ctx):
    return asyncio.run(translate_blocks((block(SOURCE),), "en", "vi", context=ctx))


def test_dropped_version_marker_is_recovered_without_exposing_the_ip():
    def model(text):
        if "CVSS v3.1" in text:  # visible retry: the model keeps the identifier
            return "Xếp loại: " + text
        return "Xếp loại: " + MARKER.sub("", text, count=1)  # masked reply drops marker 0

    ctx = Ctx(model)
    out = run(ctx)[0].translated_text
    assert "CVSS v3.1" in out and "10.0.0.5" in out
    assert len(ctx.calls) == 2
    assert all("10.0.0.5" not in call for call in ctx.calls)


def test_visible_retry_that_alters_the_identifier_still_fails_loudly():
    def model(text):
        if "CVSS v3.1" in text:
            return "Xếp loại: " + text.replace("CVSS v3.1", "CVSS v3.2")
        return "Xếp loại: " + MARKER.sub("", text, count=1)

    with pytest.raises(TranslationError) as exc:
        run(Ctx(model))
    assert exc.value.code == "TOKEN_INTEGRITY_FAILED"


def test_sensitive_literal_is_never_shown_to_the_model_during_recovery():
    ctx = Ctx(lambda text: "Xếp loại: " + MARKER.findall(text)[0] + " xong")  # drops the IP marker
    try:
        run(ctx)
    except TranslationError:
        pass
    assert ctx.calls and all("10.0.0.5" not in call for call in ctx.calls)


@pytest.mark.parametrize("literal,public", [
    ("CVSS v3.1", True), ("GitLab CE version 7.12.8", False), ("version 7.12.8", True),
    ("CVE-2026-12345", True), ("CWE-79", True), ("host 10.0.0.5", False),
    ("10.0.0.5", False), ("https://a.example/x", False), ("password: hunter2", False),
])
def test_public_identifier_classification(literal, public):
    assert _is_public_identifier(literal) is public


def test_version_literal_whose_leading_word_is_translated_is_still_recovered():
    source = "Zimbra Version 2.0.13 runs on host 10.0.0.5 today."

    def model(text):
        if "Version 2.0.13" in text:  # visible retry: the model translates the word and moves it
            return "Máy chủ chạy phiên bản Zimbra 2.0.13 tại " + MARKER.findall(text)[-1] + " hôm nay."
        return "Máy chủ chạy tại " + MARKER.findall(text)[-1] + " hôm nay."  # masked reply drops marker 0

    block = DocumentBlock(id="b1", kind="paragraph", order=0, text=source, metadata={})
    ctx = Ctx(model)
    out = asyncio.run(translate_blocks((block,), "en", "vi", context=ctx))[0].translated_text
    assert "2.0.13" in out and "10.0.0.5" in out
    assert all("10.0.0.5" not in call for call in ctx.calls)


def test_blocks_translate_concurrently_in_order_and_cancel_siblings_on_error():
    import time
    from secai_gateway.translation.types import DocumentBlock

    class Slow:
        block_concurrency = 4

        def __init__(self, fail_on=None):
            self.active = self.peak = 0
            self.fail_on = fail_on

        async def checkpoint(self, *_a, **_k):
            pass

        async def translate(self, text, _s, _t):
            self.active += 1
            self.peak = max(self.peak, self.active)
            await asyncio.sleep(0.05)
            self.active -= 1
            if self.fail_on and self.fail_on in text:
                raise TranslationError("TRANSLATION_UNAVAILABLE", "boom")
            return "Dịch " + text

    blocks = tuple(DocumentBlock(id=f"b{i}", kind="paragraph", order=i, text=f"Sentence number {i} here.", metadata={})
                   for i in range(12))
    ctx = Slow()
    started = time.monotonic()
    out = asyncio.run(translate_blocks(blocks, "en", "vi", context=ctx))
    assert [b.translated_text for b in out] == [f"Dịch Sentence number {i} here." for i in range(12)]
    assert ctx.peak == 4 and time.monotonic() - started < 0.4  # 12 sequential calls would take ~0.6s
    with pytest.raises(TranslationError):
        asyncio.run(translate_blocks(blocks, "en", "vi", context=Slow(fail_on="number 5")))
