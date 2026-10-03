import pytest

from secai_gateway.translation.ocr_client import _base_url, _parse_response
from secai_gateway.translation.settings import TranslationSettings
from secai_gateway.translation.types import TranslationError


def test_private_loopback_only():
    assert _base_url(TranslationSettings(ocr_base_url="http://127.0.0.1:18083/")) == "http://127.0.0.1:18083"
    with pytest.raises(TranslationError):
        _base_url(TranslationSettings(ocr_base_url="http://public.example:18083"))


def test_response_metadata_must_be_truthful():
    output = _parse_response({"model": "test", "blocks": [
        {"text": "Xin chào", "bbox": None, "confidence": None}]})
    assert output.raw_text == "Xin chào"
    assert output.blocks[0].metadata == {}
    with pytest.raises(TranslationError):
        _parse_response({"model": "test", "blocks": [
            {"text": "fake", "bbox": None, "confidence": 1.2}]})
