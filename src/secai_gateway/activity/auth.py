"""Password gate for the /logs viewer.

The viewer shows conversation content verbatim, so it is never served unauthenticated:
with no password configured the UI refuses to start rather than defaulting to open.

Sessions are stateless signed cookies (HMAC-SHA256 over ``user|expiry``) — no server-side
store to keep, and no new dependency: ``hmac``/``hashlib``/``secrets`` are stdlib. This is
deliberately not a general-purpose auth system; it gates one internal read-only viewer.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import secrets
import time
from typing import Any

logger = logging.getLogger(__name__)

COOKIE_NAME = "secai_logs_session"


def _secret(settings: Any) -> bytes:
    configured = str(getattr(settings, "logs_ui_session_secret", "") or "")
    if configured:
        return configured.encode("utf-8")
    # No secret configured: use a per-process random one. Sessions then survive until the
    # gateway restarts, which is acceptable for an internal viewer but worth saying out loud.
    global _EPHEMERAL_SECRET
    if _EPHEMERAL_SECRET is None:
        _EPHEMERAL_SECRET = secrets.token_bytes(32)
        logger.warning("logs UI: LOGS_UI_SESSION_SECRET not set — sessions reset on restart")
    return _EPHEMERAL_SECRET


_EPHEMERAL_SECRET: bytes | None = None


def is_configured(settings: Any) -> bool:
    """A password (plaintext or sha256) must be set before the viewer will serve."""
    return bool(getattr(settings, "logs_ui_password", "")
                or getattr(settings, "logs_ui_password_sha256", ""))


def check_credentials(settings: Any, username: str, password: str) -> bool:
    """Constant-time comparison on both fields (a fast reject on username would leak which
    half was wrong)."""
    expected_user = str(getattr(settings, "logs_ui_username", "") or "")
    user_ok = hmac.compare_digest(username or "", expected_user)

    sha = str(getattr(settings, "logs_ui_password_sha256", "") or "").strip().lower()
    if sha:
        digest = hashlib.sha256((password or "").encode("utf-8")).hexdigest()
        pass_ok = hmac.compare_digest(digest, sha)
    else:
        expected_pass = str(getattr(settings, "logs_ui_password", "") or "")
        pass_ok = bool(expected_pass) and hmac.compare_digest(password or "", expected_pass)
    return user_ok and pass_ok


def issue_cookie(settings: Any, username: str) -> tuple[str, int]:
    """Return ``(cookie_value, max_age_seconds)``."""
    hours = int(getattr(settings, "logs_ui_session_hours", 12) or 12)
    max_age = hours * 3600
    payload = {"u": username, "exp": int(time.time()) + max_age}
    raw = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode()
    sig = hmac.new(_secret(settings), raw.encode(), hashlib.sha256).hexdigest()
    return f"{raw}.{sig}", max_age


def verify_cookie(settings: Any, cookie: str | None) -> str | None:
    """Return the username for a valid, unexpired cookie, else None."""
    if not cookie or "." not in cookie:
        return None
    raw, _, sig = cookie.rpartition(".")
    expected = hmac.new(_secret(settings), raw.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected):
        return None
    try:
        payload = json.loads(base64.urlsafe_b64decode(raw.encode()))
    except Exception:
        return None
    if int(payload.get("exp", 0)) <= int(time.time()):
        return None
    user = payload.get("u")
    return user if isinstance(user, str) and user else None
