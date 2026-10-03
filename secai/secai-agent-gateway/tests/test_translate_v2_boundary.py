from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest


def test_frozen_keyword_only_boundary():
    from secai_gateway.translation.types import DocumentBlock, TranslationError

    with pytest.raises(TypeError):
        DocumentBlock("b000001", "paragraph", 0, "x", {})
    block = DocumentBlock(id="b000001", kind="paragraph", order=0, text="x", metadata={})
    with pytest.raises(FrozenInstanceError):
        block.text = "y"
    error = TranslationError("PROCESSING_TIMEOUT", "Processing timed out.", retryable=True)
    assert (error.code, error.message, error.retryable) == (
        "PROCESSING_TIMEOUT", "Processing timed out.", True,
    )


def test_settings_defaults_and_secret_repr(monkeypatch):
    from secai_gateway.translation.settings import TranslationSettings

    monkeypatch.setenv("TRANSLATE_V2_OCR_API_KEY", "canary-secret")
    settings = TranslationSettings(_env_file=None)
    assert settings.enabled is False
    assert settings.ocr_enabled is False
    assert settings.max_upload_bytes == 10 * 1024 * 1024
    assert settings.max_request_bytes == 11 * 1024 * 1024
    assert settings.ocr_api_key == "canary-secret"
    assert "canary-secret" not in repr(settings)
