#!/usr/bin/env python3
"""Explicitly promote evaluated metrics as the approved regression baseline."""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path

from check_finetune_thresholds import load_reports, read_json


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument("--policy", type=Path, default=root / "configs/finetune_thresholds.yml")
    parser.add_argument("--report", action="append", required=True, help="PATH or component=PATH; repeatable")
    parser.add_argument("--approve", action="store_true", help="Required acknowledgement before replacing the baseline")
    args = parser.parse_args()
    if not args.approve:
        parser.error("pass --approve after reviewing the report; baseline promotion is deliberate")
    policy = read_json(args.policy)
    reports = load_reports(args.report)
    baseline_path = (args.policy.parent / policy["baseline_file"]).resolve()
    baseline = read_json(baseline_path) if baseline_path.exists() else {"reports": {}}
    approved = baseline.setdefault("reports", {})
    for name, report in reports.items():
        approved[name] = {
            "suite_id": report.get("suite_id"),
            "examples": report.get("examples"),
            "metrics": report["metrics"],
            "source_report": report["_path"],
        }
    baseline.update({
        "schema_version": 1,
        "policy_id": policy["policy_id"],
        "approved_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "policy_sha256": hashlib.sha256(args.policy.read_bytes()).hexdigest(),
    })
    baseline_path.parent.mkdir(parents=True, exist_ok=True)
    baseline_path.write_text(json.dumps(baseline, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Approved baseline for: {', '.join(sorted(reports))} -> {baseline_path}")


if __name__ == "__main__":
    main()
