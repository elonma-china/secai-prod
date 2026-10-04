"""Benchmark a private loopback OCR service against a frozen manifest."""
from __future__ import annotations

import argparse
import base64
import json
import random
import time
import urllib.error
import urllib.request
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--model", required=True)
    parser.add_argument("--token-file", type=Path, required=True)
    parser.add_argument("--base-url", default="http://127.0.0.1:18083")
    parser.add_argument("--rounds", type=int, default=3)
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text())
    key = args.token_file.read_text().strip()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for seed in range(args.rounds):
        items = list(manifest["images"])
        random.Random(seed).shuffle(items)
        with (args.output_dir / f"round-{seed}.jsonl").open("w") as sink:
            for item in items:
                image = args.manifest.parent / item["image"]
                body = json.dumps({"model": args.model,
                    "image_base64": base64.b64encode(image.read_bytes()).decode()}).encode()
                request = urllib.request.Request(args.base_url + "/v1/ocr", data=body,
                    headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
                start = time.monotonic()
                error = None
                try:
                    with urllib.request.urlopen(request, timeout=120) as response:
                        payload = json.load(response)
                    raw = "\n".join(block["text"] for block in payload["blocks"])
                except (urllib.error.URLError, ValueError, KeyError) as exc:
                    raw = ""
                    error = type(exc).__name__
                record = {"id": item["id"], "raw_text": raw, "corrected_text": raw,
                          "latency_seconds": round(time.monotonic() - start, 3), "error": error}
                sink.write(json.dumps(record, ensure_ascii=False) + "\n")
                sink.flush()
                print(seed, item["id"], record["latency_seconds"], error, flush=True)


if __name__ == "__main__":
    main()
