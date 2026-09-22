#!/usr/bin/env python3
"""task-summary.json (task_kpi.py: SAST/Exploit/Remediation accuracy, grader tất định tự-sinh từ
CVE mới) -> 3 report RIÊNG sast.json / exploit.json / remediation.json đúng schema
check_finetune_thresholds.py. Tách 3 report vì mỗi task có SỐ MẪU khác nhau (exploit hiếm) -> phải
kiểm min_examples riêng, không gộp min chung. Metric = accuracy (fraction 0..1) = R-LLM-SAST85/
EXPLOIT85/REMED60, đo trên FULL AGENT (mã SAST inline vào /ai/chat).
"""
import argparse
import json
from pathlib import Path

SUITE = "secai-tasks-product-v1"
MAP = {"sast": "R-LLM-SAST85", "exploit": "R-LLM-EXPLOIT85", "remediation": "R-LLM-REMED60"}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--task-summary", required=True)
    ap.add_argument("--out-dir", required=True)
    args = ap.parse_args()

    s = json.loads(Path(args.task_summary).read_text(encoding="utf-8"))
    out = Path(args.out_dir); out.mkdir(parents=True, exist_ok=True)
    written = {}
    for name, code in MAP.items():
        d = s.get(code) or {}
        acc = d.get("accuracy_pct")
        report = {
            "schema_version": 1, "component": name, "suite_id": SUITE,
            "examples": d.get("n", 0),
            "metrics": {"accuracy": round(acc / 100.0, 6) if isinstance(acc, (int, float)) else None},
            "detail": {"pass": d.get("pass"), "accuracy_pct": acc},
        }
        (out / f"{name}.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        written[name] = {"n": d.get("n"), "accuracy": report["metrics"]["accuracy"]}
    print(json.dumps(written, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
