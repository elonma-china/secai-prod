"""Unified PoC catalog: multi-source normalized store + 2-tool (search/fetch) linch tools.

Read-only, never executes an artifact. Returns real source code only for confirmed-
redistributable sources (exploitdb/metasploit/nuclei/ghsa), always grounded in the actual
fetched artifact, never model-invented — see docs/unified-poc-db-design.md."""

from .backend import LocalPocBackend, PocError
from .catalog import PocCatalog
from .models import FetchResult, NormalizedPoc, SearchHit
from .tools import build_poc_tools

__all__ = ["LocalPocBackend", "PocError", "PocCatalog", "FetchResult",
           "NormalizedPoc", "SearchHit", "build_poc_tools"]
