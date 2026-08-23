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
