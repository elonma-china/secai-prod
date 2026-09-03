import pytest


@pytest.fixture(autouse=True)
def hermetic_env(monkeypatch):
    """Unit tests must not depend on external services. An empty REDIS_URL makes the
    runner fall back to linch's InMemorySessionStore (no live Redis). Production is
    unchanged — it reads REDIS_URL from .env and uses the RedisSessionStore.
    """
    monkeypatch.setenv("REDIS_URL", "")
