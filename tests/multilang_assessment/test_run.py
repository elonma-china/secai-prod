from datetime import datetime, timedelta, timezone

import httpx
import pytest

from evals.multilang.run import RunError, call_one, plan_run, require_live_permission, verify_model_identity

from .test_manifest import row


REVISION = "7e7e6718ce268d2b00adc6e3ea43fcd33452f516"


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
    with pytest.raises(RunError, match="sealed TEST"):
        require_live_permission(True, inventory(), lease, "model-only", 1, now, split="TEST")


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
            "choices": [{"message": {"content": "こんにちは。"}, "finish_reason": "stop"}],
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
