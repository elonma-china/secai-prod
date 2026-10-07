"""CVE discovery: finding a CVE from a description ("CVE nào của Citrix cho phép chiếm phiên đăng nhập?").

Two measured misses behind this module:
  * the record is filed under a different vendor name than the one people use (Citrix Bleed is stored as
    "NetScaler ADC / Gateway"), so neither dense nor BM25 sees the user's word in it;
  * a plain description query returns arbitrary same-topic CVEs of 760k, while what the asker means is
    nearly always one of the actively exploited ones (CISA KEV, ~1.7k).
``expand_discovery_query`` fixes the first on the retrieval side, ``DISCOVER_KEV_K`` feeds the second:
KEV-only candidates join the rerank pool and the reranker decides whether they are relevant.
"""
from __future__ import annotations

import re

from .vi_en import fold

DISCOVER_KEV_K = 8  # extra KEV candidates per discovery query (the pool is 20, the rerank window grows to 28)
DISCOVER_RERANK_WINDOW = 28

# folded user wording → names the CVE records actually use. Only vendor/product renames and stable
# short names: anything that names a single CVE belongs in the alias data (alias_enrich.py), not here.
_SYNONYMS: tuple[tuple[str, str], ...] = (
    (r"\bcitrix\b", "NetScaler ADC Gateway"),
    (r"\bnetscaler\b", "Citrix"),
    (r"\bpulse secure\b|\bpulse connect\b", "Ivanti Connect Secure"),
    (r"\bpalo alto\b|\bglobalprotect\b", "PAN-OS GlobalProtect"),
    (r"\bfortinet\b|\bfortigate\b", "FortiOS FortiGate"),
    (r"\bmicrosoft exchange\b|\bexchange server\b", "Exchange Server"),
    (r"\bstruts ?2?\b", "Apache Struts"),
    (r"\bf5\b|\bbig-?ip\b", "F5 BIG-IP"),
    (r"\bvmware\b|\besxi\b|\bvcenter\b", "VMware ESXi vCenter"),
    (r"\bsonicwall\b", "SonicWall SMA SonicOS"),
    (r"\bcisco\b", "Cisco IOS XE ASA"),
    (r"\bconfluence\b|\bjira\b|\batlassian\b", "Atlassian Confluence"),
)
# folded Vietnamese effect → English record vocabulary
_EFFECTS: tuple[tuple[str, str], ...] = (
    (r"chiem (?:quyen )?phien|cuop phien|phien dang nhap", "session hijacking session token"),
    (r"thuc thi (?:ma|lenh) (?:tu xa|bat ky)|chay (?:ma|lenh) tu xa", "remote code execution"),
    (r"vuot (?:qua )?xac thuc|bo qua xac thuc", "authentication bypass"),
    (r"leo thang dac quyen|nang quyen", "privilege escalation"),
    (r"lo thong tin|ro ri thong tin|doc (?:du lieu|file) tuy y", "information disclosure"),
    (r"tu choi dich vu", "denial of service"),
    (r"chen (?:lenh|ma) he dieu hanh", "OS command injection"),
    (r"chen (?:cau lenh )?sql", "SQL injection"),
    (r"duyet thu muc|path traversal", "path traversal directory traversal"),
    (r"giai ma hoa du lieu khong tin cay|deserialization|giai tuan tu", "deserialization of untrusted data"),
)
_COMPILED = tuple((re.compile(p), en) for p, en in _SYNONYMS + _EFFECTS)


def expand_discovery_query(query: str) -> str:
    """The query plus the record vocabulary for the vendor/effect words in it; unchanged if none."""
    folded = fold(query)
    extra = [en for rx, en in _COMPILED if rx.search(folded) and en.lower() not in folded]
    return f"{query} {' '.join(dict.fromkeys(extra))}" if extra else query
