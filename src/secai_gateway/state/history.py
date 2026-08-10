"""Small Redis-compatible conversation-history stores."""
from __future__ import annotations

import json
import time
from typing import Any


class MemoryHistory:
    """Redis-shaped development fallback; values expire after 24 hours."""

    def __init__(self, ttl_seconds: int = 86400) -> None:
        self.ttl_seconds = ttl_seconds
        self._values: dict[str, tuple[float, list[dict[str, Any]]]] = {}

    async def get(self, user_id: str | int) -> list[dict[str, Any]]:
        key = self._key(user_id)
        entry = self._values.get(key)
        if entry is None:
            return []
        expires_at, messages = entry
        if expires_at <= time.time():
            self._values.pop(key, None)
            return []
        return list(messages)

    async def set(self, user_id: str | int, messages: list[dict[str, Any]]) -> None:
        self._values[self._key(user_id)] = (
            time.time() + self.ttl_seconds,
            list(messages or []),
        )

    @staticmethod
    def _key(user_id: str | int) -> str:
        return f"chat:{user_id}"


class RedisHistory:
    """Redis-backed history sharing the legacy ``chat:{user_id}`` list contract."""

    def __init__(self, url: str, ttl_seconds: int = 86400) -> None:
        from redis.asyncio import Redis

        self.ttl_seconds = ttl_seconds
        self._redis = Redis.from_url(url)

    @staticmethod
    def _key(user_id: str | int) -> str:
        return f"chat:{user_id}"

    async def get(self, user_id: str | int) -> list[dict[str, Any]]:
        rows = await self._redis.lrange(self._key(user_id), 0, -1)
        messages: list[dict[str, Any]] = []
        for row in rows:
            if isinstance(row, bytes):
                row = row.decode("utf-8")
            try:
                item = json.loads(row)
            except (TypeError, ValueError):
                continue
            if isinstance(item, dict):
                messages.append(item)
        return messages

    async def set(self, user_id: str | int, messages: list[dict[str, Any]]) -> None:
        key = self._key(user_id)
        encoded = [json.dumps(message, ensure_ascii=False) for message in (messages or [])]
        pipe = self._redis.pipeline(transaction=True)
        pipe.delete(key)
        if encoded:
            pipe.rpush(key, *encoded)
            pipe.expire(key, self.ttl_seconds)
        await pipe.execute()

    async def ping(self) -> bool:
        return bool(await self._redis.ping())
