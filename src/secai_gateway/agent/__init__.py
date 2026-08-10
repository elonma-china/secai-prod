"""Linch agent wiring: profile registry, provider selection, factory and runner."""

from .profiles import PROFILES, BASE_SYSTEM, Profile, registry_for
from .runner import CompletionRunner, LinchRunner

__all__ = ["PROFILES", "BASE_SYSTEM", "Profile", "registry_for", "CompletionRunner", "LinchRunner"]
