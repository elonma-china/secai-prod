#!/usr/bin/env python3
"""Build a LAB copy of cve_kb_hybrid for A/B tests, without touching production.

Copies (with vectors, same ids) the gold CVEs of an eval set, every definition node
(weakness/technique/pattern) and the first N points of production into a new collection with
the same vector/payload-index config. Read-only on the source collection.

  python3 make_lab_collection.py --dst cve_kb_knowledge_lab --extra 40000 \
      --gold-file ../../secai-agent-gateway/evals/rag_eval/eval_set.jsonl
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import kb_knowledge as kb  # noqa: E402

SRC = kb.PROD_COLLECTION


def scroll_src(flt: dict | None, limit: int | None = None, page: int = 256):
    offset, n = None, 0
    while True:
        body = {"limit": page, "with_payload": True, "with_vector": True}
        if flt:
            body["filter"] = flt
        if offset is not None:
            body["offset"] = offset
        r = kb.http_json(f"{kb.QDRANT}/collections/{SRC}/points/scroll", method="POST", body=body, timeout=180)["result"]
        for p in r["points"]:
            yield p
            n += 1
            if limit and n >= limit:
                return
        offset = r.get("next_page_offset")
        if offset is None:
            return


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dst", required=True)
    ap.add_argument("--extra", type=int, default=40000)
    ap.add_argument("--gold-file")
    a = ap.parse_args()
    if a.dst == SRC:
        raise SystemExit("dst must differ from the source collection")

    src_cfg = kb.http_json(f"{kb.QDRANT}/collections/{SRC}")["result"]
    try:
        kb.http_json(f"{kb.QDRANT}/collections/{a.dst}")
        raise SystemExit(f"{a.dst} already exists; delete it first (DELETE /collections/{a.dst})")
    except Exception as e:  # 404 expected
        if "404" not in str(e):
            raise
    kb.http_json(f"{kb.QDRANT}/collections/{a.dst}", method="PUT", body={
        "vectors": src_cfg["config"]["params"]["vectors"],
        "sparse_vectors": src_cfg["config"]["params"]["sparse_vectors"]})
    for field, meta in src_cfg["payload_schema"].items():
        kb.http_json(f"{kb.QDRANT}/collections/{a.dst}/index?wait=true", method="PUT",
                     body={"field_name": field, "field_schema": meta["data_type"]})

    gold: set[str] = set()
    if a.gold_file:
        for line in open(a.gold_file, encoding="utf-8"):
            if line.strip():
                gold.update(c.upper() for c in json.loads(line).get("relevant_cves") or [])
    plan = [("definition nodes", {"must": [{"key": "kind", "match": {"any": ["weakness", "technique", "pattern"]}}]}, None)]
    if gold:
        plan.append((f"gold CVEs ({len(gold)})", {"must": [{"key": "cve_id", "match": {"any": sorted(gold)}}]}, None))
    plan.append((f"first {a.extra} points", None, a.extra))
    seen: set[str] = set()
    for label, flt, limit in plan:
        batch, n = [], 0
        for p in scroll_src(flt, limit):
            if str(p["id"]) in seen:
                continue
            seen.add(str(p["id"]))
            batch.append({"id": p["id"], "vector": p["vector"], "payload": p["payload"]})
            if len(batch) >= 256:
                kb.http_json(f"{kb.QDRANT}/collections/{a.dst}/points?wait=true", method="PUT", body={"points": batch}, timeout=300)
                n += len(batch)
                batch = []
        if batch:
            kb.http_json(f"{kb.QDRANT}/collections/{a.dst}/points?wait=true", method="PUT", body={"points": batch}, timeout=300)
            n += len(batch)
        print(f"{label}: +{n}", flush=True)
    cnt = kb.http_json(f"{kb.QDRANT}/collections/{a.dst}/points/count", method="POST", body={"exact": True})["result"]["count"]
    print(f"{a.dst}: {cnt} points")
    return 0


if __name__ == "__main__":
    sys.exit(main())
