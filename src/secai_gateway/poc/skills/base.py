"""Skill = một playbook knowledge theo lớp lỗ hổng (port concept PentestCode `knowledge`).

Được `lane1.build_react_prompt` nhét vào prompt để mồi cho LLM yếu: context ngắn (như brief cho
pentester), chuỗi tool gợi ý theo phase, và template payload tham số hoá `{CMD}`/`{NONCE}` để oracle
chèn nonce tự-inject. `oracle_hint` mô tả "thành công trông thế nào" — vừa feed prompt vừa gợi oracle.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Skill:
    vuln_class: str
    context: str                                   # brief ngắn cho LLM
    suggested_sequence: list[str] = field(default_factory=list)   # gợi ý tool theo thứ tự phase
    payload_templates: list[str] = field(default_factory=list)    # template có {CMD}/{NONCE}
    oracle_hint: str = ""                          # thành công trông thế nào
