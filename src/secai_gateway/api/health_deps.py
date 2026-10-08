"""Dependency + freshness checks surfaced by ``GET /health``.

``/health`` used to prove only that the LLM answers ``/models``. After a restart or a ``.env``
change that is not enough (CLAUDE.md: "/health đơn lẻ không chứng minh model hoạt động"), so this
adds real authenticated probes for embedding/rerank, the databases the RAG path needs, and the
age of the nightly KB sync (whose silent failure left the KB stale 3-7 Oct 2026).

Probes run concurrently with short timeouts and the result is cached for ``_TTL_S`` seconds, so
the 30s Docker healthcheck + dashboards cannot hammer the model servers. Nothing here raises.
"""

from __future__ import annotations

import asyncio
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

_TTL_S = 30.0
_PROBE_TIMEOUT_S = 4.0
_STARTED_AT = time.time()

# Cron fires daily 03:00. A run older than this means cron itself did not fire; a last success
# older than this means the KB is stale (one missed night is tolerated, two is not).
CRON_MAX_AGE_H = 30.0
KB_MAX_AGE_H = 36.0

_START = re.compile(r"Starting sync at (.+)")
_END = re.compile(r"sync exit=(\d+) at (.+)")
_cache: dict[str, Any] = {"ts": 0.0, "value": None}
_lock: asyncio.Lock | None = None


def _hostport(url: str | None) -> str | None:
    """host:port only — never leak path, query or credentials into /health."""
    if not url:
        return None
    p = urlparse(url if "//" in url else f"//{url}")
    return f"{p.hostname}:{p.port}" if p.port else p.hostname


def _parse_cron_date(text: str) -> datetime | None:
    """Parse `date` output as written by run_sync.sh, e.g. 'Thu Oct  8 03:11:26 AM +07 2026'."""
    t = re.sub(r"([+-]\d\d) (\d{4})$", r"\g<1>00 \2", " ".join(text.split()))
    for fmt in ("%a %b %d %I:%M:%S %p %z %Y", "%a %b %d %H:%M:%S %z %Y"):
        try:
            return datetime.strptime(t, fmt)
        except ValueError:
            continue
    return None


def kb_sync_status(log_path: str, now: datetime | None = None) -> dict[str, Any]:
    """Freshness of the nightly CVE->KB cron, read from the tail of its own log.

    ``cron_ok``: a run started recently (cron is firing). ``kb_fresh``: the last run that exited
    0 is recent (the KB actually received data). Both are needed: the 3-7 Oct 2026 outage had a
    healthy cron and a failing ingest."""
    now = now or datetime.now(timezone.utc)
    p = Path(log_path)
    out: dict[str, Any] = {"log": log_path, "cron_ok": False, "kb_fresh": False,
                           "last_run": None, "last_run_exit": None, "last_success": None,
                           "last_run_age_h": None, "last_success_age_h": None}
    try:
        with p.open("rb") as fh:
            fh.seek(0, 2)
            size = fh.tell()
            fh.seek(max(0, size - 256 * 1024))
            lines = fh.read().decode("utf-8", "replace").splitlines()
    except OSError as exc:
        out["detail"] = f"không đọc được log: {type(exc).__name__}"
        return out

    last_started: datetime | None = None
    last_end: tuple[int, datetime] | None = None
    last_ok: datetime | None = None
    for ln in lines:
        ms = _START.search(ln)
        if ms:
            last_started = _parse_cron_date(ms.group(1)) or last_started
            continue
        me = _END.search(ln)
        if me:
            when = _parse_cron_date(me.group(2))
            if when is None:
                continue
            last_end = (int(me.group(1)), when)
            if int(me.group(1)) == 0:
                last_ok = when
    if last_started is None:
        out["detail"] = "log chưa có lần chạy nào"
        return out

    def age_h(d: datetime) -> float:
        return round((now - d).total_seconds() / 3600, 1)

    out["last_run"] = last_started.isoformat()
    out["last_run_age_h"] = age_h(last_started)
    out["cron_ok"] = out["last_run_age_h"] <= CRON_MAX_AGE_H
    if last_end is not None:
        out["last_run_exit"] = last_end[0]
    if last_ok is not None:
        out["last_success"] = last_ok.isoformat()
        out["last_success_age_h"] = age_h(last_ok)
        out["kb_fresh"] = out["last_success_age_h"] <= KB_MAX_AGE_H
    return out


async def _timed(role: str, coro: Any, **extra: Any) -> dict[str, Any]:
    t0 = time.monotonic()
    try:
        ok, detail = await asyncio.wait_for(coro, timeout=_PROBE_TIMEOUT_S + 1)
    except Exception as exc:  # noqa: BLE001 — a probe must never break /health
        ok, detail = False, f"{type(exc).__name__}: {str(exc)[:120]}"
    return {"role": role, "ok": bool(ok), "latency_ms": round((time.monotonic() - t0) * 1000),
            "detail": detail, **extra}


async def _http_probe(method: str, url: str, *, headers: dict | None = None,
                      json: dict | None = None, auth_hint: str = "") -> tuple[bool, str | None]:
    async with httpx.AsyncClient(timeout=_PROBE_TIMEOUT_S) as client:
        r = await client.request(method, url, headers=headers, json=json)
    if r.is_success:
        return True, None
    return False, f"HTTP {r.status_code}{auth_hint}"


def _bearer(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"} if key else {}


async def _check_embedding(s: Any) -> dict[str, Any]:
    url = (s.embedding_api_url or "").rstrip("/")
    if not url:
        return {"role": "embedding", "ok": False, "detail": "chưa cấu hình EMBEDDING_API_URL"}
    return await _timed("embedding", _http_probe(
        "POST", f"{url}/v1/embeddings", headers=_bearer(s.embedding_api_key),
        json={"model": s.embedding_model, "input": "healthcheck"},
        auth_hint=f" (model '{s.embedding_model}' có thể sai tên — RAG sẽ rơi về BM25-only)"),
        endpoint=_hostport(url), model=s.embedding_model)


async def _check_reranker(s: Any) -> dict[str, Any]:
    url = (s.reranker_api_url or "").rstrip("/")
    if not url:
        return {"role": "reranker", "ok": False, "detail": "chưa cấu hình RERANKER_API_URL"}
    return await _timed("reranker", _http_probe(
        "POST", f"{url}/v1/rerank", headers=_bearer(s.reranker_api_key),
        json={"model": s.reranker_model, "query": "healthcheck", "documents": ["healthcheck"], "top_n": 1},
        auth_hint=f" (model '{s.reranker_model}' có thể sai tên)"),
        endpoint=_hostport(url), model=s.reranker_model)


async def _check_qdrant(s: Any) -> dict[str, Any]:
    url = s.qdrant_url.rstrip("/")
    seen: dict[str, Any] = {}

    async def probe() -> tuple[bool, str | None]:
        async with httpx.AsyncClient(timeout=_PROBE_TIMEOUT_S) as client:
            r = await client.get(f"{url}/collections/{s.qdrant_collection}")
        if not r.is_success:
            return False, f"HTTP {r.status_code} (collection '{s.qdrant_collection}')"
        res = r.json().get("result", {})
        if res.get("status") != "green":
            return False, f"collection status={res.get('status')}"
        seen["points"] = res.get("points_count")
        return True, None

    row = await _timed("qdrant", probe(), endpoint=_hostport(url), collection=s.qdrant_collection)
    row["points"] = seen.get("points")
    return row


async def _check_neo4j(s: Any) -> dict[str, Any]:
    from ..retrieval.neo4j import _auth_header, _neo4j_http_url

    base = _neo4j_http_url()
    return await _timed("neo4j", _http_probe(
        "POST", f"{base}/db/{s.neo4j_database}/tx/commit",
        headers={"Authorization": _auth_header()},
        json={"statements": [{"statement": "MATCH (c:CVE) RETURN count(c) AS n LIMIT 1"}]}),
        endpoint=_hostport(base))


async def collect(settings: Any) -> dict[str, Any]:
    """Run every probe once (concurrently) and shape the /health extension."""
    emb, rer, qd, neo = await asyncio.gather(
        _check_embedding(settings), _check_reranker(settings),
        _check_qdrant(settings), _check_neo4j(settings))
    kb = kb_sync_status(settings.showcase_kb_sync_log)
    return {"serving": {"embedding": emb, "reranker": rer},
            "databases": {"qdrant": qd, "neo4j": neo}, "kb_sync": kb}


async def cached_collect(settings: Any) -> dict[str, Any]:
    global _lock
    if _lock is None:
        _lock = asyncio.Lock()
    async with _lock:
        now = time.monotonic()
        if _cache["value"] is None or now - _cache["ts"] > _TTL_S:
            _cache["value"] = await collect(settings)
            _cache["ts"] = now
        return _cache["value"]


def problems_from(extra: dict[str, Any]) -> list[str]:
    """Human-readable list of what is wrong; empty means every dependency + the cron is healthy."""
    out: list[str] = []
    for group in ("serving", "databases"):
        for name, row in extra[group].items():
            if not row.get("ok"):
                out.append(f"{name}: {row.get('detail') or 'không phản hồi'}")
    kb = extra["kb_sync"]
    if not kb.get("cron_ok"):
        out.append(f"cron: không chạy trong {CRON_MAX_AGE_H:.0f}h qua ({kb.get('detail') or 'log cũ'})")
    elif not kb.get("kb_fresh"):
        out.append(f"kb_sync: lần nạp thành công gần nhất {kb.get('last_success') or 'không rõ'} "
                   f"(>{KB_MAX_AGE_H:.0f}h) — KB có thể cũ")
    return out


def uptime() -> dict[str, Any]:
    return {"started_at": datetime.fromtimestamp(_STARTED_AT, timezone.utc).isoformat(),
            "uptime_s": round(time.time() - _STARTED_AT)}
