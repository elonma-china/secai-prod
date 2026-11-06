#!/usr/bin/env python3
"""Create the ias_howto_kb Qdrant collection and upsert chunks from extract_docx.py.

Schema mirrors cve_kb_hybrid (verified live 2026-11-04): named dense vector "dense"
(size 1024, Cosine) + sparse vector "bm25" (idf modifier) — reuses the same embedding
endpoint/model (embed() in retrieval/embedding.py) and the same FastEmbed BM25 encoder
(_encode_query() in retrieval/bm25.py) as the CVE KB. Never touches Neo4j.

Usage:
  python3 ingest.py <chunks.jsonl> [--dry-run] [--collection NAME] [--recreate]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys

import httpx

from secai_gateway.config import get_settings
from secai_gateway.retrieval.bm25 import _encode_query
from secai_gateway.retrieval.embedding import embed

BATCH_SIZE = 16


async def ensure_collection(client: httpx.AsyncClient, url: str, name: str, recreate: bool) -> None:
    if recreate:
        await client.delete(f"{url}/collections/{name}")
    resp = await client.get(f"{url}/collections/{name}")
    if resp.status_code == 200:
        return
    body = {
        "vectors": {"dense": {"size": 1024, "distance": "Cosine"}},
        "sparse_vectors": {"bm25": {"modifier": "idf"}},
    }
    resp = await client.put(f"{url}/collections/{name}", json=body, timeout=30)
    resp.raise_for_status()


async def embed_chunk(client: httpx.AsyncClient, rec: dict, point_id: int) -> dict:
    dense = await embed(client, rec["text"])
    sparse = _encode_query(rec["text"]) or {"indices": [], "values": []}
    return {
        # Qdrant point IDs must be an unsigned int or UUID — rec["id"] (e.g.
        # "ias-howto-0000") is kept in the payload for traceability instead.
        "id": point_id,
        "vector": {"dense": dense, "bm25": sparse},
        "payload": {
            "chunk_kind": "howto",
            "source": "ias_user_guide",
            "chunk_id": rec["id"],
            "chuong": rec["chuong"],
            "tieu_de": rec["tieu_de"],
            "part": rec["part"],
            "text": rec["text"],
        },
    }


async def main_async(args: argparse.Namespace) -> None:
    settings = get_settings()
    url = settings.qdrant_url.rstrip("/")
    collection = args.collection or settings.qdrant_howto_collection
    records = [json.loads(line) for line in open(args.chunks_path, encoding="utf-8") if line.strip()]
    print(f"loaded {len(records)} chunks from {args.chunks_path}")
    if args.dry_run:
        print(f"dry-run: would ensure collection '{collection}' at {url} and upsert {len(records)} points")
        return
    async with httpx.AsyncClient() as client:
        await ensure_collection(client, url, collection, args.recreate)
        for i in range(0, len(records), BATCH_SIZE):
            batch = records[i:i + BATCH_SIZE]
            points = await asyncio.gather(
                *(embed_chunk(client, r, i + j) for j, r in enumerate(batch)))
            resp = await client.put(
                f"{url}/collections/{collection}/points",
                json={"points": points}, timeout=60,
            )
            resp.raise_for_status()
            print(f"upserted {min(i + BATCH_SIZE, len(records))}/{len(records)}")
    print(f"done: collection '{collection}' now has {len(records)} points upserted")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("chunks_path")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--collection", default=None)
    parser.add_argument("--recreate", action="store_true", help="drop and recreate the collection first")
    args = parser.parse_args()
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
