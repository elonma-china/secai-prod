"""Persistent per-CVE cache for the CONSOLIDATED (AI-merged) PoC file.

Distinct from ``ondemand_cache`` — that tier caches the RAW code fetched from the internet
so it isn't re-downloaded; this tier caches the *output* of the expensive consolidation LLM
pass (``poc/consolidate.py``) so a repeat request for the same CVE reuses the merged file
instead of paying another 8-55s model round-trip. The two compose: raw-fetch cache removes
the network round-trip, this removes the LLM round-trip.

Design notes (mirrors ``ondemand_cache``'s fail-open contract):
- One JSON file per (CVE, cache-key) holding exactly the consolidated evidence dict
  ``consolidate_poc`` returns (``consolidated=True`` shape). PoC files are small text →
  stored inline, no blob store.
- **Cache key = digest of the exact consolidation PROMPT** (which already folds in the source
  bytes, CVE id, language, and budget-truncation) PLUS the sampling temperature and a
  ``_CACHE_VERSION`` bump. So the cache invalidates automatically when the underlying PoC
  changes, the prompt is truncated differently, or the temperature is retuned; bump
  ``_CACHE_VERSION`` (or delete the dir) to invalidate everything at once after a
  prompt-template / model change, which the key can't see.
- Fail-open everywhere: a missing/corrupt/oversize/version-mismatched file simply misses and
  the caller recomputes; any write error is swallowed. Only VALIDATED successes are cached —
  a consolidation miss (timeout, no code block, bad syntax) is never stored, so it retries.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Bump when the consolidation prompt template (consolidate.md) or the model changes in a way
# the per-request prompt hash can't see — invalidates every stored entry at once.
_CACHE_VERSION = 1
_MAX_CACHE_BYTES = 4_000_000  # backstop a pathological file (PoC sets are KB-range)
_CVE_KEY = re.compile(r"^CVE-\d{4}-\d{3,7}$", re.I)


def resolve_consolidated_cache_dir(settings: Any) -> Path | None:
    """The consolidated-output cache directory, defaulting to
    ``<poc_artifact_store>/../consolidated-cache`` when unset. ``None`` only when no base path
    can be derived at all (misconfigured store) — caching then silently no-ops."""
    explicit = (getattr(settings, "poc_consolidated_cache_dir", "") or "").strip()
    if explicit:
        return Path(explicit)
    store = (getattr(settings, "poc_artifact_store", "") or "").strip()
    if store:
        return Path(store).parent / "consolidated-cache"
    return None


def cache_key(prompt: str, temperature: float) -> str:
    """A stable 16-hex-char key for a consolidation request. Folds in the full prompt (source
    bytes + CVE + language + budget-truncated content), the sampling temperature, and the
    module cache version — so any change to what would actually be generated yields a new
    key and misses the stale entry."""
    h = hashlib.sha256()
    h.update(f"v{_CACHE_VERSION}\x00{temperature}\x00".encode())
    h.update((prompt or "").encode("utf-8", "replace"))
    return h.hexdigest()[:16]


def _cache_file(cache_dir: Path, cve_id: str, key: str) -> Path | None:
    cve = (cve_id or "").strip().upper()
    if not _CVE_KEY.match(cve):  # never build a path from an unvalidated/arbitrary string
        return None
    if not re.fullmatch(r"[0-9a-f]{16}", key or ""):
        return None
    return cache_dir / f"{cve}__{key}.json"


def load_consolidated(cache_dir: Path | None, cve_id: str, key: str) -> dict[str, Any] | None:
    """Return the cached consolidated evidence dict, or ``None`` on any miss (disabled,
    absent, corrupt, oversize, version drift). Never raises."""
    if cache_dir is None:
        return None
    path = _cache_file(cache_dir, cve_id, key)
    if path is None or not path.is_file():
        return None
    try:
        if path.stat().st_size > _MAX_CACHE_BYTES:
            return None
        ev = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.info("consolidated cache read failed for %s", cve_id)
        return None
    if not isinstance(ev, dict) or not ev.get("source_code") or not ev.get("consolidated"):
        return None
    ev["consolidated_cache_hit"] = True  # provenance breadcrumb; harmless to the injector
    return ev


def save_consolidated(cache_dir: Path | None, cve_id: str, key: str,
                      evidence: dict[str, Any]) -> None:
    """Persist a VALIDATED consolidated dict for ``(cve_id, key)``. Best-effort: any failure is
    logged and swallowed — the request already has the result in hand, caching is a bonus.
    Refuses to cache anything that isn't a real consolidated file (no ``source_code``)."""
    if cache_dir is None or not evidence:
        return
    if not evidence.get("source_code") or not evidence.get("consolidated"):
        return
    path = _cache_file(cache_dir, cve_id, key)
    if path is None:
        return
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        # Atomic-ish: temp file then replace, so a concurrent reader never sees half-written JSON.
        tmp = path.with_suffix(".json.tmp")
        payload = {k: v for k, v in evidence.items() if k != "consolidated_cache_hit"}
        tmp.write_text(json.dumps(payload, ensure_ascii=False, default=str), encoding="utf-8")
        tmp.replace(path)
    except OSError:
        logger.info("consolidated cache write failed for %s", cve_id)
