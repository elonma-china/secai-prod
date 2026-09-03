"""Deterministic literal-masking for /ai/translate.

Customer report (2026-09): the translator "tinh chỉnh quá nhiều → mất tài khoản/mật
khẩu". These tests prove — WITHOUT a live model — that credentials and other literals
survive the round trip verbatim, that secrets never reach the model, and that a
credential-bearing input no longer 422s.
"""
from __future__ import annotations

import re

import pytest

from secai_gateway.api.literal_mask import mask_literals, unmask_literals
from secai_gateway.api.messages import translate_prompt
from secai_gateway.api.schemas import LegacyRequest

_PH = re.compile(r"⟦L\d+⟧")


def _simulate_model_translation(masked: str) -> str:
    """A faithful model keeps placeholders intact while rewording prose. We emulate the
    worst realistic behaviour: aggressive rewrite of the words, placeholders copied."""
    out = masked
    for vi, en in (("Tài khoản", "Account"), ("tài khoản", "account"),
                   ("Mật khẩu", "Password"), ("mật khẩu", "password"),
                   ("Máy chủ", "Server"), ("Đăng nhập vào", "Log in to")):
        out = out.replace(vi, en)
    return out


def test_credential_value_roundtrips_verbatim():
    raw = "Tài khoản: admin\nMật khẩu: P@ssw0rd!2024"
    masked, literals = mask_literals(raw)
    # the raw secret values are gone from what the model would see
    assert "admin" not in masked
    assert "P@ssw0rd!2024" not in masked
    assert masked.count("⟦L") == 2
    # model rewrites the labels, keeps the placeholders
    out = unmask_literals(_simulate_model_translation(masked), literals)
    assert "Account: admin" in out
    assert "Password: P@ssw0rd!2024" in out


def test_vietnamese_labelled_credentials_are_masked():
    # These slip past the English-only input guard, so the model used to mangle them.
    masked, literals = mask_literals("mật khẩu = hunter2, tài khoản = root")
    assert "hunter2" in literals and "root" in literals
    assert "hunter2" not in masked and "root" not in masked


@pytest.mark.parametrize("raw,secret", [
    ("password: hunter2", "hunter2"),                          # english label → 422'd raw
    ("Authorization: Bearer abcDEF1234567890xyz", "abcDEF1234567890xyz"),
    ("api_key=sk_live_ABCDEFGHIJKLMNOP1234", "sk_live_ABCDEFGHIJKLMNOP1234"),
    ("Thẻ 4111 1111 1111 1111 hết hạn", "4111 1111 1111 1111"),  # 16-digit identifier
])
def test_secret_value_never_reaches_the_model(raw, secret):
    """The exact spans the input guard would 422 on are captured in the map, so the secret
    VALUE is absent from the masked text sent to the model — and restored byte-for-byte."""
    masked, literals = mask_literals(raw)
    assert literals                        # something was actually captured
    assert secret not in masked            # value never sent to the model
    assert unmask_literals(masked, literals) == raw  # and comes back verbatim, no nesting


def test_structural_literals_roundtrip():
    raw = ("Gửi tới https://target.local:8443/login từ IP 10.0.0.5:22 . "
           "Hash 5f4dcc3b5aa765d61d8327deb882cf99 , CVE-2024-1234 , "
           "chạy `curl -X POST /api/exec` ngay.")
    masked, literals = mask_literals(raw)
    for lit in ("https://target.local:8443/login", "10.0.0.5:22",
                "5f4dcc3b5aa765d61d8327deb882cf99", "CVE-2024-1234",
                "`curl -X POST /api/exec`"):
        assert lit in literals, lit
        assert lit not in masked, lit
    # every literal comes back byte-for-byte after a no-op "translation"
    assert unmask_literals(masked, literals) == raw


def test_plain_prose_is_untouched():
    raw = "Lỗ hổng cho phép kẻ tấn công thực thi mã từ xa và/hoặc leo thang đặc quyền."
    masked, literals = mask_literals(raw)
    assert literals == []          # no false positives ("và/hoặc", "user experience"…)
    assert masked == raw


def test_no_false_positive_on_prose_slash_and_labels():
    for raw in ("nhập user experience tốt", "chọn A/B/C", "read and/or write"):
        masked, literals = mask_literals(raw)
        assert literals == [], raw
        assert masked == raw


def test_unmask_is_whitespace_tolerant_and_safe():
    _, literals = mask_literals("password: s3cr3t")
    # model padded the token with spaces
    assert unmask_literals("Password: ⟦ L 0 ⟧", literals) == "Password: s3cr3t"
    # out-of-range placeholder is left as-is, never raises
    assert unmask_literals("⟦L9⟧", literals) == "⟦L9⟧"


def test_translate_prompt_returns_prompt_and_literals_without_422():
    # A raw "password: ..." previously raised HTTPException(422) in translate_prompt.
    body = LegacyRequest(text="Đăng nhập với password: TopSecret1", target_lang="en")
    prompt, literals = translate_prompt(body)
    assert "sang en" in prompt
    assert "TopSecret1" not in prompt          # secret is masked out of the model prompt
    assert "TopSecret1" in literals
    assert _PH.search(prompt)                   # a placeholder made it into the prompt


def test_empty_input_is_noop():
    assert mask_literals("") == ("", [])
    assert unmask_literals("", []) == ""
