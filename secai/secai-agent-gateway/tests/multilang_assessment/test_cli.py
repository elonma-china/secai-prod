import fcntl
import json
from datetime import datetime, timedelta, timezone

import httpx

import pytest

from evals.multilang.run import RunError, main as run_main
from evals.multilang.manifest import canonical_hash
from evals.multilang.score import main as score_main
from evals.multilang.validate_corpus import CorpusError, load_jsonl, main as corpus_main

from .test_manifest import row
from .test_score import annotation, result
from .test_run import REVISION


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


def test_live_model_only_uses_mocked_endpoint_and_records_pinned_evidence(tmp_path, monkeypatch):
    sample = row()
    sample["data_classification"] = "public"
    manifest = write_jsonl(tmp_path / "manifest.jsonl", [sample])
    endpoint = "http://192.165.134.28:10739/v1"
    inventory = tmp_path / "inventory.json"
    inventory.write_text(json.dumps({
        "instance_id": 53846446, "endpoint": endpoint,
        "model_id": "DuyTa/Cyber-F1-AWQ", "model_revision": REVISION,
        "usd_per_million_input_tokens": 1.0,
        "usd_per_million_output_tokens": 1.0,
    }))
    now = datetime.now(timezone.utc)
    lease = tmp_path / "lease.json"
    lease.write_text(json.dumps({
        "status": "granted", "owner": "multilang-assessment", "ocr_released": True,
        "instance_id": 53846446, "endpoint": endpoint, "model_id": "DuyTa/Cyber-F1-AWQ",
        "model_revision": REVISION, "starts_at": (now - timedelta(minutes=1)).isoformat(),
        "ends_at": (now + timedelta(minutes=5)).isoformat(), "max_requests": 1,
        "max_rpm": 6000, "max_cost_usd": 0.05,
        "worst_case_cost_usd_per_request": 0.01,
        "allowed_modes": ["model-only"],
    }))
    prompt = tmp_path / "prompt.txt"
    prompt.write_text("Translate {source_lang} to {target_lang}.")
    decoding = tmp_path / "decoding.json"
    decoding.write_text(json.dumps({"temperature": 0.0, "top_p": 1.0, "max_tokens": 64}))
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
    monkeypatch.setattr("evals.multilang.run.httpx.Client",
                        lambda **kwargs: real_client(transport=httpx.MockTransport(handle), **kwargs))
    output = tmp_path / "results.jsonl"
    assert run_main([
        "--manifest", str(manifest), "--mode", "model-only", "--split", "DEV",
        "--rounds", "1", "--output", str(output), "--live",
        "--inventory", str(inventory), "--reservation", str(lease),
        "--prompt", str(prompt), "--decoding", str(decoding),
    ]) == 0
    recorded = load_jsonl(output)
    assert seen == ["/v1/models", "/v1/chat/completions"]

    assert recorded[0]["model_revision"] == REVISION
    assert recorded[0]["error"] is None
    assert recorded[0]["cost_usd"] == 0.00001

    lease_payload = json.loads(lease.read_text())
    lease_payload["max_requests"] = 2
    lease.write_text(json.dumps(lease_payload))
    seen.clear()

    def revoke_while_waiting(_seconds):
        lease_payload["ocr_released"] = False
        lease.write_text(json.dumps(lease_payload))

    monkeypatch.setattr("evals.multilang.run.time.sleep", revoke_while_waiting)
    with pytest.raises(RunError, match="OCR"):
        run_main([
            "--manifest", str(manifest), "--mode", "model-only", "--split", "DEV",
            "--rounds", "2", "--output", str(tmp_path / "revoked.jsonl"), "--live",
            "--inventory", str(inventory), "--reservation", str(lease),
            "--prompt", str(prompt), "--decoding", str(decoding),
        ])
    assert seen == ["/v1/models", "/v1/chat/completions"]

    lease_payload["ocr_released"] = True
    lease_payload["max_requests"] = 1
    lease.write_text(json.dumps(lease_payload))
    lock_path = lease.with_suffix(lease.suffix + ".lock")
    with lock_path.open("a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(RunError, match="lock is held"):
            run_main([
                "--manifest", str(manifest), "--mode", "model-only", "--split", "DEV",
                "--rounds", "1", "--output", str(tmp_path / "locked.jsonl"), "--live",
                "--inventory", str(inventory), "--reservation", str(lease),
                "--prompt", str(prompt), "--decoding", str(decoding),
            ])
