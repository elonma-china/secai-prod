"""Embedding + numpy linear head for chat-input classification.

Load-once singleton (thread-locked), mirroring ``retrieval/bm25.py::_load_sparse_model``.
The head is a single linear projection ``W(5, dim) @ x + b`` over an L2-normalised
embedding; rows 0..2 are the softmax intent, rows 3..4 the independent risk sigmoids.

Shipped assets (packaged via importlib.resources, like ``prompts/*.md``):

  * ``assets/head.npz``       — arrays ``W`` (float32, 5×dim) and ``b`` (float32, 5)
  * ``assets/head.json``      — {embed_model, dim, labels, trained, trained_at, notes}
  * ``assets/thresholds.json``— {injection, pii, offtopic} decision thresholds

An untrained/placeholder deployment simply omits ``head.npz`` (or sets
``head.json:trained=false``); every lookup then fails open.
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

# Row order in the head matrix. Intent rows are a softmax group; risk rows are
# independent sigmoids. Keep in sync with scripts/train_sanitize_head.py.
INTENT_LABELS: tuple[str, ...] = ("security", "chitchat", "offtopic")
RISK_LABELS: tuple[str, ...] = ("injection", "pii")
ALL_LABELS: tuple[str, ...] = INTENT_LABELS + RISK_LABELS

_DEFAULT_THRESHOLDS: dict[str, float] = {"injection": 0.5, "pii": 0.5, "offtopic": 0.5}


@dataclass(frozen=True, slots=True)
class InputVerdict:
    """One classification result for a chat prompt.

    ``ok=False`` means the classifier was unavailable and the caller must fail open
    (treat as PASS). ``scores`` holds per-label probabilities when ``ok``.
    """

    ok: bool
    intent: str            # security | chitchat | offtopic  (or "unknown" when not ok)
    injection: bool
    pii: bool
    scores: dict[str, float]
    latency_ms: float
    reason: str = ""       # why the verdict is unknown (for logs); "" on success

    @classmethod
    def unknown(cls, reason: str, latency_ms: float = 0.0) -> "InputVerdict":
        return cls(False, "unknown", False, False, {}, latency_ms, reason)

    def summary(self) -> str:
        """Compact, log-safe one-liner (no user text)."""
        if not self.ok:
            return f"unknown({self.reason})"
        flags = "".join(f"+{n}" for n, on in (("inj", self.injection), ("pii", self.pii)) if on)
        return f"{self.intent}{flags}"


@dataclass(frozen=True, slots=True)
class _Head:
    """Trained classifier head — numpy-only at inference. Three independent sub-heads
    (``intent`` → 3 logits softmax; ``injection``/``pii`` → 1 logit sigmoid), each held in
    ``subheads`` as a spec:

    * ``("linear", W, b)`` — ``W·x + b`` (W shape ``(out, dim)``).
    * ``("mlp", (W1, b1, W2, b2))`` — ``relu(x·W1 + b1)·W2 + b2``.

    A sub-head can independently be linear or MLP (the shipped default uses an MLP intent +
    logistic injection/pii). ``W``/``b`` hold the LEGACY single 5-row linear head
    (``W(5, dim)·x + b``) for backward compatibility; when ``subheads`` is set it wins.
    """

    dim: int
    embed_model: str         # embedding model the head was trained on (must match runtime)
    trained: bool
    thresholds: dict[str, float]
    kind: str = "linear"
    W: np.ndarray | None = None            # legacy 5-row linear
    b: np.ndarray | None = None
    subheads: dict[str, tuple] | None = None


_lock = threading.Lock()
_state: dict[str, Any] = {"loaded": False, "head": None, "path": None}


def _asset_bytes(name: str) -> bytes | None:
    try:
        res = files("secai_gateway.sanitize").joinpath("assets", name)
        if not res.is_file():
            return None
        return res.read_bytes()
    except (FileNotFoundError, ModuleNotFoundError, OSError):
        return None


def _read_json_asset(name: str, override_dir: Path | None) -> dict[str, Any]:
    if override_dir is not None:
        p = override_dir / name
        if p.is_file():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return {}
        return {}
    raw = _asset_bytes(name)
    if raw is None:
        return {}
    try:
        return json.loads(raw.decode("utf-8"))
    except ValueError:
        return {}


def _load_head_uncached(weights_path: str = "") -> _Head | None:
    override_dir = Path(weights_path).expanduser() if weights_path else None

    meta = _read_json_asset("head.json", override_dir)
    thresholds = {**_DEFAULT_THRESHOLDS, **_read_json_asset("thresholds.json", override_dir)}

    try:
        if override_dir is not None:
            npz_path = override_dir / "head.npz"
            if not npz_path.is_file():
                logger.info("input-classifier: no head.npz at %s → fail-open", npz_path)
                return None
            data = np.load(npz_path)
        else:
            raw = _asset_bytes("head.npz")
            if raw is None:
                logger.info("input-classifier: no packaged head.npz → fail-open")
                return None
            import io

            data = np.load(io.BytesIO(raw))
    except (OSError, ValueError) as exc:
        logger.warning("input-classifier: head load failed (%s) → fail-open", exc)
        return None

    embed_model = str(meta.get("embed_model") or "")
    trained = bool(meta.get("trained", True))
    keys = set(getattr(data, "files", []) or list(data.keys()))

    try:
        # Per-sub-head format: intent_* / injection_* / pii_* (each linear or mlp).
        if any(f"{n}_W1" in keys or f"{n}_W" in keys for n in ("intent", "injection", "pii")):
            subheads: dict[str, tuple] = {}
            for name in ("intent", "injection", "pii"):
                if f"{name}_W1" in keys:
                    subheads[name] = ("mlp", tuple(np.asarray(data[f"{name}_{p}"], dtype=np.float32)
                                                   for p in ("W1", "b1", "W2", "b2")))
                elif f"{name}_W" in keys:
                    subheads[name] = ("linear", np.asarray(data[f"{name}_W"], dtype=np.float32),
                                      np.asarray(data[f"{name}_b"], dtype=np.float32))
                else:
                    raise KeyError(f"missing sub-head {name}")
            k0, s0 = subheads["intent"][0], subheads["intent"]
            dim = int(meta.get("dim") or (s0[1].shape[0] if k0 == "mlp" else s0[1].shape[1]))
            kind = str(meta.get("kind") or "mixed")
            return _Head(dim=dim, embed_model=embed_model, trained=trained,
                         thresholds=thresholds, kind=kind, subheads=subheads)
        # Legacy 5-row linear.
        W = np.asarray(data["W"], dtype=np.float32)
        b = np.asarray(data["b"], dtype=np.float32)
    except (ValueError, KeyError, IndexError) as exc:
        logger.warning("input-classifier: head parse failed (%s) → fail-open", exc)
        return None

    if W.ndim != 2 or W.shape[0] != len(ALL_LABELS) or b.shape[0] != len(ALL_LABELS):
        logger.warning("input-classifier: head shape %s/%s unexpected → fail-open",
                       W.shape, b.shape)
        return None
    dim = int(meta.get("dim") or W.shape[1])
    if W.shape[1] != dim:
        dim = int(W.shape[1])
    return _Head(dim=dim, embed_model=embed_model, trained=trained, thresholds=thresholds,
                 kind="linear", W=W, b=b)


def load_head(settings: Any | None = None, *, force_reload: bool = False) -> _Head | None:
    """Return the cached head (or None). Thread-safe lazy singleton."""
    weights_path = str(getattr(settings, "input_classifier_weights_path", "") or "")
    if not force_reload and _state.get("loaded") and _state.get("path") == weights_path:
        return _state.get("head")
    with _lock:
        if not force_reload and _state.get("loaded") and _state.get("path") == weights_path:
            return _state.get("head")
        head = _load_head_uncached(weights_path)
        _state["head"] = head
        _state["path"] = weights_path
        _state["loaded"] = True
        return head


def reset_cache() -> None:
    """Drop the cached head (tests / after retraining)."""
    with _lock:
        _state["loaded"] = False
        _state["head"] = None
        _state["path"] = None


# ── fail-open observability (ops hardening) ──────────────────────────────────
# The guard fails open by design, which is safe but SILENT: a misconfig (e.g. serving loads a
# different embedding model than EMBEDDING_MODEL) disables the guard on every request while
# /health still says ok (real incident, CLAUDE.md 2026-08-23). These two helpers make that
# visible: a boot-time self-check and a process-wide unknown-rate counter.
_stats_lock = threading.Lock()
_verdict_stats: dict[str, int] = {"total": 0, "unknown": 0}


def note_verdict(ok: bool) -> None:
    """Record one classification outcome for the fail-open rate metric."""
    with _stats_lock:
        _verdict_stats["total"] += 1
        if not ok:
            _verdict_stats["unknown"] += 1


def verdict_stats() -> dict[str, float]:
    """Return {total, unknown, unknown_rate}. A spiking unknown_rate = silent fail-open."""
    with _stats_lock:
        t, u = _verdict_stats["total"], _verdict_stats["unknown"]
    return {"total": t, "unknown": u, "unknown_rate": (u / t) if t else 0.0}


def reset_stats() -> None:
    with _stats_lock:
        _verdict_stats["total"] = 0
        _verdict_stats["unknown"] = 0


def startup_head_check(head: "_Head | None", embedding_model: str) -> str | None:
    """Return a loud warning string if the classifier will silently fail open, else ``None``.

    Called at app startup so an operator sees the misconfiguration at boot instead of discovering
    (much later) that every chat request quietly bypassed the guard. Pure/sync — unit-testable.
    """
    if head is None:
        return "input-classifier: no trained head packaged → guard is OFF (fail-open every request)"
    if not head.trained:
        return "input-classifier: head present but trained=false → guard is OFF (fail-open)"
    rt = str(embedding_model or "")
    if head.embed_model and rt and head.embed_model != rt:
        return (f"input-classifier: EMBED-MODEL MISMATCH head={head.embed_model!r} != "
                f"EMBEDDING_MODEL={rt!r} → guard fails open on EVERY request (see CLAUDE.md)")
    return None


def _softmax(v: np.ndarray) -> np.ndarray:
    m = float(np.max(v))
    e = np.exp(v - m)
    s = float(np.sum(e))
    return e / s if s > 0 else np.full_like(v, 1.0 / len(v))


def _sigmoid(x: float) -> float:
    if x >= 0:
        return 1.0 / (1.0 + float(np.exp(-x)))
    z = float(np.exp(x))
    return z / (1.0 + z)


def _apply_spec(spec: tuple, x: np.ndarray) -> np.ndarray:
    """Evaluate one sub-head spec on x → output logit vector."""
    if spec[0] == "mlp":
        W1, b1, W2, b2 = spec[1]
        return np.maximum(x @ W1 + b1, 0.0) @ W2 + b2
    W, b = spec[1], spec[2]              # linear: W (out, dim)
    return W @ x + b


def _forward(head: _Head, x: np.ndarray) -> tuple[np.ndarray, float, float]:
    """Return (intent_logits(3,), injection_logit, pii_logit) for any head representation."""
    if head.subheads is not None:
        li = _apply_spec(head.subheads["intent"], x).reshape(-1)
        lj = float(_apply_spec(head.subheads["injection"], x).reshape(-1)[0])
        lp = float(_apply_spec(head.subheads["pii"], x).reshape(-1)[0])
        return li, lj, lp
    logits = head.W @ x + head.b        # legacy 5-row linear
    return logits[:3], float(logits[3]), float(logits[4])


def decode(vec: Any, head: _Head, settings: Any | None = None) -> InputVerdict:
    """Apply the head to one embedding vector. Pure/sync — the unit-testable core."""
    x = np.asarray(vec, dtype=np.float32).reshape(-1)
    if x.shape[0] != head.dim:
        return InputVerdict.unknown(f"dim-mismatch:{x.shape[0]}!={head.dim}")
    norm = float(np.linalg.norm(x))
    if norm > 0:
        x = x / norm
    intent_logits, inj_logit, pii_logit = _forward(head, x)
    p_intent = _softmax(intent_logits)
    p_inj = _sigmoid(inj_logit)
    p_pii = _sigmoid(pii_logit)
    intent_idx = int(np.argmax(p_intent))

    thr = dict(head.thresholds)
    if settings is not None:
        for lbl in ("injection", "pii", "offtopic"):
            ov = getattr(settings, f"input_classifier_{lbl}_threshold", None)
            if isinstance(ov, (int, float)) and ov > 0:
                thr[lbl] = float(ov)

    scores = {INTENT_LABELS[i]: float(p_intent[i]) for i in range(3)}
    scores["injection"] = p_inj
    scores["pii"] = p_pii
    return InputVerdict(
        ok=True,
        intent=INTENT_LABELS[intent_idx],
        injection=p_inj >= thr["injection"],
        pii=p_pii >= thr["pii"],
        scores=scores,
        latency_ms=0.0,
    )


async def classify_input(
    text: str,
    *,
    settings: Any,
    client: Any | None = None,
) -> InputVerdict:
    """Classify one chat prompt. Fail-open on any error (returns ``ok=False``)."""
    t0 = time.monotonic()
    if not (text or "").strip():
        return InputVerdict.unknown("empty-input")

    head = load_head(settings)
    if head is None or not head.trained:
        return InputVerdict.unknown("head-missing")

    # The head is only valid in the embedding space it was trained on. If the deployment
    # points at a different embedding model, refuse to classify (fail-open) rather than
    # applying the head to out-of-distribution vectors.
    runtime_model = str(getattr(settings, "embedding_model", "") or "")
    if head.embed_model and runtime_model and head.embed_model != runtime_model:
        return InputVerdict.unknown(f"embed-model-mismatch:{head.embed_model}!={runtime_model}")

    timeout = float(getattr(settings, "input_classifier_embed_timeout_s", 5.0) or 5.0)
    try:
        from ..retrieval.embedding import embed

        if client is None:
            import httpx

            async with httpx.AsyncClient() as c:
                vec = await asyncio.wait_for(embed(c, text), timeout=timeout)
        else:
            vec = await asyncio.wait_for(embed(client, text), timeout=timeout)
    except Exception as exc:  # noqa: BLE001 — fail-open by design (timeouts included)
        return InputVerdict.unknown(f"embed-failed:{type(exc).__name__}",
                                    (time.monotonic() - t0) * 1000)

    verdict = decode(vec, head, settings)
    latency = (time.monotonic() - t0) * 1000
    if not verdict.ok:
        return InputVerdict.unknown(verdict.reason, latency)
    return InputVerdict(
        ok=True,
        intent=verdict.intent,
        injection=verdict.injection,
        pii=verdict.pii,
        scores=verdict.scores,
        latency_ms=latency,
    )
