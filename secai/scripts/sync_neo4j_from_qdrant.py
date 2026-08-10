#!/usr/bin/env python3
"""Sync Neo4j CVE nodes from Qdrant cve_kb payloads (no re-embed).

Default: points with source=opencve-backfill-* and chunk_kind=core.
Idempotent MERGE on CVE + HAS_WEAKNESS + AFFECTS.

Usage:
  python3 sync_neo4j_from_qdrant.py
  python3 sync_neo4j_from_qdrant.py --source-prefix opencve-backfill
  python3 sync_neo4j_from_qdrant.py --year 2026
"""
from __future__ import annotations

import argparse
import base64
import json
import time
import urllib.request
from pathlib import Path
from typing import Any

QDRANT = "http://localhost:6333"
COLLECTION = "cve_kb"
NEO4J_HTTP = "http://localhost:7474/db/neo4j/tx/commit"
NEO4J_AUTH = ("neo4j", "secai2026")
STATE = Path("/home/ict/AIEngines/secai/secai-data/_state/neo4j_sync_from_qdrant.json")


def _basic(user: str, password: str) -> str:
    return "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()


def http_json(url: str, *, method: str = "GET", body: dict | None = None,
              headers: dict | None = None, timeout: float = 120) -> Any:
    data = None if body is None else json.dumps(body).encode()
    hdrs = {"Accept": "application/json", **(headers or {})}
    if data is not None:
        hdrs["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


def neo4j_tx(statements: list[dict]) -> Any:
    return http_json(
        NEO4J_HTTP,
        method="POST",
        body={"statements": statements},
        headers={"Authorization": _basic(*NEO4J_AUTH)},
        timeout=120,
    )


def build_filter(source_prefix: str | None, year: str | None) -> dict:
    must: list[dict] = [{"key": "chunk_kind", "match": {"value": "core"}}]
    if source_prefix:
        # Qdrant text match on prefix via should-match value when exact source known;
        # use MatchText-like: we store exact source "opencve-backfill-2026-08".
        must.append({"key": "source", "match": {"value": source_prefix}})
    if year:
        must.append({"key": "year", "match": {"value": year}})
    return {"must": must}


def scroll_batch(flt: dict, offset: Any, limit: int) -> tuple[list[dict], Any]:
    body: dict[str, Any] = {
        "filter": flt,
        "limit": limit,
        "with_payload": True,
        "with_vector": False,
    }
    if offset is not None:
        body["offset"] = offset
    r = http_json(
        f"{QDRANT}/collections/{COLLECTION}/points/scroll",
        method="POST",
        body=body,
        timeout=120,
    )
    result = r["result"]
    return result.get("points") or [], result.get("next_page_offset")


def payload_to_row(pl: dict) -> dict | None:
    cve = (pl.get("cve_id") or "").upper()
    if not cve.startswith("CVE-"):
        return None
    cwes = [str(w).upper() for w in (pl.get("cwe_refs") or []) if w]
    products = [str(p).strip().lower().replace(" ", "_") for p in (pl.get("products") or []) if p]
    # dedupe
    products = list(dict.fromkeys(products))[:20]
    return {
        "id": cve,
        "year": str(pl.get("year") or (cve.split("-")[1] if "-" in cve else "")),
        "severity": pl.get("severity") or "UNKNOWN",
        "cvss": pl.get("cvss_score"),
        "kev": bool(pl.get("is_in_kev")),
        "cwes": cwes,
        "products": products,
    }


MERGE_BATCH = """
UNWIND $rows AS row
MERGE (c:CVE {id: row.id})
SET c.year = row.year,
    c.severity = row.severity,
    c.cvss_score = row.cvss,
    c.is_in_kev = row.kev
WITH c, row
FOREACH (wid IN row.cwes |
  MERGE (w:CWE {id: wid})
  MERGE (c)-[:HAS_WEAKNESS]->(w)
)
FOREACH (pname IN row.products |
  MERGE (p:Product {name: pname})
  MERGE (c)-[:AFFECTS]->(p)
)
RETURN count(DISTINCT c) AS n
"""


def flush(rows: list[dict]) -> int:
    if not rows:
        return 0
    # dedupe by CVE id (keep last)
    by_id = {r["id"]: r for r in rows}
    rows = list(by_id.values())
    r = neo4j_tx([{"statement": MERGE_BATCH, "parameters": {"rows": rows}}])
    if r.get("errors"):
        raise RuntimeError(str(r["errors"]))
    data = r["results"][0]["data"]
    return int(data[0]["row"][0]) if data else len(rows)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source-prefix", default="opencve-backfill-2026-08",
                    help="Exact Qdrant payload.source value to sync (empty = all core chunks)")
    ap.add_argument("--year", default="", help="Optional year filter, e.g. 2026")
    ap.add_argument("--batch", type=int, default=200)
    ap.add_argument("--scroll-size", type=int, default=256)
    ap.add_argument("--max", type=int, default=0, help="Stop after N CVEs (0=all)")
    args = ap.parse_args()

    src = args.source_prefix.strip() or None
    year = args.year.strip() or None
    flt = build_filter(src, year)
    offset = None
    synced = 0
    seen: set[str] = set()
    buf: list[dict] = []
    pages = 0

    print(f"neo4j sync start filter={flt} batch={args.batch}", flush=True)
    while True:
        points, offset = scroll_batch(flt, offset, args.scroll_size)
        pages += 1
        if not points:
            break
        for pt in points:
            row = payload_to_row(pt.get("payload") or {})
            if not row or row["id"] in seen:
                continue
            seen.add(row["id"])
            buf.append(row)
            if len(buf) >= args.batch:
                n = flush(buf)
                synced += len(buf)
                print(f"  synced +{len(buf)} (total unique={synced}, neo_merge≈{n})", flush=True)
                buf = []
                STATE.write_text(json.dumps({
                    "synced": synced, "pages": pages, "source": src, "year": year,
                }, indent=2))
                if args.max and synced >= args.max:
                    break
        if args.max and synced >= args.max:
            break
        if offset is None:
            break
        time.sleep(0.01)

    if buf:
        n = flush(buf)
        synced += len(buf)
        print(f"  final +{len(buf)} (total unique={synced}, neo_merge≈{n})", flush=True)

    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps({
        "synced": synced, "pages": pages, "source": src, "year": year, "done": True,
    }, indent=2))
    print("DONE", json.dumps({"synced": synced, "pages": pages}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
