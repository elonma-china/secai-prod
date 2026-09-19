#!/usr/bin/env python3
"""Fit Platt scaling + the uncertain-band cascade for the injection score (Ư2).

Pipeline: embed evals/sanitize/{overdefense,attacks}.jsonl (the held-out sets — deliberately
NOT the training set, so calibration targets the real-traffic-like distribution rather than
the in-distribution training scores) with the shipped head's embedding space → get the RAW
injection LOGIT (pre-sigmoid) per row via classifier._forward → fit Platt scaling (logit →
true label) using the ORIGINAL Platt 1999 target-smoothing (see ``_fit_platt`` — plain MLE on
hard 0/1 labels diverges when the data is perfectly separable, which ours is) → compute
calibrated scores for every row → the uncertain band is [max calibrated score among benign
rows, min calibrated score among attack rows] — the region with NO direct held-out evidence
either way, so anything landing there gets a second opinion from the LLM judge
(sanitize/cascade.py) instead of a threshold coin-flip.

HONESTY NOTE: as of 2026-09-01 the shipped head already separates this exact held-out set with
ZERO errors and a 0.24 raw-score margin (see evals/sanitize/report-2026-09-01.md) — so nothing
in THIS held-out set will actually fall inside the fitted band today. Calibration is forward-
looking robustness against future/OOD drift, not a fix for a currently-measured error. It also
double-dips: the same held-out rows both fit the Platt scaler AND derive the band boundaries —
acceptable for a small, curated set, but a proper 3-way train/calibrate/test split would be
more rigorous with more data (tracked as future work).

Writes sanitize/assets/calibration.json — a SEPARATE, additive asset. It never touches
head.npz/thresholds.json, and decode() only applies it when present; the cascade itself stays
inert unless BOTH this asset is shipped AND config.injection_cascade_enabled=True (default
False — opt in once verified against a live judge call; vast was down when this was built).
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from secai_gateway.sanitize.classifier import _forward, _load_head_uncached  # noqa: E402

DEFAULT_OUT = REPO / "src" / "secai_gateway" / "sanitize" / "assets" / "calibration.json"


def _read(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def embed_st(texts: list[str], model_id: str) -> np.ndarray:
    from sentence_transformers import SentenceTransformer

    m = SentenceTransformer(model_id)
    return np.asarray(m.encode(texts, batch_size=32, convert_to_numpy=True, show_progress_bar=False),
                      dtype=np.float32)


def embed_endpoint(texts: list[str]) -> np.ndarray:
    import os

    import httpx

    url = os.environ.get("EMBEDDING_API_URL", "").rstrip("/")
    model = os.environ.get("EMBEDDING_MODEL", "")
    key = os.environ.get("EMBEDDING_API_KEY", "")
    if not url or not model:
        raise SystemExit("endpoint backend needs EMBEDDING_API_URL + EMBEDDING_MODEL in env/.env")
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    vecs: list[list[float]] = []
    with httpx.Client(timeout=60) as c:
        for i in range(0, len(texts), 32):
            r = c.post(f"{url}/v1/embeddings", headers=headers,
                       json={"model": model, "input": texts[i:i + 32]})
            r.raise_for_status()
            vecs.extend(d["embedding"] for d in r.json()["data"])
    return np.asarray(vecs, dtype=np.float32)


def _fit_platt(logits: np.ndarray, labels: np.ndarray) -> tuple[float, float]:
    """Platt scaling with the original paper's target-smoothing (Platt 1999, §2): fit against
    ``t+ = (N+ + 1)/(N+ + 2)`` / ``t- = 1/(N- + 2)`` instead of hard 0/1 labels. Plain MLE
    logistic regression on hard labels DIVERGES (coefficients → ∞) when the data is perfectly
    linearly separable in logit space — exactly our case (0 held-out errors) — producing a
    near-step sigmoid that pushes calibrated scores to the extremes almost everywhere and (via
    this script's band definition) a nonsensically wide "uncertain" band. The smoothed targets
    keep the fit finite and well-behaved regardless of separation."""
    from scipy.optimize import minimize

    n_pos = int((labels == 1).sum())
    n_neg = int((labels == 0).sum())
    t_pos = (n_pos + 1) / (n_pos + 2)
    t_neg = 1.0 / (n_neg + 2)
    targets = np.where(labels == 1, t_pos, t_neg)

    def neg_log_likelihood(params: np.ndarray) -> float:
        a, b = params
        z = a * logits + b
        log_sig = -np.logaddexp(0, -z)          # log(sigmoid(z)), numerically stable
        log_one_minus_sig = -np.logaddexp(0, z)  # log(1 - sigmoid(z))
        return float(-np.sum(targets * log_sig + (1 - targets) * log_one_minus_sig))

    res = minimize(neg_log_likelihood, x0=[1.0, 0.0], method="Nelder-Mead",
                   options={"xatol": 1e-8, "fatol": 1e-8, "maxiter": 5000})
    return float(res.x[0]), float(res.x[1])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--embed", choices=["st", "endpoint"], default="st")
    ap.add_argument("--model", default="DuyTa/sec-embedding", help="model id for the st backend")
    ap.add_argument("--weights", default="", help="head assets dir override (default: packaged head)")
    ap.add_argument("--evdir", default=str(REPO / "evals" / "sanitize"))
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    args = ap.parse_args()

    head = _load_head_uncached(args.weights)
    if head is None or not head.trained:
        raise SystemExit("no trained head found — cannot calibrate")

    ev = Path(args.evdir)
    benign_rows = _read(ev / "overdefense.jsonl")
    attack_rows = _read(ev / "attacks.jsonl")
    texts = [r["text"] for r in benign_rows] + [r["text"] for r in attack_rows]
    labels = np.array([0] * len(benign_rows) + [1] * len(attack_rows))
    print(f"calibrating on {len(benign_rows)} benign + {len(attack_rows)} attack held-out rows"
         f" (embed={args.embed})")

    X = embed_st(texts, args.model) if args.embed == "st" else embed_endpoint(texts)

    logits = np.empty(len(texts), dtype=np.float64)
    for i, x in enumerate(X):
        xn = x / (float(np.linalg.norm(x)) or 1.0)
        _, lj, _ = _forward(head, xn.astype(np.float32))
        logits[i] = lj

    platt_a, platt_b = _fit_platt(logits, labels)

    calibrated = 1.0 / (1.0 + np.exp(-(platt_a * logits + platt_b)))
    benign_scores = calibrated[labels == 0]
    attack_scores = calibrated[labels == 1]
    band_low = float(benign_scores.max())
    band_high = float(attack_scores.min())
    if band_low >= band_high:
        print(f"WARNING: calibrated held-out scores overlap (benign_max={band_low:.4f} >= "
             f"attack_min={band_high:.4f}) — collapsing band to empty (no escalation) rather "
             "than ship an inverted range. Re-check the head/held-out sets.")
        band_low = band_high = 0.5

    out = {
        "platt_a": round(platt_a, 6),
        "platt_b": round(platt_b, 6),
        "band_low": round(band_low, 4),
        "band_high": round(band_high, 4),
        "fitted_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "embed_model": args.model if args.embed == "st" else head.embed_model,
        "n_benign": len(benign_rows),
        "n_attack": len(attack_rows),
        "note": (
            "Ư2 cascade. Platt scaling (original Platt 1999 target-smoothing, "
            "t+=(N+ +1)/(N+ +2), t-=1/(N- +2), fit via Nelder-Mead — see scripts/"
            "calibrate_injection_cascade.py::_fit_platt) on the injection sub-head's raw "
            "logit, fit on evals/sanitize/{overdefense,attacks}.jsonl (held-out, not training "
            "data). Plain MLE on hard 0/1 labels diverges on this perfectly-separable data "
            "(tried first, produced a near-step sigmoid and a nonsensical ~0.98-wide band); "
            "the smoothing keeps it well-behaved. band=[max calibrated benign score, min "
            "calibrated attack score] — the region with no direct held-out evidence either "
            "way. This score is used ONLY for injection_uncertain (band membership) — it never "
            "feeds the primary injection threshold decision, which stays on the raw sigmoid "
            "(thresholds.json's 0.45 was tuned and verified against that raw scale; see "
            "classifier.py::decode()). Inert unless config.injection_cascade_enabled=True "
            "(default False)."
        ),
    }
    Path(args.out).write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {args.out}")
    print(f"platt_a={platt_a:.4f} platt_b={platt_b:.4f}")
    print(f"calibrated benign scores: min={benign_scores.min():.4f} max={benign_scores.max():.4f}")
    print(f"calibrated attack scores: min={attack_scores.min():.4f} max={attack_scores.max():.4f}")
    print(f"uncertain band = [{band_low:.4f}, {band_high:.4f}]"
         f"  (width={band_high-band_low:.4f})")


if __name__ == "__main__":
    main()
