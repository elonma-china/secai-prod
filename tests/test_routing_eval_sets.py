"""Routing quality on two independently written sets (A: used while tuning, B: held out, never tuned on).

Floors, not targets: a regression of routing precision/recall fails here. Measured 2026-10-07 with all routing flags on:
A knowledge 0.97 / cve 1.00 / look-alike false 0.10; B knowledge 0.88 / cve 0.94 / look-alike false 0.11.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from secai_gateway.agent.hooks import classify_chat_grounding

SETS = Path(__file__).resolve().parents[1] / "evals" / "core_eval"


def _score(name: str) -> dict[str, float]:
    rows = [json.loads(l) for l in (SETS / name).read_text(encoding="utf-8").splitlines() if l.strip()]
    hit = {"knowledge": [0, 0], "cve_search": [0, 0], "lookalike": [0, 0]}
    for r in rows:
        plan = classify_chat_grounding(r["query"], True, True, True)
        knowledge = plan.route in ("concept_kb", "taxonomy")
        cve = plan.route in ("cve_search", "id_lookup", "entity")
        if r["label"] == "knowledge":
            hit["knowledge"][0] += knowledge or plan.knowledge; hit["knowledge"][1] += 1
        elif r["label"] == "cve_search":
            hit["cve_search"][0] += cve; hit["cve_search"][1] += 1
        elif r["kind"] not in ("chitchat", "meta", "chit-chat", "id_lookup"):
            hit["lookalike"][0] += knowledge or cve; hit["lookalike"][1] += 1  # triggered = false trigger
    return {k: v[0] / v[1] for k, v in hit.items()}


@pytest.mark.parametrize("name", ["routing_eval_set_a.jsonl", "routing_eval_set_b.jsonl"])
def test_routing_floors(name):
    s = _score(name)
    assert s["knowledge"] >= 0.85, s
    assert s["cve_search"] >= 0.90, s
    assert s["lookalike"] <= 0.15, s
