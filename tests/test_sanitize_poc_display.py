"""Display-time PoC banner cleanup — strip floral noise, keep usage/docstring/argparse."""
from __future__ import annotations

from secai_gateway.poc.inject import enforce_reference_code, sanitize_poc_display

_NOISY = '''\
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ============================================================
# Author: somebody
# Twitter: @hax
# DISCLAIMER: for educational purposes only
# ============================================================
"""Usage: python3 cve-x.py -u TARGET

Requires network access to the vulnerable host.
"""
import argparse

# probe the login endpoint before sending the payload
parser = argparse.ArgumentParser(description="demo sqli")
parser.add_argument("-u", "--url", required=True, help="target base URL")
args = parser.parse_args()
print(args.url)
'''

_CLEAN = '''\
#!/usr/bin/env python3
import argparse
from requests_ntlm import HttpNtlmAuth

def parse_args():
    p = argparse.ArgumentParser(description="exchange ssrf via file read")
    p.add_argument("--attacker-ip", required=True, help="attacker ip ssrf callback")
    return p.parse_args()
'''


def test_strips_banner_author_twitter_disclaimer() -> None:
    out = sanitize_poc_display(_NOISY)
    assert "=====" not in out
    assert "Author:" not in out
    assert "Twitter:" not in out
    assert "DISCLAIMER" not in out
    # kept: shebang, encoding, docstring usage, technical comment, argparse
    assert out.startswith("#!/usr/bin/env python3")
    assert "# -*- coding: utf-8 -*-" in out
    assert '"""Usage: python3 cve-x.py -u TARGET' in out
    assert "Requires network access" in out
    assert "# probe the login endpoint" in out
    assert "argparse.ArgumentParser" in out
    assert 'help="target base URL"' in out


def test_clean_hawktrace_style_unchanged() -> None:
    assert sanitize_poc_display(_CLEAN) == _CLEAN


def test_idempotent() -> None:
    once = sanitize_poc_display(_NOISY)
    assert sanitize_poc_display(once) == once


def test_strips_c_style_and_sql_asm_comment_prefixes() -> None:
    src = "\n".join([
        "// ============================================================",
        "// Author: hax",
        "/* ********************************************************** */",  # not a pure ruler line — keep if doesn't match
        "; --------------------------------------------------------",
        "-- ========== TITLE ==========",
        "' Author: vb-dude",
        "REM DISCLAIMER: demo only",
        "int main() { return 0; }",
        "",
    ])
    out = sanitize_poc_display(src)
    assert "// Author:" not in out
    assert "Author: hax" not in out
    assert "Author: vb-dude" not in out
    assert "DISCLAIMER" not in out
    assert "=====" not in out
    assert "----" not in out or "-----" not in out  # ruler gone
    assert "int main()" in out


def test_keeps_short_technical_comments_all_styles() -> None:
    src = (
        "# probe login before payload\n"
        "// retry once on 503\n"
        "; load shellcode stub\n"
        "-- filter active sessions\n"
        "x = 1\n"
    )
    assert sanitize_poc_display(src) == src


def test_empty_safe() -> None:
    assert sanitize_poc_display("") == ""


def test_inject_section1_shows_cleaned_code_keeps_sha() -> None:
    """Mục 1 is cleaned for display; evidence sha256 (provenance) stays the original."""
    sha = "a" * 64
    ev = [{
        "source": "exploitdb", "kind": "exploit", "source_ref": "1", "sha256": sha,
        "source_code": _NOISY,
        "static_indicators": {"language": "py"},
    }]
    out, report = enforce_reference_code("## 2. Cách chạy PoC\n\n- x\n", ev)
    assert report["reference_code_injected"] is True
    assert report["sha256"] == [sha]
    assert "Author:" not in out
    assert "=====" not in out
    assert "argparse.ArgumentParser" in out
    assert '"""Usage: python3 cve-x.py -u TARGET' in out
