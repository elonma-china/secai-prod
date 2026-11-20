"""Event dict helpers shared by the streaming PoC flows (exploit-v2, review, document agent).

Flows are async generators that emit event dicts; the endpoint wraps them as SSE
(`step`/`log`/`final`). Kept in one place so every flow shares a single frame format for the FE.
(The exploit-advance / lane1 flows that originally shared it were removed on 2026-11-20.)
"""
from __future__ import annotations

from typing import Any


def _step(index: int, name: str, status: str, detail: str = "",
          verdict: str | None = None) -> dict[str, Any]:
    return {"type": "step", "index": index, "name": name, "status": status,
            "detail": detail, "verdict": verdict}


def _log(line: str, stream: str = "stdout") -> dict[str, Any]:
    return {"type": "log", "stream": stream, "line": line}
