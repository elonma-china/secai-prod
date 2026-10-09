import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from linch.evals import ScriptedProvider, TextTurn, ToolUseTurn

from secai_gateway.document_agent.contracts import ImageAttachment, ScopedTask
from secai_gateway.document_agent.runner import DocumentRunner, DocumentTokenBudget
from secai_gateway.document_agent.tools import DocumentRunDeps


class Backend:
    def __init__(self):
        self.calls = 0

    async def submit(self, scope, attachment, peer, *, submission_key):
        self.calls += 1
        return ScopedTask("a" * 32, "queued", False)


def test_scripted_linch_extract_produces_honest_pending_response():
    backend = Backend()
    image = ImageAttachment("f" * 32, "image/png", b"png")
    deps = DocumentRunDeps(object(), {image.attachment_id: image}, backend, "127.0.0.1", "c" * 64)
    runner = DocumentRunner(provider=ScriptedProvider([
        ToolUseTurn("ocr_extract", {"attachment_id": image.attachment_id}),
        TextTurn("Đã đọc xong ảnh."),
    ]), model="scripted", budget=SimpleNamespace(check=lambda req, window: None))
    response = asyncio.run(runner.complete("Đọc ảnh", deps))
    assert backend.calls == 1
    assert response["tasks"] == [{"task_handle": "a" * 32, "status": "queued", "needs_review": False}]
    assert "đang xử lý" in response["answer"].lower()


def test_unregistered_tool_cannot_call_backend():
    backend = Backend()
    deps = DocumentRunDeps(object(), {}, backend, "127.0.0.1", "c" * 64)
    runner = DocumentRunner(provider=ScriptedProvider([
        ToolUseTurn("poc_exec", {"target": "example.com"}),
        TextTurn("Không thể gọi công cụ."),
    ]), model="scripted", budget=SimpleNamespace(check=lambda req, window: None))
    response = asyncio.run(runner.complete("xin chào", deps))
    assert backend.calls == 0
    assert response["tasks"] == []


def test_failed_ocr_admission_cannot_be_reported_as_success():
    class FailingBackend:
        async def submit(self, scope, attachment, peer, *, submission_key):
            raise RuntimeError("private job-id must stay private")

    image = ImageAttachment("f" * 32, "image/png", b"png")
    deps = DocumentRunDeps(object(), {image.attachment_id: image}, FailingBackend(), "127.0.0.1", "c" * 64)
    runner = DocumentRunner(provider=ScriptedProvider([
        ToolUseTurn("ocr_extract", {"attachment_id": image.attachment_id}),
        TextTurn("OCR đã thành công."),
    ]), model="scripted", budget=SimpleNamespace(check=lambda req, window: None))
    response = asyncio.run(runner.complete("Đọc ảnh", deps))
    assert "không thể tiếp nhận" in response["answer"].lower()
    assert "private job-id" not in str(response)


def test_verified_tokenizer_rejects_over_budget_before_scripted_provider_call():
    root = Path('/home/ict/secai-multilang-benchmark-corpus/runtime-prep')
    budget = DocumentTokenBudget.from_files(
        model='scripted', tokenizer_path=root / 'tokenizer.json',
        tokenizer_sha256='87a7830d63fcf43bf241c3c5242e96e62dd3fdc29224ca26fed8ea333db72de4',
        template_path=root / 'chat_template.jinja',
        template_sha256='a4aee8afcf2e0711942cf848899be66016f8d14a889ff9ede07bca099c28f715',
        context_tokens=4096,
    )
    provider = ScriptedProvider([TextTurn('must not be consumed')])
    runner = DocumentRunner(provider=provider, model='scripted', budget=budget)
    deps = DocumentRunDeps(object(), {}, Backend(), '127.0.0.1', 'c' * 64)
    large_prompt = ''.join(chr(0x4e00 + i % 1000) for i in range(10000))
    with pytest.raises(RuntimeError, match='document run did not finish'):
        asyncio.run(runner.complete(large_prompt, deps))
    assert provider._index == 0


def test_budget_checks_every_generation_including_tool_result():
    class RecordingBudget:
        def __init__(self):
            self.requests = []
        def check(self, req, window):
            self.requests.append((len(req.tools), len(req.messages)))
    budget = RecordingBudget()
    image = ImageAttachment('f' * 32, 'image/png', b'png')
    deps = DocumentRunDeps(object(), {image.attachment_id: image}, Backend(), '127.0.0.1', 'c' * 64)
    runner = DocumentRunner(provider=ScriptedProvider([
        ToolUseTurn('ocr_extract', {'attachment_id': image.attachment_id}),
        TextTurn('pending'),
    ]), model='scripted', budget=budget)
    asyncio.run(runner.complete('Đọc ảnh', deps))
    assert len(budget.requests) == 2
    assert budget.requests[0][0] == budget.requests[1][0] == 2
    assert budget.requests[1][1] > budget.requests[0][1]
