"""Shared, immutable boundary for document translation and image OCR."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol

SourceLang = Literal["auto", "vi", "en", "zh", "ja", "ko", "fr", "de", "es", "ru", "ar"]
TargetLang = Literal["vi", "en", "zh", "ja", "ko", "fr", "de", "es", "ru", "ar"]
BlockKind = Literal["paragraph", "table_cell", "header", "footer", "footnote", "literal", "ocr_text"]


@dataclass(frozen=True, kw_only=True)
class WarningItem:
    code: str
    message: str
    block_id: str | None = None


@dataclass(frozen=True, kw_only=True)
class DocumentBlock:
    id: str
    kind: BlockKind
    order: int
    text: str
    metadata: dict[str, Any]


@dataclass(frozen=True, kw_only=True)
class ExtractedDocument:
    blocks: tuple[DocumentBlock, ...]
    warnings: tuple[WarningItem, ...] = ()


@dataclass(frozen=True, kw_only=True)
class OcrEdit:
    block_id: str
    start: int
    end: int
    before: str
    after: str
    reason: Literal["spelling", "spacing", "punctuation"]


@dataclass(frozen=True, kw_only=True)
class OcrResult:
    blocks: tuple[DocumentBlock, ...]
    raw_text: str
    warnings: tuple[WarningItem, ...] = ()


@dataclass(frozen=True, kw_only=True)
class CorrectedOcr:
    blocks: tuple[DocumentBlock, ...]
    raw_text: str
    corrected_text: str
    edits: tuple[OcrEdit, ...] = ()
    warnings: tuple[WarningItem, ...] = ()
    needs_review: bool = False


@dataclass(frozen=True, kw_only=True)
class PreparedImage:
    path: Path
    media_type: Literal["image/png", "image/jpeg"]
    width: int
    height: int


@dataclass(frozen=True, kw_only=True)
class TranslatedBlock:
    source: DocumentBlock
    translated_text: str


class TranslationError(Exception):
    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


class ProcessingContext(Protocol):
    async def checkpoint(self, stage: str, completed: int = 0, total: int | None = None) -> None: ...
    async def complete(self, profile: str, prompt: str) -> tuple[str, dict[str, Any]]: ...
    async def translate(self, text: str, source_lang: TargetLang,
                        target_lang: TargetLang) -> str: ...


class OcrPipeline(Protocol):
    async def process(self, input_path: Path, *, work_dir: Path,
                      context: ProcessingContext) -> CorrectedOcr: ...
