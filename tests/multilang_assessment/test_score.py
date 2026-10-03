import hashlib

import pytest

from evals.multilang.manifest import canonical_hash
from evals.multilang.score import ScoreError, score_results as _score_results

from .test_manifest import row


def score_results(manifest, results, annotations, **kwargs):
    manifest = list(manifest)
    digest = canonical_hash(manifest)
    for item in results:
        item["data_hash"] = digest
    mode = kwargs.pop("mode", "model-only")
    phase = kwargs.pop("phase", "DEV")
    return _score_results(manifest, results, annotations, mode=mode, phase=phase, **kwargs)


def result(sample_id, round_number=1, output="こんにちは。", error=None):
    return {
        "sample_id": sample_id,
        "round": round_number,
        "phase": "DEV",
        "mode": "model-only",
        "model_id": "DuyTa/Cyber-F1-AWQ",
        "model_revision": "7e7e6718ce268d2b00adc6e3ea43fcd33452f516",
        "prompt_hash": "a" * 64,
        "config_hash": "b" * 64,
        "data_hash": "c" * 64,
        "raw_output": output if not error else None,
        "validated_output": output if not error else None,
        "latency_ms": 100.0,
        "input_tokens": 7,
        "output_tokens": 5 if not error else 0,
        "cost_usd": None,
        "error": error,
    }


def annotation(sample_id, round_number=1, passed=True, off_target=False, output="こんにちは。"):
    return {
        "sample_id": sample_id,
        "round": round_number,
        "decision": "pass" if passed else "fail",
        "rubric": {
            "target_variant": passed,
            "meaning_preserved": True,
            "no_hallucination": True,
            "readable": True,
            "register_appropriate": True,
            "literals_preserved": True,
        },
        "off_target": off_target,
        "reviewer": "blind-reviewer-1",
        "blind": True,
        "calibration_id": "labeled-calibration@1234567",
        "output_sha256": hashlib.sha256(output.encode("utf-8")).hexdigest(),
    }


def test_failed_rows_stay_in_denominator():
    manifest = [row("ok", group="doc-ok"), row("timed", group="doc-timed", source="Câu khác.")]
    report = score_results(
        manifest,
        [result("ok"), result("timed", error="timeout")],
        [annotation("ok")],
        rounds=1, min_samples_per_direction=2,
    )
    direction = report["directions"]["vi>ja"]["rounds"]["1"]
    assert direction["scheduled"] == 2
    assert direction["quality_passes"] == 1
    assert direction["quality_rate"] == 0.5
    assert direction["errors"] == 1
    assert direction["quality_gate"] == "FAIL"


def test_shuffle_is_not_new_sample():
    manifest = [row()]
    report = score_results(
        manifest,
        [result("s1", n) for n in (1, 2, 3)],
        [annotation("s1", n) for n in (1, 2, 3)],
        rounds=3, min_samples_per_direction=1,
    )
    direction = report["directions"]["vi>ja"]
    assert direction["unique_sources"] == 1
    assert direction["scheduled_requests"] == 3
    assert direction["wilson_unique_n"] == 1


def test_missing_result_is_fail_not_dropped_and_blocks_complete_gate():
    report = score_results([row()], [], [], rounds=1, min_samples_per_direction=1)
    direction = report["directions"]["vi>ja"]["rounds"]["1"]
    assert direction["scheduled"] == 1
    assert direction["quality_passes"] == 0
    assert direction["missing_results"] == 1
    assert direction["quality_gate"] == "BLOCKED"


def test_entity_mutation_fails_exact_gate_even_if_reviewer_passed():
    sample = row(source="Kiểm tra CVE-2024-1234.")
    start = sample["source"].index("CVE-")
    sample["protected_spans"] = [{"start": start, "end": start + 13, "text": "CVE-2024-1234", "kind": "cve"}]
    report = score_results(
        [sample], [result("s1", output="CVE-2024-1235 を確認。")],
        [annotation("s1")], rounds=1, min_samples_per_direction=1,
    )
    direction = report["directions"]["vi>ja"]["rounds"]["1"]
    assert direction["entity_exact"] is False
    assert direction["quality_passes"] == 0


def test_extra_cve_fails_entity_gate():
    sample = row(source="Kiểm tra CVE-2024-1234.")
    start = sample["source"].index("CVE-")
    sample["protected_spans"] = [{"start": start, "end": start + 13,
                                  "text": "CVE-2024-1234", "kind": "cve"}]
    report = score_results(
        [sample], [result("s1", output="CVE-2024-1234 と CVE-2024-9999 を確認。")],
        [annotation("s1")], rounds=1, min_samples_per_direction=1,
    )
    direction = report["directions"]["vi>ja"]["rounds"]["1"]
    assert direction["entity_exact"] is False
    assert direction["quality_passes"] == 0


def test_extra_url_fails_exact_gate_with_per_sample_mismatch():
    source = "Mở https://example.org/path để kiểm tra."
    sample = row(source=source)
    literal = "https://example.org/path"
    start = source.index(literal)
    sample["protected_spans"] = [{"start": start, "end": start + len(literal),
                                  "text": literal, "kind": "url"}]
    output = "https://example.org/path と https://evil.example/extra を開く。"
    report = score_results([sample], [result("s1", output=output)],
                           [annotation("s1", output=output)], rounds=1,
                           min_samples_per_direction=1)
    direction = report["directions"]["vi>ja"]["rounds"]["1"]
    assert direction["entity_gate"] == "FAIL"
    assert direction["entity_details"][0]["sample_id"] == "s1"
    assert any(item["kind"] == "url" and item["status"] == "MISMATCH"
               for item in direction["entity_details"][0]["literals"])


def test_duplicate_result_row_is_rejected():
    with pytest.raises(ScoreError, match="duplicate result"):
        score_results([row()], [result("s1"), result("s1")], [], rounds=1)


def test_unknown_review_cannot_be_counted_as_quality_pass():
    report = score_results([row()], [result("s1")], [], rounds=1, min_samples_per_direction=1)
    direction = report["directions"]["vi>ja"]["rounds"]["1"]
    assert direction["quality_passes"] == 0
    assert direction["quality_gate"] == "BLOCKED"


def test_one_off_target_in_thirty_fails_separate_gate():
    manifest = [row(f"s{i}", source=f"Câu số {i}.", group=f"doc-{i}") for i in range(30)]
    results = [result(f"s{i}") for i in range(30)]
    reviews = [annotation(f"s{i}", off_target=(i == 0)) for i in range(30)]
    report = score_results(manifest, results, reviews, rounds=1)
    direction = report["directions"]["vi>ja"]["rounds"]["1"]
    assert direction["rubric_threshold_gate"] == "PASS"
    assert direction["quality_gate"] == "BLOCKED"
    assert direction["off_target"] == 1
    assert direction["off_target_gate"] == "FAIL"


def test_thirty_repeats_of_one_group_cannot_pass_quality_quota():
    manifest = [row(f"s{i}", source=f"Câu {i}.", group="one-document") for i in range(30)]
    report = score_results(manifest, [result(f"s{i}") for i in range(30)],
                           [annotation(f"s{i}") for i in range(30)], rounds=1)
    direction = report["directions"]["vi>ja"]["rounds"]["1"]
    assert direction["unique_sources"] == 1
    assert direction["rubric_threshold_gate"] == "BLOCKED"
    assert direction["quality_gate"] == "BLOCKED"


def test_stale_pass_review_cannot_score_changed_output():
    report = score_results([row()], [result("s1", output="Clearly wrong output")],
                           [annotation("s1")], rounds=1, min_samples_per_direction=1)
    direction = report["directions"]["vi>ja"]["rounds"]["1"]
    assert direction["stale_reviews"] == 1
    assert direction["quality_passes"] == 0
    assert direction["quality_gate"] == "BLOCKED"


def test_changed_config_between_repeated_rounds_is_rejected():
    first, second = result("s1", 1), result("s1", 2)
    second["config_hash"] = "d" * 64
    with pytest.raises(ScoreError, match="changed"):
        score_results([row()], [first, second], [], rounds=2)


def test_stale_result_data_hash_is_rejected():
    with pytest.raises(ScoreError, match="data_hash"):
        _score_results([row()], [result("s1")], [], mode="model-only", phase="DEV", rounds=1)


def test_score_only_selected_mode_and_phase_and_counts_missing_results():
    dev = row("dev-ja", group="dev-ja", source="Câu dev ja.")
    test = row("test-ja", group="test-ja", source="Câu test ja.")
    test["split"] = "TEST"
    control = row("dev-en", direction="vi>en", group="dev-en", source="Câu dev en.")
    report = score_results([dev, test, control], [], [], mode="model-only", phase="DEV",
                           rounds=1, min_samples_per_direction=1)
    assert set(report["directions"]) == {"vi>ja", "vi>en"}
    assert report["directions"]["vi>ja"]["rounds"]["1"]["missing_results"] == 1
    report = score_results([dev, test, control], [], [], mode="legacy-api", phase="DEV",
                           rounds=1, min_samples_per_direction=1)
    assert set(report["directions"]) == {"vi>en"}
    assert report["directions"]["vi>en"]["rounds"]["1"]["missing_results"] == 1


def test_score_rejects_result_not_scheduled_for_mode_or_phase():
    sample = row()
    control = row("en", direction="vi>en", group="en", source="Câu en.")
    heldout = row("heldout", group="heldout", source="Câu test.")
    heldout["split"] = "TEST"
    with pytest.raises(ScoreError, match="not scheduled"):
        score_results([sample, control], [result("s1")], [], mode="legacy-api", phase="DEV", rounds=1)
    with pytest.raises(ScoreError, match="not scheduled"):
        score_results([sample, heldout], [result("s1")], [], mode="model-only", phase="TEST", rounds=1)


def test_metric_coverage_excludes_error_but_quality_denominator_keeps_it():
    manifest = [row("ok", group="doc-ok"), row("timed", group="doc-timed", source="Câu khác.")]
    report = score_results(
        manifest, [result("ok"), result("timed", error="timeout")], [annotation("ok")],
        rounds=1, min_samples_per_direction=2,
    )
    direction = report["directions"]["vi>ja"]["rounds"]["1"]
    assert direction["diagnostics"]["coverage"] == {"scored": 1, "scheduled": 2}
    assert direction["quality_rate"] == 0.5


def test_boolean_only_review_cannot_certify_quality():
    weak = {"sample_id": "s1", "round": 1, "rubric_pass": True, "off_target": False,
            "reviewer": "judge"}
    with pytest.raises(ScoreError, match="rubric"):
        score_results([row()], [result("s1")], [weak], rounds=1)


def test_uncertain_review_requires_adjudication_before_gate():
    review = annotation("s1")
    review["decision"] = "uncertain"
    report = score_results([row()], [result("s1")], [review], rounds=1, min_samples_per_direction=1)
    direction = report["directions"]["vi>ja"]["rounds"]["1"]
    assert direction["quality_passes"] == 0
    assert direction["unadjudicated"] == 1
    assert direction["quality_gate"] == "BLOCKED"


def test_missing_tariff_or_usage_reports_cost_unknown():
    report = score_results([row()], [result("s1")], [annotation("s1")],
                           rounds=1, min_samples_per_direction=1)
    direction = report["directions"]["vi>ja"]["rounds"]["1"]
    assert direction["cost_status"] == "UNKNOWN"
    assert direction["usd_per_1000_input_chars"] is None
    assert direction["token_coverage"] == {"input_known": 1, "output_known": 1, "scheduled": 1}


def test_known_cost_and_timestamps_report_rate_and_throughput():
    sample = row()
    translated = result("s1")
    translated["cost_usd"] = 0.002
    translated["started_at_utc"] = "2026-10-04T00:00:00+00:00"
    translated["completed_at_utc"] = "2026-10-04T00:00:02+00:00"
    report = score_results([sample], [translated], [annotation("s1")],
                           rounds=1, min_samples_per_direction=1)
    direction = report["directions"]["vi>ja"]["rounds"]["1"]
    assert direction["cost_status"] == "KNOWN"
    assert direction["usd_per_1000_input_chars"] == pytest.approx(0.002 * 1000 / len(sample["source"]))
    assert direction["throughput_requests_per_second"] == 0.5


def test_diagnostic_rates_and_off_target_interval_report_coverage():
    sample = row()
    output = "Translation: Xin chào bạn. Xin chào bạn. Xin chào bạn."
    translated = result("s1", output=output)
    review = annotation("s1", off_target=True, output=output)
    review["rubric"]["no_hallucination"] = False
    review["decision"] = "fail"
    report = score_results([sample], [translated], [review], rounds=1,
                           min_samples_per_direction=1)
    direction = report["directions"]["vi>ja"]["rounds"]["1"]
    assert direction["off_target_wilson95_unique"] is not None
    assert direction["diagnostic_rates"]["script_mismatch"]["count"] == 1
    assert direction["diagnostic_rates"]["preamble"]["count"] == 1
    assert direction["diagnostic_rates"]["repetition"]["count"] == 1
    assert direction["diagnostic_rates"]["hallucination"]["count"] == 1
