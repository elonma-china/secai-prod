"""Regression tests for RedisSessionStore (Bug #1: chatbot 'session not found').

The store previously raised ``KeyError('session not found: ...')`` on the first
write to a session id, which — via the chat streaming path — surfaced as an empty
assistant bubble for every message. Writes must now get-or-create the record.

Uses an in-process fake of ``redis.asyncio.Redis`` so no live Redis is needed.
"""
from __future__ import annotations

import pytest

from linch.types import Message, TextBlock

from secai_gateway.state.session_store import RedisSessionStore


class _FakeRedis:
    """Minimal async subset of redis.asyncio.Redis used by RedisSessionStore."""

    def __init__(self) -> None:
        self.kv: dict[str, str] = {}
        self.lists: dict[str, list[str]] = {}

    async def get(self, k):  # noqa: ANN001
        return self.kv.get(k)

    async def set(self, k, v, ex=None):  # noqa: ANN001
        self.kv[k] = v

    async def lrange(self, k, a, b):  # noqa: ANN001
        arr = self.lists.get(k, [])
        return arr[a:] if b == -1 else arr[a : b + 1]

    async def llen(self, k):  # noqa: ANN001
        return len(self.lists.get(k, []))

    async def rpush(self, k, *vals):  # noqa: ANN001
        self.lists.setdefault(k, []).extend(vals)

    async def expire(self, k, ttl):  # noqa: ANN001
        return None

    async def delete(self, *ks):  # noqa: ANN001
        for k in ks:
            self.kv.pop(k, None)
            self.lists.pop(k, None)


@pytest.fixture()
def store(monkeypatch) -> RedisSessionStore:
    import redis.asyncio as raio

    shared = _FakeRedis()
    monkeypatch.setattr(raio.Redis, "from_url", classmethod(lambda cls, url, decode_responses=True: shared))
    return RedisSessionStore("redis://fake")


def _msg(role: str, text: str) -> Message:
    return Message(role=role, content=[TextBlock(text=text)])


@pytest.mark.asyncio
async def test_append_without_create_gets_or_creates(store: RedisSessionStore) -> None:
    # Live-repro id shape: chat:<sha256(user_id)>. Before the fix this raised KeyError.
    sid = "chat:f6d80f8fd034"
    stored = await store.append_messages(sid, [_msg("user", "Xin chào")])
    assert [s.seq for s in stored] == [1]
    # Record is now persisted and a second turn continues the same conversation.
    again = await store.append_messages(sid, [_msg("assistant", "Chào bạn")])
    assert [s.seq for s in again] == [2]
    assert len(await store.load_messages(sid)) == 2


@pytest.mark.asyncio
async def test_update_meta_and_skills_autocreate(store: RedisSessionStore) -> None:
    rec = await store.update_meta("chat:new1", {"profile": "chat"})
    assert rec.meta["profile"] == "chat"
    await store.set_invoked_skills("chat:new2", [{"name": "x", "substituted_body": ""}])
    loaded = await store.load("chat:new2")
    assert loaded is not None and loaded.invoked_skills


class _TtlRecordingRedis(_FakeRedis):
    """Records the ex/ttl passed to each key so the TTL parametrization can be asserted."""

    def __init__(self) -> None:
        super().__init__()
        self.ttl_by_key: dict[str, int] = {}

    async def set(self, k, v, ex=None):  # noqa: ANN001
        self.ttl_by_key[k] = ex
        await super().set(k, v)

    async def expire(self, k, ttl):  # noqa: ANN001
        self.ttl_by_key[k] = ttl


def _recording_store(monkeypatch, **kwargs) -> tuple[RedisSessionStore, _TtlRecordingRedis]:
    import redis.asyncio as raio

    rec = _TtlRecordingRedis()
    monkeypatch.setattr(raio.Redis, "from_url",
                        classmethod(lambda cls, url, decode_responses=True: rec))
    return RedisSessionStore("redis://fake", **kwargs), rec


@pytest.mark.asyncio
async def test_ttl_parametrized(monkeypatch) -> None:
    store, rec = _recording_store(monkeypatch, ttl_seconds=1234)
    await store.append_messages("chat:conv:h:cid", [_msg("user", "hi")])
    assert rec.ttl_by_key["linch:sess:chat:conv:h:cid"] == 1234       # record
    assert rec.ttl_by_key["linch:sess:chat:conv:h:cid:msgs"] == 1234  # messages list


@pytest.mark.asyncio
async def test_ttl_defaults_to_legacy_24h(monkeypatch) -> None:
    store, rec = _recording_store(monkeypatch)   # no ttl_seconds → legacy default
    await store.update_meta("chat:abc", {"profile": "chat"})
    assert rec.ttl_by_key["linch:sess:chat:abc"] == 86400
