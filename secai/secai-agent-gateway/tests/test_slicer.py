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
    apply_decision,
    build_decision_prompt,
    cut_code,
    extract_graph,
    imports_local_sibling,
    safe_slice,
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
