from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
import unicodedata
from collections.abc import AsyncIterator
from contextlib import aclosing
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any
from uuid import uuid4

import httpx
from fastapi import FastAPI, Header, HTTPException, UploadFile, File, Form
from fastapi.responses import StreamingResponse, Response
from fastapi.staticfiles import StaticFiles

from linch.events import PartialAssistantEvent, ResultEvent, SystemEvent

from ..agent import registry_for
from ..agent.hooks import (
    CHAT_PRE_FETCH_MARKER, _EXPLOIT_KB_SEARCH_EXHAUSTED, classify_chat_grounding,
)
from ..poc import (
    canonical_block,
    cli_surface,
    collect_named_targets,
    consolidate_poc,
    ensure_target_disclaimer,
    enforce_section1,
    extract_cve_id,
    has_real_code_evidence,
    looks_like_python2,
    merge_evidence,
    normalize_cve_ids,
    override_section3,
    prefetch_evidence,
    prune_evidence,
    redact_user_target,
    sanitize_airgap_runguide,
    slice_entry,
    strip_system_narration,
    strip_tool_name_mentions,
)
from ..poc.inject import has_synthesis_evidence
from ..poc.trace import build_generation_trace, render_generation_trace
from ..poc.prefetch import session_coverage_scope
from ..poc.backend import PocBadRequest, PocError, PocForbidden, PocNotFound
from ..poc.fixed_pocs import extract_target
from ..poc.kt_client import KtClientError
from ..poc.kt_exploit import parse_exploit_intent, run_kt_exploit
from ..poc.runners.toolbox import run_raw_command, run_tool
from ..poc.verify import verify_poc
from ..retrieval import gather_web_hints
from ..retrieval.filters import ENTITY_ID_RES
from ..retrieval.qdrant import _qdrant_search
from ..retrieval.date_lookup import _date_grounding
from ..sanitize import classify_input, decide, judge_injection
from ..sanitize.classifier import note_verdict, verdict_stats
from ..sanitize.gate import Action, Decision, GREET_MSG, REFUSE_OFFTOPIC_MSG, REFUSE_INJECTION_MSG
from ..sanitize.cascade import is_direct_override, judge_chat_scope, judge_security_input
from ..security import (
    UNSUPPORTED_LANGUAGE_MSG, has_hidden_smuggling, is_unsupported_language,
    neutralize_active_content, require_public_input, sanitize_output_language,
    wrap_untrusted_content,
)
from .literal_mask import (
    PlaceholderIntegrityError, mask_literals, placeholder_ids, restore_checked,
)
from .projects import _norm_owner
from .messages import (
    build_translate_prompt_masked, merge_detail_table, parse_statistical, report_prompt,
    statistical_detail_table, statistical_prompt,
    translate_langs, translate_prompt,
)
from .text_chunk import split_for_translation
from .schemas import (
    BughuntRequest,
    ExecRequest,
    ExploitAdvanceRequest,
    ExploitAdvanceReplyRequest,
    ExploitRunRequest,
    LegacyRequest,
    ToolRequest,
    VerifyRequest,
)
from .sse import event_to_sse, sse_frame

logger = logging.getLogger(__name__)

# Shown to the user when a run fails. Delivered on the *visible* channels (token +
# final), not only as an `event: error` frame — some UIs render assistant text but
# ignore the error event, and a silent empty bubble is worse than a plain apology.
STREAM_ERROR_MESSAGE = (
    "Xin lỗi, trợ lý đang gặp sự cố tạm thời nên chưa thể trả lời. Vui lòng thử lại sau giây lát."
)

# The exploit endpoint expects a CVE id. When the caller instead sends a bare greeting /
# smalltalk (no CVE, no keyword), the normal pipeline still forces a "## 1. Mã Khai thác …
# Chưa có thông tin PoC" report header onto a "xin chào" — awkward. Detect that ONE narrow
# case and answer with a short prompt for a CVE instead, skipping the Section-1 scaffolding.
EXPLOIT_CVE_GUIDE = (
    "Vui lòng nhập mã CVE cần phân tích khai thác, ví dụ: CVE-2026-42533. "
    "Bạn cũng có thể kèm từ khoá sản phẩm hoặc loại lỗ hổng để tìm PoC công khai."
)
_CVE_ANY = re.compile(r"CVE-\d{4}-\d{3,7}", re.I)
# Whole-message greeting / smalltalk / meta only (anchored ^…$), matched against a
# diacritic-folded copy so "xin chào", "xin chao" and typos all land. The token group is
# repeated ((?:…)+) so a chain of greeting/smalltalk chunks with no security content still
# matches as a whole — e.g. "xin chào bạn khoẻ không" (greeting + "how are you"), which a
# single-token anchor missed and let fall through into off-topic PoC synthesis. Anything
# with real content past the greeting (e.g. "chào, tìm poc struts") still fails the anchor
# at the first non-greeting token → normal flow, so keyword-only PoC searches are safe.
_CHITCHAT_RE = re.compile(
    r"^(?:(?:"
    r"xin\s*chao|chao(?:\s*ban)?|hello+|helo|hi+|hey+|yo|"
    r"cam\s*on|thanks?|thank\s*you|"
    r"ban\s*(?:la\s*ai|ten\s*(?:gi|la)|co\s*khoe(?:\s*khong)?|khoe(?:\s*khong)?|lam\s*(?:duoc\s*)?gi)|who\s*are\s*you|"
    r"khoe\s*khong|co\s*khoe\s*khong|"
    r"gioi\s*thieu(?:\s*ban)?|"
    r"help|giup(?:\s*(?:toi|minh|em|voi))?|huong\s*dan|hd|"
    r"test(?:ing)?|/?start|menu|oke?y?|ok"
    r")[\s.,!?…]*)+$",
    re.I,
)
# Strict CVE format: CVE-YYYY-NNNNN (4-digit year, 3-7 digit ID, only digits/hyphens)
_CVE_STRICT = re.compile(r"^CVE-\d{4}-\d{3,7}$", re.I)


def _validate_cve_format(cve_id: str) -> tuple[bool, str]:
    """Validate CVE ID format strictly (CVE-YYYY-NNNNN only, no 'zz' or other suffixes)."""
    cve_id = (cve_id or "").strip().upper()
    if not cve_id:
        return False, "CVE ID không được để trống."
    if not _CVE_STRICT.match(cve_id):
        return False, f"Định dạng CVE không hợp lệ: '{cve_id}'. Cần CVE-YYYY-NNNNN (ví dụ CVE-2026-42533)."
    return True, ""




def _fold_diacritics(text: str) -> str:
    """Lowercase + strip Vietnamese tone/diacritic marks so greetings match with or without
    them (đ→d isn't decomposed by NFD, so map it explicitly)."""
    decomposed = unicodedata.normalize("NFD", text or "")
    stripped = "".join(c for c in decomposed if unicodedata.category(c) != "Mn")
    return stripped.replace("đ", "d").replace("Đ", "d").lower().strip()


def _is_exploit_chitchat(text: str) -> bool:
    """True when `text` is empty or a bare greeting/smalltalk with no CVE — i.e. the exploit
    endpoint has nothing to research and should ask for a CVE instead of emitting an empty
    "no PoC" report. A (possibly malformed) CVE anywhere in the text is never chitchat."""
    folded = _fold_diacritics(text)
    if not folded:
        return True
    if _CVE_ANY.search(normalize_cve_ids(text or "")):
        return False
    return bool(_CHITCHAT_RE.match(folded))


def _chat_session_id(body: LegacyRequest) -> str:
    # Stable per-user session id → linch loads/compacts the prior conversation from its
    # (Redis) session store. Only the new message is sent; history is NOT re-stuffed.
    user_id = str(body.user_id or "").strip()
    if not user_id or user_id == "anonymous":
        # No stable identity → a fresh id per request, so anonymous callers never
        # share (and read back) one another's conversation history.
        return f"chat:anon:{uuid4().hex}"
    return f"chat:{hashlib.sha256(user_id.encode()).hexdigest()}"


def _exploit_session_id(body: LegacyRequest) -> str:
    # Separate memory thread from chat (own prefix) so a PoC-verify conversation ("giờ verify
    # vào target X") remembers the CVE discussed 1-2 turns earlier — exploit_research was
    # stateless (session_id always None) until poc_verify/poc_scan/poc_exec became agent tools,
    # at which point "interactive" chaining across turns needs real conversation memory.
    user_id = str(body.user_id or "").strip()
    if not user_id or user_id == "anonymous":
        return f"exploit:anon:{uuid4().hex}"
    return f"exploit:{hashlib.sha256(user_id.encode()).hexdigest()}"


def _kt_slots_from_request(body: LegacyRequest, text: str) -> dict[str, Any] | None:
    """Structured fields win; else fail-closed free-text parse (CVE + IP + intent)."""
    cve = (getattr(body, "cve", None) or "").strip()
    target = (getattr(body, "target", None) or getattr(body, "target_system", None) or "").strip()
    port = getattr(body, "port", None)
    if cve and target:
        # strip host:port into parts if needed
        ip, p = target, port
        if "://" not in target and target.count(":") == 1:
            left, right = target.rsplit(":", 1)
            if right.isdigit():
                ip, p = left, p or right
        return {"ip": ip, "cve": cve, "port": str(p) if p not in (None, "") else None,
                "command": (getattr(body, "command", None) or "id")}
    return parse_exploit_intent(text)


_VALID_POC_STRATEGIES = {"prune", "consolidate", "auto"}


def _resolve_poc_strategy(override: str | None, settings: Any) -> str:
    """Effective PoC strategy: a valid per-request override wins (A/B on one gateway), else
    Settings.poc_strategy, else 'prune'. Junk overrides are ignored (not trusted from clients)."""
    o = str(override or "").strip().lower()
    if o in _VALID_POC_STRATEGIES:
        return o
    d = str(getattr(settings, "poc_strategy", "prune") or "prune").lower()
    return d if d in _VALID_POC_STRATEGIES else "prune"


def _exploit_target(body: LegacyRequest, text: str) -> str | None:
    """Normalized ``host[:port]`` the request is targeting, for scoping the verified-good
    (fixed) PoC lookup to the right lab. Free-text target wins; else the structured
    ``target`` (+``port``) fields; else ``None`` (→ generic/default fixed PoC)."""
    t = extract_target(text)
    if t:
        return t
    btarget = (getattr(body, "target", None) or getattr(body, "target_system", None) or "").strip()
    if not btarget:
        return None
    bport = getattr(body, "port", None)
    if bport not in (None, ""):
        return f"{btarget}:{bport}"
    return btarget


def _kt_prompt_appendix(result: dict[str, Any]) -> str:
    """Compact JSON for the model — full log stays in metadata / SSE for the FE."""
    scan = result.get("scan") or {}
    preview = (scan.get("cleaned_output") or "")[:2000]
    slim = {
        "exploit_verdict": result.get("exploit_verdict"),
        "scanner_finished": result.get("scanner_finished"),
        "has_poc": result.get("has_poc"),
        "has_msf": result.get("has_msf"),
        "target_ip": result.get("target_ip"),
        "target_port": result.get("target_port"),
        "cve": result.get("cve"),
        "command": result.get("command"),
        "error": result.get("error"),
        "cleaned_output_preview": preview,
    }
    return ("\n\n[Hệ thống đã chạy deterministic poc_kt_exploit — CHỈ tóm tắt JSON sau, "
            "không bịa, không gọi lại trừ khi user yêu cầu chạy lại:\n"
            + json.dumps(slim, ensure_ascii=False) + "\n]")


# Chars of fetched-PoC preview handed to the model to ground Mục 2 (how-to-run) and Mục 3
# (overview). The FULL PoC still goes to Section 1 deterministically; this is only the
# grounding slice for the model's own prose, kept small so it doesn't bloat the context.
_PREFETCH_APPENDIX_BUDGET = 3000
# Larger budget for the web-hints appendix: a GitHub fix-commit DIFF (the highest-value
# synthesis input — it shows the real vulnerable code, not prose) can be several KB, and the
# whole point is to ground the synthesized PoC in it. Kept well under the consolidated PoC's
# 40k so it doesn't drown the prompt, but comfortably above the 3k used for catalog prose.
_WEB_HINTS_APPENDIX_BUDGET = 12000
# H3 (2026-09-22): SSE `tool_result` frames used to carry up to 4000 raw KB chars, but
# ToolTimeline.tsx (FE) only ever renders `summary` — falling back to `result.slice(0, 140)`
# only when `summary` is absent. Once every `tool_result` frame always sets a clean
# `summary` (see `_grounding_sse_frames` + `event_to_sse`'s ToolCallEndEvent branch), the
# raw `result` is only ever needed for that 140-char fallback preview — kept slightly above
# it (not dropped: ToolTimeline's `result === undefined` check is how it knows a tool_call
# is still pending, so the key itself must still land in the merged row) so the wire no
# longer duplicates several KB of raw KB/tool payload the FE never reads.
_SSE_RESULT_PREVIEW_CHARS = 200


def _health_status(qwen_ok: bool, sanitize_stats: dict[str, float]) -> str:
    """Pure decision for /health's ``status`` field. Degraded when the LLM is unreachable OR
    the input classifier has been failing open on more than half its calls over a real sample
    (``total>=20`` — a small/cold-start sample shouldn't flip status on one or two unlucky
    calls). Separate function so this can be unit-tested without a live LLM/httpx round trip."""
    sanitize_degraded = sanitize_stats.get("total", 0) >= 20 and sanitize_stats.get("unknown_rate", 0.0) > 0.5
    return "ok" if (qwen_ok and not sanitize_degraded) else "degraded"


def _evidence_preview(evidence: list[dict[str, Any]], budget: int) -> str:
    """Truncated, path-labelled dump of the fetched PoC bytes (README/entry first) — enough
    for the model to write Mục 2/3, not the whole repo (that is Section 1's job)."""
    out: list[str] = []
    used = 0
    for ev in evidence:
        if used >= budget:
            break
        files = ev.get("source_files")
        if files:
            ordered = sorted(files, key=lambda f: (f.get("role") != "readme", f.get("role") != "entry"))
            for f in ordered:
                code = f.get("code") or ""
                if not code or used >= budget:
                    continue
                take = code[: budget - used]
                out.append(f"# {f.get('path', 'poc')}\n{take}")
                used += len(take)
        else:
            # source_code (real PoC bytes) or, for a web reference page, its markdown text.
            code = ev.get("source_code") or ev.get("description") or ""
            if code:
                take = code[: budget - used]
                ref = ev.get("source_ref") or ev.get("url") or ev.get("edb_id") or "poc"
                out.append(f"# {ref}\n{take}")
                used += len(take)
    return "\n\n".join(out)


def _prefetch_prompt_appendix(prefetch_result: dict[str, Any]) -> str:
    """Feed deterministically-fetched PoC evidence into the model prompt so Mục 2/3 (and
    Mục 1 when synthesizing) are grounded REGARDLESS of whether the model calls its own
    tools. Empty evidence → no appendix, so the honest not-found path is unchanged.

    Two framings:
    * T1 — real exploit code present → system will inject Mục 1; model must NOT write it.
    * T2 — only detection YAML / README / description → model MUST write Mục 1 (standalone
      PoC, not pasted nuclei YAML); system adds the SYNTHESIZED banner later.
    """
    evidence = prefetch_result.get("evidence") or []
    if not evidence:
        return ""
    preview = _evidence_preview(evidence, _PREFETCH_APPENDIX_BUDGET)
    if not preview.strip():
        return ""
    sources = [f"{ev.get('source') or '?'}: {ev.get('url') or ev.get('source_ref') or ''}"
               for ev in evidence]
    sources_line = "Nguồn: " + " ; ".join(sources) + "\n"
    # Externally-sourced (fetched PoC/advisory) bytes — spotlight as untrusted data, never
    # instructions (indirect prompt-injection defense; see <untrusted_content_policy>).
    body = ("Nội dung (đã cắt bớt theo giới hạn):\n"
            + wrap_untrusted_content(preview, source="poc_prefetch") + "\n]")
    if has_real_code_evidence(evidence):
        return (
            "\n\n[Hệ thống đã tìm thấy PoC khai thác công khai đã xác thực cho lỗ hổng này qua "
            "tra cứu nội bộ (deterministic). Mục 1 (mã PoC/Payload) sẽ do hệ thống tự chèn — "
            "bạn KHÔNG viết Mục 1. Hãy dùng bằng chứng thật dưới đây làm căn cứ để viết Mục 2 "
            "(Cách chạy PoC) và Mục 3 (Tổng quan Lỗ hổng); TUYỆT ĐỐI không trả lời 'không có "
            "thông tin' khi bằng chứng này tồn tại. Chỉ dùng thông tin có trong bằng chứng, "
            "không bịa.\n"
            + sources_line + body
        )
    # Detection YAML / README / advisory-only — synthesis tier.
    return (
        "\n\n[Hệ thống đã tìm thấy bằng chứng detection/mô tả (template nuclei YAML, README "
        "hoặc advisory) — CHƯA phải PoC khai thác đã fetch. BẮT BUỘC viết Mục 1 (`## 1. Mã "
        "Khai thác (PoC) và Payload`) dưới dạng script độc lập (ưu tiên Python requests hoặc "
        "curl) bám method/path/header/body/payload/matcher và mô tả CVE trong bằng chứng; "
        "TUYỆT ĐỐI không dán YAML nuclei làm PoC. Hệ thống sẽ tự chèn banner SYNTHESIZED — "
        "bạn không tự viết banner. Dùng bằng chứng dưới đây cho Mục 1, Mục 2 và Mục 3; không "
        "trả lời 'không có thông tin' khi bằng chứng này tồn tại; không bịa chi tiết không có "
        "trong bằng chứng.\n"
        + sources_line + body
    )


def _web_hints_prompt_appendix(evidence: list[dict[str, Any]]) -> str:
    """Case 3 (CVE real, no catalog PoC — the T3 tier): feed deterministically-gathered PUBLIC
    WEB reference pages (advisories/write-ups, via SearXNG + fetch_reference) into the prompt
    so the model can build a REFERENCE PoC and write Mục 2/3 from real hints — while making it
    unmistakable this is unverified material (the T3 SYNTHESIZED banner still applies). Empty
    evidence → no appendix. Distinct framing from _prefetch_prompt_appendix, which announces a
    *verified fetched* PoC; here nothing is verified.

    Includes an explicit instruction to write ANALYSIS instead of fake-looking code when the
    evidence lacks concrete implementation detail (function/param/endpoint names) — a 10-CVE
    quality review found code grounded in specifics (an exact function/file/param from the
    evidence) scored ~2x closer to a working PoC than code synthesized from a bare one-line
    NVD summary, which still LOOKED like working code (compiles, has a plausible-sounding
    payload) while contributing almost nothing toward an actual exploit — worse than admitting
    the gap, since a confident-looking placeholder is easy to mistake for a grounded one."""
    if not evidence:
        return ""
    preview = _evidence_preview(evidence, _WEB_HINTS_APPENDIX_BUDGET)
    if not preview.strip():
        return ""
    sources = [f"{ev.get('title') or ev.get('source') or '?'}: {ev.get('url') or ''}"
               for ev in evidence]
    return (
        "\n\n[KHÔNG có PoC đã xác thực trong dữ liệu nội bộ, nhưng CVE này có thật và hệ thống "
        "đã thu thập THÔNG TIN THAM KHẢO CÔNG KHAI từ web (advisory/write-up — CHƯA phải PoC đã "
        "kiểm chứng). Hãy dùng làm gợi ý để dựng MÃ PoC THAM KHẢO cho Mục 1 và viết Mục 2 (Cách "
        "chạy) + Mục 3 (Tổng quan). Mọi mã dựng ra là bản nháp CHƯA được thực thi/xác thực — nêu "
        "rõ điều đó. Chỉ dùng thông tin trong bằng chứng dưới đây, không bịa số liệu.\n"
        "Trước khi viết Mục 1, tự đánh giá bằng chứng dưới đây có chi tiết TRIỂN KHAI CỤ THỂ hay "
        "không (tên hàm, đường dẫn file, tham số HTTP/endpoint, cấu trúc gói tin, offset...). "
        "NẾU CÓ: bám sát đúng các chi tiết đó, không tự đổi tên hàm/tham số. NẾU KHÔNG — bằng "
        "chứng chỉ có mô tả chung chung, hay gặp ở lỗ hổng kernel/firmware/giao thức nội bộ mà "
        "advisory công khai không đủ chi tiết — KHÔNG viết code giả trông như chạy được. Thay "
        "vào đó viết PHÂN TÍCH KỸ THUẬT (cơ chế lỗi, điều kiện kích hoạt) và liệt kê rõ cần thêm "
        "thông tin gì (cấu trúc gói tin thật, offset chính xác, source code sản phẩm...) để xây "
        "được PoC thật. Một đoạn phân tích trung thực còn hữu ích hơn một đoạn code trông giống "
        "thật nhưng không dựa trên gì cả.\n"
        "Nguồn tham khảo:\n- " + "\n- ".join(sources) + "\n"
        "Nội dung trang tham khảo (đã cắt bớt):\n"
        + wrap_untrusted_content(preview, source="web_hints") + "\n]"
    )


def _kb_prompt_appendix(kb_content: str | None) -> str:
    """Feed deterministically-retrieved internal Knowledge Base (KB) context into the prompt
    for CVEs present in the internal DB (Qdrant/Neo4j) but absent from the PoC catalog (T3 tier).
    Gives the model exact technical details (CWE, CVSS, affected products, vulnerability description)
    to synthesize Section 1, 2, and 3 without hallucination."""
    if not kb_content or not kb_content.strip():
        return ""
    if "Không tìm thấy kết quả trong knowledge base" in kb_content:
        return ""
    return (
        "\n\n[Hệ thống đã tìm thấy thông tin kỹ thuật của lỗ hổng trong Cơ sở dữ liệu nội bộ (KB):\n"
        + wrap_untrusted_content(kb_content.strip()[:6000], source="kb_prefetch")
        + "\n\nHãy căn cứ vào các thông tin kỹ thuật xác thực trên (CWE, vector tấn công, sản phẩm bị ảnh hưởng, mô tả cơ chế lỗi) "
        "để phân tích và xây dựng mã khai thác PoC (Mục 1), Hướng dẫn chạy (Mục 2) và Tổng quan kỹ thuật (Mục 3). "
        "Ưu tiên thông tin riêng của CVE; tài liệu CWE/CAPEC/ATT&CK chỉ là kiến thức bổ trợ, không xác nhận chi tiết khai thác. Nếu thiếu điều kiện hoặc phiên bản cần thiết, nêu rõ phần thiếu, không đoán payload. Giữ nguồn trích dẫn và ghi rõ mã tổng hợp chưa chạy xác minh.]\n"
    )


def _kb_unavailable_response(prefetch_result: dict[str, Any], evidence: list[dict[str, Any]]) -> tuple[str, dict[str, Any]] | None:
    """Stop before generation when neither KB context nor artifact evidence supports it."""
    cve_id = prefetch_result.get("cve_id")
    usable_context = (prefetch_result.get("kb_status") == "found"
                      and bool((prefetch_result.get("kb_content") or "").strip()))
    if (not cve_id or usable_context
            or has_real_code_evidence(evidence) or has_synthesis_evidence(evidence)):
        return None
    status = prefetch_result.get("kb_status", "error")
    if status == "not_found":
        return_text, metadata = enforce_section1("", [], cve_id=cve_id)
        return return_text, {**metadata, "kb_status": status}
    detail = (f"Đã tìm thấy {cve_id} nhưng chưa đủ thông tin kỹ thuật để sinh PoC. "
              "Cần bổ sung mô tả cơ chế lỗi và điều kiện ảnh hưởng."
              if status == "insufficient" else
              f"Không thể truy vấn đầy đủ cơ sở dữ liệu nội bộ (KB) để xác thực {cve_id}. "
              "Vui lòng thử lại; hệ thống không sinh PoC khi chưa có bằng chứng xác thực.")
    return ("## 1. Mã Khai thác (PoC) và Payload\n\n" + detail,
            {"candidate_status": "not_generated", "section1_tier": "synthesis_failed",
             "reference_code_injected": False, "kb_status": status})


async def _pad_to_floor(start: float, floor_s: float) -> None:
    """Sleep just enough so total elapsed since ``start`` reaches ``floor_s`` — the uniform
    exploit-latency floor (see Settings.poc_exploit_simulate_delay_s). No-op when the floor
    is disabled (<=0) or the work already took longer. Applied AFTER the pipeline, not before
    it, so it pads to a target instead of adding on top and never blocks a stream's first byte."""
    if floor_s <= 0:
        return
    remaining = floor_s - (time.monotonic() - start)
    if remaining > 0:
        await asyncio.sleep(remaining)



async def _run_kt_if_applicable(settings: Any, body: LegacyRequest, text: str,
                                run_id: str) -> dict[str, Any] | None:
    if not getattr(settings, "poc_exploit_enabled", False):
        return None
    if not (getattr(settings, "poc_exploit_api_url", "") or "").strip():
        return None
    slots = _kt_slots_from_request(body, text)
    if not slots:
        return None
    try:
        return await run_kt_exploit(
            settings,
            ip=slots["ip"], cve=slots["cve"], port=slots.get("port"),
            command=slots.get("command") or "id",
            use_proxy=True, run_id=run_id,
        )
    except (PocError, KtClientError) as exc:
        return {
            "ok": False,
            "run_id": run_id,
            "error": str(exc),
            "exploit_verdict": "error",
            "target_ip": slots.get("ip"),
            "cve": slots.get("cve"),
            "target_port": slots.get("port"),
        }


# Total chars of FULL PoC source rendered into ONE exploit Section 1. The model fetches
# ≤ _MAX_FETCH_PER_RUN PoCs/run so the real set is small; this only backstops a pathological
# repo. Fullening happens AFTER the model runs → it never enters the model context.
_MAX_DISPLAY_CHARS = 2_000_000


async def _fullen_evidence_for_display(evidence: list[dict[str, Any]], poc: Any,
                                       budget: int = _MAX_DISPLAY_CHARS) -> list[dict[str, Any]]:
    """Swap each evidence file's context-truncated code for its FULL, untruncated bytes,
    read back from the content store via the PoC backend (same license gate + path-escape
    guard as poc_fetch). This runs at Section-1 injection time — AFTER the model produced its
    answer — so the DISPLAYED PoC (in `description`/`exploit_guide`, which the FE renders) is
    complete, while the model's own context stays bounded by the fetch-time caps. Bounded by
    `budget`; fail-open per file (a file that can't be re-read keeps its truncated block)."""
    if poc is None:
        return evidence
    total = 0
    for ev in evidence or []:
        # A consolidated (AI-merged) OR pruned (poc/slicer.py:prune_evidence) entry no longer
        # matches its stored bytes — `sha256` is the ORIGINAL artifact's, so re-reading by it
        # would overwrite the merged/pruned code with the untouched verbatim source (un-doing the
        # whole prune). Leave both exactly as produced.
        if ev.get("consolidated") or ev.get("pruned"):
            continue
        files = ev.get("source_files")
        if files:
            # First try to render the WHOLE repo, not just the files the fetch budget let in:
            # resolve the owning PoC from any file's hash and pull its complete set, full.
            entry_sha = next((f.get("sha256") for f in files if f.get("sha256")), None)
            full_set = None
            if entry_sha:
                try:
                    full_set = await poc.read_full_fileset(entry_sha)
                except Exception:
                    full_set = None
            if full_set:
                set_chars = sum(len(f.get("code") or "") for f in full_set)
                if total + set_chars <= budget:
                    ev["source_files"] = full_set          # ALL files, full content
                    total += set_chars
                    continue
            # Fallback (no stored set, or over budget): fullen each present file's content.
            for f in files:
                sha = f.get("sha256")
                if not sha:
                    continue
                try:
                    full = await poc.read_artifact(sha)
                except Exception:
                    continue
                content = full.get("content") or ""
                if not content or total + len(content) > budget:
                    continue
                f["code"], f["truncated"] = content, bool(full.get("truncated"))
                total += len(content)
        elif ev.get("source_code") and ev.get("sha256"):
            try:
                full = await poc.read_artifact(ev["sha256"])
            except Exception:
                continue
            content = full.get("content") or ""
            if not content or total + len(content) > budget:
                continue
            ev["source_code"], ev["code_truncated"] = content, bool(full.get("truncated"))
            total += len(content)
    return evidence


# language -> local air-gap file extension for the consolidated-file name in the model appendix
# (mirrors poc/inject.py _LANG_EXT for the handful of PoC languages we ever emit).
_APPENDIX_EXT = {
    "python": "py", "py": "py", "ruby": "rb", "rb": "rb", "php": "php", "javascript": "js",
    "js": "js", "typescript": "ts", "go": "go", "rust": "rs", "c": "c", "cpp": "cpp",
    "java": "java", "perl": "pl", "bash": "sh", "sh": "sh", "powershell": "ps1",
}


def _consolidated_local_name(consolidated: dict[str, Any], cve_id: str | None) -> str:
    """Air-gap local filename for the consolidated PoC (``<cve>.<ext>`` lowercased) — the name
    Mục 2 must reference, matching what poc/inject.py renders as the Section-1 header."""
    lang = str(consolidated.get("language") or "").strip().lower()
    ext = _APPENDIX_EXT.get(lang, lang or "txt")
    stem = (cve_id or "poc").strip().lower()
    return f"{stem}.{ext}" if ext else stem


# The consolidated file is a single bounded script produced by our OWN consolidation call
# (poc/consolidate.py caps its OUTPUT at ~_RESERVE_OUTPUT_TOKENS*_CHARS_PER_TOKEN = 36k chars
# worst case) — NOT a raw multi-file repo, so it doesn't need the tight shared
# `_PREFETCH_APPENDIX_BUDGET` (3000 chars) used for the T1-verbatim/T2/T3 evidence preview.
# A truncated preview here is a real correctness bug, not just a context-saving nicety: the
# argparse/CLI block a PoC needs for Mục 2 is often near the END of the file (after the class/
# functions), so a short head-only preview silently hides the real flags and the model falls
# back to plausible-looking-but-wrong ones (observed live — see CVE-2024-38856 eval). Give the
# model the WHOLE merged file, capped only as a pathological-size backstop.
_CONSOLIDATED_APPENDIX_MAX_CHARS = 40_000


def _consolidated_prompt_appendix(consolidated: dict[str, Any], cve_id: str | None) -> str:
    """Tell the model the consolidated single file WILL be Mục 1 (system-injected) so it writes
    Mục 2 (Cách chạy) against exactly that file + Mục 3 — never Mục 1 itself. Includes the FULL
    merged code (see budget note above) so run steps are grounded in the real file, including
    argparse/CLI blocks that often sit near the end, not just the first few KB."""
    name = _consolidated_local_name(consolidated, cve_id)
    code = consolidated.get("source_code") or ""
    if not code:
        # Pruned MULTI-FILE entry (poc/slicer.py:prune_evidence) has no single `source_code` —
        # ground the run-steps/CLI surface on its ENTRY file so the model still targets the real
        # main script (the full file set is what gets injected as Mục 1 via render_files).
        _files = consolidated.get("source_files") or []
        _entry = next((f for f in _files if (f.get("role") or "") == "entry"),
                      _files[0] if _files else None)
        code = (_entry.get("code") if _entry else "") or ""
    truncated = len(code) > _CONSOLIDATED_APPENDIX_MAX_CHARS
    preview = code[:_CONSOLIDATED_APPENDIX_MAX_CHARS]
    cut_note = " (đã cắt bớt do quá dài)" if truncated else ""
    # Deterministic CLI surface (argparse AST) — the ground truth for the run-steps, so the model
    # names the REAL flags/subcommands even when the code preview is truncated (argparse blocks
    # often sit past the 40k preview cut; the model otherwise invents plausible-but-wrong flags,
    # observed live on CVE-2026-60137). Computed from the FULL code, independent of `preview`.
    cli = cli_surface(code)
    cli_lines: list[str] = []
    if cli.get("subcommands"):
        cli_lines.append("subcommands: " + ", ".join(cli["subcommands"]))
    if cli.get("flags"):
        cli_lines.append("cờ: " + ", ".join(cli["flags"]))
    if cli.get("positionals"):
        cli_lines.append("đối số vị trí: " + ", ".join(cli["positionals"]))
    cli_ground = ("\nGiao diện dòng lệnh THẬT của tệp (trích tự động từ mã — chỉ mô tả đúng các "
                  "tên này, không bịa thêm cờ/đối số nào khác):\n- " + "\n- ".join(cli_lines)) \
        if cli_lines else ""
    # Author README (if the entry had one): the primary Mục 2 ground truth — real setup, CLI
    # examples, ports and prerequisites the model cannot infer from code alone. Fed verbatim
    # (bounded) so the model translates it instead of guessing. README wins over the CLI surface
    # when both are present; CLI surface is the fallback (no README, or README has no usage).
    readme = (consolidated.get("readme_text") or "").strip()
    if readme:
        readme_budget = 8_000
        readme_preview = readme[:readme_budget]
        readme_cut = " (đã cắt bớt)" if len(readme) > readme_budget else ""
        readme_ground = (
            "\nTÀI LIỆU HƯỚNG DẪN CHẠY THẬT của tác giả PoC (README/hướng dẫn gốc đã fetch)"
            f"{readme_cut} — DỊCH lại thành Mục 2 bằng tiếng Việt, bám ĐÚNG các lệnh/tham số/cổng/"
            "tiền đề trong đó, không bịa thêm:\n---\n"
            + wrap_untrusted_content(readme_preview, source="consolidated_poc_readme") + "\n---"
        )
    else:
        readme_ground = ""
    return (
        "\n\n[Hệ thống đã hợp nhất PoC công khai đã fetch thành MỘT tệp cục bộ tên "
        f"`{name}` và sẽ TỰ chèn nó làm Mục 1 — bạn KHÔNG viết Mục 1 (không viết `## 1.`, "
        "không dán lại mã). Nhiệm vụ của bạn: viết Mục 2 (Cách chạy PoC) bám ĐÚNG tệp "
        f"`{name}` này — lệnh chạy, tham số (kể cả các cờ argparse/CLI thật xuất hiện ở BẤT KỲ "
        "đâu trong mã, kể cả cuối tệp), tiền đề — dựa trên chính đoạn mã dưới đây; và Mục 3 "
        "(Tổng quan Lỗ hổng). Chỉ dùng thông tin có trong mã, không bịa endpoint/tham số/"
        f"phiên bản/cờ dòng lệnh không tồn tại.{readme_ground}{cli_ground} "
        "Giữ air-gap: không tải/clone/cài từ internet.\n"
        f"Mã tệp `{name}`{cut_note} (KHÔNG chép lại vào câu trả lời):\n"
        + wrap_untrusted_content(preview, source="consolidated_poc") + "\n]"
    )


def _github_evidence_from_fetch(entry_bytes: bytes, entry_rel: str,
                                file_set: list[tuple[str, bytes]], row: dict[str, Any],
                                cve_id: str | None,
                                fileset_truncated: bool = False) -> dict[str, Any]:
    """Turn a request-path on-demand GitHub tarball fetch (fetch_github.fetch_repo_code) into
    the same evidence-dict shape poc_fetch produces, so consolidation + Section-1 injection
    treat it identically to a pre-fetched catalog PoC. Full bytes (no truncation).

    ``fileset_truncated`` (from ``fetch_repo_code``'s 4th return value) flags that
    ``_pick_set``'s file-count/byte cap discarded a candidate repo file for THIS fetch — a
    provenance signal (see 2026-08 research finding silent-dependency-drop), not a hard error:
    the entry file itself is always complete, only a same-repo sibling may be missing."""
    from ..poc.fetch_github import _is_doc_file
    entry_text = entry_bytes.decode("utf-8", errors="replace")
    entry_sha = hashlib.sha256(entry_bytes).hexdigest()
    lang = Path(entry_rel).suffix.lstrip(".") or None
    cves = [cve_id.upper()] if cve_id else []
    base = {
        "source": "poc_github",
        "source_ref": row.get("source_ref") or entry_rel,
        "url": row.get("url") or "",
        "kind": "exploit",
        "title": (row.get("source_ref") or entry_rel or "GitHub PoC"),
        "description": "",
        "cve_ids": cves,
        "language": lang,
        "sha256": entry_sha,
        "code_truncated": False,
        "candidate_status": "draft_not_executed",
        "static_indicators": {"language": lang, "on_demand": True,
                              "fileset_truncated": bool(fileset_truncated)},
    }
    if len(file_set) > 1:
        source_files: list[dict[str, Any]] = []
        for frel, fdata in file_set:
            role = ("entry" if frel == entry_rel
                    else "readme" if _is_doc_file(Path(frel).name) else "support")
            source_files.append({
                "path": frel, "code": fdata.decode("utf-8", errors="replace"),
                "sha256": hashlib.sha256(fdata).hexdigest(),
                "language": Path(frel).suffix.lstrip(".") or None,
                "role": role, "truncated": False,
            })
        base["source_files"] = source_files
        base["source_code"] = entry_text
    else:
        base["source_code"] = entry_text
        base["source_files"] = None
    return base


async def _ondemand_github_evidence(poc: Any, cve_id: str, timeout: int) -> list[dict[str, Any]]:
    """Fetch a link-only GitHub PoC's code on demand for ``cve_id`` (highest-star, link-only
    first) and return it as real-code evidence — or ``[]`` on any miss. Blocking tarball I/O is
    off-loaded to a thread; stops at the first repo that yields a picked PoC. Never raises."""
    from ..poc.fetch_github import fetch_repo_code
    catalog = getattr(poc, "catalog", None)
    if catalog is None:
        return []
    try:
        rows = await asyncio.to_thread(catalog.github_candidates, cve_id, 3)
    except Exception:
        logger.exception("github_candidates lookup failed for %s", cve_id)
        return []
    for row in rows:
        try:
            prov = json.loads(row.get("provenance") or "{}")
        except (ValueError, TypeError):
            prov = {}
        branch = prov.get("default_branch") or "master"
        try:
            fetched = await asyncio.to_thread(
                fetch_repo_code, row.get("source_ref") or "", row.get("url") or "",
                branch, cve_id, timeout)
        except Exception:
            logger.info("on-demand github fetch errored for %s (%s)", cve_id, row.get("url"))
            continue
        if fetched:
            entry_bytes, entry_rel, file_set, fileset_truncated = fetched
            return [_github_evidence_from_fetch(
                entry_bytes, entry_rel, file_set, row, cve_id, fileset_truncated)]
    return []


def register_routes(app: FastAPI, state: Any) -> None:
    """Attach every gateway endpoint to ``app`` (closures capture the shared state)."""


    def _chat_tool_registry() -> list[str]:
        """Bug #13/CQ4: the ``tool_registry`` VALUE only — key stays, shape stays list[str].
        Internal retrieval-tool names (qdrant_search/neo4j_search/poc_search/fetch_reference/
        web_fetch) are gateway-internal detail; hidden (empty list) from the public chat
        metadata by default, restored in full only when ``expose_internal_tool_metadata`` is
        explicitly turned on for debugging. Scoped to the "chat" profile only (the profile the
        bug report is about) — other profiles' tool_registry (translate/report/exploit_research)
        are untouched."""
        if getattr(state.settings, "expose_internal_tool_metadata", False):
            return sorted(registry_for("chat"))
        return []

    async def complete(profile: str, body: LegacyRequest, *, prompt_override: str | None = None,
                       session_id: str | None = None,
                       grounding_context: str | None = None,
                       keep_cjk: bool = False,
                       raw_output: bool = False) -> tuple[str, dict[str, Any]]:
        scope_context: dict[str, str] = {}
        if prompt_override is not None:
            # Endpoint already built the task-specific user message (translate/report/…).
            prompt = prompt_override
        else:
            prompt = require_public_input(body.supplied_text())
            grounding: dict[str, Any] | None = None
            if profile == "chat":
                gated = await _chat_gate(prompt, session_id, scope_context)
                if gated is not None:  # model classifier short-circuited (injection/off-topic)
                    return gated
                prompt, grounding = await _ground_chat_prompt(prompt, session_id)
                grounding_context = _grounding_context_text(grounding)
        try:
            # grounding_context is delivered to the model via LinchRunner's ephemeral
            # per-turn context channel (agent/hooks.py::EphemeralTurnContextHook), never
            # baked into `prompt` — passed only when truthy so a runner double whose
            # complete()/stream() doesn't accept this kwarg still works for scenarios that
            # never trigger chat grounding. NOTE (2026-09-21, Opus review round 2): a
            # runner double that DOES get exercised with a real CVE/CWE/CAPEC-id message
            # (grounding truthy) MUST still accept this kwarg — the kwarg then always gets
            # passed, and a double missing it raises TypeError inside this try, which the
            # broad `except Exception` below silently downgrades to a 503 (a real failure
            # got misread as a passing "handles-503-cleanly" test in the past — see
            # tests/test_chat_grounding_duration_regression.py's ``_FakeRunner``).
            text, metadata = await state.runner.complete(
                profile, prompt, session_id,
                **({"grounding_context": grounding_context} if grounding_context else {}),
                **({"scope_context": scope_context["text"]} if scope_context else {}),
            )
        except HTTPException:
            raise
        except Exception as exc:  # provider/runtime failure → clean 503, not an opaque 500
            # Mirrors the streaming path (see stream()), which already surfaces failures
            # as an SSE error event instead of tearing down the response.
            raise HTTPException(status_code=503, detail=f"AI service unavailable: {exc}") from exc
        if profile == "chat":
            # Bug #13/CQ4: state.runner.complete() (agent/runner.py) builds tool_registry from
            # the full profile.tool_names for every profile — gate it back down to the public
            # default here too. This is the path a PLAIN GREETING takes (no gate short-circuit
            # fires), so without this the fix in _chat_gate()'s 3 refusal branches below would
            # only cover refusals, not the common case the bug report calls out.
            metadata = {**metadata, "tool_registry": _chat_tool_registry()}
        # pre-fetch grounding (xem _ground_chat_prompt) không qua tool-call của model nên
        # không có mặt trong tool_registry/tool-log thông thường — lộ ra đây để client JSON
        # thường (không SSE) cũng biết nguồn đã dùng, tương ứng tool_call/tool_result giả
        # lập bên stream().
        #
        # H2 (2026-09-22): this used to spread the WHOLE `grounding` dict in — including
        # `result`, the raw KB dump (up to several KB of chunk text) that fed the model's
        # ephemeral context. No FE consumer reads `metadata.sources` (grepped: 0 hits in
        # secai-fe-devspace) so nothing renders it, but a plain JSON API client legitimately
        # inspecting "what did this answer cite" got the entire internal KB payload dumped
        # back at it instead of a citation. Redact to citation-only fields — same query/
        # summary the synthetic SSE tool_call/tool_result pair (_grounding_sse_frames) shows
        # a streaming client, just without the raw `result` body.
        if prompt_override is None and grounding:
            metadata = {**metadata, "sources": [{
                "name": "qdrant_search",
                "query": grounding.get("query"),
                "summary": grounding.get("summary"),
            }]}
        if raw_output:
            # Translation is lossless by contract: the chat-oriented scrubbers (meta-narration,
            # tool-call echo, reasoning-leak, CJK strip, HTML/JS defang) delete or rewrite
            # legitimate translated content, so they are bypassed (translate has no tools).
            return text, metadata
        return sanitize_output_language(text, keep_cjk=keep_cjk), metadata

    def base_meta(profile: str, run_id: str | None) -> dict[str, Any]:
        # Bug #13/CQ4: only "chat" is gated (public tool_registry default → []); other
        # profiles (translate/report/exploit_research) are out of the bug's scope and keep
        # their existing full list.
        tool_registry = _chat_tool_registry() if profile == "chat" else sorted(registry_for(profile))
        return {"run_id": run_id, "profile": profile, "tool_registry": tool_registry}

    def stream(profile: str, prompt: str, session_id: str | None, build_final,
              pre_frames: list[bytes] | None = None,
              grounding_context: str | None = None, scope_context: str | None = None) -> StreamingResponse:
        """Shared SSE runner for every agentic/text endpoint: streams tool_call /
        tool_result / token events as they happen, then one final event built by the
        endpoint's ``build_final(final_text, run_id)`` (which may also persist state).

        ``pre_frames`` — frame(s) to yield BEFORE the agent runs at all (currently: the
        synthetic tool_call/tool_result pair from ``_grounding_sse_frames()`` when
        ``_ground_chat_prompt()`` pre-fetched KB context — that fetch bypasses the model's
        own tool-use decision, so it never appears in ``state.runner.stream()``'s real
        event stream; without this the client's ToolTimeline has nothing to render even
        though the answer IS grounded).

        A ``regenerate`` event (see api/sse.py) can arrive mid-stream when a hook forces
        the model to redo its answer (e.g. it answered before calling required tools) —
        clients accumulating `token` deltas into one buffer MUST clear that buffer on
        ``regenerate``, or the rejected draft and the corrected one render concatenated
        with no boundary between them. The final `final` event is always the corrected,
        authoritative text regardless — only the raw `token` stream is affected."""

        async def gen() -> AsyncIterator[bytes]:
            for frame in pre_frames or []:
                yield frame
            final = ""
            run_id = None
            failed = False
            try:
                # See complete()'s comment: grounding_context only passed when truthy — but
                # any runner double exercised with a grounding-triggering message (real
                # CVE/CWE/CAPEC-id) must accept this kwarg, or the call raises inside this
                # try and gets silently folded into the generic "stream failed" error path.
                async for event in state.runner.stream(
                    profile, prompt, session_id,
                    **({"grounding_context": grounding_context} if grounding_context else {}),
                    **({"scope_context": scope_context} if scope_context else {}),
                ):
                    if isinstance(event, SystemEvent):
                        run_id = getattr(event, "run_id", None) or run_id
                    if isinstance(event, ResultEvent):
                        final = sanitize_output_language(event.final_text or "")
                    frame = event_to_sse(event)
                    if frame:
                        yield frame
            except Exception:  # surface runtime failures as a visible message, not a broken/blank stream
                failed = True
                # Log the real cause server-side (the client only gets a friendly message).
                logger.exception("stream failed for profile=%s", profile)
                # error frame for UIs that handle it, PLUS token+final so token-only
                # UIs still show the failure instead of an empty assistant bubble.
                yield sse_frame("error", {"error": STREAM_ERROR_MESSAGE})
                yield sse_frame("token", {"delta": STREAM_ERROR_MESSAGE})
                yield sse_frame("final", await build_final(STREAM_ERROR_MESSAGE, run_id))
            if not failed:
                yield sse_frame("final", await build_final(final, run_id))
            yield b"data: [DONE]\n\n"

        return StreamingResponse(gen(), media_type="text/event-stream")

    async def _resolve_followup_entity_id(session_id: str | None) -> str | None:
        """🅱2 lớp 1 (entity-follow-up): khi câu hỏi HIỆN TẠI không nêu ID tường minh
        nhưng là một câu hỏi kiểu "có CVE nào?" / "còn CVE nào khác không?" / "CWE
        nào?" (route ``id_lookup``, xem ``classify_chat_grounding``), thực thể cần tra
        thường nằm ở LƯỢT HỎI TRƯỚC của cùng phiên, không phải ở câu hiện tại.

        Chỉ quét các message role=``user`` ĐÃ PERSIST trong session store (linch
        ``load_messages``), từ MỚI NHẤT về CŨ NHẤT, trả về entity-id đầu tiên khớp
        ``ENTITY_ID_RES`` (CVE/CWE/CAPEC/ATT&CK). Cố tình KHÔNG qt message role=
        ``assistant``: câu trả lời trước có thể trích dẫn nhiều CVE ví dụ không liên
        quan trực tiếp đến chủ đề người dùng đang hỏi (vd trả lời "CWE-89 là gì?" có
        thể nêu vài CVE minh hoạ) — quét cả hai vai sẽ dễ resolve nhầm sang một entity
        không phải chủ đề hội thoại. Chủ đề hội thoại luôn do NGƯỜI DÙNG đặt ra.

        Fail-open: không có session_id / session rỗng / lỗi store → None (giữ nguyên
        hành vi cũ — không pre-fetch, hook vẫn ép model tự tra như trước)."""
        if not session_id:
            return None
        try:
            rows = await state.runner.session_store.load_messages(session_id)
        except Exception:
            logger.exception("follow-up entity resolve: load_messages lỗi cho sid=%s", session_id)
            return None
        for row in reversed(rows):
            message = getattr(row, "message", None)
            if message is None or getattr(message, "role", None) != "user":
                continue
            for block in message.content or []:
                text = getattr(block, "text", None)
                if not text:
                    continue
                for regex in ENTITY_ID_RES.values():
                    match = regex.search(text)
                    if match:
                        return match.group(0).upper()
        return None

    async def _ground_chat_prompt(prompt: str, session_id: str | None = None,
                                  ) -> tuple[str, dict[str, Any] | None]:
        """Chủ động tra KB (qdrant_search) TRƯỚC khi đưa prompt vào agent, nếu câu hỏi
        nêu CVE/CWE/CAPEC/ATT&CK-id tường minh — KHÔNG phụ thuộc việc model tự quyết định
        gọi tool hay không.

        Lý do: cơ chế ép grounding có sẵn (``RequireChatGroundingHook.on_before_final_answer``,
        agent/hooks.py) CÓ được linch dispatch (correction 2026-09-26 — claim "INERT" trước đây
        là chẩn đoán sai, xem agent/hooks.py::chat_final_text docstring) nhưng KHÔNG đủ: nó chỉ
        ép model gọi tool trong một số lần retry giới hạn rồi bỏ cuộc nếu model vẫn không gọi —
        không đảm bảo context tồn tại. Đo thật 2026-09-19 (KPI-VA-GEN-TEST, 1000 câu độc lập):
        46.9% câu trả lời có ``tools: []`` — model không tra KB — và với câu có CVE-id cụ
        thể, model thường bịa nhầm sang 1 CVE nổi tiếng khác có id gần giống (vd
        CVE-2021-37630 bị trả lời như thể là CVE-2021-44228/Log4Shell). Faithfulness đo
        được chỉ 58-63% (ngưỡng hợp đồng >98%).

        Tra cứu xảy ra ở tầng code, không qua model, nên đảm bảo có context bất kể model
        có gọi tool hay không. Fail-open: KB lỗi/timeout → trả nguyên prompt gốc, không
        chặn chat.

        Trả về ``(prompt, grounding)`` — ``grounding`` là dict {query, result, summary,
        duration_ms} khi thật sự tra được, hoặc ``None`` khi không match entity-id / KB
        lỗi / rỗng. ``prompt`` LUÔN là bản gốc KHÔNG đổi (kể cả khi tra được) — 2026-09-21
        (chat-history-leak fix): trước đây hàm này trả về ``prompt`` đã bị CHÈN THẲNG
        marker+KB dump ở đầu, và runner lại persist NGUYÊN chuỗi đó vào session store làm
        user turn (linch: ``session.run(prompt)`` → ``build_user_message(prompt)`` →
        ``session.append(...)``) — khiến việc quay lại chatbot hiển thị lộ toàn bộ context
        nội bộ. Grounding giờ được deliver cho model qua kênh ephemeral riêng (xem
        ``_grounding_context_text`` + ``agent/runner.py``/``agent/hooks.py::
        EphemeralTurnContextHook``), KHÔNG còn qua chuỗi ``prompt`` — nên user turn được
        lưu/hiển thị luôn là bản gốc sạch.

        2026-09-21 (trước đó, cùng ngày): trước NỮA chỉ trả về prompt, khiến FE mất tín
        hiệu "nguồn" — vì pre-fetch này thay hẳn việc model tự gọi tool, nên KHÔNG còn
        tool_call/tool_result SSE event nào phát ra cho UI (ToolTimeline.tsx) render trích
        dẫn, dù model vẫn dùng đúng context. Gọi nơi này (chat_stream/session_chat_stream)
        tự dựng cặp tool_call/tool_result giả lập từ ``grounding`` để khôi phục tín hiệu
        nguồn cho FE.

        🅱2 lớp 1 (2026-09-26, entity-follow-up): khi câu hỏi HIỆN TẠI không nêu ID
        tường minh, KHÔNG còn bỏ cuộc ngay — nếu câu thuộc route ``id_lookup``
        (``classify_chat_grounding``: "CVE nào?", "còn CVE nào khác không?", "CWE
        nào?", … mà không kèm ID) thì thử resolve entity từ LƯỢT HỎI TRƯỚC của cùng
        phiên (``_resolve_followup_entity_id``) rồi tra KB bằng entity đó + câu hỏi
        hiện tại. Trước fix này route ``id_lookup`` không có ID luôn rơi vào nhánh
        early-return này → không pre-fetch → hook ép model tự gọi tool → Cyber-F1 (mô
        hình nhỏ) thường không gọi/gọi sai → hết ``max_retries`` → mutate xin lỗi đè
        lên draft đã stream ("giật" — xem ``RequireChatGroundingHook`` + FE
        ToolTimeline). Route ``taxonomy``/``concept`` không đổi (vẫn early-return
        None) — lớp 1 CHỈ mở rộng cho id_lookup, KHÔNG đụng semantics `missing_web`."""
        try:
            dated = await _date_grounding(prompt)
        except Exception:
            logger.exception("date grounding lookup lỗi — dùng nguyên prompt gốc")
            dated = None
        if dated is not None:
            return prompt, dated
        query = prompt
        if not any(regex.search(prompt or "") for regex in ENTITY_ID_RES.values()):
            plan = classify_chat_grounding(prompt)
            if plan.route != "id_lookup":
                return prompt, None
            entity_id = await _resolve_followup_entity_id(session_id)
            if not entity_id:
                return prompt, None
            query = f"{entity_id} {prompt}".strip()
        try:
            # top_k=10 (không phải 5): khi có cve_id pin, _qdrant_search dùng "EXACT-CVE FAST
            # PATH" (retrieval/qdrant.py) — scroll max(top_k,8) dòng theo THỨ TỰ LƯU TRỮ
            # QDRANT (không xếp hạng độ liên quan) rồi cắt cứng về top_k. Với top_k=5, 1 CVE
            # có >5 chunk (core/context/…) có thể rơi mất chunk "context" (chứa KEV/severity)
            # một cách ngẫu nhiên — đo thật 2026-09-20: câu hỏi KEV-status trả lời sai vì thiếu
            # đúng chunk. top_k=10 đủ dư cho số chunk/CVE thực tế mà vẫn không làm loãng context.
            _t0 = time.monotonic()
            result = await _qdrant_search(query=query, ctx=None, top_k=10)
            grounding_ms = int(round((time.monotonic() - _t0) * 1000))
        except Exception:
            logger.exception("pre-fetch grounding lookup lỗi — dùng nguyên prompt gốc, không chặn chat")
            return prompt, None
        if not result.content:
            return prompt, None
        # duration_ms: đo thời gian tra qdrant THẬT ở bước pre-fetch để event tool_result
        # giả lập (xem _grounding_sse_frames) hiển thị đúng độ trễ thay vì hard-code 0ms.
        # "query" phản ánh CHUỖI THẬT đã gửi cho qdrant_search (có thể là entity-id resolved
        # + câu hỏi gốc, không chỉ câu hỏi gốc) — minh bạch cho FE ToolTimeline/API JSON.
        return prompt, {"query": query, "result": result.content,
                        "summary": result.summary, "duration_ms": grounding_ms}

    def _grounding_context_text(grounding: dict[str, Any] | None) -> str | None:
        """Builds the KB-grounding block delivered to the model via LinchRunner's
        per-turn ephemeral context channel (agent/hooks.py::EphemeralTurnContextHook) —
        NOT appended to the persisted user turn (see ``_ground_chat_prompt``'s docstring
        for why). Same marker/content shape the old inline prompt used, minus the
        trailing "[Câu hỏi của người dùng]\\n"+prompt echo — that's redundant now since
        the real question already IS the (clean, separately-persisted) user turn earlier
        in the same request; this block is appended as one more message right AFTER it
        (see EphemeralTurnContextHook's docstring for why it's a message, not a system
        block), so the model still sees question-then-context, immediately before
        generating its answer."""
        if not grounding:
            return None
        return (
            CHAT_PRE_FETCH_MARKER + " — PHẢI dùng để trả lời chính xác, "
            "KHÔNG bịa/suy đoán khi thiếu thông tin]\n" + grounding["result"]
        )

    def _grounding_sse_frames(grounding: dict[str, Any] | None) -> list[bytes]:
        """Dựng cặp SSE tool_call/tool_result giả lập từ kết quả pre-fetch của
        ``_ground_chat_prompt`` — cùng shape với ``event_to_sse`` (api/sse.py) thật, để FE
        (``ToolTimeline.tsx``) render đúng như khi model tự gọi ``qdrant_search``.

        H3 (2026-09-22): ``tool_result`` now always carries the same clean ``summary`` the
        real ``qdrant_search`` produced (previously only the ``tool_call`` frame had it) and
        shrinks ``result`` to a short preview (``_SSE_RESULT_PREVIEW_CHARS``) instead of the
        raw KB dump — ToolTimeline only reads ``summary``/first-140-chars-of-``result``, so
        the rest was pure wire weight with no FE reader."""
        if not grounding:
            return []
        tool_id = f"pre-fetch-{uuid4()}"
        summary = grounding.get("summary")
        return [
            sse_frame("tool_call", {
                "id": tool_id, "name": "qdrant_search",
                "input": {"query": grounding["query"]}, "summary": summary,
            }),
            sse_frame("tool_result", {
                "id": tool_id, "name": "qdrant_search", "is_error": False,
                "duration_ms": int(grounding.get("duration_ms", 0) or 0),
                "summary": summary,
                "result": (grounding.get("result") or "")[:_SSE_RESULT_PREVIEW_CHARS],
            }),
        ]

    async def _chat_gate(prompt: str, session_id: str | None = None,
                         scope_context: dict[str, str] | None = None) -> tuple[str, dict[str, Any]] | None:
        """Model-based input sanitization for the chat path (runs BEFORE the agent).

        Embeds the prompt with the deployed sec-embedding model and applies the numpy head
        (secai_gateway.sanitize) to classify intent + risk. Returns a short-circuit
        ``(refusal_text, metadata)`` when the classifier blocks (injection / off-topic), or
        ``None`` to let the agent answer. FAIL-OPEN: any classifier error/unavailability, or
        the feature being disabled, returns ``None`` (pass through)."""
        # Language gate (runs first, no embedding needed): the classifier head was trained on
        # vi/en only, so a message predominantly in a non-Latin script (CJK/kana/Hangul/
        # Cyrillic/…) is refused with a bilingual ask to use Vietnamese or English. Latin-script
        # languages pass through to the classifier. Independent of input_classifier_enabled.
        if getattr(state.settings, "language_gate_enabled", True) and is_unsupported_language(
                prompt, ratio=getattr(state.settings, "language_gate_nonlatin_ratio", 0.5)):
            run_id = uuid4().hex
            logger.info("input-sanitize run_id=%s action=refuse_language", run_id)
            _audit(run_id, "input_sanitize", prompt,
                   {"label": "unsupported_language", "action": "refuse_language", "pii": False})
            metadata = {
                "run_id": run_id, "profile": "chat",
                "tool_registry": _chat_tool_registry(),  # bug #13/CQ4: gated, see helper above
                "input_classification": {"label": "unsupported_language",
                                         "action": "refuse_language", "blocked": True, "pii": False},
                "short_circuit": True,
            }
            return UNSUPPORTED_LANGUAGE_MSG, metadata
        # Unicode-smuggling injection gate (loop iter 4, FT-A): a prompt-injection payload hidden
        # in non-rendering codepoints (TAG block / variation selectors / zero-width / interlinear)
        # is invisible to the sec-embedding model, so the classifier head cannot see it. Catch it
        # deterministically BEFORE embedding and refuse as an injection. High-precision — legit
        # emoji / ZWJ / flag-tag sequences never trip it (see security.has_hidden_smuggling).
        if getattr(state.settings, "input_classifier_enabled", True) and has_hidden_smuggling(prompt):
            run_id = uuid4().hex
            logger.info("input-sanitize run_id=%s action=block_injection reason=unicode_smuggling",
                        run_id)
            _audit(run_id, "input_sanitize", prompt,
                   {"label": "injection", "action": "block_injection", "pii": False})
            classification = {"label": "injection", "action": "block_injection",
                              "blocked": True, "pii": False, "reason": "unicode_smuggling"}
            metadata = {
                "run_id": run_id, "profile": "chat",
                "tool_registry": _chat_tool_registry(),  # bug #13/CQ4: gated, see helper above
                "input_classification": classification, "short_circuit": True,
            }
            return REFUSE_INJECTION_MSG, metadata
        if not getattr(state.settings, "input_classifier_enabled", True):
            return None
        run_id = uuid4().hex
        try:
            verdict = await classify_input(prompt, settings=state.settings,
                                           client=getattr(state, "http_client", None))
            if getattr(state.settings, "chat_scope_judge_enabled", False):
                decision = decide(verdict, state.settings, text=prompt)
                if not (verdict.injection and is_direct_override(prompt)):
                    history = ""
                    if session_id:
                        rows = await state.runner.session_store.load_messages(session_id)
                        history = json.dumps([
                            {"role": row.message.role, "text": "\n".join(
                                getattr(b, "text", "") or "" for b in row.message.content or [])[:1500]}
                            for row in rows[-4:]
                            if getattr(row, "message", None) is not None
                        ], ensure_ascii=False)
                    judge_started = time.monotonic()
                    scope = await judge_chat_scope(
                        state.runner, prompt, verdict.scores.get("injection", 0.0), history=history,
                        timeout_s=state.settings.injection_cascade_timeout_s)
                    if scope is None:
                        decision = Decision(Action.SCOPE_UNAVAILABLE,
                            "Dịch vụ kiểm tra phạm vi tạm thời không khả dụng. Vui lòng thử lại.", "scope_unavailable")
                    elif scope.injection:
                        decision = Decision(Action.BLOCK_INJECTION, REFUSE_INJECTION_MSG, "injection", pii=verdict.pii)
                    elif scope.scope == "out_of_scope":
                        decision = Decision(Action.REFUSE_OFFTOPIC, REFUSE_OFFTOPIC_MSG, "offtopic", pii=verdict.pii)
                    elif scope.scope == "chitchat":
                        decision = Decision(Action.GREET_CHITCHAT, GREET_MSG, "chitchat", pii=verdict.pii)
                    elif scope.scope == "ambiguous":
                        decision = Decision(Action.ASK_CLARIFICATION,
                            "Bạn cần hỗ trợ vấn đề An toàn thông tin nào? Vui lòng nói rõ câu hỏi.", "ambiguous")
                    else:
                        decision = Decision(Action.PASS, "", "chitchat" if scope.scope == "chitchat" else "security", pii=verdict.pii)
                        if scope.scope == "mixed" and scope_context is not None:
                            scope_context["text"] = json.dumps(list(scope.security_requests), ensure_ascii=False)
                    logger.info("chat-scope run_id=%s scope=%s injection=%s action=%s judge_latency_ms=%.1f",
                                run_id, scope.scope if scope else "unavailable", scope.injection if scope else None,
                                decision.action.value, (time.monotonic() - judge_started) * 1000)
            else:
                confirmed_injection = False
                # Ư2 uncertain-band cascade: an ambiguous (calibrated) injection score gets a
                # second opinion from the LLM judge instead of a threshold coin-flip. Only fires
                # when a calibration asset is shipped (injection_uncertain) AND the flag is on;
                # ANY judge failure (timeout/unreachable/malformed) returns None and this falls
                # straight back to the classifier's own threshold decision — never a new failure
                # mode. See sanitize/cascade.py.
                if (verdict.ok and verdict.injection_uncertain
                        and getattr(state.settings, "injection_cascade_enabled", False)):
                    # Prefer the calibrated score (the one band-membership was actually decided on)
                    # if shipped; falls back to the raw score for display when it isn't.
                    cascade_score = verdict.scores.get("injection_calibrated",
                                                       verdict.scores.get("injection", 0.0))
                    judge_verdict = await judge_injection(
                        state.runner, prompt, cascade_score,
                        timeout_s=getattr(state.settings, "injection_cascade_timeout_s", 8.0))
                    if judge_verdict is not None:
                        logger.info(
                            "input-sanitize-cascade run_id=%s score=%.3f threshold_verdict=%s "
                            "judge_verdict=%s", run_id, cascade_score, verdict.injection, judge_verdict)
                        verdict = replace(verdict, injection=judge_verdict)
                        confirmed_injection = judge_verdict
                decision = decide(verdict, state.settings, text=prompt)
                # A confident head prediction can still misclassify ATTT quizzes and scenarios.
                if (verdict.ok and not confirmed_injection and not is_direct_override(prompt)
                        and decision.action in (Action.BLOCK_INJECTION, Action.REFUSE_OFFTOPIC)):
                    adjudication = await judge_security_input(
                        state.runner, prompt, verdict.scores.get("injection", 0.0),
                        timeout_s=getattr(state.settings, "injection_cascade_timeout_s", 8.0))
                    if adjudication is not None:
                        injection, security = adjudication
                        verdict = replace(verdict, injection=injection,
                                          intent="security" if security else "offtopic")
                        decision = decide(verdict, state.settings, text=prompt)
                        if not injection and not security:
                            decision = Decision(Action.REFUSE_OFFTOPIC, REFUSE_OFFTOPIC_MSG, "offtopic", pii=verdict.pii)
                        logger.info("input-security-judge run_id=%s injection=%s security=%s action=%s",
                                    run_id, injection, security, decision.action.value)
        except Exception:  # a guard must never break chat
            if getattr(state.settings, "chat_scope_judge_enabled", False):
                logger.exception("chat scope check failed; returning temporary unavailability")
                return "Dịch vụ kiểm tra phạm vi tạm thời không khả dụng. Vui lòng thử lại.", {
                    "run_id": run_id, "profile": "chat", "tool_registry": [], "short_circuit": True,
                    "input_classification": {"label": "scope_unavailable", "action": "scope_unavailable", "blocked": True},
                }
            logger.exception("input classifier failed → fail-open")
            return None

        classification = {
            "label": decision.label,
            "action": decision.action.value,
            "blocked": decision.short_circuit,
            "pii": decision.pii,
            "scores": {k: round(v, 4) for k, v in verdict.scores.items()},
            "latency_ms": round(verdict.latency_ms, 1),
        }
        # Fail-open observability: a verdict that is not ``ok`` means the guard was bypassed
        # (embed down / embed-model mismatch / timeout). That is safe but SILENT, so surface it
        # at WARNING with the running unknown-rate rather than only INFO (see CLAUDE.md incident).
        note_verdict(verdict.ok)
        if not verdict.ok:
            st = verdict_stats()
            logger.warning("input-sanitize FAIL-OPEN run_id=%s reason=%s unknown_rate=%.3f (%d/%d)",
                           run_id, verdict.reason, st["unknown_rate"], st["unknown"], st["total"])
        logger.info("input-sanitize run_id=%s verdict=%s action=%s latency_ms=%.1f",
                    run_id, verdict.summary(), decision.action.value, verdict.latency_ms)
        # Persist an audit line (digest, not plaintext) for any block or PII hit.
        if decision.short_circuit or decision.pii:
            _audit(run_id, "input_sanitize", prompt,
                   {"label": decision.label, "action": decision.action.value, "pii": decision.pii})

        if not decision.short_circuit:
            return None
        metadata = {
            "run_id": run_id, "profile": "chat",
            "tool_registry": _chat_tool_registry(),  # bug #13/CQ4: gated, see helper above
            "input_classification": classification, "short_circuit": True,
        }
        return decision.message, metadata

    def _short_circuit_stream(message: str, metadata: dict[str, Any]) -> StreamingResponse:
        """SSE response for a classifier short-circuit: one token + one final, then DONE
        (same frame shape as chat_stream's build_final, so token-only UIs still render it)."""
        async def gen() -> AsyncIterator[bytes]:
            yield sse_frame("token", {"delta": message})
            yield sse_frame("final", {"content": message, "answer": message,
                                      "message": message, "metadata": metadata})
            yield b"data: [DONE]\n\n"
        return StreamingResponse(gen(), media_type="text/event-stream")

    @app.post("/chat_no_stream")
    @app.post("/ai/chat")
    async def chat(body: LegacyRequest) -> dict[str, Any]:
        text, metadata = await complete("chat", body, session_id=_chat_session_id(body))
        return {"response": text, "answer": text, "message": text, "metadata": metadata}

    @app.post("/ai/chat/stream")
    async def chat_stream(body: LegacyRequest) -> StreamingResponse:
        text = require_public_input(body.supplied_text())
        session_id = _chat_session_id(body)
        scope_context: dict[str, str] = {}
        gated = await _chat_gate(text, session_id, scope_context)
        if gated is not None:  # model classifier short-circuited before the agent runs
            return _short_circuit_stream(gated[0], gated[1])
        text, grounding = await _ground_chat_prompt(text, session_id)

        async def build_final(final: str, run_id: str | None) -> dict[str, Any]:
            # linch persists the turn to its session store during the run — nothing to do
            # here. `text` passed to stream() below is the ORIGINAL clean prompt (grounding
            # goes through the ephemeral context channel instead), so the persisted turn is
            # clean too — see _ground_chat_prompt()'s docstring.
            return {"content": final, "answer": final, "message": final, "metadata": base_meta("chat", run_id)}

        return stream("chat", text, session_id, build_final, pre_frames=_grounding_sse_frames(grounding),
                      grounding_context=_grounding_context_text(grounding), scope_context=scope_context.get("text"))

    async def _stamp_session_activity(sid: str, rec: Any, body: LegacyRequest) -> None:
        """Best-effort bookkeeping after a session-scoped chat turn: bump ``last_activity``
        and backfill a ``title`` from the first ~60 chars of the user's message if the
        session doesn't already have one. ``rec`` is the SessionRecord loaded BEFORE the
        turn ran — used only as a fallback if the fresh re-load below fails for some
        reason. We re-``load(sid)`` right before deciding the title (rather than trusting
        ``rec.meta``) because a concurrent ``PATCH /sessions/{sid}`` rename can land while
        the turn is in flight; deciding off the stale pre-turn snapshot would blow that
        rename away by re-deriving a title from this message. Wrapped in try/except — a
        session-store hiccup here must never turn a good reply into a failed request."""
        try:
            fresh = await state.runner.session_store.load(sid)
            meta = (fresh.meta if fresh is not None else rec.meta) or {}
            updates: dict[str, Any] = {"last_activity": datetime.now(timezone.utc).isoformat()}
            if not meta.get("title"):
                title = (body.supplied_text() or "").strip().replace("\n", " ")[:60]
                if title:
                    updates["title"] = title
            await state.runner.session_store.update_meta(sid, updates)
        except Exception:
            logger.exception("session meta stamp failed for sid=%s", sid)

    @app.post("/sessions/{sid}/chat")
    async def session_chat(sid: str, body: LegacyRequest) -> dict[str, Any]:
        """Session-scoped sibling of ``/ai/chat``: the caller picks the linch session id
        explicitly via the URL (instead of it being derived from a hashed ``user_id``), so
        multiple sessions per user/project are addressable. 404s on an unknown session
        rather than silently creating one — sessions are provisioned via
        ``POST /projects/{pid}/sessions`` (see api/projects.py). 403s on an owner
        mismatch — mutation (a chat turn is appended to the session) — so a caller
        holding someone else's ``sid`` can't inject turns / exfiltrate context."""
        rec = await state.runner.session_store.load(sid)
        if rec is None:
            raise HTTPException(status_code=404, detail="session not found")
        if (rec.meta or {}).get("owner") != _norm_owner(body.user_id):
            raise HTTPException(status_code=403, detail="not the session owner")
        text, metadata = await complete("chat", body, session_id=sid)
        await _stamp_session_activity(sid, rec, body)
        return {"response": text, "answer": text, "message": text, "metadata": metadata}

    @app.post("/sessions/{sid}/chat/stream")
    async def session_chat_stream(sid: str, body: LegacyRequest) -> StreamingResponse:
        """Session-scoped sibling of ``/ai/chat/stream`` — same SSE framing, same input
        gate, but ``sid`` (from the URL) is the runner session id. The existence check
        happens before any streaming starts (mirrors how ``require_public_input`` already
        raises before this handler's non-streaming counterpart above does), so an unknown
        session gets a clean 404 instead of a 200 SSE stream carrying an error frame. The
        owner check (403 on mismatch) happens right alongside it, before any streaming
        starts, for the same reason."""
        rec = await state.runner.session_store.load(sid)
        if rec is None:
            raise HTTPException(status_code=404, detail="session not found")
        if (rec.meta or {}).get("owner") != _norm_owner(body.user_id):
            raise HTTPException(status_code=403, detail="not the session owner")
        text = require_public_input(body.supplied_text())
        # Stamp last_activity/auto-title HERE — once, before the input gate and before
        # any streaming starts — rather than in build_final on completion. The FE aborts
        # the stream (client disconnect) when the user switches chats mid-turn, which
        # raises asyncio.CancelledError inside stream()'s generator; that is NOT an
        # Exception subclass, so it skips the `except Exception` in stream() and
        # build_final() never runs. A first turn cut off that way would otherwise never
        # get a title/last_activity and would reload as "Cuộc trò chuyện mới" even
        # though the FE had already shown the message as the title. Stamping here still
        # preserves the two cases that matter: a 422 from require_public_input above
        # never stamps (no turn happened), and a gate refusal below still counts as a
        # turn and is still stamped (it already happened, by this same call).
        await _stamp_session_activity(sid, rec, body)
        scope_context: dict[str, str] = {}
        gated = await _chat_gate(text, sid, scope_context)
        if gated is not None:  # model classifier short-circuited before the agent runs
            return _short_circuit_stream(gated[0], gated[1])
        text, grounding = await _ground_chat_prompt(text, sid)

        async def build_final(final: str, run_id: str | None) -> dict[str, Any]:
            # linch persists the turn to its session store during the run; the meta
            # bookkeeping (title/last_activity) already happened above, before streaming
            # started — see the comment there for why. `text` is the ORIGINAL clean prompt
            # (see _ground_chat_prompt()'s docstring), so the persisted turn is clean too.
            return {"content": final, "answer": final, "message": final, "metadata": base_meta("chat", run_id)}

        return stream("chat", text, sid, build_final, pre_frames=_grounding_sse_frames(grounding),
                      grounding_context=_grounding_context_text(grounding), scope_context=scope_context.get("text"))

    # Defensive cleanup for very large/unusual (e.g. raw-JSON) chunks: the model
    # occasionally echoes its own instruction preamble back verbatim instead of only
    # translating past it. Reproduced 2026-09-12 on a >1MB CVE-detail-JSON translate
    # input — each per-chunk echo landed mid-string after rejoin, splitting words in
    # half (e.g. "...ji[Hôm nay là ...]\nDịch văn bản sau...:\n\nffies..."). Applied
    # per-chunk (inside render(), below) so a leak never survives into the join.
    # Two independent fragments can leak, together or alone (observed both: the date
    # bracket alone with no instruction line, and the full "date + instruction" combo
    # mid-chunk) — stripped as two separate passes rather than one combined pattern so
    # either survives without the other.
    _DATE_BRACKET_RE = re.compile(r"\[Hôm nay là [^\]]*\]\s*")
    # Bound raised 400 → 2000 (TR2/F1 fix, 2026-09-22): messages.build_translate_prompt's
    # instruction "head" grew from ~150-300 chars to up to ~1410 chars once the TR2
    # anti-injection spotlighting guard (_INJECTION_GUARD) was added in front of the wrapped
    # input — the old 400-char cap silently stopped matching the (now longer) head, so an
    # echoed preamble — INCLUDING the anti-injection guard text itself — leaked straight into
    # the translated output instead of being stripped. Anchored on the
    # `<untrusted_retrieved_content` wrapper tag (always immediately follows the head's
    # trailing blank line — see build_translate_prompt) so the match reliably covers the
    # WHOLE head regardless of its exact length, with the raised numeric cap kept as a
    # backstop (never eats arbitrarily far into real translated content if the tag anchor
    # doesn't apply for some reason) and a bare `\n\n?` fallback for that same case.
    _INSTRUCTION_RE = re.compile(
        r"Dịch văn bản sau\b.{0,2000}?(?:\n\n(?=<untrusted_retrieved_content\b)|\n\n?)",
        re.DOTALL,
    )
    # F3 fix (2026-09-22): strip the `<untrusted_retrieved_content>` spotlighting wrapper
    # itself out of the OUTPUT if the model echoes it verbatim (open tag, close tag, or the
    # angle-escaped `&lt;...&gt;` form a forged closing tag inside the input is neutralized
    # to — see security.wrap_untrusted_content) — belt-and-suspenders alongside
    # _INSTRUCTION_RE, which only strips the head that precedes the tag, not the tag itself.
    _WRAPPER_TAG_RE = re.compile(
        r"(?:<|&lt;)/?untrusted_retrieved_content(?:\s[^>&]*)?(?:>|&gt;)\s*", re.IGNORECASE)

    def _strip_prompt_echo(text: str, source: str = "") -> str:
        """Remove an echoed instruction head / date preamble / spotlight wrapper from MODEL
        output (call BEFORE restoring literals, while they are still opaque placeholders).
        An echo pattern is only stripped when the same phrase is not part of the user's
        ``source`` text, so legitimate content that happens to contain it is never eaten.
        The wrapper tag is always safe to strip: a tag in the source is masked as a literal."""
        cleaned = text
        if not _DATE_BRACKET_RE.search(source):
            cleaned = _DATE_BRACKET_RE.sub("", cleaned)
        if not _INSTRUCTION_RE.search(source):
            cleaned = _INSTRUCTION_RE.sub("", cleaned)
        return _WRAPPER_TAG_RE.sub("", cleaned)

    _LEADING_THINK_RE = re.compile(r"\A\s*<think>.*?</think>\s*", re.DOTALL)

    # A caller can post a whole JSON record as the text to translate — the ias.id.vn
    # datalake page sends the entire "cves_detail" object. Round-tripping that through
    # the model as ONE string is what produced garbled output: the same description is
    # mirrored 6-9× across nvd_json/mitre_json/kb_json/history (measured: 17-50 total
    # occurrences behind only 4-18 DISTINCT strings), so every copy was re-translated,
    # and at that size the model also lost the instruction/content boundary. Instead,
    # walk the object and translate every prose value wherever it sits in the tree,
    # deduplicated — one model call per distinct string, reused at each occurrence —
    # then splice the results back into the original structure. Identifiers, enums,
    # URLs, versions, CVSS vectors, CPEs and timestamps stay byte-identical; translating
    # those would corrupt the record.
    _PROSE_KEYS = frozenset({"title", "description", "value", "summary", "new", "old", "data"})
    _NON_PROSE_PREFIXES = ("http://", "https://", "cpe:", "CVSS:", "/")

    def _is_prose(text: str) -> bool:
        """Prose = long enough to be a sentence AND multi-word. Timestamps, ids, enums,
        severities, versions, hashes and single-token paths all fail one of the two."""
        stripped = text.strip()
        return (len(stripped) >= 12 and " " in stripped
                and not stripped.startswith(_NON_PROSE_PREFIXES))

    def _collect_prose(node: Any, key: str | None, seen: set[str], out: list[str]) -> None:
        if isinstance(node, dict):
            for child_key, value in node.items():
                _collect_prose(value, child_key, seen, out)
        elif isinstance(node, list):
            for item in node:  # a list inherits its parent's key ("descriptions": [...])
                _collect_prose(item, key, seen, out)
        elif isinstance(node, str) and key in _PROSE_KEYS and _is_prose(node) and node not in seen:
            seen.add(node)
            out.append(node)

    def _replace_prose(node: Any, key: str | None, translated: dict[str, str]) -> Any:
        if isinstance(node, dict):
            return {k: _replace_prose(v, k, translated) for k, v in node.items()}
        if isinstance(node, list):
            return [_replace_prose(item, key, translated) for item in node]
        if isinstance(node, str) and key in _PROSE_KEYS:
            return translated.get(node, node)
        return node

    def _prose_values(text: str) -> tuple[dict[str, Any], list[str]] | None:
        """(parsed object, distinct prose values) for JSON-object input, else None."""
        try:
            obj = json.loads(text)
        except (json.JSONDecodeError, TypeError, ValueError):
            return None
        if not isinstance(obj, dict):
            return None
        seen: set[str] = set()
        values: list[str] = []
        _collect_prose(obj, None, seen, values)
        return (obj, values) if values else None

    def _guard_chunk_cap(count: int) -> None:
        """One request must not fan out into unbounded model calls."""
        if count > state.settings.translate_max_chunks:
            raise HTTPException(413, f"Văn bản quá dài để dịch (>{state.settings.translate_max_chunks} "
                                     "khối). Vui lòng chia nhỏ yêu cầu.")

    # TR5 fix (2026-09-22): source_lang/target_lang used to flow straight into
    # build_translate_prompt() with zero validation — it just interpolates whatever string
    # arrives ("Dịch văn bản sau từ {src} sang {tgt}."). A typo'd/garbage code (e.g.
    # source_lang="xx", target_lang="zz") silently produced an unrecognizable instruction
    # instead of failing — reproduced live as HTTP 200 with a silently-ignored code instead
    # of an error. Validate against the real ISO 639-1 two-letter codes — a superset that
    # covers everything the gateway documents/tests (vi, en — see docs/api-guide.md §3 and
    # tests/test_translate_mask.py) while keeping any other language already reachable
    # through this same permissive field (e.g. "fr") working, unchanged. `None`/omitted
    # (→ the documented bidirectional vi/en auto-detect default) and the explicit sentinel
    # "auto" are always valid: they mean "unspecified", not a language choice, so there is
    # nothing to validate.
    _ISO_639_1_LANGS = frozenset({
        "aa", "ab", "ae", "af", "ak", "am", "an", "ar", "as", "av", "ay", "az",
        "ba", "be", "bg", "bh", "bi", "bm", "bn", "bo", "br", "bs",
        "ca", "ce", "ch", "co", "cr", "cs", "cu", "cv", "cy",
        "da", "de", "dv", "dz",
        "ee", "el", "en", "eo", "es", "et", "eu",
        "fa", "ff", "fi", "fj", "fo", "fr", "fy",
        "ga", "gd", "gl", "gn", "gu", "gv",
        "ha", "he", "hi", "ho", "hr", "ht", "hu", "hy", "hz",
        "ia", "id", "ie", "ig", "ii", "ik", "io", "is", "it", "iu",
        "ja", "jv",
        "ka", "kg", "ki", "kj", "kk", "kl", "km", "kn", "ko", "kr", "ks", "ku", "kv", "kw", "ky",
        "la", "lb", "lg", "li", "ln", "lo", "lt", "lu", "lv",
        "mg", "mh", "mi", "mk", "ml", "mn", "mr", "ms", "mt", "my",
        "na", "nb", "nd", "ne", "ng", "nl", "nn", "no", "nr", "nv", "ny",
        "oc", "oj", "om", "or", "os",
        "pa", "pi", "pl", "ps", "pt",
        "qu",
        "rm", "rn", "ro", "ru", "rw",
        "sa", "sc", "sd", "se", "sg", "si", "sk", "sl", "sm", "sn", "so", "sq", "sr", "ss",
        "st", "su", "sv", "sw",
        "ta", "te", "tg", "th", "ti", "tk", "tl", "tn", "to", "tr", "ts", "tt", "tw", "ty",
        "ug", "uk", "ur", "uz",
        "ve", "vi", "vo",
        "wa", "wo",
        "xh",
        "yi", "yo",
        "za", "zh", "zu",
    })

    def _validate_lang_code(code: Any, field: str) -> None:
        """400 if `code` is a non-empty value that isn't a real ISO 639-1 code. Omitted/None
        (the pre-existing auto-detect default) and the explicit sentinel "auto" are always
        accepted — they carry no language-code claim to validate, so must NOT be rejected."""
        if code is None:
            return
        normalized = str(code).strip().lower()
        if not normalized or normalized == "auto":
            return
        if normalized not in _ISO_639_1_LANGS:
            raise HTTPException(
                400,
                f"{field} không hợp lệ: {code!r}. Dùng mã ngôn ngữ ISO 639-1 (vd: vi, en) "
                "hoặc bỏ trống/'auto' để tự nhận diện.",
            )

    def _validate_translate_langs(body: LegacyRequest) -> None:
        src, tgt = translate_langs(body)
        _validate_lang_code(src, "source_lang")
        _validate_lang_code(tgt, "target_lang")

    # Chunks below this aren't worth the per-call overhead and start costing coherence
    # (each chunk is translated without its neighbours' context).
    _MIN_CHUNK_CHARS = 800

    def _chunk_budget(text_len: int) -> int:
        """Size chunks to fill the concurrency budget in ONE wave instead of always cutting
        at the configured ceiling: a 4k-char value at ceiling 6000 is a single sequential
        call (measured 13.9s), while the same value split into ~concurrency pieces
        translates in parallel (4.0s). Short text still returns the ceiling, so it stays a
        single call — and the ceiling still bounds very long input, so the max translatable
        size (max_chunks × ceiling) is unchanged."""
        ceiling = state.settings.translate_chunk_chars
        if text_len <= _MIN_CHUNK_CHARS:
            return ceiling
        target = text_len // max(1, state.settings.translate_chunk_concurrency)
        return max(_MIN_CHUNK_CHARS, min(ceiling, target))

    def _split_adaptive(text: str) -> list[tuple[str, str]]:
        return split_for_translation(text, _chunk_budget(len(text)))

    def _plan_value(text: str) -> tuple[list[str], list[tuple[str, str]]]:
        """Mask the WHOLE value once (so a label/value pair split by a chunk boundary stays
        protected), then chunk the MASKED text (placeholders are atomic). Returns
        ``(literals, [(masked_chunk, sep_after), …])``."""
        masked, literals = mask_literals(text)
        return literals, _split_adaptive(masked)

    def _target_writes_han(tgt: Any) -> bool:
        """True when the requested translation target is written with Han ideographs.

        The legacy endpoint is what the IAS text panel calls (``e.translation``), so the
        chat-oriented CJK strip in sanitize_output_language() must not run on a zh/ja
        target (TXT-DIRECT-vi-ja / TXT-DIRECT-vi-zh-Hans, 2026-10-09)."""
        if not isinstance(tgt, str):
            return False
        code = tgt.strip().lower()
        return code in {"zh", "ja"} or code.startswith("zh-")

    def _translate_plan(body: LegacyRequest) -> tuple[list[str], list[tuple[str, str]], Any, Any]:
        """Mask + split the input and enforce the max-chunk cap (413). Returns
        (literals, masked chunks, src, tgt).

        TR4/TR5 fix (2026-09-22): reject an empty/whitespace-only input up front (400) — no
        text means there is nothing to translate, so there is no legitimate reason to still
        call the model (which would otherwise either echo nothing back, giving the old
        200-empty bug its input, or burn a call for no reason) — and validate source_lang/
        target_lang before any model call, not after."""
        if not body.supplied_text().strip():
            raise HTTPException(400, "Thiếu nội dung cần dịch (text rỗng).")
        _validate_translate_langs(body)
        src, tgt = translate_langs(body)
        literals, chunks = _plan_value(body.supplied_text())
        _guard_chunk_cap(len(chunks))
        return literals, chunks, src, tgt

    def _integrity_error(detail: str) -> HTTPException:
        return HTTPException(502, detail={
            "code": "TRANSLATION_INTEGRITY",
            "message": ("Dịch thất bại: mô hình làm mất/đổi dữ liệu được bảo vệ (tài khoản, mật khẩu, "
                        "URL, IP…) nên kết quả không được trả về để tránh mất thông tin. "
                        "Vui lòng thử lại. (translation integrity check failed) " + detail),
        })

    def _is_integrity_error(exc: BaseException) -> bool:
        return (isinstance(exc, HTTPException) and isinstance(exc.detail, dict)
                and exc.detail.get("code") == "TRANSLATION_INTEGRITY")

    async def translate_assemble(body: LegacyRequest) -> tuple[str, dict[str, Any]]:
        """Translate the whole input losslessly. Every value is masked ONCE (literals →
        ``⟦Ln⟧``), the masked text is chunked (so no model turn exceeds its output cap and
        no label/value pair is split unprotected), each chunk is translated at temperature 0
        with NO output scrubbing, and each chunk's placeholders are verified (every one
        exactly once, none stray) before the originals are restored verbatim. An incomplete
        chunk is retried once; if still incomplete the request fails with 502
        TRANSLATION_INTEGRITY — never a silent 200 with lost data.

        TR5 fix (2026-09-22): validated here (before EITHER the record or the plain-text
        branch below runs) so a garbage source_lang/target_lang 400s regardless of which
        shape the input takes."""
        _validate_translate_langs(body)
        src, tgt = translate_langs(body)
        sem = asyncio.Semaphore(max(1, state.settings.translate_chunk_concurrency))

        async def render(chunk_masked: str, literals: list[str], source: str) -> str:
            expected = placeholder_ids(chunk_masked)
            base_prompt = build_translate_prompt_masked(chunk_masked, src, tgt)
            last = "no attempt"
            for attempt in range(2):
                prompt = base_prompt
                if attempt:
                    names = ", ".join(f"⟦L{i}⟧" for i in expected)
                    prompt += ("\n\nLƯU Ý BẮT BUỘC: bản dịch trước đã làm mất/đổi ký hiệu giữ chỗ. "
                               f"Hãy chép NGUYÊN VĂN, đúng một lần mỗi ký hiệu: {names}.")
                out, _meta = await complete("translate", body, prompt_override=prompt,
                                            keep_cjk=True, raw_output=True)
                if "<think" not in source:
                    out = _LEADING_THINK_RE.sub("", out)
                out = _strip_prompt_echo(out, source)  # BEFORE restore: literals still opaque
                if not out.strip():
                    # TR4 fix (2026-09-22): a blank completion used to flow through as a
                    # "successful" 200 with translated_text="" — surface it as a real error.
                    raise HTTPException(
                        502,
                        "Dịch thất bại: mô hình trả về kết quả rỗng (translation failed / empty completion).",
                    )
                try:
                    return restore_checked(out, literals, expected)
                except PlaceholderIntegrityError as exc:
                    last = str(exc)
                    logger.warning("translate integrity check failed (attempt %d): %s", attempt + 1, exc)
            raise _integrity_error(last)

        async def render_bounded(chunk_masked: str, literals: list[str], source: str) -> str:
            async with sem:
                return await render(chunk_masked, literals, source)

        async def render_value(literals: list[str], chunks: list[tuple[str, str]], source: str) -> str:
            """Translate one value's chunks concurrently and rejoin on the exact separators."""
            outs = await asyncio.gather(*(render_bounded(c, literals, source) for c, _sep in chunks))
            return "".join(out + sep for out, (_c, sep) in zip(outs, chunks))

        record = _prose_values(body.supplied_text())
        if record is not None:
            obj, values = record
            plans = [_plan_value(v) for v in values]
            _guard_chunk_cap(sum(len(chunks) for _lits, chunks in plans))
            outs = await asyncio.gather(*(
                render_value(lits, chunks, v) for v, (lits, chunks) in zip(values, plans)))
            meta = base_meta("translate", None)
            meta["prose_values_translated"] = len(values)
            return json.dumps(_replace_prose(obj, None, dict(zip(values, outs))), ensure_ascii=False), meta

        literals, chunks, _src, _tgt = _translate_plan(body)
        source = body.supplied_text()
        if len(chunks) == 1:
            return await render(chunks[0][0], literals, source), base_meta("translate", None)
        final = await render_value(literals, chunks, source)
        meta = base_meta("translate", None)
        meta["chunks"] = len(chunks)
        return final, meta

    @app.post("/ai/translate")
    async def translate(body: LegacyRequest) -> dict[str, Any]:
        text, metadata = await translate_assemble(body)
        return {"translated_text": text, "translation": text, "metadata": metadata}

    def _sse_assemble_stream(body: LegacyRequest) -> AsyncIterator[bytes]:
        """Assemble via translate_assemble() and emit as one token+final SSE frame. True
        token streaming is traded for guaranteed completeness: placeholders can only be
        verified/restored on the whole chunk, and the per-token Han/HTML scrubbing of the
        generic stream path would delete or rewrite translated content."""
        async def gen() -> AsyncIterator[bytes]:
            try:
                final, meta = await translate_assemble(body)
            except Exception as exc:  # provider/runtime failure → visible error, not a torn stream
                if _is_integrity_error(exc):
                    # Never present an apology as the "translation": explicit error + empty text.
                    msg = exc.detail["message"]  # type: ignore[union-attr]
                    yield sse_frame("error", {"error": msg, "code": "TRANSLATION_INTEGRITY"})
                    yield sse_frame("final", {"content": "", "translation": "", "translated_text": "",
                                              "error": msg, "code": "TRANSLATION_INTEGRITY",
                                              "metadata": base_meta("translate", None)})
                    yield b"data: [DONE]\n\n"
                    return
                logger.exception("chunked translate_stream failed")
                final, meta = STREAM_ERROR_MESSAGE, base_meta("translate", None)
                yield sse_frame("error", {"error": STREAM_ERROR_MESSAGE})
            yield sse_frame("token", {"delta": final})
            yield sse_frame("final", {"content": final, "translation": final,
                                      "translated_text": final, "metadata": meta})
            yield b"data: [DONE]\n\n"
        return gen()

    @app.post("/ai/translate/stream")
    async def translate_stream(body: LegacyRequest) -> StreamingResponse:
        record = _prose_values(body.supplied_text())
        if record is not None:
            # Same cap check as the plain-text path below, done BEFORE opening the stream —
            # otherwise an oversized JSON record would 200+SSE-error mid-stream instead of a
            # clean 413, breaking the "413 before streaming starts" contract for this path only.
            _, values = record
            _guard_chunk_cap(sum(len(_plan_value(v)[1]) for v in values))
        else:
            _translate_plan(body)  # raises 400/413 before streaming starts
        return StreamingResponse(_sse_assemble_stream(body), media_type="text/event-stream")

    @app.post("/ai/generate-report")
    async def report(body: LegacyRequest) -> dict[str, Any]:
        text, metadata = await complete("report", body, prompt_override=report_prompt(body))
        return {"report": text, "metadata": metadata}

    @app.post("/ai/generate-report/stream")
    async def report_stream(body: LegacyRequest) -> StreamingResponse:
        async def build_final(final: str, run_id: str | None) -> dict[str, Any]:
            return {"content": final, "report": final, "metadata": base_meta("report", run_id)}
        return stream("report", report_prompt(body), None, build_final)

    @app.post("/ai/generate-statistical-report")
    async def statistical_report(body: LegacyRequest) -> dict[str, Any]:
        text, metadata = await complete("statistical_report", body, prompt_override=statistical_prompt(body))
        # Legacy gemini shape: report_title / report_summary / report_detail (parsed from JSON).
        result = parse_statistical(text)
        table = statistical_detail_table(body)
        if table is not None:  # structured `detail[]` / `listData` given — merge table with model recommendations.
            result["report_detail"] = merge_detail_table(result.get("report_detail", ""), table)
        return {**result, "report": text, "statistical_report": text, "metadata": metadata}

    @app.post("/ai/generate-template-report", response_class=Response)
    async def generate_template_report(
        file: UploadFile | None = None,
        template_text: str | None = Form(None),
        data: str = Form(...),
        output_format: str = Form("docx"),
        file_format: str | None = Form(None),
        raw_binary: bool = Form(False)
    ):
        import os, tempfile, uuid, json, base64
        from fastapi import HTTPException
        from fastapi.responses import Response, JSONResponse
        from docxtpl import DocxTemplate
        import docx
        import docx.text.paragraph
        import unicodedata
        import re

        output_format_lower = str(output_format or "docx").strip().lower()
        if output_format_lower not in ["docx", "pdf", "markdown", "md", "text", "json"]:
            raise HTTPException(
                status_code=400,
                detail="output_format must be 'docx', 'pdf', 'markdown' (or 'md'), or 'json'"
            )
        
        try:
            scan_data = json.loads(data)
            if not isinstance(scan_data, dict):
                scan_data = {"raw_data": scan_data}
        except json.JSONDecodeError:
            raise HTTPException(status_code=400, detail="Invalid JSON in data field")

        def clean_text(text: str) -> str:
            if not text:
                return ""
            text = unicodedata.normalize('NFC', text)
            return re.sub(r'[\s\xa0\t]+', ' ', text).strip()

        def strip_accents(s: str) -> str:
            s = str(s).replace('đ', 'd').replace('Đ', 'd')
            return ''.join(c for c in unicodedata.normalize('NFD', s) if unicodedata.category(c) != 'Mn').lower()

        ROMAN_TO_INT = {
            "I": 1, "II": 2, "III": 3, "IV": 4, "V": 5,
            "VI": 6, "VII": 7, "VIII": 8, "IX": 9, "X": 10,
            "XI": 11, "XII": 12, "XIII": 13, "XIV": 14, "XV": 15
        }
        ROMAN_NUMS_PATTERN = r"(?:I|II|III|IV|V|VI|VII|VIII|IX|X|XI|XII|XIII|XIV|XV)"

        def extract_section_number(text: str) -> str | None:
            t = clean_text(text).strip()
            if not t:
                return None
            t = re.sub(r'^[#*_\s]+|[#*_\s]+$', '', t)
            # 1. Matches: "Mục 1", "Phần 2", "Chương 3", "Mục II", "Phần IV"
            m_pref = re.match(rf'^(?:mục|phần|chương|bài|điều)\s*(\d+(?:\.\d+)*|{ROMAN_NUMS_PATTERN})[\.\)\:\s-]?', t, flags=re.IGNORECASE)
            if m_pref:
                val = m_pref.group(1).upper()
                if val in ROMAN_TO_INT:
                    return str(ROMAN_TO_INT[val])
                return val.split('.')[0]
            # 2. Matches Roman: "I.", "II.", "III.", "IV.", "V.", etc.
            m_rom = re.match(rf'^({ROMAN_NUMS_PATTERN})[\.\)\:\s-]', t, flags=re.IGNORECASE)
            if m_rom:
                roman = m_rom.group(1).upper()
                if roman in ROMAN_TO_INT:
                    return str(ROMAN_TO_INT[roman])
            # 3. Matches standard digits: "1.", "1.1", "1)", "1:", "1 -", "1 "
            m_num = re.match(r'^(\d+(?:\.\d+)*)[\.\)\:\s-]', t)
            if m_num:
                return m_num.group(1).split('.')[0]
            # 4. Pure leading digits: "1", "2"
            m_pure = re.match(r'^(\d+)\b', t)
            if m_pure:
                return m_pure.group(1)
            return None

        def normalize_key(text: str) -> str:
            text = clean_text(text)
            text = re.sub(r'^[#*_\s]+', '', text)
            text = re.sub(r'[#*_\s]+$', '', text)
            text = re.sub(r'^\{\{\s*|\s*\}\}$', '', text)
            text = re.sub(r'[:\-.]+$', '', text).strip()
            text = re.sub(rf'^(?:mục|phần|chương|bài|điều)?\s*(?:\d+(?:\.\d+)*|{ROMAN_NUMS_PATTERN})[\.\)\:\s-]+', '', text, flags=re.IGNORECASE).strip()
            text = text.replace('_', ' ')
            return text.lower()

        def is_static_template_element(text: str) -> bool:
            if not text:
                return True
            t_clean = clean_text(text).strip()
            if not t_clean:
                return True
            t_noaccent = strip_accents(t_clean).lower()

            # 1. Jinja variable tags
            if "{{" in t_clean and "}}" in t_clean:
                return True

            # 2. Quốc hiệu, Tiêu ngữ
            if any(q in t_noaccent for q in ["cong hoa xa hoi chu nghia viet nam", "doc lap - tu do - hanh phuc", "doc lap – tu do – hanh phuc", "doc lap — tu do — hanh phuc"]):
                return True

            # 3. Ngày tháng, địa danh: "Hà Nội, ngày 25 tháng 8 năm 2026", "ngày ... tháng ... năm ..."
            if re.search(r'ngay\s+\d+\s+thang\s+\d+\s+nam\s+\d+', t_noaccent) or re.search(r'ngay\s*\.{2,}\s*thang', t_noaccent):
                return True

            # 4. Tiêu đề chung tài liệu: "Báo cáo", "Báo cáo đánh giá..."
            if t_noaccent in ["bao cao", "bao cao danh gia", "bao cao ket qua", "bao cao kiem thu", "phieu danh gia", "bien ban"]:
                return True
            if re.match(r'^(?:bao cao|phieu danh gia|bien ban)\b', t_noaccent) and extract_section_number(t_clean) is None:
                return True

            # 5. Kính gửi, Nơi nhận và các gạch đầu dòng người nhận (QUAN TRỌNG)
            if re.match(r'^(?:kinh gui|noi nhan)\b', t_noaccent):
                return True
            if re.match(r'^[\-•\+\*]\s+', t_clean) and extract_section_number(t_clean) is None:
                return True
            if t_clean.rstrip().endswith(';') and extract_section_number(t_clean) is None:
                return True
            recipient_keywords = [
                "thu truong", "ban giam doc", "dong chi", "cac dong chi", "lanh dao", "don vi",
                "nhu tren", "luu:", "luu vt", "phong ban", "ban chi huy"
            ]
            if extract_section_number(t_clean) is None and any(rk in t_noaccent for rk in recipient_keywords):
                return True

            # 6. Căn cứ, Thực hiện, Mục đích ban hành văn bản hành chính
            if re.match(r'^(?:can cu|thuc hien|nham|theo quyet dinh|theo ke hoach)\b', t_noaccent):
                return True

            # 7. Số hiệu văn bản
            if re.match(r'^so\s*[:\.]', t_noaccent):
                return True

            # 8. Tên cơ quan, đơn vị ban hành
            agency_keywords = [
                "bo quoc phong", "bo cong an", "bo thong tin", "bo tu lenh", "trung tam",
                "cuc an toan", "tap doan", "tong cong ty", "cong ty", "so thong tin",
                "phong an toan thong tin", "trung tam giam sat", "vien cong nghe"
            ]
            if extract_section_number(t_clean) is None and any(ak in t_noaccent for ak in agency_keywords):
                return True

            # 9. Khối chức vụ & chữ ký người ký
            sig_keywords = [
                "chi huy", "giam doc", "pho giam doc", "truong phong", "pho phong",
                "truong doan", "nguoi lap", "nguoi lap bao cao", "nguoi soan", "nguoi soan bao cao",
                "nguoi duyet", "xac nhan", "ki ten", "ky ten", "chu ky", "dai dien"
            ]
            if t_noaccent in sig_keywords:
                return True
            for sk in sig_keywords:
                if t_noaccent.startswith(sk + ":") or t_noaccent.startswith(sk + " -") or t_noaccent.startswith(sk + " –"):
                    return True

            # 10. Tên người ký hoặc dòng văn bản không phải đầu mục nội dung
            sec_num = extract_section_number(t_clean)
            if sec_num is None:
                sec_words = [
                    "tong quan", "chi tiet", "khuyen nghi", "ket luan", "lo hong",
                    "danh gia", "bien phap", "pham vi", "muc tieu", "noi dung",
                    "thoi gian", "phuong phap", "hien trang"
                ]
                has_sec_word = any(sw in t_noaccent for sw in sec_words)
                if not has_sec_word:
                    return True
                name_starts = ["nguyen", "tran", "le", "pham", "hoang", "huynh", "phan", "vu", "vo", "dang", "bui", "do", "ho", "ngo", "duong", "dinh"]
                if any(t_noaccent.startswith(ns + " ") for ns in name_starts):
                    return True

            return False

        def split_template_text(text: str) -> list[str]:
            if not text:
                return []
            text = text.replace("\\r\\n", "\n").replace("\\n", "\n").replace("\\r", "\n")
            text = text.replace("\r\n", "\n").replace("\r", "\n")
            
            initial_chunks = [c.strip() for c in text.split("\n") if c.strip()]
            pattern = r"(?<!^)(?=(?:\b(?<!Mục\s)(?<!Phần\s)(?<!Chương\s)\d+[\.\)\/:]|\b(?:I|II|III|IV|V|VI|VII|VIII|IX|X)[\.\)\/:]|\b(?:Mục|Phần|Chương)\s+\d+[\.\:\/]?|\bKí tên\b|\bKý tên\b|\bChữ ký\b)\s*)"
            
            final_headings = []
            for chunk in initial_chunks:
                chunk = re.sub(r"\s*[\-|–—|]\s*(?=(?:\b(?<!Mục\s)(?<!Phần\s)\d+[\.\)\/:]|\b(?:I|II|III|IV|V)[\.\)\/:]|\b(?:Mục|Phần)\s+\d+))", " ", chunk)
                sub_chunks = re.split(pattern, chunk, flags=re.IGNORECASE)
                for sc in sub_chunks:
                    sc = sc.strip()
                    if not sc:
                        continue
                    # If this chunk starts with a bullet/dash before a numbered heading, clean the leading dash
                    if re.match(r'^[\-•*–—|]\s*(?:\b(?<!Mục\s)(?<!Phần\s)\d+[\.\)\/:]|\b(?:I|II|III|IV|V)[\.\)\/:]|\b(?:Mục|Phần)\s+\d+)', sc, flags=re.IGNORECASE):
                        sc = re.sub(r'^[\-•*–—|]\s*', '', sc).strip()
                    # Strip trailing dashes, commas or pipes from splitting, but keep trailing semicolon for list items
                    sc = sc.rstrip('-–—|, \t')
                    if not sc:
                        continue
                    sig_match = re.match(r"^(kí tên|ký tên|chữ ký)[\s:\-]+(.+)$", sc, flags=re.IGNORECASE)
                    if sig_match:
                        final_headings.append(sig_match.group(1).strip())
                        final_headings.append(sig_match.group(2).strip())
                    else:
                        final_headings.append(sc)
            return final_headings

        effective_template_text = (template_text or "").strip()
        if not effective_template_text and isinstance(scan_data, dict):
            effective_template_text = str(scan_data.get("template_text") or scan_data.get("template") or "").strip()

        file_bytes = b""
        if file is not None and getattr(file, "filename", None):
            try:
                file_bytes = await file.read()
            except Exception:
                file_bytes = b""

        if not file_bytes and not effective_template_text:
            raise HTTPException(
                status_code=400,
                detail="Vui lòng tải lên file template Word (.docx) hoặc nhập nội dung mẫu báo cáo (template_text)."
            )

        with tempfile.TemporaryDirectory() as tmpdir:
            input_docx = os.path.join(tmpdir, f"{uuid.uuid4()}.docx")
            if file_bytes:
                with open(input_docx, "wb") as f:
                    f.write(file_bytes)
            else:
                doc_init = docx.Document()
                for section in doc_init.sections:
                    section.top_margin = docx.shared.Inches(1.0)
                    section.bottom_margin = docx.shared.Inches(1.0)
                    section.left_margin = docx.shared.Inches(1.0)
                    section.right_margin = docx.shared.Inches(1.0)

                parsed_headings = split_template_text(effective_template_text)
                for line_s in parsed_headings:
                    p = doc_init.add_paragraph()
                    p.paragraph_format.space_before = docx.shared.Pt(3)
                    p.paragraph_format.space_after = docx.shared.Pt(3)
                    p.paragraph_format.line_spacing = 1.15
                    run = p.add_run(line_s)
                    run.font.name = "Times New Roman"
                    line_norm = normalize_key(line_s)
                    line_noaccent = strip_accents(line_s).lower()

                    if any(q in line_noaccent for q in ["cong hoa xa hoi chu nghia viet nam", "doc lap - tu do - hanh phuc", "doc lap – tu do – hanh phuc"]):
                        p.alignment = docx.enum.text.WD_ALIGN_PARAGRAPH.CENTER
                        run.bold = True
                        run.font.size = docx.shared.Pt(12)
                    elif re.search(r'ngay\s+\d+\s+thang\s+\d+\s+nam\s+\d+', line_noaccent):
                        p.alignment = docx.enum.text.WD_ALIGN_PARAGRAPH.RIGHT
                        run.italic = True
                        run.font.size = docx.shared.Pt(11)
                    elif line_norm in ["báo cáo", "bao cao"] or (line_noaccent.startswith("bao cao") and extract_section_number(line_s) is None):
                        p.alignment = docx.enum.text.WD_ALIGN_PARAGRAPH.CENTER
                        p.paragraph_format.space_before = docx.shared.Pt(14)
                        p.paragraph_format.space_after = docx.shared.Pt(12)
                        run.bold = True
                        run.font.size = docx.shared.Pt(16)
                    elif re.match(r'^(?:kinh gui|noi nhan)\b', line_noaccent):
                        p.paragraph_format.left_indent = docx.shared.Pt(14)
                        p.paragraph_format.space_before = docx.shared.Pt(8)
                        p.paragraph_format.space_after = docx.shared.Pt(2)
                        run.bold = True
                        run.font.size = docx.shared.Pt(12)
                    elif line_s.strip().startswith("-") or line_s.strip().startswith("•") or line_s.strip().endswith(";"):
                        p.paragraph_format.left_indent = docx.shared.Pt(28)
                        p.paragraph_format.space_before = docx.shared.Pt(1)
                        p.paragraph_format.space_after = docx.shared.Pt(2)
                        run.font.size = docx.shared.Pt(12)
                    elif extract_section_number(line_s) is not None or re.match(r'^\d+[\.\)]\s*', line_s):
                        p.paragraph_format.space_before = docx.shared.Pt(8)
                        p.paragraph_format.space_after = docx.shared.Pt(4)
                        run.bold = True
                        run.font.size = docx.shared.Pt(13)
                    elif line_norm in ["kí tên", "ki ten", "chữ ký", "chu ky"] or any(sk in line_noaccent for sk in ["chi huy", "nguoi soan bao cao", "nguoi lap"]):
                        p.alignment = docx.enum.text.WD_ALIGN_PARAGRAPH.RIGHT
                        p.paragraph_format.space_before = docx.shared.Pt(14)
                        run.bold = True
                        run.font.size = docx.shared.Pt(12)
                    elif any(line_noaccent.startswith(ns + " ") for ns in ["nguyen", "tran", "le", "pham", "hoang", "huynh", "phan", "vu", "vo", "dang", "bui", "do", "ho", "ngo", "duong", "dinh"]) and extract_section_number(line_s) is None:
                        p.alignment = docx.enum.text.WD_ALIGN_PARAGRAPH.RIGHT
                        run.bold = True
                        run.font.size = docx.shared.Pt(12)
                    else:
                        run.font.size = docx.shared.Pt(11)

                doc_init.save(input_docx)

            SEMANTIC_SYNONYMS = {
                "chi tiet": ["vulnerabilities", "details", "vulns", "lo hong", "danh sach lo hong", "chi tiet lo hong", "danh sach chi tiet", "cac lo hong", "ket qua chi tiet", "diem yeu", "diem yeu an ninh", "lo hong bao mat"],
                "tong quan": ["summary", "overview", "tong quan danh gia", "danh gia tong quan", "tong quan kiem thu", "khai quat", "khao sat", "muc tieu khao sat", "pham vi khao sat", "ket qua khao sat"],
                "thoi gian": ["scan_time", "time", "date", "ngay quet", "thoi gian quet", "thoi gian thuc hien", "thoi gian kiem thu", "thoi diem", "ngay lap"],
                "khuyen nghi": ["recommendations", "khuyen nghi khac phuc", "remediation", "giai phap", "bien phap", "phong ngua", "khac phuc", "bien phap khac phuc", "phuong an", "phuong an xu ly", "lo trinh", "lo trinh khac phuc"],
                "ket luan": ["conclusion", "danh gia chung", "assessment", "kien nghi", "ket luan chung"],
            }

            def find_matching_content(p_text: str, data_dict: dict, used_keys: set):
                if not p_text or not data_dict:
                    return None, None
                p_clean = clean_text(p_text)
                p_norm = normalize_key(p_text)
                p_norm_noaccent = strip_accents(p_norm)
                p_sec = extract_section_number(p_text)

                if p_text in data_dict and p_text not in used_keys:
                    return p_text, data_dict[p_text]

                for k, v in data_dict.items():
                    if k in used_keys:
                        continue
                    if clean_text(k) == p_clean:
                        return k, v

                for k, v in data_dict.items():
                    if k in used_keys:
                        continue
                    if normalize_key(k) == p_norm:
                        return k, v

                for k, v in data_dict.items():
                    if k in used_keys:
                        continue
                    if strip_accents(normalize_key(k)) == p_norm_noaccent:
                        return k, v

                for std_term, synonyms in SEMANTIC_SYNONYMS.items():
                    if p_norm_noaccent == std_term or any(syn in p_norm_noaccent for syn in synonyms):
                        for k, v in data_dict.items():
                            if k in used_keys:
                                continue
                            k_norm_noaccent = strip_accents(normalize_key(k))
                            if k_norm_noaccent == std_term or any(syn in k_norm_noaccent for syn in synonyms):
                                return k, v

                if p_sec:
                    for k, v in data_dict.items():
                        if k in used_keys:
                            continue
                        k_sec = extract_section_number(k)
                        if k_sec and k_sec == p_sec:
                            return k, v

                for k, v in data_dict.items():
                    if k in used_keys:
                        continue
                    k_norm = normalize_key(k)
                    if len(p_norm) >= 4 and (p_norm in k_norm or k_norm in p_norm):
                        return k, v

                return None, None

            def format_vulnerabilities_table(vulns: Any, sec_num: str = "2") -> str:
                if not isinstance(vulns, list) or not vulns:
                    return ""
                sub_heading = f"{sec_num}.1. Bảng danh sách chi tiết các lỗ hổng an toàn thông tin phát hiện được:" if sec_num else "Bảng danh sách chi tiết các lỗ hổng an toàn thông tin phát hiện được:"
                lines = [
                    sub_heading,
                    "| STT | Tên lỗ hổng | Mức độ | Mô tả chi tiết | Biện pháp khắc phục |",
                    "|---|---|---|---|---|"
                ]
                for idx, v in enumerate(vulns, 1):
                    if isinstance(v, dict):
                        name = (v.get("name") or v.get("title") or v.get("vuln_name") or v.get("cve") or f"Lỗ hổng #{idx}").replace("|", "-")
                        sev = (v.get("severity") or v.get("level") or v.get("risk") or "Trung bình").replace("|", "-")
                        desc = (v.get("description") or v.get("desc") or v.get("detail") or v.get("details") or "").replace("|", "-").replace("\n", " ")
                        rec = (v.get("recommendation") or v.get("solution") or v.get("fix") or v.get("remediation") or v.get("mitigation") or "").replace("|", "-").replace("\n", " ")
                        lines.append(f"| {idx} | {name} | {sev} | {desc} | {rec} |")
                    else:
                        lines.append(f"| {idx} | {str(v).replace('|', '-')} | Thông tin | - | - |")
                return "\n".join(lines)

            def format_summary_table(scan_data_dict: Any, sec_num: str = "1") -> str:
                if not isinstance(scan_data_dict, dict):
                    return ""
                vulns = scan_data_dict.get("vulnerabilities") or scan_data_dict.get("chi_tiet") or scan_data_dict.get("details") or scan_data_dict.get("lo_hong") or []
                summary = scan_data_dict.get("summary") or scan_data_dict.get("statistics")
                if not isinstance(summary, dict):
                    summary = {}
                
                def _to_cnt(val):
                    if isinstance(val, int):
                        return val
                    if isinstance(val, str) and val.strip().isdigit():
                        return int(val.strip())
                    return 0

                sum_lower = {str(k).lower(): v for k, v in summary.items()} if isinstance(summary, dict) else {}
                crit_count = _to_cnt(sum_lower.get("critical") or sum_lower.get("nghiem_trong") or sum_lower.get("nghiêm trọng"))
                high_count = _to_cnt(sum_lower.get("high") or sum_lower.get("cao"))
                med_count = _to_cnt(sum_lower.get("medium") or sum_lower.get("trung_binh") or sum_lower.get("trung bình"))
                low_count = _to_cnt(sum_lower.get("low") or sum_lower.get("thap") or sum_lower.get("thấp"))

                if not (crit_count or high_count or med_count or low_count) and isinstance(vulns, list):
                    for v in vulns:
                        if isinstance(v, dict):
                            sev = str(v.get("severity") or v.get("level") or v.get("risk") or "").lower()
                            if "crit" in sev or "nghiêm trọng" in sev or "nghiem trong" in sev:
                                crit_count += 1
                            elif "high" in sev or "cao" in sev:
                                high_count += 1
                            elif "med" in sev or "trung bình" in sev or "trung binh" in sev:
                                med_count += 1
                            elif "low" in sev or "thấp" in sev or "thap" in sev:
                                low_count += 1
                                
                total = crit_count + high_count + med_count + low_count
                if total == 0 and not vulns:
                    return ""
                    
                sub_heading = f"{sec_num}.2. Bảng thống kê lỗ hổng phát hiện:" if sec_num else "Bảng thống kê lỗ hổng phát hiện:"
                lines = [
                    sub_heading,
                    "| Mức độ rủi ro | Số lượng phát hiện | Đánh giá / Mức độ ưu tiên |",
                    "|---|---|---|",
                    f"| Nghiêm trọng (Critical) | {crit_count} | Khẩn cấp (trong vòng 24h) |",
                    f"| Cao (High) | {high_count} | Ưu tiên cao |",
                    f"| Trung bình (Medium) | {med_count} | Kế hoạch bảo trì tiếp theo |",
                    f"| Thấp (Low) | {low_count} | Theo dõi thường xuyên |"
                ]
                return "\n".join(lines)

            def create_styled_table(doc, anchor, headers, rows_data, col_widths=None, insert_before: bool = False):
                num_rows = len(rows_data) + 1
                num_cols = len(headers)
                table = doc.add_table(rows=num_rows, cols=num_cols)
                table.alignment = docx.enum.table.WD_TABLE_ALIGNMENT.CENTER
                table.autofit = False

                if insert_before:
                    if hasattr(anchor, '_p'):
                        anchor._p.addprevious(table._tbl)
                    elif hasattr(anchor, '_tbl'):
                        anchor._tbl.addprevious(table._tbl)
                else:
                    if hasattr(anchor, '_p'):
                        anchor._p.addnext(table._tbl)
                    elif hasattr(anchor, '_tbl'):
                        anchor._tbl.addnext(table._tbl)

                if not col_widths or len(col_widths) != num_cols:
                    if num_cols == 5:
                        col_widths = [docx.shared.Inches(0.5), docx.shared.Inches(1.5), docx.shared.Inches(0.9), docx.shared.Inches(1.8), docx.shared.Inches(1.8)]
                    elif num_cols == 3:
                        col_widths = [docx.shared.Inches(2.0), docx.shared.Inches(1.2), docx.shared.Inches(3.3)]
                    else:
                        col_widths = [docx.shared.Inches(6.5 / num_cols)] * num_cols

                for col_idx, width in enumerate(col_widths):
                    table.columns[col_idx].width = width
                for row in table.rows:
                    for idx, width in enumerate(col_widths):
                        row.cells[idx].width = width

                tblPr = table._tbl.tblPr
                borders = docx.oxml.parse_xml(
                    r'<w:tblBorders {} >'
                    r'<w:top w:val="single" w:sz="6" w:space="0" w:color="1F4E79"/>'
                    r'<w:bottom w:val="single" w:sz="6" w:space="0" w:color="1F4E79"/>'
                    r'<w:left w:val="none"/>'
                    r'<w:right w:val="none"/>'
                    r'<w:insideH w:val="single" w:sz="4" w:space="0" w:color="E0E0E0"/>'
                    r'<w:insideV w:val="single" w:sz="4" w:space="0" w:color="EAECEE"/>'
                    r'</w:tblBorders>'.format(docx.oxml.ns.nsdecls('w'))
                )
                tblPr.append(borders)
                tblCellMar = docx.oxml.parse_xml(
                    r'<w:tblCellMar {} >'
                    r'<w:top w:w="120" w:type="dxa"/>'
                    r'<w:bottom w:w="120" w:type="dxa"/>'
                    r'<w:left w:w="160" w:type="dxa"/>'
                    r'<w:right w:w="160" w:type="dxa"/>'
                    r'</w:tblCellMar>'.format(docx.oxml.ns.nsdecls('w'))
                )
                tblPr.append(tblCellMar)

                header_tr = table.rows[0]._tr.get_or_add_trPr()
                header_tr.append(docx.oxml.parse_xml(r'<w:tblHeader {} />'.format(docx.oxml.ns.nsdecls('w'))))
                for col_idx, text in enumerate(headers):
                    cell = table.cell(0, col_idx)
                    cell._tc.get_or_add_tcPr().append(docx.oxml.parse_xml(r'<w:shd {} w:fill="1F4E79"/>'.format(docx.oxml.ns.nsdecls('w'))))
                    p = cell.paragraphs[0]
                    p.paragraph_format.space_before = docx.shared.Pt(4)
                    p.paragraph_format.space_after = docx.shared.Pt(4)
                    p.alignment = docx.enum.text.WD_ALIGN_PARAGRAPH.CENTER if col_idx in [0, 2] else docx.enum.text.WD_ALIGN_PARAGRAPH.LEFT
                    clean_h = text.replace('*', '').replace('_', '').strip()
                    run = p.add_run(clean_h)
                    run.bold = True
                    run.font.color.rgb = docx.shared.RGBColor(255, 255, 255)
                    run.font.name = 'Times New Roman'
                    run.font.size = docx.shared.Pt(10)

                for row_idx, r_data in enumerate(rows_data, start=1):
                    trPr = table.rows[row_idx]._tr.get_or_add_trPr()
                    trPr.append(docx.oxml.parse_xml(r'<w:cantSplit {} />'.format(docx.oxml.ns.nsdecls('w'))))
                    bg_color = 'F7F9FB' if row_idx % 2 == 1 else 'FFFFFF'
                    for col_idx in range(num_cols):
                        val = r_data[col_idx] if col_idx < len(r_data) else ""
                        cell = table.cell(row_idx, col_idx)
                        cell._tc.get_or_add_tcPr().append(docx.oxml.parse_xml(r'<w:shd {} w:fill="{}"/>'.format(docx.oxml.ns.nsdecls('w'), bg_color)))
                        p = cell.paragraphs[0]
                        p.paragraph_format.space_before = docx.shared.Pt(3)
                        p.paragraph_format.space_after = docx.shared.Pt(3)
                        p.alignment = docx.enum.text.WD_ALIGN_PARAGRAPH.CENTER if col_idx in [0, 2] else docx.enum.text.WD_ALIGN_PARAGRAPH.LEFT
                        val_str = str(val).strip()
                        clean_markdown_and_add_runs(p, val_str, base_font='Times New Roman', font_size=docx.shared.Pt(9.5))
                        if col_idx == 2 or (num_cols == 3 and col_idx == 0):
                            is_crit = any(x in val_str for x in ['Nghiêm trọng', 'Critical'])
                            is_high = any(x in val_str for x in ['Cao', 'High'])
                            is_med = any(x in val_str for x in ['Trung bình', 'Medium'])
                            is_low = any(x in val_str for x in ['Thấp', 'Low'])
                            for r in p.runs:
                                if is_crit:
                                    r.bold = True
                                    r.font.color.rgb = docx.shared.RGBColor(156, 0, 6)
                                elif is_high:
                                    r.bold = True
                                    r.font.color.rgb = docx.shared.RGBColor(192, 0, 0)
                                elif is_med:
                                    r.bold = True
                                    r.font.color.rgb = docx.shared.RGBColor(180, 100, 0)
                                elif is_low:
                                    r.bold = True
                                    r.font.color.rgb = docx.shared.RGBColor(39, 174, 96)

                return table

            def clean_markdown_and_add_runs(paragraph, text, base_font="Times New Roman", font_size=docx.shared.Pt(11)):
                text = re.sub(r'^\s*#{1,6}\s*', '', text)
                pattern = r'(\*\*\*.*?\*\*\*|\*\*.*?\*\*|\*.*?\*|___.*?___|__.*?__|_[^_]+_)'
                tokens = re.split(pattern, text)
                for token in tokens:
                    if not token:
                        continue
                    bold = False
                    italic = False
                    content = token
                    if (content.startswith('***') and content.endswith('***')) or (content.startswith('___') and content.endswith('___')):
                        content = content[3:-3]
                        bold = True
                        italic = True
                    elif (content.startswith('**') and content.endswith('**')) or (content.startswith('__') and content.endswith('__')):
                        content = content[2:-2]
                        bold = True
                    elif (content.startswith('*') and content.endswith('*')) or (content.startswith('_') and content.endswith('_')):
                        content = content[1:-1]
                        italic = True
                    content = content.replace('**', '').replace('***', '').replace('__', '').replace('*', '')
                    if not content:
                        continue
                    run = paragraph.add_run(content)
                    run.bold = bold
                    run.italic = italic
                    run.font.name = base_font
                    run.font.size = font_size

            def render_content_with_tables(doc, current_anchor, content_str, base_font="Times New Roman"):
                lines = content_str.split('\n')
                idx = 0
                while idx < len(lines):
                    line = lines[idx].strip()
                    if not line:
                        idx += 1
                        continue
                        
                    if line.startswith('|'):
                        table_lines = []
                        while idx < len(lines) and lines[idx].strip().startswith('|'):
                            table_lines.append(lines[idx].strip())
                            idx += 1
                        rows = []
                        for tl in table_lines:
                            raw_line = tl.strip()
                            raw_line = re.sub(r'^\|\s*', '', raw_line)
                            raw_line = re.sub(r'\s*\|$', '', raw_line)
                            cells = [c.strip() for c in raw_line.split('|')]
                            if all(re.match(r'^:?-+:?$', c) for c in cells):
                                continue
                            rows.append(cells)
                        if len(rows) >= 2:
                            headers = rows[0]
                            num_cols = len(headers)
                            data = []
                            for r in rows[1:]:
                                if len(r) < num_cols:
                                    r = r + [""] * (num_cols - len(r))
                                elif len(r) > num_cols:
                                    r = r[:num_cols]
                                data.append(r)
                            if num_cols == 5:
                                col_w = [docx.shared.Inches(0.5), docx.shared.Inches(1.5), docx.shared.Inches(0.9), docx.shared.Inches(1.8), docx.shared.Inches(1.8)]
                            elif num_cols == 3:
                                col_w = [docx.shared.Inches(2.0), docx.shared.Inches(1.2), docx.shared.Inches(3.3)]
                            else:
                                col_w = [docx.shared.Inches(6.5 / num_cols)] * num_cols
                            current_anchor = create_styled_table(doc, current_anchor, headers, data, col_w)
                        continue

                    new_p = docx.text.paragraph.Paragraph(docx.oxml.OxmlElement('w:p'), doc._body._element)
                    if hasattr(current_anchor, '_p'):
                        current_anchor._p.addnext(new_p._p)
                    elif hasattr(current_anchor, '_tbl'):
                        current_anchor._tbl.addnext(new_p._p)
                    current_anchor = new_p

                    if re.match(r'^\d+\.\d+(?:\.\d+)*\b', line):
                        new_p.paragraph_format.left_indent = docx.shared.Pt(14)
                        new_p.paragraph_format.space_before = docx.shared.Pt(5)
                        new_p.paragraph_format.space_after = docx.shared.Pt(2)
                        new_p.paragraph_format.line_spacing = 1.15
                        match_colon = re.match(r'^(\d+\.\d+(?:\.\d+)*\.?\s*[^:]+:)\s*(.*)$', line)
                        if match_colon:
                            lbl, val = match_colon.group(1), match_colon.group(2)
                            r_lbl = new_p.add_run(lbl + " ")
                            r_lbl.bold = True
                            r_lbl.font.name = base_font
                            r_lbl.font.size = docx.shared.Pt(11)
                            clean_markdown_and_add_runs(new_p, val, base_font=base_font, font_size=docx.shared.Pt(11))
                        else:
                            r = new_p.add_run(re.sub(r'[\*_]', '', line))
                            r.bold = True
                            r.font.name = base_font
                            r.font.size = docx.shared.Pt(11)
                    elif re.match(r'^[a-z]\)\s*', line, flags=re.IGNORECASE):
                        new_p.paragraph_format.left_indent = docx.shared.Pt(28)
                        new_p.paragraph_format.space_before = docx.shared.Pt(1)
                        new_p.paragraph_format.space_after = docx.shared.Pt(2)
                        new_p.paragraph_format.line_spacing = 1.15
                        match_colon = re.match(r'^([a-z]\)\s*[^:]+:)\s*(.*)$', line, flags=re.IGNORECASE)
                        if match_colon:
                            lbl, val = match_colon.group(1), match_colon.group(2)
                            r_lbl = new_p.add_run(lbl + " ")
                            r_lbl.bold = True
                            r_lbl.font.name = base_font
                            r_lbl.font.size = docx.shared.Pt(11)
                            clean_markdown_and_add_runs(new_p, val, base_font=base_font, font_size=docx.shared.Pt(11))
                        else:
                            clean_markdown_and_add_runs(new_p, line, base_font=base_font, font_size=docx.shared.Pt(11))
                    elif line.startswith('- ') or line.startswith('• ') or line.startswith('* '):
                        new_p.paragraph_format.left_indent = docx.shared.Pt(28)
                        new_p.paragraph_format.space_before = docx.shared.Pt(1)
                        new_p.paragraph_format.space_after = docx.shared.Pt(2)
                        new_p.paragraph_format.line_spacing = 1.15
                        clean_bullet = re.sub(r'^[\-\*•]\s*', '• ', line)
                        clean_markdown_and_add_runs(new_p, clean_bullet, base_font=base_font, font_size=docx.shared.Pt(11))
                    else:
                        new_p.paragraph_format.left_indent = docx.shared.Pt(14)
                        new_p.paragraph_format.space_before = docx.shared.Pt(2)
                        new_p.paragraph_format.space_after = docx.shared.Pt(3)
                        new_p.paragraph_format.line_spacing = 1.15
                        clean_markdown_and_add_runs(new_p, line, base_font=base_font, font_size=docx.shared.Pt(11))

                    idx += 1

                return current_anchor

            def parse_markdown_table_lines(table_lines: list[str]):
                rows = []
                for tl in table_lines:
                    raw_line = tl.strip()
                    raw_line = re.sub(r'^\|\s*', '', raw_line)
                    raw_line = re.sub(r'\s*\|$', '', raw_line)
                    cells = [c.strip() for c in raw_line.split('|')]
                    if all(re.match(r'^:?-+:?$', c) for c in cells):
                        continue
                    rows.append(cells)
                if len(rows) >= 2:
                    headers = rows[0]
                    num_cols = len(headers)
                    data = []
                    for r in rows[1:]:
                        if len(r) < num_cols:
                            r = r + [''] * (num_cols - len(r))
                        elif len(r) > num_cols:
                            r = r[:num_cols]
                        data.append(r)
                    return headers, data
                return None, None

            def postprocess_document(doc):
                paragraphs = list(doc.paragraphs)
                i = 0
                while i < len(paragraphs):
                    p = paragraphs[i]
                    p_text = p.text.strip()
                    if p_text.startswith('|') and '\n' not in p_text:
                        tbl_paras = []
                        while i < len(paragraphs) and paragraphs[i].text.strip().startswith('|') and '\n' not in paragraphs[i].text.strip():
                            tbl_paras.append(paragraphs[i])
                            i += 1
                        tbl_lines = [tp.text.strip() for tp in tbl_paras]
                        headers, data = parse_markdown_table_lines(tbl_lines)
                        if headers and data:
                            create_styled_table(doc, tbl_paras[0], headers, data, insert_before=True)
                            for tp in tbl_paras:
                                try:
                                    tp._element.getparent().remove(tp._element)
                                except Exception:
                                    pass
                        continue
                    i += 1

                for p in list(doc.paragraphs):
                    p_text = p.text.strip()
                    if not p_text:
                        continue
                    if '|' in p_text and ('\n' in p_text or p_text.startswith('|')):
                        lines = [l.strip() for l in p.text.split('\n')]
                        has_table = any(l.startswith('|') for l in lines)
                        if has_table:
                            anchor = p
                            idx = 0
                            while idx < len(lines):
                                line = lines[idx]
                                if not line:
                                    idx += 1
                                    continue
                                if line.startswith('|'):
                                    tbl_lines = []
                                    while idx < len(lines) and lines[idx].startswith('|'):
                                        tbl_lines.append(lines[idx])
                                        idx += 1
                                    headers, data = parse_markdown_table_lines(tbl_lines)
                                    if headers and data:
                                        create_styled_table(doc, anchor, headers, data, insert_before=True)
                                    continue
                                
                                new_p = docx.text.paragraph.Paragraph(docx.oxml.OxmlElement('w:p'), doc._body._element)
                                anchor._p.addprevious(new_p._p)
                                new_p.paragraph_format.left_indent = docx.shared.Pt(14)
                                new_p.paragraph_format.space_before = docx.shared.Pt(2)
                                new_p.paragraph_format.space_after = docx.shared.Pt(3)
                                new_p.paragraph_format.line_spacing = 1.15
                                clean_markdown_and_add_runs(new_p, line, base_font=base_font, font_size=docx.shared.Pt(11))
                                idx += 1
                            try:
                                p._element.getparent().remove(p._element)
                            except Exception:
                                pass
                            continue

                for p in list(doc.paragraphs):
                    p_text = p.text.strip()
                    if not p_text:
                        continue
                    if is_static_template_element(p_text):
                        continue
                    match_sec = re.match(r'^(\d+\.\d+(?:\.\d+)*\.?\s*[^:]+:)\s*(.*)$', p_text)
                    match_alpha = re.match(r'^([a-z]\)\s*[^:]+:)\s*(.*)$', p_text, flags=re.IGNORECASE)
                    match_bullet = re.match(r'^[\-\*•]\s*(.*)$', p_text)

                    if match_sec:
                        lbl, val = match_sec.group(1), match_sec.group(2)
                        p.text = ''
                        p.paragraph_format.left_indent = docx.shared.Pt(14)
                        p.paragraph_format.space_before = docx.shared.Pt(4)
                        p.paragraph_format.space_after = docx.shared.Pt(2)
                        p.paragraph_format.line_spacing = 1.15
                        r_lbl = p.add_run(lbl + " ")
                        r_lbl.bold = True
                        r_lbl.font.name = base_font
                        r_lbl.font.size = docx.shared.Pt(11)
                        clean_markdown_and_add_runs(p, val, base_font=base_font, font_size=docx.shared.Pt(11))
                    elif match_alpha:
                        lbl, val = match_alpha.group(1), match_alpha.group(2)
                        p.text = ''
                        p.paragraph_format.left_indent = docx.shared.Pt(28)
                        p.paragraph_format.space_before = docx.shared.Pt(2)
                        p.paragraph_format.space_after = docx.shared.Pt(2)
                        p.paragraph_format.line_spacing = 1.15
                        r_lbl = p.add_run(lbl + " ")
                        r_lbl.bold = True
                        r_lbl.font.name = base_font
                        r_lbl.font.size = docx.shared.Pt(11)
                        clean_markdown_and_add_runs(p, val, base_font=base_font, font_size=docx.shared.Pt(11))
                    elif match_bullet:
                        val = match_bullet.group(1)
                        p.text = ''
                        p.paragraph_format.left_indent = docx.shared.Pt(28)
                        p.paragraph_format.space_before = docx.shared.Pt(1)
                        p.paragraph_format.space_after = docx.shared.Pt(2)
                        p.paragraph_format.line_spacing = 1.15
                        r_b = p.add_run("• ")
                        r_b.bold = True
                        r_b.font.name = base_font
                        clean_markdown_and_add_runs(p, val, base_font=base_font, font_size=docx.shared.Pt(11))
                    elif any(c in p.text for c in ['***', '**', '___', '__', '#']):
                        raw = p.text
                        p.text = ''
                        clean_markdown_and_add_runs(p, raw, base_font=base_font, font_size=docx.shared.Pt(11))

            # Build fallback text/table for vulnerabilities and overview summary if present in request data
            raw_vulns = (scan_data.get("vulnerabilities") or scan_data.get("chi_tiet") or scan_data.get("details") or scan_data.get("lo_hong")) if isinstance(scan_data, dict) else []
            fallback_details_str = format_vulnerabilities_table(raw_vulns, sec_num="2") if raw_vulns else ""
            fallback_summary_str = format_summary_table(scan_data)

            undeclared = set()
            try:
                doc_tpl = DocxTemplate(input_docx)
                undeclared = doc_tpl.get_undeclared_template_variables()
            except Exception as e:
                logger.warning(f"DocxTemplate undeclared template variables inspection failed: {e}")

            context = {}
            if isinstance(scan_data, dict):
                for var in undeclared:
                    context[var] = scan_data.get(var, "")
                context.update(scan_data)
            if fallback_details_str:
                for k in ["chi_tiet", "vulnerabilities", "details", "lo_hong", "danh_sach_chi_tiet"]:
                    if k in undeclared and not context.get(k):
                        context[k] = fallback_details_str

            doc_raw = docx.Document(input_docx)
            for p in list(doc_raw.paragraphs):
                p_txt = p.text.strip()
                headings = split_template_text(p_txt)
                if len(headings) > 1:
                    p.text = headings[0]
                    curr_p = p
                    for h in headings[1:]:
                        new_p = docx.text.paragraph.Paragraph(docx.oxml.OxmlElement('w:p'), doc_raw._body._element)
                        curr_p._p.addnext(new_p._p)
                        run = new_p.add_run(h)
                        run.font.name = "Times New Roman"
                        run.font.size = docx.shared.Pt(13)
                        run.bold = True
                        curr_p = new_p

            all_paragraphs = list(doc_raw.paragraphs)

            template_headings = [
                clean_text(p.text) for p in all_paragraphs
                if clean_text(p.text) and not is_static_template_element(p.text)
            ]
            template_text = "\n".join(template_headings)

            needed_ai_fields = [f for f in undeclared if f not in scan_data]

            scan_time_val = (scan_data.get("scan_time") or scan_data.get("time") or scan_data.get("date") or "2026-10-12 10:00:00") if isinstance(scan_data, dict) else "2026-10-12 10:00:00"
            report_date_val = (scan_data.get("date") or scan_data.get("report_date") or scan_time_val.split()[0]) if isinstance(scan_data, dict) else "2026-10-12"

            is_freeform_idea = len(template_headings) < 2
            user_prompt_desc = effective_template_text if effective_template_text else "Báo cáo đánh giá an toàn thông tin hệ thống"

            section_plan = []
            sec_counter = 0
            for h in template_headings:
                ext_n = extract_section_number(h)
                if ext_n:
                    s_num = ext_n
                    if s_num.isdigit():
                        sec_counter = max(sec_counter, int(s_num))
                else:
                    sec_counter += 1
                    s_num = str(sec_counter)

                h_norm = strip_accents(normalize_key(h))
                if h_norm == "thoi gian" or any(syn in h_norm for syn in SEMANTIC_SYNONYMS["thoi gian"]):
                    s_type = "thoi_gian"
                elif h_norm == "tong quan" or any(syn in h_norm for syn in SEMANTIC_SYNONYMS["tong quan"]):
                    s_type = "tong_quan"
                elif h_norm == "chi tiet" or any(syn in h_norm for syn in SEMANTIC_SYNONYMS["chi tiet"]):
                    s_type = "chi_tiet"
                elif h_norm == "khuyen nghi" or any(syn in h_norm for syn in SEMANTIC_SYNONYMS["khuyen nghi"]):
                    s_type = "khuyen_nghi"
                elif h_norm == "ket luan" or any(syn in h_norm for syn in SEMANTIC_SYNONYMS["ket luan"]):
                    s_type = "ket_luan"
                else:
                    s_type = "other"

                section_plan.append({"heading": h, "sec_num": s_num, "sec_type": s_type})

            dynamic_section_rules = []
            for sp in section_plan:
                h = sp["heading"]
                s_num = sp["sec_num"]
                s_type = sp["sec_type"]
                if s_type == "thoi_gian":
                    dynamic_section_rules.append(
                        f"     * Với đầu mục '{h}' (Mục {s_num}): Các mục con BẮT BUỘC đánh số {s_num}.1, {s_num}.2...\n"
                        f"       {s_num}.1. Thời gian thực hiện kiểm thử: [Nêu thời gian thực hiện quét từ scan_data: {scan_time_val}]\n"
                        f"       {s_num}.2. Thời điểm ghi nhận và lập báo cáo: [Nêu ngày lập báo cáo: {report_date_val}]"
                    )
                elif s_type == "tong_quan":
                    dynamic_section_rules.append(
                        f"     * Với đầu mục '{h}' (Mục {s_num}): Các mục con BẮT BUỘC đánh số {s_num}.1, {s_num}.2, {s_num}.3...\n"
                        f"       {s_num}.1. Phạm vi và tóm tắt kiểm thử: [Nội dung tổng quan về phạm vi kiểm thử mục tiêu]\n"
                        f"       {s_num}.2. Bảng thống kê lỗ hổng phát hiện: [Bắt buộc bảng Markdown 3 cột: | Mức độ rủi ro | Số lượng phát hiện | Đánh giá / Mức độ ưu tiên |]\n"
                        f"       {s_num}.3. Đánh giá rủi ro tổng thể: [Đánh giá tổng thể mức độ rủi ro hệ thống]"
                    )
                elif s_type == "chi_tiet":
                    dynamic_section_rules.append(
                        f"     * Với đầu mục '{h}' (Mục {s_num}): Các mục con BẮT BUỘC đánh số {s_num}.1, {s_num}.2...\n"
                        f"       {s_num}.1. Bảng danh sách chi tiết các lỗ hổng an toàn thông tin phát hiện được: [Bắt buộc bảng Markdown 5 cột: | STT | Tên lỗ hổng | Mức độ | Mô tả chi tiết | Biện pháp khắc phục |]\n"
                        f"       {s_num}.2. Phân tích chi tiết và tác động kỹ thuật: [Nội dung phân tích kỹ thuật các điểm yếu]"
                    )
                elif s_type == "khuyen_nghi":
                    dynamic_section_rules.append(
                        f"     * Với đầu mục '{h}' (Mục {s_num}): Các mục con BẮT BUỘC đánh số {s_num}.1, {s_num}.2...\n"
                        f"       {s_num}.1. Các biện pháp khắc phục khẩn cấp: [Các ý a), b)...]\n"
                        f"       {s_num}.2. Kế hoạch phòng ngừa và giám sát định kỳ: [Các ý a), b)...]"
                    )
                elif s_type == "ket_luan":
                    dynamic_section_rules.append(
                        f"     * Với đầu mục '{h}' (Mục {s_num}): Các mục con BẮT BUỘC đánh số {s_num}.1, {s_num}.2...\n"
                        f"       {s_num}.1. Đánh giá hiện trạng an toàn hệ thống: [Đánh giá tổng quát]\n"
                        f"       {s_num}.2. Đề xuất hành động tiếp theo: [Các bước tiếp theo]"
                    )
                else:
                    dynamic_section_rules.append(
                        f"     * Với đầu mục '{h}' (Mục {s_num}): Các mục con BẮT BUỘC đánh số {s_num}.1, {s_num}.2... [Nội dung chuyên môn phù hợp với chủ đề đầu mục và dữ liệu quét]"
                    )

            prompt_rules_text = "\n".join(dynamic_section_rules)

            fallback_content_map = {
                "thoi_gian": (
                    f"1.1. Thời gian thực hiện kiểm thử: Thực hiện từ lúc {scan_time_val}.\n"
                    f"1.2. Thời điểm ghi nhận và lập báo cáo: Ngày {report_date_val}."
                ),
                "tong_quan": (
                    f"2.1. Phạm vi và tóm tắt kiểm thử: Đánh giá an toàn thông tin toàn diện trên hệ thống mục tiêu.\n\n"
                    f"{format_summary_table(scan_data, sec_num='2')}\n\n"
                    f"2.3. Đánh giá rủi ro tổng thể: Hệ thống đang đối mặt với các rủi ro an toàn thông tin cần được xem xét và xử lý theo đúng mức độ ưu tiên."
                ),
                "chi_tiet": (
                    format_vulnerabilities_table(raw_vulns, sec_num="3") if raw_vulns else
                    "3.1. Bảng danh sách chi tiết các lỗ hổng an toàn thông tin phát hiện được:\n| STT | Tên lỗ hổng | Mức độ | Mô tả chi tiết | Biện pháp khắc phục |\n|---|---|---|---|---|\n| 1 | Chưa ghi nhận lỗ hổng nghiêm trọng | Thấp | Không phát hiện rủi ro cao | Tiếp tục giám sát định kỳ |\n\n3.2. Phân tích chi tiết và tác động kỹ thuật: Các thành phần hệ thống cơ bản đáp ứng tiêu chuẩn an toàn."
                ),
                "khuyen_nghi": (
                    f"4.1. Các biện pháp khắc phục khẩn cấp:\n"
                    f"a) Vá và cập nhật các lỗ hổng bảo mật đã phát hiện theo đúng khuyến nghị kỹ thuật.\n"
                    f"b) Thiết lập cấu hình tường lửa ứng dụng (WAF) và hạn chế truy cập trái phép đối với các dịch vụ mạng trọng yếu.\n"
                    f"4.2. Kế hoạch phòng ngừa và giám sát định kỳ:\n"
                    f"a) Thiết lập lịch rà quét an ninh mạng tự động định kỳ hàng tuần để phát hiện sớm các nguy cơ mới.\n"
                    f"b) Nâng cao nhận thức an toàn thông tin và giám sát nhật ký truy cập hệ thống thường xuyên."
                ),
                "ket_luan": (
                    f"5.1. Đánh giá hiện trạng an toàn hệ thống:\n"
                    f"Hệ thống mục tiêu đã được kiểm tra và đánh giá toàn diện, phát hiện các điểm yếu bảo mật cần được khắc phục kịp thời nhằm đảm bảo an toàn vận hành.\n"
                    f"5.2. Đề xuất hành động tiếp theo:\n"
                    f"Đơn vị vận hành cần khẩn trương thực hiện kế hoạch khắc phục theo thứ tự ưu tiên và tiến hành tái kiểm thử sau khi hoàn thành vá lỗi."
                )
            }

            ai_data = {}
            doc_title_ai = "BÁO CÁO ĐÁNH GIÁ AN TOÀN THÔNG TIN"
            parsed_sections = []

            if needed_ai_fields or template_headings or is_freeform_idea:
                if is_freeform_idea:
                    prompt_content = (
                        "Bạn đóng vai trò là Trưởng nhóm Chuyên gia An toàn thông tin cấp cao. Dưới đây là yêu cầu/ý tưởng từ người dùng và dữ liệu quét an toàn thông tin (scan_data):\n\n"
                        f"=== YÊU CẦU / Ý TƯỞNG CỦA NGƯỜI DÙNG ===\n{user_prompt_desc}\n\n"
                        f"=== DỮ LIỆU QUÉT BẢO MẬT (SCAN DATA) ===\n{json.dumps(scan_data, ensure_ascii=False, indent=2)[:10000]}\n\n"
                        "NHIỆM VỤ CỦA BẠN:\n"
                        "Dựa vào ý tưởng của người dùng và dữ liệu quét, hãy TỰ ĐỘNG THIẾT KẾ CÁC ĐẦU MỤC (CÁC Ý CHÍNH) VÀ SINH NỘI DUNG TOÀN DIỆN cho một bản báo cáo an toàn thông tin chuyên nghiệp (tối thiểu 5 mục logic chuẩn an toàn thông tin):\n"
                        f"1. Thời gian và Phạm vi kiểm thử: {scan_time_val}, ngày lập {report_date_val} (mục con: 1.1, 1.2)\n"
                        "2. Tổng quan kết quả kiểm thử: Tóm tắt phạm vi, BẮT BUỘC có Bảng thống kê lỗ hổng 3 cột (| Mức độ rủi ro | Số lượng phát hiện | Đánh giá / Mức độ ưu tiên |), và đánh giá rủi ro tổng thể (mục con: 2.1, 2.2, 2.3)\n"
                        "3. Chi tiết các lỗ hổng an toàn thông tin: BẮT BUỘC có Bảng chi tiết lỗ hổng 5 cột (| STT | Tên lỗ hổng | Mức độ | Mô tả chi tiết | Biện pháp khắc phục |), và phân tích tác động kỹ thuật (mục con: 3.1, 3.2)\n"
                        "4. Khuyến nghị và Biện pháp khắc phục: Khắc phục khẩn cấp và phòng ngừa dài hạn (mục con: 4.1, 4.2 với các ý a), b)...)\n"
                        "5. Kết luận và Đề xuất hành động tiếp theo: Hiện trạng an toàn và kiến nghị triển khai (mục con: 5.1, 5.2)\n\n"
                        "HƯỚNG DẪN ĐỊNH DẠNG (BẮT BUỘC TUÂN THỦ):\n"
                        "1. Trong phần nội dung (value), TUYỆT ĐỐI KHÔNG lặp lại tiêu đề mục lớn ở dòng đầu tiên.\n"
                        "2. Số thứ tự mục con BẮT BUỘC phải khớp với mục lớn (Mục 1 -> 1.1, Mục 2 -> 2.1...).\n"
                        "3. Bảng markdown chuẩn cho mục tổng quan (3 cột) và mục chi tiết (5 cột).\n"
                        "4. Tuyệt đối không dùng dấu sao ** hoặc *** để in đậm vì đây là định dạng Word.\n\n"
                        "QUY ĐỊNH ĐỊNH DẠNG ĐẦU RA:\n"
                        "Trả về DUY NHẤT một khối JSON hợp lệ theo cấu trúc:\n"
                        "{\n"
                        '  "document_title": "BÁO CÁO ĐÁNH GIÁ AN TOÀN THÔNG TIN...",\n'
                        '  "sections": [\n'
                        '    {\n'
                        '      "heading": "1. Thời gian và Phạm vi kiểm thử",\n'
                        '      "content": "1.1. Thời gian thực hiện kiểm thử: ...\\n1.2. Thời điểm ghi nhận và lập báo cáo: ..."\n'
                        '    },\n'
                        '    {\n'
                        '      "heading": "2. Tổng quan kết quả kiểm thử",\n'
                        '      "content": "2.1. Phạm vi và tóm tắt kiểm thử: ...\\n2.2. Bảng thống kê lỗ hổng phát hiện:\\n| Mức độ rủi ro | Số lượng phát hiện | Đánh giá / Mức độ ưu tiên |\\n|---|---|---|\\n...\\n2.3. Đánh giá rủi ro tổng thể: ..."\n'
                        '    },\n'
                        '    {\n'
                        '      "heading": "3. Chi tiết các lỗ hổng an toàn thông tin",\n'
                        '      "content": "3.1. Bảng danh sách chi tiết các lỗ hổng an toàn thông tin phát hiện được:\\n| STT | Tên lỗ hổng | Mức độ | Mô tả chi tiết | Biện pháp khắc phục |\\n|---|---|---|---|---|\\n...\\n3.2. Phân tích chi tiết và tác động kỹ thuật: ..."\n'
                        '    },\n'
                        '    {\n'
                        '      "heading": "4. Khuyến nghị và Biện pháp khắc phục",\n'
                        '      "content": "4.1. Các biện pháp khắc phục khẩn cấp:\\na) ...\\nb) ...\\n4.2. Kế hoạch phòng ngừa và giám sát định kỳ:\\na) ...\\nb) ..."\n'
                        '    },\n'
                        '    {\n'
                        '      "heading": "5. Kết luận và Đề xuất hành động tiếp theo",\n'
                        '      "content": "5.1. Đánh giá hiện trạng an toàn hệ thống: ...\\n5.2. Đề xuất hành động tiếp theo: ..."\n'
                        '    }\n'
                        '  ]\n'
                        "}\n"
                        "Tuyệt đối không dùng markdown block (như ```json) hay thêm văn bản giải thích thừa thãi bên ngoài khối JSON."
                    )
                else:
                    prompt_content = (
                        "Bạn đóng vai trò là chuyên gia an toàn thông tin cấp cao. Dưới đây là dữ liệu quét an toàn thông tin (scan_data) và danh sách các đầu mục của tài liệu báo cáo (template):\n\n"
                        f"=== DỮ LIỆU QUÉT BẢO MẬT (SCAN DATA) ===\n{json.dumps(scan_data, ensure_ascii=False, indent=2)[:10000]}\n\n"
                        f"=== CÁC ĐẦU MỤC TRONG TEMPLATE BÁO CÁO ===\n{template_text[:4000]}\n\n"
                        "NHIỆM VỤ CỦA BẠN:\n"
                        "Dựa vào dữ liệu quét, hãy suy luận ngữ nghĩa của TỪNG ĐẦU MỤC trong template (kể cả đầu mục chuẩn hay đầu mục tùy biến của người dùng) và sinh nội dung chi tiết, chuẩn xác, đầy đủ cho TỪNG ĐẦU MỤC đó.\n\n"
                        "HƯỚNG DẪN ĐẶC BIỆT VỀ ĐỊNH DẠNG BẢNG (TABLE) VÀ PHÂN CẤP ĐẦU MỤC (BẮT BUỘC TUÂN THỦ):\n"
                        "1. QUY TẮC TUYỆT ĐỐI KHÔNG LẶP TIÊU ĐỀ:\n"
                        "   - Trong phần nội dung (value của từng key JSON), TUYỆT ĐỐI KHÔNG lặp lại tên/tiêu đề của đầu mục lớn đó ở dòng đầu tiên.\n"
                        "   - Ví dụ: Key là '2. Tổng quan', trong value TUYỆT ĐỐI KHÔNG viết lại '2. Tổng quan' ở dòng đầu tiên, mà phải bắt đầu NGAY bằng mục con '2.1. ...'.\n"
                        "2. QUY TẮC ĐÁNH SỐ ĐẦU MỤC CON (X.1, X.2, X.3...):\n"
                        "   - Số thứ tự của các mục con BẮT BUỘC phải khớp CHÍNH XÁC với số thứ tự của mục lớn chứa nó trong template:\n"
                        f"{prompt_rules_text}\n"
                        "   - TUYỆT ĐỐI CẤM đánh số mục con bị lệch (ví dụ nếu đầu mục là Mục 2 thì mục con PHẢI là 2.1, 2.2, CẤM dùng 1.1 hay 3.1).\n"
                        "3. CHỖ NÀO NÊN ĐỂ BẢNG THÌ HÃY ĐỂ BẢNG (Dùng cú pháp bảng Markdown chuẩn: | Cột 1 | Cột 2 | ... |):\n"
                        "   - Bảng chi tiết lỗ hổng: | STT | Tên lỗ hổng | Mức độ | Mô tả chi tiết | Biện pháp khắc phục |\n"
                        "   - Bảng thống kê tổng quan: | Mức độ rủi ro | Số lượng phát hiện | Đánh giá / Mức độ ưu tiên |\n"
                        "4. ĐỐI VỚI CÁC ĐẦU MỤC TÙY BIẾN CỦA NGƯỜI DÙNG: Phân tích chủ đề và dùng scan_data sinh nội dung kỹ thuật phù hợp.\n"
                        "5. TUYỆT ĐỐI KHÔNG DÙNG DẤU SAO ** HOẶC *** ĐỂ IN ĐẬM VÌ ĐÂY LÀ ĐỊNH DẠNG WORD.\n"
                        "6. Các phần đã có sẵn nội dung cố định trong template (như tiêu đề 'Báo cáo', tên người ký): Giữ nguyên, không cần sinh thêm.\n"
                        "7. QUY TẮC TUYỆT ĐỐI VỀ CÁC MỤC HÀNH CHÍNH / KÍNH GỬI / NƠI NHẬN:\n"
                        "   - TUYỆT ĐỐI KHÔNG sinh nội dung cho các phần hành chính như 'Kính gửi', 'Nơi nhận', danh sách người nhận, căn cứ, quốc hiệu, tiêu ngữ hay chữ ký.\n"
                        "   - CHỈ sinh nội dung cho các ĐẦU MỤC NỘI DUNG BÁO CÁO THỰC SỰ (như Thời gian, Tổng quan, Chi tiết, Khuyến nghị, Kết luận...).\n\n"
                        "QUY ĐỊNH ĐỊNH DẠNG ĐẦU RA:\n"
                        "- Trả về DUY NHẤT một khối JSON hợp lệ.\n"
                        "- Mỗi key trong JSON phải là NGUYÊN VĂN tiêu đề của từng đầu mục trong template (ví dụ: " + ", ".join([f"'{h}'" for h in template_headings[:10]]) + ").\n"
                        "- BẮT BUỘC sinh đầy đủ TẤT CẢ các mục có trong template, TUYỆT ĐỐI KHÔNG gộp các mục lại với nhau và không được bỏ sót bất kỳ mục nào.\n"
                        "- Value là nội dung văn bản (string) tiếng Việt hoàn chỉnh, chứa bảng markdown chuẩn cho mục bảng.\n"
                        "- Tuyệt đối không dùng markdown block (như ```json) hay thêm văn bản giải thích thừa thãi bên ngoài khối JSON."
                    )

                try:
                    text, _ = await complete("statistical_report", LegacyRequest(), prompt_override=prompt_content)
                    text = text.strip()
                    if text.startswith("```json"):
                        text = text[7:]
                    if text.startswith("```"):
                        text = text[3:]
                    if text.endswith("```"):
                        text = text[:-3]
                    text = text.strip()

                    ai_data = json.loads(text)
                except Exception as e:
                    logger.error(f"AI gen for template failed: {e}")

                if isinstance(ai_data, dict):
                    if ai_data.get("document_title"):
                        doc_title_ai = str(ai_data["document_title"]).strip()
                    if "sections" in ai_data and isinstance(ai_data["sections"], list):
                        for sec in ai_data["sections"]:
                            if isinstance(sec, dict) and "heading" in sec and "content" in sec:
                                parsed_sections.append(sec)
                                ai_data[sec["heading"]] = sec["content"]
                    else:
                        for k, v in ai_data.items():
                            if k not in ["document_title", "meta"]:
                                parsed_sections.append({"heading": k, "content": str(v)})

            # Sanitizer to prevent duplicate headings and correct sub-heading numbers
            def sanitize_section_content(sec_num: str, heading_text: str, content_str: str) -> str:
                lines = [l for l in str(content_str).strip().split('\n')]
                if not lines:
                    return ""

                norm_head = normalize_key(heading_text)
                norm_head_noaccent = strip_accents(norm_head)

                while lines:
                    first_clean = clean_text(lines[0])
                    if not first_clean:
                        lines = lines[1:]
                        continue
                    first_norm = normalize_key(first_clean)
                    first_noaccent = strip_accents(first_norm)

                    if (first_norm == norm_head or 
                        (first_noaccent and first_noaccent == norm_head_noaccent) or
                        first_clean.lower().rstrip(':.-') == heading_text.lower().rstrip(':.-') or
                        first_clean.lower().startswith(heading_text.lower() + ':') or
                        first_clean.lower().startswith(heading_text.lower() + ' -') or
                        first_clean.lower().startswith(heading_text.lower() + ' –')):
                        lines = lines[1:]
                        continue

                    clean_first_header = re.sub(r'^[#*_\s]+|[#*_\s:]+$', '', first_clean)
                    if clean_first_header.lower() == heading_text.lower() or (first_noaccent and strip_accents(clean_first_header.lower()) == norm_head_noaccent):
                        lines = lines[1:]
                        continue
                    break

                while lines and not lines[0].strip():
                    lines = lines[1:]

                new_lines = []
                for line in lines:
                    l_s = line.strip()
                    match_sub = re.match(r'^([#*_\s]*)(\d+)\.(\d+(?:\.\d+)*)([\.\:\s-].*)$', l_s)
                    if match_sub:
                        lead = match_sub.group(1)
                        old_parent = match_sub.group(2)
                        sub_part = match_sub.group(3)
                        rest = match_sub.group(4)
                        if sec_num and old_parent != sec_num:
                            line = f"{lead}{sec_num}.{sub_part}{rest}"
                    new_lines.append(line)

                return '\n'.join(new_lines).strip()

            base_font = "Times New Roman"
            used_ai_keys = set()

            if is_freeform_idea and not file_bytes:
                if not parsed_sections:
                    # Provide robust intelligent default sections when LLM is unavailable or fails
                    parsed_sections = [
                        {"heading": "1. Thời gian và Phạm vi kiểm thử", "content": fallback_content_map.get("thoi_gian", "")},
                        {"heading": "2. Tổng quan kết quả kiểm thử", "content": fallback_content_map.get("tong_quan", "")},
                        {"heading": "3. Chi tiết các lỗ hổng an toàn thông tin", "content": fallback_content_map.get("chi_tiet", "")},
                        {"heading": "4. Khuyến nghị và Biện pháp khắc phục", "content": fallback_content_map.get("khuyen_nghi", "")},
                        {"heading": "5. Kết luận và Đề xuất hành động tiếp theo", "content": fallback_content_map.get("ket_luan", "")},
                    ]
                if user_prompt_desc and (not doc_title_ai or doc_title_ai == "BÁO CÁO ĐÁNH GIÁ AN TOÀN THÔNG TIN"):
                    clean_desc = user_prompt_desc.strip()
                    if clean_desc.lower().startswith("báo cáo"):
                        doc_title_ai = clean_desc.upper()
                    else:
                        doc_title_ai = f"BÁO CÁO {clean_desc.upper()}"

                # Dynamic full-report construction from AI planner
                doc_raw = docx.Document()
                for section in doc_raw.sections:
                    section.top_margin = docx.shared.Inches(1.0)
                    section.bottom_margin = docx.shared.Inches(1.0)
                    section.left_margin = docx.shared.Inches(1.0)
                    section.right_margin = docx.shared.Inches(1.0)

                # 1. Quốc hiệu, Tiêu ngữ
                p_qh = doc_raw.add_paragraph()
                p_qh.alignment = docx.enum.text.WD_ALIGN_PARAGRAPH.CENTER
                p_qh.paragraph_format.space_before = docx.shared.Pt(0)
                p_qh.paragraph_format.space_after = docx.shared.Pt(2)
                r_qh = p_qh.add_run("CỘNG HOÀ XÃ HỘI CHỦ NGHĨA VIỆT NAM\nĐộc lập - Tự do - Hạnh phúc")
                r_qh.bold = True
                r_qh.font.name = base_font
                r_qh.font.size = docx.shared.Pt(12)

                # 2. Ngày tháng
                p_dt = doc_raw.add_paragraph()
                p_dt.alignment = docx.enum.text.WD_ALIGN_PARAGRAPH.RIGHT
                p_dt.paragraph_format.space_before = docx.shared.Pt(2)
                p_dt.paragraph_format.space_after = docx.shared.Pt(6)
                r_dt = p_dt.add_run(f"Hà Nội, ngày {report_date_val.split('-')[-1] if '-' in report_date_val else '12'} tháng {report_date_val.split('-')[1] if len(report_date_val.split('-')) > 1 else '10'} năm {report_date_val.split('-')[0] if '-' in report_date_val else '2026'}")
                r_dt.italic = True
                r_dt.font.name = base_font
                r_dt.font.size = docx.shared.Pt(11)

                # 3. Tiêu đề
                p_tit = doc_raw.add_paragraph()
                p_tit.alignment = docx.enum.text.WD_ALIGN_PARAGRAPH.CENTER
                p_tit.paragraph_format.space_before = docx.shared.Pt(14)
                p_tit.paragraph_format.space_after = docx.shared.Pt(12)
                r_tit = p_tit.add_run(doc_title_ai.upper())
                r_tit.bold = True
                r_tit.font.name = base_font
                r_tit.font.size = docx.shared.Pt(16)

                # 4. Kính gửi
                p_kg = doc_raw.add_paragraph()
                p_kg.paragraph_format.left_indent = docx.shared.Pt(14)
                p_kg.paragraph_format.space_before = docx.shared.Pt(6)
                p_kg.paragraph_format.space_after = docx.shared.Pt(2)
                r_kg = p_kg.add_run("Kính gửi:")
                r_kg.bold = True
                r_kg.font.name = base_font
                r_kg.font.size = docx.shared.Pt(12)

                recip_list = ["- Thủ trưởng cơ quan, đơn vị;", "- Các đồng chí trong đơn vị"]
                if "ban giám đốc" in user_prompt_desc.lower():
                    recip_list = ["- Ban Giám đốc;", "- Các đồng chí phụ trách công nghệ và an toàn thông tin"]
                elif "lãnh đạo" in user_prompt_desc.lower():
                    recip_list = ["- Lãnh đạo đơn vị;", "- Bộ phận chuyên môn an toàn thông tin"]

                for idx_r, r_item in enumerate(recip_list):
                    p_rx = doc_raw.add_paragraph()
                    p_rx.paragraph_format.left_indent = docx.shared.Pt(28)
                    p_rx.paragraph_format.space_before = docx.shared.Pt(1)
                    p_rx.paragraph_format.space_after = docx.shared.Pt(6 if idx_r == len(recip_list) - 1 else 2)
                    r_run = p_rx.add_run(r_item)
                    r_run.font.name = base_font
                    r_run.font.size = docx.shared.Pt(12)

                # 5. Các sections
                curr_anchor = p_rx
                for sec in parsed_sections:
                    h_text = sec["heading"]
                    c_text = sec["content"]
                    p_h = doc_raw.add_paragraph()
                    p_h.paragraph_format.space_before = docx.shared.Pt(10)
                    p_h.paragraph_format.space_after = docx.shared.Pt(4)
                    r_h = p_h.add_run(h_text)
                    r_h.bold = True
                    r_h.font.name = base_font
                    r_h.font.size = docx.shared.Pt(13)

                    sec_num = extract_section_number(h_text) or "1"
                    c_clean = sanitize_section_content(sec_num, h_text, c_text)
                    curr_anchor = render_content_with_tables(doc_raw, p_h, c_clean, base_font=base_font)

                # 6. Chữ ký
                p_sig1 = doc_raw.add_paragraph()
                p_sig1.alignment = docx.enum.text.WD_ALIGN_PARAGRAPH.RIGHT
                p_sig1.paragraph_format.space_before = docx.shared.Pt(18)
                p_sig1.paragraph_format.space_after = docx.shared.Pt(2)
                r_s1 = p_sig1.add_run("CHỈ HUY")
                r_s1.bold = True
                r_s1.font.name = base_font
                r_s1.font.size = docx.shared.Pt(12)

                p_name1 = doc_raw.add_paragraph()
                p_name1.alignment = docx.enum.text.WD_ALIGN_PARAGRAPH.RIGHT
                p_name1.paragraph_format.space_before = docx.shared.Pt(2)
                p_name1.paragraph_format.space_after = docx.shared.Pt(10)
                r_n1 = p_name1.add_run("Nguyễn Văn Tốt")
                r_n1.bold = True
                r_n1.font.name = base_font
                r_n1.font.size = docx.shared.Pt(12)

                p_sig2 = doc_raw.add_paragraph()
                p_sig2.alignment = docx.enum.text.WD_ALIGN_PARAGRAPH.RIGHT
                p_sig2.paragraph_format.space_before = docx.shared.Pt(4)
                p_sig2.paragraph_format.space_after = docx.shared.Pt(2)
                r_s2 = p_sig2.add_run("NGƯỜI SOẠN BÁO CÁO")
                r_s2.bold = True
                r_s2.font.name = base_font
                r_s2.font.size = docx.shared.Pt(12)

                p_name2 = doc_raw.add_paragraph()
                p_name2.alignment = docx.enum.text.WD_ALIGN_PARAGRAPH.RIGHT
                p_name2.paragraph_format.space_before = docx.shared.Pt(2)
                p_name2.paragraph_format.space_after = docx.shared.Pt(4)
                r_n2 = p_name2.add_run("Bùi Thị Xuân")
                r_n2.bold = True
                r_n2.font.name = base_font
                r_n2.font.size = docx.shared.Pt(12)

            else:
                # In-place insertion for structured template or uploaded docx with headings
                matched_section_counter = 0

                for idx_p, p in enumerate(all_paragraphs):
                    p_text = clean_text(p.text)
                    if not p_text:
                        continue
                    if is_static_template_element(p_text):
                        continue

                    extracted_num = extract_section_number(p_text)
                    if extracted_num:
                        sec_num = extracted_num
                        if sec_num.isdigit():
                            matched_section_counter = max(matched_section_counter, int(sec_num))
                    else:
                        matched_section_counter += 1
                        sec_num = str(matched_section_counter)

                    p_norm_noaccent = strip_accents(normalize_key(p_text))
                    is_time_section = p_norm_noaccent == "thoi gian" or any(syn in p_norm_noaccent for syn in SEMANTIC_SYNONYMS["thoi gian"])
                    is_detail_section = p_norm_noaccent == "chi tiet" or any(syn in p_norm_noaccent for syn in SEMANTIC_SYNONYMS["chi tiet"])
                    is_overview_section = p_norm_noaccent == "tong quan" or any(syn in p_norm_noaccent for syn in SEMANTIC_SYNONYMS["tong quan"])
                    is_rec_section = p_norm_noaccent == "khuyen nghi" or any(syn in p_norm_noaccent for syn in SEMANTIC_SYNONYMS["khuyen nghi"])
                    is_conclusion_section = p_norm_noaccent == "ket luan" or any(syn in p_norm_noaccent for syn in SEMANTIC_SYNONYMS["ket luan"])

                    matched_k, content = find_matching_content(p_text, ai_data, used_ai_keys)

                    if not content:
                        if is_time_section:
                            content = (
                                f"{sec_num}.1. Thời gian thực hiện kiểm thử: Thực hiện từ lúc {scan_time_val}.\n"
                                f"{sec_num}.2. Thời điểm ghi nhận và lập báo cáo: Ngày {report_date_val}."
                            )
                            matched_k = matched_k or "thoi_gian"
                        elif is_detail_section:
                            target_sec_num = sec_num if sec_num else "3"
                            content = format_vulnerabilities_table(raw_vulns, sec_num=target_sec_num) if raw_vulns else ""
                            matched_k = matched_k or "chi_tiet"
                        elif is_overview_section:
                            target_sec_num = sec_num if sec_num else "2"
                            intro_txt = f"{target_sec_num}.1. Phạm vi và tóm tắt kiểm thử: Đánh giá an toàn thông tin toàn diện trên hệ thống mục tiêu."
                            risk_txt = f"\n\n{target_sec_num}.3. Đánh giá rủi ro tổng thể: Hệ thống đang đối mặt với các rủi ro an toàn thông tin cần được xem xét và xử lý theo đúng mức độ ưu tiên."
                            content = intro_txt + "\n\n" + format_summary_table(scan_data, sec_num=target_sec_num) + risk_txt
                            matched_k = matched_k or "tong_quan"
                        elif is_rec_section:
                            content = (
                                f"{sec_num}.1. Các biện pháp khắc phục khẩn cấp:\n"
                                f"a) Vá và cập nhật các lỗ hổng bảo mật đã phát hiện theo đúng khuyến nghị kỹ thuật.\n"
                                f"b) Thiết lập cấu hình tường lửa ứng dụng (WAF) và hạn chế truy cập trái phép đối với các dịch vụ mạng trọng yếu.\n"
                                f"{sec_num}.2. Kế hoạch phòng ngừa và giám sát định kỳ:\n"
                                f"a) Thiết lập lịch rà quét an ninh mạng tự động định kỳ hàng tuần để phát hiện sớm các nguy cơ mới.\n"
                                f"b) Nâng cao nhận thức an toàn thông tin và giám sát nhật ký truy cập hệ thống thường xuyên."
                            )
                            matched_k = matched_k or "khuyen_nghi"
                        elif is_conclusion_section:
                            content = (
                                f"{sec_num}.1. Đánh giá hiện trạng an toàn hệ thống:\n"
                                f"Hệ thống mục tiêu đã được kiểm tra và đánh giá toàn diện, phát hiện các điểm yếu bảo mật cần được khắc phục kịp thời nhằm đảm bảo an toàn vận hành.\n"
                                f"{sec_num}.2. Đề xuất hành động tiếp theo:\n"
                                f"Đơn vị vận hành cần khẩn trương thực hiện kế hoạch khắc phục theo thứ tự ưu tiên và tiến hành tái kiểm thử sau khi hoàn thành vá lỗi."
                            )
                            matched_k = matched_k or "ket_luan"
                        else:
                            content = (
                                f"{sec_num}.1. Nội dung đánh giá chuyên môn: Đã tiến hành khảo sát và đánh giá toàn diện theo yêu cầu kỹ thuật.\n"
                                f"{sec_num}.2. Đề xuất thực hiện: Tiếp tục theo dõi và phối hợp triển khai các biện pháp tương ứng."
                            )
                            matched_k = matched_k or p_text

                    if is_detail_section and raw_vulns and "|" not in str(content):
                        target_sec_num = sec_num if sec_num else "3"
                        cur_details_str = format_vulnerabilities_table(raw_vulns, sec_num=target_sec_num)
                        content = cur_details_str + ("\n\n" + str(content) if content else "")

                    if is_overview_section and "|" not in str(content):
                        target_sec_num = sec_num if sec_num else "2"
                        cur_summary_str = format_summary_table(scan_data, sec_num=target_sec_num)
                        if cur_summary_str:
                            content = str(content) + "\n\n" + cur_summary_str

                    if content:
                        content_clean = sanitize_section_content(sec_num, p_text, str(content))
                        if not content_clean:
                            continue
                        if content_clean.lower() == p_text.lower():
                            continue
                        if idx_p + 1 < len(all_paragraphs):
                            next_p_text = clean_text(all_paragraphs[idx_p + 1].text)
                            if content_clean.lower() == next_p_text.lower():
                                continue

                        if matched_k:
                            used_ai_keys.add(matched_k)
                        render_content_with_tables(doc_raw, p, content_clean, base_font=base_font)

                if matched_section_counter == 0 and parsed_sections:
                    for sec in parsed_sections:
                        h_text = sec["heading"]
                        c_text = sec["content"]
                        p_h = doc_raw.add_paragraph()
                        p_h.paragraph_format.space_before = docx.shared.Pt(12)
                        p_h.paragraph_format.space_after = docx.shared.Pt(4)
                        p_h.paragraph_format.keep_with_next = True
                        r_h = p_h.add_run(h_text)
                        r_h.bold = True
                        r_h.font.name = base_font
                        r_h.font.size = docx.shared.Pt(13)

                        sec_num = extract_section_number(h_text) or "1"
                        c_clean = sanitize_section_content(sec_num, h_text, c_text)
                        render_content_with_tables(doc_raw, p_h, c_clean, base_font=base_font)

            # Sync any remaining ai_data into context for docxtpl variables
            for k, v in ai_data.items():
                if k not in used_ai_keys:
                    context[k] = v
                for var in undeclared:
                    if normalize_key(var) == normalize_key(k) or strip_accents(normalize_key(var)) == strip_accents(normalize_key(k)):
                        context[var] = v

            intermediate_docx = os.path.join(tmpdir, f"inter_{uuid.uuid4()}.docx")
            doc_raw.save(intermediate_docx)

            output_docx = os.path.join(tmpdir, f"output_{uuid.uuid4()}.docx")
            try:
                final_tpl = DocxTemplate(intermediate_docx)
                final_tpl.render(context)
                final_tpl.save(output_docx)
            except Exception as e:
                logger.warning(f"DocxTemplate render fallback to doc_raw: {e}")
                output_docx = intermediate_docx

            try:
                final_doc = docx.Document(output_docx)
                postprocess_document(final_doc)
                final_doc.save(output_docx)
            except Exception as e:
                logger.warning(f"postprocess_document failed: {e}")

            def docx_to_markdown(doc) -> str:
                md_blocks = []
                for element in doc._body._element:
                    if element.tag.endswith('p'):
                        p_elem = docx.text.paragraph.Paragraph(element, doc)
                        text = p_elem.text.strip()
                        if not text:
                            continue
                        t_noaccent = strip_accents(text).lower()
                        
                        if any(q in t_noaccent for q in ['cong hoa xa hoi chu nghia viet nam', 'doc lap - tu do - hanh phuc', 'doc lap – tu do – hanh phuc']):
                            md_blocks.append(f"**{text}**")
                        elif t_noaccent in ['bao cao', 'bao cao danh gia an toan thong tin', 'bao cao ket qua', 'bao cao kiem thu'] or (t_noaccent.startswith('bao cao') and not extract_section_number(text)):
                            md_blocks.append(f"# {text}")
                        elif re.match(r'^(?:kinh gui|noi nhan)\b', t_noaccent):
                            md_blocks.append(f"**{text}**")
                        elif extract_section_number(text) is not None and not re.match(r'^\d+\.\d+', text):
                            md_blocks.append(f"## {text}")
                        elif re.match(r'^\d+\.\d+(?:\.\d+)*', text):
                            md_blocks.append(f"### {text}")
                        elif re.match(r'^[a-z]\)\s*', text, flags=re.IGNORECASE):
                            md_blocks.append(f"- {text}")
                        elif text.startswith('• ') or text.startswith('- '):
                            bullet_clean = re.sub(r'^[\-\*•]\s*', '', text)
                            md_blocks.append(f"- {bullet_clean}")
                        elif any(sig in t_noaccent for sig in ['chi huy', 'nguoi soan bao cao', 'nguoi lap', 'nguoi duyet', 'giam doc', 'truong phong']):
                            md_blocks.append(f"\n**{text}**")
                        else:
                            md_blocks.append(text)
                    elif element.tag.endswith('tbl'):
                        tbl_elem = docx.table.Table(element, doc)
                        rows_cells = []
                        for row in tbl_elem.rows:
                            cells = [c.text.strip().replace('\n', '<br>').replace('|', '\\|') for c in row.cells]
                            rows_cells.append(cells)
                        if rows_cells:
                            headers = rows_cells[0]
                            tbl_md = []
                            tbl_md.append("| " + " | ".join(headers) + " |")
                            tbl_md.append("| " + " | ".join(["---"] * len(headers)) + " |")
                            for r in rows_cells[1:]:
                                tbl_md.append("| " + " | ".join(r) + " |")
                            md_blocks.append("\n".join(tbl_md))

                result_lines = []
                for b in md_blocks:
                    if result_lines and (b.startswith('- ') and (result_lines[-1].startswith('- ') or result_lines[-1].startswith('**Kính gửi'))):
                        result_lines.append(b)
                    else:
                        if result_lines:
                            result_lines.append("")
                        result_lines.append(b)
                return "\n".join(result_lines)

            md_text = docx_to_markdown(final_doc)

            if output_format_lower in ["markdown", "md", "text"]:
                return Response(
                    content=md_text.encode("utf-8"),
                    media_type="text/markdown; charset=utf-8",
                    headers={
                        "Content-Disposition": 'inline; filename="report.md"',
                        "X-Report-Format": "markdown"
                    }
                )

            # Determine target document format (pdf or docx)
            effective_file_format = (file_format or "").strip().lower()
            if output_format_lower == "pdf" or effective_file_format == "pdf":
                # Convert to PDF using LibreOffice
                process = await asyncio.create_subprocess_exec(
                    "soffice", "--headless", "--convert-to", "pdf", output_docx, "--outdir", tmpdir,
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
                )
                stdout, stderr = await process.communicate()
                if process.returncode != 0:
                    logger.error(f"PDF conversion failed: {stderr.decode()}")
                    raise HTTPException(status_code=500, detail="Lỗi chuyển đổi PDF (LibreOffice).")
                
                base_name = os.path.splitext(os.path.basename(output_docx))[0]
                output_file = os.path.join(tmpdir, f"{base_name}.pdf")
                filename = "report.pdf"
                media_type = "application/pdf"
                target_format = "pdf"
            else:
                output_file = output_docx
                filename = "report.docx"
                media_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                target_format = "docx"

            with open(output_file, "rb") as f:
                content_bytes = f.read()

            raw_binary_flag = raw_binary is True or str(raw_binary).strip().lower() in ["true", "1", "yes"]
            if raw_binary_flag:
                return Response(
                    content=content_bytes,
                    media_type=media_type,
                    headers={"Content-Disposition": f"attachment; filename={filename}"}
                )

            file_b64 = base64.b64encode(content_bytes).decode("utf-8")
            return JSONResponse(
                content={
                    "status": "success",
                    "format": target_format,
                    "file_name": filename,
                    "media_type": media_type,
                    "markdown": md_text,
                    "file_base64": file_b64,
                    "sections": ai_data
                }
            )


    @app.post("/ai/generate-statistical-report/stream")
    async def statistical_stream(body: LegacyRequest) -> StreamingResponse:
        async def build_final(final: str, run_id: str | None) -> dict[str, Any]:
            result = parse_statistical(final)
            table = statistical_detail_table(body)
            if table is not None:
                result["report_detail"] = merge_detail_table(result.get("report_detail", ""), table)
            return {**result, "content": final, "report": final, "statistical_report": final, "metadata": base_meta("statistical_report", run_id)}
        return stream("statistical_report", statistical_prompt(body), None, build_final)

    async def exploit_prefetch(prompt: str, session_id: str | None = None) -> dict[str, Any]:
        """Gather catalog and exact KB context concurrently, scoped to this request."""
        from ..poc.prefetch import (
            clear_session_coverage, exploit_relevance_ok, set_session_coverage,
            set_session_cve_known, set_session_kb_docs, set_session_kb_searched,
            set_session_real_code_evidence, set_session_synthesis_evidence,
        )
        from ..retrieval.kb_context import get_cve_context

        clear_session_coverage(session_id)
        cve_id = extract_cve_id(prompt)
        poc = getattr(state.runner, "poc", None)
        ledger = getattr(state.runner, "ledger", None)
        run_id = f"prefetch:{uuid4().hex}"

        async def catalog_search() -> dict[str, Any]:
            if poc is None or ledger is None:
                return {"coverage": "failed"}
            return await prefetch_evidence(poc, ledger, prompt, run_id)

        async def kb_search() -> dict[str, Any]:
            if not cve_id:
                return {"status": "not_queried", "content": "", "cwe_refs": []}
            return await get_cve_context(cve_id)

        report, kb = await asyncio.gather(catalog_search(), kb_search(), return_exceptions=True)
        if isinstance(report, BaseException):
            report = {"coverage": "failed"}
        if isinstance(kb, BaseException):
            kb = {"status": "error", "content": "", "cwe_refs": []}
        status = kb.get("status", "error")
        kb_content = str(kb.get("content") or "") if status == "found" else ""
        cve_known = status == "found" and bool(kb_content.strip())
        if status == "found" and not cve_known:
            status = "insufficient"
        evidence = ledger.evidence_for_run(run_id) if ledger is not None else []
        coverage = report.get("coverage", "failed")
        if coverage not in ("enough", "empty", "partial", "failed"):
            coverage = "failed"
        if not cve_id and poc is not None and await exploit_relevance_ok(prompt) is False:
            coverage, evidence = "empty", []
        set_session_coverage(session_id, coverage)
        set_session_cve_known(session_id, cve_known)
        set_session_kb_searched(session_id, cve_known)
        set_session_kb_docs(session_id, kb_content)
        set_session_synthesis_evidence(session_id, has_synthesis_evidence(evidence))
        set_session_real_code_evidence(session_id, has_real_code_evidence(evidence))
        return {"evidence": evidence, "cve_id": cve_id, "cve_known": cve_known,
                "cwe_refs": kb.get("cwe_refs") or [], "kb_content": kb_content,
                "kb_status": status, "coverage": coverage}

    async def inject_reference_code(text: str, run_id: str | None,
                                    pre_evidence: list[dict[str, Any]] | None = None, *,
                                    cve_known: bool = False,
                                    cve_id: str | None = None,
                                    prompt_text: str | None = None,
                                    target: str | None = None) -> tuple[str, dict[str, Any]]:
        """Deterministic Section-1 placement via `enforce_section1` (poc/inject.py): the
        tier (real fetched code / synthesized-from-README / synthesized-from-CVE-definition
        / not-found) is decided from the merged evidence (deterministic prefetch + whatever
        the model's own agentic run gathered, deduplicated by sha256) and `cve_known`, never
        from what the model claims. Real evidence → the exact fetched code (never the
        model's own words). Synthesize tiers → the model's own Section-1 text (it was told
        to write one only for these tiers) with a mandatory AI-SYNTHESIZED banner
        force-prepended. Neither → the canonical "no PoC info" sentence, naming the CVE
        when one was identified. Also strips any line where the model narrates the
        Section-1 auto-insertion mechanism back to the user (observed live — a leak of the
        system prompt's own instruction wording into the visible answer). Fail-open — on
        any error the model's own text is returned so this never breaks the response.

        Before rendering, each evidence file's context-truncated code is swapped for its FULL
        store bytes (`_fullen_evidence_for_display`) — the model already ran on the bounded
        slice, so the DISPLAYED Section 1 (which the FE renders) shows the complete PoC while
        the model context stays capped. No new endpoint, no FE change needed.

        Also redacts any REAL target host the user named in `prompt_text`/`target` (see
        `collect_named_targets`) out of the model's Mục 2/3 prose and force-inserts the
        mandatory authorization disclaimer under Mục 2 — PC2 (2026-09-22): a request that
        both asked for a real CVE's PoC and named a live third-party host got that host
        baked verbatim into a runnable Mục 2 command with no authorization framing at all."""
        ledger = getattr(state.runner, "ledger", None)
        if text.strip() == _EXPLOIT_KB_SEARCH_EXHAUSTED.strip():
            return text, {"candidate_status": "not_generated", "section1_tier": "synthesis_failed",
                          "reference_code_injected": False, "reason": "kb_search_before_synthesis_exhausted"}
        try:
            text = strip_system_narration(text)
            # Deterministic backstop for the sibling leak: internal TOOL identifiers
            # (poc_search, qdrant_search, …) named in the model's own "Phân tích bằng chứng"
            # prose instead of described in plain Vietnamese — observed live for the
            # synthesized tiers. Word-substitutes in place (keeps the surrounding analysis).
            text = strip_tool_name_mentions(text)
            # Deterministic air-gap backstop: strip internet/URL/upstream-path leaks the model
            # may still emit in Mục 2/3 despite prompt rules. Runs on model text only, before
            # Section-1 injection, so the real injected PoC code is never rewritten.
            text = sanitize_airgap_runguide(text, cve_id)
            # Deterministic target-redaction + authorization backstop (PC2): same placement
            # (model text only, before Section-1 injection) so the real injected PoC source
            # is never touched — only the model's own Mục 2/3 prose/commands are.
            text = redact_user_target(text, collect_named_targets(prompt_text, target))
            text = ensure_target_disclaimer(text)
            run_evidence = ledger.evidence_for_run(run_id) if (ledger is not None and run_id) else []
            evidence = merge_evidence(pre_evidence or [], run_evidence)
            evidence = await _fullen_evidence_for_display(evidence, getattr(state.runner, "poc", None))
            text, report = enforce_section1(text, evidence, cve_known=cve_known, cve_id=cve_id)
            # Curated Mục 3 override for fixed (lab-verified) CVEs — the model's Tổng quan can
            # hallucinate product/version/class; the store carries an authoritative section.
            fixed3 = next((e.get("section3") for e in evidence
                           if e.get("fixed") and e.get("section3")), None)
            if fixed3:
                text = override_section3(text, fixed3)
            return text, report
        except Exception:
            logger.exception("reference-code injection failed run_id=%s", run_id)
            return text, {"reference_code_injected": False, "reason": "error"}

    def exploit_final_meta(run_id: str | None) -> dict[str, Any]:
        meta = base_meta("exploit_research", run_id)
        ledger = getattr(state.runner, "ledger", None)
        if ledger is not None and run_id:
            meta.update(ledger.exploit_metadata(run_id))
        meta.setdefault("evidence_ids", []); meta.setdefault("edb_ids", [])
        meta.setdefault("provenance", []); meta.setdefault("candidate_status", "not_generated")
        meta.setdefault("cwe_refs", [])  # H1: populated from prefetch_result by the caller
        return meta

    def exploit_guidance_meta() -> dict[str, Any]:
        """Metadata for the chitchat→"nhập CVE" guidance reply: keeps the same shape the FE
        reads (evidence/provenance/candidate_status) but flags no PoC work was done."""
        meta = base_meta("exploit_research", None)
        meta.update({"evidence_ids": [], "edb_ids": [], "provenance": [], "cwe_refs": [],
                     "candidate_status": "not_generated",
                     "reference_code_injected": False, "input_kind": "chitchat"})
        return meta

    async def _maybe_web_hints(prefetch_result: dict[str, Any]) -> list[dict[str, Any]]:
        """Deterministic web-research when WEB_SEARCH_ENABLED and the catalog has NO real
        exploit code for the CVE. Per the requirement "any CVE not in the DB must be
        websearched", this fires for ANY identified CVE-ID lacking fetched code — not only
        CVEs already confirmed in the internal KB (``cve_known``). ``gather_web_hints`` has its
        own CVE-mention relevance gate, so a bogus/unknown id simply returns []. The hints feed
        (a) a raw PoC script found on the web → real code → consolidation, or (b) advisory/
        description text → the synthesize-a-logic-following-PoC tier. Fail-open — any miss
        (disabled, no SearXNG, error) → []."""
        if not getattr(state.settings, "web_search_enabled", False):
            return []
        # Real code already in the catalog (T1) — no web needed. Description-only catalog
        # evidence does NOT block web research: we still want the internet's richer hints.
        if has_real_code_evidence(prefetch_result.get("evidence") or []):
            return []
        cve_id = prefetch_result.get("cve_id")
        if not cve_id:
            return []
        ledger = getattr(state.runner, "ledger", None)
        if ledger is None:
            return []
        try:
            return await gather_web_hints(cve_id, ledger, f"webhint:{uuid4().hex}")
        except Exception:
            logger.exception("web hints gather failed")
            return []

    def _evidence_is_python2(ev: dict[str, Any]) -> bool:
        """True when an evidence dict's ENTRY code carries strong Python-2-only markers."""
        code = ev.get("source_code") or ""
        if not code:
            sf = ev.get("source_files") or []
            entry = next((f for f in sf if (f.get("role") or "") == "entry"),
                         sf[0] if sf else None)
            code = (entry or {}).get("code") or ""
        return looks_like_python2(code)

    async def _build_exploit_grounding(
        prefetch_result: dict[str, Any], web_hints: list[dict[str, Any]],
        session_id: str | None, target: str | None = None,
        strategy_override: str | None = None
    ) -> tuple[list[dict[str, Any]], str, bool, dict[str, Any]]:
        """Acquire the best real PoC code (catalog → temp cache → on-demand GitHub → web raw
        script), consolidate it into ONE runnable file when enabled, and build the model
        appendix. Internet-fetched code is saved to the temp cache for reuse next time.

        Returns ``(pre_evidence, appendix, consolidated, acquisition)``:
        * consolidated → ``pre_evidence`` is ``[merged_file] + description-only hints``; the
          appendix tells the model Mục 1 is that file (system-injected) and to write Mục 2/3
          against it. Session real-code/coverage flags are set so the synthesis/search hooks
          don't force a spurious retry.
        * not consolidated → the unchanged verbatim path: on-demand/web real code is still
          merged in (better coverage), and the existing prefetch + web-hint appendices apply.
        * ``acquisition`` — ACQUISITION-level flags (how the winning evidence was found, as
          opposed to per-evidence flags like `consolidated_from`/`pruned`/`extracted` which
          live on the evidence dict itself) for `poc/trace.py::build_generation_trace` (Mục 4,
          "Giải thích quy trình tạo PoC"). Deterministic bookkeeping only — never influences
          which evidence is chosen, purely descriptive of the path already taken above.
        """
        from ..poc.ondemand_cache import load_cached, resolve_cache_dir, save_cached
        from ..poc.fixed_pocs import load_fixed, resolve_fixed_dir
        from ..poc.prefetch import set_session_coverage, set_session_real_code_evidence
        settings = state.settings
        poc = getattr(state.runner, "poc", None)
        cve_id = prefetch_result.get("cve_id")
        base_evidence = prefetch_result.get("evidence") or []
        base_real = [e for e in base_evidence if has_real_code_evidence([e])]

        # Acquisition priority when the catalog has no code: TEMP cache → INTERNET (GitHub
        # on-demand). Internet-acquired code is saved back to the temp cache below so the next
        # request for the same CVE reuses it instead of re-downloading.
        cache_dir = resolve_cache_dir(settings)
        extra_real: list[dict[str, Any]] = []
        cached_hit = False
        if not base_real and cve_id:
            cached = load_cached(cache_dir, cve_id)
            if cached:
                extra_real = [cached]
                cached_hit = True
            elif poc is not None and getattr(settings, "poc_github_ondemand_enabled", False):
                extra_real = await _ondemand_github_evidence(
                    poc, cve_id, int(getattr(settings, "poc_github_ondemand_timeout_s", 30)))

        web_real = [e for e in (web_hints or []) if e.get("source_code")]
        all_real = base_real + extra_real
        used_web_real = not all_real and bool(web_real)
        if not all_real:
            all_real = web_real  # web raw PoC script → real code (also internet)

        # Mục 4 ("Giải thích quy trình tạo PoC") bookkeeping — see acquisition docstring above.
        # github_ondemand = extra_real populated AND it did NOT come from the temp cache.
        acquisition: dict[str, Any] = {
            "canonical": False, "fixed": False,
            "cached_hit": cached_hit,
            "github_ondemand": bool(extra_real) and not cached_hit,
            "web_real": used_web_real,
            "is_multifile": False, "strategy": None,
        }

        # Cache whatever we pulled from the internet (GitHub on-demand or web) so DB→temp→internet
        # holds next time. Skip DB-sourced code (already persistent) and cache re-reads.
        if cve_id and not base_real and not cached_hit and all_real:
            save_cached(cache_dir, cve_id, all_real[0])

        if all_real:  # tell the hooks real code exists (prefetch only saw the catalog)
            set_session_real_code_evidence(session_id, True)
            set_session_coverage(session_id, "enough")

        consolidated: dict[str, Any] | None = None
        # Verified-good override: a PoC previously fixed + lab-verified wins over the LLM
        # consolidation, so the CVE returns the known-good runnable file with zero re-fix.
        # Scoped to the request's target lab when one is identifiable (one CVE → many labs).
        fixed = load_fixed(resolve_fixed_dir(settings), cve_id, target=target) if cve_id else None
        if fixed:
            set_session_real_code_evidence(session_id, True)
            set_session_coverage(session_id, "enough")
            # Keep response latency comparable to a normal CVE (which pays an LLM consolidation
            # pass) instead of returning the cached copy instantly.
            delay = float(getattr(settings, "poc_fixed_simulate_delay_s", 0) or 0)
            if delay > 0:
                await asyncio.sleep(delay)
            consolidated = fixed
            acquisition["fixed"] = True
        canonical_entry = any(e.get("canonical") for e in all_real)
        if canonical_entry:
            acquisition["canonical"] = True
            # Verified-canonical PoC (poc_canonical, executed against a lab/target): serve ONLY
            # that artifact, VERBATIM — no LLM slice/consolidate, and no sibling artifact from
            # the same prefetch (rendering two real PoCs would collide on identical filenames and
            # the entry would be overwritten). This makes /ai/exploit return the exact
            # known-working PoC bytes, deterministically.
            canon_ev = next(e for e in all_real if e.get("canonical"))
            desc_only = [e for e in list(base_evidence) + list(web_hints or [])
                         if not has_real_code_evidence([e])]
            pre_evidence = [canon_ev] + merge_evidence(desc_only)
            appendix = (_prefetch_prompt_appendix({**prefetch_result, "evidence": [canon_ev]})
                        + _web_hints_prompt_appendix(web_hints or [])
                        + _kb_prompt_appendix(prefetch_result.get("kb_content")))
            return pre_evidence, appendix, False, acquisition
        elif all_real and (getattr(settings, "poc_consolidate_enabled", False)
                           or _resolve_poc_strategy(strategy_override, settings) in ("prune", "auto")):
            try:
                real_full = await _fullen_evidence_for_display(list(all_real), poc)
                entry_ev = real_full[0]
                strategy = _resolve_poc_strategy(strategy_override, settings)
                can_consolidate = getattr(settings, "poc_consolidate_enabled", False)
                use_llm = getattr(settings, "poc_slice_llm_enabled", True)
                # Multi-file iff the entry carries a >1 non-readme file set.
                _sf = entry_ev.get("source_files") or []
                is_multifile = len([f for f in _sf if (f.get("role") or "") != "readme"]) > 1
                acquisition["is_multifile"] = is_multifile
                acquisition["strategy"] = strategy
                if strategy == "consolidate":
                    # Legacy path, unchanged: deterministic verbatim slice first (no LLM on the
                    # bytes), else the LLM merge fills what slicing can't (cross-file, non-Python).
                    sliced = await slice_entry(entry_ev, state.runner, cve_id, use_llm=use_llm)
                    consolidated = sliced if sliced is not None else (
                        await consolidate_poc(state.runner, real_full, cve_id) if can_consolidate else None)
                else:
                    # "prune" (default) / "auto": keep the ORIGINAL multi-file structure and remove
                    # only unnecessary parts (higher fidelity, air-gap-verifiable — no LLM rewrite);
                    # a single-file PoC still takes the deterministic verbatim slice.
                    if is_multifile:
                        consolidated = await prune_evidence(entry_ev, state.runner, cve_id, use_llm=use_llm)
                    else:
                        consolidated = await slice_entry(entry_ev, state.runner, cve_id, use_llm=use_llm)
                    # LLM merge is the LAST resort — only if prune/slice produced nothing and it's on.
                    if consolidated is None and can_consolidate:
                        consolidated = await consolidate_poc(state.runner, real_full, cve_id)

                # Python-2 PoC: the air-gap runner executes with python3, so a verbatim/pruned
                # py2 entry (`urllib2`/`urlparse`/`raw_input`…) dies at import before it can
                # exploit (observed: CVE-2014-6271 served a `urllib2` PoC → ImportError). Route
                # it through the consolidate LLM (instructed to port to py3) and keep the port
                # only if it is genuinely py3 afterwards; otherwise leave the py2 bytes as-is so
                # this can never make a working PoC worse.
                if consolidated is not None and can_consolidate and _evidence_is_python2(consolidated):
                    ported = await consolidate_poc(state.runner, real_full, cve_id)
                    if ported and has_real_code_evidence([ported]) and not _evidence_is_python2(ported):
                        consolidated = ported
            except Exception:
                logger.exception("poc grounding (strategy=%s) failed for %s",
                                 _resolve_poc_strategy(strategy_override, settings), cve_id)
                consolidated = None

        # PC4 (2026-09-22): a stub/degenerate merge output (poc/inject.py::_is_stub_markup —
        # e.g. the LLM ignoring its real source and emitting a narrative "it worked" HTML shell,
        # observed live for CVE-2025-30065, tagged `section1_tier=real_consolidated` even though
        # Mục 1 had zero exploit logic) must NOT be used as Section 1 just because *some* bytes
        # came back. Treat it exactly like a failed consolidation (`consolidated is None`) and
        # fall through to the verbatim/synthesis path below, which still has the genuinely real
        # fetched evidence (`base_evidence`/`extra_real`, untouched by consolidate_poc) to inject
        # instead of silently losing a real PoC just because its LLM merge degraded.
        if consolidated and has_real_code_evidence([consolidated]):
            desc_only = [e for e in list(base_evidence) + list(web_hints or [])
                         if not has_real_code_evidence([e])]
            pre_evidence = [consolidated] + merge_evidence(desc_only)
            return pre_evidence, _consolidated_prompt_appendix(consolidated, cve_id), True, acquisition

        # Fallback (consolidation off/failed, or no code anywhere): unchanged verbatim/synthesis.
        merged_for_appendix = {**prefetch_result,
                               "evidence": merge_evidence(base_evidence, extra_real)}
        pre_evidence = merge_evidence(base_evidence, extra_real, web_hints or [])
        appendix = (_prefetch_prompt_appendix(merged_for_appendix)
                    + _web_hints_prompt_appendix(web_hints or [])
                    + _kb_prompt_appendix(prefetch_result.get("kb_content")))
        return pre_evidence, appendix, False, acquisition

    @app.post("/ai/exploit")
    async def exploit(body: LegacyRequest) -> dict[str, Any]:
        with session_coverage_scope():
            return await _exploit(body)

    async def _exploit(body: LegacyRequest) -> dict[str, Any]:
        prompt_text = require_public_input(body.supplied_text())
        if _is_exploit_chitchat(prompt_text):
            return {"description": EXPLOIT_CVE_GUIDE, "exploit_guide": EXPLOIT_CVE_GUIDE,
                    "metadata": exploit_guidance_meta()}
        prompt_text = normalize_cve_ids(prompt_text)
        session_id = _exploit_session_id(body)
        # Uniform total-latency floor (anti timing-side-channel): pad to this AFTER the work,
        # not a fixed sleep before it — see _pad_to_floor. Capture the start here so the whole
        # pipeline counts toward the floor (a slow real pipeline is not padded on top).
        _floor = float(getattr(state.settings, "poc_exploit_simulate_delay_s", 0) or 0)
        _start = time.monotonic()

        # Validate CVE format (reject invalid suffixes like "zz")
        cve_match = _CVE_ANY.search(prompt_text)
        if cve_match:
            cve_id = cve_match.group(0).upper()
            # Check if there's a non-numeric suffix immediately after the CVE number
            end_pos = cve_match.end()
            if end_pos < len(prompt_text) and prompt_text[end_pos].isalpha():
                fmt_err = f"Định dạng CVE không hợp lệ: '{cve_id}{prompt_text[end_pos:].split()[0]}'. Cần CVE-YYYY-NNNNN (ví dụ CVE-2026-42533)."
                return {"description": fmt_err, "exploit_guide": fmt_err,
                        "metadata": {"error": fmt_err, "cve_id": cve_id}}
            valid, fmt_err = _validate_cve_format(cve_id)
            if not valid:
                return {"description": fmt_err, "exploit_guide": fmt_err,
                        "metadata": {"error": fmt_err, "cve_id": cve_id}}

        kt_run_id = f"kt:{uuid4().hex}"
        kt_result = await _run_kt_if_applicable(state.settings, body, prompt_text, kt_run_id)
        # Await prefetch BEFORE the model so its fetched PoC grounds the model's Mục 2/3 (see
        # _prefetch_prompt_appendix). A successful prefetch otherwise stands the search hooks
        # down and the model non-deterministically writes "no info" under a populated Section
        # 1. Adds prefetch latency to the critical path — it's a local catalog lookup, fast.
        prefetch_result = await exploit_prefetch(prompt_text, session_id)
        # No real catalog code → websearch (any CVE not in DB) so the consolidate/synthesize
        # tiers have real hints; no-op unless WEB_SEARCH_ENABLED. gather_web_hints self-gates
        # on CVE relevance, so an unknown id simply yields nothing.
        web_hints = await _maybe_web_hints(prefetch_result)
        # Acquire best real code (catalog → on-demand GitHub → web), consolidate into ONE
        # runnable file, and build the grounding appendix so Mục 2 targets that exact file.
        exploit_target = _exploit_target(body, prompt_text)
        pre_evidence, grounding_appendix, _consolidated, acquisition = await _build_exploit_grounding(
            prefetch_result, web_hints, session_id, target=exploit_target,
            strategy_override=getattr(body, "poc_strategy", None))
        unavailable = _kb_unavailable_response(prefetch_result, pre_evidence)
        if unavailable is not None:
            text, injected = unavailable
            metadata = exploit_final_meta(None)
        else:
            grounding_context = ((_kt_prompt_appendix(kt_result) if kt_result else "")
                                 + grounding_appendix)
            text, metadata = await complete("exploit_research", body, session_id=session_id,
                                            prompt_override=prompt_text,
                                            grounding_context=grounding_context)
            text, injected = await inject_reference_code(
                text, metadata.get("run_id"), pre_evidence,
                cve_known=prefetch_result.get("cve_known", False), cve_id=prefetch_result.get("cve_id"),
                prompt_text=prompt_text, target=exploit_target)
        no_poc = "No verified public PoC was found in the supplied evidence."
        metadata.setdefault("evidence_ids", []); metadata.setdefault("edb_ids", [])
        metadata.setdefault("provenance", []); metadata.setdefault("candidate_status", "not_generated")
        metadata.update(injected)
        # H1 (2026-09-22): cwe_refs comes from the CVE-record KB lookup exploit_prefetch()
        # already did (cve_cwe_refs, retrieval/neo4j.py), not from PoC evidence — set
        # independently of `injected` (enforce_section1's report never carries this key).
        metadata["cwe_refs"] = prefetch_result.get("cwe_refs", [])
        metadata["kb_status"] = prefetch_result.get("kb_status", "not_queried")
        metadata["poc_strategy"] = _resolve_poc_strategy(getattr(body, "poc_strategy", None), state.settings)
        if kt_result is not None:
            metadata["poc_kt_exploit"] = kt_result
            _audit(kt_run_id, "poc_kt_exploit_http",
                   f"{kt_result.get('cve')}:{kt_result.get('target_ip')}",
                   {"user_id": body.user_id, "exploit_verdict": kt_result.get("exploit_verdict"),
                    "ok": kt_result.get("ok"), "error": kt_result.get("error")})
        # Mục 4 ("Giải thích quy trình tạo PoC") — 100% server-rendered từ `acquisition` +
        # evidence flags + `injected["section1_tier"]`, KHÔNG qua LLM (xem poc/trace.py).
        # Nối vào `text` TRƯỚC `neutralize_active_content` nhưng SAU toàn bộ pipeline
        # strip_tool_name_mentions/sanitize_airgap_runguide trong inject_reference_code đã
        # chạy xong — nên áp lại đúng 1 lưới quét an toàn (strip_tool_name_mentions) riêng
        # cho khối trace trước khi nối, để một bug trong build_generation_trace không thể lọt
        # tên tool nội bộ ra UI mà không bị chặn.
        # Ngoại lệ: refusal hook-exhaustion (`reason=kb_search_before_synthesis_exhausted`,
        # inject_reference_code's early-return ở trên) PHẢI đi qua byte-exact — bất biến này
        # có test riêng (test_exploit_kb_grounding.py) — không nối Mục 4 vào case đó.
        text = text or no_poc
        if injected.get("reason") != "kb_search_before_synthesis_exhausted":
            trace = build_generation_trace(acquisition, pre_evidence, injected)
            metadata["generation_trace"] = trace
            text = text + strip_tool_name_mentions(render_generation_trace(trace))
        await _pad_to_floor(_start, _floor)  # uniform total latency (see _pad_to_floor)
        safe = neutralize_active_content(text)  # defang XSS in the injected PoC too
        return {"description": safe, "exploit_guide": safe, "metadata": metadata}

    @app.post("/ai/exploit/stream")
    async def exploit_stream(body: LegacyRequest) -> StreamingResponse:
        with session_coverage_scope() as coverage_state:
            response = await _exploit_stream(body)
        iterator = response.body_iterator

        async def scoped_stream():
            with session_coverage_scope(coverage_state):
                async with aclosing(iterator):
                    async for frame in iterator:
                        yield frame

        response.body_iterator = scoped_stream()
        return response

    async def _exploit_stream(body: LegacyRequest) -> StreamingResponse:
        """Own generator (not the shared `stream()`) so it can emit a `section1` PREVIEW
        event as soon as the deterministic prefetch resolves — which runs concurrently
        with, and often finishes well before, the model's own turn. Without this, Section
        1 (often the largest part of the answer, sometimes >10k chars) only ever appears
        in one lump inside the final `final` event, which — even though the model's own
        `token` deltas WERE streaming smoothly the whole time — reads as "not streaming"
        to anyone watching the message reflow. The model still never writes Section 1
        (unchanged invariant); this only changes WHEN the already-fetched real evidence is
        pushed to the client, not what generates it. `final` remains authoritative — it
        re-merges prefetch + any evidence the model's own tool calls gathered, so a preview
        that was incomplete (or arrived too early to have anything yet) gets corrected.

        When POC_EXPLOIT_ENABLED and the request parses to target+CVE+intent, also runs
        deterministic ``poc_kt_exploit`` (5568), emits tool_call/tool_result SSE, and
        appends a compact JSON appendix to the model prompt so it summarizes real logs."""
        text = require_public_input(body.supplied_text())
        no_poc = "No verified public PoC was found in the supplied evidence."

        # Bare greeting / no CVE → short guide, no model turn, no forced "## 1." scaffolding.
        if _is_exploit_chitchat(text):
            async def gen_guide() -> AsyncIterator[bytes]:
                yield sse_frame("token", {"delta": EXPLOIT_CVE_GUIDE})
                yield sse_frame("final", {
                    "content": EXPLOIT_CVE_GUIDE, "exploit_guide": EXPLOIT_CVE_GUIDE,
                    "description": EXPLOIT_CVE_GUIDE, "metadata": exploit_guidance_meta()})
                yield b"data: [DONE]\n\n"
            return StreamingResponse(gen_guide(), media_type="text/event-stream")

        # Uniform total-latency floor — pad the FINAL event to this (see _pad_to_floor),
        # NOT a fixed sleep here: the old up-front sleep blocked the first byte for the whole
        # delay, so the early Section-1 preview (this endpoint's whole reason to exist) never
        # arrived early. Now the preview + tokens stream immediately and only the terminal
        # event is held back to keep total wall-clock uniform.
        _floor = float(getattr(state.settings, "poc_exploit_simulate_delay_s", 0) or 0)
        _start = time.monotonic()

        text = normalize_cve_ids(text)
        session_id = _exploit_session_id(body)

        # Validate CVE format before streaming (reject invalid suffixes like "zz")
        cve_match = _CVE_ANY.search(text)
        if cve_match:
            cve_id = cve_match.group(0).upper()
            # Check if there's a non-numeric suffix immediately after the CVE number
            end_pos = cve_match.end()
            if end_pos < len(text) and text[end_pos].isalpha():
                fmt_err = f"Định dạng CVE không hợp lệ: '{cve_id}{text[end_pos:].split()[0]}'. Cần CVE-YYYY-NNNNN (ví dụ CVE-2026-42533)."
                async def gen_error_fmt() -> AsyncIterator[bytes]:
                    yield sse_frame("token", {"delta": fmt_err})
                    yield sse_frame("final", {
                        "content": fmt_err, "exploit_guide": fmt_err,
                        "description": fmt_err, "metadata": {"error": fmt_err, "cve_id": cve_id}})
                    yield b"data: [DONE]\n\n"
                return StreamingResponse(gen_error_fmt(), media_type="text/event-stream")
            valid, fmt_err = _validate_cve_format(cve_id)
            if not valid:
                async def gen_error_fmt2() -> AsyncIterator[bytes]:
                    yield sse_frame("token", {"delta": fmt_err})
                    yield sse_frame("final", {
                        "content": fmt_err, "exploit_guide": fmt_err,
                        "description": fmt_err, "metadata": {"error": fmt_err, "cve_id": cve_id}})
                    yield b"data: [DONE]\n\n"
                return StreamingResponse(gen_error_fmt2(), media_type="text/event-stream")

        prefetch_task = asyncio.create_task(exploit_prefetch(text, session_id))
        kt_run_id = f"kt:{uuid4().hex}"
        kt_task: asyncio.Task | None = asyncio.create_task(
            _run_kt_if_applicable(state.settings, body, text, kt_run_id))

        # Populated by gen()'s prefetch step below, before build_final ever runs (build_final
        # is only invoked from inside gen(), after that step completes or fails) — read here
        # so enforce_section1's synthesis footer can name its web sources, same as non-stream.
        web_hints_holder: list[dict[str, Any]] = []
        # gen() stores the grounding result (consolidated pre_evidence) here so build_final and
        # the Section-1 preview use the SAME evidence the model was grounded on.
        grounding_holder: dict[str, Any] = {}

        async def build_final(final: str, run_id: str | None,
                              kt_result: dict[str, Any] | None = None) -> dict[str, Any]:
            if final == STREAM_ERROR_MESSAGE:
                safe = neutralize_active_content(STREAM_ERROR_MESSAGE)
                meta = exploit_final_meta(run_id)
                meta.update({"candidate_status": "not_generated", "section1_tier": "error"})
                if kt_result is not None:
                    meta["poc_kt_exploit"] = kt_result
                return {"content": safe, "exploit_guide": safe, "description": safe, "metadata": meta}

            prefetch_result = await prefetch_task
            pre_evidence = grounding_holder.get("pre_evidence")
            if pre_evidence is None:  # prelude failed before grounding — fall back to raw merge
                pre_evidence = merge_evidence(prefetch_result.get("evidence") or [], web_hints_holder)
            unavailable = _kb_unavailable_response(prefetch_result, pre_evidence)
            if unavailable is not None:
                final, injected = unavailable
            else:
                final, injected = await inject_reference_code(
                    final, run_id, pre_evidence,
                    cve_known=prefetch_result.get("cve_known", False), cve_id=prefetch_result.get("cve_id"),
                    prompt_text=text, target=_exploit_target(body, text))
            meta = exploit_final_meta(run_id)
            meta.update(injected)
            # H1 (2026-09-22): see the non-stream exploit() endpoint's identical comment —
            # cwe_refs comes from the prefetch's CVE-record lookup, not from `injected`.
            meta["cwe_refs"] = prefetch_result.get("cwe_refs", [])
            meta["kb_status"] = prefetch_result.get("kb_status", "not_queried")
            if kt_result is not None:
                meta["poc_kt_exploit"] = kt_result
            # Mục 4 ("Giải thích quy trình tạo PoC") — cùng cơ chế tất định với non-stream
            # `_exploit` (xem comment ở đó). `grounding_holder["acquisition"]` có thể thiếu
            # nếu prelude grounding lỗi (try/except ở generate()) — fail-open về {} thì
            # build_generation_trace vẫn trả câu generic hợp lệ, không bao giờ raise.
            # Ngoại lệ byte-exact: xem comment tương ứng trong `_exploit` (non-stream).
            final = final or no_poc
            if injected.get("reason") != "kb_search_before_synthesis_exhausted":
                acquisition = grounding_holder.get("acquisition") or {}
                trace = build_generation_trace(acquisition, pre_evidence, injected)
                meta["generation_trace"] = trace
                final = final + strip_tool_name_mentions(render_generation_trace(trace))
            safe = neutralize_active_content(final)  # defang XSS in the injected PoC too
            return {"content": safe, "exploit_guide": safe, "description": safe, "metadata": meta}

        async def generate() -> AsyncIterator[bytes]:
            final = ""
            run_id = None
            failed = False
            section1_sent = False
            kt_result: dict[str, Any] | None = None
            kt_emitted = False
            # Await prefetch BEFORE the model so its fetched PoC grounds Mục 2/3 (parity with
            # the non-stream path — otherwise a successful prefetch stands the search hooks
            # down and the model non-deterministically writes "no info" under a populated
            # Section 1). The task result is cached, so the loop below still emits the Section-1
            # preview from `pending_prefetch` as before (it's simply already done by then).
            prefetch_appendix = ""
            try:
                _pf = await prefetch_task
                _hints = await _maybe_web_hints(_pf)
                web_hints_holder.extend(_hints)  # visible to build_final (see declaration above)
                _pre, prefetch_appendix, _cons, _acq = await _build_exploit_grounding(
                    _pf, _hints, session_id, target=_exploit_target(body, text),
                    strategy_override=getattr(body, "poc_strategy", None))
                grounding_holder["pre_evidence"] = _pre
                grounding_holder["consolidated"] = _cons
                grounding_holder["acquisition"] = _acq
            except Exception:
                logger.exception("prefetch appendix build failed")
            # Wait up to 2s for KT so we can append appendix before the model starts when
            # intent is clear; if slower, start model and emit tool SSE when KT finishes.
            grounding_context = prefetch_appendix
            pending_kt: asyncio.Task | None = kt_task
            if pending_kt is not None:
                done, _ = await asyncio.wait({pending_kt}, timeout=2.0)
                if pending_kt in done:
                    try:
                        kt_result = pending_kt.result()
                    except Exception:
                        logger.exception("kt exploit early wait failed")
                        kt_result = None
                    pending_kt = None
                    if kt_result is not None:
                        grounding_context = _kt_prompt_appendix(kt_result) + prefetch_appendix

            _pf = await prefetch_task
            unavailable = _kb_unavailable_response(
                _pf, grounding_holder.get("pre_evidence", _pf.get("evidence") or []))

            if unavailable is not None:
                if pending_kt is not None:
                    try:
                        kt_result = await pending_kt
                    except Exception:
                        logger.exception("kt exploit task failed")
                await _pad_to_floor(_start, _floor)
                payload = await build_final(unavailable[0], None, kt_result)
                yield sse_frame("token", {"delta": payload["content"]})
                yield sse_frame("final", payload)
                yield b"data: [DONE]\n\n"
                return

            model_agen = state.runner.stream(
                "exploit_research", text, session_id,
                **({"grounding_context": grounding_context} if grounding_context else {}),
            ).__aiter__()
            model_next = asyncio.ensure_future(model_agen.__anext__())
            pending_prefetch: asyncio.Task | None = prefetch_task
            try:
                if kt_result is not None and not kt_emitted:
                    kt_emitted = True
                    tid = kt_run_id
                    yield sse_frame("tool_call", {
                        "id": tid, "name": "poc_kt_exploit",
                        "input": {"ip": kt_result.get("target_ip"), "cve": kt_result.get("cve"),
                                  "port": kt_result.get("target_port")},
                        "summary": "poc_kt_exploit(deterministic)",
                    })
                    payload = json.dumps(kt_result, ensure_ascii=False)
                    yield sse_frame("tool_result", {
                        "id": tid, "name": "poc_kt_exploit",
                        "is_error": not kt_result.get("ok", True),
                        "result": payload[:8000],
                        "summary": f"poc_kt_exploit {kt_result.get('exploit_verdict')}",
                    })
                    _audit(kt_run_id, "poc_kt_exploit_http",
                           f"{kt_result.get('cve')}:{kt_result.get('target_ip')}",
                           {"user_id": body.user_id,
                            "exploit_verdict": kt_result.get("exploit_verdict"),
                            "ok": kt_result.get("ok")})

                while True:
                    waiting = {model_next}
                    if pending_prefetch is not None:
                        waiting.add(pending_prefetch)
                    if pending_kt is not None:
                        waiting.add(pending_kt)
                    done, _ = await asyncio.wait(waiting, return_when=asyncio.FIRST_COMPLETED)

                    if pending_kt is not None and pending_kt in done:
                        try:
                            kt_result = pending_kt.result()
                        except Exception:
                            logger.exception("kt exploit task failed")
                            kt_result = None
                        pending_kt = None
                        if kt_result is not None and not kt_emitted:
                            kt_emitted = True
                            tid = kt_run_id
                            yield sse_frame("tool_call", {
                                "id": tid, "name": "poc_kt_exploit",
                                "input": {"ip": kt_result.get("target_ip"),
                                          "cve": kt_result.get("cve"),
                                          "port": kt_result.get("target_port")},
                                "summary": "poc_kt_exploit(deterministic)",
                            })
                            payload = json.dumps(kt_result, ensure_ascii=False)
                            yield sse_frame("tool_result", {
                                "id": tid, "name": "poc_kt_exploit",
                                "is_error": not kt_result.get("ok", True),
                                "result": payload[:8000],
                                "summary": f"poc_kt_exploit {kt_result.get('exploit_verdict')}",
                            })
                            _audit(kt_run_id, "poc_kt_exploit_http",
                                   f"{kt_result.get('cve')}:{kt_result.get('target_ip')}",
                                   {"user_id": body.user_id,
                                    "exploit_verdict": kt_result.get("exploit_verdict"),
                                    "ok": kt_result.get("ok")})

                    if pending_prefetch is not None and pending_prefetch in done:
                        _pfres = pending_prefetch.result()
                        pending_prefetch = None
                        if not section1_sent:
                            # Preview the SAME evidence the model was grounded on (consolidated
                            # single file when available) — set by the prelude's grounding step,
                            # which has already run by now. Fall back to raw prefetch evidence.
                            preview_evidence = grounding_holder.get("pre_evidence")
                            if preview_evidence is None:
                                preview_evidence = _pfres.get("evidence") or []
                            fullened = await _fullen_evidence_for_display(
                                list(preview_evidence), getattr(state.runner, "poc", None))
                            preview_body = canonical_block(fullened, _pfres.get("cve_id"))
                            if preview_body:
                                section1_sent = True
                                yield sse_frame("section1",
                                                {"body": neutralize_active_content(preview_body),
                                                 "preview": True})

                    if model_next in done:
                        try:
                            event = model_next.result()
                        except StopAsyncIteration:
                            break
                        model_next = asyncio.ensure_future(model_agen.__anext__())
                        if isinstance(event, SystemEvent):
                            run_id = getattr(event, "run_id", None) or run_id
                        if isinstance(event, ResultEvent):
                            final = sanitize_output_language(event.final_text or "")
                        if not isinstance(event, PartialAssistantEvent):
                            frame = event_to_sse(event)
                            if frame:
                                yield frame
            except Exception:
                failed = True
                logger.exception("stream failed for profile=exploit_research")
                yield sse_frame("error", {"error": STREAM_ERROR_MESSAGE})
                yield sse_frame("token", {"delta": STREAM_ERROR_MESSAGE})
                if pending_kt is not None:
                    try:
                        kt_result = await pending_kt
                    except Exception:
                        pass
                yield sse_frame("final", await build_final(STREAM_ERROR_MESSAGE, run_id, kt_result))
            finally:
                model_next.cancel()
                await asyncio.gather(model_next, return_exceptions=True)
                close_model = getattr(model_agen, "aclose", None)
                if close_model is not None:
                    await close_model()
            if not failed:
                # Ensure KT finished for metadata even if SSE already emitted
                if pending_kt is not None:
                    try:
                        kt_result = await pending_kt
                    except Exception:
                        pass
                # Hold the authoritative final event back to the uniform latency floor —
                # tokens + Section-1 preview already streamed live above, so this only
                # normalizes total wall-clock, it does not delay progressive delivery.
                await _pad_to_floor(_start, _floor)
                payload = await build_final(final, run_id, kt_result)
                yield sse_frame("token", {"delta": payload["content"]})
                yield sse_frame("final", payload)
            yield b"data: [DONE]\n\n"

        async def gen() -> AsyncIterator[bytes]:
            try:
                async with aclosing(generate()) as frames:
                    async for frame in frames:
                        yield frame
            finally:
                for task in (prefetch_task, kt_task):
                    if task is not None and not task.done():
                        task.cancel()
                await asyncio.gather(*(task for task in (prefetch_task, kt_task) if task is not None),
                                     return_exceptions=True)

        return StreamingResponse(gen(), media_type="text/event-stream")

    def _audit(run_id: str, tool_name: str, key_material: str, meta: dict[str, Any]) -> None:
        """Ghi 1 dòng audit cho lệnh gọi HTTP trực tiếp vào Kali (verify/tool/exec) — song song
        với ledger.audit_once mà agent-tool path (poc/tools.py) đã dùng, để MỌI cách gọi (LLM lẫn
        API trực tiếp) đều để lại vết ai/khi nào/gọi gì/kết quả gì. Cần khi nhiều người dùng gọi
        đồng thời: đây là nguồn duy nhất phân xử "ai đã ra lệnh cho container nào". Fail-open —
        audit hỏng không được làm rớt request thật."""
        ledger = getattr(state.runner, "ledger", None)
        if ledger is None:
            return
        try:
            ledger.audit_once(f"{run_id}:audit", run_id, tool_name,
                              hashlib.sha256(key_material.encode()).hexdigest(), meta)
        except Exception:
            logger.exception("audit log failed run_id=%s tool=%s", run_id, tool_name)

    @app.post("/ai/verify")
    async def verify(body: VerifyRequest) -> dict[str, Any]:
        """Mode A — kiểm chứng PoC vào target qua secai-kali-executor (verify-only).
        Tắt mặc định: trả 404 khi VERIFY_ENABLED chưa bật (không lộ endpoint). Deterministic,
        không qua LLM. Xem poc/verify.py + docs/poc-verify-architecture.md."""
        settings = state.settings
        if not settings.verify_enabled:
            raise HTTPException(404, "verify disabled (đặt VERIFY_ENABLED=true để bật)")
        poc = getattr(state.runner, "poc", None)
        if poc is None:
            raise HTTPException(503, "PoC backend unavailable")
        run_id = f"verify:{uuid4().hex}"
        try:
            result = await verify_poc(poc, settings, target=body.target, run_id=run_id,
                                      cve=body.cve, ref=body.ref, source=body.source, rport=body.rport)
        except PocForbidden as exc:
            raise HTTPException(403, str(exc)) from exc
        except PocBadRequest as exc:
            raise HTTPException(400, str(exc)) from exc
        except PocNotFound as exc:
            raise HTTPException(404, str(exc)) from exc
        except PocError as exc:
            raise HTTPException(422, str(exc)) from exc
        except Exception as exc:  # docker/runtime lỗi → 503, không phải 500 mờ
            logger.exception("verify failed target=%s", body.target)
            raise HTTPException(503, f"verify runtime error: {exc}") from exc
        _audit(run_id, "poc_verify_http", f"{body.cve or body.ref}:{body.target}",
              {"user_id": body.user_id, "target": body.target, "cve": body.cve, "ref": body.ref,
               "verdict": result.get("verdict"), "source": result.get("source")})
        return {**result, "run_id": run_id}

    @app.post("/ai/bughunt/stream")
    async def bughunt_stream(body: BughuntRequest) -> StreamingResponse:
        """BugHunter-style agent hunt via Linch ``run_workflow`` (AgentHunt → Validate).

        LLM ReAct tool loop inside AgentHunt (``run_recon``, ``run_vuln_scan``, …);
        Validate is a deterministic lead gate. User supplies ``target`` (not CVE).
        """
        target = (body.target or "").strip()
        if not target:
            raise HTTPException(422, "target is required")
        # Soft authz preview: when verify allowlist is active, reject early (same policy
        # scanners will use in Phase 1+). VERIFY_ALLOW_ALL / empty allowlist → pass.
        from ..poc.verify import target_allowed
        settings = state.settings
        if not target_allowed(settings, target) and not getattr(settings, "verify_allow_all", False):
            # If allowlist empty and allow_all false, target_allowed is False — for Phase 0
            # skeleton we only enforce when an explicit allowlist is configured.
            allow = getattr(settings, "verify_targets", None) or []
            if allow:
                raise HTTPException(403, f"Target '{target}' ngoài allowlist (VERIFY_ALLOWED_TARGETS)")

        scope_note = (body.scope_note or "").strip()
        run_id = (body.run_id or "").strip() or f"bh:{uuid4().hex}"

        async def gen() -> AsyncIterator[bytes]:
            result: dict[str, Any] | None = None
            failed = False
            try:
                stream_fn = getattr(state.runner, "stream_bughunt", None)
                if stream_fn is None:
                    raise RuntimeError("runner does not support stream_bughunt")
                async for event in stream_fn(
                    target=target,
                    scope_note=scope_note,
                    run_id=run_id,
                    enable_exploit=bool(body.enable_exploit),
                    run_cve_sweep=bool(getattr(body, "run_cve_sweep", True)),
                ):
                    if isinstance(event, dict) and event.get("type") == "bughunt_result":
                        result = event.get("result") if isinstance(event.get("result"), dict) else event
                        continue
                    if isinstance(event, dict) and event.get("type") == "log":
                        yield sse_frame("log", {
                            "stream": event.get("stream") or "stdout",
                            "line": event.get("line") or "",
                            "run_id": event.get("run_id") or run_id,
                        })
                        # Flush each scanner line so the FE console streams live.
                        await asyncio.sleep(0)
                        continue
                    frame = event_to_sse(event)
                    if frame:
                        yield frame
                        # Let the event loop flush to the socket between heavy steps.
                        await asyncio.sleep(0)
            except Exception:
                failed = True
                logger.exception("bughunt stream failed target=%s", target)
                yield sse_frame("error", {"error": STREAM_ERROR_MESSAGE})
                yield sse_frame("final", {
                    "ok": False,
                    "error": STREAM_ERROR_MESSAGE,
                    "metadata": {"run_id": run_id, "profile": "bughunt"},
                })
            if not failed:
                payload = result or {"status": "empty", "target": target, "phases": []}
                msg = payload.get("message") if isinstance(payload, dict) else None
                yield sse_frame("final", {
                    "ok": True,
                    "content": msg or json.dumps(payload, ensure_ascii=False),
                    "result": payload,
                    "metadata": {
                        "run_id": run_id,
                        "profile": "bughunt",
                        "phases": (payload.get("phases") if isinstance(payload, dict) else None),
                    },
                })
            yield b"data: [DONE]\n\n"

        return StreamingResponse(
            gen(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    @app.post("/ai/exploit/run")
    async def exploit_run(body: ExploitRunRequest) -> dict[str, Any]:
        """Deterministic PoC run qua phân hệ 5568 — không LLM. Tắt → 404.
        Trả scan.output (log pocsuite) + exploit_verdict (parse từ bảng, không tin scan.success)."""
        settings = state.settings
        if not settings.poc_exploit_enabled or not (settings.poc_exploit_api_url or "").strip():
            raise HTTPException(404, "poc exploit disabled (POC_EXPLOIT_ENABLED + POC_EXPLOIT_API_URL)")
        run_id = f"kt:{uuid4().hex}"
        try:
            result = await run_kt_exploit(
                settings, ip=body.target, cve=body.cve, port=body.port,
                command=body.command or "id", use_proxy=body.use_proxy, run_id=run_id,
            )
        except PocForbidden as exc:
            raise HTTPException(403, str(exc)) from exc
        except PocBadRequest as exc:
            raise HTTPException(400, str(exc)) from exc
        except PocNotFound as exc:
            raise HTTPException(404, str(exc)) from exc
        except (PocError, KtClientError) as exc:
            raise HTTPException(503, str(exc)) from exc
        except Exception as exc:
            logger.exception("exploit/run failed target=%s cve=%s", body.target, body.cve)
            raise HTTPException(503, f"exploit runtime error: {exc}") from exc
        _audit(run_id, "poc_kt_exploit_http", f"{body.cve}:{body.target}",
              {"user_id": body.user_id, "target": body.target, "cve": body.cve,
               "port": body.port, "exploit_verdict": result.get("exploit_verdict"),
               "ok": result.get("ok")})
        return result

    def _advance_authz(target: str) -> str:
        """Fail-closed authz cho exploit-advance (tấn công target thật): engine bật + target trong
        allowlist. Trả host đã parse."""
        from ..poc.advance import parse_target
        from ..poc.verify import target_allowed
        settings = state.settings
        pocsuite_ok = settings.poc_exploit_enabled and (settings.poc_exploit_api_url or "").strip()
        lane1_ok = getattr(settings, "lane1_enabled", False)
        if not pocsuite_ok and not lane1_ok:
            raise HTTPException(404, "exploit-advance disabled (POC_EXPLOIT_ENABLED+POC_EXPLOIT_API_URL hoặc LANE1_ENABLED)")
        host, _ = parse_target(target)
        if not host:
            raise HTTPException(422, "target không hợp lệ")
        if not target_allowed(settings, target) and not getattr(settings, "verify_allow_all", False):
            raise HTTPException(403, f"Target '{target}' ngoài allowlist (VERIFY_ALLOWED_TARGETS/VERIFY_ALLOW_ALL)")
        return host

    @app.get("/ai/exploit-advance/fields")
    async def exploit_advance_fields() -> dict[str, Any]:
        """Spec field cho FE dựng form: name/type/required/default/description (đọc từ schema)."""
        schema = ExploitAdvanceRequest.model_json_schema()
        props = schema.get("properties", {})
        required = set(schema.get("required", []))

        def _ftype(p: dict[str, Any]) -> str:
            if p.get("type"):
                return p["type"]
            for t in p.get("anyOf", []):
                if t.get("type") and t["type"] != "null":
                    return t["type"]
            return "string"

        fields = [{"name": n, "type": _ftype(p), "required": n in required,
                   "default": p.get("default"), "description": p.get("description", "")}
                  for n, p in props.items()]
        return {"endpoint": "/ai/exploit-advance/stream", "fields": fields}

    @app.post("/ai/exploit-advance/stream")
    async def exploit_advance_stream(body: ExploitAdvanceRequest) -> StreamingResponse:
        """Khai thác ĐA-BƯỚC một CVE vào TARGET thật; stream tiến trình (event `step`/`log`/`final`)
        cho FE — user thấy đang ở bước nào + kết quả. Gated: POC_EXPLOIT_ENABLED + allowlist."""
        _advance_authz(body.target)
        from ..poc.advance import run_exploit_advance
        run_id = (body.run_id or "").strip() or f"adv:{uuid4().hex}"
        _audit(run_id, "exploit_advance_http", f"{body.cve}:{body.target}",
               {"user_id": body.user_id, "target": body.target, "cve": body.cve,
                "scope_note": body.scope_note})

        async def gen() -> AsyncIterator[bytes]:
            final_seen = False
            try:
                async for ev in run_exploit_advance(state.settings, state.runner, body, run_id=run_id):
                    et = ev.get("type")
                    if et == "final":
                        final_seen = True
                        yield sse_frame("final", {k: ev.get(k) for k in
                                                  ("ok", "verdict", "content", "result", "metadata")})
                    elif et == "step":
                        yield sse_frame("step", {k: ev.get(k) for k in
                                                 ("index", "name", "status", "detail", "verdict")})
                    elif et == "log":
                        yield sse_frame("log", {"stream": ev.get("stream", "stdout"),
                                                "line": ev.get("line", ""), "run_id": run_id})
                    elif et == "question":
                        # Human-in-the-loop: lane1 cần thêm thông tin từ user.
                        # FE nhận event này → hiện input → gọi /ai/exploit-advance/reply khi user trả lời.
                        final_seen = True   # Không phát error-final sau question
                        yield sse_frame("question", {k: ev.get(k)
                                                     for k in ("run_id", "question", "suggestions")})
                    await asyncio.sleep(0)  # flush từng event cho FE console
            except Exception:
                logger.exception("exploit-advance stream failed target=%s cve=%s", body.target, body.cve)
                yield sse_frame("error", {"error": STREAM_ERROR_MESSAGE})
                if not final_seen:
                    yield sse_frame("final", {"ok": False, "verdict": "error",
                                              "content": STREAM_ERROR_MESSAGE, "result": {},
                                              "metadata": {"run_id": run_id, "profile": "exploit_advance"}})
            yield b"data: [DONE]\n\n"

        return StreamingResponse(gen(), media_type="text/event-stream",
            headers={"Cache-Control": "no-cache, no-transform", "Connection": "keep-alive",
                     "X-Accel-Buffering": "no"})

    @app.post("/ai/exploit-advance/reply")
    async def exploit_advance_reply(body: ExploitAdvanceReplyRequest) -> StreamingResponse:
        """Tiếp tục lane1 sau khi user cung cấp thông tin bổ sung (human-in-the-loop).
        Nhận `run_id` từ event `question` + `answer` của user → resume stream."""
        from ..poc.lane1_state import load as _load_lane1, clear as _clear_lane1
        from ..poc.advance import run_exploit_advance_resumed

        paused = _load_lane1(body.run_id)
        if paused is None:
            raise HTTPException(404, f"Session '{body.run_id}' không tìm thấy hoặc đã hết hạn (30 phút). "
                                     "Hãy bắt đầu lại từ /ai/exploit-advance/stream.")
        _clear_lane1(body.run_id)

        _advance_authz(paused.host)
        run_id = body.run_id
        _audit(run_id, "exploit_advance_reply", f"{paused.cve}:{paused.host}",
               {"user_id": body.user_id, "answer_len": len(body.answer)})

        async def gen() -> AsyncIterator[bytes]:
            final_seen = False
            try:
                async for ev in run_exploit_advance_resumed(
                        state.settings, state.runner, paused, body.answer, run_id=run_id):
                    et = ev.get("type")
                    if et == "final":
                        final_seen = True
                        yield sse_frame("final", {k: ev.get(k) for k in
                                                  ("ok", "verdict", "content", "result", "metadata")})
                    elif et == "step":
                        yield sse_frame("step", {k: ev.get(k) for k in
                                                 ("index", "name", "status", "detail", "verdict")})
                    elif et == "log":
                        yield sse_frame("log", {"stream": ev.get("stream", "stdout"),
                                                "line": ev.get("line", ""), "run_id": run_id})
                    elif et == "question":
                        final_seen = True
                        yield sse_frame("question", {k: ev.get(k)
                                                     for k in ("run_id", "question", "suggestions")})
                    await asyncio.sleep(0)
            except Exception:
                logger.exception("exploit-advance reply stream failed run_id=%s", run_id)
                yield sse_frame("error", {"error": STREAM_ERROR_MESSAGE})
                if not final_seen:
                    yield sse_frame("final", {"ok": False, "verdict": "error",
                                              "content": STREAM_ERROR_MESSAGE, "result": {},
                                              "metadata": {"run_id": run_id, "profile": "exploit_advance"}})
            yield b"data: [DONE]\n\n"

        return StreamingResponse(gen(), media_type="text/event-stream",
            headers={"Cache-Control": "no-cache, no-transform", "Connection": "keep-alive",
                     "X-Accel-Buffering": "no"})

    @app.post("/ai/exploit-advance")
    async def exploit_advance(body: ExploitAdvanceRequest) -> dict[str, Any]:
        """Bản không-SSE của exploit-advance: chạy pipeline, gom các bước + kết quả cuối trả JSON."""
        _advance_authz(body.target)
        from ..poc.advance import run_exploit_advance
        run_id = (body.run_id or "").strip() or f"adv:{uuid4().hex}"
        _audit(run_id, "exploit_advance_http", f"{body.cve}:{body.target}",
               {"user_id": body.user_id, "target": body.target, "cve": body.cve,
                "scope_note": body.scope_note})
        steps: list[dict[str, Any]] = []
        final_ev: dict[str, Any] | None = None
        try:
            async for ev in run_exploit_advance(state.settings, state.runner, body, run_id=run_id):
                if ev.get("type") == "step":
                    steps.append({k: ev.get(k) for k in ("index", "name", "status", "detail", "verdict")})
                elif ev.get("type") == "final":
                    final_ev = ev
        except Exception as exc:
            logger.exception("exploit-advance failed target=%s cve=%s", body.target, body.cve)
            raise HTTPException(503, f"exploit-advance runtime error: {exc}") from exc
        out = final_ev or {"ok": False, "verdict": "error", "result": {}, "metadata": {"run_id": run_id}}
        return {**{k: out.get(k) for k in ("ok", "verdict", "content", "result", "metadata")}, "steps": steps}

    @app.post("/ai/tool")
    async def tool(body: ToolRequest) -> dict[str, Any]:
        """Scoped pentest tool (nmap/searchsploit/gobuster/sqlmap/hydra) chạy trong Kali executor.
        Tắt mặc định (404 khi VERIFY_ENABLED chưa bật). argv do gateway dựng từ tham số đã validate."""
        settings = state.settings
        if not settings.verify_enabled:
            raise HTTPException(404, "tool disabled (đặt VERIFY_ENABLED=true để bật)")
        run_id = f"tool:{uuid4().hex}"
        params = body.model_dump(exclude_none=True, exclude={"user_id"})
        try:
            result = await run_tool(settings, body.tool, params, run_id=run_id)
        except PocForbidden as exc:
            raise HTTPException(403, str(exc)) from exc
        except PocBadRequest as exc:
            raise HTTPException(400, str(exc)) from exc
        except PocError as exc:
            raise HTTPException(422, str(exc)) from exc
        except Exception as exc:
            logger.exception("tool failed tool=%s", body.tool)
            raise HTTPException(503, f"tool runtime error: {exc}") from exc
        _audit(run_id, "poc_scan_http", f"{body.tool}:{body.target or body.query}",
              {"user_id": body.user_id, "tool": body.tool, "target": body.target,
               "rc": result.get("rc")})
        return {**result, "run_id": run_id}

    @app.post("/ai/exec")
    async def exec_raw(body: ExecRequest,
                       x_api_key: Annotated[str | None, Header()] = None) -> dict[str, Any]:
        """RAW exec (RCE có chủ đích): chạy lệnh BẤT KỲ trong Kali với --privileged + root.
        Tắt mặc định (404). LUÔN yêu cầu admin API key để không thành RCE mở toang cho mọi caller."""
        settings = state.settings
        if not settings.exec_enabled:
            raise HTTPException(404, "exec disabled (đặt EXEC_ENABLED=true để bật)")
        if not settings.admin_api_key or x_api_key != settings.admin_api_key:
            raise HTTPException(401, "exec cần header X-API-Key == ADMIN_API_KEY")
        run_id = f"exec:{uuid4().hex}"
        try:
            result = await run_raw_command(settings, argv=body.cmd, shell=body.shell,
                                           timeout_s=body.timeout_s, privileged=body.privileged, run_id=run_id)
        except PocForbidden as exc:
            raise HTTPException(403, str(exc)) from exc
        except PocBadRequest as exc:
            raise HTTPException(400, str(exc)) from exc
        except Exception as exc:
            logger.exception("exec failed")
            raise HTTPException(503, f"exec runtime error: {exc}") from exc
        # Luôn audit đầy đủ (kể cả không có ledger.evidence-style dedup) — /ai/exec là bề mặt
        # nguy hiểm nhất, audit là cơ chế trách nhiệm giải trình duy nhất khi có sự cố.
        _audit(run_id, "poc_exec_http", body.shell or json.dumps(body.cmd or []),
              {"user_id": body.user_id, "cmd": body.cmd, "shell": body.shell, "rc": result.get("rc")})
        return {**result, "run_id": run_id}

    @app.get("/health")
    async def health() -> dict[str, Any]:
        s = state.settings
        provider_kind = s.llm_provider.lower()
        if provider_kind == "deepseek":
            llm_base = s.deepseek_base_url.rstrip("/")
            llm_key = s.deepseek_api_key
        else:
            llm_base = s.qwen_base_url.rstrip("/")
            llm_key = s.qwen_api_key
        llm_headers = {"Authorization": f"Bearer {llm_key}"} if llm_key else {}
        qwen_ok = False
        try:
            async with httpx.AsyncClient(timeout=2.5) as client:
                response = await client.get(llm_base + "/models", headers=llm_headers)
                qwen_ok = response.is_success
        except httpx.HTTPError:
            pass
        # PoC backend health — in-process searchsploit/Exploit-DB.
        searchsploit_ok = False
        commit = None
        poc = getattr(state.runner, "poc", None)
        if poc is not None and hasattr(poc, "health"):
            try:
                searchsploit_ok, commit = await poc.health()
            except Exception:
                pass
        store = getattr(state.runner, "session_store", None)
        if store is not None and hasattr(store, "ping"):
            redis_status = "connected" if await store.ping() else "unreachable"
        else:
            redis_status = "ok"
        session_backend = state.settings.session_store_backend
        # Ư4: surface the input-classifier fail-open rate here too, not just as a WARNING log
        # line — this is exactly the CLAUDE.md-documented failure mode (guard silently disabled,
        # /health said ok) that fail-open observability was built to catch. A high unknown_rate
        # over a real sample means the guard is bypassed on most requests right now.
        sanitize_stats = verdict_stats()
        status = _health_status(qwen_ok, sanitize_stats)
        payload: dict[str, Any] = {"status": status, "message": "secai gateway", "backend": provider_kind, "redis": redis_status, "session_backend": session_backend, "postgres": "disabled", "llm": qwen_ok, "qwen": qwen_ok, "searchsploit": searchsploit_ok, "exploitdb_commit": commit, "input_sanitize": sanitize_stats}
        if s.health_deep_checks:
            # Real embedding/rerank probes + Qdrant/Neo4j + nightly-KB-sync age (see health_deps).
            # Lets an operator confirm after a restart or .env change that the stack is actually
            # usable, not just that the process is up. Still HTTP 200 (Docker healthcheck only
            # needs liveness); readiness is in `ready`/`problems`.
            from . import health_deps

            extra = await health_deps.cached_collect(s)
            problems = health_deps.problems_from(extra)
            if redis_status == "unreachable":
                problems.append("redis: unreachable")
            if not qwen_ok:
                problems.insert(0, f"llm: {llm_base} không phản hồi /models")
            payload.update(extra)
            payload.update(health_deps.uptime())
            payload["problems"] = problems
            payload["ready"] = not problems
            if problems:
                payload["status"] = "degraded"
        return payload

    @app.post("/admin/refresh-models")
    async def refresh_models(x_api_key: Annotated[str | None, Header()] = None) -> dict[str, Any]:
        expected = state.settings.admin_api_key
        if not expected or x_api_key != expected:
            raise HTTPException(401, "Invalid admin API key")
        return {"status": "ok", "refreshed": True}

    _register_showcase_routes(app, state)


def _register_showcase_routes(app: FastAPI, state: Any) -> None:
    """Nhóm F/D/K/L/E (docs/nghiem-thu-showcase/) — read-only evidence APIs for the
    nghiệm-thu demo, Stage 1 (KB + S — hồ sơ gốc browser — deliberately not included yet).
    None of these need ``state`` (no LLM/KB calls, no closures) — kept as a separate
    function purely so this block stays easy to lift out later if the showcase moves repos.
    """
    from .showcase import finetune as showcase_finetune
    from .showcase import kpi_bench as showcase_kpi_bench
    from .showcase import kpi_studio as showcase_kpi_studio
    from .showcase import kpi_run as showcase_kpi_run
    from .showcase import model_info as showcase_model_info
    from .showcase import sanitize_demo as showcase_sanitize_demo
    from .showcase import source_dossier as showcase_source_dossier
    from .showcase import kb as showcase_kb
    from .showcase import deploy as showcase_deploy
    from .showcase import tb_proxy as showcase_tb_proxy
    from .showcase import testset as showcase_testset
    from .showcase import retraining_proxy as showcase_retraining_proxy

    app.include_router(showcase_finetune.router)
    app.include_router(showcase_model_info.router)
    app.include_router(showcase_kpi_bench.router)
    app.include_router(showcase_kpi_studio.router)
    app.include_router(showcase_kpi_run.router)
    app.include_router(showcase_sanitize_demo.router)
    app.include_router(showcase_source_dossier.router)
    app.include_router(showcase_kb.router)
    app.include_router(showcase_deploy.router)
    app.include_router(showcase_tb_proxy.router)  # /tb/* → TensorBoard (đi ké qua :8131)
    app.include_router(showcase_testset.router)
    app.include_router(showcase_retraining_proxy.router)  # /showcase-api/retraining/* → trigger_api host :8877

    try:
        from .showcase import evidence as showcase_evidence
    except ImportError:
        logger.warning("showcase evidence router (nhóm E) chưa sẵn sàng — bỏ qua /ai/evidence/*")
    else:
        app.include_router(showcase_evidence.router)

    # StaticFiles gửi Cache-Control: no-cache → trình duyệt LUÔN revalidate qua ETag (304 nếu
    # không đổi, tải mới ngay khi file đổi). Tránh kẹt module JS/CSS cũ mỗi lần cập nhật showcase.
    class _NoCacheStatic(StaticFiles):
        def file_response(self, *args: Any, **kwargs: Any):
            resp = super().file_response(*args, **kwargs)
            resp.headers["Cache-Control"] = "no-cache, must-revalidate"
            return resp

    finetune_figures_dir = Path(state.settings.showcase_finetune_dossier_dir) / "dashboards" / "figures"
    if finetune_figures_dir.is_dir():
        app.mount("/showcase-api/finetune/assets", _NoCacheStatic(directory=str(finetune_figures_dir)),
                  name="showcase-finetune-assets")

    # Serve the throwaway demo page same-origin (no CORS needed) — repo_root/demo/showcase/.
    demo_dir = Path(__file__).resolve().parents[3] / "demo" / "showcase"
    if demo_dir.is_dir():
        app.mount("/showcase-demo", _NoCacheStatic(directory=str(demo_dir), html=True), name="showcase-demo")

    # KPI Studio — standalone app (not a showcase-demo tab), same-origin, own sidebar shell.
    # Calls the same real /showcase-api/kpi-studio/* + /showcase-api/kpi-bench/* endpoints above.
    kpi_studio_dir = Path(__file__).resolve().parents[3] / "demo" / "kpi-studio"
    if kpi_studio_dir.is_dir():
        app.mount("/kpi-studio", _NoCacheStatic(directory=str(kpi_studio_dir), html=True), name="kpi-studio-demo")
