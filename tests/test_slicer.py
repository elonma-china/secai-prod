"""poc/slicer.py — deterministic core extraction. No network; hand-built Python source.

The safety contract under test:
  * output is ALWAYS a verbatim subset of the input (never re-synthesised),
  * clearly-noise unreferenced nodes (banner/author/color vars + bare banner calls) are dropped,
  * referenced nodes (a banner fn CALLED from main) are KEPT (whole-node drop only),
  * imports + shebang/encoding + ``__main__`` guard always survive,
  * a slice that would break (dropped import, undefined name) fails → None (caller fallback).
"""
from __future__ import annotations

from secai_gateway.poc.slicer import (
    _dependency_header,
    apply_decision,
    build_decision_prompt,
    cut_code,
    extract_graph,
    imports_local_sibling,
    safe_slice,
    slice_entry,
    validate_slice,
)


def test_extract_graph_nodes_and_refs() -> None:
    code = (
        "import socket\n"
        "\n"
        "COLORS = {'red': 1}\n"
        "AUTHOR = 'me'\n"
        "\n"
        "def banner():\n"
        "    print('art')\n"
        "\n"
        "def exploit(t):\n"
        "    banner()\n"
        "    return COLORS['red']\n"
    )
    g = extract_graph(code)
    nodes = {n["id"]: n for n in g["nodes"]}
    assert "fn_exploit" in nodes and "fn_banner" in nodes
    assert "var_COLORS" in nodes and "var_AUTHOR" in nodes
    # exploit references banner + COLORS
    assert set(nodes["fn_exploit"]["refs"]) == {"fn_banner", "var_COLORS"}
    assert nodes["fn_banner"]["referenced_by"] == ["fn_exploit"]
    # import is always-keep, not a node
    assert any(r == [1, 1] for r in g["always_keep"])


def test_safe_slice_drops_noise_keeps_logic() -> None:
    code = (
        "#!/usr/bin/env python3\n"
        "# coding=utf-8\n"
        "# Author: someone\n"
        "import socket\n"
        "\n"
        "COLORS = {'red': 1}\n"
        "AUTHOR = 'someone'\n"
        "\n"
        "def build_payload(host):\n"
        "    return 'PAYLOAD ' + host\n"
        "\n"
        "def exploit(target):\n"
        "    p = build_payload(target)\n"
        "    return p\n"
        "\n"
        "if __name__ == '__main__':\n"
        "    exploit('127.0.0.1')\n"
    )
    sliced = safe_slice(code, "python")
    assert sliced is not None
    # noise dropped
    assert "COLORS" not in sliced
    assert "AUTHOR" not in sliced
    assert "# Author" not in sliced
    # logic kept
    assert "def build_payload" in sliced
    assert "def exploit" in sliced
    assert "PAYLOAD" in sliced
    # shebang + import + main guard kept
    assert sliced.startswith("#!/usr/bin/env python3")
    assert "import socket" in sliced
    assert "if __name__ == '__main__':" in sliced
    # still valid python
    assert validate_slice(sliced, "python") is None


def test_referenced_banner_fn_is_kept() -> None:
    # banner() is CALLED from exploit → it is referenced → whole-node drop only keeps it.
    code = (
        "def banner():\n"
        "    print('art')\n"
        "\n"
        "def exploit(t):\n"
        "    banner()\n"
        "    return t\n"
    )
    sliced = safe_slice(code, "python")
    assert sliced is not None
    assert "def banner" in sliced  # referenced → kept (intra-function call can't be dropped)
    assert validate_slice(sliced, "python") is None


def test_verbatim_subset_invariant() -> None:
    code = (
        "import os\n"
        "NOISE = 'x'\n"
        "\n"
        "def run():\n"
        "    return os.getcwd()\n"
    )
    sliced = safe_slice(code, "python")
    assert sliced is not None
    # every non-blank output line must appear verbatim in the input
    for line in sliced.splitlines():
        if line.strip():
            assert line in code.splitlines()


def test_safe_slice_falls_back_none_on_broken_code() -> None:
    # Syntax error → extract_graph yields no nodes → safe_slice returns None (verbatim fallback).
    assert safe_slice("def (broken", "python") is None
    assert safe_slice("", "python") is None


def test_apply_decision_guards_referenced_nodes() -> None:
    code = (
        "def banner():\n"
        "    print('art')\n"
        "\n"
        "def exploit(t):\n"
        "    banner()\n"
        "    return t\n"
    )
    g = extract_graph(code)
    ids = {n["id"] for n in g["nodes"]}
    # LLM asks to drop banner (referenced) and a bogus id — both guarded.
    keep = apply_decision(g, {"keep": ["fn_exploit"], "drop": ["fn_banner", "nope"]})
    assert keep == ids - set()  # banner is referenced → kept
    assert "fn_banner" in keep and "fn_exploit" in keep


def test_decision_prompt_contains_no_source_code() -> None:
    src = (
        "SECRET = 'real-payload-123'\n"
        "def banner():\n"
        "    print('art')\n"
        "def exploit():\n"
        "    return SECRET\n"
    )
    g = extract_graph(src)
    prompt = build_decision_prompt(g, "CVE-2021-44228")
    # the prompt must never carry the real payload bytes — only names/summaries
    assert "real-payload-123" not in prompt
    assert "SECRET" in prompt  # the NAME is fine, the VALUE is not
    assert "exploit" in prompt
    assert "CVE-2021-44228" in prompt


def test_imports_local_sibling_detection() -> None:
    assert imports_local_sibling("from utils.menu_functions import run\n", {"utils", "requirements"})
    assert imports_local_sibling("import utils\n", {"utils"})
    assert imports_local_sibling("from . import version\n", {"x"})  # relative always local
    assert not imports_local_sibling("import requests\n", {"x", "y"})
    assert not imports_local_sibling("from Crypto.Cipher import AES\n", {"x"})


def test_imports_local_sibling_detects_string_literal_refs() -> None:
    """A sibling referenced by path (subprocess/open), not import, must ALSO force the
    cross-file bail-out — same missing-file-dependency gap as consolidate.py's
    _sibling_string_refs, exercised here through slicer's own entry point."""
    sibs = {"listener", "payload"}
    assert imports_local_sibling('subprocess.run(["python3", "listener.py"])\n', sibs)
    assert imports_local_sibling('open("payload.json")\n', sibs)
    assert not imports_local_sibling('open("unrelated.txt")\n', sibs)


def test_main_guard_only_call_is_referenced() -> None:
    """Regression guard for the dead-code-misclassification gap (2026-08 research pass):
    extract_graph used to build refs ONLY from `func`-type node bodies, never scanning the
    `if __name__=="__main__":` guard itself — so an entry function called ONLY from that
    guard showed an EMPTY referenced_by and looked droppable to decide_keep_drop, even though
    the guard (always kept verbatim) still calls it. Dropping just the definition then leaves
    a call to an undefined name (caught downstream by validate_slice's undefined-name check),
    but the real danger is the sibling case below where NOTHING catches it."""
    code = (
        "def exploit(target):\n"
        "    return f'pwned {target}'\n"
        "\n"
        "if __name__ == '__main__':\n"
        "    exploit('10.0.0.1')\n"
    )
    g = extract_graph(code)
    fn = next(n for n in g["nodes"] if n["id"] == "fn_exploit")
    assert fn["referenced_by"], "call from the __main__ guard must produce a referenced_by edge"
    # decide_keep_drop's apply_decision must now refuse to drop it even if asked to.
    keep = apply_decision(g, {"keep": [], "drop": ["fn_exploit"]})
    assert "fn_exploit" in keep


def test_bare_toplevel_call_is_referenced() -> None:
    """Same gap, worse sub-case: an entry point invoked as a BARE top-level statement (no
    __main__ guard at all — common in short PoC scripts) is itself a `stmt`-type node whose
    OWN body was never scanned for refs either. Before the fix, dropping the `stmt` node AND
    the function definition as one LLM decision left a file that compiles clean, has no
    undefined name, and silently does nothing — undetectable by any existing check."""
    code = (
        "def exploit(target):\n"
        "    return f'pwned {target}'\n"
        "\n"
        "exploit('10.0.0.1')\n"
    )
    g = extract_graph(code)
    fn = next(n for n in g["nodes"] if n["id"] == "fn_exploit")
    assert fn["referenced_by"], "a bare top-level call must produce a referenced_by edge"
    keep = apply_decision(g, {"keep": [], "drop": ["fn_exploit"]})
    assert "fn_exploit" in keep


def test_var_referencing_another_var_is_tracked() -> None:
    """A var initializer reading another var/name is also a top-level-only reference the old
    code missed (var/stmt node bodies were never scanned, only func bodies)."""
    code = (
        "HOST = '10.0.0.1'\n"
        "TARGET_URL = f'http://{HOST}/exploit'\n"
    )
    g = extract_graph(code)
    host = next(n for n in g["nodes"] if n["id"] == "var_HOST")
    assert host["referenced_by"]


def test_dependency_header_flags_third_party_only() -> None:
    """Regression guard for the environment-dependency-mismatch gap (2026-08 research pass):
    slice_entry's deterministic path never surfaced a required `pip install` the way
    consolidate.md's rules already do for the LLM-merge path. Stdlib imports must be silent
    (no false-positive noise); a third-party import must be named."""
    code = "import os\nimport requests\nimport socket\n\ndef run():\n    return requests.get('x')\n"
    header = _dependency_header(code)
    assert "requests" in header
    assert "os" not in header.split("pip install")[1]  # stdlib not flagged
    assert "socket" not in header.split("pip install")[1]
    assert _dependency_header("import os\n") == ""  # stdlib-only → no header at all
    assert _dependency_header("from . import x\n") == ""  # relative import → not a pip package


async def test_slice_entry_prepends_dependency_header() -> None:
    ev = {
        "source": "exploitdb", "kind": "exploit", "url": "http://x/y",
        "sha256": "a" * 64, "cve_ids": ["CVE-2021-44228"],
        "language": "python",
        "source_code": (
            "import requests\n"
            "\n"
            "def exploit(target):\n"
            "    return requests.get(target)\n"
            "\n"
            "if __name__ == '__main__':\n"
            "    exploit('http://10.0.0.1')\n"
        ),
    }
    out = await slice_entry(ev, runner=None, cve_id="CVE-2021-44228", use_llm=False)
    assert out is not None
    assert "pip install requests" in out["source_code"]
    # add-only: every original line must still be present verbatim somewhere in the output
    for line in ev["source_code"].splitlines():
        assert line in out["source_code"]


async def test_slice_entry_no_header_when_stdlib_only() -> None:
    ev = {
        "source": "exploitdb", "kind": "exploit", "url": "http://x/y",
        "sha256": "b" * 64, "cve_ids": ["CVE-2021-44228"],
        "language": "python",
        "source_code": (
            "import socket\n"
            "\n"
            "def exploit(target):\n"
            "    return socket.gethostbyname(target)\n"
            "\n"
            "if __name__ == '__main__':\n"
            "    exploit('10.0.0.1')\n"
        ),
    }
    out = await slice_entry(ev, runner=None, cve_id="CVE-2021-44228", use_llm=False)
    assert out is not None
    assert "pip install" not in out["source_code"]
