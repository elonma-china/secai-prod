"""Unified PoC catalog: multi-source normalized store + 2-tool (search/fetch) linch tools.

Read-only, never executes an artifact. Returns real source code only for confirmed-
redistributable sources (exploitdb/metasploit/nuclei/ghsa), always grounded in the actual
fetched artifact, never model-invented — see docs/unified-poc-db-design.md."""

from .backend import LocalPocBackend, PocError
from .catalog import PocCatalog
from .inject import enforce_reference_code, force_no_poc_notice, render_files, strip_system_narration
from .models import FetchResult, NormalizedPoc, SearchHit
from .prefetch import merge_evidence, prefetch_evidence
from .tools import build_poc_tools

__all__ = ["LocalPocBackend", "PocError", "PocCatalog", "FetchResult",
           "NormalizedPoc", "SearchHit", "build_poc_tools",
           "enforce_reference_code", "force_no_poc_notice", "render_files",
           "prefetch_evidence", "merge_evidence", "strip_system_narration"]
