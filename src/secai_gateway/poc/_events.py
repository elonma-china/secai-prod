"""Event dict helpers dùng chung cho các lane của exploit-advance (`advance.py` + `lane1.py`).

Các lane là async generator phát ra event dict; endpoint (`routes.py`) bọc thành SSE
(`step`/`log`/`final`). Tách ra đây để `lane1.py` và `advance.py` cùng dùng, tránh vòng import
và giữ MỘT định dạng frame duy nhất cho FE.
"""
from __future__ import annotations

from typing import Any


def _step(index: int, name: str, status: str, detail: str = "",
          verdict: str | None = None) -> dict[str, Any]:
    return {"type": "step", "index": index, "name": name, "status": status,
            "detail": detail, "verdict": verdict}


def _log(line: str, stream: str = "stdout") -> dict[str, Any]:
    return {"type": "log", "stream": stream, "line": line}
