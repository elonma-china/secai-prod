"""Regression suite for the chat OWASP-version-fabrication bug (CQ1a / Bug #8, 2026-09-22).

Bug: asking "OWASP Top 10 2021 gồm những gì?" (or a paraphrase) made the model, in about
1/4-1/3 of calls, relabel the title as "OWASP Top 10 2025" while still listing the real
A01-A10 items from the 2021 edition underneath the wrong year label -- a title/content
year mismatch. Non-deterministic, so it is a knowledge/prompt-anchoring gap rather than a
hard bug: the model has no anchored rule about year labels needing to match listed
content, so temperature-driven sampling sometimes drifted the year label.

Root cause: knowledge/prompt gap, not a KB data gap. In production the gateway runs with
``airgap=true`` by default (``config.py: Settings.airgap``), which force-disables
``web_fetch``/``web_search`` (see ``Settings._enforce_airgap``) even though
``chat_retrieval_matrix`` in ``prompts/chat.md`` tells the model to fetch the official
OWASP/CWE/MITRE Top-N page for "Top-N theo nam" questions. With that tool unavailable,
the model answers from parametric memory with no anchor for title/content consistency.

Round 1 fix (2026-09-22, REVERTED by round 2 below): anchored "OWASP Top 10's latest
edition is 2021 (2003/2004/2007/2010/2013/2017/2021 are the only real editions)" and told
the model to deny a "2025" edition and substitute 2021 content. THIS WAS FACTUALLY WRONG:
OWASP Top 10:2025 was released (~Nov 2025 RC / early 2026 final) and is the actual current
edition, with real content changes vs. 2021 (SSRF folded into Broken Access Control; new
A03 Software Supply Chain Failures and A10 Mishandling of Exceptional Conditions). The
round-1 prompt was hard-pinning a "latest = 2021" fact that rots the moment a new edition
ships -- it would have made the model confidently deny a real 2025 answer and serve stale
2021 content instead, which is the same class of bug (or worse, since it's a confident,
policy-mandated denial of true information).

Round 2 fix (this version, prompt-only, ``prompts/chat.md``): replaced the hard-pinned
year facts with a **version-agnostic** rule that does not go stale: (1) never invent/swap
the year the user asked about, and never flatly deny a year the user names unless
absolutely certain it doesn't exist; (2) the year label in the answer's title must match
the content actually listed -- this is the actual bug pattern (label year A, content year
B) and does not require knowing which year is "latest"; (3) a confidence hedge: since the
gateway cannot verify owasp.org/cwe.mitre.org content live, note that in the answer and
avoid confidently listing item details it isn't sure are correct for that exact year,
pointing the user at the official site instead; (4) never assert "latest edition is year
Y" with confidence, since the system cannot verify that live.

Round-2 iteration notes (found during live before/after verification, fixed in this same
block before hand-off): the first draft of the hedge told the model to state the caveat
"ngay tu dau" (right at the start) and used the word "air-gap" + repeated "ngay"/"thoi
gian thuc" -- this combined with an EXISTING, unrelated line already in
``prompts/base_system.md`` (``tool_routing``: "...hoac ngay hien tai (dau cau hoi nguoi
dung) cho thay kha nang da co ban moi hon... de xac nhan dung ban moi nhat...") to make
the model mistake the runner-injected ``[Hom nay la ...]`` date banner (prepended to every
user turn by ``agent/runner.py``) for user-supplied "date information" and reply "Cam on
ban da cung cap thong tin ngay thang" instead of answering -- confirmed via a live A/B
probe (SSRF and CVE-2021-44228 questions: 0/8 misfired; "OWASP Top 10 2021": 3/4 misfired
with the first hedge wording, ~1/12 with the softened wording below, matching the ~5%
baseline rate already present with NO versioned_list_policy block at all). Softened the
hedge to "khong bat buoc phai la cau dau tien" and dropped the literal repetition, which
cut the misfire rate back toward baseline. Separately, live testing also caught the model
occasionally swapping a "2021"-asked question over to a confident "2025" answer (the
inverse of the original bug, likely pulled by that same base_system.md line and by
built-in knowledge of a newer edition) -- the first versioned_list_policy bullet was
strengthened to explicitly forbid this even when the model believes a newer edition
exists, only allowing a short note about it AFTER answering the asked year in full.

This suite is a cheap prompt-content guard (no LLM call) — it catches an accidental
future revert/edit of the new block, and specifically guards against round-1's mistake of
re-introducing a hardcoded "latest = 2021" claim. Live before/after verification (>=5
calls per phrasing, temperature 0.3, against a real running instance, for both "2021" and
"2025" prompts, plus the SSRF/CVE-2021-44228 date-banner control probe) was done manually
— see the Bug #8 fix report (round 2).
"""
from __future__ import annotations

from xml.etree import ElementTree

from secai_gateway.agent.profiles import PROFILES, compose_system_prompt

_CHAT_MD = PROFILES["chat"].system


def test_chat_md_has_versioned_list_policy_block() -> None:
    assert "<versioned_list_policy" in _CHAT_MD
    assert "</versioned_list_policy>" in _CHAT_MD


def test_rule_does_not_hardcode_a_latest_owasp_year() -> None:
    # Round-1 regression guard: the fix must NOT claim a specific year is "the latest"
    # OWASP Top 10 edition, and must not hard-list "only these years are real" -- both
    # rot the moment a new edition ships (exactly what broke round 1: OWASP Top 10:2025
    # is real, but round 1 told the model to deny it and serve 2021 content instead).
    assert "Bản MỚI NHẤT hiện có là" not in _CHAT_MD
    assert "CHƯA có bản 2023/2024/2025" not in _CHAT_MD
    assert "các bản đã phát hành THẬT SỰ: 2003, 2004, 2007, 2010, 2013, 2017" not in _CHAT_MD


def test_rule_forbids_denying_a_user_named_year() -> None:
    assert (
        'KHÔNG khẳng định một năm người dùng nêu là "chưa tồn tại / chưa có bản đó" '
        "trừ khi CHẮC CHẮN TUYỆT ĐỐI" in _CHAT_MD
    )


def test_rule_forbids_swapping_the_asked_year() -> None:
    assert "TUYỆT ĐỐI KHÔNG tự ý đổi năm mà người dùng đã hỏi sang một năm khác" in _CHAT_MD
    # Strengthened after live testing caught the model swapping a "2021" question over to
    # a confident "2025" answer (believing a newer edition exists) -- must forbid that
    # explicitly, not just "don't invent an unasked year".
    assert "kể cả khi bạn tin rằng có một bản" in _CHAT_MD


def test_rule_requires_title_year_matches_listed_content() -> None:
    assert "Nhãn năm/phiên bản ở tiêu đề PHẢI khớp ĐÚNG nội dung liệt kê" in _CHAT_MD
    assert "đây LÀ bịa phiên bản" in _CHAT_MD


def test_rule_has_confidence_hedge_for_versioned_top_n() -> None:
    # Round-3 wording (avoids "ngay tu dau"/repeated "ngay"/"air-gap" literal, which
    # combined with base_system.md's own "ngay hien tai (dau cau hoi nguoi dung)" date-
    # banner note to make the model misfire on the injected `[Hom nay la ...]` date
    # banner and reply "cam on ban da cung cap thong tin ngay thang" instead of
    # answering -- see the round-3 fix report). Same hedge substance, safer phrasing.
    assert "hệ thống không truy cập được owasp.org/cwe.mitre.org để đối chiếu trực tiếp" in _CHAT_MD
    assert "không có cách kiểm chứng độc lập nội dung này" in _CHAT_MD
    assert "owasp.org" in _CHAT_MD and "cwe.mitre.org" in _CHAT_MD


def test_rule_does_not_force_answer_to_open_with_date_caveat() -> None:
    # The confidence-hedge note must be positionable anywhere in the answer, not forced
    # to be the opening sentence -- forcing "state this first" is what triggered the
    # date-banner misfire in round 2.
    assert "không bắt buộc phải là câu đầu tiên" in _CHAT_MD
    assert "PHẢI nói rõ ngay từ đầu" not in _CHAT_MD


def test_rule_forbids_confident_latest_year_claim() -> None:
    assert 'TUYỆT ĐỐI KHÔNG tự khẳng định chắc nịch "bản mới nhất hiện nay là năm Y"' in _CHAT_MD


def test_rule_forbids_assigning_a_year_when_user_did_not_ask_one() -> None:
    # Residual bug (found 2026-09-27, live isolated testing): the round-2 block above only
    # covered "user names a year" — it said nothing about what happens when the user asks
    # a bare "OWASP Top 10 gồm những gì?"/"CWE Top 25 gồm những gì?" with NO year at all.
    # Live probes (N=5 each, temperature default, isolated instance) found the model
    # self-assigned a specific year into the TITLE with no evidence in 8/10 calls -- mostly
    # "(2025)"/"2025" (matching base_system.md's tool_routing hint that
    # https://owasp.org/Top10/2025/ is the "verified current" fetch URL, which leaked into
    # the answer as a label even though web_fetch was never actually called/available under
    # airgap) while the listed content underneath was the real 2021 edition -- the exact
    # label/content mismatch this suite exists to catch, just triggered from the "no year"
    # angle instead of the "explicit year" angle the earlier tests cover.
    assert "Người dùng KHÔNG nêu năm/phiên bản nào" in _CHAT_MD
    assert (
        'TUYỆT ĐỐI CẤM tự gắn MỘT NĂM CỤ THỂ vào TIÊU ĐỀ hay khẳng định "đây là bản/phiên '
        'bản mới nhất hiện hành là năm X"' in _CHAT_MD
    )


def test_rule_no_year_case_requires_actual_successful_fetch_before_labeling() -> None:
    assert (
        "trừ khi vừa `web_fetch` THÀNH CÔNG một trang OWASP/CWE chính thức trong CHÍNH "
        "LƯỢT NÀY" in _CHAT_MD
    )
    assert "không phải lý do để tự bịa nhãn năm thay thế" in _CHAT_MD


def test_rule_no_year_case_has_concrete_wrong_example() -> None:
    # Must name the exact failure pattern observed live so the model has a concrete
    # negative example, not just an abstract prohibition (abstract-only wording is what
    # let the "explicit year" version of this bug slip through in earlier rounds).
    assert '"## OWASP Top 10 (2025)"' in _CHAT_MD
    assert "nội dung 2021 bị gán nhãn năm khác" in _CHAT_MD


def test_composed_chat_prompt_still_well_formed_xml() -> None:
    # The new block must not break the XML well-formedness Qwen's chat template and
    # test_prompts.py rely on.
    text = compose_system_prompt(PROFILES["chat"])
    root = ElementTree.fromstring(text)
    assert any(el.tag == "versioned_list_policy" for el in root.iter())
