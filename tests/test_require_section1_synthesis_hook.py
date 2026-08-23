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
    clear_session_real_code_evidence,
    clear_session_synthesis_evidence,
    set_session_coverage,
    set_session_cve_known,
    set_session_real_code_evidence,
    set_session_synthesis_evidence,
)
from secai_gateway.state import SqliteLedger

_NO_SECTION1 = "## 2. Cách chạy PoC\n\n- không có\n\n## 3. Tổng quan\n- mô tả\n"
_WITH_SECTION1 = (
    "## 1. Mã Khai thác (PoC) và Payload\n\n```python\nx=1\n```\n\n"
    "## 2. Cách chạy PoC\n- chạy thử\n\n## 3. Tổng quan Lỗ hổng\n- mô tả\n"
)
_SECTION1_ONLY = "## 1. Mã Khai thác (PoC) và Payload\n\n```python\nx=1\n```\n"
_SECTION1_AND_2_ONLY = _SECTION1_ONLY + "\n## 2. Cách chạy PoC\n- chạy thử\n"


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
    clear_session_synthesis_evidence()
    clear_session_real_code_evidence()


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


def test_blocks_when_section1_written_but_section2_and_3_missing(tmp_path) -> None:
    """Regression guard: observed live twice (CVE-2025-40639, CVE-2026-41265) — the model
    writes a correct, banner-wrapped Mục 1 then jumps straight to the closing footer,
    skipping Mục 2 and Mục 3 entirely. section1_has_body alone must not be read as "done"."""
    _reset()
    ledger = SqliteLedger(tmp_path / "ledger.db")
    ledger.save_evidence("ev:1", "run1", {"source": "ghsa", "description": "SSRF trong endpoint /admin"})
    hook = _hook(ledger)
    result = asyncio.run(hook.on_before_final_answer(_final_ctx("run1", "exploit_research", _SECTION1_ONLY)))
    assert result is not None and result.action == "force_continue"
    assert "## 2" in result.feedback and "## 3" in result.feedback


def test_blocks_when_only_section3_missing(tmp_path) -> None:
    _reset()
    ledger = SqliteLedger(tmp_path / "ledger.db")
    ledger.save_evidence("ev:1", "run1", {"source": "ghsa", "description": "SSRF trong endpoint /admin"})
    hook = _hook(ledger)
    result = asyncio.run(hook.on_before_final_answer(_final_ctx("run1", "exploit_research", _SECTION1_AND_2_ONLY)))
    assert result is not None and result.action == "force_continue"
    # Feedback asks for the FULL required set (## 1/2/3), not just the literally-missing
    # piece — force_continue's retry turn is NOT merged with the rejected draft into the
    # final delivered text (see class docstring), so "just add what's missing" instructions
    # measurably caused the model to drop already-written sections on retry. Must also name
    # the "rewrite everything" framing so the model doesn't rely on the old draft persisting.
    assert "## 1" in result.feedback and "## 2" in result.feedback and "## 3" in result.feedback
    assert "REPLACED" in result.feedback or "COMPLETE" in result.feedback


def test_blocks_when_cve_known_but_no_catalog_evidence(tmp_path) -> None:
    _reset()
    set_session_cve_known("sess-1", True)
    ledger = SqliteLedger(tmp_path / "ledger.db")
    hook = _hook(ledger)
    result = asyncio.run(hook.on_before_final_answer(
        _final_ctx("run1", "exploit_research", _NO_SECTION1, session_id="sess-1")))
    assert result is not None and result.action == "force_continue"


def test_blocks_when_prefetch_found_description_but_model_fetched_nothing_itself(tmp_path) -> None:
    """The gap this test guards: `exploit_prefetch` (routes.py) runs the deterministic
    catalog search under its OWN scratch run_id, so a T2-worthy catalog description it
    found is never in `ledger.evidence_for_run(ctx.run_id)` — only `set_session_synthesis_
    evidence` (called from prefetch) can carry that signal to this hook. Empty ledger
    evidence for THIS run_id must not be read as "nothing to synthesize from" when
    prefetch already found something."""
    _reset()
    set_session_synthesis_evidence("sess-3", True)
    ledger = SqliteLedger(tmp_path / "ledger.db")
    hook = _hook(ledger)
    result = asyncio.run(hook.on_before_final_answer(
        _final_ctx("run1", "exploit_research", _NO_SECTION1, session_id="sess-3")))
    assert result is not None and result.action == "force_continue"


def test_skips_when_prefetch_found_real_code_regardless_of_section1(tmp_path) -> None:
    # T1 applies regardless — forcing a synthesis retry here would be pure waste. Note
    # _NO_SECTION1 already HAS both Mục 2 and Mục 3 (it's only missing Mục 1) — this test
    # is about Section 1 being optional in T1, not about Mục 2/3 completeness (see the
    # T1-specific tests below for that).
    _reset()
    set_session_real_code_evidence("sess-2", True)
    set_session_cve_known("sess-2", True)
    ledger = SqliteLedger(tmp_path / "ledger.db")
    hook = _hook(ledger)
    result = asyncio.run(hook.on_before_final_answer(
        _final_ctx("run1", "exploit_research", _NO_SECTION1, session_id="sess-2")))
    assert result is None


def test_t1_blocks_when_section3_missing_after_section2(tmp_path) -> None:
    """Regression guard for the eval-discovered gap (evals/, 2026-08-23): T1 (real fetched
    code) used to `return None` before ever checking Mục 2/3, so a model that wrote Mục 2
    then stopped — never reaching Mục 3 — sailed through unnoticed. ~8% of real/
    real_consolidated exploit answers hit exactly this in the eval run."""
    _reset()
    set_session_real_code_evidence("sess-t1", True)
    ledger = SqliteLedger(tmp_path / "ledger.db")
    hook = _hook(ledger)
    text = "## 2. Cách chạy PoC\n\n- chạy thử\n"  # Mục 3 missing
    result = asyncio.run(hook.on_before_final_answer(
        _final_ctx("run1", "exploit_research", text, session_id="sess-t1")))
    assert result is not None and result.action == "force_continue"
    # Feedback asks for BOTH ## 2 and ## 3 (the full T1 set), not just the literally-missing
    # ## 3 — see class docstring: a "just add what's missing" instruction was tried first and
    # measurably caused the model to drop the already-written ## 2 on retry (second eval pass,
    # same session: all 8 T1 retries landed on "Mục 2 now missing, Mục 3 now present" — the
    # exact inverse of this test's starting point).
    assert "## 2" in result.feedback and "## 3" in result.feedback
    assert "REPLACED" in result.feedback or "COMPLETE" in result.feedback
    assert "Section 1" not in result.feedback.split("required")[0]  # không nói viết lại Mục 1


def test_t1_blocks_when_both_section2_and_3_missing(tmp_path) -> None:
    _reset()
    ledger = SqliteLedger(tmp_path / "ledger.db")
    ledger.save_evidence("ev:1", "run1", {"source": "exploitdb", "sha256": "b" * 64,
                                          "source_code": "import socket\n"})
    hook = _hook(ledger)
    text = "Một vài câu mở đầu không đúng heading nào."
    result = asyncio.run(hook.on_before_final_answer(_final_ctx("run1", "exploit_research", text)))
    assert result is not None and result.action == "force_continue"
    assert "## 2" in result.feedback and "## 3" in result.feedback


def test_t1_allows_when_section2_and_3_both_present(tmp_path) -> None:
    _reset()
    set_session_real_code_evidence("sess-t1b", True)
    ledger = SqliteLedger(tmp_path / "ledger.db")
    hook = _hook(ledger)
    text = "## 2. Cách chạy PoC\n- chạy thử\n\n## 3. Tổng quan Lỗ hổng\n- mô tả\n"
    result = asyncio.run(hook.on_before_final_answer(
        _final_ctx("run1", "exploit_research", text, session_id="sess-t1b")))
    assert result is None


def test_coverage_enough_alone_does_NOT_skip_when_it_was_description_only(tmp_path) -> None:
    """Regression guard for the bug this fix closes: `coverage == "enough"` only means
    prefetch fetched *something*, not that it was real code — a metadata-only ghsa/README
    fetch also sets it. Must NOT be read as "T1 will happen regardless" on its own."""
    _reset()
    set_session_coverage("sess-4", "enough")  # description-only fetch also sets this
    set_session_synthesis_evidence("sess-4", True)
    ledger = SqliteLedger(tmp_path / "ledger.db")
    hook = _hook(ledger)
    result = asyncio.run(hook.on_before_final_answer(
        _final_ctx("run1", "exploit_research", _NO_SECTION1, session_id="sess-4")))
    assert result is not None and result.action == "force_continue"


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
