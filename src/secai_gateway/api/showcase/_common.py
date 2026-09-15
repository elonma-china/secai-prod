"""Shared helpers for the showcase routers (nhóm F/D/K/L/E) — file I/O + error envelope.

Every showcase endpoint is read-only and reads from local evidence directories
(see Settings.showcase_* in secai_gateway.config). Errors use the same envelope
as docs/nghiem-thu-showcase/01-API-DESIGN.md §Quy ước chung: ``{"error","message"}``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import HTTPException


def not_found(message: str) -> HTTPException:
    return HTTPException(status_code=404, detail={"error": "not_found", "message": message})


def bad_request(message: str) -> HTTPException:
    return HTTPException(status_code=400, detail={"error": "bad_request", "message": message})


def read_json(path: Path) -> Any:
    """Load one JSON file. Raises the 404 HTTPException above if missing."""
    if not path.is_file():
        raise not_found(f"Không tìm thấy file: {path}")
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def read_jsonl(path: Path, limit: int | None = None) -> list[Any]:
    """Load a .jsonl file (one JSON object per line), optionally capped at ``limit`` rows."""
    if not path.is_file():
        raise not_found(f"Không tìm thấy file: {path}")
    rows: list[Any] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
            if limit is not None and len(rows) >= limit:
                break
    return rows
