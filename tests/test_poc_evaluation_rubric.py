"""Hermetic API checks for the exploit KB grounding rubric.

These tests exercise the HTTP contract with a scripted runner and an isolated KB result.
They do not establish live retrieval quality or prove that synthesized code works.
"""
from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from linch.events import PartialAssistantEvent, ResultEvent, StopReason
from linch.types import Usage

from secai_gateway.agent.hooks import RequireKbSearchBeforeSynthesisHook
from secai_gateway.app import create_app
from secai_gateway.poc.prefetch import session_coverage_scope
from secai_gateway.retrieval import kb_context

CVE = "CVE-2018-19983"
KB_MARKER = "KB_TECHNICAL_MARKER nonce is generated from the current timestamp"
DRAFT_MARKER = "FABRICATED_MODEL_DRAFT_MARKER"


class CaptureRunner:
    """Capture exactly what the model boundary receives; never echo hidden context."""

    def __init__(self, *, answer: str | None = None):
        self.answer = answer or (
            "## 1. Mã Khai thác (PoC) và Payload\n"
            f"```python\nprint('{DRAFT_MARKER}')\n```\n"
            "## 2. Hướng dẫn chạy\nChưa chạy xác minh."
        )
        self.calls: list[dict] = []

    async def complete(self, profile_name, prompt, session_id=None, **kwargs):
        self.calls.append({"kind": "complete", "profile": profile_name, "prompt": prompt,
                           "session_id": session_id,
                           "grounding_context": kwargs.get("grounding_context")})
        return self.answer, {"run_id": "rubric-run", "profile": profile_name}

    async def stream(self, profile_name, prompt, session_id=None, **kwargs):
        self.calls.append({"kind": "stream", "profile": profile_name, "prompt": prompt,
                           "session_id": session_id,
                           "grounding_context": kwargs.get("grounding_context")})
        yield PartialAssistantEvent(delta={"kind": "text", "text": self.answer})
        yield ResultEvent(subtype="success", stop_reason="end_turn", total_usage=Usage(),
                          duration_ms=1, final_text=self.answer)


@pytest.fixture(autouse=True)
def no_external_exploit_dependencies(monkeypatch, tmp_path):
    """Keep route tests offline and make every persistent path test-owned."""
    monkeypatch.setenv("POC_CATALOG_DB", str(tmp_path / "catalog.db"))
    monkeypatch.setenv("POC_EXPLOIT_SIMULATE_DELAY_S", "0")
    monkeypatch.setenv("POC_EXPLOIT_ENABLED", "false")
    monkeypatch.setenv("POC_GITHUB_ONDEMAND_ENABLED", "false")
    monkeypatch.setenv("WEB_SEARCH_ENABLED", "false")
    monkeypatch.setenv("POC_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("POC_FIXED_DIR", str(tmp_path / "fixed"))

    async def legacy_lookup_must_not_authorize(_cve):
        raise AssertionError("legacy existence lookup must not authorize KB synthesis")

    async def legacy_cwe_must_not_run(_cve):
        raise AssertionError("legacy CWE lookup should not run in these isolated API tests")

    monkeypatch.setattr("secai_gateway.retrieval.neo4j.cve_exists_in_kb", legacy_lookup_must_not_authorize)
    monkeypatch.setattr("secai_gateway.retrieval.neo4j.cve_cwe_refs", legacy_cwe_must_not_run)
    monkeypatch.setattr("secai_gateway.api.routes._run_kt_if_applicable", _no_kt)
    monkeypatch.setattr("secai_gateway.api.routes.gather_web_hints", _no_web)
    monkeypatch.setattr("secai_gateway.poc.ondemand_cache.load_cached", lambda *_a, **_kw: None)
    monkeypatch.setattr("secai_gateway.poc.fixed_pocs.load_fixed", lambda *_a, **_kw: None)


async def _no_kt(*_args, **_kwargs):
    return None


async def _no_web(*_args, **_kwargs):
    return []


def install_kb(monkeypatch, status: str, *, content: str = "", cwe_refs=None):
    calls: list[str] = []

    async def get_context(cve_id):
        calls.append(cve_id)
        return {"status": status, "content": content, "cwe_refs": list(cwe_refs or [])}

    monkeypatch.setattr(kb_context, "get_cve_context", get_context)
    return calls


async def request(path: str, runner: CaptureRunner, payload: dict):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(runner)),
                                 base_url="http://test") as client:
        return await client.post(path, json=payload)


def sse_events(body: str) -> list[tuple[str, dict]]:
    events = []
    for frame in body.split("\n\n"):
        name = next((line[7:] for line in frame.splitlines() if line.startswith("event: ")), None)
        data_line = next((line[6:] for line in frame.splitlines() if line.startswith("data: ")), None)
        if name and data_line and data_line != "[DONE]":
            events.append((name, json.loads(data_line)))
    return events


def test_rubric_found_kb_is_passed_out_of_band_and_raw_prompt_stays_clean(monkeypatch):
    kb_calls = install_kb(monkeypatch, "found", content=f"{CVE}\n{KB_MARKER}\nCWE-330",
                          cwe_refs=["CWE-330"])
    runner = CaptureRunner()
    response = asyncio.run(request("/ai/exploit", runner,
                                   {"user_id": "luna", "question": f"Analyze {CVE}"}))

    assert response.status_code == 200
    result = response.json()
    assert result["description"] == result["exploit_guide"]
    assert result["metadata"]["kb_status"] == "found"
    assert result["metadata"]["cwe_refs"] == ["CWE-330"]
    assert kb_calls == [CVE]
    assert len(runner.calls) == 1
    call = runner.calls[0]
    assert call["prompt"] == f"Analyze {CVE}"
    assert KB_MARKER not in call["prompt"]
    assert KB_MARKER in call["grounding_context"]
    assert "<untrusted_retrieved_content" in call["grounding_context"]
    assert result["metadata"].get("candidate_status") != "verified"


@pytest.mark.parametrize("status", ["not_found", "error", "insufficient"])
def test_rubric_unusable_kb_fails_closed_without_model_call(monkeypatch, status):
    kb_calls = install_kb(monkeypatch, status)
    runner = CaptureRunner()
    response = asyncio.run(request("/ai/exploit", runner,
                                   {"user_id": "luna", "question": f"Analyze {CVE}"}))

    assert response.status_code == 200
    result = response.json()
    assert result["description"] == result["exploit_guide"]
    assert runner.calls == [], "unusable evidence must be gated before generation"
    assert kb_calls == [CVE]
    assert result["metadata"]["candidate_status"] == "not_generated"
    assert result["metadata"].get("reference_code_injected") is False
    assert DRAFT_MARKER not in result["description"]


@pytest.mark.parametrize("status", ["not_found", "error", "insufficient"])
def test_rubric_unusable_kb_sse_has_terminal_final_and_never_starts_model(monkeypatch, status):
    kb_calls = install_kb(monkeypatch, status)
    runner = CaptureRunner()
    response = asyncio.run(request("/ai/exploit/stream", runner,
                                   {"user_id": "luna", "question": f"Analyze {CVE}"}))

    assert response.status_code == 200
    body = response.text
    assert "event: final" in body
    assert body.count("event: final") == 1
    assert "data: [DONE]" in body
    assert runner.calls == [], "SSE must not start generation before refusal"
    assert kb_calls == [CVE]
    assert DRAFT_MARKER not in body
    finals = [payload for name, payload in sse_events(body) if name == "final"]
    assert len(finals) == 1
    final = finals[0]
    assert final["content"] == final["description"] == final["exploit_guide"]
    assert final["metadata"]["candidate_status"] == "not_generated"
    assert final["metadata"].get("reference_code_injected") is False


def test_rubric_legacy_cve_exists_flag_cannot_upgrade_insufficient_context(monkeypatch):
    install_kb(monkeypatch, "insufficient")
    legacy_calls = []
    async def legacy_exists(cve_id):
        legacy_calls.append(cve_id)
        return True
    monkeypatch.setattr("secai_gateway.retrieval.neo4j.cve_exists_in_kb", legacy_exists)
    runner = CaptureRunner()
    response = asyncio.run(request("/ai/exploit", runner,
                                   {"user_id": "luna", "question": f"Analyze {CVE}"}))
    assert response.status_code == 200
    assert legacy_calls == [], "bare record existence must not replace exact technical-context status"
    assert runner.calls == []
    assert response.json()["metadata"]["candidate_status"] == "not_generated"


@pytest.mark.parametrize("path", ["/ai/exploit", "/ai/exploit/stream"])
def test_rubric_kb_lookup_exception_becomes_error_and_stays_fail_closed(monkeypatch, path):
    calls = []
    async def unavailable(_cve):
        calls.append(_cve)
        raise TimeoutError("private KB endpoint detail")
    monkeypatch.setattr(kb_context, "get_cve_context", unavailable)
    runner = CaptureRunner()
    response = asyncio.run(request(path, runner, {"user_id": "luna", "question": f"Analyze {CVE}"}))
    assert response.status_code == 200
    assert calls == [CVE]
    assert runner.calls == []
    serialized = response.text if path.endswith("/stream") else response.text
    assert "private KB endpoint detail" not in serialized
    if path.endswith("/stream"):
        assert response.text.count("event: final") == 1
        assert "data: [DONE]" in response.text
    else:
        assert response.json()["metadata"]["candidate_status"] == "not_generated"


class _Session:
    def __init__(self, id_value: str):
        self.id = id_value
        self.meta = {"profile": "exploit_research"}


def test_rubric_hook_exhaustion_mutates_fabricated_draft_fail_closed():
    from linch.hooks.contexts import BeforeFinalAnswerContext

    async def exercise():
        with session_coverage_scope():
            hook = RequireKbSearchBeforeSynthesisHook(
                tools=("qdrant_search", "neo4j_search"),
                profile="exploit_research", max_retries=2,
            )
            context = BeforeFinalAnswerContext(
                session=_Session("rubric-hook"), run_id="rubric-hook-run", turn_index=1,
                final_text=DRAFT_MARKER,
            )
            first = await hook.on_before_final_answer(context)
            second = await hook.on_before_final_answer(context)
            terminal = await hook.on_before_final_answer(context)
            repeated = await hook.on_before_final_answer(context)
            return first, second, terminal, repeated

    first, second, terminal, repeated = asyncio.run(exercise())
    assert first.action == second.action == "force_continue"
    assert terminal.action == repeated.action == "mutate"
    assert terminal.reason == "kb_search_before_synthesis_exhausted"
    assert DRAFT_MARKER not in terminal.final_text
    assert "Không thể xác thực thông tin lỗ hổng từ cơ sở dữ liệu nội bộ (KB)" in terminal.final_text
