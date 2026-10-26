"""R2-FAITH98 (RAGAS Faithfulness) and R2-RELEV90 (RAGAS Answer Relevancy).

Judge LLM = config.JUDGE_MODEL @ config.JUDGE_BASE_URL (OpenRouter deepseek) — INDEPENDENT
of the model under test (Cyber-F1-AWQ). Answer-relevancy embeddings = config.RAGAS_EMBED_MODEL
(sentence-transformers, INDEPENDENT of sec-embedding).

retrieved_contexts = the REAL top-5 hybrid(dense+BM25 RRF, no oracle) chunks for that exact
query (see quality/common.py:hybrid_candidates), NOT a gold re-fetch by CVE-id. We do NOT use
the gateway's own SSE `tool_result` frames for this, even though the schema captures them:
empirically (verified live during recon), the production chat path pre-fetches KB grounding
into the PROMPT TEXT for any query naming a CVE/CWE/CAPEC/ATT&CK id (routes.py:_ground_chat_prompt,
added 2026-09-19 to fix a 46.9%-ungrounded / 58-63%-faithfulness bug) rather than emitting a
`tool_call`/`tool_result` pair — so `tool_results` is empty for almost every item in this gold
set and cannot serve as the context source. Reproducing the retrieval mechanism directly is the
task-authorized alternative ("reproduce production hybrid retrieval, or capture SSE tool_result").
"""
from __future__ import annotations

import json
import math

from .. import config, schema
from . import common

CTX_K = 5


def _contexts_for(query: str) -> tuple[list[str], str]:
    try:
        cand = common.hybrid_candidates(query, top_k=common.CANDIDATE_LIMIT)
        ctx = [cand["text_by_id"].get(pid, "")[:1500] for pid in cand["hybrid_ids"][:CTX_K]
               if cand["text_by_id"].get(pid)]
        return ctx, "ok"
    except Exception as e:  # noqa: BLE001
        return [], f"retrieval_error:{repr(e)[:120]}"


def run_ragas_kpis(answered: list[dict]) -> list[dict]:
    """``answered``: gateway answers for acceptance_indep_dinh_danh items, ALREADY collected
    by run.py (shared with entity_kpi.run_entity_kpis so each gold item costs one prod call)."""
    n_req = len(answered)
    n_blocked = sum(1 for a in answered if a["error"])
    usable = [a for a in answered if not a["error"] and (a["answer"] or "").strip()]

    samples = []
    ctx_status = {}
    for a in usable:
        ctx, status = _contexts_for(a["input"])
        ctx_status[a["id"]] = status
        if not ctx:
            continue
        samples.append({"id": a["id"], "user_input": a["input"], "response": a["answer"], "contexts": ctx})

    n_no_context = len(usable) - len(samples)

    log_path = config.RESULTS / "quality_faith_relev_items.jsonl"
    if not samples:
        (config.RESULTS / "quality_faith_relev_items.jsonl").write_text("", encoding="utf-8")
        notes = (f"0 samples had usable retrieved_contexts (n_gateway_errors={n_blocked}, "
                 f"n_no_context={n_no_context}) — RAGAS not run.")
        return [
            schema.make_result("R2-FAITH98", None, method="RAGAS Faithfulness", basis="", n=0,
                                evidence=str(log_path), notes=notes),
            schema.make_result("R2-RELEV90", None, method="RAGAS Answer Relevancy", basis="", n=0,
                                evidence=str(log_path), notes=notes),
        ]

    # ── RAGAS wiring: independent judge (OpenRouter deepseek) + independent embeddings ──
    from . import _ragas_compat
    _ragas_compat.ensure_langchain_community_vertexai_stub()
    from langchain_openai import ChatOpenAI
    from langchain_huggingface import HuggingFaceEmbeddings
    from ragas import SingleTurnSample, EvaluationDataset, evaluate
    from ragas.embeddings import LangchainEmbeddingsWrapper
    from ragas.llms import LangchainLLMWrapper
    from ragas.metrics import Faithfulness, ResponseRelevancy

    judge_lc = ChatOpenAI(base_url=config.JUDGE_BASE_URL, api_key=config.JUDGE_KEY,
                          model=config.JUDGE_MODEL, temperature=0, timeout=120, max_retries=2)
    judge = LangchainLLMWrapper(judge_lc)
    emb_lc = HuggingFaceEmbeddings(model_name=config.RAGAS_EMBED_MODEL)
    ragas_emb = LangchainEmbeddingsWrapper(emb_lc)

    ds = EvaluationDataset(samples=[
        SingleTurnSample(user_input=s["user_input"], response=s["response"], retrieved_contexts=s["contexts"])
        for s in samples
    ])
    try:
        result = evaluate(dataset=ds, metrics=[Faithfulness(llm=judge), ResponseRelevancy(llm=judge, embeddings=ragas_emb)],
                          show_progress=False)
        df = result.to_pandas()
    except Exception as e:  # noqa: BLE001
        notes = f"RAGAS evaluate() raised: {type(e).__name__}: {e}"
        return [
            schema.make_result("R2-FAITH98", None, method="RAGAS Faithfulness", basis="", n=0,
                                evidence=str(log_path), notes=notes),
            schema.make_result("R2-RELEV90", None, method="RAGAS Answer Relevancy", basis="", n=0,
                                evidence=str(log_path), notes=notes),
        ]

    with log_path.open("w", encoding="utf-8") as logf:
        for s, (_, row) in zip(samples, df.iterrows()):
            logf.write(json.dumps({
                "id": s["id"], "user_input": s["user_input"], "response": s["response"][:800],
                "n_contexts": len(s["contexts"]),
                "faithfulness": None if math.isnan(row.get("faithfulness", float("nan"))) else round(float(row["faithfulness"]), 4),
                "answer_relevancy": None if math.isnan(row.get("answer_relevancy", float("nan"))) else round(float(row["answer_relevancy"]), 4),
            }, ensure_ascii=False) + "\n")

    faith_vals = [v for v in df.get("faithfulness", []) if v is not None and not (isinstance(v, float) and math.isnan(v))]
    relev_vals = [v for v in df.get("answer_relevancy", []) if v is not None and not (isinstance(v, float) and math.isnan(v))]
    faith_pct = round(100 * sum(faith_vals) / len(faith_vals), 2) if faith_vals else None
    relev_pct = round(100 * sum(relev_vals) / len(relev_vals), 2) if relev_vals else None

    basis = (f"gateway :8131 /ai/chat/stream real answers, gold=acceptance_indep_dinh_danh, "
             f"n_requested={n_req}, n_gateway_errors={n_blocked}, n_usable_answers={len(usable)}, "
             f"n_no_context={n_no_context}, n_scored={len(samples)}; judge={config.JUDGE_MODEL}; "
             f"relevancy_embeddings={config.RAGAS_EMBED_MODEL}")
    notes_common = ("retrieved_contexts = reproduced production hybrid retrieval (dense+BM25 RRF, "
                     "no oracle) top-5 for the exact query — see module docstring for why SSE "
                     "tool_result was not usable as the context source.")

    return [
        schema.make_result("R2-FAITH98", faith_pct, method="RAGAS Faithfulness, judge=deepseek (independent)",
                            basis=basis, n=len(faith_vals), evidence=str(log_path), notes=notes_common,
                            extra={"n_nan_parse_fail": len(samples) - len(faith_vals)}),
        schema.make_result("R2-RELEV90", relev_pct,
                            method="RAGAS Answer Relevancy (ResponseRelevancy), judge=deepseek + "
                                   "embeddings=paraphrase-multilingual-MiniLM (independent)",
                            basis=basis, n=len(relev_vals), evidence=str(log_path), notes=notes_common,
                            extra={"n_nan_parse_fail": len(samples) - len(relev_vals)}),
    ]
