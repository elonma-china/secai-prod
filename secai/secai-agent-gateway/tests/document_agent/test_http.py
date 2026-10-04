import asyncio
import io
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from linch.evals import ScriptedProvider, TextTurn, ToolUseTurn
from PIL import Image

from secai_gateway.document_agent.contracts import ScopedTask
from secai_gateway.document_agent.runner import DocumentRunner
from secai_gateway.document_agent.scope import DocumentScopeStore
from secai_gateway.document_app import create_document_app


class Backend:
    def __init__(self):
        self.calls = 0

    async def submit(self, scope, attachment, peer):
        self.calls += 1
        return ScopedTask("a" * 32, "queued")

    async def status(self, scope, task_handle):
        self.calls += 1
        return ScopedTask(task_handle, "queued")

    async def result(self, scope, task_handle):
        raise AssertionError("pending task has no result")

    async def cancel(self, scope, task_handle):
        return ScopedTask(task_handle, "cancelling")


def _png():
    output = io.BytesIO()
    Image.new("RGB", (2, 2), "white").save(output, format="PNG")
    return output.getvalue()


def test_asgi_image_turn_replays_without_second_ocr(tmp_path, monkeypatch):
    monkeypatch.setattr("secai_gateway.api.document_agent.routes.secrets.token_hex", lambda n: "f" * (n * 2))
    backend = Backend()
    runner = DocumentRunner(provider=ScriptedProvider([
        ToolUseTurn("ocr_extract", {"attachment_id": "f" * 32}),
        TextTurn("Đã xong"),
    ]), model="scripted")
    app = create_document_app(FastAPI(), DocumentScopeStore(tmp_path / "scope.db"), backend, runner, SimpleNamespace(document_agent_enabled=True))

    async def run():
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                created = await client.post("/ai/v3/document-agent/contexts")
                token = created.json()["context_token"]
                headers = {"X-Document-Context": token, "Idempotency-Key": "b" * 32}
                files = {"prompt": (None, "Đọc ảnh"), "file": ("page.png", _png(), "image/png")}
                first = await client.post("/ai/v3/document-agent/turns", headers=headers, files=files)
                second = await client.post("/ai/v3/document-agent/turns", headers=headers, files=files)
                return created, first, second

    created, first, second = asyncio.run(run())
    assert created.status_code == 201
    assert first.status_code == 200, first.text
    assert first.json() == second.json()
    assert backend.calls == 1
    assert first.json()["tasks"][0]["status"] == "queued"
    assert first.headers["cache-control"] == "no-store"


def test_disabled_composition_keeps_legacy_app(tmp_path):
    base = FastAPI()
    @base.get("/health")
    def health():
        return {"ok": True}
    app = create_document_app(base, None, None, None, SimpleNamespace(document_agent_enabled=False))
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            return await client.get("/health"), await client.post("/ai/v3/document-agent/contexts")
    health, missing = asyncio.run(run())
    assert health.json() == {"ok": True}
    assert missing.status_code == 404


@pytest.mark.parametrize("files, expected", [
    ([('prompt', (None, 'đọc ảnh')), ('prompt', (None, 'lần hai'))], 400),
    ({'prompt': (None, 'đọc ảnh'), 'file': ('scan.pdf', b'%PDF-1.4', 'application/pdf')}, 415),
    ({'prompt': (None, 'đọc ảnh'), 'file': ('fake.png', b'%PDF-1.4', 'image/png')}, 400),
])
def test_invalid_multipart_never_calls_runner(tmp_path, files, expected):
    backend = Backend()
    runner = DocumentRunner(provider=ScriptedProvider([TextTurn('unused')]), model='scripted')
    app = create_document_app(FastAPI(), DocumentScopeStore(tmp_path / 'scope.db'), backend, runner, SimpleNamespace(document_agent_enabled=True))
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            created = await client.post('/ai/v3/document-agent/contexts')
            return await client.post('/ai/v3/document-agent/turns', headers={'X-Document-Context': created.json()['context_token'], 'Idempotency-Key': 'b' * 32}, files=files)
    response = asyncio.run(run())
    assert response.status_code == expected
    assert backend.calls == 0


def test_out_of_scope_task_never_reaches_backend(tmp_path):
    backend = Backend()
    store = DocumentScopeStore(tmp_path / 'scope.db')
    import time
    owner = store.create('owner', time.time())
    other = store.create('other', time.time())
    handle = store.bind_task(store.resolve(owner.context_token, time.time()), 'private-job-id')
    runner = DocumentRunner(provider=ScriptedProvider([TextTurn('unused')]), model='scripted')
    app = create_document_app(FastAPI(), store, backend, runner, SimpleNamespace(document_agent_enabled=True))
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            return await client.get(f'/ai/v3/document-agent/tasks/{handle}', headers={'X-Document-Context': other.context_token})
    response = asyncio.run(run())
    assert response.status_code == 404
    assert 'private-job-id' not in response.text
    assert backend.calls == 0
