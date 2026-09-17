#!/usr/bin/env python3
"""detector.py — Phát hiện & làm sạch PII/Secret trước inference (R-SEC-PII).

Hai tầng (theo hợp đồng "Regex-based rules + Model-based classification"):
  1. Regex rules — bắt định dạng chuẩn: email, IP, số điện thoại, API key, password hardcoded.
  2. Entropy classifier — bắt secret/khóa lạ không khớp regex: token entropy cao trong ngữ cảnh
     gán giá trị (key/token/secret = <chuỗi ngẫu nhiên entropy cao>). Đây là tầng "model-based"
     thống kê (giống secret-scanner truffleHog/gitleaks), không phụ thuộc ML deps.

Stdlib-only. Dùng cho cả detect (eval) lẫn redact (làm sạch).
"""
import math
import re

# ---------------- Regex rules (mỗi type: pattern + nhãn) ----------------
EMAIL = re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")
IPV4 = re.compile(r"\b(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)\b")
# IPv6 thật: phải có "::" hoặc ≥7 nhóm hex (tránh nuốt nhầm timestamp HH:MM:SS = 3 nhóm)
IPV6 = re.compile(r"\b(?:[A-Fa-f0-9]{1,4}:){7}[A-Fa-f0-9]{1,4}\b|\b(?:[A-Fa-f0-9]{1,4}:){1,7}:(?:[A-Fa-f0-9]{1,4})?\b")
# số điện thoại VN (0xxxxxxxxx / +84xxxxxxxxx) — chặn khi nằm trong id có dấu '-' (vd TXN-2024-...)
PHONE = re.compile(r"(?<![\w.\-])(?:\+?84|0)(?:\d[\s.]?){8,10}\d(?![\w\-])")
# API keys — các nhà cung cấp phổ biến
API_KEYS = [
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),                          # AWS access key
    re.compile(r"\bghp_[A-Za-z0-9]{36}\b"),                       # GitHub PAT
    re.compile(r"\bgh[ousr]_[A-Za-z0-9]{36}\b"),                  # GitHub other
    re.compile(r"\bAIza[0-9A-Za-z\-_]{35}\b"),                    # Google API key
    re.compile(r"\bxox[baprs]-[0-9A-Za-z\-]{10,48}\b"),           # Slack token
    re.compile(r"\bsk-[A-Za-z0-9]{20,}\b"),                       # OpenAI/Stripe-style
    re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\b"),  # JWT
]
# password/secret hardcoded: key gợi ý (cho phép tiền tố như DB_, SMTP_) + dấu gán + giá trị
SECRET_ASSIGN = re.compile(
    r"(?i)\b(\w*(?:password|passwd|pwd|pass|secret|api[_\-]?key|apikey|token|access[_\-]?key|"
    r"private[_\-]?key))\b\s*[:=]\s*[\"']?([^\s\"',;]{6,})[\"']?")

# Entropy classifier: token "lạ" entropy cao đứng sau key gợi ý
ASSIGN_CTX = re.compile(
    r"(?i)\b(key|token|secret|pass|pwd|credential|auth)\w*\b\s*[:=]\s*[\"']?([A-Za-z0-9+/=_\-]{16,})[\"']?")

PLACEHOLDERS = {"password", "changeme", "your_password", "xxxx", "example", "redacted",
                "placeholder", "<password>", "******", "********", "none", "null", "todo"}
# substring → coi là placeholder (vd "<your_password_here>", "your_api_key")
PLACEHOLDER_SUBSTR = ("your_", "_here", "<", "example", "changeme", "xxxx", "placeholder", "redacted", "...")


def is_placeholder(v):
    lv = v.lower()
    return lv in PLACEHOLDERS or any(sub in lv for sub in PLACEHOLDER_SUBSTR)


def shannon_entropy(s):
    if not s:
        return 0.0
    freq = {}
    for c in s:
        freq[c] = freq.get(c, 0) + 1
    n = len(s)
    return -sum((c / n) * math.log2(c / n) for c in freq.values())


def detect(text):
    """Trả list span: {type, value, start, end, source} (regex|entropy)."""
    spans = []

    def add(t, m, gi=0, source="regex"):
        val = m.group(gi) if gi else m.group(0)
        spans.append({"type": t, "value": val, "start": m.start(gi) if gi else m.start(),
                      "end": m.end(gi) if gi else m.end(), "source": source})

    for m in EMAIL.finditer(text):
        add("email", m)
    for m in IPV4.finditer(text):
        add("ip", m)
    for m in IPV6.finditer(text):
        add("ip", m)
    for m in PHONE.finditer(text):
        # tránh nuốt nhầm CVE năm / số version: loại nếu nằm trong CVE-...
        ctx = text[max(0, m.start() - 4):m.start()]
        if "CVE" in ctx.upper() or "CWE" in ctx.upper():
            continue
        add("phone", m)
    for rx in API_KEYS:
        for m in rx.finditer(text):
            add("api_key", m)
    seen = {(s["type"], s["start"]) for s in spans}
    for m in SECRET_ASSIGN.finditer(text):
        val = m.group(2)
        if is_placeholder(val):
            continue
        key = m.group(1).lower()
        t = "api_key" if ("key" in key or "token" in key or "secret" in key) else "password"
        if ("api_key", m.start(2)) in seen or ("password", m.start(2)) in seen:
            continue
        spans.append({"type": t, "value": val, "start": m.start(2), "end": m.end(2), "source": "regex"})
    # Entropy classifier: secret lạ entropy>3.5 trong ngữ cảnh gán, chưa bắt được
    covered = [(s["start"], s["end"]) for s in spans]
    for m in ASSIGN_CTX.finditer(text):
        val = m.group(2)
        if is_placeholder(val) or len(val) < 16:
            continue
        if shannon_entropy(val) < 3.5:
            continue
        s0, e0 = m.start(2), m.end(2)
        if any(a <= s0 < b for a, b in covered):
            continue
        spans.append({"type": "api_key", "value": val, "start": s0, "end": e0, "source": "entropy"})
    # dedup theo (start,end) giữ type đầu
    out, used = [], set()
    for s in sorted(spans, key=lambda x: (x["start"], -(x["end"]))):
        k = (s["start"], s["end"])
        if k in used:
            continue
        used.add(k)
        out.append(s)
    return out


def redact(text):
    """Làm sạch: thay mỗi span bằng [REDACTED_<TYPE>]."""
    spans = sorted(detect(text), key=lambda x: x["start"], reverse=True)
    for s in spans:
        text = text[:s["start"]] + f"[REDACTED_{s['type'].upper()}]" + text[s["end"]:]
    return text


if __name__ == "__main__":
    sample = ('DB_PASSWORD = "Sup3rS3cret_2024!" và liên hệ admin@corp.vn, server 192.168.10.5, '
              'hotline 0912345678, AWS AKIAIOSFODNN7EXAMPLE, api_key=sk-abcd1234efgh5678ijkl')
    for s in detect(sample):
        print(f"  {s['type']:10} [{s['source']}] {s['value']}")
    print("\nREDACTED:\n", redact(sample))
