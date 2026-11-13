# /ai/v3/translate: counter memoization + shared specialist client (2026-11-13)

Branch `perf/translate-counter-cache` (commits `8b75e8dd`, `ce7fe066`).
Harness: `scripts/bench_translate_v3.py`.

## Root cause (measured in the production container)

| Cost per request | Measured |
| --- | --- |
| `verified_specialist_counter()` (read + sha256 + parse the 16 MB tokenizer, never cached) | **641 ms CPU** |
| New httpx client per chunk → new TCP/SSH channel to the GPU host | 288 ms vs 98 ms reused |
| `MultilangSettings()` construction | 1.2 ms (irrelevant) |

The CPU cost capped the route near 1.4 req/s; the model server (vLLM FP8 Hy-MT2-7B on an RTX 5090,
41 req/s at 96 concurrent through the sidecar) sat idle (vLLM saw 0–8 running requests).

## Change

1. `multilang/config.py` — memoize successful counters keyed by file stat identity; settings guard still runs
   every call; any file change forces full sha256 + parse; failures are not cached; single-flight; bounded.
2. `api/multilang/routes.py` — one lazily created keep-alive client (no proxy, no redirects, 4 s idle expiry
   < sidecar's 5 s) instead of one per call; closed on shutdown; injected clients respected.

## A/B result (same host, same tunnel, same MT; 20 req warm-up excluded; loopback smoke containers)

Control = production image `translate-hyperlink-fix-20261112`; candidate = that image + the two files
(`translate-ce7fe066-20261113`). Admission limits raised identically on both so the rate limiter does not interfere.

| Concurrency | Control req/s | Control p50 / p95 | Candidate (model calls=8) | Candidate (model calls=16) |
| ---: | ---: | ---: | ---: | ---: |
| 1  | 0.61 | 1.81 s / 1.90 s | 1.04 req/s, 1.07 s / 1.30 s | 1.03 req/s, 1.08 s / 1.29 s |
| 8  | 1.43 | 5.45 s / 6.92 s | 7.66 req/s, 1.09 s / 1.33 s | 7.57 req/s, 1.11 s / 1.31 s |
| 16 | 1.51 | 10.3 s / 17.3 s | 7.80 req/s, 1.97 s / 2.35 s | **14.23 req/s, 1.15 s / 1.37 s** |
| 32 | 1.43 | 21.9 s / 37.3 s | 7.85 req/s, 3.88 s / 4.29 s | **14.84 req/s, 2.06 s / 2.41 s** |

Control re-measured afterwards: c=1 1.84 s, c=8 1.47 req/s (no drift). All requests succeeded; the only
errors (25 × 429 at c=48) were the 600 req/min per-peer rate limit set for the benchmark.

Plateaus equal `MULTILANG_MAX_CONCURRENT_MODEL_CALLS` × ~1 s/call (8 → 7.8 req/s, 16 → 14.8 req/s), i.e. the
gateway is now limited by its own semaphore, not by CPU or by the model.

## Caveats

- The production image is a pinned base plus overlaid modules: its `api/multilang/routes.py` is older than
  `main` (no `/stream` support) while every other multilang file equals `main`. The candidate therefore applies
  the same routes hunks to the image's file instead of copying `main`'s. Rebuild from `main` only as a
  deliberate release (it changes streaming behaviour).
- `~/secai-translation-docker-release/release.json` was stale (older image/env than the running containers);
  do not `release.py deploy` from it without reconciling.
- Production rate limits (`MULTILANG_RATE_LIMIT_PER_MINUTE=120` per peer IP, `MAX_ACTIVE_REQUESTS=32`) become the
  next ceiling: 120/min ≈ 2 req/s per source IP.
- Single-chunk requests only. Multi-chunk documents also benefit (shared client) but were not re-benchmarked.
- Not separately ablated: the 0.74 s drop at c=1 is consistent with 0.64 s (counter) + ~0.1–0.19 s (connection).
