"""Retry-on-fidelity-failure for poc.consolidate.consolidate_poc.

A consolidated merge that fails the execution-free validator (dropped import / undefined
name / syntax / relative import / missing code block) is retried with the exact defect fed
back, up to ``poc_consolidate_max_attempts``, instead of falling straight back to the verbatim
dump. These use a scripted fake runner — no live LLM — so they assert the control flow:
recovery on a later attempt, give-up after the cap, and that the corrective feedback is
actually appended to the retry prompt.
"""
from __future__ import annotations

import asyncio
from typing import Any

import pytest

from secai_gateway.poc import consolidate as C

_GOOD = "```python\nimport sys\n\ndef main():\n    print('ok')\n\nif __name__ == '__main__':\n    main()\n```"
# uses POSTS_ITEM_SOURCE_PATH but never defines it -> _undefined_names rejects (the live bug)
_BAD_UNDEF = "```python\ndef run():\n    return POSTS_ITEM_SOURCE_PATH\n```"
_NO_BLOCK = "Xin lỗi, tôi không thể hợp nhất tệp này."


class _ScriptedRunner:
    """Returns the next scripted completion text per call; records prompts seen."""

    def __init__(self, outputs: list[str]) -> None:
        self._outputs = list(outputs)
        self.prompts: list[str] = []

    async def complete(self, profile: str, prompt: str) -> tuple[str, dict[str, Any]]:
        self.prompts.append(prompt)
        out = self._outputs.pop(0) if self._outputs else _NO_BLOCK
        return out, {}


def _evidence() -> list[dict[str, Any]]:
    return [{
        "source": "poc_github", "source_ref": "acme/poc/exploit.py",
        "url": "https://github.com/acme/poc", "kind": "exploit",
        "title": "demo", "cve_ids": ["CVE-2026-60137"], "language": "python",
        "sha256": "deadbeef", "source_code": "print('original tiny poc')\n",
        "source_files": [{"path": "exploit.py", "code": "print('original tiny poc')\n",
                          "role": "entry", "language": "python"}],
    }]


def test_recovers_on_second_attempt(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(C, "get_settings", lambda: _settings(max_attempts=3))
    runner = _ScriptedRunner([_BAD_UNDEF, _GOOD])
    res = asyncio.run(C.consolidate_poc(runner, _evidence(), "CVE-2026-60137"))
    assert res is not None and res["consolidated"] is True
    assert "def main()" in res["source_code"]
    # exactly two attempts, and the 2nd prompt carries the corrective feedback naming the defect
    assert len(runner.prompts) == 2
    assert "POSTS_ITEM_SOURCE_PATH" in runner.prompts[1]
    assert "SỬA LỖI" in runner.prompts[1]


def test_gives_up_after_max_attempts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(C, "get_settings", lambda: _settings(max_attempts=3))
    runner = _ScriptedRunner([_BAD_UNDEF, _BAD_UNDEF, _BAD_UNDEF])
    res = asyncio.run(C.consolidate_poc(runner, _evidence(), "CVE-2026-60137"))
    assert res is None
    assert len(runner.prompts) == 3  # exhausted the cap, no more


def test_no_retry_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(C, "get_settings", lambda: _settings(max_attempts=1))
    runner = _ScriptedRunner([_BAD_UNDEF, _GOOD])  # 2nd would pass, but retries disabled
    res = asyncio.run(C.consolidate_poc(runner, _evidence(), "CVE-2026-60137"))
    assert res is None
    assert len(runner.prompts) == 1  # single shot only


def test_missing_code_block_is_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(C, "get_settings", lambda: _settings(max_attempts=2))
    runner = _ScriptedRunner([_NO_BLOCK, _GOOD])
    res = asyncio.run(C.consolidate_poc(runner, _evidence(), "CVE-2026-60137"))
    assert res is not None
    assert len(runner.prompts) == 2
    assert "khối mã" in runner.prompts[1]  # feedback mentions the missing code block


def test_first_attempt_success_no_feedback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(C, "get_settings", lambda: _settings(max_attempts=3))
    runner = _ScriptedRunner([_GOOD])
    res = asyncio.run(C.consolidate_poc(runner, _evidence(), "CVE-2026-60137"))
    assert res is not None
    assert len(runner.prompts) == 1  # clean first try — no retry, no wasted call
    assert "SỬA LỖI" not in runner.prompts[0]


class _Settings:
    def __init__(self, max_attempts: int) -> None:
        self.poc_consolidate_max_attempts = max_attempts
        self.poc_consolidate_timeout_s = 180
        # _source_budget reads these:
        self.llm_provider = "vllm"
        self.qwen_context_window = 32768
        self.deepseek_context_window = 131072


def _settings(max_attempts: int) -> _Settings:
    return _Settings(max_attempts)
