"""Phase-control cho lane1 (port concept PentestCode `phase-control`).

Máy trạng thái RECON → ENUM → EXPLOIT → VERIFY giữ LLM yếu (Cyber-F1) không loạn: mỗi phase chỉ
cho phép một tập tool, và luật tiến phase là TẤT ĐỊNH (theo fact quan sát được), không để LLM tự
quyết. `stuck` đếm các lượt phí (reject/dedup/observation rỗng) → vượt ngưỡng thì force-advance để
không kẹt mãi ở một phase.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

STUCK_LIMIT = 3          # số lượt phí liên tiếp trước khi force-advance
ENUM_STEP_LIMIT = 3      # số bước enum tối đa trước khi ép sang EXPLOIT


class Phase(str, Enum):
    RECON = "recon"
    ENUM = "enum"
    EXPLOIT = "exploit"
    VERIFY = "verify"


_ORDER = [Phase.RECON, Phase.ENUM, Phase.EXPLOIT, Phase.VERIFY]

# Tool được phép theo phase. http_request có mặt mọi phase (vũ khí chính, in-process, an toàn).
PHASE_TOOLS: dict[Phase, frozenset[str]] = {
    Phase.RECON: frozenset({"nmap", "http_request"}),
    Phase.ENUM: frozenset({"http_request", "searchsploit", "gobuster"}),
    Phase.EXPLOIT: frozenset({"http_request", "sqlmap", "run_cmd"}),
    Phase.VERIFY: frozenset({"http_request", "run_cmd"}),
}


@dataclass
class Lane1State:
    phase: Phase = Phase.RECON
    step: int = 0
    facts: dict[str, Any] = field(default_factory=dict)   # alive, open_ports, endpoints, partial_hit
    stuck: int = 0
    enum_steps: int = 0


def allowed_tools(state: Lane1State) -> frozenset[str]:
    return PHASE_TOOLS.get(state.phase, PHASE_TOOLS[Phase.EXPLOIT])


def _next(phase: Phase) -> Phase:
    i = _ORDER.index(phase)
    return _ORDER[min(i + 1, len(_ORDER) - 1)]


def advance_phase(state: Lane1State, observation: Any, action: Any) -> Lane1State:
    """Cập nhật phase tại chỗ theo fact quan sát. Trả lại chính `state` để tiện chaining.

    Luật:
      RECON→ENUM  : có fact target sống / port mở.
      ENUM →EXPLOIT: xác nhận có endpoint mục tiêu (fact 'endpoint') hoặc quá ENUM_STEP_LIMIT bước.
      EXPLOIT→VERIFY: observation có chỉ dấu thành công một phần (fact 'partial_hit').
    Force-advance khi `stuck` >= STUCK_LIMIT (chống kẹt).
    """
    if state.stuck >= STUCK_LIMIT and state.phase != Phase.VERIFY:
        state.phase = _next(state.phase)
        state.stuck = 0
        return state

    if state.phase is Phase.RECON:
        if state.facts.get("alive") or state.facts.get("open_ports"):
            state.phase = Phase.ENUM
    elif state.phase is Phase.ENUM:
        state.enum_steps += 1
        if state.facts.get("endpoint") or state.enum_steps >= ENUM_STEP_LIMIT:
            state.phase = Phase.EXPLOIT
    elif state.phase is Phase.EXPLOIT:
        if state.facts.get("partial_hit"):
            state.phase = Phase.VERIFY
    return state
