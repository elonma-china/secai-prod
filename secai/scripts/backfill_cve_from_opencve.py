#!/usr/bin/env python3
"""Backfill missing CVEs from live OpenCVE API into Qdrant cve_kb (+ optional Neo4j).

Idempotent: point IDs are uuid5(NAMESPACE_DNS, "{cve_id}::{chunk_kind}").
Resume via --state JSON watermark (last page + stats).

Usage:
  python3 backfill_cve_from_opencve.py --dry-run --max-pages 5
  python3 backfill_cve_from_opencve.py --max-missing 500
  python3 backfill_cve_from_opencve.py   # full gap until consecutive full pages
"""
from __future__ import annotations

import argparse
import base64
import json
import sys
import time
import uuid
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

_GW_SRC = Path("/home/ict/AIEngines/secai/secai-agent-gateway/src")
if str(_GW_SRC) not in sys.path:
    sys.path.insert(0, str(_GW_SRC))
try:
    from secai_gateway.retrieval.cve_overrides import apply_cve_override
except ImportError:  # pragma: no cover — script still ingests without the overlay
    def apply_cve_override(payload: dict[str, Any] | None) -> dict[str, Any]:
        return payload or {}

OPENCVE = "http://103.216.117.216:8899"
OPENCVE_AUTH = ("opencve", "opencve")
QDRANT = "http://localhost:6333"
COLLECTION = "cve_kb"
EMBED_URL = "http://23.127.144.217:18906/v1/embeddings"
EMBED_KEY = "sk-secai2026"
EMBED_MODEL = "BAAI/bge-m3"
NEO4J_HTTP = "http://localhost:7474/db/neo4j/tx/commit"
NEO4J_AUTH = ("neo4j", "secai2026")
SOURCE = "opencve-backfill-2026-08"
NS = uuid.NAMESPACE_DNS

DEFAULT_STATE = Path("/home/ict/AIEngines/secai/secai-data/_state/opencve_backfill.json")


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


def opencve_get(path: str, **params: Any) -> Any:
    q = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
    url = f"{OPENCVE}{path}" + (f"?{q}" if q else "")
    return http_json(url, headers={"Authorization": _basic(*OPENCVE_AUTH)})


def qdrant_count(cve_id: str) -> int:
    r = http_json(
        f"{QDRANT}/collections/{COLLECTION}/points/count",
        method="POST",
        body={"exact": True, "filter": {"must": [{"key": "cve_id", "match": {"value": cve_id}}]}},
    )
    return int(r["result"]["count"])


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


def extract_cvss(metrics: dict | None) -> tuple[float | None, str | None]:
    metrics = metrics or {}
    for key in ("cvssV4_0", "cvssV3_1", "cvssV3_0", "cvssV2_0"):
        data = (metrics.get(key) or {}).get("data") or {}
        if isinstance(data, dict) and data.get("score") is not None:
            try:
                return float(data["score"]), data.get("vector")
            except (TypeError, ValueError):
                pass
    return None, None


def is_kev(metrics: dict | None) -> bool:
    data = ((metrics or {}).get("kev") or {}).get("data") or {}
    return bool(data)


def products_from(detail: dict) -> list[str]:
    out: list[str] = []
    parsed = detail.get("parsed_vendors") or {}
    if isinstance(parsed, dict):
        for _vendor, products in parsed.items():
            if isinstance(products, list):
                out.extend(str(p) for p in products)
            elif products:
                out.append(str(products))
    if not out:
        for v in detail.get("vendors") or []:
            s = str(v)
            if "$PRODUCT$" in s:
                out.append(s.split("$PRODUCT$", 1)[1])
    # dedupe preserve order
    seen: set[str] = set()
    uniq = []
    for p in out:
        p = p.strip().lower().replace(" ", "_")
        if p and p not in seen:
            seen.add(p)
            uniq.append(p)
    # .NET/ASP.NET CPEs include a Linux *platform*; OpenCVE maps that to linux_kernel.
    if any(p in {".net", "asp.net", "aspnet"} or p.startswith(".net") for p in uniq):
        uniq = [p for p in uniq if p != "linux_kernel"]
    return uniq[:20]


def build_chunks(detail: dict) -> list[dict[str, Any]]:
    cve = detail["cve_id"].upper()
    year = cve.split("-")[1] if "-" in cve else ""
    score, vector = extract_cvss(detail.get("metrics"))
    sev = severity_of(score)
    cwes = [str(w).upper() for w in (detail.get("weaknesses") or []) if w]
    products = products_from(detail)
    title = (detail.get("title") or "").strip()
    desc = (detail.get("description") or "").strip()
    epss = ((detail.get("metrics") or {}).get("epss") or {}).get("data") or {}
    epss_s = epss.get("score")
    kev = is_kev(detail.get("metrics"))

    core_parts = [
        f"CVE ID: {cve}",
        f"Title: {title}" if title else "",
        f"Severity: {sev}",
        f"CVSS: {score}" if score is not None else "",
        f"Vector: {vector}" if vector else "",
        f"CWE Weaknesses: {', '.join(cwes)}" if cwes else "",
        f"Affected Products: {', '.join(products)}" if products else "",
        f"Description: {desc[:1800]}" if desc else "",
    ]
    core_text = " | ".join(p for p in core_parts if p)

    ctx_parts = [
        f"CVE ID: {cve}",
        f"Severity: {sev}",
        f"EPSS: {epss_s}" if epss_s is not None else "",
        f"In KEV: {kev}",
        f"Vendors/Products: {json.dumps(detail.get('parsed_vendors') or detail.get('vendors') or [])}",
        f"Weaknesses: {', '.join(cwes)}" if cwes else "",
        f"Created: {detail.get('created_at')}",
        f"Updated: {detail.get('updated_at')}",
        f"Summary: {(desc or title)[:900]}",
    ]
    ctx_text = " | ".join(p for p in ctx_parts if p)

    base = {
        "cve_id": cve,
        "year": year,
        "cvss_score": score,
        "severity": sev,
        "cwe_refs": cwes,
        "products": products,
        "is_in_kev": kev,
        "capec_entries": [],
        "mitre_tactics": [],
        "mitre_techniques": [],
        "source": SOURCE,
    }
    return [
        {**base, "chunk_kind": "core", "kind": "core", "chunk_id": f"{cve}::core", "text": core_text},
        {**base, "chunk_kind": "context", "kind": "context", "chunk_id": f"{cve}::context", "text": ctx_text},
    ]


def embed_batch(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []
    r = http_json(
        EMBED_URL,
        method="POST",
        body={"model": EMBED_MODEL, "input": texts},
        headers={"Authorization": f"Bearer {EMBED_KEY}"},
        timeout=180,
    )
    data = sorted(r["data"], key=lambda x: x["index"])
    return [row["embedding"] for row in data]


def qdrant_upsert(points: list[dict]) -> None:
    if not points:
        return
    http_json(
        f"{QDRANT}/collections/{COLLECTION}/points?wait=true",
        method="PUT",
        body={"points": points},
        timeout=120,
    )


def neo4j_merge(detail: dict) -> None:
    cve = detail["cve_id"].upper()
    score, _ = extract_cvss(detail.get("metrics"))
    sev = severity_of(score)
    year = cve.split("-")[1] if "-" in cve else ""
    cwes = [str(w).upper() for w in (detail.get("weaknesses") or []) if w]
    products = products_from(detail)
    stmt = """
    MERGE (c:CVE {id:$id})
    SET c.year=$year, c.severity=$severity, c.cvss_score=$cvss, c.is_in_kev=$kev
    WITH c
    UNWIND $cwes AS wid
      MERGE (w:CWE {id: wid})
      MERGE (c)-[:HAS_WEAKNESS]->(w)
    WITH c
    UNWIND $products AS pname
      MERGE (p:Product {name: pname})
      MERGE (c)-[:AFFECTS]->(p)
    RETURN c.id AS id
    """
    try:
        http_json(
            NEO4J_HTTP,
            method="POST",
            body={"statements": [{"statement": stmt, "parameters": {
                "id": cve, "year": year, "severity": sev,
                "cvss": score, "kev": is_kev(detail.get("metrics")),
                "cwes": cwes, "products": products,
            }}]},
            headers={"Authorization": _basic(*NEO4J_AUTH)},
            timeout=30,
        )
    except Exception as exc:  # noqa: BLE001 — best-effort graph sync
        print(f"  neo4j warn {cve}: {exc}")


def load_state(path: Path) -> dict:
    if path.exists():
        return json.loads(path.read_text())
    return {"page": 1, "ingested": 0, "skipped": 0, "errors": 0, "checked": 0}


def save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2))


def ingest_details(details: list[dict], *, dry_run: bool, do_neo4j: bool) -> int:
    if not details:
        return 0
    all_chunks: list[dict] = []
    owners: list[dict] = []
    for d in details:
        chunks = [apply_cve_override(c) for c in build_chunks(d)]
        all_chunks.extend(chunks)
        owners.append(d)
    if dry_run:
        for d in details:
            print(f"  DRY {d['cve_id']} chunks=2 title={(d.get('title') or '')[:60]}")
        return len(details)

    texts = [c["text"] for c in all_chunks]
    # embed in sub-batches of 32
    vectors: list[list[float]] = []
    for i in range(0, len(texts), 32):
        vectors.extend(embed_batch(texts[i : i + 32]))
        time.sleep(0.05)

    points = []
    for chunk, vec in zip(all_chunks, vectors, strict=True):
        points.append({
            "id": str(uuid.uuid5(NS, chunk["chunk_id"])),
            "vector": {"dense": vec},
            "payload": chunk,
        })
    # upsert in batches of 64
    for i in range(0, len(points), 64):
        qdrant_upsert(points[i : i + 64])
    if do_neo4j:
        for d in owners:
            neo4j_merge(d)
    return len(details)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--page-size", type=int, default=50)
    ap.add_argument("--start-page", type=int, default=None)
    ap.add_argument("--max-pages", type=int, default=0, help="0 = until stop condition")
    ap.add_argument("--max-missing", type=int, default=0, help="stop after N newly ingested")
    ap.add_argument("--stop-full-pages", type=int, default=5,
                    help="stop after N consecutive pages with 0 missing")
    ap.add_argument("--created-before", default="2026-04-15",
                    help="also stop when page max created_at < this (ISO date)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-neo4j", action="store_true")
    ap.add_argument("--state", type=Path, default=DEFAULT_STATE)
    ap.add_argument("--batch-upsert", type=int, default=20, help="missing CVEs per embed flush")
    args = ap.parse_args()

    state = load_state(args.state)
    page = args.start_page or int(state.get("page") or 1)
    consecutive_full = 0
    pending: list[dict] = []
    do_neo4j = not args.no_neo4j and not args.dry_run

    print(f"backfill start page={page} dry_run={args.dry_run} state={args.state}")
    pages_done = 0

    while True:
        if args.max_pages and pages_done >= args.max_pages:
            break
        try:
            listing = opencve_get("/api/cve", page=page, page_size=args.page_size)
        except urllib.error.HTTPError as e:
            print(f"list page {page} HTTP {e.code}; retry in 3s")
            time.sleep(3)
            listing = opencve_get("/api/cve", page=page, page_size=args.page_size)

        results = listing.get("results") or []
        if not results:
            print(f"empty page {page}; done")
            break

        missing_ids = []
        oldest = None
        for row in results:
            state["checked"] = int(state.get("checked", 0)) + 1
            cve = row["cve_id"].upper()
            created = row.get("created_at") or ""
            if oldest is None or created < oldest:
                oldest = created
            try:
                n = qdrant_count(cve)
            except Exception as exc:  # noqa: BLE001
                print(f"  count fail {cve}: {exc}")
                state["errors"] = int(state.get("errors", 0)) + 1
                continue
            if n == 0:
                missing_ids.append(cve)
            else:
                state["skipped"] = int(state.get("skipped", 0)) + 1

        print(f"page {page}: {len(results)} rows, missing={len(missing_ids)}, oldest={oldest}")

        if not missing_ids:
            consecutive_full += 1
        else:
            consecutive_full = 0
            for cve in missing_ids:
                try:
                    detail = opencve_get(f"/api/cve/{cve}")
                    pending.append(detail)
                except Exception as exc:  # noqa: BLE001
                    print(f"  detail fail {cve}: {exc}")
                    state["errors"] = int(state.get("errors", 0)) + 1
                if len(pending) >= args.batch_upsert:
                    n = ingest_details(pending, dry_run=args.dry_run, do_neo4j=do_neo4j)
                    state["ingested"] = int(state.get("ingested", 0)) + n
                    print(f"  flushed {n} CVEs (total ingested={state['ingested']})")
                    pending = []
                    save_state(args.state, state)
                    if args.max_missing and state["ingested"] >= args.max_missing:
                        break

        pages_done += 1
        state["page"] = page + 1
        save_state(args.state, state)

        if args.max_missing and int(state.get("ingested", 0)) >= args.max_missing:
            print("hit --max-missing")
            break
        if consecutive_full >= args.stop_full_pages:
            print(f"hit {consecutive_full} consecutive full pages; stopping")
            break
        if oldest and oldest[:10] < args.created_before:
            print(f"oldest created {oldest} < {args.created_before}; stopping")
            break
        if not listing.get("next"):
            print("no next page")
            break
        page += 1
        time.sleep(0.15)

    if pending:
        n = ingest_details(pending, dry_run=args.dry_run, do_neo4j=do_neo4j)
        state["ingested"] = int(state.get("ingested", 0)) + n
        print(f"final flush {n} (total ingested={state['ingested']})")
    save_state(args.state, state)
    print("DONE", json.dumps(state))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
