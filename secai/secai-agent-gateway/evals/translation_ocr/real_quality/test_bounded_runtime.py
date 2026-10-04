"""CPU-only regression checks for the private bounded OCR runtime."""
from __future__ import annotations

import asyncio
import importlib.util
import os
import sys
import tempfile
import threading
import time
import types
from pathlib import Path

import torch
from fastapi import HTTPException


def load_server():
    fake_server = types.ModuleType("server")
    fake_server._free_mib = lambda: 9000
    fake_server._owned_mib = lambda: 100
    fake_server._guard = lambda: None
    fake_server.MAX_PEAK_MIB = 7168
    sys.modules["server"] = fake_server
    path = Path(__file__).with_name("qwen_server_real_pages_bounded.py")
    spec = importlib.util.spec_from_file_location("bounded_eval_server", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


async def main() -> None:
    server = load_server()
    os.environ["OCR_SERVICE_TOKEN"] = "synthetic-test-only"
    with tempfile.TemporaryDirectory() as directory:
        os.environ["OCR_TRACE_PATH"] = str(Path(directory) / "events.jsonl")
        assert server._resource_sample() == (9000, 100)
        server._free_mib = lambda: 8043
        try:
            server._resource_sample()
        except HTTPException as exc:
            assert exc.status_code == 503
        else:
            raise AssertionError("combined reserve breach was accepted")
        server._free_mib = lambda: 9000
        server._owned_mib = lambda: None
        try:
            server._resource_sample()
        except HTTPException as exc:
            assert exc.status_code == 503
        else:
            raise AssertionError("missing measurement was accepted")
        server._owned_mib = lambda: 100

        stop = threading.Event()
        criterion = server.BoundedStop(time.monotonic() + 10, stop, prompt_tokens=2)
        ids = torch.tensor([[1, 2, *([3, 4, 5, 6] * 40)]])
        assert criterion(ids, None).item()
        assert criterion.repetition
        criterion = server.BoundedStop(time.monotonic() + 10, stop, prompt_tokens=2)
        stop.set()
        assert criterion(torch.tensor([[1, 2, 3]]), None).item()
        assert criterion.cancelled

        request = server.Request(model=server.MODEL_ID, image_base64="invalid-base64")
        server._model = object()
        server._processor = object()
        class Connected:
            async def is_disconnected(self):
                return False
        try:
            await server.ocr(request, Connected(), "Bearer synthetic-test-only", "bad-image")
        except HTTPException as exc:
            assert exc.status_code == 422
        else:
            raise AssertionError("invalid image was accepted")
        assert not server._lock.locked()

        started = threading.Event()
        stopped = threading.Event()
        def slow_worker(*args):
            started.set()
            while not args[-1].is_set():
                time.sleep(0.005)
            stopped.set()
            return {"model": server.MODEL_ID, "blocks": []}
        server._infer = slow_worker
        class Disconnect:
            async def is_disconnected(self):
                return started.is_set()
        try:
            await server.ocr(request, Disconnect(), "Bearer synthetic-test-only", "disconnect")
        except HTTPException as exc:
            assert exc.status_code == 503
        else:
            raise AssertionError("disconnected inference was accepted")
        assert stopped.is_set(), "route returned before generation stopped"
        assert not server._lock.locked(), "lock remained held after worker stopped"
        assert '"event": "released"' in Path(os.environ["OCR_TRACE_PATH"]).read_text()
    print("resource, repetition, exception, disconnect and lock paths passed")


if __name__ == "__main__":
    asyncio.run(main())
