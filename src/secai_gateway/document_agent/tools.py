"""Only the two scope-bound tools exposed to the document profile."""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from linch.tools.base import ToolContext, ToolResult
from linch.tools.function import tool

from ..security import wrap_untrusted_content
from .contracts import ImageAttachment, OcrBackend, ScopedTask
from .scope import DocumentScope

PREVIEW_CODEPOINTS = 4000


@dataclass(slots=True)
class DocumentRunDeps:
    scope: DocumentScope
    attachments: dict[str, ImageAttachment]
    backend: OcrBackend
    peer: str
    submission_key: str = ""
    allowed_task_handles: set[str] = field(default_factory=set)
    submitted: ScopedTask | None = None
    observed_tasks: dict[str, ScopedTask] = field(default_factory=dict)
    admission_failed: bool = False


def _deps(ctx: ToolContext) -> DocumentRunDeps | None:
    value = ctx.deps
    return value if isinstance(value, DocumentRunDeps) else None


def _error(code: str) -> ToolResult:
    return ToolResult(content=json.dumps({"error": code}), summary=code, is_error=True)


def _json(value: dict, summary: str) -> ToolResult:
    return ToolResult(content=json.dumps(value, ensure_ascii=False), summary=summary)


def build_document_tools() -> dict[str, object]:
    @tool(name="ocr_extract", description="Submit the attached image for OCR. Pass only its attachment_id.", scope="read", parallel=False, retryable=False)
    async def ocr_extract(attachment_id: str, ctx: ToolContext) -> ToolResult:
        deps = _deps(ctx)
        if deps is None or len(deps.submission_key) != 64:
            return _error("DOCUMENT_CONTEXT_REQUIRED")
        attachment = deps.attachments.get(attachment_id)
        if attachment is None:
            return _error("ATTACHMENT_NOT_FOUND")
        if deps.submitted is None:
            try:
                deps.submitted = await deps.backend.submit(deps.scope, attachment, deps.peer, submission_key=deps.submission_key)
            except Exception:
                deps.admission_failed = True
                return _error("OCR_ADMISSION_FAILED")
            deps.allowed_task_handles.add(deps.submitted.task_handle)
        deps.observed_tasks[deps.submitted.task_handle] = deps.submitted
        return _json(deps.submitted.public(), "OCR task " + deps.submitted.status)

    @tool(name="ocr_result", description="Read status and a bounded OCR preview for a scoped task_handle.", scope="read", parallel=False, retryable=False)
    async def ocr_result(task_handle: str, ctx: ToolContext) -> ToolResult:
        deps = _deps(ctx)
        if deps is None:
            return _error("DOCUMENT_CONTEXT_REQUIRED")
        if task_handle not in deps.allowed_task_handles:
            return _error("TASK_NOT_FOUND")
        try:
            task = await deps.backend.status(deps.scope, task_handle)
            deps.observed_tasks[task_handle] = task
            if task.status != "succeeded":
                return _json(task.public(), "OCR task " + task.status)
            result = await deps.backend.result(deps.scope, task_handle)
        except Exception:
            return _error("TASK_NOT_FOUND")
        text = result.corrected_text
        # Leave room for escaped delimiters, which may expand literal OCR text.
        wrapped = wrap_untrusted_content(text[:3000], source="ocr_result")
        preview = wrapped[:PREVIEW_CODEPOINTS]
        return _json({**task.public(), "preview": preview, "truncated": len(text) > 3000 or len(wrapped) > PREVIEW_CODEPOINTS}, "OCR result available")

    return {"ocr_extract": ocr_extract, "ocr_result": ocr_result}
