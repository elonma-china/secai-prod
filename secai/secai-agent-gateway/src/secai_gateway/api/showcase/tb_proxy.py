"""Reverse-proxy TensorBoard qua chính gateway (:8131) — để khách xem TB same-origin, KHÔNG cần
mở thêm cổng/router/Cloudflare. FE đã gọi được :8131 nên tự trỏ tới TB qua /showcase-tb/ luôn.

TB chạy với ``--path_prefix=/showcase-tb`` (asset của nó nằm dưới /showcase-tb). Gateway nhận
/showcase-tb/* rồi forward tới TB nội bộ (Settings.showcase_tensorboard_upstream). Cùng tiền tố
``/showcase`` với trang + API nên FE chỉ cần proxy một dòng. Chỉ đọc/hiển thị — không sửa gì.
"""

from __future__ import annotations

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import Response

from ...config import get_settings

router = APIRouter()

# Header hop-by-hop + header do httpx tự xử (content-encoding/length) — không chuyển tiếp nguyên trạng.
_DROP = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailers",
    "transfer-encoding", "upgrade", "content-encoding", "content-length", "host",
}


def _upstream() -> str:
    return get_settings().showcase_tensorboard_upstream.rstrip("/")


async def _forward(request: Request, path: str) -> Response:
    target = f"{_upstream()}/showcase-tb/{path}"
    async with httpx.AsyncClient(timeout=60.0, follow_redirects=False) as client:
        upstream = client.build_request(
            request.method, target,
            params=request.query_params,
            headers={k: v for k, v in request.headers.items() if k.lower() not in _DROP},
            content=await request.body(),
        )
        r = await client.send(upstream)
    headers = {k: v for k, v in r.headers.items() if k.lower() not in _DROP}
    return Response(content=r.content, status_code=r.status_code, headers=headers,
                    media_type=r.headers.get("content-type"))


@router.api_route("/showcase-tb", methods=["GET"])
async def tb_root(request: Request) -> Response:
    return await _forward(request, "")


@router.api_route("/showcase-tb/{path:path}", methods=["GET", "POST"])
async def tb_path(request: Request, path: str) -> Response:
    return await _forward(request, path)
