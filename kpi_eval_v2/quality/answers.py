"""Collect the gateway's REAL answers for a gold set — sequential, self-paced (see
common.gateway_chat), shared by faithfulness/relevancy, entity/hallucination, and the
task-rubric KPIs so each gold item only ever costs ONE call to production :8131.
"""
from __future__ import annotations

import json

from .. import config
from . import common


def collect_answers(items: list[dict], *, message_key: str = "query",
                     cache_name: str | None = None) -> list[dict]:
    """items: list of {id, <message_key>, ...gold fields}. Calls gateway_chat once per item,
    sequentially. Returns items augmented with answer/tools/error/timings. Writes evidence
    jsonl to results/ if cache_name is given."""
    out = []
    logf = None
    if cache_name:
        logf = (config.RESULTS / f"quality_answers_{cache_name}.jsonl").open("w", encoding="utf-8")
    for i, it in enumerate(items, 1):
        message = it[message_key]
        r = common.gateway_chat(message)
        rec = {**it, "answer": r["text"], "tools": r["tools"], "tool_results": r["tool_results"],
               "error": r["error"], "ttft_ms": r["ttft_ms"], "total_ms": r["total_ms"],
               "input_classification": r["input_classification"]}
        out.append(rec)
        if logf:
            logf.write(json.dumps({k: v for k, v in rec.items() if k != "code"}, ensure_ascii=False,
                                   default=str) + "\n")
            logf.flush()
    if logf:
        logf.close()
    return out
