"""Bounded structural and literal checks before storing model drafted reports."""
from __future__ import annotations

import re

from fastapi import HTTPException

from ..security import neutralize_active_content

_HEADING = re.compile(r'^\s{0,3}#{1,6}\s+(?:\d+[.)]\s+)?(.+?)\s*#*\s*$', re.M)
_LITERAL = re.compile(
    r'\bCVE-\d{4}-\d{3,7}\b'
    r'|\b(?:\d{1,3}\.){3}\d{1,3}\b'
    r'|https?://[^\s<>)\]]+'
    r'|\b[a-fA-F0-9]{32,64}\b'
    r'|(?<![\w])\d+(?:[.,]\d+)*(?!\w)', re.I,
)


def validate_report(content: str, sections: list[str], source: str) -> str:
    if not content.strip() or len(content) > 40000:
        raise HTTPException(422, 'Model output is empty or too long')
    found = [re.sub(r'\s+', ' ', match.group(1)).casefold() for match in _HEADING.finditer(content)]
    required = [re.sub(r'\s+', ' ', section).casefold() for section in sections]
    cursor = 0
    for section in required:
        try:
            cursor = found.index(section, cursor) + 1
        except ValueError as exc:
            raise HTTPException(422, 'Report is missing a required section or section order') from exc
    # Ignore required heading labels: the template may contain an ordinal or year not in source facts.
    body = _HEADING.sub(lambda match: '' if re.sub(r'\s+', ' ', match.group(1)).casefold() in required else match.group(0), content)
    allowed = {match.group(0).casefold() for match in _LITERAL.finditer(source)}
    for match in _LITERAL.finditer(body):
        if match.group(0).casefold() not in allowed:
            raise HTTPException(422, 'Report contains an unverified identifier or number')
    return neutralize_active_content(content.strip())
