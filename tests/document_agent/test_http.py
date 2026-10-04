import asyncio
import io
import time
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
from secai_gateway.api.document_agent.schemas import MAX_REQUEST_BYTES, parse_turn
from starlette.requests import Request
from fastapi import HTTPException


class Backend:
    def __init__(self):
        self.calls = 0

    async def submit(self, scope, attachment, peer, *, submission_key):
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
    ]), model="scripted", budget=SimpleNamespace(check=lambda req, window: None))
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
    runner = DocumentRunner(provider=ScriptedProvider([TextTurn('unused')]), model='scripted', budget=SimpleNamespace(check=lambda req, window: None))
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
    runner = DocumentRunner(provider=ScriptedProvider([TextTurn('unused')]), model='scripted', budget=SimpleNamespace(check=lambda req, window: None))
    app = create_document_app(FastAPI(), store, backend, runner, SimpleNamespace(document_agent_enabled=True))
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            return await client.get(f'/ai/v3/document-agent/tasks/{handle}', headers={'X-Document-Context': other.context_token})
    response = asyncio.run(run())
    assert response.status_code == 404
    assert 'private-job-id' not in response.text
    assert backend.calls == 0


def test_single_oversized_asgi_chunk_is_rejected_before_copy():
    class HugeChunk:
        def __len__(self):
            return MAX_REQUEST_BYTES + 1
        def __iter__(self):
            raise AssertionError('oversized chunk was copied')
    async def receive():
        return {'type': 'http.request', 'body': HugeChunk(), 'more_body': False}
    request = Request({'type': 'http', 'method': 'POST', 'path': '/', 'headers': [(b'content-type', b'multipart/form-data; boundary=x')]}, receive)
    with pytest.raises(HTTPException) as raised:
        asyncio.run(parse_turn(request))
    assert raised.value.status_code == 413


def test_result_endpoint_rejects_oversized_ocr_before_response(tmp_path, monkeypatch):
    from secai_gateway.document_agent.contracts import OcrResult
    from secai_gateway.api.document_agent import routes
    monkeypatch.setattr(routes, 'MAX_OCR_RESULT_BYTES', 100)
    class DoneBackend(Backend):
        async def status(self, scope, handle):
            return ScopedTask(handle, 'succeeded')
        async def result(self, scope, handle):
            return OcrResult('x' * 200, 'valid', (), (), False)
    store = DocumentScopeStore(tmp_path / 'scope.db')
    grant = store.create('owner', time.time())
    handle = store.bind_task(store.resolve(grant.context_token, time.time()), 'private-id')
    runner = DocumentRunner(provider=ScriptedProvider([TextTurn('unused')]), model='scripted', budget=SimpleNamespace(check=lambda req, window: None))
    app = create_document_app(FastAPI(), store, DoneBackend(), runner, SimpleNamespace(document_agent_enabled=True))
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            return await client.get(f'/ai/v3/document-agent/tasks/{handle}/result', headers={'X-Document-Context': grant.context_token})
    response = asyncio.run(run())
    assert response.status_code == 413
    assert 'private-id' not in response.text


def test_retry_after_cancelled_admitted_turn_uses_same_durable_key(tmp_path):
    class DurableBackend(Backend):
        def __init__(self):
            super().__init__()
            self.keys = []
        async def submit(self, scope, attachment, peer, *, submission_key):
            self.keys.append(submission_key)
            if len(set(self.keys)) == 1:
                self.calls = 1
            else:
                self.calls += 1
            return ScopedTask('a' * 32, 'queued')
    class CancelOnceRunner:
        def __init__(self):
            self.calls = 0
        async def stream(self, prompt, deps):
            self.calls += 1
            image = next(iter(deps.attachments.values()))
            task = await deps.backend.submit(deps.scope, image, deps.peer, submission_key=deps.submission_key)
            if self.calls == 1:
                raise asyncio.CancelledError
            yield {'type': 'final', 'answer': 'OCR đang xử lý.', 'tasks': [task.public()]}
    backend = DurableBackend()
    app = create_document_app(FastAPI(), DocumentScopeStore(tmp_path / 'scope.db'), backend, CancelOnceRunner(), SimpleNamespace(document_agent_enabled=True))
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            created = await client.post('/ai/v3/document-agent/contexts')
            headers = {'X-Document-Context': created.json()['context_token'], 'Idempotency-Key': 'b' * 32}
            files = {'prompt': (None, 'Đọc ảnh'), 'file': ('page.png', _png(), 'image/png')}
            try:
                await client.post('/ai/v3/document-agent/turns', headers=headers, files=files)
            except (asyncio.CancelledError, RuntimeError):
                pass
            return await client.post('/ai/v3/document-agent/turns', headers=headers, files=files)
    second = asyncio.run(run())
    assert second.status_code == 200
    assert backend.calls == 1
    assert len(backend.keys) == 2 and backend.keys[0] == backend.keys[1]
