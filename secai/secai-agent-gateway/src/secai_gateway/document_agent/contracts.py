"""Small boundary implemented by the shared OCR queue after its owner merges it."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Protocol

from .scope import DocumentScope


@dataclass(frozen=True, slots=True, repr=False)
class ImageAttachment:
    attachment_id: str
    media_type: str
    data: bytes = field(repr=False)


@dataclass(frozen=True, slots=True)
class ScopedTask:
    task_handle: str
    status: str
    needs_review: bool = False

    def public(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class OcrResult:
    raw_text: str
    corrected_text: str
    edits: tuple[Any, ...] = ()
    warnings: tuple[Any, ...] = ()
    needs_review: bool = False

    def public(self) -> dict[str, Any]:
        return asdict(self)


class OcrBackend(Protocol):
    async def submit(self, scope: DocumentScope, attachment: ImageAttachment, peer: str) -> ScopedTask: ...
    async def status(self, scope: DocumentScope, task_handle: str) -> ScopedTask: ...
    async def result(self, scope: DocumentScope, task_handle: str) -> OcrResult: ...
    async def cancel(self, scope: DocumentScope, task_handle: str) -> ScopedTask: ...
