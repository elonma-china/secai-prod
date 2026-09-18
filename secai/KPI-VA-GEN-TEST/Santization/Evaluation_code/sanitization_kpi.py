#!/usr/bin/env python3
"""sanitization_kpi.py — [LOCAL] Đo KPI Sanitization PII/Secret (R-SEC-PII).

Chạy detector trên sanitization_pii.jsonl → so với nhãn gold → Recall/Precision/F1 mỗi type
+ confusion matrix + raw log True/Predicted + phân tích False Positive.

Ngưỡng hợp đồng: Email/IP R>98% P>95% · Phone R>95% P>90% · API key R>85% P>80% ·
Password R>80% P>75% · F1 tổng > 90%.

Usage: python3 sanitization_kpi.py [--dataset ../Dataset/sanitization_pii.jsonl] [--out ../Logs]
"""
import argparse
import collections
import json
from pathlib import Path

import detector  # cùng thư mục Evaluation_code

ROOT = Path(__file__).resolve().parents[1]  # santization/

TYPES = ["email", "ip", "phone", "api_key", "password"]
TARGETS = {  # (recall, precision) ngưỡng hợp đồng
    "email": (98, 95), "ip": (98, 95), "phone": (95, 90),
    "api_key": (85, 80), "password": (80, 75),
}


def norm(v):
    return v.strip().strip("\"'").replace(" ", "").replace("-", "").replace(".", "").lower()


def match(pred_val, gold_vals):
    """True nếu pred khớp 1 gold (chứa nhau sau normalize — robust biên span)."""
    pv = norm(pred_val)
    for gv in gold_vals:
        gn = norm(gv)
        if pv == gn or pv in gn or gn in pv:
            return gv
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", default=str(ROOT / "Dataset" / "sanitization_pii.jsonl"))
    ap.add_argument("--out", default=str(ROOT / "Logs"))
    args = ap.parse_args()

    rows = [json.loads(l) for l in Path(args.dataset).read_text(encoding="utf-8").splitlines() if l.strip()]
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    tp = collections.Counter()
    fp = collections.Counter()
    fn = collections.Counter()
    fp_examples = collections.defaultdict(list)
    fn_examples = collections.defaultdict(list)
    raw = (out_dir / "sanitization.jsonl").open("w", encoding="utf-8")

    for s in rows:
        gold = collections.defaultdict(list)
        for e in s["entities"]:
            gold[e["type"]].append(e["value"])
        pred = detector.detect(s["text"])
        pred_by = collections.defaultdict(list)
        for p in pred:
            pred_by[p["type"]].append(p["value"])

        matched_gold = collections.defaultdict(set)
        per_sample_tp = 0
        for t in TYPES:
            for pv in pred_by.get(t, []):
                g = match(pv, gold.get(t, []))
                if g is not None:
                    tp[t] += 1
                    per_sample_tp += 1
                    matched_gold[t].add(norm(g))
                else:
                    fp[t] += 1
                    if len(fp_examples[t]) < 12:
                        fp_examples[t].append({"id": s["id"], "value": pv, "kind": s["kind"]})
            for gv in gold.get(t, []):
                if norm(gv) not in matched_gold[t]:
                    fn[t] += 1
                    if len(fn_examples[t]) < 12:
                        fn_examples[t].append({"id": s["id"], "value": gv})

        raw.write(json.dumps({
            "id": s["id"], "kind": s["kind"],
            "true_label": [{"type": e["type"], "value": e["value"]} for e in s["entities"]],
            "predicted_label": [{"type": p["type"], "value": p["value"], "source": p["source"]} for p in pred],
            "score": per_sample_tp,
        }, ensure_ascii=False) + "\n")
    raw.close()

    def prf(t):
        p_tp, p_fp, p_fn = tp[t], fp[t], fn[t]
        rec = 100 * p_tp / (p_tp + p_fn) if (p_tp + p_fn) else 100.0
        pre = 100 * p_tp / (p_tp + p_fp) if (p_tp + p_fp) else 100.0
        f1 = 2 * pre * rec / (pre + rec) if (pre + rec) else 0.0
        return rec, pre, f1

    print("\n=== Sanitization PII/Secret KPI ===")
    print(f"{'Type':10} {'Recall':>8} {'Prec':>8} {'F1':>8}  {'TP/FP/FN':>14}  ngưỡng(R/P)")
    overall_tp = overall_fp = overall_fn = 0
    per_type = {}
    for t in TYPES:
        rec, pre, f1 = prf(t)
        tr, tpc = TARGETS[t]
        ok = "✓" if rec >= tr and pre >= tpc else "✗"
        per_type[t] = {"recall": round(rec, 2), "precision": round(pre, 2), "f1": round(f1, 2),
                       "tp": tp[t], "fp": fp[t], "fn": fn[t], "pass": rec >= tr and pre >= tpc}
        overall_tp += tp[t]; overall_fp += fp[t]; overall_fn += fn[t]
        print(f"{t:10} {rec:7.1f}% {pre:7.1f}% {f1:7.1f}%  {tp[t]:4}/{fp[t]:3}/{fn[t]:3}      {tr}/{tpc} {ok}")

    o_rec = 100 * overall_tp / (overall_tp + overall_fn) if (overall_tp + overall_fn) else 100.0
    o_pre = 100 * overall_tp / (overall_tp + overall_fp) if (overall_tp + overall_fp) else 100.0
    o_f1 = 2 * o_pre * o_rec / (o_pre + o_rec) if (o_pre + o_rec) else 0.0
    print(f"{'TỔNG':10} {o_rec:7.1f}% {o_pre:7.1f}% {o_f1:7.1f}%  {overall_tp:4}/{overall_fp:3}/{overall_fn:3}   F1 ngưỡng >90 {'✓' if o_f1 > 90 else '✗'}")

    summary = {
        "kpi": "sanitization", "n_samples": len(rows),
        "per_type": per_type,
        "overall": {"recall": round(o_rec, 2), "precision": round(o_pre, 2), "f1": round(o_f1, 2),
                    "tp": overall_tp, "fp": overall_fp, "fn": overall_fn, "f1_pass": o_f1 > 90},
        "confusion": {t: {"tp": tp[t], "fp": fp[t], "fn": fn[t]} for t in TYPES},
        "false_positives": dict(fp_examples),
        "false_negatives": dict(fn_examples),
    }
    (out_dir / "sanitization-summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"\nraw-log: {out_dir/'sanitization.jsonl'} · summary: {out_dir/'sanitization-summary.json'}")
    if fp_examples:
        print("\nFalse Positives (mẫu):")
        for t, ex in fp_examples.items():
            if ex:
                print(f"  {t}: {ex[0]['value'][:40]} (id {ex[0]['id']}, {ex[0]['kind']})")


if __name__ == "__main__":
    main()
