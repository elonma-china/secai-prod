import pytest


@pytest.fixture(autouse=True)
def hermetic_env(monkeypatch):
    """Unit tests must not depend on external services. An empty REDIS_URL makes the
    runner fall back to linch's InMemorySessionStore (no live Redis). Production is
    unchanged — it reads REDIS_URL from .env and uses the RedisSessionStore.

    AIRGAP defaults ON in production (config.Settings.airgap) which force-disables every
    internet feature and makes the public-content fetchers refuse. The unit tests that
    exercise those very features (test_web_fetch/test_fetch_reference/test_websearch/
    test_chat_grounding_hook) need them enabled, so the harness runs air-gap OFF by
    default; the dedicated test_airgap.py sets AIRGAP=true explicitly to assert the
    lock-down. Production is unaffected — it never sets this env var, so it keeps the
    secure default.
    """
    monkeypatch.setenv("REDIS_URL", "")
    monkeypatch.setenv("AIRGAP", "false")
