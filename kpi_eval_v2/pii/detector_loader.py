"""Load the REAL production PII/secret detector — read-only, never modified.

Which module is "the real production detector" for the R2-PII-* KPIs (span-level,
per-type Email/IP·Phone·APIKey·Password) is NOT ``security.py::require_public_input``
(that is the hard-422 gate wired into ``/ai/chat`` — it only matches *secret* shapes:
private keys, ~30 vendor API-key formats, ``password=`` assignments, and long numeric
identifiers. It has no email regex and no Vietnamese-phone regex, so it cannot populate
a per-type Email/Phone/Key/Password KPI at all).

The module that actually matches this KPI's taxonomy — and that the acceptance dossier's
own R-SEC-PII KPI (F1 98.06%, per ``docs/nghiem-thu-showcase``) is measured against — is
``detector.py`` (regex rules + an entropy classifier for high-entropy secrets), loaded
dynamically by the live showcase endpoint ``POST /showcase-api/sanitize-demo``
(``src/secai_gateway/api/showcase/sanitize_demo.py``) from
``Settings.showcase_sanitizer_detector`` (see ``src/secai_gateway/config.py``). That is
the SAME file — confirmed byte-identical (``diff`` exit 0, 2026-09-20) — as
``kpi_bench/sanitizer/detector.py``.

This loader mirrors ``sanitize_demo.py``'s own loading technique (``importlib`` from a
file path) so we evaluate the literal file production points at, not a hand-copied
stand-in. It tries the dossier path first (what ``Settings.showcase_sanitizer_detector``
defaults to) and falls back to the gateway-repo mirror if that path is unavailable in
this environment (e.g. a checkout without the dossier).

IMPORTANT: this file only *loads* the detector; it must never be edited to make gold
records pass. The independence between this KPI's gold set (``datasets/pii_gold_v2.jsonl``,
hand-authored — see its own docstring/header) and the detector's regex is the whole point.
"""
from __future__ import annotations

import importlib.util
import pathlib
from typing import Any

from .. import config

# Preferred: the exact path production's Settings.showcase_sanitizer_detector points at
# (the acceptance dossier's original evaluation code — what the contract's R-SEC-PII KPI
# was measured against).
_DOSSIER_PATH = pathlib.Path(
    "/home/ict/AIEngines/secai/HO-SO-GOC-NGHIEM-THU-AI/03. Kpi_results/"
    "Santization/Evaluation_code/detector.py"
)
# Fallback: byte-identical mirror checked into the gateway repo (kpi_bench/sanitizer/).
_MIRROR_PATH = config.GATEWAY_ROOT / "kpi_bench" / "sanitizer" / "detector.py"


def detector_path() -> pathlib.Path:
    if _DOSSIER_PATH.exists():
        return _DOSSIER_PATH
    if _MIRROR_PATH.exists():
        return _MIRROR_PATH
    raise FileNotFoundError(
        f"real production detector.py not found at dossier path ({_DOSSIER_PATH}) "
        f"or gateway mirror ({_MIRROR_PATH})"
    )


_cached: Any = None


def load_detector() -> Any:
    """Import and return the detector module (has ``.detect(text) -> list[span]``)."""
    global _cached
    if _cached is not None:
        return _cached
    path = detector_path()
    spec = importlib.util.spec_from_file_location("secai_kpi2_real_detector", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"could not load spec for {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    _cached = mod
    return _cached
