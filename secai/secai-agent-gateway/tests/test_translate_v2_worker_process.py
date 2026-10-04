"""Real local API/store/standalone-worker process handoff; no external model call."""
import asyncio
import os
import subprocess
import sys
from pathlib import Path

import httpx
import pytest

from secai_gateway.app import create_app


class NoModel:
    async def complete(self, *args, **kwargs):
        raise AssertionError("same-language job must not invoke a model")


@pytest.mark.asyncio
async def test_standalone_worker_process_consumes_api_job(tmp_path, monkeypatch):
    monkeypatch.setenv("TRANSLATE_V2_ENABLED", "true")
    monkeypatch.setenv("TRANSLATE_V2_DB_PATH", str(tmp_path / "jobs.db"))
    monkeypatch.setenv("TRANSLATE_V2_ARTIFACT_DIR", str(tmp_path / "artifacts"))
    monkeypatch.setenv("TRANSLATE_V2_WORKER_DB_PATH", str(tmp_path / "worker.db"))
    monkeypatch.setenv("TRANSLATE_V2_WORKER_POC_DB_PATH", str(tmp_path / "poc.db"))
    monkeypatch.setenv("AIRGAP", "true")
    app = create_app(NoModel())
    script = Path(__file__).resolve().parents[1] / "deploy" / "translate-v2" / "worker.py"
    process = subprocess.Popen([sys.executable, str(script)], env=os.environ.copy(),
                               stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                               stderr=subprocess.PIPE)
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
            admitted = await client.post("/ai/v2/translate/files",
                                         files={"file": ("x.txt", "Xin chào".encode())},
                                         data={"source_lang": "vi", "target_lang": "vi"})
            assert admitted.status_code == 202, admitted.text
            result_url = admitted.json()["result_url"]
            for _ in range(80):
                result = await client.get(result_url)
                if result.status_code == 200:
                    break
                await asyncio.sleep(0.1)
            else:
                raise AssertionError("standalone worker did not publish result")
            assert result.json()["translated_text"] == "Xin chào"
            assert process.poll() is None
            assert "Xin chào".encode() not in (tmp_path / "worker.db").read_bytes()
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
