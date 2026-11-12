"""Bounded, logical-order text extraction for TXT and Word documents."""
from __future__ import annotations

import re
import os
import posixpath
import shutil
import secrets
import subprocess
import threading
import unicodedata
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

import olefile
import pypdf
import pypdfium2
from pypdf.errors import PyPdfError

from .settings import TranslationSettings
from .types import DocumentBlock, ExtractedDocument, TranslationError, WarningItem

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
_VI = set("ăâđêôơưĂÂĐÊÔƠƯ")
_SIGNATURES = (b"%PDF", b"PK\x03\x04", b"\xd0\xcf\x11\xe0", b"\x89PNG", b"\xff\xd8\xff")


def _vietnamese_signal(text: str) -> bool:
    if _VI & set(text):
        return True
    decomposed = unicodedata.normalize("NFD", text)
    for match in re.finditer(r"[aeiouyAEIOUY][\u0300-\u036f]*", decomposed):
        if any(mark in match.group() for mark in "\u0300\u0301\u0303\u0309\u0323"):
            return True
    return False


def _bad_controls(text: str) -> bool:
    return any(ord(c) < 32 and c not in "\t\n\r" or 0x7f <= ord(c) <= 0x9f for c in text)


def _make_blocks(items: list[tuple[str, str, dict]], settings: TranslationSettings,
                 warnings: list[WarningItem] | None = None) -> ExtractedDocument:
    if len(items) > settings.max_blocks:
        raise TranslationError("TEXT_TOO_LONG", "Document has too many blocks.")
    if sum(len(text) for _, text, _ in items) > settings.max_text_codepoints:
        raise TranslationError("TEXT_TOO_LONG", "Document text is too long.")
    if not any(text.strip() for _, text, _ in items):
        raise TranslationError("NO_TEXT_DETECTED", "No text was detected.")
    blocks = tuple(DocumentBlock(id=f"b{i:06d}", kind=kind, order=i-1,
                                 text=text, metadata=metadata)
                   for i, (kind, text, metadata) in enumerate(items, 1))
    return ExtractedDocument(blocks=blocks, warnings=tuple(warnings or ()))


def extract_txt(payload: bytes, settings: TranslationSettings) -> ExtractedDocument:
    if not payload:
        raise TranslationError("EMPTY_FILE", "File is empty.")
    if len(payload) > settings.max_upload_bytes:
        raise TranslationError("FILE_TOO_LARGE", "File is too large.")
    warnings: list[WarningItem] = []
    try:
        if payload.startswith(b"\xef\xbb\xbf"):
            text = payload.decode("utf-8-sig", errors="strict")
        elif payload.startswith(b"\xff\xfe"):
            text = payload[2:].decode("utf-16-le", errors="strict")
        elif payload.startswith(b"\xfe\xff"):
            text = payload[2:].decode("utf-16-be", errors="strict")
        else:
            if payload.startswith(_SIGNATURES):
                raise TranslationError("FILE_TYPE_MISMATCH", "File type does not match extension.")
            try:
                text = payload.decode("utf-8", errors="strict")
            except UnicodeDecodeError:
                text = payload.decode("cp1258", errors="strict")
                if (text.encode("cp1258", errors="strict") != payload
                        or sum(char.isalpha() for char in text) < 4
                        or not _vietnamese_signal(text)):
                    raise TranslationError("UNSUPPORTED_ENCODING", "Text encoding is unsupported.")
                warnings.append(WarningItem(code="ENCODING_ASSUMED",
                                            message="CP1258 encoding was assumed."))
    except UnicodeError as exc:
        raise TranslationError("UNSUPPORTED_ENCODING", "Text encoding is unsupported.") from exc
    if _bad_controls(text) or "\x00" in text:
        raise TranslationError("UNSUPPORTED_ENCODING", "Text contains invalid controls.")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return _make_blocks([("paragraph", line, {}) for line in text.split("\n")], settings, warnings)


def _safe_zip(path: Path, settings: TranslationSettings) -> dict[str, bytes]:
    try:
        archive = zipfile.ZipFile(path)
    except (OSError, zipfile.BadZipFile) as exc:
        raise TranslationError("CORRUPT_DOCUMENT", "Document package is invalid.") from exc
    with archive:
        entries = archive.infolist()
        if len(entries) > settings.max_docx_entries:
            raise TranslationError("ARCHIVE_LIMIT_EXCEEDED", "Document archive limit exceeded.")
        packed = sum(max(1, x.compress_size) for x in entries)
        inflated = sum(x.file_size for x in entries)
        # Only XML/rels parts are inflated; media is accounted for but never read into memory.
        xml_inflated = sum(x.file_size for x in entries if x.filename.lower().endswith((".xml", ".rels")))
        if (inflated > settings.max_docx_inflated_bytes or xml_inflated > settings.max_docx_xml_bytes
                or inflated > settings.max_docx_ratio * packed):
            raise TranslationError("ARCHIVE_LIMIT_EXCEEDED", "Document archive limit exceeded.")
        result: dict[str, bytes] = {}
        for item in entries:
            name = item.filename
            if item.flag_bits & 0x1:
                raise TranslationError("UNSAFE_DOCUMENT", "Encrypted document parts are unsupported.")
            if (name.startswith("/") or "\\" in name or "\x00" in name
                    or any(part in {"", ".", ".."} for part in name.rstrip("/").split("/"))
                    or (item.external_attr >> 16) & 0o170000 == 0o120000):
                raise TranslationError("UNSAFE_DOCUMENT", "Document contains unsafe archive entries.")
            if name in result:
                raise TranslationError("UNSAFE_DOCUMENT", "Document contains duplicate archive entries.")
            if name.endswith("/"):
                continue
            if not name.lower().endswith((".xml", ".rels")):
                result[name] = b""  # name is enough for the active-content checks; skip media bytes
                continue
            with archive.open(item) as stream:
                chunks: list[bytes] = []
                total = 0
                while chunk := stream.read(64 * 1024):
                    total += len(chunk)
                    if total > item.file_size or total > settings.max_docx_xml_bytes:
                        raise TranslationError("ARCHIVE_LIMIT_EXCEEDED", "Document archive limit exceeded.")
                    chunks.append(chunk)
                result[name] = b"".join(chunks)
        return result


def _xml(data: bytes) -> ET.Element:
    if re.search(br"<!\s*(?:DOCTYPE|ENTITY)", data, flags=re.I):
        raise TranslationError("UNSAFE_DOCUMENT", "Document contains unsafe XML.")
    try:
        return ET.fromstring(data)
    except ET.ParseError as exc:
        raise TranslationError("CORRUPT_DOCUMENT", "Document XML is invalid.") from exc


def _paragraph_text(element: ET.Element) -> str:
    pieces: list[str] = []
    for node in element.iter():
        if node.tag == _W + "t":
            pieces.append(node.text or "")
        elif node.tag in {_W + "br", _W + "cr"}:
            pieces.append("\n")
        elif node.tag == _W + "tab":
            pieces.append("\t")
        elif node.tag == _W + "noBreakHyphen":
            pieces.append("-")  # keeps "CVE-2024-1234" a CVE instead of "CVE20241234"
    return "".join(pieces)


def _children_text(element: ET.Element) -> str:
    return "\n".join(_paragraph_text(p) for p in element.findall(_W + "p"))


def extract_docx(path: Path, settings: TranslationSettings) -> ExtractedDocument:
    parts = _safe_zip(path, settings)
    if "[Content_Types].xml" not in parts or "word/document.xml" not in parts:
        raise TranslationError("CORRUPT_DOCUMENT", "Word document parts are missing.")
    for name, data in parts.items():
        low = name.lower()
        if any(x in low for x in ("vbaproject", "activex", "embeddings/", "oleobject")):
            raise TranslationError("UNSAFE_DOCUMENT", "Active document content is unsupported.")
        if low.endswith(".xml") or low.endswith(".rels"):
            root = _xml(data)
            if low.endswith(".rels"):
                for rel in root:
                    if (rel.attrib.get("TargetMode", "").lower() == "external"
                            and not rel.attrib.get("Type", "").lower().endswith("/hyperlink")):
                        raise TranslationError("UNSAFE_DOCUMENT", "External document links are unsupported.")
    root = _xml(parts["word/document.xml"])
    content_types = parts["[Content_Types].xml"]
    if b"wordprocessingml.document.main+xml" not in content_types:
        raise TranslationError("FILE_TYPE_MISMATCH", "File type does not match extension.")
    body = root.find(_W + "body")
    if body is None:
        raise TranslationError("CORRUPT_DOCUMENT", "Word body is missing.")
    items: list[tuple[str, str, dict]] = []
    warnings: list[WarningItem] = []
    table_number = 0
    notes: dict[str, ET.Element] = {}
    if "word/footnotes.xml" in parts:
        for note in _xml(parts["word/footnotes.xml"]).findall(_W + "footnote"):
            notes[note.attrib.get(_W + "id", "")] = note
    referenced: set[str] = set()

    def add_notes(element: ET.Element) -> None:
        for ref in element.iter(_W + "footnoteReference"):
            note_id = ref.attrib.get(_W + "id", "")
            if note_id in referenced:
                continue
            referenced.add(note_id)
            note = notes.get(note_id)
            if note is None:
                warnings.append(WarningItem(code="UNSUPPORTED_PART", message="Footnote is unavailable."))
            else:
                note_text = "\n".join(_paragraph_text(p) for p in note.iter(_W + "p"))
                items.append(("footnote", note_text, {"footnote_id": note_id}))

    rels: dict[str, str] = {}
    if "word/_rels/document.xml.rels" in parts:
        for rel in _xml(parts["word/_rels/document.xml.rels"]):
            target = rel.attrib.get("Target", "")
            resolved = posixpath.normpath("word/" + target)
            if target.startswith("/") or resolved == ".." or resolved.startswith("../"):
                raise TranslationError("UNSAFE_DOCUMENT", "Document relationship is unsafe.")
            rels[rel.attrib.get("Id", "")] = resolved
    section_specs = [node for node in body.iter(_W + "sectPr")]
    if not section_specs:
        section_specs = [ET.Element(_W + "sectPr")]
    section_parts: list[dict[str, str | None]] = []
    inherited: dict[str, str | None] = {"header": None, "footer": None}
    for spec in section_specs:
        effective = dict(inherited)
        for kind in ("header", "footer"):
            for ref in spec.findall(_W + kind + "Reference"):
                if ref.attrib.get(_W + "type", "default") == "default":
                    name = rels.get(ref.attrib.get(_R + "id", ""))
                    if name not in parts:
                        raise TranslationError("CORRUPT_DOCUMENT", "Section part is missing.")
                    effective[kind] = name
        section_parts.append(effective)
        inherited = effective

    def emit_part(kind: str, section: int) -> None:
        name = section_parts[min(section, len(section_parts) - 1)][kind]
        if not name:
            return
        node = _xml(parts[name])
        for p in node.iter(_W + "p"):
            value = _paragraph_text(p)
            if value:
                items.append((kind, value, {"section": section, "source_part": name}))

    section = 0

    def walk_table(table: ET.Element) -> None:
        nonlocal table_number
        table_number += 1
        current_table = table_number
        table_rows: list[list[tuple[int, ET.Element, int]]] = []
        for row in table.findall(_W + "tr"):
            col_index = 0
            cells: list[tuple[int, ET.Element, int]] = []
            for cell in row.findall(_W + "tc"):
                span = cell.find("./" + _W + "tcPr/" + _W + "gridSpan")
                width = int(span.attrib.get(_W + "val", "1")) if span is not None else 1
                if width < 1:
                    raise TranslationError("CORRUPT_DOCUMENT", "Table span is invalid.")
                cells.append((col_index, cell, width))
                col_index += width
            table_rows.append(cells)
        for row_index, row_cells in enumerate(table_rows):
            for col_index, cell, width in row_cells:
                merge = cell.find("./" + _W + "tcPr/" + _W + "vMerge")
                if merge is not None and merge.attrib.get(_W + "val") != "restart":
                    continue
                meta = {"section": section, "table": current_table, "row": row_index,
                        "column": col_index}
                if width > 1:
                    meta["column_span"] = width
                if merge is not None:
                    row_span = 1
                    for next_row in table_rows[row_index + 1:]:
                        following = next((other for column, other, other_width in next_row
                                          if column == col_index and other_width == width), None)
                        if following is None:
                            break
                        continuation = following.find("./" + _W + "tcPr/" + _W + "vMerge")
                        if continuation is None or continuation.attrib.get(_W + "val") == "restart":
                            break
                        row_span += 1
                    if row_span > 1:
                        meta["row_span"] = row_span
                items.append(("table_cell", _children_text(cell), meta))
                add_notes(cell)
                for nested in cell.findall(_W + "tbl"):
                    walk_table(nested)

    emit_part("header", section)
    for child in body:
        if child.tag == _W + "p":
            items.append(("paragraph", _paragraph_text(child), {"section": section}))
            add_notes(child)
            if child.find("./" + _W + "pPr/" + _W + "sectPr") is not None:
                emit_part("footer", section)
                section += 1
                emit_part("header", section)
        elif child.tag == _W + "tbl":
            walk_table(child)
    emit_part("footer", section)
    if any(child.tag in {_W + "sdt", _W + "customXml"} or child.tag.endswith("}AlternateContent")
           for child in body):
        # Content controls are not extracted; say so rather than dropping silently.
        warnings.append(WarningItem(
            code="UNSUPPORTED_PART",
            message="Content controls (w:sdt) were not extracted and need review."))
    if any("endnotes" in name or "comments" in name for name in parts):
        warnings.append(WarningItem(code="UNSUPPORTED_PART", message="Some document parts are unsupported."))
    if any(node.tag.endswith("}txbxContent") or node.tag in {_W + "instrText", _W + "fldSimple"}
           for node in root.iter()):
        warnings.append(WarningItem(code="UNSUPPORTED_PART",
                                    message="Floating or field content needs review."))
    return _make_blocks(items, settings, warnings)


def extract_pdf(path: Path, settings: TranslationSettings
                ) -> tuple[ExtractedDocument, tuple[int, ...]]:
    """Extract the native text layer. A page with no text layer (a scanned page)
    is reported as a 1-indexed page number in the second return value instead of
    raising, so a caller with OCR available can render and recognize just those
    pages; one with no fallback can treat a non-empty result as the whole story.
    """
    try:
        reader = pypdf.PdfReader(str(path))
    except (PyPdfError, OSError, ValueError) as exc:
        raise TranslationError("CORRUPT_DOCUMENT", "PDF structure is invalid.") from exc
    if reader.is_encrypted:
        try:
            decrypted = reader.decrypt("")
        except (PyPdfError, NotImplementedError) as exc:
            raise TranslationError("UNSAFE_DOCUMENT", "Encrypted PDF is unsupported.") from exc
        if decrypted == pypdf.PasswordType.NOT_DECRYPTED:
            raise TranslationError("UNSAFE_DOCUMENT", "Encrypted PDF is unsupported.")
    try:
        pages = reader.pages
        page_count = len(pages)
    except (PyPdfError, OSError, ValueError) as exc:
        raise TranslationError("CORRUPT_DOCUMENT", "PDF structure is invalid.") from exc
    if page_count > settings.pdf_max_pages:
        raise TranslationError("TEXT_TOO_LONG", "PDF has too many pages.")
    items: list[tuple[str, str, dict]] = []
    warnings: list[WarningItem] = []
    has_annotations = False
    scan_pages: list[int] = []
    for index, page in enumerate(pages):
        try:
            text = page.extract_text() or ""
        except (PyPdfError, OSError, ValueError, TypeError) as exc:
            raise TranslationError("CORRUPT_DOCUMENT", "PDF page could not be read.") from exc
        if _bad_controls(text) or "\x00" in text:
            raise TranslationError("UNSUPPORTED_ENCODING", "PDF text contains invalid controls.")
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        if not text.strip():
            scan_pages.append(index + 1)
            continue
        for line in text.split("\n"):
            if line.strip():
                items.append(("paragraph", line, {"page": index + 1}))
        if page.annotations:
            has_annotations = True
    if has_annotations:
        warnings.append(WarningItem(code="UNSUPPORTED_PART",
                                    message="PDF annotations were not extracted and need review."))
    if not items and not scan_pages:
        raise TranslationError("NO_TEXT_DETECTED", "No text was detected.")
    if not items:
        return ExtractedDocument(blocks=(), warnings=tuple(warnings)), tuple(scan_pages)
    return _make_blocks(items, settings, warnings), tuple(scan_pages)


_PDFIUM_LOCK = threading.Lock()


def render_pdf_page(path: Path, page_number: int, work_dir: Path, *, dpi: int = 200) -> Path:
    """Rasterize one 1-indexed PDF page to a PNG for the image-OCR fallback.

    200 DPI was picked from a prior production evaluation: it reads small text
    (phone numbers, form codes) noticeably more reliably than 110-150 DPI, where
    OCR silently swapped digits.
    """
    # PDFium is not thread-safe: concurrent PdfDocument use from the worker's to_thread pool
    # corrupts the native heap ("corrupted double-linked list") and kills the whole worker.
    with _PDFIUM_LOCK:
        try:
            pdf = pypdfium2.PdfDocument(str(path))
        except pypdfium2.PdfiumError as exc:
            raise TranslationError("CORRUPT_DOCUMENT", "PDF structure is invalid.") from exc
        try:
            if not 1 <= page_number <= len(pdf):
                raise TranslationError("CORRUPT_DOCUMENT", "PDF page is out of range.")
            try:
                page = pdf[page_number - 1]
                bitmap = page.render(scale=dpi / 72)
                image = bitmap.to_pil()
            except pypdfium2.PdfiumError as exc:
                raise TranslationError("CORRUPT_DOCUMENT", "PDF page could not be rendered.") from exc
        finally:
            pdf.close()
    out_path = work_dir / f"pdf-page-{page_number:04d}.png"
    image.save(out_path, format="PNG")
    return out_path


def validate_doc(path: Path) -> None:
    try:
        with olefile.OleFileIO(str(path)) as ole:
            streams = ole.listdir()
            if ["WordDocument"] not in streams:
                raise TranslationError("CORRUPT_DOCUMENT", "WordDocument stream is missing.")
            if any(any(part.lower() in {"vba", "macros", "objectpool", "_vba_project_cur"}
                       for part in stream) for stream in streams):
                raise TranslationError("UNSAFE_DOCUMENT", "Active document content is unsupported.")
    except TranslationError:
        raise
    except (OSError, ValueError, IOError) as exc:
        raise TranslationError("CORRUPT_DOCUMENT", "DOC structure is invalid.") from exc


def converter_available(settings: TranslationSettings) -> bool:
    if not shutil.which("docker"):
        return False
    try:
        return subprocess.run(["docker", "image", "inspect", settings.converter_image],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                              timeout=5, check=False).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def convert_doc(path: Path, work_dir: Path, settings: TranslationSettings) -> Path:
    """Convert old Word input in a networkless, bounded, read-only container."""
    validate_doc(path)
    if not converter_available(settings):
        raise TranslationError("DEPENDENCY_UNAVAILABLE", "DOC converter is unavailable.")
    incoming = work_dir / "incoming"
    outgoing = work_dir / "outgoing"
    incoming.mkdir(mode=0o700)
    outgoing.mkdir(mode=0o700)
    source = incoming / "document.doc"
    source.write_bytes(path.read_bytes())
    name = "secai-convert-" + secrets.token_hex(8)
    argv = [
        "docker", "run", "--rm", "--name", name,
        "--network", "none", "--read-only", "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges", "--pids-limit", "64", "--memory", "768m",
        "--cpus", "1", "--user", f"{os.getuid()}:{os.getgid()}",
        "--tmpfs", "/tmp:rw,nosuid,nodev,size=256m",
        "--mount", f"type=bind,source={incoming.resolve()},target=/input,readonly",
        "--mount", f"type=bind,source={outgoing.resolve()},target=/output",
        settings.converter_image,
    ]
    try:
        completed = subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL,
                                   timeout=settings.converter_timeout_seconds, check=False)
    except subprocess.TimeoutExpired as exc:
        subprocess.run(["docker", "rm", "-f", name], stdin=subprocess.DEVNULL,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       timeout=5, check=False)
        raise TranslationError("PROCESSING_TIMEOUT", "DOC conversion timed out.") from exc
    result = outgoing / "document.docx"
    if completed.returncode != 0 or not result.is_file():
        raise TranslationError("CORRUPT_DOCUMENT", "DOC conversion failed.")
    return result
