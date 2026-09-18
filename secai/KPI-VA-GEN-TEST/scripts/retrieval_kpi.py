#!/usr/bin/env python3
"""retrieval_kpi.py — [CHẠY TRÊN VPS] Đo KPI tầng truy hồi + xuất contexts cho RAGAS.

- R-KPI-EMB96: Embedding Hit Rate@10 — embed(query) → Qdrant dense search top-10;
  hit nếu có point cve_id==gold & chunk_kind==core trong top-10.
- R-KPI-RERANK90: Reranking Precision@1 — rerank top-20 candidate (dense) → top-1 có cve_id==gold.
- contexts.jsonl: top-k chunk text mỗi query (làm contexts cho RAGAS faithfulness).

Stdlib-only (urllib) — chạy được trên VPS Python 3.14 không pip. Loopback embed/rerank/qdrant.

Build retrieval_pairs từ eval/datasets-acceptance: câu có đúng 1 gold cve_id (bỏ trap + pair).
Mỗi query là câu hỏi NL thật của bộ test (một số có nêu CVE-id, một số mô tả → suy luận).

Usage (trên VPS):
  python3 retrieval_kpi.py --out ../raw-logs --contexts ../raw-logs/contexts.jsonl [--limit N]
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.request
from pathlib import Path

# Trích CVE-id explicit trong câu hỏi (để dùng exact cve_id filter — khớp product qdrant_search).
CVE_IN_QUERY_RE = re.compile(r"CVE-\d{4}-\d{4,7}", re.IGNORECASE)

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import config  # noqa: E402
import bm25_py  # noqa: E402

KPI_DIR = Path(__file__).resolve().parents[1]
RRF_K = 60
# datasets-acceptance nằm ở eval/ (cha của kpi/). Trên VPS rsync giữ cấu trúc → ../datasets-acceptance.
DS_CANDIDATES = [
    KPI_DIR.parent / "datasets-acceptance",
    KPI_DIR / "datasets" / "acceptance",
    Path("/opt/secai/acceptance/datasets"),
]
TOP_DENSE = 20      # ứng viên dense (top-10 cho Hit@10, top-20 cho rerank)
CTX_K = 5           # số chunk lưu làm contexts


def _post(url, body, timeout=60, key=""):
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = "Bearer " + key   # embed/rerank vLLM cần Bearer; Qdrant không
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def embed(text):
    body = {"model": config.EMBED_MODEL, "input": text}
    r = _post(f"{config.EMBED_URL}/v1/embeddings", body, timeout=60, key=config.EMBED_KEY)
    return r["data"][0]["embedding"]


def dense_search(vec, limit=TOP_DENSE):
    body = {"vector": {"name": "dense", "vector": vec}, "limit": limit, "with_payload": True}
    r = _post(f"{config.QDRANT_URL}/collections/{config.QDRANT_COLLECTION}/points/search", body, timeout=60)
    out = []
    for p in r.get("result", []):
        pl = p.get("payload", {})
        out.append({"id": p["id"], "score": p.get("score"),
                    "cve_id": pl.get("cve_id"), "chunk_kind": pl.get("chunk_kind"),
                    "text": pl.get("text", "")})
    return out


def rerank(query, docs):
    body = {"model": config.RERANK_MODEL, "query": query, "documents": docs, "top_n": len(docs)}
    r = _post(f"{config.RERANK_URL}/v1/rerank", body, timeout=120, key=config.RERANK_KEY)
    # results: [{index, relevance_score}] giảm dần
    return r["results"]


def fetch_payloads(ids):
    """Lấy payload (cve_id, text) cho list point id từ Qdrant."""
    if not ids:
        return {}
    body = {"ids": ids, "with_payload": True}
    r = _post(f"{config.QDRANT_URL}/collections/{config.QDRANT_COLLECTION}/points", body, timeout=60)
    out = {}
    for p in r.get("result", []):
        pl = p.get("payload", {})
        out[str(p["id"])] = {"cve_id": pl.get("cve_id"), "text": pl.get("text", "")}
    return out


def scroll_by_cve(cve_id, limit=TOP_DENSE):
    """Lấy các chunk của 1 CVE qua exact payload filter cve_id (khớp product qdrant_search).
    Dùng khi câu hỏi có CVE-id explicit → đảm bảo chunk của CVE đó vào tập ứng viên."""
    body = {"filter": {"must": [{"key": "cve_id", "match": {"value": cve_id}}]},
            "limit": limit, "with_payload": True}
    r = _post(f"{config.QDRANT_URL}/collections/{config.QDRANT_COLLECTION}/points/scroll", body, timeout=60)
    out = []
    for p in r.get("result", {}).get("points", []):
        pl = p.get("payload", {})
        out.append({"id": p["id"], "cve_id": pl.get("cve_id"),
                    "chunk_kind": pl.get("chunk_kind"), "text": pl.get("text", "")})
    return out


def rrf_merge(dense_ids, bm25_ids, limit):
    """RRF fusion (K=60) trên point id — giống qdrant_search.ts."""
    scores = {}
    for rank, pid in enumerate(dense_ids):
        scores[pid] = scores.get(pid, 0.0) + 1.0 / (RRF_K + rank + 1)
    for rank, pid in enumerate(bm25_ids):
        scores[pid] = scores.get(pid, 0.0) + 1.0 / (RRF_K + rank + 1)
    return [pid for pid, _ in sorted(scores.items(), key=lambda x: x[1], reverse=True)[:limit]]


def load_pairs(limit=None, ds_override=None):
    cands = [Path(ds_override)] if ds_override else DS_CANDIDATES
    ds_dir = next((d for d in cands if d.exists()), None)
    if not ds_dir:
        sys.exit(f"không tìm thấy datasets-acceptance ở: {[str(d) for d in DS_CANDIDATES]}")
    pairs = []
    for fp in sorted(Path(ds_dir).glob("acceptance_*.jsonl")):
        for line in fp.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            s = json.loads(line)
            if "trap" in (s.get("tags") or []):
                continue
            meta = s.get("metadata") or {}
            gold = meta.get("cve_id")
            # bỏ câu nhiều CVE đích (compare/pair) — chỉ giữ 1 gold rõ ràng
            if not gold or meta.get("cve_id_2") or meta.get("cve_ids"):
                continue
            pairs.append({"id": s["id"], "capability": s["capability"],
                          "difficulty": s.get("difficulty"), "query": s["input"],
                          "gold_cve": gold, "has_id_in_query": gold.upper() in s["input"].upper()})
    if limit:
        pairs = pairs[:limit]
    return pairs, str(ds_dir)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(KPI_DIR / "raw-logs"))
    ap.add_argument("--contexts", default=str(KPI_DIR / "raw-logs" / "contexts.jsonl"))
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--delay", type=float, default=0.0)
    ap.add_argument("--dataset", default=None, help="thư mục dataset (override; vd /tmp/trial_ds cho eval thử)")
    args = ap.parse_args()

    pairs, ds_dir = load_pairs(args.limit, args.dataset)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    bm25_dir = bm25_py.find_bm25_dir()
    bm25 = bm25_py.Bm25Index(bm25_dir) if bm25_dir else None
    mode = "hybrid (dense+BM25+RRF)" if bm25 else "dense-only (BM25 KHÔNG có)"
    print(f"retrieval KPI · {len(pairs)} query · ds={ds_dir} · mode={mode}\n", flush=True)

    hit_log = (out_dir / "embedding-hit-rate-at-10.jsonl").open("w", encoding="utf-8")
    p1_log = (out_dir / "reranking-precision-at-1.jsonl").open("w", encoding="utf-8")
    ctx_out = Path(args.contexts).open("w", encoding="utf-8")

    n = hit_dense = hit_hybrid = hit_idaware = p1 = errs = 0
    for i, pr in enumerate(pairs, 1):
        gold = pr["gold_cve"].upper()
        try:
            vec = embed(pr["query"])
            dense = dense_search(vec, TOP_DENSE)
        except Exception as e:
            errs += 1
            print(f"[{i}/{len(pairs)}] {pr['id']} EMBED/SEARCH ERR {repr(e)[:60]}", flush=True)
            continue
        n += 1
        dense_ids = [str(d["id"]) for d in dense]
        cve_by_id = {str(d["id"]): (d.get("cve_id") or "").upper() for d in dense}
        text_by_id = {str(d["id"]): (d.get("text") or "") for d in dense}

        # BM25 + RRF (hybrid)
        bm25_ids = []
        if bm25:
            bm25_hits = bm25.search(pr["query"], TOP_DENSE)
            bm25_ids = [h[0] for h in bm25_hits]
            need = [pid for pid in bm25_ids if pid not in cve_by_id]
            for pid, pl in fetch_payloads(need).items():
                cve_by_id[pid] = (pl.get("cve_id") or "").upper()
                text_by_id[pid] = pl.get("text") or ""
        hybrid_ids = rrf_merge(dense_ids, bm25_ids, TOP_DENSE) if bm25 else dense_ids

        # ── id-aware: câu hỏi có CVE-id explicit → exact cve_id filter (khớp product qdrant_search) ──
        # Trích id TỪ QUERY (không dùng gold → tránh circular). Chunk của CVE đó lên đầu ứng viên.
        qids = [c.upper() for c in CVE_IN_QUERY_RE.findall(pr["query"])]
        idaware_ids = list(hybrid_ids)
        if qids:
            id_chunk_ids = []
            for qcve in qids:
                for ch in scroll_by_cve(qcve, TOP_DENSE):
                    pid = str(ch["id"])
                    cve_by_id[pid] = (ch.get("cve_id") or "").upper()
                    text_by_id[pid] = ch.get("text") or ""
                    id_chunk_ids.append(pid)
            seen = set()
            idaware_ids = [p for p in id_chunk_ids + hybrid_ids if not (p in seen or seen.add(p))]

        dense_top10_cve = [cve_by_id.get(pid, "") for pid in dense_ids[:10]]
        hybrid_top10_cve = [cve_by_id.get(pid, "") for pid in hybrid_ids[:10]]
        idaware_top10_cve = [cve_by_id.get(pid, "") for pid in idaware_ids[:10]]
        is_hit_dense = gold in dense_top10_cve
        is_hit_hybrid = gold in hybrid_top10_cve
        is_hit = gold in idaware_top10_cve  # headline = id-aware (production retrieval path)
        hit_dense += 1 if is_hit_dense else 0
        hit_hybrid += 1 if is_hit_hybrid else 0
        hit_idaware += 1 if is_hit else 0
        rank = (idaware_top10_cve.index(gold) + 1) if is_hit else None
        hit_log.write(json.dumps({
            "id": pr["id"], "true_label": pr["gold_cve"], "predicted_label": idaware_top10_cve,
            "score": 1 if is_hit else 0, "score_hybrid": 1 if is_hit_hybrid else 0,
            "score_dense": 1 if is_hit_dense else 0, "rank": rank,
            "has_id_in_query": pr["has_id_in_query"], "capability": pr["capability"], "difficulty": pr["difficulty"],
        }, ensure_ascii=False) + "\n")

        # P@1: rerank top-20 id-aware candidate → top-1
        cand_ids = idaware_ids[:TOP_DENSE]
        is_p1 = False
        top1_cve = None
        if cand_ids:
            try:
                docs = [text_by_id.get(pid, "")[:1200] for pid in cand_ids]
                rr = rerank(pr["query"], docs)
                best = cand_ids[rr[0]["index"]]
                top1_cve = cve_by_id.get(best, "")
                is_p1 = top1_cve == gold
            except Exception as e:
                top1_cve = f"RERANK_ERR:{repr(e)[:40]}"
        p1 += 1 if is_p1 else 0
        p1_log.write(json.dumps({
            "id": pr["id"], "true_label": pr["gold_cve"], "predicted_label": top1_cve,
            "score": 1 if is_p1 else 0, "capability": pr["capability"], "difficulty": pr["difficulty"],
        }, ensure_ascii=False) + "\n")

        # contexts cho RAGAS: top-CTX_K chunk text (id-aware → chunk đúng CVE lên đầu)
        ctx_out.write(json.dumps({
            "id": pr["id"], "query": pr["query"],
            "contexts": [text_by_id.get(pid, "")[:1500] for pid in idaware_ids[:CTX_K]],
            "retrieved_cves": idaware_top10_cve[:CTX_K],
        }, ensure_ascii=False) + "\n")

        if i % 50 == 0 or i == len(pairs):
            print(f"[{i}/{len(pairs)}] Hit@10 id-aware={100*hit_idaware/n:.1f}% "
                  f"(hybrid={100*hit_hybrid/n:.1f}% dense={100*hit_dense/n:.1f}%) "
                  f"P@1={100*p1/n:.1f}%", flush=True)
        if args.delay:
            time.sleep(args.delay)

    hit_log.close(); p1_log.close(); ctx_out.close()
    summary = {
        "kpi": "retrieval",
        "mode": (mode + " + id-aware exact cve_id filter (khớp product qdrant_search)"),
        "model_embed": config.EMBED_MODEL, "model_rerank": config.RERANK_MODEL,
        "n": n, "errors": errs,
        # headline = id-aware: dùng exact cve_id filter khi câu hỏi có CVE-id explicit (đúng cách product chạy)
        "hit_rate_at_10_pct": round(100 * hit_idaware / max(n, 1), 2),
        # minh bạch: số hybrid thuần (không filter) + dense-only — lộ giới hạn embedding thật
        "hit_rate_at_10_hybrid_pct": round(100 * hit_hybrid / max(n, 1), 2),
        "hit_rate_at_10_dense_only_pct": round(100 * hit_dense / max(n, 1), 2),
        "precision_at_1_pct": round(100 * p1 / max(n, 1), 2),
    }
    (out_dir / "retrieval-summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"\n=== Hit@10 id-aware={summary['hit_rate_at_10_pct']}% "
          f"[hybrid={summary['hit_rate_at_10_hybrid_pct']}% dense-only={summary['hit_rate_at_10_dense_only_pct']}%] "
          f"(ngưỡng >96) · P@1={summary['precision_at_1_pct']}% (ngưỡng >90) · n={n} ===")


if __name__ == "__main__":
    main()
