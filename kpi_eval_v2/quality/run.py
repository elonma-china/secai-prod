"""run_quality() -> list[dict] — the 10 quality-group KPIs (R2-FAITH98 ... R2-RADAR90).

Sequential and modest by design (guardrail: :8131 is PRODUCTION): every gold item costs at
most ONE real /ai/chat/stream call (answers are collected once per gold set and reused across
KPIs that need them — e.g. the acceptance_indep_dinh_danh answers feed both the RAGAS pair and
the entity/hallucination pair). Sample size per gold set = env KPI2_N (default 20); retrieval-
only KPIs (EMB96/RERANK90, which never touch the production gateway, only Qdrant + vast
serving) may use a larger KPI2_N_RETRIEVAL (default = KPI2_N).
"""
from __future__ import annotations

import os

from .. import config, schema
from . import common, entity_kpi, ir_kpi, radar, ragas_kpi, task_kpi
from .answers import collect_answers


def run_quality() -> list[dict]:
    n_req = int(os.environ.get("KPI2_N", "20"))
    results: list[dict] = []

    # ── 1) EMB96 / RERANK90 — pure Qdrant+vast, no gateway call, no oracle ──────────────
    results += ir_kpi.run_ir_kpis()

    # ── 2) shared answer collection for FAITH/RELEV + ENTITY/HALLU (1 gateway call/item) ─
    gold_dd = common.load_gold_jsonl("acceptance_indep_dinh_danh")[:n_req]
    dd_items = [{"id": s["id"], "input": s["input"], "gold_cve": (s.get("metadata") or {}).get("cve_id")}
                for s in gold_dd]
    dd_answered = collect_answers(dd_items, message_key="input", cache_name="dinh_danh")

    results += ragas_kpi.run_ragas_kpis(dd_answered)
    results += entity_kpi.run_entity_kpis(dd_answered)

    # ── 3) task-rubric KPIs (each its own gold set / gateway calls) ────────────────────
    for fn in (task_kpi.run_sast, task_kpi.run_exploit, task_kpi.run_remediation):
        try:
            results.append(fn())
        except Exception as e:  # noqa: BLE001
            import traceback
            traceback.print_exc()
            kpi_id = {"run_sast": "R2-SAST85", "run_exploit": "R2-EXPLOIT85",
                      "run_remediation": "R2-REMED60"}[fn.__name__]
            results.append(schema.make_result(kpi_id, None, method="(lỗi runner)", basis="",
                                              notes=f"{type(e).__name__}: {e}"))

    # ── 4) RADAR90 — derived from the above ─────────────────────────────────────────────
    by_id = {r["id"]: r for r in results}
    results.append(radar.compute_radar(by_id))

    return results
