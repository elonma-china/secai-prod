"""Air-gap egress backstop.

The gateway's air-gap guarantee has two layers (see ``config.Settings._enforce_airgap``):
layer 1 coerces every internet-feature flag off so the tools are never built and the
deterministic web steps skip; this module is layer 2 — a hard refusal wired into every
low-level *public-content* fetcher, so that even a code path which bypassed the flags
(a bug, a future feature, a direct call in a test/script) still cannot reach the internet
while air-gap is on.

Deliberately scoped to PUBLIC-CONTENT fetch only (web_fetch / fetch_reference / websearch).
The internal service clients — Qdrant, Neo4j, Redis, and the LLM /
embedding / reranker serving — are NOT guarded here: air-gap means "no internet", not "no
network", and those endpoints are expected to be in-network at deploy time.
"""
from __future__ import annotations

from .config import get_settings


class AirgapViolation(ValueError):
    """Raised when an outbound internet fetch is attempted while air-gap mode is on.

    Subclasses ``ValueError`` on purpose: the web_fetch / fetch_reference tool wrappers
    already catch ``ValueError`` and turn it into a clean, model-visible "rejected" tool
    result, so an air-gap refusal degrades exactly like any other policy denial instead of
    crashing the run.
    """


def airgap_enabled() -> bool:
    """True when the gateway is in air-gap mode (no outbound internet permitted)."""
    return bool(getattr(get_settings(), "airgap", True))


def assert_online_fetch_allowed(url: str) -> None:
    """Refuse an outbound public-internet fetch when air-gap mode is on.

    Call this at the top of every function that would fetch public-internet content,
    before any socket work. Raises :class:`AirgapViolation` (a ``ValueError``) so existing
    ``except ValueError`` handlers surface it as a policy denial.
    """
    if airgap_enabled():
        raise AirgapViolation(
            f"air-gap mode is ON — outbound internet fetch is blocked (url={url!r}). "
            "Set AIRGAP=false only on an internet-connected build/sync machine."
        )
