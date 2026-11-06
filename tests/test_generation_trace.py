"""Unit tests cho `poc/trace.py` — Mục 4 ("Giải thích quy trình tạo PoC"), xem
`module-map/explain-poc/`. Thuần hàm, không I/O, không cần service nào (qdrant/neo4j/LLM) —
test trực tiếp `build_generation_trace`/`render_generation_trace` với input giả lập từng tổ
hợp flag, bao phủ cả 10 nhánh CVE-thật + `synthesis_failed` liệt kê ở
`module-map/explain-poc/01-api-va-luong-xu-ly.txt` mục 3, cộng cơ chế render (heading/banner)
và ngoại lệ byte-exact cho refusal hook-exhaustion.
"""
from __future__ import annotations

from secai_gateway.poc.trace import (
    TRACE_BANNER,
    TRACE_HEADING,
    build_generation_trace,
    render_generation_trace,
)


def _injected(tier: str, status: str = "draft_not_executed") -> dict:
    return {"section1_tier": tier, "candidate_status": status}


def test_canonical_branch():
    top = {"canonical": True}
    trace = build_generation_trace({}, [top], _injected("real"))
    assert len(trace) == 1
    assert "xác minh chạy thành công" in trace[0]


def test_fixed_branch():
    top = {"fixed": True, "consolidated": True}
    trace = build_generation_trace({}, [top], _injected("real_consolidated"))
    assert "kỹ thuật viên hiệu chỉnh" in trace[0]


def test_llm_merge_branch_requires_consolidated_from():
    """Tín hiệu ĐÚNG cho "AI hợp nhất" là key `consolidated_from` — KHÔNG phải bool
    `consolidated` đơn thuần (bug đã bắt ở vòng review 1: `fixed`/`slice_entry` cũng set
    `consolidated=True` nhưng KHÔNG dùng LLM)."""
    top = {"consolidated": True, "consolidated_from": {"sha256": "x", "url": "y"}}
    trace = build_generation_trace({}, [top], _injected("real_consolidated"))
    assert "AI hợp nhất" in trace[0]


def test_slice_entry_is_not_mislabelled_as_llm_merge():
    """`slice_entry` (tất định, 1 file) set `consolidated=True` + `extracted=True` nhưng
    KHÔNG có `consolidated_from` — câu trace phải là "trích xuất", không phải "AI hợp nhất"."""
    top = {"consolidated": True, "extracted": True}
    trace = build_generation_trace({}, [top], _injected("real_consolidated"))
    assert "AI hợp nhất" not in trace[0]
    assert "trích xuất" in trace[0]


def test_pruned_branch():
    top = {"consolidated": False, "pruned": True}
    trace = build_generation_trace({}, [top], _injected("real_pruned"))
    assert "tất định, không qua AI" in trace[0]
    assert "nhiều tệp" in trace[0]


def test_single_file_extracted_no_acquisition_hint():
    top = {"consolidated": True, "extracted": True}
    trace = build_generation_trace({}, [top], _injected("real_consolidated"))
    assert "một tệp" in trace[0]


def test_cached_hit_branch():
    top = {"consolidated": True, "extracted": True}
    trace = build_generation_trace({"cached_hit": True}, [top], _injected("real_consolidated"))
    assert "lưu lại từ một lần tra cứu trước" in trace[0]


def test_github_ondemand_branch():
    top = {"consolidated": True, "extracted": True}
    trace = build_generation_trace({"github_ondemand": True}, [top], _injected("real_consolidated"))
    assert "liên kết chưa tải" in trace[0]


def test_web_real_branch():
    top = {"consolidated": True, "extracted": True}
    trace = build_generation_trace({"web_real": True}, [top], _injected("real_consolidated"))
    assert "internet" in trace[0]


def test_acquisition_priority_over_cache_when_evidence_flag_present():
    """Per-evidence flag (fixed/consolidated_from/pruned) luôn thắng acquisition flag — một
    PoC `fixed` lấy qua cache trước đó vẫn phải báo là "đã hiệu chỉnh", không phải "cache"."""
    top = {"fixed": True, "consolidated": True}
    trace = build_generation_trace({"cached_hit": True}, [top], _injected("real_consolidated"))
    assert "kỹ thuật viên hiệu chỉnh" in trace[0]
    assert "lưu lại từ một lần tra cứu trước" not in trace[0]


def test_synthesized_branch():
    trace = build_generation_trace({}, [], _injected("synthesized_cve", "synthesized_not_executed"))
    assert "tự tổng hợp" in trace[0]
    trace2 = build_generation_trace({}, [], _injected("synthesized_readme", "synthesized_not_executed"))
    assert "tự tổng hợp" in trace2[0]


def test_not_found_branch():
    trace = build_generation_trace({}, [], _injected("not_found", "not_generated"))
    assert "Không tìm thấy CVE này" in trace[0]


def test_synthesis_failed_branch():
    trace = build_generation_trace({}, [], _injected("synthesis_failed", "not_generated"))
    assert "chưa tạo được bản minh hoạ hợp lệ" in trace[0]


def test_fail_open_unknown_tier_never_empty():
    trace = build_generation_trace({}, [], {})
    assert trace and isinstance(trace[0], str) and trace[0].strip()


def test_fail_open_real_tier_no_matching_flag():
    """Real evidence nhưng không khớp flag nào đã biết (vd evidence dict bất thường từ code
    path tương lai) → câu generic trung thực, không raise, không bịa chi tiết sai."""
    trace = build_generation_trace({}, [{"source": "mystery"}], _injected("real"))
    assert trace and "mã khai thác thật" in trace[0]


def test_render_heading_matches_fe_split_section1_regex():
    """FE `FileCode.tsx::splitSection1` coi `#{2,6}[ \\t]*[2-9]\\.` là ranh giới section — heading
    Mục 4 PHẢI khớp dạng này để tự rơi vào card "Phân tích / hướng dẫn" (không cần sửa FE)."""
    import re
    assert re.match(r"#{2,6}[ \t]*[2-9]\.", TRACE_HEADING)


def test_render_includes_banner_and_bullets():
    md = render_generation_trace(["câu một", "câu hai"])
    assert TRACE_HEADING in md
    assert TRACE_BANNER in md
    assert "- câu một" in md
    assert "- câu hai" in md


def test_render_never_empty_even_with_empty_trace():
    md = render_generation_trace([])
    assert TRACE_HEADING in md and TRACE_BANNER in md
    assert md.strip() != f"{TRACE_HEADING}\n\n{TRACE_BANNER}"  # có ít nhất 1 dòng nội dung


def test_no_internal_tool_names_leak_in_any_sentence():
    """Tiêu chí #4 ở module-map/explain-poc/03-tieu-chi-pass.txt — quét toàn bộ câu cố định
    trong trace.py không chứa tên tool/hàm nội bộ."""
    from secai_gateway.poc import trace as trace_mod
    blocked = ("poc_search", "poc_fetch", "qdrant_search", "neo4j_search", "web_search",
               "fetch_reference", "_build_exploit_grounding", "consolidate_poc",
               "slice_entry", "prune_evidence", "catalog.db", ".py:", "SELECT ")
    sentences = [
        trace_mod._NOT_FOUND, trace_mod._SYNTHESIS_FAILED, trace_mod._SYNTHESIZED,
        trace_mod._CANONICAL, trace_mod._FIXED, trace_mod._LLM_MERGE, trace_mod._PRUNED,
        trace_mod._SINGLE_FILE, trace_mod._CACHE_HIT, trace_mod._GITHUB_ONDEMAND,
        trace_mod._WEB_REAL, trace_mod._GENERIC_REAL, trace_mod._GENERIC_UNKNOWN,
    ]
    for s in sentences:
        for term in blocked:
            assert term not in s, f"leaked {term!r} in sentence: {s!r}"
