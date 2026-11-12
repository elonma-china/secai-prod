from io import BytesIO
from pathlib import Path
from zipfile import ZipFile
import shutil
import subprocess

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


def test_cp1258_vowel_plus_combining_tone_is_accepted():
    from secai_gateway.translation.documents import extract_txt

    text = "Hoa\u0300n tha\u0300nh"
    payload = text.encode("cp1258")
    result = extract_txt(payload, _settings())
    assert result.blocks[0].text == text
    assert result.warnings[0].code == "ENCODING_ASSUMED"


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


def test_docx_allows_ordinary_external_hyperlink(tmp_path):
    """A citation/reference link (e.g. a threat-intel report linking to an advisory) is an
    External-TargetMode relationship of Type .../hyperlink. It is never dereferenced by the
    extractor -- only its rId text appears in the XML -- so it must not trip UNSAFE_DOCUMENT
    the way a real external OLE/package link does."""
    from docx import Document
    from secai_gateway.translation.documents import extract_docx

    document = Document()
    document.add_paragraph("See the advisory for details.")
    path = tmp_path / "x.docx"
    document.save(path)
    out = tmp_path / "linked.docx"
    with ZipFile(path) as source, ZipFile(out, "w") as dest:
        for item in source.infolist():
            data = source.read(item.filename)
            if item.filename == "word/_rels/document.xml.rels":
                data = data.replace(
                    b"</Relationships>",
                    b'<Relationship Id="rLink" '
                    b'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink" '
                    b'Target="https://example.com/advisory" TargetMode="External"/></Relationships>',
                )
            dest.writestr(item, data)
    doc = extract_docx(out, _settings())
    assert [b.text for b in doc.blocks] == ["See the advisory for details."]


@pytest.mark.asyncio
async def test_encrypted_docx_fails_with_unsafe_document_in_extractor_and_worker(tmp_path, monkeypatch):
    from docx import Document
    import httpx
    from secai_gateway.app import create_app
    from secai_gateway.translation.documents import extract_docx
    from secai_gateway.translation.jobs import JobStore
    from secai_gateway.translation.worker import Worker

    if shutil.which("zip") is None:
        pytest.skip("zip utility unavailable for encrypted fixture")
    source = tmp_path / "plain.docx"
    document = Document()
    document.add_paragraph("Private text")
    document.save(source)
    parts = tmp_path / "parts"
    parts.mkdir()
    with ZipFile(source) as archive:
        archive.extractall(parts)
    encrypted = tmp_path / "encrypted.docx"
    subprocess.run(["zip", "-q", "-P", "synthetic-only", "-r", str(encrypted), "."],
                   cwd=parts, check=True)

    config = TranslationSettings(db_path=str(tmp_path / "jobs.db"),
                                 artifact_dir=str(tmp_path / "artifacts"), _env_file=None)
    with pytest.raises(TranslationError) as error:
        extract_docx(encrypted, config)
    assert error.value.code == "UNSAFE_DOCUMENT"

    class NoModel:
        async def complete(self, *args, **kwargs):
            raise AssertionError("encrypted input must not reach the model")

    store = JobStore(config)
    job = store.admit("file", encrypted.read_bytes(), ".docx", "en", "vi", "peer", None)
    assert await Worker(store, config, NoModel()).run_once()
    row = store.get(job["job_id"])
    assert (row["status"], row["error_code"]) == ("failed", "UNSAFE_DOCUMENT")

    monkeypatch.setenv("TRANSLATE_V2_ENABLED", "true")
    monkeypatch.setenv("TRANSLATE_V2_DB_PATH", str(tmp_path / "api-jobs.db"))
    monkeypatch.setenv("TRANSLATE_V2_ARTIFACT_DIR", str(tmp_path / "api-artifacts"))
    app = create_app(NoModel())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
        response = await client.post("/ai/v2/translate/files",
                                     files={"file": ("encrypted.docx", encrypted.read_bytes())},
                                     data={"source_lang": "en", "target_lang": "vi"})
        assert response.status_code == 202
        admitted = response.json()
        assert await Worker(app.state.translation_v2_store, app.state.translation_v2_settings,
                            NoModel()).run_once()
        status = await client.get(admitted["status_url"])
        assert (status.status_code, status.json()["status"], status.json()["error"]["code"]) == (
            200, "failed", "UNSAFE_DOCUMENT")
        result = await client.get(admitted["result_url"])
        assert (result.status_code, result.json()["error"]["code"]) == (409, "JOB_FAILED")


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


def test_docx_nested_table_depth_first_without_duplicate_text(tmp_path):
    from docx import Document
    from secai_gateway.translation.documents import extract_docx

    document = Document()
    outer = document.add_table(rows=1, cols=1)
    outer.cell(0, 0).paragraphs[0].text = "Outer"
    inner = outer.cell(0, 0).add_table(rows=1, cols=1)
    inner.cell(0, 0).text = "Inner"
    path = tmp_path / "nested.docx"
    document.save(path)
    result = extract_docx(path, _settings())
    cells = [b for b in result.blocks if b.kind == "table_cell"]
    assert [(b.text, b.metadata["table"]) for b in cells] == [("Outer\n", 1), ("Inner", 2)]


def test_docx_footnote_follows_reference_block(tmp_path):
    from docx import Document
    from secai_gateway.translation.documents import extract_docx

    document = Document()
    document.add_paragraph("Main")
    source = tmp_path / "plain.docx"
    document.save(source)
    target = tmp_path / "note.docx"
    ns = b"http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    with ZipFile(source) as old, ZipFile(target, "w") as new:
        for item in old.infolist():
            data = old.read(item.filename)
            if item.filename == "word/document.xml":
                data = data.replace(b"Main</w:t>", b'Main</w:t><w:footnoteReference w:id="2"/>')
            new.writestr(item, data)
        new.writestr("word/footnotes.xml", b'<w:footnotes xmlns:w="' + ns +
                     b'"><w:footnote w:id="2"><w:p><w:r><w:t>Note</w:t></w:r></w:p>'
                     b'</w:footnote></w:footnotes>')
    result = extract_docx(target, _settings())
    assert [(b.kind, b.text) for b in result.blocks if b.text] == [
        ("paragraph", "Main"), ("footnote", "Note")]


def test_docx_merged_cells_emit_one_origin_with_spans(tmp_path):
    from docx import Document
    from secai_gateway.translation.documents import extract_docx

    document = Document()
    table = document.add_table(rows=2, cols=2)
    table.cell(0, 0).merge(table.cell(1, 1)).text = "Merged"
    path = tmp_path / "merged.docx"
    document.save(path)
    result = extract_docx(path, _settings())
    cells = [b for b in result.blocks if b.kind == "table_cell"]
    assert len(cells) == 1
    assert cells[0].metadata["row_span"] == 2
    assert cells[0].metadata["column_span"] == 2


def test_docx_paragraph_linebreak_and_tab_are_preserved(tmp_path):
    from docx import Document
    from secai_gateway.translation.documents import extract_docx

    document = Document()
    run = document.add_paragraph().add_run("First")
    run.add_break()
    run.add_text("Second")
    run.add_tab()
    run.add_text("Third")
    path = tmp_path / "breaks.docx"
    document.save(path)
    result = extract_docx(path, _settings())
    assert result.blocks[0].text == "First\nSecond\tThird"


def _write_pdf(path, lines):
    from reportlab.pdfgen import canvas

    c = canvas.Canvas(str(path))
    y = 750
    for line in lines:
        c.drawString(100, y, line)
        y -= 20
    c.save()


def test_pdf_text_extraction_with_page_metadata(tmp_path):
    from secai_gateway.translation.documents import extract_pdf

    path = tmp_path / "x.pdf"
    _write_pdf(path, ["Hello translation test CVE-2024-1234"])
    doc, scan_pages = extract_pdf(path, _settings())
    assert [(b.kind, b.text, b.metadata["page"]) for b in doc.blocks] == [
        ("paragraph", "Hello translation test CVE-2024-1234", 1),
    ]
    assert scan_pages == ()


def test_pdf_blank_page_is_reported_as_scan_candidate(tmp_path):
    from reportlab.pdfgen import canvas
    from secai_gateway.translation.documents import extract_pdf

    path = tmp_path / "mixed.pdf"
    c = canvas.Canvas(str(path))
    c.showPage()  # page 1: blank, no text layer
    c.drawString(100, 750, "Page two has text")
    c.save()
    doc, scan_pages = extract_pdf(path, _settings())
    assert [(b.text, b.metadata["page"]) for b in doc.blocks] == [("Page two has text", 2)]
    assert scan_pages == (1,)


def test_pdf_fully_blank_is_all_scan_candidates(tmp_path):
    from reportlab.pdfgen import canvas
    from secai_gateway.translation.documents import extract_pdf

    path = tmp_path / "blank.pdf"
    c = canvas.Canvas(str(path))
    c.showPage()
    c.showPage()
    c.save()
    doc, scan_pages = extract_pdf(path, _settings())
    assert doc.blocks == ()
    assert scan_pages == (1, 2)


def test_pdf_corrupt_is_rejected(tmp_path):
    from secai_gateway.translation.documents import extract_pdf

    path = tmp_path / "bad.pdf"
    path.write_bytes(b"%PDF-1.7 not really a pdf" + b"\x00" * 50)
    with pytest.raises(TranslationError) as e:
        extract_pdf(path, _settings())
    assert e.value.code == "CORRUPT_DOCUMENT"


def test_pdf_encrypted_is_rejected(tmp_path):
    import io

    import pypdf

    from secai_gateway.translation.documents import extract_pdf

    source = tmp_path / "plain.pdf"
    _write_pdf(source, ["Secret"])
    writer = pypdf.PdfWriter()
    writer.append(str(source))
    writer.encrypt(user_password="secret123", owner_password=None)
    out = io.BytesIO()
    writer.write(out)
    path = tmp_path / "enc.pdf"
    path.write_bytes(out.getvalue())
    with pytest.raises(TranslationError) as e:
        extract_pdf(path, _settings())
    assert e.value.code == "UNSAFE_DOCUMENT"


def test_pdf_too_many_pages_is_rejected(tmp_path):
    from secai_gateway.translation.documents import extract_pdf

    path = tmp_path / "many.pdf"
    _write_pdf(path, [f"Page {i}" for i in range(3)])
    settings = TranslationSettings(_env_file=None, pdf_max_pages=0)
    with pytest.raises(TranslationError) as e:
        extract_pdf(path, settings)
    assert e.value.code == "TEXT_TOO_LONG"


def test_render_pdf_page_rasterizes_at_expected_dpi(tmp_path):
    from secai_gateway.translation.documents import render_pdf_page

    path = tmp_path / "x.pdf"
    _write_pdf(path, ["Text for rasterization"])
    out = render_pdf_page(path, 1, tmp_path, dpi=200)
    from PIL import Image

    with Image.open(out) as image:
        # reportlab's default page is A4 (595x842 pt) at 200 DPI.
        assert image.size == (1654, 2339)


def test_render_pdf_page_out_of_range_is_rejected(tmp_path):
    from secai_gateway.translation.documents import render_pdf_page

    path = tmp_path / "x.pdf"
    _write_pdf(path, ["One page"])
    with pytest.raises(TranslationError) as e:
        render_pdf_page(path, 2, tmp_path)
    assert e.value.code == "CORRUPT_DOCUMENT"
