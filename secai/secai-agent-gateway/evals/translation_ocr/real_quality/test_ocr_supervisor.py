"""CPU-only safety checks for the isolated OCR supervisor."""
from __future__ import annotations

import asyncio
import base64
import os
import tempfile
import time
from pathlib import Path

import qwen_ocr_supervisor as supervisor


async def main():
    os.environ["OCR_SERVICE_TOKEN"] = "test-token"
    real_sample = supervisor._sample_gpu
    with tempfile.TemporaryDirectory() as directory:
        trace_path = Path(directory) / "trace.jsonl"
        os.environ["OCR_TRACE_PATH"] = str(trace_path)
        child = Path(directory) / "fake_child.py"
        child.write_text(
            "import json,sys,time,os\n"
            "body=json.loads(sys.stdin.read())\n"
            "if body['image_base64']!='ZmFzdA==':\n"
            "    open(os.environ['OCR_OWNED_PID_FILE'],'w').write(str(os.getpid()))\n"
            "if body['image_base64'] in ('bG9hZA==','cHJlZmlsbA=='): time.sleep(999)\n"
            "if body['image_base64']=='Y2xlYW51cA==': time.sleep(1)\n"
            "time.sleep(.4)\n"
            "print(json.dumps({'status':200,'body':{'model':body['model'],"
            "'blocks':[{'text':'ok','bbox':None,'confidence':None}]}}))\n"
        )
        supervisor.CHILD_PATH = child
        supervisor.DEADLINE_SECONDS = 1.2
        def good_sample(expected=None, allow_unknown=False):
            if expected is not None and Path(f"/proc/{expected}").exists():
                return supervisor.GpuSample(9000, 100, expected)
            return supervisor.GpuSample(9000, 0)
        supervisor._sample_gpu = good_sample
        supervisor._baseline = object()

        class Connected:
            async def is_disconnected(self):
                return False
        class Gone:
            async def is_disconnected(self):
                return True
        async def call(data, conn):
            body = supervisor.OcrRequest(model=supervisor.MODEL_ID,
                                         image_base64=base64.b64encode(data).decode())
            return await supervisor.ocr(body, conn, "Bearer test-token", "test")
        for payload, connection in ((b"load", Connected()),
                                    (b"prefill", Connected()),
                                    (b"load", Gone())):
            try:
                await call(payload, connection)
            except Exception as exc:
                assert getattr(exc, "status_code", None) == 503
            else:
                raise AssertionError("hung or disconnected child passed")
            assert not supervisor._lock.locked(), "child lock not released after kill"
            assert (await call(b"ok", Connected()))["blocks"][0]["text"] == "ok"
        assert '"event": "disconnect_observed"' in trace_path.read_text()

        supervisor._sample_gpu = lambda expected=None, allow_unknown=False: supervisor.GpuSample(100, 9999)
        try:
            await call(b"load", Connected())
        except Exception as exc:
            assert getattr(exc, "status_code", None) == 503
        else:
            raise AssertionError("GPU breach admitted")
        supervisor._sample_gpu = good_sample
        os.environ["OCR_TRACE_PATH"] = str(Path(directory) / "missing" / "trace.jsonl")
        assert (await call(b"ok", Connected()))["blocks"][0]["text"] == "ok"
        assert not supervisor._lock.locked()

        calls = 0
        def midflight_breach(expected=None, allow_unknown=False):
            nonlocal calls
            calls += 1
            return (supervisor.GpuSample(100, 9999) if calls == 2
                    else good_sample(expected))
        supervisor._sample_gpu = midflight_breach
        try:
            await call(b"load", Connected())
        except Exception as exc:
            assert getattr(exc, "status_code", None) == 503
        else:
            raise AssertionError("midflight GPU breach did not stop owned child")
        assert not supervisor._lock.locked()

        blocked = True
        restore_calls = 0
        def delayed_restore(expected=None, allow_unknown=False):
            nonlocal restore_calls
            restore_calls += 1
            return (supervisor.GpuSample(100, 9999) if blocked and restore_calls > 1
                    else good_sample(expected))
        supervisor._sample_gpu = delayed_restore
        supervisor.RESPONSE_SECONDS = .2
        try:
            await call(b"ok", Connected())
        except Exception as exc:
            assert getattr(exc, "status_code", None) == 503
        else:
            raise AssertionError("HTTP response exceeded cleanup bound")
        assert supervisor._lock.locked(), "lock opened while GPU reserve unavailable"
        blocked = False
        for _ in range(20):
            if not supervisor._lock.locked():
                break
            await asyncio.sleep(.1)
        assert not supervisor._lock.locked(), "background reserve restoration did not unlock"

        supervisor._sample_gpu = good_sample
        supervisor.RESPONSE_SECONDS = 2
        supervisor.DEADLINE_SECONDS = .2
        os.environ["OCR_TRACE_PATH"] = str(trace_path)
        delayed_calls = 0
        def delayed_sample(expected=None, allow_unknown=False):
            nonlocal delayed_calls
            delayed_calls += 1
            if delayed_calls == 2:
                time.sleep(.55)
            return good_sample(expected, allow_unknown)
        supervisor._sample_gpu = delayed_sample
        try:
            await call(b"load", Connected())
        except Exception as exc:
            assert getattr(exc, "status_code", None) == 503
        else:
            raise AssertionError("delayed GPU sample hid OCR deadline")
        assert not supervisor._lock.locked()
        import json
        deadline_events = [json.loads(line) for line in trace_path.read_text().splitlines()
                           if '"event": "deadline_kill"' in line]
        assert deadline_events and deadline_events[-1]["elapsed_seconds"] < .35

        supervisor._sample_gpu = good_sample
        supervisor.RESPONSE_SECONDS = 110
        supervisor.DEADLINE_SECONDS = 2
        verified = False
        gap = False
        def cleanup_gap(expected=None, allow_unknown=False):
            nonlocal verified, gap
            sample = good_sample(expected, allow_unknown)
            if sample.owned and not verified:
                verified = True
                return sample
            if verified and not gap:
                gap = True
                return supervisor.GpuSample(4000, 0)
            if gap:
                return supervisor.GpuSample(9000, 0)
            return sample
        supervisor._sample_gpu = cleanup_gap
        assert (await call(b"cleanup", Connected()))["blocks"][0]["text"] == "ok"
        assert gap and '"event": "gpu_cleanup_pending"' in trace_path.read_text()
        assert not supervisor._lock.locked()

        # A vanished GPU PID can occur while the owned child is still running.
        # A later recovery must not turn an observed absolute-floor breach into 200.
        saw_owner = False
        saw_low_free = False
        def active_child_floor_breach(expected=None, allow_unknown=False):
            nonlocal saw_owner, saw_low_free
            sample = good_sample(expected, allow_unknown)
            if sample.owned:
                if saw_owner and not saw_low_free:
                    saw_low_free = True
                    return supervisor.GpuSample(1000, 0)
                saw_owner = True
                return sample
            if saw_low_free:
                return supervisor.GpuSample(9000, 0)
            return sample
        supervisor._sample_gpu = active_child_floor_breach
        try:
            await call(b"cleanup", Connected())
        except Exception as exc:
            assert getattr(exc, "status_code", None) == 503
        else:
            raise AssertionError("active child reserve breach recovered into HTTP 200")
        assert saw_owner and saw_low_free
        assert not supervisor._lock.locked()

        supervisor._sample_gpu = good_sample
        supervisor.DEADLINE_SECONDS = 1.2
        try:
            await call(b"fast", Connected())
        except Exception as exc:
            assert getattr(exc, "status_code", None) == 503
        else:
            raise AssertionError("fast child without GPU ownership was accepted")
        assert not supervisor._lock.locked()

        original_snapshot = supervisor._snapshot
        supervisor._snapshot = lambda: supervisor.GpuSnapshot(
            "GPU-test", 9000, {999: ("GPU-test", 100)})
        supervisor._baseline = supervisor.GpuSnapshot("GPU-test", 9000, {})
        # Call the real sampler, not the test's process-liveness stub.
        try:
            real_sample(None)
        except RuntimeError:
            pass
        else:
            raise AssertionError("unknown GPU host PID was attributed to OCR")
        provisional = real_sample(None, True)
        assert provisional.owned == 0 and provisional.unidentified_pid == 999
        supervisor._snapshot = original_snapshot
    print("supervisor hang, disconnect, resource, trace and recovery paths passed")


if __name__ == "__main__":
    asyncio.run(main())
