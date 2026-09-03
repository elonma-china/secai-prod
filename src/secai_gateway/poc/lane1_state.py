"""Store trạng thái paused của lane1 (human-in-the-loop) — in-memory, TTL 30 phút."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .lane1_tools import Observation
    from .phases import Lane1State

_TTL = 1800  # giây — session hết hạn sau 30 phút không reply


@dataclass
class PausedState:
    run_id: str
    question: str
    suggestions: list[str]
    # Execution context để resume
    history: list["Observation"]
    phase_state: "Lane1State"
    step: int                        # bước đã chạy tới
    nonce: str
    seen: set[str]                   # dedup keys đã chạy
    # Params để tái tạo run_lane1 call
    cve: str
    host: str
    port: str | None
    vuln_class: str
    success_signal: str | None
    command: str
    max_steps: int
    step_timeout_s: int
    total_timeout_s: int
    base_index: int
    extra: dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.monotonic)


_STORE: dict[str, PausedState] = {}


def save(state: PausedState) -> None:
    _purge()
    _STORE[state.run_id] = state


def load(run_id: str) -> PausedState | None:
    return _STORE.get(run_id)


def clear(run_id: str) -> None:
    _STORE.pop(run_id, None)


def _purge() -> None:
    now = time.monotonic()
    expired = [k for k, v in _STORE.items() if now - v.created_at > _TTL]
    for k in expired:
        del _STORE[k]
