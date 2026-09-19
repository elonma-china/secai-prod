"""Reverse-proxy service kích hoạt MLOps (trigger_api, chạy trên HOST :8877) qua chính gateway
(:8131) — để trang nghiệm thu (/showcase-demo) gọi same-origin `/showcase-api/retraining/*`,
KHÔNG cần mở thêm cổng ra ngoài, KHÔNG cần CORS.

Vì sao proxy chứ không nhúng thẳng vào gateway: pipeline retraining phải chạy trên HOST (venv/
GPU/vast CLI/HF token ~/.config), còn gateway chạy trong container Docker — không chạy pipeline
được. trigger_api là 1 service riêng trên host (mlops/trigger_api/app.py); gateway chỉ chuyển
tiếp. Mutation (POST /trigger, /smoke) vẫn bị trigger_api chặn bằng X-API-Key (header được
forward nguyên trạng) — gateway KHÔNG tự cấp quyền.

Streaming: /runs/{id}/stream là SSE vô hạn — proxy phải stream, không buffer cả response (khác
tb_proxy chỉ buffer). Dùng httpx.stream để đẩy từng chunk.
"""
from __future__ import annotations

import base64

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import Response, StreamingResponse

from ...config import get_settings

router = APIRouter()

_DROP = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailers",
    "transfer-encoding", "upgrade", "content-encoding", "content-length", "host",
}


def _upstream() -> str:
    return get_settings().showcase_retraining_upstream.rstrip("/")


def _fwd_headers(request: Request) -> dict:
    headers = {k: v for k, v in request.headers.items() if k.lower() not in _DROP}
    # Nếu trigger_api bật Basic Auth (lớp ngoài), proxy tự thêm để đường same-origin của trang
    # nghiệm thu qua được lớp đó — X-API-Key (lớp trong, cho mutation) vẫn do client gửi như cũ.
    creds = get_settings().showcase_retraining_basic_auth
    if creds:
        headers["authorization"] = "Basic " + base64.b64encode(creds.encode()).decode()
    return headers


@router.api_route("/showcase-api/retraining/{path:path}",
                  methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def proxy(request: Request, path: str) -> Response:
    target = f"{_upstream()}/{path}"
    body = await request.body()

    # SSE stream: giữ kết nối mở, đẩy từng chunk — không đọc hết r.content (stream vô hạn).
    if path.endswith("/stream"):
        client = httpx.AsyncClient(timeout=None)
        req = client.build_request("GET", target, params=request.query_params,
                                   headers=_fwd_headers(request))
        r = await client.send(req, stream=True)
        out_headers = {k: v for k, v in r.headers.items() if k.lower() not in _DROP}

        async def body_iter():
            try:
                async for chunk in r.aiter_raw():
                    yield chunk
            finally:
                await r.aclose()
                await client.aclose()

        return StreamingResponse(body_iter(), status_code=r.status_code,
                                 headers=out_headers,
                                 media_type=r.headers.get("content-type", "text/event-stream"))

    async with httpx.AsyncClient(timeout=120.0, follow_redirects=False) as client:
        upstream = client.build_request(request.method, target, params=request.query_params,
                                        headers=_fwd_headers(request), content=body)
        r = await client.send(upstream)
    headers = {k: v for k, v in r.headers.items() if k.lower() not in _DROP}
    return Response(content=r.content, status_code=r.status_code, headers=headers,
                    media_type=r.headers.get("content-type"))
