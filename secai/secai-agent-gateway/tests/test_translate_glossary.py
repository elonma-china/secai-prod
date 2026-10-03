"""Prompt-content regression tests for the /ai/translate security-terminology glossary
(Bug #12 / TR3, 2026-09-22).

Bug: EN->VI translation of security terms was non-standard AND inconsistent between calls
— e.g. "attacker" came back as "Tấn công viên" (nonstandard; the ATTT-vn standard is
"kẻ tấn công") and "privilege escalation" came back as "nâng cấp đặc quyền" / "tăng cấp đặc
quyền" / "tăng cường đặc quyền" (three different wordings across three calls; the standard
is "leo thang đặc quyền"). Root cause: translate.md had no domain glossary/constraint for
security terminology, so the model free-styled a plausible-sounding but non-standard and
non-reproducible translation for each call (temperature-driven variance).

Fix: `<security_glossary>` block added to prompts/translate.md with fixed EN->VI mappings
for the common ATTT-vn terms + an explicit instruction to use them consistently instead of
inventing variants.

IMPORTANT (found + fixed during this same investigation): a first draft of the glossary
block regressed the TR1 "target language always wins" guarantee — having English glossary
words (attacker, privilege escalation, ...) sitten in the system prompt measurably increased
how often a same-language Vietnamese->Vietnamese request got flipped into English (0/2 with
no glossary -> 2/2 flipped with the first wording -> 1/3 flipped with an intermediate
wording -> 0/4 flipped after adding an explicit "this table is reference data only, it is
NEVER a reason to change the output language" reminder, live-verified on :13905). These
tests pin BOTH properties in the prompt text so a future edit can't silently re-introduce
either failure mode: the glossary entries used in the bug report, AND the language-lock
subordination clause that guards the glossary from overriding target_lang.

No LLM call, no network — pure prompt-content assertions (mirrors the UC5-style guards in
tests/test_chat_poc_drilldown_regression.py), plus one well-formedness check on the composed
system prompt (XML well-formedness matters — the same document is fed through Qwen's chat
template).
"""
from __future__ import annotations

from secai_gateway.agent.profiles import PROFILES, compose_system_prompt

_TRANSLATE_MD = PROFILES["translate"].system


def test_glossary_block_present_and_well_formed_tags() -> None:
    assert "<security_glossary" in _TRANSLATE_MD
    assert "</security_glossary>" in _TRANSLATE_MD
    # opens before it closes
    assert _TRANSLATE_MD.index("<security_glossary") < _TRANSLATE_MD.index("</security_glossary>")


def test_glossary_has_the_two_terms_from_the_bug_report() -> None:
    # attacker -> "kẻ tấn công" (NOT "Tấn công viên")
    assert "attacker → kẻ tấn công" in _TRANSLATE_MD
    # privilege escalation -> "leo thang đặc quyền" (NOT "nâng cấp"/"tăng cấp"/"tăng cường")
    assert "privilege escalation → leo thang đặc quyền" in _TRANSLATE_MD


def test_glossary_has_the_other_proposed_standard_terms() -> None:
    for pair in (
        "vulnerability → lỗ hổng",
        "buffer overflow → tràn bộ đệm",
        "injection → chèn mã",
        "SQL injection → chèn câu lệnh SQL",
        "authentication → xác thực",
        "authorization → phân quyền",
        "remote code execution → thực thi mã từ xa",
        "backdoor → cửa hậu",
        "brute force → tấn công vét cạn",
        "patch → bản vá",
        "mitigation → biện pháp giảm thiểu",
        "threat actor → tác nhân đe dọa",
    ):
        assert pair in _TRANSLATE_MD, f"missing glossary entry: {pair!r}"


def test_glossary_excludes_payload_deferring_to_verbatim_preservation() -> None:
    # Round 2 (Opus review): "payload → mã tải" conflicted with verbatim_preservation
    # (which lists "payload" as a term to keep verbatim, never translated). The glossary
    # must NOT contain a payload mapping, and must say so explicitly, so the two policies
    # don't fight — a future edit re-adding "payload →" here would reintroduce the clash.
    assert "payload →" not in _TRANSLATE_MD
    assert "\"payload\": KHÔNG áp dụng" in _TRANSLATE_MD


def test_glossary_warns_against_the_exact_nonstandard_wordings_from_the_bug_report() -> None:
    # These must appear as named anti-examples so a future edit doesn't accidentally
    # reintroduce them as "acceptable variants".
    assert "Tấn công viên" in _TRANSLATE_MD
    assert "nâng cấp đặc quyền" in _TRANSLATE_MD
    assert "tăng quyền" in _TRANSLATE_MD
    assert "tăng cường đặc quyền" in _TRANSLATE_MD


def test_glossary_is_subordinate_to_the_target_language_lock() -> None:
    # Regression guard for the language-flip issue found while fixing this bug: the
    # glossary must explicitly say it is reference data, not a license to change the
    # output language away from target_lang.
    assert "KHÔNG được dùng làm lý do để tự ý đổi ngôn ngữ đầu ra" in _TRANSLATE_MD
    assert "TUYỆT ĐỐI KHÔNG dịch ngược chúng sang tiếng Anh" in _TRANSLATE_MD


def test_composed_translate_prompt_is_well_formed_xml() -> None:
    from xml.etree import ElementTree

    text = compose_system_prompt(PROFILES["translate"])
    root = ElementTree.fromstring(text)
    assert any(el.tag == "security_glossary" for el in root.iter())
