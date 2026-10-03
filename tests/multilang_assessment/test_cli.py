import fcntl
import hashlib
import json
from datetime import datetime, timedelta, timezone

import httpx

import pytest

from evals.multilang.run import RunError, main as run_main
from evals.multilang.manifest import canonical_hash
from evals.multilang.evidence import EvidenceError, digest
from evals.multilang.score import main as score_main
from evals.multilang.validate_corpus import CorpusError, load_jsonl, main as corpus_main

from .test_manifest import row
from .test_score import annotation, result
from .test_run import REVISION
from .signing import configure_signer, sign
from .test_evidence import alignment_entries, sign_alignment, sign_reviews


def write_jsonl(path, rows):
    path.write_text("".join(json.dumps(item, ensure_ascii=False) + "\n" for item in rows))
    return path


def test_corpus_cli_reports_subset_without_fabricating_gold(tmp_path, capsys):
    manifest = write_jsonl(tmp_path / "manifest.jsonl", [row()])
    assert corpus_main([str(manifest), "--allow-subset"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["samples"] == 1
    assert report["directions"] == ["vi>ja"]
    assert "quality" not in report


def test_corpus_reader_rejects_malformed_jsonl(tmp_path):
    path = tmp_path / "bad.jsonl"
    path.write_text('{"id":\n')
    with pytest.raises(CorpusError, match="line 1"):
        load_jsonl(path)


def test_run_dry_plan_does_not_call_network_or_write_results(tmp_path, capsys):
    manifest = write_jsonl(tmp_path / "manifest.jsonl", [row()])
    output = tmp_path / "results.jsonl"
    assert run_main([
        "--manifest", str(manifest), "--mode", "model-only", "--split", "DEV",
        "--rounds", "3", "--output", str(output),
    ]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["scheduled_requests"] == 3
    assert report["live"] is False
    assert not output.exists()


def test_run_live_without_inventory_and_reservation_fails_closed(tmp_path):
    manifest = write_jsonl(tmp_path / "manifest.jsonl", [row()])
    with pytest.raises(RunError, match="inventory"):
        run_main([
            "--manifest", str(manifest), "--mode", "model-only", "--split", "DEV",
            "--rounds", "1", "--output", str(tmp_path / "results.jsonl"), "--live",
        ])


def test_score_cli_writes_rubric_report_without_claiming_release(tmp_path):
    sample = row()
    manifest = write_jsonl(tmp_path / "manifest.jsonl", [sample])
    scored = result("s1")
    scored["data_hash"] = canonical_hash([sample])
    results = write_jsonl(tmp_path / "results.jsonl", [scored])
    reviews = write_jsonl(tmp_path / "reviews.jsonl", [annotation("s1")])
    output = tmp_path / "score.json"
    assert score_main([
        "--manifest", str(manifest), "--results", str(results),
        "--annotations", str(reviews), "--mode", "model-only", "--phase", "DEV", "--rounds", "1",
        "--min-samples-per-direction", "1", "--output", str(output),
    ]) == 0
    report = json.loads(output.read_text())
    assert report["directions"]["vi>ja"]["rounds"]["1"]["quality_passes"] == 1
    assert report["release_gate"] == "BLOCKED"


def test_score_cli_accepts_tester_signed_alignment_and_review_bundle(tmp_path, monkeypatch):
    private, _ = configure_signer(tmp_path, monkeypatch)
    sample = row()
    scored = dict(result("s1"), data_hash=canonical_hash([sample]))
    reviews = [annotation("s1")]
    manifest_path = write_jsonl(tmp_path / "manifest.jsonl", [sample])
    results_path = write_jsonl(tmp_path / "results.jsonl", [scored])
    reviews_path = write_jsonl(tmp_path / "reviews.jsonl", reviews)
    alignment_path = tmp_path / "alignment.json"
    alignment_path.write_text(json.dumps(sign_alignment(tmp_path, private, [sample])))
    review_path = tmp_path / "review-bundle.json"
    review_path.write_text(json.dumps(sign_reviews(tmp_path, private, [sample], [scored], reviews)))
    output = tmp_path / "score.json"
    assert score_main([
        "--manifest", str(manifest_path), "--results", str(results_path),
        "--annotations", str(reviews_path), "--alignment-attestation", str(alignment_path),
        "--review-attestation", str(review_path), "--mode", "model-only", "--phase", "DEV",
        "--rounds", "1", "--min-samples-per-direction", "1", "--output", str(output),
    ]) == 0
    report = json.loads(output.read_text())
    direction = report["directions"]["vi>ja"]["rounds"]["1"]
    assert direction["quality_gate"] == "PASS"
    assert direction["off_target_gate"] == "PASS"
    assert report["release_gate"] == "BLOCKED"


@pytest.mark.parametrize("split", ["DEV", "TEST"])
def test_live_model_only_uses_mocked_endpoint_and_records_pinned_evidence(tmp_path, monkeypatch, split):
    private, _ = configure_signer(tmp_path, monkeypatch)
    sample = row(split=split)
    sample["data_classification"] = "public"
    manifest = write_jsonl(tmp_path / "manifest.jsonl", [sample])
    endpoint = "http://192.165.134.28:10739/v1"
    inventory = tmp_path / "inventory.json"
    inventory_data = {
        "instance_id": 53846446, "endpoint": endpoint,
        "model_id": "DuyTa/Cyber-F1-AWQ", "model_revision": REVISION,
        "usd_per_million_input_tokens": 1.0,
        "usd_per_million_output_tokens": 1.0,
    }
    inventory.write_text(json.dumps(inventory_data))
    alignment = sign(tmp_path, private, "alignment-map",
                     manifest_sha256=canonical_hash([sample]),
                     source_dataset_id="pinned-fixture@1234567",
                     entries=alignment_entries([sample]))
    alignment_path = tmp_path / "alignment.json"
    alignment_path.write_text(json.dumps(alignment))
    now = datetime.now(timezone.utc)
    lease = tmp_path / "lease.json"
    prompt = tmp_path / "prompt.txt"
    prompt.write_text("Translate {source_lang} to {target_lang}.")
    decoding = tmp_path / "decoding.json"
    decoding_data = {"temperature": 0.0, "top_p": 1.0, "max_tokens": 64}
    decoding.write_text(json.dumps(decoding_data))
    prompt_hash = hashlib.sha256(prompt.read_text().encode()).hexdigest()
    config_data = json.dumps({
        "mode": "model-only", "model_id": "DuyTa/Cyber-F1-AWQ",
        "model_revision": REVISION, "endpoint": endpoint,
        "decoding": decoding_data, "prompt_hash": prompt_hash,
    }, sort_keys=True, separators=(",", ":"))
    config_hash = hashlib.sha256(config_data.encode()).hexdigest()

    def make_lease(run_id, output_path, max_requests=1, holdout_id=None):
        payload = {
            "status": "granted", "owner": "multilang-assessment", "ocr_released": True,
            "instance_id": 53846446, "endpoint": endpoint, "model_id": "DuyTa/Cyber-F1-AWQ",
            "model_revision": REVISION, "starts_at": (now - timedelta(minutes=1)).isoformat(),
            "ends_at": (now + timedelta(minutes=5)).isoformat(), "max_requests": max_requests,
            "max_rpm": 60, "max_cost_usd": 0.05,
            "worst_case_cost_usd_per_request": 0.01,
            "allowed_modes": ["model-only"], "run_id": run_id,
            "data_hash": canonical_hash([sample]), "config_hash": config_hash,
            "inventory_sha256": digest(inventory_data),
            "alignment_map_sha256": digest(alignment["payload"]),
            "output_path_sha256": hashlib.sha256(str(output_path.resolve()).encode()).hexdigest(),
        }
        if split == "TEST":
            payload.update(sealed_test_hash=canonical_hash([sample]),
                           candidate_config_hash=config_hash,
                           holdout_id=holdout_id or "sealed-" + run_id)
        envelope = sign(tmp_path, private, "run-lease", **payload)
        lease.write_text(json.dumps(envelope))
        return envelope
    seen = []

    def handle(request):
        seen.append(request.url.path)
        if request.url.path == "/v1/models":
            return httpx.Response(200, json={"data": [{"id": "DuyTa/Cyber-F1-AWQ"}]})
        return httpx.Response(200, json={
            "model": "DuyTa/Cyber-F1-AWQ",
            "choices": [{"message": {"content": "こんにちは。"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 7, "completion_tokens": 3},
        })

    real_client = httpx.Client
    real_async_client = httpx.AsyncClient
    monkeypatch.setattr("evals.multilang.run.httpx.Client",
                        lambda **kwargs: real_client(transport=httpx.MockTransport(handle), **kwargs))
    monkeypatch.setattr("evals.multilang.run.httpx.AsyncClient",
                        lambda **kwargs: real_async_client(transport=httpx.MockTransport(handle), **kwargs))
    output = tmp_path / "results.jsonl"
    make_lease("assessment-run-001", output)
    assert run_main([
        "--manifest", str(manifest), "--mode", "model-only", "--split", split,
        "--rounds", "1", "--output", str(output), "--live",
        "--inventory", str(inventory), "--reservation", str(lease),
        "--alignment-attestation", str(alignment_path),
        "--prompt", str(prompt), "--decoding", str(decoding),
    ]) == 0
    recorded = load_jsonl(output)
    assert seen == ["/v1/models", "/v1/chat/completions"]

    assert recorded[0]["model_revision"] == REVISION
    assert recorded[0]["error"] is None
    assert recorded[0]["cost_usd"] == 0.00001

    replay_output = tmp_path / "replay.jsonl"
    make_lease("assessment-run-002" if split == "TEST" else "assessment-run-001",
               replay_output, holdout_id="sealed-assessment-run-001")
    seen.clear()
    with pytest.raises(EvidenceError, match="consumed"):
        run_main([
            "--manifest", str(manifest), "--mode", "model-only", "--split", split,
            "--rounds", "1", "--output", str(replay_output), "--live",
            "--inventory", str(inventory), "--reservation", str(lease),
            "--alignment-attestation", str(alignment_path),
            "--prompt", str(prompt), "--decoding", str(decoding),
        ])
    assert seen == []

    revoked_output = tmp_path / "revoked.jsonl"
    lease_envelope = make_lease("assessment-run-004", revoked_output, max_requests=2)
    seen.clear()

    def revoke_while_waiting(_seconds):
        if _seconds < 0.1:
            return
        changed = dict(lease_envelope["payload"])
        changed.pop("schema")
        changed.pop("kind")
        changed["ocr_released"] = False
        lease.write_text(json.dumps(sign(tmp_path, private, "run-lease", **changed)))

    monkeypatch.setattr("evals.multilang.run.time.sleep", revoke_while_waiting)
    with pytest.raises(RunError, match="reservation artifact changed"):
        run_main([
            "--manifest", str(manifest), "--mode", "model-only", "--split", split,
            "--rounds", "2", "--output", str(revoked_output), "--live",
            "--inventory", str(inventory), "--reservation", str(lease),
            "--alignment-attestation", str(alignment_path),
            "--prompt", str(prompt), "--decoding", str(decoding),
        ])
    assert seen == ["/v1/models", "/v1/chat/completions"]

    locked_output = tmp_path / "locked.jsonl"
    make_lease("assessment-run-005", locked_output)
    lock_path = lease.with_suffix(lease.suffix + ".lock")
    with lock_path.open("a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(RunError, match="lock is held"):
            run_main([
                "--manifest", str(manifest), "--mode", "model-only", "--split", split,
                "--rounds", "1", "--output", str(locked_output), "--live",
                "--inventory", str(inventory), "--reservation", str(lease),
                "--alignment-attestation", str(alignment_path),
                "--prompt", str(prompt), "--decoding", str(decoding),
            ])
