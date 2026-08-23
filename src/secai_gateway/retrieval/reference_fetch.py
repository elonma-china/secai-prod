"""fetch_reference — SSRF-guarded HTTPS fetch of security-reference pages → markdown.

A broader-scoped sibling of ``web_fetch`` (see ``http_fetch.py``). Where ``web_fetch``
is locked to the OWASP/CWE/CISA taxonomy allowlist for chat grounding, this tool serves
**both** the chatbot (``chat``) and exploit-research (``exploit_research``) profiles: it
fetches CVE records, GHSA/vendor advisories, MITRE CWE/CAPEC/ATT&CK pages, CISA alerts
and public PoC pages from a curated security-domain allowlist, converts them to clean
markdown, and supports chunked reads (``start_index``) so a long advisory can be paged.

The security-critical primitives are reused verbatim from ``http_fetch`` — HTTPS only,
no credentials, DNS→public-IP check, and per-hop redirect re-validation — so the SSRF
posture is identical to ``web_fetch``; only the host allowlist and read window differ.
"""
from __future__ import annotations

import hashlib
import re
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx
from linch.tools.base import ToolContext, ToolResult
from linch.tools.function import tool

from ..airgap import assert_online_fetch_allowed
from ..config import get_settings
from ..poc.tools import record_fetch_evidence
from ..state import SqliteLedger
from .http_fetch import html_to_markdown, host_allowed, validate_fetch_url

# Curated public security-reference hosts. Broader than web_fetch's taxonomy allowlist
# but still a fail-closed set — arbitrary internet fetch is never permitted. Override
# with FETCH_REFERENCE_ALLOWED_HOSTS (comma-separated apexes) when a deployment needs more.
_DEFAULT_REFERENCE_HOSTS = (
    # CVE / weakness / technique taxonomies
    "nvd.nist.gov",
    "cve.org",
    "cve.mitre.org",
    "cwe.mitre.org",
    "capec.mitre.org",
    "attack.mitre.org",
    "kb.cert.org",
    # advisories, alerts, trackers
    "cisa.gov",
    "owasp.org",
    "github.com",              # GHSA advisories + PoC repos
    "githubusercontent.com",   # raw.* / gist.* PoC source
    "security.snyk.io",
    "vuldb.com",
    "huntr.com",
    "portswigger.net",
    # public exploit / mailing-list archives
    "exploit-db.com",
    "packetstormsecurity.com",
    "seclists.org",
)


def reference_hosts() -> list[str]:
    """Effective allowlist: FETCH_REFERENCE_ALLOWED_HOSTS or the built-in default set."""
    raw = (get_settings().fetch_reference_allowed_hosts or "").strip()
    hosts = [h.strip().lower().rstrip(".") for h in raw.split(",") if h.strip()]
    return hosts or list(_DEFAULT_REFERENCE_HOSTS)


def reference_host_allowed(host: str) -> bool:
    """Public predicate used by the pre-tool hook for defense-in-depth."""
    return host_allowed(host, reference_hosts())


async def fetch_reference_markdown(
    url: str,
    *,
    start_index: int = 0,
    raw: bool = False,
    max_chars: int | None = None,
    timeout_s: float | None = None,
) -> dict[str, Any]:
    """Fetch an allowlisted HTTPS reference URL and return a windowed markdown payload.

    Raises ``ValueError`` on any policy violation (scheme/credentials/allowlist/SSRF/
    redirect). The read window is ``text[start_index : start_index + max_chars]`` so a
    long page can be paged with the returned ``next_start_index``.
    """
    assert_online_fetch_allowed(url)  # air-gap backstop (config also disables the tool)
    settings = get_settings()
    allowed = reference_hosts()
    timeout = timeout_s if timeout_s is not None else float(settings.fetch_reference_timeout_s)
    limit = max_chars if max_chars is not None else int(settings.fetch_reference_max_chars)
    start = max(0, int(start_index))

    current = validate_fetch_url(url, allowed=allowed)
    headers = {
        "User-Agent": "SECAI-IAS-fetch_reference/1.0 (+security-assistant; allowlisted)",
        "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
    }
    final_url = current
    body = ""
    status = 0
    is_html = False
    async with httpx.AsyncClient(follow_redirects=False, timeout=timeout) as client:
        for _ in range(5):
            validate_fetch_url(current, allowed=allowed)  # re-validate every hop (SSRF)
            resp = await client.get(current, headers=headers)
            status = resp.status_code
            if resp.is_redirect:
                loc = resp.headers.get("location")
                if not loc:
                    raise ValueError(f"redirect without Location ({status})")
                current = urljoin(str(resp.url), loc)
                continue
            resp.raise_for_status()
            final_url = str(resp.url)
            validate_fetch_url(final_url, allowed=allowed)
            ctype = (resp.headers.get("content-type") or "").lower()
            body = resp.text
            is_html = "html" in ctype or body.lstrip().startswith("<")
            break
        else:
            raise ValueError("too many redirects")

    text = body.strip() if (raw or not is_html) else html_to_markdown(body)
    total_chars = len(text)

    window = text[start:]
    truncated = False
    if len(window) > limit:
        window = window[:limit].rstrip() + "\n\n…[truncated — call again with start_index to page further]"
        truncated = True
    title_m = re.search(r"^#\s+(.+)$", text, re.M)
    return {
        "url": final_url,
        "status": status,
        "title": title_m.group(1).strip() if title_m else "",
        "raw": raw or not is_html,
        "start_index": start,
        "next_start_index": start + limit if truncated else None,
        "total_chars": total_chars,
        "chars": len(window),
        "truncated": truncated,
        "markdown": window,
    }


_INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "url": {
            "type": "string",
            "description": (
                "HTTPS URL on an allowlisted security-reference host — e.g. a CVE record "
                "(https://nvd.nist.gov/vuln/detail/CVE-2024-XXXX), a GitHub advisory/PoC "
                "(https://github.com/…), MITRE CWE/CAPEC/ATT&CK, CISA, exploit-db, seclists "
                "or packetstormsecurity. HTTPS only; the host must be on the reference allowlist."
            ),
        },
        "start_index": {
            "type": "integer",
            "description": (
                "Character offset to start the read window at (default 0). When a result is "
                "truncated, call again with the returned next_start_index to page further."
            ),
        },
        "raw": {
            "type": "boolean",
            "description": (
                "Return the raw response text without HTML→markdown conversion. Use for "
                "plain-text PoC/source files (e.g. raw.githubusercontent.com). Default false."
            ),
        },
    },
    "required": ["url"],
}


def _ctx_run_id(ctx: ToolContext) -> str:
    return str(getattr(ctx, "run_id", None) or getattr(ctx, "session_id", None) or "")


def _ctx_idempotency_key(ctx: ToolContext, suffix: str) -> str:
    return str(getattr(ctx, "idempotency_key", None) or f"{_ctx_run_id(ctx)}:{suffix}")


_EXT_LANG = {
    "py": "python", "js": "javascript", "ts": "typescript", "rb": "ruby", "go": "go",
    "php": "php", "sh": "bash", "bash": "bash", "pl": "perl", "c": "c", "h": "c",
    "cpp": "cpp", "cc": "cpp", "java": "java", "rs": "rust", "ps1": "powershell",
    "yaml": "yaml", "yml": "yaml", "json": "json", "xml": "xml", "html": "html",
}


def _lang_from_url(url: str) -> str:
    path = urlparse(url).path
    ext = path.rsplit(".", 1)[-1].lower() if "." in path.rsplit("/", 1)[-1] else ""
    return _EXT_LANG.get(ext, "text")


def _evidence_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Shape a fetched page into the evidence dict poc/inject.py consumes.

    HTML advisory/reference page → ``description`` only (T2 synthesize-from-text tier);
    a raw/plain-text file (``raw`` mode or non-HTML content-type) → ``source_code`` +
    ``sha256`` (T1 real-code tier). Both carry the canonical URL; record_fetch_evidence
    stamps the shared draft/not-executed markers. A page whose markdown is empty yields
    an empty description and is dropped by record_fetch_evidence (no sha256, no text)."""
    url = payload["url"]
    text = payload.get("markdown") or ""
    evidence: dict[str, Any] = {
        "url": url,
        "source": "web_reference",
        "source_ref": url,
        "title": payload.get("title") or "",
    }
    if payload.get("raw"):
        # Real code/source fetched verbatim → runnable-evidence tier.
        evidence["source_code"] = text
        evidence["sha256"] = hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()
        evidence["language"] = _lang_from_url(url)
        evidence["code_truncated"] = bool(payload.get("truncated"))
    else:
        # Prose advisory/reference → synthesis-input tier (no runnable code, no sha256).
        evidence["description"] = text
    return evidence


def build_reference_tools(ledger: SqliteLedger, settings: Any = None) -> dict[str, Any]:
    """Build the ``fetch_reference`` tool bound to the evidence ledger.

    Mirrors ``build_poc_tools``: a successful fetch is persisted via
    ``record_fetch_evidence`` so a page pulled during an exploit-research run is available
    to poc/inject.py's Section-1 tiers (real code for raw files, synthesis text for
    advisories). Ledger writes never sink the fetch itself — the tool still returns the
    page even if persistence hiccups."""

    async def _fetch_reference(
        url: str, ctx: ToolContext, start_index: int = 0, raw: bool = False,
    ) -> ToolResult:
        try:
            payload = await fetch_reference_markdown(url, start_index=int(start_index), raw=bool(raw))
        except ValueError as exc:
            return ToolResult(
                content=f"fetch_reference rejected: {exc}",
                summary=f"fetch_reference denied ({exc})",
                is_error=True,
            )
        except httpx.HTTPError as exc:
            return ToolResult(
                content=f"fetch_reference failed: {exc}",
                summary=f"fetch_reference error ({type(exc).__name__})",
                is_error=True,
            )

        try:
            record_fetch_evidence(
                ledger,
                _ctx_run_id(ctx),
                _ctx_idempotency_key(ctx, "fetch_reference"),
                _evidence_from_payload(payload),
                tool_name="fetch_reference",
            )
        except Exception:  # noqa: BLE001 — evidence bookkeeping must not fail the fetch
            pass

        nxt = payload["next_start_index"]
        header = (
            f"### Fetched: {payload['url']}\n"
            f"status={payload['status']} chars={payload['chars']}/{payload['total_chars']} "
            f"truncated={payload['truncated']}"
            + (f" next_start_index={nxt}" if nxt is not None else "")
            + "\n\n"
        )
        return ToolResult(
            content=header + payload["markdown"],
            summary=f"fetch_reference({payload['url']}) {payload['chars']} chars",
            truncated=bool(payload["truncated"]),
        )

    fetch_reference = tool(
        name="fetch_reference",
        description=(
            "Fetch an allowlisted public security-reference page (CVE records, GHSA/vendor "
            "advisories, MITRE CWE/CAPEC/ATT&CK, CISA alerts, exploit-db, public PoC pages) "
            "and return clean markdown. HTTPS only; the host must be on the reference "
            "allowlist. For long pages, page through with start_index; pass raw=true for "
            "plain-text PoC/source files. Ground CVE/advisory/PoC details on the live page "
            "instead of answering from memory."
        ),
        input_schema=_INPUT_SCHEMA,
        scope="read",
        parallel=True,
        summary=lambda input: f"fetch_reference({(input.get('url') or '?')[:60]})",
    )(_fetch_reference)

    return {"fetch_reference": fetch_reference}
