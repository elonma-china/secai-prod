"""Single supervised translation worker; never uses the web GatewayState runner."""
from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from linch.errors import ProviderError

from .documents import extract_docx, extract_pdf, extract_txt, render_pdf_page
from .jobs import JobStore
from .service import translate_blocks
from .settings import TranslationSettings
from .specialist import TranslationAgent
from .types import DocumentBlock, ProcessingContext, TranslationError, WarningItem


class _JobStopped(asyncio.CancelledError):
    """The job was cancelled or this worker no longer owns its claim."""


class WorkerContext(ProcessingContext):
    def __init__(self, store: JobStore, settings: TranslationSettings, runner: Any,
                 row: dict, llm_gate: asyncio.Semaphore):
        self.store = store
        self.settings = settings
        self.runner = runner
        self.row = row
        self.llm_gate = llm_gate
        self.model_context_tokens = settings.specialist_context_tokens
        self.literal_integrity_max_retries = settings.literal_integrity_max_retries
        # Independent blocks translate concurrently; max_llm_calls still caps total model calls.
        self.block_concurrency = max(1, min(settings.block_concurrency, settings.max_llm_calls))
        self.max_chunks = settings.max_chunks

    async def checkpoint(self, stage: str, completed: int = 0, total: int | None = None) -> None:
        if time.time() >= self.row["deadline"]:
            raise TranslationError("PROCESSING_TIMEOUT", "Processing timed out.")
        ok = await asyncio.to_thread(self.store.progress, self.row["job_id"],
                                     self.row["generation"], stage, completed, total)
        if not ok:
            raise _JobStopped

    async def complete(self, profile: str, prompt: str) -> tuple[str, dict[str, Any]]:
        if profile != "ocr_correction" or not self.settings.ocr_correction_supported or self.runner is None:
            raise TranslationError("INTERNAL_ERROR", "Unsupported transformation profile.")
        await self.checkpoint("translating" if profile == "translate_v2" else "correcting")
        async with self.llm_gate:
            retries = min(max(0, self.settings.llm_max_retries), 1)
            for attempt in range(retries + 1):
                try:
                    return await self.run_bounded(
                        self.runner.complete(profile, prompt, None),
                        timeout=self.settings.llm_timeout_seconds)
                except (asyncio.TimeoutError, OSError) as exc:
                    if attempt >= retries:
                        raise TranslationError("TRANSLATION_UNAVAILABLE",
                                               "Translation service is unavailable.") from exc
                except ProviderError as exc:
                    if not exc.retryable or attempt >= retries:
                        raise TranslationError("TRANSLATION_UNAVAILABLE",
                                               "Translation service is unavailable.") from exc
        raise TranslationError("TRANSLATION_UNAVAILABLE", "Translation service is unavailable.")

    async def translate(self, text: str, source_lang: str, target_lang: str) -> str:
        await self.checkpoint("translating")
        remaining = self.row["deadline"] - time.time()
        if remaining <= 0:
            raise TranslationError("PROCESSING_TIMEOUT", "Processing timed out.")
        async with self.llm_gate:
            try:
                return await self.run_bounded(
                    TranslationAgent(self.settings).translate(
                        text, source_lang, target_lang, timeout_seconds=remaining),
                    timeout=self.settings.specialist_timeout_seconds)
            except asyncio.TimeoutError as exc:
                raise TranslationError("PROCESSING_TIMEOUT", "Processing timed out.", retryable=True) from exc

    async def run_bounded(self, operation: Any, *, timeout: float | None = None) -> Any:
        remaining = self.row["deadline"] - time.time()
        if remaining <= 0:
            operation.close()
            raise TranslationError("PROCESSING_TIMEOUT", "Processing timed out.")
        call = asyncio.create_task(operation)

        async def watch_cancel() -> None:
            while True:
                await asyncio.sleep(0.5)
                state = await asyncio.to_thread(self.store.get, self.row["job_id"])
                if state["status"] != "running" or state["generation"] != self.row["generation"]:
                    return

        watcher = asyncio.create_task(watch_cancel())
        try:
            done, _ = await asyncio.wait(
                {call, watcher}, timeout=min(remaining, timeout) if timeout else remaining,
                return_when=asyncio.FIRST_COMPLETED)
            if watcher in done:
                raise _JobStopped
            if call in done:
                return call.result()
            if time.time() >= self.row["deadline"]:
                raise TranslationError("PROCESSING_TIMEOUT", "Processing timed out.")
            raise asyncio.TimeoutError
        finally:
            call.cancel()
            watcher.cancel()
            await asyncio.gather(call, watcher, return_exceptions=True)


class Worker:
    def __init__(self, store: JobStore, settings: TranslationSettings, runner: Any):
        self.store = store
        self.settings = settings
        self.runner = runner
        self.llm_gate = asyncio.Semaphore(settings.max_llm_calls)
        self.ocr_gate = asyncio.Semaphore(settings.max_ocr_calls)

    async def _heartbeat(self, row: dict) -> None:
        while True:
            await asyncio.sleep(self.settings.heartbeat_seconds)
            if not await asyncio.to_thread(self.store.heartbeat, row["job_id"], row["generation"]):
                return

    async def _extract_pdf_with_ocr_fallback(
            self, context: WorkerContext, input_path: Path
            ) -> tuple[tuple[DocumentBlock, ...], tuple[WarningItem, ...], dict | None]:
        """Native text per page, falling back to image OCR for any page with no text
        layer (a scanned page). Scan pages are rendered and recognized with bounded
        concurrency (pdf_ocr_concurrency) and still serialize on the shared OCR
        backend gate, the same one the standalone "image" kind uses.
        """
        extracted, scan_pages = await asyncio.to_thread(extract_pdf, input_path, self.settings)
        warnings = list(extracted.warnings)
        native_items = [(block.metadata.get("page", 0), 0, block.kind, block.text, block.metadata)
                        for block in extracted.blocks]
        if not scan_pages:
            return extracted.blocks, tuple(warnings), None
        if not self.settings.ocr_enabled:
            if not extracted.blocks:
                raise TranslationError("DEPENDENCY_UNAVAILABLE", "OCR service is unavailable.")
            warnings.append(WarningItem(code="OCR_CORRECTION_REJECTED",
                                        message="Scanned pages were skipped; OCR is unavailable."))
            return extracted.blocks, tuple(warnings), None
        if len(scan_pages) > self.settings.pdf_max_ocr_pages:
            raise TranslationError("TEXT_TOO_LONG", "PDF has too many scanned pages.")

        from .ocr_client import ImageOcrPipeline

        ocr_items: list[tuple[int, int, str, str, dict]] = []
        ocr_summary: dict[str, dict] = {}
        concurrency = asyncio.Semaphore(max(1, self.settings.pdf_ocr_concurrency))

        async def ocr_page(page_number: int) -> None:
            async with concurrency:
                with TemporaryDirectory(prefix="secai-pdf-render-") as render_dir:
                    page_path = await asyncio.to_thread(
                        render_pdf_page, input_path, page_number, Path(render_dir))
                    async with self.ocr_gate:
                        with TemporaryDirectory(prefix="secai-pdf-ocr-") as work_dir:
                            corrected = await context.run_bounded(
                                ImageOcrPipeline(settings=self.settings).process(
                                    page_path, work_dir=Path(work_dir), context=context))
                for sub_index, block in enumerate(corrected.blocks):
                    ocr_items.append((page_number, sub_index, "ocr_text", block.text,
                                      {**block.metadata, "page": page_number}))
                if corrected.needs_review:
                    warnings.append(WarningItem(
                        code="OCR_LOW_CONFIDENCE", message=f"Page {page_number} OCR needs review."))
                ocr_summary[str(page_number)] = {
                    "raw_text": corrected.raw_text, "corrected_text": corrected.corrected_text,
                    "needs_review": corrected.needs_review,
                }

        await asyncio.gather(*(ocr_page(page) for page in scan_pages))
        merged = sorted(native_items + ocr_items, key=lambda item: (item[0], item[1]))
        blocks = tuple(
            DocumentBlock(id=f"b{i:06d}", kind=kind, order=i - 1, text=text, metadata=metadata)
            for i, (_, _, kind, text, metadata) in enumerate(merged, 1))
        if not blocks:
            raise TranslationError("NO_TEXT_DETECTED", "No text was detected.")
        ocr = {"pages": ocr_summary} if ocr_summary else None
        return blocks, tuple(warnings), ocr

    async def run_once(self) -> bool:
        row = await asyncio.to_thread(self.store.claim)
        if row is None:
            return False
        heartbeat = asyncio.create_task(self._heartbeat(row))
        try:
            context = WorkerContext(self.store, self.settings, self.runner, row, self.llm_gate)
            await context.checkpoint("extracting")
            input_path = Path(row["input_path"])
            if row["kind"] == "image":
                from .ocr_client import ImageOcrPipeline

                async with self.ocr_gate:
                    with TemporaryDirectory(prefix="secai-image-") as tmp:
                        corrected = await context.run_bounded(
                            ImageOcrPipeline(settings=self.settings).process(
                                input_path, work_dir=Path(tmp), context=context))
                blocks = corrected.blocks
                warnings = corrected.warnings
                ocr = {
                    "raw_text": corrected.raw_text,
                    "corrected_text": corrected.corrected_text,
                    "edits": [vars(edit) for edit in corrected.edits],
                    "needs_review": corrected.needs_review,
                }
            elif row["extension"] == ".txt":
                payload = await asyncio.to_thread(input_path.read_bytes)
                extracted = await asyncio.to_thread(extract_txt, payload, self.settings)
                blocks, warnings, ocr = extracted.blocks, extracted.warnings, None
            elif row["extension"] == ".docx":
                extracted = await asyncio.to_thread(extract_docx, input_path, self.settings)
                blocks, warnings, ocr = extracted.blocks, extracted.warnings, None
            elif row["extension"] == ".doc":
                from .documents import convert_doc

                with TemporaryDirectory(prefix="secai-doc-") as tmp:
                    converted = await asyncio.to_thread(convert_doc, input_path, Path(tmp), self.settings)
                    extracted = await asyncio.to_thread(extract_docx, converted, self.settings)
                blocks, warnings, ocr = extracted.blocks, extracted.warnings, None
            elif row["extension"] == ".pdf":
                blocks, warnings, ocr = await self._extract_pdf_with_ocr_fallback(context, input_path)
            else:
                raise TranslationError("UNSUPPORTED_FILE_TYPE", "File type is unsupported.")
            translated = await translate_blocks(blocks, row["source_lang"], row["target_lang"],
                                                context=context)
            result = {
                "job_id": row["job_id"], "kind": row["kind"], "status": "succeeded",
                "source_lang": row["source_lang"], "target_lang": row["target_lang"],
                "translated_text": "\n".join(item.translated_text for item in translated),
                "blocks": [
                    {"id": item.source.id, "kind": item.source.kind, "order": item.source.order,
                     "source_text": item.source.text, "translated_text": item.translated_text,
                     "metadata": item.source.metadata}
                    for item in translated
                ],
                "warnings": [vars(warning) for warning in warnings],
                "ocr": ocr,
            }
            await asyncio.to_thread(self.store.finish, row["job_id"], row["generation"], result=result)
        except _JobStopped:
            await asyncio.to_thread(self.store.finish, row["job_id"], row["generation"])
        except asyncio.CancelledError:
            # A concurrent DELETE may be acknowledged, but a running claim
            # without a result stays recoverable when its lease expires.
            await asyncio.to_thread(self.store.finish, row["job_id"], row["generation"])
            raise
        except TranslationError as exc:
            await asyncio.to_thread(self.store.finish, row["job_id"], row["generation"],
                                    error_code=exc.code)
        except Exception:
            await asyncio.to_thread(self.store.finish, row["job_id"], row["generation"],
                                    error_code="INTERNAL_ERROR")
        finally:
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)
        return True

    async def serve(self) -> None:
        while True:
            worked = await self.run_once()
            await asyncio.to_thread(self.store.reap)
            if not worked:
                await asyncio.sleep(0.5)


async def main() -> None:
    """Process entrypoint expects private stores selected before package import."""
    if os.environ.get("SESSION_STORE_BACKEND") != "memory" or os.environ.get("REDIS_URL"):
        raise RuntimeError("Translation worker requires in-memory sessions and disabled Redis")
    settings = TranslationSettings()
    if not settings.enabled:
        raise RuntimeError("Translation v2 is disabled")
    from ..agent.runner import LinchRunner

    runner = (LinchRunner(settings.worker_db_path, strict_transform_errors=True)
              if settings.ocr_correction_supported else None)
    if runner is not None and runner.session_store.__class__.__name__ != "InMemorySessionStore":
        raise RuntimeError("Translation worker session store is not in memory")
    if runner is not None:
        await _disable_provider_retries(runner)
    await Worker(JobStore(settings), settings, runner).serve()


async def _disable_provider_retries(runner: Any) -> None:
    """Leave the worker's single retry budget at WorkerContext, not inside OpenAI SDK."""
    runner.agent.max_retries = 0
    provider = runner.agent.provider
    client = await provider._get_client()
    provider._client = client.with_options(max_retries=0)


if __name__ == "__main__":
    asyncio.run(main())
