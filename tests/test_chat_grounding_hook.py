"""RequireChatGroundingHook — route taxonomy / entity / id_lookup / concept."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass

from linch.hooks.contexts import BeforeFinalAnswerContext, PostToolUseContext, PreToolUseContext
from linch.hooks.types import ToolResult

from secai_gateway.agent.hooks import RequireChatGroundingHook, classify_chat_grounding


@dataclass
class _FakeSession:
    meta: dict
    id: str | None = None


def _pre(tool: str, run_id: str, url: str | None = None) -> PreToolUseContext:
    inp = {"url": url} if url is not None else {}
    return PreToolUseContext(
        session=_FakeSession(meta={"profile": "chat"}),
        run_id=run_id,
        turn_index=0,
        tool_name=tool,
        input=inp,
    )


def _post(tool: str, run_id: str, *, is_error: bool = False) -> PostToolUseContext:
    return PostToolUseContext(
        session=_FakeSession(meta={"profile": "chat"}),
        run_id=run_id,
        turn_index=0,
        tool_name=tool,
        result=ToolResult(content="ok" if not is_error else "fail", is_error=is_error),
    )


def _final(run_id: str, profile: str, prompt: str) -> BeforeFinalAnswerContext:
    return BeforeFinalAnswerContext(
        session=_FakeSession(meta={"profile": profile, "last_user_prompt": prompt}),
        run_id=run_id,
        turn_index=1,
        final_text="answer",
    )


def test_classify_routes() -> None:
    assert classify_chat_grounding("OWASP Top 10").route == "taxonomy"
    assert classify_chat_grounding("Top 25 điểm yếu phần mềm của MITRE").need_web
    assert classify_chat_grounding("CWE-120 là gì?").route == "entity"
    assert classify_chat_grounding("buffer copy… CWE nào?").route == "id_lookup"
    assert classify_chat_grounding("nginx CVE nào năm 2026?").route == "id_lookup"
    assert classify_chat_grounding("SQL Injection là gì?").route == "concept"


def test_blocks_owasp_without_web_fetch() -> None:
    hook = RequireChatGroundingHook(max_retries=2)
    result = asyncio.run(hook.on_before_final_answer(
        _final("r1", "chat", "OWASP Top 10 bao gồm những nhóm rủi ro nào?")))
    assert result is not None and result.action == "force_continue"
    assert "web_fetch" in result.feedback


def test_allows_owasp_after_successful_web_fetch() -> None:
    hook = RequireChatGroundingHook()
    asyncio.run(hook.on_post_tool_use(_post("web_fetch", "r1")))
    result = asyncio.run(hook.on_before_final_answer(
        _final("r1", "chat", "Hãy liệt kê CWE Top 25")))
    assert result is None


def test_failed_web_fetch_does_not_satisfy() -> None:
    hook = RequireChatGroundingHook()
    asyncio.run(hook.on_post_tool_use(_post("web_fetch", "r1", is_error=True)))
    result = asyncio.run(hook.on_before_final_answer(
        _final("r1", "chat", "CWE Top 25")))
    assert result is not None


def test_blocks_owasp_top3_without_web_fetch() -> None:
    hook = RequireChatGroundingHook(max_retries=3)
    result = asyncio.run(hook.on_before_final_answer(
        _final("r1", "chat", "OWASP top 3 hiện tại đang là gì?")))
    assert result is not None and "web_fetch" in result.feedback


def test_owasp_plus_cve_requires_web_then_kb() -> None:
    hook = RequireChatGroundingHook(max_retries=3)
    prompt = "OWASP top 3 hiện tại đang là gì và nó có liên quan gì đến các CVE?"
    r1 = asyncio.run(hook.on_before_final_answer(_final("r1", "chat", prompt)))
    assert r1 is not None and "web_fetch" in r1.feedback
    asyncio.run(hook.on_post_tool_use(_post("web_fetch", "r1")))
    r2 = asyncio.run(hook.on_before_final_answer(_final("r1", "chat", prompt)))
    assert r2 is not None and ("qdrant_search" in r2.feedback or "neo4j_search" in r2.feedback)
    asyncio.run(hook.on_post_tool_use(_post("qdrant_search", "r1")))
    r3 = asyncio.run(hook.on_before_final_answer(_final("r1", "chat", prompt)))
    assert r3 is None


def test_blocks_cwe_id_without_qdrant() -> None:
    hook = RequireChatGroundingHook()
    result = asyncio.run(hook.on_before_final_answer(
        _final("r1", "chat", "CWE-120 là gì?")))
    assert result is not None and "qdrant_search" in result.feedback


def test_id_lookup_paraphrase_requires_kb() -> None:
    hook = RequireChatGroundingHook()
    result = asyncio.run(hook.on_before_final_answer(
        _final("r1", "chat", "Điểm yếu buffer copy không kiểm tra size — CWE nào?")))
    assert result is not None and result.reason == "chat_id_lookup_required"


def test_allows_cwe_id_after_qdrant() -> None:
    hook = RequireChatGroundingHook()
    asyncio.run(hook.on_post_tool_use(_post("qdrant_search", "r1")))
    result = asyncio.run(hook.on_before_final_answer(
        _final("r1", "chat", "CWE-120 là gì?")))
    assert result is None


def test_concept_question_does_not_force_tools() -> None:
    hook = RequireChatGroundingHook()
    result = asyncio.run(hook.on_before_final_answer(
        _final("r1", "chat", "SQL Injection trong form đăng nhập là gì?")))
    assert result is None


def test_other_profile_ignored() -> None:
    hook = RequireChatGroundingHook()
    result = asyncio.run(hook.on_before_final_answer(
        _final("r1", "exploit_research", "OWASP Top 10")))
    assert result is None


def test_pre_blocks_non_allowlisted_web_fetch(monkeypatch) -> None:
    import secai_gateway.retrieval.http_fetch as hf

    def fake_getaddrinfo(host, port, **kwargs):  # noqa: ANN001
        return [(None, None, None, None, ("1.2.3.4", 443))]

    monkeypatch.setattr(hf.socket, "getaddrinfo", fake_getaddrinfo)
    hook = RequireChatGroundingHook()
    blocked = asyncio.run(hook.on_pre_tool_use(
        _pre("web_fetch", "r1", url="https://evil.example/")))
    assert blocked is not None and blocked.action == "block"

    allowed = asyncio.run(hook.on_pre_tool_use(
        _pre("web_fetch", "r1", url="https://owasp.org/Top10/2025/")))
    assert allowed is None


def test_gives_up_after_retries() -> None:
    hook = RequireChatGroundingHook(max_retries=2)
    ctx = _final("r1", "chat", "OWASP Top 10")
    assert asyncio.run(hook.on_before_final_answer(ctx)) is not None
    assert asyncio.run(hook.on_before_final_answer(ctx)) is not None
    assert asyncio.run(hook.on_before_final_answer(ctx)) is None
