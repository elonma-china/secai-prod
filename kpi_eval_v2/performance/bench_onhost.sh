#!/usr/bin/env bash
# bench_onhost.sh — MANUAL on-host throughput probe for R2-EMB1200 / R2-RERANK180.
#
# Run this directly ON the serving box (paste into an interactive SSH session, or
# `scp` it up and run it there) when the automated SSH path in performance/onhost.py
# can't reach the box (key/network/instance-restart issues). It hits the LOCAL nginx
# front for the embed/rerank vLLM pooling servers over loopback — the same on-host
# path onhost.py uses — so the >=1200 emb/s / >=180 doc/s thresholds (which assume
# on-host measurement) are read honestly, not deflated ~5x by WAN latency.
#
# Usage (from your workstation):
#   ssh -p <PORT> root@<HOST> 'bash -s' < kpi_eval_v2/performance/bench_onhost.sh
# or, on the box itself:
#   bash bench_onhost.sh
#
# Confirmed live topology (2026-09-20, instance 51522779): nginx :8000 -> embedding
# vLLM (127.0.0.1:18001, model DuyTa/sec-embedding), nginx :8002 -> reranker vLLM
# (127.0.0.1:18201, model DuyTa/sec-rerank). Override EMBED_URL/RERANK_URL/MODEL/KEY
# below if the box's port mapping or model name changes.

set -euo pipefail

EMBED_URL="${EMBED_URL:-http://127.0.0.1:8000/v1/embeddings}"
RERANK_URL="${RERANK_URL:-http://127.0.0.1:8002/v1/rerank}"
EMBED_MODEL="${EMBED_MODEL:-DuyTa/sec-embedding}"
RERANK_MODEL="${RERANK_MODEL:-DuyTa/sec-rerank}"
# Set SERVING_API_KEY in the environment before running — never hardcode it here.
API_KEY="${SERVING_API_KEY:-}"

python3 - "$EMBED_URL" "$RERANK_URL" "$EMBED_MODEL" "$RERANK_MODEL" "$API_KEY" <<'PYEOF'
import json, sys, time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

embed_url, rerank_url, embed_model, rerank_model, api_key = sys.argv[1:6]
headers = {"Content-Type": "application/json"}
if api_key:
    headers["Authorization"] = f"Bearer {api_key}"


def post(url, body, timeout=60):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def embed_one(_):
    texts = [f"vulnerability test query about CVE and CWE number {i} SQL injection buffer overflow"
             for i in range(64)]
    try:
        resp = post(embed_url, {"model": embed_model, "input": texts})
        return len(resp.get("data", []))
    except Exception as e:
        print(f"  embed error: {e}", file=sys.stderr)
        return 0


def rerank_one(i):
    docs = [f"vulnerability doc {j}: SQL injection buffer overflow XSS RCE in product version {j}"
            for j in range(20)]
    try:
        resp = post(rerank_url, {"model": rerank_model, "query": f"injection vulnerability {i}",
                                  "documents": docs})
        return len(resp.get("results", resp.get("data", [])))
    except Exception as e:
        print(f"  rerank error: {e}", file=sys.stderr)
        return 0


print("=== Embedding throughput (target: R2-EMB1200 >= 1200 emb/s, on-host) ===")
for conc in (4, 8, 16):
    nb = conc * 3
    t0 = time.time()
    done = sum(ThreadPoolExecutor(max_workers=conc).map(embed_one, range(nb)))
    dt = time.time() - t0
    rate = done / dt if dt else 0
    print(f"  concurrency={conc:3d}  n_embedded={done:5d}  seconds={dt:6.3f}  "
          f"emb/s={rate:8.1f}  {'PASS' if rate >= 1200 else 'below 1200'}")

print("\n=== Rerank throughput (target: R2-RERANK180 >= 180 doc/s, on-host) ===")
for conc in (2, 4, 8):
    nb = conc * 3
    t0 = time.time()
    done = sum(ThreadPoolExecutor(max_workers=conc).map(rerank_one, range(nb)))
    dt = time.time() - t0
    rate = done / dt if dt else 0
    print(f"  concurrency={conc:3d}  n_reranked={done:5d}  seconds={dt:6.3f}  "
          f"docs/s={rate:8.1f}  {'PASS' if rate >= 180 else 'below 180'}")
PYEOF
