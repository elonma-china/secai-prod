"""Conservative offline scoring; unreviewed translations never become passes."""

from __future__ import annotations

import math
import argparse
import json
import random
import re
from datetime import datetime
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

from .manifest import DIRECTIONS, ManifestError, validate_manifest
from .metrics import diagnostic_metrics


class ScoreError(ValueError):
    """Result/annotation evidence is inconsistent with the scheduled corpus."""


RUBRIC_DIMENSIONS = frozenset({
    "target_variant", "meaning_preserved", "no_hallucination",
    "readable", "register_appropriate", "literals_preserved",
})


def wilson95(successes: int, total: int) -> tuple[float, float] | None:
    if total == 0:
        return None
    z = 1.959963984540054
    p = successes / total
    denominator = 1 + z * z / total
    centre = (p + z * z / (2 * total)) / denominator
    radius = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denominator
    return max(0.0, centre - radius), min(1.0, centre + radius)


def paired_bootstrap_delta(
    baseline: list[float], candidate: list[float], source_groups: list[str],
    *, replicates: int = 10000, seed: int = 20261003,
) -> dict[str, Any]:
    if (not baseline or len(baseline) != len(candidate) or len(baseline) != len(source_groups) or
            not isinstance(replicates, int) or replicates < 1 or
            any(not isinstance(group, str) or not group for group in source_groups) or
            any(not isinstance(value, (int, float)) or not math.isfinite(value)
                for value in baseline + candidate)):
        raise ScoreError("paired baseline/candidate source rows are invalid")
    by_group: dict[str, list[float]] = defaultdict(list)
    for before, after, group in zip(baseline, candidate, source_groups):
        by_group[group].append(after - before)
    group_deltas = [sum(values) / len(values) for values in by_group.values()]
    observed = sum(group_deltas) / len(group_deltas)
    rng = random.Random(seed)
    bootstraps = [
        sum(rng.choices(group_deltas, k=len(group_deltas))) / len(group_deltas)
        for _ in range(replicates)
    ]
    return {
        "delta": observed,
        "ci95": [_percentile(bootstraps, 0.025), _percentile(bootstraps, 0.975)],
        "unique_groups": len(group_deltas),
        "replicates": replicates,
        "seed": seed,
        "noninferiority_gate": "PENDING_BASELINE_MARGIN",
    }


def _entity_evidence(sample: dict[str, Any], output: str | None) -> tuple[int, int]:
    spans = sample["protected_spans"]
    patterns = (
        re.compile(r"\bCVE-\d{4}-\d{4,}\b"),
        re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])"),
    )
    expected_entities = [Counter(pattern.findall(sample["source"])) for pattern in patterns]
    total = max(len(spans), sum(sum(counter.values()) for counter in expected_entities))
    if output is not None:
        observed_entities = [Counter(pattern.findall(output)) for pattern in patterns]
        if observed_entities != expected_entities:
            return 0, max(1, total)
    if not spans and total == 0:
        return 0, 0
    if not output:
        return 0, total
    expected = [span["text"] for span in spans]
    found = []
    for text in expected:
        if output.count(text) == expected.count(text) and text not in found:
            found.append(text)
    # Exact count alone is insufficient: protected occurrences must keep their order.
    positions = []
    cursor = 0
    for text in expected:
        pos = output.find(text, cursor)
        if pos < 0:
            return 0, total
        positions.append(pos)
        cursor = pos + len(text)
    if len(found) != len(set(expected)):
        return 0, total
    return total, total


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = (len(ordered) - 1) * percentile
    low, high = math.floor(index), math.ceil(index)
    return ordered[low] + (ordered[high] - ordered[low]) * (index - low)


def _gate(*, complete: bool, enough: bool, threshold_met: bool) -> str:
    if not complete or not enough:
        return "BLOCKED"
    return "PASS" if threshold_met else "FAIL"


def score_results(
    manifest: Iterable[dict[str, Any]],
    results: Iterable[dict[str, Any]],
    annotations: Iterable[dict[str, Any]],
    *,
    mode: str,
    phase: str,
    rounds: int = 3,
    min_samples_per_direction: int = 30,
) -> dict[str, Any]:
    if not isinstance(rounds, int) or rounds < 1:
        raise ScoreError("rounds must be a positive integer")
    if mode not in {"model-only", "legacy-api", "multilang-api"} or phase not in {"DEV", "TEST"}:
        raise ScoreError("mode and phase must be explicit supported values")
    samples = list(manifest)
    try:
        inventory = validate_manifest(samples, require_directions=False)
    except ManifestError as exc:
        raise ScoreError(str(exc)) from exc
    samples = [
        sample for sample in samples if sample["split"] == phase and
        (mode == "model-only" or
         (mode == "legacy-api" and sample["direction"] in {"vi>en", "en>vi"}) or
         (mode == "multilang-api" and sample["direction"] not in {"vi>en", "en>vi"}))
    ]
    if not samples:
        raise ScoreError("no samples scheduled for mode and phase")
    by_id = {sample["id"]: sample for sample in samples}
    result_map = {}
    modes = set()
    hashes = {"model_id": set(), "model_revision": set(), "prompt_hash": set(), "config_hash": set(), "data_hash": set()}
    for result in results:
        if not isinstance(result, dict):
            raise ScoreError("result must be an object")
        sample_id, turn = result.get("sample_id"), result.get("round")
        if sample_id not in by_id or not isinstance(turn, int) or isinstance(turn, bool) or not 1 <= turn <= rounds or result.get("mode") != mode:
            raise ScoreError(f"result is not scheduled: {sample_id}/{turn}")
        if result.get("phase") != by_id[sample_id]["split"]:
            raise ScoreError(f"result phase differs from manifest split: {sample_id}")
        key = sample_id, turn
        if key in result_map:
            raise ScoreError(f"duplicate result: {sample_id}/{turn}")
        result_map[key] = result
        modes.add(result.get("mode"))
        for field, values in hashes.items():
            value = result.get(field)
            if not isinstance(value, str) or not value:
                raise ScoreError(f"result missing {field}: {sample_id}/{turn}")
            values.add(value)
        if result["data_hash"] != inventory.data_hash:
            raise ScoreError(f"result data_hash differs from manifest: {sample_id}/{turn}")
        if result.get("model_id") != "DuyTa/Cyber-F1-AWQ":
            raise ScoreError(f"unexpected translator model: {sample_id}/{turn}")
    if len(modes) > 1 or any(len(values) > 1 for values in hashes.values()):
        raise ScoreError("mode/model/prompt/config/data hash changed within one scored run")
    annotation_map = {}
    for annotation in annotations:
        if not isinstance(annotation, dict):
            raise ScoreError("annotation must be an object")
        sample_id, turn = annotation.get("sample_id"), annotation.get("round")
        if sample_id not in by_id or not isinstance(turn, int) or isinstance(turn, bool) or not 1 <= turn <= rounds:
            raise ScoreError(f"annotation is not scheduled: {sample_id}/{turn}")
        key = sample_id, turn
        if key in annotation_map:
            raise ScoreError(f"duplicate annotation: {sample_id}/{turn}")
        rubric = annotation.get("rubric")
        if (not isinstance(rubric, dict) or set(rubric) != RUBRIC_DIMENSIONS or
                any(not isinstance(value, bool) for value in rubric.values()) or
                annotation.get("decision") not in {"pass", "fail", "uncertain"} or
                not isinstance(annotation.get("off_target"), bool) or
                not isinstance(annotation.get("reviewer"), str) or not annotation["reviewer"].strip() or
                annotation.get("blind") is not True or
                not isinstance(annotation.get("calibration_id"), str) or not annotation["calibration_id"].strip()):
            raise ScoreError(f"annotation rubric/independent review fields invalid: {sample_id}/{turn}")
        if annotation["decision"] == "pass" and not all(rubric.values()):
            raise ScoreError(f"annotation rubric contradicts pass decision: {sample_id}/{turn}")
        annotation_map[key] = annotation

    directions: dict[str, Any] = {}
    for direction in DIRECTIONS:
        selected = [sample for sample in samples if sample["direction"] == direction]
        if not selected:
            continue
        round_reports = {}
        for turn in range(1, rounds + 1):
            passed = errors = missing = unreviewed = unadjudicated = off_target = entity_ok = entity_total = 0
            latencies = []
            references = []
            predictions = []
            input_known = output_known = 0
            input_total = output_total = 0
            costs = []
            starts = []
            completions = []
            group_passes: dict[str, list[bool]] = defaultdict(list)
            for sample in selected:
                key = sample["id"], turn
                result = result_map.get(key)
                review = annotation_map.get(key)
                output = result.get("validated_output") if result else None
                errored = result is not None and (bool(result.get("error")) or not isinstance(output, str) or not output.strip())
                references.append(sample["reference"])
                predictions.append(None if errored else output)
                if result is None:
                    missing += 1
                elif errored:
                    errors += 1
                elif review is None:
                    unreviewed += 1
                elif review["decision"] == "uncertain":
                    unadjudicated += 1
                entity_good, entity_count = _entity_evidence(sample, output if not errored else None)
                entity_ok += entity_good
                entity_total += entity_count
                if review and result and not errored:
                    off_target += int(review["off_target"])
                sample_pass = bool(
                    result and not errored and review and review["decision"] == "pass" and
                    all(review["rubric"].values()) and
                    not review["off_target"] and entity_good == entity_count
                )
                passed += int(sample_pass)
                group_passes[sample["source_group"]].append(sample_pass)
                if result and isinstance(result.get("latency_ms"), (int, float)):
                    latencies.append(float(result["latency_ms"]))
                if result:
                    for token_field in ("input_tokens", "output_tokens"):
                        value = result.get(token_field)
                        if isinstance(value, int) and value >= 0:
                            if token_field == "input_tokens":
                                input_known += 1
                                input_total += value
                            else:
                                output_known += 1
                                output_total += value
                    cost = result.get("cost_usd")
                    if isinstance(cost, (int, float)) and cost >= 0 and math.isfinite(cost):
                        costs.append(float(cost))
                    if result.get("started_at_utc") and result.get("completed_at_utc"):
                        try:
                            starts.append(datetime.fromisoformat(result["started_at_utc"]))
                            completions.append(datetime.fromisoformat(result["completed_at_utc"]))
                        except (TypeError, ValueError) as exc:
                            raise ScoreError(f"invalid run timestamp: {sample['id']}/{turn}") from exc
            scheduled = len(selected)
            unique_passes = sum(all(outcomes) for outcomes in group_passes.values())
            complete = missing == 0 and unreviewed == 0 and unadjudicated == 0
            enough = scheduled >= min_samples_per_direction
            span_seconds = (
                (max(completions) - min(starts)).total_seconds()
                if len(starts) == scheduled and len(completions) == scheduled else None
            )
            known_cost = len(costs) == scheduled
            round_reports[str(turn)] = {
                "scheduled": scheduled,
                "unique_sources": len(group_passes),
                "quality_passes": passed,
                "quality_rate": passed / scheduled,
                "quality_wilson95_unique": wilson95(unique_passes, len(group_passes)),
                "errors": errors,
                "missing_results": missing,
                "unreviewed_outputs": unreviewed,
                "unadjudicated": unadjudicated,
                "quality_gate": _gate(complete=complete, enough=enough, threshold_met=passed / scheduled >= 0.70),
                "off_target": off_target,
                "off_target_rate": off_target / scheduled,
                "off_target_gate": _gate(complete=complete and errors == 0, enough=enough, threshold_met=off_target / scheduled <= 0.02),
                "entity_exact_occurrences": entity_ok,
                "entity_total_occurrences": entity_total,
                "entity_exact": None if entity_total == 0 else entity_ok == entity_total,
                "entity_gate": "N/A" if entity_total == 0 else _gate(complete=complete, enough=enough, threshold_met=entity_ok == entity_total),
                "latency_p50_ms": _percentile(latencies, 0.50),
                "latency_p95_ms": _percentile(latencies, 0.95),
                "throughput_requests_per_second": scheduled / span_seconds if span_seconds and span_seconds > 0 else None,
                "token_coverage": {"input_known": input_known, "output_known": output_known, "scheduled": scheduled},
                "input_tokens_total": input_total if input_known == scheduled else None,
                "output_tokens_total": output_total if output_known == scheduled else None,
                "cost_status": "KNOWN" if known_cost else "UNKNOWN",
                "cost_usd_total": sum(costs) if known_cost else None,
                "usd_per_1000_input_chars": (
                    sum(costs) * 1000 / sum(len(sample["source"]) for sample in selected)
                    if known_cost else None
                ),
                "diagnostics": diagnostic_metrics(references, predictions, target_lang=direction.split(">", 1)[1]),
            }
        quality_gates = [record["quality_gate"] for record in round_reports.values()]
        directions[direction] = {
            "unique_sources": len({sample["source_group"] for sample in selected}),
            "wilson_unique_n": len({sample["source_group"] for sample in selected}),
            "scheduled_requests": len(selected) * rounds,
            "rounds": round_reports,
            "quality_gate": "FAIL" if "FAIL" in quality_gates else ("PASS" if all(x == "PASS" for x in quality_gates) else "BLOCKED"),
        }
    return {
        "data_hash": inventory.data_hash,
        "mode": mode,
        "phase": phase,
        "model_id": next(iter(hashes["model_id"])) if hashes["model_id"] else None,
        "model_revision": next(iter(hashes["model_revision"])) if hashes["model_revision"] else None,
        "prompt_hash": next(iter(hashes["prompt_hash"])) if hashes["prompt_hash"] else None,
        "config_hash": next(iter(hashes["config_hash"])) if hashes["config_hash"] else None,
        "directions": directions,
        "release_gate": "BLOCKED",
        "release_note": "Requires all 18 directions, independent rubric review, sealed TEST and AS-10 baseline margin.",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--annotations", type=Path, required=True)
    parser.add_argument("--mode", choices=("model-only", "legacy-api", "multilang-api"), required=True)
    parser.add_argument("--phase", choices=("DEV", "TEST"), required=True)
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--min-samples-per-direction", type=int, default=30)
    parser.add_argument("--output", type=Path, required=True)
    parsed = parser.parse_args(argv)
    from .validate_corpus import load_jsonl

    report = score_results(
        load_jsonl(parsed.manifest), load_jsonl(parsed.results),
        load_jsonl(parsed.annotations), mode=parsed.mode, phase=parsed.phase,
        rounds=parsed.rounds,
        min_samples_per_direction=parsed.min_samples_per_direction,
    )
    with parsed.output.open("x", encoding="utf-8") as output:
        json.dump(report, output, ensure_ascii=False, indent=2)
        output.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
