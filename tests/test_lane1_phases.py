"""phase-control: chuyển phase tất định + force-advance khi stuck."""
from __future__ import annotations

from secai_gateway.poc.phases import (ENUM_STEP_LIMIT, STUCK_LIMIT, Lane1State, Phase,
                                      advance_phase, allowed_tools)


def test_allowed_tools_per_phase():
    s = Lane1State()
    assert "http_request" in allowed_tools(s) and "nmap" in allowed_tools(s)
    s.phase = Phase.EXPLOIT
    assert "run_cmd" in allowed_tools(s) and "sqlmap" in allowed_tools(s)


def test_recon_to_enum_on_alive():
    s = Lane1State()
    s.facts["alive"] = True
    advance_phase(s, None, None)
    assert s.phase is Phase.ENUM


def test_enum_to_exploit_on_endpoint():
    s = Lane1State(phase=Phase.ENUM)
    s.facts["endpoint"] = True
    advance_phase(s, None, None)
    assert s.phase is Phase.EXPLOIT


def test_enum_to_exploit_after_step_limit():
    s = Lane1State(phase=Phase.ENUM)
    for _ in range(ENUM_STEP_LIMIT):
        advance_phase(s, None, None)
    assert s.phase is Phase.EXPLOIT


def test_exploit_to_verify_on_partial():
    s = Lane1State(phase=Phase.EXPLOIT)
    s.facts["partial_hit"] = True
    advance_phase(s, None, None)
    assert s.phase is Phase.VERIFY


def test_stuck_force_advance():
    s = Lane1State()  # RECON, không có fact
    s.stuck = STUCK_LIMIT
    advance_phase(s, None, None)
    assert s.phase is Phase.ENUM and s.stuck == 0


def test_verify_is_terminal():
    s = Lane1State(phase=Phase.VERIFY)
    s.stuck = STUCK_LIMIT + 5
    advance_phase(s, None, None)
    assert s.phase is Phase.VERIFY
