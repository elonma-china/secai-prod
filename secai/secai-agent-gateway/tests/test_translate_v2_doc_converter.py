"""Local isolated-container integration test; skips when converter image is absent."""
from pathlib import Path

import pytest

from secai_gateway.translation.documents import (convert_doc, converter_available,
                                                 extract_docx, validate_doc)
from secai_gateway.translation.settings import TranslationSettings
from secai_gateway.translation.types import TranslationError


FIXTURE = Path(__file__).parent / "fixtures" / "translate_v2" / "basic.doc"


def test_doc_requires_real_worddocument_stream(tmp_path):
    path = tmp_path / "bad.doc"
    path.write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\0" * 512)
    with pytest.raises(TranslationError) as e:
        validate_doc(path)
    assert e.value.code == "CORRUPT_DOCUMENT"


def test_networkless_converter_preserves_header_table_footer(tmp_path):
    settings = TranslationSettings(_env_file=None)
    if not converter_available(settings):
        pytest.skip("isolated converter image is unavailable")
    output = convert_doc(FIXTURE, tmp_path, settings)
    result = extract_docx(output, settings)
    assert [(block.kind, block.text) for block in result.blocks if block.text] == [
        ("header", "Header"), ("paragraph", "Hello"),
        ("table_cell", "One"), ("table_cell", "Two"), ("footer", "Footer")]
