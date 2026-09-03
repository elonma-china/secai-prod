"""command_risk.classify: chặn phá hoại / tool sai phase / run_cmd khi tắt / target ngoài scope."""
from __future__ import annotations

from secai_gateway.poc.command_risk import classify
from secai_gateway.poc.lane1 import Lane1Action
from secai_gateway.poc.phases import Phase


class _S:
    exec_enabled = True
    lane1_use_raw_cmd = True
    verify_enabled = True
    verify_allow_all = False
    verify_targets = ["127.0.0.1"]


def _act(tool, **args):
    return Lane1Action(thought="t", tool=tool, args=dict(args))


def test_run_cmd_benign_allowed():
    d = classify(_act("run_cmd", shell="echo hi; id"), Phase.EXPLOIT, "127.0.0.1", "80", _S())
    assert d.allow, d.reason


def test_run_cmd_destructive_blocked():
    d = classify(_act("run_cmd", shell="rm -rf /"), Phase.EXPLOIT, "127.0.0.1", "80", _S())
    assert not d.allow and "phá hoại" in d.reason


def test_run_cmd_forkbomb_blocked():
    d = classify(_act("run_cmd", shell=":(){ :|:& };:"), Phase.EXPLOIT, "127.0.0.1", "80", _S())
    assert not d.allow


def test_run_cmd_blocked_when_exec_disabled():
    class S2(_S):
        exec_enabled = False
    d = classify(_act("run_cmd", shell="id"), Phase.EXPLOIT, "127.0.0.1", "80", S2())
    assert not d.allow and "EXEC_ENABLED" in d.reason


def test_run_cmd_toolword_leak_blocked():
    d = classify(_act("run_cmd", shell="poc_search(query='x')"), Phase.EXPLOIT, "127.0.0.1", "80", _S())
    assert not d.allow


def test_tool_wrong_phase_blocked():
    # nmap không thuộc phase EXPLOIT
    d = classify(_act("nmap", target="127.0.0.1"), Phase.EXPLOIT, "127.0.0.1", "80", _S())
    assert not d.allow and "phase" in d.reason


def test_out_of_scope_target_blocked():
    d = classify(_act("nmap", target="10.0.0.9"), Phase.RECON, "127.0.0.1", "80", _S())
    assert not d.allow and "allowlist" in d.reason


def test_in_scope_target_allowed():
    d = classify(_act("nmap", target="127.0.0.1"), Phase.RECON, "127.0.0.1", "80", _S())
    assert d.allow


def test_allow_all_bypasses_scope():
    class S3(_S):
        verify_allow_all = True
    d = classify(_act("nmap", target="10.0.0.9"), Phase.RECON, "127.0.0.1", "80", S3())
    assert d.allow
