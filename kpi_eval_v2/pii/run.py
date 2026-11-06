"""PII group runner — evaluates the REAL production PII/secret detector
(see ``detector_loader.py`` for which module and why) against an INDEPENDENT,
hand-authored gold set (``datasets/pii_gold_v2.jsonl``) — never generated from the
detector's own regex (that circularity is documented in
``kpi_bench/scripts/sanitization_kpi.py`` + ``kpi_bench/datasets/sanitization_pii.jsonl``,
which this suite deliberately does NOT reuse or extend).

Exposes ``run_pii() -> list[dict]`` (schema.make_result() dicts) per the run_all contract.
"""
from __future__ import annotations

import json

from .. import config, schema
from . import detector_loader, scorer

GOLD_PATH = config.DATASETS / "pii_gold_v2.jsonl"

_KPI_BY_BUCKET = {
    "EMAIL_IP": ("R2-PII-EMAIL-R", "R2-PII-EMAIL-P"),
    "PHONE": ("R2-PII-PHONE-R", "R2-PII-PHONE-P"),
    "APIKEY": ("R2-PII-KEY-R", "R2-PII-KEY-P"),
    "PASSWORD": ("R2-PII-PWD-R", "R2-PII-PWD-P"),
}


def _load_gold() -> list[dict]:
    rows = []
    with GOLD_PATH.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def run_pii() -> list[dict]:
    results: list[dict] = []

    try:
        gold = _load_gold()
    except FileNotFoundError as e:
        return [schema.make_result(
            "R2-PII-F1", None, method="(gold set missing)", basis=str(GOLD_PATH),
            notes=f"pii_gold_v2.jsonl not found: {e}")]

    try:
        det = detector_loader.load_detector()
        det_path = detector_loader.detector_path()
    except Exception as e:
        notes = f"real production detector.py could not be loaded: {type(e).__name__}: {e}"
        return [schema.make_result(kpi_id, None, method="(detector load failed)", basis="", notes=notes)
                for pair in _KPI_BY_BUCKET.values() for kpi_id in pair] + \
               [schema.make_result("R2-PII-F1", None, method="(detector load failed)", basis="", notes=notes)]

    n_gold_spans = sum(len(r.get("spans", [])) for r in gold)
    n_hard_neg = sum(1 for r in gold if not r.get("spans"))
    basis = (f"gold=datasets/pii_gold_v2.jsonl (independent, hand-authored, n={len(gold)} "
             f"records / {n_gold_spans} spans / {n_hard_neg} hard-negative), "
             f"detector={det_path}")

    result = scorer.score(gold, det.detect)

    # Evidence: full per-record prediction trail + confusion examples.
    ev_path = config.RESULTS / "pii_v2_evidence.jsonl"
    with ev_path.open("w", encoding="utf-8") as f:
        f.write(json.dumps({"detector_path": str(det_path), "gold_path": str(GOLD_PATH),
                             "n_gold_records": len(gold)}, ensure_ascii=False) + "\n")
        for row in result["evidence"]:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        f.write(json.dumps({"overall": result["overall"],
                             "per_bucket_summary": {
                                 b: {k: v for k, v in d.items() if k not in ("fp_examples", "fn_examples")}
                                 for b, d in result["per_bucket"].items()}},
                            ensure_ascii=False) + "\n")

    for bucket, (r_id, p_id) in _KPI_BY_BUCKET.items():
        d = result["per_bucket"][bucket]
        method = f"Entity-level match (fuzzy-normalized) vs independent gold, bucket={bucket}"
        extra = {"tp": d["tp"], "fp": d["fp"], "fn": d["fn"],
                 "fp_examples": d["fp_examples"][:5], "fn_examples": d["fn_examples"][:5]}
        results.append(schema.make_result(
            r_id, d["recall"], method=method, basis=basis,
            n=d["tp"] + d["fn"], evidence=str(ev_path), extra=extra))
        results.append(schema.make_result(
            p_id, d["precision"], method=method, basis=basis,
            n=d["tp"] + d["fp"], evidence=str(ev_path), extra=extra))

    o = result["overall"]
    results.append(schema.make_result(
        "R2-PII-F1", o["f1"],
        method="Micro-F1 across all 4 buckets (EMAIL_IP/PHONE/APIKEY/PASSWORD) vs independent gold",
        basis=basis, n=o["tp"] + o["fn"], evidence=str(ev_path),
        extra={"recall": o["recall"], "precision": o["precision"], "tp": o["tp"], "fp": o["fp"], "fn": o["fn"],
               "per_bucket": {b: {"recall": v["recall"], "precision": v["precision"], "f1": v["f1"]}
                              for b, v in result["per_bucket"].items()}}))

    return results
