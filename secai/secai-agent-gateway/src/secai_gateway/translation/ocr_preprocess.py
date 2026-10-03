"""Bounded, metadata-free image preparation for the isolated OCR service."""
from __future__ import annotations

import asyncio
import io
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

from .types import PreparedImage, TranslationError

_MAX_BYTES = 10 * 1024 * 1024
_MAX_PIXELS = 20_000_000
_MAX_EDGE = 10_000


def _prepare(input_path: Path, work_dir: Path) -> PreparedImage:
    try:
        if input_path.stat().st_size > _MAX_BYTES:
            raise TranslationError("UPLOAD_TOO_LARGE", "Image exceeds size limit")
        data = input_path.read_bytes()
        if not (data.startswith(b"\x89PNG\r\n\x1a\n") or data.startswith(b"\xff\xd8\xff")):
            raise TranslationError("CORRUPT_IMAGE", "Unsupported image encoding")
        with Image.open(io.BytesIO(data)) as source:
            if source.format not in {"PNG", "JPEG"}:
                raise TranslationError("CORRUPT_IMAGE", "Unsupported image encoding")
            width, height = source.size
            if width < 1 or height < 1 or max(width, height) > _MAX_EDGE or width * height > _MAX_PIXELS:
                raise TranslationError("IMAGE_TOO_LARGE", "Image exceeds pixel limit")
            source.load()
            image = ImageOps.exif_transpose(source).convert("RGB")
            # Preserve clean pixels; EXIF orientation is the only automatic transform.
            output = work_dir / "ocr-prepared.png"
            work_dir.mkdir(parents=True, exist_ok=True)
            image.save(output, format="PNG", exif=b"")
            return PreparedImage(path=output, media_type="image/png", width=image.width, height=image.height)
    except TranslationError:
        raise
    except (OSError, ValueError, UnidentifiedImageError, Image.DecompressionBombError) as exc:
        raise TranslationError("CORRUPT_IMAGE", "Cannot decode image") from exc


async def preprocess_image(input_path: Path, *, work_dir: Path) -> PreparedImage:
    return await asyncio.to_thread(_prepare, input_path, work_dir)
