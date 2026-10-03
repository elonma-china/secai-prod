"""Verify tester-issued evidence and consume signed run/holdout tickets once."""

from __future__ import annotations

import base64
import fcntl
import hashlib
import json
import math
import os
import re
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from .manifest import canonical_hash


class EvidenceError(ValueError):
    """Tester evidence is absent, invalid, stale, or already consumed."""


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def digest(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def _trusted_key() -> Path:
    key_name = os.environ.get("MULTILANG_TESTER_PUBKEY_PATH")
    expected = os.environ.get("MULTILANG_TESTER_PUBKEY_SHA256")
    if not key_name or not expected or re.fullmatch(r"[0-9a-f]{64}", expected) is None:
        raise EvidenceError("configured tester public-key trust anchor is required")
    key = Path(key_name)
    if key.is_symlink() or not key.is_file() or key.stat().st_mode & 0o022:
        raise EvidenceError("tester public-key file is absent or writable by others")
    if hashlib.sha256(key.read_bytes()).hexdigest() != expected:
        raise EvidenceError("tester public-key fingerprint mismatch")
    return key


def verify_envelope(envelope: dict[str, Any], kind: str) -> dict[str, Any]:
    if not isinstance(envelope, dict) or set(envelope) != {"payload", "signature_b64"}:
        raise EvidenceError("signed evidence envelope is malformed")
    payload = envelope["payload"]
    if not isinstance(payload, dict) or payload.get("schema") != "multilang-evidence-v1" or payload.get("kind") != kind:
        raise EvidenceError("signed evidence kind/schema mismatch")
    try:
        signature = base64.b64decode(envelope["signature_b64"], validate=True)
        data = canonical_bytes(payload)
    except (TypeError, ValueError) as exc:
        raise EvidenceError("signed evidence encoding is invalid") from exc
    key = _trusted_key()
    with tempfile.TemporaryDirectory(prefix="multilang-verify-") as directory:
        message = Path(directory) / "payload.json"
        sig = Path(directory) / "signature.bin"
        message.write_bytes(data)
        sig.write_bytes(signature)
        try:
            verified = subprocess.run(
                ["openssl", "pkeyutl", "-verify", "-pubin", "-inkey", str(key),
                 "-rawin", "-in", str(message), "-sigfile", str(sig)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
                timeout=10,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise EvidenceError("OpenSSL signature verifier unavailable") from exc
    if verified.returncode != 0:
        raise EvidenceError("tester signature verification failed")
    return payload


def verify_alignment(rows: Iterable[dict[str, Any]], envelope: dict[str, Any]) -> str:
    records = list(rows)
    payload = verify_envelope(envelope, "alignment-map")
    expected = {
        row["id"]: {
            "direction": row["direction"], "split": row["split"],
            "aligned_item_id": row["aligned_item_id"], "source_group": row["source_group"],
            "source_revision": row["source_revision"],
            "source_sha256": hashlib.sha256(row["source"].encode()).hexdigest(),
            "reference_sha256": hashlib.sha256(row["reference"].encode()).hexdigest(),
        }
        for row in records
    }
    if payload.get("manifest_sha256") != canonical_hash(records) or payload.get("entries") != expected:
        raise EvidenceError("alignment map differs from pinned corpus records")
    if not isinstance(payload.get("source_dataset_id"), str) or not payload["source_dataset_id"].strip():
        raise EvidenceError("alignment source dataset ID is missing")
    return digest(payload)


def verify_reviews(
    rows: Iterable[dict[str, Any]], results: Iterable[dict[str, Any]],
    annotations: Iterable[dict[str, Any]], envelope: dict[str, Any],
) -> str:
    records, outputs, reviews = list(rows), list(results), list(annotations)
    payload = verify_envelope(envelope, "review-bundle")
    expected = {
        "manifest_sha256": canonical_hash(records),
        "results_sha256": digest(outputs),
        "annotations_sha256": digest(reviews),
        "reviewer_ids": sorted({item["reviewer"] for item in reviews}),
        "calibration_ids": sorted({item["calibration_id"] for item in reviews}),
    }
    if any(payload.get(key) != value for key, value in expected.items()):
        raise EvidenceError("review bundle differs from scored outputs or annotations")
    if payload.get("blind_attested") is not True or payload.get("qualification_attested") is not True:
        raise EvidenceError("tester did not attest blind qualified review")
    return digest(payload)


def verify_margin_policy(
    rows: Iterable[dict[str, Any]], baseline: Iterable[dict[str, Any]],
    candidate: Iterable[dict[str, Any]], envelope: dict[str, Any],
) -> tuple[float, str]:
    records, before, after = list(rows), list(baseline), list(candidate)
    payload = verify_envelope(envelope, "en-vi-margin-policy")
    margin = payload.get("margin")
    if (type(margin) not in (int, float) or not math.isfinite(margin) or not 0 <= margin <= 1 or
            payload.get("multiplicity_policy") != "bonferroni-two-directions" or
            payload.get("familywise_alpha") != 0.05):
        raise EvidenceError("margin or multiplicity policy is invalid")
    if payload.get("manifest_sha256") != canonical_hash(records) or payload.get("baseline_results_sha256") != digest(before):
        raise EvidenceError("margin policy is not bound to baseline and corpus")
    try:
        frozen = datetime.fromisoformat(payload["frozen_at_utc"])
        starts = [datetime.fromisoformat(item["started_at_utc"]) for item in after]
        completions = [datetime.fromisoformat(item["completed_at_utc"]) for item in before]
        if (frozen.tzinfo is None or not starts or not completions or
                any(start.tzinfo is None or start <= frozen for start in starts) or
                any(end.tzinfo is None or end > frozen for end in completions)):
            raise ValueError
    except (KeyError, TypeError, ValueError) as exc:
        raise EvidenceError("margin policy was not demonstrably frozen before candidate run") from exc
    return float(margin), digest(payload)


def claim_signed_run(lease: dict[str, Any], envelope: dict[str, Any]) -> Path:
    """Consume a lease and optional TEST holdout before the first network call."""
    if verify_envelope(envelope, "run-lease") != lease:
        raise EvidenceError("run claim does not match signed lease")
    directory_name = os.environ.get("MULTILANG_TESTER_LEDGER_DIR")
    if not directory_name:
        raise EvidenceError("tester-owned ledger directory is required")
    directory = Path(directory_name)
    if directory.is_symlink() or not directory.is_dir() or directory.stat().st_mode & 0o022:
        raise EvidenceError("tester ledger directory is absent or writable by others")
    run_id = lease.get("run_id")
    if not isinstance(run_id, str) or re.fullmatch(r"[A-Za-z0-9_-]{8,100}", run_id) is None:
        raise EvidenceError("signed lease run_id is invalid")
    holdout_id = lease.get("holdout_id")
    if holdout_id is not None and (not isinstance(holdout_id, str) or re.fullmatch(r"[A-Za-z0-9_-]{8,100}", holdout_id) is None):
        raise EvidenceError("signed holdout_id is invalid")
    artifact_hash = digest(envelope)
    run_name = "run-" + hashlib.sha256(run_id.encode()).hexdigest() + ".json"
    names = []
    if holdout_id is not None:
        # Retire TEST first: a crash between exclusive creates cannot leave a reusable holdout.
        names.append("holdout-" + hashlib.sha256(holdout_id.encode()).hexdigest() + ".json")
    names.append(run_name)
    lock_fd = os.open(directory / ".claims.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        if any((directory / name).exists() or (directory / name).is_symlink() for name in names):
            raise EvidenceError("signed lease or TEST holdout has already been consumed")
        for name in names:
            path = directory / name
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
            try:
                record = {"status": "retired-on-claim", "run_id": run_id,
                          "holdout_id": holdout_id, "lease_envelope_sha256": artifact_hash,
                          "data_hash": lease.get("data_hash"), "config_hash": lease.get("config_hash")}
                os.write(fd, canonical_bytes(record) + b"\n")
                os.fsync(fd)
            finally:
                os.close(fd)
        dir_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)
    return directory / run_name
