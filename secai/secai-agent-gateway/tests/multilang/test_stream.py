import asyncio
import json
import re

import httpx
import pytest

from secai_gateway.api.multilang import routes
from secai_gateway.multilang.config import EXPECTED_MODEL
from secai_gateway.multilang.provider import GenerationResult

from .test_api import configured, incumbent
from secai_gateway.multilang.service import TranslationRequest, translate


@pytest.mark.asyncio
async def test_stream_validated_chunks_and_final(incumbent):
    class Translator:
        async def generate(self, request):
            tokens = re.findall(r'⟦ML-[^⟧]+⟧', request.user)
            return GenerationResult('Inspect security logs ' + ' '.join(tokens), 'stop', EXPECTED_MODEL)
    app = routes.create_v3_app(settings_factory=lambda: configured(enable_en=True),
                              legacy=incumbent, llm=Translator())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        response = await client.post('/ai/v3/translate/stream', json={
            'text': 'Kiểm tra nhật ký tại 192.0.2.15.', 'source_lang': 'vi', 'target_lang': 'en'})
        assert response.status_code == 200
        assert response.headers['content-type'].startswith('text/event-stream')
        events = [(frame.splitlines()[0], json.loads(frame.split('data: ', 1)[1]))
                  for frame in response.text.strip().split('\n\n') if frame.startswith('event:')]
        assert events[0][0] == 'event: chunk'
        assert '192.0.2.15' in events[0][1]['delta']
        assert events[-1][0] == 'event: final'
        assert ''.join(data['delta'] for name, data in events if name == 'event: chunk') == events[-1][1]['translated_text']
        assert response.text.endswith('data: [DONE]\n\n')
        disabled = await client.post('/ai/v3/translate/stream', json={
            'text': 'Kiểm tra nhật ký.', 'source_lang': 'vi', 'target_lang': 'ja'})
        assert disabled.status_code == 403


@pytest.mark.asyncio
async def test_stream_does_not_emit_invalid_draft(incumbent):
    class Broken:
        async def generate(self, request):
            return GenerationResult('Wrong output without required IP.', 'stop', EXPECTED_MODEL)
    app = routes.create_v3_app(settings_factory=lambda: configured(enable_en=True), legacy=incumbent, llm=Broken())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        response = await client.post('/ai/v3/translate/stream', json={
            'text': 'Kiểm tra nhật ký tại 192.0.2.15.', 'source_lang': 'vi', 'target_lang': 'en'})
    assert response.status_code == 502
    assert response.json()['error']['code'] == 'OUTPUT_VALIDATION_FAILED'
    assert 'Wrong output' not in response.text


@pytest.mark.asyncio
async def test_stream_later_failure_has_error_without_success_final(incumbent):
    class LaterFailure:
        calls = 0
        async def generate(self, request):
            self.calls += 1
            if self.calls > 1:
                return GenerationResult('', 'stop', EXPECTED_MODEL)
            return GenerationResult('Inspect security logs for suspicious activity.', 'stop', EXPECTED_MODEL)
    app = routes.create_v3_app(settings_factory=lambda: configured(enable_en=True, chunk_input_tokens=80),
                              legacy=incumbent, llm=LaterFailure())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        response = await client.post('/ai/v3/translate/stream', json={
            'text': 'Kiểm tra nhật ký bảo mật để phát hiện sự cố. ' * 5,
            'source_lang': 'vi', 'target_lang': 'en'})
    assert response.status_code == 200
    assert 'event: chunk' in response.text and 'event: error' in response.text
    assert 'EMPTY_TRANSLATION' in response.text and 'event: final' not in response.text


@pytest.mark.asyncio
async def test_chunk_callback_cancellation_stops_next_model_call():
    class Translator:
        calls = 0
        async def generate(self, request):
            self.calls += 1
            return GenerationResult('Inspect security logs for suspicious activity.', 'stop', EXPECTED_MODEL)
    llm = Translator()
    async def disconnected(chunk):
        raise asyncio.CancelledError
    with pytest.raises(asyncio.CancelledError):
        await translate(TranslationRequest('Kiểm tra nhật ký bảo mật để phát hiện sự cố. ' * 5, 'vi', 'en'),
            llm=llm, settings=configured(enable_en=True, chunk_input_tokens=80), token_count=len,
            on_chunk=disconnected)
    assert llm.calls == 1


@pytest.mark.asyncio
async def test_disconnect_after_first_chunk_releases_admission():
    import anyio

    class Request:
        async def receive(self):
            await asyncio.sleep(3600)

    released = []
    producer_stopped = asyncio.Event()

    async def run(chunk):
        try:
            await chunk({"delta": "Validated first chunk"})
            await asyncio.sleep(3600)
        finally:
            producer_stopped.set()

    response = await routes._validated_stream(
        Request(), run, deadline=asyncio.get_running_loop().time() + 30,
        request_id="disconnect-test", release=lambda: released.append(True))
    # Starlette cancels its streaming task group when the HTTP client disconnects.
    async with anyio.create_task_group() as group:
        async def consume():
            async for frame in response.body_iterator:
                assert frame.startswith("event: chunk")
                group.cancel_scope.cancel()
        group.start_soon(consume)

    assert released == [True]
    assert producer_stopped.is_set()
