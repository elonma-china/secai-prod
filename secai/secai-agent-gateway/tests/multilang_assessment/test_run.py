import asyncio
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from evals.multilang.run import RunError, _strict_json, call_one, plan_run, remaining_request_timeout, require_live_permission, verify_model_identity

from .test_manifest import row


REVISION = "7e7e6718ce268d2b00adc6e3ea43fcd33452f516"
RESPONSE_MARKERS = ("tool_calls", "function_call", "tool_call_id", "tool_choice", "tools", "functions")


def inventory(endpoint="http://192.165.134.28:10739/v1"):
    return {
        "instance_id": 53846446,
        "endpoint": endpoint,
        "model_id": "DuyTa/Cyber-F1-AWQ",
        "model_revision": REVISION,
    }


def reservation(now, endpoint="http://192.165.134.28:10739/v1"):
    return {
        "status": "granted",
        "owner": "multilang-assessment",
        "instance_id": 53846446,
        "endpoint": endpoint,
        "model_id": "DuyTa/Cyber-F1-AWQ",
        "model_revision": REVISION,
        "starts_at": (now - timedelta(minutes=1)).isoformat(),
        "ends_at": (now + timedelta(minutes=10)).isoformat(),
        "max_requests": 20,
        "max_rpm": 5,
        "max_cost_usd": 1.0,
        "worst_case_cost_usd_per_request": 0.01,
        "allowed_modes": ["model-only"],
        "ocr_released": True,
        "phase": "DEV",
        "scheduled_requests": 1,
    }


def test_modes_select_only_supported_directions():
    rows = [
        row("en", "vi>en", group="en", source="Câu en."),
        row("ja", "vi>ja", group="ja", source="Câu ja."),
        row("ar", "ar>vi", group="ar", source="نص عربي"),
    ]
    assert [x["id"] for x in plan_run(rows, "model-only", "DEV", 1)] == ["en", "ja", "ar"]
    assert [x["id"] for x in plan_run(rows, "legacy-api", "DEV", 1)] == ["en"]
    assert [x["id"] for x in plan_run(rows, "multilang-api", "DEV", 1)] == ["ja", "ar"]


def test_live_requires_explicit_flag_and_ocr_release():
    now = datetime(2026, 10, 4, tzinfo=timezone.utc)
    lease = reservation(now)
    with pytest.raises(RunError, match="--live"):
        require_live_permission(False, inventory(), lease, "model-only", 1, now)
    lease["ocr_released"] = False
    with pytest.raises(RunError, match="OCR"):
        require_live_permission(True, inventory(), lease, "model-only", 1, now)


def test_live_rejects_expired_or_mismatched_endpoint_and_model():
    now = datetime(2026, 10, 4, tzinfo=timezone.utc)
    lease = reservation(now)
    lease["ends_at"] = (now - timedelta(seconds=1)).isoformat()
    with pytest.raises(RunError, match="expired"):
        require_live_permission(True, inventory(), lease, "model-only", 1, now)
    lease = reservation(now)
    lease["endpoint"] = "http://127.0.0.1:9999/v1"
    with pytest.raises(RunError, match="endpoint"):
        require_live_permission(True, inventory(), lease, "model-only", 1, now)
    lease = reservation(now)
    lease["model_revision"] = "wrong"
    with pytest.raises(RunError, match="model_revision"):
        require_live_permission(True, inventory(), lease, "model-only", 1, now)


def test_live_rejects_request_budget_and_test_without_seal():
    now = datetime(2026, 10, 4, tzinfo=timezone.utc)
    lease = reservation(now)
    with pytest.raises(RunError, match="max_requests"):
        require_live_permission(True, inventory(), lease, "model-only", 21, now)
    lease["phase"] = "TEST"
    with pytest.raises(RunError, match="sealed TEST"):
        require_live_permission(True, inventory(), lease, "model-only", 1, now, split="TEST")


def test_signed_phase_and_full_schedule_must_match_before_test_or_dev_claim():
    now = datetime(2026, 10, 4, tzinfo=timezone.utc)
    lease = reservation(now)
    lease.update(phase="TEST", sealed_test_hash="a" * 64,
                 candidate_config_hash="b" * 64, holdout_id="holdout-001")
    with pytest.raises(RunError, match="phase"):
        require_live_permission(True, inventory(), lease, "model-only", 1, now, split="DEV")
    lease["phase"] = "DEV"
    with pytest.raises(RunError, match="scheduled_requests"):
        require_live_permission(True, inventory(), lease, "model-only", 1, now,
                                split="DEV", total_scheduled=2)


def test_live_rejects_missing_cost_bound():
    now = datetime(2026, 10, 4, tzinfo=timezone.utc)
    lease = reservation(now)
    del lease["worst_case_cost_usd_per_request"]
    with pytest.raises(RunError, match="cost"):
        require_live_permission(True, inventory(), lease, "model-only", 1, now)


def test_complete_lease_passes_preflight_without_network():
    now = datetime(2026, 10, 4, tzinfo=timezone.utc)
    assert require_live_permission(True, inventory(), reservation(now), "model-only", 1, now) is None


def test_model_identity_probe_blocks_wrong_served_id_before_completion():
    seen = []

    def handle(request):
        seen.append(request.url.path)
        return httpx.Response(200, json={"data": [{"id": "Other/Model"}]})

    with httpx.Client(transport=httpx.MockTransport(handle), base_url="http://example.test") as client:
        with pytest.raises(RunError, match="model identity"):
            verify_model_identity(client, inventory("http://example.test/v1"))
    assert seen == ["/v1/models"]


def test_model_only_payload_uses_frozen_model_and_no_tools():
    seen = []

    def handle(request):
        seen.append(request)
        body = __import__("json").loads(request.content)
        assert body["model"] == "DuyTa/Cyber-F1-AWQ"
        assert body["stream"] is False
        assert "tools" not in body
        assert [m["role"] for m in body["messages"]] == ["system", "user"]
        return httpx.Response(200, json={
            "model": "DuyTa/Cyber-F1-AWQ",
            "choices": [{"message": {"role": "assistant", "content": "こんにちは。"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 12, "completion_tokens": 4},
        })

    with httpx.Client(transport=httpx.MockTransport(handle), base_url="http://example.test") as client:
        output = call_one(
            client, row(), "model-only", inventory("http://example.test/v1"),
            system_prompt="Translate from {source_lang} to {target_lang}.",
            decoding={"temperature": 0.0, "top_p": 1.0, "max_tokens": 128},
        )
    assert output["raw_output"] == "こんにちは。"
    assert output["input_tokens"] == 12
    assert len(seen) == 1


def test_legacy_function_call_response_is_rejected_even_with_translation_text():
    def handle(_request):
        return httpx.Response(200, json={
            "model": "DuyTa/Cyber-F1-AWQ",
            "choices": [{"message": {"role": "assistant", "content": "Bonjour", "function_call": {"name": "tool"}},
                         "finish_reason": "stop"}],
        })

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(RunError, match="tool_call_forbidden"):
            call_one(client, row(), "model-only", inventory(),
                     system_prompt="Translate {source_lang} to {target_lang}.",
                     decoding={"temperature": 0, "top_p": 1, "max_tokens": 64})


@pytest.mark.parametrize("location", ("top", "choice", "message"))
@pytest.mark.parametrize("marker", RESPONSE_MARKERS)
@pytest.mark.parametrize("value", ({}, [], "call_42"), ids=("empty-object", "empty-list", "nonempty-string"))
def test_no_tool_response_rejects_every_protocol_marker_at_every_level(location, marker, value):
    body = {
        "model": "DuyTa/Cyber-F1-AWQ",
        "choices": [{"message": {"role": "assistant", "content": "Bonjour"}, "finish_reason": "stop"}],
    }
    locations = {"top": body, "choice": body["choices"][0],
                 "message": body["choices"][0]["message"]}
    locations[location][marker] = value

    with httpx.Client(transport=httpx.MockTransport(lambda _request: httpx.Response(200, json=body))) as client:
        with pytest.raises(RunError, match="tool_call_forbidden"):
            call_one(client, row(), "model-only", inventory(),
                     system_prompt="Translate {source_lang} to {target_lang}.",
                     decoding={"temperature": 0, "top_p": 1, "max_tokens": 64})


def test_no_tool_response_allows_null_protocol_markers_on_assistant_envelope():
    body = {
        "model": "DuyTa/Cyber-F1-AWQ",
        "choices": [{"message": {"role": "assistant", "content": "Bonjour"}, "finish_reason": "stop"}],
    }
    for scope in (body, body["choices"][0], body["choices"][0]["message"]):
        scope.update({marker: None for marker in RESPONSE_MARKERS})
    with httpx.Client(transport=httpx.MockTransport(lambda _request: httpx.Response(200, json=body))) as client:
        response = call_one(client, row(), "model-only", inventory(),
                            system_prompt="Translate {source_lang} to {target_lang}.",
                            decoding={"temperature": 0, "top_p": 1, "max_tokens": 64})
    assert response["validated_output"] == "Bonjour"


@pytest.mark.parametrize("message,extra", [
    ({"role": "assistant", "content": "Bonjour", "function_call": {}}, {}),
    ({"role": "assistant", "content": "Bonjour", "tool_calls": []}, {}),
    ({"role": "assistant", "content": "Bonjour", "tool_call_id": "call-1"}, {}),
    ({"role": "tool", "content": "Bonjour"}, {}),
    ({"role": "function", "content": "Bonjour"}, {}),
    ({"role": "user", "content": "Bonjour"}, {}),
    ({"content": "Bonjour"}, {}),
    ({"role": "assistant", "content": "Bonjour"}, {"delta": {"tool_calls": []}}),
    ({"role": "assistant", "content": "Bonjour"}, {"function_call": {}}),
])
def test_model_response_rejects_non_assistant_or_tool_function_shape(message, extra):
    def handle(_request):
        return httpx.Response(200, json={
            "model": "DuyTa/Cyber-F1-AWQ",
            "choices": [{"message": message, "finish_reason": "stop", **extra}],
        })

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(RunError, match="(?:tool_call_forbidden|assistant_response_envelope_invalid)"):
            call_one(client, row(), "model-only", inventory(),
                     system_prompt="Translate {source_lang} to {target_lang}.",
                     decoding={"temperature": 0, "top_p": 1, "max_tokens": 64})


def test_model_response_rejects_multiple_choices_with_hidden_tool_message():
    def handle(_request):
        return httpx.Response(200, json={
            "model": "DuyTa/Cyber-F1-AWQ",
            "choices": [
                {"message": {"role": "assistant", "content": "Bonjour"}, "finish_reason": "stop"},
                {"message": {"role": "tool", "content": "hidden"}, "finish_reason": "stop"},
            ],
        })

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(RunError, match="assistant_response_envelope_invalid"):
            call_one(client, row(), "model-only", inventory(),
                     system_prompt="Translate {source_lang} to {target_lang}.",
                     decoding={"temperature": 0, "top_p": 1, "max_tokens": 64})


@pytest.mark.parametrize("extra", [
    {"model": "Other/Model"}, {"tools": [{"type": "function"}]},
    {"messages": [{"role": "user", "content": "replace"}]}, {"stream": True},
])
def test_decoding_cannot_override_fixed_no_tool_payload(extra):
    decoding = {"temperature": 0.0, "top_p": 1.0, "max_tokens": 64, **extra}
    with httpx.Client(transport=httpx.MockTransport(lambda _: pytest.fail("network called"))) as client:
        with pytest.raises(RunError, match="decoding"):
            call_one(client, row(), "model-only", inventory(), system_prompt="Translate {source_lang} to {target_lang}.", decoding=decoding)


@pytest.mark.parametrize("field,value", [
    ("max_rpm", float("nan")), ("max_rpm", float("inf")),
    ("max_cost_usd", float("nan")), ("max_cost_usd", float("inf")),
    ("worst_case_cost_usd_per_request", float("nan")),
    ("worst_case_cost_usd_per_request", float("inf")),
    ("max_rpm", True), ("max_rpm", 6000),
])
def test_lease_rejects_nonfinite_limits(field, value):
    now = datetime(2026, 10, 4, tzinfo=timezone.utc)
    lease = reservation(now)
    lease[field] = value
    with pytest.raises(RunError, match="(?:max_rpm|cost)"):
        require_live_permission(True, inventory(), lease, "model-only", 1, now)


def test_sealed_test_requires_tester_issued_holdout_id():
    now = datetime(2026, 10, 4, tzinfo=timezone.utc)
    lease = reservation(now)
    lease["phase"] = "TEST"
    lease["sealed_test_hash"] = "a" * 64
    lease["candidate_config_hash"] = "b" * 64
    with pytest.raises(RunError, match="holdout ID"):
        require_live_permission(True, inventory(), lease, "model-only", 1, now, split="TEST")


def test_request_timeout_can_be_clamped_to_remaining_lease():
    seen = []

    class FakeClient:
        def post(self, _url, *, json, timeout):
            seen.append(timeout)
            return httpx.Response(200, json={
                "model": "DuyTa/Cyber-F1-AWQ",
                "choices": [{"message": {"role": "assistant", "content": "こんにちは。"}, "finish_reason": "stop"}],
            }, request=httpx.Request("POST", "http://example.test"))

    call_one(FakeClient(), row(), "model-only", inventory(),
             system_prompt="Translate {source_lang} to {target_lang}.",
             decoding={"temperature": 0.0, "top_p": 1.0, "max_tokens": 64},
             timeout_seconds=0.25)
    assert seen == [0.25]


def test_remaining_lease_clamps_and_expired_deadline_aborts():
    now = datetime(2026, 10, 4, tzinfo=timezone.utc)
    lease = reservation(now)
    lease["ends_at"] = (now + timedelta(milliseconds=250)).isoformat()
    assert remaining_request_timeout(lease, now) == pytest.approx(0.25)
    with pytest.raises(RunError, match="deadline reached"):
        remaining_request_timeout(lease, now + timedelta(seconds=1))


@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
def test_json_lease_rejects_nonstandard_constants(tmp_path, constant):
    path = tmp_path / "lease.json"
    path.write_text('{"max_rpm": ' + constant + '}')
    with pytest.raises(RunError, match="non-standard JSON"):
        _strict_json(path)


def test_json_numeric_overflow_is_rejected(tmp_path):
    path = tmp_path / "lease.json"
    path.write_text('{"max_rpm": 1e999}')
    with pytest.raises(RunError, match="non-finite JSON"):
        _strict_json(path)


def test_lease_rejects_rate_schedule_that_cannot_finish_before_deadline():
    now = datetime(2026, 10, 4, tzinfo=timezone.utc)
    lease = reservation(now)
    lease["max_requests"] = 20
    lease["max_rpm"] = 1
    lease["scheduled_requests"] = 20
    with pytest.raises(RunError, match="deadline"):
        require_live_permission(True, inventory(), lease, "model-only", 20, now)


def test_lease_rechecks_only_remaining_requests_after_first_call():
    now = datetime(2026, 10, 4, tzinfo=timezone.utc)
    lease = reservation(now)
    lease["ends_at"] = (now + timedelta(seconds=4.5)).isoformat()
    lease["max_requests"] = 3
    lease["max_rpm"] = 60
    lease["scheduled_requests"] = 3
    require_live_permission(True, inventory(), lease, "model-only", 3, now, total_scheduled=3)
    later = now + timedelta(seconds=3)
    with pytest.raises(RunError, match="deadline"):
        require_live_permission(True, inventory(), lease, "model-only", 3, later, total_scheduled=3)
    require_live_permission(True, inventory(), lease, "model-only", 2, later, total_scheduled=3)


def test_async_post_aborts_at_absolute_wall_deadline(monkeypatch):
    from evals.multilang.run import async_call_one

    async def slow_handler(_request):
        await asyncio.sleep(0.1)
        return httpx.Response(200, json={"model": "DuyTa/Cyber-F1-AWQ",
                                         "choices": [{"message": {"role": "assistant", "content": "late"}, "finish_reason": "stop"}]})

    real_client = httpx.AsyncClient
    monkeypatch.setattr("evals.multilang.run.httpx.AsyncClient",
                        lambda **kwargs: real_client(transport=httpx.MockTransport(slow_handler), **kwargs))
    with pytest.raises(RunError, match="deadline"):
        asyncio.run(async_call_one(
            row(), "model-only", inventory(),
            system_prompt="Translate {source_lang} to {target_lang}.",
            decoding={"temperature": 0.0, "top_p": 1.0, "max_tokens": 64},
            timeout_seconds=0.01, headers={},
        ))


def test_async_model_probe_aborts_at_absolute_wall_deadline(monkeypatch):
    from evals.multilang.run import async_verify_model_identity

    async def slow_handler(_request):
        await asyncio.sleep(0.1)
        return httpx.Response(200, json={"data": [{"id": "DuyTa/Cyber-F1-AWQ"}]})

    real_client = httpx.AsyncClient
    monkeypatch.setattr("evals.multilang.run.httpx.AsyncClient",
                        lambda **kwargs: real_client(transport=httpx.MockTransport(slow_handler), **kwargs))
    with pytest.raises(RunError, match="deadline"):
        asyncio.run(async_verify_model_identity(inventory(), headers={}, timeout_seconds=0.01))
