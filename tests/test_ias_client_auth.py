"""IasClient gửi Basic auth tới engine khi có IAS_SERVER_PASSWORD; rỗng thì không gửi gì (prod không đổi).
Engine (packages/opencode/src/server/auth.ts) mặc định user là `ias` (OPENCODE_SERVER_USERNAME)."""
import base64

import httpx
import pytest

from secai_gateway.poc import exploit_v2, ias_client
from secai_gateway.poc.ias_client import IasClient

_orig = httpx.AsyncClient
_BASIC = "Basic " + base64.b64encode(b"ias:s3cret").decode()


def _mock_transport(monkeypatch, handler):
    # IasClient tự tạo AsyncClient: chèn MockTransport, giữ nguyên mọi tham số nó truyền (kể cả auth).
    monkeypatch.setattr(ias_client.httpx, "AsyncClient",
                        lambda **kw: _orig(transport=httpx.MockTransport(handler), **kw))


async def _auth_header_sent(monkeypatch, password):
    seen = {}

    def handler(req):
        seen["auth"] = req.headers.get("authorization")
        return httpx.Response(200, json={"id": "s1"})

    _mock_transport(monkeypatch, handler)
    await IasClient("http://vm-a:5101", password=password).create_session()
    return seen["auth"]


async def test_password_sent_as_basic_auth(monkeypatch):
    assert await _auth_header_sent(monkeypatch, "s3cret") == _BASIC


async def test_empty_password_sends_no_auth(monkeypatch):
    assert await _auth_header_sent(monkeypatch, "") is None


async def test_default_sends_no_auth(monkeypatch):
    seen = []
    _mock_transport(monkeypatch, lambda req: seen.append(req.headers.get("authorization")) or httpx.Response(200, json={"id": "s"}))
    await IasClient("http://vm-a:5101").create_session()
    assert seen == [None]


async def test_every_call_carries_auth(monkeypatch):
    """abort / reply / events / list_messages đều qua cùng _http() nên cùng auth."""
    seen = []

    def handler(req):
        seen.append(req.headers.get("authorization"))
        if req.url.path == "/event":
            return httpx.Response(200, text='data: {"type":"x"}\n\n')
        return httpx.Response(200, json=[])

    _mock_transport(monkeypatch, handler)
    c = IasClient("http://vm-a:5101", password="s3cret")
    await c.abort_session("s1")
    await c.reply_question("q1", [["a"]])
    await c.list_messages("s1")
    assert [e async for e in c.events()] == [{"type": "x"}]
    assert seen == [_BASIC] * 4


async def test_reply_exploit_v2_question_passes_password(monkeypatch):
    seen = []
    _mock_transport(monkeypatch, lambda req: seen.append(req.headers.get("authorization")) or httpx.Response(200, json={}))
    await exploit_v2.reply_exploit_v2_question("http://vm-a:5101", "q1", "yes", password="s3cret")
    assert seen == [_BASIC]


def test_setting_defaults_empty(monkeypatch):
    from secai_gateway.config import Settings
    monkeypatch.delenv("IAS_SERVER_PASSWORD", raising=False)
    assert Settings().ias_server_password == ""
    monkeypatch.setenv("IAS_SERVER_PASSWORD", "x")
    assert Settings().ias_server_password == "x"
