"""CPU-only private broker contract and shared-stage ordering checks."""
from __future__ import annotations

import asyncio
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

from deploy.gpu_stage.broker import (
    _mt_command, _mt_proof, _ocr_command, _ocr_proof, _ocr_result, _ocr_work,
    create_broker_app, create_gpu_stage_app,
)
from deploy.gpu_stage.coordinator import GpuStageQueue, StageFailure
from deploy.gpu_stage.owner import PinnedCommand


class FakeOwner:
    def __init__(self):
        self.calls = []
        self.first_started = asyncio.Event()
        self.release_first = asyncio.Event()

    async def run(self, command, work, prove_owner, *, deadline, cancel):
        stage = "ocr" if command.stdio == "pipes" else "mt"
        self.calls.append(stage)
        if len(self.calls) == 1:
            self.first_started.set()
            await self.release_first.wait()
        return ({"model": "Qwen/Qwen3-VL-2B-Instruct",
                 "blocks": [{"text": "xin chào", "bbox": None, "confidence": None}]}
                if stage == "ocr" else "Bonjour")


@pytest.mark.asyncio
async def test_ocr_and_mt_share_one_stage_slot_and_private_envelopes():
    owner = FakeOwner()
    app = create_broker_app(owner=owner, queue=GpuStageQueue(max_pending=2),
                            mt_token="private-mt-test-token", ocr_token="private-ocr-test-token",
                            enable_mt=True, enable_ocr=True)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://127.0.0.1") as client:
        ocr = asyncio.create_task(client.post("/v1/ocr", json={
            "model": "Qwen/Qwen3-VL-2B-Instruct", "image_base64": "aW1hZ2U="},
            headers={"Authorization": "Bearer private-ocr-test-token"}))
        await asyncio.wait_for(owner.first_started.wait(), 1)
        mt = asyncio.create_task(client.post("/v1/translate", json={
            "text": "Xin chào", "source_lang": "vi", "target_lang": "fr"},
            headers={"Authorization": "Bearer private-mt-test-token"}))
        await asyncio.sleep(.02)
        assert owner.calls == ["ocr"]
        owner.release_first.set()
        ocr_result, mt_result = await asyncio.gather(ocr, mt)
    assert ocr_result.status_code == mt_result.status_code == 200
    assert set(ocr_result.json()) == {"model", "blocks"}
    assert mt_result.json() == {"translated_text": "Bonjour", "model": "tencent/Hy-MT2-7B"}
    assert owner.calls == ["ocr", "mt"]


@pytest.mark.asyncio
async def test_ocr_flag_auth_and_shape_fail_before_stage_admission():
    owner = FakeOwner()
    app = create_broker_app(owner=owner, queue=GpuStageQueue(max_pending=2),
                            mt_token="private-mt-test-token", ocr_token="private-ocr-test-token",
                            enable_mt=True, enable_ocr=False)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://127.0.0.1") as client:
        body = {"model": "Qwen/Qwen3-VL-2B-Instruct", "image_base64": "aW1hZ2U="}
        assert (await client.post("/v1/ocr", json=body)).status_code == 401
        assert (await client.post("/v1/ocr", json=body, headers={
            "Authorization": "Bearer private-ocr-test-token"})).status_code == 503
        assert (await client.post("/v1/ocr", json={**body, "unexpected": 1}, headers={
            "Authorization": "Bearer private-ocr-test-token"})).status_code == 400
    assert owner.calls == []


@pytest.mark.asyncio
async def test_mt_gpu_pid_proof_requires_exact_listener_and_full_offload(monkeypatch, tmp_path):
    from deploy.gpu_stage import broker

    class Child:
        pid = 456

    command = PinnedCommand(Path(sys.executable), (), "irrelevant", (),
                            stdio="log", log_path=tmp_path / "mt.log")
    command.log_path.write_text("offloaded 33/33 layers to GPU", encoding="utf-8")
    monkeypatch.setattr(broker.gpu, "listener_pid", lambda: 999)
    monkeypatch.setattr(broker.gpu, "_full_offload", lambda _path: True)
    assert await _mt_proof(Child(), 789, command) is False
    monkeypatch.setattr(broker.gpu, "listener_pid", lambda: None)
    assert await _mt_proof(Child(), 789, command) is None
    monkeypatch.setattr(broker.gpu, "listener_pid", lambda: 456)
    assert await _mt_proof(Child(), 789, command) is True
    monkeypatch.setattr(broker.gpu, "_full_offload", lambda _path: False)
    assert await _mt_proof(Child(), 789, command) is None


@pytest.mark.asyncio
async def test_ocr_pid_file_proof_and_single_child_wire(tmp_path):
    owner_file = tmp_path / "host-pid"
    command = PinnedCommand(Path(sys.executable), (), "irrelevant", (),
                            environment={"OCR_OWNED_PID_FILE": str(owner_file)}, stdio="pipes")
    assert await _ocr_proof(None, 321, command) is None
    owner_file.write_text("999", encoding="ascii")
    assert await _ocr_proof(None, 321, command) is False
    owner_file.write_text("321", encoding="ascii")
    assert await _ocr_proof(None, 321, command) is True

    script = ("import json,sys; body=json.load(sys.stdin); "
              "assert body['calibration_prompt']=='conditional'; "
              "print(json.dumps({'status':200,'body':{'model':body['model'],"
              "'blocks':[{'text':'xin chào','bbox':None,'confidence':None}]}}))")
    child = subprocess.Popen([sys.executable, "-c", script], stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    value = await _ocr_work(child, asyncio.Event(), time.monotonic() + 2,
                            {"model": "Qwen/Qwen3-VL-2B-Instruct", "image_base64": "aW1hZ2U="})
    assert value == {"model": "Qwen/Qwen3-VL-2B-Instruct",
                     "blocks": [{"text": "xin chào", "bbox": None, "confidence": None}]}
    assert child.wait(timeout=1) == 0


def test_ocr_result_rejects_invalid_unicode_and_shape():
    assert not _ocr_result({"model": "Qwen/Qwen3-VL-2B-Instruct", "blocks": [
        {"text": "\ud800", "bbox": None, "confidence": None}]})
    assert not _ocr_result({"model": "Qwen/Qwen3-VL-2B-Instruct", "blocks": [
        {"text": "ok", "bbox": [1, 2, 3], "confidence": None}]})


def test_file_mt_stage_uses_reviewed_4096_context_recipe():
    command = _mt_command()
    index = command.args.index("--ctx-size")
    assert command.args[index + 1] == "4096"
    assert command.args[command.args.index("--n-gpu-layers") + 1] == "99"
    assert command.args[command.args.index("--api-key-file") + 1] != ""
    assert command.sha256 and command.stdio == "log"
    assert command.environment["LD_LIBRARY_PATH"] == (
        "/usr/local/cuda-13.0/compat:/usr/local/cuda-13.0/lib64")


@pytest.mark.asyncio
async def test_artifact_or_process_error_is_private_unavailable_response():
    class FailingOwner:
        async def run(self, *_args, **_kwargs):
            raise OSError("private filesystem path")

    app = create_broker_app(owner=FailingOwner(), queue=GpuStageQueue(max_pending=2),
                            mt_token="private-mt-test-token", ocr_token="",
                            enable_mt=True)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://127.0.0.1") as client:
        response = await client.post("/v1/translate", json={
            "text": "Xin chào", "source_lang": "vi", "target_lang": "fr"},
            headers={"Authorization": "Bearer private-mt-test-token"})
    assert response.status_code == 503
    assert response.json() == {"error": "UNAVAILABLE"}
    assert "private filesystem path" not in response.text


def test_broker_factory_rejects_prior_guard_cleanup_poison(tmp_path, monkeypatch):
    from deploy.gpu_stage import broker

    token = tmp_path / "mt-token"
    token.write_text("m" * 32, encoding="ascii")
    token.chmod(0o600)
    poison = tmp_path / "poisoned-cleanup.json"
    poison.write_text("{}", encoding="ascii")
    monkeypatch.setattr(broker.gpu, "POISONED", poison)
    monkeypatch.setenv("GPU_STAGE_BROKER_ENABLED", "1")
    monkeypatch.setenv("GPU_STAGE_MT_ENABLED", "1")
    monkeypatch.setenv("GPU_STAGE_MT_TOKEN_FILE", str(token))
    with pytest.raises(RuntimeError, match="cleanup"):
        create_gpu_stage_app()


def test_ocr_weight_attestation_is_bound_to_loaded_snapshot(tmp_path, monkeypatch):
    model = tmp_path / "model"
    model.mkdir()
    different = tmp_path / "different.safetensors"
    monkeypatch.setenv("GPU_STAGE_OCR_MODEL_PATH", str(model))
    monkeypatch.setenv("GPU_STAGE_OCR_WEIGHT_PATH", str(different))
    with pytest.raises(StageFailure, match="ARTIFACT"):
        _ocr_command()
