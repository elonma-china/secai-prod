"""Bounded, logical-order text extraction for TXT and Word documents."""
from __future__ import annotations

import re
import os
import posixpath
import shutil
import subprocess
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

import olefile

from .settings import TranslationSettings
from .types import DocumentBlock, ExtractedDocument, TranslationError, WarningItem

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
_VI = set("ăâđêôơưĂÂĐÊÔƠƯ")
_SIGNATURES = (b"%PDF", b"PK\x03\x04", b"\xd0\xcf\x11\xe0", b"\x89PNG", b"\xff\xd8\xff")


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
                if text.encode("cp1258", errors="strict") != payload or not (_VI & set(text)):
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
        if inflated > settings.max_docx_inflated_bytes or inflated > settings.max_docx_ratio * packed:
            raise TranslationError("ARCHIVE_LIMIT_EXCEEDED", "Document archive limit exceeded.")
        result: dict[str, bytes] = {}
        for item in entries:
            name = item.filename
            if (name.startswith("/") or "\\" in name or "\x00" in name
                    or any(part in {"", ".", ".."} for part in name.rstrip("/").split("/"))
                    or (item.external_attr >> 16) & 0o170000 == 0o120000):
                raise TranslationError("UNSAFE_DOCUMENT", "Document contains unsafe archive entries.")
            if name in result:
                raise TranslationError("UNSAFE_DOCUMENT", "Document contains duplicate archive entries.")
            if name.endswith("/"):
                continue
            with archive.open(item) as stream:
                chunks: list[bytes] = []
                total = 0
                while chunk := stream.read(64 * 1024):
                    total += len(chunk)
                    if total > item.file_size or total > settings.max_docx_inflated_bytes:
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
    return "".join(node.text or "" for node in element.iter()
                   if node.tag in {_W + "t", _W + "delText"})


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
                    if rel.attrib.get("TargetMode", "").lower() == "external":
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
                items.append(("footnote", _children_text(note), {"footnote_id": note_id}))

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
        for row_index, row in enumerate(table.findall(_W + "tr")):
            col_index = 0
            for cell in row.findall(_W + "tc"):
                span = cell.find("./" + _W + "tcPr/" + _W + "gridSpan")
                width = int(span.attrib.get(_W + "val", "1")) if span is not None else 1
                merge = cell.find("./" + _W + "tcPr/" + _W + "vMerge")
                if merge is not None and merge.attrib.get(_W + "val") != "restart":
                    col_index += width
                    continue
                meta = {"section": section, "table": current_table, "row": row_index,
                        "column": col_index}
                if width > 1:
                    meta["column_span"] = width
                items.append(("table_cell", _children_text(cell), meta))
                add_notes(cell)
                for nested in cell.findall(_W + "tbl"):
                    walk_table(nested)
                col_index += width

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
    if any("endnotes" in name or "comments" in name for name in parts):
        warnings.append(WarningItem(code="UNSUPPORTED_PART", message="Some document parts are unsupported."))
    return _make_blocks(items, settings, warnings)


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
    return subprocess.run(["docker", "image", "inspect", settings.converter_image],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                          timeout=5, check=False).returncode == 0


def convert_doc(path: Path, work_dir: Path, settings: TranslationSettings) -> Path:
    """Convert old Word input in a networkless, bounded, read-only container."""
    validate_doc(path)
    if not converter_available(settings):
        raise TranslationError("DEPENDENCY_UNAVAILABLE", "DOC converter is unavailable.")
    incoming = work_dir / "incoming"
    outgoing = work_dir / "outgoing"
    incoming.mkdir(mode=0o755)
    outgoing.mkdir(mode=0o777)
    source = incoming / "document.doc"
    source.write_bytes(path.read_bytes())
    source.chmod(0o644)
    argv = [
        "docker", "run", "--rm", "--network", "none", "--read-only", "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges", "--pids-limit", "64", "--memory", "768m",
        "--cpus", "1", "--user", "65534:65534", "--tmpfs", "/tmp:rw,nosuid,nodev,size=256m",
        "--mount", f"type=bind,source={incoming.resolve()},target=/input,readonly",
        "--mount", f"type=bind,source={outgoing.resolve()},target=/output",
        settings.converter_image,
    ]
    try:
        completed = subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL,
                                   timeout=settings.converter_timeout_seconds, check=False)
    except subprocess.TimeoutExpired as exc:
        raise TranslationError("PROCESSING_TIMEOUT", "DOC conversion timed out.") from exc
    result = outgoing / "document.docx"
    if completed.returncode != 0 or not result.is_file():
        raise TranslationError("CORRUPT_DOCUMENT", "DOC conversion failed.")
    return result
