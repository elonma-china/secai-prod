"""Verified-good PoC override store, keyed by CVE.

The exploit endpoint normally consolidates a fetched PoC into ONE runnable file via an LLM
pass (`poc/consolidate.py`). That pass is lossy in practice — it can drop an ``import``,
rename a module constant inconsistently, or merge from an *unfixed* upstream checkout whose
own bugs (a missing function param, a dataclass field, a missing localhost-redirect handler)
were already patched in the lab's copy. The result is output that needs manual fixing before
it runs.

This module is the fix: once a PoC has been fetched, FIXED and **verified against a lab**, the
operator saves the good copy here keyed by CVE. On the request path `load_fixed()` wins over the
LLM consolidation, so the CVE always returns the known-good, copy-paste-runnable file.

Design mirrors `ondemand_cache.py`:
- One JSON file per CVE (``CVE-YYYY-NNNN.json``) holding an evidence dict the injector
  consumes directly (same shape `poc/consolidate.py` returns, plus ``fixed=True``).
- Fail-open everywhere: a missing/corrupt/oversize file simply misses and the normal
  consolidation path runs instead.
- The entry is flagged ``consolidated=True`` (single ``source_code`` file, skipped by the
  display fuller) AND ``fixed=True`` so `poc/inject.py` renders it without the "AI hợp nhất /
  CHƯA chạy thử" banner — the fixed copy *has* been verified.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_MAX_BYTES = 4_000_000
_CVE_KEY = re.compile(r"^CVE-\d{4}-\d{3,7}$", re.I)


def resolve_fixed_dir(settings: Any) -> Path | None:
    """The verified-good store directory from settings, defaulting to
    ``<poc_artifact_store>/../pocs-fixed``. ``None`` only if no base path can be derived."""
    explicit = (getattr(settings, "poc_fixed_dir", "") or "").strip()
    if explicit:
        return Path(explicit)
    store = (getattr(settings, "poc_artifact_store", "") or "").strip()
    if store:
        return Path(store).parent / "pocs-fixed"
    return None


def _fixed_file(cache_dir: Path, cve_id: str) -> Path | None:
    key = (cve_id or "").strip().upper()
    if not _CVE_KEY.match(key):
        return None
    return cache_dir / f"{key}.json"


def load_fixed(cache_dir: Path | None, cve_id: str | None) -> dict[str, Any] | None:
    """Return the verified-good evidence dict for ``cve_id``, or ``None`` on any miss
    (disabled, absent, corrupt, oversize). Never raises."""
    if cache_dir is None or not cve_id:
        return None
    path = _fixed_file(cache_dir, cve_id)
    if path is None or not path.is_file():
        return None
    try:
        if path.stat().st_size > _MAX_BYTES:
            return None
        ev = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.info("fixed-poc read failed for %s", cve_id)
        return None
    if not isinstance(ev, dict) or not ev.get("source_code"):
        return None
    ev["consolidated"] = True  # single source_code file — skip the display fuller re-read
    ev["fixed"] = True
    return ev


def save_fixed(cache_dir: Path | None, cve_id: str, code: str, language: str = "python",
               *, url: str | None = None, sha256: str | None = None,
               source: str | None = None, section3: str | None = None) -> bool:
    """Persist a verified-good PoC ``code`` for ``cve_id`` as an injectable evidence dict.
    ``section3`` (optional) is a curated, authoritative ``## 3. Tổng quan Lỗ hổng`` body that
    overrides the model's (potentially inaccurate) overview for this CVE. Best-effort: any
    failure is logged and returns False."""
    if cache_dir is None or not cve_id or not code:
        return False
    path = _fixed_file(cache_dir, cve_id)
    if path is None:
        return False
    key = cve_id.strip().upper()
    ev = {
        "kind": "exploit",
        "title": f"Verified PoC {key}",
        "cve_ids": [key],
        "language": language,
        "source_code": code,
        "source_files": None,
        "code_truncated": False,
        "sha256": sha256,
        "url": url,
        "source": source,
        "source_ref": url,
        "consolidated": True,
        "fixed": True,
        "section3": section3,
        "static_indicators": {"language": language, "fixed": True},
    }
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(ev, ensure_ascii=False, default=str), encoding="utf-8")
        tmp.replace(path)
        return True
    except OSError:
        logger.info("fixed-poc write failed for %s", cve_id)
        return False
