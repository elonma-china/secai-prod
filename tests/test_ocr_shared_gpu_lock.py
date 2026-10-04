"""CPU-only proof that the existing OCR child shares MT's GPU flock."""
from __future__ import annotations

import asyncio
import base64
import fcntl
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "evals/translation_ocr/real_quality"))
import qwen_ocr_supervisor as supervisor  # noqa: E402


class _Connected:
    async def is_disconnected(self):
        return False


def _request():
    return supervisor.OcrRequest(model=supervisor.MODEL_ID,
                                 image_base64=base64.b64encode(b"image").decode())


@pytest.mark.asyncio
async def test_busy_shared_lock_rejects_before_gpu_baseline(tmp_path, monkeypatch):
    path = tmp_path / "admission.lock"
    monkeypatch.setenv("OCR_SHARED_GPU_LOCK_PATH", str(path))
    monkeypatch.setenv("OCR_SERVICE_TOKEN", "private-test-token")
    monkeypatch.setattr(supervisor, "_baseline", None)
    baseline_calls = []
    monkeypatch.setattr(supervisor, "_snapshot", lambda: baseline_calls.append(True))
    with path.open("a+b") as held:
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(supervisor.HTTPException) as error:
            await supervisor.ocr(_request(), _Connected(), "Bearer private-test-token", "test")
        assert error.value.status_code == 503
        assert not baseline_calls
    assert not supervisor._lock.locked()


@pytest.mark.asyncio
@pytest.mark.parametrize("history", ["poison", "missing", "false", "corrupt"])
async def test_unproved_shared_gpu_history_vetoes_ocr_before_baseline(
        tmp_path, monkeypatch, history):
    path = tmp_path / "admission.lock"
    monkeypatch.setenv("OCR_SHARED_GPU_LOCK_PATH", str(path))
    monkeypatch.setenv("OCR_SERVICE_TOKEN", "private-test-token")
    monkeypatch.setattr(supervisor, "_baseline", None)
    baseline_calls = []
    monkeypatch.setattr(supervisor, "_snapshot", lambda: baseline_calls.append(True))
    if history == "poison":
        (tmp_path / "poisoned-cleanup.json").write_text("{}", encoding="utf-8")
    else:
        record = tmp_path / "evidence/gpu-run-broker-incomplete"
        record.mkdir(parents=True)
        if history == "false":
            (record / "summary.json").write_text(
                json.dumps({"clean_exit_attested": False}), encoding="utf-8")
        if history == "corrupt":
            (record / "summary.json").write_text("[", encoding="utf-8")
    with pytest.raises(supervisor.HTTPException) as error:
        await supervisor.ocr(_request(), _Connected(), "Bearer private-test-token", "test")
    assert error.value.status_code == 503
    assert not baseline_calls and not supervisor._lock.locked()
    with path.open("a+b") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)


def test_proven_shared_gpu_history_allows_lock(tmp_path, monkeypatch):
    path = tmp_path / "admission.lock"
    record = tmp_path / "evidence/gpu-run-broker-complete"
    record.mkdir(parents=True)
    (record / "summary.json").write_text(
        json.dumps({"clean_exit_attested": True}), encoding="utf-8")
    monkeypatch.setenv("OCR_SHARED_GPU_LOCK_PATH", str(path))
    descriptor = supervisor._acquire_shared_gpu_lock()
    os.close(descriptor)


@pytest.mark.asyncio
async def test_ocr_child_inherits_same_lock_fd_without_pid_wrapper(tmp_path, monkeypatch):
    path = tmp_path / "admission.lock"
    child = tmp_path / "fake_ocr_child.py"
    child.write_text(
        "import json,os,sys,time\n"
        "body=json.load(sys.stdin)\n"
        "target=os.environ['OCR_SHARED_GPU_LOCK_PATH']\n"
        "held=False\n"
        "for fd in os.listdir('/proc/self/fd'):\n"
        " try:\n"
        "  held |= os.readlink('/proc/self/fd/'+fd)==target\n"
        " except OSError: pass\n"
        "open(os.environ['OCR_OWNED_PID_FILE'],'w').write(str(os.getpid()))\n"
        "time.sleep(.4)\n"
        "print(json.dumps({'status':200 if held else 503,'body':{'model':body['model'],"
        "'blocks':[{'text':'ok','bbox':None,'confidence':None}]}}))\n",
        encoding="utf-8")
    monkeypatch.setenv("OCR_SHARED_GPU_LOCK_PATH", str(path))
    monkeypatch.setenv("OCR_SERVICE_TOKEN", "private-test-token")
    monkeypatch.setattr(supervisor, "CHILD_PATH", child)
    monkeypatch.setattr(supervisor, "_baseline", object())
    monkeypatch.setattr(supervisor, "_sample_gpu", lambda expected=None, allow_unknown=False:
                        supervisor.GpuSample(9000, 100, expected)
                        if expected is not None and Path(f"/proc/{expected}").exists()
                        else supervisor.GpuSample(9000, 0))
    value = await asyncio.wait_for(
        supervisor.ocr(_request(), _Connected(), "Bearer private-test-token", "test"), 3)
    assert value["blocks"][0]["text"] == "ok"
    assert not supervisor._lock.locked()
    with path.open("a+b") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
