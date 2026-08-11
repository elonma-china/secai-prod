"""RequireSection1SynthesisHook — when T2/T3 evidence exists but the model skipped
Section 1 anyway, force one retry instead of accepting a "không tổng hợp được" answer the
first time the model is reticent. Mirrors RequireRunInstructionsHook's pattern."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass

from linch.hooks.contexts import BeforeFinalAnswerContext

from secai_gateway.agent.hooks import RequireSection1SynthesisHook
from secai_gateway.poc.prefetch import (
    clear_session_coverage,
    clear_session_cve_known,
    set_session_coverage,
    set_session_cve_known,
)
from secai_gateway.state import SqliteLedger

_NO_SECTION1 = "## 2. Cách chạy PoC\n\n- không có\n\n## 3. Tổng quan\n- mô tả\n"
_WITH_SECTION1 = "## 1. Mã Khai thác (PoC) và Payload\n\n```python\nx=1\n```\n\n## 2. Cách chạy PoC\n"


@dataclass
class _FakeSession:
    meta: dict
    id: str | None = None


def _final_ctx(run_id: str, profile: str, final_text: str = "",
               session_id: str | None = None) -> BeforeFinalAnswerContext:
    return BeforeFinalAnswerContext(
        session=_FakeSession(meta={"profile": profile}, id=session_id),
        run_id=run_id, turn_index=1, final_text=final_text)


def _hook(ledger: SqliteLedger, max_retries: int = 2) -> RequireSection1SynthesisHook:
    return RequireSection1SynthesisHook(ledger=ledger, profile="exploit_research", max_retries=max_retries)


def _reset() -> None:
    clear_session_coverage()
    clear_session_cve_known()


def test_allows_when_no_synthesis_evidence_at_all(tmp_path) -> None:
    _reset()
    ledger = SqliteLedger(tmp_path / "ledger.db")
    hook = _hook(ledger)
    result = asyncio.run(hook.on_before_final_answer(_final_ctx("run1", "exploit_research", _NO_SECTION1)))
    assert result is None  # genuinely nothing to ground on — T4 is the correct answer


def test_blocks_when_description_evidence_exists_but_section1_missing(tmp_path) -> None:
    _reset()
    ledger = SqliteLedger(tmp_path / "ledger.db")
    ledger.save_evidence("ev:1", "run1", {"source": "ghsa", "description": "SSRF trong endpoint /admin"})
    hook = _hook(ledger)
    result = asyncio.run(hook.on_before_final_answer(_final_ctx("run1", "exploit_research", _NO_SECTION1)))
    assert result is not None and result.action == "force_continue"
    assert "Mục 1" in result.feedback or "Section 1" in result.feedback


def test_allows_when_section1_already_written(tmp_path) -> None:
    _reset()
    ledger = SqliteLedger(tmp_path / "ledger.db")
    ledger.save_evidence("ev:1", "run1", {"source": "ghsa", "description": "SSRF trong endpoint /admin"})
    hook = _hook(ledger)
    result = asyncio.run(hook.on_before_final_answer(_final_ctx("run1", "exploit_research", _WITH_SECTION1)))
    assert result is None


def test_blocks_when_cve_known_but_no_catalog_evidence(tmp_path) -> None:
    _reset()
    set_session_cve_known("sess-1", True)
    ledger = SqliteLedger(tmp_path / "ledger.db")
    hook = _hook(ledger)
    result = asyncio.run(hook.on_before_final_answer(
        _final_ctx("run1", "exploit_research", _NO_SECTION1, session_id="sess-1")))
    assert result is not None and result.action == "force_continue"


def test_skips_when_coverage_enough_real_code_regardless_of_section1(tmp_path) -> None:
    # T1 applies regardless — forcing a synthesis retry here would be pure waste
    _reset()
    set_session_coverage("sess-2", "enough")
    set_session_cve_known("sess-2", True)
    ledger = SqliteLedger(tmp_path / "ledger.db")
    hook = _hook(ledger)
    result = asyncio.run(hook.on_before_final_answer(
        _final_ctx("run1", "exploit_research", _NO_SECTION1, session_id="sess-2")))
    assert result is None


def test_skips_when_model_itself_fetched_real_code(tmp_path) -> None:
    _reset()
    ledger = SqliteLedger(tmp_path / "ledger.db")
    ledger.save_evidence("ev:1", "run1", {"source": "exploitdb", "sha256": "a" * 64,
                                          "source_code": "import socket\n"})
    hook = _hook(ledger)
    result = asyncio.run(hook.on_before_final_answer(_final_ctx("run1", "exploit_research", _NO_SECTION1)))
    assert result is None


def test_does_not_fire_for_other_profiles(tmp_path) -> None:
    _reset()
    ledger = SqliteLedger(tmp_path / "ledger.db")
    ledger.save_evidence("ev:1", "run1", {"source": "ghsa", "description": "SSRF"})
    hook = _hook(ledger)
    result = asyncio.run(hook.on_before_final_answer(_final_ctx("run1", "chat", _NO_SECTION1)))
    assert result is None


def test_gives_up_after_max_retries_and_allows_through(tmp_path) -> None:
    _reset()
    ledger = SqliteLedger(tmp_path / "ledger.db")
    ledger.save_evidence("ev:1", "run1", {"source": "ghsa", "description": "SSRF"})
    hook = _hook(ledger, max_retries=1)
    ctx = _final_ctx("run1", "exploit_research", _NO_SECTION1)
    first = asyncio.run(hook.on_before_final_answer(ctx))
    second = asyncio.run(hook.on_before_final_answer(ctx))
    assert first is not None and first.action == "force_continue"
    assert second is None  # gave up — synthesis_failed fallback takes over


def test_state_is_independent_per_run(tmp_path) -> None:
    _reset()
    ledger = SqliteLedger(tmp_path / "ledger.db")
    ledger.save_evidence("ev:1", "run1", {"source": "ghsa", "description": "SSRF"})
    ledger.save_evidence("ev:2", "run2", {"source": "ghsa", "description": "XSS"})
    hook = _hook(ledger, max_retries=1)
    asyncio.run(hook.on_before_final_answer(_final_ctx("run1", "exploit_research", _NO_SECTION1)))
    result = asyncio.run(hook.on_before_final_answer(_final_ctx("run2", "exploit_research", _NO_SECTION1)))
    assert result is not None and result.action == "force_continue"
