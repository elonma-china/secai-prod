"""One public gateway; feature services retain their auth, stores and workers."""
from __future__ import annotations

import httpx
from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse

from .app import app as legacy_app
from .config import get_settings


HOP_HEADERS = {b'host', b'connection', b'keep-alive', b'transfer-encoding',
               b'te', b'trailer', b'upgrade', b'proxy-authorization', b'proxy-authenticate'}


class FeatureRouter:
    def __init__(self, legacy, *, report='', translation='', client=None):
        self.legacy = legacy
        self.routes = (('/ai/v2/reports', report), ('/ai/v2/translate', translation),
                       ('/ai/v3/translate', translation))
        self.client = client

    async def __call__(self, scope, receive, send):
        if scope['type'] == 'lifespan' and self.client is None:
            async with httpx.AsyncClient(trust_env=False, follow_redirects=False,
                    timeout=httpx.Timeout(600, connect=5),
                    limits=httpx.Limits(max_connections=32, max_keepalive_connections=16)) as client:
                self.client = client
                try:
                    await self.legacy(scope, receive, send)
                finally:
                    self.client = None
            return
        if scope['type'] != 'http':
            return await self.legacy(scope, receive, send)
        path = scope.get('path', '')
        target = next((url for prefix, url in self.routes if url and
                       (path == prefix or path.startswith(prefix + '/'))), None)
        if target is None:
            return await self.legacy(scope, receive, send)
        request = Request(scope, receive)
        client = self.client or httpx.AsyncClient(trust_env=False, follow_redirects=False,
                                                timeout=httpx.Timeout(600, connect=5))
        response = None
        try:
            url = target.rstrip('/') + scope.get('raw_path', path.encode()).decode('ascii')
            if scope.get('query_string'):
                url += '?' + scope['query_string'].decode('ascii')
            headers = [(k, v) for k, v in scope['headers'] if k.lower() not in HOP_HEADERS]
            headers = [(k, v) for k, v in headers if k.lower() != b'accept-encoding']
            headers.append((b'accept-encoding', b'identity'))
            outbound = client.build_request(request.method, url, headers=headers, content=request.stream())
            try:
                response = await client.send(outbound, stream=True)
            except httpx.HTTPError:
                return await JSONResponse({'detail': 'Feature service unavailable'}, status_code=502)(scope, receive, send)
            stream = StreamingResponse(response.aiter_raw(), status_code=response.status_code)
            stream.raw_headers = [(k, v) for k, v in response.headers.raw
                                  if k.lower() not in HOP_HEADERS]
            await stream(scope, receive, send)
        finally:
            if response is not None:
                await response.aclose()
            if client is not self.client:
                await client.aclose()


settings = get_settings()
app = FeatureRouter(legacy_app, report=settings.report_upstream_url,
                    translation=settings.translation_upstream_url)
