"""Tests for the prune-in-place multi-file PoC path (poc/slicer.py:prune_evidence) and the
language sniffer — the "hướng mới" that keeps a fetched PoC's original structure and removes
only the unnecessary parts, never rewriting a byte (contrast poc/consolidate.py's LLM merge)."""
from __future__ import annotations

import asyncio

from secai_gateway.poc.slicer import prune_evidence, sniff_language
from secai_gateway.poc.inject import (
    _CONSOLIDATED_BANNER,
    _EXTRACTED_BANNER,
    _FIXED_BANNER,
    enforce_section1,
    has_real_code_evidence,
)


def _run(coro):
    return asyncio.run(coro)


def _mk(files, **extra):
    ev = {"source": "poc_github", "source_ref": "r", "url": "http://x", "sha256": "sha0",
          "kind": "exploit", "cve_ids": ["CVE-2021-41773"], "source_files": files}
    ev.update(extra)
    return ev


# ── sniff_language ───────────────────────────────────────────────────────────
def test_sniff_txt_python_by_shebang():
    assert sniff_language("#!/usr/bin/env python3\nimport os\n", "42.txt", "txt") == "python"


def test_sniff_txt_php_by_content():
    assert sniff_language("<?php system($_GET['c']); ?>", "x.txt", "txt") == "php"


def test_sniff_ruby_by_content():
    assert sniff_language("require 'socket'\ndef go\n  puts 1\nend\n", "e.txt", "") == "ruby"


def test_sniff_extension_wins_over_declared():
    assert sniff_language("x=1", "e.py", "txt") == "python"


def test_sniff_declared_kept_when_not_ambiguous():
    assert sniff_language("some prose", "notes", "markdown") == "markdown"


def test_sniff_empty_on_unknown():
    assert sniff_language("just words here", "a.txt", "txt") == ""


# ── prune_evidence: file-level keep/drop ─────────────────────────────────────
def test_single_file_returns_none():
    """single-file → caller uses slice_entry/verbatim, prune is a no-op."""
    ev = _mk([{"path": "poc.py", "role": "entry", "language": "python",
               "code": "print('x')\n"}])
    assert _run(prune_evidence(ev, None, "CVE-2021-41773")) is None


def test_keeps_imported_sibling_drops_unrelated():
    ev = _mk([
        {"path": "exploit.py", "role": "entry", "language": "python",
         "code": "from helper import p\ndef main():\n    print(p())\nif __name__=='__main__':\n    main()\n"},
        {"path": "helper.py", "role": "support", "language": "python",
         "code": "def p():\n    return 'x'\n"},
        {"path": "other_cve.py", "role": "support", "language": "python",
         "code": "print('unrelated exploit')\n"},
        {"path": "README.md", "role": "readme", "language": "md", "code": "# run\n"},
        {"path": "requirements.txt", "role": "support", "language": "txt", "code": "requests\n"},
    ])
    res = _run(prune_evidence(ev, None, "CVE-2021-41773"))
    kept = {f["path"] for f in res["source_files"]}
    assert "exploit.py" in kept and "helper.py" in kept           # entry + load-bearing sibling
    assert "README.md" in kept and "requirements.txt" in kept     # readme + manifest kept
    assert "other_cve.py" not in kept                             # unreferenced support dropped
    assert res["static_indicators"]["files_dropped"] == ["other_cve.py"]


def test_string_referenced_data_file_kept():
    """A data file the entry open()s is load-bearing (FileNotFound if dropped)."""
    ev = _mk([
        {"path": "exploit.py", "role": "entry", "language": "python",
         "code": "def main():\n    open('wordlist.txt')\nif __name__=='__main__':\n    main()\n"},
        {"path": "wordlist.txt", "role": "support", "language": "txt", "code": "a\nb\n"},
        {"path": "junk.py", "role": "support", "language": "python", "code": "print('x')\n"},
    ])
    res = _run(prune_evidence(ev, None, "CVE-2021-41773"))
    kept = {f["path"] for f in res["source_files"]}
    assert "wordlist.txt" in kept          # referenced by string literal → kept
    assert "junk.py" not in kept


def test_transitive_closure_kept():
    """entry → helper → helper2: all three survive."""
    ev = _mk([
        {"path": "exploit.py", "role": "entry", "language": "python",
         "code": "import helper\ndef main():\n    helper.go()\nif __name__=='__main__':\n    main()\n"},
        {"path": "helper.py", "role": "support", "language": "python",
         "code": "import helper2\ndef go():\n    helper2.run()\n"},
        {"path": "helper2.py", "role": "support", "language": "python",
         "code": "def run():\n    return 1\n"},
        {"path": "unused.py", "role": "support", "language": "python", "code": "x=1\n"},
    ])
    res = _run(prune_evidence(ev, None, "CVE-2021-41773"))
    kept = {f["path"] for f in res["source_files"]}
    assert {"exploit.py", "helper.py", "helper2.py"} <= kept
    assert "unused.py" not in kept


# ── prune_evidence: intra-file behaviour ─────────────────────────────────────
def test_entry_noise_dropped_but_support_verbatim():
    ev = _mk([
        {"path": "exploit.py", "role": "entry", "language": "python",
         "code": "import helper\ndef banner():\n    print('L33T ART')\n"
                 "def main():\n    helper.go()\nif __name__=='__main__':\n    main()\n"},
        {"path": "helper.py", "role": "support", "language": "python",
         # a noise-named but cross-file-used function must survive (support kept verbatim)
         "code": "def banner():\n    return 'used-by-entry'\ndef go():\n    return banner()\n"},
    ])
    res = _run(prune_evidence(ev, None, "CVE-2021-41773"))
    entry = next(f for f in res["source_files"] if f["role"] == "entry")
    helper = next(f for f in res["source_files"] if f["path"] == "helper.py")
    assert "def banner" not in entry["code"]        # entry's unreferenced noise dropped
    assert "def main" in entry["code"]
    assert "def banner" in helper["code"]            # support file untouched (verbatim)


def test_dynamic_dispatch_entry_kept_whole():
    ev = _mk([
        {"path": "exploit.py", "role": "entry", "language": "python",
         "code": "import importlib\ndef banner():\n    print('art')\n"
                 "def main():\n    importlib.import_module('x')\nif __name__=='__main__':\n    main()\n"},
        {"path": "helper.py", "role": "support", "language": "python", "code": "y=1\n"},
    ])
    res = _run(prune_evidence(ev, None, "CVE-2021-41773"))
    entry = next(f for f in res["source_files"] if f["role"] == "entry")
    # dynamic dispatch present → intra-file prune skipped → banner() NOT dropped
    assert "def banner" in entry["code"]


def test_dep_header_excludes_siblings():
    ev = _mk([
        {"path": "exploit.py", "role": "entry", "language": "python",
         "code": "import requests\nimport helper\ndef main():\n    helper.go(requests)\nif __name__=='__main__':\n    main()\n"},
        {"path": "helper.py", "role": "support", "language": "python", "code": "def go(r):\n    return r\n"},
    ])
    res = _run(prune_evidence(ev, None, "CVE-2021-41773"))
    entry = next(f for f in res["source_files"] if f["role"] == "entry")
    hdr = entry["code"].splitlines()[0]
    assert "pip install" in hdr and "requests" in hdr
    assert "helper" not in hdr                       # sibling not a pip package


# ── prune_evidence: shape + provenance ───────────────────────────────────────
def test_shape_flags():
    ev = _mk([
        {"path": "exploit.py", "role": "entry", "language": "txt",
         "code": "#!/usr/bin/env python3\nimport helper\nif __name__=='__main__':\n    helper.go()\n"},
        {"path": "helper.py", "role": "support", "language": "python", "code": "def go():\n    return 1\n"},
    ])
    res = _run(prune_evidence(ev, None, "CVE-2021-41773"))
    assert res["pruned"] is True and res["consolidated"] is False
    assert res["extracted"] is True
    assert res["source_files"] is not None and res["source_code"] is None
    assert res["language"] == "python"               # sniffed from shebang despite language=txt
    assert res["sha256"] == "sha0"                    # provenance preserved


# ── injection: tier + banner ─────────────────────────────────────────────────
def test_injection_tier_and_banner():
    ev = _mk([
        {"path": "exploit.py", "role": "entry", "language": "python",
         "code": "import helper\nif __name__=='__main__':\n    helper.go()\n"},
        {"path": "helper.py", "role": "support", "language": "python", "code": "def go():\n    return 1\n"},
    ])
    res = _run(prune_evidence(ev, None, "CVE-2021-41773"))
    assert has_real_code_evidence([res])
    text, report = enforce_section1("model wrote text", [res], cve_known=True, cve_id="CVE-2021-41773")
    assert report["section1_tier"] == "real_pruned"
    assert report.get("pruned") is True
    assert _EXTRACTED_BANNER in text
    assert _CONSOLIDATED_BANNER not in text and _FIXED_BANNER not in text
    assert "Các tệp PoC (cục bộ)" in text            # multi-file listing rendered
