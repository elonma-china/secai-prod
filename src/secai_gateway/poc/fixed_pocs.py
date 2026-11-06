"""Verified-good PoC override store, keyed by CVE and (optionally) target lab.

The exploit endpoint normally consolidates a fetched PoC into ONE runnable file via an LLM
pass (`poc/consolidate.py`). That pass is lossy in practice — it can drop an ``import``,
rename a module constant inconsistently, or merge from an *unfixed* upstream checkout whose
own bugs (a missing function param, a dataclass field, a missing localhost-redirect handler)
were already patched in the lab's copy. The result is output that needs manual fixing before
it runs.

This module is the fix: once a PoC has been fetched, FIXED and **verified against a lab**, the
operator saves the good copy here keyed by CVE. On the request path `load_fixed()` wins over the
LLM consolidation, so the CVE always returns the known-good, copy-paste-runnable file.

Some CVEs have SEVERAL distinct public PoCs that each target a *different* lab (e.g.
CVE-2026-42533: ``DepthFirstDisclosures/Nginx-Rift`` → a TLS stream lab on :29443, versus
``imbas007/CVE-2026-42533`` → an HTTP ``map``-clobber lab on :8094). A single CVE-keyed file
can't serve both, so entries may be **target-scoped**:

* ``CVE-YYYY-NNNN.json``                  — the default (generic) verified PoC. Unscoped
  (no ``targets`` field) it matches any request; with a ``targets`` field it only matches
  those targets and lets a non-matching request fall through to the normal fetch path.
* ``CVE-YYYY-NNNN@<host>_<port>.json``    — a target-specific variant; scoped by its
  ``targets`` field (or its filename) to that lab.

Selection is target-aware but backward compatible: a request with no identifiable target still
gets the CVE-level default, and an unscoped default still matches any target — so existing
single-PoC-per-CVE stores behave exactly as before.

Design mirrors `ondemand_cache.py`:
- One JSON file per (CVE[, target]) holding an evidence dict the injector consumes directly
  (same shape `poc/consolidate.py` returns, plus ``fixed=True``).
- Fail-open everywhere: a missing/corrupt/oversize file simply misses and the normal
  consolidation path runs instead.
- The entry is flagged ``consolidated=True`` (single ``source_code`` file, skipped by the
  display fuller) AND ``fixed=True`` so `poc/inject.py` renders it as a curated copy.
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

# Target parsing (host[:port], optional scheme). Lab targets are IPv4/hostname + port, e.g.
# "46.250.247.218:8094" or "http://46.250.247.218:8094". Requiring a port OR an http(s) scheme
# keeps version strings ("1.27.4", "37.0.3.1") from being mistaken for a target in free text.
_IPV4 = r"(?:(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\.){3}(?:25[0-5]|2[0-4]\d|[01]?\d\d?)"
_HOSTNAME = r"(?=.{1,253}$)(?!-)[A-Za-z0-9-]{1,63}(?<!-)(?:\.(?!-)[A-Za-z0-9-]{1,63}(?<!-))*"
_TARGET_RE = re.compile(
    rf"(?:(?P<scheme>https?://))?"
    rf"(?P<host>{_IPV4}|{_HOSTNAME})"
    rf"(?::(?P<port>\d{{1,5}}))?",
    re.I,
)
_SCHEME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*://")


def extract_target(text: str | None) -> str | None:
    """Extract a normalized ``host[:port]`` target from free text, or ``None``.

    Only matches targets that are unambiguous: an IPv4/hostname followed by a ``:port``, or
    prefixed with an ``http(s)://`` scheme. A bare dotted quad (e.g. a version ``37.0.3.1``)
    is ignored. Used by the exploit route to scope the verified-good (fixed) PoC lookup to the
    lab actually being targeted."""
    if not text:
        return None
    for m in _TARGET_RE.finditer(text):
        host = m.group("host")
        port = m.group("port")
        scheme = m.group("scheme")
        if not (scheme or port):
            continue  # bare host without port/scheme → skip (version lookalike risk)
        if port is not None and not (1 <= int(port) <= 65535):
            continue
        if host.startswith("[") and host.endswith("]"):
            host = host[1:-1]
        host = host.rstrip(".").lower()
        if not host:
            continue
        return f"{host}:{port}" if port else host
    return None


def _split_target(target: str | None) -> tuple[str | None, str | None]:
    """Normalize a raw target (host[:port], optional scheme) into ``(host, port)``.
    ``(None, None)`` when nothing usable is present. Never raises."""
    t = (target or "").strip()
    if not t:
        return None, None
    t = _SCHEME_RE.sub("", t)  # strip scheme
    t = t.split("/", 1)[0].split("?", 1)[0].split("#", 1)[0]  # strip path/query/fragment
    if t.startswith("["):  # IPv6 literal [::1]:8080
        m = re.match(r"^\[([^\]]+)\](?::(\d+))?$", t)
        if not m:
            return None, None
        return m.group(1).lower(), m.group(2)
    if t.count(":") == 1:
        host, _, port = t.partition(":")
        if port.isdigit() and 1 <= int(port) <= 65535:
            return host.rstrip(".").lower(), port
        return None, None  # a colon that isn't a valid port → not a target
    return t.rstrip(".").lower(), None


def _target_key(host: str | None, port: str | None) -> str:
    """Filesystem-safe suffix for a target-specific fixed file."""
    host_key = re.sub(r"[^a-z0-9.-]+", "_", host or "")
    return f"{host_key}_{port}" if port else host_key


def _targets_match(host: str, port: str | None, entry: Any) -> bool:
    """Whether a scoping entry (e.g. ``"46.250.247.218:8094"``) matches ``(host, port)``.
    Either side lacking a port matches on host alone."""
    eh, ep = _split_target(entry)
    if not eh or eh != host:
        return False
    if port is None or ep is None:
        return True
    return port == ep


def resolve_fixed_dir(settings: Any) -> Path | None:
    """The verified-good store directory from settings.

    Priority:
    1. explicit ``poc_fixed_dir``;
    2. ``<poc_artifact_store>/../pocs-fixed`` — the historical default;
    3. ``<poc_catalog_db>/../pocs-fixed`` — the DEPLOYED layout, where the catalog DB and the
       ``pocs-fixed`` store both live in the same mounted ``/srv/poc`` directory. Preferred as a
       fallback because the historical default resolves to an UNMOUNTED, non-writable path
       (``/srv/pocs-fixed`` when the store is ``/srv/poc-store``), silently orphaning every
       operator-verified PoC. Falls back to (2) when neither exists so a fresh checkout still
       writes somewhere predictable.
    """
    explicit = (getattr(settings, "poc_fixed_dir", "") or "").strip()
    if explicit:
        return Path(explicit)
    store = (getattr(settings, "poc_artifact_store", "") or "").strip()
    store_fixed = (Path(store).parent / "pocs-fixed") if store else None
    catalog = (getattr(settings, "poc_catalog_db", "") or "").strip()
    catalog_fixed = (Path(catalog).parent / "pocs-fixed") if catalog else None
    # Prefer whichever actually exists; the deployed /srv/poc/pocs-fixed wins over the
    # unmounted /srv/pocs-fixed default when both paths are computable.
    for cand in (catalog_fixed, store_fixed):
        if cand is not None and cand.is_dir():
            return cand
    return store_fixed or catalog_fixed


def _fixed_file(cache_dir: Path, cve_id: str) -> Path | None:
    key = (cve_id or "").strip().upper()
    if not _CVE_KEY.match(key):
        return None
    return cache_dir / f"{key}.json"


def _variant_file(cache_dir: Path, cve_id: str, host: str, port: str | None) -> Path:
    return cache_dir / f"{cve_id}@{_target_key(host, port)}.json"


def _read_evidence(path: Path | None, cve_id: str) -> dict[str, Any] | None:
    """Load + validate one fixed file into an injectable evidence dict, or ``None``."""
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


def load_fixed(cache_dir: Path | None, cve_id: str | None,
               target: str | None = None) -> dict[str, Any] | None:
    """Return the verified-good evidence dict for ``cve_id`` scoped to ``target``, or ``None``
    on any miss (disabled, absent, corrupt, oversize, or scoped-to-another-lab). Never raises.

    Selection (target-aware, backward compatible):
    * No ``target`` → the CVE-level default file (current behaviour).
    * ``target`` present →
        1. exact variant file ``<CVE>@<host>_<port>.json``, then ``<CVE>@<host>.json``;
        2. any ``<CVE>@*.json`` variant whose ``targets`` list matches;
        3. the default file — returned when it is unscoped (no ``targets``) or when its
           ``targets`` match the request target; otherwise ``None`` so the normal catalog
           fetch/consolidation path runs (a different lab's PoC must not win)."""
    if cache_dir is None or not cve_id:
        return None
    key = cve_id.strip().upper()
    if not _CVE_KEY.match(key):
        return None
    host, port = _split_target(target) if target else (None, None)

    if host:
        # 1. exact variant files (host:port first, then host-only)
        cand_ports = (port, None) if port else (None,)
        for cand_port in cand_ports:
            ev = _read_evidence(_variant_file(cache_dir, key, host, cand_port), key)
            if ev is not None:
                return ev
        # 2. any variant whose `targets` list matches
        try:
            for path in sorted(cache_dir.glob(f"{key}@*.json")):
                ev = _read_evidence(path, key)
                if ev is None:
                    continue
                if any(_targets_match(host, port, t) for t in (ev.get("targets") or [])):
                    return ev
        except OSError:
            pass

    # 3. default file — unscoped matches any target; scoped must match.
    default = _read_evidence(_fixed_file(cache_dir, key), key)
    if default is None:
        return None
    scoped = default.get("targets") or []
    if not scoped or host is None:
        return default
    if any(_targets_match(host, port, t) for t in scoped):
        return default
    return None


def save_fixed(cache_dir: Path | None, cve_id: str, code: str, language: str = "python",
               *, url: str | None = None, sha256: str | None = None,
               source: str | None = None, section3: str | None = None,
               target: str | None = None, targets: list[str] | None = None) -> bool:
    """Persist a verified-good PoC ``code`` for ``cve_id`` as an injectable evidence dict.
    ``section3`` (optional) is a curated, authoritative ``## 3. Tổng quan Lỗ hổng`` body that
    overrides the model's (potentially inaccurate) overview for this CVE.

    ``target`` (optional) saves a target-specific variant (``<CVE>@<host>_<port>.json``) and
    scopes the entry to that target; ``targets`` (optional list) overrides the stored scope
    list (aliases). Without either, saves the CVE-level default file (unscoped).

    Best-effort: any failure is logged and returns False.

    ``targets=`` without a paired ``target=`` is REJECTED (2026-08 research finding
    target-scope-bypass): without a ``target``, the entry is written to the DEFAULT (unscoped)
    file path, but carries a `"targets"` scope list — and `load_fixed` unconditionally returns
    the default file whenever the REQUEST itself carries no target
    (`if not scoped or host is None: return default`), regardless of that scope. A
    default-path file that LOOKS scoped but is actually served to every target-less request is
    exactly the mis-scoped-entry risk this guard exists to prevent before it can happen —
    calling with only ``target=`` (the normal target-specific-variant path) is unaffected."""
    if targets is not None and not target:
        logger.info("fixed-poc save rejected for %s: targets= given without target= — would "
                    "write a scoped list into the DEFAULT (unscoped) file; pass target= too, "
                    "or omit targets= to save an unscoped default", cve_id)
        return False
    if cache_dir is None or not cve_id or not code:
        return False
    key = cve_id.strip().upper()
    if not _CVE_KEY.match(key):
        return False
    host, port = _split_target(target)
    path = _variant_file(cache_dir, key, host, port) if host else _fixed_file(cache_dir, key)
    if path is None:
        return False
    scope = [t for t in (targets or [])] if targets is not None else ([target] if target else None)
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
        "targets": scope,
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
