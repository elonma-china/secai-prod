import hashlib
import os
from datetime import datetime, timedelta, timezone

import pytest

from evals.multilang.evidence import (
    EvidenceError, claim_signed_run, digest, verify_alignment, verify_envelope,
    verify_margin_policy, verify_reviews,
)
from evals.multilang.manifest import canonical_hash, validate_manifest
from evals.multilang.score import paired_quality_report, score_results

from .signing import configure_signer, sign
from .test_manifest import row
from .test_score import annotation, result


def alignment_entries(rows):
    return {item["id"]: {
        "direction": item["direction"], "split": item["split"],
        "aligned_item_id": item["aligned_item_id"], "source_group": item["source_group"],
        "source_revision": item["source_revision"],
        "source_sha256": hashlib.sha256(item["source"].encode()).hexdigest(),
        "reference_sha256": hashlib.sha256(item["reference"].encode()).hexdigest(),
    } for item in rows}


def sign_alignment(tmp_path, private, rows):
    return sign(tmp_path, private, "alignment-map", manifest_sha256=canonical_hash(rows),
                source_dataset_id="tester-pinned-fixture@1234567", entries=alignment_entries(rows))


def sign_reviews(tmp_path, private, rows, outputs, reviews):
    return sign(tmp_path, private, "review-bundle", manifest_sha256=canonical_hash(rows),
                results_sha256=digest(outputs), annotations_sha256=digest(reviews),
                reviewer_ids=["blind-reviewer-1"],
                calibration_ids=["labeled-calibration@1234567"],
                blind_attested=True, qualification_attested=True)


def test_signed_alignment_rejects_falsified_cross_language_ids(tmp_path, monkeypatch):
    private, _ = configure_signer(tmp_path, monkeypatch)
    rows = [row(), row("en", "en>vi", "TEST", "English version.", "other-doc")]
    validate_manifest(rows, require_directions=False)
    signed = sign(tmp_path, private, "alignment-map", manifest_sha256=canonical_hash(rows),
                  source_dataset_id="pinned-corpus@1234567", entries=alignment_entries(rows))
    assert verify_alignment(rows, signed)
    forged = [dict(item) for item in rows]
    forged[1]["aligned_item_id"] = "not-the-trusted-alignment"
    with pytest.raises(EvidenceError, match="alignment map"):
        verify_alignment(forged, signed)
    signed["payload"]["entries"]["en"]["aligned_item_id"] = "forged-and-unsigned"
    with pytest.raises(EvidenceError, match="signature"):
        verify_envelope(signed, "alignment-map")


def test_signed_review_bundle_is_bound_to_exact_outputs(tmp_path, monkeypatch):
    private, _ = configure_signer(tmp_path, monkeypatch)
    rows = [row()]
    outputs = [result("s1")]
    outputs[0]["data_hash"] = canonical_hash(rows)
    reviews = [annotation("s1")]
    signed = sign(tmp_path, private, "review-bundle",
                  manifest_sha256=canonical_hash(rows), results_sha256=digest(outputs),
                  annotations_sha256=digest(reviews), reviewer_ids=["blind-reviewer-1"],
                  calibration_ids=["labeled-calibration@1234567"],
                  blind_attested=True, qualification_attested=True)
    assert verify_reviews(rows, outputs, reviews, signed)
    changed = [dict(outputs[0], validated_output="Clearly wrong output")]
    with pytest.raises(EvidenceError, match="review bundle"):
        verify_reviews(rows, changed, reviews, signed)


def test_signed_run_and_holdout_are_durably_consumed_once(tmp_path, monkeypatch):
    private, ledger = configure_signer(tmp_path, monkeypatch)
    lease = {"schema": "multilang-evidence-v1", "kind": "run-lease",
             "run_id": "assessment-run-001", "holdout_id": "sealed-holdout-001",
             "data_hash": "a" * 64, "config_hash": "b" * 64}
    signed = sign(tmp_path, private, "run-lease", **{k: v for k, v in lease.items() if k not in {"schema", "kind"}})
    claim = claim_signed_run(lease, signed)
    assert claim.is_file()
    assert len(list(ledger.glob("holdout-*.json"))) == 1
    with pytest.raises(EvidenceError, match="consumed"):
        claim_signed_run(lease, signed)
    replacement = dict(lease, run_id="assessment-run-002")
    replacement_signed = sign(tmp_path, private, "run-lease",
                              **{k: v for k, v in replacement.items() if k not in {"schema", "kind"}})
    with pytest.raises(EvidenceError, match="consumed"):
        claim_signed_run(replacement, replacement_signed)
    new_holdout = dict(replacement, holdout_id="sealed-holdout-002")
    new_signed = sign(tmp_path, private, "run-lease",
                      **{k: v for k, v in new_holdout.items() if k not in {"schema", "kind"}})
    assert claim_signed_run(new_holdout, new_signed).is_file()


def test_interrupted_test_claim_still_retires_holdout(tmp_path, monkeypatch):
    private, ledger = configure_signer(tmp_path, monkeypatch)
    lease = {"schema": "multilang-evidence-v1", "kind": "run-lease",
             "run_id": "interrupted-run-001", "holdout_id": "interrupted-holdout-001"}
    signed = sign(tmp_path, private, "run-lease", run_id=lease["run_id"], holdout_id=lease["holdout_id"])
    original_open = os.open

    def interrupt_before_run_claim(path, flags, mode=0o777, *, dir_fd=None):
        if str(path).endswith(".json") and "/run-" in str(path):
            raise OSError("injected interruption after holdout retirement")
        return original_open(path, flags, mode, dir_fd=dir_fd)

    with monkeypatch.context() as patch:
        patch.setattr("evals.multilang.evidence.os.open", interrupt_before_run_claim)
        with pytest.raises(OSError, match="injected interruption"):
            claim_signed_run(lease, signed)
    assert len(list(ledger.glob("holdout-*.json"))) == 1
    replacement = dict(lease, run_id="interrupted-run-002")
    replacement_signed = sign(tmp_path, private, "run-lease",
                              run_id=replacement["run_id"], holdout_id=replacement["holdout_id"])
    with pytest.raises(EvidenceError, match="consumed"):
        claim_signed_run(replacement, replacement_signed)


def test_margin_policy_must_be_signed_and_precede_candidate_run(tmp_path, monkeypatch):
    private, _ = configure_signer(tmp_path, monkeypatch)
    rows = [row("vi", "vi>en", source="Câu nguồn vi.")]
    baseline = [dict(result("vi"), completed_at_utc=datetime(2026, 10, 3, tzinfo=timezone.utc).isoformat())]
    before = datetime(2026, 10, 4, tzinfo=timezone.utc)
    candidate = [dict(result("vi"), started_at_utc=(before + timedelta(hours=1)).isoformat())]
    signed = sign(tmp_path, private, "en-vi-margin-policy",
                  manifest_sha256=canonical_hash(rows), baseline_results_sha256=digest(baseline),
                  margin=0.05, multiplicity_policy="bonferroni-two-directions",
                  familywise_alpha=0.05, frozen_at_utc=before.isoformat())
    assert verify_margin_policy(rows, baseline, candidate, signed)[0] == 0.05
    too_early = [dict(candidate[0], started_at_utc=(before - timedelta(seconds=1)).isoformat())]
    with pytest.raises(EvidenceError, match="frozen"):
        verify_margin_policy(rows, baseline, too_early, signed)
    baseline_after_freeze = [dict(baseline[0], completed_at_utc=(before + timedelta(seconds=1)).isoformat())]
    with pytest.raises(EvidenceError, match="frozen"):
        verify_margin_policy(rows, baseline_after_freeze, candidate,
                             sign(tmp_path, private, "en-vi-margin-policy",
                                  manifest_sha256=canonical_hash(rows),
                                  baseline_results_sha256=digest(baseline_after_freeze),
                                  margin=0.05, multiplicity_policy="bonferroni-two-directions",
                                  familywise_alpha=0.05, frozen_at_utc=before.isoformat()))


def test_signed_alignment_and_review_can_open_hard_gates_without_claiming_release(tmp_path, monkeypatch):
    private, _ = configure_signer(tmp_path, monkeypatch)
    rows = [row(f"sample-{i}", "vi>ja", source=f"Corpus text {i} {hashlib.sha256(str(i).encode()).hexdigest()[:16]}.",
                group=f"doc-{i}") for i in range(30)]
    outputs = [dict(result(item["id"]), data_hash=canonical_hash(rows)) for item in rows]
    reviews = [annotation(item["id"]) for item in rows]
    alignment = sign_alignment(tmp_path, private, rows)
    signed_review = sign_reviews(tmp_path, private, rows, outputs, reviews)
    unverified = score_results(rows, outputs, reviews, mode="model-only", phase="DEV", rounds=1,
                               alignment_attestation=alignment)
    assert unverified["directions"]["vi>ja"]["rounds"]["1"]["quality_gate"] == "BLOCKED"
    verified = score_results(rows, outputs, reviews, mode="model-only", phase="DEV", rounds=1,
                             alignment_attestation=alignment, review_attestation=signed_review)
    gates = verified["directions"]["vi>ja"]["rounds"]["1"]
    assert gates["quality_gate"] == "PASS"
    assert gates["off_target_gate"] == "PASS"
    assert verified["release_gate"] == "BLOCKED"


def test_frozen_signed_margin_can_open_en_vi_noninferiority_gate(tmp_path, monkeypatch):
    private, _ = configure_signer(tmp_path, monkeypatch)
    rows = [row(f"{direction}-{i}", direction,
                source=f"{direction} corpus text {i} {hashlib.sha256((direction + str(i)).encode()).hexdigest()[:16]}.",
                group=f"{direction}-doc-{i}")
            for direction in ("vi>en", "en>vi") for i in range(30)]
    manifest_hash = canonical_hash(rows)
    frozen = datetime(2026, 10, 4, tzinfo=timezone.utc)
    baseline = [dict(result(item["id"], turn), mode="legacy-api", data_hash=manifest_hash,
                     completed_at_utc=(frozen - timedelta(hours=1)).isoformat())
                for item in rows for turn in (1, 2, 3)]
    candidate = [dict(result(item["id"], turn), data_hash=manifest_hash,
                      started_at_utc=(frozen + timedelta(hours=1)).isoformat())
                 for item in rows for turn in (1, 2, 3)]
    reviews = [annotation(item["id"], turn) for item in rows for turn in (1, 2, 3)]
    alignment = sign_alignment(tmp_path, private, rows)
    before_review = sign_reviews(tmp_path, private, rows, baseline, reviews)
    after_review = sign_reviews(tmp_path, private, rows, candidate, reviews)
    margin = sign(tmp_path, private, "en-vi-margin-policy",
                  manifest_sha256=manifest_hash, baseline_results_sha256=digest(baseline),
                  margin=0.05, multiplicity_policy="bonferroni-two-directions",
                  familywise_alpha=0.05, frozen_at_utc=frozen.isoformat())
    report = paired_quality_report(rows, baseline, candidate, reviews, reviews,
                                   phase="DEV", rounds=3, alignment_attestation=alignment,
                                   baseline_review_attestation=before_review,
                                   candidate_review_attestation=after_review,
                                   margin_attestation=margin)
    assert report["noninferiority_gate"] == "PASS"
    assert {item["noninferiority_gate"] for item in report["directions"].values()} == {"PASS"}
    assert report["release_gate"] == "BLOCKED"
