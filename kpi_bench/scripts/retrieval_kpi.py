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
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

# Trích CVE-id explicit trong câu hỏi (để dùng exact cve_id filter — khớp product qdrant_search).
CVE_IN_QUERY_RE = re.compile(r"CVE-\d{4}-\d{4,7}", re.IGNORECASE)

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import config  # noqa: E402
import bm25_py  # noqa: E402

KPI_DIR = Path(__file__).resolve().parents[1]
RRF_K = 60
# ── BM25 nửa lexical: dùng Qdrant SPARSE vector (KHỚP production gateway retrieval/bm25.py)
# thay cho bm25_py local. Encode query bằng FastEmbed Qdrant/bm25 rồi search sparse trên Qdrant.
# Cần fastembed trong env; thiếu -> tự fallback bm25_py local (kém khớp) rồi dense-only.
BM25_VECTOR = os.environ.get("QDRANT_BM25_VECTOR", "bm25")
BM25_MODEL = os.environ.get("BM25_MODEL", "Qdrant/bm25")
BM25_LOCAL_MODEL_DIR = os.environ.get(
    "BM25_LOCAL_MODEL_DIR", "/home/ict/AIEngines/secai/secai-data/_state/bm25_local_model")
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
    # knowledge chunks (kind cheatsheet/requirement/mitigation) share this collection: acceptance KPIs measure CVE retrieval only
    body = {"filter": {"must_not": [{"key": "kind", "match": {"any": ["cheatsheet", "requirement", "mitigation"]}}]}, "vector": {"name": "dense", "vector": vec}, "limit": limit, "with_payload": True}
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


class QdrantSparseBM25:
    """Nửa lexical BM25 qua Qdrant SPARSE vector — KHỚP production (gateway retrieval/bm25.py):
    FastEmbed(Qdrant/bm25) encode query -> Qdrant /points/search trên sparse vector 'bm25'.
    Thay bm25_py local (index + tokenizer/scoring riêng, lệch production). Encode fastembed
    serialize bằng 1 khoá (model không đảm bảo thread-safe); phần HTTP dense vẫn song song."""

    def __init__(self):
        import threading
        self._lock = threading.Lock()
        self._model = None

    def _encode(self, query):
        text = (query or "").strip()
        if not text:
            return None
        with self._lock:
            if self._model is None:
                from fastembed import SparseTextEmbedding
                kwargs = {}
                if os.path.isdir(BM25_LOCAL_MODEL_DIR):
                    kwargs["specific_model_path"] = BM25_LOCAL_MODEL_DIR
                self._model = SparseTextEmbedding(model_name=BM25_MODEL, **kwargs)
            emb = next(iter(self._model.embed([text])))
        indices = [int(i) for i in emb.indices]
        values = [float(v) for v in emb.values]
        return {"indices": indices, "values": values} if indices else None

    def search(self, query, top_k):
        sp = self._encode(query)
        if not sp:
            return []
        # knowledge chunks (kind cheatsheet/requirement/mitigation) share this collection: acceptance KPIs measure CVE retrieval only
        body = {"filter": {"must_not": [{"key": "kind", "match": {"any": ["cheatsheet", "requirement", "mitigation"]}}]}, "vector": {"name": BM25_VECTOR, "vector": sp}, "limit": top_k, "with_payload": False}
        r = _post(f"{config.QDRANT_URL}/collections/{config.QDRANT_COLLECTION}/points/search", body, timeout=30)
        return [(str(x["id"]), float(x.get("score", 0.0)))
                for x in r.get("result", []) if x.get("score", 0) > 0]


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


def _rerank_p1(query, cand_ids, text_by_id, cve_by_id, gold):
    """Rerank 1 pool cand_ids (đã cắt top-20) → (is_p1, top1_cve). Dùng chung cho id-aware/
    hybrid/dense-only P@1 (chỉ khác pool truyền vào)."""
    if not cand_ids:
        return False, None
    try:
        docs = [text_by_id.get(pid, "")[:1200] for pid in cand_ids]
        rr = rerank(query, docs)
        best = cand_ids[rr[0]["index"]]
        top1_cve = cve_by_id.get(best, "")
        return (top1_cve == gold), top1_cve
    except Exception as e:
        return False, f"RERANK_ERR:{repr(e)[:40]}"


def process_one(pr, bm25):
    """Toàn bộ gọi mạng (embed/Qdrant/BM25-local/rerank) cho 1 câu — chạy trong worker thread.
    Không đụng file/counter chung (những cái đó ở lại main thread, xem main())."""
    gold = pr["gold_cve"].upper()
    try:
        vec = embed(pr["query"])
        dense = dense_search(vec, TOP_DENSE)
    except Exception as e:
        return {"pr": pr, "gold": gold, "ok": False, "err": repr(e)[:60]}

    dense_ids = [str(d["id"]) for d in dense]
    cve_by_id = {str(d["id"]): (d.get("cve_id") or "").upper() for d in dense}
    text_by_id = {str(d["id"]): (d.get("text") or "") for d in dense}

    # BM25 + RRF (hybrid) — BM25 tra cứu local (thread-safe, chỉ đọc), chỉ fetch_payloads gọi mạng.
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
    is_hit = gold in idaware_top10_cve
    rank = (idaware_top10_cve.index(gold) + 1) if is_hit else None

    # P@1 — SỐ CŨ (headline precision_at_1_pct, GIỮ NGUYÊN): rerank top-20 id-aware candidate.
    # SỐ MỚI (cách đo chuẩn, additive, không đổi số cũ): rerank thêm trên pool hybrid/dense-only
    # (không có oracle id-filter) → precision_at_1_hybrid_pct / precision_at_1_dense_pct.
    # Cache theo tuple(cand_ids): khi 2 pool trùng nhau (câu không có CVE-id explicit →
    # idaware_ids==hybrid_ids; hoặc không có BM25 → hybrid_ids==dense_ids) thì KHÔNG rerank
    # 2 lần cho cùng 1 pool (tránh nhân 3 số lượt gọi rerank network).
    idaware_cand = idaware_ids[:TOP_DENSE]
    hybrid_cand = hybrid_ids[:TOP_DENSE]
    dense_cand = dense_ids[:TOP_DENSE]
    _p1_cache = {}

    def _p1(cand_ids):
        key = tuple(cand_ids)
        if key not in _p1_cache:
            _p1_cache[key] = _rerank_p1(pr["query"], cand_ids, text_by_id, cve_by_id, gold)
        return _p1_cache[key]

    is_p1, top1_cve = _p1(idaware_cand)                    # SỐ CŨ — headline precision_at_1_pct
    is_p1_hybrid, top1_cve_hybrid = _p1(hybrid_cand)       # MỚI — cách đo chuẩn (hybrid, không oracle)
    is_p1_dense, top1_cve_dense = _p1(dense_cand)          # MỚI — cách đo chuẩn (dense-only)

    return {
        "pr": pr, "gold": gold, "ok": True,
        "idaware_top10_cve": idaware_top10_cve, "is_hit": is_hit, "is_hit_hybrid": is_hit_hybrid,
        "is_hit_dense": is_hit_dense, "rank": rank, "is_p1": is_p1, "top1_cve": top1_cve,
        "is_p1_hybrid": is_p1_hybrid, "top1_cve_hybrid": top1_cve_hybrid,
        "is_p1_dense": is_p1_dense, "top1_cve_dense": top1_cve_dense,
        "text_by_id": text_by_id, "idaware_ids": idaware_ids,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(KPI_DIR / "raw-logs"))
    ap.add_argument("--contexts", default=str(KPI_DIR / "raw-logs" / "contexts.jsonl"))
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--delay", type=float, default=0.0)
    ap.add_argument("--dataset", default=None, help="thư mục dataset (override; vd /tmp/trial_ds cho eval thử)")
    ap.add_argument("--workers", type=int, default=16, help="số luồng song song embed/Qdrant/rerank (mặc định 16, giống mức đã dùng ổn ở faith_judge.py/llm_kpi.py)")
    args = ap.parse_args()

    pairs, ds_dir = load_pairs(args.limit, args.dataset)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    # Ưu tiên Qdrant SPARSE (khớp production) → fallback bm25_py local → dense-only.
    bm25 = None
    bm25_source = "none (dense-only)"
    try:
        _sp = QdrantSparseBM25()
        _sp.search("healthcheck log4shell", 1)  # ép nạp model + xác nhận Qdrant sparse chạy
        bm25 = _sp
        bm25_source = f"qdrant_sparse (vector=\'{BM25_VECTOR}\', model={BM25_MODEL}) — khớp production"
    except Exception as e:  # noqa: BLE001
        print(f"[BM25] Qdrant sparse không dùng được ({repr(e)[:90]}) → thử bm25_py local...", flush=True)
        try:
            _d = bm25_py.find_bm25_dir()
            if _d:
                bm25 = bm25_py.Bm25Index(_d)
                bm25_source = f"local_bm25 ({_d}) — FALLBACK, KHÔNG khớp production"
        except Exception:  # noqa: BLE001
            bm25 = None
    mode = f"hybrid (dense + {bm25_source} + RRF)" if bm25 else "dense-only (không có BM25)"
    print(f"retrieval KPI · {len(pairs)} query · ds={ds_dir} · mode={mode}\n", flush=True)

    hit_log = (out_dir / "embedding-hit-rate-at-10.jsonl").open("w", encoding="utf-8")
    p1_log = (out_dir / "reranking-precision-at-1.jsonl").open("w", encoding="utf-8")
    ctx_out = Path(args.contexts).open("w", encoding="utf-8")

    n = hit_dense = hit_hybrid = hit_idaware = p1 = p1_hybrid = p1_dense = errs = 0
    # Gọi mạng (embed/Qdrant/rerank) song song trên args.workers luồng — ex.map() trả kết quả
    # đúng thứ tự pairs dù việc xử lý chạy đồng thời, nên phần đếm/ghi log dưới đây vẫn ở main
    # thread, KHÔNG cần lock (giống pattern llm_kpi.py judge_relevance / faith_judge.py).
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        results_iter = ex.map(lambda pr: process_one(pr, bm25), pairs)
        for i, (pr, res) in enumerate(zip(pairs, results_iter), 1):
            if not res["ok"]:
                errs += 1
                print(f"[{i}/{len(pairs)}] {pr['id']} EMBED/SEARCH ERR {res['err']}", flush=True)
                if args.delay:
                    time.sleep(args.delay)
                continue
            n += 1
            gold = res["gold"]
            idaware_top10_cve = res["idaware_top10_cve"]
            is_hit, is_hit_hybrid, is_hit_dense = res["is_hit"], res["is_hit_hybrid"], res["is_hit_dense"]
            hit_dense += 1 if is_hit_dense else 0
            hit_hybrid += 1 if is_hit_hybrid else 0
            hit_idaware += 1 if is_hit else 0
            hit_log.write(json.dumps({
                "id": pr["id"], "true_label": pr["gold_cve"], "predicted_label": idaware_top10_cve,
                "score": 1 if is_hit else 0, "score_hybrid": 1 if is_hit_hybrid else 0,
                "score_dense": 1 if is_hit_dense else 0, "rank": res["rank"],
                "has_id_in_query": pr["has_id_in_query"], "capability": pr["capability"], "difficulty": pr["difficulty"],
            }, ensure_ascii=False) + "\n")

            p1 += 1 if res["is_p1"] else 0
            p1_hybrid += 1 if res["is_p1_hybrid"] else 0
            p1_dense += 1 if res["is_p1_dense"] else 0
            p1_log.write(json.dumps({
                "id": pr["id"], "true_label": pr["gold_cve"], "predicted_label": res["top1_cve"],
                # SỐ CŨ (headline precision_at_1_pct, GIỮ NGUYÊN):
                "score": 1 if res["is_p1"] else 0,
                # MỚI, additive — cách đo chuẩn (không oracle id-filter):
                "score_hybrid": 1 if res["is_p1_hybrid"] else 0,
                "predicted_label_hybrid": res["top1_cve_hybrid"],
                "score_dense": 1 if res["is_p1_dense"] else 0,
                "predicted_label_dense": res["top1_cve_dense"],
                "capability": pr["capability"], "difficulty": pr["difficulty"],
            }, ensure_ascii=False) + "\n")

            # contexts cho RAGAS: top-CTX_K chunk text (id-aware → chunk đúng CVE lên đầu)
            text_by_id = res["text_by_id"]; idaware_ids = res["idaware_ids"]
            ctx_out.write(json.dumps({
                "id": pr["id"], "query": pr["query"],
                "contexts": [text_by_id.get(pid, "")[:1500] for pid in idaware_ids[:CTX_K]],
                "retrieved_cves": idaware_top10_cve[:CTX_K],
            }, ensure_ascii=False) + "\n")

            if i % 50 == 0 or i == len(pairs):
                print(f"[{i}/{len(pairs)}] Hit@10 id-aware={100*hit_idaware/n:.1f}% "
                      f"(hybrid={100*hit_hybrid/n:.1f}% dense={100*hit_dense/n:.1f}%) "
                      f"P@1 id-aware={100*p1/n:.1f}% (hybrid={100*p1_hybrid/n:.1f}% dense={100*p1_dense/n:.1f}%)", flush=True)
            if args.delay:
                time.sleep(args.delay)

    hit_log.close(); p1_log.close(); ctx_out.close()
    summary = {
        "kpi": "retrieval",
        "mode": (mode + " + id-aware exact cve_id filter (khớp product qdrant_search)"),
        "bm25_source": bm25_source,
        "model_embed": config.EMBED_MODEL, "model_rerank": config.RERANK_MODEL,
        "n": n, "errors": errs,
        # ── SỐ CŨ, GIỮ NGUYÊN Ý NGHĨA/GIÁ TRỊ (theo yêu cầu additive — không đổi số cũ) ──
        # headline = id-aware: dùng exact cve_id filter khi câu hỏi có CVE-id explicit (đúng cách product chạy)
        "hit_rate_at_10_pct": round(100 * hit_idaware / max(n, 1), 2),
        "precision_at_1_pct": round(100 * p1 / max(n, 1), 2),
        # ── MỚI, ADDITIVE — cách đo CHUẨN NGÀNH (TREC/MTEB Hit@k, RRF hybrid): KHÔNG có oracle
        # exact-cve id-filter injection vào candidate pool; phản ánh năng lực retrieval thật ──
        "hit_rate_at_10_hybrid_pct": round(100 * hit_hybrid / max(n, 1), 2),
        "hit_rate_at_10_dense_only_pct": round(100 * hit_dense / max(n, 1), 2),
        "precision_at_1_hybrid_pct": round(100 * p1_hybrid / max(n, 1), 2),
        "precision_at_1_dense_pct": round(100 * p1_dense / max(n, 1), 2),
        "headline_basis": (
            "hit_rate_at_10_pct/precision_at_1_pct = id-aware (oracle exact-cve_id filter chèn "
            "trước khi rank — SỐ CŨ, giữ nguyên theo yêu cầu, KHÔNG phải năng lực retrieval thuần). "
            "Cách đo CHUẨN (TREC/MTEB Hit@k, không oracle id-filter) = "
            "hit_rate_at_10_hybrid_pct/hit_rate_at_10_dense_only_pct + "
            "precision_at_1_hybrid_pct/precision_at_1_dense_pct (MỚI, additive)."
        ),
    }
    (out_dir / "retrieval-summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"\n=== Hit@10 id-aware={summary['hit_rate_at_10_pct']}% "
          f"[hybrid={summary['hit_rate_at_10_hybrid_pct']}% dense-only={summary['hit_rate_at_10_dense_only_pct']}%] "
          f"(ngưỡng >96) · P@1 id-aware={summary['precision_at_1_pct']}% "
          f"[hybrid={summary['precision_at_1_hybrid_pct']}% dense={summary['precision_at_1_dense_pct']}%] "
          f"(ngưỡng >90) · n={n} ===")


if __name__ == "__main__":
    main()
