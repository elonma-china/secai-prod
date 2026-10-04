"""Bounded structural and literal checks before storing model drafted reports."""
from __future__ import annotations

import re

from fastapi import HTTPException

from ..security import neutralize_active_content

_HEADING = re.compile(r'^\s{0,3}#{1,6}\s+(?:\d+[.)]\s+)?(.+?)\s*#*\s*$', re.M)
_CRITICAL = re.compile(
    r'\bCVE-\d{4}-\d{3,7}\b'
    r'|\b(?:\d{1,3}\.){3}\d{1,3}\b'
    r'|https?://[^\s<>)\]]+'
    r'|\b[a-fA-F0-9]{32,64}\b', re.I,
)
_CONFLICT_COUNT = re.compile(r'(?:báo cáo|hồ sơ)\s+[AB]\s+ghi\s+(\d+)\s+tài khoản', re.I)
_NO_DATA_LOSS = re.compile(r'(?:chưa|không)[^\n.]{0,40}mất dữ liệu', re.I)
_RECORDED_ACTION = re.compile(r'(?:chặn|cô lập) phiên|đổi mật khẩu', re.I)
_UNKNOWN = re.compile(r'chưa có (?:thông tin|dữ liệu)', re.I)

_LITERAL = re.compile(
    r'\bCVE-\d{4}-\d{3,7}\b'
    r'|\b(?:\d{1,3}\.){3}\d{1,3}\b'
    r'|https?://[^\s<>)\]]+'
    r'|\b[a-fA-F0-9]{32,64}\b'
    r'|(?<![\w])\d+(?:[.,]\d+)*(?!\w)', re.I,
)


def _literal_value(match: re.Match) -> str:
    value = match.group(0)
    if value.lower().startswith(('http://', 'https://')):
        value = value.rstrip('.,;')
    return value.casefold()


def preserve_other_sections(content: str, previous: str, sections: list[str], instruction: str) -> str:
    named = [section for section in sections if section.casefold() in instruction.casefold()]
    if len(named) != 1:
        return content

    def spans(text: str):
        matches = list(_HEADING.finditer(text))
        result = []
        cursor = 0
        for section in sections:
            target = section.casefold()
            found = next((m for m in matches[cursor:] if re.sub(r'\s+', ' ', m.group(1)).casefold() == target), None)
            if found is None:
                return None
            result.append(found)
            cursor = matches.index(found) + 1
        return [(m.start(), result[i + 1].start() if i + 1 < len(result) else len(text))
                for i, m in enumerate(result)]

    old_spans, new_spans = spans(previous), spans(content)
    if not old_spans or not new_spans:
        return content
    return content[:new_spans[0][0]] + ''.join(
        content[new_spans[i][0]:new_spans[i][1]] if section == named[0]
        else previous[old_spans[i][0]:old_spans[i][1]]
        for i, section in enumerate(sections)
    )


def validate_report(content: str, sections: list[str], source: str, previous: str = "") -> str:
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
    allowed = {_literal_value(match) for match in _LITERAL.finditer(source)}
    for match in _LITERAL.finditer(body):
        if _literal_value(match) not in allowed:
            raise HTTPException(422, 'Report contains an unverified identifier or number')
    prior_literals = {_literal_value(match) for match in _CRITICAL.finditer(previous)}
    current_literals = {_literal_value(match) for match in _CRITICAL.finditer(content)}
    if not prior_literals.issubset(current_literals):
        raise HTTPException(422, 'Report edit removed a critical identifier')
    counts = set(_CONFLICT_COUNT.findall(source))
    if len(counts) > 1 and not all(re.search(r'(?<!\d)' + re.escape(count) + r'(?!\d)', content) for count in counts):
        raise HTTPException(422, 'Report omitted conflicting source counts')
    if _RECORDED_ACTION.search(source):
        treatment = re.search(r'^#{1,6}\s+Xử lý\s*$([\s\S]*?)(?=^#{1,6}\s+|\Z)', content, re.I | re.M)
        if treatment and _UNKNOWN.search(treatment.group(1)) and not _RECORDED_ACTION.search(treatment.group(1)):
            raise HTTPException(422, 'Recorded action cannot be reported as missing')
    if previous and _NO_DATA_LOSS.search(source) and _NO_DATA_LOSS.search(previous) and not _NO_DATA_LOSS.search(content):
        raise HTTPException(422, 'Report edit removed the no-data-loss observation')
    return neutralize_active_content(content.strip())
