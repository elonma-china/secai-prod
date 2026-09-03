"""Buffered, fail-open Elasticsearch writer for the activity log.

Design contract — in priority order:
  1. **Never break a request.** ``emit()`` swallows everything and never awaits the network.
  2. **Never slow a request.** ``emit()`` is a bounded ``put_nowait`` (O(1)); a background
     worker does the actual HTTP.
  3. Only then: don't lose records.

That ordering is why a full queue drops the record (and counts it) instead of applying
backpressure, and why a failed bulk is dropped rather than retried — a retry would either
block the pipeline or duplicate rows, both worse than a gap in an observability log.

Uses ``httpx`` against the Elasticsearch REST API directly: the gateway already depends on
httpx, so this adds no dependency, and the surface used (``_bulk``, ``_search``, template
setup) is small and stable.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

import httpx

from .schema import LogRecord, ilm_policy, index_template

logger = logging.getLogger(__name__)

_LOG_EVERY_N_FAILURES = 10  # throttle: a dead ES must not flood the gateway log


class ActivityLogSink:
    """Queue → batch → ``_bulk``. Started/stopped by the app lifespan.

    Not started (unit tests, which never run the lifespan) means ``emit()`` is a no-op, so
    tests exercise the routes without any Elasticsearch or network involvement.
    """

    def __init__(self, settings: Any, client: httpx.AsyncClient | None = None) -> None:
        self._settings = settings
        self._client = client
        self._queue: asyncio.Queue[LogRecord] = asyncio.Queue(
            maxsize=int(getattr(settings, "activity_log_queue_max", 10000)))
        self._task: asyncio.Task | None = None
        self._running = False
        # counters surfaced on /health
        self._sent = 0
        self._dropped = 0
        self._failures = 0            # consecutive flush failures (drives the breaker)
        self._breaker_until = 0.0     # monotonic deadline; 0 = closed
        self._last_error = ""

    # ── lifecycle ─────────────────────────────────────────────────────────────────────
    @property
    def enabled(self) -> bool:
        return bool(getattr(self._settings, "activity_log_enabled", True)
                    and getattr(self._settings, "elasticsearch_url", ""))

    async def start(self, client: httpx.AsyncClient | None = None) -> None:
        """Idempotent. Bootstrap failures are non-fatal: the template can be created later
        by a subsequent boot, and ES applies it to indices created after that point."""
        if self._running or not self.enabled:
            return
        if client is not None:
            self._client = client
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=float(getattr(self._settings, "activity_log_timeout_s", 5.0)))
        self._running = True
        try:
            await self._bootstrap()
        except Exception:
            logger.warning("activity log: index bootstrap failed (will still try to write)",
                           exc_info=True)
        self._task = asyncio.create_task(self._worker(), name="activity-log-worker")

    async def stop(self) -> None:
        """Drain what is already queued, flush once, then stop. Bounded so shutdown cannot
        hang on an unreachable Elasticsearch."""
        self._running = False
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: B014 — shutdown must not raise
                pass
        pending: list[LogRecord] = []
        while not self._queue.empty() and len(pending) < 1000:
            try:
                pending.append(self._queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        if pending:
            try:
                await asyncio.wait_for(self._flush(pending), timeout=3.0)
            except Exception:
                logger.warning("activity log: final flush dropped %d record(s)", len(pending))

    # ── producer side (request path) ──────────────────────────────────────────────────
    async def emit(self, record: LogRecord) -> None:
        """Fire-and-forget from the request path. Never raises, never awaits the network."""
        try:
            if not self._running or not self.enabled:
                return
            try:
                self._queue.put_nowait(record)
            except asyncio.QueueFull:
                self._dropped += 1
        except Exception:  # pragma: no cover — belt and braces; logging must never bubble up
            pass

    def stats(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "running": self._running,
            "queued": self._queue.qsize(),
            "sent": self._sent,
            "dropped": self._dropped,
            "breaker_open": self._breaker_until > time.monotonic(),
            "last_error": self._last_error[:200],
        }

    # ── consumer side (background) ────────────────────────────────────────────────────
    async def _worker(self) -> None:
        interval = float(getattr(self._settings, "activity_log_flush_interval_s", 1.0))
        batch_max = int(getattr(self._settings, "activity_log_batch_max", 200))
        while self._running:
            batch: list[LogRecord] = []
            try:
                # Block until the first record, then drain whatever else is already there.
                first = await asyncio.wait_for(self._queue.get(), timeout=interval)
                batch.append(first)
                while len(batch) < batch_max:
                    try:
                        batch.append(self._queue.get_nowait())
                    except asyncio.QueueEmpty:
                        break
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                raise
            except Exception:  # pragma: no cover
                continue
            if time.monotonic() < self._breaker_until:
                self._dropped += len(batch)   # breaker open: shed load, keep counting
                continue
            try:
                await self._flush(batch)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._on_failure(exc, len(batch))

    def _on_failure(self, exc: Exception, n: int) -> None:
        self._failures += 1
        self._dropped += n
        self._last_error = f"{type(exc).__name__}: {exc}"
        threshold = int(getattr(self._settings, "activity_log_breaker_threshold", 5))
        if self._failures >= threshold:
            cooldown = float(getattr(self._settings, "activity_log_breaker_cooldown_s", 30.0))
            self._breaker_until = time.monotonic() + cooldown
            self._failures = 0
            logger.warning("activity log: Elasticsearch unreachable, pausing %.0fs "
                           "(total dropped=%d, last=%s)", cooldown, self._dropped,
                           self._last_error)
        elif self._dropped % _LOG_EVERY_N_FAILURES == 1:
            logger.warning("activity log: flush failed, dropped %d record(s): %s",
                           n, self._last_error)

    async def _flush(self, batch: list[LogRecord]) -> None:
        index = getattr(self._settings, "activity_log_index", "secai-ai-logs")
        cap = int(getattr(self._settings, "activity_log_max_field_chars", 200_000))
        lines: list[str] = []
        for rec in batch:
            lines.append('{"create":{}}')     # data streams accept `create` only
            lines.append(json.dumps(rec.to_doc(max_field_chars=cap), ensure_ascii=False))
        body = "\n".join(lines) + "\n"
        resp = await self._request("POST", f"/{index}/_bulk", content=body.encode("utf-8"),
                                   headers={"Content-Type": "application/x-ndjson"})
        resp.raise_for_status()
        payload = resp.json()
        if payload.get("errors"):
            # Partial failure: some docs rejected (mapping conflict, etc). Count them, log
            # the first reason, and move on — a rejected doc will never succeed on retry.
            failed = [i for i in payload.get("items", [])
                      if (i.get("create") or {}).get("error")]
            if failed:
                self._dropped += len(failed)
                first = (failed[0].get("create") or {}).get("error", {})
                self._last_error = f"bulk-rejected: {first.get('type')}: {first.get('reason')}"
                logger.warning("activity log: %d/%d doc(s) rejected — %s",
                               len(failed), len(batch), self._last_error)
            self._sent += len(batch) - len(failed)
        else:
            self._sent += len(batch)
        self._failures = 0
        self._breaker_until = 0.0

    # ── Elasticsearch plumbing ────────────────────────────────────────────────────────
    async def _request(self, method: str, path: str, **kw: Any) -> httpx.Response:
        base = str(getattr(self._settings, "elasticsearch_url", "")).rstrip("/")
        user = getattr(self._settings, "elasticsearch_username", "")
        pwd = getattr(self._settings, "elasticsearch_password", "")
        auth = (user, pwd) if user else None
        timeout = float(getattr(self._settings, "activity_log_timeout_s", 5.0))
        assert self._client is not None
        return await self._client.request(method, base + path, auth=auth, timeout=timeout, **kw)

    async def _bootstrap(self) -> None:
        """Create the ILM policy + index template. Idempotent (PUT), safe to repeat."""
        index = getattr(self._settings, "activity_log_index", "secai-ai-logs")
        days = int(getattr(self._settings, "activity_log_retention_days", 30))
        policy = f"{index}-policy"
        r = await self._request("PUT", f"/_ilm/policy/{policy}", json=ilm_policy(days))
        r.raise_for_status()
        r = await self._request("PUT", f"/_index_template/{index}",
                                json=index_template(index, policy))
        r.raise_for_status()
        logger.info("activity log: index template %s ready (retention %dd)", index, days)

    # ── read side (used by the /logs UI) ──────────────────────────────────────────────
    async def search(self, body: dict[str, Any]) -> dict[str, Any]:
        index = getattr(self._settings, "activity_log_index", "secai-ai-logs")
        resp = await self._request("POST", f"/{index}/_search", json=body)
        resp.raise_for_status()
        return resp.json()
