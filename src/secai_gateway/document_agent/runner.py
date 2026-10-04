"""A short, isolated DuyTa turn for document OCR orchestration."""
from __future__ import annotations

import asyncio
import hashlib
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from linch import Agent, InMemoryRunStore, InMemorySessionStore, RunOptions
from linch.config import FeatureFlags
from linch.events import ResultEvent, ToolCallEndEvent, ToolCallStartEvent
from linch.tools.registry import empty_tools
from linch.providers.openai_chat import _build_chat_payload
from jinja2 import Environment

from ..agent.profiles import PROFILES, compose_system_prompt
from ..agent.providers import build_llm_provider
from ..config import get_settings
from ..multilang.config import EXPECTED_MODEL, MultilangSettings, verified_counter
from ..security import neutralize_active_content
from .tools import DocumentRunDeps, build_document_tools


@dataclass(frozen=True, slots=True)
class DocumentTokenBudget:
    """Pinned DuyTa tokenizer/template, checked against every Linch provider request."""

    model: str
    count: Any
    template: Any
    context_tokens: int

    @classmethod
    def from_files(cls, *, model: str, tokenizer_path: Path, tokenizer_sha256: str,
                   template_path: Path, template_sha256: str, context_tokens: int) -> "DocumentTokenBudget":
        if not 1024 <= context_tokens <= 131072:
            raise ValueError("DOCUMENT_BUDGET_UNVERIFIED")
        settings = MultilangSettings(
            _env_file=None, tokenizer_json=str(tokenizer_path), tokenizer_sha256=tokenizer_sha256,
            context_tokens=context_tokens, chat_template_overhead_tokens=1,
        )
        count = verified_counter(settings)
        if count is None:
            raise ValueError("DOCUMENT_BUDGET_UNVERIFIED")
        try:
            template_bytes = Path(template_path).read_bytes()
            if hashlib.sha256(template_bytes).hexdigest() != template_sha256.lower():
                raise ValueError("DOCUMENT_BUDGET_UNVERIFIED")
            template = Environment().from_string(template_bytes.decode("utf-8"))
        except (OSError, UnicodeError) as exc:
            raise ValueError("DOCUMENT_BUDGET_UNVERIFIED") from exc
        return cls(model, count, template, context_tokens)

    @classmethod
    def from_settings(cls, model: str) -> "DocumentTokenBudget":
        settings = get_settings()
        return cls.from_files(
            model=model,
            tokenizer_path=Path(settings.document_agent_tokenizer_json),
            tokenizer_sha256=settings.document_agent_tokenizer_sha256,
            template_path=Path(settings.document_agent_chat_template_path),
            template_sha256=settings.document_agent_chat_template_sha256,
            context_tokens=settings.document_agent_context_tokens,
        )

    def check(self, req: Any, provider_window: int) -> None:
        if req.model != self.model or provider_window <= 0:
            raise ValueError("DOCUMENT_BUDGET_UNVERIFIED")
        payload = _build_chat_payload(req)
        rendered = self.template.render(
            messages=payload["messages"], tools=payload.get("tools", []),
            add_generation_prompt=True, enable_thinking=False,
        )
        output = req.max_output_tokens or 1024
        if output > 1024 or self.count(rendered) + output > min(self.context_tokens, provider_window):
            raise ValueError("DOCUMENT_CONTEXT_LIMIT")


class _BudgetedProvider:
    def __init__(self, provider: Any, budget: DocumentTokenBudget) -> None:
        self.provider = provider
        self.budget = budget
        self.id = provider.id

    def context_window(self, model: str) -> int:
        return self.provider.context_window(model)

    def capabilities(self, model: str):
        return self.provider.capabilities(model)

    async def stream(self, req: Any):
        await asyncio.to_thread(self.budget.check, req, self.provider.context_window(req.model))
        async for event in self.provider.stream(req):
            yield event


class DocumentRunner:
    def __init__(self, *, provider: Any = None, model: str | None = None,
                 budget: DocumentTokenBudget | None = None) -> None:
        if provider is None:
            provider, model = build_llm_provider()
            if model != EXPECTED_MODEL:
                raise ValueError("document agent requires the configured DuyTa model")
        if not model:
            raise ValueError("model identity required")
        budget = budget or DocumentTokenBudget.from_settings(model)
        profile = PROFILES["document_ocr"]
        tools = build_document_tools()
        if set(tools) != set(profile.tool_names):
            raise ValueError("document tool registry differs from profile")
        self.agent = Agent(
            provider=_BudgetedProvider(provider, budget), model=model, tools=empty_tools(*tools.values()),
            system_prompt=compose_system_prompt(profile),
            session_store=InMemorySessionStore(), run_store=InMemoryRunStore(),
            features=FeatureFlags(skills=False, subagents=False, mcp=False, filesystem=False),
            max_turns=3, max_output_tokens=1024, include_partial_messages=True,
            max_tool_concurrency=1, tool_retry=False, max_retries=0,
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
