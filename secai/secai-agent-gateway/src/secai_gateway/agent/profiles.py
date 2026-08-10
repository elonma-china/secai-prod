"""Declarative profile registry, loaded from ``prompts/profiles.yaml`` + the markdown
prompt files. Prompts are data, not code — edit them without touching Python.
"""
from __future__ import annotations

from dataclasses import dataclass
from importlib.resources import files
from typing import Any

import yaml

_PROMPTS = files("secai_gateway.prompts")


def _read(name: str) -> str:
    return _PROMPTS.joinpath(name).read_text(encoding="utf-8").strip()


BASE_SYSTEM = _read("base_system.md")


@dataclass(frozen=True, slots=True)
class Profile:
    name: str
    system: str
    tool_names: frozenset[str]
    max_turns: int = 8
    base: bool = True  # prepend the shared BASE_SYSTEM? False for pure text-transform profiles.


def compose_system_prompt(profile: Profile | None = None, *,
                          settings: Any | None = None) -> str:
    """Build one well-formed XML-like system document for Qwen's chat template.

    When ``settings.poc_exploit_enabled`` and profile is ``exploit_research``, append the
    lab KT-exploit instructions so the model may call ``poc_kt_exploit`` and must not
    invent scan output.
    """
    if profile is None:
        body = BASE_SYSTEM
    elif profile.base:
        body = f"{BASE_SYSTEM}\n\n{profile.system}"
    else:
        body = profile.system
    if (settings is not None
            and getattr(settings, "poc_exploit_enabled", False)
            and (getattr(settings, "poc_exploit_api_url", "") or "").strip()
            and profile is not None
            and profile.name == "exploit_research"):
        try:
            lab = _read("lab_kt_exploit.md")
            body = f"{body}\n\n{lab}"
        except (FileNotFoundError, OSError):
            pass
    return f"<system_prompt>\n{body}\n</system_prompt>"


def _load_profiles() -> dict[str, Profile]:
    spec = yaml.safe_load(_PROMPTS.joinpath("profiles.yaml").read_text(encoding="utf-8"))
    return {
        name: Profile(
            name=name,
            system=_read(cfg["prompt"]),
            tool_names=frozenset(cfg.get("tools", [])),
            max_turns=int(cfg.get("max_turns", 8)),
            base=bool(cfg.get("base", True)),
        )
        for name, cfg in spec.items()
    }


PROFILES = _load_profiles()


def registry_for(profile_name: str) -> frozenset[str]:
    return PROFILES[profile_name].tool_names
