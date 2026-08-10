"""Per-run isolated workdirs for multi-user / CCU-safe bughunt artifacts."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any


_SAFE_RUN = re.compile(r"[^a-zA-Z0-9._:-]+")


def sanitize_run_id(run_id: str) -> str:
    raw = (run_id or "").strip() or "anon"
    cleaned = _SAFE_RUN.sub("_", raw)[:120]
    return cleaned or "anon"


def bughunt_data_root(settings: Any | None = None) -> Path:
    if settings is None:
        from ..config import get_settings
        settings = get_settings()
    base = Path(getattr(settings, "gateway_db_path", "./data/gateway.db")).resolve().parent
    root = base / "bughunt"
    root.mkdir(parents=True, exist_ok=True)
    return root


def ensure_run_workdir(run_id: str, *, settings: Any | None = None) -> Path:
    """Return ``…/bughunt/{run_id}/`` with recon/findings layout."""
    out = bughunt_data_root(settings) / sanitize_run_id(run_id)
    for sub in (
        "recon",
        "recon/subdomains",
        "recon/live",
        "recon/ports",
        "recon/urls",
        "recon/js",
        "recon/dirs",
        "recon/params",
        "recon/nuclei",
        "recon/exposure",
        "findings",
    ):
        (out / sub).mkdir(parents=True, exist_ok=True)
    return out


def recon_dir(workdir: Path) -> Path:
    return workdir / "recon"


def findings_dir(workdir: Path) -> Path:
    return workdir / "findings"


def session_file(workdir: Path) -> Path:
    return workdir / "agent_session.json"
