"""Entity-level Recall/Precision/F1 scoring of the production PII detector against the
independent gold set (``datasets/pii_gold_v2.jsonl``).

Matching is entity-level per record (not exact char offsets): a gold span counts as
matched if ANY predicted span of the corresponding class normalizes to something that
contains, or is contained by, the gold value. This tolerates harmless boundary
differences (trailing punctuation, a captured label fragment) without inventing credit
the detector didn't earn — a total miss (obfuscated email, mis-shaped phone, missing
generic-hex key) still scores as a miss, which is the honest point of this suite.

Bucket mapping (per contract, IP folds into the Email/IP class):
    EMAIL_IP -> gold type in {EMAIL, IP},   predicted type in {"email", "ip"}
    PHONE    -> gold PHONE,                 predicted "phone"
    APIKEY   -> gold APIKEY,                predicted "api_key"
    PASSWORD -> gold PASSWORD,              predicted "password"
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

BUCKETS = ("EMAIL_IP", "PHONE", "APIKEY", "PASSWORD")

_GOLD_TO_BUCKET = {
    "EMAIL": "EMAIL_IP", "IP": "EMAIL_IP",
    "PHONE": "PHONE",
    "APIKEY": "APIKEY",
    "PASSWORD": "PASSWORD",
}
_PRED_TO_BUCKET = {
    "email": "EMAIL_IP", "ip": "EMAIL_IP",
    "phone": "PHONE",
    "api_key": "APIKEY",
    "password": "PASSWORD",
}

_STRIP_CHARS = re.compile(r"[\s.\-()\[\]]+")


def normalize(value: str) -> str:
    """Case-insensitive, whitespace/punctuation-collapsed normal form — tolerant enough
    to bridge "0912 345 678" vs "0912345678"-style formatting drift without conflating
    genuinely different values (a full value must still match, not just a fragment)."""
    return _STRIP_CHARS.sub("", value.strip().strip("\"'")).lower()


def _fuzzy_match(a: str, b: str) -> bool:
    na, nb = normalize(a), normalize(b)
    if not na or not nb:
        return False
    return na == nb or na in nb or nb in na


@dataclass
class Confusion:
    tp: int = 0
    fp: int = 0
    fn: int = 0
    fp_examples: list = field(default_factory=list)
    fn_examples: list = field(default_factory=list)

    def prf(self) -> tuple[float, float, float]:
        rec = 100.0 * self.tp / (self.tp + self.fn) if (self.tp + self.fn) else 100.0
        pre = 100.0 * self.tp / (self.tp + self.fp) if (self.tp + self.fp) else 100.0
        f1 = 2 * pre * rec / (pre + rec) if (pre + rec) else 0.0
        return round(rec, 2), round(pre, 2), round(f1, 2)


def score(gold_records: list[dict], detect_fn) -> dict:
    """Run ``detect_fn(text) -> list[{"type","value",...}]`` (the real detector's
    ``.detect``) over every gold record and return per-bucket + overall confusion stats
    plus a per-record evidence trail (for ``results/*.jsonl``)."""
    conf = {b: Confusion() for b in BUCKETS}
    evidence = []

    for rec in gold_records:
        text = rec["text"]
        gold_spans = rec.get("spans", [])
        try:
            pred_spans = detect_fn(text)
        except Exception as e:  # fail-closed: a detector crash is never a pass
            pred_spans = []
            evidence.append({"id": rec["id"], "error": f"{type(e).__name__}: {e}"})

        gold_by_bucket: dict[str, list[str]] = {b: [] for b in BUCKETS}
        for s in gold_spans:
            b = _GOLD_TO_BUCKET.get(s["type"].upper())
            if b:
                gold_by_bucket[b].append(s["value"])
        pred_by_bucket: dict[str, list[str]] = {b: [] for b in BUCKETS}
        for s in pred_spans:
            b = _PRED_TO_BUCKET.get(str(s.get("type", "")).lower())
            if b:
                pred_by_bucket[b].append(str(s.get("value", "")))

        rec_ev = {"id": rec["id"], "note": rec.get("note", ""), "per_bucket": {}}
        for b in BUCKETS:
            gvals = list(gold_by_bucket[b])
            pvals = list(pred_by_bucket[b])
            matched_gold = [False] * len(gvals)
            matched_pred = [False] * len(pvals)
            for pi, pv in enumerate(pvals):
                for gi, gv in enumerate(gvals):
                    if not matched_gold[gi] and _fuzzy_match(pv, gv):
                        matched_gold[gi] = True
                        matched_pred[pi] = True
                        conf[b].tp += 1
                        break
            for gi, ok in enumerate(matched_gold):
                if not ok:
                    conf[b].fn += 1
                    if len(conf[b].fn_examples) < 15:
                        conf[b].fn_examples.append({"id": rec["id"], "value": gvals[gi]})
            for pi, ok in enumerate(matched_pred):
                if not ok:
                    conf[b].fp += 1
                    if len(conf[b].fp_examples) < 15:
                        conf[b].fp_examples.append({"id": rec["id"], "value": pvals[pi]})
            if gvals or pvals:
                rec_ev["per_bucket"][b] = {"gold": gvals, "pred": pvals}
        evidence.append(rec_ev)

    per_bucket_result = {}
    overall_tp = overall_fp = overall_fn = 0
    for b in BUCKETS:
        rec_, pre_, f1_ = conf[b].prf()
        overall_tp += conf[b].tp
        overall_fp += conf[b].fp
        overall_fn += conf[b].fn
        per_bucket_result[b] = {
            "recall": rec_, "precision": pre_, "f1": f1_,
            "tp": conf[b].tp, "fp": conf[b].fp, "fn": conf[b].fn,
            "fp_examples": conf[b].fp_examples, "fn_examples": conf[b].fn_examples,
        }
    o_rec = 100.0 * overall_tp / (overall_tp + overall_fn) if (overall_tp + overall_fn) else 100.0
    o_pre = 100.0 * overall_tp / (overall_tp + overall_fp) if (overall_tp + overall_fp) else 100.0
    o_f1 = 2 * o_pre * o_rec / (o_pre + o_rec) if (o_pre + o_rec) else 0.0
    return {
        "per_bucket": per_bucket_result,
        "overall": {"recall": round(o_rec, 2), "precision": round(o_pre, 2), "f1": round(o_f1, 2),
                    "tp": overall_tp, "fp": overall_fp, "fn": overall_fn},
        "evidence": evidence,
    }
