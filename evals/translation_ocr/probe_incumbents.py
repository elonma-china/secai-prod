"""Sequential synthetic availability/latency probes over private SSH forwards."""
from __future__ import annotations

import argparse
import json
import statistics
import time
import urllib.request
from pathlib import Path


def _keys(path: Path) -> dict[str, str]:
    values = {}
    for line in path.read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _post(port: int, route: str, key: str, body: dict) -> tuple[float, bool]:
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}{route}", json.dumps(body).encode(), headers,
    )
    started = time.perf_counter()
    with urllib.request.urlopen(request, timeout=30) as response:
        data = json.load(response)
    latency = time.perf_counter() - started
    return latency, bool(data)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("env_file", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    keys = _keys(args.env_file)
    tasks = (
        ("llm", 18401, "/v1/chat/completions", keys.get("QWEN_API_KEY", ""),
         {"model": "DuyTa/Cyber-F1-AWQ", "messages": [{"role": "user", "content": "Reply with OK."}],
          "max_tokens": 8, "temperature": 0}),
        ("embedding", 18400, "/v1/embeddings", keys.get("EMBEDDING_API_KEY", ""),
         {"model": "DuyTa/sec-embedding", "input": "Synthetic CVE security note."}),
        ("rerank", 18402, "/v1/rerank", keys.get("RERANKER_API_KEY", ""),
         {"model": "DuyTa/sec-rerank", "query": "Synthetic security note",
          "documents": ["CVE vulnerability note", "Unrelated calendar text"], "top_n": 2}),
    )
    rows = []
    for iteration in range(3):
        for name, port, route, key, body in tasks:
            latency, valid = _post(port, route, key, body)
            rows.append({"service": name, "iteration": iteration,
                         "latency_seconds": round(latency, 3), "response_nonempty": valid})
            print(name, iteration, round(latency, 3), valid, flush=True)
    args.output.write_text(json.dumps({"started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                                      "probes": rows}, indent=2) + "\n")
    for name, *_ in tasks:
        values = [row["latency_seconds"] for row in rows if row["service"] == name]
        print(name, "median", statistics.median(values))


if __name__ == "__main__":
    main()
