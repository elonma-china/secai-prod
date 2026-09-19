#!/usr/bin/env python3
"""Ư5 — PII detection comparison: current regex gate (require_public_input) vs GLiNER
(zero-shot NER), on the SAME PII-positive/negative rows used by eval_sanitize.py's PII probe.

Purpose: get REAL comparative numbers instead of just the literature's general claim
("hybrid regex+neural beats either alone") before deciding whether GLiNER is worth the
torch-in-runtime tradeoff (see docs/input-classifier-improvement-plan.md Ư5 section — GLiNER
needs torch, conflicting with the numpy-only air-gap serving runtime; this script is a dev-only,
offline exploration, NOT a production integration).

Ground truth: dataset.jsonl's own `pii` label (1 = row contains a synthetic secret via
scripts/build_sanitize_dataset.py::_fake_secret — openai/aws/pk/password/bearer/card/cccd/
phone/email/jwt/ghp shapes; 0 = benign).
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from secai_gateway.security import require_public_input  # noqa: E402
from fastapi import HTTPException  # noqa: E402

# Generic PII/secret label set for GLiNER's zero-shot prompt — intentionally broad (GLiNER
# needs no fine-tuning per label; it matches on the label STRING semantically).
GLINER_LABELS = [
    "email address", "phone number", "credit card number", "national id number",
    "api key", "password", "bearer token", "jwt token", "person name",
]


def _read(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def regex_gate_flags(text: str) -> bool:
    try:
        require_public_input(text)
        return False
    except HTTPException:
        return True


def main() -> None:
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="urchade/gliner_multi_pii-v1")
    ap.add_argument("--n-negative", type=int, default=200)
    ap.add_argument("--data", default=str(REPO / "evals" / "sanitize" / "dataset.jsonl"))
    args = ap.parse_args()

    rows = _read(Path(args.data))
    pos = [r for r in rows if r.get("pii") == 1]
    neg = [r for r in rows if r.get("pii") == 0][: args.n_negative]
    print(f"n_positive={len(pos)} n_negative={len(neg)}")

    print("\n=== regex gate (require_public_input) ===")
    reg_tp = sum(regex_gate_flags(r["text"]) for r in pos)
    reg_fp = sum(regex_gate_flags(r["text"]) for r in neg)
    print(f"recall={reg_tp}/{len(pos)}={reg_tp/len(pos):.3f}  "
         f"false-trip={reg_fp}/{len(neg)}={reg_fp/len(neg):.3f}")

    print(f"\n=== GLiNER ({args.model}) zero-shot NER ===")
    try:
        from gliner import GLiNER
    except ImportError:
        print("gliner not installed — skip. `uv pip install gliner`")
        return
    t0 = time.monotonic()
    model = GLiNER.from_pretrained(args.model)
    print(f"loaded in {time.monotonic()-t0:.1f}s")

    def gliner_flags(text: str) -> tuple[bool, float]:
        t0 = time.monotonic()
        ents = model.predict_entities(text, GLINER_LABELS, threshold=0.5)
        return bool(ents), (time.monotonic() - t0) * 1000

    lat = []
    gl_tp = 0
    for r in pos:
        hit, ms = gliner_flags(r["text"]); lat.append(ms)
        gl_tp += hit
    gl_fp = 0
    for r in neg:
        hit, ms = gliner_flags(r["text"]); lat.append(ms)
        gl_fp += hit
    import statistics

    print(f"recall={gl_tp}/{len(pos)}={gl_tp/len(pos):.3f}  "
         f"false-trip={gl_fp}/{len(neg)}={gl_fp/len(neg):.3f}")
    print(f"latency mean={statistics.mean(lat):.1f}ms p95="
         f"{sorted(lat)[int(len(lat)*0.95)]:.1f}ms")

    print("\n=== hybrid (regex OR GLiNER) ===")
    hyb_tp = hyb_fp = 0
    for r in pos:
        reg = regex_gate_flags(r["text"])
        gl, _ = gliner_flags(r["text"]) if not reg else (False, 0.0)
        hyb_tp += reg or gl
    for r in neg:
        reg = regex_gate_flags(r["text"])
        gl, _ = gliner_flags(r["text"]) if not reg else (False, 0.0)
        hyb_fp += reg or gl
    print(f"recall={hyb_tp}/{len(pos)}={hyb_tp/len(pos):.3f}  "
         f"false-trip={hyb_fp}/{len(neg)}={hyb_fp/len(neg):.3f}")


if __name__ == "__main__":
    main()
