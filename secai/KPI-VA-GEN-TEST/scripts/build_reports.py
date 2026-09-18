#!/usr/bin/env python3
"""build_reports.py — [LOCAL] Gộp raw-logs/summaries → báo cáo hợp đồng.

Sinh:
  reports/ai-task-kpi-report.md, embedding-report.md, reranking-report.md, llm-kpi-report.md,
          performance-report.md, sanitization-report.md, radar.png, radar-data.json
  raw-logs/kpi-summary.json, kpi-summary.xlsx, run-manifest.json

Đọc các *-summary.json do các script KPI sinh ra. Thiếu cái nào → ghi "chưa đo".
Usage: python3 build_reports.py [--logs ../raw-logs] [--reports ../reports] [--model deepseek-v4-flash] [--date YYYY-MM-DD]
"""
import argparse
import json
import math
import subprocess
import sys
from pathlib import Path

KPI_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(KPI_DIR))
import config  # noqa: E402


def load(logs, name):
    p = Path(logs) / name
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return None
    return None


def fmt(v, suffix="%"):
    return f"{v}{suffix}" if v is not None else "—"


def badge(ok):
    return "✅ ĐẠT" if ok else ("❌ CHƯA ĐẠT" if ok is False else "⏳ chưa đo")


# Khuyến nghị cải thiện theo từng KPI (nền từ docs/BAO-CAO-KPI-AI.md §5).
RECS = {
    "R-KPI-EMB96": "Xác nhận serving embed == bge-m3 (khớp index sau re-index); tăng top-k ứng viên; tối ưu trọng số BM25 cho từ khoá kỹ thuật.",
    "R-KPI-RERANK90": "P@1 bị chặn bởi recall ứng viên — cải thiện Hit@10 (embedding đa ngữ) sẽ kéo P@1 lên.",
    "R-KPI-FAITH98": "Đưa đủ context agent thực dùng (qdrant+neo4j+poc) vào phép chấm thay vì chỉ chunk gold; cân nhắc prompt agent bám sát nguồn/trích dẫn.",
    "R-KPI-RELEV90": "Đo cosine bằng embedding đa ngữ (bge-m3) cho tiếng Việt, hoặc dùng RAGAS answer_relevancy (LLM sinh câu hỏi ngược).",
    "R-KPI-ENTITY100": "Mở rộng tập grounding sang toàn bộ NVD để CVE thật ngoài KB không bị tính sai.",
    "R-KPI-HALLU2": "Đã đạt — duy trì trap-set chống bịa, theo dõi định kỳ.",
    "R-SEC-PII": "Bổ sung hard-negative cho loại Precision thấp; tinh chỉnh regex/entropy theo từng loại.",
    "R-LLM-SAST85": "Đã đạt — mở rộng CWE coverage nếu cần.",
    "R-LLM-EXPLOIT85": "Đã đạt.",
    "R-LLM-REMED60": "Đã đạt.",
    "R-KPI-RADAR90": "Tăng 2 trục thấp (Faithfulness/Relevance) sẽ kéo diện tích phủ.",
    "R-CTRL-RESP15": "Giới hạn độ dài trả lời cho truy vấn đơn giản; model nhanh hơn / on-prem A100; stream + cache câu lặp.",
}


def _short(v):
    if v is None:
        return ""
    return json.dumps(v, ensure_ascii=False)[:200]


def gather_failed_samples(logs):
    """Gom mọi record FAIL từ raw-logs *.jsonl (trừ sanitization — xử lý riêng từ summary)."""
    rows = []
    skip = {"answers.jsonl", "contexts.jsonl", "performance-per-request.jsonl", "sanitization.jsonl"}
    for p in sorted(Path(logs).glob("*.jsonl")):
        if p.name in skip:
            continue
        is_hallu = "hallucination" in p.name
        for line in p.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                r = json.loads(line)
            except Exception:
                continue
            score, passed, bad = r.get("score"), r.get("pass"), r.get("bad")
            if is_hallu:
                failed = isinstance(score, (int, float)) and score >= 1
            else:
                failed = (passed is False) or (isinstance(score, (int, float)) and score == 0) or (bad not in (None, [], ""))
            if failed:
                rows.append({"source": p.name, "id": r.get("id"),
                             "label": r.get("capability") or r.get("kind") or "",
                             "difficulty": r.get("difficulty") or "",
                             "true_label": _short(r.get("true_label")),
                             "predicted_label": _short(r.get("predicted_label")),
                             "bad": _short(bad), "score": score})
    return rows


def write_failed_samples_xlsx(logs, reports, sani):
    """failed-samples.xlsx (§5.2): gom mẫu fail từ mọi raw-log + FP/FN sanitization."""
    rows = gather_failed_samples(logs)
    for t, exs in (sani.get("false_negatives") or {}).items():
        for e in exs:
            rows.append({"source": "sanitization (FN)", "id": e.get("id"), "label": t,
                         "difficulty": "", "true_label": _short(e.get("value")), "predicted_label": "(miss)", "bad": "", "score": 0})
    for t, exs in (sani.get("false_positives") or {}).items():
        for e in exs:
            rows.append({"source": "sanitization (FP)", "id": e.get("id"), "label": t,
                         "difficulty": e.get("kind", ""), "true_label": "(none)", "predicted_label": _short(e.get("value")), "bad": "", "score": 0})
    try:
        import pandas as pd
        with pd.ExcelWriter(Path(reports) / "failed-samples.xlsx", engine="openpyxl") as xw:
            pd.DataFrame(rows or [{"source": "(none)", "id": "", "label": "", "difficulty": "",
                                   "true_label": "", "predicted_label": "", "bad": "", "score": ""}]).to_excel(
                xw, sheet_name="Failed Samples", index=False)
        return len(rows), True
    except Exception as e:
        (Path(reports) / "failed-samples-error.txt").write_text(repr(e))
        return len(rows), False


def write_improvement_plan(reports, K, perf, hdr):
    """improvement-plan.md (§5.1): phân tích KPI chưa đạt + kế hoạch cải thiện."""
    failed = [(c, v) for c, v in K.items() if v["pass"] is False]
    lines = ["# Kế hoạch cải thiện & phân tích lỗi (improvement-plan)\n", hdr,
             f"\n**{len(failed)}/{len(K)} KPI chưa đạt ngưỡng.** Phân tích nguyên nhân + hướng xử lý dưới đây.\n"]
    if not failed:
        lines.append("\n> Tất cả KPI lõi đạt ngưỡng ở run này.\n")
    else:
        lines += ["\n| Mã | KPI | Ngưỡng | Thực đo | Hướng cải thiện |", "|----|-----|--------|---------|-----------------|"]
        for c, v in failed:
            lines.append(f"| `{c}` | {v['name']} | {v['thr']}{v['unit']} | {fmt(v['value'], v['unit'])} | {RECS.get(c, '—')} |")
    d = perf.get("derived", {}) if perf else {}
    resp = (d.get("response_ms") or {})
    if resp.get("pct_le_15s") is not None and resp.get("pct_le_15s") < 100:
        lines += ["\n## Hiệu năng — Response ≤ 15s",
                  f"- p50 = {resp.get('p50')} ms · chỉ {resp.get('pct_le_15s')}% câu ≤ 15s. {RECS['R-CTRL-RESP15']}"]
    lines += ["\n## Phân tích False-Positive (chống bịa đặt)",
              "- Entity Precision + Hallucination là thước đo 'không bịa'. Xem `raw-logs/llm-entity-precision.jsonl` (`bad != []`) và `llm-hallucination.jsonl` (score=1) để liệt kê ca sai; chi tiết FP/FN: `false-positive-analysis.md`, `false-negative-analysis.md`, `failed-samples.xlsx`.",
              "- Nguyên nhân gốc retrieval/relevance: embedding EN baseline trước đây không hợp tiếng Việt — đã chuyển bge-m3 đa ngữ (xem `embedding-report.md`)."]
    (Path(reports) / "improvement-plan.md").write_text("\n".join(lines), encoding="utf-8")


def write_sanitization_error_analysis(reports, sani, hdr):
    """error-analysis-and-improvement-plan.md (§6.1): phân tích lỗi sanitization + kế hoạch."""
    pt, ov = sani.get("per_type", {}), sani.get("overall", {})
    lines = ["# Sanitization — Phân tích lỗi & kế hoạch cải thiện\n", hdr,
             f"\n- **F1 tổng: {fmt(ov.get('f1'))}** (ngưỡng > 90%) · Recall {fmt(ov.get('recall'))} · Precision {fmt(ov.get('precision'))} · N = {sani.get('n_samples','—')} mẫu\n",
             "\n| Type | Recall | Precision | F1 | TP/FP/FN | Đạt |", "|------|--------|-----------|----|----------|-----|"]
    for t, v in pt.items():
        lines.append(f"| {t} | {fmt(v.get('recall'))} | {fmt(v.get('precision'))} | {fmt(v.get('f1'))} | {v.get('tp')}/{v.get('fp')}/{v.get('fn')} | {'✅' if v.get('pass') else '❌'} |")
    lines += ["\n## Kế hoạch cải thiện",
              "- Loại có Recall thấp (thường `password`): bổ sung mẫu sinh đa dạng + nới rule pattern mật khẩu.",
              "- Loại có Precision thấp: thêm hard-negative (chuỗi giống PII nhưng không phải) để giảm False-Positive.",
              "- Hai tầng Regex + entropy/model-based theo HĐ; tinh chỉnh ngưỡng entropy per-type.",
              "\nChi tiết FN/FP: `false-negative-analysis.md`, `false-positive-analysis.md`. Raw log: `raw-logs/sanitization.jsonl`."]
    (Path(reports) / "error-analysis-and-improvement-plan.md").write_text("\n".join(lines), encoding="utf-8")


def write_fn_analysis(reports, sani, hdr):
    """false-negative-analysis.md (§5.3)."""
    fn = sani.get("false_negatives") or {}
    conf = sani.get("confusion") or {}
    total_fn = sum(v.get("fn", 0) for v in conf.values())
    lines = ["# Phân tích False Negative\n", hdr,
             f"\n**Tổng FN (sanitization): {total_fn}.** FN = PII/secret có thật nhưng detector bỏ sót → rủi ro lộ dữ liệu.\n",
             "\n| Type | FN count | Mẫu bỏ sót (rút gọn) |", "|------|----------|----------------------|"]
    for t in ["email", "ip", "phone", "api_key", "password"]:
        cnt = conf.get(t, {}).get("fn", 0)
        ex = "; ".join(_short(e.get("value"))[:24] for e in (fn.get(t) or [])[:3]) or "—"
        lines.append(f"| {t} | {cnt} | {ex} |")
    lines += ["\n## Nhận định",
              "- FN tập trung ở `password` (chuỗi mật khẩu đa dạng, khó bắt bằng regex thuần) — kế hoạch xử lý ở `error-analysis-and-improvement-plan.md`.",
              "- Retrieval 'miss' (gold không vào top-10) là FN tầng truy hồi — xem `raw-logs/embedding-hit-rate-at-10.jsonl` (score=0) và `embedding-report.md`."]
    (Path(reports) / "false-negative-analysis.md").write_text("\n".join(lines), encoding="utf-8")


def write_fp_analysis(reports, sani, hdr):
    """false-positive-analysis.md (§5.4) — ghi nhận kể cả khi FP=0."""
    fp = sani.get("false_positives") or {}
    conf = sani.get("confusion") or {}
    total_fp = sum(v.get("fp", 0) for v in conf.values())
    lines = ["# Phân tích False Positive\n", hdr,
             f"\n**Tổng FP (sanitization): {total_fp}.** " +
             ("FP = 0 → detector không gắn nhãn nhầm chuỗi vô hại (đã ghi nhận theo HĐ).\n" if total_fp == 0
              else "FP = detector gắn nhãn PII cho chuỗi KHÔNG phải PII (hard-negative: CVE id, version, hash, placeholder).\n")]
    if total_fp:
        lines += ["\n| Type | FP count | Mẫu nhầm (rút gọn) |", "|------|----------|--------------------|"]
        for t in ["email", "ip", "phone", "api_key", "password"]:
            cnt = conf.get(t, {}).get("fp", 0)
            ex = "; ".join(_short(e.get("value"))[:24] for e in (fp.get(t) or [])[:3]) or "—"
            lines.append(f"| {t} | {cnt} | {ex} |")
    lines += ["\n## Nhận định",
              "- Hard-negative trong bộ test (CVE-id, version `192.168`, hash SHA-256, `changeme`) dùng để đo FP — Precision cao nghĩa là detector phân biệt tốt.",
              "- FP tầng nội dung: agent khẳng định CVE-giả là thật → xem `raw-logs/llm-hallucination.jsonl`."]
    (Path(reports) / "false-positive-analysis.md").write_text("\n".join(lines), encoding="utf-8")


def write_run_summary(run_dir, K, manifest, args, commit):
    """summary.md ở gốc run folder — bảng KPI + endpoint + đường dẫn artifact."""
    npass = sum(1 for v in K.values() if v["pass"] is True)
    ep = manifest.get("endpoints", {})
    lines = [f"# Bench KPI AI — Run {args.date or '(local)'}\n",
             f"> Model: **{args.model}** · Commit: `{commit}` · {npass}/{len(K)} KPI đạt · Test set: 1000 mẫu (40-40-20)\n",
             "\n## Endpoints (đã dùng cho run này)",
             f"- LLM: `{ep.get('llm')}`", f"- Embedding: `{ep.get('embed')}`",
             f"- Reranker: `{ep.get('rerank')}`", f"- Qdrant: `{ep.get('qdrant')}`",
             f"- RAGAS judge: `{ep.get('ragas_judge')}`",
             "\n## Kết quả KPI", "\n| Mã | KPI | Ngưỡng | Thực đo | Kết quả |", "|----|-----|--------|---------|---------|"]
    for c, v in K.items():
        lines.append(f"| `{c}` | {v['name']} | {v['thr']}{v['unit']} | {fmt(v['value'], v['unit'])} | {badge(v['pass'])} |")
    lines += ["\n## Artifact trong run folder",
              "- `reports/` — báo cáo .md (ai-task-kpi, embedding, reranking, llm-kpi, performance, sanitization), `radar.png`,",
              "  deliverable: `improvement-plan.md`, `error-analysis-and-improvement-plan.md`,",
              "  `false-negative-analysis.md`, `false-positive-analysis.md`, `failed-samples.xlsx`.",
              "- `raw-logs/` — `*.jsonl` (True/Predicted/Score), `*-summary.json`, `kpi-summary.json/.xlsx`,",
              "  `performance-per-request.jsonl`, `run-manifest.json`.",
              "\nTái chạy: `bash eval/kpi/scripts/bench.sh` (điền `.env.bench` trước)."]
    (Path(run_dir) / "summary.md").write_text("\n".join(lines), encoding="utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--logs", default=str(KPI_DIR / "raw-logs"))
    ap.add_argument("--reports", default=str(KPI_DIR / "reports"))
    ap.add_argument("--model", default=config.LLM_MODEL)
    ap.add_argument("--date", default="")
    ap.add_argument("--run-dir", default="", help="thư mục gốc của run (nơi ghi summary.md); mặc định = cha của --reports")
    args = ap.parse_args()
    logs, reports = Path(args.logs), Path(args.reports)
    reports.mkdir(parents=True, exist_ok=True)
    run_dir = Path(args.run_dir) if args.run_dir else reports.parent

    retr = load(logs, "retrieval-summary.json") or {}
    llm = load(logs, "llm-kpi-summary.json") or {}
    task = load(logs, "task-summary.json") or {}
    sani = load(logs, "sanitization-summary.json") or {}
    perf = load(logs, "performance-summary.json") or {}

    # ---- KPI tổng hợp: {code: {name, value, threshold, pass}} ----
    def g(d, *keys, default=None):
        for k in keys:
            if isinstance(d, dict) and k in d:
                d = d[k]
            else:
                return default
        return d

    K = {}
    K["R-KPI-EMB96"] = {"name": "Embedding Hit Rate@10", "unit": "%", "thr": "> 96",
                        "value": retr.get("hit_rate_at_10_pct"), "pass": (retr.get("hit_rate_at_10_pct") or 0) > 96}
    K["R-KPI-RERANK90"] = {"name": "Reranking Precision@1", "unit": "%", "thr": "> 90",
                           "value": retr.get("precision_at_1_pct"), "pass": (retr.get("precision_at_1_pct") or 0) > 90}
    faith = load(logs, "faithfulness-summary.json") or {}
    fa = faith.get("mean_faithfulness_pct", g(llm, "R-KPI-FAITH98", "mean_faithfulness_pct"))
    K["R-KPI-FAITH98"] = {"name": "Faithfulness (RAGAS)", "unit": "%", "thr": "> 98",
                          "value": fa, "pass": (fa is not None and fa > 98)}
    rel = g(llm, "R-KPI-RELEV90", "mean_relevance_pct")
    if rel is None:
        rel = g(llm, "R-KPI-RELEV90", "mean_cosine_pct")  # tương thích run cũ
    K["R-KPI-RELEV90"] = {"name": "Answer Relevance (LLM-judge)", "unit": "%", "thr": "> 90",
                          "value": rel, "pass": (rel is not None and rel > 90)}
    ent = g(llm, "R-KPI-ENTITY100", "mean_precision_pct")
    K["R-KPI-ENTITY100"] = {"name": "Entity Precision CVE/CWE", "unit": "%", "thr": "= 100",
                            "value": ent, "pass": (ent is not None and ent >= 99.999)}
    hal = g(llm, "R-KPI-HALLU2", "hallucination_rate_pct")
    K["R-KPI-HALLU2"] = {"name": "Hallucination Rate", "unit": "%", "thr": "< 2",
                         "value": hal, "pass": (hal is not None and hal < 2)}
    K["R-LLM-SAST85"] = {"name": "SAST accuracy", "unit": "%", "thr": "≥ 85",
                         "value": g(task, "R-LLM-SAST85", "accuracy_pct"), "pass": g(task, "R-LLM-SAST85", "passed")}
    K["R-LLM-EXPLOIT85"] = {"name": "Exploit Explanation", "unit": "%", "thr": "≥ 85",
                            "value": g(task, "R-LLM-EXPLOIT85", "accuracy_pct"), "pass": g(task, "R-LLM-EXPLOIT85", "passed")}
    K["R-LLM-REMED60"] = {"name": "Auto-remediation", "unit": "%", "thr": "≥ 60",
                          "value": g(task, "R-LLM-REMED60", "accuracy_pct"), "pass": g(task, "R-LLM-REMED60", "passed")}
    sf1 = g(sani, "overall", "f1")
    K["R-SEC-PII"] = {"name": "Sanitization PII/Secret F1", "unit": "%", "thr": "> 90",
                      "value": sf1, "pass": (sf1 is not None and sf1 > 90)}
    reasoning = g(llm, "R-KPI-REASONING", "reasoning_present_pct")

    # ---- Radar (5 trục), diện tích bao phủ vs ngưỡng cam kết ----
    safety = (100 - hal) if hal is not None else None
    axes = [("Faithfulness", fa, 98), ("Relevance", rel, 90), ("Entity Precision", ent, 100),
            ("Logic/Reasoning", reasoning, 80), ("Safety/Hallucination", safety, 98)]
    actual = [(a[1] if a[1] is not None else 0) for a in axes]
    committed = [a[2] for a in axes]

    def poly_area(r):
        n = len(r)
        return 0.5 * abs(math.sin(2 * math.pi / n)) * sum(r[i] * r[(i + 1) % n] for i in range(n))
    cov = 100 * poly_area(actual) / poly_area(committed) if poly_area(committed) else None
    K["R-KPI-RADAR90"] = {"name": "Radar coverage area", "unit": "%", "thr": "> 90",
                          "value": round(cov, 1) if cov else None, "pass": (cov is not None and cov > 90)}
    radar_data = {"axes": [a[0] for a in axes], "actual": actual, "committed": committed,
                  "coverage_area_pct": round(cov, 1) if cov else None}
    (reports / "radar-data.json").write_text(json.dumps(radar_data, ensure_ascii=False, indent=2))

    # radar.png
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        labels = [a[0] for a in axes]
        ang = [i / len(labels) * 2 * math.pi for i in range(len(labels))] + [0]
        fig, ax = plt.subplots(figsize=(7, 7), subplot_kw=dict(polar=True))
        for vals, name, color in [(committed, "KPI cam kết", "#bbbbbb"), (actual, "Thực đo", "#1f77b4")]:
            v = vals + [vals[0]]
            ax.plot(ang, v, "o-", linewidth=2, label=name, color=color)
            ax.fill(ang, v, alpha=0.15, color=color)
        ax.set_xticks(ang[:-1]); ax.set_xticklabels(labels, fontsize=9)
        ax.set_ylim(0, 100); ax.set_title(f"KPI AI Radar — coverage {radar_data['coverage_area_pct']}% (model {args.model})", fontsize=11)
        ax.legend(loc="upper right", bbox_to_anchor=(1.25, 1.1))
        plt.tight_layout(); plt.savefig(reports / "radar.png", dpi=130, bbox_inches="tight"); plt.close()
        radar_ok = True
    except Exception as e:
        radar_ok = False
        (reports / "radar-error.txt").write_text(repr(e))

    # ---- run-manifest.json ----
    try:
        commit = subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], cwd=KPI_DIR, text=True).strip()
    except Exception:
        commit = "unknown"
    manifest = {
        "date": args.date, "model_llm": args.model, "git_commit": commit,
        "test_set": "eval/datasets-acceptance (1000 mẫu, 40-40-20)",
        "embed_model": config.EMBED_MODEL, "rerank_model": config.RERANK_MODEL,
        "endpoints": {
            "llm": config.LLM_BASE_URL, "embed": config.EMBED_URL,
            "rerank": config.RERANK_URL, "qdrant": f"{config.QDRANT_URL}/{config.QDRANT_COLLECTION}",
            "ragas_judge": config.RAGAS_JUDGE_BASE_URL,
        },
        "kpi": {c: {"name": v["name"], "value": v["value"], "threshold": v["thr"], "pass": v["pass"]} for c, v in K.items()},
        "raw_logs": sorted(p.name for p in logs.glob("*.jsonl")),
        "summaries": sorted(p.name for p in logs.glob("*-summary.json")),
        "note": f"LLM={args.model} @ {config.LLM_BASE_URL}. Bench endpoint-driven; đổi stack: set LLM_*/EMBED_URL/RERANK_URL/QDRANT_URL rồi chạy lại bench.sh.",
    }
    (logs / "run-manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    (logs / "kpi-summary.json").write_text(json.dumps(K, ensure_ascii=False, indent=2))

    # ---- kpi-summary.xlsx ----
    try:
        import pandas as pd
        rows = [{"Mã": c, "KPI": v["name"], "Ngưỡng": v["thr"],
                 "Thực đo": fmt(v["value"], v.get("unit", "")), "Kết quả": badge(v["pass"])} for c, v in K.items()]
        with pd.ExcelWriter(logs / "kpi-summary.xlsx", engine="openpyxl") as xw:
            pd.DataFrame(rows).to_excel(xw, sheet_name="KPI Summary", index=False)
        xlsx_ok = True
    except Exception as e:
        xlsx_ok = False
        (logs / "xlsx-error.txt").write_text(repr(e))

    # ---- Reports .md ----
    def kpi_row(code):
        v = K[code]
        return f"| `{code}` | {v['name']} | {v['thr']}{v['unit']} | {fmt(v['value'], v['unit'])} | {badge(v['pass'])} |"

    hdr = f"> Model: **{args.model}** · Ngày: {args.date or '—'} · Commit: `{commit}` · Test set: 1000 mẫu (40-40-20)\n"

    # ai-task-kpi-report.md (tổng hợp)
    npass = sum(1 for v in K.values() if v["pass"] is True)
    ntot = len(K)
    lines = [f"# Báo cáo KPI AI — Tổng hợp\n", hdr,
             f"\n**Tổng quan: {npass}/{ntot} KPI đạt.** Radar coverage: {fmt(K['R-KPI-RADAR90']['value'])}.\n",
             "\n| Mã | KPI | Ngưỡng | Thực đo | Kết quả |", "|----|-----|--------|---------|---------|"]
    for c in K:
        lines.append(kpi_row(c))
    lines += ["\n## Diễn giải", "",
              "- Raw logs True/Predicted/Score: `raw-logs/*.jsonl`. Tổng hợp: `kpi-summary.json`/`.xlsx`, `run-manifest.json`.",
              "- Báo cáo chi tiết từng mô hình: `embedding-report.md`, `reranking-report.md`, `llm-kpi-report.md`, `performance-report.md`, `sanitization-report.md`.",
              "- Radar: `radar.png` (+ `radar-data.json`)."]
    (reports / "ai-task-kpi-report.md").write_text("\n".join(lines), encoding="utf-8")

    # embedding-report.md
    (reports / "embedding-report.md").write_text("\n".join([
        "# Embedding Model — Hit Rate@10 (R-KPI-EMB96)\n", hdr,
        f"\n- **Hit Rate@10 (hybrid dense+BM25): {fmt(retr.get('hit_rate_at_10_pct'))}** · ngưỡng > 96% · {badge(K['R-KPI-EMB96']['pass'])}",
        f"- Dense-only (embedding thuần): {fmt(retr.get('hit_rate_at_10_dense_only_pct'))}",
        f"- Mode: {retr.get('mode','—')} · N = {retr.get('n','—')} · model {retr.get('model_embed','—')}",
        "\nRaw log: `raw-logs/embedding-hit-rate-at-10.jsonl` (True=gold CVE, Predicted=top-10 CVE, Score=1/0).",
        "\n## Phân tích & cải thiện",
        "- Dense dùng embedding đa ngữ bge-m3 (hỗ trợ tiếng Việt) + BM25 (hybrid).",
        "- Hướng cải thiện: embedding đa ngữ (bge-m3) / dịch truy vấn EN trước khi embed / tăng trọng số BM25.",
    ]), encoding="utf-8")

    # reranking-report.md
    (reports / "reranking-report.md").write_text("\n".join([
        "# Reranking Model — Precision@1 (R-KPI-RERANK90)\n", hdr,
        f"\n- **Precision@1: {fmt(retr.get('precision_at_1_pct'))}** · ngưỡng > 90% · {badge(K['R-KPI-RERANK90']['pass'])}",
        f"- N = {retr.get('n','—')} · model {retr.get('model_rerank','—')}",
        "\nRaw log: `raw-logs/reranking-precision-at-1.jsonl` (True=gold CVE, Predicted=top-1 sau rerank, Score=1/0).",
        "\n## Phân tích",
        "- P@1 bị chặn bởi recall ứng viên: nếu gold không nằm trong candidate (Hit miss) thì rerank không cứu được.",
        "- Cải thiện recall ứng viên (embedding đa ngữ + BM25) sẽ kéo P@1 lên.",
    ]), encoding="utf-8")

    # llm-kpi-report.md
    (reports / "llm-kpi-report.md").write_text("\n".join([
        "# LLM Model — KPI (Faithfulness/Relevance/Entity/Hallucination/Reasoning)\n", hdr,
        "\n| Mã | KPI | Ngưỡng | Thực đo | Kết quả |", "|----|-----|--------|---------|---------|",
        kpi_row("R-KPI-FAITH98"), kpi_row("R-KPI-RELEV90"), kpi_row("R-KPI-ENTITY100"), kpi_row("R-KPI-HALLU2"),
        f"| `R-KPI-REASONING` | Reasoning từng bước | — | {fmt(reasoning)} | {badge(None)} |",
        "\nRaw logs: `llm-faithfulness.jsonl`, `llm-answer-relevance.jsonl`, `llm-entity-precision.jsonl`, `llm-hallucination.jsonl` (True/Predicted/Score).",
        "\n## Phương pháp",
        "- Faithfulness: RAGAS (LLM judge) trên (question, answer, contexts truy hồi).",
        "- Answer Relevance: cosine(embed(answer), embed(question)) — bge-m3.",
        "- Entity Precision: regex CVE/CWE, mỗi id đối chiếu KB Qdrant (grounded).",
        "- Hallucination: id bịa (không có trong KB) hoặc khẳng định CVE-giả (trap) là thật.",
        "\n## False-Positive analysis",
        "- Xem `raw-logs/llm-hallucination.jsonl` (score=1) và `llm-entity-precision.jsonl` (bad != []) để liệt kê ca sai.",
    ]), encoding="utf-8")

    # performance-report.md
    d = perf.get("derived", {})
    (reports / "performance-report.md").write_text("\n".join([
        "# Hiệu năng suy luận (R-PERF-*, R-CTRL-RESP15)\n", hdr,
        f"\n> {perf.get('note','')}\n",
        "\n| Chỉ tiêu | Ngưỡng | Thực đo |", "|----------|--------|---------|",
        f"| TTFT (p50) | 900–1500 ms | {fmt(g(d,'ttft_ms','p50'),' ms')} (trong dải: {fmt(g(d,'ttft_ms','in_900_1500_pct'))}) |",
        f"| TPS ước lượng (mean) | 20–35 tok/s | {fmt(g(d,'tps_est','mean'),' tok/s')} |",
        f"| Response time (p50) | ≤ 15 s | {fmt(g(d,'response_ms','p50'),' ms')} (≤15s: {fmt(g(d,'response_ms','pct_le_15s'))}) |",
        f"| Embedding throughput | ~1200/s (A100) | {fmt(g(perf,'throughput_embedding','embeddings_per_s'),'/s')} |",
        f"| Reranker throughput | ~180/s (A100) | {fmt(g(perf,'throughput_reranker','docs_per_s'),'/s')} |",
        f"| Concurrency RPS | ~0.6 RPS / 30 CCU | {fmt(g(perf,'concurrency','rps'),' RPS')} |",
        "\n## Response time theo độ khó (≤15s)",
        "| Độ khó | p50 (ms) | p95 (ms) | %≤15s |", "|--------|----------|----------|-------|",
    ] + [f"| {k} | {v.get('p50_ms')} | {v.get('p95_ms')} | {v.get('pct_le_15s')}% |" for k, v in (g(d, "by_difficulty") or {}).items()]
      + ["\n## Lưu ý", "- Đo trên deepseek-v4-flash (cloud) + bge endpoint VPS — KHÔNG phải 3×A100/Qwen on-prem như cam kết phần cứng.",
         "- Response >15s ở câu khó/tư duy do LLM sinh câu trả lời dài; cân nhắc giới hạn độ dài / model nhanh hơn / on-prem A100 để đạt ngưỡng."]),
        encoding="utf-8")

    # sanitization-report.md
    pt = sani.get("per_type", {})
    ov = sani.get("overall", {})
    lines = ["# Sanitization PII/Secret (R-SEC-PII)\n", hdr,
             f"\n- **F1 tổng: {fmt(ov.get('f1'))}** · ngưỡng > 90% · {badge(K['R-SEC-PII']['pass'])}",
             f"- Recall tổng {fmt(ov.get('recall'))} · Precision tổng {fmt(ov.get('precision'))} · N = {sani.get('n_samples','—')} mẫu",
             "\n| Type | Recall | Precision | F1 | TP/FP/FN |", "|------|--------|-----------|----|----------|"]
    for t, v in pt.items():
        lines.append(f"| {t} | {fmt(v.get('recall'))} | {fmt(v.get('precision'))} | {fmt(v.get('f1'))} | {v.get('tp')}/{v.get('fp')}/{v.get('fn')} |")
    lines += ["\n## Confusion matrix", "```", json.dumps(sani.get("confusion", {}), ensure_ascii=False, indent=2), "```",
              "\n## False-Positive analysis", "Raw log: `raw-logs/sanitization.jsonl` (True/Predicted). FP/FN mẫu:",
              "```", json.dumps({"false_positives": sani.get("false_positives", {}),
                                 "false_negatives": {k: v[:3] for k, v in (sani.get("false_negatives", {}) or {}).items()}}, ensure_ascii=False, indent=2)[:1500], "```",
              "\nPhương pháp: regex rules + entropy classifier (truffleHog-style). Hai tầng theo HĐ 'Regex + Model-based'."]
    (reports / "sanitization-report.md").write_text("\n".join(lines), encoding="utf-8")

    # ---- Deliverable §5/§6/§7 + summary run ----
    nfail, xlsx2_ok = write_failed_samples_xlsx(logs, reports, sani)
    write_improvement_plan(reports, K, perf, hdr)
    write_sanitization_error_analysis(reports, sani, hdr)
    write_fn_analysis(reports, sani, hdr)
    write_fp_analysis(reports, sani, hdr)
    write_run_summary(run_dir, K, manifest, args, commit)

    print(f"Đã sinh {npass}/{ntot} KPI đạt. Reports → {reports}")
    print(f"  deliverables: improvement-plan.md, error-analysis-and-improvement-plan.md, "
          f"false-negative-analysis.md, false-positive-analysis.md, failed-samples.xlsx ({nfail} mẫu, {'OK' if xlsx2_ok else 'FAIL'})")
    print(f"  summary.md → {run_dir/'summary.md'}")
    print(f"  radar.png: {'OK' if radar_ok else 'FAIL'} · xlsx: {'OK' if xlsx_ok else 'FAIL'} · coverage {radar_data['coverage_area_pct']}%")
    for c, v in K.items():
        print(f"  {c:18} {fmt(v['value'], v['unit']):>10}  thr {v['thr']:6}  {badge(v['pass'])}")


if __name__ == "__main__":
    main()
