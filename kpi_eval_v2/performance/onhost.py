"""R2-EMB1200 (>=1200 emb/s) + R2-RERANK180 (>=180 doc/s) — throughput of the embed/rerank
vLLM servers, measured ON-HOST (loopback on the A100 serving box), because the >=1200/>=180
thresholds assume on-host and a WAN measurement reads ~5x low (see
``kpi_bench/scripts/perf_kpi.py`` / memory note ``embed-rerank-onhost-vs-wan``) and would
misrepresent the server's real capacity.

On-host path: SSH to the serving box and run a small embedded Python probe against
``127.0.0.1`` (the internal nginx front for the vLLM pooling servers — same path a
same-host client would use; confirmed via ``ss -tlnp`` + nginx conf, 2026-09-20:
port 8000 = embedding_backend, 8002 = reranker_backend, both proxying to
``127.0.0.1:1800x``). Falls back to a WAN measurement (reported honestly as WAN,
``value=None`` on the KPI so a WAN number never certifies an on-host threshold) if SSH
is unavailable.

SSH target resolution order:
  1. ``config.SERVING_SSH`` if the shared config sets it (parsed leniently: accepts
     ``user@host``, ``user@host:port`` or ``user@host -p port``).
  2. ``KPI2_SERVING_SSH_HOST`` / ``_PORT`` / ``_USER`` env vars.
  3. Fallback default resolved via ``vastai show instances`` on 2026-09-20 for the live
     instance (51522779): ``root@ssh2.vast.ai -p 12778``. SSH port/host can change across
     a vast.ai instance restart — override via env vars (or set ``KPI2_SERVING_SSH`` in
     ``secai-docker/.env``) if this default goes stale.

``performance/bench_onhost.sh`` ships alongside this file for a fully manual run
(scp it to the box, or paste it into an interactive SSH session) when this automated
path can't reach the box at all.
"""
from __future__ import annotations

import json
import re
import subprocess
import time

import httpx

from .. import config

_DEFAULT_SSH_HOST = "ssh2.vast.ai"
_DEFAULT_SSH_PORT = "12778"
_DEFAULT_SSH_USER = "root"


def _resolve_ssh() -> tuple[str, str, str]:
    import os
    raw = config.SERVING_SSH or os.environ.get("KPI2_SERVING_SSH", "")
    if raw:
        m = re.match(r"(?:(?P<user>[\w.-]+)@)?(?P<host>[\w.-]+)(?:[:\s]+-?p?\s*(?P<port>\d+))?", raw)
        if m:
            return (m.group("user") or _DEFAULT_SSH_USER, m.group("host") or _DEFAULT_SSH_HOST,
                    m.group("port") or _DEFAULT_SSH_PORT)
    return (os.environ.get("KPI2_SERVING_SSH_USER", _DEFAULT_SSH_USER),
            os.environ.get("KPI2_SERVING_SSH_HOST", _DEFAULT_SSH_HOST),
            os.environ.get("KPI2_SERVING_SSH_PORT", _DEFAULT_SSH_PORT))


_REMOTE_PROBE = """
import json, time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

EMBED_URL = "http://127.0.0.1:8000/v1/embeddings"
RERANK_URL = "http://127.0.0.1:8002/v1/rerank"
KEY = "%(key)s"
HEADERS = {"Content-Type": "application/json"}
if KEY:
    HEADERS["Authorization"] = "Bearer " + KEY


def post(url, body, timeout=60):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def embed_one(_):
    texts = [f"vulnerability test query about CVE and CWE number {i} SQL injection buffer overflow"
             for i in range(64)]
    try:
        resp = post(EMBED_URL, {"model": "%(embed_model)s", "input": texts})
        return len(resp.get("data", []))
    except Exception:
        return 0


def rerank_one(i):
    docs = [f"vulnerability doc {j}: SQL injection buffer overflow XSS RCE in product version {j}"
            for j in range(20)]
    try:
        resp = post(RERANK_URL, {"model": "%(rerank_model)s", "query": f"injection vulnerability {i}",
                                  "documents": docs})
        return len(resp.get("results", resp.get("data", [])))
    except Exception:
        return 0


out = {"embed": {}, "rerank": {}}
for conc in (4, 8, 16):
    nb = conc * 3
    t0 = time.time()
    done = 0
    with ThreadPoolExecutor(max_workers=conc) as ex:
        for got in ex.map(embed_one, range(nb)):
            done += got
    dt = time.time() - t0
    out["embed"][str(conc)] = {"n_embedded": done, "seconds": round(dt, 3),
                                "embeddings_per_s": round(done / dt, 1) if dt else None}

for conc in (2, 4, 8):
    nb = conc * 3
    t0 = time.time()
    done = 0
    with ThreadPoolExecutor(max_workers=conc) as ex:
        for got in ex.map(rerank_one, range(nb)):
            done += got
    dt = time.time() - t0
    out["rerank"][str(conc)] = {"n_reranked": done, "seconds": round(dt, 3),
                                 "docs_per_s": round(done / dt, 1) if dt else None}

print("KPI2_ONHOST_RESULT " + json.dumps(out))
"""


def measure_onhost(timeout: float = 90.0) -> dict | None:
    """SSH in, run the embedded probe, return parsed {"embed": {...}, "rerank": {...}}
    or None if SSH/the probe failed (caller falls back to WAN + value=None)."""
    user, host, port = _resolve_ssh()
    code = _REMOTE_PROBE % {
        "key": config.EMBED_KEY or config.RERANK_KEY or "",
        "embed_model": config.EMBED_MODEL, "rerank_model": config.RERANK_MODEL,
    }
    try:
        proc = subprocess.run(
            ["ssh", "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=10",
             "-p", str(port), f"{user}@{host}", "python3", "-"],
            input=code, capture_output=True, text=True, timeout=timeout,
        )
    except Exception as e:
        return {"_error": f"ssh exec failed: {type(e).__name__}: {e}", "_target": f"{user}@{host}:{port}"}
    for line in proc.stdout.splitlines():
        if line.startswith("KPI2_ONHOST_RESULT "):
            data = json.loads(line[len("KPI2_ONHOST_RESULT "):])
            data["_target"] = f"{user}@{host}:{port}"
            return data
    return {"_error": f"no result line (rc={proc.returncode}); stderr={proc.stderr[-800:]}",
            "_target": f"{user}@{host}:{port}"}


# ── WAN fallback (over the public internet — expected to read ~5x low vs on-host) ──
def _wan_headers(key: str | None) -> dict:
    h = {"Content-Type": "application/json"}
    if key:
        h["Authorization"] = f"Bearer {key}"
    return h


def measure_wan_embed(batch: int = 32, conc: int = 4, nbatches: int = 8) -> dict:
    texts = [f"vulnerability test query about CVE and CWE number {i}" for i in range(batch)]
    headers = _wan_headers(config.EMBED_KEY)

    def one(_):
        try:
            with httpx.Client(timeout=60) as c:
                r = c.post(f"{config.EMBED_URL}/v1/embeddings",
                          json={"model": config.EMBED_MODEL, "input": texts}, headers=headers)
                r.raise_for_status()
                return len(r.json().get("data", []))
        except Exception:
            return 0
    from concurrent.futures import ThreadPoolExecutor
    t0 = time.time()
    done = 0
    with ThreadPoolExecutor(max_workers=conc) as ex:
        for got in ex.map(one, range(nbatches)):
            done += got
    dt = time.time() - t0
    return {"n_embedded": done, "seconds": round(dt, 2), "embeddings_per_s": round(done / dt, 1) if dt else None}


def measure_wan_rerank(batch: int = 20, conc: int = 4, nreq: int = 8) -> dict:
    docs = [f"vulnerability doc {j}: SQL injection buffer overflow XSS RCE in product" for j in range(batch)]
    headers = _wan_headers(config.RERANK_KEY)

    def one(i):
        try:
            with httpx.Client(timeout=60) as c:
                r = c.post(f"{config.RERANK_URL}/v1/rerank",
                          json={"model": config.RERANK_MODEL, "query": f"injection vulnerability {i}",
                                "documents": docs}, headers=headers)
                r.raise_for_status()
                data = r.json()
                return len(data.get("results", data.get("data", [])))
        except Exception:
            return 0
    from concurrent.futures import ThreadPoolExecutor
    t0 = time.time()
    done = 0
    with ThreadPoolExecutor(max_workers=conc) as ex:
        for got in ex.map(one, range(nreq)):
            done += got
    dt = time.time() - t0
    return {"n_reranked": done, "seconds": round(dt, 2), "docs_per_s": round(done / dt, 1) if dt else None}
