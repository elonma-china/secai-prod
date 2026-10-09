"""Frozen FLORES DEV client for the isolated Hy-MT2-7B Q4 full-GPU diagnostic.

The model server runs on the Vast host under gpu_specialist_server.py. This
client only connects through a root-owned SSH loopback tunnel; it never loads
a model or starts a GPU process. Every selected row keeps a denominator slot.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from collections import Counter
from pathlib import Path

import httpx

from evals.multilang.cpu_specialist import (
    CORPUS, DIRECTIONS, EVIDENCE_DIR, MIN_AVAILABLE_DISK, MIN_AVAILABLE_RAM,
    resources, schedule, utc,
)
from evals.multilang.cpu_specialist_hymt2_7b import (
    BASE_CONFIG_SHA256, BASE_GENERATION_CONFIG_SHA256, BASE_REVISION,
    BOS_ID, CHAT_TEMPLATE_SHA256, DECODING, EOG_IDS, GGUF_DECLARED_EOS_ID,
    MAX_INPUT_TOKENS, MAX_OUTPUT_TOKENS, PROMPT_TEMPLATE, TARGET_NAMES,
    probe_eog, requested_stop_reason, translate,
)
from evals.multilang.gpu_specialist_server import (
    COMPAT_SHA256, DRIVER, EOG_OVERRIDE, GPU_UUID, LLAMA_COMMIT,
    MODEL_REVISION, MODEL_SHA256, OWNED_CAP_MIB, PORT,
    PRELOAD_FREE_MIB, SERVER_SHA256,
)
from evals.multilang.manifest import canonical_hash, validate_manifest

MODEL_ID = "tencent/Hy-MT2-7B-GGUF"
OUTPUT_DIR = EVIDENCE_DIR.parent / "gpu-hymt2-7b-q4"
REQUEST_TIMEOUT_SECONDS = 300
MAX_WHOLE_SECONDS = 6 * 60 * 60


def config(rows: list[dict], selected: list[dict], groups: int,
           whole_seconds: int) -> dict:
    return {
        "kind": "EXPLORATORY_GPU_Q4_DIAGNOSTIC_NOT_SEMANTIC_ACCEPTANCE",
        "model_id": MODEL_ID, "revision": MODEL_REVISION, "quantization": "Q4_K_M",
        "model_sha256": MODEL_SHA256, "llama_commit": LLAMA_COMMIT,
        "server_sha256": SERVER_SHA256, "compat_sha256": COMPAT_SHA256,
        "gpu_uuid": GPU_UUID, "gpu_driver": DRIVER,
        "owned_gpu_cap_mib": OWNED_CAP_MIB,
        "minimum_free_before_load_mib": PRELOAD_FREE_MIB,
        "gguf_bos_token_id": BOS_ID,
        "gguf_declared_eos_token_id": GGUF_DECLARED_EOS_ID,
        "official_terminal_eog_token_ids": EOG_IDS,
        "runtime_metadata_override": EOG_OVERRIDE,
        "official_base_revision": BASE_REVISION,
        "official_base_config_sha256": BASE_CONFIG_SHA256,
        "official_base_generation_config_sha256": BASE_GENERATION_CONFIG_SHA256,
        "gguf_chat_template_sha256": CHAT_TEMPLATE_SHA256,
        "corpus_sha256": hashlib.sha256(CORPUS.read_bytes()).hexdigest(),
        "corpus_canonical_hash": canonical_hash(rows),
        "selected_row_ids": [row["id"] for row in selected],
        "selected_source_groups": list(dict.fromkeys(row["source_group"] for row in selected)),
        "groups_per_direction": groups,
        "prompt_template": PROMPT_TEMPLATE,
        "prompt_template_sha256": hashlib.sha256(PROMPT_TEMPLATE.encode()).hexdigest(),
        "target_names": TARGET_NAMES,
        "prompt_flow": ["POST /apply-template (one user message)",
                        "POST /tokenize (no added BOS; parse special tokens)",
                        "POST /completion (prompt token IDs)"],
        "decoding": {**DECODING, "max_input_tokens": MAX_INPUT_TOKENS,
                     "max_output_tokens": MAX_OUTPUT_TOKENS, "n_ctx": 2048,
                     "batch_size": 512, "ubatch_size": 256,
                     "threads": 4, "parallel": 1, "n_gpu_layers": 99,
                     "fit": "off", "custom_stop_words": []},
        "success": "nonempty content; stop_type=eos; final token in official EOG IDs; truncated=false",
        "limits": {"request_seconds": REQUEST_TIMEOUT_SECONDS,
                   "whole_run_seconds": whole_seconds,
                   "minimum_client_ram_bytes": MIN_AVAILABLE_RAM,
                   "minimum_client_disk_bytes": MIN_AVAILABLE_DISK},
        "server": "root-owned Vast loopback llama-server through root-owned SSH tunnel",
        "started_utc": utc(),
    }


def write_json(path: Path, value: dict) -> None:
    with path.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n")
        stream.flush(); os.fsync(stream.fileno())


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--groups", type=int, choices=(1, 30), required=True)
    parser.add_argument("--whole-seconds", type=int, default=MAX_WHOLE_SECONDS)
    parser.add_argument("--local-port", type=int, default=PORT)
    args = parser.parse_args()
    if not 60 <= args.whole_seconds <= MAX_WHOLE_SECONDS:
        parser.error("whole-seconds must be within 60..21600")
    os.umask(0o077)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    run_dir = OUTPUT_DIR / ("smoke18" if args.groups == 1 else "dev30")
    run_dir.mkdir(mode=0o700, exist_ok=False)
    rows = [json.loads(line) for line in CORPUS.read_bytes().splitlines() if line.strip()]
    manifest = validate_manifest(rows, min_per_direction=60)
    if len(rows) != 1080 or len(manifest.directions) != 18:
        raise RuntimeError("public DEV corpus inventory mismatch")
    selected = schedule(rows, args.groups)
    frozen = config(rows, selected, args.groups, args.whole_seconds)
    frozen["stop_request_path"] = str(run_dir / "stop-request.json")
    frozen["stop_request_check"] = "between completed generations, after row fsync"
    write_json(run_dir / "config.json", frozen)
    started = time.monotonic()
    stop_reason = None
    recorded = 0
    try:
        with httpx.Client(base_url=f"http://127.0.0.1:{args.local_port}",
                          timeout=REQUEST_TIMEOUT_SECONDS, trust_env=False,
                          follow_redirects=False) as client:
            health = client.get("/health", timeout=5)
            health.raise_for_status()
            if health.json().get("status") != "ok":
                raise RuntimeError("gpu_server_health_not_ready")
            props = client.get("/props", timeout=5)
            props.raise_for_status()
            write_json(run_dir / "server-props.json", props.json())
            observations = probe_eog(client)
            write_json(run_dir / "eog-probe.json",
                       {"runtime_override": EOG_OVERRIDE, "observations": observations})
            print(json.dumps({"event": "ready", "run": str(run_dir),
                              "rows": len(selected)}), flush=True)
            with (run_dir / "results.jsonl").open("x", encoding="utf-8") as stream:
                for index, row in enumerate(selected, 1):
                    stop_reason = requested_stop_reason(run_dir)
                    if stop_reason:
                        break
                    current = resources()
                    if (current["available_ram_bytes"] < MIN_AVAILABLE_RAM or
                            current["available_disk_bytes"] < MIN_AVAILABLE_DISK):
                        stop_reason = "client_resource_guard"
                        break
                    if time.monotonic() - started >= args.whole_seconds:
                        stop_reason = "whole_run_deadline"
                        break
                    record = {"index": index, "sample_id": row["id"],
                              "direction": row["direction"],
                              "source_group": row["source_group"],
                              "source": row["source"], "reference": row["reference"],
                              "source_sha256": hashlib.sha256(row["source"].encode()).hexdigest(),
                              "reference_sha256": hashlib.sha256(row["reference"].encode()).hexdigest(),
                              "started_utc": utc(), "client_resources_before": current}
                    begun = time.monotonic()
                    try:
                        record.update(translate(client, row))
                    except (httpx.HTTPError, ValueError, KeyError) as error:
                        record.update({"status": "inference_error", "output": "",
                                       "error_type": type(error).__name__,
                                       "error": str(error)[:1000], "mechanical_flags": []})
                        stop_reason = "request_or_protocol_error"
                    record["latency_ms"] = round((time.monotonic()-begun)*1000, 3)
                    record["completed_utc"] = utc()
                    stream.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
                    stream.flush(); os.fsync(stream.fileno())
                    recorded = index
                    if args.groups == 1 or index % len(DIRECTIONS) == 0:
                        print(json.dumps({"event": "result", "index": index,
                                          "direction": row["direction"],
                                          "status": record["status"],
                                          "stop_type": record.get("stop_type"),
                                          "latency_ms": record["latency_ms"]}), flush=True)
                    if stop_reason:
                        break
    except Exception as error:
        stop_reason = stop_reason or "server_start_or_runtime_error"
        write_json(run_dir / "startup-error.json",
                   {"type": type(error).__name__, "message": str(error)[:1000]})
        print(json.dumps({"event": "error", "type": type(error).__name__,
                          "message": str(error)[:250]}), flush=True)
    finally:
        with (run_dir / "results.jsonl").open("a", encoding="utf-8") as stream:
            for index, row in enumerate(selected[recorded:], recorded+1):
                stream.write(json.dumps({"index": index, "sample_id": row["id"],
                                         "direction": row["direction"],
                                         "source_group": row["source_group"],
                                         "status": "not_run_" + (stop_reason or "unknown"),
                                         "output": "", "mechanical_flags": []},
                                        sort_keys=True) + "\n")
            stream.flush(); os.fsync(stream.fileno())
        results = [json.loads(line) for line in (run_dir / "results.jsonl").read_text().splitlines()]
        summary = {"scheduled": len(selected), "completed_inference_rows": recorded,
                   "counts": dict(Counter(row["status"] for row in results)),
                   "stop_reason": stop_reason,
                   "elapsed_s": round(time.monotonic()-started, 3),
                   "completed_utc": utc()}
        write_json(run_dir / "summary.json", summary)
        print(json.dumps({"event": "complete", **summary}), flush=True)


if __name__ == "__main__":
    main()
