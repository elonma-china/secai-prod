"""Preview plain-text and DOCX documents as editable report templates."""
from __future__ import annotations

import re
from io import BytesIO
from pathlib import Path
from xml.etree import ElementTree
from zipfile import BadZipFile, ZipFile

from fastapi import HTTPException

MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_TEXT_CHARS = 40000
_DOCX_NS = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'


def _docx_text(raw: bytes) -> str:
    try:
        with ZipFile(BytesIO(raw)) as archive:
            entries = archive.infolist()
            if len(entries) > 200 or sum(item.file_size for item in entries) > 8 * 1024 * 1024:
                raise ValueError('DOCX archive too large')
            names = {item.filename for item in entries}
            if any('vbaProject' in name or name.endswith(('.bin', '.exe')) for name in names):
                raise ValueError('Active content is not allowed')
            if 'word/document.xml' not in names:
                raise ValueError('Missing document text')
            xml = archive.read('word/document.xml')
            if b'<!DOCTYPE' in xml.upper() or b'<!ENTITY' in xml.upper():
                raise ValueError('DTD is not allowed')
            root = ElementTree.fromstring(xml)
            paragraphs = []
            for paragraph in root.iter(_DOCX_NS + 'p'):
                line = ''.join(node.text or '' for node in paragraph.iter(_DOCX_NS + 't'))
                if line.strip():
                    paragraphs.append(line)
            return '\n'.join(paragraphs)
    except (BadZipFile, KeyError, ElementTree.ParseError, ValueError, RuntimeError, NotImplementedError) as exc:
        raise HTTPException(422, f'Invalid DOCX: {exc}') from exc


def preview(filename: str, raw: bytes) -> dict:
    if len(raw) > MAX_FILE_BYTES:
        raise HTTPException(413, 'File exceeds 2 MiB')
    suffix = Path(filename).suffix.lower()
    if suffix not in {'.docx', '.md', '.txt'}:
        raise HTTPException(422, 'Supported files: .docx, .md, .txt')
    if suffix == '.docx':
        text = _docx_text(raw)
    else:
        try:
            text = raw.decode('utf-8-sig')
        except UnicodeDecodeError as exc:
            raise HTTPException(422, 'File must be UTF-8') from exc
    text = text.strip()
    if not text or len(text) > MAX_TEXT_CHARS:
        raise HTTPException(422, 'Document is empty or exceeds 40000 characters')
    headings = [match.group(1).strip() for line in text.splitlines()
                if (match := re.match(r'^#{1,3}\s+(.+)$', line))]
    return {
        'name': Path(filename).stem[:120], 'description': '',
        'sections': headings[:20] or ['Nội dung'], 'instructions': '', 'example': text,
    }
