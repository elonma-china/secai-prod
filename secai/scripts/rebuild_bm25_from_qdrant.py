#!/usr/bin/env python3
"""Rebuild BM25 index for cve_kb from live Qdrant payloads.

bm25s cannot incrementally add docs (IDF/avgdl are corpus-global), so this rebuilds
the full index and atomically swaps into BM25_DIR.

Usage:
  .venv/bin/python /home/ict/AIEngines/secai/scripts/rebuild_bm25_from_qdrant.py
  .venv/bin/python ... --out-dir /path/to/bm25_cve_kb.new
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import tempfile
import time
import urllib.request
from pathlib import Path
from typing import Any

QDRANT = "http://localhost:6333"
COLLECTION = "cve_kb"
DEFAULT_BM25 = Path("/home/ict/AIEngines/secai/secai-data/bm25/bm25_cve_kb")
STATE = Path("/home/ict/AIEngines/secai/secai-data/_state/bm25_rebuild.json")


def http_json(url: str, *, method: str = "GET", body: dict | None = None, timeout: float = 120) -> Any:
    data = None if body is None else json.dumps(body).encode()
    headers = {"Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


def scroll_all(scroll_size: int = 512) -> tuple[list[str], list[str]]:
    """Return (doc_ids, texts) aligned lists for every point that has non-empty text."""
    doc_ids: list[str] = []
    texts: list[str] = []
    offset = None
    pages = 0
    while True:
        body: dict[str, Any] = {
            "limit": scroll_size,
            "with_payload": {"include": ["text", "cve_id", "chunk_kind"]},
            "with_vector": False,
        }
        if offset is not None:
            body["offset"] = offset
        r = http_json(f"{QDRANT}/collections/{COLLECTION}/points/scroll", method="POST", body=body)
        pts = r["result"].get("points") or []
        offset = r["result"].get("next_page_offset")
        pages += 1
        for pt in pts:
            pid = str(pt["id"])
            pl = pt.get("payload") or {}
            text = (pl.get("text") or "").strip()
            if not text:
                # keep searchable stub so ID still joins; rare
                text = f"{pl.get('cve_id') or ''} {pl.get('chunk_kind') or ''}".strip() or pid
            doc_ids.append(pid)
            texts.append(text)
        if pages % 50 == 0 or not pts:
            print(f"  scroll pages={pages} docs={len(doc_ids)}", flush=True)
            STATE.write_text(json.dumps({"phase": "scroll", "docs": len(doc_ids), "pages": pages}, indent=2))
        if offset is None or not pts:
            break
    return doc_ids, texts


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bm25-dir", type=Path, default=DEFAULT_BM25)
    ap.add_argument("--scroll-size", type=int, default=512)
    ap.add_argument("--keep-backup", action="store_true", default=True)
    args = ap.parse_args()

    bm25_dir: Path = args.bm25_dir
    parent = bm25_dir.parent
    parent.mkdir(parents=True, exist_ok=True)
    STATE.parent.mkdir(parents=True, exist_ok=True)

    print(f"BM25 rebuild: scroll {COLLECTION} → index → swap into {bm25_dir}", flush=True)
    t0 = time.time()
    doc_ids, texts = scroll_all(args.scroll_size)
    print(f"scrolled {len(doc_ids)} docs in {time.time()-t0:.1f}s", flush=True)
    if not doc_ids:
        print("ERROR: no docs", flush=True)
        return 1

    import bm25s

    print("tokenizing…", flush=True)
    STATE.write_text(json.dumps({"phase": "tokenize", "docs": len(doc_ids)}, indent=2))
    t1 = time.time()
    corpus_tokens = bm25s.tokenize(texts, stopwords="en", show_progress=True)
    # free raw texts ASAP
    del texts
    print(f"tokenized in {time.time()-t1:.1f}s", flush=True)

    print("indexing (lucene k1=1.5 b=0.75)…", flush=True)
    STATE.write_text(json.dumps({"phase": "index", "docs": len(doc_ids)}, indent=2))
    t2 = time.time()
    retriever = bm25s.BM25(
        k1=1.5, b=0.75, delta=0.5, method="lucene", idf_method="lucene",
        dtype="float32", int_dtype="int32",
    )
    retriever.index(corpus_tokens, show_progress=True)
    print(f"indexed in {time.time()-t2:.1f}s", flush=True)

    # write to temp dir then atomic replace
    tmp = Path(tempfile.mkdtemp(prefix="bm25_rebuild_", dir=str(parent)))
    try:
        print(f"saving → {tmp}", flush=True)
        STATE.write_text(json.dumps({"phase": "save", "docs": len(doc_ids), "tmp": str(tmp)}, indent=2))
        retriever.save(str(tmp))
        with open(tmp / "doc_ids.json", "w", encoding="utf-8") as fh:
            json.dump(doc_ids, fh)
        with open(tmp / "embedding_model.json", "w", encoding="utf-8") as fh:
            json.dump({"model": "bge-m3"}, fh)

        backup = parent / f"{bm25_dir.name}.bak.{int(time.time())}"
        if bm25_dir.exists():
            print(f"backup {bm25_dir} → {backup}", flush=True)
            os.rename(bm25_dir, backup)
        os.rename(tmp, bm25_dir)
        tmp = None  # moved
        print(f"swapped live index → {bm25_dir}", flush=True)
        if not args.keep_backup and backup.exists():
            shutil.rmtree(backup, ignore_errors=True)
    finally:
        if tmp is not None and Path(tmp).exists():
            shutil.rmtree(tmp, ignore_errors=True)

    elapsed = time.time() - t0
    STATE.write_text(json.dumps({
        "phase": "done", "docs": len(doc_ids), "elapsed_s": round(elapsed, 1),
        "bm25_dir": str(bm25_dir), "done": True,
    }, indent=2))
    print(f"DONE docs={len(doc_ids)} elapsed={elapsed:.1f}s", flush=True)
    print("Restart gateway to reload BM25 into memory.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
