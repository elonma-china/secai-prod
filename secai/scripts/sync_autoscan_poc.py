#!/usr/bin/env python3
"""Ingest PoC/exploit rows from the Intelligent-AutoScan data API into the gateway's
unified PoC catalog (SQLite, ``secai_gateway.poc.catalog.PocCatalog``).

Sibling of ``sync_autoscan_to_kb.py`` (same API host, same watermark/incremental shape) —
that script feeds the CVE knowledge base (Qdrant/Neo4j); this one feeds the PoC catalog the
``poc_search``/``poc_fetch`` tools query::

    GET /api/exploitdb?page=N&page_size=100        (list; newest-updated first, no CVE ids)
    GET /api/exploitdb/{edb_id}                     (detail; adds cve_ids, file, updated_at)

Every row is upserted under a DEDICATED source label ``autoscan-exploitdb`` — deliberately
distinct from the git-collector's ``exploitdb`` source (``poc/collectors/exploitdb.py``,
which reads a locally-cloned ``files_exploits.csv``). ``UNIQUE(source, source_ref)`` in the
catalog schema means the two never collide or overwrite one another, even though both may
carry the same numeric EDB id as ``source_ref``.

CVE links only exist on the detail endpoint, so by default every list row's detail is
fetched too (parallel, ThreadPoolExecutor) so ``poc_cve`` gets populated. ``--no-detail``
skips that for a much faster pass when only the catalog metadata (no CVE links) matters.

Re-running is safe: catalog upserts are ``ON CONFLICT(source,source_ref) DO UPDATE`` (never
INSERT-duplicates) and ``poc_cve`` links are ``INSERT OR IGNORE`` (never duplicated) — see
``secai_gateway/poc/catalog.py``. On top of that DB-level dedup, the default is
REFRESH-IF-NEWER: a row is re-upserted when the source is newer than its stored
``src_updated_at`` (kept in provenance) or when it is still missing CVE links this record
can supply; an unchanged, fully-linked row is skipped, so steady-state reruns are near
no-ops. ``--no-refresh`` restores pure insert-only; ``--update-existing`` forces every row.
Nothing here ever DELETEs or touches a row under a different ``source``.

Usage:
  python3 sync_autoscan_poc.py --dry-run --max-pages 1     # fetch+map+print only, no DB
  python3 sync_autoscan_poc.py --max-pages 5                # a manual slice (no watermark move)
  python3 sync_autoscan_poc.py                               # incremental to watermark (default)
  python3 sync_autoscan_poc.py --full                        # walk the whole API
  python3 sync_autoscan_poc.py --no-detail                   # fast pass, no cve_ids/language
"""
from __future__ import annotations

import argparse
import base64
import importlib
import json
import os
import re
import sqlite3
import sys
import time
import types
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

# ── endpoints / constants (mirror sync_autoscan_to_kb.py) ─────────────────────────
API_BASE = os.environ.get("AUTOSCAN_API", "http://34.50.65.146:8789").rstrip("/")
API_USER = os.environ.get("AUTOSCAN_USER", "cyber")
# No password baked into source: it must come from AUTOSCAN_PASS or the gateway .env
# (resolved just below, after _ENV loads). main() fail-fasts if it is still empty.
API_PASS = os.environ.get("AUTOSCAN_PASS", "")
GATEWAY_ROOT = "/home/ict/AIEngines/secai/secai-agent-gateway"
GATEWAY_ENV = f"{GATEWAY_ROOT}/.env"
GATEWAY_SRC = f"{GATEWAY_ROOT}/src"
# Same tier of "source" as poc/collectors/exploitdb.py's "exploitdb" — but a DEDICATED
# label, never that one. See module docstring: UNIQUE(source, source_ref) keeps the two
# fully isolated even for the same numeric EDB id.
SOURCE = "autoscan-exploitdb"
STATE = Path("/home/ict/AIEngines/secai/secai-data/_state/autoscan_poc.json")
_CVE = re.compile(r"CVE-\d{4}-\d{3,7}", re.I)


# ── config from gateway .env ────────────────────────────────────────────────────
def load_env(path: str) -> dict[str, str]:
    env: dict[str, str] = {}
    p = Path(path)
    if not p.exists():
        return env
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")
    return env


_ENV = load_env(GATEWAY_ENV)
# Fall back to the gateway .env for the API creds when not set as real env vars (env wins).
if not API_PASS:
    API_PASS = _ENV.get("AUTOSCAN_PASS", "")
if not os.environ.get("AUTOSCAN_USER") and _ENV.get("AUTOSCAN_USER"):
    API_USER = _ENV["AUTOSCAN_USER"]
# Real env var > gateway .env > config.py's own default (secai_gateway.config.Settings.
# poc_catalog_db) — same precedence Settings itself uses (env always wins over .env file).
DEFAULT_DB = os.environ.get("POC_CATALOG_DB") or _ENV.get("POC_CATALOG_DB") or "/srv/exploitdb/poc-catalog.db"


# ── reuse the gateway's PoC models/catalog (no hand-rolled SQL) ────────────────
def _load_gateway_poc_modules():
    """Import ``NormalizedPoc`` and ``PocCatalog`` straight from the gateway source tree,
    WITHOUT executing ``secai_gateway/__init__.py`` (``from .app import create_app`` — a
    module-level ``app = create_app()`` that eagerly builds the entire gateway: LinchRunner,
    LLM/Qdrant/Neo4j/Redis clients, PocCatalog on the DEFAULT (not env-resolved) path, all as
    an import-time side effect — verified: a bare ``import secai_gateway`` from a plain
    ``sys.path`` insert crashes with ``PermissionError: /srv/exploitdb`` because cwd here has
    no ``.env`` for pydantic-settings to pick up) nor ``secai_gateway/poc/__init__.py``
    (heavy re-exports incl. ``LocalPocBackend``, LLM-backed consolidation, etc.).

    Both parent packages are stubbed in ``sys.modules`` with just a ``__path__`` pointing at
    their real directory, so the two LEAF submodules we actually need import their REAL code
    (unmodified), and ``catalog.py``'s own relative import (``from .models import
    NormalizedPoc``) resolves against the stub normally. Neither ``models.py`` nor
    ``catalog.py`` has any import beyond the stdlib, so this needs no extra dependencies
    (no fastapi/pydantic) — matches this script's "stdlib + reused gateway modules" budget.
    """
    if GATEWAY_SRC not in sys.path:
        sys.path.insert(0, GATEWAY_SRC)
    for name, rel in (("secai_gateway", "secai_gateway"), ("secai_gateway.poc", "secai_gateway/poc")):
        if name not in sys.modules:
            stub = types.ModuleType(name)
            stub.__path__ = [str(Path(GATEWAY_SRC) / rel)]  # type: ignore[attr-defined]
            sys.modules[name] = stub
    models = importlib.import_module("secai_gateway.poc.models")
    catalog = importlib.import_module("secai_gateway.poc.catalog")
    return models.NormalizedPoc, catalog.PocCatalog


NormalizedPoc, PocCatalog = _load_gateway_poc_modules()


# ── http helpers ─────────────────────────────────────────────────────────────────
def _basic(user: str, password: str) -> str:
    return "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()


def http_json(url: str, *, method: str = "GET", body: dict | None = None,
              headers: dict | None = None, timeout: float = 60) -> Any:
    data = None if body is None else json.dumps(body).encode()
    hdrs = {"Accept": "application/json", **(headers or {})}
    if data is not None:
        hdrs["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


def api_page(page: int, page_size: int, timeout: float = 60) -> dict[str, Any]:
    q = urllib.parse.urlencode({"page": page, "page_size": page_size})
    return http_json(f"{API_BASE}/api/exploitdb?{q}",
                     headers={"Authorization": _basic(API_USER, API_PASS)}, timeout=timeout)


def api_detail(edb_id: int, timeout: float = 30) -> dict[str, Any]:
    return http_json(f"{API_BASE}/api/exploitdb/{edb_id}",
                     headers={"Authorization": _basic(API_USER, API_PASS)}, timeout=timeout)


# ── field mapping: AutoScan API record(s) → NormalizedPoc ──────────────────────
def normalize_record(rec: dict, detail: dict | None = None) -> "NormalizedPoc":
    """Map one ``/api/exploitdb`` list record (+ its optional ``/api/exploitdb/{id}``
    detail record) to a ``NormalizedPoc``.

    ``rec`` is the list-endpoint shape (``edb_id, description, date_published, author,
    type, platform, verified, source_url, raw_file_url, is_active`` — no CVE ids). When
    ``detail`` is given, its richer fields (``cve_ids``, ``file``, ``updated_at``, ``port``,
    ``tags``) are merged in and win over ``rec`` where both are present.
    """
    merged: dict[str, Any] = {**rec, **(detail or {})}
    edb = str(merged.get("edb_id"))
    desc = (merged.get("description") or "").strip() or "Untitled"
    # `type` (webapps/dos/remote/...) preferred over `platform` (linux/multiple/...) per
    # the agreed mapping convention — whichever is present, `type` wins when both are.
    platform = merged.get("type") or merged.get("platform") or None
    file_path = merged.get("file")  # only present on the detail record
    language = (Path(file_path).suffix.lstrip(".") or None) if file_path else None
    verified = bool(merged.get("verified"))
    raw_cves = merged.get("cve_ids") or []  # only present on the detail record
    cve_ids = sorted({m.upper() for c in raw_cves for m in _CVE.findall(str(c))})
    provenance = {
        "collector": "autoscan-exploitdb",
        "edb_id": merged.get("edb_id"),
        "raw_file_url": merged.get("raw_file_url"),
        # The source freshness stamp (detail `updated_at`, else `date_published`), stored so a
        # later run can REFRESH this row only when the source is genuinely newer (see
        # _poc_needs_write / load_existing). `_wm` is the value fetch_candidates already picked.
        "src_updated_at": merged.get("_wm") or merged.get("updated_at") or merged.get("date_published") or "",
    }
    return NormalizedPoc(
        source=SOURCE, source_ref=edb, kind="exploit", title=desc,
        cve_ids=cve_ids, description=desc, platform=platform, language=language,
        verified=verified, url=merged.get("source_url") or "",
        license="Exploit-DB terms; verify artifact license", redistributable=True,
        date_published=merged.get("date_published"), provenance=provenance,
    )


def wm_of(rec: dict) -> str:
    """Watermark value for one merged record: ``updated_at`` (detail-only) when present,
    else ``date_published`` (both endpoints have it). Both are ISO-8601-prefixed
    (``YYYY-MM-DD...``), so plain string comparison across the two stays watermark-safe —
    but note ``--no-detail`` runs only ever see the coarser date-only value, so switching
    modes between runs can leave the watermark a little behind (never ahead) until the
    next detail-fetching run catches up. The default (detail-fetching) mode is the
    documented incremental/production path; ``--no-detail`` is for a fast manual pass."""
    return merged_get(rec, "updated_at") or merged_get(rec, "date_published") or ""


def merged_get(rec: dict, key: str) -> str:
    return str(rec.get(key) or "")


# ── detail fetch (parallel) ─────────────────────────────────────────────────────
def fetch_details(records: list[dict], workers: int) -> dict[int, dict]:
    """``/api/exploitdb/{edb_id}`` for every record in ``records``, in parallel. A failed
    fetch for one id is logged and simply omitted — that record falls back to list-only
    fields (no cve_ids/language/updated_at) instead of dropping the whole page."""
    out: dict[int, dict] = {}
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futs = {pool.submit(api_detail, r["edb_id"]): r["edb_id"] for r in records}
        for fut in as_completed(futs):
            edb = futs[fut]
            try:
                out[edb] = fut.result()
            except Exception as exc:  # noqa: BLE001 — one bad id must not kill the run
                print(f"  detail fetch fail edb={edb}: {exc}", flush=True)
    return out


def fetch_candidates(watermark: str, page_size: int, max_pages: int, *,
                     fetch_detail: bool, workers: int) -> list[dict]:
    """Page ``/api/exploitdb`` newest-first (raw merged dicts, each carrying a ``_wm``
    watermark key). Stops once a page's OLDEST watermark value is <= ``watermark``
    (incremental steady state), or at ``max_pages`` / end of results."""
    out: list[dict] = []
    page = 1
    while True:
        if max_pages and page > max_pages:
            break
        try:
            data = api_page(page, page_size)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:  # past the last page
                break
            raise
        results = data.get("results") or []
        if not results:
            break

        if fetch_detail:
            details = fetch_details(results, workers)
            merged = [{**r, **details.get(r["edb_id"], {})} for r in results]
        else:
            merged = list(results)
        for r in merged:
            r["_wm"] = wm_of(r)

        if watermark:
            # L4: >= (not >) — same-second boundary rows must not be skipped; the
            # skip-existing / refresh-if-newer gate makes re-processing the boundary idempotent.
            out.extend(r for r in merged if r["_wm"] >= watermark)
            oldest = min((r["_wm"] for r in merged), default="")
            if oldest and oldest < watermark:
                break
        else:
            out.extend(merged)

        if not data.get("next"):
            break
        page += 1
    return out


# ── skip-existing (steady-state incremental reruns become near no-ops) ─────────
def load_existing(db_path: Path) -> dict[str, dict[str, Any]]:
    """``{source_ref: {"poc_id":int, "cve_count":int, "src_updated_at":str}}`` for every
    already-catalogued ``autoscan-exploitdb`` row. ``src_updated_at`` is read back from the
    stored provenance JSON so a rerun can REFRESH a row only when the source is newer. Reads
    via a throwaway READ-ONLY connection (not through ``PocCatalog``, whose ``__init__``
    unconditionally creates the DB/schema) so a ``--dry-run`` — or a first run against a
    catalog that doesn't exist yet — never creates or touches the file."""
    if not db_path.exists():
        return {}
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            "SELECT p.source_ref, p.id, p.provenance, COUNT(c.cve_id) FROM poc p "
            "LEFT JOIN poc_cve c ON c.poc_id = p.id "
            "WHERE p.source = ? GROUP BY p.id", (SOURCE,),
        ).fetchall()
        out: dict[str, dict[str, Any]] = {}
        for source_ref, poc_id, prov, cnt in rows:
            try:
                src_lu = str((json.loads(prov) if prov else {}).get("src_updated_at") or "")
            except (ValueError, TypeError):
                src_lu = ""
            out[source_ref] = {"poc_id": poc_id, "cve_count": cnt, "src_updated_at": src_lu}
        return out
    except sqlite3.OperationalError:
        return {}  # `poc` table not created yet (fresh/empty file) -> nothing to skip
    finally:
        conn.close()


def _poc_needs_write(prior: dict[str, Any] | None, src_lu: str, has_cve_ids: bool,
                     *, no_refresh: bool) -> bool:
    """Refresh-if-newer decision for one PoC row (pure, unit-tested). Write when:
      • the row is new, OR
      • it exists but is still missing CVE links this record can now supply (backfill), OR
      • it exists but carries no comparable stamp (legacy row → adopt current), OR
      • the source record is strictly newer than what is stored.
    An unchanged, fully-linked row is skipped. ``no_refresh`` forces pure insert-only."""
    if prior is None:
        return True
    if no_refresh:
        return False
    if int(prior.get("cve_count", 0)) == 0 and has_cve_ids:
        return True
    stored = prior.get("src_updated_at") or ""
    if not stored:
        return True
    return (src_lu or "") > stored


# ── state (watermark) ───────────────────────────────────────────────────────────
def load_state() -> dict:
    if STATE.exists():
        return json.loads(STATE.read_text())
    return {"watermark": "", "added": 0, "updated": 0, "skipped": 0, "errors": 0}


def save_state(state: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state, indent=2))


# ── main ─────────────────────────────────────────────────────────────────────────
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=DEFAULT_DB, help="PoC catalog sqlite path (default: gateway .env POC_CATALOG_DB)")
    ap.add_argument("--page-size", type=int, default=100, help="API page size")
    ap.add_argument("--max-pages", type=int, default=0,
                    help="stop after N API list pages (0=all); manual slice, does NOT advance the watermark")
    ap.add_argument("--workers", type=int, default=16, help="parallel detail-fetch threads")
    ap.add_argument("--full", action="store_true", help="ignore watermark; walk the whole API")
    ap.add_argument("--no-detail", action="store_true",
                    help="skip the /api/exploitdb/{id} detail fetch (faster; no cve_ids/language, coarser watermark)")
    ap.add_argument("--update-existing", action="store_true",
                    help="force re-upsert of EVERY row regardless of freshness (blanket)")
    ap.add_argument("--no-refresh", action="store_true",
                    help="pure insert-only: skip any row already in the catalog even if the "
                         "source is newer (disables the default refresh-if-newer behavior)")
    ap.add_argument("--dry-run", action="store_true", help="fetch+map+print only; touches no DB (no read, no write)")
    args = ap.parse_args()

    if not API_PASS:
        print("[autoscan->poc] FATAL: AUTOSCAN_PASS is not set. Export it (or add "
              "AUTOSCAN_PASS=... to the gateway .env). Refusing to run.", flush=True)
        return 2

    db_path = Path(args.db)
    state = load_state()
    watermark = "" if args.full else state.get("watermark", "")
    fetch_detail = not args.no_detail

    # L2: a missing/empty watermark must NOT silently become a full-API walk on the default
    # path. A full scan has to be explicit via --full.
    if not args.full and not watermark:
        print(f"[autoscan->poc] FATAL: no watermark — state file ({STATE}) missing or empty. "
              "Refusing a full-API scan on the default path. Pass --full to intentionally run a "
              "full backfill, or restore/seed the watermark file.", flush=True)
        return 2

    t0 = time.time()
    cands = fetch_candidates(watermark, args.page_size, args.max_pages,
                             fetch_detail=fetch_detail, workers=args.workers)
    print(f"[autoscan->poc] candidates since '{watermark or 'BEGIN'}' "
          f"(detail={'on' if fetch_detail else 'off'}): {len(cands)} ({time.time()-t0:.1f}s)", flush=True)
    if not cands:
        print("[autoscan->poc] nothing to do", flush=True)
        return 0

    # de-dup within this run (defensive against odd pagination overlap / repeated edb_id)
    seen: set[str] = set()
    dedup_cands: list[dict] = []
    for c in cands:
        edb = str(c.get("edb_id"))
        if edb in seen:
            continue
        seen.add(edb)
        dedup_cands.append(c)
    cands = dedup_cands
    max_wm = max((c.get("_wm") or "") for c in cands)

    pocs = [normalize_record(c) for c in cands]

    if args.dry_run:
        for p in pocs[:30]:
            print(f"  DRY edb={p.source_ref} title={p.title!r} cves={p.cve_ids} "
                  f"platform={p.platform} lang={p.language} verified={p.verified} pub={p.date_published}")
        print(f"[autoscan->poc] DRY mapped {len(pocs)} PoCs (watermark would -> {max_wm})", flush=True)
        return 0

    existing = load_existing(db_path)
    catalog = PocCatalog(db_path)

    added = updated = skipped = errors = 0
    for poc in pocs:
        prior = existing.get(poc.source_ref)
        # Default REFRESH-IF-NEWER: write a new row; refresh one the source has updated;
        # backfill one still missing CVE links; skip an unchanged, fully-linked row. Same
        # (source, source_ref) → PocCatalog.upsert overwrites in place, never duplicates.
        # --update-existing forces every row through; --no-refresh restores insert-only.
        src_lu = str((poc.provenance or {}).get("src_updated_at") or "")
        if not args.update_existing and not _poc_needs_write(
                prior, src_lu, bool(poc.cve_ids), no_refresh=args.no_refresh):
            skipped += 1
            continue
        try:
            catalog.upsert(poc)
        except Exception as exc:  # noqa: BLE001 — one bad row must not lose the whole run
            errors += 1
            print(f"  upsert FAIL {poc.source_ref}: {exc}", flush=True)
            continue
        if prior is None:
            added += 1
        else:
            updated += 1
    catalog.commit()
    started = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t0))
    catalog.log_run(SOURCE, started, added=added, updated=updated, errors=errors, note=f"skipped={skipped}")
    print(f"[autoscan->poc] added={added} updated={updated} skipped={skipped} errors={errors} "
          f"(catalog now {catalog.count()} rows)", flush=True)

    # L5: advance the watermark ONLY on a zero-error full-window run — a failed upsert must not
    # let the watermark jump past rows that were never written. --max-pages never advances.
    # Counters are per-run (not cumulative) for a readable nightly health signal.
    summary = {"added": added, "updated": updated, "skipped": skipped, "errors": errors}
    if args.max_pages:
        print(f"[autoscan->poc] --max-pages: watermark left at '{state.get('watermark','')}' "
              f"(manual slice) {json.dumps(summary)}", flush=True)
    elif errors:
        save_state({"watermark": state.get("watermark", ""), **summary})
        print(f"[autoscan->poc] {errors} error(s) — watermark NOT advanced "
              f"(left '{state.get('watermark','')}'); rerun to retry. {json.dumps(summary)}", flush=True)
    else:
        save_state({"watermark": max_wm, **summary})
        print(f"[autoscan->poc] DONE watermark->{max_wm} {json.dumps(summary)}", flush=True)
    return 1 if errors else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001
        import traceback
        print(f"[autoscan->poc] FATAL: {exc}", flush=True)
        traceback.print_exc()
        raise SystemExit(1)
