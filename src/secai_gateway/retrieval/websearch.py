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
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse

import httpx
from linch.tools.base import ToolContext, ToolResult
from linch.tools.function import tool

from ..airgap import airgap_enabled
from ..config import get_settings
from ..poc.tools import record_fetch_evidence
from ..state import SqliteLedger
from .http_fetch import host_allowed, validate_fetch_url
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
        try:
            host = urlparse(url).hostname or ""
        except ValueError:
            # Malformed URL (e.g. an unbalanced IPv6 bracket like "https://[::1") — urlparse
            # raises ValueError instead of returning a parsed result. Skip it, don't crash the
            # whole reference scan (this was breaking patch-diff acquisition for GHSA-only CVEs).
            continue
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
    if airgap_enabled():  # air-gap: no GitHub advisory lookup
        return None
    try:
        payload = await fetch_reference_markdown(
            f"https://github.com/advisories?query={quote(cve_id)}")
    except (ValueError, httpx.HTTPError) as exc:
        logger.info("_find_ghsa_url search failed for %s: %s", cve_id, exc)
        return None
    m = re.search(r"/advisories/(GHSA-[\w-]+)", payload.get("markdown") or "")
    return f"https://github.com/advisories/{m.group(1)}" if m else None


# ── GitHub advisory + patch-diff grounding ─────────────────────────────────────
# The T3 synthesize tier's weakest output is a PoC whose endpoint/param/payload is GUESSED
# because the model only saw a one-line description. The single highest-value antidote is the
# FIXING COMMIT's diff — it shows the exact vulnerable code before/after. GitHub Security
# Advisories carry ``first_patched_version``; comparing that tag against the previous release
# yields the diff, fetched here via the GitHub REST API with a Bearer token (``github_token``
# setting, or auto-resolved from the local ``gh auth token`` CLI). All results are synthesis
# INPUT (``description`` only — never ``source_code``), so Section 1 still carries the
# SYNTHESIZED banner; nothing here is ever treated as a verified, runnable exploit.
_GH_TOKEN_CACHE: dict[str, Any] = {"value": None, "ts": 0.0}
_GH_TOKEN_TTL_S = 300.0
_GH_API_TIMEOUT_S = 20.0
# A compare diff is usually several files; paths matching these are the ones most likely to
# be the vulnerable component itself (vs. changelog/version-bump noise) — ranked first + kept
# even when the total diff budget is tight.
_DIFF_RELEVANT_PATH_RE = re.compile(
    r"(customer|contact|view|validate|login|auth|admin|controller|upload|email|user|session|"
    r"sanitize|escape|mail|form)", re.I)
_REF_PREFIXES = ("", "edition/", "release/", "v")


def _github_token_from_cli() -> str | None:
    # 1) gh CLI — try PATH first, then the common user-local install (the systemd service's
    #    PATH does not include ~/.local/bin, so `gh` alone is not found there).
    for gh in ("gh", os.path.expanduser("~/.local/bin/gh")):
        try:
            out = subprocess.run([gh, "auth", "token"], capture_output=True, text=True, timeout=5)
        except (OSError, subprocess.SubprocessError):
            continue
        tok = (out.stdout or "").strip()
        if tok:
            return tok
    # 2) fallback — read gh's stored credential file directly (no subprocess, no PATH dependency).
    try:
        text = Path.home().joinpath(".config", "gh", "hosts.yml").read_text(encoding="utf-8")
        m = re.search(r"oauth_token:\s*(\S+)", text)
        if m:
            return m.group(1)
    except OSError:
        pass
    return None


async def _github_token() -> str | None:
    """Resolve a GitHub token: explicit ``github_token`` setting, else the local ``gh`` CLI
    (cached a few minutes). Never logged."""
    settings = get_settings()
    tok = (getattr(settings, "github_token", "") or "").strip()
    if tok:
        return tok
    now = time.monotonic()
    if _GH_TOKEN_CACHE["value"] and now - _GH_TOKEN_CACHE["ts"] < _GH_TOKEN_TTL_S:
        return _GH_TOKEN_CACHE["value"]
    tok = await asyncio.to_thread(_github_token_from_cli)
    _GH_TOKEN_CACHE["value"] = tok
    _GH_TOKEN_CACHE["ts"] = now
    return tok


async def _github_api_get(url: str, token: str) -> Any:
    """GET a GitHub REST API URL (api.github.com) with a Bearer token, SSRF-guarded by the
    same validate_fetch_url the rest of the pipeline uses. Returns parsed JSON, or None on any
    policy/transport/parse miss (fail-open)."""
    try:
        validate_fetch_url(url, allowed=reference_hosts())
    except ValueError as exc:
        logger.info("github api URL rejected (%s): %s", url, exc)
        return None
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "SECAI-IAS-github-advisory/1.0",
    }
    try:
        async with httpx.AsyncClient(timeout=_GH_API_TIMEOUT_S, follow_redirects=False) as client:
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()
            return resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        logger.info("github api fetch failed for %s: %s", url, exc)
        return None


async def _resolve_ghsa(cve_id: str, token: str) -> dict[str, Any] | None:
    """Resolve the GitHub Security Advisory for ``cve_id`` via the advisory list API. Returns
    the advisory dict (ghsa_id, description, html_url, vulnerabilities, …) or None."""
    data = await _github_api_get(
        f"https://api.github.com/advisories?cve_id={quote(cve_id)}", token)
    if not isinstance(data, list) or not data:
        return None
    for adv in data:
        if str(adv.get("cve_id") or "").upper() == cve_id.upper():
            return adv
    return data[0]


def _prev_version(patched: str) -> str | None:
    """Best-effort previous release by decrementing the last numeric segment (covers the common
    patch-release fix, e.g. 8.2.6 → 8.2.5). None when the version isn't a plain X.Y.Z."""
    m = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", patched)
    if not m:
        return None
    a, b, c = (int(x) for x in m.groups())
    if c > 0:
        return f"{a}.{b}.{c - 1}"
    if b > 0:
        return f"{a}.{b - 1}.0"
    return f"{a - 1}.0.0"


def _render_diff(files: list[dict[str, Any]], max_chars: int) -> str:
    parts: list[str] = []
    used = 0
    ordered = sorted(
        files,
        key=lambda f: (0 if _DIFF_RELEVANT_PATH_RE.search(f.get("filename") or "") else 1,
                       f.get("filename") or ""),
    )
    for f in ordered:
        patch = f.get("patch") or ""
        fn = f.get("filename") or ""
        if not patch:
            continue
        chunk = f"=== {fn} ({f.get('status')} +{f.get('additions', 0)}/-{f.get('deletions', 0)}) ===\n{patch}"
        if used + len(chunk) > max_chars:
            continue
        parts.append(chunk)
        used += len(chunk)
    return "\n\n".join(parts)


async def _compare_diff(owner: str, repo: str, base: str, head: str, token: str,
                        max_chars: int) -> str | None:
    """Fetch the fix diff between the previous release (``base``) and the patched one (``head``)
    via the compare API, trying a few common tag-prefix variants. Returns the bounded, rendered
    diff, or None when no comparison yielded files."""
    for bp in _REF_PREFIXES:
        for hp in _REF_PREFIXES:
            url = (f"https://api.github.com/repos/{owner}/{repo}/compare/"
                   f"{quote(bp + base)}...{quote(hp + head)}")
            data = await _github_api_get(url, token)
            if not isinstance(data, dict):
                continue
            files = data.get("files") or []
            if not files or data.get("status") == "identical":
                continue
            diff = _render_diff(files, max_chars)
            if diff:
                return diff
    return None


async def _fetch_fix_diff(advisory: dict[str, Any], token: str, max_chars: int) -> str | None:
    """Given a resolved advisory, derive owner/repo + patched version and fetch the fix diff."""
    owner = repo = None
    # owner/repo comes from the repo-level advisory URL or the source-code location (the
    # advisory's own html_url points at /advisories/GHSA-…, which has no repo path).
    for url in (advisory.get("repository_advisory_url") or "",
                advisory.get("source_code_location") or ""):
        m = re.search(r"github\.com/(?:repos/)?([^/]+)/([^/]+)", url or "")
        if m:
            owner, repo = m.group(1), m.group(2)
            break
    if not owner:
        # composer/npm/go package names are usually owner/name — last resort.
        for v in advisory.get("vulnerabilities") or []:
            name = (v.get("package") or {}).get("name") or ""
            if "/" in name:
                owner, repo = name.split("/", 1)
                break
    if not owner or not repo:
        return None
    for v in advisory.get("vulnerabilities") or []:
        patched = (v.get("first_patched_version") or "").strip()
        base = _prev_version(patched)
        if not patched or not base:
            continue
        diff = await _compare_diff(owner, repo, base, patched, token, max_chars)
        if diff:
            return diff
    return None


async def _github_advisory_evidence(cve_id: str) -> list[dict[str, Any]]:
    """Fetch the CVE's GitHub advisory description + fixing commit diff and shape them as
    synthesis-input evidence (``description`` only). [] on any miss — the caller degrades to the
    existing description-only path."""
    if airgap_enabled():  # air-gap: no api.github.com fetch
        return []
    settings = get_settings()
    if not getattr(settings, "github_advisory_enabled", True):
        return []
    token = await _github_token()
    if not token:
        return []
    advisory = await _resolve_ghsa(cve_id, token)
    if not advisory:
        return []
    out: list[dict[str, Any]] = []
    url = advisory.get("html_url") or ""
    desc = (advisory.get("description") or "").strip()
    if desc:
        out.append({
            "url": url,
            "source": "github_advisory",
            "source_ref": url,
            "title": advisory.get("summary") or f"GitHub Advisory {advisory.get('ghsa_id') or cve_id}",
            "cve_ids": [cve_id],
            "description": desc,
        })
    diff = await _fetch_fix_diff(advisory, token, int(getattr(settings, "github_diff_max_chars", 30000)))
    if diff:
        out.append({
            "url": url,
            "source": "github_compare",
            "source_ref": url,
            "title": "GitHub fix commit diff (base...patched)",
            "cve_ids": [cve_id],
            "description": diff,
        })
    return out


async def searxng_search(query: str, *, max_results: int | None = None) -> list[dict[str, Any]]:
    """Query the configured SearXNG JSON API. Returns ``[{title, url, snippet}]`` ranked as
    SearXNG returned them. Fail-open: no URL configured, disabled, or any transport/parse
    error → ``[]`` (callers degrade to KB-only, never crash)."""
    if airgap_enabled():  # air-gap: no SearXNG / web discovery
        return []
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
    if airgap_enabled():  # air-gap: no NVD/GHSA/GitHub/SearXNG fetch of any kind
        return []
    cve_id = (cve_id or "").strip().upper()
    if not cve_id:
        return []
    allowed = reference_hosts()
    nvd_url = f"https://nvd.nist.gov/vuln/detail/{cve_id}"

    ghsa_url_task = _find_ghsa_url(cve_id)
    gh_evidence_task = _github_advisory_evidence(cve_id)
    nvd_fetch = fetch_reference_markdown(nvd_url)
    search_fetches = [searxng_search(t.format(cve=cve_id)) for t in _SEARCH_QUERY_TEMPLATES]
    (nvd_result, ghsa_url, gh_evidence, *hit_lists) = await asyncio.gather(
        nvd_fetch, ghsa_url_task, gh_evidence_task, *search_fetches, return_exceptions=True)
    if not isinstance(gh_evidence, list):
        gh_evidence = []

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
    # GitHub advisory + fix diff are the highest-value synthesis input — return them FIRST so
    # they outrank NVD/SearXNG prose in the model's grounding appendix.
    return gh_evidence + evidence


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
