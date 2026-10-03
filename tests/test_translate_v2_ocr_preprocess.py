import io

import pytest
from PIL import Image

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
@pytest.mark.parametrize("data", [b"<svg/>", b"%PDF-1.7", b"\x89PNG\r\n\x1a\ninvalid"])
async def test_rejects_bad_image(data, tmp_path):
    source = tmp_path / "image"
    source.write_bytes(data)
    with pytest.raises(TranslationError) as error:
        await preprocess_image(source, work_dir=tmp_path / "work")
    assert error.value.code == "CORRUPT_IMAGE"
