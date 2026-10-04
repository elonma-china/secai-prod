import asyncio

from linch.evals import ScriptedProvider, TextTurn, ToolUseTurn

from secai_gateway.document_agent.contracts import ImageAttachment, ScopedTask
from secai_gateway.document_agent.runner import DocumentRunner
from secai_gateway.document_agent.tools import DocumentRunDeps


class Backend:
    def __init__(self):
        self.calls = 0

    async def submit(self, scope, attachment, peer):
        self.calls += 1
        return ScopedTask("a" * 32, "queued", False)


def test_scripted_linch_extract_produces_honest_pending_response():
    backend = Backend()
    image = ImageAttachment("f" * 32, "image/png", b"png")
    deps = DocumentRunDeps(object(), {image.attachment_id: image}, backend, "127.0.0.1")
    runner = DocumentRunner(provider=ScriptedProvider([
        ToolUseTurn("ocr_extract", {"attachment_id": image.attachment_id}),
        TextTurn("Đã đọc xong ảnh."),
    ]), model="scripted")
    response = asyncio.run(runner.complete("Đọc ảnh", deps))
    assert backend.calls == 1
    assert response["tasks"] == [{"task_handle": "a" * 32, "status": "queued", "needs_review": False}]
    assert "đang xử lý" in response["answer"].lower()


def test_unregistered_tool_cannot_call_backend():
    backend = Backend()
    deps = DocumentRunDeps(object(), {}, backend, "127.0.0.1")
    runner = DocumentRunner(provider=ScriptedProvider([
        ToolUseTurn("poc_exec", {"target": "example.com"}),
        TextTurn("Không thể gọi công cụ."),
    ]), model="scripted")
    response = asyncio.run(runner.complete("xin chào", deps))
    assert backend.calls == 0
    assert response["tasks"] == []


def test_failed_ocr_admission_cannot_be_reported_as_success():
    class FailingBackend:
        async def submit(self, scope, attachment, peer):
            raise RuntimeError("private job-id must stay private")

    image = ImageAttachment("f" * 32, "image/png", b"png")
    deps = DocumentRunDeps(object(), {image.attachment_id: image}, FailingBackend(), "127.0.0.1")
    runner = DocumentRunner(provider=ScriptedProvider([
        ToolUseTurn("ocr_extract", {"attachment_id": image.attachment_id}),
        TextTurn("OCR đã thành công."),
    ]), model="scripted")
    response = asyncio.run(runner.complete("Đọc ảnh", deps))
    assert "không thể tiếp nhận" in response["answer"].lower()
    assert "private job-id" not in str(response)
