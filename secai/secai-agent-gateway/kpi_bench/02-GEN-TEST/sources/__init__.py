"""Các nguồn dữ liệu để sinh bộ test độc lập.

Mỗi module cung cấp `fetch(...)` trả về danh sách bản ghi CVE đã CHUẨN HOÁ:

    {
      "cve_id":   "CVE-2024-3272",      # bắt buộc
      "severity": "CRITICAL",           # CRITICAL|HIGH|MEDIUM|LOW
      "cvss_score": 9.8,
      "cvss_vector": "CVSS:3.1/AV:N/...",
      "year": "2024",
      "kev": True,
      "cwe_refs": ["CWE-798"],
      "products": ["dns-320l"],
      "text": "<mô tả gốc, tiếng Anh>",   # dùng làm 'payload nguồn' khi validate
      "capec_entries": [], "mitre_techniques": [],
      "_source": "qdrant|public|customer"
    }
"""
from . import from_qdrant, from_public, from_customer  # noqa: F401

__all__ = ["from_qdrant", "from_public", "from_customer"]
