import pytest
import httpx
import asyncio
import gzip
import json

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


@pytest.mark.asyncio
async def test_trickling_ocr_response_obeys_absolute_call_timeout(tmp_path, monkeypatch):
    from secai_gateway.translation.ocr_client import recognize_image
    from secai_gateway.translation.types import PreparedImage

    class Trickle(httpx.AsyncByteStream):
        async def __aiter__(self):
            while True:
                await asyncio.sleep(0.025)
                yield b" "

    transport = httpx.MockTransport(lambda request: httpx.Response(200, stream=Trickle()))
    real_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real_client(transport=transport, **kw))
    path = tmp_path / "image.png"
    path.write_bytes(b"test")
    image = PreparedImage(path=path, media_type="image/png", width=1, height=1)
    settings = TranslationSettings(ocr_enabled=True, ocr_api_key="secret", ocr_model="test",
                                   ocr_base_url="http://127.0.0.1:18083",
                                   ocr_timeout_seconds=0.05, ocr_max_retries=0, _env_file=None)
    with pytest.raises(TranslationError) as error:
        await asyncio.wait_for(recognize_image(image, settings=settings), timeout=0.3)
    assert error.value.code == "OCR_UNAVAILABLE"
    assert error.value.retryable


@pytest.mark.asyncio
async def test_gzip_ocr_response_is_decoded_with_decoded_size_cap(tmp_path, monkeypatch):
    from secai_gateway.translation.ocr_client import recognize_image
    from secai_gateway.translation.types import PreparedImage

    payload = json.dumps({"model": "test", "blocks": [
        {"text": "hello", "bbox": None, "confidence": 0.9}]}).encode()
    compressed = gzip.compress(payload)
    body = [compressed]
    class OneChunk(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield body[0]

    transport = httpx.MockTransport(lambda request: httpx.Response(
        200, headers={"Content-Encoding": "gzip"}, stream=OneChunk()))
    real_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real_client(transport=transport, **kw))
    path = tmp_path / "image.png"
    path.write_bytes(b"test")
    image = PreparedImage(path=path, media_type="image/png", width=1, height=1)
    settings = TranslationSettings(ocr_enabled=True, ocr_api_key="secret", ocr_model="test",
                                   ocr_base_url="http://127.0.0.1:18083", ocr_max_retries=0,
                                   _env_file=None)
    assert (await recognize_image(image, settings=settings)).raw_text == "hello"
    body[0] = gzip.compress(b"x" * (2 * 1024 * 1024 + 1))
    with pytest.raises(TranslationError, match="exceeds limit"):
        await recognize_image(image, settings=settings)


@pytest.mark.asyncio
async def test_ocr_retry_count_cannot_exceed_contract_when_configured_higher(tmp_path, monkeypatch):
    from secai_gateway.translation import ocr_client
    from secai_gateway.translation.types import PreparedImage

    calls = 0

    def unavailable(request):
        nonlocal calls
        calls += 1
        return httpx.Response(503)

    transport = httpx.MockTransport(unavailable)
    real_client = httpx.AsyncClient
    real_sleep = asyncio.sleep
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real_client(transport=transport, **kw))
    monkeypatch.setattr(ocr_client.asyncio, "sleep", lambda seconds: real_sleep(0))
    path = tmp_path / "image.png"
    path.write_bytes(b"test")
    image = PreparedImage(path=path, media_type="image/png", width=1, height=1)
    settings = TranslationSettings(ocr_enabled=True, ocr_api_key="secret", ocr_model="test",
                                   ocr_base_url="http://127.0.0.1:18083", ocr_max_retries=3,
                                   _env_file=None)
    with pytest.raises(TranslationError) as error:
        await ocr_client.recognize_image(image, settings=settings)
    assert error.value.code == "OCR_UNAVAILABLE"
    assert calls == 2
