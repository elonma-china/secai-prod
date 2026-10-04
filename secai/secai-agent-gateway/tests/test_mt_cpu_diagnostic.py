from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sys
import zipfile

import pytest
from docx import Document

from deploy.mt_cpu_service import diagnostic
from evals.multilang.prepare_multifile_dev_grouped import APPROVED_DEV_SHA256, DIRECTIONS


@pytest.fixture(autouse=True)
def restore_process_umask():
    prior = os.umask(0o077)
    os.umask(prior)
    yield
    os.umask(prior)


def _grouped_inputs(tmp_path):
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    files = []
    plan = []
    for direction in DIRECTIONS:
        source, target = direction.split(">")
        source = "zh" if source == "zh-Hans" else source
        target = "zh" if target == "zh-Hans" else target
        path = inputs / f"{source}-to-{target}.docx"
        document = Document()
        blocks = []
        for index in range(30):
            text = f"Prepared {direction} source {index}"
            document.add_paragraph(text)
            blocks.append({"index": index, "block_id": f"b{index + 1:06d}",
                           "source_sha256": hashlib.sha256(text.encode()).hexdigest()})
        document.save(path)
        files.append({"path": str(path), "direction": direction,
                      "source_lang": source, "target_lang": target, "blocks": blocks})
        plan.append({"path": str(path), "source_lang": source, "target_lang": target})
    (inputs / "sample-block-map.json").write_text(json.dumps({
        "schema": "translate-v2-grouped-dev-v1",
        "manifest_sha256": APPROVED_DEV_SHA256, "files": files,
    }))
    (inputs / "batch-plan.jsonl").write_text(
        "".join(json.dumps(item) + "\n" for item in plan))


def test_live_requires_explicit_gate_before_any_network(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "argv", ["diagnostic", "run", "--work-dir", str(tmp_path),
                                  "--base-url", "http://127.0.0.1:18181"])
    with pytest.raises(SystemExit, match="2"):
        diagnostic.main()


def test_live_rejects_shared_work_directory_before_network(monkeypatch, tmp_path):
    tmp_path.chmod(0o755)
    monkeypatch.setattr(sys, "argv", ["diagnostic", "run", "--work-dir", str(tmp_path),
                                  "--base-url", "http://127.0.0.1:18181", "--allow-live"])
    with pytest.raises(SystemExit, match="2"):
        diagnostic.main()


@pytest.mark.parametrize("url", ["http://example.com:18181", "http://127.0.0.1:18181/redirect",
                                  "https://127.0.0.1:18181", "http://user:pass@127.0.0.1:18181"])
def test_live_gateway_must_be_plain_loopback(url):
    with pytest.raises(ValueError, match="loopback"):
        diagnostic._loopback_url(url)


def test_live_batch_uses_private_http_and_two_durable_jobs(monkeypatch, tmp_path):
    _grouped_inputs(tmp_path)
    assert diagnostic._create_seal(tmp_path) == 18
    observed = {}

    class FakeClient:
        def __init__(self, **kwargs):
            observed.update(kwargs)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

    async def fake_run(client, entries, manifest, results, **kwargs):
        assert client is not None
        assert entries is None  # The offline-sealed manifest owns the file digests.
        assert manifest.parent == tmp_path
        assert results == tmp_path / "results"
        observed.update(kwargs)
        return {"succeeded": 18, "failed": 0, "unfinished": 0}

    monkeypatch.setattr(diagnostic.httpx, "AsyncClient", FakeClient)
    monkeypatch.setattr(diagnostic, "run_batch", fake_run)
    result = asyncio.run(diagnostic._live("run", tmp_path, "http://127.0.0.1:18181"))
    assert result["succeeded"] == 18
    assert observed == {"base_url": "http://127.0.0.1:18181", "trust_env": False,
                        "follow_redirects": False, "max_inflight": 2,
                        "upload_interval": 12, "poll_interval": 4, "job_timeout": 7500}


def test_tampered_plan_rejected_before_network(monkeypatch, tmp_path):
    _grouped_inputs(tmp_path)
    path = tmp_path / "inputs/batch-plan.jsonl"
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    rows[0]["path"] = "/tmp/unrelated.docx"
    path.write_text("".join(json.dumps(item) + "\n" for item in rows))
    with pytest.raises(ValueError, match="differs"):
        diagnostic._entries(tmp_path)


def test_changed_source_docx_rejected_before_client_creation(monkeypatch, tmp_path):
    _grouped_inputs(tmp_path)
    assert diagnostic._create_seal(tmp_path) == 18
    path = next((tmp_path / "inputs").glob("*.docx"))
    document = Document(path)
    document.paragraphs[0].text = "MUTATED SOURCE"
    document.save(path)

    def forbidden_client(**kwargs):
        raise AssertionError("network client must not be created")

    monkeypatch.setattr(diagnostic.httpx, "AsyncClient", forbidden_client)
    with pytest.raises(ValueError, match="source block"):
        asyncio.run(diagnostic._live("run", tmp_path, "http://127.0.0.1:18181"))


def test_changed_docx_bytes_with_same_source_rejected_before_network(monkeypatch, tmp_path):
    _grouped_inputs(tmp_path)
    assert diagnostic._create_seal(tmp_path) == 18
    path = next((tmp_path / "inputs").glob("*.docx"))
    with zipfile.ZipFile(path, "a") as archive:
        archive.comment = b"changed after prepare"

    def forbidden_client(**kwargs):
        raise AssertionError("network client must not be created")

    monkeypatch.setattr(diagnostic.httpx, "AsyncClient", forbidden_client)
    with pytest.raises(ValueError, match="digest"):
        asyncio.run(diagnostic._live("run", tmp_path, "http://127.0.0.1:18181"))


def test_v3_smoke_uses_five_sequential_public_dev_and_literal_requests(monkeypatch, tmp_path):
    directions = ("vi>ja", "ja>vi", "vi>zh-Hans", "vi>ar")
    manifest = tmp_path / "dev.jsonl"
    manifest.write_text("".join(json.dumps({"direction": direction, "source": "Xin chào",
                                            "id": f"sample-{index}"}) + "\n"
                                for index, direction in enumerate(directions)))
    monkeypatch.setattr(diagnostic, "APPROVED_DEV_SHA256",
                        hashlib.sha256(manifest.read_bytes()).hexdigest())
    monkeypatch.setattr(diagnostic, "_verified_batch", lambda work_dir: None)
    calls = []

    class Response:
        status_code = 200

        def __init__(self, translated_text):
            self.translated_text = translated_text

        def json(self):
            return {"translated_text": self.translated_text, "metadata": {"attempts": 1}}

    class Client:
        def __init__(self, **kwargs):
            assert kwargs["trust_env"] is False
            assert kwargs["follow_redirects"] is False

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def post(self, path, *, json):
            calls.append((path, json))
            return Response(json["text"] if "CVE-2024-12345" in json["text"] else "Hello")

    monkeypatch.setattr(diagnostic.httpx, "AsyncClient", Client)
    result = asyncio.run(diagnostic._v3_smoke(tmp_path, "http://127.0.0.1:18181", manifest))
    assert result == {"ok": 5, "total": 5}
    assert [body["target_lang"] for _, body in calls] == ["ja", "vi", "zh-Hans", "ar", "ja"]
    saved = json.loads((tmp_path / "v3-smoke.json").read_text())
    assert len(saved["results"]) == 5
    assert saved["results"][-1]["literal_ok"] is True


def test_v3_smoke_requires_live_gate(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "argv", ["diagnostic", "v3-smoke", "--work-dir", str(tmp_path),
                                  "--dev-manifest", str(tmp_path / "dev.jsonl"),
                                  "--base-url", "http://127.0.0.1:18181"])
    with pytest.raises(SystemExit, match="2"):
        diagnostic.main()


def test_v3_smoke_rejects_unfinished_batch_before_client_creation(monkeypatch, tmp_path):
    manifest = tmp_path / "dev.jsonl"
    manifest.write_text("{}\n")
    monkeypatch.setattr(diagnostic, "APPROVED_DEV_SHA256",
                        hashlib.sha256(manifest.read_bytes()).hexdigest())

    def forbidden_client(**kwargs):
        raise AssertionError("network client must not be created")

    monkeypatch.setattr(diagnostic.httpx, "AsyncClient", forbidden_client)
    with pytest.raises(ValueError, match="batch"):
        asyncio.run(diagnostic._v3_smoke(tmp_path, "http://127.0.0.1:18181", manifest))
