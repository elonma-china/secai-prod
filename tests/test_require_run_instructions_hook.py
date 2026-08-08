from __future__ import annotations

import asyncio
from dataclasses import dataclass

from linch.hooks.contexts import BeforeFinalAnswerContext

from secai_gateway.agent.hooks import RequireRunInstructionsHook
from secai_gateway.state import SqliteLedger


@dataclass
class _FakeSession:
    meta: dict


def _final_ctx(run_id: str, profile: str, final_text: str = "") -> BeforeFinalAnswerContext:
    return BeforeFinalAnswerContext(session=_FakeSession(meta={"profile": profile}), run_id=run_id,
                                    turn_index=1, final_text=final_text)


def _hook(ledger: SqliteLedger, max_retries: int = 1) -> RequireRunInstructionsHook:
    return RequireRunInstructionsHook(ledger=ledger, profile="exploit_research", max_retries=max_retries)


def test_allows_when_no_evidence_at_all(tmp_path) -> None:
    ledger = SqliteLedger(tmp_path / "ledger.db")
    hook = _hook(ledger)
    result = asyncio.run(hook.on_before_final_answer(_final_ctx("run1", "exploit_research", "## 2. Tổng quan\n")))
    assert result is None  # nothing to require a run-instructions section from


def test_blocks_when_readme_evidence_exists_but_section_missing(tmp_path) -> None:
    ledger = SqliteLedger(tmp_path / "ledger.db")
    ledger.save_evidence("ev:1", "run1", {
        "sha256": "a" * 64,
        "source_files": [{"path": "poc.py", "role": "entry"}, {"path": "README.md", "role": "readme"}],
    })
    hook = _hook(ledger)
    result = asyncio.run(hook.on_before_final_answer(_final_ctx("run1", "exploit_research", "## 2. Tổng quan\n")))
    assert result is not None and result.action == "force_continue"
    assert "Cách chạy PoC" in result.feedback


def test_blocks_when_argparse_signal_in_source_code(tmp_path) -> None:
    ledger = SqliteLedger(tmp_path / "ledger.db")
    ledger.save_evidence("ev:1", "run1", {
        "sha256": "a" * 64,
        "source_code": "import argparse\nparser = argparse.ArgumentParser()\n",
    })
    hook = _hook(ledger)
    result = asyncio.run(hook.on_before_final_answer(_final_ctx("run1", "exploit_research", "## 2. Tổng quan\n")))
    assert result is not None and result.action == "force_continue"


def test_allows_when_section_already_present(tmp_path) -> None:
    ledger = SqliteLedger(tmp_path / "ledger.db")
    ledger.save_evidence("ev:1", "run1", {
        "sha256": "a" * 64,
        "source_files": [{"path": "README.md", "role": "readme"}],
    })
    hook = _hook(ledger)
    text = "## 2. Tổng quan\n\n### Cách chạy PoC\n- chạy: python poc.py --host X\n"
    result = asyncio.run(hook.on_before_final_answer(_final_ctx("run1", "exploit_research", text)))
    assert result is None


def test_allows_when_evidence_has_no_run_info_signal(tmp_path) -> None:
    # real source_code, but no README/argparse-style signal — nothing to require
    ledger = SqliteLedger(tmp_path / "ledger.db")
    ledger.save_evidence("ev:1", "run1", {
        "sha256": "a" * 64,
        "source_code": "import socket\ns = socket.socket()\ns.connect(('x', 80))\n",
    })
    hook = _hook(ledger)
    result = asyncio.run(hook.on_before_final_answer(_final_ctx("run1", "exploit_research", "## 2. Tổng quan\n")))
    assert result is None


def test_does_not_fire_for_other_profiles(tmp_path) -> None:
    ledger = SqliteLedger(tmp_path / "ledger.db")
    ledger.save_evidence("ev:1", "run1", {
        "sha256": "a" * 64,
        "source_files": [{"path": "README.md", "role": "readme"}],
    })
    hook = _hook(ledger)
    result = asyncio.run(hook.on_before_final_answer(_final_ctx("run1", "chat", "no section here")))
    assert result is None


def test_gives_up_after_max_retries_and_allows_through(tmp_path) -> None:
    ledger = SqliteLedger(tmp_path / "ledger.db")
    ledger.save_evidence("ev:1", "run1", {
        "sha256": "a" * 64,
        "source_files": [{"path": "README.md", "role": "readme"}],
    })
    hook = _hook(ledger, max_retries=1)
    ctx = _final_ctx("run1", "exploit_research", "## 2. Tổng quan\n")
    first = asyncio.run(hook.on_before_final_answer(ctx))
    second = asyncio.run(hook.on_before_final_answer(ctx))
    assert first is not None and first.action == "force_continue"
    assert second is None  # gave up after max_retries=1


def test_state_is_independent_per_run(tmp_path) -> None:
    ledger = SqliteLedger(tmp_path / "ledger.db")
    ledger.save_evidence("ev:1", "run1", {
        "sha256": "a" * 64,
        "source_files": [{"path": "README.md", "role": "readme"}],
    })
    ledger.save_evidence("ev:2", "run2", {
        "sha256": "b" * 64,
        "source_files": [{"path": "README.md", "role": "readme"}],
    })
    hook = _hook(ledger, max_retries=1)
    asyncio.run(hook.on_before_final_answer(_final_ctx("run1", "exploit_research", "no section")))
    # run2 is independent — still gets its own first force_continue, not affected by run1's count
    result = asyncio.run(hook.on_before_final_answer(_final_ctx("run2", "exploit_research", "no section")))
    assert result is not None and result.action == "force_continue"
