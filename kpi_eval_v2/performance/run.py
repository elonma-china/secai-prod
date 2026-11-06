"""Performance group runner — exposes ``run_performance() -> list[dict]`` per the
run_all contract. Every number is measured live against the real gateway (:8131), the
real raw LLM (:13905), and the real embed/rerank servers — never estimated from chars
or echoed from an input parameter. See each submodule's docstring for method + honesty
notes (esp. ``load_ramp.py`` for the prod-safety guard and ``onhost.py`` for the
on-host-vs-WAN distinction).
"""
from __future__ import annotations

import json

from .. import config, schema
from . import chatflow, ctx_needle, llm_tps, load_ramp, onhost


def run_performance() -> list[dict]:
    results: list[dict] = []
    ev_path = config.RESULTS / "performance_v2_evidence.jsonl"
    evidence_rows: list[dict] = []

    def _evidence(tag: str, payload: dict) -> None:
        evidence_rows.append({"kpi_group": tag, **payload})

    # ── R2-RESP15 + R2-TTFT + R2-CHATFLOW — gateway :8131, MODEST sequential queries ──
    try:
        resp = chatflow.measure_resp15()
        _evidence("resp15", resp)
        results.append(schema.make_result(
            "R2-RESP15", resp["p50_ms"],
            method="p50 of non-streaming POST /ai/chat full response, sequential real queries",
            basis=f"gateway={config.GATEWAY_URL}, n={resp['n']} (n_ok={resp['n_ok']}), "
                  f"queries=performance/queries.py",
            n=resp["n_ok"], evidence=str(ev_path),
            extra={"p95_ms": resp["p95_ms"], "mean_ms": resp["mean_ms"]}))
    except Exception as e:
        results.append(schema.make_result("R2-RESP15", None, method="(error)", basis="",
                                           notes=f"{type(e).__name__}: {e}"))

    try:
        tc = chatflow.measure_ttft_and_chatflow()
        _evidence("ttft_chatflow", tc)
        results.append(schema.make_result(
            "R2-TTFT", tc["ttft_p50_ms"],
            method="p50 time-to-first-SSE-token, POST /ai/chat/stream",
            basis="client wall-clock (incl RTT + RAG retrieval + guard layers)",
            n=tc["n_ok"], evidence=str(ev_path), extra={"ttft_p95_ms": tc["ttft_p95_ms"]}))
        results.append(schema.make_result(
            "R2-CHATFLOW", tc["chatflow_tok_s_p50"],
            method="end-to-end decode tok/s through gateway SSE: completion tokens counted by "
                   "tokenizing the reassembled final answer with the REAL model tokenizer "
                   "(vLLM native /tokenize — the gateway's own SSE carries no usage block)",
            basis=f"gateway={config.GATEWAY_URL}, basis=vLLM /tokenize on final SSE text "
                  f"(real tokenizer, not char/4 or tiktoken approx)",
            n=tc["n_ok"], evidence=str(ev_path), extra={"tok_s_mean": tc["chatflow_tok_s_mean"]}))
    except Exception as e:
        for kid in ("R2-TTFT", "R2-CHATFLOW"):
            results.append(schema.make_result(kid, None, method="(error)", basis="",
                                               notes=f"{type(e).__name__}: {e}"))

    # ── R2-TPS + R2-LLMTPS28 — raw LLM :13905, REAL usage.completion_tokens ──
    try:
        tps = llm_tps.measure()
        _evidence("llm_tps", tps)
        method = ("decode tok/s from usage.completion_tokens (stream_options.include_usage), "
                   "generation window = first content delta -> stream end (excludes TTFT/prefill)")
        basis = f"raw LLM={config.LLM_BASE_URL}, model={config.LLM_MODEL}, n={tps['n']} prompts"
        results.append(schema.make_result("R2-TPS", tps["tok_s_p50"], method=method, basis=basis,
                                           n=tps["n_ok"], evidence=str(ev_path),
                                           extra={"mean": tps["tok_s_mean"], "min": tps["tok_s_min"],
                                                  "max": tps["tok_s_max"]}))
        results.append(schema.make_result("R2-LLMTPS28", tps["tok_s_p50"], method=method, basis=basis,
                                           n=tps["n_ok"], evidence=str(ev_path),
                                           extra={"mean": tps["tok_s_mean"]}))
    except Exception as e:
        for kid in ("R2-TPS", "R2-LLMTPS28"):
            results.append(schema.make_result(kid, None, method="(error)", basis="",
                                               notes=f"{type(e).__name__}: {e}"))

    # ── R2-CTX16K — needle-in-haystack, raw LLM ──
    try:
        ctx = ctx_needle.measure()
        _evidence("ctx_needle", ctx)
        notes = ""
        if ctx.get("served_max_model_len") and ctx["served_max_model_len"] < 16000:
            notes = f"served max_model_len={ctx['served_max_model_len']} < 16000 target"
        results.append(schema.make_result(
            "R2-CTX16K", ctx["effective_context_tokens"],
            method="needle-in-haystack: unique fact at ~50% depth, prompt length measured "
                   "exactly via vLLM /tokenize; effective context = largest tested length "
                   "still answered correctly",
            basis=f"raw LLM={config.LLM_BASE_URL}, model={config.LLM_MODEL}, "
                  f"served_max_model_len={ctx.get('served_max_model_len')}",
            n=len(ctx["steps"]), evidence=str(ev_path), notes=notes,
            extra={"steps": [{"target_tokens": s["target_tokens"],
                               "actual_prompt_tokens": s["actual_prompt_tokens"],
                               "found": s["found"]} for s in ctx["steps"]]}))
    except Exception as e:
        results.append(schema.make_result("R2-CTX16K", None, method="(error)", basis="",
                                           notes=f"{type(e).__name__}: {e}"))

    # ── R2-RPS + R2-CCU — raw LLM ramp, prod-safety guarded ──
    try:
        load = load_ramp.measure()
        _evidence("load_ramp", load)
        ceiling = load["sustained_ceiling"]
        full = load["mode"] == "full_ramp"
        if ceiling:
            rps_val = ceiling["rps"]
            ccu_val = ceiling["concurrency"]
        else:
            rps_val = ccu_val = None
        guard_notes = ("" if full else
                       "SMALL validation only (prod-safety guard, default) — this is what was "
                       "cleanly validated, NOT a certified ceiling (the real ceiling may be higher); "
                       "set KPI2_LOAD=1 to run the full ramp in a maintenance window")
        results.append(schema.make_result(
            "R2-RPS", rps_val,
            method="sustained RPS at the highest clean concurrency level in the ramp "
                   "(raw LLM, short completions)",
            basis=f"raw LLM={config.LLM_BASE_URL}; {load['guard_note']}",
            n=len(load["steps"]), evidence=str(ev_path),
            extra={"steps": load["steps"]}, notes=guard_notes))
        results.append(schema.make_result(
            "R2-CCU", ccu_val,
            method="highest concurrency level with 0 errors and p95 latency within bound "
                   "(raw LLM, short completions) — a REAL ramp, not an echo of the input concurrency",
            basis=f"raw LLM={config.LLM_BASE_URL}; {load['guard_note']}",
            n=len(load["steps"]), evidence=str(ev_path),
            extra={"steps": load["steps"]}, notes=guard_notes))
    except Exception as e:
        for kid in ("R2-RPS", "R2-CCU"):
            results.append(schema.make_result(kid, None, method="(error)", basis="",
                                               notes=f"{type(e).__name__}: {e}"))

    # ── R2-EMB1200 + R2-RERANK180 — on-host via SSH, WAN fallback ──
    try:
        oh = onhost.measure_onhost()
        _evidence("onhost", oh or {})
        oh_ok = oh and not oh.get("_error")
        if oh_ok:
            best_embed = max((v["embeddings_per_s"] for v in oh["embed"].values() if v.get("embeddings_per_s")),
                              default=None)
            best_rerank = max((v["docs_per_s"] for v in oh["rerank"].values() if v.get("docs_per_s")),
                               default=None)
            results.append(schema.make_result(
                "R2-EMB1200", best_embed,
                method="batched+concurrent /v1/embeddings throughput, measured ON-HOST via SSH "
                       "(loopback on the serving A100 box, bypassing WAN)",
                basis=f"on-host target={oh['_target']}, model={config.EMBED_MODEL}",
                evidence=str(ev_path), extra={"by_concurrency": oh["embed"]}))
            results.append(schema.make_result(
                "R2-RERANK180", best_rerank,
                method="concurrent /v1/rerank throughput, measured ON-HOST via SSH "
                       "(loopback on the serving A100 box, bypassing WAN)",
                basis=f"on-host target={oh['_target']}, model={config.RERANK_MODEL}",
                evidence=str(ev_path), extra={"by_concurrency": oh["rerank"]}))
        else:
            wan_embed = onhost.measure_wan_embed()
            wan_rerank = onhost.measure_wan_rerank()
            _evidence("onhost_wan_fallback", {"embed": wan_embed, "rerank": wan_rerank,
                                              "ssh_error": (oh or {}).get("_error")})
            results.append(schema.make_result(
                "R2-EMB1200", None,
                method="on-host SSH unavailable; WAN measurement only (reads ~5x low vs on-host, "
                       "cannot certify the on-host threshold) — see performance/bench_onhost.sh "
                       "to run manually on the box",
                basis=f"WAN (~5x low); ssh_error={(oh or {}).get('_error')}",
                extra={"wan_embeddings_per_s": wan_embed.get("embeddings_per_s")},
                notes="value withheld: WAN cannot certify an on-host threshold"))
            results.append(schema.make_result(
                "R2-RERANK180", None,
                method="on-host SSH unavailable; WAN measurement only (reads ~5x low vs on-host, "
                       "cannot certify the on-host threshold) — see performance/bench_onhost.sh "
                       "to run manually on the box",
                basis=f"WAN (~5x low); ssh_error={(oh or {}).get('_error')}",
                extra={"wan_docs_per_s": wan_rerank.get("docs_per_s")},
                notes="value withheld: WAN cannot certify an on-host threshold"))
    except Exception as e:
        for kid in ("R2-EMB1200", "R2-RERANK180"):
            results.append(schema.make_result(kid, None, method="(error)", basis="",
                                               notes=f"{type(e).__name__}: {e}"))

    with ev_path.open("w", encoding="utf-8") as f:
        for row in evidence_rows:
            f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")

    return results
