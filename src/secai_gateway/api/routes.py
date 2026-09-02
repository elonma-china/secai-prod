from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
import unicodedata
from collections.abc import AsyncIterator
from dataclasses import replace
from pathlib import Path
from typing import Annotated, Any
from uuid import uuid4

import httpx
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import StreamingResponse

from linch.events import ResultEvent, SystemEvent

from ..agent import registry_for
from ..poc import (
    canonical_block,
    cli_surface,
    consolidate_poc,
    enforce_section1,
    extract_cve_id,
    has_real_code_evidence,
    merge_evidence,
    normalize_cve_ids,
    override_section3,
    prefetch_evidence,
    sanitize_airgap_runguide,
    slice_entry,
    strip_system_narration,
    strip_tool_name_mentions,
)
from ..poc.backend import PocBadRequest, PocError, PocForbidden, PocNotFound
from ..poc.fixed_pocs import extract_target
from ..poc.kt_client import KtClientError
from ..poc.kt_exploit import parse_exploit_intent, run_kt_exploit
from ..poc.runners.toolbox import run_raw_command, run_tool
from ..poc.verify import verify_poc
from ..retrieval import gather_web_hints
from ..sanitize import classify_input, decide, judge_injection
from ..sanitize.classifier import note_verdict, verdict_stats
from ..security import (
    neutralize_active_content, require_public_input, sanitize_output_language,
    wrap_untrusted_content,
)
from .messages import parse_statistical, report_prompt, statistical_prompt, translate_prompt
from .schemas import (
    BughuntRequest,
    ConversationChatRequest,
    CreateConversationRequest,
    ExecRequest,
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


# ── Multi-conversation (durable chat history) helpers ────────────────────────────
# The NEW /ai/conversations/* endpoints layer a client-supplied conversation_id on the same
# chat pipeline. owner_hash = sha256(user_id) is folded into the linch session id, so user B
# can only ever address `chat:conv:{sha256(B)}:{id}` — a different, empty session. Cross-user
# access is structurally impossible; the SQLite owner_hash column is the second, explicit gate.
_CONV_DATE_PREFIX_RE = re.compile(r"^\[Hôm nay là [^\]]*\]\n")


def _strip_date_prefix(text: str) -> str:
    """Drop the ``[Hôm nay là …]\\n`` prefix ``runner._dated_prompt`` prepends to a user turn,
    so the durable transcript stores the user's own words (defensive — the route stores the
    pre-dated ``supplied_text`` anyway, this only matters if a dated prompt ever reaches here)."""
    return _CONV_DATE_PREFIX_RE.sub("", text or "", count=1)


def _conv_owner_hash(user_id: Any) -> str | None:
    """sha256 of a real user_id, or None for anonymous/blank (mirrors _chat_session_id)."""
    uid = str(user_id or "").strip()
    if not uid or uid == "anonymous":
        return None
    return hashlib.sha256(uid.encode()).hexdigest()


def _conv_session_id(owner_hash: str | None, conversation_id: str) -> str:
    """linch session id for a conversation. Real owner → deterministic, owner-scoped id;
    anonymous → a fresh ephemeral id per request (never indexed, no cross-user readback)."""
    if not owner_hash:
        return f"chat:conv:anon:{uuid4().hex}"
    return f"chat:conv:{owner_hash}:{conversation_id}"


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
        # A consolidated (AI-merged) entry has no bytes in the store — its `sha256` is the
        # ORIGINAL artifact's, so re-reading by it would overwrite the merged code with the
        # verbatim source. Leave it exactly as produced by poc/consolidate.py.
        if ev.get("consolidated"):
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

    async def complete(profile: str, body: LegacyRequest, *, prompt_override: str | None = None,
                       session_id: str | None = None) -> tuple[str, dict[str, Any]]:
        if prompt_override is not None:
            # Endpoint already built the task-specific user message (translate/report/…).
            prompt = prompt_override
        else:
            prompt = require_public_input(body.supplied_text())
            if profile == "chat":
                gated = await _chat_gate(prompt)
                if gated is not None:  # model classifier short-circuited (injection/off-topic)
                    return gated
        try:
            text, metadata = await state.runner.complete(profile, prompt, session_id)
        except HTTPException:
            raise
        except Exception as exc:  # provider/runtime failure → clean 503, not an opaque 500
            # Mirrors the streaming path (see stream()), which already surfaces failures
            # as an SSE error event instead of tearing down the response.
            raise HTTPException(status_code=503, detail=f"AI service unavailable: {exc}") from exc
        return sanitize_output_language(text), metadata

    def base_meta(profile: str, run_id: str | None) -> dict[str, Any]:
        return {"run_id": run_id, "profile": profile, "tool_registry": sorted(registry_for(profile))}

    def stream(profile: str, prompt: str, session_id: str | None, build_final, *,
               prelude=None, observe=None) -> StreamingResponse:
        """Shared SSE runner for every agentic/text endpoint: streams tool_call /
        tool_result / token events as they happen, then one final event built by the
        endpoint's ``build_final(final_text, run_id)`` (which may also persist state).

        ``prelude`` (list of pre-encoded SSE frames) is emitted before the run starts and
        ``observe(event) -> bytes|None`` may emit an extra additive frame per run event —
        both default to nothing, so the pre-existing call-sites are byte-identical. The
        conversation endpoints use them for a leading ``conversation`` frame and an optional
        ``compaction`` frame WITHOUT touching event_to_sse (old streams gain no new frames).

        A ``regenerate`` event (see api/sse.py) can arrive mid-stream when a hook forces
        the model to redo its answer (e.g. it answered before calling required tools) —
        clients accumulating `token` deltas into one buffer MUST clear that buffer on
        ``regenerate``, or the rejected draft and the corrected one render concatenated
        with no boundary between them. The final `final` event is always the corrected,
        authoritative text regardless — only the raw `token` stream is affected."""

        async def gen() -> AsyncIterator[bytes]:
            for pf in (prelude or []):   # additive leading frames; empty for legacy callers
                yield pf
            final = ""
            run_id = None
            failed = False
            try:
                async for event in state.runner.stream(profile, prompt, session_id):
                    if isinstance(event, SystemEvent):
                        run_id = getattr(event, "run_id", None) or run_id
                    if isinstance(event, ResultEvent):
                        final = sanitize_output_language(event.final_text or "")
                    if observe is not None:   # additive side-frames (e.g. compaction); None for legacy
                        extra = observe(event)
                        if extra:
                            yield extra
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

    async def _chat_gate(prompt: str) -> tuple[str, dict[str, Any]] | None:
        """Model-based input sanitization for the chat path (runs BEFORE the agent).

        Embeds the prompt with the deployed sec-embedding model and applies the numpy head
        (secai_gateway.sanitize) to classify intent + risk. Returns a short-circuit
        ``(refusal_text, metadata)`` when the classifier blocks (injection / off-topic), or
        ``None`` to let the agent answer. FAIL-OPEN: any classifier error/unavailability, or
        the feature being disabled, returns ``None`` (pass through)."""
        if not getattr(state.settings, "input_classifier_enabled", True):
            return None
        run_id = uuid4().hex
        try:
            verdict = await classify_input(prompt, settings=state.settings,
                                           client=getattr(state, "http_client", None))
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
            decision = decide(verdict, state.settings)
        except Exception:  # a guard must never break chat
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
            "tool_registry": sorted(registry_for("chat")),
            "input_classification": classification, "short_circuit": True,
        }
        return decision.message, metadata

    def _short_circuit_stream(message: str, metadata: dict[str, Any], *,
                              prelude=None, top_level=None) -> StreamingResponse:
        """SSE response for a classifier short-circuit: one token + one final, then DONE
        (same frame shape as chat_stream's build_final, so token-only UIs still render it).

        ``prelude``/``top_level`` are additive (leading frames; extra final-frame keys) and
        default to nothing → the legacy call-site is byte-identical."""
        async def gen() -> AsyncIterator[bytes]:
            for pf in (prelude or []):
                yield pf
            yield sse_frame("token", {"delta": message})
            final_payload = {"content": message, "answer": message,
                             "message": message, "metadata": metadata}
            if top_level:
                final_payload.update(top_level)
            yield sse_frame("final", final_payload)
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
        gated = await _chat_gate(text)
        if gated is not None:  # model classifier short-circuited before the agent runs
            return _short_circuit_stream(gated[0], gated[1])
        session_id = _chat_session_id(body)

        async def build_final(final: str, run_id: str | None) -> dict[str, Any]:
            # linch persists the turn to its session store during the run — nothing to do here.
            return {"content": final, "answer": final, "message": final, "metadata": base_meta("chat", run_id)}

        return stream("chat", text, session_id, build_final)

    # ── Multi-conversation chat (durable history) — NEW, additive endpoints ─────────
    # Same chat pipeline (_chat_gate + complete/stream) scoped by a conversation_id. The old
    # /ai/chat endpoints are untouched. Chat output is a strict SUPERSET of the old shape
    # (adds conversation_id). Durable index+transcript in SQLite (runner.conversations); the
    # linch Redis store stays the live, auto-compacted working memory.

    async def _maybe_rehydrate(session_id: str, conversation_id: str) -> None:
        """Reopen-after-expiry: if the linch session has no live messages but the durable
        transcript does, seed the session with the last N turns so context survives the 24h
        Redis window. Fail-open — any error just means older context isn't reloaded."""
        conversations = getattr(state.runner, "conversations", None)
        store = getattr(state.runner, "session_store", None)
        n = int(getattr(state.settings, "chat_history_rehydrate_turns", 0) or 0)
        if conversations is None or store is None or n <= 0:
            return
        try:
            existing = await store.load_messages(session_id)
            if existing:
                return  # live memory still present — nothing to rehydrate
            turns = conversations.recent_turns(conversation_id, n)
            if not turns:
                return
            from linch.types import Message, TextBlock
            msgs = [Message(role=t["role"], content=[TextBlock(text=t["content"])])
                    for t in turns
                    if (t.get("content") or "").strip() and t.get("role") in ("user", "assistant")]
            if not msgs:
                return
            try:
                await store.create(id=session_id)
            except Exception:
                pass
            await store.append_messages(session_id, msgs)
        except Exception:
            logger.exception("conversation rehydrate failed (fail-open)")

    def _persist_turn(owner_hash: str, conversation_id: str, session_id: str,
                      user_text: str, assistant_text: str) -> None:
        """Durably record one user+assistant turn in SQLite (create-if-first). Fail-open: a
        store error must never break a chat reply that already succeeded. Skips a cross-owner
        conversation_id collision (owns() re-checked after create)."""
        conversations = getattr(state.runner, "conversations", None)
        if conversations is None or not owner_hash:
            return
        try:
            now = int(time.time())
            if not conversations.owns(owner_hash, conversation_id):
                conversations.create(owner_hash, conversation_id, session_id, "", now)
            if conversations.owns(owner_hash, conversation_id):
                conversations.append_turn(
                    conversation_id, _strip_date_prefix(user_text), assistant_text, now)
        except Exception:
            logger.exception("durable conversation persist failed (fail-open)")

    def _new_cid(supplied: str | None) -> str:
        cid = (supplied or "").strip()
        return cid or uuid4().hex

    @app.post("/ai/conversations/chat")
    async def conversations_chat(body: ConversationChatRequest) -> dict[str, Any]:
        owner_hash = _conv_owner_hash(body.user_id)
        conversation_id = _new_cid(body.conversation_id)
        session_id = _conv_session_id(owner_hash, conversation_id)
        if owner_hash:
            await _maybe_rehydrate(session_id, conversation_id)
        text, metadata = await complete("chat", body, session_id=session_id)
        metadata = {**metadata, "conversation_id": conversation_id}
        if owner_hash and not metadata.get("short_circuit"):
            _persist_turn(owner_hash, conversation_id, session_id, body.supplied_text(), text)
        return {"response": text, "answer": text, "message": text,
                "metadata": metadata, "conversation_id": conversation_id}

    @app.post("/ai/conversations/chat/stream")
    async def conversations_chat_stream(body: ConversationChatRequest) -> StreamingResponse:
        text = require_public_input(body.supplied_text())
        owner_hash = _conv_owner_hash(body.user_id)
        conversation_id = _new_cid(body.conversation_id)
        session_id = _conv_session_id(owner_hash, conversation_id)
        conv_frame = sse_frame("conversation", {"conversation_id": conversation_id})
        gated = await _chat_gate(text)
        if gated is not None:  # classifier short-circuit before the agent runs (not persisted)
            md = {**gated[1], "conversation_id": conversation_id}
            return _short_circuit_stream(gated[0], md, prelude=[conv_frame],
                                         top_level={"conversation_id": conversation_id})
        if owner_hash:
            await _maybe_rehydrate(session_id, conversation_id)
        user_text = body.supplied_text()
        holder: dict[str, Any] = {"compaction": None}

        def observe(event: Any) -> bytes | None:
            if (getattr(state.settings, "conversation_compaction_meta", True)
                    and type(event).__name__ == "CompactionEvent"):
                info = {k: getattr(event, k, None) for k in
                        ("strategy", "tokens_before", "tokens_after",
                         "messages_before", "messages_after")}
                holder["compaction"] = info
                return sse_frame("compaction", info)
            return None

        async def build_final(final: str, run_id: str | None) -> dict[str, Any]:
            md = {**base_meta("chat", run_id), "conversation_id": conversation_id}
            if holder["compaction"]:
                md["compaction"] = holder["compaction"]
            if owner_hash and final and final != STREAM_ERROR_MESSAGE:
                _persist_turn(owner_hash, conversation_id, session_id, user_text, final)
            return {"content": final, "answer": final, "message": final,
                    "metadata": md, "conversation_id": conversation_id}

        return stream("chat", text, session_id, build_final,
                      prelude=[conv_frame], observe=observe)

    @app.post("/ai/conversations")
    async def create_conversation(body: CreateConversationRequest) -> dict[str, Any]:
        owner_hash = _conv_owner_hash(body.user_id)
        if not owner_hash:
            raise HTTPException(400, "user_id là bắt buộc để tạo hội thoại có lịch sử.")
        conversation_id = uuid4().hex
        session_id = _conv_session_id(owner_hash, conversation_id)
        now = int(time.time())
        conversations = getattr(state.runner, "conversations", None)
        if conversations is not None:
            try:
                conversations.create(owner_hash, conversation_id, session_id, (body.title or ""), now)
            except Exception:
                logger.exception("create_conversation persist failed")
        return {"conversation_id": conversation_id, "title": body.title or "",
                "created_at": now, "updated_at": now, "message_count": 0}

    @app.get("/ai/conversations")
    async def list_conversations(user_id: str | None = None, limit: int = 20,
                                 offset: int = 0) -> dict[str, Any]:
        owner_hash = _conv_owner_hash(user_id)
        if not owner_hash:
            raise HTTPException(400, "user_id là bắt buộc để liệt kê hội thoại.")
        conversations = getattr(state.runner, "conversations", None)
        if conversations is None:
            return {"conversations": [], "total": 0, "limit": limit, "offset": offset}
        rows, total = conversations.list_for_owner(owner_hash, limit, offset)
        return {"conversations": rows, "total": total, "limit": limit, "offset": offset}

    @app.get("/ai/conversations/{conversation_id}")
    async def get_conversation(conversation_id: str, user_id: str | None = None,
                               limit: int = 200, offset: int = 0) -> dict[str, Any]:
        owner_hash = _conv_owner_hash(user_id)
        if not owner_hash:
            raise HTTPException(400, "user_id là bắt buộc để đọc hội thoại.")
        conversations = getattr(state.runner, "conversations", None)
        if conversations is None or not conversations.owns(owner_hash, conversation_id):
            raise HTTPException(404, "Không tìm thấy hội thoại.")   # 404, không xác nhận tồn tại
        row = conversations.get(conversation_id) or {}
        messages, total = conversations.messages(conversation_id, limit, offset)
        return {"conversation_id": conversation_id, "title": row.get("title", ""),
                "created_at": row.get("created_at"), "updated_at": row.get("updated_at"),
                "message_count": row.get("message_count", total), "messages": messages,
                "total": total, "limit": limit, "offset": offset}

    @app.delete("/ai/conversations/{conversation_id}")
    async def delete_conversation(conversation_id: str,
                                  user_id: str | None = None) -> dict[str, Any]:
        owner_hash = _conv_owner_hash(user_id)
        if not owner_hash:
            raise HTTPException(400, "user_id là bắt buộc để xoá hội thoại.")
        conversations = getattr(state.runner, "conversations", None)
        if conversations is None or not conversations.owns(owner_hash, conversation_id):
            raise HTTPException(404, "Không tìm thấy hội thoại.")
        row = conversations.get(conversation_id) or {}
        conversations.soft_delete(conversation_id)
        store = getattr(state.runner, "session_store", None)
        sid = row.get("session_id")
        if store is not None and sid:
            try:
                await store.delete(sid)
            except Exception:
                logger.exception("session delete failed for %s", sid)
        return {"deleted": True, "conversation_id": conversation_id}

    @app.post("/ai/translate")
    async def translate(body: LegacyRequest) -> dict[str, Any]:
        text, metadata = await complete("translate", body, prompt_override=translate_prompt(body))
        return {"translated_text": text, "translation": text, "metadata": metadata}

    @app.post("/ai/translate/stream")
    async def translate_stream(body: LegacyRequest) -> StreamingResponse:
        async def build_final(final: str, run_id: str | None) -> dict[str, Any]:
            return {"content": final, "translation": final, "translated_text": final, "metadata": base_meta("translate", run_id)}
        return stream("translate", translate_prompt(body), None, build_final)

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
        return {**parse_statistical(text), "report": text, "statistical_report": text, "metadata": metadata}

    @app.post("/ai/generate-statistical-report/stream")
    async def statistical_stream(body: LegacyRequest) -> StreamingResponse:
        async def build_final(final: str, run_id: str | None) -> dict[str, Any]:
            return {**parse_statistical(final), "content": final, "report": final, "statistical_report": final, "metadata": base_meta("statistical_report", run_id)}
        return stream("statistical_report", statistical_prompt(body), None, build_final)

    async def exploit_prefetch(prompt: str, session_id: str | None = None) -> dict[str, Any]:
        """Deterministic evidence gathering (see poc/prefetch.py) — runs independently of,
        and concurrently with, the model's own agentic turn; its own scratch run_id keeps
        it fully separate from whatever run_id the model's session ends up using, so it
        can never collide with or be starved by the agentic path. Records prefetch
        ``coverage`` on ``session_id`` so RequireSearchBeforeAnswerHook can skip
        force_continue when coverage is already ``enough``|``empty``. Fail-open: any
        error → coverage ``failed`` + empty evidence (hook + enforce_section1 backstop).

        Also runs the CVE-KB existence check (``cve_exists_in_kb``) concurrently with the
        catalog prefetch — same fail-open contract — so the T2/T3 synthesize-from-CVE tier
        in ``enforce_section1`` knows whether the CVE is real even when the catalog has
        nothing for it. Also records it on ``session_id`` (``set_session_cve_known``) so
        RequireSection1SynthesisHook can force a retry when the model skipped Section 1
        despite the CVE being confirmed real. Same for T2 catalog-description evidence
        (``set_session_synthesis_evidence``) and T1 real-code evidence
        (``set_session_real_code_evidence``) — prefetch's own fetches land in the ledger
        under ITS scratch ``run_id`` (below), never the model's turn's run_id, so the hook
        cannot see them any other way (see prefetch.py's own docstring on these functions).
        Returns ``{"evidence", "cve_id", "cve_known"}``."""
        from ..poc.inject import has_synthesis_evidence
        from ..poc.prefetch import (
            set_session_coverage, set_session_cve_known, set_session_real_code_evidence,
            set_session_synthesis_evidence,
        )
        from ..retrieval.neo4j import cve_exists_in_kb
        cve_id = extract_cve_id(prompt)
        poc = getattr(state.runner, "poc", None)
        ledger = getattr(state.runner, "ledger", None)
        if poc is None or ledger is None:
            set_session_coverage(session_id, "failed")
            cve_known = await cve_exists_in_kb(cve_id) if cve_id else False
            set_session_cve_known(session_id, cve_known)
            set_session_synthesis_evidence(session_id, False)
            set_session_real_code_evidence(session_id, False)
            return {"evidence": [], "cve_id": cve_id, "cve_known": cve_known}
        run_id = f"prefetch:{uuid4().hex}"
        try:
            if cve_id:
                report, cve_known = await asyncio.gather(
                    prefetch_evidence(poc, ledger, prompt, run_id), cve_exists_in_kb(cve_id))
            else:
                report, cve_known = await prefetch_evidence(poc, ledger, prompt, run_id), False
            cov = report.get("coverage") or "failed"
            if cov not in ("enough", "empty", "partial", "failed"):
                cov = "failed"
            set_session_coverage(session_id, cov)
            set_session_cve_known(session_id, cve_known)
            evidence = ledger.evidence_for_run(run_id)
            set_session_synthesis_evidence(session_id, has_synthesis_evidence(evidence))
            set_session_real_code_evidence(session_id, has_real_code_evidence(evidence))
            return {"evidence": evidence, "cve_id": cve_id, "cve_known": cve_known}
        except Exception:
            logger.exception("deterministic PoC prefetch failed")
            set_session_coverage(session_id, "failed")
            set_session_cve_known(session_id, False)
            set_session_synthesis_evidence(session_id, False)
            set_session_real_code_evidence(session_id, False)
            return {"evidence": [], "cve_id": cve_id, "cve_known": False}

    async def inject_reference_code(text: str, run_id: str | None,
                                    pre_evidence: list[dict[str, Any]] | None = None, *,
                                    cve_known: bool = False,
                                    cve_id: str | None = None) -> tuple[str, dict[str, Any]]:
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
        the model context stays capped. No new endpoint, no FE change needed."""
        ledger = getattr(state.runner, "ledger", None)
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
        return meta

    def exploit_guidance_meta() -> dict[str, Any]:
        """Metadata for the chitchat→"nhập CVE" guidance reply: keeps the same shape the FE
        reads (evidence/provenance/candidate_status) but flags no PoC work was done."""
        meta = base_meta("exploit_research", None)
        meta.update({"evidence_ids": [], "edb_ids": [], "provenance": [],
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

    async def _build_exploit_grounding(
        prefetch_result: dict[str, Any], web_hints: list[dict[str, Any]],
        session_id: str | None, target: str | None = None) -> tuple[list[dict[str, Any]], str, bool]:
        """Acquire the best real PoC code (catalog → temp cache → on-demand GitHub → web raw
        script), consolidate it into ONE runnable file when enabled, and build the model
        appendix. Internet-fetched code is saved to the temp cache for reuse next time.

        Returns ``(pre_evidence, appendix, consolidated)``:
        * consolidated → ``pre_evidence`` is ``[merged_file] + description-only hints``; the
          appendix tells the model Mục 1 is that file (system-injected) and to write Mục 2/3
          against it. Session real-code/coverage flags are set so the synthesis/search hooks
          don't force a spurious retry.
        * not consolidated → the unchanged verbatim path: on-demand/web real code is still
          merged in (better coverage), and the existing prefetch + web-hint appendices apply.
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
        if not all_real:
            all_real = web_real  # web raw PoC script → real code (also internet)

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
        elif all_real and getattr(settings, "poc_consolidate_enabled", False):
            try:
                real_full = await _fullen_evidence_for_display(list(all_real), poc)
                # Deterministic core extraction first (Python entry): a verbatim subset of the
                # real source, never re-written — this is the "no LLM on the bytes" fast path.
                # Falls back to the LLM consolidation below on any miss (non-Python, cross-file
                # dependency, parse failure) so the model merge still covers what slicing can't.
                sliced = await slice_entry(real_full[0], state.runner, cve_id,
                                           use_llm=getattr(settings, "poc_slice_llm_enabled", True))
                if sliced is not None:
                    consolidated = sliced
                else:
                    consolidated = await consolidate_poc(state.runner, real_full, cve_id)
            except Exception:
                logger.exception("poc consolidation failed for %s", cve_id)
                consolidated = None

        if consolidated:
            desc_only = [e for e in list(base_evidence) + list(web_hints or [])
                         if not has_real_code_evidence([e])]
            pre_evidence = [consolidated] + merge_evidence(desc_only)
            return pre_evidence, _consolidated_prompt_appendix(consolidated, cve_id), True

        # Fallback (consolidation off/failed, or no code anywhere): unchanged verbatim/synthesis.
        merged_for_appendix = {**prefetch_result,
                               "evidence": merge_evidence(base_evidence, extra_real)}
        pre_evidence = merge_evidence(base_evidence, extra_real, web_hints or [])
        appendix = (_prefetch_prompt_appendix(merged_for_appendix)
                    + _web_hints_prompt_appendix(web_hints or []))
        return pre_evidence, appendix, False

    @app.post("/ai/exploit")
    async def exploit(body: LegacyRequest) -> dict[str, Any]:
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
        pre_evidence, grounding_appendix, _consolidated = await _build_exploit_grounding(
            prefetch_result, web_hints, session_id, target=_exploit_target(body, prompt_text))
        agent_prompt = (prompt_text
                        + (_kt_prompt_appendix(kt_result) if kt_result else "")
                        + grounding_appendix)
        text, metadata = await complete("exploit_research", body, session_id=session_id,
                                        prompt_override=agent_prompt)
        text, injected = await inject_reference_code(
            text, metadata.get("run_id"), pre_evidence,
            cve_known=prefetch_result.get("cve_known", False), cve_id=prefetch_result.get("cve_id"))
        no_poc = "No verified public PoC was found in the supplied evidence."
        metadata.setdefault("evidence_ids", []); metadata.setdefault("edb_ids", [])
        metadata.setdefault("provenance", []); metadata.setdefault("candidate_status", "not_generated")
        metadata.update(injected)
        if kt_result is not None:
            metadata["poc_kt_exploit"] = kt_result
            _audit(kt_run_id, "poc_kt_exploit_http",
                   f"{kt_result.get('cve')}:{kt_result.get('target_ip')}",
                   {"user_id": body.user_id, "exploit_verdict": kt_result.get("exploit_verdict"),
                    "ok": kt_result.get("ok"), "error": kt_result.get("error")})
        await _pad_to_floor(_start, _floor)  # uniform total latency (see _pad_to_floor)
        safe = neutralize_active_content(text or no_poc)  # defang XSS in the injected PoC too
        return {"description": safe, "exploit_guide": safe, "metadata": metadata}

    @app.post("/ai/exploit/stream")
    async def exploit_stream(body: LegacyRequest) -> StreamingResponse:
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
            prefetch_result = await prefetch_task
            pre_evidence = grounding_holder.get("pre_evidence")
            if pre_evidence is None:  # prelude failed before grounding — fall back to raw merge
                pre_evidence = merge_evidence(prefetch_result.get("evidence") or [], web_hints_holder)
            final, injected = await inject_reference_code(
                final, run_id, pre_evidence,
                cve_known=prefetch_result.get("cve_known", False), cve_id=prefetch_result.get("cve_id"))
            meta = exploit_final_meta(run_id)
            meta.update(injected)
            if kt_result is not None:
                meta["poc_kt_exploit"] = kt_result
            safe = neutralize_active_content(final or no_poc)  # defang XSS in the injected PoC too
            return {"content": safe, "exploit_guide": safe, "description": safe, "metadata": meta}

        async def gen() -> AsyncIterator[bytes]:
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
                _pre, prefetch_appendix, _cons = await _build_exploit_grounding(
                    _pf, _hints, session_id, target=_exploit_target(body, text))
                grounding_holder["pre_evidence"] = _pre
                grounding_holder["consolidated"] = _cons
            except Exception:
                logger.exception("prefetch appendix build failed")
            # Wait up to 2s for KT so we can append appendix before the model starts when
            # intent is clear; if slower, start model and emit tool SSE when KT finishes.
            agent_text = text + prefetch_appendix
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
                        agent_text = text + _kt_prompt_appendix(kt_result) + prefetch_appendix

            model_agen = state.runner.stream("exploit_research", agent_text, session_id).__aiter__()
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
                yield sse_frame("final", await build_final(final, run_id, kt_result))
            yield b"data: [DONE]\n\n"

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
            redis_status = "memory-dev"
        # Ư4: surface the input-classifier fail-open rate here too, not just as a WARNING log
        # line — this is exactly the CLAUDE.md-documented failure mode (guard silently disabled,
        # /health said ok) that fail-open observability was built to catch. A high unknown_rate
        # over a real sample means the guard is bypassed on most requests right now.
        sanitize_stats = verdict_stats()
        status = _health_status(qwen_ok, sanitize_stats)
        return {"status": status, "message": "secai gateway", "backend": provider_kind, "redis": redis_status, "postgres": "disabled", "llm": qwen_ok, "qwen": qwen_ok, "searchsploit": searchsploit_ok, "exploitdb_commit": commit, "input_sanitize": sanitize_stats}

    @app.post("/admin/refresh-models")
    async def refresh_models(x_api_key: Annotated[str | None, Header()] = None) -> dict[str, Any]:
        expected = state.settings.admin_api_key
        if not expected or x_api_key != expected:
            raise HTTPException(401, "Invalid admin API key")
        return {"status": "ok", "refreshed": True}
