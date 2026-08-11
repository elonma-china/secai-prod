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
    enable_exploit: bool = False,
    run_cve_sweep: bool = True,
) -> AsyncIterator[Event | dict[str, Any]]:
    """Yield workflow events + live ``log`` dicts, then ``bughunt_result``."""
    rid = (run_id or "").strip() or f"bh:{uuid4().hex}"
    queue: asyncio.Queue[Event | dict[str, Any] | None] = asyncio.Queue()
    loop = asyncio.get_running_loop()

    def on_event(event: Event) -> None:
        queue.put_nowait(event)

    def on_log(stream: str, line: str) -> None:
        payload = {
            "type": "log",
            "stream": stream,
            "line": line,
            "run_id": rid,
        }
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is loop:
            queue.put_nowait(payload)
        else:
            loop.call_soon_threadsafe(queue.put_nowait, payload)

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
                    enable_exploit=enable_exploit,
                    run_cve_sweep=run_cve_sweep,
                ),
                run_id=rid,
                max_concurrency=max_concurrency,
                on_event=on_event,
            )
            queue.put_nowait({"type": "bughunt_result", "run_id": rid, "result": result})
        except Exception as exc:  # noqa: BLE001
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
