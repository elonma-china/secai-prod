"""Tolerant CVE-ID normalization.

The exploit endpoints are meant to receive a CVE ID (``CVE-YYYY-NNNN``), but callers paste
it with case drift or separator typos — ``cve 2026 42533``, ``CVE2026-42533``,
``cve_2026_42533``. Every downstream CVE-exact lookup keys on the canonical
``CVE-\\d{4}-\\d{3,7}`` shape (catalog's ``poc_cve`` join, prefetch's query split, live-exploit
intent parsing), so a malformed token silently falls through to weaker keyword search and a
real, indexed PoC gets reported as "not found".

``normalize_cve_ids`` repairs those tokens IN PLACE before any search runs, so all three
paths see the canonical form. It only touches spans that already start with the literal
``cve`` token, so a bare ``2026 42533`` in prose is never invented into a CVE id. Idempotent:
an already-canonical id rewrites to itself.
"""
from __future__ import annotations

import re

# "cve" (any case) + 4-digit year + 3-7 digit id, with any run of space/dot/dash/underscore
# (or nothing between "cve" and the year) as separators. A separator between year and id IS
# required, so a fully run-together "CVE202642533" is left alone on purpose — the year/id
# split there is ambiguous. The leading \b + literal "cve" keeps prose numbers untouched.
_MALFORMED_CVE = re.compile(r"\bcve[\s._-]*(\d{4})[\s._-]+(\d{3,7})\b", re.I)


def normalize_cve_ids(text: str) -> str:
    """Rewrite every malformed/mixed-case CVE token in ``text`` to canonical
    ``CVE-YYYY-NNNNN``. Text with no CVE-like token is returned unchanged."""
    if not text:
        return text
    return _MALFORMED_CVE.sub(lambda m: f"CVE-{m.group(1)}-{m.group(2)}", text)
