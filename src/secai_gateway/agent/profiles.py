"""Declarative profile registry, loaded from ``prompts/profiles.yaml`` + the markdown
prompt files. Prompts are data, not code — edit them without touching Python.
"""
from __future__ import annotations

from dataclasses import dataclass
from importlib.resources import files

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


def compose_system_prompt(profile: Profile | None = None) -> str:
    """Build one well-formed XML-like system document for Qwen's chat template."""
    if profile is None:
        body = BASE_SYSTEM
    elif profile.base:
        body = f"{BASE_SYSTEM}\n\n{profile.system}"
    else:
        body = profile.system
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
