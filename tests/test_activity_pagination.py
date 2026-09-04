"""Pagination on /logs/api/search — page maths and the Elasticsearch window clamp."""
from __future__ import annotations

import httpx
import pytest

from secai_gateway.activity import auth
import secai_gateway.app as app_module


class _FakeSink:
    """Answers _search with a synthetic page, and records the body it was asked for."""

    enabled = True

    def __init__(self, total: int = 1234) -> None:
        self.total = total
        self.bodies: list[dict] = []

    async def emit(self, record) -> None:  # pragma: no cover — unused here
        pass

    def stats(self) -> dict:
        return {"enabled": True}

    async def search(self, body: dict) -> dict:
        self.bodies.append(body)
        size = body.get("size", 50)
        rows = [{"_id": f"d{i}", "sort": [1000 + i, f"r{i}"],
                 "_source": {"function": "chat", "question": f"q{i}", "answer": f"a{i}"}}
                for i in range(size)]
        return {"hits": {"total": {"value": self.total}, "hits": rows}}


@pytest.fixture()
def client_and_sink(monkeypatch):
    holder: dict = {}
    original = app_module.GatewayState

    class Patched(original):  # type: ignore[misc, valid-type]
        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            self.settings.logs_ui_password = "pw"
            self.settings.logs_ui_username = "admin"
            self.settings.logs_ui_session_secret = "test-secret"
            self.logsink = _FakeSink()
            holder["sink"] = self.logsink
            holder["settings"] = self.settings

    monkeypatch.setattr(app_module, "GatewayState", Patched)
    app = app_module.create_app(runner=object())
    cookie, _ = auth.issue_cookie(holder["settings"], "admin")
    transport = httpx.ASGITransport(app=app)
    return transport, holder, cookie


async def _get(transport, cookie, url):
    async with httpx.AsyncClient(transport=transport, base_url="http://test",
                                 cookies={auth.COOKIE_NAME: cookie}) as c:
        return await c.get(url)


@pytest.mark.asyncio
async def test_first_page_has_no_offset(client_and_sink) -> None:
    transport, holder, cookie = client_and_sink
    r = await _get(transport, cookie, "/logs/api/search?page=1&size=50")
    body = r.json()
    assert body["page"] == 1 and body["size"] == 50
    assert body["total"] == 1234
    assert body["pages"] == 25          # ceil(1234 / 50)
    assert "from" not in holder["sink"].bodies[-1]


@pytest.mark.asyncio
async def test_page_three_offsets_by_two_pages(client_and_sink) -> None:
    transport, holder, cookie = client_and_sink
    await _get(transport, cookie, "/logs/api/search?page=3&size=50")
    assert holder["sink"].bodies[-1]["from"] == 100


@pytest.mark.asyncio
async def test_page_is_clamped_to_the_elasticsearch_window(client_and_sink) -> None:
    """from+size past max_result_window is a hard ES error; the pager must clamp and say so
    rather than surfacing a 500."""
    transport, holder, cookie = client_and_sink
    holder["sink"].total = 500_000
    r = await _get(transport, cookie, "/logs/api/search?page=9999&size=50")
    body = r.json()
    assert body["capped"] is True
    assert holder["sink"].bodies[-1]["from"] + body["size"] <= 10_000


@pytest.mark.asyncio
async def test_size_is_bounded(client_and_sink) -> None:
    transport, holder, cookie = client_and_sink
    assert (await _get(transport, cookie, "/logs/api/search?size=99999")).json()["size"] == 200
    assert (await _get(transport, cookie, "/logs/api/search?size=1")).json()["size"] == 10


@pytest.mark.asyncio
async def test_rows_carry_the_answer_for_the_response_column(client_and_sink) -> None:
    transport, holder, cookie = client_and_sink
    rows = (await _get(transport, cookie, "/logs/api/search?size=10")).json()["rows"]
    assert rows and all("answer" in r and "question" in r for r in rows)


@pytest.mark.asyncio
async def test_search_requires_a_session(client_and_sink) -> None:
    transport, _holder, _cookie = client_and_sink
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        assert (await c.get("/logs/api/search")).status_code == 401
