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
_RECORDED_ACTION = re.compile(
    r'(?:chặn|cô lập) phiên|đổi mật khẩu|thời điểm cô lập (?:đã )?xác nhận|đã cô lập', re.I,
)
_UNCERTAIN_ACTION = re.compile(
    r'chưa (?:thể )?xác nhận|chưa xác định|không xác nhận|chưa rõ|chưa có (?:thông tin|bằng chứng|dữ liệu)', re.I,
)
_UNKNOWN = re.compile(r'chưa có (?:thông tin|dữ liệu)|chưa thể xác nhận|chưa xác định', re.I)
_CONTROL_ID = re.compile(r'\bCT-\d+\b', re.I)
_IP_ADDRESS = re.compile(r'\b(?:\d{1,3}\.){3}\d{1,3}\b')
_DUPLICATE_MARKER = re.compile(r'\bduplicate\b|trùng lặp|bản ghi lặp', re.I)
_EVENT_LINE = re.compile(r'^\s*[-*]\s*(?:(\d{4}-\d{2}-\d{2})\s+)?(\d{2}:\d{2}):\s*(.+?)\s*$', re.I)
_OUTCOME_WORD = re.compile(
    r'(?<!\w)(?:(?:chưa|không)\s+)?đạt(?!\w)|không\s+đáp\s+ứng(?:\s+yêu\s+cầu)?|đáp\s+ứng\s+yêu\s+cầu',
    re.I,
)
_OUTCOME_CONTEXT = re.compile(r'\bCT-\d+|kiểm soát|trạng thái|tuân thủ|đánh giá|hồ sơ', re.I)
_BARE_OUTCOME = re.compile(
    r'^\s*[-*]?\s*(?:(?:(?:chưa|không)\s+)?đạt(?:\s+yêu\s+cầu)?|'
    r'(?:không\s+)?đáp\s+ứng\s+yêu\s+cầu)\s*[.!]?\s*$', re.I,
)
_UNCERTAIN_OUTCOME = re.compile(
    r'chưa thể xác nhận|chưa xác định|không thể kết luận|chưa có kết luận|chưa đối soát|'
    r'không đủ bằng chứng để kết luận', re.I,
)
_IMPERATIVE_OUTCOME = re.compile(r'\bhãy\b|đề nghị\s+kết luận|yêu cầu\s+(?:viết|kết luận|ghi)', re.I)


def _has_conclusive_outcome(text: str) -> bool:
    # Clause boundaries prevent uncertainty for one control from excusing a claim about another.
    for clause in re.split(r'\.\s+|[\n;,]|\bnhưng\b', text, flags=re.I):
        if _UNCERTAIN_OUTCOME.search(clause) or _IMPERATIVE_OUTCOME.search(clause):
            continue
        if not (_OUTCOME_CONTEXT.search(clause) or _BARE_OUTCOME.match(clause)):
            continue
        for match in _OUTCOME_WORD.finditer(clause):
            # "đạt được mục tiêu" describes achievement, not a pass/fail status.
            if not clause[match.end():].lstrip().casefold().startswith('được'):
                return True
    return False


def _unsupported_control_ip_pair(content: str, source: str) -> bool:
    # Bounded to CT-<number> control IDs; a source-wide IP is shared, a record IP is local.
    factual_source = re.sub(r'(?ms)^```.*?^```', '', source)
    shared_ips: set[str] = set()
    pairs: set[tuple[str, str]] = set()
    source_controls: set[str] = set()
    for clause in re.split(r'\.\s+|[;\n]', factual_source):
        controls = {match.group(0).casefold() for match in _CONTROL_ID.finditer(clause)}
        source_controls.update(controls)
        ips = {match.group(0) for match in _IP_ADDRESS.finditer(clause)}
        if not controls:
            shared_ips.update(ips)
        pairs.update((control, ip) for control in controls for ip in ips)
    for clause in re.split(r'\.\s+|[;\n]', content):
        controls = {match.group(0).casefold() for match in _CONTROL_ID.finditer(clause)}
        ips = {match.group(0) for match in _IP_ADDRESS.finditer(clause)}
        if any(ip not in shared_ips and (control, ip) not in pairs
               for control in controls for ip in ips):
            return True
    # In a multi-control report, a record-only IP shown alone under scope implies a shared scope.
    scope = re.search(r'^#{1,6}\s+Phạm vi\s*$([\s\S]*?)(?=^#{1,6}\s+|\Z)', content, re.I | re.M)
    if scope and len(source_controls) > 1:
        for clause in re.split(r'\.\s+|[;\n]', scope.group(1)):
            if _CONTROL_ID.search(clause):
                continue
            for match in _IP_ADDRESS.finditer(clause):
                ip = match.group(0)
                if ip not in shared_ips and {control for control, value in pairs if value == ip} != source_controls:
                    return True
    return False


def _repeated_duplicate_event(content: str, source: str) -> bool:
    if not _DUPLICATE_MARKER.search(source):
        return False
    seen: set[tuple[str, str]] = set()
    for line in content.splitlines():
        if _HEADING.match(line):
            seen.clear()  # A treatment recap may repeat the timeline's action.
        match = _EVENT_LINE.match(line)
        if not match:
            continue
        action = match.group(3).strip(' .')
        action = re.sub(r'\s*\((?:lặp lại|trùng lặp|duplicate)\)$', '', action, flags=re.I)
        key = (f'{match.group(1) or ""} {match.group(2)}', action.strip().casefold())
        if key in seen:
            return True
        seen.add(key)
    return False


def _confirmed_action(source: str) -> re.Match | None:
    for clause in re.split(r'\.\s+|[;\n,]', source):
        if _UNCERTAIN_ACTION.search(clause):
            continue
        match = _RECORDED_ACTION.search(clause)
        if match:
            return match
    return None

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
        # A dot inside <...> or (...) is URL data; bare sentence punctuation is not.
        if match.end() == len(match.string) or match.string[match.end()].isspace():
            value = value.rstrip('.,;')
        return value  # URL path and query are case-sensitive.
    return value.casefold()


def preserve_other_sections(content: str, previous: str, sections: list[str], instruction: str) -> str:
    mentions = []
    occupied = []
    for section in sorted(sections, key=len, reverse=True):
        pattern = r'(?<!\w)' + re.escape(section) + r'(?!\w)'
        for match in re.finditer(pattern, instruction, re.I):
            if not any(match.start() < end and start < match.end() for start, end in occupied):
                mentions.append((section, match.start()))
                occupied.append((match.start(), match.end()))
    kept = set()
    for section, start in mentions:
        prefix = instruction[max(0, start - 35):start]
        if re.search(r'(?:giữ nguyên|không sửa|đừng thay đổi|đừng sửa)\s+(?:(?:phần|mục)\s+)?$',
                     prefix, re.I):
            kept.add(section)
    editable = [section for section, _ in mentions if section not in kept]
    if len(editable) != 1:
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
        content[new_spans[i][0]:new_spans[i][1]] if section == editable[0]
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
    # One document title before the first section is fine; a new peer-level section is not.
    headings = list(_HEADING.finditer(content))
    required_matches = [match for match in headings if re.sub(r'\s+', ' ', match.group(1)).casefold() in required]
    if required_matches:
        level = min(len(re.match(r'^\s*(#+)', match.group(0)).group(1)) for match in required_matches)
        first = required_matches[0].start()
        if any(match.start() > first and len(re.match(r'^\s*(#+)', match.group(0)).group(1)) <= level
               and re.sub(r'\s+', ' ', match.group(1)).casefold() not in required
               for match in headings):
            raise HTTPException(422, 'Report contains a section outside the template')
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
    if _unsupported_control_ip_pair(content, source):
        raise HTTPException(422, 'Report associates a control with an IP from another source record')
    if _repeated_duplicate_event(content, source):
        raise HTTPException(422, 'Report repeats a source-marked duplicate event as a second action')
    if not _has_conclusive_outcome(source) and _has_conclusive_outcome(content):
        raise HTTPException(422, 'Unsupported outcome: source has no conclusive status; use chưa thể xác nhận and remove inferred reasons')
    counts = set(_CONFLICT_COUNT.findall(source))
    if len(counts) > 1 and not all(re.search(r'(?<!\d)' + re.escape(count) + r'(?!\d)', content) for count in counts):
        raise HTTPException(422, 'Report omitted conflicting source counts')
    action = _confirmed_action(source)
    if action:
        treatment = re.search(r'^#{1,6}\s+Xử lý\s*$([\s\S]*?)(?=^#{1,6}\s+|\Z)', content, re.I | re.M)
        if treatment and _UNKNOWN.search(treatment.group(1)) and not _RECORDED_ACTION.search(treatment.group(1)):
            raise HTTPException(422, f'Mục Xử lý phải nêu hành động nguồn đã xác nhận ({action.group(0)}); không ghi chung là chưa rõ')
    if previous and _NO_DATA_LOSS.search(source) and _NO_DATA_LOSS.search(previous) and not _NO_DATA_LOSS.search(content):
        raise HTTPException(422, 'Report edit removed the no-data-loss observation')
    return neutralize_active_content(content.strip())
