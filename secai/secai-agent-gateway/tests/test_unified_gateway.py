import httpx
import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse, StreamingResponse

from secai_gateway.unified_app import FeatureRouter


@pytest.mark.asyncio
async def test_unified_routes_preserve_auth_body_query_and_legacy():
    legacy = FastAPI()
    @legacy.get('/health')
    async def health():
        return {'service': 'legacy'}
    seen = []
    def upstream(request):
        seen.append((str(request.url), request.headers.get('x-api-key'), request.content))
        return httpx.Response(401, stream=httpx.ByteStream(b'{"detail":"Invalid report API key"}'),
                              headers={'Content-Type': 'application/json'})
    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as outbound:
        app = FeatureRouter(legacy, report='http://127.0.0.1:18183', translation='http://127.0.0.1:18185', client=outbound)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            assert (await client.get('/health')).json() == {'service': 'legacy'}
            result = await client.post('/ai/v2/reports/generate?test=1', content=b'{bad-json', headers={'X-API-Key': 'bad'})
            assert result.status_code == 401
            assert seen == [('http://127.0.0.1:18183/ai/v2/reports/generate?test=1', 'bad', b'{bad-json')]
            assert (await client.get('/ai/v2/reports-other')).status_code == 404


@pytest.mark.asyncio
async def test_unified_translation_preserves_stream_and_upload():
    upstream = FastAPI()
    @upstream.post('/ai/v3/translate/stream')
    async def stream():
        async def frames():
            yield b'event: chunk\ndata: {"delta":"Hello"}\n\n'
            yield b'event: final\ndata: {"translated_text":"Hello"}\n\n'
        return StreamingResponse(frames(), media_type='text/event-stream')
    @upstream.post('/ai/v2/translate/files')
    async def upload():
        return JSONResponse({'job_id': 'saved'}, status_code=202)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=upstream)) as outbound:
        app = FeatureRouter(FastAPI(), translation='http://127.0.0.1:18185', client=outbound)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            response = await client.post('/ai/v3/translate/stream', json={'text': 'Hello'})
            assert response.status_code == 200 and 'text/event-stream' in response.headers['content-type']
            assert 'event: chunk' in response.text and 'event: final' in response.text
            response = await client.post('/ai/v2/translate/files', files={'file': ('test.txt', b'Hello')}, data={'target_lang': 'vi'})
            assert response.status_code == 202 and response.json()['job_id'] == 'saved'
