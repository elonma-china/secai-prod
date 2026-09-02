#!/usr/bin/env python3
"""Ingest CVEs from the Intelligent-AutoScan data API into the live KB (Qdrant + Neo4j).

Drop-in alternative to ``sync_nvd_to_kb.py``: same output (core/context chunks +
dense(bge-m3/sec-embedding) + sparse(bm25) vectors in Qdrant ``cve_kb_hybrid``, and
CVE/CWE/Product MERGE in Neo4j) — only the SOURCE differs. Instead of reading the
locally-synced ``cve_intel.db``, it pages the AutoScan REST API::

    GET http://<host>:8789/api/cve?page=N&page_size=M   (HTTP Basic auth)

which already merges NVD + MITRE + RedHat + vulnrichment (EPSS/SSVC/KEV) + CWE +
vendor/product + exploit/MISP cross-links per CVE — a richer upstream than NVD alone.

Only records MISSING from Qdrant are embedded (cheap steady state), so re-runs are
idempotent (uuid5 point ids). The API returns rows newest-first by ``updated_at``,
so incremental mode walks pages from page 1 and stops once it crosses the watermark.
By default this is pure insert-only: a CVE already in Qdrant is skipped outright and
its existing points are never re-upserted or mutated; pass ``--update-existing`` to
opt into re-embedding+upserting CVEs that already exist.

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
EMBED_URL = _ENV.get("EMBEDDING_API_URL", "http://117.18.102.42:37893").rstrip("/") + "/v1/embeddings"
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


def qdrant_count(cve_id: str) -> int:
    r = http_json(
        f"{QDRANT}/collections/{COLLECTION}/points/count",
        method="POST",
        body={"exact": True, "filter": {"must": [{"key": "cve_id", "match": {"value": cve_id}}]}},
    )
    return int(r["result"]["count"])


def qdrant_upsert(points: list[dict]) -> None:
    if not points:
        return
    http_json(f"{QDRANT}/collections/{COLLECTION}/points?wait=true",
              method="PUT", body={"points": points}, timeout=300)


# ── field mapping: AutoScan API record → normalized row ───────────────────────
_CVSS_PREF = ("cvssV4_0", "cvssV3_1", "cvssV3_0", "cvssV2_0")


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


def normalize(rec: dict) -> dict[str, Any]:
    """Map one /api/cve record to the flat shape build_chunks expects."""
    metrics = rec.get("metrics") or {}
    score, vector = _pick_cvss(metrics)

    # CWE: prefer parsed_weaknesses keys, fall back to the flat weaknesses list.
    pw = rec.get("parsed_weaknesses")
    if isinstance(pw, dict) and pw:
        cwes = [str(k).upper() for k in pw.keys() if str(k).upper().startswith("CWE-")]
    else:
        cwes = [str(w).upper() for w in (rec.get("weaknesses") or []) if str(w).upper().startswith("CWE-")]

    # products / vendors from parsed_vendors {vendor: [products...]}
    pv = rec.get("parsed_vendors") if isinstance(rec.get("parsed_vendors"), dict) else {}
    vendors = [str(v) for v in pv.keys()]
    products: list[str] = []
    for prods in pv.values():
        products.extend(str(p) for p in (prods or []))

    kev_data = ((metrics.get("kev") or {}).get("data")) or {}
    epss_data = ((metrics.get("epss") or {}).get("data")) or {}

    return {
        "cve_id": str(rec.get("cve_id") or "").upper(),
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


def build_chunks(row: dict) -> list[dict[str, Any]]:
    cve = row["cve_id"].upper()
    year = cve.split("-")[1] if "-" in cve else ""
    score = row.get("cvss_score")
    sev = severity_of(score)
    vector = (row.get("cvss_vector") or "").strip()
    title = (row.get("title") or "").strip()
    desc = (row.get("summary") or "").strip()
    cwes = row.get("cwes") or []
    products = row.get("products") or []
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
        "capec_entries": [], "mitre_tactics": [], "mitre_techniques": [],
        "epss_score": row.get("epss_score"), "has_public_exploit": bool(row.get("has_public_exploit")),
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
    """Page the API newest-first; keep rows with updated_at > watermark. Stop once a
    page's oldest row has crossed the watermark (incremental), or at max_pages / end."""
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
        rows = [normalize(r) for r in results]
        if watermark:
            fresh = [r for r in rows if r["updated_at"] > watermark]
            out.extend(fresh)
            # descending order → once the last (oldest) row on the page is <= watermark,
            # every later page is older too.
            if rows[-1]["updated_at"] <= watermark:
                break
        else:
            out.extend(rows)
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
    ap.add_argument("--update-existing", action="store_true",
                     help="also re-embed and upsert CVEs already present in Qdrant "
                          "(default OFF: pure insert-only — existing points are never "
                          "re-upserted or mutated)")
    args = ap.parse_args()

    if not API_PASS:
        print("[autoscan->kb] FATAL: AUTOSCAN_PASS is not set. Export it (or add "
              "AUTOSCAN_PASS=... to the gateway .env). Refusing to run.", flush=True)
        return 2

    state = load_state()
    watermark = "" if args.full else state.get("watermark", "")

    t0 = time.time()
    cands = fetch_candidates(watermark, args.page_size, args.max_pages)
    print(f"[autoscan->kb] candidates since '{watermark or 'BEGIN'}': {len(cands)} "
          f"({time.time()-t0:.1f}s)", flush=True)
    if not cands:
        print("[autoscan->kb] nothing to do", flush=True)
        return 0

    # de-dup within this run (same CVE can only appear once; guards odd pagination)
    seen: set[str] = set()
    cands = [c for c in cands if c["cve_id"] and not (c["cve_id"] in seen or seen.add(c["cve_id"]))]
    max_lu = max((c.get("updated_at") or "") for c in cands)

    if args.dry_run:
        for c in cands[:30]:
            print(f"  DRY {c['cve_id']} cvss={c.get('cvss_score')} kev={c.get('is_in_kev')} "
                  f"cwes={c.get('cwes')} prods={c.get('products')[:3]} lu={c.get('updated_at')}")
        print(f"[autoscan->kb] DRY mapped {len(cands)} CVEs (watermark would -> {max_lu})", flush=True)
        return 0

    # existence check (parallel) — only embed the ones actually missing from Qdrant.
    # --update-existing bypasses this gate entirely: every candidate is re-embedded
    # and re-upserted, existing or not. Default (off) is pure insert-only: an existing
    # CVE is skipped outright, its old points are never touched.
    if args.update_existing:
        missing: list[dict] = cands
        print(f"[autoscan->kb] --update-existing set: re-embedding all {len(missing)} "
              f"candidates (existence check skipped)", flush=True)
    else:
        missing = []
        with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
            futs = {pool.submit(qdrant_count, c["cve_id"]): c for c in cands}
            for fut in as_completed(futs):
                c = futs[fut]
                try:
                    if fut.result() == 0:
                        missing.append(c)
                    else:
                        state["skipped"] = int(state.get("skipped", 0)) + 1
                except Exception as exc:  # noqa: BLE001
                    print(f"  count fail {c['cve_id']}: {exc}", flush=True)
                    state["errors"] = int(state.get("errors", 0)) + 1
        print(f"[autoscan->kb] missing (to ingest): {len(missing)} / {len(cands)}", flush=True)

    for i in range(0, len(missing), args.batch):
        slice_rows = missing[i:i + args.batch]
        all_chunks: list[dict] = []
        for row in slice_rows:
            all_chunks.extend(build_chunks(row))
        texts = [c["text"] for c in all_chunks]
        try:
            vectors = embed_batch(texts)
            sparse = embed_sparse_batch(texts)
            points = [{
                "id": str(uuid.uuid5(NS, ch["chunk_id"])),
                "vector": {"dense": vec, "bm25": sv},
                "payload": ch,
            } for ch, vec, sv in zip(all_chunks, vectors, sparse, strict=True)]
            qdrant_upsert(points)
            if not args.no_neo4j:
                neo4j_merge(slice_rows)
            state["ingested"] = int(state.get("ingested", 0)) + len(slice_rows)
            print(f"  +{len(slice_rows)} CVEs (total ingested={state['ingested']})", flush=True)
        except Exception as exc:  # noqa: BLE001
            state["errors"] = int(state.get("errors", 0)) + len(slice_rows)
            print(f"  slice FAIL ({len(slice_rows)} CVEs): {exc}", flush=True)

    # Advance the watermark only on a full-window incremental scan (no page cap). A
    # bounded --max-pages run is a manual slice and must not move the watermark, or the
    # unscanned older rows would be skipped forever.
    if args.max_pages:
        print(f"[autoscan->kb] --max-pages set: watermark left at '{state.get('watermark','')}' "
              f"(manual slice) {json.dumps(state)}", flush=True)
    else:
        state["watermark"] = max_lu
        save_state(state)
        print(f"[autoscan->kb] DONE watermark->{max_lu} {json.dumps(state)}", flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001
        import traceback
        print(f"[autoscan->kb] FATAL: {exc}", flush=True)
        traceback.print_exc()
        raise SystemExit(1)
