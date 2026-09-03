"""check_success: oracle tất định theo độ mạnh nonce > uid=/root: > success_signal."""
from __future__ import annotations

from secai_gateway.poc.lane1 import check_success


def test_nonce_strongest():
    ok, why = check_success("blah pzabcd1234 reflected", "wp_users", "pzabcd1234")
    assert ok and "nonce" in why


def test_uid_indicator():
    ok, why = check_success("output: uid=0(root) gid=0(root)", None, None)
    assert ok and "uid=" in why


def test_passwd_indicator():
    ok, _ = check_success("root:x:0:0:root:/root:/bin/bash", None, "pzX")
    assert ok


def test_success_signal_substring():
    ok, why = check_success("dumped table wp_users ok", "wp_users", None)
    assert ok and "wp_users" in why


def test_negative_no_proof():
    ok, why = check_success("HTTP 200 hello world", "wp_users", "pzX")
    assert not ok and why == ""


def test_signal_absent_but_no_indicator():
    ok, _ = check_success("payload echoed back: id", "uid=", "pzY")
    # 'uid=' là success_signal ở đây; chuỗi không chứa 'uid=' thực → phải False
    assert not ok
