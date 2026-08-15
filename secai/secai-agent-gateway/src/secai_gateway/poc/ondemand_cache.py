"""Persistent per-CVE cache for PoC code fetched on-demand from the internet.

The exploit endpoint acquires real PoC code in priority order **DB → temp cache → internet**
(GitHub tarball / web raw script). This module is the "temp cache" tier: the FIRST time a
CVE's code is pulled from the internet it is saved here as one JSON file, so every later
request for the same CVE reuses the saved copy instead of re-downloading — faster and less
network egress on the request path.

Design notes:
- One file per CVE (``CVE-YYYY-NNNN.json``) holding exactly the evidence dict the injector /
  consolidation consume (``_github_evidence_from_fetch`` shape). PoC files are small text, so
  the whole set is stored inline as UTF-8 — no separate blob store, no catalog mutation (the
  authoritative catalog is built by the offline batch pipeline; this stays a throwaway cache).
- Fail-open everywhere: a missing/corrupt/oversize file simply misses, and any write error is
  swallowed — the caller re-fetches from the internet, exactly as if the cache weren't there.
- No TTL: a public PoC repo is effectively immutable for our purposes; delete the dir to reset.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Guard against a giant cached file eating memory on load (PoC sets are KB-range; this only
# backstops a pathological repo that slipped through the fetcher's own caps).
_MAX_CACHE_BYTES = 4_000_000
_CVE_KEY = re.compile(r"^CVE-\d{4}-\d{3,7}$", re.I)


def resolve_cache_dir(settings: Any) -> Path | None:
    """The on-demand cache directory from settings, defaulting to
    ``<poc_artifact_store>/../ondemand-cache`` when unset. ``None`` only if no base path can be
    derived at all (misconfigured store)."""
    explicit = (getattr(settings, "poc_ondemand_cache_dir", "") or "").strip()
    if explicit:
        return Path(explicit)
    store = (getattr(settings, "poc_artifact_store", "") or "").strip()
    if store:
        return Path(store).parent / "ondemand-cache"
    return None


def _cache_file(cache_dir: Path, cve_id: str) -> Path | None:
    key = (cve_id or "").strip().upper()
    if not _CVE_KEY.match(key):  # never build a path from an unvalidated/arbitrary string
        return None
    return cache_dir / f"{key}.json"


def load_cached(cache_dir: Path | None, cve_id: str) -> dict[str, Any] | None:
    """Return the cached real-code evidence dict for ``cve_id``, or ``None`` on any miss
    (disabled, absent, corrupt, oversize). Never raises."""
    if cache_dir is None:
        return None
    path = _cache_file(cache_dir, cve_id)
    if path is None or not path.is_file():
        return None
    try:
        if path.stat().st_size > _MAX_CACHE_BYTES:
            return None
        ev = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.info("ondemand cache read failed for %s", cve_id)
        return None
    if not isinstance(ev, dict) or not (ev.get("source_code") or ev.get("source_files")):
        return None
    ev["ondemand_cached"] = True  # provenance breadcrumb; harmless to the injector
    return ev


def save_cached(cache_dir: Path | None, cve_id: str, evidence: dict[str, Any]) -> None:
    """Persist ``evidence`` (a real-code dict) for ``cve_id``. Best-effort: any failure is
    logged and swallowed — the request already has the evidence in hand, caching is a bonus."""
    if cache_dir is None or not evidence:
        return
    path = _cache_file(cache_dir, cve_id)
    if path is None:
        return
    if not (evidence.get("source_code") or evidence.get("source_files")):
        return
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        # Atomic-ish write: temp file then replace, so a concurrent reader never sees a
        # half-written JSON.
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(evidence, ensure_ascii=False, default=str), encoding="utf-8")
        tmp.replace(path)
    except OSError:
        logger.info("ondemand cache write failed for %s", cve_id)
