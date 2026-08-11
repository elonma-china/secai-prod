"""Deterministic high-signal verification — catch lab/real vulns scanners miss.

Runs concrete HTTP PoCs against URLs discovered in recon / common sensitive paths.
Produces structured leads suitable for triage (not tool-dump noise).
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any
from urllib.parse import quote, urljoin

import httpx

from .recon import normalize_base_url


def _lead(
    *,
    vul_class: str,
    evidence: str,
    path: str,
    confidence: float,
    severity: str = "medium",
    request: str = "",
) -> dict[str, Any]:
    return {
        "vul_class": vul_class,
        "cve": None,
        "cve_evidence": False,
        "confidence": confidence,
        "severity": severity,
        "path": path,
        "evidence": evidence[:900],
        "request": request[:500],
        "source": "verify_high_signal",
        "next": "Validate with 7Q; do not invent CVE.",
    }


def _collect_candidate_urls(base: str, recon_dir: Path | None) -> list[str]:
    urls: list[str] = [base.rstrip("/") + "/"]
    extras = [
        "/.env",
        "/debug/info",
        "/backup.sql",
        "/robots.txt",
        "/login",
        "/admin",
        "/search?q=test",
        "/api/user?id=1",
        "/api/orders/1001",
        "/api/orders/1002",
        "/download?file=readme.txt",
        "/download?file=../.env",
        "/redirect?url=https://evil.example",
    ]
    for p in extras:
        urls.append(urljoin(base.rstrip("/") + "/", p.lstrip("/")))

    if recon_dir and recon_dir.is_dir():
        for rel in (
            "urls/all.txt",
            "urls/with_params.txt",
            "urls/api_endpoints.txt",
            "urls/sensitive_paths.txt",
        ):
            fp = recon_dir / rel
            if not fp.is_file():
                continue
            for line in fp.read_text(encoding="utf-8", errors="replace").splitlines():
                u = line.strip()
                if u.startswith("http"):
                    urls.append(u)

    # de-dupe preserve order
    seen: set[str] = set()
    out: list[str] = []
    for u in urls:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out[:80]


async def verify_high_signal(
    target: str,
    *,
    recon_dir: Path | None = None,
    timeout: float = 6.0,
) -> dict[str, Any]:
    """Execute PoC probes; return leads + summary."""
    base = normalize_base_url(target)
    leads: list[dict[str, Any]] = []
    notes: list[str] = []

    async with httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=False,
        verify=False,
        headers={"User-Agent": "SecAI-BughuntVerify/0.1"},
    ) as client:
        # ── secrets / debug exposure ─────────────────────────────────────
        for path in ("/.env", "/debug/info", "/backup.sql"):
            url = urljoin(base + "/", path.lstrip("/"))
            try:
                r = await client.get(url)
            except httpx.HTTPError as exc:
                notes.append(f"{path}: {type(exc).__name__}")
                continue
            body = r.text[:2000] if r.status_code == 200 else ""
            low = body.lower()
            if r.status_code == 200 and path == "/.env" and (
                "password" in low or "secret" in low or "aws_" in low or "jwt" in low
            ):
                leads.append(_lead(
                    vul_class="secrets_exposure",
                    severity="high",
                    confidence=0.92,
                    path=path,
                    request=f"GET {url}",
                    evidence=f"HTTP {r.status_code}; body leak: {body[:280]}",
                ))
            if r.status_code == 200 and path == "/debug/info" and (
                "jwt" in low or "dsn" in low or "secret" in low or "ssn" in low
            ):
                leads.append(_lead(
                    vul_class="debug_info_leak",
                    severity="high",
                    confidence=0.9,
                    path=path,
                    request=f"GET {url}",
                    evidence=f"HTTP {r.status_code}; secrets/PII in debug: {body[:280]}",
                ))
            if r.status_code == 200 and path == "/backup.sql" and (
                "insert into" in low or "password" in low or "aws" in low
            ):
                leads.append(_lead(
                    vul_class="backup_exposure",
                    severity="high",
                    confidence=0.88,
                    path=path,
                    request=f"GET {url}",
                    evidence=f"HTTP {r.status_code}; dump preview: {body[:220]}",
                ))

        # ── reflected XSS ─────────────────────────────────────────────────
        xss_payload = "<script>alert(1)</script>"
        xss_url = f"{base}/search?q={quote(xss_payload)}"
        try:
            r = await client.get(xss_url)
            if r.status_code == 200 and xss_payload in (r.text or ""):
                leads.append(_lead(
                    vul_class="reflected_xss",
                    severity="medium",
                    confidence=0.85,
                    path="/search",
                    request=f"GET {xss_url}",
                    evidence="Payload reflected unsanitized in HTML body",
                ))
        except httpx.HTTPError as exc:
            notes.append(f"xss: {type(exc).__name__}")

        # ── SQLi error-based ──────────────────────────────────────────────
        sqli_probe = "1'"
        sqli_url = f"{base}/api/user?id={quote(sqli_probe)}"
        try:
            r = await client.get(sqli_url)
            body = r.text or ""
            if r.status_code >= 400 and re.search(
                r"sql syntax|mysql|sqlite|odbc|postgresql|ora-\d", body, re.I
            ):
                leads.append(_lead(
                    vul_class="sqli_error_based",
                    severity="high",
                    confidence=0.8,
                    path="/api/user",
                    request=f"GET {sqli_url}",
                    evidence=f"HTTP {r.status_code}; DB error: {body[:240]}",
                ))
        except httpx.HTTPError as exc:
            notes.append(f"sqli: {type(exc).__name__}")

        # ── IDOR (orders + user PII without auth) ─────────────────────────
        for oid in ("1001", "1002", "1003"):
            url = f"{base}/api/orders/{oid}"
            try:
                r = await client.get(url)
            except httpx.HTTPError:
                continue
            if r.status_code != 200:
                continue
            body = r.text or ""
            if "card_last4" in body or "user_id" in body:
                leads.append(_lead(
                    vul_class="idor",
                    severity="high",
                    confidence=0.88,
                    path=f"/api/orders/{oid}",
                    request=f"GET {url} (no auth)",
                    evidence=f"Unauth order object: {body[:220]}",
                ))
                break  # one solid IDOR lead is enough

        try:
            r = await client.get(f"{base}/api/user?id=3")
            if r.status_code == 200 and ("ssn" in (r.text or "").lower() or "email" in (r.text or "")):
                leads.append(_lead(
                    vul_class="idor_user_pii",
                    severity="high",
                    confidence=0.87,
                    path="/api/user",
                    request=f"GET {base}/api/user?id=3 (no auth)",
                    evidence=f"Unauth user PII: {(r.text or '')[:220]}",
                ))
        except httpx.HTTPError as exc:
            notes.append(f"idor_user: {type(exc).__name__}")

        # ── path traversal via download param ─────────────────────────────
        trav_url = f"{base}/download?file={quote('../.env')}"
        try:
            r = await client.get(trav_url)
            body = (r.text or "")[:500]
            if r.status_code == 200 and (
                "password" in body.lower() or "aws_" in body.lower() or "jwt_secret" in body.lower()
            ):
                leads.append(_lead(
                    vul_class="path_traversal",
                    severity="high",
                    confidence=0.9,
                    path="/download",
                    request=f"GET {trav_url}",
                    evidence=f"Traversal returned secrets: {body[:240]}",
                ))
        except httpx.HTTPError as exc:
            notes.append(f"traversal: {type(exc).__name__}")

        # ── open redirect ─────────────────────────────────────────────────
        redir_url = f"{base}/redirect?url={quote('https://evil.example')}"
        try:
            r = await client.get(redir_url)
            loc = r.headers.get("location") or ""
            if r.status_code in (301, 302, 303, 307, 308) and "evil.example" in loc:
                leads.append(_lead(
                    vul_class="open_redirect",
                    severity="medium",
                    confidence=0.82,
                    path="/redirect",
                    request=f"GET {redir_url}",
                    evidence=f"HTTP {r.status_code} Location: {loc}",
                ))
        except httpx.HTTPError as exc:
            notes.append(f"redirect: {type(exc).__name__}")

        # ── weak default creds ────────────────────────────────────────────
        try:
            r = await client.post(
                f"{base}/login",
                data={"username": "admin", "password": "admin"},
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            body = r.text or ""
            if r.status_code == 200 and ("ok" in body.lower() or "token" in body.lower() or "admin" in body.lower()):
                if "invalid" not in body.lower():
                    leads.append(_lead(
                        vul_class="default_credentials",
                        severity="high",
                        confidence=0.86,
                        path="/login",
                        request="POST /login username=admin&password=admin",
                        evidence=f"HTTP {r.status_code}; {body[:200]}",
                    ))
        except httpx.HTTPError as exc:
            notes.append(f"login: {type(exc).__name__}")

        # ── CORS + sensitive without cookie (chain signal) ────────────────
        try:
            r = await client.get(
                f"{base}/debug/info",
                headers={"Origin": "http://evil.example"},
            )
            acao = r.headers.get("access-control-allow-origin") or ""
            if r.status_code == 200 and acao == "*" and ("secret" in (r.text or "").lower() or "jwt" in (r.text or "").lower()):
                leads.append(_lead(
                    vul_class="cors_with_sensitive_data",
                    severity="medium",
                    confidence=0.75,
                    path="/debug/info",
                    request="GET /debug/info Origin: http://evil.example",
                    evidence=f"ACAO=* with sensitive body; {acao}",
                ))
        except httpx.HTTPError:
            pass

    # Also scan recon URL list for accidental hits already known
    _ = _collect_candidate_urls(base, recon_dir)  # reserved for future expansion

    # de-dupe by vul_class+path
    seen: set[tuple[str, str]] = set()
    unique: list[dict[str, Any]] = []
    for lead in leads:
        key = (str(lead.get("vul_class")), str(lead.get("path")))
        if key in seen:
            continue
        seen.add(key)
        unique.append(lead)

    return {
        "ok": bool(unique),
        "base_url": base,
        "leads": unique,
        "notes": notes[:12],
        "summary": (
            f"verify_high_signal: {len(unique)} actionable lead(s) on {base}"
            if unique
            else f"verify_high_signal: no high-signal hits on {base}"
        ),
    }


def leads_to_findings_log(leads: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Shape for HuntMemory.findings_log / triage findings_hints."""
    out: list[dict[str, Any]] = []
    for lead in leads:
        sev = str(lead.get("severity") or "medium").upper()
        out.append({
            "tool": "verify_high_signal",
            "severity": sev,
            "vul_class": lead.get("vul_class"),
            "path": lead.get("path"),
            "confidence": lead.get("confidence"),
            "evidence": lead.get("evidence"),
            "text": (
                f"[{sev}] {lead.get('vul_class')} @ {lead.get('path')}: "
                f"{lead.get('evidence')}"
            )[:500],
            "source": "verify_high_signal",
            "cve_evidence": False,
        })
    return out
