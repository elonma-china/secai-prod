"""SanitizeToolResultHook — indirect prompt-injection defense (spotlight untrusted tool results)."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass

from linch.hooks.contexts import PostToolUseContext
from linch.hooks.types import ToolResult

from secai_gateway.agent.hooks import SanitizeToolResultHook, UNTRUSTED_CONTENT_TOOLS


@dataclass
class _FakeSession:
    meta: dict
    id: str | None = None


def _post(tool: str, content: str, *, is_error: bool = False) -> PostToolUseContext:
    return PostToolUseContext(
        session=_FakeSession(meta={"profile": "chat"}),
        run_id="r1",
        turn_index=0,
        tool_name=tool,
        result=ToolResult(content=content, is_error=is_error, summary="s", truncated=False),
    )


def _run(coro):
    return asyncio.run(coro)


def test_wraps_untrusted_tool_result():
    hook = SanitizeToolResultHook()
    ctx = _post("qdrant_search", "### Result 1\nSome KB text")
    result = _run(hook.on_post_tool_use(ctx))
    assert result is not None and result.action == "mutate"
    assert result.tool_result.content.startswith(
        '<untrusted_retrieved_content source="qdrant_search">')
    assert "Some KB text" in result.tool_result.content


def test_all_documented_untrusted_tools_get_wrapped():
    hook = SanitizeToolResultHook()
    for tool in UNTRUSTED_CONTENT_TOOLS:
        ctx = _post(tool, "external content")
        result = _run(hook.on_post_tool_use(ctx))
        assert result is not None and result.action == "mutate", tool


def test_ignores_tool_not_in_untrusted_set():
    hook = SanitizeToolResultHook()
    ctx = _post("poc_verify", "internal deterministic output")
    assert _run(hook.on_post_tool_use(ctx)) is None


def test_bughunt_scan_tools_are_wrapped():
    """Bughunt tools share the same gateway Agent (LinchRunner merges build_bughunt_tools()
    into the one shared self.tools/self.agent) — probe/scan output from a user-specified,
    potentially hostile target must be spotlighted just like web_fetch/qdrant_search."""
    from secai_gateway.bughunt.tools import AGENT_TOOL_NAMES

    hook = SanitizeToolResultHook()
    for tool in ("bh_http_probe", "run_recon", "run_vuln_scan", "run_verify_leads",
                 "run_sqlmap_targeted", "read_recon_summary", "read_findings_summary"):
        ctx = _post(tool, "reflected payload from the live target")
        result = _run(hook.on_post_tool_use(ctx))
        assert result is not None and result.action == "mutate", tool

    # update_working_memory/finish only echo the MODEL's own input, never target content.
    for tool in ("update_working_memory", "finish"):
        ctx = _post(tool, "model-authored text")
        assert _run(hook.on_post_tool_use(ctx)) is None, tool
        assert tool in AGENT_TOOL_NAMES  # sanity: tool name is real, not a typo


def test_poc_search_excluded_json_stays_parseable():
    """RequireSearchBeforeAnswerHook.on_post_tool_use does json.loads(ctx.result.content) for
    poc_search — wrapping it in a delimiter would break that parse for any hook dispatched
    after this one. poc_fetch (the actual PoC source/README bytes) stays wrapped instead."""
    import json

    hook = SanitizeToolResultHook()
    payload = '{"hits": [{"ref": "x", "title": "some PoC"}]}'
    ctx = _post("poc_search", payload)
    assert _run(hook.on_post_tool_use(ctx)) is None
    assert json.loads(payload)["hits"][0]["ref"] == "x"  # unchanged, still parseable
    assert "poc_search" not in UNTRUSTED_CONTENT_TOOLS
    assert "poc_fetch" in UNTRUSTED_CONTENT_TOOLS


def test_skips_error_results():
    hook = SanitizeToolResultHook()
    ctx = _post("web_fetch", "fetch failed: timeout", is_error=True)
    assert _run(hook.on_post_tool_use(ctx)) is None


def test_skips_empty_content():
    hook = SanitizeToolResultHook()
    ctx = _post("qdrant_search", "")
    assert _run(hook.on_post_tool_use(ctx)) is None


def test_preserves_other_tool_result_fields():
    hook = SanitizeToolResultHook()
    ctx = PostToolUseContext(
        session=_FakeSession(meta={"profile": "chat"}),
        run_id="r1", turn_index=0, tool_name="fetch_reference",
        result=ToolResult(content="text", summary="my summary", is_error=False,
                          metadata={"k": "v"}, duration_ms=42, truncated=True,
                          recovery_hint="hint"),
    )
    result = _run(hook.on_post_tool_use(ctx))
    tr = result.tool_result
    assert tr.summary == "my summary"
    assert tr.metadata == {"k": "v"}
    assert tr.duration_ms == 42
    assert tr.truncated is True
    assert tr.recovery_hint == "hint"
