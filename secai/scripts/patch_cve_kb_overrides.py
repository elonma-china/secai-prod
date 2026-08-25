#!/usr/bin/env python3
"""Apply curated CVE payload overrides to live Qdrant + Neo4j + PoC catalog.

Patches both ``cve_kb_hybrid`` and ``cve_kb``. Re-embeds dense (+ BM25 sparse on hybrid)
when the embedding endpoint matches the collection size; otherwise payload-only.

Also reclassifies GitHub writeup/hardening rows as ``kind=advisory``.

Usage:
  .venv/bin/python /home/ict/AIEngines/secai/scripts/patch_cve_kb_overrides.py
  .venv/bin/python ... --dry-run
"""
from __future__ import annotations

import argparse
import base64
import json
import sqlite3
import sys
import urllib.request
from pathlib import Path
from typing import Any

QDRANT = "http://localhost:6333"
COLLECTIONS = ("cve_kb_hybrid", "cve_kb")
NEO4J_HTTP = "http://localhost:7474/db/neo4j/tx/commit"
NEO4J_AUTH = ("neo4j", "secai2026")
CATALOG = Path("/home/ict/AIEngines/secai/secai-data/poc/catalog.db")
GW_SRC = Path("/home/ict/AIEngines/secai/secai-agent-gateway/src")
GW_ENV = Path("/home/ict/AIEngines/secai/secai-agent-gateway/.env")

sys.path.insert(0, str(GW_SRC))
from secai_gateway.poc.collectors.github_kind import classify_github_poc_kind  # noqa: E402
from secai_gateway.retrieval.cve_overrides import CVE_OVERRIDES, apply_cve_override  # noqa: E402


def http_json(url: str, *, method: str = "GET", body: dict | None = None,
              headers: dict | None = None, timeout: float = 120) -> Any:
    data = None if body is None else json.dumps(body).encode()
    hdrs = {"Accept": "application/json", **(headers or {})}
    if data is not None:
        hdrs["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


def _basic(user: str, password: str) -> str:
    return "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()


def load_env(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.is_file():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def scroll_cve(collection: str, cve: str) -> list[dict]:
    r = http_json(
        f"{QDRANT}/collections/{collection}/points/scroll",
        method="POST",
        body={
            "filter": {"must": [{"key": "cve_id", "match": {"value": cve}}]},
            "limit": 8, "with_payload": True, "with_vector": False,
        },
    )
    return r["result"].get("points") or []


def set_payload(collection: str, point_id: Any, payload: dict) -> None:
    http_json(
        f"{QDRANT}/collections/{collection}/points/payload?wait=true",
        method="POST",
        body={"payload": payload, "points": [point_id]},
        timeout=60,
    )


def collection_dense_size(collection: str) -> int | None:
    r = http_json(f"{QDRANT}/collections/{collection}")
    vecs = r["result"]["config"]["params"].get("vectors") or {}
    dense = vecs.get("dense") if isinstance(vecs, dict) else None
    if isinstance(dense, dict):
        return int(dense.get("size") or 0) or None
    return None


def collection_has_bm25(collection: str) -> bool:
    r = http_json(f"{QDRANT}/collections/{collection}")
    sparse = r["result"]["config"]["params"].get("sparse_vectors") or {}
    return "bm25" in sparse


def embed_dense(texts: list[str], *, url: str, key: str, model: str) -> list[list[float]]:
    r = http_json(
        f"{url.rstrip('/')}/v1/embeddings",
        method="POST",
        body={"model": model, "input": texts},
        headers={"Authorization": f"Bearer {key}"} if key else None,
        timeout=180,
    )
    data = sorted(r["data"], key=lambda x: x["index"])
    return [row["embedding"] for row in data]


def encode_bm25(texts: list[str]) -> list[dict[str, list]]:
    from secai_gateway.retrieval.bm25 import _load_sparse_model
    model = _load_sparse_model()
    out: list[dict[str, list]] = []
    for emb in model.embed(texts):
        out.append({
            "indices": [int(i) for i in emb.indices],
            "values": [float(v) for v in emb.values],
        })
    return out


def update_vectors(collection: str, point_id: Any, vectors: dict) -> None:
    http_json(
        f"{QDRANT}/collections/{collection}/points?wait=true",
        method="PUT",
        body={"points": [{"id": point_id, "vector": vectors}]},
        timeout=60,
    )


def neo4j_tx(statements: list[dict]) -> Any:
    return http_json(
        NEO4J_HTTP,
        method="POST",
        body={"statements": statements},
        headers={"Authorization": _basic(*NEO4J_AUTH)},
        timeout=60,
    )


def patch_neo4j(cve: str, ov: dict, new_pl: dict) -> None:
    stmts: list[dict] = []
    if ov.get("cwe_refs") is not None:
        stmts.append({
            "statement": """
            MATCH (c:CVE {id:$id})
            OPTIONAL MATCH (c)-[r:HAS_WEAKNESS]->()
            DELETE r
            WITH c
            UNWIND $cwes AS wid
              MERGE (w:CWE {id: wid})
              MERGE (c)-[:HAS_WEAKNESS]->(w)
            RETURN c.id
            """,
            "parameters": {"id": cve, "cwes": new_pl.get("cwe_refs") or []},
        })
    if ov.get("products_remove"):
        stmts.append({
            "statement": """
            MATCH (c:CVE {id:$id})-[r:AFFECTS]->(p:Product)
            WHERE toLower(p.name) IN $remove
            DELETE r
            """,
            "parameters": {
                "id": cve,
                "remove": [str(p).lower() for p in ov["products_remove"]],
            },
        })
    if ov.get("products_add"):
        stmts.append({
            "statement": """
            MATCH (c:CVE {id:$id})
            UNWIND $add AS pname
              MERGE (p:Product {name: pname})
              MERGE (c)-[:AFFECTS]->(p)
            RETURN c.id
            """,
            "parameters": {"id": cve, "add": list(ov["products_add"])},
        })
    if ov.get("is_in_kev") is True:
        stmts.append({
            "statement": "MATCH (c:CVE {id:$id}) SET c.is_in_kev = true RETURN c.id",
            "parameters": {"id": cve},
        })
    if stmts:
        r = neo4j_tx(stmts)
        if r.get("errors"):
            raise RuntimeError(str(r["errors"]))


def patch_catalog(dry_run: bool) -> int:
    if not CATALOG.is_file():
        print(f"catalog missing: {CATALOG}")
        return 0
    db = sqlite3.connect(str(CATALOG))
    db.row_factory = sqlite3.Row
    cves = list(CVE_OVERRIDES) + ["CVE-2026-21509"]
    marks = ",".join("?" * len(cves))
    rows = db.execute(
        f"""SELECT DISTINCT p.id, p.kind, p.title, p.description, p.source_ref
            FROM poc p JOIN poc_cve pc ON pc.poc_id=p.id
            WHERE p.source='poc_github' AND p.duplicate_of IS NULL
              AND pc.cve_id IN ({marks})""",
        tuple(cves),
    ).fetchall()
    n = 0
    for r in rows:
        new_kind = classify_github_poc_kind(r["title"] or "", r["description"] or "",
                                            r["source_ref"] or "")
        if new_kind == "advisory" and r["kind"] != "advisory":
            print(f"  catalog [{r['id']}] {r['source_ref']}: {r['kind']} → advisory")
            if not dry_run:
                db.execute("UPDATE poc SET kind='advisory' WHERE id=?", (r["id"],))
            n += 1
    if not dry_run:
        db.commit()
    db.close()
    return n


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-embed", action="store_true")
    args = ap.parse_args()

    env = load_env(GW_ENV)
    embed_url = env.get("EMBEDDING_API_URL", "")
    embed_key = env.get("EMBEDDING_API_KEY", "")
    embed_model = env.get("EMBEDDING_MODEL", "")

    print(f"overrides: {len(CVE_OVERRIDES)} CVEs  dry_run={args.dry_run}")
    for col in COLLECTIONS:
        dense_size = collection_dense_size(col)
        has_bm25 = collection_has_bm25(col)
        print(f"\n=== {col} dense={dense_size} bm25={has_bm25} ===")
        embed_ok = False
        if not args.no_embed and not args.dry_run and embed_url and dense_size:
            try:
                probe = embed_dense(["probe"], url=embed_url, key=embed_key, model=embed_model)
                embed_ok = bool(probe) and len(probe[0]) == dense_size
                print(f"  embed dim={len(probe[0]) if probe else 0} match={embed_ok}")
            except Exception as exc:  # noqa: BLE001
                print(f"  embed skipped: {exc}")

        for cve, ov in CVE_OVERRIDES.items():
            pts = scroll_cve(col, cve)
            if not pts:
                print(f"  {cve}: not in {col}")
                continue
            for pt in pts:
                old = pt.get("payload") or {}
                new = apply_cve_override(old)
                changed = (
                    old.get("cwe_refs") != new.get("cwe_refs")
                    or old.get("products") != new.get("products")
                    or old.get("is_in_kev") != new.get("is_in_kev")
                    or old.get("text") != new.get("text")
                )
                kind = old.get("chunk_kind") or old.get("kind")
                print(f"  {cve} [{kind}] changed={changed} "
                      f"cwe={old.get('cwe_refs')}→{new.get('cwe_refs')} "
                      f"kev={old.get('is_in_kev')}→{new.get('is_in_kev')}")
                if args.dry_run or not changed:
                    continue
                set_payload(col, pt["id"], new)
                if embed_ok:
                    vectors: dict[str, Any] = {
                        "dense": embed_dense([new.get("text") or ""], url=embed_url,
                                             key=embed_key, model=embed_model)[0],
                    }
                    if has_bm25:
                        try:
                            vectors["bm25"] = encode_bm25([new.get("text") or ""])[0]
                        except Exception as exc:  # noqa: BLE001
                            print(f"    bm25 encode skip: {exc}")
                    update_vectors(col, pt["id"], vectors)

    print("\n=== Neo4j ===")
    for cve, ov in CVE_OVERRIDES.items():
        # Use hybrid core payload (after override) as the graph source of truth.
        pts = scroll_cve("cve_kb_hybrid", cve) or scroll_cve("cve_kb", cve)
        core = next((p["payload"] for p in pts
                     if (p.get("payload") or {}).get("chunk_kind") == "core"), None)
        if not core:
            print(f"  {cve}: skip (no core point)")
            continue
        new = apply_cve_override(core)
        print(f"  {cve} cwe={new.get('cwe_refs')} kev={new.get('is_in_kev')}")
        if not args.dry_run:
            patch_neo4j(cve, ov, new)

    print("\n=== catalog kind ===")
    n = patch_catalog(args.dry_run)
    print(f"  advisory reclassifications: {n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
