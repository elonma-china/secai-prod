#!/usr/bin/env python3
"""Knowledge KB pipeline: chunk / index / ingest / verify / rollback.

Plan: secai-agent-gateway/docs/plans/kb-knowledge-grounding.md. Raw sources live in
secai-data/knowledge/raw/ (git-ignored) with provenance in manifest.json.

  python3 kb_knowledge.py chunk                       # raw -> chunks.jsonl + stats (offline)
  python3 kb_knowledge.py ingest --collection NAME    # embed + upsert (needs embed serving)
  python3 kb_knowledge.py verify --collection NAME
  python3 kb_knowledge.py rollback --collection NAME --batch BATCH

``ingest`` refuses to write to the live production collection unless --allow-prod is passed:
prod gateway code predates the knowledge `scope` filter, so knowledge chunks would leak into
CVE answers until the new image is deployed.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import chunkers as ck  # noqa: E402

ROOT = Path("/home/ict/AIEngines/secai/secai-data/knowledge")
RAW = ROOT / "raw"
CHUNKS = ROOT / "chunks.jsonl"
MANIFEST = ROOT / "manifest.json"
GATEWAY_ENV = "/home/ict/AIEngines/secai/secai-agent-gateway/.env"
DOCKER_ENV = "/home/ict/AIEngines/secai/secai-docker/.env"
QDRANT = os.environ.get("QDRANT_URL", "http://localhost:6333")
PROD_COLLECTION = "cve_kb_hybrid"
EXPECTED_DENSE_DIM = 1024
EXPECTED_EMBED_MODEL = "DuyTa/sec-embedding"
BM25_MODEL = "Qdrant/bm25"
LOCAL_BM25_MODEL_DIR = "/home/ict/AIEngines/secai/secai-data/_state/bm25_local_model"
PAYLOAD_INDEXES = {"sensitivity": "keyword", "ingest_batch": "keyword", "source": "keyword",
                   "license": "keyword", "doc_id": "keyword", "parent_id": "keyword"}


def load_env(*paths: str) -> dict[str, str]:
    env: dict[str, str] = {}
    for path in paths:
        p = Path(path)
        if not p.exists():
            continue
        for line in p.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    return env


def http_json(url: str, *, method: str = "GET", body: Any = None, headers: dict | None = None, timeout: float = 120) -> Any:
    data = None if body is None else json.dumps(body).encode()
    h = {"Accept": "application/json", **(headers or {})}
    if data is not None:
        h["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=h, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


def git_sha(repo: Path) -> str:
    return subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()


# ── chunk ──────────────────────────────────────────────────────────────────────
def cmd_chunk(args: argparse.Namespace) -> int:
    batch = args.batch or "knowledge-" + dt.date.today().isoformat()
    cs_repo, asvs_repo = RAW / "owasp-cheatsheet/repo", RAW / "owasp-asvs/repo"
    cwe_xml = next((RAW / "cwe").glob("cwec_v*.xml"))
    chunks: list[dict[str, Any]] = []
    chunks += ck.chunk_cheatsheets(cs_repo, "git:" + git_sha(cs_repo), batch)
    chunks += ck.chunk_asvs(asvs_repo, "git:" + git_sha(asvs_repo), batch)
    chunks += ck.chunk_cwe(cwe_xml, batch)
    chunks += ck.chunk_capec(RAW / "capec/capec_latest.xml", batch)
    chunks += ck.chunk_attack(RAW / "attack/enterprise-attack.json", batch)
    chunks += ck.chunk_cwe_top25(cwe_xml, batch)
    t10_repo = RAW / "owasp-top10/repo"
    chunks += ck.chunk_owasp_top10(t10_repo, "git:" + git_sha(t10_repo), batch)
    chunks, dups = ck.dedup(chunks)
    with CHUNKS.open("w", encoding="utf-8") as f:
        for c in chunks:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")
    lens = sorted(len(c["text"]) for c in chunks)
    pick = lambda q: lens[min(len(lens) - 1, int(len(lens) * q))]  # noqa: E731
    stats = {
        "batch": batch, "total": len(chunks), **dups,
        "by_source": dict(Counter(c["source"] for c in chunks)),
        "by_kind": dict(Counter(c["kind"] for c in chunks)),
        "by_sensitivity": dict(Counter(c["sensitivity"] for c in chunks)),
        "text_chars": {"min": lens[0], "p1": pick(0.01), "p50": pick(0.5), "p99": pick(0.99), "max": lens[-1]},
        "empty_text": sum(1 for c in chunks if not c["text"].strip()),
        "over_max": sum(1 for c in chunks if len(c["text"]) > ck.MAX_CHARS + 200),
        "missing_provenance": sum(1 for c in chunks if not all(c.get(k) for k in ("license", "attribution", "source_url", "version"))),
        "versions": sorted({c["version"] for c in chunks}),
        "raw_sources": {  # what the chunks were built from, for audit / re-fetch
            "owasp-cheatsheet": {"git_sha": git_sha(cs_repo), "url": "https://github.com/OWASP/CheatSheetSeries"},
            "owasp-asvs": {"git_sha": git_sha(asvs_repo), "url": "https://github.com/OWASP/ASVS"},
            "cwe": {"file": cwe_xml.name, "sha256": ck.sha(cwe_xml.read_text(encoding="utf-8")), "url": "https://cwe.mitre.org/data/downloads.html"},
            "owasp-top10": {"git_sha": git_sha(t10_repo), "url": "https://github.com/OWASP/Top10"},
            "capec": {"sha256": ck.sha((RAW / "capec/capec_latest.xml").read_text(encoding="utf-8")), "url": "https://capec.mitre.org/data/downloads.html"},
            "attack": {"sha256": ck.sha((RAW / "attack/enterprise-attack.json").read_text(encoding="utf-8")), "url": "https://github.com/mitre-attack/attack-stix-data"},
        },
    }
    MANIFEST.write_text(json.dumps(stats, indent=2, ensure_ascii=False))
    print(json.dumps(stats, indent=2, ensure_ascii=False))
    return 0


def read_chunks() -> list[dict[str, Any]]:
    return [json.loads(line) for line in CHUNKS.read_text(encoding="utf-8").splitlines() if line.strip()]


# ── ingest ─────────────────────────────────────────────────────────────────────
_sparse = None


def sparse_embed(texts: list[str]) -> list[dict[str, list]]:
    global _sparse
    if _sparse is None:
        from fastembed import SparseTextEmbedding

        kw = {"specific_model_path": LOCAL_BM25_MODEL_DIR} if os.path.isdir(LOCAL_BM25_MODEL_DIR) else {}
        _sparse = SparseTextEmbedding(model_name=BM25_MODEL, **kw)
    return [{"indices": [int(i) for i in e.indices], "values": [float(v) for v in e.values]} for e in _sparse.embed(texts)]


def embed_cfg() -> tuple[str, str, str]:
    env = load_env(DOCKER_ENV, GATEWAY_ENV)
    return env["EMBEDDING_API_URL"].rstrip("/"), env.get("EMBEDDING_API_KEY", ""), env.get("EMBEDDING_MODEL", "")


def check_embed_model() -> None:
    """Dim 1024 does not prove the right model (bge-m3 is 1024 too): ask the server."""
    url, key, model = embed_cfg()
    ids = [m["id"] for m in http_json(f"{url}/v1/models", headers={"Authorization": f"Bearer {key}"})["data"]]
    if EXPECTED_EMBED_MODEL not in ids or model != EXPECTED_EMBED_MODEL:
        raise SystemExit(f"embedding model mismatch: env={model!r} serving={ids} want {EXPECTED_EMBED_MODEL}")


CLIP_STEPS = (None, 800, 600, 450, 300)  # the serving window is 512 tokens; code is token-dense
CLIPPED: dict[str, int] = {}  # doc_id -> chars actually embedded, for chunks that did not fit


def _embed_call(texts: list[str]) -> list[list[float]]:
    url, key, model = embed_cfg()
    r = http_json(f"{url}/v1/embeddings", method="POST", body={"model": model, "input": texts},
                  headers={"Authorization": f"Bearer {key}"}, timeout=180)
    vecs = [row["embedding"] for row in sorted(r["data"], key=lambda x: x["index"])]
    if any(len(v) != EXPECTED_DENSE_DIM for v in vecs):
        raise SystemExit("dense dim mismatch")
    return vecs


def embed_dense(texts: list[str], doc_ids: list[str] | None = None) -> list[list[float]]:
    """Whole batch first; on HTTP 400 (a chunk over the 512-token window) fall back to one
    request per chunk, clipping the embedded text step by step. The stored text stays whole."""
    try:
        return _embed_call(texts)
    except urllib.error.HTTPError as e:
        if e.code != 400:
            raise
    out = []
    for i, t in enumerate(texts):
        for step in CLIP_STEPS:
            try:
                out.append(_embed_call([t if step is None else t[:step]])[0])
                if step is not None:
                    CLIPPED[(doc_ids or [str(i)])[i]] = step
                break
            except urllib.error.HTTPError as e:
                if e.code != 400 or step == CLIP_STEPS[-1]:
                    raise
    return out


def ensure_collection(name: str) -> None:
    try:
        http_json(f"{QDRANT}/collections/{name}")
    except urllib.error.HTTPError as e:
        if e.code != 404:
            raise
        http_json(f"{QDRANT}/collections/{name}", method="PUT", body={
            "vectors": {"dense": {"size": EXPECTED_DENSE_DIM, "distance": "Cosine"}},
            "sparse_vectors": {"bm25": {"modifier": "idf"}}})
    ensure_indexes(name)


def ensure_indexes(name: str) -> None:
    for field, schema in PAYLOAD_INDEXES.items():
        http_json(f"{QDRANT}/collections/{name}/index?wait=true", method="PUT",
                  body={"field_name": field, "field_schema": schema})


def cmd_ingest(args: argparse.Namespace) -> int:
    if args.collection == PROD_COLLECTION and not args.allow_prod:
        raise SystemExit(f"refusing to write {PROD_COLLECTION} without --allow-prod (see module docstring)")
    chunks = read_chunks()
    if args.limit:
        chunks = chunks[: args.limit]
    print(f"{len(chunks)} chunks -> {args.collection}" + (" [dry-run]" if args.dry_run else ""))
    if args.dry_run:
        return 0
    check_embed_model()
    e_url, _, e_model = embed_cfg()
    print(f"embedding: model={e_model} url={e_url} (DOCKER_ENV wins over GATEWAY_ENV)")
    ensure_collection(args.collection)
    t0, done = time.time(), 0
    skipped = 0
    for i in range(0, len(chunks), args.batch_size):
        part = chunks[i: i + args.batch_size]
        ids = [ck.point_id(c["doc_id"]) for c in part]
        # Skip only points that exist AND carry the same text: a changed text must be re-embedded.
        have = {str(r["id"]): (r.get("payload") or {}).get("text_sha") for r in http_json(
            f"{QDRANT}/collections/{args.collection}/points", method="POST",
            body={"ids": ids, "with_payload": ["text_sha"], "with_vector": False})["result"]}
        todo = [(c, pid) for c, pid in zip(part, ids) if have.get(pid) != c["text_sha"]]
        skipped += len(part) - len(todo)
        if not todo:
            done += len(part)
            continue
        part = [c for c, _ in todo]
        texts = [c["text"][: ck.MAX_CHARS + 200] for c in part]
        dense, sparse = embed_dense(texts, [c["doc_id"] for c in part]), sparse_embed(texts)
        points = [{"id": pid, "vector": {"dense": d, "bm25": s}, "payload": {**c, **({"embed_clip_chars": CLIPPED[c["doc_id"]]} if c["doc_id"] in CLIPPED else {})}}
                  for (c, pid), d, s in zip(todo, dense, sparse)]
        http_json(f"{QDRANT}/collections/{args.collection}/points?wait=true", method="PUT", body={"points": points}, timeout=300)
        done += len(part)
        if (i // args.batch_size) % 10 == 0:
            print(f"  {done}/{len(chunks)} ({time.time() - t0:.0f}s)", flush=True)
    print(f"done {done} in {time.time() - t0:.0f}s; unchanged and skipped {skipped}; clipped for embedding {len(CLIPPED)}: {sorted(CLIPPED)[:10]}")
    (ROOT / "ingest_log.json").write_text(json.dumps({
        "collection": args.collection, "batch": chunks[0]["ingest_batch"] if chunks else None, "chunks": len(chunks),
        "skipped_unchanged": skipped, "embedding_model": e_model, "embedding_url": e_url,
        "embed_clipped": CLIPPED, "finished": dt.datetime.now().isoformat(timespec="seconds")}, indent=2))
    return 0


# ── verify / rollback ──────────────────────────────────────────────────────────
def scroll(collection: str, flt: dict, with_vector: bool = False, page: int = 512):
    offset = None
    while True:
        body = {"filter": flt, "limit": page, "with_payload": True, "with_vector": with_vector}
        if offset is not None:
            body["offset"] = offset
        r = http_json(f"{QDRANT}/collections/{collection}/points/scroll", method="POST", body=body, timeout=120)["result"]
        yield from r["points"]
        offset = r.get("next_page_offset")
        if offset is None:
            return


def cmd_verify(args: argparse.Namespace) -> int:
    expected = {c["doc_id"]: c for c in read_chunks()}
    flt = {"must": [{"key": "kind", "match": {"any": list(ck.KNOWLEDGE_KINDS)}}]}
    seen, no_dense, no_sparse, bad_prov, stale_text = {}, 0, 0, 0, 0
    for p in scroll(args.collection, flt, with_vector=True):
        pl = p["payload"]
        stale_text += pl.get("text_sha") != (expected.get(pl["doc_id"]) or {}).get("text_sha")
        seen[pl["doc_id"]] = seen.get(pl["doc_id"], 0) + 1
        vec = p.get("vector") or {}
        no_dense += not (isinstance(vec.get("dense"), list) and len(vec["dense"]) == EXPECTED_DENSE_DIM)
        no_sparse += not (isinstance(vec.get("bm25"), dict) and vec["bm25"].get("indices"))
        bad_prov += not all(pl.get(k) for k in ("license", "attribution", "source_url", "version"))
    report = {
        "collection": args.collection, "expected": len(expected), "found": sum(seen.values()),
        "unique_doc_ids": len(seen), "duplicate_points": sum(v - 1 for v in seen.values()),
        "missing": len(set(expected) - set(seen)), "unexpected": len(set(seen) - set(expected)),
        "points_without_dense": no_dense, "points_without_sparse_bm25": no_sparse, "missing_provenance": bad_prov,
        "text_differs_from_chunks_jsonl": stale_text,
    }
    print(json.dumps(report, indent=2))
    return 0 if report["missing"] == report["unexpected"] == no_dense == no_sparse == bad_prov == stale_text == report["duplicate_points"] == 0 else 1


def cmd_rollback(args: argparse.Namespace) -> int:
    if args.collection == PROD_COLLECTION and not args.allow_prod and not args.dry_run:
        raise SystemExit(f"refusing to delete from {PROD_COLLECTION} without --allow-prod")
    # Batch name AND knowledge kinds: a mistyped/reused batch name can never touch CVE or definition points.
    flt = {"must": [{"key": "ingest_batch", "match": {"value": args.batch}},
                    {"key": "kind", "match": {"any": list(ck.KNOWLEDGE_KINDS)}}]}
    before = http_json(f"{QDRANT}/collections/{args.collection}/points/count", method="POST", body={"filter": flt, "exact": True})["result"]["count"]
    print(f"points in batch {args.batch}: {before}")
    if args.dry_run:
        return 0
    http_json(f"{QDRANT}/collections/{args.collection}/points/delete?wait=true", method="POST", body={"filter": flt}, timeout=300)
    after = http_json(f"{QDRANT}/collections/{args.collection}/points/count", method="POST", body={"filter": flt, "exact": True})["result"]["count"]
    print(f"after delete: {after}")
    return 0 if after == 0 else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("chunk"); c.add_argument("--batch"); c.set_defaults(fn=cmd_chunk)
    i = sub.add_parser("ingest"); i.add_argument("--collection", required=True); i.add_argument("--limit", type=int)
    i.add_argument("--batch-size", type=int, default=32); i.add_argument("--dry-run", action="store_true")
    i.add_argument("--allow-prod", action="store_true"); i.set_defaults(fn=cmd_ingest)
    v = sub.add_parser("verify"); v.add_argument("--collection", required=True); v.set_defaults(fn=cmd_verify)
    r = sub.add_parser("rollback"); r.add_argument("--collection", required=True); r.add_argument("--batch", required=True)
    r.add_argument("--dry-run", action="store_true"); r.add_argument("--allow-prod", action="store_true"); r.set_defaults(fn=cmd_rollback)
    a = ap.parse_args()
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
