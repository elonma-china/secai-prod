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
    monkeypatch.setenv("POC_EXPLOIT_SIMULATE_DELAY_S", "0")
    # The default session store backend is now `sqlite` (durable, config.Settings default),
    # which would otherwise write a real ./data/sessions.db during a test run (or a
    # tmp_path one via test_airgap.py's LinchRunner) — unit tests must stay hermetic and
    # not touch disk-persisted conversation state. Force in-memory here; production is
    # unaffected since it never sets this env var and keeps the durable sqlite default.
    monkeypatch.setenv("SESSION_STORE_BACKEND", "memory")
    # The chat input-sanitization classifier embeds via the sec-embedding endpoint. Since
    # .env now points EMBEDDING_MODEL at the shipped head's model, an un-mocked /ai/chat in a
    # unit test would make a real network embed call. Point the head at a nonexistent dir so
    # load_head() returns None → the classifier fails open (no network) for tests that don't
    # mock it. The dedicated test_input_classifier suite monkeypatches classify_input/load_head
    # directly, so it is unaffected. Production is unchanged (it never sets this var).
    monkeypatch.setenv("INPUT_CLASSIFIER_WEIGHTS_PATH", "/nonexistent/hermetic-sanitize-head")
