from __future__ import annotations

import time


class FixedWindowRateLimiter:
    def __init__(self) -> None:
        self._buckets: dict[tuple[str, str], list[float]] = {}

    def check(self, client: str, route: str, limit: int) -> None:
        now = time.monotonic()
        key = (client, route)
        bucket = [stamp for stamp in self._buckets.get(key, []) if stamp > now - 60]
        if len(bucket) >= limit:
            raise RuntimeError("rate limit exceeded")
        bucket.append(now)
        self._buckets[key] = bucket
