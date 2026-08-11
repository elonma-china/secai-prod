"""Deterministic HTTP surface recon for bughunt Phase 1 (no LLM, no CVE)."""
from __future__ import annotations

import re
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx

# Lightweight path wordlist — enough to fingerprint the local demo + common web apps.
# Lightweight path wordlist — enough to fingerprint the local demo + common web apps.
_DEFAULT_PATHS: tuple[str, ...] = (
    "/",
    "/robots.txt",
    "/sitemap.xml",
    "/login",
    "/admin",
    "/api/health",
    "/api",
    "/.env",
    "/debug/info",
    "/wp-login.php",
    "/.well-known/security.txt",
)

_INTERESTING_HEADERS = (
    "server",
    "x-powered-by",
    "x-app-name",
    "x-generator",
    "via",
    "x-frame-options",
    "content-security-policy",
    "set-cookie",
    "www-authenticate",
)


def normalize_base_url(target: str) -> str:
    """Turn ``host``, ``host:port``, or URL into an absolute http(s) base."""
    raw = (target or "").strip()
    if not raw:
        raise ValueError("empty target")
    if "://" not in raw:
        # Bare host:port → prefer http for local labs; https only when scheme given.
        raw = f"http://{raw}"
    parsed = urlparse(raw)
    if not parsed.hostname:
        raise ValueError(f"invalid target: {target!r}")
    scheme = parsed.scheme or "http"
    netloc = parsed.netloc
    # Drop path/query from user input — recon starts at site root.
    return f"{scheme}://{netloc}".rstrip("/")


def _pick_headers(resp: httpx.Response) -> dict[str, str]:
    out: dict[str, str] = {}
    for key in _INTERESTING_HEADERS:
        val = resp.headers.get(key)
        if val:
            out[key] = val[:500]
    return out


def _tech_hints(headers: dict[str, str], body: str, path: str, status: int) -> list[str]:
    hints: list[str] = []
    powered = (headers.get("x-powered-by") or "").lower()
    server = (headers.get("server") or "").lower()
    app = (headers.get("x-app-name") or "").lower()
    if "php" in powered or "php" in server:
        hints.append("php")
    if "nginx" in server:
        hints.append("nginx")
    if "apache" in server:
        hints.append("apache")
    if "secaidemo" in app or "secai-demo" in server:
        hints.append("secaidemo")
    if "wordpress" in body.lower() or path.endswith("wp-login.php") and status < 500:
        if status in (200, 301, 302, 403):
            hints.append("wordpress?")
    gen = re.search(r'name=["\']generator["\']\s+content=["\']([^"\']+)', body, re.I)
    if gen:
        hints.append(f"generator:{gen.group(1)[:80]}")
    if path == "/login" and status == 200 and "password" in body.lower():
        hints.append("login-form")
    return hints


def _finding(
    *,
    kind: str,
    title: str,
    evidence: str,
    severity: str = "info",
    path: str | None = None,
) -> dict[str, Any]:
    return {
        "kind": kind,
        "title": title,
        "severity": severity,
        "path": path,
        "evidence": evidence[:800],
    }


async def probe_http_surface(
    target: str,
    *,
    paths: tuple[str, ...] | None = None,
    timeout: float = 5.0,
) -> dict[str, Any]:
    """GET common paths; return surface checklist + info findings (fingerprints only)."""
    base = normalize_base_url(target)
    path_list = paths or _DEFAULT_PATHS
    endpoints: list[dict[str, Any]] = []
    findings: list[dict[str, Any]] = []
    tech: set[str] = set()
    errors: list[str] = []

    async with httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=True,
        verify=False,
        headers={"User-Agent": "SecAI-BughuntRecon/0.1"},
    ) as client:
        for path in path_list:
            url = urljoin(base + "/", path.lstrip("/"))
            if path == "/":
                url = base + "/"
            try:
                resp = await client.get(url)
            except httpx.HTTPError as exc:
                errors.append(f"{path}: {type(exc).__name__}: {exc}")
                endpoints.append({
                    "path": path,
                    "url": url,
                    "ok": False,
                    "error": f"{type(exc).__name__}: {exc}",
                })
                continue

            body = ""
            try:
                body = resp.text[:4000]
            except Exception:  # noqa: BLE001
                body = ""
            headers = _pick_headers(resp)
            hints = _tech_hints(headers, body, path, resp.status_code)
            tech.update(hints)
            endpoints.append({
                "path": path,
                "url": str(resp.url),
                "ok": True,
                "status": resp.status_code,
                "headers": headers,
                "body_preview": body[:240].replace("\n", " "),
                "tech_hints": hints,
            })

            if headers.get("server"):
                findings.append(_finding(
                    kind="fingerprint",
                    title=f"Server banner on {path}",
                    evidence=f"Server: {headers['server']}",
                    path=path,
                ))
            if headers.get("x-powered-by"):
                findings.append(_finding(
                    kind="fingerprint",
                    title=f"X-Powered-By on {path}",
                    evidence=headers["x-powered-by"],
                    path=path,
                ))
            if headers.get("x-app-name"):
                findings.append(_finding(
                    kind="fingerprint",
                    title=f"X-App-Name on {path}",
                    evidence=headers["x-app-name"],
                    path=path,
                ))
            if path == "/robots.txt" and resp.status_code == 200 and body.strip():
                findings.append(_finding(
                    kind="disclosure",
                    title="robots.txt present",
                    evidence=body.strip()[:400],
                    severity="low",
                    path=path,
                ))
                for line in body.splitlines():
                    if line.lower().startswith("disallow:"):
                        dis = line.split(":", 1)[-1].strip()
                        if dis and dis != "/":
                            findings.append(_finding(
                                kind="disclosure",
                                title=f"robots Disallow: {dis}",
                                evidence=line.strip(),
                                severity="low",
                                path="/robots.txt",
                            ))
            if path == "/admin" and resp.status_code in (200, 401, 403):
                findings.append(_finding(
                    kind="surface",
                    title=f"/admin responds {resp.status_code}",
                    evidence=f"HTTP {resp.status_code}",
                    severity="low",
                    path=path,
                ))
            if path == "/login" and resp.status_code == 200 and "password" in body.lower():
                findings.append(_finding(
                    kind="surface",
                    title="Login form at /login",
                    evidence="HTML form with password field",
                    severity="info",
                    path=path,
                ))
            if path == "/api/health" and resp.status_code == 200:
                findings.append(_finding(
                    kind="surface",
                    title="Health API exposed",
                    evidence=body[:200],
                    severity="info",
                    path=path,
                ))
            if path == "/.env" and resp.status_code == 200 and any(
                k in body.lower() for k in ("password", "secret", "aws_", "jwt")
            ):
                findings.append(_finding(
                    kind="secrets_exposure",
                    title="Exposed .env with secrets",
                    evidence=body[:300],
                    severity="high",
                    path="/.env",
                ))
            if path == "/debug/info" and resp.status_code == 200 and any(
                k in body.lower() for k in ("secret", "dsn", "jwt", "ssn")
            ):
                findings.append(_finding(
                    kind="debug_info_leak",
                    title="Debug endpoint leaks secrets/PII",
                    evidence=body[:300],
                    severity="high",
                    path="/debug/info",
                ))

    # De-dupe findings by (kind, title, path)
    seen: set[tuple[str, str, str | None]] = set()
    unique: list[dict[str, Any]] = []
    for f in findings:
        key = (f["kind"], f["title"], f.get("path"))
        if key in seen:
            continue
        seen.add(key)
        unique.append(f)

    reachable = any(e.get("ok") for e in endpoints)
    return {
        "base_url": base,
        "reachable": reachable,
        "tech": sorted(tech),
        "endpoints": endpoints,
        "findings": unique,
        "errors": errors[:20],
        "summary": (
            f"Reachable {base}; tech={sorted(tech) or ['unknown']}; "
            f"{len(unique)} findings; {sum(1 for e in endpoints if e.get('ok'))}/{len(endpoints)} paths OK"
            if reachable
            else f"Unreachable {base}; errors={errors[:3]}"
        ),
    }


def hunt_from_surface(surface: dict[str, Any]) -> dict[str, Any]:
    """Phase Hunt stub: turn recon evidence into hypotheses — no invented CVEs."""
    hypotheses: list[dict[str, Any]] = []
    tech = set(surface.get("tech") or [])
    findings = surface.get("findings") or []

    if not surface.get("reachable"):
        return {
            "hypotheses": [],
            "summary": "Hunt skipped — target unreachable in Recon.",
        }

    if any(f.get("kind") == "disclosure" and "robots" in (f.get("title") or "").lower() for f in findings):
        hypotheses.append({
            "vul_class": "information_disclosure",
            "cve": None,
            "confidence": 0.55,
            "evidence": "robots.txt discloses hidden paths",
            "next": "Enumerate Disallow paths; do not claim CVE yet.",
        })
    if "login-form" in tech:
        hypotheses.append({
            "vul_class": "auth_surface",
            "cve": None,
            "confidence": 0.4,
            "evidence": "Login form discovered",
            "next": "Check default creds / lockout / SQLi only with authz — Phase Validate later.",
        })
    if "php" in tech:
        hypotheses.append({
            "vul_class": "stack_php",
            "cve": None,
            "confidence": 0.35,
            "evidence": "PHP fingerprint from headers",
            "next": "Map version/banner to known CVEs in Hunt Phase 2 (nuclei) — not claimed here.",
        })
    if "secaidemo" in tech:
        hypotheses.append({
            "vul_class": "lab_demo_app",
            "cve": None,
            "confidence": 0.5,
            "evidence": "X-App-Name/Server match SecAI demo target",
            "next": "Local demo — run verify_high_signal; not a CVE claim.",
        })

    for f in findings:
        kind = str(f.get("kind") or "")
        sev = str(f.get("severity") or "").lower()
        if kind in {"secrets_exposure", "debug_info_leak"} or sev == "high":
            hypotheses.append({
                "vul_class": kind or "high_finding",
                "cve": None,
                "confidence": 0.85,
                "evidence": str(f.get("evidence") or f.get("title") or "")[:400],
                "path": f.get("path"),
                "next": "Validate with concrete HTTP PoC; do not invent CVE.",
            })

    if not hypotheses:
        hypotheses.append({
            "vul_class": "needs_deeper_scan",
            "cve": None,
            "confidence": 0.2,
            "evidence": surface.get("summary") or "surface only",
            "next": "Run nuclei/tech templates in Phase 2.",
        })

    return {
        "hypotheses": hypotheses,
        "summary": f"{len(hypotheses)} hypothes(es); none claimed as confirmed CVE.",
    }


def validate_hypotheses(surface: dict[str, Any], hunt: dict[str, Any]) -> dict[str, Any]:
    """Deprecated alias — use ``triage.triage_leads`` (BB triage-validation)."""
    from .triage import triage_leads
    return triage_leads(surface, hunt)

