"""Small boundary implemented by the shared OCR queue after its owner merges it."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from json import JSONEncoder
from typing import Any, Protocol

from fastapi.encoders import jsonable_encoder

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
        return jsonable_encoder(self)

    def bounded_public(self, limit: int) -> dict[str, Any] | None:
        """Normalize once, then bound the exact object returned by HTTP."""
        if len(self.raw_text.encode("utf-8")) > limit or len(self.corrected_text.encode("utf-8")) > limit:
            return None
        payload = self.public()
        total = 0
        for piece in JSONEncoder(ensure_ascii=False, allow_nan=False, separators=(",", ":")).iterencode(payload):
            total += len(piece.encode("utf-8"))
            if total > limit:
                return None
        return payload


class OcrBackend(Protocol):
    async def submit(self, scope: DocumentScope, attachment: ImageAttachment, peer: str, *, submission_key: str) -> ScopedTask: ...
    async def status(self, scope: DocumentScope, task_handle: str) -> ScopedTask: ...
    async def result(self, scope: DocumentScope, task_handle: str) -> OcrResult: ...
    async def cancel(self, scope: DocumentScope, task_handle: str) -> ScopedTask: ...
