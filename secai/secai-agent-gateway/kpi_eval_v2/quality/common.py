"""Shared helpers for the quality group: gateway SSE client, production-parity hybrid
retrieval (dense bge/sec-embedding + BM25 sparse -> RRF, rerank) WITHOUT the exact-CVE-id
oracle shortcut, fail-closed KB existence lookup, and gold-set loaders.

Nothing here imports the gateway's own `src/secai_gateway` package — this is a clean-room
re-implementation of the HTTP contracts (verified live against :8131 / Qdrant / vast serving
during recon), so kpi_eval_v2 stays fully isolated from production code per the task rules.
"""
from __future__ import annotations

import json
import re
import socket
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .. import config

CVE_RE = re.compile(r"CVE-\d{4}-\d{4,7}", re.IGNORECASE)
CWE_RE = re.compile(r"CWE-\d+", re.IGNORECASE)

RRF_K = 60
CANDIDATE_LIMIT = 20  # dense/bm25 candidates fetched before RRF fusion (matches production)
BM25_MODEL = "Qdrant/bm25"
BM25_VECTOR = "bm25"
BM25_LOCAL_MODEL_DIR = "/home/ict/AIEngines/secai/secai-data/_state/bm25_local_model"

# Broad refusal / "not found" detection (vi + en) — used for the hallucination fake-CVE probe.
# Deliberately NOT a 5-phrase allowlist: ~20 phrasings across both languages.
REFUSAL_MARKERS = [
    "không tìm thấy", "không tồn tại", "không có thông tin", "không có dữ liệu",
    "chưa được index", "chưa index", "không có trong", "không xuất hiện trong",
    "không chính xác", "có thể là lỗi", "sai chính tả", "có thể bạn muốn nói",
    "không rõ", "chưa có bản ghi", "không có bản ghi", "không được ghi nhận",
    "no information", "not found", "does not exist", "doesn't exist", "no record",
    "no results", "unable to find", "cannot find", "can't find", "no data available",
    "not a valid cve", "no such cve", "i could not find", "i couldn't find",
    "invalid cve", "no matching", "did not find", "doesn't appear to exist",
]


# ── Qdrant reachability (docker-internal hostname `qdrant` doesn't resolve from the host;
#    config.py deliberately reads whatever secai-docker/.env says, which is the in-container
#    value — fall back to the mapped host port without touching the shared config module). ──
def _reachable(host: str, port: int, timeout: float = 2.0) -> bool:
    try:
        socket.create_connection((host, port), timeout=timeout).close()
        return True
    except OSError:
        return False


def resolve_qdrant_url() -> str:
    base = config.QDRANT_URL.rstrip("/")
    parsed = urlparse(base)
    host, port = parsed.hostname or "127.0.0.1", parsed.port or 6333
    if _reachable(host, port):
        return base
    if _reachable("127.0.0.1", port):
        return f"{parsed.scheme}://127.0.0.1:{port}"
    return base  # let the caller's request fail loudly/honestly rather than silently swap


QDRANT_URL = resolve_qdrant_url()
QDRANT_COLLECTION = "cve_kb_hybrid"  # matches src/secai_gateway/config.py Settings.qdrant_collection


def _post(url: str, body: dict, timeout: float = 30, key: str = "") -> dict:
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = "Bearer " + key
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


# ── embed / rerank (vast serving — verified live during recon) ─────────────────────────────
def embed(text: str) -> list[float]:
    r = _post(f"{config.EMBED_URL}/v1/embeddings", {"model": config.EMBED_MODEL, "input": text},
              timeout=30, key=config.EMBED_KEY)
    return r["data"][0]["embedding"]


def rerank(query: str, docs: list[str]) -> list[dict]:
    if not docs:
        return []
    r = _post(f"{config.RERANK_URL}/v1/rerank",
              {"model": config.RERANK_MODEL, "query": query, "documents": docs, "top_n": len(docs)},
              timeout=60, key=config.RERANK_KEY)
    return r.get("results", [])


# ── Qdrant dense + BM25 sparse (fastembed), RRF-fused — NO exact-CVE-id oracle shortcut ────
def dense_search(vector: list[float], limit: int = CANDIDATE_LIMIT) -> list[dict]:
    # knowledge chunks (kind cheatsheet/requirement/mitigation) share this collection: acceptance KPIs measure CVE retrieval only
    body = {"filter": {"must_not": [{"key": "kind", "match": {"any": ["cheatsheet", "requirement", "mitigation"]}}]}, "vector": {"name": "dense", "vector": vector}, "limit": limit, "with_payload": True}
    r = _post(f"{QDRANT_URL}/collections/{QDRANT_COLLECTION}/points/search", body, timeout=30)
    out = []
    for p in r.get("result", []):
        pl = p.get("payload") or {}
        out.append({"id": str(p["id"]), "score": p.get("score"), "cve_id": (pl.get("cve_id") or "").upper(),
                    "chunk_kind": pl.get("chunk_kind"), "text": pl.get("text") or ""})
    return out


def fetch_payloads(ids: list[str]) -> dict[str, dict]:
    if not ids:
        return {}
    r = _post(f"{QDRANT_URL}/collections/{QDRANT_COLLECTION}/points", {"ids": ids, "with_payload": True}, timeout=30)
    out = {}
    for p in r.get("result", []):
        pl = p.get("payload") or {}
        out[str(p["id"])] = {"cve_id": (pl.get("cve_id") or "").upper(), "chunk_kind": pl.get("chunk_kind"),
                              "text": pl.get("text") or ""}
    return out


def kb_count(key: str, value: str, timeout: float = 15) -> int | None:
    """Fail-closed Qdrant point count. Returns None on any error (caller must NOT treat
    None as 0/absent — it means "unknown", to be excluded from the denominator)."""
    try:
        r = _post(f"{QDRANT_URL}/collections/{QDRANT_COLLECTION}/points/count",
                  {"filter": {"must": [{"key": key, "match": {"value": value}}]}, "exact": False}, timeout=timeout)
        return r.get("result", {}).get("count", 0)
    except Exception:
        return None


class _Bm25:
    """Lazy singleton fastembed sparse encoder — matches production retrieval/bm25.py
    (Qdrant/bm25 model, IDF-modifier sparse vector named 'bm25' on cve_kb_hybrid)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._model = None

    def _encode(self, text: str):
        text = (text or "").strip()
        if not text:
            return None
        with self._lock:
            if self._model is None:
                from fastembed import SparseTextEmbedding
                kwargs = {}
                if Path(BM25_LOCAL_MODEL_DIR).is_dir():
                    kwargs["specific_model_path"] = BM25_LOCAL_MODEL_DIR
                self._model = SparseTextEmbedding(model_name=BM25_MODEL, **kwargs)
            emb = next(iter(self._model.embed([text])))
        indices = [int(i) for i in emb.indices]
        values = [float(v) for v in emb.values]
        return {"indices": indices, "values": values} if indices else None

    def search(self, query: str, top_k: int) -> list[tuple[str, float]]:
        sp = self._encode(query)
        if not sp:
            return []
        # knowledge chunks (kind cheatsheet/requirement/mitigation) share this collection: acceptance KPIs measure CVE retrieval only
        body = {"filter": {"must_not": [{"key": "kind", "match": {"any": ["cheatsheet", "requirement", "mitigation"]}}]}, "vector": {"name": BM25_VECTOR, "vector": sp}, "limit": top_k, "with_payload": False}
        r = _post(f"{QDRANT_URL}/collections/{QDRANT_COLLECTION}/points/search", body, timeout=30)
        return [(str(x["id"]), float(x.get("score", 0.0))) for x in r.get("result", []) if x.get("score", 0) > 0]


_bm25_singleton: _Bm25 | None = None
_bm25_available: bool | None = None


def _bm25() -> _Bm25 | None:
    global _bm25_singleton, _bm25_available
    if _bm25_available is False:
        return None
    if _bm25_singleton is None:
        try:
            b = _Bm25()
            b.search("healthcheck", 1)  # force-load model + confirm Qdrant sparse works
            _bm25_singleton = b
            _bm25_available = True
        except Exception:
            _bm25_available = False
            return None
    return _bm25_singleton


def rrf_merge(dense_ids: list[str], bm25_ids: list[str], limit: int) -> list[str]:
    scores: dict[str, float] = {}
    for rank, pid in enumerate(dense_ids):
        scores[pid] = scores.get(pid, 0.0) + 1.0 / (RRF_K + rank + 1)
    for rank, pid in enumerate(bm25_ids):
        scores[pid] = scores.get(pid, 0.0) + 1.0 / (RRF_K + rank + 1)
    return [pid for pid, _ in sorted(scores.items(), key=lambda x: -x[1])[:limit]]


def hybrid_candidates(query: str, top_k: int = CANDIDATE_LIMIT) -> dict[str, Any]:
    """Reproduce production hybrid retrieval for one query — dense (sec-embedding) + BM25
    sparse (fastembed Qdrant/bm25) -> RRF fusion. Deliberately DROPS the "EXACT-CVE FAST
    PATH" / exact-CVE-id scroll filter that `src/secai_gateway/retrieval/qdrant.py` and the
    old `kpi_bench/scripts/retrieval_kpi.py` apply when the query text names a CVE-id
    explicitly — that shortcut trivially guarantees the gold chunk enters the candidate
    pool for id-explicit queries and is the "oracle" this suite must NOT replicate.

    Returns dense-only ids/cve, hybrid (RRF) ids/cve, and hydrated text_by_id for both."""
    vec = embed(query)
    dense = dense_search(vec, top_k)
    dense_ids = [d["id"] for d in dense]
    cve_by_id = {d["id"]: d["cve_id"] for d in dense}
    kind_by_id = {d["id"]: d["chunk_kind"] for d in dense}
    text_by_id = {d["id"]: d["text"] for d in dense}

    bm25 = _bm25()
    bm25_ids: list[str] = []
    bm25_source = "none (dense-only fallback)"
    if bm25 is not None:
        hits = bm25.search(query, top_k)
        bm25_ids = [h[0] for h in hits]
        bm25_source = "qdrant_sparse(Qdrant/bm25) — matches production retrieval/bm25.py"
        need = [pid for pid in bm25_ids if pid not in cve_by_id]
        for pid, pl in fetch_payloads(need).items():
            cve_by_id[pid] = pl["cve_id"]
            kind_by_id[pid] = pl["chunk_kind"]
            text_by_id[pid] = pl["text"]

    hybrid_ids = rrf_merge(dense_ids, bm25_ids, top_k) if bm25_ids else dense_ids
    return {
        "dense_ids": dense_ids, "hybrid_ids": hybrid_ids,
        "cve_by_id": cve_by_id, "kind_by_id": kind_by_id, "text_by_id": text_by_id,
        "bm25_source": bm25_source,
    }


# ── gold-set loaders ─────────────────────────────────────────────────────────────────────
def load_gold_jsonl(name: str) -> list[dict]:
    fp = config.GOLD_QUALITY_DIR / f"{name}.jsonl"
    out = []
    for line in fp.read_text(encoding="utf-8").splitlines():
        if line.strip():
            out.append(json.loads(line))
    return out


def load_retrieval_pairs(name: str = "acceptance_indep_dinh_danh", limit: int | None = None) -> list[dict]:
    """Items with a single unambiguous gold cve_id (no compare/pair questions)."""
    pairs = []
    for s in load_gold_jsonl(name):
        meta = s.get("metadata") or {}
        gold = meta.get("cve_id")
        if not gold or meta.get("cve_id_2") or meta.get("cve_ids"):
            continue
        pairs.append({"id": s["id"], "query": s["input"], "gold_cve": gold.upper(),
                      "capability": s.get("capability"), "difficulty": s.get("difficulty")})
    return pairs[:limit] if limit else pairs


VULN_SAMPLES_DIR = config.GOLD_QUALITY_DIR / "vuln-samples"
_SAST_FNAME_RE = re.compile(r"^ind_sast_(\d+)_cwe(\d+)\.")


def load_sast_samples(limit: int | None = None) -> list[dict]:
    out = []
    for fp in sorted(VULN_SAMPLES_DIR.iterdir()):
        m = _SAST_FNAME_RE.match(fp.name)
        if not m:
            continue
        out.append({"id": fp.stem, "path": fp, "gold_cwe": f"CWE-{int(m.group(2))}",
                    "code": fp.read_text(encoding="utf-8", errors="replace")})
    return out[:limit] if limit else out


# ── gateway SSE client (contract verified against kpi_bench/02-GEN-TEST/adapters/
#    bench_answers_gateway.py — same endpoint/frames, re-implemented here so kpi_eval_v2
#    stays self-contained) ───────────────────────────────────────────────────────────────
def _iter_sse(resp):
    event_name = None
    data_lines: list[str] = []
    for raw in resp:
        line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
        if line == "":
            if data_lines:
                data_str = "\n".join(data_lines)
                if data_str.strip() == "[DONE]":
                    return
                try:
                    payload = json.loads(data_str)
                except json.JSONDecodeError:
                    payload = {}
                yield (event_name or "message", payload)
            event_name, data_lines = None, []
            continue
        if line.startswith("event:"):
            event_name = line[len("event:"):].strip()
        elif line.startswith("data:"):
            data_lines.append(line[len("data:"):].lstrip(" "))
    if data_lines and "\n".join(data_lines).strip() != "[DONE]":
        try:
            yield (event_name or "message", json.loads("\n".join(data_lines)))
        except json.JSONDecodeError:
            pass


_last_call_ts = [0.0]
_call_lock = threading.Lock()
MIN_GAP_S = 1.2  # self-pacing: keep well under the gateway's 15 req/60s (route_limit) bucket


def gateway_chat(message: str, timeout: float = 260) -> dict:
    """POST /ai/chat/stream, consume the SSE stream, return the final answer + diagnostics.
    Sequential-safe: self-paces so this suite never bursts the production gateway."""
    with _call_lock:
        wait = MIN_GAP_S - (time.time() - _last_call_ts[0])
        if wait > 0:
            time.sleep(wait)
        _last_call_ts[0] = time.time()

    rec = {"text": "", "tools": [], "tool_results": [], "ttft_ms": None, "total_ms": None,
           "error": None, "input_classification": None}
    body = json.dumps({"message": message}).encode("utf-8")
    t0 = time.time()
    req = urllib.request.Request(config.GATEWAY_URL.rstrip("/") + "/ai/chat/stream", data=body,
                                 headers={"Content-Type": "application/json"}, method="POST")
    token_buf: list[str] = []
    ttft_recorded = False
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            for event_name, payload in _iter_sse(resp):
                if event_name == "tool_call":
                    name = payload.get("name")
                    if name:
                        rec["tools"].append(name)
                elif event_name == "tool_result":
                    result = payload.get("result")
                    if result:
                        rec["tool_results"].append(result)
                elif event_name == "token":
                    if not ttft_recorded:
                        rec["ttft_ms"] = int((time.time() - t0) * 1000)
                        ttft_recorded = True
                    delta = payload.get("delta", "")
                    if delta:
                        token_buf.append(delta)
                elif event_name == "regenerate":
                    token_buf = []
                elif event_name == "final":
                    final_text = payload.get("content")
                    if final_text is None:
                        final_text = payload.get("answer") or payload.get("message") or "".join(token_buf)
                    rec["text"] = final_text
                    meta = payload.get("metadata") or {}
                    rec["input_classification"] = meta.get("input_classification")
        rec["total_ms"] = int((time.time() - t0) * 1000)
        if not rec["text"] and token_buf:
            rec["text"] = "".join(token_buf)
        return rec
    except urllib.error.HTTPError as e:
        rec["total_ms"] = int((time.time() - t0) * 1000)
        if e.code == 422:
            rec["error"] = "HTTP422:blocked_by_require_public_input"
        else:
            try:
                body_txt = e.read().decode("utf-8", errors="replace")[:300]
            except Exception:
                body_txt = ""
            rec["error"] = f"HTTPError:{e.code}:{body_txt}"
        return rec
    except Exception as e:  # noqa: BLE001
        rec["total_ms"] = int((time.time() - t0) * 1000)
        rec["error"] = f"{type(e).__name__}: {e}"
        return rec


def extract_ids(text: str) -> tuple[set[str], set[str]]:
    return {c.upper() for c in CVE_RE.findall(text or "")}, {c.upper() for c in CWE_RE.findall(text or "")}


def looks_like_refusal(text: str) -> bool:
    low = (text or "").lower()
    return any(m in low for m in REFUSAL_MARKERS)
