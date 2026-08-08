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

(Earlier version, `prefetch_by_cve`, was gated on a CVE-ID regex match — narrower. Live
testing then showed the SAME unreliability on keyword-only queries too: e.g. "PoC cho lỗ
hổng RCE trong Langflow AI" (no CVE-ID) — the model's first attempt guessed at CWEs with
no evidence and asked the user to supply the CVE-ID itself, before a forced-retry hook
(agent/hooks.py's RequireSearchBeforeAnswerHook) made it search and self-correct. This
broadened version removes the CVE-ID gate so the harness resolves that case directly too,
without depending on the retry hook at all — the hook remains as a backstop for whatever
this still misses, e.g. a prompt too vague even for keyword FTS to match anything.)

This runs INDEPENDENTLY of the model's own agentic run, not instead of it — the model still
has `poc_search`/`poc_fetch` and may use them (e.g. for a second, related CVE). The two
results are merged and deduplicated by sha256 before the final injection step, so a model
that DOES search correctly doesn't produce a doubled-up Section 1.
"""
from __future__ import annotations

import re
from typing import Any

from ..state import SqliteLedger
from .backend import LocalPocBackend, PocError
from .tools import record_fetch_evidence

_CVE = re.compile(r"CVE-\d{4}-\d{3,7}", re.I)
# Same priority `required_tool_sequence` step 4 already tells the model to apply: verified
# hits first, then sources whose license makes real `source_code` likely.
_PRIORITY_SOURCES = {"exploitdb", "metasploit", "nuclei", "ghsa"}
_MAX_PREFETCH = 2  # matches poc/backend.py's _MAX_FETCH_PER_RUN — same evidence budget


def _rank(hit: dict[str, Any]) -> tuple[bool, bool]:
    return (not hit.get("verified"), hit.get("source") not in _PRIORITY_SOURCES)


async def prefetch_evidence(poc: LocalPocBackend, ledger: SqliteLedger, prompt: str,
                            run_id: str) -> dict[str, Any]:
    """Search + fetch deterministically for `prompt`, writing real evidence to the ledger
    under `run_id` (own scratch id — independent of whatever run_id the model's own
    agentic session ends up using). Returns a small report, never raises — the caller's
    agentic path and the final injection backstop both work with zero evidence, so a
    prefetch failure here degrades gracefully, not fatally."""
    m = _CVE.search(prompt or "")
    query = m.group(0).upper() if m else (prompt or "").strip()
    if not query:
        return {"prefetched": False, "reason": "empty_prompt"}
    try:
        result = await poc.search(query, 10, run_id)
    except PocError:
        return {"prefetched": False, "reason": "search_error", "query": query}
    hits = result.get("hits", [])
    if not hits:
        return {"prefetched": False, "reason": "no_hits", "query": query}
    ranked = sorted(hits, key=_rank)
    fetched = 0
    for i, hit in enumerate(ranked[:_MAX_PREFETCH]):
        try:
            fr = await poc.fetch(hit["ref"], run_id)
        except PocError:
            continue
        record_fetch_evidence(ledger, run_id, f"{run_id}:prefetch:{i}", fr)
        fetched += 1
    return {"prefetched": True, "query": query, "hits": len(hits), "fetched": fetched}


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
