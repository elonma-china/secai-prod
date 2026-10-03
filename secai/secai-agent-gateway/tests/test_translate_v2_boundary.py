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


def test_multilingual_types_and_scoped_profile():
    from typing import get_args
    from secai_gateway.translation.types import SourceLang, TargetLang
    from secai_gateway.agent.profiles import PROFILES
    from secai_gateway.agent.runner import _ephemeral_context_text

    assert set(get_args(TargetLang)) == {"vi", "en", "zh", "ja", "ko", "fr", "de", "es", "ru", "ar"}
    assert set(get_args(SourceLang)) == set(get_args(TargetLang)) | {"auto"}
    assert PROFILES["translate_v2"].tool_names == frozenset()
    assert PROFILES["translate_v2"].base is False
    assert PROFILES["translate_v2"].max_turns == 2
    assert _ephemeral_context_text("translate_v2", None) is None
    assert _ephemeral_context_text("ocr_correction", None) is None
