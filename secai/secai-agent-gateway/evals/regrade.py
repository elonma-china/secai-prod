#!/usr/bin/env python3
"""Chấm lại 1 file kết quả jsonl bằng graders.py hiện tại (KHÔNG gọi lại LLM) — dùng khi
sửa grader sau khi đã thu thập response thật. Ghi đè summary; jsonl gốc giữ nguyên response,
chỉ field 'grade' được cập nhật (ghi ra file mới *_regraded.jsonl để không mất bản gốc).

Dùng: python3 regrade.py results/exploit_<ts>.jsonl
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from graders import GRADERS  # noqa: E402
import run_eval as _run_eval  # noqa: E402 — tái dùng grade_bughunt cho function=bughunt


def infer_function(path: Path) -> str:
    name = path.stem
    for fn in ("statistical_report", "exploit", "chat", "translate", "report", "bughunt"):
        if name.startswith(fn):
            return fn
    raise ValueError(f"không suy ra được function từ tên file: {path.name}")


def regrade(path: Path) -> dict:
    function = infer_function(path)
    rows = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    out_rows = []
    for r in rows:
        sample, status, body, elapsed = r["sample"], r["http_status"], r["response"], r["elapsed_s"]
        if function == "bughunt":
            grade = _run_eval.grade_bughunt(sample, status, body, elapsed)
        else:
            grade = GRADERS[function](sample, status, body, elapsed).to_dict()
        out_rows.append({**r, "grade": grade})

    out_path = path.with_name(path.stem + "_regraded.jsonl")
    with out_path.open("w", encoding="utf-8") as f:
        for row in out_rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    total = len(out_rows)
    passed = sum(1 for r in out_rows if r["grade"]["passed"])
    by_category: dict[str, list[dict]] = {}
    for r in out_rows:
        cat = r["sample"].get("category", "?")
        by_category.setdefault(cat, []).append(r)
    reason_counter: Counter[str] = Counter()
    for r in out_rows:
        for reason in r["grade"].get("reasons", []):
            reason_counter[reason.split(":")[0][:80]] += 1

    summary = {
        "function": function, "total": total, "passed": passed,
        "pass_rate": round(100.0 * passed / total, 2) if total else 0.0,
        "by_category": {
            cat: {"total": len(rws), "passed": sum(1 for r in rws if r["grade"]["passed"]),
                  "pass_rate": round(100.0 * sum(1 for r in rws if r["grade"]["passed"]) / len(rws), 1)}
            for cat, rws in by_category.items()
        },
        "top_failure_reasons": reason_counter.most_common(15),
        "results_file": str(out_path), "regraded_from": str(path),
    }
    summary_path = out_path.with_name(out_path.stem + "_summary.json")
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[{function}] REGRADED {passed}/{total} = {summary['pass_rate']}%  -> {summary_path}")
    return summary


def main() -> None:
    if len(sys.argv) < 2:
        print("dùng: python3 regrade.py <results/xxx.jsonl> [thêm file...]")
        sys.exit(1)
    summaries = [regrade(Path(p)) for p in sys.argv[1:]]
    print()
    for s in summaries:
        print(f"  {s['function']:20s} {s['passed']:4d}/{s['total']:4d} = {s['pass_rate']:6.2f}%")


if __name__ == "__main__":
    main()
