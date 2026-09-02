#!/usr/bin/env python3
"""Benchmark runner (Agent 2) — run the 1000-sample labelled test set THROUGH the REAL
production input-classification decision path against the LIVE serving endpoint.

This does NOT re-implement any classifier logic. It reuses the exact runtime pieces used by
``api/routes.py``:

  * ``security.require_public_input``      — PII / secret hard gate (HTTPException(422))
  * ``security.is_unsupported_language``   — non-Latin-script language gate (ratio=0.5)
  * ``sanitize.classifier.decode``         — numpy head over the live sec-embedding vector
  * ``sanitize.gate.decide``               — PASS / BLOCK_INJECTION / REFUSE_OFFTOPIC / GREET_CHITCHAT

Production precedence emulated for ``predicted_action`` (mirrors routes.py::complete → _chat_gate):
  1. PII gate  (require_public_input raises 422)         → "pii_block"
  2. LANGUAGE  (is_unsupported_language, ratio=0.5)      → "refuse_language"
  3. CLASSIFIER (embed → decode → decide):
        Action.BLOCK_INJECTION → "block_injection"
        Action.REFUSE_OFFTOPIC → "refuse_offtopic"
        Action.GREET_CHITCHAT  → "greet"   (shortcircuit off by default → never fires)
        Action.PASS            → "pass"    (also the fail-open result)

The Settings shim mirrors config.Settings production DEFAULTS (all classifier thresholds 0.0 →
decode() uses the head's own thresholds.json for injection/pii; decide() uses its 0.5 offtopic
fallback exactly as production does — see gate.decide `... or 0.5`). Cascade is OFF (deterministic).

All 1000 texts are embedded via the LIVE endpoint in batches (the sec-embedding server accepts a
list ``input``); this is the same transport routes.py uses per-request, just batched for speed —
embeddings are computed per input independently, so a batched vector is identical to a singleton
one. ``predicted_action`` still follows the short-circuit precedence above; ``gate_source`` records
which gate actually decided each row. Robust: a failing batch is retried once, then those rows are
recorded verdict_ok=False and fail open to "pass" (production behaviour) — the run never crashes.
"""
from __future__ import annotations

import json
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))  # make secai_gateway importable without install

from fastapi import HTTPException  # noqa: E402

from secai_gateway.sanitize.classifier import decode, load_head  # noqa: E402
from secai_gateway.sanitize.gate import Action, decide  # noqa: E402
from secai_gateway.security import (  # noqa: E402
    is_unsupported_language,
    require_public_input,
)

BENCH = REPO / "evals" / "sanitize" / "benchmark_1000.jsonl"
RESULTS = REPO / "evals" / "sanitize" / "benchmark_1000_results.jsonl"
SUMMARY = REPO / "evals" / "sanitize" / "benchmark_1000_summary.json"

BATCH = 64
LANG_RATIO = 0.5  # config default: language_gate_nonlatin_ratio

ACTION_MAP = {
    Action.PASS: "pass",
    Action.BLOCK_INJECTION: "block_injection",
    Action.REFUSE_OFFTOPIC: "refuse_offtopic",
    Action.GREET_CHITCHAT: "greet",
}


# ── minimal settings shim: mirrors config.Settings production DEFAULTS ─────────
class Settings:
    def __init__(self, env: dict[str, str]) -> None:
        self.embedding_api_url = env.get("EMBEDDING_API_URL", "").rstrip("/")
        self.embedding_model = env.get("EMBEDDING_MODEL", "DuyTa/sec-embedding")
        self.embedding_api_key = env.get("EMBEDDING_API_KEY", "")
        self.input_classifier_enabled = True
        self.input_classifier_weights_path = env.get("INPUT_CLASSIFIER_WEIGHTS_PATH", "")
        self.input_classifier_block_injection = True
        self.input_classifier_block_offtopic = True
        self.input_classifier_shortcircuit_chitchat = False
        # 0.0 → use the head's own thresholds.json (decode) / gate's 0.5 fallback (decide).
        self.input_classifier_injection_threshold = 0.0
        self.input_classifier_pii_threshold = 0.0
        self.input_classifier_offtopic_threshold = 0.0
        self.input_classifier_embed_timeout_s = 5.0
        # cascade OFF for the primary deterministic run.
        self.injection_cascade_enabled = False
        self.injection_cascade_timeout_s = 8.0
        self.language_gate_enabled = True
        self.language_gate_nonlatin_ratio = LANG_RATIO


def _load_env() -> dict[str, str]:
    import os

    env = dict(os.environ)
    dotenv = REPO / ".env"
    if dotenv.is_file():
        for line in dotenv.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env.setdefault(k.strip(), v.strip())
    return env


def _read_rows(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def _embed_batch(client, texts: list[str], s: Settings):
    """POST one batch to the live endpoint. Returns list[np.ndarray] (one per text)."""
    headers = {"Content-Type": "application/json"}
    if s.embedding_api_key:
        headers["Authorization"] = f"Bearer {s.embedding_api_key}"
    r = client.post(f"{s.embedding_api_url}/v1/embeddings", headers=headers,
                    json={"model": s.embedding_model, "input": texts})
    r.raise_for_status()
    data = r.json()["data"]
    # be defensive about ordering: the API returns an "index" per row.
    data = sorted(data, key=lambda d: d.get("index", 0))
    return [np.asarray(d["embedding"], dtype=np.float32) for d in data]


def embed_all(texts: list[str], s: Settings) -> tuple[list, dict]:
    """Embed every text via the LIVE endpoint in batches. Retry a failed batch once, then
    record None for those rows (→ verdict_ok=False, fail-open). Returns (vectors, stats)."""
    import httpx

    if not s.embedding_api_url or not s.embedding_model:
        raise SystemExit("endpoint backend needs EMBEDDING_API_URL + EMBEDDING_MODEL in .env")
    vecs: list = [None] * len(texts)
    stats = {"batches": 0, "batch_retries": 0, "failed_rows": 0}
    with httpx.Client(timeout=60) as client:
        for i in range(0, len(texts), BATCH):
            chunk = texts[i:i + BATCH]
            stats["batches"] += 1
            got = None
            for attempt in (1, 2):
                try:
                    got = _embed_batch(client, chunk, s)
                    if len(got) != len(chunk):
                        raise ValueError(f"embedding count {len(got)} != batch {len(chunk)}")
                    break
                except Exception as exc:  # noqa: BLE001 — retry once, then fail-open those rows
                    if attempt == 1:
                        stats["batch_retries"] += 1
                        print(f"  batch @{i} error ({type(exc).__name__}: {exc}); retrying once…",
                              file=sys.stderr)
                        time.sleep(2.0)
                    else:
                        print(f"  batch @{i} failed twice ({type(exc).__name__}: {exc}); "
                              f"{len(chunk)} rows → fail-open", file=sys.stderr)
                        got = None
            if got is None:
                stats["failed_rows"] += len(chunk)
            else:
                for j, v in enumerate(got):
                    vecs[i + j] = v
    return vecs, stats


def main() -> None:
    env = _load_env()
    s = Settings(env)
    head = load_head(s, force_reload=True)
    if head is None or not head.trained:
        raise SystemExit("no trained head found (assets/head.npz missing) — cannot benchmark")
    print(f"head: embed_model={head.embed_model} dim={head.dim} kind={head.kind} "
          f"thresholds={head.thresholds}")
    print(f"endpoint: {s.embedding_api_url}  model={s.embedding_model}")

    rows = _read_rows(BENCH)
    print(f"loaded {len(rows)} benchmark rows; embedding via LIVE endpoint (batch={BATCH})…")

    t0 = time.monotonic()
    vecs, embed_stats = embed_all([r["text"] for r in rows], s)
    print(f"embedded in {time.monotonic()-t0:.1f}s  stats={embed_stats}")

    results: list[dict] = []
    by_action: dict[str, list[int]] = defaultdict(lambda: [0, 0])   # action → [correct,total]
    by_function: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    by_lang: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    gate_counts = {"pii_block": 0, "refuse_language": 0, "classifier": 0}
    n_correct = 0
    n_failopen = 0

    for r, vec in zip(rows, vecs):
        text = r["text"]

        # ── precedence step 1: PII / secret hard gate (runs before _chat_gate) ──
        pii_gate = False
        try:
            require_public_input(text)
        except HTTPException:
            pii_gate = True

        # ── precedence step 2: language gate (first thing inside _chat_gate) ──
        lang_gate = is_unsupported_language(text, ratio=LANG_RATIO)

        # ── precedence step 3: model classifier (embed → decode → decide) ──
        verdict = None
        verdict_ok = False
        predicted_intent = None
        scores: dict = {}
        injection_uncertain = False
        classifier_action = "pass"  # fail-open default
        if vec is not None:
            verdict = decode(vec, head, s)
            verdict_ok = bool(verdict.ok)
            if verdict_ok:
                predicted_intent = verdict.intent
                scores = {k: round(float(v), 6) for k, v in verdict.scores.items()}
                injection_uncertain = bool(verdict.injection_uncertain)
                decision = decide(verdict, s)
                classifier_action = ACTION_MAP.get(decision.action, "pass")
            else:
                # decode returned unknown (e.g. dim mismatch) → decide → PASS (fail-open)
                classifier_action = "pass"
        if not verdict_ok:
            n_failopen += 1

        # ── resolve predicted_action by production precedence + record gate source ──
        if pii_gate:
            predicted_action = "pii_block"
            gate_source = "pii_block"
        elif lang_gate:
            predicted_action = "refuse_language"
            gate_source = "refuse_language"
        else:
            predicted_action = classifier_action
            gate_source = "classifier"
        gate_counts[gate_source] += 1

        expected_action = r["expected_action"]
        correct = predicted_action == expected_action
        if correct:
            n_correct += 1

        by_action[expected_action][0] += int(correct)
        by_action[expected_action][1] += 1
        by_function[r.get("function", "?")][0] += int(correct)
        by_function[r.get("function", "?")][1] += 1
        by_lang[r.get("lang", "?")][0] += int(correct)
        by_lang[r.get("lang", "?")][1] += 1

        results.append({
            "id": r["id"],
            "function": r.get("function"),
            "category": r.get("category"),
            "lang": r.get("lang"),
            "text": text,
            "expected_action": expected_action,
            "predicted_action": predicted_action,
            "correct": correct,
            "gate_source": gate_source,
            "expected_intent": r.get("expected_intent"),
            "predicted_intent": predicted_intent,
            "expected_injection": r.get("expected_injection"),
            "expected_pii": r.get("expected_pii"),
            "scores": scores,
            "injection_uncertain": injection_uncertain,
            "verdict_ok": verdict_ok,
        })

    # ── write per-sample results ──
    with RESULTS.open("w", encoding="utf-8") as f:
        for row in results:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    # ── summary ──
    def _pack(d: dict[str, list[int]]) -> dict:
        out = {}
        for k, (c, t) in sorted(d.items()):
            out[k] = {"total": t, "correct": c, "acc": round(c / t, 4) if t else 0.0}
        return out

    summary = {
        "n": len(results),
        "overall_accuracy": round(n_correct / len(results), 4) if results else 0.0,
        "correct": n_correct,
        "by_expected_action": _pack(by_action),
        "by_function": _pack(by_function),
        "by_lang": _pack(by_lang),
        "gate_source_counts": gate_counts,
        "embed_stats": embed_stats,
        "fail_open_rows": n_failopen,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "embed_backend": "endpoint",
        "cascade": "off",
        "head": {"embed_model": head.embed_model, "dim": head.dim, "kind": head.kind,
                 "thresholds": head.thresholds},
    }
    SUMMARY.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    # ── console summary ──
    print("\n=== BENCH SUMMARY ===")
    print(f"n={summary['n']}  overall_accuracy={summary['overall_accuracy']} "
          f"({n_correct}/{summary['n']})")
    print(f"fail-open rows (verdict_ok=False): {n_failopen}")
    print(f"gate_source_counts: {gate_counts}")
    print("\nby_expected_action (action → correct/total → acc):")
    for k, v in summary["by_expected_action"].items():
        print(f"  {k:16s} {v['correct']:4d}/{v['total']:<4d}  {v['acc']:.4f}")
    print(f"\nwrote results → {RESULTS}")
    print(f"wrote summary → {SUMMARY}")


if __name__ == "__main__":
    main()
