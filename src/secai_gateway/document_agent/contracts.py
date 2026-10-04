"""Small boundary implemented by the shared OCR queue after its owner merges it."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from json import JSONEncoder
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

    def public_size_exceeds(self, limit: int) -> bool:
        """Bound the JSON payload before constructing the HTTP response."""
        if len(self.raw_text.encode("utf-8")) > limit or len(self.corrected_text.encode("utf-8")) > limit:
            return True
        total = 0
        fields = {
            "raw_text": self.raw_text, "corrected_text": self.corrected_text,
            "edits": self.edits, "warnings": self.warnings, "needs_review": self.needs_review,
        }
        for piece in JSONEncoder(ensure_ascii=False, separators=(",", ":")).iterencode(fields):
            total += len(piece.encode("utf-8"))
            if total > limit:
                return True
        return False


class OcrBackend(Protocol):
    async def submit(self, scope: DocumentScope, attachment: ImageAttachment, peer: str, *, submission_key: str) -> ScopedTask: ...
    async def status(self, scope: DocumentScope, task_handle: str) -> ScopedTask: ...
    async def result(self, scope: DocumentScope, task_handle: str) -> OcrResult: ...
    async def cancel(self, scope: DocumentScope, task_handle: str) -> ScopedTask: ...
