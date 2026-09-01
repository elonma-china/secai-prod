"""Model-based input classification for chat sanitization.

A lightweight guard that runs BEFORE the agent on the chat path. It embeds the user
prompt with the existing security embedding endpoint (``retrieval.embedding.embed``,
reusing the deployed ``sec-embedding`` model) and applies a tiny numpy linear head to
assign a combined taxonomy:

  * intent  (softmax, mutually exclusive): ``security`` | ``chitchat`` | ``offtopic``
  * risk    (independent sigmoids):        ``injection`` (prompt-injection/jailbreak), ``pii``

The head is trained offline (``scripts/train_sanitize_head.py``) and shipped as a small
``assets/head.npz`` + metadata. Runtime stays numpy-only — no torch/transformers.

Everything here is FAIL-OPEN: a missing/untrained head, an embed-model mismatch, a
dimension mismatch, or any embedding error yields an ``unknown`` verdict that the gate
treats as PASS. The classifier can only ever ADD a block, never take chat down.
"""
from __future__ import annotations

from .cascade import judge_injection
from .classifier import (
    ALL_LABELS,
    INTENT_LABELS,
    RISK_LABELS,
    InputVerdict,
    classify_input,
    load_head,
)
from .gate import Action, Decision, decide, refusal_messages

__all__ = [
    "ALL_LABELS",
    "INTENT_LABELS",
    "RISK_LABELS",
    "InputVerdict",
    "classify_input",
    "load_head",
    "judge_injection",
    "Action",
    "Decision",
    "decide",
    "refusal_messages",
]
