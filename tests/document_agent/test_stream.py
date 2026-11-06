import asyncio
import json
from types import SimpleNamespace

import httpx
from fastapi import FastAPI
from linch.evals import ScriptedProvider, TextTurn

from secai_gateway.document_agent.runner import DocumentRunner
from secai_gateway.document_agent.scope import DocumentScopeStore
from secai_gateway.document_app import create_document_app


class Backend:
    async def status(self, scope, handle):
        raise AssertionError("no task")


def test_text_turn_stream_has_safe_token_and_same_final_as_replay(tmp_path):
    runner = DocumentRunner(provider=ScriptedProvider([TextTurn("Xin chào")]), model="scripted", budget=SimpleNamespace(check=lambda req, window: None))
    app = create_document_app(FastAPI(), DocumentScopeStore(tmp_path / "scope.db"), Backend(), runner, SimpleNamespace(document_agent_enabled=True))

    async def run():
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                created = await client.post("/ai/v3/document-agent/contexts")
                headers = {"X-Document-Context": created.json()["context_token"], "Idempotency-Key": "b" * 32}
                files = {"prompt": (None, "Xin chào"), "stream": (None, "true")}
                first = await client.post("/ai/v3/document-agent/turns", headers=headers, files=files)
                replay = await client.post("/ai/v3/document-agent/turns", headers=headers, files=files)
                return first, replay

    first, replay = asyncio.run(run())
    assert first.status_code == 200
    assert "event: token\n" in first.text
    final = json.loads(first.text.split("event: final\ndata: ")[1].split("\n")[0])
    again = json.loads(replay.text.split("event: final\ndata: ")[1].split("\n")[0])
    assert final == again
    assert final["answer"] == "Xin chào"
    assert "context_token" not in first.text
