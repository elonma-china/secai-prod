from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

import pytest

from secai_gateway.translation.settings import TranslationSettings
from secai_gateway.translation.types import TranslationError


def _settings():
    return TranslationSettings(_env_file=None)


@pytest.mark.parametrize("encoding,bom", [
    ("utf-8", b""), ("utf-8", b"\xef\xbb\xbf"),
    ("utf-16-le", b"\xff\xfe"), ("utf-16-be", b"\xfe\xff"),
])
def test_txt_strict_bom_and_newlines(encoding, bom):
    from secai_gateway.translation.documents import extract_txt

    doc = extract_txt(bom + "Hà Nội\r\nLine\rEnd".encode(encoding), _settings())
    assert [b.text for b in doc.blocks] == ["Hà Nội", "Line", "End"]


def test_cp1258_assumption_and_ambiguous_rejection():
    from secai_gateway.translation.documents import extract_txt

    payload = "Tôi đi đây".encode("cp1258")
    doc = extract_txt(payload, _settings())
    assert "đi" in "\n".join(b.text for b in doc.blocks)
    assert [w.code for w in doc.warnings] == ["ENCODING_ASSUMED"]
    with pytest.raises(TranslationError) as e:
        extract_txt(b"\xe9", _settings())
    assert e.value.code == "UNSUPPORTED_ENCODING"


def test_docx_interleaved_body_table_and_header(tmp_path):
    from docx import Document
    from secai_gateway.translation.documents import extract_docx

    document = Document()
    document.sections[0].header.paragraphs[0].text = "Header"
    document.add_paragraph("Before")
    table = document.add_table(rows=1, cols=2)
    table.cell(0, 0).text = "One"
    table.cell(0, 1).text = "Two"
    document.add_paragraph("After")
    document.sections[0].footer.paragraphs[0].text = "Footer"
    path = tmp_path / "x.docx"
    document.save(path)
    doc = extract_docx(path, _settings())
    assert [(b.kind, b.text) for b in doc.blocks] == [
        ("header", "Header"), ("paragraph", "Before"),
        ("table_cell", "One"), ("table_cell", "Two"),
        ("paragraph", "After"), ("footer", "Footer"),
    ]


def test_docx_rejects_external_relationship(tmp_path):
    from docx import Document
    from secai_gateway.translation.documents import extract_docx

    path = tmp_path / "x.docx"
    Document().save(path)
    out = tmp_path / "bad.docx"
    with ZipFile(path) as source, ZipFile(out, "w") as dest:
        for item in source.infolist():
            data = source.read(item.filename)
            if item.filename == "word/_rels/document.xml.rels":
                data = data.replace(b"</Relationships>", b'<Relationship Id="evil" Type="x" Target="http://example.com" TargetMode="External"/></Relationships>')
            dest.writestr(item, data)
    with pytest.raises(TranslationError) as e:
        extract_docx(out, _settings())
    assert e.value.code == "UNSAFE_DOCUMENT"


def test_docx_section_headers_follow_effective_section(tmp_path):
    from docx import Document
    from docx.enum.section import WD_SECTION_START
    from secai_gateway.translation.documents import extract_docx

    document = Document()
    document.sections[0].header.paragraphs[0].text = "First header"
    document.add_paragraph("First body")
    second = document.add_section(WD_SECTION_START.NEW_PAGE)
    second.header.is_linked_to_previous = False
    second.header.paragraphs[0].text = "Second header"
    document.add_paragraph("Second body")
    path = tmp_path / "sections.docx"
    document.save(path)
    result = extract_docx(path, _settings())
    observed = [(b.kind, b.text, b.metadata.get("section")) for b in result.blocks if b.text]
    assert observed == [
        ("header", "First header", 0), ("paragraph", "First body", 0),
        ("header", "Second header", 1), ("paragraph", "Second body", 1),
    ]
