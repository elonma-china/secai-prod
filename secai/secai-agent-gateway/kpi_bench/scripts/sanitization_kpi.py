#!/usr/bin/env python3
"""sanitization_kpi.py — [LOCAL] Đo KPI Sanitization PII/Secret (R-SEC-PII).

Chạy detector trên sanitization_pii.jsonl → so với nhãn gold → Recall/Precision/F1 mỗi type
+ confusion matrix + raw log True/Predicted + phân tích False Positive.

Ngưỡng hợp đồng: Email/IP R>98% P>95% · Phone R>95% P>90% · API key R>85% P>80% ·
Password R>80% P>75% · F1 tổng > 90%.

Usage: python3 sanitization_kpi.py [--dataset ../datasets/sanitization_pii.jsonl] [--out ../raw-logs]
"""
import argparse
import collections
import json
import os
import sys
from pathlib import Path

KPI_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(KPI_DIR))
from sanitizer import detector  # noqa: E402

# [MỚI — chuẩn ngành, ADDITIVE] Bộ dataset self-generated (gen_pii_dataset.py) dùng CÙNG regex/
# exception-list với detector.py → circular (đo self-consistency, không phải recall/precision
# thật). Đặt SANITIZE_GOLD_PATH=<file.jsonl> (mỗi dòng {"text": "...", "entities"|"labels": [
# {"type"|"label": "...", "value"|"text": "..."}]}) trỏ tới 1 bộ gold ĐỘC LẬP (không do cùng
# tác giả/regex sinh ra) để có thêm số liệu recall/precision thật, báo ở field `independent_gold`
# RIÊNG — KHÔNG thay số self-generated cũ (summary["overall"]/["per_type"] vẫn giữ nguyên).
# TODO: import Presidio research-eval / CoNLL-2003 / OntoNotes gold (hoặc nhật ký sự cố PII thật
# đã được ẩn danh) làm bộ SANITIZE_GOLD_PATH mặc định — hiện chưa có sẵn trong repo.

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


def load_independent_gold(path):
    """[MỚI — ADDITIVE] Đọc bộ gold ĐỘC LẬP từ SANITIZE_GOLD_PATH (JSONL: text + labels).
    Chấp nhận cả 2 tên khoá để dễ tích hợp với export của công cụ khác:
      - entity: {"type"|"label": ..., "value"|"text": ...}
      - dòng:   {"id"?: ..., "text": ..., "kind"?: ..., "entities"|"labels": [entity, ...]}
    Trả về list các row cùng shape với sanitization_pii.jsonl (id/text/kind/entities)."""
    rows = []
    for i, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines()):
        if not line.strip():
            continue
        r = json.loads(line)
        raw_ents = r.get("entities") or r.get("labels") or []
        ents = []
        for e in raw_ents:
            t = e.get("type") or e.get("label")
            v = e.get("value") or e.get("text")
            if t and v:
                ents.append({"type": t, "value": v})
        rows.append({"id": r.get("id", i), "text": r["text"], "kind": r.get("kind", "independent_gold"),
                    "entities": ents})
    return rows


def score_dataset(rows):
    """[MỚI — ADDITIVE] Chạy detector.detect() trên `rows` (shape id/text/kind/entities) → trả
    (tp, fp, fn Counters theo type; fp_examples/fn_examples; list raw-row dict để ghi log).
    Tách ra để tái dùng cho CẢ dataset self-generated (legacy) LẪN independent gold (mới) mà
    không đổi logic chấm — cùng 1 thuật toán match()/normalize, chỉ khác nguồn dữ liệu."""
    tp = collections.Counter(); fp = collections.Counter(); fn = collections.Counter()
    fp_examples = collections.defaultdict(list)
    fn_examples = collections.defaultdict(list)
    raw_rows = []
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

        raw_rows.append({
            "id": s["id"], "kind": s["kind"],
            "true_label": [{"type": e["type"], "value": e["value"]} for e in s["entities"]],
            "predicted_label": [{"type": p["type"], "value": p["value"], "source": p["source"]} for p in pred],
            "score": per_sample_tp,
        })
    return tp, fp, fn, fp_examples, fn_examples, raw_rows


def prf(tp, fp, fn, t):
    p_tp, p_fp, p_fn = tp[t], fp[t], fn[t]
    rec = 100 * p_tp / (p_tp + p_fn) if (p_tp + p_fn) else 100.0
    pre = 100 * p_tp / (p_tp + p_fp) if (p_tp + p_fp) else 100.0
    f1 = 2 * pre * rec / (pre + rec) if (pre + rec) else 0.0
    return rec, pre, f1


def summarize(tp, fp, fn, per_type_extra=None):
    """[MỚI — ADDITIVE] Gói recall/precision/F1 per-type + overall thành 1 dict summary con,
    dùng chung cho block independent_gold. KHÔNG dùng lại cho block self-generated/legacy ở main()
    (block đó giữ nguyên code cũ nguyên vẹn, không refactor qua hàm này, để không đổi hành vi cũ)."""
    per_type = {}
    overall_tp = overall_fp = overall_fn = 0
    for t in TYPES:
        rec, pre, f1 = prf(tp, fp, fn, t)
        tr, tpc = TARGETS[t]
        per_type[t] = {"recall": round(rec, 2), "precision": round(pre, 2), "f1": round(f1, 2),
                       "tp": tp[t], "fp": fp[t], "fn": fn[t], "pass": rec >= tr and pre >= tpc}
        overall_tp += tp[t]; overall_fp += fp[t]; overall_fn += fn[t]
    o_rec = 100 * overall_tp / (overall_tp + overall_fn) if (overall_tp + overall_fn) else 100.0
    o_pre = 100 * overall_tp / (overall_tp + overall_fp) if (overall_tp + overall_fp) else 100.0
    o_f1 = 2 * o_pre * o_rec / (o_pre + o_rec) if (o_pre + o_rec) else 0.0
    return {
        "per_type": per_type,
        "overall": {"recall": round(o_rec, 2), "precision": round(o_pre, 2), "f1": round(o_f1, 2),
                    "tp": overall_tp, "fp": overall_fp, "fn": overall_fn, "f1_pass": o_f1 > 90},
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", default=str(KPI_DIR / "datasets" / "sanitization_pii.jsonl"))
    ap.add_argument("--out", default=str(KPI_DIR / "raw-logs"))
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
        # [MỚI — ADDITIVE] nhãn rõ nguồn dataset, KHÔNG đổi ý nghĩa các field ở trên (vẫn là số đo
        # trên bộ self-generated cũ). Xem summary["independent_gold"] bên dưới nếu có.
        "dataset": "self-generated (CIRCULAR — not independent; set SANITIZE_GOLD_PATH), see TODO",
    }

    # [MỚI — chuẩn ngành, ADDITIVE] Nếu có SANITIZE_GOLD_PATH → đo THÊM trên bộ gold ĐỘC LẬP,
    # ghi vào summary["independent_gold"] (field RIÊNG) + file raw log riêng. KHÔNG đụng tới
    # các số self-generated ở trên.
    gold_path = os.environ.get("SANITIZE_GOLD_PATH")
    if gold_path:
        try:
            gold_rows = load_independent_gold(gold_path)
            g_tp, g_fp, g_fn, g_fp_ex, g_fn_ex, g_raw = score_dataset(gold_rows)
            g_raw_f = (out_dir / "sanitization-independent.jsonl").open("w", encoding="utf-8")
            for row in g_raw:
                g_raw_f.write(json.dumps(row, ensure_ascii=False) + "\n")
            g_raw_f.close()
            g_summary = summarize(g_tp, g_fp, g_fn)
            g_summary.update({
                "dataset": "independent gold (SANITIZE_GOLD_PATH)",
                "dataset_path": gold_path,
                "n_samples": len(gold_rows),
                "confusion": {t: {"tp": g_tp[t], "fp": g_fp[t], "fn": g_fn[t]} for t in TYPES},
                "false_positives": dict(g_fp_ex),
                "false_negatives": dict(g_fn_ex),
            })
            summary["independent_gold"] = g_summary
            print(f"\n=== Sanitization PII/Secret KPI — independent gold ({gold_path}) ===")
            print(f"  overall F1={g_summary['overall']['f1']:.1f}%  "
                  f"(n={len(gold_rows)}, tp/fp/fn={sum(g_tp.values())}/{sum(g_fp.values())}/{sum(g_fn.values())})")
        except Exception as e:
            summary["independent_gold"] = {"error": repr(e)[:300], "dataset_path": gold_path}
            print(f"\n[independent_gold] lỗi đọc/chấm SANITIZE_GOLD_PATH={gold_path}: {e!r}")
    else:
        summary["independent_gold"] = {
            "available": False,
            "note": "set SANITIZE_GOLD_PATH=<file.jsonl text+labels độc lập> để đo recall/precision "
                    "thật, không circular. TODO: import Presidio research-eval / CoNLL / OntoNotes gold.",
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
