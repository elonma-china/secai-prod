#!/usr/bin/env python3
"""Ư3 — Leave-one-hard-negative-family-out robustness check.

metrics.json's in-distribution numbers can't answer the question that actually matters for the
over-defense fix: does the round-1/round-2 hard-negative technique GENERALIZE across phrasing
families, or did the model just memorize each family's specific templates? This is the
Leave-One-Dataset-Out idea (arXiv:2602.14161) applied at the smallest useful grain we have:
instead of holding out unrelated published corpora (we don't have several), hold out an entire
MOF-style hard-negative FAMILY (scripts/build_sanitize_dataset.py::HN_*) from training — not
just some skeletons within it, ALL of it — and measure the false-positive rate on that family's
corresponding held-out over-defense category (evals/sanitize/overdefense.jsonl). If removing a
family regresses its category's FP back up, the family was doing real, family-specific work; if
FP stays low, the fix generalized from the OTHER families.

Family → eval category:
  hnreport   -> report-numbered
  hnaudit    -> audit-imperative (SAST wording is bundled into hnaudit's own templates)
  hncve      -> cve-detail
  hnpentest  -> pentest-writeup
(hnimp has no 1:1 eval category — its "list payloads for detection rules" framing doesn't map
cleanly onto one overdefense.jsonl cat; skipped here, noted as a gap.)

Nothing here touches the shipped assets — every retrain writes to a fresh temp dir; the
baseline row is scored from the CURRENTLY SHIPPED head (assets/), read-only.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from secai_gateway.sanitize.classifier import _load_head_uncached, decode  # noqa: E402
from secai_gateway.sanitize.gate import decide  # noqa: E402

FAMILY_TO_CATEGORY = {
    "hnreport": "report-numbered",
    "hnaudit": "audit-imperative",
    "hncve": "cve-detail",
    "hnpentest": "pentest-writeup",
}


class _Settings:
    embedding_model = "DuyTa/sec-embedding"
    input_classifier_injection_threshold = 0.0
    input_classifier_pii_threshold = 0.0
    input_classifier_offtopic_threshold = 0.0


def _read(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def _embed(texts: list[str], model_id: str) -> np.ndarray:
    from sentence_transformers import SentenceTransformer

    m = SentenceTransformer(model_id)
    return np.asarray(m.encode(texts, batch_size=32, convert_to_numpy=True, show_progress_bar=False),
                      dtype=np.float32)


def _score_category(weights_dir: str, category_rows: list[dict], attack_rows: list[dict],
                    model_id: str) -> dict:
    head = _load_head_uncached(weights_dir)
    if head is None or not head.trained:
        raise SystemExit(f"no trained head in {weights_dir}")
    cat_texts = [r["text"] for r in category_rows]
    atk_texts = [r["text"] for r in attack_rows]
    X_cat = _embed(cat_texts, model_id)
    X_atk = _embed(atk_texts, model_id)
    fp = 0
    for x in X_cat:
        v = decode(x, head, _Settings)
        d = decide(v, _Settings)
        if d.short_circuit:
            fp += 1
    detected = 0
    for x in X_atk:
        v = decode(x, head, _Settings)
        d = decide(v, _Settings)
        if d.action.value == "block_injection":
            detected += 1
    return {
        "category_n": len(category_rows), "category_fp": fp,
        "attack_n": len(attack_rows), "attack_detected": detected,
    }


def main() -> None:
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="DuyTa/sec-embedding")
    ap.add_argument("--families", default=",".join(FAMILY_TO_CATEGORY),
                    help="comma-separated hard-negative families to test")
    args = ap.parse_args()
    families = [f.strip() for f in args.families.split(",") if f.strip()]

    ev = REPO / "evals" / "sanitize"
    overdefense = _read(ev / "overdefense.jsonl")
    attacks = _read(ev / "attacks.jsonl")

    print(f"=== baseline (currently shipped head) ===")
    baseline: dict[str, dict] = {}
    for fam in families:
        cat = FAMILY_TO_CATEGORY[fam]
        cat_rows = [r for r in overdefense if r["cat"] == cat]
        res = _score_category("", cat_rows, attacks, args.model)
        baseline[fam] = res
        print(f"  {fam:10s} -> {cat:20s} FP={res['category_fp']}/{res['category_n']}  "
             f"recall={res['attack_detected']}/{res['attack_n']}")

    results: dict[str, dict] = {}
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        for fam in families:
            cat = FAMILY_TO_CATEGORY[fam]
            print(f"\n=== excluding {fam!r} (eval category: {cat!r}) ===")
            ds_path = tmp_path / f"dataset_excl_{fam}.jsonl"
            subprocess.run(
                [sys.executable, str(REPO / "scripts" / "build_sanitize_dataset.py"),
                 "--exclude-groups", fam, "--out", str(ds_path)],
                check=True, capture_output=True, text=True,
            )
            head_dir = tmp_path / f"head_excl_{fam}"
            head_dir.mkdir()
            train_proc = subprocess.run(
                [sys.executable,
                 str(REPO / "scripts" / "train_sanitize_head.py"),
                 "--embed", "st", "--model", args.model, "--head", "mixed",
                 "--data", str(ds_path), "--out", str(head_dir)],
                check=True, capture_output=True, text=True,
            )
            if train_proc.returncode != 0:
                print(train_proc.stdout[-2000:], train_proc.stderr[-2000:])
                raise SystemExit(f"retrain failed for {fam}")

            cat_rows = [r for r in overdefense if r["cat"] == cat]
            res = _score_category(str(head_dir), cat_rows, attacks, args.model)
            results[fam] = res
            print(f"  {fam:10s} -> {cat:20s} FP={res['category_fp']}/{res['category_n']}  "
                 f"recall={res['attack_detected']}/{res['attack_n']}")

    print("\n=== SUMMARY: does removing this family regress its own eval category? ===")
    print(f"{'family':10s} {'category':20s} {'baseline FP':>12s} {'excluded FP':>12s} {'verdict'}")
    for fam in families:
        cat = FAMILY_TO_CATEGORY[fam]
        b = baseline[fam]["category_fp"]
        e = results[fam]["category_fp"]
        n = baseline[fam]["category_n"]
        verdict = ("REGRESSES (family was necessary)" if e > b
                  else "generalizes from other families")
        print(f"{fam:10s} {cat:20s} {b:>9d}/{n:<3d} {e:>9d}/{n:<3d}  {verdict}")


if __name__ == "__main__":
    main()
