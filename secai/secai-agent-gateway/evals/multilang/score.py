"""Conservative offline scoring; unreviewed translations never become passes."""

from __future__ import annotations

import math
import argparse
import hashlib
import json
import random
import re
from datetime import datetime
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

from .manifest import DIRECTIONS, ManifestError, canonical_hash, normalized_source, validate_manifest
from .metrics import diagnostic_metrics


class ScoreError(ValueError):
    """Result/annotation evidence is inconsistent with the scheduled corpus."""


RUBRIC_DIMENSIONS = frozenset({
    "target_variant", "meaning_preserved", "no_hallucination",
    "readable", "register_appropriate", "literals_preserved",
})
LITERAL_PATTERNS = {
    "cve": re.compile(r"\bCVE-\d{4}-\d{4,}\b"),
    "ip": re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])"),
    "url": re.compile(r"https?://[^\s<>\]\[\"']+"),
    "code": re.compile(r"(?<!`)`[^`\n]+`(?!`)"),
    "date": re.compile(r"\b\d{4}-\d{2}-\d{2}\b"),
    "number": re.compile(r"(?<!\w)\d+(?:[.,]\d+)*(?!\w)"),
}


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


def _literal_inventory(kind: str, value: str) -> Counter[str]:
    found = LITERAL_PATTERNS[kind].findall(value)
    if kind == "url":
        found = [item.rstrip(".,;!?") for item in found]
    return Counter(found)


def _entity_evidence(sample: dict[str, Any], output: str | None) -> tuple[int, int, list[dict[str, Any]], bool]:
    spans = sample["protected_spans"]
    source = sample["source"]
    kinds = {"cve", "ip", "url"} | {span["kind"] for span in spans if span["kind"] in LITERAL_PATTERNS}
    unsupported = {span["kind"] for span in spans if span["kind"] not in LITERAL_PATTERNS}
    details: list[dict[str, Any]] = []
    total = len(spans)
    unannotated_core_total = 0
    matched_core = 0
    matched_spans = 0
    mismatch = False
    for kind in sorted(kinds):
        expected = _literal_inventory(kind, source)
        observed = _literal_inventory(kind, output or "")
        if kind in {"cve", "ip", "url"}:
            unannotated_core_total += sum(expected.values())
        for literal in sorted(expected.keys() | observed.keys()):
            expected_count, observed_count = expected[literal], observed[literal]
            status = "MATCH" if expected_count == observed_count else "MISMATCH"
            mismatch |= status == "MISMATCH"
            if status == "MATCH":
                matched_core += expected_count
            details.append({
                "kind": kind, "literal_sha256": hashlib.sha256(literal.encode()).hexdigest(),
                "expected_count": expected_count, "observed_count": observed_count,
                "status": status,
            })
    expected_spans = [span["text"] for span in spans]
    cursor = 0
    for span in spans:
        literal = span["text"]
        observed_count = (output or "").count(literal)
        expected_count = expected_spans.count(literal)
        position = (output or "").find(literal, cursor)
        exact = observed_count == expected_count and position >= cursor
        if exact:
            cursor = position + len(literal)
            matched_spans += 1
        mismatch |= not exact
        details.append({
            "kind": span["kind"], "literal_sha256": hashlib.sha256(literal.encode()).hexdigest(),
            "expected_count": expected_count, "observed_count": observed_count,
            "status": "MATCH" if exact else "MISMATCH", "annotated": True,
        })
    if unsupported:
        for kind in sorted(unsupported):
            details.append({"kind": kind, "status": "UNVERIFIABLE"})
    total = max(total, unannotated_core_total)
    if mismatch and total == 0:
        total = 1
    good = min(total, max(matched_core, matched_spans))
    if mismatch:
        good = min(good, total - 1)
    if unsupported:
        good = 0
    return good, total, details, bool(unsupported)


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


def _output_diagnostics(direction: str, source: str, output: str | None, error: str | None) -> dict[str, bool | None]:
    if not isinstance(output, str) or not output.strip():
        return {"script_mismatch": None, "copy": None, "repetition": None,
                "preamble": None, "truncation": bool(error and "truncated" in error)}
    target = direction.split(">", 1)[1]
    script = {
        "zh-Hans": r"[\u4e00-\u9fff]", "ja": r"[\u3040-\u30ff\u4e00-\u9fff]",
        "ko": r"[\uac00-\ud7af]", "ar": r"[\u0600-\u06ff]",
        "ru": r"[\u0400-\u052f]",
    }.get(target, r"[A-Za-zÀ-ỹ]")
    return {
        "script_mismatch": re.search(script, output) is None,
        "copy": normalized_source(source) == normalized_source(output),
        "repetition": re.search(r"(.{6,80}?)(?:\s+\1){2,}", output) is not None,
        "preamble": re.match(r"^\s*(?:translation|translated text|bản dịch|dịch)\s*:", output, re.IGNORECASE) is not None,
        "truncation": bool(error and "truncated" in error),
    }


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
        digest = annotation.get("output_sha256")
        if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
            raise ScoreError(f"annotation output_sha256 invalid: {sample_id}/{turn}")
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
            passed = errors = missing = unreviewed = unadjudicated = stale_reviews = off_target = entity_ok = entity_total = entity_unverifiable = 0
            entity_details = []
            diagnostics: dict[str, list[bool | None]] = defaultdict(list)
            off_target_groups: dict[str, list[bool]] = defaultdict(list)
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
                if review is not None and isinstance(output, str) and review["output_sha256"] != hashlib.sha256(output.encode("utf-8")).hexdigest():
                    stale_reviews += 1
                    review = None
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
                entity_good, entity_count, literal_details, unknown_literals = _entity_evidence(sample, output if not errored else None)
                entity_ok += entity_good
                entity_total += entity_count
                entity_unverifiable += int(unknown_literals)
                entity_details.append({"sample_id": sample["id"], "round": turn,
                                       "exact": entity_good == entity_count and not unknown_literals,
                                       "literals": literal_details})
                if review and result and not errored:
                    off_target += int(review["off_target"])
                    off_target_groups[sample["source_group"]].append(review["off_target"])
                flags = _output_diagnostics(sample["direction"], sample["source"],
                                            output if not errored else None, result.get("error") if result else None)
                for name, flag in flags.items():
                    diagnostics[name].append(flag)
                diagnostics["hallucination"].append(
                    not review["rubric"]["no_hallucination"] if review and result and not errored else None)
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
            enough = scheduled >= min_samples_per_direction and len(group_passes) >= min_samples_per_direction
            rubric_threshold_gate = _gate(complete=complete, enough=enough, threshold_met=passed / scheduled >= 0.70)
            span_seconds = (
                (max(completions) - min(starts)).total_seconds()
                if len(starts) == scheduled and len(completions) == scheduled else None
            )
            known_cost = len(costs) == scheduled
            diagnostic_rates = {
                name: {
                    "count": sum(value is True for value in values),
                    "rate": sum(value is True for value in values) / scheduled,
                    "coverage": sum(value is not None for value in values),
                    "scheduled": scheduled,
                    "method": "review_rubric" if name == "hallucination" else "heuristic",
                }
                for name, values in diagnostics.items()
            }
            round_reports[str(turn)] = {
                "scheduled": scheduled,
                "unique_sources": len(group_passes),
                "quality_passes": passed,
                "quality_rate": passed / scheduled,
                "quality_wilson95_unique": wilson95(unique_passes, len(group_passes)),
                "errors": errors,
                "missing_results": missing,
                "unreviewed_outputs": unreviewed,
                "stale_reviews": stale_reviews,
                "unadjudicated": unadjudicated,
                "rubric_threshold_gate": rubric_threshold_gate,
                "quality_gate": "BLOCKED" if rubric_threshold_gate == "PASS" else rubric_threshold_gate,
                "off_target": off_target,
                "off_target_rate": off_target / scheduled,
                "off_target_wilson95_unique": (
                    wilson95(sum(any(values) for values in off_target_groups.values()), len(off_target_groups))
                    if complete and errors == 0 and len(off_target_groups) == len(group_passes) else None
                ),
                "off_target_gate": _gate(complete=complete and errors == 0, enough=enough, threshold_met=off_target / scheduled <= 0.02),
                "entity_exact_occurrences": entity_ok,
                "entity_total_occurrences": entity_total,
                "entity_unverifiable_samples": entity_unverifiable,
                "entity_details": entity_details,
                "entity_exact": None if entity_total == 0 else entity_ok == entity_total,
                "entity_gate": "BLOCKED" if entity_unverifiable else ("N/A" if entity_total == 0 else _gate(complete=complete, enough=enough, threshold_met=entity_ok == entity_total)),
                "diagnostic_rates": diagnostic_rates,
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
        "review_provenance_status": "UNVERIFIED_DECLARATIONS",
        "release_gate": "BLOCKED",
        "release_note": "Requires all 18 directions, independent rubric review, sealed TEST and AS-10 baseline margin.",
    }


def paired_quality_report(
    manifest: Iterable[dict[str, Any]],
    baseline_results: Iterable[dict[str, Any]], candidate_results: Iterable[dict[str, Any]],
    baseline_annotations: Iterable[dict[str, Any]], candidate_annotations: Iterable[dict[str, Any]],
    *, phase: str, rounds: int = 3,
) -> dict[str, Any]:
    samples = list(manifest)
    baseline = list(baseline_results)
    candidate = list(candidate_results)
    before_reviews = list(baseline_annotations)
    after_reviews = list(candidate_annotations)
    # Reuse run integrity checks before forming pairs; declarative review still cannot certify release.
    score_results(samples, baseline, before_reviews, mode="legacy-api", phase=phase, rounds=rounds)
    score_results(samples, candidate, after_reviews, mode="model-only", phase=phase, rounds=rounds)
    before = {(item["sample_id"], item["round"]): item for item in baseline}
    after = {(item["sample_id"], item["round"]): item for item in candidate}
    before_review = {(item["sample_id"], item["round"]): item for item in before_reviews}
    after_review = {(item["sample_id"], item["round"]): item for item in after_reviews}

    sample_by_id = {sample["id"]: sample for sample in samples}

    def outcome(key: tuple[str, int], result_map: dict, review_map: dict) -> float:
        result = result_map.get(key)
        if result is None:
            raise ScoreError(f"paired run missing result: {key}")
        output = result.get("validated_output")
        if result.get("error") or not isinstance(output, str) or not output.strip():
            return 0.0
        review = review_map.get(key)
        if review is None or review.get("decision") == "uncertain":
            raise ScoreError(f"paired run lacks adjudicated output-bound review: {key}")
        if review["output_sha256"] != hashlib.sha256(output.encode()).hexdigest():
            raise ScoreError(f"paired run has stale review: {key}")
        exact, total, _, unsupported = _entity_evidence(sample_by_id[key[0]], output)
        return float(review["decision"] == "pass" and all(review["rubric"].values()) and
                     not review["off_target"] and exact == total and not unsupported)

    directions = {}
    for direction in ("vi>en", "en>vi"):
        selected = [sample for sample in samples if sample["direction"] == direction and sample["split"] == phase]
        if not selected:
            continue
        baseline_values, candidate_values, groups = [], [], []
        for sample in selected:
            for turn in range(1, rounds + 1):
                key = sample["id"], turn
                baseline_values.append(outcome(key, before, before_review))
                candidate_values.append(outcome(key, after, after_review))
                groups.append(sample["source_group"])
        directions[direction] = paired_bootstrap_delta(baseline_values, candidate_values, groups)
        directions[direction]["baseline_quality_rate"] = sum(baseline_values) / len(baseline_values)
        directions[direction]["candidate_quality_rate"] = sum(candidate_values) / len(candidate_values)
        directions[direction]["paired_requests"] = len(groups)
    if set(directions) != {"vi>en", "en>vi"}:
        raise ScoreError("paired report requires both en/vi control directions")
    return {"phase": phase, "data_hash": canonical_hash(samples), "directions": directions,
            "review_provenance_status": "UNVERIFIED_DECLARATIONS",
            "release_gate": "BLOCKED",
            "reason": "Independent review provenance and pre-frozen baseline margin are absent"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--annotations", type=Path, required=True)
    parser.add_argument("--baseline-results", type=Path)
    parser.add_argument("--baseline-annotations", type=Path)
    parser.add_argument("--mode", choices=("model-only", "legacy-api", "multilang-api"), required=True)
    parser.add_argument("--phase", choices=("DEV", "TEST"), required=True)
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--min-samples-per-direction", type=int, default=30)
    parser.add_argument("--output", type=Path, required=True)
    parsed = parser.parse_args(argv)
    from .validate_corpus import load_jsonl

    manifest = load_jsonl(parsed.manifest)
    results = load_jsonl(parsed.results)
    annotations = load_jsonl(parsed.annotations)
    report = score_results(
        manifest, results, annotations, mode=parsed.mode, phase=parsed.phase,
        rounds=parsed.rounds,
        min_samples_per_direction=parsed.min_samples_per_direction,
    )
    if bool(parsed.baseline_results) != bool(parsed.baseline_annotations):
        raise ScoreError("both paired baseline result and annotation files are required")
    if parsed.baseline_results:
        if parsed.mode != "model-only":
            raise ScoreError("paired en/vi comparison requires model-only candidate results")
        report["en_vi_paired"] = paired_quality_report(
            manifest, load_jsonl(parsed.baseline_results), results,
            load_jsonl(parsed.baseline_annotations), annotations,
            phase=parsed.phase, rounds=parsed.rounds,
        )
    with parsed.output.open("x", encoding="utf-8") as output:
        json.dump(report, output, ensure_ascii=False, indent=2)
        output.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
