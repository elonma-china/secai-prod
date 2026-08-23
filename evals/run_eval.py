#!/usr/bin/env python3
"""Chạy 1 bộ eval qua gateway thật (HTTP), chấm điểm bằng graders.py, ghi JSONL + summary.

Dùng:
  python3 run_eval.py exploit --limit 100
  python3 run_eval.py chat translate report statistical_report bughunt --limit 100

Rate-limit CLIENT-side cố tình thấp hơn server (middleware.py route_limit) để không bao giờ
ăn 429 — xem ROUTE_LIMITS bên dưới, khớp bảng trong secai_gateway/api/middleware.py.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from collections import Counter, deque
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).parent))
from graders import GRADERS  # noqa: E402

HERE = Path(__file__).parent
SAMPLES_DIR = HERE / "samples"
RESULTS_DIR = HERE / "results"

ENDPOINTS = {
    "exploit": "/ai/exploit",
    "chat": "/ai/chat",
    "translate": "/ai/translate",
    "report": "/ai/generate-report",
    "statistical_report": "/ai/generate-statistical-report",
    "bughunt": "/ai/bughunt/stream",
}
# Server limit (middleware.py route_limit) trừ biên an toàn.
ROUTE_LIMITS = {"exploit": 17, "chat": 12, "translate": 8, "report": 8,
                "statistical_report": 8, "bughunt": 12}
CONCURRENCY = {"exploit": 8, "chat": 8, "translate": 5, "report": 5,
               "statistical_report": 5, "bughunt": 3}
TIMEOUT_S = {"exploit": 240, "chat": 90, "translate": 60, "report": 90,
             "statistical_report": 90, "bughunt": 240}
# 240s cho exploit (>poc_consolidate_timeout_s=180 nội bộ config.py) — quan sát: 1 case ăn
# ReadTimeout đúng 180.01s ở lần chạy trước, nghi do consolidation nội bộ tự hết giờ ở mốc
# đó rồi vẫn cần thêm thời gian; nới biên ngoài để không cắt ngang trước khi app tự fallback.


class RateGate:
    """Sliding-window limiter phía client — mô phỏng đúng thuật toán
    FixedWindowRateLimiter của server (state/ratelimit.py) để không bao giờ vượt quá."""

    def __init__(self, limit_per_min: int) -> None:
        self.limit = limit_per_min
        self.times: deque[float] = deque()
        self.lock = asyncio.Lock()

    async def acquire(self) -> None:
        while True:
            async with self.lock:
                now = time.monotonic()
                while self.times and self.times[0] <= now - 60:
                    self.times.popleft()
                if len(self.times) < self.limit:
                    self.times.append(now)
                    return
                sleep_for = 60 - (now - self.times[0]) + 0.05
            await asyncio.sleep(max(sleep_for, 0.05))


def load_samples(function: str, limit: int | None) -> list[dict]:
    path = SAMPLES_DIR / f"{function}.jsonl"
    rows = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    return rows[:limit] if limit else rows


def build_payload(function: str, sample: dict) -> dict:
    uid = f"eval-{sample['id']}"
    if function in ("exploit", "chat"):
        return {"message": sample["message"], "user_id": uid}
    if function == "translate":
        return {"message": sample["message"], "source_lang": sample["source_lang"],
                 "target_lang": sample["target_lang"], "user_id": uid}
    if function == "report":
        return {"logs": sample["logs"], "sections": sample["sections"], "user_id": uid}
    if function == "statistical_report":
        return {"from_date": sample["from_date"], "to_date": sample["to_date"],
                 "summary": sample["summary"], "detail": sample["detail"], "user_id": uid}
    raise ValueError(function)


async def call_json_endpoint(client: httpx.AsyncClient, base_url: str, function: str,
                              sample: dict, timeout: float) -> tuple[int, dict]:
    url = base_url + ENDPOINTS[function]
    payload = build_payload(function, sample)
    try:
        r = await client.post(url, json=payload, timeout=httpx.Timeout(timeout, connect=10))
    except Exception as exc:
        return 0, {"_error": f"{type(exc).__name__}: {exc}"}
    try:
        return r.status_code, r.json()
    except Exception:
        return r.status_code, {"_raw": r.text[:800]}


async def call_bughunt(client: httpx.AsyncClient, base_url: str, sample: dict,
                        timeout: float) -> tuple[int, dict]:
    url = base_url + ENDPOINTS["bughunt"]
    payload = {"target": sample["target"], "scope_note": sample.get("scope_note", ""),
               "run_cve_sweep": bool(sample.get("run_cve_sweep", False)),
               "enable_exploit": bool(sample.get("enable_exploit", False)),
               "user_id": f"eval-{sample['id']}"}
    events_seen: list[str] = []
    final_payload = None
    error_payload = None
    cur_event = None
    try:
        async with client.stream("POST", url, json=payload,
                                 timeout=httpx.Timeout(timeout, connect=10)) as r:
            status = r.status_code
            async for raw_line in r.aiter_lines():
                line = (raw_line or "").rstrip("\n")
                if line.startswith("event:"):
                    cur_event = line[len("event:"):].strip()
                    events_seen.append(cur_event)
                elif line.startswith("data:"):
                    data_str = line[len("data:"):].strip()
                    if data_str == "[DONE]":
                        continue
                    if cur_event == "final":
                        try:
                            final_payload = json.loads(data_str)
                        except Exception:
                            final_payload = {"_raw": data_str[:500]}
                    elif cur_event == "error":
                        try:
                            error_payload = json.loads(data_str)
                        except Exception:
                            error_payload = {"_raw": data_str[:500]}
    except Exception as exc:
        return 0, {"_error": f"{type(exc).__name__}: {exc}", "events_seen": events_seen}
    return status, {"events_seen": events_seen, "final": final_payload, "error": error_payload}


def grade_bughunt(sample: dict, status: int, body: dict, elapsed: float) -> dict:
    reasons: list[str] = []
    warnings: list[str] = []
    checks: dict[str, bool] = {}
    passed = True
    if status != 200:
        return {"sample_id": sample["id"], "passed": False, "checks": {"http_200": False},
                "reasons": [f"HTTP {status}: {body.get('_error', '')}"], "warnings": []}
    checks["http_200"] = True
    events = body.get("events_seen") or []
    if "final" in events:
        checks["completed"] = True
    else:
        checks["completed"] = False
        passed = False
        reasons.append("không thấy event 'final' (stream chưa hoàn tất/timeout)")
    final = body.get("final") or {}
    if "error" in events or final.get("ok") is False:
        checks["no_error_event"] = False
        passed = False
        reasons.append(f"có event 'error' hoặc final.ok=false: {body.get('error') or final.get('error')}")
    else:
        checks["no_error_event"] = True
    if "phase" not in events:
        warnings.append("không thấy event 'phase' nào (AgentHunt/Validate/...)")
    return {"sample_id": sample["id"], "passed": passed, "checks": checks,
            "reasons": reasons, "warnings": warnings}


async def run_function(function: str, base_url: str, limit: int | None,
                       concurrency: int | None, out_path: Path) -> dict:
    samples = load_samples(function, limit)
    if not samples:
        print(f"[{function}] KHÔNG có sample (chạy gen_samples.py trước)")
        return {"function": function, "total": 0}

    gate = RateGate(ROUTE_LIMITS[function])
    sem = asyncio.Semaphore(concurrency or CONCURRENCY[function])
    timeout = TIMEOUT_S[function]
    results: list[dict] = []
    done = 0
    passed_n = 0
    t_start = time.monotonic()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_f = out_path.open("w", encoding="utf-8")
    print_lock = asyncio.Lock()

    async def worker(client: httpx.AsyncClient, sample: dict) -> None:
        nonlocal done, passed_n
        async with sem:
            await gate.acquire()
            t0 = time.monotonic()
            if function == "bughunt":
                status, body = await call_bughunt(client, base_url, sample, timeout)
                grade = grade_bughunt(sample, status, body, time.monotonic() - t0)
            else:
                status, body = await call_json_endpoint(client, base_url, function, sample, timeout)
                grade = GRADERS[function](sample, status, body, time.monotonic() - t0).to_dict()
            elapsed = time.monotonic() - t0
        row = {"sample": sample, "http_status": status, "elapsed_s": round(elapsed, 2),
               "grade": grade, "response": body}
        results.append(row)
        async with print_lock:
            out_f.write(json.dumps(row, ensure_ascii=False) + "\n")
            out_f.flush()
            done += 1
            if grade["passed"]:
                passed_n += 1
            if done % 5 == 0 or done == len(samples):
                pct = 100.0 * passed_n / done
                wall = time.monotonic() - t_start
                print(f"[{function}] {done}/{len(samples)} pass={passed_n} "
                      f"({pct:.1f}%) elapsed={wall:.0f}s", flush=True)

    async with httpx.AsyncClient() as client:
        await asyncio.gather(*(worker(client, s) for s in samples))
    out_f.close()

    total = len(results)
    passed = sum(1 for r in results if r["grade"]["passed"])
    by_category: dict[str, list[dict]] = {}
    for r in results:
        cat = r["sample"].get("category", "?")
        by_category.setdefault(cat, []).append(r)
    reason_counter: Counter[str] = Counter()
    for r in results:
        for reason in r["grade"].get("reasons", []):
            reason_counter[reason.split(":")[0][:80]] += 1

    summary = {
        "function": function, "total": total, "passed": passed,
        "pass_rate": round(100.0 * passed / total, 2) if total else 0.0,
        "wall_s": round(time.monotonic() - t_start, 1),
        "by_category": {
            cat: {"total": len(rows), "passed": sum(1 for r in rows if r["grade"]["passed"]),
                  "pass_rate": round(100.0 * sum(1 for r in rows if r["grade"]["passed"]) / len(rows), 1)}
            for cat, rows in by_category.items()
        },
        "top_failure_reasons": reason_counter.most_common(15),
        "results_file": str(out_path),
    }
    summary_path = out_path.with_name(out_path.stem + "_summary.json")
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[{function}] DONE {passed}/{total} = {summary['pass_rate']}%  -> {summary_path}", flush=True)
    return summary


async def main_async(functions: list[str], base_url: str, limit: int | None,
                      concurrency: int | None) -> None:
    ts = int(time.time())
    # Mỗi chức năng có rate-limit bucket riêng (khác path) -> chạy SONG SONG, không tuần tự.
    all_summaries = await asyncio.gather(*(
        run_function(fn, base_url, limit, concurrency, RESULTS_DIR / f"{fn}_{ts}.jsonl")
        for fn in functions
    ))
    all_summaries = list(all_summaries)
    combined_path = RESULTS_DIR / f"_combined_{ts}_summary.json"
    combined_path.write_text(json.dumps(all_summaries, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n=== TỔNG HỢP -> {combined_path} ===")
    for s in all_summaries:
        print(f"  {s['function']:20s} {s.get('passed', 0):4d}/{s.get('total', 0):4d} "
              f"= {s.get('pass_rate', 0):6.2f}%")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("functions", nargs="+", choices=list(ENDPOINTS.keys()))
    ap.add_argument("--base-url", default="http://127.0.0.1:8131")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--concurrency", type=int, default=None)
    args = ap.parse_args()
    RESULTS_DIR.mkdir(exist_ok=True)
    asyncio.run(main_async(args.functions, args.base_url, args.limit, args.concurrency))


if __name__ == "__main__":
    main()
