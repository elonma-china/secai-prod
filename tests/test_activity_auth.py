"""Session cookie + credential checks for the /logs viewer."""
from __future__ import annotations

import hashlib
import time

from secai_gateway.activity import auth


class _S:
    logs_ui_username = "admin"
    logs_ui_password = "s3cret"
    logs_ui_password_sha256 = ""
    logs_ui_session_secret = "unit-test-secret"
    logs_ui_session_hours = 12


def test_credentials_accept_exact_match() -> None:
    assert auth.check_credentials(_S(), "admin", "s3cret") is True


def test_credentials_reject_wrong_password_or_user() -> None:
    assert auth.check_credentials(_S(), "admin", "nope") is False
    assert auth.check_credentials(_S(), "root", "s3cret") is False


def test_sha256_password_is_supported() -> None:
    s = _S()
    s.logs_ui_password = ""
    s.logs_ui_password_sha256 = hashlib.sha256(b"hunter2").hexdigest()
    assert auth.check_credentials(s, "admin", "hunter2") is True
    assert auth.check_credentials(s, "admin", "hunter3") is False


def test_empty_password_never_authenticates() -> None:
    """Fail-safe: an unconfigured viewer must not accept a blank password."""
    s = _S()
    s.logs_ui_password = ""
    s.logs_ui_password_sha256 = ""
    assert auth.is_configured(s) is False
    assert auth.check_credentials(s, "admin", "") is False


def test_cookie_roundtrip() -> None:
    cookie, max_age = auth.issue_cookie(_S(), "admin")
    assert max_age == 12 * 3600
    assert auth.verify_cookie(_S(), cookie) == "admin"


def test_tampered_cookie_is_rejected() -> None:
    cookie, _ = auth.issue_cookie(_S(), "admin")
    payload, _, sig = cookie.rpartition(".")
    assert auth.verify_cookie(_S(), f"{payload}.{'0' * len(sig)}") is None
    assert auth.verify_cookie(_S(), f"{payload}x.{sig}") is None
    assert auth.verify_cookie(_S(), None) is None
    assert auth.verify_cookie(_S(), "garbage") is None


def test_cookie_signed_with_another_secret_is_rejected() -> None:
    other = _S()
    other.logs_ui_session_secret = "different-secret"
    cookie, _ = auth.issue_cookie(_S(), "admin")
    assert auth.verify_cookie(other, cookie) is None


def test_expired_cookie_is_rejected(monkeypatch) -> None:
    cookie, _ = auth.issue_cookie(_S(), "admin")
    later = time.time() + 13 * 3600           # session_hours is 12
    monkeypatch.setattr(auth.time, "time", lambda: later)
    assert auth.verify_cookie(_S(), cookie) is None
