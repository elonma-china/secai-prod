"""Heavy DOCX (large embedded media) must be extractable without inflating the media."""
import io
import os

import pytest
from docx import Document
from docx.shared import Inches
from PIL import Image

from secai_gateway.translation import documents
from secai_gateway.translation.documents import extract_docx
from secai_gateway.translation.settings import TranslationSettings
from secai_gateway.translation.types import TranslationError


def heavy_docx(tmp_path, side=600):
    image = Image.frombytes("RGB", (side, side), os.urandom(side * side * 3))
    raw = io.BytesIO()
    image.save(raw, "PNG", compress_level=1)
    document = Document()
    document.add_paragraph("Patch CVE-2026-31337 on 10.0.0.5 immediately.")
    document.add_picture(io.BytesIO(raw.getvalue()), width=Inches(2))
    path = tmp_path / "heavy.docx"
    document.save(path)
    return path, len(raw.getvalue())


def test_media_is_not_inflated_and_does_not_count_against_the_xml_cap(tmp_path):
    path, media = heavy_docx(tmp_path)
    settings = TranslationSettings(max_docx_xml_bytes=900 * 1024, max_docx_inflated_bytes=10 * media)
    parts = documents._safe_zip(path, settings)
    assert any(name.startswith("word/media/") and parts[name] == b"" for name in parts)
    blocks = extract_docx(path, settings).blocks
    assert any("CVE-2026-31337" in block.text for block in blocks)


def test_total_declared_size_is_still_capped(tmp_path):
    path, media = heavy_docx(tmp_path)
    with pytest.raises(TranslationError) as exc:
        extract_docx(path, TranslationSettings(max_docx_inflated_bytes=media // 2))
    assert exc.value.code == "ARCHIVE_LIMIT_EXCEEDED"


def test_xml_cap_is_enforced(tmp_path):
    path, _ = heavy_docx(tmp_path)
    with pytest.raises(TranslationError) as exc:
        extract_docx(path, TranslationSettings(max_docx_xml_bytes=64))
    assert exc.value.code == "ARCHIVE_LIMIT_EXCEEDED"
