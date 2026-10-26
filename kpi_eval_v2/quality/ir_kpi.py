"""R2-EMB96 (Hit Rate@10) and R2-RERANK90 (Precision@1) — production-parity hybrid
retrieval (dense + BM25 RRF, then rerank) with NO exact-CVE-id oracle shortcut.

Differs from the old `kpi_bench/scripts/retrieval_kpi.py` in exactly one way: that script
detects a CVE-id in the query text and, when found, scrolls Qdrant for the gold CVE's own
chunks by exact `cve_id` filter and splices them to the front of the candidate list before
computing Hit@10/P@1 ("id-aware exact cve_id filter"). Since the vast majority of the gold
set's queries literally name the CVE-id in the question, that shortcut makes both KPIs
trivially near-100% regardless of how good the embedding/reranker actually are. This module
drops it entirely: candidates come ONLY from dense search + BM25 sparse search -> RRF.
"""
from __future__ import annotations

import json
import time

from .. import config, schema
from . import common


def _hit_at_10(cand: dict, ids: list[str], gold: str) -> bool:
    """gold cve_id present among the top-10 candidates AND on a chunk_kind=='core' point
    (matches the KPI definition: the CVE's own core record must surface, not just any
    context/related chunk that happens to mention it)."""
    for pid in ids[:10]:
        if cand["cve_by_id"].get(pid) == gold and cand["kind_by_id"].get(pid) == "core":
            return True
    return False


def run_ir_kpis() -> list[dict]:
    n_req = int(__import__("os").environ.get("KPI2_N_RETRIEVAL",
                __import__("os").environ.get("KPI2_N", "20")))
    pairs = common.load_retrieval_pairs("acceptance_indep_dinh_danh", limit=n_req)

    log_path = config.RESULTS / "quality_ir_items.jsonl"
    logf = log_path.open("w", encoding="utf-8")

    n = errs = 0
    hit_hybrid = hit_dense = p1_hybrid = 0
    bm25_source = "unknown"
    for i, pr in enumerate(pairs, 1):
        gold = pr["gold_cve"]
        try:
            cand = common.hybrid_candidates(pr["query"], top_k=common.CANDIDATE_LIMIT)
        except Exception as e:  # noqa: BLE001
            errs += 1
            logf.write(json.dumps({"id": pr["id"], "gold": gold, "error": repr(e)[:200]},
                                   ensure_ascii=False) + "\n")
            continue
        bm25_source = cand["bm25_source"]
        n += 1
        is_hit_hybrid = _hit_at_10(cand, cand["hybrid_ids"], gold)
        is_hit_dense = _hit_at_10(cand, cand["dense_ids"], gold)
        hit_hybrid += is_hit_hybrid
        hit_dense += is_hit_dense

        # P@1: rerank top-20 hybrid candidates (NO oracle prepend) -> is the #1 result the gold CVE?
        cand_ids = cand["hybrid_ids"][:common.CANDIDATE_LIMIT]
        top1_cve = None
        is_p1 = False
        if cand_ids:
            try:
                docs = [cand["text_by_id"].get(pid, "")[:1200] for pid in cand_ids]
                rr = common.rerank(pr["query"], docs)
                if rr:
                    best_pid = cand_ids[rr[0]["index"]]
                    top1_cve = cand["cve_by_id"].get(best_pid)
                    is_p1 = top1_cve == gold
            except Exception as e:  # noqa: BLE001
                top1_cve = f"RERANK_ERR:{repr(e)[:60]}"
        p1_hybrid += is_p1

        logf.write(json.dumps({
            "id": pr["id"], "gold": gold, "hybrid_top10_cve": [cand["cve_by_id"].get(p) for p in cand["hybrid_ids"][:10]],
            "dense_top10_cve": [cand["cve_by_id"].get(p) for p in cand["dense_ids"][:10]],
            "hit_hybrid": is_hit_hybrid, "hit_dense": is_hit_dense,
            "top1_cve_hybrid_reranked": top1_cve, "p1": is_p1,
        }, ensure_ascii=False) + "\n")
        if i % 10 == 0:
            logf.flush()
    logf.close()

    hit_pct = round(100 * hit_hybrid / n, 2) if n else None
    dense_pct = round(100 * hit_dense / n, 2) if n else None
    p1_pct = round(100 * p1_hybrid / n, 2) if n else None

    basis = (f"gateway retrieval NOT called; direct Qdrant({common.QDRANT_URL})+vast serving "
             f"(embed={config.EMBED_MODEL}, rerank={config.RERANK_MODEL}), collection="
             f"{common.QDRANT_COLLECTION}, gold=acceptance_indep_dinh_danh (single-cve_id items), n={n}"
             + (f", errors={errs}" if errs else ""))

    results = [
        schema.make_result(
            "R2-EMB96", hit_pct,
            method="Dense(sec-embedding)+BM25 sparse(fastembed Qdrant/bm25) -> RRF(k=60), "
                   "top-10, hit=gold cve_id on a chunk_kind='core' point. NO exact-CVE-id oracle.",
            basis=basis, n=n,
            evidence=str(log_path),
            notes="Headline = hybrid (RRF). dense_only reported in extra for transparency.",
            extra={"hit_rate_hybrid_pct": hit_pct, "hit_rate_dense_only_pct": dense_pct,
                   "bm25_source": bm25_source, "errors": errs},
        ),
        schema.make_result(
            "R2-RERANK90", p1_pct,
            method="rerank top-20 hybrid(non-oracle) candidates with sec-rerank; P@1 = "
                   "rerank's #1 result cve_id == gold.",
            basis=basis, n=n,
            evidence=str(log_path),
            notes="",
            extra={"errors": errs},
        ),
    ]
    return results
