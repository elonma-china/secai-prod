import json

import pytest

from secai_gateway.translation.ocr_correction import correct_ocr
from secai_gateway.translation.ocr_correction import _has_ambiguous_technical
from secai_gateway.translation.types import DocumentBlock, OcrResult, TranslationError


class Context:
    def __init__(self, proposal):
        self.proposal = proposal
        self.profile = None

    async def complete(self, profile, prompt):
        self.profile = profile
        assert "OCR_DATA_JSON" in prompt
        return json.dumps(self.proposal), {}


def result(text):
    block = DocumentBlock(id="b000001", kind="ocr_text", order=0, text=text, metadata={})
    return OcrResult(blocks=(block,), raw_text=text)


def test_only_ambiguous_technical_tokens_need_review():
    assert not _has_ambiguous_technical("CVE-2024-1234 at 192.0.2.1/24")
    assert _has_ambiguous_technical("CVE-2O24-1234 at 192.0.2.1")
    assert _has_ambiguous_technical("server 192.O.2.1")


@pytest.mark.asyncio
async def test_prose_edit_and_profile():
    source = result("Hel1o, this is a long ordinary English sentence without technical content.")
    context = Context({"edits": [{"block_id": "b000001", "start": 3, "end": 4,
                                  "before": "1", "after": "l", "reason": "spelling"}]})
    output = await correct_ocr(source, context=context)
    assert context.profile == "ocr_correction"
    assert output.corrected_text.startswith("Hello")
    assert not output.needs_review


@pytest.mark.asyncio
@pytest.mark.parametrize("proposal", [
    {"edits": [{"block_id": "b000001", "start": 0, "end": 13, "before": "CVE-2024-1234",
                "after": "CVE-2024-1235", "reason": "spelling"}]},
    {"edits": [], "ignore": "me"},
    {"edits": [{"block_id": "b000001", "start": 100, "end": 101,
                "before": "x", "after": "y", "reason": "spelling"}]},
])
async def test_invalid_proposal_falls_back(proposal):
    source = result("CVE-2024-1234 appears in a sufficiently long sentence for review.")
    output = await correct_ocr(source, context=Context(proposal))
    assert output.corrected_text == source.raw_text
    assert output.needs_review
    assert output.warnings[-1].code == "OCR_CORRECTION_REJECTED"


@pytest.mark.asyncio
async def test_correction_provider_outage_fails_job_instead_of_accepting_raw_text():
    class UnavailableContext:
        async def complete(self, profile, prompt):
            raise TranslationError("TRANSLATION_UNAVAILABLE", "Provider unavailable")

    with pytest.raises(TranslationError) as error:
        await correct_ocr(result("The server is unavailable."), context=UnavailableContext())
    assert error.value.code == "TRANSLATION_UNAVAILABLE"
