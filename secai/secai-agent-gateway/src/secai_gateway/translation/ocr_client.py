"""Strict adapter for the private OCR service; only this module owns its wire format."""
from __future__ import annotations

import asyncio
import base64
import ipaddress
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


async def recognize_image(image: PreparedImage, *, settings: TranslationSettings) -> OcrResult:
    if not settings.ocr_enabled or not settings.ocr_api_key or not settings.ocr_model:
        raise TranslationError("OCR_UNAVAILABLE", "OCR is not configured")
    url = _base_url(settings) + "/v1/ocr"
    encoded = base64.b64encode(await asyncio.to_thread(image.path.read_bytes)).decode("ascii")
    body = {"model": settings.ocr_model, "image_base64": encoded}
    timeout = httpx.Timeout(settings.ocr_timeout_seconds)
    for attempt in range(settings.ocr_max_retries + 1):
        try:
            async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
                response = await client.post(url, json=body, headers={
                    "Authorization": f"Bearer {settings.ocr_api_key}"})
            if response.status_code in {502, 503, 504} and attempt < settings.ocr_max_retries:
                await asyncio.sleep(min(2 ** attempt, 2))
                continue
            response.raise_for_status()
            if len(response.content) > _MAX_RESPONSE:
                raise TranslationError("OCR_UNAVAILABLE", "OCR response exceeds limit")
            result = _parse_response(response.json())
            if response.json()["model"] != settings.ocr_model:
                raise TranslationError("OCR_UNAVAILABLE", "OCR model mismatch")
            return result
        except TranslationError:
            raise
        except (httpx.HTTPError, ValueError) as exc:
            if attempt == settings.ocr_max_retries:
                raise TranslationError("OCR_UNAVAILABLE", "OCR service unavailable", retryable=True) from exc
            await asyncio.sleep(min(2 ** attempt, 2))
    raise TranslationError("OCR_UNAVAILABLE", "OCR service unavailable", retryable=True)


class ImageOcrPipeline:
    def __init__(self, *, settings: TranslationSettings):
        self.settings = settings

    async def process(self, input_path: Path, *, work_dir: Path,
                      context: ProcessingContext):
        from .ocr_correction import correct_ocr

        await context.checkpoint("preprocessing")
        prepared = await preprocess_image(input_path, work_dir=work_dir)
        await context.checkpoint("ocr")
        result = await recognize_image(prepared, settings=self.settings)
        if not result.blocks:
            raise TranslationError("NO_TEXT_DETECTED", "No text detected")
        await context.checkpoint("correcting")
        return await correct_ocr(result, context=context)
