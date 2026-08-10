from __future__ import annotations

import json

from linch.events import HookEventRecord, PartialAssistantEvent

from secai_gateway.api.sse import event_to_sse


def _decode(frame: bytes) -> tuple[str, dict]:
    text = frame.decode()
    event_line, data_line = text.strip("\n").split("\n", 1)
    event_name = event_line.removeprefix("event: ")
    payload = json.loads(data_line.removeprefix("data: "))
    return event_name, payload


def test_force_continue_hook_emits_regenerate() -> None:
    # RequireSearchBeforeAnswerHook/RequireRunInstructionsHook (agent/hooks.py) both use
    # HookResult.force_continue() when the model answers before gathering evidence — the
    # client must be told to discard the rejected draft's `token` deltas already streamed.
    record = HookEventRecord(event="before_final_answer", hook="require_search_before_answer",
                              action="force_continue", reason="search_before_answer_unsatisfied")
    frame = event_to_sse(record)
    assert frame is not None
    name, payload = _decode(frame)
    assert name == "regenerate"
    assert payload == {"hook": "require_search_before_answer", "reason": "search_before_answer_unsatisfied"}


def test_retry_hook_emits_regenerate() -> None:
    record = HookEventRecord(event="pre_tool_use", hook="some_hook", action="retry", reason="bad_input")
    frame = event_to_sse(record)
    assert frame is not None
    name, _ = _decode(frame)
    assert name == "regenerate"


def test_non_regenerating_hook_actions_are_dropped() -> None:
    # "block"/"stop"/"resolve"/"mutate" don't discard already-streamed assistant text —
    # forwarding them as `regenerate` would make well-behaved clients clear text that was
    # never wrong.
    for action in ("block", "stop", "resolve", "mutate", "continue"):
        record = HookEventRecord(event="pre_tool_use", hook="some_hook", action=action, reason="")
        assert event_to_sse(record) is None


def test_partial_assistant_event_still_maps_to_token() -> None:
    event = PartialAssistantEvent(delta={"kind": "text", "text": "hello"})
    frame = event_to_sse(event)
    assert frame is not None
    name, payload = _decode(frame)
    assert name == "token"
    assert payload == {"delta": "hello"}
