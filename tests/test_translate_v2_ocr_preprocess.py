import asyncio
import io
import threading

import pytest
from PIL import Image
from PIL import ImageDraw, ImageFont

from secai_gateway.translation.ocr_preprocess import preprocess_image
from secai_gateway.translation.types import TranslationError


@pytest.mark.asyncio
async def test_strips_metadata_and_preserves_dimensions(tmp_path):
    source = tmp_path / "source.png"
    image = Image.new("RGB", (30, 20), "white")
    image.save(source, pnginfo=None)
    output = await preprocess_image(source, work_dir=tmp_path / "work")
    assert (output.width, output.height, output.media_type) == (30, 20, "image/png")
    with Image.open(output.path) as prepared:
        assert prepared.size == (30, 20)


@pytest.mark.asyncio
async def test_exif_orientation_and_deskew(tmp_path):
    jpeg = tmp_path / "rotated.jpg"
    image = Image.new("RGB", (40, 80), "white")
    ImageDraw.Draw(image).text((3, 10), "TEST", fill="black")
    exif = Image.Exif()
    exif[274] = 6
    image.save(jpeg, exif=exif)
    output = await preprocess_image(jpeg, work_dir=tmp_path / "orientation")
    assert output.media_type == "image/jpeg"
    assert (output.width, output.height) == (80, 40)
    with Image.open(output.path) as prepared:
        assert not prepared.getexif()

    skew = tmp_path / "skew.png"
    line = Image.new("RGB", (800, 150), "white")
    font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 28)
    ImageDraw.Draw(line).text((30, 30), "Security review completed", font=font, fill="black")
    line.rotate(3, expand=True, fillcolor="white").save(skew)
    prepared = await preprocess_image(skew, work_dir=tmp_path / "deskew")
    assert prepared.width > 800  # a conservative correction was selected


@pytest.mark.asyncio
@pytest.mark.parametrize("data", [b"<svg/>", b"%PDF-1.7", b"\x89PNG\r\n\x1a\ninvalid"])
async def test_rejects_bad_image(data, tmp_path):
    source = tmp_path / "image"
    source.write_bytes(data)
    with pytest.raises(TranslationError) as error:
        await preprocess_image(source, work_dir=tmp_path / "work")
    assert error.value.code == "CORRUPT_IMAGE"


@pytest.mark.asyncio
@pytest.mark.parametrize("fmt", ["PNG", "JPEG"])
async def test_cancel_preserves_caller_owned_workdir_and_files(tmp_path, monkeypatch, fmt):
    from secai_gateway.translation import ocr_preprocess

    source = tmp_path / ("source.png" if fmt == "PNG" else "source.jpg")
    Image.new("RGB", (64, 64), "white").save(source, format=fmt)
    work_dir = tmp_path / "existing"
    work_dir.mkdir()
    png = work_dir / "ocr-prepared.png"
    jpg = work_dir / "ocr-prepared.jpg"
    png.write_bytes(b"caller PNG")
    jpg.write_bytes(b"caller JPEG")
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    real_prepare = ocr_preprocess._prepare

    def stalled_deskew(image):
        started.set()
        assert release.wait(5)
        return image

    def tracked_prepare(*args):
        try:
            return real_prepare(*args)
        finally:
            finished.set()

    monkeypatch.setattr(ocr_preprocess, "_deskew", stalled_deskew)
    monkeypatch.setattr(ocr_preprocess, "_prepare", tracked_prepare)
    task = asyncio.create_task(preprocess_image(source, work_dir=work_dir))
    try:
        assert await asyncio.wait_for(asyncio.to_thread(started.wait, 2), 3)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 1)
    finally:
        release.set()
        assert await asyncio.wait_for(asyncio.to_thread(finished.wait, 3), 4)
    assert work_dir.is_dir()
    assert png.read_bytes() == b"caller PNG"
    assert jpg.read_bytes() == b"caller JPEG"


@pytest.mark.asyncio
async def test_cancel_preserves_caller_owned_empty_workdir(tmp_path, monkeypatch):
    from secai_gateway.translation import ocr_preprocess

    source = tmp_path / "source.png"
    Image.new("RGB", (64, 64), "white").save(source)
    work_dir = tmp_path / "existing"
    work_dir.mkdir()
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    real_prepare = ocr_preprocess._prepare

    def stalled_deskew(image):
        started.set()
        assert release.wait(5)
        return image

    def tracked_prepare(*args):
        try:
            return real_prepare(*args)
        finally:
            finished.set()

    monkeypatch.setattr(ocr_preprocess, "_deskew", stalled_deskew)
    monkeypatch.setattr(ocr_preprocess, "_prepare", tracked_prepare)
    task = asyncio.create_task(preprocess_image(source, work_dir=work_dir))
    try:
        assert await asyncio.wait_for(asyncio.to_thread(started.wait, 2), 3)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 1)
    finally:
        release.set()
        assert await asyncio.wait_for(asyncio.to_thread(finished.wait, 3), 4)
    assert work_dir.is_dir()
