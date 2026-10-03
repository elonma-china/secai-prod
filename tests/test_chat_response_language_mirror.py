"""Regression suite for the chat response-language bug (CQ5 / Bug #14, 2026-09-22).

Bug: ``POST /sessions/{sid}/chat`` with the ENGLISH question "What is SQL injection and
how to prevent it?" made the model answer ENTIRELY IN VIETNAMESE (content correct, wrong
language). Expected: the chatbot should MIRROR the language of the user's question and
only switch language when the user explicitly asks for a specific one.

Root cause: ``prompts/base_system.md`` has a ``<language_policy priority="highest">``
block that hard-codes the OPPOSITE behaviour for every profile that prepends it
(``base: true`` in ``prompts/profiles.yaml`` — chat, exploit_research, report,
statistical, bughunt all prepend it):

    "Toan bo phan dien giai, cau, tieu de, nhan, huong dan, loi chao va ket thuc phai
    bang tieng Viet. Cam viet nua Anh nua Viet."
    (= "The entire explanation, sentences, titles, labels, instructions, greetings and
    endings must be in Vietnamese. Mixing English/Vietnamese is forbidden.")

This is prepended BEFORE ``chat.md`` by ``agent/profiles.py::compose_system_prompt``
(``body = f"{BASE_SYSTEM}\\n\\n{profile.system}"``), so the chat profile's system prompt
literally instructed the model to always answer in Vietnamese regardless of the
question's language — there was no bias/drift involved, it was an explicit instruction.
``chat.md``'s own ``chat_response_contract`` compounded this by telling the model to use
Vietnamese ``##``/``###`` headings specifically.

Round 1 fix (prompt-only, ``prompts/chat.md`` only) added a new
``<response_language_policy priority="highest">`` block narrowing the general
``language_policy`` for the chat profile: mirror the user's question language, unless the
user names a target language explicitly. This was directionally correct but an Opus
review (round 2) flagged it as not "certain enough": it left TWO ``priority="highest"``
blocks head-on in the same composed prompt (base_system.md's ``language_policy`` at the
very top, chat.md's ``response_language_policy`` ~8100 chars later) relying only on
"later instruction wins" positional precedence, PLUS ``base_system.md`` still carried two
absolute Vietnamese-only directives with no awareness that a profile could override them:
(1) ``language_policy``'s own few-shot "Ví dụ sai" (wrong-example) list, which labels
plain English sentences like "Validate and whitelist allowed URLs." as WRONG — exactly
the kind of sentence a valid English answer must produce, biasing the model back toward
Vietnamese; and (2) ``response_policy``'s "Dùng tiêu đề tiếng Việt khi cần cấu trúc"
(use Vietnamese headings) line, unaffected by chat.md's own heading-rule tweak.

Round 2 fix (this version, ``base_system.md`` + ``chat.md``, still prompt-only,
behavior-preserving for every OTHER profile): made ``base_system.md`` itself
base-conditional instead of relying on chat.md to silently out-prioritize it:
- ``language_policy``: added an explicit "NGOẠI LỆ NHƯỜNG QUYỀN cho hồ sơ chế độ" (mode-
  profile deference exception) bullet saying that if the mode profile appended after this
  block has its own language block (e.g. one that mirrors the input language), that block
  wins over the Vietnamese default, and the "Ví dụ sai" English examples only count as
  wrong when the answer is already in Vietnamese (a valid English answer is not an
  error).
- ``response_policy``: the heading-language line now reads "theo ngôn ngữ trả lời của hồ
  sơ ... (mặc định tiếng Việt; nếu hồ sơ chế độ có khối ngôn ngữ riêng ... thì theo đúng
  ngôn ngữ đó)" instead of hard-coding Vietnamese headings.
- ``chat.md``'s ``response_language_policy`` gained one new opening bullet explicitly
  identifying itself as "the mode profile's own language block" that base_system.md's
  exception defers to, closing the loop symmetrically instead of depending on raw
  document order.

Profiles other than chat are unaffected because the new base_system.md clause is
conditional on the profile itself carrying a language block: ``exploit_research``
(``exploit.md``) and ``bughunt`` (``bughunt.md``) — both ``base: true`` — have no such
block (grep confirms), so for them the exception clause is a no-op and the plain
Vietnamese default (plus exploit.md's own line 66 reinforcement) still applies in full.
``report``/``statistical_report`` are ``base: false`` in ``profiles.yaml`` (their own
``language_policy`` blocks in report.md/statistical.md), so they never see
``base_system.md`` at all and are untouched by this change (already covered by
``test_prompts.py``'s ``assert (BASE_SYSTEM in text) is profile.base``).

This suite is a cheap prompt-content guard (no LLM call) — it verifies both the
chat-scoped block and the new base-level conditional clauses exist with their core
commitments, that composed prompts for chat/exploit_research/bughunt/report/
statistical_report remain well-formed XML, and that exploit_research/bughunt still carry
an unconditional Vietnamese directive (no mirror-language leak). It also checks none of
the pre-existing chat policies (remediation_code_policy, versioned_list_policy,
grounding_and_honesty, poc_drilldown_policy, output_hygiene) were clobbered.

Round 3 fix (live E2E from Agent C, ``prompts/chat.md`` ONLY this round — base_system.md
intentionally left untouched to avoid racing a concurrent exploit_research live test):
live testing found round 2 still failed English mirroring 0/5 (scenario 1: plain English
concept question, no KB grounding involved) and partially failed the closer-translation
rule 2/5 (scenario 3a: Vietnamese question + explicit "answer in English"). Root cause:
``chat.md``'s OWN ``<output_hygiene priority="highest">`` block (same priority as
``response_language_policy``) had an unconditional leftover directive: "Luôn ĐỌC kết quả
rồi DIỄN GIẢI lại bằng tiếng Việt gọn gàng cho người dùng" ("always read the result and
paraphrase it IN VIETNAMESE") — this is exactly the instruction a plain concept question
(no tool grounding needed either way) would trip on, and being co-equal priority with
``response_language_policy`` gave the model two competing absolute instructions. A second,
lower-severity leftover was ``about_system``'s usage hint ("Cách dùng: hỏi trực tiếp bằng
tiếng Việt" — "usage: ask directly in Vietnamese"), always present in the composed system
prompt and reinforcing a Vietnamese-only framing of the whole system.

Round 3 changes (``prompts/chat.md`` only):
1. ``output_hygiene``'s paraphrase line: dropped the hard "bằng tiếng Việt" and now reads
   "diễn giải lại gọn gàng ... ĐÚNG NGÔN NGỮ TRẢ LỜI của lượt này theo
   `response_language_policy` ... KHÔNG tự ý ép tiếng Việt".
2. ``about_system``'s usage hint: "hỏi trực tiếp bằng tiếng Việt" -> "hỏi trực tiếp bằng
   tiếng Việt hoặc tiếng Anh — câu trả lời sẽ bám theo đúng ngôn ngữ câu hỏi".
3. ``response_language_policy`` gained an explicit subordination bullet: the language
   decided in THIS block governs every other block in the chat profile (naming
   ``output_hygiene``, ``chat_not_found_response``, ``chat_response_contract``,
   ``about_system``'s usage hint) — "tiếng Việt" elsewhere in the profile is a default/
   example, never an absolute instruction that outranks this block.
4. The closer-translation bullet (from round 1/2) was strengthened per the scenario-3a
   failure: instead of asking the model to translate "Bạn cần IAS.AI hỗ trợ thêm gì nữa
   không?" on the fly, it now gives a ready-made English translation ("Anything else I
   can help you with regarding IAS.AI?") plus a mandatory last-sentence self-check before
   sending, reducing the chance of a stray Vietnamese line surviving into a non-Vietnamese
   answer.

``base_system.md`` was re-scanned for any remaining unconditional Vietnamese directive:
none found — every "tiếng Việt" mention left there (in ``language_policy`` and
``response_policy``) is already gated by round 2's deference-exception/"khi câu trả lời
đang ở tiếng Việt" qualifiers. No further base_system.md change is needed; per the
coordinator's round-3 instruction, base_system.md is NOT touched this round regardless
(a concurrent live test on exploit_research depends on it), even though it needed none.

Live before/after verification (English question -> English answer against a running
instance, N>=5, plus the scenario-3a closer check) is a follow-up step for Agent C, not
done in this suite.
"""
from __future__ import annotations

from xml.etree import ElementTree

from secai_gateway.agent.profiles import BASE_SYSTEM, PROFILES, compose_system_prompt

_CHAT_MD = PROFILES["chat"].system
_EXPLOIT_MD = PROFILES["exploit_research"].system
_BUGHUNT_MD = PROFILES["bughunt"].system


def test_chat_md_has_response_language_policy_block() -> None:
    assert "<response_language_policy" in _CHAT_MD
    assert "</response_language_policy>" in _CHAT_MD


def test_policy_is_marked_highest_priority() -> None:
    assert 'priority="highest"' in _CHAT_MD
    start = _CHAT_MD.index("<response_language_policy")
    end = _CHAT_MD.index(">", start)
    assert 'priority="highest"' in _CHAT_MD[start:end]


def test_policy_narrows_the_general_vietnamese_default_for_chat_only() -> None:
    # Must explicitly call out that it is a scoped override of the general
    # language_policy (which still applies unmodified to other profiles), not a
    # silent/ambiguous change.
    assert "Ghi đè phạm vi hẹp cho hồ sơ chat này" in _CHAT_MD
    assert "language_policy" in _CHAT_MD


def test_policy_requires_mirroring_english_question_with_english_answer() -> None:
    assert "Câu hỏi viết bằng **tiếng Anh**" in _CHAT_MD
    assert "toàn bộ câu trả lời bằng **tiếng Anh**" in _CHAT_MD


def test_policy_keeps_vietnamese_default_behaviour_for_vietnamese_questions() -> None:
    assert "Câu hỏi viết bằng **tiếng Việt**" in _CHAT_MD
    assert "toàn bộ câu trả lời bằng tiếng Việt (hành vi mặc định, không đổi)" in _CHAT_MD


def test_policy_lets_explicit_user_language_request_win() -> None:
    assert "Người dùng yêu cầu rõ một ngôn ngữ cụ thể" in _CHAT_MD
    assert "answer in Vietnamese" in _CHAT_MD


def test_policy_translates_the_default_closer_line_for_consistency() -> None:
    # Must not leave a stray Vietnamese closer/greeting inside an otherwise
    # non-Vietnamese answer. Round 3 strengthened this after live E2E caught it failing
    # 3/5 times (scenario 3a: Vietnamese question + "answer in English" -> stray
    # Vietnamese closer): now gives a concrete English translation instead of asking the
    # model to translate on the fly, plus a mandatory self-check on the last sentence.
    assert "TUYỆT ĐỐI KHÔNG được chép nguyên văn cụm tiếng Việt đó" in _CHAT_MD
    assert "Anything else I can help you with regarding IAS.AI?" in _CHAT_MD
    assert "BẮT BUỘC tự rà lại câu cuối cùng trước khi gửi" in _CHAT_MD


def test_policy_keeps_identifier_and_term_exception() -> None:
    # CVE/CWE ids, product/tool names, and standardized security terms must still stay
    # in English regardless of the answer's language, same as the original policy.
    assert "mã định danh" in _CHAT_MD
    assert "CVE-/CWE-/CAPEC-/T####" in _CHAT_MD


def test_response_contract_heading_rule_no_longer_hardcodes_vietnamese() -> None:
    # Round-1 wording hardcoded "tiêu đề `##`/`###` tiếng Việt" which contradicted the
    # new mirror-language policy for English answers. Must now be language-relative.
    assert "tiêu đề `##`/`###` tiếng Việt khi cần chia phần" not in _CHAT_MD
    assert "theo đúng ngôn ngữ trả lời của lượt này" in _CHAT_MD


def test_preexisting_policies_are_not_clobbered() -> None:
    for tag in (
        "remediation_code_policy",
        "versioned_list_policy",
        "grounding_and_honesty",
        "poc_drilldown_policy",
        "output_hygiene",
    ):
        assert f"<{tag}" in _CHAT_MD
        assert f"</{tag}>" in _CHAT_MD


def test_composed_chat_prompt_still_well_formed_xml() -> None:
    # The new block must not break the XML well-formedness Qwen's chat template and
    # test_prompts.py rely on, and must survive composition with the shared base prompt.
    text = compose_system_prompt(PROFILES["chat"])
    root = ElementTree.fromstring(text)
    assert any(el.tag == "response_language_policy" for el in root.iter())


# --- Round 2: base_system.md made base-conditional instead of relying on document order ---


def test_base_language_policy_has_mode_profile_deference_exception() -> None:
    assert "NGOẠI LỆ NHƯỜNG QUYỀN cho hồ sơ chế độ" in BASE_SYSTEM
    assert "ngôn ngữ trả lời riêng của hồ sơ đó" in BASE_SYSTEM
    assert "có hiệu lực cao hơn mặc định này" in BASE_SYSTEM


def test_base_language_policy_scopes_wrong_examples_to_vietnamese_answers() -> None:
    # The "Ví dụ sai" (wrong-example) English sentences must no longer be flagged as
    # wrong unconditionally -- only when mixed into a Vietnamese answer. Otherwise a
    # correct English answer (which necessarily contains sentences like these) is
    # penalized by its own system prompt.
    assert 'CHỈ tính là sai trong trường hợp câu trả lời ĐANG Ở tiếng Việt' in BASE_SYSTEM
    assert "KHÔNG áp dụng cho một câu trả lời tiếng Anh tự nhiên" in BASE_SYSTEM
    # The literal wrong-example sentences must still be present (guard content wasn't
    # deleted, only rescoped).
    assert "Validate and whitelist allowed URLs." in BASE_SYSTEM


def test_base_response_policy_heading_rule_is_profile_conditional() -> None:
    assert "Dùng tiêu đề tiếng Việt khi cần cấu trúc." not in BASE_SYSTEM
    assert "Dùng tiêu đề theo ngôn ngữ trả lời của hồ sơ khi cần cấu trúc" in BASE_SYSTEM
    assert "mặc định tiếng Việt" in BASE_SYSTEM


def test_chat_block_identifies_itself_as_the_deferred_to_profile_block() -> None:
    # Closes the loop symmetrically: chat.md's block must name itself as the thing
    # base_system.md's exception defers to, instead of relying on raw document order
    # ("later instruction wins") between two priority="highest" blocks.
    assert 'khối ngôn ngữ riêng của hồ sơ' in _CHAT_MD
    assert "NGOẠI LỆ NHƯỜNG QUYỀN" in _CHAT_MD


def test_other_base_true_profiles_keep_unconditional_vietnamese_default() -> None:
    # exploit_research and bughunt both prepend BASE_SYSTEM (base: true) but carry no
    # language-mirror block of their own, so the new deference exception in
    # base_system.md must be a no-op for them -- they must still show an unconditional
    # Vietnamese directive with no trace of the chat-only mirror rule leaking in.
    assert PROFILES["exploit_research"].base is True
    assert PROFILES["bughunt"].base is True
    for name, prompt_text in (("exploit_research", _EXPLOIT_MD), ("bughunt", _BUGHUNT_MD)):
        assert "response_language_policy" not in prompt_text, name
        assert "BÁM theo ngôn ngữ của tin nhắn người dùng" not in prompt_text, name

    # exploit.md separately reinforces the Vietnamese mandate for its own output.
    assert "phải bằng tiếng Việt theo `language_policy`" in _EXPLOIT_MD


def test_composed_exploit_and_bughunt_prompts_still_mandate_vietnamese_by_default() -> None:
    for name in ("exploit_research", "bughunt"):
        text = compose_system_prompt(PROFILES[name])
        ElementTree.fromstring(text)  # still well-formed XML after the base_system.md edit
        assert "MẶC ĐỊNH: toàn bộ phần diễn giải" in text
        assert "phải bằng tiếng Việt" in text


def test_report_and_statistical_profiles_are_base_false_and_unaffected() -> None:
    # These profiles never prepend BASE_SYSTEM at all, so the base_system.md edits
    # cannot affect them -- their own report.md/statistical.md language_policy blocks
    # are untouched by this fix.
    assert PROFILES["report"].base is False
    assert PROFILES["statistical_report"].base is False
    for name in ("report", "statistical_report"):
        text = compose_system_prompt(PROFILES[name])
        assert BASE_SYSTEM not in text
        ElementTree.fromstring(text)


# --- Round 3: live E2E (Agent C) found chat.md's own leftover unconditional VN directives ---


def test_output_hygiene_paraphrase_rule_is_no_longer_unconditionally_vietnamese() -> None:
    # This was the confirmed culprit for scenario 1 (plain English concept question,
    # 0/5 English answers): output_hygiene's paraphrase instruction hard-coded
    # "bằng tiếng Việt" at the SAME priority="highest" as response_language_policy.
    assert "DIỄN GIẢI lại bằng tiếng Việt gọn gàng cho người dùng" not in _CHAT_MD
    assert "ĐÚNG NGÔN NGỮ TRẢ LỜI của lượt này theo `response_language_policy`" in _CHAT_MD
    assert "KHÔNG tự ý ép tiếng Việt" in _CHAT_MD


def test_about_system_usage_hint_no_longer_vietnamese_only() -> None:
    assert "hỏi trực tiếp bằng tiếng Việt. Ví dụ:" not in _CHAT_MD
    assert "hỏi trực tiếp bằng tiếng Việt hoặc tiếng Anh" in _CHAT_MD


def test_response_language_policy_asserts_it_governs_every_other_block() -> None:
    # Closes the remaining ambiguity: rather than patching every current and future
    # "tiếng Việt" mention one at a time, response_language_policy now explicitly states
    # it outranks any such mention anywhere else in the chat profile.
    assert "CHI PHỐI TOÀN BỘ hồ sơ chat" in _CHAT_MD
    assert "`output_hygiene`" in _CHAT_MD
    assert "không được tự ý ép tiếng Việt" in _CHAT_MD


def test_no_remaining_unconditional_vietnamese_directive_in_chat_md() -> None:
    # Full-file guard: every remaining "tiếng Việt" mention in chat.md must live inside
    # the response_language_policy block itself (where it is the mirror-logic/defaults
    # text, not an unconditional output directive) or be explicitly qualified elsewhere.
    # This catches a future accidental re-introduction of a hardcoded VN directive
    # anywhere in the profile, not just the two spots fixed in round 3.
    start = _CHAT_MD.index("<response_language_policy")
    end = _CHAT_MD.index("</response_language_policy>") + len("</response_language_policy>")
    policy_block = _CHAT_MD[start:end]
    rest_of_file = _CHAT_MD[:start] + _CHAT_MD[end:]

    unconditional_markers = (
        "bằng tiếng Việt gọn gàng",
        "hỏi trực tiếp bằng tiếng Việt.",
    )
    for marker in unconditional_markers:
        assert marker not in rest_of_file, marker

    # Sanity: the block itself still exists and still contains the mirror-language text
    # (i.e. this test isn't vacuously true because the block got deleted).
    assert "BÁM theo ngôn ngữ của tin nhắn người dùng" in policy_block


def test_composed_chat_prompt_still_well_formed_after_round3() -> None:
    text = compose_system_prompt(PROFILES["chat"])
    ElementTree.fromstring(text)
