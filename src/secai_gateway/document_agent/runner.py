"""A short, isolated DuyTa turn for document OCR orchestration."""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

from linch import Agent, InMemoryRunStore, InMemorySessionStore, RunOptions
from linch.config import FeatureFlags
from linch.events import ResultEvent, ToolCallEndEvent, ToolCallStartEvent
from linch.tools.registry import empty_tools

from ..agent.profiles import PROFILES, compose_system_prompt
from ..agent.providers import build_llm_provider
from ..security import neutralize_active_content
from .tools import DocumentRunDeps, build_document_tools


class DocumentRunner:
    def __init__(self, *, provider: Any = None, model: str | None = None) -> None:
        if provider is None:
            provider, model = build_llm_provider()
            if not model or "duyta" not in model.lower():
                raise ValueError("document agent requires the configured DuyTa model")
        if not model:
            raise ValueError("model identity required")
        profile = PROFILES["document_ocr"]
        tools = build_document_tools()
        if set(tools) != set(profile.tool_names):
            raise ValueError("document tool registry differs from profile")
        self.agent = Agent(
            provider=provider, model=model, tools=empty_tools(*tools.values()),
            system_prompt=compose_system_prompt(profile),
            session_store=InMemorySessionStore(), run_store=InMemoryRunStore(),
            features=FeatureFlags(skills=False, subagents=False, mcp=False),
            max_turns=3, max_output_tokens=1024, include_partial_messages=True,
            max_tool_concurrency=1, tool_retry=False,
        )

    async def stream(self, prompt: str, deps: DocumentRunDeps) -> AsyncIterator[dict[str, Any]]:
        if not isinstance(deps, DocumentRunDeps):
            raise ValueError("document dependencies required")
        session = await self.agent.session()
        final = ""
        async with asyncio.timeout(120):
            async for event in session.run(prompt, RunOptions(deps=deps, max_output_tokens=1024)):
                if isinstance(event, ToolCallStartEvent) and event.tool_name in {"ocr_extract", "ocr_result"}:
                    yield {"type": "tool_start", "name": event.tool_name}
                elif isinstance(event, ToolCallEndEvent) and event.tool_name in {"ocr_extract", "ocr_result"}:
                    task = deps.submitted if event.tool_name == "ocr_extract" else next(iter(deps.observed_tasks.values()), None)
                    yield {"type": "tool_end", "name": event.tool_name, **(task.public() if task else {"status": "unavailable"})}
                elif isinstance(event, ResultEvent):
                    final = event.final_text or ""
                    if event.subtype != "success":
                        yield {"type": "error", "code": "DOCUMENT_RUN_FAILED"}
                        return
        tasks = [task.public() for task in deps.observed_tasks.values()]
        if any(t["status"] in {"queued", "running", "cancelling"} for t in tasks):
            final = "OCR đang xử lý. Hãy kiểm tra trạng thái tác vụ rồi gửi lượt mới với task_handle."
        elif deps.admission_failed:
            final = "Không thể tiếp nhận ảnh để OCR. Vui lòng thử lại sau."
        elif not final.strip():
            final = "Chưa có kết quả OCR."
        final = neutralize_active_content(final[:10000])
        if final:
            yield {"type": "token", "delta": final[:10000]}
        yield {"type": "final", "answer": final[:10000], "tasks": tasks}

    async def complete(self, prompt: str, deps: DocumentRunDeps) -> dict[str, Any]:
        final: dict[str, Any] | None = None
        async for event in self.stream(prompt, deps):
            if event["type"] == "final":
                final = {"answer": event["answer"], "tasks": event["tasks"]}
        if final is None:
            raise RuntimeError("document run did not finish")
        return final
