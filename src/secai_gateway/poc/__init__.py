"""Unified PoC catalog: normalized sources and read-only Linch tools."""
from .backend import LocalPocBackend, PocBadRequest, PocError, PocForbidden, PocNotFound
from .catalog import PocCatalog
from .consolidate import consolidate_poc
from .fidelity import cli_surface
from .slicer import prune_evidence, safe_slice, slice_entry, sniff_language
from .inject import (
    canonical_block,
    enforce_reference_code,
    enforce_section1,
    force_no_poc_notice,
    has_real_code_evidence,
    has_synthesis_evidence,
    override_section3,
    render_files,
    sanitize_airgap_runguide,
    sanitize_poc_display,
    section1_has_body,
    strip_system_narration,
    strip_tool_name_mentions,
)
from .kt_exploit import parse_exploit_intent, parse_pocsuite_verdict, run_kt_exploit, strip_ansi
from .models import FetchResult, NormalizedPoc, SearchHit
from .normalize import normalize_cve_ids
from .prefetch import (
    clear_session_coverage,
    clear_session_cve_known,
    extract_cve_id,
    get_session_coverage,
    get_session_cve_known,
    merge_evidence,
    prefetch_evidence,
    set_session_coverage,
    set_session_cve_known,
)
from .tools import build_poc_tools

__all__ = [
    "LocalPocBackend",
    "PocError",
    "PocBadRequest",
    "PocForbidden",
    "PocNotFound",
    "PocCatalog",
    "consolidate_poc",
    "cli_surface",
    "prune_evidence",
    "safe_slice",
    "sniff_language",
    "slice_entry",
    "FetchResult",
    "NormalizedPoc",
    "SearchHit",
    "build_poc_tools",
    "enforce_reference_code",
    "enforce_section1",
    "force_no_poc_notice",
    "has_real_code_evidence",
    "has_synthesis_evidence",
    "section1_has_body",
    "render_files",
    "canonical_block",
    "override_section3",
    "normalize_cve_ids",
    "prefetch_evidence",
    "extract_cve_id",
    "merge_evidence",
    "strip_system_narration",
    "strip_tool_name_mentions",
    "sanitize_airgap_runguide",
    "sanitize_poc_display",
    "set_session_coverage",
    "get_session_coverage",
    "clear_session_coverage",
    "set_session_cve_known",
    "get_session_cve_known",
    "clear_session_cve_known",
    "run_kt_exploit",
    "parse_exploit_intent",
    "parse_pocsuite_verdict",
    "strip_ansi",
]
