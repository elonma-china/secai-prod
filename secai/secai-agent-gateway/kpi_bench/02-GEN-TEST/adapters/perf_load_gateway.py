#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""perf_load_gateway.py — Drop-in replacement for perf-bench/perf_load.py that hits the
LIVE secai-agent-gateway (/ai/chat/stream, SSE) instead of the old opencode /session API.

Same CLI, same pct() percentile helper, same ThreadPoolExecutor pattern, and the EXACT
summary-dict computation logic from perf_load.py — only the HTTP call inside one() changes
(one POST + SSE stream instead of POST /session + POST /session/{id}/message).

Config qua ENV:
  GW_BASE       gateway base URL (mặc định http://127.0.0.1:8131)

  python3 perf_load_gateway.py --dataset perf_bench_100.jsonl --concurrency 10 \
      --results out/results.jsonl --summary out/summary.json
"""
import argparse
import json
import os
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

BASE = os.environ.get("GW_BASE", "http://127.0.0.1:8131").rstrip("/")

# Self-pacing for the gateway's rate limit: api/middleware.py:route_limit() strips a
# trailing "/stream" before matching, so POST /ai/chat/stream is limited as /ai/chat,
# which falls into the DEFAULT bucket → 15 requests / 60s PER CLIENT IP (fixed/rolling
# window, state/ratelimit.py: FixedWindowRateLimiter keeps stamps > now-60). Every
# worker thread in this process shares one outbound IP, so a single process-wide
# limiter — mirroring that exact same rolling-window rule — keeps ALL of them under the
# ceiling instead of racing into 429s and then reacting after the fact.
_RATE_LIMIT = int(os.environ.get("GW_RATE_LIMIT", "80"))  # margin under the real 90/60s cap (raised 2026-08-27)
_RATE_WINDOW_S = 60.0
# Retry-on-429 safety net for edge cases the pacer above doesn't cover (e.g. brief overlap
# with unrelated traffic sharing this host/IP). Not the primary defense — the pacer is.
_MAX_429_RETRIES = int(os.environ.get("GW_MAX_429_RETRIES", "3"))


class _RateLimiter:
    """Thread-safe rolling-window limiter: acquire() blocks the caller until issuing
    another request would keep this process at or under `limit` requests in the last
    `window_s` seconds — same rule as the gateway's own FixedWindowRateLimiter."""

    def __init__(self, limit, window_s):
        self._limit = limit
        self._window = window_s
        self._lock = threading.Lock()
        self._stamps = deque()

    def acquire(self):
        while True:
            with self._lock:
                now = time.time()
                while self._stamps and self._stamps[0] <= now - self._window:
                    self._stamps.popleft()
                if len(self._stamps) < self._limit:
                    self._stamps.append(now)
                    return
                wait = self._stamps[0] + self._window - now
            time.sleep(max(wait, 0.01))


_rate_limiter = _RateLimiter(_RATE_LIMIT, _RATE_WINDOW_S)


def pct(xs, p):
    if not xs:
        return None
    xs = sorted(xs)
    return xs[min(int(round(p / 100 * (len(xs) - 1))), len(xs) - 1)]


def _stream_chat(message, timeout=280):
    """POST /ai/chat/stream and consume the SSE stream. Returns
    (ttft_ms, final_text, tools, input_classification, had_error_event)."""
    body = {"message": message}
    req = urllib.request.Request(
        BASE + "/ai/chat/stream",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "text/event-stream"},
    )
    _rate_limiter.acquire()  # self-pace to the gateway's 15 req/60s bucket before sending
    t0 = time.time()
    ttft_ms = None
    token_buf = ""  # best-effort running draft; reset on "regenerate" (see api/sse.py)
    final_text = None
    tools = []
    input_classification = None
    had_error_event = False

    with urllib.request.urlopen(req, timeout=timeout) as r:
        event_name = None
        data_lines = []

        def flush():
            nonlocal event_name, data_lines, ttft_ms, token_buf, final_text, tools
            nonlocal input_classification, had_error_event
            if not data_lines:
                event_name = None
                data_lines = []
                return
            raw = "\n".join(data_lines)
            event_name = event_name or "message"
            data_lines = []
            if raw.strip() == "[DONE]":
                event_name = None
                return
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                event_name = None
                return
            if event_name == "tool_call":
                tools.append(payload.get("name"))
            elif event_name == "token":
                if ttft_ms is None:
                    ttft_ms = (time.time() - t0) * 1000.0
                token_buf += payload.get("delta", "")
            elif event_name == "regenerate":
                # A hook forced a full redo — the previously buffered draft is stale.
                token_buf = ""
            elif event_name == "error":
                had_error_event = True
            elif event_name == "final":
                final_text = payload.get("content")
                if final_text is None:
                    final_text = payload.get("answer")
                if final_text is None:
                    final_text = payload.get("message")
                meta = payload.get("metadata") or {}
                input_classification = meta.get("input_classification")
            event_name = None

        for raw_line in r:
            line = raw_line.decode("utf-8", errors="replace").rstrip("\n").rstrip("\r")
            if line == "":
                flush()
                continue
            if line.startswith("event:"):
                event_name = line[len("event:"):].strip()
            elif line.startswith("data:"):
                data_lines.append(line[len("data:"):].lstrip(" "))
            elif line.strip() == "[DONE]":
                break
        flush()

    return ttft_ms, (final_text if final_text is not None else token_buf), tools, input_classification, had_error_event


def one(item):
    """Một request full pipeline (gateway /ai/chat/stream) → dict record (hoặc error).

    429 KHÔNG phải lỗi fatal cho câu này: pacer ở _rate_limiter là tuyến phòng thủ
    chính (giữ dưới ~12 req/60s); nếu vẫn dính 429 (traffic khác chung IP/host), retry
    ngắn tối đa _MAX_429_RETRIES lần trước khi coi là lỗi cuối cùng cho câu này. 422 vẫn
    fatal-cho-câu-này ngay lập tức (require_public_input từ chối nội dung, không phải
    "chậm lại") — hành vi đó giữ nguyên như cũ.
    """
    t0 = time.time()
    attempt = 0
    retries_429 = 0
    while True:
        attempt += 1
        try:
            ttft, text, tools, input_classification, had_error_event = _stream_chat(item["input"])
            wall = (time.time() - t0) * 1000.0
            total = wall
            # No true token-count field in the gateway's SSE stream (unlike the old opencode
            # API's info.tokens.output). Approximate as len(text)//4 — same convention already
            # used elsewhere in this codebase (eval-standalone/scripts/perf_kpi.py: "tokens_out
            # ≈ len(answer)/4"). This is chars/4, NOT a count of "token" SSE chunk events —
            # chunk count would be a different, incomparable unit.
            toks = max(len(text or "") // 4, 1)
            rec = {"id": item["id"], "capability": item.get("capability"), "difficulty": item.get("difficulty"),
                   "ttftMs": ttft, "totalMs": total, "wallMs": round(wall), "tokens_out": toks,
                   "text": text, "error": None, "retries_429": retries_429}
            rec["tools"] = tools
            if input_classification is not None:
                rec["input_classification"] = input_classification
            if had_error_event:
                rec["degraded"] = True
            return rec
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt <= _MAX_429_RETRIES:
                retries_429 += 1
                time.sleep(3.0)
                continue
            if e.code == 422:
                err = "HTTP422:blocked_by_require_public_input"
            elif e.code == 429:
                # Hết số lần retry cho phép, vẫn 429 — gắn nhãn riêng để phân biệt với
                # lỗi HTTP chung, và để main() đếm riêng "429-final-failures".
                err = "HTTP429:rate_limited_after_retries"
            else:
                err = f"HTTPError({e.code}):{repr(e)[:140]}"
            return {"id": item["id"], "capability": item.get("capability"), "difficulty": item.get("difficulty"),
                    "ttftMs": None, "totalMs": None, "wallMs": round((time.time() - t0) * 1000),
                    "tokens_out": None, "text": "", "error": err, "retries_429": retries_429}
        except Exception as e:
            return {"id": item["id"], "capability": item.get("capability"), "difficulty": item.get("difficulty"),
                    "ttftMs": None, "totalMs": None, "wallMs": round((time.time() - t0) * 1000),
                    "tokens_out": None, "text": "", "error": repr(e)[:160], "retries_429": retries_429}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", required=True)
    ap.add_argument("--concurrency", type=int, default=10)
    ap.add_argument("--limit", type=int, default=0, help="0 = tất cả")
    ap.add_argument("--results", required=True)
    ap.add_argument("--summary", required=True)
    args = ap.parse_args()

    items = [json.loads(l) for l in Path(args.dataset).read_text(encoding="utf-8").splitlines() if l.strip()]
    if args.limit:
        items = items[:args.limit]

    print(f"=== load: concurrency={args.concurrency} n={len(items)} → {BASE} (POST /ai/chat/stream) ===", flush=True)
    t0 = time.time()
    recs = []
    with ThreadPoolExecutor(max_workers=args.concurrency) as ex:
        for i, rec in enumerate(ex.map(one, items), 1):
            recs.append(rec)
            if i % 10 == 0:
                n429ok = sum(1 for r in recs if not r["error"] and r.get("retries_429", 0) > 0)
                n429fail = sum(1 for r in recs if r["error"] == "HTTP429:rate_limited_after_retries")
                print(f"  {i}/{len(items)} done · 429_retried_ok={n429ok} 429_final_fail={n429fail}", flush=True)
    wall = time.time() - t0

    ok = [r for r in recs if not r["error"]]
    errs = [r for r in recs if r["error"]]
    blocked_422 = [r for r in errs if r["error"] == "HTTP422:blocked_by_require_public_input"]
    retried_429_ok = [r for r in ok if r.get("retries_429", 0) > 0]
    failed_429 = [r for r in errs if r["error"] == "HTTP429:rate_limited_after_retries"]
    ttfts = [r["ttftMs"] for r in ok if r["ttftMs"]]
    tps = []
    for r in ok:
        if r.get("tokens_out") and r.get("totalMs") and r.get("ttftMs"):
            gen = (r["totalMs"] - r["ttftMs"]) / 1000.0
            if gen > 0.3:
                tps.append(r["tokens_out"] / gen)
    summary = {
        "concurrency": args.concurrency, "n": len(recs), "ok": len(ok), "errors": len(errs),
        "wall_s": round(wall, 1), "rps": round(len(ok) / wall, 3) if wall else None,
        "ttft_ms_p50": pct(ttfts, 50), "ttft_ms_p95": pct(ttfts, 95),
        "ttft_in_900_1500_pct": round(100 * sum(1 for x in ttfts if 900 <= x <= 1500) / len(ttfts), 1) if ttfts else None,
        "tps_per_stream_p50": round(pct(tps, 50), 1) if tps else None,
        "tps_per_stream_mean": round(sum(tps) / len(tps), 1) if tps else None,
        "tps_in_20_35_pct": round(100 * sum(1 for x in tps if 20 <= x <= 35) / len(tps), 1) if tps else None,
        "tokens_out_mean": round(sum(r["tokens_out"] for r in ok if r["tokens_out"]) / max(1, sum(1 for r in ok if r["tokens_out"]))) if ok else None,
        "latency_total_p50_ms": pct([r["totalMs"] for r in ok if r["totalMs"]], 50),
        "latency_total_p95_ms": pct([r["totalMs"] for r in ok if r["totalMs"]], 95),
        "error_samples": [r["error"] for r in errs[:3]],
        # Extra diagnostic (not read by build_perf_summary.py, but a real finding per the
        # task spec): how many questions the require_public_input() guard blocked with 422.
        "blocked_422_count": len(blocked_422),
        # Rate-limit diagnostics: pacer should keep both of these near 0 under normal
        # conditions. retried_ok = hit 429 but a retry within this same question succeeded.
        # final_fail = still 429 after exhausting retries (error tagged HTTP429:...).
        "rate_limit_429_retried_ok_count": len(retried_429_ok),
        "rate_limit_429_final_fail_count": len(failed_429),
    }
    Path(args.results).parent.mkdir(parents=True, exist_ok=True)
    with open(args.results, "w", encoding="utf-8") as f:
        for r in recs:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    Path(args.summary).parent.mkdir(parents=True, exist_ok=True)
    Path(args.summary).write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
