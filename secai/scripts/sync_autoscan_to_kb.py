#!/usr/bin/env python3
"""Ingest CVEs from the Intelligent-AutoScan data API into the live KB (Qdrant + Neo4j).

Drop-in alternative to ``sync_nvd_to_kb.py``: same output (core/context chunks +
dense(bge-m3/sec-embedding) + sparse(bm25) vectors in Qdrant ``cve_kb_hybrid``, and
CVE/CWE/Product MERGE in Neo4j) — only the SOURCE differs. Instead of reading the
locally-synced ``cve_intel.db``, it pages the AutoScan REST API::

    GET http://<host>:8789/api/cve?page=N&page_size=M   (HTTP Basic auth)

which already merges NVD + MITRE + RedHat + vulnrichment (EPSS/SSVC/KEV) + CWE +
vendor/product + exploit/MISP cross-links per CVE — a richer upstream than NVD alone.

Default is REFRESH-IF-NEWER: a CVE not yet in Qdrant is embedded; one the source has
updated (its ``updated_at`` is newer than the ``updated_at`` stored in the point payload)
is re-embedded in place (same uuid5 id → overwrites, never duplicates); an unchanged one
is skipped, so steady-state re-runs stay cheap. The API returns rows newest-first by
``updated_at``, so incremental mode walks pages from page 1 and stops once it crosses the
watermark. ``--no-refresh`` restores pure insert-only (skip any existing CVE); a legacy
point with no stored ``updated_at`` is treated as stale and refreshed; ``--update-existing``
forces re-embedding of every candidate (one-time migration).

Config (embed endpoint/model/key, neo4j auth) is read from the gateway ``.env`` so it
tracks the current serving IP — identical to sync_nvd_to_kb.py. NOTE: this reaches the
public internet; run it on a build box with AIRGAP=false, never inside the air-gapped
serving deployment (which reads the local Qdrant/Neo4j snapshot).

Usage:
  python3 sync_autoscan_to_kb.py --dry-run --max-pages 2      # fetch+map, no writes
  python3 sync_autoscan_to_kb.py --max-pages 20               # a manual slice
  python3 sync_autoscan_to_kb.py                              # incremental to watermark
  python3 sync_autoscan_to_kb.py --full                       # walk the whole API
  python3 sync_autoscan_to_kb.py --update-existing --full     # re-embed everything
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

# ── endpoints / constants (mirror sync_nvd_to_kb.py) ──────────────────────────
API_BASE = os.environ.get("AUTOSCAN_API", "http://34.50.65.146:8789").rstrip("/")
API_USER = os.environ.get("AUTOSCAN_USER", "cyber")
# No password baked into source: it must come from AUTOSCAN_PASS or the gateway .env
# (resolved just below, after _ENV loads). main() fail-fasts if it is still empty.
API_PASS = os.environ.get("AUTOSCAN_PASS", "")
GATEWAY_ENV = "/home/ict/AIEngines/secai/secai-agent-gateway/.env"
QDRANT = "http://localhost:6333"
COLLECTION = "cve_kb_hybrid"
EXPECTED_DENSE_DIM = 1024  # collection's `dense` vector size; a mismatch = wrong embed model
NEO4J_HTTP = "http://localhost:7474/db/neo4j/tx/commit"
BM25_MODEL = "Qdrant/bm25"
LOCAL_BM25_MODEL_DIR = "/home/ict/AIEngines/secai/secai-data/_state/bm25_local_model"
SOURCE = "autoscan-api-sync"
STATE = Path("/home/ict/AIEngines/secai/secai-data/_state/autoscan_to_kb.json")
NS = uuid.NAMESPACE_DNS
MAX_EMBED_CHARS = 1000


# ── config from gateway .env ──────────────────────────────────────────────────
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
EMBED_URL = _ENV.get("EMBEDDING_API_URL", "http://23.127.144.217:18906").rstrip("/") + "/v1/embeddings"
EMBED_KEY = _ENV.get("EMBEDDING_API_KEY", "")
EMBED_MODEL = _ENV.get("EMBEDDING_MODEL", "BAAI/bge-m3")
NEO4J_AUTH = (_ENV.get("NEO4J_USER", "neo4j"), _ENV.get("NEO4J_PASSWORD", "secai2026"))

_sparse_model = None


def sparse_model():
    global _sparse_model
    if _sparse_model is None:
        from fastembed import SparseTextEmbedding

        kwargs = {"specific_model_path": LOCAL_BM25_MODEL_DIR} if os.path.isdir(LOCAL_BM25_MODEL_DIR) else {}
        _sparse_model = SparseTextEmbedding(model_name=BM25_MODEL, **kwargs)
    return _sparse_model


def embed_sparse_batch(texts: list[str]) -> list[dict[str, list]]:
    if not texts:
        return []
    return [
        {"indices": [int(i) for i in emb.indices], "values": [float(v) for v in emb.values]}
        for emb in sparse_model().embed(texts)
    ]


# ── http helpers ──────────────────────────────────────────────────────────────
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
    return http_json(f"{API_BASE}/api/cve?{q}",
                     headers={"Authorization": _basic(API_USER, API_PASS)}, timeout=timeout)


def api_detail(cve_id: str, timeout: float = 30) -> dict[str, Any]:
    """GET /api/cve/{cve_id} — the DETAIL record. Only the detail carries `parsed_vendors`,
    `parsed_weaknesses`, `has_public_exploit` and `title`; the list endpoint omits them, so
    ingesting from the list alone yields empty products + exploit-flag=false (bug L1)."""
    return http_json(f"{API_BASE}/api/cve/{urllib.parse.quote(cve_id)}",
                     headers={"Authorization": _basic(API_USER, API_PASS)}, timeout=timeout)


def fetch_details(cve_ids: list[str], workers: int) -> dict[str, dict]:
    """Fetch /api/cve/{id} for each id in parallel. A failed fetch is logged and omitted —
    that CVE falls back to list-only fields (products parsed from the flat `vendors` field)
    instead of dropping the whole batch."""
    out: dict[str, dict] = {}
    if not cve_ids:
        return out
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futs = {pool.submit(api_detail, c): c for c in cve_ids}
        for fut in as_completed(futs):
            cid = futs[fut]
            try:
                out[cid] = fut.result()
            except Exception as exc:  # noqa: BLE001 — one bad id must not kill the run
                print(f"  detail fetch fail {cid}: {exc}", flush=True)
    return out


def qdrant_count(cve_id: str) -> int:
    r = http_json(
        f"{QDRANT}/collections/{COLLECTION}/points/count",
        method="POST",
        body={"exact": True, "filter": {"must": [{"key": "cve_id", "match": {"value": cve_id}}]}},
    )
    return int(r["result"]["count"])


_PRESERVE_KEYS = ("products", "capec_entries", "mitre_techniques", "mitre_tactics")


def qdrant_existing(cve_id: str) -> tuple[bool, str, dict[str, Any]]:
    """``(exists, stored_updated_at, preserve)`` for a CVE already in the collection.
    ``preserve`` carries the existing point's ``products``/``capec_entries``/``mitre_*`` so a
    refresh can KEEP them when the incoming record lacks them (L3 — otherwise refreshing a
    richer legacy point blanks fields AutoScan never fills, e.g. CAPEC/ATT&CK). ``stored_lu``
    is ``""`` when absent OR present but written by an ingest that never stamped ``updated_at``
    (legacy ``nvd-cron-sync``/``hf``/``opencve`` points)."""
    r = http_json(
        f"{QDRANT}/collections/{COLLECTION}/points/scroll",
        method="POST",
        body={"filter": {"must": [{"key": "cve_id", "match": {"value": cve_id}}]},
              "limit": 1, "with_payload": ["updated_at", *_PRESERVE_KEYS], "with_vector": False},
    )
    pts = r.get("result", {}).get("points", [])
    if not pts:
        return (False, "", {})
    pl = pts[0].get("payload") or {}
    preserve = {k: pl.get(k) for k in _PRESERVE_KEYS if pl.get(k)}
    return (True, str(pl.get("updated_at") or ""), preserve)


def _needs_ingest(exists: bool, stored_lu: str, src_lu: str) -> bool:
    """Refresh-if-newer decision (pure, unit-tested). Ingest/refresh when:
      • the CVE is not in Qdrant yet (new), OR
      • it is present but carries no comparable stamp (legacy/other-source point → adopt as
        the current record), OR
      • the source record's ``updated_at`` is strictly newer than what is stored.
    An unchanged (stored >= source) CVE is skipped, so re-runs stay cheap and never churn."""
    if not exists:
        return True
    if not stored_lu:
        return True
    return (src_lu or "") > stored_lu


def qdrant_upsert(points: list[dict]) -> None:
    if not points:
        return
    http_json(f"{QDRANT}/collections/{COLLECTION}/points?wait=true",
              method="PUT", body={"points": points}, timeout=300)


# ── field mapping: AutoScan API record → normalized row ───────────────────────
_CVSS_PREF = ("cvssV4_0", "cvssV3_1", "cvssV3_0", "cvssV2_0")

# CVSS overlay bền: bản hiệu đính CVSS đối chiếu NVD (nvd@nist.gov) — sinh bởi
# scripts/bulk_reconcile_cvss.py. AutoScan (như OpenCVE) chỉ lưu 1 score/version, đôi khi
# là điểm CNA/secondary sai (vd Zerologon 5.5 thay 10.0). Overlay ép CVSS đúng ở MỌI lần
# sync → bản vá không mất khi cập nhật lại dữ liệu. Severity suy ra từ score (severity_of).
import json as _json
import os as _os
_CVSS_OVERLAY: dict[str, float] = {}
try:
    _op = _os.environ.get("CVSS_OVERLAY_PATH",
                          "/home/ict/AIEngines/secai/secai-data/_state/cvss_overrides_nvd.json")
    if _os.path.isfile(_op):
        for _c, _v in _json.load(open(_op)).items():
            if isinstance(_v, dict) and _v.get("cvss_score") is not None:
                _CVSS_OVERLAY[_c.upper()] = float(_v["cvss_score"])
        print(f"[overlay] nạp {len(_CVSS_OVERLAY)} CVSS override từ {_op}", flush=True)
except Exception as _e:  # noqa: BLE001 — overlay tuỳ chọn
    print(f"[overlay] bỏ qua ({_e})", flush=True)


def _pick_cvss(metrics: dict) -> tuple[float | None, str]:
    """Best available CVSS score+vector, preferring the newest scheme present."""
    metrics = metrics or {}
    for key in _CVSS_PREF:
        data = ((metrics.get(key) or {}).get("data")) or {}
        score = data.get("score")
        if score is not None:
            try:
                return float(score), str(data.get("vector") or "")
            except (TypeError, ValueError):
                return None, str(data.get("vector") or "")
    return None, ""


def _cid(rec: dict) -> str:
    """Canonical CVE id: strip + upper (D2 — a stray-whitespace id would otherwise yield a
    different chunk_id/uuid5 and leave an orphan duplicate)."""
    return str(rec.get("cve_id") or "").strip().upper()


def _vendors_products(rec: dict) -> tuple[list[str], list[str]]:
    """(vendors, products) from a CVE record. Prefers the DETAIL `parsed_vendors`
    ({vendor: [products]}); falls back to the LIST `vendors` field, whose entries are
    ``"vendor$PRODUCT$product"`` (product half already snake_case = the reader's convention)
    or a bare vendor. Fixes L1: the list endpoint has no `parsed_vendors`, so without this
    every list-ingested CVE got empty products."""
    pv = rec.get("parsed_vendors")
    if isinstance(pv, dict) and pv:
        vendors = [str(v) for v in pv.keys()]
        products: list[str] = []
        for prods in pv.values():
            products.extend(str(p) for p in (prods or []))
        return vendors, products
    # flat `vendors` fallback
    vendors_set: dict[str, None] = {}
    products_out: list[str] = []
    for entry in (rec.get("vendors") or []):
        s = str(entry)
        vend, sep, prod = s.partition("$PRODUCT$")
        if vend:
            vendors_set.setdefault(vend, None)
        if sep and prod:
            products_out.append(prod)
    return list(vendors_set.keys()), products_out


def normalize(rec: dict) -> dict[str, Any]:
    """Map one /api/cve record (LIST row optionally merged with its DETAIL) to the flat shape
    build_chunks expects. Detail fields (parsed_vendors/parsed_weaknesses/has_public_exploit/
    title) win over list fields when both are present."""
    metrics = rec.get("metrics") or {}
    score, vector = _pick_cvss(metrics)
    _ov = _CVSS_OVERLAY.get(_cid(rec))  # ép CVSS đúng theo overlay NVD (durability)
    if _ov is not None:
        score = _ov

    # CWE: prefer parsed_weaknesses keys, fall back to the flat weaknesses list.
    pw = rec.get("parsed_weaknesses")
    if isinstance(pw, dict) and pw:
        cwes = [str(k).upper() for k in pw.keys() if str(k).upper().startswith("CWE-")]
    else:
        cwes = [str(w).upper() for w in (rec.get("weaknesses") or []) if str(w).upper().startswith("CWE-")]

    vendors, products = _vendors_products(rec)

    kev_data = ((metrics.get("kev") or {}).get("data")) or {}
    epss_data = ((metrics.get("epss") or {}).get("data")) or {}

    return {
        "cve_id": _cid(rec),
        "title": rec.get("title") or "",
        "summary": rec.get("description") or rec.get("title") or "",
        "cvss_score": score,
        "cvss_vector": vector,
        "cwes": cwes,
        "vendors": vendors,
        "products": products,
        "is_in_kev": bool(kev_data),
        "epss_score": epss_data.get("score"),
        "has_public_exploit": bool(rec.get("has_public_exploit")),
        "published_date": rec.get("created_at") or "",
        "updated_at": rec.get("updated_at") or "",
    }


# ── chunk building (same payload schema as sync_nvd_to_kb.py) ──────────────────
def severity_of(score: float | None) -> str:
    if score is None:
        return "UNKNOWN"
    if score >= 9.0:
        return "CRITICAL"
    if score >= 7.0:
        return "HIGH"
    if score >= 4.0:
        return "MEDIUM"
    if score > 0:
        return "LOW"
    return "UNKNOWN"


def build_chunks(row: dict, preserve: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """``preserve`` (from the existing point, via qdrant_existing) lets a REFRESH keep fields
    the incoming record does not supply, so we never blank a richer legacy point (L3)."""
    preserve = preserve or {}
    cve = row["cve_id"].upper()
    year = cve.split("-")[1] if "-" in cve else ""
    score = row.get("cvss_score")
    sev = severity_of(score)
    vector = (row.get("cvss_vector") or "").strip()
    title = (row.get("title") or "").strip()
    desc = (row.get("summary") or "").strip()
    cwes = row.get("cwes") or []
    # L3: keep the existing point's fields when the incoming record has none (AutoScan never
    # supplies CAPEC/ATT&CK, and a list-only row may lack products).
    products = (row.get("products") or []) or list(preserve.get("products") or [])
    capec = list(preserve.get("capec_entries") or [])
    mitre_tac = list(preserve.get("mitre_tactics") or [])
    mitre_tech = list(preserve.get("mitre_techniques") or [])
    pub = row.get("published_date") or ""

    core_parts = [
        f"CVE ID: {cve}",
        f"Severity: {sev}",
        f"CVSS: {score}" if score is not None else "",
        f"Vector: {vector}" if vector else "",
        f"CWE Weaknesses: {', '.join(cwes)}" if cwes else "",
        f"Description: {desc[:700]}" if desc else "",
    ]
    core_text = " | ".join(p for p in core_parts if p)

    ctx_parts = [
        f"CVE ID: {cve}",
        f"Severity: {sev}",
        f"Weaknesses: {', '.join(cwes)}" if cwes else "",
        f"Affected: {', '.join(products[:8])}" if products else "",
        f"Published: {pub}" if pub else "",
        f"Summary: {desc[:500]}" if desc else "",
    ]
    ctx_text = " | ".join(p for p in ctx_parts if p)

    base = {
        "cve_id": cve, "title": title, "year": year, "cvss_score": score, "severity": sev,
        "cwe_refs": cwes, "products": products, "is_in_kev": bool(row.get("is_in_kev")),
        "capec_entries": capec, "mitre_tactics": mitre_tac, "mitre_techniques": mitre_tech,
        "epss_score": row.get("epss_score"), "has_public_exploit": bool(row.get("has_public_exploit")),
        # Stored so a later run can compare source freshness and REFRESH only when the
        # source record is genuinely newer (see _needs_ingest / qdrant_existing).
        "updated_at": row.get("updated_at") or "",
        "source": SOURCE,
    }
    return [
        {**base, "chunk_kind": "core", "kind": "core", "chunk_id": f"{cve}::core", "text": core_text},
        {**base, "chunk_kind": "context", "kind": "context", "chunk_id": f"{cve}::context", "text": ctx_text},
    ]


def _truncate(text: str) -> str:
    text = (text or "").strip()
    if len(text) <= MAX_EMBED_CHARS:
        return text
    return text[: MAX_EMBED_CHARS - 1].rsplit(" ", 1)[0] + "…"


def embed_batch(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []
    clipped = [_truncate(t) for t in texts]

    def _call(payload: list[str]) -> list[list[float]]:
        last_err: Exception | None = None
        for limit in (MAX_EMBED_CHARS, 800, 600, 400):
            try:
                body = [t[:limit] for t in payload]
                r = http_json(EMBED_URL, method="POST",
                              body={"model": EMBED_MODEL, "input": body},
                              headers={"Authorization": f"Bearer {EMBED_KEY}"}, timeout=120)
                data = sorted(r["data"], key=lambda x: x["index"])
                return [row["embedding"] for row in data]
            except urllib.error.HTTPError as exc:
                last_err = exc
                if exc.code != 400:
                    raise
            except (TimeoutError, urllib.error.URLError) as exc:
                last_err = exc
                break
        assert last_err is not None
        raise last_err

    try:
        return _call(clipped)
    except (TimeoutError, urllib.error.URLError, urllib.error.HTTPError):
        if len(clipped) == 1:
            raise
        mid = max(1, len(clipped) // 2)
        return embed_batch(clipped[:mid]) + embed_batch(clipped[mid:])


def neo4j_merge(rows: list[dict]) -> None:
    """Batch MERGE CVE + HAS_WEAKNESS->CWE + AFFECTS->Product for a slice."""
    batch = []
    for row in rows:
        cve = row["cve_id"].upper()
        score = row.get("cvss_score")
        batch.append({
            "id": cve, "year": cve.split("-")[1] if "-" in cve else "",
            "severity": severity_of(score), "cvss": score,
            "kev": bool(row.get("is_in_kev")),
            "cwes": row.get("cwes") or [],
            "products": (row.get("products") or [])[:25],
        })
    stmt = """
    UNWIND $rows AS r
      MERGE (c:CVE {id: r.id})
      SET c.year=r.year, c.severity=r.severity, c.cvss_score=r.cvss, c.is_in_kev=r.kev
      WITH c, r
      CALL {
        WITH c, r
        UNWIND r.cwes AS wid
          MERGE (w:CWE {id: wid})
          MERGE (c)-[:HAS_WEAKNESS]->(w)
      }
      CALL {
        WITH c, r
        UNWIND r.products AS pname
          MERGE (p:Product {name: pname})
          MERGE (c)-[:AFFECTS]->(p)
      }
    """
    try:
        http_json(NEO4J_HTTP, method="POST",
                  body={"statements": [{"statement": stmt, "parameters": {"rows": batch}}]},
                  headers={"Authorization": _basic(*NEO4J_AUTH)}, timeout=60)
    except Exception as exc:  # noqa: BLE001 — best-effort graph sync
        print(f"  neo4j warn ({len(batch)} cves): {exc}", flush=True)


# ── main ──────────────────────────────────────────────────────────────────────
def load_state() -> dict:
    if STATE.exists():
        return json.loads(STATE.read_text())
    return {"watermark": "", "ingested": 0, "skipped": 0, "errors": 0}


def save_state(state: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state, indent=2))


def fetch_candidates(watermark: str, page_size: int, max_pages: int) -> list[dict]:
    """Page the API newest-first; return RAW list records with ``updated_at >= watermark``.
    (L4: ``>=``, not ``>`` — many rows share the exact boundary second, and the reference NVD
    script deliberately uses ``>=`` so same-second late rows are not skipped; the existence
    + refresh-if-newer gate makes re-scanning the boundary batch idempotent.) Detail merge +
    normalize happen later, per CVE actually ingested. Stops once a page's OLDEST row is
    strictly older than the watermark, at max_pages, or at the last page."""
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
        if watermark:
            out.extend(r for r in results if str(r.get("updated_at") or "") >= watermark)
            if str(results[-1].get("updated_at") or "") < watermark:
                break
        else:
            out.extend(results)
        if not data.get("next"):
            break
        page += 1
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--page-size", type=int, default=100, help="API page size")
    ap.add_argument("--max-pages", type=int, default=0, help="stop after N API pages (0=all)")
    ap.add_argument("--batch", type=int, default=64, help="embed/upsert batch size")
    ap.add_argument("--workers", type=int, default=16, help="parallel qdrant existence checks")
    ap.add_argument("--full", action="store_true", help="ignore watermark; walk the whole API")
    ap.add_argument("--dry-run", action="store_true", help="fetch+map+print only; no Qdrant/embed/Neo4j")
    ap.add_argument("--no-neo4j", action="store_true")
    ap.add_argument("--no-detail", action="store_true",
                     help="skip the /api/cve/{id} detail fetch (faster; products parsed from the "
                          "list `vendors` field, no has_public_exploit/title)")
    ap.add_argument("--update-existing", action="store_true",
                     help="force re-embed+upsert of EVERY candidate regardless of freshness "
                          "(blanket; one-time migration of legacy points)")
    ap.add_argument("--no-refresh", action="store_true",
                     help="pure insert-only: skip any CVE already in Qdrant even if the source "
                          "is newer (disables the default refresh-if-newer behavior)")
    args = ap.parse_args()

    if not API_PASS:
        print("[autoscan->kb] FATAL: AUTOSCAN_PASS is not set. Export it (or add "
              "AUTOSCAN_PASS=... to the gateway .env). Refusing to run.", flush=True)
        return 2

    state = load_state()
    watermark = "" if args.full else state.get("watermark", "")

    # L2: a missing/empty watermark must NOT silently become a full-API destructive walk on
    # the default path (that would re-embed/refresh hundreds of thousands of records). A full
    # scan has to be explicit via --full.
    if not args.full and not watermark:
        print(f"[autoscan->kb] FATAL: no watermark — state file ({STATE}) missing or empty. "
              "Refusing a full-API scan on the default path. Pass --full to intentionally run a "
              "full backfill, or restore/seed the watermark file.", flush=True)
        return 2

    t0 = time.time()
    cands = fetch_candidates(watermark, args.page_size, args.max_pages)
    print(f"[autoscan->kb] candidates since '{watermark or 'FULL'}': {len(cands)} "
          f"({time.time()-t0:.1f}s)", flush=True)
    if not cands:
        print("[autoscan->kb] nothing to do", flush=True)
        return 0

    # de-dup within this run by canonical cve_id (newest-first order → first wins)
    seen: set[str] = set()
    deduped: list[dict] = []
    for c in cands:
        cid = _cid(c)
        if not cid or cid in seen:
            continue
        seen.add(cid)
        deduped.append(c)
    cands = deduped
    max_lu = max((str(c.get("updated_at") or "") for c in cands), default="")

    if args.dry_run:
        for c in cands[:30]:
            n = normalize(c)  # list-only (dry-run does not fetch detail)
            print(f"  DRY {n['cve_id']} cvss={n['cvss_score']} kev={n['is_in_kev']} "
                  f"cwes={n['cwes']} prods(list)={n['products'][:3]} lu={n['updated_at']}")
        print(f"[autoscan->kb] DRY mapped {len(cands)} CVEs (detail not fetched) "
              f"(watermark would -> {max_lu})", flush=True)
        return 0

    run_ingested = run_skipped = run_errors = 0
    preserve_map: dict[str, dict] = {}

    # Selection (parallel). Default = REFRESH-IF-NEWER; also captures each existing point's
    # preservable fields (products/capec/mitre) so a refresh never blanks them (L3).
    # --update-existing = blanket force; --no-refresh = pure insert-only.
    def _lookup(c: dict) -> tuple[bool, str]:
        cid = _cid(c)
        exists, stored_lu, extra = qdrant_existing(cid)
        if extra:
            preserve_map[cid] = extra
        return exists, stored_lu

    if args.update_existing:
        missing: list[dict] = cands
        with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
            list(pool.map(_lookup, cands))  # populate preserve_map for a clean refresh
        print(f"[autoscan->kb] --update-existing: re-embedding all {len(missing)} candidates", flush=True)
    else:
        missing = []

        def _select(c: dict) -> dict | None:
            exists, stored_lu = _lookup(c)
            if args.no_refresh:
                return c if not exists else None
            return c if _needs_ingest(exists, stored_lu, str(c.get("updated_at") or "")) else None

        with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
            futs = {pool.submit(_select, c): c for c in cands}
            for fut in as_completed(futs):
                c = futs[fut]
                try:
                    res = fut.result()
                    if res is not None:
                        missing.append(res)
                    else:
                        run_skipped += 1
                except Exception as exc:  # noqa: BLE001
                    print(f"  lookup fail {_cid(c)}: {exc}", flush=True)
                    run_errors += 1
        mode = "insert-only" if args.no_refresh else "refresh-if-newer"
        print(f"[autoscan->kb] to ingest ({mode}): {len(missing)} / {len(cands)}", flush=True)

    # L1: fetch the DETAIL record for each CVE we will ingest (small delta), merge it over the
    # list row, then normalize — so products/has_public_exploit/title come from the detail
    # endpoint (the list endpoint omits parsed_vendors/has_public_exploit/title).
    detail_map = {} if args.no_detail else fetch_details([_cid(c) for c in missing], args.workers)
    norm_rows = [normalize({**c, **(detail_map.get(_cid(c)) or {})}) for c in missing]

    for i in range(0, len(norm_rows), args.batch):
        slice_rows = norm_rows[i:i + args.batch]
        all_chunks: list[dict] = []
        for row in slice_rows:
            all_chunks.extend(build_chunks(row, preserve_map.get(row["cve_id"])))
        texts = [c["text"] for c in all_chunks]
        try:
            vectors = embed_batch(texts)
            # D2: a 200 from the wrong embed model returns the wrong dim; fail loud instead of
            # letting Qdrant reject the upsert (which L5 would then log as a lost slice).
            if vectors and len(vectors[0]) != EXPECTED_DENSE_DIM:
                raise ValueError(f"embed dim {len(vectors[0])} != {EXPECTED_DENSE_DIM} (wrong model?)")
            sparse = embed_sparse_batch(texts)
            points = [{
                "id": str(uuid.uuid5(NS, ch["chunk_id"])),
                "vector": {"dense": vec, "bm25": sv},
                "payload": ch,
            } for ch, vec, sv in zip(all_chunks, vectors, sparse, strict=True)]
            qdrant_upsert(points)
            if not args.no_neo4j:
                neo4j_merge(slice_rows)
            run_ingested += len(slice_rows)
            print(f"  +{len(slice_rows)} CVEs (ingested={run_ingested})", flush=True)
        except Exception as exc:  # noqa: BLE001
            run_errors += len(slice_rows)
            print(f"  slice FAIL ({len(slice_rows)} CVEs): {exc}", flush=True)

    # L5: advance the watermark ONLY when the run had zero errors — a failed slice (or a
    # failed lookup) must not let the watermark jump past CVEs that were never ingested, or
    # they are lost until a manual --full. --max-pages is a manual slice and never advances.
    # Counters are per-run (not cumulative) for a readable nightly health signal.
    summary = {"ingested": run_ingested, "skipped": run_skipped, "errors": run_errors}
    if args.max_pages:
        print(f"[autoscan->kb] --max-pages: watermark left at '{state.get('watermark','')}' "
              f"(manual slice) {json.dumps(summary)}", flush=True)
    elif run_errors:
        save_state({"watermark": state.get("watermark", ""), **summary})
        print(f"[autoscan->kb] {run_errors} error(s) — watermark NOT advanced "
              f"(left '{state.get('watermark','')}'); rerun to retry. {json.dumps(summary)}", flush=True)
    else:
        save_state({"watermark": max_lu, **summary})
        print(f"[autoscan->kb] DONE watermark->{max_lu} {json.dumps(summary)}", flush=True)
    return 1 if run_errors else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001
        import traceback
        print(f"[autoscan->kb] FATAL: {exc}", flush=True)
        traceback.print_exc()
        raise SystemExit(1)
