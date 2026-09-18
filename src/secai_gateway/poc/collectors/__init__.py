"""PoC catalog collectors — one module per source, each yielding NormalizedPoc.

Offline/structured (artifact in the clone): exploitdb, metasploit, nuclei, vulhub, ghsa.
Link-only (sandbox pre-fetch into the content store): poc_github.
See docs/unified-poc-db-design.md.
"""
from .exploitdb import ExploitDbCollector
from .ghsa import GhsaCollector
from .metasploit import MetasploitCollector
from .nuclei import NucleiCollector
from .poc_github import PocGithubCollector
from .trickest import TrickestCollector
from .vulhub import VulhubCollector

__all__ = ["ExploitDbCollector", "MetasploitCollector", "NucleiCollector",
           "VulhubCollector", "GhsaCollector", "PocGithubCollector", "TrickestCollector"]
