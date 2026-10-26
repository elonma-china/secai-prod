"""Chuẩn kết quả KPI dùng chung + hàm chấm đạt/rớt + gộp báo cáo.

Mỗi KPI trả về 1 dict theo make_result(). Ba nhóm (quality/performance/pii) mỗi nhóm
có 1 runner trả về list[dict]; run_all gộp lại thành results/kpi_v2_summary.json + báo cáo .md.
"""
from __future__ import annotations

import time

from . import config


def thr_str(kpi_id: str) -> str:
    spec = config.THRESHOLDS.get(kpi_id)
    if not spec:
        return ""
    op, bound = spec[0], spec[1]
    sym = {"gt": "> ", "ge": ">= ", "lt": "< ", "le": "<= ", "eq": "= "}.get(op)
    if op == "range":
        return f"{bound[0]}–{bound[1]}"
    return f"{sym}{bound}"


def eval_pass(kpi_id: str, value):
    """True/False nếu đo được; None nếu chưa đo được (framework) hoặc không có ngưỡng."""
    spec = config.THRESHOLDS.get(kpi_id)
    if spec is None or value is None:
        return None
    op, bound = spec[0], spec[1]
    try:
        if op == "gt":
            return value > bound
        if op == "ge":
            return value >= bound
        if op == "lt":
            return value < bound
        if op == "le":
            return value <= bound
        if op == "eq":
            return value == bound
        if op == "range":
            return bound[0] <= value <= bound[1]
    except TypeError:
        return None
    return None


def make_result(
    kpi_id: str,
    value,
    *,
    method: str,
    basis: str,
    n=None,
    evidence: str = "",
    notes: str = "",
    extra: dict | None = None,
    pass_override=None,
) -> dict:
    """Tạo 1 bản ghi KPI chuẩn.

    - value: số đo (None nếu chưa đo được -> pass=None, notes ghi rõ lý do TODO).
    - method: cách đo NÀY (vd 'RAGAS Faithfulness, judge=deepseek độc lập').
    - basis: căn cứ/nguồn dữ liệu (vd 'gateway :8131, gold=acceptance_indep_*, n=120').
    - extra: các số phụ (vd dense/hybrid, recall+precision, per-item) — luôn kèm để minh bạch.
    """
    spec = config.THRESHOLDS.get(kpi_id, ("", "", "", kpi_id, ""))
    unit, name, group = spec[2], spec[3], spec[4]
    return {
        "id": kpi_id,
        "name": name,
        "group": group,
        "threshold": thr_str(kpi_id),
        "value": value,
        "unit": unit,
        "pass": pass_override if pass_override is not None else eval_pass(kpi_id, value),
        "method": method,
        "basis": basis,
        "n": n,
        "evidence": evidence,
        "notes": notes,
        "extra": extra or {},
        "measured_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }


def write_summary(results: list[dict], path=None) -> str:
    """Ghi kpi_v2_summary.json + kpi_v2_report.md; trả về đường dẫn json."""
    import json

    path = path or (config.RESULTS / "kpi_v2_summary.json")
    payload = {
        "suite": "kpi_eval_v2",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "system_under_test": {
            "gateway": config.GATEWAY_URL,
            "llm_model": config.LLM_MODEL,
            "embed_model": config.EMBED_MODEL,
            "rerank_model": config.RERANK_MODEL,
        },
        "judge": {"model": config.JUDGE_MODEL, "base_url": config.JUDGE_BASE_URL,
                  "independent_of_sut": True},
        "n_kpis": len(results),
        "results": results,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    # báo cáo markdown gọn
    lines = ["# KPI Eval v2 — kết quả", "",
             f"- Sinh lúc: {payload['generated_at']}",
             f"- Model bị đo: `{config.LLM_MODEL}` · Judge độc lập: `{config.JUDGE_MODEL}`", "",
             "| KPI | Ngưỡng | Giá trị | Đạt | Cách đo |", "|---|---|---|---|---|"]
    badge = {True: "✅", False: "❌", None: "—"}
    for r in results:
        v = r["value"]
        vs = "—" if v is None else f"{v}{r['unit']}"
        lines.append(f"| {r['name']} ({r['id']}) | {r['threshold']}{r['unit']} | {vs} "
                     f"| {badge.get(r['pass'])} | {r['method']} |")
    (config.RESULTS / "kpi_v2_report.md").write_text("\n".join(lines), encoding="utf-8")
    return str(path)
