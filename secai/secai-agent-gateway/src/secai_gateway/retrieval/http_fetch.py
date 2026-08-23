"""web_fetch — allowlisted HTTPS fetch → clean markdown for chat grounding.

Used for time-sensitive taxonomy pages (OWASP Top 10, CWE Top 25) that must not
come from stale model memory. Direct httpx + stdlib HTML stripping — no Playwright.
"""
from __future__ import annotations

import ipaddress
import re
import socket
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx
from linch.tools.base import ToolResult
from linch.tools.function import tool

from ..airgap import assert_online_fetch_allowed
from ..config import get_settings

_DEFAULT_HOSTS = (
    "owasp.org",
    "cwe.mitre.org",
    "cisa.gov",
    "www.cisa.gov",
    "nvd.nist.gov",
)

_SKIP_TAGS = frozenset({
    "script", "style", "noscript", "svg", "iframe", "template", "nav", "footer",
    "header", "aside", "form",
})
_BLOCK_TAGS = frozenset({
    "p", "div", "section", "article", "main", "li", "tr", "br", "hr",
    "h1", "h2", "h3", "h4", "h5", "h6", "pre", "blockquote", "ul", "ol", "table",
})
_HEADING = {"h1": "#", "h2": "##", "h3": "###", "h4": "####", "h5": "#####", "h6": "######"}


class _HtmlToMarkdown(HTMLParser):
    """Minimal HTML → markdown (stdlib only)."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._skip_depth = 0
        self._in_pre = False
        self._href: str | None = None
        self._link_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        if tag == "br":
            self._parts.append("\n")
            return
        if tag == "hr":
            self._parts.append("\n\n---\n\n")
            return
        if tag == "pre" or tag == "code":
            self._in_pre = True
            self._parts.append("\n```\n" if tag == "pre" else "`")
            return
        if tag in _HEADING:
            self._parts.append(f"\n\n{_HEADING[tag]} ")
            return
        if tag == "li":
            self._parts.append("\n- ")
            return
        if tag in _BLOCK_TAGS:
            self._parts.append("\n\n")
            return
        if tag == "a":
            href = dict(attrs).get("href")
            self._href = href
            self._link_text = []
            return
        if tag in {"b", "strong"}:
            self._parts.append("**")
            return
        if tag in {"i", "em"}:
            self._parts.append("*")
            return

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in _SKIP_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if self._skip_depth:
            return
        if tag == "pre":
            self._in_pre = False
            self._parts.append("\n```\n")
            return
        if tag == "code" and not self._in_pre:
            self._parts.append("`")
            return
        if tag in {"b", "strong", "i", "em"}:
            self._parts.append("**" if tag in {"b", "strong"} else "*")
            return
        if tag == "a":
            text = "".join(self._link_text).strip() or (self._href or "")
            if self._href and text:
                self._parts.append(f"[{text}]({self._href})")
            elif text:
                self._parts.append(text)
            self._href = None
            self._link_text = []
            return
        if tag in _HEADING or tag in {"p", "div", "section", "article", "li"}:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        if self._href is not None:
            self._link_text.append(data)
            return
        if self._in_pre:
            self._parts.append(data)
            return
        cleaned = re.sub(r"[ \t]+", " ", data)
        if cleaned.strip() or cleaned == " ":
            self._parts.append(cleaned)

    def output(self) -> str:
        text = "".join(self._parts)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()


def _allowed_hosts() -> list[str]:
    raw = (get_settings().web_fetch_allowed_hosts or "").strip()
    hosts = [h.strip().lower().rstrip(".") for h in raw.split(",") if h.strip()]
    return hosts or list(_DEFAULT_HOSTS)


def host_allowed(host: str, allowed: list[str] | None = None) -> bool:
    host = (host or "").lower().rstrip(".")
    if not host:
        return False
    for apex in allowed or _allowed_hosts():
        if host == apex or host.endswith("." + apex):
            return True
    return False


def _is_public_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def validate_fetch_url(url: str, *, allowed: list[str] | None = None) -> str:
    """Return normalized HTTPS URL or raise ValueError (SSRF / allowlist)."""
    raw = (url or "").strip()
    if not raw:
        raise ValueError("url is required")
    parsed = urlparse(raw)
    if parsed.scheme.lower() != "https":
        raise ValueError("only https URLs are allowed")
    if parsed.username or parsed.password:
        raise ValueError("URLs with credentials are not allowed")
    host = (parsed.hostname or "").lower().rstrip(".")
    if not host_allowed(host, allowed):
        raise ValueError(f"host '{host}' is not in the fetch allowlist")
    try:
        infos = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ValueError(f"DNS resolution failed for '{host}': {exc}") from exc
    if not infos:
        raise ValueError(f"no addresses for '{host}'")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not _is_public_ip(ip):
            raise ValueError(f"refusing non-public address for '{host}' ({ip})")
    # Rebuild without fragment; keep path/query.
    path = parsed.path or "/"
    query = f"?{parsed.query}" if parsed.query else ""
    return f"https://{host}{path}{query}"


def html_to_markdown(html: str) -> str:
    parser = _HtmlToMarkdown()
    try:
        parser.feed(html or "")
        parser.close()
    except Exception:
        # Extremely broken HTML → fall back to tag strip.
        return re.sub(r"<[^>]+>", " ", html or "")
    return parser.output()


async def fetch_url_markdown(url: str, *, max_chars: int | None = None, timeout_s: float | None = None) -> dict[str, Any]:
    """Fetch allowlisted HTTPS URL and return markdown payload (raises ValueError on policy)."""
    assert_online_fetch_allowed(url)  # air-gap backstop (config also disables the tool)
    settings = get_settings()
    allowed = _allowed_hosts()
    timeout = timeout_s if timeout_s is not None else float(settings.web_fetch_timeout_s)
    limit = max_chars if max_chars is not None else int(settings.web_fetch_max_chars)

    current = validate_fetch_url(url, allowed=allowed)
    headers = {
        "User-Agent": "SECAI-IAS-web_fetch/1.0 (+security-assistant; allowlisted)",
        "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
    }
    final_url = current
    body = ""
    status = 0
    text = ""
    async with httpx.AsyncClient(follow_redirects=False, timeout=timeout) as client:
        for _ in range(5):
            validate_fetch_url(current, allowed=allowed)
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
            if "html" not in ctype and not body.lstrip().startswith("<"):
                text = body.strip()
            else:
                text = html_to_markdown(body)
            break
        else:
            raise ValueError("too many redirects")

    if not text and body:
        text = html_to_markdown(body)

    truncated = False
    if len(text) > limit:
        text = text[:limit].rstrip() + "\n\n…[truncated]"
        truncated = True
    title_m = re.search(r"^#\s+(.+)$", text, re.M)
    return {
        "url": final_url,
        "status": status,
        "title": title_m.group(1).strip() if title_m else "",
        "truncated": truncated,
        "chars": len(text),
        "markdown": text,
    }


_INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "url": {
            "type": "string",
            "description": (
                "HTTPS URL on an allowlisted host (owasp.org, cwe.mitre.org, cisa.gov, "
                "nvd.nist.gov). Prefer canonical pages, e.g. "
                "https://owasp.org/Top10/2025/ or "
                "https://cwe.mitre.org/top25/archive/2025/2025_cwe_top25.html"
            ),
        },
    },
    "required": ["url"],
}


async def _web_fetch(url: str) -> ToolResult:
    try:
        payload = await fetch_url_markdown(url)
    except ValueError as exc:
        return ToolResult(
            content=f"web_fetch rejected: {exc}",
            summary=f"web_fetch denied ({exc})",
            is_error=True,
        )
    except httpx.HTTPError as exc:
        return ToolResult(
            content=f"web_fetch failed: {exc}",
            summary=f"web_fetch error ({type(exc).__name__})",
            is_error=True,
        )

    header = (
        f"### Fetched: {payload['url']}\n"
        f"status={payload['status']} chars={payload['chars']} "
        f"truncated={payload['truncated']}\n\n"
    )
    return ToolResult(
        content=header + payload["markdown"],
        summary=f"web_fetch({payload['url']}) {payload['chars']} chars",
    )


web_fetch = tool(
    name="web_fetch",
    description=(
        "Fetch an allowlisted public security reference page (OWASP Top 10, CWE Top 25, "
        "CISA) and return clean markdown. Use for current-year taxonomy lists — do not "
        "answer OWASP Top 10 / CWE Top 25 from memory. HTTPS only; host must be allowlisted."
    ),
    input_schema=_INPUT_SCHEMA,
    scope="read",
    parallel=True,
    summary=lambda input: f"web_fetch({(input.get('url') or '?')[:60]})",
)(_web_fetch)
