"""Decision policy for the input classifier. Pure logic — no I/O, easy to unit-test.

Maps an :class:`InputVerdict` + settings to one :class:`Decision`. The route layer runs
the effect (short-circuit refusal vs. pass to the agent) and the audit log.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from .classifier import InputVerdict

# Off-topic refusal — MUST match prompts/base_system.md <scope_policy> verbatim so the
# deterministic gate and the LLM fallback speak with one voice.
REFUSE_OFFTOPIC_MSG = "Xin lỗi, câu hỏi này không thuộc phạm vi An toàn thông tin."
# Injection/jailbreak refusal — decline without echoing the attempted instruction.
REFUSE_INJECTION_MSG = (
    "Xin lỗi, yêu cầu này có dấu hiệu can thiệp vào chỉ thị hệ thống nên không được xử lý.\n\n"
    "Bạn cần IAS.AI hỗ trợ vấn đề An toàn thông tin nào không?"
)
# Bare greeting (only used when shortcircuit_chitchat is explicitly enabled).
GREET_MSG = "Chào bạn, tôi là IAS.AI. Bạn cần hỗ trợ vấn đề An toàn thông tin nào?"


def refusal_messages() -> dict[str, str]:
    return {
        "offtopic": REFUSE_OFFTOPIC_MSG,
        "injection": REFUSE_INJECTION_MSG,
        "chitchat": GREET_MSG,
    }


class Action(str, Enum):
    PASS = "pass"                         # let the agent answer
    BLOCK_INJECTION = "block_injection"   # short-circuit: injection refusal
    REFUSE_OFFTOPIC = "refuse_offtopic"   # short-circuit: scope_policy refusal
    GREET_CHITCHAT = "greet_chitchat"     # short-circuit: bare greeting (opt-in)


@dataclass(frozen=True, slots=True)
class Decision:
    action: Action
    message: str          # user-facing text when short-circuiting ("" for PASS)
    label: str            # taxonomy label for logs/metadata
    pii: bool = False     # soft flag surfaced even on PASS

    @property
    def short_circuit(self) -> bool:
        return self.action is not Action.PASS


def decide(verdict: InputVerdict, settings: Any) -> Decision:
    """Choose an action from a verdict. Fail-open: an ``unknown`` verdict always passes."""
    if not verdict.ok:
        return Decision(Action.PASS, "", "unknown")

    # 1) Injection/jailbreak — highest priority, orthogonal to intent.
    if verdict.injection and bool(getattr(settings, "input_classifier_block_injection", True)):
        return Decision(Action.BLOCK_INJECTION, REFUSE_INJECTION_MSG, "injection", pii=verdict.pii)

    # 2) Off-topic — deterministic scope refusal (thresholded, so borderline stays PASS).
    off_p = float(verdict.scores.get("offtopic", 0.0))
    off_thr = float(getattr(settings, "input_classifier_offtopic_threshold", 0.5) or 0.5)
    if (
        verdict.intent == "offtopic"
        and off_p >= off_thr
        and bool(getattr(settings, "input_classifier_block_offtopic", True))
    ):
        return Decision(Action.REFUSE_OFFTOPIC, REFUSE_OFFTOPIC_MSG, "offtopic", pii=verdict.pii)

    # 3) Chitchat — opt-in only (default OFF): the LLM <interaction_policy> already
    #    handles greetings, and a false positive here would swallow a real question.
    if verdict.intent == "chitchat" and bool(
            getattr(settings, "input_classifier_shortcircuit_chitchat", False)):
        return Decision(Action.GREET_CHITCHAT, GREET_MSG, "chitchat", pii=verdict.pii)

    # 4) PII is a soft flag — never short-circuits (require_public_input already hard-fails
    #    on secrets). Surface it so the caller can log/redact.
    return Decision(Action.PASS, "", verdict.intent, pii=verdict.pii)
