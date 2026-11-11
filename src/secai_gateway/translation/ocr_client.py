"""Strict adapter for the private OCR service; only this module owns its wire format."""
from __future__ import annotations

import asyncio
import random
import base64
import ipaddress
import json
import zlib
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from .ocr_preprocess import preprocess_image
from .settings import TranslationSettings
from .types import DocumentBlock, OcrResult, PreparedImage, ProcessingContext, TranslationError

_MAX_RESPONSE = 2 * 1024 * 1024
_MAX_BLOCKS = 1024
_MAX_TEXT = 200_000


def _base_url(settings: TranslationSettings) -> str:
    url = settings.ocr_base_url.rstrip("/")
    parsed = urlsplit(url)
    try:
        local = parsed.hostname in {"localhost", "127.0.0.1", "::1"} or (
            parsed.hostname and ipaddress.ip_address(parsed.hostname).is_private
        )
    except ValueError:
        local = parsed.hostname == "localhost"
    if parsed.scheme != "http" or not local or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise TranslationError("OCR_UNAVAILABLE", "Private OCR endpoint is not configured")
    return url


def _parse_response(payload: object) -> OcrResult:
    if not isinstance(payload, dict) or set(payload) != {"model", "blocks"}:
        raise TranslationError("OCR_UNAVAILABLE", "Invalid OCR response")
    rows = payload["blocks"]
    if not isinstance(rows, list) or len(rows) > _MAX_BLOCKS:
        raise TranslationError("OCR_UNAVAILABLE", "Invalid OCR response")
    blocks = []
    total = 0
    for index, row in enumerate(rows):
        if not isinstance(row, dict) or set(row) != {"text", "bbox", "confidence"}:
            raise TranslationError("OCR_UNAVAILABLE", "Invalid OCR response")
        value, box, confidence = row["text"], row["bbox"], row["confidence"]
        if not isinstance(value, str) or not value or "\x00" in value:
            raise TranslationError("OCR_UNAVAILABLE", "Invalid OCR response")
        total += len(value)
        if total > _MAX_TEXT:
            raise TranslationError("OCR_UNAVAILABLE", "Invalid OCR response")
        if box is not None and (not isinstance(box, list) or len(box) != 4 or
                any(type(x) is not int or x < 0 for x in box)):
            raise TranslationError("OCR_UNAVAILABLE", "Invalid OCR response")
        if confidence is not None and (type(confidence) not in {int, float} or
                                       not 0 <= confidence <= 1):
            raise TranslationError("OCR_UNAVAILABLE", "Invalid OCR response")
        metadata = {}
        if box is not None:
            metadata["bbox"] = box
        if confidence is not None:
            metadata["confidence"] = confidence
        blocks.append(DocumentBlock(id=f"b{index + 1:06d}", kind="ocr_text", order=index,
                                    text=value, metadata=metadata))
    return OcrResult(blocks=tuple(blocks), raw_text="\n".join(b.text for b in blocks))


class _OcrBusy(Exception):
    """The single-slot OCR server is serving another request (503 "OCR busy"): wait and retry."""


async def recognize_image(image: PreparedImage, *, settings: TranslationSettings) -> OcrResult:
    if not settings.ocr_enabled or not settings.ocr_api_key or not settings.ocr_model:
        raise TranslationError("OCR_UNAVAILABLE", "OCR is not configured")
    url = _base_url(settings) + "/v1/ocr"
    encoded = base64.b64encode(await asyncio.to_thread(image.path.read_bytes)).decode("ascii")
    body = {"model": settings.ocr_model, "image_base64": encoded}
    timeout = httpx.Timeout(settings.ocr_timeout_seconds)
    async def request_with_retries() -> OcrResult:
        retries = min(max(0, settings.ocr_max_retries), 1)
        attempt = -1
        while attempt < retries:
            attempt += 1
            try:
                async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
                    async with client.stream("POST", url, json=body, headers={
                        "Authorization": f"Bearer {settings.ocr_api_key}",
                        "Accept-Encoding": "gzip, identity"}) as response:
                        if response.status_code == 503 and b"OCR busy" in (await response.aread()):
                            raise _OcrBusy
                        if response.status_code in {502, 503, 504}:
                            raise httpx.HTTPStatusError("OCR temporarily unavailable",
                                                        request=response.request, response=response)
                        response.raise_for_status()
                        encoding = response.headers.get("Content-Encoding", "identity").lower()
                        if encoding not in {"identity", "gzip"}:
                            raise TranslationError("OCR_UNAVAILABLE", "Unsupported OCR response encoding")
                        decoder = zlib.decompressobj(16 + zlib.MAX_WBITS) if encoding == "gzip" else None
                        chunks = []
                        wire_size = decoded_size = 0
                        async for chunk in response.aiter_raw():
                            wire_size += len(chunk)
                            if wire_size > _MAX_RESPONSE:
                                raise TranslationError("OCR_UNAVAILABLE", "OCR response exceeds limit")
                            try:
                                part = decoder.decompress(chunk, _MAX_RESPONSE - decoded_size + 1) if decoder else chunk
                            except zlib.error as exc:
                                raise TranslationError("OCR_UNAVAILABLE", "Invalid OCR response") from exc
                            decoded_size += len(part)
                            if decoded_size > _MAX_RESPONSE or (decoder and decoder.unconsumed_tail):
                                raise TranslationError("OCR_UNAVAILABLE", "OCR response exceeds limit")
                            chunks.append(part)
                        if decoder:
                            try:
                                tail = decoder.flush(_MAX_RESPONSE - decoded_size + 1)
                            except zlib.error as exc:
                                raise TranslationError("OCR_UNAVAILABLE", "Invalid OCR response") from exc
                            decoded_size += len(tail)
                            if decoded_size > _MAX_RESPONSE:
                                raise TranslationError("OCR_UNAVAILABLE", "OCR response exceeds limit")
                            if not decoder.eof or decoder.unused_data:
                                raise TranslationError("OCR_UNAVAILABLE", "Invalid OCR response")
                            chunks.append(tail)
                payload = json.loads(b"".join(chunks))
                result = _parse_response(payload)
                if payload["model"] != settings.ocr_model:
                    raise TranslationError("OCR_UNAVAILABLE", "OCR model mismatch")
                return result
            except TranslationError:
                raise
            except _OcrBusy:
                # Waiting for the slot is not a failure and does not use a retry; the overall
                # ocr_timeout_seconds bound below still applies.
                attempt -= 1
                await asyncio.sleep(random.uniform(0.5, 2.0))
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code not in {502, 503, 504} or attempt == retries:
                    raise TranslationError("OCR_UNAVAILABLE", "OCR service unavailable",
                                           retryable=exc.response.status_code in {502, 503, 504}) from exc
                await asyncio.sleep(min(2 ** attempt, 2))
            except ValueError as exc:
                raise TranslationError("OCR_UNAVAILABLE", "Invalid OCR response") from exc
            except httpx.TransportError as exc:
                if attempt == retries:
                    raise TranslationError("OCR_UNAVAILABLE", "OCR service unavailable", retryable=True) from exc
                await asyncio.sleep(min(2 ** attempt, 2))
        raise TranslationError("OCR_UNAVAILABLE", "OCR service unavailable", retryable=True)

    try:
        # HTTP read timeout is idle-based; this bounds the whole request and retry sequence.
        return await asyncio.wait_for(request_with_retries(), settings.ocr_timeout_seconds)
    except asyncio.TimeoutError as exc:
        raise TranslationError("OCR_UNAVAILABLE", "OCR service timed out", retryable=True) from exc


class ImageOcrPipeline:
    def __init__(self, *, settings: TranslationSettings):
        self.settings = settings

    async def process(self, input_path: Path, *, work_dir: Path,
                      context: ProcessingContext):
        from .ocr_correction import correct_ocr
        from .types import CorrectedOcr, WarningItem

        await context.checkpoint("preprocessing")
        prepared = await preprocess_image(input_path, work_dir=work_dir)
        await context.checkpoint("ocr")
        result = await recognize_image(prepared, settings=self.settings)
        if not result.blocks:
            raise TranslationError("NO_TEXT_DETECTED", "No text detected")
        await context.checkpoint("correcting")
        if not self.settings.ocr_correction_supported:
            return CorrectedOcr(blocks=result.blocks, raw_text=result.raw_text,
                                corrected_text=result.raw_text, needs_review=True,
                                warnings=(*result.warnings, WarningItem(
                                    code="OCR_CORRECTION_REJECTED",
                                    message="Automatic OCR correction is unavailable; review raw text.")))
        return await correct_ocr(result, context=context)
