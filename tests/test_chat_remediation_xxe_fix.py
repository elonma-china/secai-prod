"""Regression suite for the chat XXE-remediation bug (CQ2 / Bug #5, 2026-09-22).

Bug: asking "XXE là gì và cách phòng chống?" made the model hand back a Python/lxml
"patch" that was itself unsafe ~40% of the time — ``etree.XMLParser(dtd_validation=False,
load_dtd=False, no_network=False)``, where ``no_network=False`` STILL lets the parser
reach the network (the exact SSRF path XXE defense is supposed to close). Every run also
omitted ``resolve_entities=False`` and never suggested a safe standard library. A dev
copying this "fix" verbatim would still be vulnerable.

Root cause: knowledge/prompt gap, not a KB data gap — ``prompts/chat.md`` had no rule
telling the model what a SAFE XXE remediation snippet must contain, so temperature-driven
sampling sometimes produced the unsafe/hallucinated-parameter variant.

Fix (prompt-only, ``prompts/chat.md``): new ``<remediation_code_policy>`` block requiring,
for Python: (1) stdlib ``xml.etree.ElementTree`` users be pointed at ``defusedxml.
ElementTree`` as the primary fix, (2) lxml users NOT be pointed at ``defusedxml.lxml``
(deprecated since defusedxml 0.7, does not fully protect lxml) but instead told to
configure ``lxml.etree.XMLParser`` with ``resolve_entities=False`` + ``no_network=True``
+ ``load_dtd=False`` + ``dtd_validation=False`` + ``huge_tree=False`` (the last guards
billion-laughs/entity-expansion DoS), (3) ``no_network=False`` never be used, (4) no
invented parameter names (e.g. ``no_dtd``/``no_net`` are not real lxml kwargs). Round 2
(Opus review nits) added: (5) short cross-language guidance — Java
``disallow-doctype-decl``, PHP ``LIBXML_NONET`` (not the PHP-8-deprecated
``libxml_disable_entity_loader``), Node libxmljs must not set ``noent: true``. And (6)
the same secure-by-default expectation generalizes to remediation code for other
vulnerability classes (SQLi, SSRF, deserialization, path traversal, command injection).

This suite is a cheap prompt-content guard (no LLM call) — it catches an accidental
future revert/edit of the new block, following the pattern in
``test_chat_poc_drilldown_regression.py``'s UC5. Live verification (≥12 calls for Python,
≥3 per other language, against a running instance, before/after) was done manually — see
the Bug #5 fix report.
"""
from __future__ import annotations

from xml.etree import ElementTree

from secai_gateway.agent.profiles import PROFILES, compose_system_prompt

_CHAT_MD = PROFILES["chat"].system


def test_chat_md_has_remediation_code_policy_block() -> None:
    assert "<remediation_code_policy" in _CHAT_MD
    assert "</remediation_code_policy>" in _CHAT_MD


def test_rule_stdlib_points_to_defusedxml_elementtree() -> None:
    assert "defusedxml.ElementTree" in _CHAT_MD
    assert "ƯU TIÊN khuyến nghị thay bằng `defusedxml.ElementTree`" in _CHAT_MD


def test_rule_lxml_does_not_recommend_deprecated_defusedxml_lxml() -> None:
    # NIT-1 (round 2): defusedxml.lxml is deprecated since defusedxml 0.7 and doesn't
    # fully protect lxml — must not be the primary fix recommended for lxml users.
    assert "KHÔNG khuyến nghị `defusedxml.lxml` làm giải pháp chính" in _CHAT_MD
    assert "DEPRECATED" in _CHAT_MD


def test_rule_required_safe_lxml_parser_kwargs() -> None:
    for kwarg in (
        "resolve_entities=False", "no_network=True", "load_dtd=False",
        "dtd_validation=False", "huge_tree=False",  # huge_tree: NIT-2 (round 2, billion-laughs DoS)
    ):
        assert kwarg in _CHAT_MD, f"missing required-safe-kwarg guidance: {kwarg}"


def test_rule_forbids_no_network_false() -> None:
    assert "TUYỆT ĐỐI KHÔNG viết `no_network=False`" in _CHAT_MD


def test_rule_forbids_invented_lxml_param_names() -> None:
    assert "no_dtd" in _CHAT_MD and "no_net" in _CHAT_MD
    assert "tên đúng duy nhất là `no_network`" in _CHAT_MD


def test_rule_cross_language_xxe_guidance() -> None:
    # NIT-3 (round 2): close the multi-language gap — Java/PHP/Node get short,
    # principle-level guidance too, not just Python.
    assert "disallow-doctype-decl" in _CHAT_MD  # Java JAXP
    assert "LIBXML_NONET" in _CHAT_MD  # PHP
    assert "libxml_disable_entity_loader" in _CHAT_MD and "PHP 8" in _CHAT_MD  # PHP<8 caveat
    assert "noent" in _CHAT_MD  # Node libxmljs


def test_rule_generalizes_to_other_vulnerability_classes() -> None:
    assert "Áp dụng chung cho MỌI lỗ hổng khác" in _CHAT_MD
    for hint in ("SQLi", "SSRF", "command injection"):
        assert hint in _CHAT_MD


def test_composed_chat_prompt_still_well_formed_xml() -> None:
    # The new block must not break the XML well-formedness Qwen's chat template and
    # test_prompts.py rely on.
    text = compose_system_prompt(PROFILES["chat"])
    root = ElementTree.fromstring(text)
    assert any(el.tag == "remediation_code_policy" for el in root.iter())
