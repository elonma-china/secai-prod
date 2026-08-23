"""Curated CVE payload corrections for the Qdrant/Neo4j knowledge base.

NVD/OpenCVE sometimes assign an injection CWE (CWE-77/CWE-89) to a Microsoft
security-feature bypass, drop CWE/product fields on incremental sync, or map a .NET
CPE platform to ``linux_kernel``. These overrides are applied:

* at ingest (``scripts/backfill_cve_from_opencve.py``)
* at query time (``qdrant_search`` display + client-side filters)
* by ``scripts/patch_cve_kb_overrides.py`` against live Qdrant/Neo4j

Idempotent: applying twice yields the same payload.
"""
from __future__ import annotations

import re
from typing import Any

# Replace CWE list; add/remove product tokens; optionally flip KEV.
# Text fields (``CWE Weaknesses`` / ``Affected Products`` / ``KEV``) are rewritten to
# match so BM25/dense chunks stay consistent with the payload facets.
CVE_OVERRIDES: dict[str, dict[str, Any]] = {
    # BitLocker/WinRE "YellowKey" SFB — upstream CWE-77 (command injection) is wrong.
    "CVE-2026-45585": {
        "cwe_refs": ["CWE-693"],
    },
    # .NET DoS — CPE platform "linux" was normalized to the kernel product.
    "CVE-2026-26127": {
        "products_remove": ["linux_kernel"],
    },
    # HTTP.sys HTTP/2 DoS — SKU list only, no component token.
    "CVE-2026-49160": {
        "products_add": ["http.sys"],
    },
    # Incremental NVD sync dropped CWE + products.
    "CVE-2026-62832": {
        "cwe_refs": ["CWE-59"],
        "products_add": ["user_profile_service"],
    },
    "CVE-2026-72971": {
        "cwe_refs": ["CWE-59"],
        "products_add": ["unionfs.sys"],
    },
    "CVE-2026-68820": {
        "cwe_refs": ["CWE-416"],
        "products_add": ["afd.sys"],
        "is_in_kev": True,
    },
}

def _set_labeled_field(text: str, label: str, value: str) -> str:
    """Replace ``Label: …`` up to the next pipe (or EOS), or append the field."""
    pat = re.compile(rf"({re.escape(label)}:\s*)([^|]*)")
    if pat.search(text):
        return pat.sub(lambda m: m.group(1) + value, text, count=1)
    if not value:
        return text
    return (text.rstrip() + f" | {label}: {value}").strip()


def _rewrite_cwe_text(text: str, cwes: list[str]) -> str:
    joined = ", ".join(cwes)
    if "CWE Weaknesses:" in text:
        return _set_labeled_field(text, "CWE Weaknesses", joined)
    if re.search(r"(?<!CWE )Weaknesses:", text):
        return _set_labeled_field(text, "Weaknesses", joined)
    return _set_labeled_field(text, "CWE Weaknesses", joined)


def apply_cve_override(payload: dict[str, Any] | None) -> dict[str, Any]:
    """Return a corrected copy of a Qdrant payload (or the input if no override)."""
    if not payload:
        return payload or {}
    cve = str(payload.get("cve_id") or "").upper()
    ov = CVE_OVERRIDES.get(cve)
    if not ov:
        return payload
    out = dict(payload)
    text = str(out.get("text") or "")

    if ov.get("cwe_refs") is not None:
        cwes = [str(w).upper() for w in ov["cwe_refs"] if w]
        out["cwe_refs"] = cwes
        text = _rewrite_cwe_text(text, cwes)

    products = [str(p) for p in (out.get("products") or []) if p]
    seen = {p.lower() for p in products}
    for p in ov.get("products_remove") or []:
        products = [x for x in products if x.lower() != str(p).lower()]
        seen.discard(str(p).lower())
    for p in ov.get("products_add") or []:
        token = str(p).strip()
        if token and token.lower() not in seen:
            products.append(token)
            seen.add(token.lower())
    if ov.get("products_add") or ov.get("products_remove"):
        out["products"] = products
        text = _set_labeled_field(text, "Affected Products", ", ".join(products))

    if ov.get("is_in_kev") is True:
        out["is_in_kev"] = True
        if re.search(r"In KEV:", text, re.I):
            text = re.sub(r"In KEV:\s*\S+", "In KEV: True", text, count=1, flags=re.I)
        elif not re.search(r"\bKEV:", text):
            text = _set_labeled_field(text, "KEV", "Known Exploited Vulnerability")

    if text != str(out.get("text") or ""):
        out["text"] = text
    return out
