"""Run an isolated local translation v2 API and worker with the existing Linch model.

Example: python scripts/run_translate_v2_local.py --env-file /private/gateway.env \
    --store-dir /tmp/secai-translate-v2 --qwen-base-url http://127.0.0.1:18080/v1
"""
from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from dotenv import dotenv_values


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, help="Private existing provider/OCR settings file")
    parser.add_argument("--store-dir", type=Path, required=True, help="Private local job and artifact directory")
    parser.add_argument("--qwen-base-url", help="Existing DuyTa vLLM endpoint override")
    parser.add_argument("--ocr-base-url", help="Private OCR tunnel endpoint override")
    parser.add_argument("--port", type=int, default=18089)
    args = parser.parse_args()

    settings = dict(os.environ)
    allowed = {
        "QWEN_BASE_URL", "QWEN_API_KEY", "QWEN_MODEL", "QWEN_CONTEXT_WINDOW",
        "LLM_TEMPERATURE", "LLM_TOP_P", "LLM_TOP_K", "LLM_MIN_P",
        "LLM_PRESENCE_PENALTY", "LLM_REPETITION_PENALTY",
        "TRANSLATE_V2_OCR_BASE_URL", "TRANSLATE_V2_OCR_API_KEY", "TRANSLATE_V2_OCR_MODEL",
    }
    if args.env_file:
        if not args.env_file.is_file():
            parser.error("--env-file must name an existing file")
        settings.update({key: value for key, value in dotenv_values(args.env_file).items()
                         if key in allowed and value is not None})
    if args.qwen_base_url:
        settings["QWEN_BASE_URL"] = args.qwen_base_url
    if args.ocr_base_url:
        settings["TRANSLATE_V2_OCR_BASE_URL"] = args.ocr_base_url
    if not settings.get("QWEN_BASE_URL") or not settings.get("QWEN_MODEL"):
        parser.error("QWEN_BASE_URL and QWEN_MODEL must identify the existing DuyTa model")
    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")

    root = Path(__file__).resolve().parents[1]
    store = args.store_dir.resolve()
    store.mkdir(parents=True, exist_ok=True, mode=0o700)
    store.chmod(0o700)
    settings.update({
        "PYTHONPATH": os.pathsep.join(filter(None, (str(root / "src"), settings.get("PYTHONPATH", "")))),
        "LLM_PROVIDER": "vllm",
        "SESSION_STORE_BACKEND": "memory",
        "REDIS_URL": "",
        "INPUT_CLASSIFIER_ENABLED": "false",
        "TRANSLATE_V2_ENABLED": "true",
        "TRANSLATE_V2_OCR_ENABLED": str(all(settings.get(key) for key in (
            "TRANSLATE_V2_OCR_BASE_URL", "TRANSLATE_V2_OCR_API_KEY",
            "TRANSLATE_V2_OCR_MODEL"))).lower(),
        "TRANSLATE_V2_DB_PATH": str(store / "jobs.sqlite3"),
        "TRANSLATE_V2_ARTIFACT_DIR": str(store / "artifacts"),
        "TRANSLATE_V2_WORKER_DB_PATH": str(store / "worker.sqlite3"),
        "GATEWAY_DB_PATH": str(store / "gateway.sqlite3"),
        "POC_CATALOG_DB": str(store / "poc.sqlite3"),
        "POC_ARTIFACT_STORE": str(store / "poc-artifacts"),
        "POC_SOURCES_ROOT": str(store / "poc-sources"),
        "EXPLOITDB_ROOT": str(store / "exploitdb"),
    })
    worker_cmd = [sys.executable, "-c", "import asyncio; from secai_gateway.translation.worker "
                  "import main; asyncio.run(main())"]
    api_cmd = [sys.executable, "-m", "uvicorn", "secai_gateway.app:app",
               "--host", "127.0.0.1", "--port", str(args.port)]
    processes: list[subprocess.Popen] = []
    stopping = False

    def stop_on_term(signum, frame):
        nonlocal stopping
        stopping = True

    previous_term = signal.signal(signal.SIGTERM, stop_on_term)
    try:
        processes.append(subprocess.Popen(api_cmd, cwd=root, env=settings))
        if not stopping:
            processes.append(subprocess.Popen(worker_cmd, cwd=root, env=settings))
        if len(processes) == 2:
            print(f"Local translation v2 API: http://127.0.0.1:{args.port} ; store: {store}", flush=True)
        while not stopping and len(processes) == 2 and all(process.poll() is None for process in processes):
            time.sleep(0.5)
        return 0 if stopping else next(
            (process.returncode for process in processes if process.returncode is not None), 1)
    except KeyboardInterrupt:
        return 0
    finally:
        for process in processes:
            if process.poll() is None:
                process.terminate()
        for process in processes:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        signal.signal(signal.SIGTERM, previous_term)


if __name__ == "__main__":
    raise SystemExit(main())
