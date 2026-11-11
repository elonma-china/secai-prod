"""v3 live translation: a model that drops the marker of a public literal (number, version,
date, CVE/CWE id) is recovered by one retry showing only that literal, verified and re-masked.
IPs, URLs, paths and credentials are never shown to the model."""
from __future__ import annotations

import re

import pytest

from secai_gateway.multilang.config import EXPECTED_MODEL
from secai_gateway.multilang.errors import TranslationError
from secai_gateway.multilang.provider import GenerationResult
from secai_gateway.multilang.service import TranslationRequest, _is_public_literal, translate

from .test_core import count, settings

TOKEN = re.compile(r"⟦ML-[A-Za-z0-9_-]+-\d+⟧")


class Llm:
    def __init__(self, fn):
        self.fn, self.calls = fn, []

    async def generate(self, request):
        self.calls.append(request.user)
        return GenerationResult(self.fn(request.user), "stop", EXPECTED_MODEL)


TEXT = "Rated critical with score 9.1 on host 10.0.0.5 today."


def run(llm, text=TEXT):
    return translate(TranslationRequest(text, "en", "vi"), llm=llm, settings=settings(enable_en=True), token_count=count)


@pytest.mark.asyncio
async def test_dropped_number_marker_is_recovered_and_ip_stays_hidden():
    def model(user):
        tokens = TOKEN.findall(user)
        if "9.1" in user:  # visible retry: the model keeps the number it is shown
            return "Xếp loại nghiêm trọng điểm " + user.split("score ")[1].split(" on host")[0] + " trên máy " + tokens[-1]
        return "Xếp loại nghiêm trọng trên máy " + tokens[-1]  # masked reply drops the number marker

    llm = Llm(model)
    result = await run(llm)
    assert "9.1" in result.translated_text and "10.0.0.5" in result.translated_text
    assert result.attempts == 2
    assert all("10.0.0.5" not in call for call in llm.calls)


@pytest.mark.asyncio
async def test_dropped_ip_marker_is_never_recovered_by_exposing_it():
    llm = Llm(lambda user: "Xếp loại nghiêm trọng " + " ".join(TOKEN.findall(user)[:1]))  # drops the IP marker
    with pytest.raises(TranslationError):
        await run(llm)
    assert llm.calls and all("10.0.0.5" not in call for call in llm.calls)


@pytest.mark.asyncio
async def test_visible_retry_that_changes_the_number_still_fails():
    def model(user):
        if "9.1" in user:
            return "Xếp loại điểm 9.2 trên máy " + TOKEN.findall(user)[-1]
        return "Xếp loại trên máy " + TOKEN.findall(user)[-1]

    with pytest.raises(TranslationError) as exc:
        await run(Llm(model))
    assert exc.value.code == "OUTPUT_VALIDATION_FAILED"


@pytest.mark.parametrize("literal,public", [
    ("9.1", True), ("3.1", True), ("2026-11-11", True), ("42", True), ("CVE-2026-31337", True),
    ("10.0.0.5", False), ("https://a.example/x", False), ("/etc/passwd", False),
    ("password: hunter2", False), ("`rm -rf /`", False),
])
def test_public_literal_classification(literal, public):
    assert _is_public_literal(literal) is public


@pytest.mark.asyncio
async def test_ellipsis_added_to_a_mid_sentence_chunk_is_trimmed_not_rejected():
    class Ellipsis:
        def __init__(self):
            self.calls = []

        async def generate(self, request):
            self.calls.append(request.user)
            tail = "" if request.user.strip().endswith(".") else "…"  # only unfinished fragments get one
            return GenerationResult("Dịch văn bản xong: " + request.user.strip() + tail, "stop", EXPECTED_MODEL)

    llm = Ellipsis()
    text = "alpha beta gamma delta epsilon zeta eta theta iota kappa lambda mu nu xi omicron pi."
    result = await translate(TranslationRequest(text, "en", "vi"), llm=llm,
                             settings=settings(enable_en=True, max_chunks=16, chunk_input_tokens=30),
                             token_count=count)
    assert result.chunks > 1 and "…" not in result.translated_text
