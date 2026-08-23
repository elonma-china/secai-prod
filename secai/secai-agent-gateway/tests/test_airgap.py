"""Air-gap lock-down: with AIRGAP on, the gateway must make NO outbound internet fetch.

Two enforcement layers are asserted here (see config.Settings._enforce_airgap + airgap.py):
  1. every internet-feature flag is force-coerced OFF, even when the env sets it true;
  2. every public-content fetcher refuses (raises AirgapViolation, a ValueError) or returns
     empty WITHOUT opening a socket.

conftest's hermetic_env sets AIRGAP=false for the rest of the suite (those tests exercise
the internet features); each test here sets AIRGAP=true explicitly.
"""
from __future__ import annotations

import asyncio

import pytest

from secai_gateway.airgap import (
    AirgapViolation,
    airgap_enabled,
    assert_online_fetch_allowed,
)
from secai_gateway.config import get_settings


def _airgap_on(monkeypatch) -> None:
    monkeypatch.setenv("AIRGAP", "true")


def _airgap_off(monkeypatch) -> None:
    monkeypatch.setenv("AIRGAP", "false")


# ── Layer 1: flag coercion ───────────────────────────────────────────────────

def test_airgap_on_coerces_every_internet_flag_off_even_when_env_sets_true(monkeypatch) -> None:
    _airgap_on(monkeypatch)
    # Adversarially set every flag ON — coercion must still win.
    for var in ("WEB_FETCH_ENABLED", "FETCH_REFERENCE_ENABLED", "WEB_SEARCH_ENABLED",
                "GITHUB_ADVISORY_ENABLED", "POC_GITHUB_ONDEMAND_ENABLED"):
        monkeypatch.setenv(var, "true")
    monkeypatch.setenv("SEARXNG_URL", "http://searx.internal:8888")
    s = get_settings()
    assert s.airgap is True
    assert s.web_fetch_enabled is False
    assert s.fetch_reference_enabled is False
    assert s.web_search_enabled is False
    assert s.github_advisory_enabled is False
    assert s.poc_github_ondemand_enabled is False
    assert s.searxng_url == ""


def test_airgap_off_leaves_flags_as_configured(monkeypatch) -> None:
    _airgap_off(monkeypatch)
    monkeypatch.setenv("WEB_FETCH_ENABLED", "true")
    monkeypatch.setenv("POC_GITHUB_ONDEMAND_ENABLED", "true")
    s = get_settings()
    assert s.airgap is False
    assert s.web_fetch_enabled is True
    assert s.poc_github_ondemand_enabled is True


def test_airgap_does_not_touch_internal_service_endpoints(monkeypatch) -> None:
    # air-gap == "no internet", not "no network": the internal service URLs stay intact.
    _airgap_on(monkeypatch)
    monkeypatch.setenv("QDRANT_URL", "http://qdrant:6333")
    monkeypatch.setenv("NEO4J_URI", "bolt://neo4j:7687")
    monkeypatch.setenv("EMBEDDING_API_URL", "http://embed.internal:8080")
    s = get_settings()
    assert s.qdrant_url == "http://qdrant:6333"
    assert s.neo4j_uri == "bolt://neo4j:7687"
    assert s.embedding_api_url == "http://embed.internal:8080"


# ── Layer 2: fetcher backstop ────────────────────────────────────────────────

def test_assert_online_fetch_allowed_raises_when_airgap_on(monkeypatch) -> None:
    _airgap_on(monkeypatch)
    assert airgap_enabled() is True
    with pytest.raises(AirgapViolation):
        assert_online_fetch_allowed("https://github.com/x/y")


def test_assert_online_fetch_allowed_passes_when_airgap_off(monkeypatch) -> None:
    _airgap_off(monkeypatch)
    assert airgap_enabled() is False
    assert_online_fetch_allowed("https://github.com/x/y")  # no raise


def test_airgap_violation_is_a_valueerror(monkeypatch) -> None:
    # Tool wrappers catch ValueError → an air-gap refusal degrades like any policy denial,
    # not a crash.
    _airgap_on(monkeypatch)
    with pytest.raises(ValueError):
        assert_online_fetch_allowed("https://example.com")


def test_web_fetch_markdown_refuses_under_airgap(monkeypatch) -> None:
    _airgap_on(monkeypatch)
    from secai_gateway.retrieval.http_fetch import fetch_url_markdown
    with pytest.raises(AirgapViolation):
        asyncio.run(fetch_url_markdown("https://owasp.org/Top10/"))


def test_fetch_reference_markdown_refuses_under_airgap(monkeypatch) -> None:
    _airgap_on(monkeypatch)
    from secai_gateway.retrieval.reference_fetch import fetch_reference_markdown
    with pytest.raises(AirgapViolation):
        asyncio.run(fetch_reference_markdown("https://github.com/advisories/GHSA-xxxx"))


def test_web_fetch_tool_returns_clean_error_not_crash_under_airgap(monkeypatch) -> None:
    # The model-facing tool must surface an is_error ToolResult, never propagate.
    _airgap_on(monkeypatch)
    from secai_gateway.retrieval.http_fetch import _web_fetch
    result = asyncio.run(_web_fetch("https://nvd.nist.gov/vuln/detail/CVE-2021-44228"))
    assert result.is_error is True


def test_searxng_search_returns_empty_under_airgap(monkeypatch) -> None:
    _airgap_on(monkeypatch)
    from secai_gateway.retrieval.websearch import searxng_search
    assert asyncio.run(searxng_search("CVE-2021-44228 poc")) == []


def test_find_ghsa_url_returns_none_under_airgap(monkeypatch) -> None:
    _airgap_on(monkeypatch)
    from secai_gateway.retrieval.websearch import _find_ghsa_url
    assert asyncio.run(_find_ghsa_url("CVE-2021-44228")) is None


def test_github_advisory_evidence_returns_empty_under_airgap(monkeypatch) -> None:
    _airgap_on(monkeypatch)
    from secai_gateway.retrieval.websearch import _github_advisory_evidence
    assert asyncio.run(_github_advisory_evidence("CVE-2021-44228")) == []


def test_gather_web_hints_returns_empty_under_airgap(monkeypatch, tmp_path) -> None:
    _airgap_on(monkeypatch)
    from secai_gateway.retrieval.websearch import gather_web_hints
    from secai_gateway.state import SqliteLedger
    ledger = SqliteLedger(tmp_path / "ledger.db")
    assert asyncio.run(gather_web_hints("CVE-2021-44228", ledger, "run-airgap")) == []


def test_fetch_repo_code_returns_none_under_airgap(monkeypatch) -> None:
    _airgap_on(monkeypatch)
    from secai_gateway.poc.fetch_github import fetch_repo_code
    assert fetch_repo_code("owner/repo", "https://github.com/owner/repo") is None


def test_tools_registry_excludes_internet_tools_under_airgap(monkeypatch, tmp_path) -> None:
    # End-to-end of layer 1: the runner must never even build web_fetch/fetch_reference/
    # web_search when air-gap is on, regardless of their individual enable flags.
    _airgap_on(monkeypatch)
    for var in ("WEB_FETCH_ENABLED", "FETCH_REFERENCE_ENABLED", "WEB_SEARCH_ENABLED"):
        monkeypatch.setenv(var, "true")
    monkeypatch.setenv("SEARXNG_URL", "http://searx.internal:8888")
    from secai_gateway.agent.runner import LinchRunner
    runner = LinchRunner(tmp_path / "gw.db")
    names = set(runner.tools.keys())
    assert "web_fetch" not in names
    assert "fetch_reference" not in names
    assert "web_search" not in names
    # KB tools (internal) must still be present.
    assert "qdrant_search" in names
    assert "neo4j_search" in names
