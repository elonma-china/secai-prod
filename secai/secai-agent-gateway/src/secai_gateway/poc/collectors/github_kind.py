"""Classify GitHub-sourced catalog rows as exploit vs advisory.

``poc_github`` / trickest collectors used to mark every repo ``kind=exploit``. Research
writeups and hardening/remediation repos then ranked above real PoCs and fed the
synthesize-from-README path — which the exploit prompt illustrates with Python
``requests``, so a Microsoft Office OLE bypass (CWE-807) was presented as SQLi.
"""
from __future__ import annotations

import re

# Title/description/repo-name hints that the row is analysis or mitigation, not a PoC.
_ADVISORY_RE = re.compile(
    r"research(?:[_\s-]?writeup)?|defensive analysis|no working exploit|"
    r"no functional exploit|no poc is published|hardening|remediation|"
    r"mitigation guidance|detection guidance|\bwriteup\b",
    re.I,
)


def classify_github_poc_kind(title: str = "", description: str = "", repo: str = "") -> str:
    """Return ``advisory`` for writeup/hardening repos, else ``exploit``."""
    blob = " ".join(part for part in (title, description, repo.replace("/", " ")) if part)
    if blob and _ADVISORY_RE.search(blob):
        return "advisory"
    return "exploit"
