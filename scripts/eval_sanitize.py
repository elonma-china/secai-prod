#!/usr/bin/env python3
"""Evaluate the input classifier on the HELD-OUT eval sets, THROUGH the production code path.

Unlike ``metrics.json`` (in-distribution, produced at train time), this scores the shipped (or a
candidate) head on ``evals/sanitize/{overdefense,attacks,indirect}.jsonl`` — sets never used for
training — so the numbers reflect generalisation, especially the over-defense (false-positive)
failure the KPI bench found in production.

It reuses the REAL runtime decision path — ``sanitize.classifier.decode`` + ``sanitize.gate.decide``
(no re-implementation) — and measures PII through the REAL input guard ``security.require_public_input``
(the 422 gate), which fixes the KPI R-SEC-PII methodology gap (that KPI scored a standalone
re-implementation, not this gate).

Backends (``--embed``): ``endpoint`` (POST the deployed sec-embedding, needs vast up + .env) or
``st`` (sentence-transformers offline, needs the ``train`` extra + the cached model). ``--dry-run``
skips embedding entirely (deterministic pseudo-vectors) so the harness I/O + code path can be
verified with NO network — the classification numbers are then meaningless and clearly marked.

Outputs a Markdown report (default ``evals/sanitize/report-<UTC-date>.md``).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))  # make secai_gateway importable without install

from secai_gateway.sanitize.classifier import decode, load_head  # noqa: E402
from secai_gateway.sanitize.gate import Action, decide  # noqa: E402
from secai_gateway.security import require_public_input  # noqa: E402
from fastapi import HTTPException  # noqa: E402


# ── minimal settings shim (reads .env / env; mirrors config.Settings field names) ──
class Settings:
    def __init__(self) -> None:
        env = _load_env()
        self.embedding_api_url = env.get("EMBEDDING_API_URL", "").rstrip("/")
        self.embedding_model = env.get("EMBEDDING_MODEL", "DuyTa/sec-embedding")
        self.embedding_api_key = env.get("EMBEDDING_API_KEY", "")
        self.input_classifier_enabled = True
        self.input_classifier_weights_path = env.get("INPUT_CLASSIFIER_WEIGHTS_PATH", "")
        self.input_classifier_block_injection = True
        self.input_classifier_block_offtopic = True
        self.input_classifier_shortcircuit_chitchat = False
        # 0 → use the head's own thresholds.json (do not override).
        self.input_classifier_injection_threshold = 0.0
        self.input_classifier_pii_threshold = 0.0
        self.input_classifier_offtopic_threshold = 0.0
        self.input_classifier_embed_timeout_s = 5.0


def _load_env() -> dict[str, str]:
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


# ── embedding backends ──
def embed_endpoint(texts: list[str], s: Settings) -> np.ndarray:
    import httpx

    if not s.embedding_api_url or not s.embedding_model:
        raise SystemExit("endpoint backend needs EMBEDDING_API_URL + EMBEDDING_MODEL in .env/env")
    headers = {"Content-Type": "application/json"}
    if s.embedding_api_key:
        headers["Authorization"] = f"Bearer {s.embedding_api_key}"
    vecs: list[list[float]] = []
    with httpx.Client(timeout=60) as c:
        for i in range(0, len(texts), 32):
            r = c.post(f"{s.embedding_api_url}/v1/embeddings", headers=headers,
                       json={"model": s.embedding_model, "input": texts[i:i + 32]})
            r.raise_for_status()
            vecs.extend(d["embedding"] for d in r.json()["data"])
    return np.asarray(vecs, dtype=np.float32)


def embed_st(texts: list[str], model_id: str) -> np.ndarray:
    from sentence_transformers import SentenceTransformer

    m = SentenceTransformer(model_id)
    return np.asarray(m.encode(texts, batch_size=32, convert_to_numpy=True), dtype=np.float32)


def embed_dry(texts: list[str], dim: int) -> np.ndarray:
    """Deterministic pseudo-embeddings from a text hash — NO network. Scores are meaningless."""
    out = np.zeros((len(texts), dim), dtype=np.float32)
    for i, t in enumerate(texts):
        h = hashlib.sha256(t.encode()).digest()
        seed = int.from_bytes(h[:8], "big")
        out[i] = np.random.RandomState(seed % (2**32)).standard_normal(dim).astype(np.float32)
    return out


def _read(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def _classify(rows: list[dict], X: np.ndarray, head, s: Settings) -> list[dict]:
    out = []
    for r, x in zip(rows, X):
        v = decode(x, head, s)
        d = decide(v, s)
        out.append({**r, "_ok": v.ok, "_intent": v.intent, "_inj_score": v.scores.get("injection"),
                    "_sec_score": v.scores.get("security"), "_off_score": v.scores.get("offtopic"),
                    "_action": d.action.value, "_blocked": d.short_circuit, "_label": d.label})
    return out


def _pct(n: int, d: int) -> str:
    return f"{100*n/d:.1f}%" if d else "—"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--embed", choices=["endpoint", "st", "dry"], default="endpoint")
    ap.add_argument("--model", default="DuyTa/sec-embedding", help="model id for st backend")
    ap.add_argument("--weights", default="", help="override assets dir (evaluate a candidate head)")
    ap.add_argument("--dry-run", action="store_true", help="alias for --embed dry (no network)")
    ap.add_argument("--evdir", default=str(REPO / "evals" / "sanitize"))
    ap.add_argument("--out", default="")
    args = ap.parse_args()
    backend = "dry" if args.dry_run else args.embed

    s = Settings()
    if args.weights:
        s.input_classifier_weights_path = args.weights
    head = load_head(s, force_reload=True)
    if head is None or not head.trained:
        raise SystemExit("no trained head found (assets/head.npz missing) — cannot evaluate")

    ev = Path(args.evdir)
    od = _read(ev / "overdefense.jsonl")
    atk = _read(ev / "attacks.jsonl")
    ind = _read(ev / "indirect.jsonl")

    def embed(texts: list[str]) -> np.ndarray:
        if backend == "dry":
            return embed_dry(texts, head.dim)
        if backend == "st":
            return embed_st(texts, args.model)
        return embed_endpoint(texts, s)

    od_r = _classify(od, embed([r["text"] for r in od]), head, s)
    atk_r = _classify(atk, embed([r["text"] for r in atk]), head, s)
    ind_r = _classify(ind, embed([r["text"] for r in ind]), head, s)

    # ── PII through the REAL guard (require_public_input → 422 on secret/PII) ──
    ds = ev / "dataset.jsonl"
    pii_pos, pii_neg = [], []
    if ds.is_file():
        for r in _read(ds):
            (pii_pos if r.get("pii") == 1 else pii_neg).append(r["text"])
    pii_neg = pii_neg[:200]

    def guard_blocks(t: str) -> bool:
        try:
            require_public_input(t)
            return False
        except HTTPException:
            return True

    pii_tp = sum(guard_blocks(t) for t in pii_pos)
    pii_fp = sum(guard_blocks(t) for t in pii_neg)

    # ── assemble report ──
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out_path = Path(args.out) if args.out else ev / f"report-{ts}.md"
    L: list[str] = []
    W = L.append
    W(f"# Input-classifier held-out eval — {ts}")
    W("")
    thr_show = {k: head.thresholds[k] for k in ("injection", "pii", "offtopic") if k in head.thresholds}
    W(f"- backend: **{backend}**  |  head: `{head.embed_model}` dim={head.dim} kind={head.kind}  "
      f"|  thresholds={json.dumps(thr_show)}")
    W(f"- weights: `{args.weights or 'packaged assets (shipped head)'}`")
    if backend == "dry":
        W("- ⚠️ **DRY-RUN**: pseudo-embeddings, NO network — classification numbers below are "
          "meaningless (this run only verifies the harness I/O + production code path).")
    W("")

    # over-defense FP-rate per cat (every row is benign security → any block = false positive)
    W("## Over-defense — false-positive rate by category (lower is better)")
    W("")
    W("| cat | n | blocked (FP) | FP-rate | mean inj | max inj |")
    W("|-----|--:|--:|--:|--:|--:|")
    by = defaultdict(list)
    for r in od_r:
        by[r["cat"]].append(r)
    tot_n = tot_fp = 0
    for cat in sorted(by):
        rs = by[cat]
        fp = sum(1 for r in rs if r["_blocked"])
        injs = [r["_inj_score"] for r in rs if r["_inj_score"] is not None]
        tot_n += len(rs); tot_fp += fp
        W(f"| {cat} | {len(rs)} | {fp} | {_pct(fp,len(rs))} | "
          f"{np.mean(injs):.3f} | {np.max(injs):.3f} |" if injs else
          f"| {cat} | {len(rs)} | {fp} | {_pct(fp,len(rs))} | — | — |")
    W(f"| **ALL** | {tot_n} | {tot_fp} | **{_pct(tot_fp,tot_n)}** | | |")
    W("")

    # injection recall per family (attacks) — must stay high
    W("## Injection recall by family (higher is better)")
    W("")
    W("| family | n | detected | recall | mean inj |")
    W("|--------|--:|--:|--:|--:|")
    fam = defaultdict(list)
    for r in atk_r:
        fam[r["cat"]].append(r)
    d_tot = n_tot = 0
    for f in sorted(fam):
        rs = fam[f]
        det = sum(1 for r in rs if r["_action"] == Action.BLOCK_INJECTION.value)
        injs = [r["_inj_score"] for r in rs if r["_inj_score"] is not None]
        d_tot += det; n_tot += len(rs)
        W(f"| {f} | {len(rs)} | {det} | {_pct(det,len(rs))} | {np.mean(injs):.3f} |")
    W(f"| **ALL** | {n_tot} | {d_tot} | **{_pct(d_tot,n_tot)}** | |")
    W("")

    # intent confusion: ground-truth `intent` field vs predicted argmax, over all labelled eval rows
    labels = ["security", "chitchat", "offtopic"]
    idx = {l: i for i, l in enumerate(labels)}
    conf = [[0, 0, 0] for _ in labels]
    for r in od_r + atk_r + ind_r:
        exp, pred = r.get("intent"), r.get("_intent")
        if exp in idx and pred in idx:
            conf[idx[exp]][idx[pred]] += 1
    W("## Intent confusion (expected ↓ / predicted →)")
    W("")
    W("| expected \\ pred | security | chitchat | offtopic |")
    W("|---|--:|--:|--:|")
    for l in labels:
        row = conf[idx[l]]
        W(f"| {l} | {row[0]} | {row[1]} | {row[2]} |")
    correct = sum(conf[i][i] for i in range(3))
    total = sum(sum(r) for r in conf)
    W("")
    W(f"- intent accuracy on labelled eval rows: **{_pct(correct, total)}** ({correct}/{total}).")
    W("")

    # indirect-injection gap tracker (expected miss)
    ind_det = sum(1 for r in ind_r if r["_action"] == Action.BLOCK_INJECTION.value)
    W("## Indirect injection (gap tracker — the user-input classifier does NOT inspect tool/RAG text)")
    W("")
    W(f"- detected {ind_det}/{len(ind_r)} ({_pct(ind_det,len(ind_r))}). Expected ~0: these payloads "
      "arrive via retrieved-doc / tool-output, a vector this classifier is not designed to cover. "
      "Tracked for a future spotlighting / tool-result-parsing / CaMeL-style defense, not scored here.")
    W("")

    # PII through the real guard
    W("## PII / secret via the REAL input guard `require_public_input` (fixes KPI R-SEC-PII gap)")
    W("")
    W(f"- probe = dataset.jsonl rows: {len(pii_pos)} secret/PII-positive, {len(pii_neg)} benign sampled.")
    W(f"- guard catches (422): **{pii_tp}/{len(pii_pos)}** positives ({_pct(pii_tp,len(pii_pos))} recall); "
      f"false-trips on benign: {pii_fp}/{len(pii_neg)} ({_pct(pii_fp,len(pii_neg))}).")
    W("- note: `require_public_input` is a regex secret/credential + long-identifier gate; it "
      "intentionally does NOT flag plain emails/short phones — so <100% recall over the broad PII set "
      "is expected and is exactly the behaviour the KPI number should be measured against (not a "
      "standalone re-implementation).")
    W("")

    # KPI known-FP before/after
    W("## KPI-bench known false positives — before/after")
    W("")
    W("| # | inj@0.25 (KPI, was BLOCKED) | inj now | action now | question |")
    W("|--:|--:|--:|:--|:--|")
    kfp = [r for r in od_r if r["cat"] == "kpi-known-fp"]
    for i, r in enumerate(kfp, 1):
        was = r.get("kpi_injection_score_at_0.25", "—")
        now = f"{r['_inj_score']:.3f}" if r["_inj_score"] is not None else "—"
        q = r["text"][:70].replace("\n", " ")
        W(f"| {i} | {was} | {now} | {r['_action']} | {q}… |")
    now_pass = sum(1 for r in kfp if not r["_blocked"])
    W("")
    W(f"- now PASS (un-blocked): **{now_pass}/{len(kfp)}**. Goal after retrain: all 11 PASS while the "
      "injection-recall table above stays ≥ baseline.")
    W("")
    W("---")
    W("_Scoring the model results (FP-drop, recall-hold, threshold retune) requires the embedding "
      "endpoint live; run `--embed endpoint` before vs. after `train_sanitize_head.py --embed endpoint`._")

    out_path.write_text("\n".join(L) + "\n", encoding="utf-8")
    print(f"wrote report → {out_path}")
    print(f"  over-defense FP: {tot_fp}/{tot_n} ({_pct(tot_fp,tot_n)})  |  "
          f"injection recall: {d_tot}/{n_tot} ({_pct(d_tot,n_tot)})  |  "
          f"PII guard recall: {pii_tp}/{len(pii_pos)}")


if __name__ == "__main__":
    main()
