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

* ``enough``  — fetched ≥1 artifact (Mục 1 injectable)
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

import re
from collections import OrderedDict
from typing import Any, Literal

from ..state import SqliteLedger
from .backend import LocalPocBackend, PocError
from .normalize import normalize_cve_ids
from .tools import record_fetch_evidence

_CVE = re.compile(r"CVE-\d{4}-\d{3,7}", re.I)
# Same priority `required_tool_sequence` step 4 already tells the model to apply: verified
# hits first, then sources whose license makes real `source_code` likely.
_PRIORITY_SOURCES = {"exploitdb", "metasploit", "nuclei", "ghsa"}
_MAX_PREFETCH = 2  # matches poc/backend.py's _MAX_FETCH_PER_RUN — same evidence budget

Coverage = Literal["enough", "empty", "partial", "failed"]
# Predicate: prefetch already closed "must search" — hook must not force_continue for that.
COVERAGE_CLOSES_SEARCH_HOOK: frozenset[str] = frozenset({"enough", "empty"})

_MAX_TRACKED_SESSIONS = 20000
_session_coverage: "OrderedDict[str, Coverage]" = OrderedDict()


def set_session_coverage(session_id: str | None, coverage: Coverage) -> None:
    """Record prefetch coverage for an exploit session (routes → hook)."""
    if not session_id:
        return
    _session_coverage[session_id] = coverage
    _session_coverage.move_to_end(session_id)
    while len(_session_coverage) > _MAX_TRACKED_SESSIONS:
        _session_coverage.popitem(last=False)


def get_session_coverage(session_id: str | None) -> Coverage | None:
    if not session_id:
        return None
    return _session_coverage.get(session_id)


def clear_session_coverage(session_id: str | None = None) -> None:
    """Test helper: clear one session or the whole store."""
    if session_id is None:
        _session_coverage.clear()
        return
    _session_coverage.pop(session_id, None)


_session_cve_known: "OrderedDict[str, bool]" = OrderedDict()


def set_session_cve_known(session_id: str | None, known: bool) -> None:
    """Record the deterministic `cve_exists_in_kb` result for an exploit session (routes →
    hook) — same shape as `set_session_coverage`, but for the T3 (synthesize-from-CVE)
    signal, which RequireSection1SynthesisHook (agent/hooks.py) needs and can't compute
    itself (it has no CVE-KB access)."""
    if not session_id:
        return
    _session_cve_known[session_id] = known
    _session_cve_known.move_to_end(session_id)
    while len(_session_cve_known) > _MAX_TRACKED_SESSIONS:
        _session_cve_known.popitem(last=False)


def get_session_cve_known(session_id: str | None) -> bool:
    if not session_id:
        return False
    return bool(_session_cve_known.get(session_id))


def clear_session_cve_known(session_id: str | None = None) -> None:
    """Test helper: clear one session or the whole store."""
    if session_id is None:
        _session_cve_known.clear()
        return
    _session_cve_known.pop(session_id, None)


def extract_cve_id(prompt: str) -> str | None:
    """The same CVE-ID extraction `prefetch_evidence` does internally, exposed standalone
    for callers that need the ID independently — e.g. the CVE-KB existence check
    (`retrieval.neo4j.cve_exists_in_kb`), which runs concurrently with, not after, the
    catalog prefetch."""
    m = _CVE.search(normalize_cve_ids(prompt or ""))
    return m.group(0).upper() if m else None


def _rank(hit: dict[str, Any]) -> tuple[bool, bool]:
    return (not hit.get("verified"), hit.get("source") not in _PRIORITY_SOURCES)


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
        result = await poc.search(query, 10, run_id)
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
