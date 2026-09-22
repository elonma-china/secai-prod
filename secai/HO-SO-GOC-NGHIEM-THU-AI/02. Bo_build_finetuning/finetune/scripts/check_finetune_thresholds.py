#!/usr/bin/env python3
"""Decide whether a component needs another fine-tune from frozen evaluation reports.

The policy file has a .yml extension but is JSON-compatible YAML, so this script needs no
third-party YAML parser. It never starts an expensive training job; it writes an auditable
decision and names the fine-tune targets that a user may launch.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import sys
from pathlib import Path


def read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path} must be JSON-compatible YAML: {exc}") from exc


def parse_report_argument(value: str) -> tuple[str | None, Path]:
    if "=" not in value:
        return None, Path(value)
    name, raw_path = value.split("=", 1)
    if not name or not raw_path:
        raise ValueError("--report must be PATH or NAME=PATH")
    return name, Path(raw_path)


def load_reports(values: list[str]) -> dict[str, dict]:
    reports: dict[str, dict] = {}
    for value in values:
        alias, path = parse_report_argument(value)
        report = read_json(path)
        name = alias or report.get("component")
        if not isinstance(name, str) or not name:
            raise ValueError(f"{path}: pass NAME=PATH because report has no component")
        if name in reports:
            raise ValueError(f"duplicate report name: {name}")
        if not isinstance(report.get("metrics"), dict):
            raise ValueError(f"{path}: report needs a metrics object")
        reports[name] = {**report, "_path": str(path)}
    return reports


def metric_value(report: dict, metric: str) -> float:
    value = report["metrics"].get(metric)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"metric {metric!r} is absent or not numeric")
    return float(value)


def baseline_value(baseline: dict, report_name: str, metric: str) -> float | None:
    report = baseline.get("reports", {}).get(report_name)
    if not isinstance(report, dict):
        return None
    value = report.get("metrics", {}).get(metric)
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def evaluate_check(check: dict, reports: dict[str, dict], baseline: dict) -> dict:
    report_name = check["report"]
    if report_name not in reports:
        return {"id": check["id"], "target": check["finetune_target"], "status": "not_evaluated",
                "reason": f"report {report_name!r} was not supplied"}
    report = reports[report_name]
    problems: list[str] = []
    if report.get("suite_id") != check["suite_id"]:
        problems.append(f"suite_id={report.get('suite_id')!r}, expected {check['suite_id']!r}")
    examples = report.get("examples")
    if not isinstance(examples, int) or examples < check.get("min_examples", 1):
        problems.append(f"examples={examples!r}, need >= {check.get('min_examples', 1)}")
    if report.get("component") == "llm" and report.get("judge_errors", 0):
        problems.append(f"judge_errors={report['judge_errors']}; rerun incomplete evaluation")
    try:
        current = metric_value(report, check["metric"])
    except ValueError as exc:
        problems.append(str(exc))
        current = None
    baseline_metric = baseline_value(baseline, report_name, check["metric"])
    if current is not None:
        if "min" in check and current < float(check["min"]):
            problems.append(f"{check['metric']}={current:.6f} < minimum {float(check['min']):.6f}")
        if "max" in check and current > float(check["max"]):
            problems.append(f"{check['metric']}={current:.6f} > maximum {float(check['max']):.6f}")
        if baseline_metric is not None and "max_relative_drop" in check and baseline_metric > 0:
            floor = baseline_metric * (1 - float(check["max_relative_drop"]))
            if current < floor:
                problems.append(f"{check['metric']}={current:.6f} dropped below baseline floor {floor:.6f}")
        if baseline_metric is not None and "max_absolute_increase" in check:
            ceiling = baseline_metric + float(check["max_absolute_increase"])
            if current > ceiling:
                problems.append(f"{check['metric']}={current:.6f} exceeded baseline ceiling {ceiling:.6f}")
    return {
        "id": check["id"], "target": check["finetune_target"], "report": report_name,
        "metric": check["metric"], "current": current, "baseline": baseline_metric,
        "status": "failed" if problems else "passed", "reason": problems or ["within configured threshold"],
        "platform_kpi": check.get("platform_kpi"),
    }


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument("--policy", type=Path, default=root / "configs/finetune_thresholds.yml")
    parser.add_argument("--report", action="append", default=[], help="PATH or component=PATH; repeatable")
    parser.add_argument("--output", type=Path, default=root.parent / "benchmarks/reports/finetune-decision.json")
    parser.add_argument("--exit-on-trigger", action="store_true", help="Exit 10 when fine-tuning is required")
    args = parser.parse_args()
    if not args.report:
        parser.error("at least one --report is required")

    policy = read_json(args.policy)
    baseline_path = (args.policy.parent / policy["baseline_file"]).resolve()
    baseline = read_json(baseline_path) if baseline_path.exists() else {"reports": {}}
    reports = load_reports(args.report)
    checks = [evaluate_check(check, reports, baseline) for check in policy["checks"]]
    failed = [check for check in checks if check["status"] == "failed"]
    assessed = [check for check in checks if check["status"] != "not_evaluated"]
    if failed:
        decision = "FINETUNE_REQUIRED"
    elif assessed:
        decision = "NO_FINETUNE_REQUIRED"
    else:
        decision = "INSUFFICIENT_EVIDENCE"
    result = {
        "schema_version": 1,
        "policy_id": policy["policy_id"],
        "policy_sha256": hashlib.sha256(args.policy.read_bytes()).hexdigest(),
        "evaluated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "decision": decision,
        "finetune_targets": sorted({check["target"] for check in failed}),
        "reports": {name: {"path": report["_path"], "suite_id": report.get("suite_id"),
                            "examples": report.get("examples")} for name, report in reports.items()},
        "checks": checks,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"decision": decision, "finetune_targets": result["finetune_targets"], "output": str(args.output)},
                     ensure_ascii=False, indent=2))
    if args.exit_on_trigger and decision == "FINETUNE_REQUIRED":
        raise SystemExit(10)


if __name__ == "__main__":
    main()
