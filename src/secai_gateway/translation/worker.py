"""Single supervised translation worker; never uses the web GatewayState runner."""
from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from .documents import extract_docx, extract_txt
from .jobs import JobStore
from .service import translate_blocks
from .settings import TranslationSettings
from .types import ProcessingContext, TranslationError


class WorkerContext(ProcessingContext):
    def __init__(self, store: JobStore, settings: TranslationSettings, runner: Any,
                 row: dict, llm_gate: asyncio.Semaphore):
        self.store = store
        self.settings = settings
        self.runner = runner
        self.row = row
        self.llm_gate = llm_gate

    async def checkpoint(self, stage: str, completed: int = 0, total: int | None = None) -> None:
        if time.time() >= self.row["deadline"]:
            raise TranslationError("PROCESSING_TIMEOUT", "Processing timed out.")
        ok = await asyncio.to_thread(self.store.progress, self.row["job_id"],
                                     self.row["generation"], stage, completed, total)
        if not ok:
            raise asyncio.CancelledError

    async def complete(self, profile: str, prompt: str) -> tuple[str, dict[str, Any]]:
        if profile not in {"translate_v2", "ocr_correction"}:
            raise TranslationError("INTERNAL_ERROR", "Unsupported transformation profile.")
        await self.checkpoint("translating" if profile == "translate_v2" else "correcting")
        remaining = max(0.01, self.row["deadline"] - time.time())
        async with self.llm_gate:
            for attempt in range(self.settings.llm_max_retries + 1):
                try:
                    call = asyncio.create_task(self.runner.complete(profile, prompt, None))

                    async def watch_cancel():
                        while True:
                            await asyncio.sleep(0.5)
                            state = await asyncio.to_thread(self.store.get, self.row["job_id"])
                            if state["status"] != "running":
                                return

                    watcher = asyncio.create_task(watch_cancel())
                    try:
                        done, _ = await asyncio.wait({call, watcher},
                            timeout=min(self.settings.llm_timeout_seconds, remaining),
                            return_when=asyncio.FIRST_COMPLETED)
                        if watcher in done:
                            raise asyncio.CancelledError
                        if call not in done:
                            raise asyncio.TimeoutError
                        return call.result()
                    finally:
                        call.cancel()
                        watcher.cancel()
                        await asyncio.gather(call, watcher, return_exceptions=True)
                except (asyncio.TimeoutError, OSError) as exc:
                    if attempt >= self.settings.llm_max_retries:
                        raise TranslationError("TRANSLATION_UNAVAILABLE",
                                               "Translation service is unavailable.") from exc
        raise TranslationError("TRANSLATION_UNAVAILABLE", "Translation service is unavailable.")


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
                        corrected = await ImageOcrPipeline(settings=self.settings).process(
                            input_path, work_dir=Path(tmp), context=context)
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
        except asyncio.CancelledError:
            await asyncio.to_thread(self.store.finish, row["job_id"], row["generation"])
        except TranslationError as exc:
            await asyncio.to_thread(self.store.finish, row["job_id"], row["generation"],
                                    error_code=exc.code)
        except Exception:
            await asyncio.to_thread(self.store.finish, row["job_id"], row["generation"],
                                    error_code="INTERNAL_ERROR")
        finally:
            heartbeat.cancel()
            try:
                await heartbeat
            except asyncio.CancelledError:
                pass
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

    runner = LinchRunner(settings.worker_db_path)
    if runner.session_store.__class__.__name__ != "InMemorySessionStore":
        raise RuntimeError("Translation worker session store is not in memory")
    await Worker(JobStore(settings), settings, runner).serve()
