"""Deterministic PoC pre-fetch for the exploit-research profile.

`prompts/exploit.md`'s `required_tool_sequence` is always the SAME fixed steps regardless
of the question (search description → search PoC → fetch top hits) — that's not agentic
judgment, so it shouldn't depend on the model reliably choosing to call tools. Live testing
showed it doesn't: the same CVE, same request, alternates between correctly searching and
skipping straight to an empty answer — independent of concurrency (confirmed via a solo
re-run). `enforce_reference_code`/`force_no_poc_notice` (poc/inject.py) already guarantee
Section 1 is never fabricated regardless, but a skipped search still means a real, findable
PoC gets reported as "chưa có thông tin" when it didn't have to.

`prefetch_evidence` closes that gap by running the SAME search+fetch the model is
instructed to do, in Python, BEFORE the model ever runs — for ANY prompt, not just ones
naming a CVE. It prefers an exact CVE-ID match when one is present (PocCatalog.search's
CVE-exact path); otherwise it falls back to the SAME keyword FTS search poc_search already
offers the model, using the raw prompt text as the query. No-op only when that search
itself finds nothing.

Each report includes ``coverage`` — a measured predicate for RequireSearchBeforeAnswerHook:

* ``enough``  — fetched ≥1 artifact (the search+fetch step is DONE, nothing left for the
  model to search for) — NOT the same as "real code was fetched": a metadata-only fetch
  (ghsa advisory, README-only PoC) still counts, since the *search* step is what this
  predicate is about. Do not read ``coverage == "enough"`` as "Section 1 will be the real
  T1 tier regardless" anywhere else — see ``set_session_real_code_evidence`` below for that.
* ``empty``   — search ran, hits=0 (đã tìm, không có)
* ``partial`` — hits>0 but fetched=0 (ref/fetch hỏng — hook vẫn bắt model thử)
* ``failed``  — chưa search được (empty prompt / search error)

``enough`` | ``empty`` close the search hook for that exploit session (no regenerate just
to re-do what prefetch already closed). ``partial`` | ``failed`` keep the hook as backstop.
Routes call ``set_session_coverage(session_id, …)`` when prefetch finishes; the hook reads
it via ``get_session_coverage``. Prefetch still runs independently of the model's tools;
results merge by sha256 before Section-1 injection.
"""
from __future__ import annotations

import logging
import re
import unicodedata
from collections import OrderedDict
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Iterator, Literal

import httpx

from ..config import get_settings
from ..state import SqliteLedger
from .backend import LocalPocBackend, PocError
from .normalize import normalize_cve_ids
from .tools import record_fetch_evidence

logger = logging.getLogger(__name__)

_CVE = re.compile(r"CVE-\d{4}-\d{3,7}", re.I)
# Exploit-capable redistributable sources first. nuclei is intentionally absent — detection
# YAML is T2 synthesis grounding, not a T1 PoC; prefetch must prefer EDB/MSF/GitHub exploit
# rows over nuclei even when nuclei is verified and higher-ranked by source tier alone.
_PRIORITY_SOURCES = {"exploitdb", "metasploit", "poc_github"}
# Declared languages the air-gap runner executes directly (prefetch ranking, `_lang_rank`).
# Ordered by how universally the interpreter is available on a Linux host: Python first (always
# present), then shell, then php/perl/node, then ruby (often absent — the eval host has no ruby).
_LANG_PY = {"py", "python", "python3"}
_LANG_SH = {"sh", "bash"}
_LANG_ALT = {"php", "pl", "perl", "js", "javascript", "node"}
_LANG_RB = {"rb", "ruby"}
_DOC_LANGS = {"md", "markdown", "rst", "txt", ""}
_RUNNABLE_LANGS = _LANG_PY | _LANG_SH | _LANG_ALT | _LANG_RB

_MAX_PREFETCH = 2  # matches poc/backend.py's _MAX_FETCH_PER_RUN — same evidence budget

Coverage = Literal["enough", "empty", "partial", "failed"]
# Predicate: prefetch already closed "must search" — hook must not force_continue for that.
COVERAGE_CLOSES_SEARCH_HOOK: frozenset[str] = frozenset({"enough", "empty"})

_MAX_TRACKED_SESSIONS = 20000
_session_coverage: "OrderedDict[str, Coverage]" = OrderedDict()
_session_cve_known: "OrderedDict[str, bool]" = OrderedDict()
_session_synthesis_evidence: "OrderedDict[str, bool]" = OrderedDict()
_session_real_code_evidence: "OrderedDict[str, bool]" = OrderedDict()
_session_kb_searched: "OrderedDict[str, bool]" = OrderedDict()
_session_kb_docs: "OrderedDict[str, str]" = OrderedDict()

# asyncio tasks inherit the same request storage; independent scopes start empty.
SessionCoverageState = dict[str, OrderedDict[str, Any]]
_session_coverage_state: ContextVar[SessionCoverageState | None] = ContextVar(
    "session_coverage_state", default=None
)
_legacy_session_stores: SessionCoverageState = {
    "coverage": _session_coverage,
    "cve_known": _session_cve_known,
    "synthesis_evidence": _session_synthesis_evidence,
    "real_code_evidence": _session_real_code_evidence,
    "kb_searched": _session_kb_searched,
    "kb_docs": _session_kb_docs,
}


def capture_session_coverage() -> SessionCoverageState | None:
    """Capture request storage for a stream that resumes after the route returns."""
    return _session_coverage_state.get()


@contextmanager
def session_coverage_scope(
    state: SessionCoverageState | None = None,
) -> Iterator[SessionCoverageState]:
    """Use fresh request evidence, or resume captured storage; always restore the caller.

    A captured scope is retained only by its request/stream, never by the global caches.
    Child asyncio tasks share this scope so prefetch and hook callbacks see the same data.
    """
    if state is None:
        state = {name: OrderedDict() for name in _legacy_session_stores}
    token = _session_coverage_state.set(state)
    try:
        yield state
    finally:
        _session_coverage_state.reset(token)


def _session_store(name: str) -> OrderedDict[str, Any]:
    state = _session_coverage_state.get()
    return (state if state is not None else _legacy_session_stores)[name]


def set_session_coverage(session_id: str | None, coverage: Coverage) -> None:
    """Record prefetch coverage for an exploit session (routes → hook)."""
    store = _session_store("coverage")
    if not session_id:
        return
    store[session_id] = coverage
    store.move_to_end(session_id)
    while len(store) > _MAX_TRACKED_SESSIONS:
        store.popitem(last=False)


def get_session_coverage(session_id: str | None) -> Coverage | None:
    store = _session_store("coverage")
    if not session_id:
        return None
    return store.get(session_id)


def clear_session_coverage(session_id: str | None = None) -> None:
    """Clear one session or all evidence in the active request (legacy store unscoped)."""
    for name in _legacy_session_stores:
        store = _session_store(name)
        if session_id is None:
            store.clear()
        else:
            store.pop(session_id, None)


def set_session_cve_known(session_id: str | None, known: bool) -> None:
    """Record the deterministic `cve_exists_in_kb` result for an exploit session (routes →
    hook) — same shape as `set_session_coverage`, but for the T3 (synthesize-from-CVE)
    signal, which RequireSection1SynthesisHook (agent/hooks.py) needs and can't compute
    itself (it has no CVE-KB access)."""
    store = _session_store("cve_known")
    if not session_id:
        return
    store[session_id] = known
    store.move_to_end(session_id)
    while len(store) > _MAX_TRACKED_SESSIONS:
        store.popitem(last=False)


def get_session_cve_known(session_id: str | None) -> bool:
    store = _session_store("cve_known")
    if not session_id:
        return False
    return bool(store.get(session_id))


def clear_session_cve_known(session_id: str | None = None) -> None:
    """Clear one session or this cache in the active request (legacy store unscoped)."""
    store = _session_store("cve_known")
    if session_id is None:
        store.clear()
        return
    store.pop(session_id, None)


def set_session_synthesis_evidence(session_id: str | None, has_evidence: bool) -> None:
    """Record whether the DETERMINISTIC prefetch (not the model's own tool calls) turned up
    T2-grade evidence (a catalog description/README, no runnable code) — same shape as
    `set_session_cve_known`, needed for the same reason: `prefetch_evidence` writes into the
    ledger under its OWN scratch `run_id` (`f"prefetch:{uuid4().hex}"`, see routes.py's
    `exploit_prefetch` docstring — "independent of ... whatever run_id the model's session
    ends up using"), so `RequireSection1SynthesisHook`'s own `ledger.evidence_for_run(ctx.run_id)`
    lookup can NEVER see it — it only sees evidence the model itself fetched during its own
    turn. Without this, the hook silently under-fires exactly when prefetch is the only
    reason synthesis evidence exists at all (the same gap prefetch.py's module docstring
    already documents for search-skipping, just one layer further down at the retry-hook)."""
    store = _session_store("synthesis_evidence")
    if not session_id:
        return
    store[session_id] = has_evidence
    store.move_to_end(session_id)
    while len(store) > _MAX_TRACKED_SESSIONS:
        store.popitem(last=False)


def get_session_synthesis_evidence(session_id: str | None) -> bool:
    store = _session_store("synthesis_evidence")
    if not session_id:
        return False
    return bool(store.get(session_id))


def clear_session_synthesis_evidence(session_id: str | None = None) -> None:
    """Clear one session or this cache in the active request (legacy store unscoped)."""
    store = _session_store("synthesis_evidence")
    if session_id is None:
        store.clear()
        return
    store.pop(session_id, None)


def set_session_real_code_evidence(session_id: str | None, has_real_code: bool) -> None:
    """Record whether the deterministic prefetch found REAL, injectable `source_code`/
    `source_files` (T1) — as opposed to merely `coverage == "enough"`, which only means
    "prefetch fetched *something*" and is true just as often for a metadata-only ghsa/
    README fetch as for real code (see the `coverage` docstring above). Consumed by
    `RequireSection1SynthesisHook`, which previously treated `coverage == "enough"` as "T1
    will happen regardless, skip forcing synthesis" — wrong whenever prefetch's one fetch
    was description-only, which silently let a description-only CVE fall through to
    `synthesis_failed` on the model's very first reticent turn, never getting the forced
    retry it needed. This flag is the precise signal that check actually wanted."""
    store = _session_store("real_code_evidence")
    if not session_id:
        return
    store[session_id] = has_real_code
    store.move_to_end(session_id)
    while len(store) > _MAX_TRACKED_SESSIONS:
        store.popitem(last=False)


def get_session_real_code_evidence(session_id: str | None) -> bool:
    store = _session_store("real_code_evidence")
    if not session_id:
        return False
    return bool(store.get(session_id))


def clear_session_real_code_evidence(session_id: str | None = None) -> None:
    """Clear one session or this cache in the active request (legacy store unscoped)."""
    store = _session_store("real_code_evidence")
    if session_id is None:
        store.clear()
        return
    store.pop(session_id, None)


def set_session_kb_searched(session_id: str | None, searched: bool) -> None:
    """Record whether this request retrieved usable internal KB context."""
    store = _session_store("kb_searched")
    if not session_id:
        return
    store[session_id] = searched
    store.move_to_end(session_id)
    while len(store) > _MAX_TRACKED_SESSIONS:
        store.popitem(last=False)


def get_session_kb_searched(session_id: str | None) -> bool:
    store = _session_store("kb_searched")
    if not session_id:
        return False
    return bool(store.get(session_id))


def clear_session_kb_searched(session_id: str | None = None) -> None:
    """Clear one session or this cache in the active request (legacy store unscoped)."""
    store = _session_store("kb_searched")
    if session_id is None:
        store.clear()
        return
    store.pop(session_id, None)


def set_session_kb_docs(session_id: str | None, docs: str) -> None:
    """Record the retrieved KB context text for this request/session."""
    store = _session_store("kb_docs")
    if not session_id:
        return
    store[session_id] = docs
    store.move_to_end(session_id)
    while len(store) > _MAX_TRACKED_SESSIONS:
        store.popitem(last=False)


def get_session_kb_docs(session_id: str | None) -> str | None:
    store = _session_store("kb_docs")
    if not session_id:
        return None
    return store.get(session_id)


def clear_session_kb_docs(session_id: str | None = None) -> None:
    """Clear one session or this cache in the active request (legacy store unscoped)."""
    store = _session_store("kb_docs")
    if session_id is None:
        store.clear()
        return
    store.pop(session_id, None)


def extract_cve_id(prompt: str) -> str | None:
    """The same CVE-ID extraction `prefetch_evidence` does internally, exposed standalone
    for callers that need the ID independently — e.g. the CVE-KB existence check
    (`retrieval.neo4j.cve_exists_in_kb`), which runs concurrently with, not after, the
    catalog prefetch."""
    m = _CVE.search(normalize_cve_ids(prompt or ""))
    return m.group(0).upper() if m else None


def _fold(text: str) -> str:
    """Lowercase + strip Vietnamese tone/diacritic marks (đ→d isn't decomposed by NFD, so map
    it explicitly) — local copy of routes.py's ``_fold_diacritics`` (kept independent to avoid
    a poc→api import; both are tiny pure functions over the same normalization)."""
    decomposed = unicodedata.normalize("NFD", text or "")
    stripped = "".join(c for c in decomposed if unicodedata.category(c) != "Mn")
    return stripped.replace("đ", "d").replace("Đ", "d").lower().strip()


# ── Layer-4b: deterministic generic-hack-intent denylist (EXPLOIT-ONLY, free-text only) ──
# Bug #1/PC1 round 2: the rerank score alone can't reliably separate a security-flavored but
# NON-specific "how do I hack <generic target>" prompt from a genuine specific product/CVE
# description — some phrasings cluster too close together (measured: EN "how do I hack
# wifi" ~0.978, VI "làm sao hack wifi" ~0.9925 — the LATTER sits ABOVE the 0.99 floor, which
# already sits in the gap right below genuine descriptions at ~0.9975; raising the floor
# further would start rejecting genuine free-text). This is a SECOND, deterministic,
# score-INDEPENDENT layer: a narrow EN+VI regex on the casual "hack"/"crack"/"break into"
# verb shape — a real vulnerability-research question says "exploit"/"khai thác" or names a
# CVE/specific product+version, never bare "hack" — runs BEFORE the rerank score is even
# consulted, so it also closes the fail-open gap for this exact class of prompt when the
# embed/rerank backend is unreachable (see the warning log in ``exploit_relevance_ok``).
# Deliberately NARROW: must NOT match "how to exploit CVE-…", "cách khai thác Apache
# Struts", "wordpress 6.x sqli", etc. — only the casual generic-hack shape.
_GENERIC_HACK_TARGETS = (
    r"wifi|wi-fi|facebook|gmail|instagram|account|someone|a phone|phone|password|network|"
    r"router|computer|pc|snapchat|whatsapp|tiktok|tai khoan|mat khau|dien thoai|may tinh|mang"
)
_GENERIC_HACK_RE = re.compile(
    r"\bhow (do i|to|can i|would i|could i)\s+(hack|crack|break into)\b"
    r"|\bhack\s+(into\s+)?(" + _GENERIC_HACK_TARGETS + r")\b"
    r"|\b(crack|break into)\s+(" + _GENERIC_HACK_TARGETS + r")\b"
    r"|\blam sao\s*(de\s*)?hack\b"
    r"|\bcach\s*hack\b"
    r"|\bhuong dan\s*hack\b",
    re.I,
)


def is_generic_hack_intent(prompt: str) -> bool:
    """True when ``prompt`` is a casual "how do I hack <generic target>" / "cách hack…" /
    "làm sao hack…" style request — no CVE id, no specific product+version framing, just the
    bare "hack" verb over a generic target. Deterministic, score-independent; see the module
    comment above for the rationale and the false-positive guardrails."""
    return bool(_GENERIC_HACK_RE.search(_fold(prompt)))


# ── Layer-4 exploit relevance gate (EXPLOIT-ONLY, default ON since 2026-09-22, fail-open) ──
async def kb_top_rerank_score(prompt: str) -> float | None:
    """Top reranker relevance_score of the KB candidates for ``prompt`` (0..1), or ``None``
    when no signal is available (no embedding/reranker configured, no hits, or any error —
    fail-open). Reuses the same embed → dense-search → rerank pieces the KB tool uses; it is
    NOT the shared qdrant_search tool, so this gate stays confined to the exploit path."""
    from ..retrieval.embedding import EMBEDDING_URL, RERANKER_URL, embed, rerank
    from ..retrieval.filters import entity_scope_filter
    from ..retrieval.qdrant import CANDIDATE_LIMIT, _dense_search
    prompt = (prompt or "").strip()
    if not prompt or not EMBEDDING_URL or not RERANKER_URL:
        return None
    try:
        async with httpx.AsyncClient() as client:
            vector = await embed(client, prompt)
            # Entity scope: knowledge chunks (cheat sheets etc.) would answer "how do I prevent XSS"
            # with a high rerank score and let off-topic prompts through this relevance gate.
            hits = await _dense_search(client, vector, entity_scope_filter(), CANDIDATE_LIMIT)
            if not hits:
                return None
            docs = [str(h["payload"].get("text") or "") for h in hits]
            results = await rerank(client, prompt, docs, len(docs))
    except Exception:
        return None
    scores = [r.get("relevance_score", 0.0) for r in (results or []) if isinstance(r, dict)]
    return max(scores) if scores else None


async def exploit_relevance_ok(prompt: str) -> bool | None:
    """Layer-4 decision for the exploit path. ``None`` = INERT (gate off, or no signal →
    fail-open, treat as pass); ``True``/``False`` = the reranker top score compared to
    ``exploit_rerank_min_score``, UNLESS the deterministic Layer-4b denylist
    (``is_generic_hack_intent``) already says the prompt is a generic "how do I hack …"
    request, in which case that decides (``False``) WITHOUT ever calling the (network-
    dependent) reranker — so this class of prompt is blocked even when the embed/rerank
    backend is unreachable (the score path alone would otherwise fail open — see the warning
    log below). Both checks are gated by ``exploit_relevance_gate_enabled`` — the single
    kill-switch (``EXPLOIT_RELEVANCE_GATE_ENABLED=false``) still restores the old, fully-
    inert behaviour for BOTH layers, not just the score. Used ONLY by routes.exploit_prefetch,
    for FREE-TEXT prompts — a named CVE keeps using cve_exists_in_kb and is re-checked here
    defensively (a CVE id anywhere in the prompt always bypasses both the denylist and the
    score, even if a future caller reaches this function without having already branched on
    cve_id)."""
    s = get_settings()
    if not s.exploit_relevance_gate_enabled:
        return None
    if _CVE.search(normalize_cve_ids(prompt or "")):
        return True
    if is_generic_hack_intent(prompt):
        return False
    score = await kb_top_rerank_score(prompt)
    if score is None:
        logger.warning(
            "exploit relevance gate INERT (no rerank signal — embed/reranker unreachable or "
            "no KB hits): a free-text off-topic prompt can slip through unfiltered by score "
            "for this request (the Layer-4b generic-hack denylist above already ran and "
            "would have blocked a recognized 'how do I hack …' shape regardless)")
        return None
    return score >= s.exploit_rerank_min_score


def _lang_rank(hit: dict[str, Any]) -> int:
    """Runnability tier by declared language: scriptable (0) < other code (1) < doc (2) < data (3).
    The air-gap runner executes python/shell/ruby/php/perl/js directly; a compiled/binary PoC
    (cpp/cs/java/go/html — frequently Windows-only) does not run on the Linux lab, so it must not
    win the top-2 fetch budget over a script sibling."""
    lang = str(hit.get("language") or "").strip().lower()
    if lang in _LANG_PY:
        return 0
    if lang in _LANG_SH:
        return 1
    if lang in _LANG_ALT:
        return 2
    if lang in _LANG_RB:
        return 3
    if lang in _DOC_LANGS:
        return 5
    if lang in ("json", "yaml", "yml"):
        return 6
    return 4


def _rank(hit: dict[str, Any]) -> tuple[int, int, bool, bool, bool, int, bool, str]:
    """Lower sorts first. A VERIFIED-CANONICAL row always wins (the operator already executed it
    against a lab/target), then the existing order: real exploit over detection, a row WITH a
    stored artifact over a link-only row, then a vetted exploit source (exploitdb/metasploit)
    over poc_github when both carry an artifact, verified, a runnable-script language,
    license-friendly sources."""
    if hit.get("canonical"):
        return (0, 0, False, False, False, 0, False, str(hit.get("ref") or ""))
    # A vulhub lab WITH a bundled exploit script is the PoC written for exactly this lab's
    # version/config — prefer it right after the operator-verified canonical, ahead of any
    # same-CVE community artifact (which may target a different variant/format, e.g. the
    # CVE-2026-27944 backup-decrypt format mismatch).
    if hit.get("bundled_poc"):
        return (0, 1, False, False, False, 0, False, str(hit.get("ref") or ""))
    kind = str(hit.get("kind") or "").strip().lower()
    source = str(hit.get("source") or "").strip().lower()
    if kind == "exploit" or source in {"exploitdb", "metasploit", "poc_github", "vendor"}:
        tier = 0
    elif kind == "detection" or source == "nuclei":
        tier = 2
    else:
        tier = 1  # advisory / lab — between exploit and detection
    has_artifact = bool(hit.get("has_artifact"))
    # Within the exploit tier a row with a stored artifact (runnable offline) beats a link-only
    # row. When BOTH carry an artifact, prefer the vetted exploit sources (exploitdb/metasploit)
    # over a community poc_github repo; when neither does, keep the legacy poc_github-first order
    # (a repo PoC is what the consolidation step merges into one runnable file).
    if has_artifact:
        # Prefer RUNNABLE standalone sources (exploitdb/poc_github/vulhub-lab) over a metasploit
        # module: MSF modules import the Msf framework and never run standalone — any LLM
        # "port" of one is fragile (observed: several rewritten .rb PoCs broke). MSF still ranks
        # (just after these) when it is the only artifact.
        source_pref = 0 if source in {"exploitdb", "poc_github", "vulhub"} else 1
    else:
        source_pref = source != "poc_github"
    return (1, tier, not has_artifact, source_pref, not hit.get("verified"), _lang_rank(hit),
            hit.get("source") not in _PRIORITY_SOURCES,
            # DETERMINISTIC tiebreak so re-ranking identical-tier hits picks the SAME top-2 every
            # run (Python sort is stable, but the input order from poc.search is not for ties).
            str(hit.get("ref") or ""))


async def prefetch_evidence(poc: LocalPocBackend, ledger: SqliteLedger, prompt: str,
                            run_id: str) -> dict[str, Any]:
    """Search + fetch deterministically for `prompt`, writing real evidence to the ledger
    under `run_id` (own scratch id — independent of whatever run_id the model's own
    agentic session ends up using). Returns a small report with ``coverage``, never raises
    — the caller's agentic path and the final injection backstop both work with zero
    evidence, so a prefetch failure here degrades gracefully, not fatally."""
    prompt = normalize_cve_ids(prompt or "")
    m = _CVE.search(prompt)
    query = m.group(0).upper() if m else prompt.strip()
    if not query:
        return {"prefetched": False, "reason": "empty_prompt", "coverage": "failed"}
    try:
        # Search WIDE enough that a CVE's own vulhub lab row (often ranked past #10 once a
        # popular exploitdb/msf/poc_github row exists) is still considered — the lab-with-poc
        # row is the highest-fidelity grounding. Only the top _MAX_PREFETCH are fetched, so a
        # wider search costs one extra DB query, not extra fetches.
        result = await poc.search(query, 25, run_id)
    except PocError:
        return {"prefetched": False, "reason": "search_error", "query": query, "coverage": "failed"}
    hits = result.get("hits", [])
    if not hits:
        return {"prefetched": False, "reason": "no_hits", "query": query, "coverage": "empty"}
    ranked = sorted(hits, key=_rank)
    fetched = 0
    for i, hit in enumerate(ranked[:_MAX_PREFETCH]):
        try:
            fr = await poc.fetch(hit["ref"], run_id)
        except PocError:
            continue
        if hit.get("canonical"):
            # Verified-canonical PoC: served VERBATIM (routes skips the lossy LLM
            # slice/consolidate for it) so /ai/exploit returns the exact known-working bytes.
            fr["canonical"] = True
        record_fetch_evidence(ledger, run_id, f"{run_id}:prefetch:{i}", fr)
        fetched += 1
    coverage: Coverage = "enough" if fetched >= 1 else "partial"
    return {"prefetched": fetched > 0, "query": query, "hits": len(hits),
            "fetched": fetched, "coverage": coverage}


def merge_evidence(*groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Combine evidence lists (e.g. prefetch + the model's own agentic run), deduplicated
    by sha256 — so a model that ALSO searched correctly doesn't render the same PoC twice
    in Section 1. First occurrence wins; order across groups is preserved."""
    seen: set[str] = set()
    merged: list[dict[str, Any]] = []
    for group in groups:
        for ev in group or []:
            sha = ev.get("sha256")
            if sha and sha in seen:
                continue
            if sha:
                seen.add(sha)
            merged.append(ev)
    return merged
