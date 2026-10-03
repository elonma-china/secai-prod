import pytest

from secai_gateway.translation.types import DocumentBlock, TranslationError


@pytest.mark.parametrize("code,text", [
    ("vi", "Hệ thống đã phát hiện lỗ hổng nghiêm trọng."),
    ("en", "The system found a critical vulnerability."),
    ("zh", "系统发现了一个严重漏洞。"),
    ("ja", "システムは重大な脆弱性を発見しました。"),
    ("ko", "시스템에서 심각한 취약점이 발견되었습니다."),
    ("fr", "Le système a détecté une vulnérabilité critique."),
    ("de", "Das System hat eine kritische Schwachstelle entdeckt."),
    ("es", "El sistema detectó una vulnerabilidad crítica."),
    ("ru", "Система обнаружила критическую уязвимость."),
    ("ar", "اكتشف النظام ثغرة أمنية خطيرة."),
])
def test_detects_supported_primary_prose(code, text):
    from secai_gateway.translation.service import detect_source_language

    block = DocumentBlock(id="b000001", kind="paragraph", order=0, text=text,
                          metadata={})
    assert detect_source_language((block,)) == code


def test_auto_language_does_not_guess_from_short_or_literal_text():
    from secai_gateway.translation.service import detect_source_language

    short = DocumentBlock(id="b000001", kind="paragraph", order=0, text="Hello", metadata={})
    with pytest.raises(TranslationError) as e:
        detect_source_language((short,))
    assert e.value.code == "LANGUAGE_DETECTION_FAILED"
    literal = DocumentBlock(id="b000001", kind="literal", order=0,
                            text="CVE-2024-12345", metadata={})
    assert detect_source_language((literal,)) is None


def test_mixed_primary_prose_is_unresolved():
    from secai_gateway.translation.service import detect_source_language

    blocks = (
        DocumentBlock(id="b000001", kind="paragraph", order=0,
                      text="The system found a critical vulnerability.", metadata={}),
        DocumentBlock(id="b000002", kind="paragraph", order=1,
                      text="Hệ thống đã phát hiện lỗ hổng nghiêm trọng.", metadata={}),
    )
    with pytest.raises(TranslationError) as e:
        detect_source_language(blocks)
    assert e.value.code == "LANGUAGE_DETECTION_FAILED"
