from __future__ import annotations

from xml.etree import ElementTree

import pytest

from secai_gateway.agent.profiles import BASE_SYSTEM, PROFILES, compose_system_prompt


@pytest.mark.parametrize("profile_name", PROFILES)
def test_composed_system_prompts_are_well_formed_xml(profile_name: str) -> None:
    profile = PROFILES[profile_name]
    text = compose_system_prompt(profile)

    root = ElementTree.fromstring(text)

    assert root.tag == "system_prompt"
    assert (BASE_SYSTEM in text) is profile.base


def test_custom_prompts_do_not_shadow_qwen_protocol_tags() -> None:
    reserved = {"think", "tool_call", "function", "parameter"}

    for profile in PROFILES.values():
        root = ElementTree.fromstring(compose_system_prompt(profile))
        assert not ({element.tag for element in root.iter()} & reserved)
