"""Bounded, metadata-free image preparation for the isolated OCR service."""
from __future__ import annotations

import asyncio
import io
from pathlib import Path

import numpy as np
from PIL import Image, ImageEnhance, ImageOps, UnidentifiedImageError

from .types import PreparedImage, TranslationError

_MAX_BYTES = 10 * 1024 * 1024
_MAX_PIXELS = 20_000_000
_MAX_EDGE = 10_000


def _deskew(image: Image.Image) -> Image.Image:
    """Rotate only when a text-line projection clearly beats the original."""
    sample = ImageOps.grayscale(image)
    sample.thumbnail((1000, 1000))
    lo, hi = sample.getextrema()
    if hi - lo < 25:  # blank or uniformly colored image
        return image
    threshold = lo + 0.55 * (hi - lo)

    def score(candidate: Image.Image) -> float:
        rows = (np.asarray(candidate) < threshold).sum(axis=1)
        total = rows.sum()
        return float(np.square(rows.astype(np.float64)).sum() / max(total * total, 1))

    baseline = score(sample)
    best_angle, best_score = 0, baseline
    for angle in (-4, -3, -2, -1, 1, 2, 3, 4):
        candidate = sample.rotate(angle, expand=True, fillcolor=255)
        value = score(candidate)
        if value > best_score:
            best_angle, best_score = angle, value
    if best_angle and best_score > baseline * 1.12:
        rotated = image.rotate(best_angle, expand=True, fillcolor="white")
        if max(rotated.size) <= _MAX_EDGE and rotated.width * rotated.height <= _MAX_PIXELS:
            return rotated
    return image


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
            image = _deskew(image)
            low, high = ImageOps.grayscale(image).getextrema()
            if low > 45 and high - low < 190:
                image = ImageEnhance.Contrast(image).enhance(1.35)
            fmt = "JPEG" if source.format == "JPEG" else "PNG"
            output = work_dir / ("ocr-prepared.jpg" if fmt == "JPEG" else "ocr-prepared.png")
            work_dir.mkdir(parents=True, exist_ok=True)
            image.save(output, format=fmt, exif=b"", quality=90 if fmt == "JPEG" else None)
            if output.stat().st_size > _MAX_BYTES:
                output.unlink(missing_ok=True)
                raise TranslationError("IMAGE_TOO_LARGE", "Prepared image exceeds size limit")
            return PreparedImage(path=output, media_type="image/jpeg" if fmt == "JPEG" else "image/png",
                                 width=image.width, height=image.height)
    except TranslationError:
        raise
    except (OSError, ValueError, UnidentifiedImageError, Image.DecompressionBombError) as exc:
        raise TranslationError("CORRUPT_IMAGE", "Cannot decode image") from exc


async def preprocess_image(input_path: Path, *, work_dir: Path) -> PreparedImage:
    return await asyncio.to_thread(_prepare, input_path, work_dir)
