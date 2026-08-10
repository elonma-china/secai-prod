"""Run ``bughunt_flow`` and yield Linch events for SSE."""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any
from uuid import uuid4

from linch.events import ErrorEvent, Event

from .workflow import bughunt_flow


async def stream_bughunt_workflow(
    agent: Any,
    *,
    target: str,
    scope_note: str = "",
    run_id: str | None = None,
    max_concurrency: int = 4,
    use_llm: bool | None = None,
    use_recon_engine: bool | None = None,
) -> AsyncIterator[Event | dict[str, Any]]:
    """Yield workflow events + live ``log`` dicts, then ``bughunt_result``.

    Bridges ``agent.run_workflow(..., on_event=...)`` through an asyncio queue so
    the FastAPI SSE generator can forward frames as they happen (not only at end).
    """
    rid = (run_id or "").strip() or f"bh:{uuid4().hex}"
    queue: asyncio.Queue[Event | dict[str, Any] | None] = asyncio.Queue()

    def on_event(event: Event) -> None:
        queue.put_nowait(event)

    def on_log(stream: str, line: str) -> None:
        queue.put_nowait({
            "type": "log",
            "stream": stream,
            "line": line,
            "run_id": rid,
        })

    async def _run() -> None:
        try:
            result = await agent.run_workflow(
                lambda wf: bughunt_flow(
                    wf,
                    target=target,
                    scope_note=scope_note,
                    use_llm=use_llm,
                    use_recon_engine=use_recon_engine,
                    on_log=on_log,
                    run_id=rid,
                ),
                run_id=rid,
                max_concurrency=max_concurrency,
                on_event=on_event,
            )
            queue.put_nowait({"type": "bughunt_result", "run_id": rid, "result": result})
        except Exception as exc:  # noqa: BLE001 — surface to SSE as ErrorEvent
            queue.put_nowait(ErrorEvent(error=exc))
        finally:
            queue.put_nowait(None)

    task = asyncio.create_task(_run())
    try:
        while True:
            item = await queue.get()
            if item is None:
                break
            yield item
    finally:
        await task
