#!/usr/bin/env python3
"""Grader tất định cho từng chức năng — black-box, dựa trên contract THẬT của gateway
(metadata.section1_tier, banner text, EXPLOIT_CVE_GUIDE, ...), không phải suy đoán.

Các hằng số dưới đây COPY nguyên văn từ source (không import trực tiếp vì `linch` chỉ có
trong venv của container, không có trên host chạy eval). Nguồn — kiểm tra lại nếu eval bắt
đầu fail hàng loạt sau khi đổi prompt/constant:
  - EXPLOIT_CVE_GUIDE       <- src/secai_gateway/api/routes.py
  - _SYNTHESIS_BANNER       <- src/secai_gateway/poc/inject.py
  - _SECTION1_HEADING       <- src/secai_gateway/poc/inject.py
  - _NO_POC_NOTE            <- src/secai_gateway/poc/inject.py
  - TOOL_NAMES (leak check) <- src/secai_gateway/agent/profiles.py (prompts/profiles.yaml)
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

EXPLOIT_CVE_GUIDE = (
    "Vui lòng nhập mã CVE cần phân tích khai thác, ví dụ: CVE-2026-42533. "
    "Bạn cũng có thể kèm từ khoá sản phẩm hoặc loại lỗ hổng để tìm PoC công khai."
)
SYNTHESIS_BANNER = (
    "⚠️ SYNTHESIZED — CẢNH BÁO: CODE DO AI TỰ TỔNG HỢP DỰA TRÊN BẰNG CHỨNG NỘI BỘ, "
    "KHÔNG PHẢI PoC ĐÃ FETCH THẬT, CHƯA ĐƯỢC THỰC THI HAY XÁC THỰC."
)
SECTION1_HEADING = "## 1. Mã Khai thác (PoC) và Payload"
NO_POC_NOTE = "Chưa có thông tin PoC được xác thực trong dữ liệu nội bộ cho lỗ hổng này."

VALID_TIERS = {
    "real", "real_consolidated", "real_pruned", "synthesized_readme", "synthesized_cve",
    "synthesis_failed", "not_found",
}
SYNTH_TIERS = {"synthesized_readme", "synthesized_cve"}
REAL_TIERS = {"real", "real_consolidated", "real_pruned"}

# Tên tool nội bộ KHÔNG được rò ra văn bản hiển thị (đã qua strip_tool_name_mentions —
# thấy lại nghĩa là backstop bị sót).
TOOL_NAMES = [
    "poc_search", "poc_fetch", "poc_verify", "poc_scan", "poc_exec", "poc_kt_exploit",
    "qdrant_search", "neo4j_search", "web_fetch", "web_search", "fetch_reference",
]
STREAM_ERROR_MESSAGE = (
    "Xin lỗi, trợ lý đang gặp sự cố tạm thời nên chưa thể trả lời. Vui lòng thử lại sau giây lát."
)

# #{2,6} (>=2 hash) khớp đúng quy ước heading thật của app (poc/inject.py's _S3 dùng
# #{2,6}) -- KHÔNG dùng #{1,6} vì PoC script tải về có thể có comment dạng "# 1. ..."
# (single-#) mà một regex lỏng sẽ nhầm là heading, cắt sai biên Mục 1/2/3.
_SECTION_HDR_RE = re.compile(r"^[ \t]*#{2,6}[ \t]*([123])\.[ \t]*\S")
_CODE_FENCE_RE = re.compile(r"```.*?```", re.S)
# Khớp CHÍNH XÁC scope thật của sanitize_airgap_runguide (poc/inject.py) — sanitizer đó
# CHỈ rewrite URL trỏ code-host (github/gitlab/raw/exploit-db/...) hoặc đuôi file mã nguồn,
# và CỐ Ý giữ nguyên URL target dạng "https://exchange.example.com" ("Target URLs ... are
# preserved (not a code host, no source ext)" — chính docstring của hàm). Một regex "bất kỳ
# http(s)://" sẽ test một bất biến CHẶT HƠN sản phẩm thực sự cam kết -> false positive.
_URL_RE = re.compile(r"https?://[^\s`)\]<>\"']+", re.I)
_CODE_HOST_RE = re.compile(
    r"(?:raw\.|gist\.|objects\.|codeload\.)?(?:github|githubusercontent|gitlab|bitbucket|gitee)"
    r"\.com|(?:www\.)?exploit-db\.com|packetstormsecurity\.com", re.I)
_SRC_EXTS = {"yaml", "yml", "py", "rb", "sh", "ps1", "pl", "php", "js", "ts", "go", "rs",
             "c", "cpp", "cc", "h", "java", "json", "nasl", "rc"}
# Footer trích dẫn nguồn do HỆ THỐNG tự chèn cho tier synthesized (_append_synthesis_footer)
# — chạy SAU sanitize_airgap_runguide, không phải văn bản model -> loại khỏi check.
_SYNTHESIS_FOOTER_MARKER = "Mã PoC/hướng dẫn ở trên là bản GỢI Ý do AI tự tổng hợp"
_VN_DIACRITIC_RE = re.compile(
    "[àáảãạăằắẳẵặâầấẩẫậèéẻẽẹêềếểễệìíỉĩịòóỏõọôồốổỗộơờớởỡợùúủũụưừứửữựỳýỷỹỵđ"
    "ÀÁẢÃẠĂẰẮẲẴẶÂẦẤẨẪẬÈÉẺẼẸÊỀẾỂỄỆÌÍỈĨỊÒÓỎÕỌÔỒỐỔỖỘƠỜỚỞỠỢÙÚỦŨỤƯỪỨỬỮỰỲÝỶỸỴĐ]"
)


@dataclass
class Grade:
    sample_id: str
    passed: bool
    checks: dict = field(default_factory=dict)
    reasons: list = field(default_factory=list)
    warnings: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"sample_id": self.sample_id, "passed": self.passed, "checks": self.checks,
                "reasons": self.reasons, "warnings": self.warnings}


def _fail(g: Grade, check: str, reason: str) -> None:
    g.checks[check] = False
    g.passed = False
    g.reasons.append(reason)


def _ok(g: Grade, check: str) -> None:
    g.checks[check] = True


def _section_spans(text: str) -> dict[int, tuple[int, int]]:
    """{1: (start,end), 2: (start,end), 3: (start,end)} theo heading '## N.' tìm được.

    Bỏ qua match NẰM TRONG fenced code block (```...```) — PoC script thật (Mục 1) có thể
    chứa comment dạng "# 1. ..."/"## 2. ..." của chính nó (vd hướng dẫn setup trong code
    gốc), regex heading không nhận biết fence sẽ cắt nhầm biên Mục 1 và khiến code hợp lệ
    (chứa URL/namespace) bị coi là "ngoài Mục 1" -> false positive airgap-leak."""
    hits: list[tuple[int, int]] = []
    pos = 0
    in_fence = False
    for line in text.split("\n"):
        if line.strip().startswith("```"):
            in_fence = not in_fence
        elif not in_fence:
            m = _SECTION_HDR_RE.match(line)
            if m:
                hits.append((int(m.group(1)), pos))
        pos += len(line) + 1
    spans: dict[int, tuple[int, int]] = {}
    for i, (num, start) in enumerate(hits):
        end = hits[i + 1][1] if i + 1 < len(hits) else len(text)
        if num not in spans:  # giữ occurrence ĐẦU nếu model lặp heading
            spans[num] = (start, end)
    return spans


def _no_tool_leak(text: str) -> str | None:
    for name in TOOL_NAMES:
        if re.search(rf"\b{re.escape(name)}\b", text):
            return name
    return None


def _airgap_leak_outside_section1(text: str) -> str | None:
    """URL trỏ CODE HOST (github/gitlab/raw/exploit-db/...) hoặc file mã nguồn, rò rỉ trong
    Mục 2/3 (run-guide) — trả về URL vi phạm đầu tiên, hoặc None nếu sạch. Loại trừ: Mục 1
    (code PoC thật có thể chứa URL của chính nó/target — không phải phạm vi sanitizer), và
    footer trích dẫn do hệ thống tự chèn cho tier synthesized (chạy SAU sanitizer, không
    phải văn bản model). URL target thường (vd "https://exchange.example.com") được sản
    phẩm CỐ Ý giữ nguyên nên KHÔNG bị coi là leak ở đây."""
    spans = _section_spans(text)
    check_text = text
    if 1 in spans:
        check_text = text[:spans[1][0]] + text[spans[1][1]:]
    footer_idx = check_text.find(_SYNTHESIS_FOOTER_MARKER)
    if footer_idx != -1:
        check_text = check_text[:footer_idx]
    check_text = _CODE_FENCE_RE.sub("", check_text)
    for m in _URL_RE.finditer(check_text):
        url = m.group(0)
        host_m = re.match(r"https?://([^/\s]+)", url, re.I)
        host = host_m.group(1) if host_m else ""
        ext = url.rsplit(".", 1)[-1].split("/")[0].split("?")[0].lower() if "." in url.rsplit("/", 1)[-1] else ""
        if _CODE_HOST_RE.search(host) or ext in _SRC_EXTS:
            return url
    return None


def grade_exploit(sample: dict, http_status: int, body: dict, elapsed: float) -> Grade:
    g = Grade(sample_id=sample["id"], passed=True)
    if http_status != 200:
        _fail(g, "http_200", f"HTTP {http_status}")
        return g
    _ok(g, "http_200")
    text = body.get("description") or body.get("exploit_guide") or ""
    meta = body.get("metadata") or {}
    category = sample["category"]

    if not text.strip():
        _fail(g, "non_empty", "description rỗng")
        return g
    _ok(g, "non_empty")

    if category == "chitchat":
        if text.strip() == EXPLOIT_CVE_GUIDE:
            _ok(g, "chitchat_guide")
        else:
            _fail(g, "chitchat_guide", "chitchat không trả đúng EXPLOIT_CVE_GUIDE")
        if meta.get("input_kind") == "chitchat":
            _ok(g, "chitchat_meta")
        else:
            g.warnings.append("metadata.input_kind != 'chitchat'")
        return g

    if category == "invalid_format":
        if meta.get("error") and ("không hợp lệ" in text or "không hợp lệ" in str(meta.get("error"))):
            _ok(g, "format_error_detected")
        else:
            _fail(g, "format_error_detected", "không phát hiện lỗi format CVE như kỳ vọng")
        return g

    # Từ đây là pipeline đầy đủ (prefetch + model): kiểm tra contract chung.
    tier = meta.get("section1_tier")
    if tier is not None and tier not in VALID_TIERS:
        _fail(g, "tier_valid", f"section1_tier lạ: {tier!r}")
    elif tier is None:
        g.warnings.append("metadata.section1_tier vắng mặt")
    else:
        _ok(g, "tier_valid")

    leak = _no_tool_leak(text)
    if leak:
        _fail(g, "no_tool_leak", f"rò tên tool nội bộ: {leak}")
    else:
        _ok(g, "no_tool_leak")

    airgap_leak = _airgap_leak_outside_section1(text)
    if airgap_leak:
        _fail(g, "no_airgap_leak", f"rò URL code-host/source trong Mục 2/3: {airgap_leak}")
    else:
        _ok(g, "no_airgap_leak")

    if category == "fake_cve":
        # Quan trọng nhất: CVE bịa KHÔNG được có tier real/synthesized — chống hallucination.
        if tier == "not_found":
            _ok(g, "no_hallucination")
        else:
            _fail(g, "no_hallucination", f"CVE giả nhưng tier={tier!r} (đáng lẽ phải 'not_found')")
        spans = _section_spans(text)
        sec1 = text[spans[1][0]:spans[1][1]] if 1 in spans else text
        if "```" in sec1:
            _fail(g, "no_fabricated_code", "CVE giả nhưng Mục 1 có code block — có thể đã bịa PoC")
        else:
            _ok(g, "no_fabricated_code")
        return g

    # curated_known / catalog_* / natural_sentence / multi_cve / english_input
    spans = _section_spans(text)
    if tier == "not_found":
        # Hợp lệ về kỹ thuật (model text bị loại bỏ hoàn toàn cho not_found) — không ép có Mục 2/3.
        if NO_POC_NOTE in text or "Không tìm thấy" in text:
            _ok(g, "not_found_notice_shape")
        else:
            g.warnings.append("tier=not_found nhưng text không khớp notice chuẩn")
        g.warnings.append(f"category={category} nhưng tier=not_found (catalog/miss) — xem hit-rate")
    else:
        missing = [n for n in (2, 3) if n not in spans]
        if missing:
            _fail(g, "three_sections", f"thiếu heading Mục {missing}")
        else:
            _ok(g, "three_sections")
        if tier in SYNTH_TIERS:
            if SYNTHESIS_BANNER in text:
                _ok(g, "synth_banner_present")
            else:
                _fail(g, "synth_banner_present", f"tier={tier} nhưng THIẾU banner AI-SYNTHESIZED")
        if tier in REAL_TIERS:
            sec1 = text[spans[1][0]:spans[1][1]] if 1 in spans else text
            if "```" in sec1:
                _ok(g, "real_tier_has_code")
            else:
                _fail(g, "real_tier_has_code", f"tier={tier} nhưng Mục 1 KHÔNG có code block")
    return g


def grade_chat(sample: dict, http_status: int, body: dict, elapsed: float) -> Grade:
    g = Grade(sample_id=sample["id"], passed=True)
    if http_status != 200:
        _fail(g, "http_200", f"HTTP {http_status}")
        return g
    _ok(g, "http_200")
    text = body.get("response") or body.get("answer") or body.get("message") or ""
    if not text.strip():
        _fail(g, "non_empty", "response rỗng")
        return g
    _ok(g, "non_empty")
    if text.strip() == STREAM_ERROR_MESSAGE:
        _fail(g, "no_backend_error", "gateway trả STREAM_ERROR_MESSAGE (backend lỗi)")
    else:
        _ok(g, "no_backend_error")
    leak = _no_tool_leak(text)
    if leak:
        _fail(g, "no_tool_leak", f"rò tên tool nội bộ: {leak}")
    else:
        _ok(g, "no_tool_leak")
    cve = sample.get("mentions_cve")
    if cve:
        if cve.lower() in text.lower():
            _ok(g, "cve_grounded")
        else:
            _fail(g, "cve_grounded", f"hỏi về {cve} nhưng response không nhắc lại mã CVE")
    cwe = sample.get("mentions_cwe")
    if cwe:
        if re.search(rf"CWE[- ]?{re.escape(cwe)}\b", text, re.I):
            _ok(g, "cwe_grounded")
        else:
            _fail(g, "cwe_grounded", f"hỏi về CWE-{cwe} nhưng response không nhắc lại mã CWE")
    return g


def _vn_diacritic_ratio(text: str) -> float:
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return 0.0
    return sum(1 for c in letters if _VN_DIACRITIC_RE.match(c)) / len(letters)


def grade_translate(sample: dict, http_status: int, body: dict, elapsed: float) -> Grade:
    g = Grade(sample_id=sample["id"], passed=True)
    if http_status != 200:
        _fail(g, "http_200", f"HTTP {http_status}")
        return g
    _ok(g, "http_200")
    out = body.get("translated_text") or body.get("translation") or ""
    if not out.strip():
        _fail(g, "non_empty", "translated_text rỗng")
        return g
    _ok(g, "non_empty")
    src_text = sample["message"]
    if out.strip().lower() == src_text.strip().lower():
        _fail(g, "actually_translated", "output giống hệt input (có thể bị echo lại, không dịch)")
    else:
        _ok(g, "actually_translated")
    ratio = len(out) / max(1, len(src_text))
    if 0.2 <= ratio <= 4.0:
        _ok(g, "length_sane")
    else:
        _fail(g, "length_sane", f"độ dài lệch bất thường (ratio={ratio:.2f})")
    vn_ratio = _vn_diacritic_ratio(out)
    tgt = sample["target_lang"]
    if tgt == "vi":
        if vn_ratio > 0.02:
            _ok(g, "target_language")
        else:
            _fail(g, "target_language", "target=vi nhưng output gần như không có dấu tiếng Việt")
    elif tgt == "en":
        if vn_ratio < 0.02:
            _ok(g, "target_language")
        else:
            _fail(g, "target_language", "target=en nhưng output vẫn còn nhiều dấu tiếng Việt")
    # Customer fix (2026-09): credentials/URLs/hashes must survive the translation verbatim.
    literals = sample.get("literals") or []
    if literals:
        missing = [lit for lit in literals if lit not in out]
        if missing:
            _fail(g, "preserved_literals", f"mất literal (tài khoản/mật khẩu/URL...): {missing}")
        else:
            _ok(g, "preserved_literals")
    return g


# report.md có <language_policy priority="highest"> BUỘC output toàn tiếng Việt (kể cả
# khi caller gửi tên section tiếng Anh) -> chấp nhận cả bản dịch tiếng Việt tương đương,
# không ép literal-match với tên section tiếng Anh gốc.
_SECTION_SYNONYMS = {
    "overview": ["overview", "tổng quan"],
    "details": ["details", "detail", "chi tiết"],
    "recommendations": ["recommendations", "recommendation", "khuyến nghị", "đề xuất"],
    "tóm tắt": ["tóm tắt", "tổng quan", "overview"],
    "chi tiết": ["chi tiết", "details", "detail"],
    "khuyến nghị": ["khuyến nghị", "đề xuất", "recommendations"],
    "executive summary": ["executive summary", "tổng quan", "tóm tắt"],
    "findings": ["findings", "chi tiết", "phát hiện"],
    "remediation": ["remediation", "khuyến nghị", "khắc phục", "đề xuất"],
}


def grade_report(sample: dict, http_status: int, body: dict, elapsed: float) -> Grade:
    g = Grade(sample_id=sample["id"], passed=True)
    if http_status != 200:
        _fail(g, "http_200", f"HTTP {http_status}")
        return g
    _ok(g, "http_200")
    text = body.get("report") or ""
    if len(text.strip()) < 150:
        _fail(g, "substantial_length", f"report quá ngắn ({len(text)} ký tự)")
    else:
        _ok(g, "substantial_length")
    text_low = text.lower()
    missing = []
    for s in sample["sections"]:
        variants = _SECTION_SYNONYMS.get(s.lower(), [s])
        if not any(v.lower() in text_low for v in variants):
            missing.append(s)
    if missing:
        _fail(g, "sections_present", f"thiếu section (kể cả bản dịch VN): {missing}")
    else:
        _ok(g, "sections_present")
    return g


def grade_statistical_report(sample: dict, http_status: int, body: dict, elapsed: float) -> Grade:
    g = Grade(sample_id=sample["id"], passed=True)
    if http_status != 200:
        _fail(g, "http_200", f"HTTP {http_status}")
        return g
    _ok(g, "http_200")
    title = (body.get("report_title") or "").strip()
    summary = (body.get("report_summary") or "").strip()
    detail = (body.get("report_detail") or "").strip()
    if title:
        _ok(g, "json_contract_honored")
    else:
        _fail(g, "json_contract_honored", "report_title rỗng -> model không trả đúng JSON 3 field")
    if summary or detail:
        _ok(g, "has_content")
    else:
        _fail(g, "has_content", "cả summary lẫn detail đều rỗng")
    return g


GRADERS = {
    "exploit": grade_exploit,
    "chat": grade_chat,
    "translate": grade_translate,
    "report": grade_report,
    "statistical_report": grade_statistical_report,
}
