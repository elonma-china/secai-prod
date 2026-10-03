"""Serial, explicitly leased translation assessment against the incumbent DuyTa."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse

import httpx

from .manifest import validate_manifest


MODES = frozenset({"model-only", "legacy-api", "multilang-api"})
MODEL_ID = "DuyTa/Cyber-F1-AWQ"
INSTANCE_ID = 53846446


class RunError(ValueError):
    """Run configuration or model evidence fails closed."""


def plan_run(rows: Iterable[dict[str, Any]], mode: str, split: str, rounds: int) -> list[dict[str, Any]]:
    records = list(rows)
    validate_manifest(records, require_directions=False)
    if mode not in MODES or split not in {"DEV", "TEST"} or not isinstance(rounds, int) or rounds < 1:
        raise RunError("invalid mode, split or rounds")
    selected = [
        row for row in records if row["split"] == split and
        (mode == "model-only" or
         (mode == "legacy-api" and row["direction"] in {"vi>en", "en>vi"}) or
         (mode == "multilang-api" and row["direction"] not in {"vi>en", "en>vi"}))
    ]
    return [dict(row, round=turn) for turn in range(1, rounds + 1) for row in selected]


def require_live_permission(
    live: bool, inventory: dict[str, Any], reservation: dict[str, Any] | None,
    mode: str, requested: int, now: datetime, *, split: str = "DEV",
) -> None:
    if not live:
        raise RunError("explicit --live is required for network calls")
    if not reservation or reservation.get("status") != "granted" or reservation.get("owner") != "multilang-assessment":
        raise RunError("a granted multilang reservation is required")
    if reservation.get("ocr_released") is not True:
        raise RunError("OCR exclusive GPU slot has not been released")
    if inventory.get("instance_id") != INSTANCE_ID or reservation.get("instance_id") != INSTANCE_ID:
        raise RunError("wrong Vast instance_id")
    if inventory.get("model_id") != MODEL_ID or reservation.get("model_id") != MODEL_ID:
        raise RunError("model identity mismatch")
    revision = inventory.get("model_revision")
    if not isinstance(revision, str) or len(revision) != 40 or reservation.get("model_revision") != revision:
        raise RunError("model_revision mismatch")
    endpoint = inventory.get("endpoint")
    parsed = urlparse(endpoint) if isinstance(endpoint, str) else None
    if not parsed or parsed.scheme not in {"http", "https"} or not parsed.hostname or reservation.get("endpoint") != endpoint:
        raise RunError("endpoint mismatch")
    if mode not in MODES or mode not in reservation.get("allowed_modes", []):
        raise RunError("mode is not included in reservation")
    try:
        start = datetime.fromisoformat(reservation["starts_at"])
        end = datetime.fromisoformat(reservation["ends_at"])
        if start.tzinfo is None or end.tzinfo is None or now.tzinfo is None or not start <= now < end:
            raise ValueError
    except (KeyError, TypeError, ValueError) as exc:
        raise RunError("reservation expired or invalid") from exc
    max_requests = reservation.get("max_requests")
    if not isinstance(max_requests, int) or requested < 1 or requested > max_requests:
        raise RunError("max_requests exceeded")
    if not isinstance(reservation.get("max_rpm"), (int, float)) or reservation["max_rpm"] <= 0:
        raise RunError("max_rpm is required")
    if not isinstance(reservation.get("max_cost_usd"), (int, float)) or reservation["max_cost_usd"] < 0:
        raise RunError("max_cost_usd is required")
    worst_case = reservation.get("worst_case_cost_usd_per_request")
    if not isinstance(worst_case, (int, float)) or worst_case <= 0 or requested * worst_case > reservation["max_cost_usd"]:
        raise RunError("worst-case cost exceeds reservation max_cost_usd")
    if split == "TEST" and (
        not reservation.get("sealed_test_hash") or
        not reservation.get("candidate_config_hash")
    ):
        raise RunError("sealed TEST and candidate config hashes are required")


def verify_model_identity(client: httpx.Client, inventory: dict[str, Any]) -> None:
    endpoint = inventory["endpoint"].rstrip("/")
    try:
        response = client.get(endpoint + "/models", timeout=5)
        response.raise_for_status()
        ids = [item.get("id") for item in response.json().get("data", [])]
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
        raise RunError("model identity probe failed") from exc
    if ids != [inventory["model_id"]]:
        raise RunError("model identity differs from frozen inventory")


def _pair(sample: dict[str, Any]) -> tuple[str, str]:
    return tuple(sample["direction"].split(">", 1))  # type: ignore[return-value]


def call_one(
    client: httpx.Client, sample: dict[str, Any], mode: str,
    inventory: dict[str, Any], *, system_prompt: str = "",
    decoding: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if mode not in MODES or inventory.get("model_id") != MODEL_ID:
        raise RunError("unsupported mode or model identity")
    source_lang, target_lang = _pair(sample)
    endpoint = inventory["endpoint"].rstrip("/")
    if mode == "model-only":
        if not system_prompt or not decoding:
            raise RunError("frozen prompt and decoding are required for model-only")
        system = system_prompt.format_map({"source_lang": source_lang, "target_lang": target_lang})
        payload = {
            "model": MODEL_ID,
            "stream": False,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": sample["source"]},
            ],
            **decoding,
        }
        url = endpoint + "/chat/completions"
    elif mode == "legacy-api":
        if sample["direction"] not in {"vi>en", "en>vi"}:
            raise RunError("legacy API assessment only supports en/vi control")
        payload = {"text": sample["source"], "source_lang": source_lang, "target_lang": target_lang}
        url = endpoint + "/ai/translate"
    else:
        if sample["direction"] in {"vi>en", "en>vi"}:
            raise RunError("multilang API does not accept en/vi")
        payload = {"text": sample["source"], "source_lang": source_lang, "target_lang": target_lang}
        url = endpoint + "/ai/v3/translate"
    try:
        response = client.post(url, json=payload, timeout=120)
        response.raise_for_status()
        body = response.json()
        if mode == "model-only":
            if body.get("model") != MODEL_ID:
                raise RunError("response model identity mismatch")
            choice = body["choices"][0]
            if choice.get("message", {}).get("tool_calls") or choice.get("finish_reason") != "stop":
                raise RunError("unexpected tool call or truncated completion")
            output = choice["message"]["content"]
            usage = body.get("usage") or {}
            input_tokens, output_tokens = usage.get("prompt_tokens"), usage.get("completion_tokens")
        else:
            output = body["translated_text"]
            input_tokens = output_tokens = None
        if not isinstance(output, str) or not output.strip():
            raise RunError("empty translation")
    except httpx.HTTPStatusError as exc:
        raise RunError(f"HTTP_{exc.response.status_code}") from exc
    except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as exc:
        if isinstance(exc, RunError):
            raise
        raise RunError(type(exc).__name__) from exc
    return {
        "raw_output": output,
        "validated_output": output,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--mode", choices=sorted(MODES), required=True)
    parser.add_argument("--split", choices=("DEV", "TEST"), required=True)
    parser.add_argument("--rounds", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--inventory", type=Path)
    parser.add_argument("--reservation", type=Path)
    parser.add_argument("--prompt", type=Path)
    parser.add_argument("--decoding", type=Path)
    parser.add_argument("--live", action="store_true")
    parsed = parser.parse_args(argv)
    from .validate_corpus import load_jsonl

    manifest = load_jsonl(parsed.manifest)
    summary = validate_manifest(manifest, require_directions=False)
    schedule = plan_run(manifest, parsed.mode, parsed.split, parsed.rounds)
    if not schedule:
        raise RunError("no scheduled samples for selected mode and split")
    if not parsed.live:
        print(json.dumps({
            "live": False,
            "mode": parsed.mode,
            "split": parsed.split,
            "rounds": parsed.rounds,
            "scheduled_requests": len(schedule),
            "unique_sources": len({row["source_group"] for row in schedule}),
            "data_hash": summary.data_hash,
        }, ensure_ascii=False, sort_keys=True))
        return 0
    if parsed.inventory is None:
        raise RunError("frozen inventory file is required for --live")
    if parsed.reservation is None:
        raise RunError("shared GPU reservation file is required for --live")
    inventory = json.loads(parsed.inventory.read_text())
    lease = json.loads(parsed.reservation.read_text())
    now = datetime.now(timezone.utc)
    require_live_permission(True, inventory, lease, parsed.mode, len(schedule), now, split=parsed.split)
    if parsed.mode == "model-only" and inventory.get("endpoint") != "http://192.165.134.28:10739/v1":
        raise RunError("model-only endpoint is not the verified incumbent Vast port")
    if parsed.mode != "model-only" and inventory.get("gateway_model_attested") is not True:
        raise RunError("gateway model configuration lacks independent attestation")
    if any(row.get("data_classification") not in {"public", "anonymized"} for row in schedule):
        raise RunError("live corpus requires public/anonymized data_classification")
    if parsed.output.exists():
        raise RunError("result output already exists; never overwrite a run")
    if parsed.mode == "model-only":
        if not parsed.prompt or not parsed.decoding:
            raise RunError("frozen --prompt and --decoding files are required")
        system_prompt = parsed.prompt.read_text(encoding="utf-8")
        decoding = json.loads(parsed.decoding.read_text(encoding="utf-8"))
        if not isinstance(decoding, dict) or not {"temperature", "top_p", "max_tokens"}.issubset(decoding):
            raise RunError("decoding is incomplete")
    else:
        system_prompt, decoding = "", {}
    prompt_hash = hashlib.sha256(system_prompt.encode()).hexdigest()
    config_payload = json.dumps({
        "mode": parsed.mode, "model_id": MODEL_ID,
        "model_revision": inventory["model_revision"],
        "endpoint": inventory["endpoint"], "decoding": decoding,
        "prompt_hash": prompt_hash,
    }, sort_keys=True, separators=(",", ":"))
    config_hash = hashlib.sha256(config_payload.encode()).hexdigest()
    if parsed.split == "TEST" and (
        lease.get("sealed_test_hash") != summary.data_hash or
        lease.get("candidate_config_hash") != config_hash
    ):
        raise RunError("sealed TEST or candidate config hash differs from frozen lease")
    worst_case = lease.get("worst_case_cost_usd_per_request")
    if not isinstance(worst_case, (int, float)) or worst_case <= 0 or len(schedule) * worst_case > lease["max_cost_usd"]:
        raise RunError("worst-case cost exceeds reservation max_cost_usd")
    lock_path = parsed.reservation.with_suffix(parsed.reservation.suffix + ".lock")
    with lock_path.open("a+") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RunError("shared assessment lock is held") from exc
        headers = {}
        token = os.environ.get("MULTILANG_ASSESSMENT_API_KEY")
        if token:
            headers["Authorization"] = "Bearer " + token
        with httpx.Client(headers=headers) as client:
            if parsed.mode == "model-only":
                verify_model_identity(client, inventory)
            next_request_at = time.monotonic()
            with parsed.output.open("x", encoding="utf-8") as output:
                for sample in schedule:
                    current_lease = json.loads(parsed.reservation.read_text())
                    require_live_permission(
                        True, inventory, current_lease, parsed.mode, len(schedule),
                        datetime.now(timezone.utc), split=parsed.split,
                    )
                    wait = next_request_at - time.monotonic()
                    if wait > 0:
                        time.sleep(wait)
                    # A lease can expire or be revoked while rate limiting sleeps.
                    current_lease = json.loads(parsed.reservation.read_text())
                    require_live_permission(
                        True, inventory, current_lease, parsed.mode, len(schedule),
                        datetime.now(timezone.utc), split=parsed.split,
                    )
                    started = time.monotonic()
                    started_utc = datetime.now(timezone.utc).isoformat()
                    error = None
                    response = {}
                    try:
                        response = call_one(
                            client, sample, parsed.mode, inventory,
                            system_prompt=system_prompt, decoding=decoding,
                        )
                    except RunError as exc:
                        error = str(exc)
                    elapsed_ms = (time.monotonic() - started) * 1000
                    completed_utc = datetime.now(timezone.utc).isoformat()
                    next_request_at = time.monotonic() + 60 / current_lease["max_rpm"]
                    usage_in, usage_out = response.get("input_tokens"), response.get("output_tokens")
                    input_rate = inventory.get("usd_per_million_input_tokens")
                    output_rate = inventory.get("usd_per_million_output_tokens")
                    cost = (
                        (usage_in * input_rate + usage_out * output_rate) / 1_000_000
                        if all(isinstance(v, (int, float)) for v in (usage_in, usage_out, input_rate, output_rate))
                        else None
                    )
                    record = {
                        "sample_id": sample["id"], "round": sample["round"],
                        "phase": parsed.split, "mode": parsed.mode,
                        "model_id": MODEL_ID, "model_revision": inventory["model_revision"],
                        "prompt_hash": prompt_hash, "config_hash": config_hash,
                        "data_hash": summary.data_hash,
                        "raw_output": response.get("raw_output"),
                        "validated_output": response.get("validated_output"),
                        "latency_ms": round(elapsed_ms, 3),
                        "started_at_utc": started_utc,
                        "completed_at_utc": completed_utc,
                        "input_tokens": usage_in, "output_tokens": usage_out,
                        "cost_usd": cost, "error": error,
                    }
                    output.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
                    output.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
