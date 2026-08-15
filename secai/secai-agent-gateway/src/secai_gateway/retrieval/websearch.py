"""web_search — SearXNG-backed discovery of public PoC/advisory pages for a CVE.

Gated OFF by default (``WEB_SEARCH_ENABLED`` + a self-hosted ``SEARXNG_URL``). Two uses:

* ``web_search`` tool — lets the exploit-research model discover candidate reference URLs
  when it has none (the catalog missed, but the CVE is real). It returns title/url/snippet
  only; the model still fetches with ``fetch_reference`` (allowlist-gated).
* ``gather_web_hints`` — a DETERMINISTIC pre-step (mirrors ``poc.prefetch``): for a known
  CVE with no catalog PoC (the T3 synthesize tier), search + fetch the top allowlisted
  pages in Python so Mục 1–3 are grounded on real hints regardless of the model's own
  non-deterministic tool-calling — then feed the result into the model prompt.

SEARCH ≠ FETCH. SearXNG may surface any URL, but ``gather_web_hints`` only ever fetches
hosts already on ``fetch_reference``'s security-reference allowlist — so the SSRF posture
is exactly ``fetch_reference``'s. A search hit on a non-allowlisted host is dropped, not
fetched. Every hint is unverified reference material, never a confirmed PoC: the T3 tier's
mandatory SYNTHESIZED banner still applies to anything built from it.
"""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Any
from urllib.parse import quote, urlparse

import httpx
from linch.tools.base import ToolContext, ToolResult
from linch.tools.function import tool

from ..config import get_settings
from ..poc.tools import record_fetch_evidence
from ..state import SqliteLedger
from .http_fetch import host_allowed
from .reference_fetch import (
    _evidence_from_payload,
    fetch_reference_markdown,
    reference_hosts,
)

logger = logging.getLogger(__name__)

# How many allowlisted pages gather_web_hints fetches. Bumped from poc.prefetch's original
# 2 to fit NVD + GHSA + one patch/commit diff without any of the three crowding the others
# out — patch diffs (see _to_diff_url) are the highest-value source added by this module but
# rank behind NVD/GHSA in the candidate list, so budget=2 meant they almost never got fetched.
_MAX_HINT_FETCH = 3

# Two distinct search intents in parallel: the general "find a PoC" query, and one biased
# toward the actual fix — a patch/commit diff shows the EXACT vulnerable code, which a
# generic advisory summary never does. Evaluated against 10 real CVEs (case-3 quality
# review): synthesized PoCs grounded only in an NVD one-liner scored ~15-20% toward a
# working exploit; those with a concrete function/param/endpoint from a richer source (a
# GHSA "Patches" section, a referenced write-up) scored close to double that.
_SEARCH_QUERY_TEMPLATES = (
    "{cve} PoC exploit proof of concept github",
    "{cve} patch commit fix",
)

_URL_RE = re.compile(r'https?://[^\s\)\]<>"\']+')
# Links whose PATH suggests an actual diff/patch (highest value: shows real before/after
# code) or a security advisory (root-cause writeup, often with a Patches section) — ranked
# ahead of generic references (changelog, mailing-list index, the CVE registry mirrors).
_PATCH_LINK_RE = re.compile(r"/(?:commit|compare|pull)/", re.I)
_ADVISORY_LINK_RE = re.compile(r"advisor|security", re.I)


def _reference_priority(url: str) -> int:
    if _PATCH_LINK_RE.search(url):
        return 0
    if _ADVISORY_LINK_RE.search(url):
        return 1
    return 2


_GITHUB_COMMIT_RE = re.compile(r"^https://github\.com/[^/]+/[^/]+/commit/[0-9a-fA-F]{7,40}/?$")
_GITHUB_PR_RE = re.compile(r"^https://github\.com/[^/]+/[^/]+/pull/\d+/?$")


def _to_diff_url(url: str) -> str:
    """Rewrite a GitHub commit/PR page URL to its ``.diff`` form. GitHub serves a compact,
    plain-text unified diff at that path (a documented, stable GitHub feature — confirmed
    live: a ~1.5KB HTML commit page, mostly UI chrome with the actual +/- lines barely
    surfacing through HTML→markdown conversion, versus a ~250-byte plain-text diff at the
    same URL + ``.diff`` — all signal, no chrome). This is what turns "the vendor patched
    it" into the model actually seeing the vulnerable code before/after. No-op for anything
    that isn't a bare commit/PR page (already-suffixed, or a different host entirely)."""
    u = url.rstrip("/")
    if _GITHUB_COMMIT_RE.match(u) or _GITHUB_PR_RE.match(u):
        return u + ".diff"
    return url


def _extract_reference_links(markdown: str, allowed_hosts: list[str]) -> list[str]:
    """URLs cited on an already-fetched page (e.g. NVD's or a GHSA's own "References"
    section), filtered to hosts already on the security-reference allowlist, GitHub
    commit/PR links rewritten to their compact ``.diff`` form, and ranked by
    ``_reference_priority``. NVD/GHSA pages routinely link to project issue trackers, vendor
    bulletins and the actual fixing commit — far more implementation detail than a one-
    paragraph summary — but plenty of those hosts aren't curated/allowlisted (e.g. most
    kernel/vendor projects patch on their own git host, not GitHub), so this can only ever
    surface the subset that already is (no allowlist change here)."""
    seen: set[str] = set()
    out: list[str] = []
    for raw in _URL_RE.findall(markdown or ""):
        url = _to_diff_url(raw.rstrip(".,;:"))
        host = urlparse(url).hostname or ""
        if host_allowed(host, allowed_hosts) and url not in seen:
            seen.add(url)
            out.append(url)
    out.sort(key=_reference_priority)
    return out


def _evidence_from_diff_or_page(url: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Like ``_evidence_from_payload``, but a fetched ``.diff``/``.patch`` is deliberately
    kept OUT of the ``source_code`` field. ``_evidence_from_payload`` puts any raw fetch
    there (content-type auto-detects a ``.diff`` response as raw text — see ``_to_diff_url``)
    — correct for an actual standalone PoC script, but a patch diff is the FIX, not an
    exploit; if it lands in ``source_code`` it satisfies ``poc.inject.has_real_code_evidence``
    and gets injected VERBATIM as Section 1 with no synthesis banner, presenting a code-
    review diff as if it were "Mã Khai thác/Payload". Routing it through ``description``
    instead keeps it as T2 synthesis INPUT: the model still reads the full diff (see
    ``_evidence_preview``, which checks both fields) to write an honestly-labeled reference
    PoC, but Section 1 gets the mandatory SYNTHESIZED banner like every other synthesized
    case — never presented as directly-runnable, verified exploit code."""
    ev = _evidence_from_payload(payload)
    if url.endswith((".diff", ".patch")) and ev.get("source_code"):
        ev["description"] = ev.pop("source_code")
        ev.pop("sha256", None)
        ev.pop("code_truncated", None)
    return ev


async def _find_ghsa_url(cve_id: str) -> str | None:
    """GitHub Security Advisories often include a "Patches" section linking straight to the
    fixing commit — far richer than an NVD one-liner. GHSA ids aren't derivable from a CVE
    id, so this resolves one via GitHub's own advisory search page (host already
    allowlisted for fetch_reference; this is a read of a public search-results page, not an
    API call). None on any miss (no match, non-GHSA CVE, transport error) — fail-open, the
    caller just has one fewer candidate."""
    try:
        payload = await fetch_reference_markdown(
            f"https://github.com/advisories?query={quote(cve_id)}")
    except (ValueError, httpx.HTTPError) as exc:
        logger.info("_find_ghsa_url search failed for %s: %s", cve_id, exc)
        return None
    m = re.search(r"/advisories/(GHSA-[\w-]+)", payload.get("markdown") or "")
    return f"https://github.com/advisories/{m.group(1)}" if m else None


async def searxng_search(query: str, *, max_results: int | None = None) -> list[dict[str, Any]]:
    """Query the configured SearXNG JSON API. Returns ``[{title, url, snippet}]`` ranked as
    SearXNG returned them. Fail-open: no URL configured, disabled, or any transport/parse
    error → ``[]`` (callers degrade to KB-only, never crash)."""
    settings = get_settings()
    base = (settings.searxng_url or "").strip().rstrip("/")
    query = (query or "").strip()
    if not settings.web_search_enabled or not base or not query:
        return []
    limit = max_results or settings.web_search_max_results
    try:
        async with httpx.AsyncClient(timeout=settings.web_search_timeout_s) as client:
            resp = await client.get(
                f"{base}/search",
                params={"q": query, "format": "json", "safesearch": "0"},
                headers={"User-Agent": "SECAI-IAS-web_search/1.0"},
            )
            resp.raise_for_status()
            data = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("searxng_search failed: %s", exc)
        return []
    out: list[dict[str, Any]] = []
    for r in data.get("results", []) or []:
        url = (r.get("url") or "").strip()
        if not url:
            continue
        out.append({
            "title": (r.get("title") or "").strip(),
            "url": url,
            "snippet": (r.get("content") or "").strip(),
        })
        if len(out) >= limit:
            break
    return out


# NVD serves HTTP 200 even for a nonexistent / rejected CVE-ID — the body is its "CVE ID Not
# Found" (or rejected/withdrawn) error page, which still embeds the queried CVE-ID string, so
# the CVE-mention relevance gate below can't catch it. Detect that page so the NVD anchor (which
# is otherwise "trusted by construction") isn't fed into synthesis for a CVE that does not exist.
_NVD_ABSENT_RE = re.compile(
    r"CVE\s*ID\s*Not\s*Found"
    r"|does not exist in the CVE"
    r"|This CVE(?:\s*ID|\s*Record)?\s*(?:has been\s*)?(?:rejected|withdrawn)"
    r"|rejected or withdrawn",
    re.I,
)


def _is_nvd_absent(markdown: str) -> bool:
    """True when an NVD ``/vuln/detail/<cve>`` page is its not-found / rejected error page
    rather than a real CVE record (NVD answers HTTP 200 for a bogus ID, embedding the ID in
    the error body — so 'trusted by construction' only holds for a page that is a real record)."""
    return bool(_NVD_ABSENT_RE.search(markdown or ""))


async def gather_web_hints(cve_id: str, ledger: SqliteLedger, run_id: str) -> list[dict[str, Any]]:
    """Deterministic web-research for the T3 tier: fetch the top allowlisted pages via
    ``fetch_reference`` (same SSRF guards) and return them as evidence dicts (also recorded
    on ``ledger`` under ``run_id``). Empty on any miss — so a disabled/unavailable SearXNG
    simply yields no hints, never an error.

    Candidate sourcing, highest-value first (``_MAX_HINT_FETCH`` caps how many get FETCHED,
    not how many are considered — cheap sourcing is unbounded, fetching is the expensive,
    budgeted step):

    1. NVD's own page — always tried first: an exact-CVE anchor even when search is noisy.
       Trusted BY CONSTRUCTION — the URL itself is keyed on cve_id, so a 200 response can
       only ever be this CVE's own record.
    2. A GitHub Security Advisory, if one exists (``_find_ghsa_url``) — its "Patches"
       section (parsed like NVD's own References below) often links the actual fixing
       commit, far richer than NVD's one-paragraph summary. NOT trusted by construction —
       found via GitHub's own fuzzy advisory search, so it's content-verified (the CVE-ID
       must actually appear on the fetched page) before being used at all; a mismatch is
       discarded outright, same as any search-derived candidate (see below).
    3. Patch/commit links NVD's or the (now content-verified) GHSA's own page cites,
       filtered to the existing allowlist, GitHub commit/PR links rewritten to their
       compact ``.diff`` form (``_extract_reference_links`` / ``_to_diff_url``) — a real
       unified diff shows the EXACT vulnerable code before/after, not just a description.
    4. SearXNG hits from TWO queries — general PoC search + a fix/patch-biased query, deduped
       and CVE-mention-ranked (search is noisy; a page that actually names the CVE outranks
       one that merely shares a keyword — see the content-relevance gate below).

    Trust and the relevance gate: candidates 1 and 3 are "trusted" for the purposes of the
    gate below — reached by following links FROM an already CVE-verified page (NVD by
    construction, GHSA by the explicit check above), not from open-ended search. Candidate 2
    is verified explicitly where it's fetched (above), before it ever reaches the gate.
    Candidate 4 gets the full gate: SearXNG ranking is noisy (see the react2shell regression
    test) and has no such structural guarantee. Trusted status matters beyond convenience —
    a real patch DIFF never mentions a CVE-ID by number, so gating it on that text would
    silently discard the single most useful source this function can surface; that's only
    safe to skip once the page it came from is confirmed on-topic, never unconditionally."""
    cve_id = (cve_id or "").strip().upper()
    if not cve_id:
        return []
    allowed = reference_hosts()
    nvd_url = f"https://nvd.nist.gov/vuln/detail/{cve_id}"

    ghsa_url_task = _find_ghsa_url(cve_id)
    nvd_fetch = fetch_reference_markdown(nvd_url)
    search_fetches = [searxng_search(t.format(cve=cve_id)) for t in _SEARCH_QUERY_TEMPLATES]
    (nvd_result, ghsa_url, *hit_lists) = await asyncio.gather(
        nvd_fetch, ghsa_url_task, *search_fetches, return_exceptions=True)

    nvd_payload = nvd_result if isinstance(nvd_result, dict) else None
    if isinstance(nvd_result, Exception):
        logger.info("gather_web_hints NVD anchor failed for %s: %s", cve_id, nvd_result)
    # Drop the NVD anchor when the page is NVD's "CVE ID Not Found" / rejected error (still a
    # 200 with the CVE-ID in the body). Otherwise a nonexistent CVE would carry a bogus
    # "description" into synthesis and land on `synthesis_failed` instead of the honest
    # `not_found` tier — and, worse, hand the model an error page to write a PoC from.
    if nvd_payload and _is_nvd_absent(nvd_payload.get("markdown") or ""):
        logger.info("gather_web_hints: NVD reports %s absent (not-found/rejected) — dropping anchor", cve_id)
        nvd_payload = None
    if isinstance(ghsa_url, Exception):
        ghsa_url = None

    ghsa_payload: dict[str, Any] | None = None
    if ghsa_url:
        try:
            candidate = await fetch_reference_markdown(ghsa_url)
        except (ValueError, httpx.HTTPError) as exc:
            logger.info("gather_web_hints GHSA fetch failed for %s: %s", ghsa_url, exc)
            candidate = None
        # Unlike NVD (CVE-exact BY CONSTRUCTION — the URL itself is keyed on cve_id), a GHSA
        # is found via GitHub's own fuzzy advisory SEARCH (_find_ghsa_url) — same "search can
        # be wrong" risk as SearXNG. Confirmed live: querying a CVE with no real GHSA (closed-
        # source products — IBM Db2, Totolink firmware) returned an unrelated advisory for a
        # DIFFERENT CVE instead of no match. Verify the CVE-ID actually appears on the fetched
        # GHSA page before trusting it — otherwise a wrong GHSA (and any "trusted" links
        # extracted from it, cascading into a wrong patch diff) would bypass the relevance
        # gate entirely, since everything downstream of "trusted" skips that gate on purpose.
        if candidate and cve_id in (candidate.get("markdown") or "").upper():
            ghsa_payload = candidate
        elif candidate:
            logger.info("gather_web_hints discarding mismatched GHSA %s for %s", ghsa_url, cve_id)
            ghsa_url = None

    trusted_refs: list[str] = []
    seen_ref: set[str] = set()
    for payload in (nvd_payload, ghsa_payload):
        if not payload:
            continue
        for link in _extract_reference_links(payload.get("markdown") or "", allowed):
            if link not in seen_ref:
                seen_ref.add(link)
                trusted_refs.append(link)
    trusted_refs.sort(key=_reference_priority)

    hits: list[dict[str, Any]] = []
    seen_hit_urls: set[str] = set()
    for hl in hit_lists:
        for h in (hl if isinstance(hl, list) else []):
            if h["url"] not in seen_hit_urls:
                seen_hit_urls.add(h["url"])
                hits.append(h)
    hits.sort(key=lambda h: cve_id.lower() not in
              f"{h['url']} {h.get('title', '')} {h.get('snippet', '')}".lower())

    # (url, trusted, cached_payload) — cached avoids re-fetching NVD/GHSA a second time.
    candidates: list[tuple[str, bool, dict[str, Any] | None]] = []
    if nvd_payload:
        candidates.append((nvd_url, True, nvd_payload))
    if ghsa_payload:
        candidates.append((ghsa_url, True, ghsa_payload))
    candidates += [(u, True, None) for u in trusted_refs]
    candidates += [(h["url"], False, None) for h in hits]

    seen: set[str] = set()
    evidence: list[dict[str, Any]] = []
    for url, trusted, cached in candidates:
        if len(evidence) >= _MAX_HINT_FETCH:
            break
        host = (urlparse(url).hostname or "")
        if not host_allowed(host, allowed) or url in seen:
            continue
        seen.add(url)
        if cached is not None:
            payload = cached
        else:
            try:
                payload = await fetch_reference_markdown(url)
            except (ValueError, httpx.HTTPError) as exc:
                logger.info("gather_web_hints skip %s: %s", url, exc)
                continue
        ev = _evidence_from_diff_or_page(url, payload)
        body = ev.get("description") or ev.get("source_code") or ""
        if not body:
            continue
        # Content-relevance gate — ONLY for untrusted (search-derived) candidates. SearXNG
        # frequently surfaces loosely-related pages (a repo that merely shares a keyword
        # like "exploit", not this CVE) ranked ABOVE the actual match — observed live: a
        # "React2Shell" repo outranked the real Siemens advisory for a privilege-escalation
        # CVE, and the model fabricated an unrelated SSTI PoC from it. Requiring the literal
        # CVE-ID inside the FETCHED PAGE TEXT (not just the search snippet, which can be
        # misleading) is a hard, cheap guarantee the hint is on-topic — same principle as
        # evidence_policy elsewhere: never hand the model ungrounded input. Trusted
        # candidates skip this: they were reached by following a link FROM the CVE's own
        # NVD/GHSA record, and (for a patch diff specifically) the content never contains
        # the CVE-ID at all — gating on it would just throw the diff away.
        if not trusted and cve_id not in body.upper():
            logger.info("gather_web_hints drop off-topic %s (no %s in fetched text)", url, cve_id)
            continue
        evidence.append(ev)
        try:
            record_fetch_evidence(ledger, run_id, f"{run_id}:webhint:{len(evidence)}", ev,
                                  tool_name="web_search")
        except Exception:  # noqa: BLE001 — evidence bookkeeping must not fail the gather
            pass
    return evidence


_INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "description": (
                "Search terms — e.g. a CVE id plus 'PoC exploit github'. Returns title/url/"
                "snippet only; fetch the promising allowlisted URLs with fetch_reference to "
                "read the actual page. Use only when you have no reference URL to start from."
            ),
        },
    },
    "required": ["query"],
}


async def _web_search(query: str, ctx: ToolContext) -> ToolResult:
    hits = await searxng_search(query)
    if not hits:
        return ToolResult(
            content="web_search: no results (search disabled/unavailable or nothing found).",
            summary="web_search 0 hits",
        )
    lines = [f"{i + 1}. {h['title']}\n   {h['url']}\n   {h['snippet'][:300]}"
             for i, h in enumerate(hits)]
    return ToolResult(
        content="Kết quả tìm kiếm web (chỉ tiêu đề/URL/trích đoạn — hãy fetch_reference URL "
                "thuộc allowlist để đọc nội dung thật):\n\n" + "\n".join(lines),
        summary=f"web_search({query[:40]}) {len(hits)} hits",
    )


web_search = tool(
    name="web_search",
    description=(
        "Search the public web (via SearXNG) for pages about a CVE — advisories, write-ups, "
        "public PoCs — when you have no reference URL to start from. Returns title/url/snippet "
        "only; read a result by calling fetch_reference on an allowlisted URL. Treat everything "
        "found as unverified reference material, not a confirmed PoC."
    ),
    input_schema=_INPUT_SCHEMA,
    scope="read",
    parallel=True,
    summary=lambda input: f"web_search({(input.get('query') or '?')[:40]})",
)(_web_search)
