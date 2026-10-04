import pytest
import httpx

from secai_gateway.translation.ocr_client import _base_url, _parse_response
from secai_gateway.translation.settings import TranslationSettings
from secai_gateway.translation.types import TranslationError


def test_private_loopback_only():
    assert _base_url(TranslationSettings(ocr_base_url="http://127.0.0.1:18083/")) == "http://127.0.0.1:18083"
    with pytest.raises(TranslationError):
        _base_url(TranslationSettings(ocr_base_url="http://public.example:18083"))


def test_response_metadata_must_be_truthful():
    output = _parse_response({"model": "test", "blocks": [
        {"text": "Xin chào", "bbox": None, "confidence": None}]})
    assert output.raw_text == "Xin chào"
    assert output.blocks[0].metadata == {}
    with pytest.raises(TranslationError):
        _parse_response({"model": "test", "blocks": [
            {"text": "fake", "bbox": None, "confidence": 1.2}]})


@pytest.mark.asyncio
async def test_oversize_ocr_response_stops_reading_at_limit(tmp_path, monkeypatch):
    from secai_gateway.translation.ocr_client import recognize_image
    from secai_gateway.translation.types import PreparedImage

    class ResponseStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            for _ in range(3):
                yield b"x" * (1024 * 1024)
            raise AssertionError("OCR client consumed past its response limit")

    transport = httpx.MockTransport(lambda request: httpx.Response(200, stream=ResponseStream()))
    real_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real_client(transport=transport, **kw))
    image_path = tmp_path / "image.png"
    image_path.write_bytes(b"test")
    image = PreparedImage(path=image_path, media_type="image/png", width=1, height=1)
    settings = TranslationSettings(ocr_enabled=True, ocr_api_key="secret", ocr_model="test",
                                   ocr_base_url="http://127.0.0.1:18083", ocr_max_retries=0,
                                   _env_file=None)
    with pytest.raises(TranslationError, match="exceeds limit"):
        await recognize_image(image, settings=settings)
