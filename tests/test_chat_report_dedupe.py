"""Chat CVE report: dedupe regenerate-concat + KB grounding note."""
from __future__ import annotations

from secai_gateway.api.routes import (
    CVE_KB_CONTEXT_NOTE,
    dedupe_repeated_markdown_report,
)


def test_dedupe_repeated_cve_h1_keeps_one_report() -> None:
    text = (
        "# CVE-2026-53435 — Jenkins: Deserialization không an toàn từ config.xml\n"
        "1. Mô tả lỗ hổng\n"
        "Nội dung A đủ dài về deserialization CWE-502 và RCE qua Script Console.\n"
        "2. Phiên bản phần mềm bị ảnh hưởng\n"
        "Weekly 2.567\n"
        "5. Khắc phục và vá lỗi\n"
        "Nâng cấp.\n"
        "Bạn cần tôi xem chi tiết?\n"
        "# CVE-2026-53435 — Jenkins: Deserialization không an toàn từ config.xml\n"
        "1. Mô tả lỗ hổng\n"
        "Nội dung B ngắn hơn.\n"
    )
    out = dedupe_repeated_markdown_report(text)
    assert out.count("# CVE-2026-53435") == 1
    assert "Nội dung A" in out
    assert "Nội dung B" not in out
    assert "Bạn cần tôi xem chi tiết?" in out


def test_dedupe_section1_mo_ta_without_second_h1() -> None:
    text = (
        "CVE overview\n"
        "1. Mô tả lỗ hổng\n"
        "Alpha alpha alpha.\n"
        "5. Khắc phục và vá lỗi\n"
        "Patch.\n"
        "1. Mô tả lỗ hổng\n"
        "Beta beta.\n"
    )
    out = dedupe_repeated_markdown_report(text)
    assert out.count("1. Mô tả lỗ hổng") == 1
    assert "Alpha" in out
    assert "Beta" not in out


def test_dedupe_noop_when_single_report() -> None:
    text = "# CVE-2021-44228 — Log4Shell\n1. Mô tả lỗ hổng\nRCE.\n"
    assert dedupe_repeated_markdown_report(text) == text.strip()


def test_dedupe_ma_poc_outline_repeated() -> None:
    """Exploit-style 1–5 (Mã PoC…) lặp×2 — regex cũ chỉ bắt 'Mô tả lỗ hổng' nên miss."""
    block = (
        "## 1. Mã PoC/Payload\n"
        "```\nprint('a')\n```\n"
        "## 2. Cách chạy PoC\n"
        "python3 exploit.py\n"
        "## 3. Tổng quan Lỗ hổng\n"
        "RCE via X.\n"
        "## 4. Ảnh hưởng\n"
        "Critical.\n"
        "## 5. Khắc phục\n"
        "Upgrade.\n"
    )
    text = block + "\n" + block
    out = dedupe_repeated_markdown_report(text)
    assert out.count("## 1. Mã PoC") == 1
    assert out.count("## 5. Khắc phục") == 1
    assert out.strip() == block.strip()


def test_cve_kb_context_note_requires_handoff_facts() -> None:
    assert "nguồn CHÍNH" in CVE_KB_CONTEXT_NOTE
    assert "ĐÚNG MỘT báo cáo" in CVE_KB_CONTEXT_NOTE
    assert "handoff" in CVE_KB_CONTEXT_NOTE.lower()
