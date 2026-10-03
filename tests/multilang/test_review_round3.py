"""R3-01: a long foreign error message must not erase declared-language evidence."""
from __future__ import annotations

import pytest

from secai_gateway.multilang.config import EXPECTED_MODEL
from secai_gateway.multilang.errors import TranslationError
from secai_gateway.multilang.provider import GenerationResult
from secai_gateway.multilang.service import TranslationRequest, translate

from .test_core import count, settings


@pytest.mark.asyncio
@pytest.mark.parametrize("source", [
    "راجع: Не удалось установить соединение с сервером после обновления операционной системы и перезапуска приложения.",
    "راجع الخطأ: Не удалось установить соединение с сервером после обновления системы.",
    "raji alkhata: Apache Kubernetes could not establish a connection after deployment.",
])
async def test_long_mixed_or_transliterated_arabic_source_is_uncertain(source):
    calls = []

    class Llm:
        async def generate(self, request):
            calls.append(request)
            return GenerationResult("Kiểm tra lỗi kết nối sau khi cập nhật hệ thống.",
                                    "stop", EXPECTED_MODEL)

    result = await translate(TranslationRequest(source, "ar", "vi"),
                             llm=Llm(), settings=settings(), token_count=count)
    assert calls
    assert "SOURCE_LANGUAGE_UNCERTAIN" in result.warnings


@pytest.mark.asyncio
async def test_wholly_cyrillic_declared_arabic_remains_mismatch():
    calls = []

    class Llm:
        async def generate(self, request):
            calls.append(request)
            return GenerationResult("Kiểm tra lỗi.", "stop", EXPECTED_MODEL)

    with pytest.raises(TranslationError, match="SOURCE_LANGUAGE_MISMATCH"):
        await translate(TranslationRequest(
            "Не удалось установить соединение с сервером после обновления системы.", "ar", "vi"),
            llm=Llm(), settings=settings(), token_count=count)
    assert not calls
