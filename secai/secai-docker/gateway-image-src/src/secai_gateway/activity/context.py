"""Per-request logging context: captures the HTTP envelope at the start of a handler and
turns it into a finished ``LogRecord`` when the answer is known.

Why the context is threaded explicitly (handler → ``LogContext`` → ``stream(log=...)``)
instead of living in a ``ContextVar``: Starlette's ``BaseHTTPMiddleware`` (which
``@app.middleware("http")`` builds on) runs the downstream app in a child task, so a
ContextVar set in middleware is not reliably visible inside a StreamingResponse generator —
exactly where the streaming endpoints finish their work.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from .schema import LogRecord


def client_ip(request: Any, *, trust_proxy: bool = True) -> tuple[str, str]:
    """Return ``(ip, raw_forwarded_for)``.

    ``request.client.host`` is the socket peer, which behind a reverse proxy is the proxy.
    The first hop of ``X-Forwarded-For`` is the original client — trusted only when
    ``trust_proxy`` is on, since a direct caller can forge the header freely.
    """
    forwarded = ""
    try:
        forwarded = request.headers.get("x-forwarded-for", "") or ""
    except Exception:  # pragma: no cover — defensive, headers should always exist
        forwarded = ""
    peer = ""
    try:
        peer = request.client.host if request.client else ""
    except Exception:  # pragma: no cover
        peer = ""
    if trust_proxy and forwarded:
        first = forwarded.split(",")[0].strip()
        if first:
            return first, forwarded
    return peer, forwarded


@dataclass(slots=True)
class LogContext:
    """Mutable per-request scratch space. ``finish()`` freezes it into a ``LogRecord``."""

    function: str
    endpoint: str
    http_method: str = "POST"
    stream: bool = False
    request_id: str = field(default_factory=lambda: uuid4().hex)
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    _t0: float = field(default_factory=time.monotonic)

    client_ip: str = ""
    client_forwarded_for: str = ""
    user_agent: str = ""
    user_id: str = ""
    conversation_id: str = ""
    question: str = ""
    profile: str = ""

    # Set to True by whoever emits, so a handler that emits on both the success and the
    # error path can never write the same request twice.
    emitted: bool = False

    @classmethod
    def from_request(cls, request: Any, *, function: str, stream: bool = False,
                     trust_proxy: bool = True, **kw: Any) -> "LogContext":
        ip, forwarded = client_ip(request, trust_proxy=trust_proxy)
        try:
            endpoint = request.url.path
            method = request.method
            ua = request.headers.get("user-agent", "") or ""
        except Exception:  # pragma: no cover — never let logging break a request
            endpoint, method, ua = "", "POST", ""
        return cls(function=function, endpoint=endpoint, http_method=method, stream=stream,
                   client_ip=ip, client_forwarded_for=forwarded, user_agent=ua, **kw)

    def finish(self, *, answer: str = "", run_id: str | None = None, profile: str = "",
               http_status: int = 200, error: bool = False, error_detail: str = "",
               blocked: bool = False, block_reason: str = "",
               tools_called: list[str] | None = None) -> LogRecord:
        return LogRecord(
            request_id=self.request_id,
            function=self.function,
            endpoint=self.endpoint,
            http_method=self.http_method,
            profile=profile or self.profile,
            stream=self.stream,
            client_ip=self.client_ip,
            client_forwarded_for=self.client_forwarded_for,
            user_agent=self.user_agent,
            user_id=self.user_id,
            conversation_id=self.conversation_id,
            run_id=run_id or "",
            question=self.question,
            answer=answer,
            duration_ms=(time.monotonic() - self._t0) * 1000.0,
            http_status=http_status,
            error=error,
            error_detail=error_detail,
            blocked=blocked,
            block_reason=block_reason,
            tools_called=tools_called or [],
            started_at=self.started_at,
        )
