"""poc/fidelity.py — deterministic semantic-fidelity checks on the consolidation output.

Reproduces the live CVE-2026-45504 bug: the merge renamed ``--attacker-ip``/``--attacker-port``
to ``--callback-url`` and hardcoded the callback bind port to 8780. ``validation_failure``
(compile/name/import) can't see these — the code still runs — so fidelity is checked against
the original entry source. No network, no LLM.
"""
from __future__ import annotations

from secai_gateway.poc.fidelity import cli_surface, fidelity_check, fidelity_failure

_ORIG = """\
import argparse
from http.server import HTTPServer

def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--attacker-ip", required=True, help="attacker ip ssrf callback")
    p.add_argument("--attacker-port", type=int, default=8780, help="attacker port")
    p.add_argument("--creds", required=True, metavar="[DOMAIN\\]USER")
    p.add_argument("--password", required=True)
    p.add_argument("--target-file", default="C:/windows/win.ini")
    p.add_argument("--target", default="https://192.168.2.145")
    args = p.parse_args()
    if "\\\\" in args.creds:
        args.domain, args.user = args.creds.split("\\\\", 1)
    else:
        args.domain, args.user = "", args.creds
    return args

ARGS = parse_args()
ATTACKER_IP = ARGS.attacker_ip
ATTACKER_PORT = ARGS.attacker_port
srv = HTTPServer(("0.0.0.0", ATTACKER_PORT), None)
attach(f"http://{ATTACKER_IP}:{ATTACKER_PORT}")
"""

_MERGED_BAD = """\
import argparse
from http.server import HTTPServer

def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--callback-url", required=True, help="Callback base URL")
    p.add_argument("--creds", required=True, metavar="[DOMAIN\\]USER")
    p.add_argument("--password", required=True)
    p.add_argument("--target-file", default="C:/windows/win.ini")
    p.add_argument("--target", required=True)
    args = p.parse_args()
    if "\\\\" in args.creds:
        args.domain, args.user = args.creds.split("\\\\", 1)
    else:
        args.domain, args.user = "", args.creds
    return args

ARGS = parse_args()
CALLBACK_URL = ARGS.callback_url.rstrip('/')
srv = HTTPServer(("0.0.0.0", 8780), None)
attach(CALLBACK_URL)
"""

_MERGED_FAITHFUL = """\
import argparse
from http.server import HTTPServer

def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--attacker-ip", required=True, help="attacker ip ssrf callback")
    p.add_argument("--attacker-port", type=int, default=8780, help="attacker port")
    p.add_argument("--creds", required=True)
    p.add_argument("--password", required=True)
    p.add_argument("--target-file", default="C:/windows/win.ini")
    p.add_argument("--target", default="https://192.168.2.145")
    args = p.parse_args()
    if "\\\\" in args.creds:
        args.domain, args.user = args.creds.split("\\\\", 1)
    else:
        args.domain, args.user = "", args.creds
    return args

ARGS = parse_args()
ATTACKER_IP = ARGS.attacker_ip
ATTACKER_PORT = ARGS.attacker_port
srv = HTTPServer(("0.0.0.0", ATTACKER_PORT), None)
attach(f"http://{ATTACKER_IP}:{ATTACKER_PORT}")
"""


def test_detects_renamed_cli_flags() -> None:
    kinds = {f.kind for f in fidelity_check(_MERGED_BAD, _ORIG)}
    assert "missing_flag" in kinds
    assert "--attacker-ip" in fidelity_failure(_MERGED_BAD, _ORIG)
    assert "--attacker-port" in fidelity_failure(_MERGED_BAD, _ORIG)


def test_detects_hardcoded_bind_port() -> None:
    kinds = {f.kind for f in fidelity_check(_MERGED_BAD, _ORIG)}
    assert "hardcoded_bind" in kinds
    assert "8780" in fidelity_failure(_MERGED_BAD, _ORIG)


def test_faithful_merge_is_clean() -> None:
    assert fidelity_check(_MERGED_FAITHFUL, _ORIG) == []
    assert fidelity_failure(_MERGED_FAITHFUL, _ORIG) is None


def test_derived_args_attrs_are_not_orphans() -> None:
    # args.domain/args.user are ASSIGNED in parse_args (derived from --creds) — not flags,
    # so a faithful merge that still derives them must not be flagged as orphan_args.
    assert not any(f.kind == "orphan_args" for f in fidelity_check(_MERGED_FAITHFUL, _ORIG))


def test_orphan_args_detected_when_flag_dropped_but_referenced() -> None:
    # drop the --attacker-port add_argument but keep `ARGS.attacker_port` referenced →
    # the surviving reference is now an orphan (AttributeError at runtime).
    import re
    merged = re.sub(r'\n\s*p\.add_argument\("--attacker-port".*', "", _MERGED_FAITHFUL, count=1)
    assert "--attacker-port" not in merged
    kinds = {f.kind for f in fidelity_check(merged, _ORIG)}
    assert "orphan_args" in kinds


def test_empty_input_no_finding() -> None:
    assert fidelity_check("", "") == []
    assert fidelity_failure("", "") is None


# ── cli_surface: deterministic argparse surface for Mục 2 grounding ─────────
def test_cli_surface_extracts_subcommands_flags_positionals() -> None:
    code = """\
import argparse
p = argparse.ArgumentParser()
sub = p.add_subparsers(dest="command", required=True)
check = sub.add_parser("check")
check.add_argument("url")
check.add_argument("--sleep", type=float, default=3.0)
shell = sub.add_parser("shell")
shell.add_argument("--cmd")
shell.add_argument("-i", "--interactive", action="store_true")
"""
    surf = cli_surface(code)
    assert surf["subcommands"] == ["check", "shell"]
    assert "--sleep" in surf["flags"] and "--cmd" in surf["flags"]
    assert "--interactive" in surf["flags"]  # long form captured even with a short alias
    assert surf["positionals"] == ["url"]


def test_cli_surface_empty_on_non_python_or_no_cli() -> None:
    assert cli_surface("") == {}
    assert cli_surface("def f(:\n  pass\n") == {}  # SyntaxError → no surface
    assert cli_surface("print('hello')\n") == {
        "subcommands": [], "flags": [], "positionals": [],
    }


def test_detects_hardcoded_string_ip_in_bind_positional() -> None:
    """Regression guard (2026-08 research pass): the original _hardcoded_binds only checked
    `isinstance(elt.value, int)` — a hardcoded IP STRING in the same bind-tuple position
    (e.g. HTTPServer(("172.17.0.1", ATTACKER_PORT), ...)) was completely invisible. Both
    tuple positions derive from args in this fixture so ONLY the host substitution is under
    test (the pre-existing int-literal check would independently flag a hardcoded port too)."""
    orig = (
        "import argparse\nfrom http.server import HTTPServer\n\n"
        "p = argparse.ArgumentParser()\n"
        "p.add_argument('--bind-ip', required=True)\np.add_argument('--bind-port', type=int, required=True)\n"
        "args = p.parse_args()\n"
        "srv = HTTPServer((args.bind_ip, args.bind_port), None)\n"
    )
    merged = (
        "import argparse\nfrom http.server import HTTPServer\n\n"
        "p = argparse.ArgumentParser()\n"
        "p.add_argument('--bind-ip', required=True)\np.add_argument('--bind-port', type=int, required=True)\n"
        "args = p.parse_args()\n"
        "srv = HTTPServer(('172.17.0.1', args.bind_port), None)\n"  # host silently hardcoded
    )
    reason = fidelity_failure(merged, orig)
    assert reason is not None and "172.17.0.1" in reason
    # a benign wildcard bind must NOT be flagged
    merged_ok = merged.replace("172.17.0.1", "0.0.0.0")
    assert fidelity_failure(merged_ok, orig) is None


def test_detects_hardcoded_ip_via_argparse_default() -> None:
    """The CVE-2026-42533 class of bug: a lab-specific callback IP reintroduced through an
    argparse `default=`, not a bind()/listen() positional literal — invisible to shape (1)
    entirely, since it never inspects add_argument's keyword arguments."""
    orig = (
        "import argparse\n\np = argparse.ArgumentParser()\n"
        "p.add_argument('--callback-ip', required=True)\n"
        "args = p.parse_args()\nprint(args.callback_ip)\n"
    )
    merged = (
        "import argparse\n\np = argparse.ArgumentParser()\n"
        "p.add_argument('--callback-ip', default='172.17.0.1')\n"  # lab IP leaked as a default
        "args = p.parse_args()\nprint(args.callback_ip)\n"
    )
    reason = fidelity_failure(merged, orig)
    assert reason is not None
    assert "172.17.0.1" in reason and "--callback-ip" in reason


def test_argparse_default_ignores_unrelated_and_benign_flags() -> None:
    orig = (
        "import argparse\n\np = argparse.ArgumentParser()\n"
        "p.add_argument('--host', default='0.0.0.0')\n"          # benign wildcard — not flagged
        "p.add_argument('--target-file', default='C:/x.ini')\n"  # unrelated flag name — not flagged
        "p.add_argument('--retries', type=int, default=3)\n"     # unrelated flag name — not flagged
        "args = p.parse_args()\n"
    )
    assert fidelity_failure(orig, orig) is None


_ORIG_LITS = """\
import requests
HEADER = "x-middleware-subrequest"
def run(base, pid):
    url = base + "/api/v4/projects/" + str(pid) + "/packages/npm"
    payload = "../etc/passwd"
    return requests.get(url, headers={HEADER: payload}, params={"ref": "main"})
"""


def test_information_loss_flags_dropped_technical_literal() -> None:
    """A merge that silently drops an endpoint/payload/header literal must be flagged even though
    the code still compiles and runs (the strongest, hardest-to-see LLM failure)."""
    merged = (
        "import requests\n\n"
        "def run(base, pid):\n"
        '    url = base + "/api/v4/projects/" + str(pid)\n'  # dropped /packages/npm
        "    return requests.get(url)\n"                       # dropped header + payload + ref
    )
    kinds = {f.kind for f in fidelity_check(merged, _ORIG_LITS)}
    assert "information_loss" in kinds
    reason = fidelity_failure(merged, _ORIG_LITS)
    assert "information_loss" in reason or "literal" in reason
    assert "/packages/npm" in reason and "x-middleware-subrequest" in reason


def test_information_loss_accepts_faithful_and_defanged_merge() -> None:
    faithful = (
        "import requests\n"
        'url = base + "/api/v4/projects/" + str(pid) + "/packages/npm"\n'
        'requests.get(url, headers={"x-middleware-subrequest": "../etc/passwd"}, params={"ref": "main"})\n'
    )
    assert [f for f in fidelity_check(faithful, _ORIG_LITS) if f.kind == "information_loss"] == []
    # a legitimately DEFANGED copy must not be flagged as lossy
    defanged = (
        "import requests\n"
        'url = base + "/api/v4/projects/" + str(pid) + "/packages/npm"\n'
        'requests.get("http://x", headers={"x-middleware-subrequest": "../etc%2fpasswd"}, params={"ref": "main"})\n'
    )
    assert [f for f in fidelity_check(defanged, _ORIG_LITS) if f.kind == "information_loss"] == []


def test_information_loss_ignores_prose_changes() -> None:
    orig = 'import os\ndef f():\n    """Original long narrative docstring here about the exploit."""\n    return os.getcwd()\n'
    merged = 'import os\ndef f():\n    """Rewritten shorter docstring."""\n    return os.getcwd()\n'
    assert [f for f in fidelity_check(merged, orig) if f.kind == "information_loss"] == []
