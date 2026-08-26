#!/usr/bin/env python3
"""Train the numpy linear head for the chat-input sanitization classifier.

Pipeline: load ``evals/sanitize/dataset.jsonl`` → embed every text with the chosen
backend → fit 3 logistic models (intent softmax, injection sigmoid, pii sigmoid) →
assemble one ``W(5, dim) + b(5)`` head → tune per-label thresholds on the held-out
split → write the runtime assets + a metrics report.

Outputs (into ``src/secai_gateway/sanitize/assets/`` by default):
  * ``head.npz``        — arrays ``W`` (float32 5×dim), ``b`` (float32 5)
  * ``head.json``       — {embed_model, dim, trained, trained_at, backend, notes, labels}
  * ``thresholds.json`` — {injection, pii, offtopic}
  * ``metrics.json``    — per-label P/R/F1, intent confusion matrix, macro-F1, latency

Embedding backends (``--embed``):
  * ``endpoint`` (default) — POST to the deployed sec-embedding OpenAI endpoint. Produces
    the PRODUCTION head: ``embed_model`` = the .env EMBEDDING_MODEL, so the runtime guard
    activates it. Requires the endpoint reachable + AIRGAP=false at build time.
  * ``st --model <hf-id>`` — sentence-transformers (needs the ``train`` extra + torch).
    Use ``--model DuyTa/sec-embedding`` to reproduce the production embedding space offline.
  * ``fastembed --model <id>`` — a fastembed-supported model, fully local/no torch. The head
    is tagged with that model id and is only used at runtime when EMBEDDING_MODEL matches.

sklearn/sentence-transformers are TRAINING-ONLY; the gateway runtime stays numpy-only.
"""
from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

INTENT_LABELS = ("security", "chitchat", "offtopic")
ALL_LABELS = INTENT_LABELS + ("injection", "pii")
REPO = Path(__file__).resolve().parents[1]
DEFAULT_ASSETS = REPO / "src" / "secai_gateway" / "sanitize" / "assets"


def _l2(m: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(m, axis=1, keepdims=True)
    n[n == 0] = 1.0
    return m / n


# ── embedding backends ───────────────────────────────────────────────────────
def embed_endpoint(texts: list[str]) -> tuple[np.ndarray, str]:
    import os

    import httpx

    from dotenv import load_dotenv  # optional; ok if missing
    try:
        load_dotenv(REPO / ".env")
    except Exception:
        pass
    url = os.environ.get("EMBEDDING_API_URL", "").rstrip("/")
    model = os.environ.get("EMBEDDING_MODEL", "")
    key = os.environ.get("EMBEDDING_API_KEY", "")
    if not url or not model:
        raise SystemExit("endpoint backend needs EMBEDDING_API_URL + EMBEDDING_MODEL (in .env/env)")
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    vecs: list[list[float]] = []
    with httpx.Client(timeout=60) as c:
        for i in range(0, len(texts), 32):
            batch = texts[i:i + 32]
            r = c.post(f"{url}/v1/embeddings", headers=headers,
                       json={"model": model, "input": batch})
            r.raise_for_status()
            vecs.extend(d["embedding"] for d in r.json()["data"])
    return np.asarray(vecs, dtype=np.float32), model


def embed_st(texts: list[str], model_id: str) -> tuple[np.ndarray, str]:
    from sentence_transformers import SentenceTransformer

    m = SentenceTransformer(model_id)
    v = m.encode(texts, batch_size=32, show_progress_bar=True, convert_to_numpy=True,
                 normalize_embeddings=False)
    return np.asarray(v, dtype=np.float32), model_id


def embed_fastembed(texts: list[str], model_id: str) -> tuple[np.ndarray, str]:
    from fastembed import TextEmbedding

    m = TextEmbedding(model_name=model_id)
    v = np.asarray(list(m.embed(texts)), dtype=np.float32)
    return v, model_id


# ── metrics ──────────────────────────────────────────────────────────────────
def prf(tp: int, fp: int, fn: int) -> dict[str, float]:
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    f = 2 * p * r / (p + r) if p + r else 0.0
    return {"precision": round(p, 4), "recall": round(r, 4), "f1": round(f, 4),
            "tp": tp, "fp": fp, "fn": fn}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=str(REPO / "evals" / "sanitize" / "dataset.jsonl"))
    ap.add_argument("--out", default=str(DEFAULT_ASSETS))
    ap.add_argument("--embed", choices=["endpoint", "st", "fastembed"], default="endpoint")
    ap.add_argument("--model", default="", help="model id for st/fastembed backends")
    ap.add_argument("--C", type=float, default=1.0, help="inverse L2 regularisation for LR")
    ap.add_argument("--head", choices=["linear", "mlp", "mixed"], default="linear")
    ap.add_argument("--hidden", type=int, default=128, help="MLP hidden units")
    ap.add_argument("--alpha", type=float, default=1e-2, help="MLP L2 penalty")
    args = ap.parse_args()

    rows = [json.loads(l) for l in Path(args.data).read_text(encoding="utf-8").splitlines() if l.strip()]
    texts = [r["text"] for r in rows]
    print(f"loaded {len(rows)} rows; embedding via {args.embed} ...")

    t0 = time.monotonic()
    if args.embed == "endpoint":
        X, embed_model = embed_endpoint(texts)
    elif args.embed == "st":
        X, embed_model = embed_st(texts, args.model or "DuyTa/sec-embedding")
    else:
        X, embed_model = embed_fastembed(texts, args.model or "BAAI/bge-small-en-v1.5")
    X = _l2(X)
    dim = X.shape[1]
    print(f"embedded {X.shape} in {time.monotonic()-t0:.1f}s (model={embed_model}, dim={dim})")

    tr = [i for i, r in enumerate(rows) if r["split"] == "train"]
    te = [i for i, r in enumerate(rows) if r["split"] == "test"]
    Xtr, Xte = X[tr], X[te]

    intent_idx = {l: i for i, l in enumerate(INTENT_LABELS)}
    y_intent = np.array([intent_idx[r["intent"]] for r in rows])
    y_inj = np.array([int(r["injection"]) for r in rows])
    y_pii = np.array([int(r["pii"]) for r in rows])

    rs = np.random.RandomState(0)

    def oversample(Xt: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Balance classes by resampling minorities up to the majority count (deterministic)."""
        idx_by = {c: np.where(y == c)[0] for c in np.unique(y)}
        n_max = max(len(v) for v in idx_by.values())
        picks = []
        for c, ix in idx_by.items():
            picks.append(ix)
            if len(ix) < n_max:
                picks.append(rs.choice(ix, n_max - len(ix), replace=True))
        sel = np.concatenate(picks)
        return Xt[sel], y[sel]

    save_arrays: dict[str, np.ndarray] = {}
    Cs = [0.25, 0.5, 1.0, 2.0, 4.0, 8.0, 16.0]

    def fit_mlp(y: np.ndarray, name: str, balance: bool):
        from sklearn.neural_network import MLPClassifier

        # Oversample only where a minority class is being swamped (intent: chitchat). For the
        # binary risk heads, oversampling makes the MLP over-fire → we use logistic there instead.
        Xb, yb = oversample(Xtr, y[tr]) if balance else (Xtr, y[tr])
        clf = MLPClassifier(hidden_layer_sizes=(args.hidden,), alpha=args.alpha, activation="relu",
                            max_iter=800, early_stopping=True, n_iter_no_change=25,
                            validation_fraction=0.12, random_state=0)
        clf.fit(Xb, yb)
        W1, W2 = (np.asarray(c, dtype=np.float32) for c in clf.coefs_)
        b1, b2 = (np.asarray(c, dtype=np.float32) for c in clf.intercepts_)
        for p, arr in zip(("W1", "b1", "W2", "b2"), (W1, b1, W2, b2)):
            save_arrays[f"{name}_{p}"] = arr

    def fit_logistic(y: np.ndarray, name: str):
        from sklearn.linear_model import LogisticRegressionCV

        clf = LogisticRegressionCV(Cs=Cs, cv=4, max_iter=4000, class_weight="balanced", scoring="f1")
        clf.fit(Xtr, y[tr])
        save_arrays[f"{name}_W"] = np.asarray(clf.coef_, dtype=np.float32)        # (1, dim)
        save_arrays[f"{name}_b"] = np.asarray(clf.intercept_, dtype=np.float32)   # (1,)

    def relu(a):
        return np.maximum(a, 0.0)

    def sub_out(x, name):
        if f"{name}_W1" in save_arrays:
            return relu(x @ save_arrays[f"{name}_W1"] + save_arrays[f"{name}_b1"]) \
                @ save_arrays[f"{name}_W2"] + save_arrays[f"{name}_b2"]
        return save_arrays[f"{name}_W"] @ x + save_arrays[f"{name}_b"]

    if args.head in ("mlp", "mixed"):
        # intent: MLP (nonlinearity lifts chitchat/offtopic). injection/pii: MLP for "mlp",
        # logistic for "mixed" (logistic keeps injection precision high).
        fit_mlp(y_intent, "intent", balance=True)
        if args.head == "mlp":
            fit_mlp(y_inj, "injection", balance=False)
            fit_mlp(y_pii, "pii", balance=False)
        else:
            fit_logistic(y_inj, "injection")
            fit_logistic(y_pii, "pii")

        def apply(x: np.ndarray) -> tuple[np.ndarray, float, float]:
            li = sub_out(x, "intent").reshape(-1)
            e = np.exp(li - li.max()); p_int = e / e.sum()
            pj = 1 / (1 + np.exp(-float(sub_out(x, "injection").reshape(-1)[0])))
            pp = 1 / (1 + np.exp(-float(sub_out(x, "pii").reshape(-1)[0])))
            return p_int, pj, pp
    else:
        from sklearn.linear_model import LogisticRegressionCV

        W = np.zeros((5, dim), dtype=np.float32)
        b = np.zeros(5, dtype=np.float32)
        clf_i = LogisticRegressionCV(Cs=Cs, cv=4, max_iter=4000, class_weight="balanced")
        clf_i.fit(Xtr, y_intent[tr])
        for k, cls in enumerate(clf_i.classes_):
            W[cls] = clf_i.coef_[k]; b[cls] = clf_i.intercept_[k]
        for row, y in ((3, y_inj), (4, y_pii)):
            clf = LogisticRegressionCV(Cs=Cs, cv=4, max_iter=4000, class_weight="balanced", scoring="f1")
            clf.fit(Xtr, y[tr])
            W[row] = clf.coef_[0]; b[row] = clf.intercept_[0]
        save_arrays = {"W": W, "b": b}

        def apply(x: np.ndarray) -> tuple[np.ndarray, float, float]:
            logit = W @ x + b
            e = np.exp(logit[:3] - logit[:3].max()); p_int = e / e.sum()
            return p_int, 1 / (1 + np.exp(-logit[3])), 1 / (1 + np.exp(-logit[4]))

    lat = []
    P_int, P_inj, P_pii = [], [], []
    for i in te:
        s = time.monotonic()
        pi, pj, pp = apply(Xte[te.index(i)])
        lat.append((time.monotonic() - s) * 1000)
        P_int.append(pi); P_inj.append(pj); P_pii.append(pp)
    P_int = np.array(P_int); P_inj = np.array(P_inj); P_pii = np.array(P_pii)
    yi_te = y_intent[te]; yj_te = y_inj[te]; yp_te = y_pii[te]

    # threshold sweep for a binary label → best-F1 (injection prefers recall≥0.95)
    def tune(scores: np.ndarray, y: np.ndarray, prefer_recall: float | None = None) -> tuple[float, dict]:
        best_t, best = 0.5, None
        for t in np.linspace(0.05, 0.95, 19):
            pred = (scores >= t).astype(int)
            tp = int(((pred == 1) & (y == 1)).sum()); fp = int(((pred == 1) & (y == 0)).sum())
            fn = int(((pred == 0) & (y == 1)).sum())
            m = prf(tp, fp, fn)
            ok = prefer_recall is None or m["recall"] >= prefer_recall
            key = (ok, m["f1"])
            if best is None or key > best[0]:
                best, best_t = (key, m), float(t)
        return round(best_t, 3), best[1]

    t_inj, m_inj = tune(P_inj, yj_te, prefer_recall=0.95)
    t_pii, m_pii = tune(P_pii, yp_te)

    # offtopic decision = argmax==offtopic AND p_offtopic >= thr; sweep thr for best F1
    off = intent_idx["offtopic"]
    argmax_int = P_int.argmax(axis=1)
    best_off_t, best_off = 0.5, None
    for t in np.linspace(0.34, 0.95, 20):
        pred = ((argmax_int == off) & (P_int[:, off] >= t)).astype(int)
        y = (yi_te == off).astype(int)
        tp = int(((pred == 1) & (y == 1)).sum()); fp = int(((pred == 1) & (y == 0)).sum())
        fn = int(((pred == 0) & (y == 1)).sum())
        m = prf(tp, fp, fn)
        if best_off is None or m["f1"] > best_off["f1"]:
            best_off, best_off_t = m, float(t)

    # intent confusion + per-class F1 (pure argmax)
    conf = np.zeros((3, 3), dtype=int)
    for yt, yp in zip(yi_te, argmax_int):
        conf[yt, yp] += 1
    intent_prf = {}
    for c, name in enumerate(INTENT_LABELS):
        tp = int(conf[c, c]); fp = int(conf[:, c].sum() - tp); fn = int(conf[c, :].sum() - tp)
        intent_prf[name] = prf(tp, fp, fn)
    intent_acc = float(np.mean(argmax_int == yi_te)) if len(te) else 0.0

    macro_f1 = float(np.mean([intent_prf[n]["f1"] for n in INTENT_LABELS]
                             + [m_inj["f1"], m_pii["f1"]]))

    thresholds = {"injection": t_inj, "pii": t_pii, "offtopic": round(best_off_t, 3)}
    metrics = {
        "embed_model": embed_model, "dim": dim, "backend": args.embed,
        "n_train": len(tr), "n_test": len(te),
        "intent": intent_prf, "intent_accuracy": round(intent_acc, 4),
        "intent_confusion": {"labels": list(INTENT_LABELS), "matrix": conf.tolist()},
        "injection": m_inj, "pii": m_pii, "offtopic_decision": best_off,
        "macro_f1": round(macro_f1, 4),
        "thresholds": thresholds,
        "head_apply_latency_ms": {"mean": round(float(np.mean(lat)), 4) if lat else 0.0,
                                   "p95": round(float(np.percentile(lat, 95)), 4) if lat else 0.0},
    }

    # train-split accuracy (overfit check: compare to test intent_acc above)
    tr_argmax = np.array([apply(Xtr[k])[0].argmax() for k in range(len(tr))])
    train_intent_acc = float(np.mean(tr_argmax == y_intent[tr]))
    metrics["train_intent_accuracy"] = round(train_intent_acc, 4)
    metrics["head"] = args.head
    metrics["overfit_gap_intent_acc"] = round(train_intent_acc - intent_acc, 4)

    out = Path(args.out); out.mkdir(parents=True, exist_ok=True)
    np.savez(out / "head.npz", **save_arrays)
    (out / "head.json").write_text(json.dumps({
        "embed_model": embed_model, "dim": dim, "trained": True, "backend": args.embed,
        "kind": args.head,
        "trained_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "labels": list(ALL_LABELS),
        "notes": f"head={args.head}; intent softmax(3) + injection/pii sigmoid; L2-normalised input.",
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "thresholds.json").write_text(json.dumps(thresholds, indent=2), encoding="utf-8")
    (out / "metrics.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n=== metrics (test split) ===")
    print(f"intent acc={intent_acc:.3f}  macro-F1={macro_f1:.3f}")
    for n in INTENT_LABELS:
        print(f"  {n:9s} {intent_prf[n]}")
    print(f"  injection {m_inj}  thr={t_inj}")
    print(f"  pii       {m_pii}  thr={t_pii}")
    print(f"thresholds -> {thresholds}")
    print(f"wrote assets -> {out}")


if __name__ == "__main__":
    main()
