"""Deterministic Linch hooks used by the shared gateway agent.

The hooks intentionally keep only bounded, per-run in-memory state.  They are
coverage/completeness nudges rather than correctness gates: after a limited
number of regeneration requests, they allow the normal deterministic fallback
paths to produce the final answer.
"""
from __future__ import annotations

import json
import re
from collections import OrderedDict
from dataclasses import dataclass, replace
from typing import Any

from linch.hooks.contexts import BeforeFinalAnswerContext, PostToolUseContext, PreToolUseContext
from linch.hooks.types import HookResult, ToolResult

from ..config import get_settings
from ..poc.inject import has_real_code_evidence, has_synthesis_evidence, section1_has_body
from ..poc.prefetch import (
    COVERAGE_CLOSES_SEARCH_HOOK, get_session_coverage, get_session_cve_known,
    get_session_kb_searched, get_session_real_code_evidence,
    get_session_synthesis_evidence,
)
try:
    from ..poc.prefetch import get_session_kb_searched
except ImportError:
    def get_session_kb_searched(session_id: str | None) -> bool:
        return False
from ..security import wrap_untrusted_content

_MAX_TRACKED_RUNS = 20000
# Numbered-heading match for Mục 2/Mục 3, mirroring poc/inject.py's own `_H1` pattern
# (any ##-###### heading starting with "2."/"3." then non-space content) rather than the
# exact Vietnamese wording, since the model's phrasing can drift slightly turn to turn.
_H2 = re.compile(r"(?m)^[ \t]*#{2,6}[ \t]*2\.[ \t]*\S")
_H3 = re.compile(r"(?m)^[ \t]*#{2,6}[ \t]*3\.[ \t]*\S")


def _trim(store: OrderedDict[str, Any]) -> None:
    """Keep an OrderedDict bounded, evicting least-recently-used run state."""
    while len(store) > _MAX_TRACKED_RUNS:
        store.popitem(last=False)


def _profile_of(ctx: Any) -> str | None:
    session = getattr(ctx, "session", None)
    meta = getattr(session, "meta", None)
    return meta.get("profile") if isinstance(meta, dict) else None


def _session_id_of(ctx: Any) -> str | None:
    session = getattr(ctx, "session", None)
    return getattr(session, "id", None)


class EphemeralTurnContextHook:
    """Delivers per-turn ephemeral context (today's date, optional pre-fetched KB
    grounding) to the model WITHOUT it ever landing in the persisted session history.

    Bug this fixes (2026-09-21): the chat leak where switching away from and back to
    the chatbot re-displayed the raw internal prompt (``<env>...``, ``[Ngữ cảnh tra
    cứu...]``, KB dump, ``[Câu hỏi của người dùng]``). Root cause: linch persists
    EXACTLY the string passed to ``session.run(prompt)`` as the user turn (see
    ``linch/loop/runner.py``: ``user_message = build_user_message(prompt, images)``
    then ``await session.append([user_message])``) — so any grounding/date text baked
    into that string is what ``GET /sessions/{sid}/messages`` returns verbatim later.

    linch's per-turn context-build channel is the supported way out: any hook exposing
    an async ``build_context(session, turn_index)`` is invoked once per model turn by
    ``loop/request.py::_build_context_result`` / ``ContextInjectionHook`` (same
    mechanism). Its returned ``ContextBuildResult`` is folded into the OUTGOING provider
    request ONLY (``_build_turn_request``) — never appended to ``session.provider_view``/
    ``full_history``/the store.

    Delivered as an extra ``messages`` entry (``ContextBuildResult.messages``), appended
    AFTER ``session.provider_view`` (``_build_turn_request``: ``messages = list(session.
    provider_view); messages.extend(context.messages)``) — i.e. as one more user-role
    turn placed right after the REAL, already-persisted, now-clean user question, seen
    by the model for this generation only. Deliberately NOT ``ContextBuildResult.
    system_blocks``: those get joined into the SAME single system-role message as
    ``session.system_blocks_override`` (see ``linch/providers/openai_chat.py::
    _build_chat_payload``: ``"\\n\\n".join(block.text for block in req.system)``), which
    sits immediately before the tool schemas in the rendered vLLM/Qwen chat-template
    prompt. Putting per-turn volatile text there would reintroduce almost exactly the
    cache-prefix problem ``agent/runner.py``'s old ``_dated_prompt`` docstring called out
    ("landing the date in the system block would invalidate that whole cached prefix
    (system + tools) once a day") — except now on EVERY turn of EVERY profile (date +,
    for chat, a multi-KB grounding dump), not once a day. Routing it through ``messages``
    instead keeps ``session.system_blocks_override`` + tool schemas byte-identical and
    fully vLLM-prefix-cacheable across requests, exactly as before this fix, while still
    keeping the ephemeral content out of the persisted store.

    ``LinchRunner`` (agent/runner.py) stashes the plain text to inject on
    ``session.meta["ephemeral_context"]`` right before every ``session.run()`` call
    (``session.meta`` is the live in-memory Session object's own field — set via plain
    attribute assignment, NOT ``session.update_meta()``, so it is never written to the
    durable ``SessionRecord`` the ``GET /sessions/{sid}`` endpoints return either).
    This hook just re-emits that string as a message on every turn of the run.
    """

    name = "ephemeral_turn_context"

    async def build_context(self, session: Any, turn_index: int) -> Any:
        meta = getattr(session, "meta", None)
        text = meta.get("ephemeral_context") if isinstance(meta, dict) else None
        if not text:
            return None
        from linch.context import ContextBuildResult
        from linch.types import Message, TextBlock

        selected = meta.get("scope_user_prompt") if isinstance(meta, dict) else None
        original = meta.get("last_user_prompt") if isinstance(meta, dict) else None
        if selected and original:
            # Linch already appended the original to the durable store/full_history.
            # Replace only its derived provider view; never mutate shared Message objects.
            for index in range(len(session.provider_view) - 1, -1, -1):
                message = session.provider_view[index]
                if message.role == "user" and any(
                        isinstance(block, TextBlock) and block.text == original
                        for block in message.content):
                    session.provider_view[index] = Message(role="user", content=[TextBlock(text=selected)])
                    break
        return ContextBuildResult(messages=[Message(role="user", content=[TextBlock(text=text)])])


class ToolCallBudgetHook:
    """Block a tool after its configured per-run invocation budget is exhausted."""

    name = "tool_call_budget"

    def __init__(self, limits: dict[str, int]) -> None:
        self.limits = limits
        self._counts: OrderedDict[str, dict[str, int]] = OrderedDict()

    async def on_pre_tool_use(self, ctx: PreToolUseContext) -> HookResult | None:
        limit = self.limits.get(ctx.tool_name)
        if limit is None:
            return None

        counts = self._counts.setdefault(ctx.run_id, {})
        self._counts.move_to_end(ctx.run_id)
        _trim(self._counts)
        used = counts.get(ctx.tool_name, 0)
        if used >= limit:
            return HookResult.block(
                f"{ctx.tool_name} limit reached ({limit} calls per run).",
                feedback=f"{ctx.tool_name} limit reached ({limit} calls per run).",
            )
        counts[ctx.tool_name] = used + 1
        return None


class RequireSearchBeforeAnswerHook:
    """Require an exploit-research run to attempt search and, when needed, fetch.

    A successful deterministic prefetch with ``enough`` or ``empty`` coverage
    closes this requirement for the corresponding session.
    """

    name = "require_search_before_answer"

    def __init__(
        self,
        search_tool: str,
        fetch_tool: str,
        profile: str,
        max_retries: int = 2,
    ) -> None:
        self.search_tool = search_tool
        self.fetch_tool = fetch_tool
        self.profile = profile
        self.max_retries = max_retries
        self._searched: OrderedDict[str, bool] = OrderedDict()
        self._has_hits: OrderedDict[str, bool] = OrderedDict()
        self._fetched: OrderedDict[str, bool] = OrderedDict()
        self._attempts: OrderedDict[str, int] = OrderedDict()

    @staticmethod
    def _mark(store: OrderedDict[str, bool], run_id: str) -> None:
        store[run_id] = True
        store.move_to_end(run_id)
        _trim(store)

    @staticmethod
    def _get(store: OrderedDict[str, Any], run_id: str, default: Any = None) -> Any:
        if run_id in store:
            store.move_to_end(run_id)
            return store[run_id]
        return default

    async def on_pre_tool_use(self, ctx: PreToolUseContext) -> None:
        if ctx.tool_name == self.search_tool:
            self._mark(self._searched, ctx.run_id)
        elif ctx.tool_name == self.fetch_tool:
            self._mark(self._fetched, ctx.run_id)
        return None

    async def on_post_tool_use(self, ctx: PostToolUseContext) -> None:
        if ctx.tool_name != self.search_tool or getattr(ctx.result, "is_error", False):
            return None
        try:
            payload = json.loads(ctx.result.content)
        except (TypeError, ValueError):
            return None
        if isinstance(payload, dict) and isinstance(payload.get("hits"), list) and payload["hits"]:
            self._mark(self._has_hits, ctx.run_id)
        return None

    async def on_before_final_answer(self, ctx: BeforeFinalAnswerContext) -> HookResult | None:
        if _profile_of(ctx) != self.profile:
            return None
        if get_session_coverage(_session_id_of(ctx)) in COVERAGE_CLOSES_SEARCH_HOOK:
            return None

        run_id = ctx.run_id
        searched = self._get(self._searched, run_id, False)
        needs_fetch = self._get(self._has_hits, run_id, False) and not self._get(
            self._fetched, run_id, False
        )
        if searched and not needs_fetch:
            return None

        attempts = self._get(self._attempts, run_id, 0)
        if attempts >= self.max_retries:
            return None
        self._attempts[run_id] = attempts + 1
        self._attempts.move_to_end(run_id)
        _trim(self._attempts)

        if not searched:
            feedback = f"Before answering, call {self.search_tool} to search for PoC evidence."
        else:
            feedback = (
                f"{self.search_tool} returned hits; before answering, call "
                f"{self.fetch_tool} for the relevant PoC evidence."
            )
        return HookResult.force_continue(feedback, reason="search_before_answer_unsatisfied")


_EXPLOIT_KB_SEARCH_EXHAUSTED = (
    "## 1. Mã Khai thác (PoC) và Payload\n\n"
    "Không thể xác thực thông tin lỗ hổng từ cơ sở dữ liệu nội bộ (KB). "
    "Hệ thống không thể sinh mã khai thác khi chưa có bằng chứng xác thực từ cơ sở dữ liệu."
)


class RequireKbSearchBeforeSynthesisHook:
    """Require usable internal KB context before trusting synthesized exploit content.

    Real fetched PoC code can bypass this synthesis requirement. Metadata-only
    prefetch and failed or empty tool attempts cannot.
    """

    name = "require_kb_search_before_synthesis"

    def __init__(self, tools: tuple[str, ...], profile: str, max_retries: int = 2) -> None:
        self.tools = set(tools)
        self.profile = profile
        self.max_retries = max_retries
        self._searched: OrderedDict[str, bool] = OrderedDict()
        self._attempts: OrderedDict[str, int] = OrderedDict()

    async def on_pre_tool_use(self, ctx: PreToolUseContext) -> None:
        # Attempts do not establish evidence; inspect the actual tool result instead.
        return None

    async def on_post_tool_use(self, ctx: PostToolUseContext) -> None:
        if ctx.tool_name not in self.tools or ctx.result is None or ctx.result.is_error:
            return None
        content = (ctx.result.content or "").strip()
        if not content or content.startswith((
            "Không tìm thấy kết quả trong knowledge base.",
            "No results returned from the graph database.",
            "No results found",
            "No CVE/CWE/CAPEC/Technique/Tactic ID or product name found",
        )):
            return None
        try:
            from secai_gateway.retrieval.kb_context import has_technical_description
            if not has_technical_description(content):
                return None
        except ImportError:
            pass
        self._searched[ctx.run_id] = True
        self._searched.move_to_end(ctx.run_id)
        _trim(self._searched)
        return None

    async def on_before_final_answer(self, ctx: BeforeFinalAnswerContext) -> HookResult | None:
        if _profile_of(ctx) != self.profile:
            return None
        session_id = _session_id_of(ctx)
        if get_session_real_code_evidence(session_id):
            return None  # real fetched PoC code already confirmed — T1 applies regardless
        if get_session_kb_searched(session_id):
            return None  # deterministic prefetch already supplied usable internal KB context

        run_id = ctx.run_id
        if run_id in self._searched:
            self._searched.move_to_end(run_id)
            return None

        attempts = self._attempts.get(run_id, 0)
        if attempts >= self.max_retries:
            return HookResult.mutate(
                final_text=_EXPLOIT_KB_SEARCH_EXHAUSTED,
                reason="kb_search_before_synthesis_exhausted",
            )
        self._attempts[run_id] = attempts + 1
        self._attempts.move_to_end(run_id)
        _trim(self._attempts)
        return HookResult.force_continue(
            "Before answering, call qdrant_search (and neo4j_search if useful) to check "
            "whether this CVE/CWE exists in the internal KB.",
            reason="kb_search_before_synthesis_unsatisfied",
        )


class RequireSection1SynthesisHook:
    """When evidence justifies synthesizing Section 1 (T2/T3 — see poc/inject.py::
    enforce_section1) but the model's candidate answer never wrote one, force a retry
    asking it to, up to `max_retries` — the same pattern `RequireRunInstructionsHook`
    already uses for a missing "Cách chạy PoC" section.

    Without this, `enforce_section1` fails closed to `synthesis_failed` (a correct but
    unhelpful "không tổng hợp được" answer) the very first time the model skips Section 1
    — even when it had everything needed (a confirmed CVE or catalog description) to write
    one. Observed live: a plain re-ask consistently produces a synthesized Section 1 on
    retry, so the failure was reticence, not incapability.

    Skipped entirely when prefetch found REAL fetched PoC code (Section 1 will be the
    deterministic real-code tier regardless of what the model writes, so forcing a
    synthesis attempt would be pure waste) or when the model's own run already surfaced
    real code itself.

    NOTE: this is deliberately NOT `get_session_coverage(session_id) == "enough"` — that
    predicate only means "prefetch fetched *something*" (see prefetch.py's `coverage`
    docstring) and is equally true for a metadata-only ghsa/README fetch, which is NOT T1.
    Using it here used to let a description-only CVE silently skip the forced retry and
    fall straight to `synthesis_failed` the first time the model was reticent — see
    `get_session_real_code_evidence`, the precise T1 signal this needs.

    Also checks Mục 2/3 are present once Mục 1 exists, not just Mục 1 alone — observed
    live (twice, same failure mode both times: CVE-2025-40639 and CVE-2026-41265) that the
    model sometimes writes a correct, banner-wrapped Mục 1 then jumps straight to the
    footer, skipping "## 2. Cách chạy PoC" and "## 3. Tổng quan Lỗ hổng" entirely even
    though `exploit_output_contract` requires all three for this tier. Nothing else in the
    hook chain catches this: `RequireRunInstructionsHook` only fires on run-info evidence
    (a README file or CLI-flag signal in real source code), which a pure GHSA-description
    synthesis never has.

    T1 (real fetched code) skips the "must write Section 1" requirement (Section 1 is the
    deterministic real-code tier regardless of what the model writes — forcing a synthesis
    attempt would be pure waste) but is NOT exempt from the Mục 2/3 completeness check:
    those two sections are still the model's own text in T1 (only Mục 1 is system-injected),
    and exploit_output_contract requires them there too ("Phần văn bản phải bắt đầu ngay
    bằng `## 2. Cách chạy PoC`... Chỉ xuất đúng hai phần"). Observed via eval suite
    (evals/, 2026-08-23): ~8% of exploit_research T1/real_consolidated answers stopped
    right after Mục 2, dropping Mục 3 entirely — the exact same reticence-to-continue
    failure mode as the T2/T3 case above, just previously unchecked because this hook
    returned early before ever looking at the text for T1.

    Retry feedback is phrased as "write the complete required content again", never
    "keep what you already wrote" — `linch`'s `force_continue` (loop/terminals.py's
    `_gate_retry_tail`) appends the feedback as a new user turn and keeps the REJECTED
    draft in the session history for the model to see, but the run's final delivered
    text is only ever the model's LATEST completion (not a merge across retry turns).
    A "just add what's missing, keep the rest as-is" instruction was tried first and
    measurably backfired: the model took it literally, emitted only the delta on retry,
    and the previously-written section silently vanished from the delivered answer (a
    second eval pass, same 2026-08-23 session, showed all 8 T1 retries landing exactly
    on "Mục 2 now missing, Mục 3 now present" — the inverse of the original gap, not a
    fix of it). The retry instruction must ask for the full section set every time."""

    name = "require_section1_synthesis"

    def __init__(self, ledger: Any, profile: str, max_retries: int = 2) -> None:
        self.ledger = ledger
        self.profile = profile
        self.max_retries = max_retries
        self._attempts: OrderedDict[str, int] = OrderedDict()

    async def on_before_final_answer(self, ctx: BeforeFinalAnswerContext) -> HookResult | None:
        if _profile_of(ctx) != self.profile:
            return None
        session_id = _session_id_of(ctx)
        run_evidence = self.ledger.evidence_for_run(ctx.run_id)
        text = ctx.final_text or ""
        is_t1 = get_session_real_code_evidence(session_id) or has_real_code_evidence(run_evidence)

        if is_t1:
            # Mục 1 needs no check (system-injected regardless of what the model wrote) —
            # only Mục 2/3, which the model still writes itself in this tier too.
            has_s1 = False
            missing = [n for n, rx in ((2, _H2), (3, _H3)) if not rx.search(text)]
            if not missing:
                return None
        else:
            # `run_evidence` only covers what the MODEL itself fetched this turn — the
            # deterministic prefetch (routes.py's `exploit_prefetch`) runs concurrently under
            # its own scratch run_id and is invisible to `ledger.evidence_for_run(ctx.run_id)`,
            # so a session-level flag (set from prefetch's own evidence) is the only way this
            # hook can see it. Without it, this exact retry silently never fires whenever
            # prefetch is the ONLY reason T2 evidence exists (the model's own poc_search/fetch
            # came up empty) — which was observed live turning a legitimate T2 case into a
            # bare "synthesis_failed" the first time, every time.
            if not (has_synthesis_evidence(run_evidence) or get_session_synthesis_evidence(session_id)
                    or get_session_cve_known(session_id)):
                return None  # genuinely nothing to ground on — T4 is the correct, honest answer
            has_s1 = section1_has_body(text)
            missing = [n for n, rx in ((2, _H2), (3, _H3)) if has_s1 and not rx.search(text)]
            if has_s1 and not missing:
                return None

        attempts = self._attempts.get(ctx.run_id, 0)
        if attempts >= self.max_retries:
            return None
        self._attempts[ctx.run_id] = attempts + 1
        self._attempts.move_to_end(ctx.run_id)
        _trim(self._attempts)
        if not is_t1 and not has_s1:
            feedback = (
                "Evidence confirms this CVE/vulnerability exists (KB match or catalog "
                "description) even though no runnable PoC was found. Your previous reply is "
                "REPLACED by this one — write the COMPLETE answer again from scratch, this "
                "time including Section 1 (`## 1. Mã Khai thác (PoC) và Payload`, a minimal "
                "illustrative exploit grounded in that evidence, per section1_policy trường "
                "hợp 2) followed by `## 2. Cách chạy PoC` and `## 3. Tổng quan Lỗ hổng`. "
                "Nothing from your previous reply carries over automatically — re-write every "
                "section you want in the final answer, not only Section 1.")
        elif is_t1:
            feedback = (
                "Your previous reply is REPLACED by this one — required sections are missing "
                "or the reply was cut short. Write the COMPLETE answer again from scratch: "
                "`## 2. Cách chạy PoC` followed by `## 3. Tổng quan Lỗ hổng`, both in full. "
                "Section 1 is injected separately by the system, so do not write it yourself. "
                "Nothing from your previous reply carries over automatically — if you keep "
                "content from it, you must retype it in this reply.")
        else:
            feedback = (
                "Your previous reply is REPLACED by this one — required sections are missing "
                "or the reply was cut short. Write the COMPLETE answer again from scratch, in "
                "order: `## 1. Mã Khai thác (PoC) và Payload`, `## 2. Cách chạy PoC`, "
                "`## 3. Tổng quan Lỗ hổng` — all three, in full. Nothing from your previous "
                "reply carries over automatically — if you keep content from it (including "
                "Section 1), you must retype it in this reply.")
        return HookResult.force_continue(feedback, reason="section1_synthesis_missing")


_RUN_INFO_SIGNAL = re.compile(r"argparse|optparse|ArgumentParser|click\.command|usage:|--help", re.I)
_RUN_SECTION_HEADING = re.compile(r"cách chạy", re.I)


class RequireRunInstructionsHook:
    """Require a run-instructions section when the run gathered run-info evidence."""

    name = "require_run_instructions"

    def __init__(self, ledger: Any, profile: str, max_retries: int = 2) -> None:
        self.ledger = ledger
        self.profile = profile
        self.max_retries = max_retries
        self._attempts: OrderedDict[str, int] = OrderedDict()

    def _has_run_info_evidence(self, run_id: str) -> bool:
        for evidence in self.ledger.evidence_for_run(run_id):
            source_files = evidence.get("source_files", [])
            if isinstance(source_files, list) and any(
                isinstance(file, dict) and file.get("role") == "readme" for file in source_files
            ):
                return True
            source_code = evidence.get("source_code", "")
            if isinstance(source_code, str) and _RUN_INFO_SIGNAL.search(source_code):
                return True
        return False

    async def on_before_final_answer(self, ctx: BeforeFinalAnswerContext) -> HookResult | None:
        if _profile_of(ctx) != self.profile or not self._has_run_info_evidence(ctx.run_id):
            return None
        # Run-info evidence alone doesn't mean Section 1 will end up with a PoC to run —
        # that's decided by enforce_section1's tiers (poc/inject.py). If there's no real
        # fetched code AND the model hasn't actually written a Section-1 body this turn,
        # Section 1 will fall through to synthesis_failed/not_found and there is no local
        # PoC file to reference. Forcing a run-instructions section in that case makes the
        # model fabricate a command for a file that will never exist (see
        # RequireSection1SynthesisHook, which gates the same way).
        run_evidence = self.ledger.evidence_for_run(ctx.run_id)
        if not (has_real_code_evidence(run_evidence) or section1_has_body(ctx.final_text or "")):
            return None
        if _RUN_SECTION_HEADING.search(ctx.final_text or ""):
            return None

        attempts = self._attempts.get(ctx.run_id, 0)
        if attempts >= self.max_retries:
            return None
        self._attempts[ctx.run_id] = attempts + 1
        self._attempts.move_to_end(ctx.run_id)
        _trim(self._attempts)
        return HookResult.force_continue(
            "Evidence includes run information. Add a 'Cách chạy PoC' section with the "
            "supported commands and prerequisites before answering.",
            reason="run_instructions_missing",
        )


# ── Chat retrieval routing (KB vs parametric vs web) ─────────────────────────
# KB (Qdrant/Neo4j) has CVE/CWE/CAPEC/ATT&CK entity docs — NOT every textbook concept.
# Yearly lists (OWASP/CWE Top) live on the public web, not as a stable KB snapshot.
# Pure concepts (SQLi login, XSS validation, JWT pitfalls, Docker hardening) may be
# answered from model knowledge; inventing CVE/CWE IDs or Top-N rankings is forbidden.

_TAXONOMY_RE = re.compile(
    r"\bOWASP\s*Top(?:\s*(?:\d+|Ten))?\b"
    r"|\bCWE\s*Top(?:\s*\d+)?\b"
    r"|\bTop\s*(?:\d+|Ten)\s+(?:OWASP|CWE)\b"
    r"|\bMITRE\b.{0,40}\bTop\s*\d+\b"
    r"|\bTop\s*\d+\b.{0,40}\bMITRE\b"
    r"|\bTop\s*25\b.{0,60}(?:điểm yếu|weakness)",
    re.I | re.S,
)
# Looser than _TAXONOMY_RE (which gates web fetching and must not move): used ONLY on the knowledge path
# to recognise "Top 10 rủi ro OWASP", "danh sách CWE Top 25 mới nhất", … and serve the whole versioned list.
_TAXONOMY_LIST_RE = re.compile(
    r"owasp.{0,24}top\s*(?:10|ten)|top\s*(?:10|ten).{0,24}owasp|(?:cwe|mitre).{0,24}top\s*25|top\s*25.{0,40}(?:cwe|weakness|điểm yếu|mitre)",
    re.I | re.S,
)
_ENTITY_ID_RE = re.compile(r"\b(?:CVE|CWE|CAPEC)-\d{1,7}(?:-\d+)?\b|\bT\d{4}(?:\.\d{3})?\b", re.I)
_CVE_FAMILY_RE = re.compile(r"\bCVEs?\b", re.I)
_ID_LOOKUP_RE = re.compile(
    r"(?:"
    r"mã\s+(?:CWE|CVE)"
    r"|(?:CWE|CVE)\s*(?:nào|gì)"
    r"|(?:CWE|CVE).{0,24}mã\s+nào"
    r"|mã\s+nào.{0,24}(?:CWE|CVE)"
    r"|which\s+(?:CWE|CVE)"
    r"|(?:CVE|CWE)\s+liên\s*quan"
    r"|liên\s*quan\s+(?:đến\s+)?(?:các\s+)?(?:CVE|CWE)"
    r"|CVE\s+nào"
    r"|CWE\s+nào"
    r"|thuộc\s+CWE\s+nào"
    r")",
    re.I | re.S,
)


# IAS product user-guide questions (route "product_howto", KB target ias_howto_kb —
# see retrieval/howto.py). Deliberately checked AFTER has_entity/id_lookup below so a
# question that also names a CVE/CWE id still goes to the security KB; this only
# catches generic "how do I use feature X" questions that would otherwise fall into
# the "concept" route and get no KB grounding at all (model answers from parametric
# knowledge, can fabricate steps that don't match the real product).
_IAS_HOWTO_RE = re.compile(
    r"(?:"
    r"(?:cách|làm\s*sao|làm\s*thế\s*nào|làm\s*gì)\s+(?:để\s+)?"
    r"(?:dùng|sử\s*dụng|thao\s*tác|thiết\s*lập|cấu\s*hình|tạo|thêm|xóa|sửa|đổi|cấp\s*lại|"
    r"ngưng|tạm\s*dừng|bật|tắt)"
    r"|hướng\s*dẫn\s+(?:sử\s*dụng|dùng|thao\s*tác)"
    r"|thao\s*tác\s+(?:trên|với|ở)"
    r"|màn\s*hình\s+(?:nào|gì)"
    r"|giao\s*diện\s+(?:nào|phần\s*mềm|IAS)"
    r"|chức\s*năng\s+(?:này|nào|gì)\s+(?:dùng|ở\s*đâu|nằm\s*ở)"
    r")",
    re.I | re.S,
)

# Multi-word Vietnamese names of IAS modules from the user-guide ToC — specific enough that
# they don't collide with ordinary English-language CVE/security prose, so they are safe to
# match standalone (no context qualifier required).
_IAS_HOWTO_SAFE_KEYWORDS_RE = re.compile(
    r"hệ\s*thống\s*ẩn\s*danh|quét\s*diện\s*rộng|quét\s*chuyên\s*sâu|sinh\s*mã\s*khai\s*thác|"
    r"phân\s*hệ\s*báo\s*cáo|phân\s*hệ\s*quản\s*lý\s*lịch|đối\s*tượng\s*theo\s*dõi|"
    r"quản\s*lý\s*vai\s*trò|quản\s*lý\s*người\s*dùng|tài\s*khoản\s*lộ\s*lọt|"
    r"trợ\s*lý\s*ảo\s*ATTT|nhận\s*thông\s*báo\s*qua\s*telegram",
    re.I | re.S,
)

# Generic terms ("dashboard", "2FA", "data lake", …) that are common vocabulary in genuine
# security/CVE prose too (e.g. "Kubernetes dashboard RCE", "bypass 2FA trong GitLab") — on
# their own these caused real misrouting (see code-review 2026-11-06). Only treated as an IAS
# howto signal when ALSO qualified by an explicit product/software context word nearby.
_IAS_HOWTO_GENERIC_KEYWORDS_RE = re.compile(
    r"scan\s*engine|engine\s*quét|nền\s*tảng\s*AI|dịch\s*văn\s*bản|data\s*lake|dashboard|"
    r"xác\s*thực\s*hai\s*yếu\s*tố|2FA|đổi\s*mật\s*khẩu|cấp\s*lại\s*mật\s*khẩu",
    re.I | re.S,
)
_IAS_CONTEXT_QUALIFIER_RE = re.compile(
    r"\bIAS\b|hệ\s*thống|phần\s*mềm|module|phân\s*hệ|tính\s*năng|màn\s*hình|giao\s*diện",
    re.I | re.S,
)


def _is_ias_howto_question(text: str) -> bool:
    """True when ``text`` is a genuine IAS product how-to question.

    Split out of a single big regex (see code-review 2026-11-06) because bare generic
    keywords like "dashboard"/"2FA"/"data lake" are also ordinary security vocabulary —
    matching them anywhere misrouted real CVE/security questions away from cve_kb_hybrid.
    Generic keywords now require a nearby IAS/product qualifier word to count."""
    if _IAS_HOWTO_RE.search(text) or _IAS_HOWTO_SAFE_KEYWORDS_RE.search(text):
        return True
    if _IAS_HOWTO_GENERIC_KEYWORDS_RE.search(text) and _IAS_CONTEXT_QUALIFIER_RE.search(text):
        return True
    return False


_CODE_AUDIT_RE = re.compile(
    r"(?:"
    r"---\s*Nội dung tệp"
    r"|rà\s*soát\s+(?:tệp\s+)?mã(?:\s*nguồn)?"
    r"|kiểm\s*tra\s+mã\s*nguồn"
    r"|phân\s*tích\s+mã\s*nguồn"
    r"|\bSAST\b"
    r"|audit\s+code"
    r"|code\s+review"
    r"|review\s+code"
    r"|source\s+code"
    r")",
    re.I,
)


# Knowledge-KB routing (docs/plans/kb-knowledge-grounding.md). Only consulted when the caller
# passes knowledge_enabled=True, so the flag-off behaviour of classify_chat_grounding is untouched.
# Concept/remediation questions carry no CVE/CWE id, so they are recognised by vocabulary: the
# security class or control being asked about, or an explicit "how do I fix/prevent" intent.
# All patterns run on diacritic-stripped, lower-cased text (retrieval.vi_en.fold), so "Mật khẩu",
# "mat khau" and "MẬT KHẨU" are one thing; Vietnamese terms below are written unaccented.
#
# STRONG: unambiguous security techniques/controls — enough on their own to want reference text.
_KN_STRONG_RE = re.compile(
    r"\b(?:sql\s*injection|sqli|xss|cross[- ]site|csrf|ssrf|xxe|rce|remote\s+code\s+execution|command\s+injection|"
    r"code\s+injection|ldap\s+injection|xpath\s+injection|nosql\s+injection|template\s+injection|ssti|prototype\s+pollution|"
    r"path\s+traversal|directory\s+traversal|deserializ\w*|idor|jwt|json\s+web\s+token|oauth\w*|saml|openid|clickjack\w*|"
    r"hsts|cors|csp|content[\s-]+security[\s-]+policy|mfa|2fa|zero\s+trust|threat\s+model\w*|stride|pentest\w*|penetration\s+test\w*|"
    r"ransomware|phishing|malware|buffer\s+overflow|owasp|asvs|capec|bcrypt|argon2|scrypt|pbkdf2|least\s+privilege|"
    r"defen[cs]e\s+in\s+depth|cia\s+triad|subdomain\s+takeover|secure\s+coding|hardening|mass\s+assignment|"
    r"session\s+(?:fixation|hijack\w*|management)|credential\s+stuffing|brute[- ]force|input\s+validation|"
    r"security\s+headers?|http\s+headers?|rbac|access\s+control|authenticat\w*|authoriz\w*|encrypt\w*|cryptograph\w*|"
    r"prompt\s+injection|denial[- ]of[- ]service|forgot(?:ten)?\s+password|password\s+(?:reset|storage|hashing)|"
    r"key\s+management|secrets?\s+management|supply\s+chain\s+(?:attack|security)|sbom|tls|ssl|insecure\s+\w+|"
    r"security\s+misconfiguration|broken\s+access|harden\w*|stor\w*\s+(?:\w+\s+)?passwords?|https|md5|sha[- ]?1|"
    r"secure\s+(?:defaults?|by\s+design|design)|security\s+(?:issues?|review|checklist|testing|audit|controls?|practices?))\b"
    r"|xac\s*thuc|phan\s*quyen|kiem\s*soat\s*truy\s*cap|ma\s*hoa\s*(?:du\s*lieu|mat\s*khau|file)|bam\s*mat\s*khau|quen\s*mat\s*khau|luu\s*(?:tru\s*)?mat\s*khau|chuoi\s*cung\s*ung|"
    r"quan\s*ly\s*khoa|quan\s*ly\s*(?:secrets?|bi\s*mat)|mo\s*hinh\s*de\s*doa|gia\s*mao\s*yeu\s*cau|chen\s*ma|"
    r"secure\s+sdlc|\bsdlc\b|\bsast\b|\bdast\b|devsecops|select\b.{0,60}\bwhere\b.{0,60}(?:['\"]\s*\+|\{\w+\}|%s)",
)
# AMBIGUOUS: everyday technical words ("docker compose up", "hash map", "log scale"): only count together
# with a security / how-to-protect intent word.
_KN_AMBIG_RE = re.compile(
    r"\b(?:passwords?|hash(?:ing)?|salt(?:ing)?|sessions?|cookies?|apis?|rest(?:ful)?\s+api|containers?|docker|kubernetes|k8s|tokens?|"
    r"ssh|logs?|logged|logging|upload\w*|validat\w*|dependenc\w*|secrets?|pipelines?|ci/cd|webhooks?|graphql|websockets?|dos|ddos|nosql|"
    r"credentials?|prompts?|template|github\s+actions|error\s+handling|firewall|waf|certificates?)\b"
    r"|mat\s*khau|ma\s*hoa|phien\s*(?:dang\s*nhap|lam\s*viec)|nhat\s*ky|dau\s*vao|tai\s*(?:len|file)|phu\s*thuoc|xu\s*ly\s*loi",
)
# INTENT: wants protection / risk / a secure way to do something. Plain "how do I …" is NOT security intent: with an
# ambiguous word it is an engineering question ("ssh vào server bằng key thế nào", "how do I rotate log files"),
# which measured 28 % false triggers on look-alike vocabulary when it counted.
_KN_INTENT_RE = re.compile(
    r"\b(?:secur\w*|safe(?:ly)?|protect\w*|prevent\w*|harden\w*|mitigat\w*|defen[cs]\w*|attack\w*|vulnerab\w*|exploit\w*|"
    r"risks?|threats?|best\s+practices?|sensitive|untrusted|leak\w*|expos\w*|mitigat\w*)\b"
    r"|bao\s*mat|an\s*toan|an\s*ninh|bao\s*ve|rui\s*ro|de\s*doa|tan\s*cong|lo\s*hong|khai\s*thac|khac\s*phuc|phong\s*(?:chong|tranh|ngua)|"
    r"ngan\s*(?:chan|ngua)|nhay\s*cam|giam\s*thieu|nguyen\s*tac|dung\s*cach|"
    r"khong\s*(?:duoc\s*)?tin|khong\s*lo|\blo\s+(?:thong|du)|stack\s*trace|bien\s*phap",
)
# Generic security words that also describe a CVE/alias question ("Lỗ hổng Heartbleed là gì?"):
# they only count together with a fix/prevent intent, otherwise the CVE KB is the right source.
_KN_GENERIC_RE = re.compile(
    r"\b(?:security|vulnerab\w*|exploit\w*|cve|cwe|mitre|risks?|safely|secure(?:ly)?)\b"
    r"|bao\s*mat|an\s*toan|an\s*ninh|lo\s*hong|khai\s*thac|tan\s*cong|rui\s*ro",
)
_REMEDIATION_RE = re.compile(
    r"khac\s*phuc|va\s*loi|ban\s*va|sua\s*loi|\bfix(?:es|ed|ing)?\b|\bpatch\w*|mitigat\w*|remediat\w*|workaround|"
    r"phong\s*(?:chong|tranh|ngua)|ngan\s*(?:chan|ngua)|\bprevent\w*|cach\s*xu\s*ly|bien\s*phap|giam\s*thieu|"
    r"harden\w*|how\s+to\s+secure|securely|best\s+practices?|defen[cs]e|cach\s*sua\b",
)
# "Lỗ hổng <vendor/product> …?" / "CVE nào của <vendor>": a CVE lookup, not a concept question.
_KN_CVE_PHRASE_RE = re.compile(r"^\s*(?:cac\s+)?(?:lo\s*hong|cve)\b|\bcve\s+nao\b|\bnhung\s+cve\b|\bcac\s+cve\b")
_PRODUCT_TOKEN_RE = re.compile(r"\b(?:[A-Z][a-z]{2,}\w*|[a-z]+[A-Z]\w+|[A-Z][a-z]+[A-Z]\w*|[A-Z]{2,}[a-z]+\w*|[A-Z]+-[A-Z0-9]+)\b")
_NOT_A_PRODUCT = frozenset({"How", "What", "Why", "When", "Where", "Which", "Who", "Explain", "Describe", "Does", "Should", "Could",
                            "Would", "Can", "Best", "The", "Are", "Is", "List", "Give", "Tell", "Show", "Please", "Compare", "Define"})
# Words that make a question about "a vulnerability / exploit / PoC" rather than a concept.
_KN_CVEWORD_RE = re.compile(r"\b(?:cve|vulnerab\w*|exploit\w*|poc|proof\s+of\s+concept)\b|lo\s*hong|khai\s*thac|ma\s*khai\s*thac")
# Questions about the assistant/system itself stay on the meta path (answered from <about_system>).
_META_RE = re.compile(
    r"ban\s+la\s+(?:ai|gi)|who\s+are\s+you|what\s+are\s+you|what\s+can\s+you\s+do|ban\s+(?:co\s+the|lam\s+duoc|biet\s+gi)|"
    r"\bias\.?ai\b|he\s*thong\s*(?:nay|cua\s+ban)|mo\s*hinh\s*(?:nao|gi|nen)|\bmodel\s+(?:nao|gi)|"
    r"du\s*lieu\s*(?:cua\s+)?(?:ban|he\s*thong)|cap\s*nhat\s*(?:den|toi|luc|khi\s+nao)|knowledge\s+base",
)


def _fold_text(raw: str) -> str:
    from ..retrieval.vi_en import fold

    return fold(raw)


def _has_product_token(raw: str) -> bool:
    return any(m.group(0) not in _NOT_A_PRODUCT for m in _PRODUCT_TOKEN_RE.finditer(raw or ""))


# "Which <product> bug lets …?", "CVE nào của …?", "Is there a CVE for …?": the asker does not know the id and
# describes the flaw. Matched on diacritic-folded text; only used when discovery routing is switched on.
_KN_DISCOVERY_RE = re.compile(
    r"\b(?:which|what)\b.{0,80}?\b(?:cves?|vulnerabilit\w*|flaws?|bugs?|issues?|zero-?days?|exploits?|type\s+confusion|"
    r"(?:buffer|heap|stack)\s+overflow|use-after-free|memory\s+corruption|(?:sql|command|code)\s+injection|"
    r"(?:auth\w*\s+)?bypass|path\s+traversal|privilege\s+escalation|deserialization|rce)\b"
    r"|\bis\s+there\s+(?:a|an|any)\b.{0,60}?\b(?:cve|vulnerab\w*|flaw|bug|issue)\b|\bany\s+cves?\b|\bcves?\s+(?:for|in|affecting)\b"
    r"|\b(?:show|list|find|search)\b.{0,40}?\b(?:cves?|vulnerabilit\w*|flaws?)\b|\b(?:tim|liet\s*ke)\s+(?:cac\s+|nhung\s+)?(?:cve|lo\s*hong)\b"
    r"|\b(?:cve|lo\s*hong|loi)\b.{0,60}?\bnao\b|\bco\s+(?:cve|lo\s*hong)\b|\b(?:nhung|cac)\s+(?:cve|lo\s*hong)\b"
)


# An explicit request for CVE ids ("liệt kê CVE giúp mình", "cho xin số CVE", "list CVEs for …") needs no capitalised product
# name: "runc", "nginx", "openssh" are products too.
_KN_CVE_REQUEST_RE = re.compile(
    r"\b(?:cho\s*(?:xin|minh|toi|em)?\s*(?:so\s*)?cves?|liet\s*ke\s+(?:cac\s+|nhung\s+)?cves?|"
    r"(?:list|give\s+me|show\s+me)\s+(?:me\s+)?(?:the\s+|all\s+)?cves?)\b"
)


def wants_remediation(raw: str) -> bool:
    """The question also asks how to fix / mitigate / harden (reference text belongs next to the CVE records)."""
    return bool(_REMEDIATION_RE.search(_fold_text(raw)))


def is_semantic_route_candidate(raw: str) -> bool:
    """A free-form question worth a semantic look at the knowledge KB when no keyword asked for it: long enough to carry
    a topic, and not a question about the assistant itself."""
    text = (raw or "").strip()
    return len(text.split()) >= 4 and not _META_RE.search(_fold_text(text)) and not _VAGUE_PROMPT_RE.match(text)


def _is_cve_discovery(raw: str) -> bool:
    """The user describes a flaw and wants the CVE(s): a KB search, not a concept answer. Questions that ask how to
    fix or prevent stay on the knowledge route."""
    t = _fold_text(raw)
    # "fixed in version 80.0.3987.122" names the patched release the asker remembers; it is not a how-to-fix question.
    remediation = _REMEDIATION_RE.search(re.sub(r"\b(?:fix(?:ed|es)|patch(?:ed|es))\s+(?:in|at|on)\b", " ", t))
    wants = (_has_product_token(raw) and bool(_KN_DISCOVERY_RE.search(t))) or bool(_KN_CVE_REQUEST_RE.search(t))
    return wants and not remediation and not _META_RE.search(t)


def _is_cve_style(raw: str) -> bool:
    """A question about a named vulnerability/product ("Lỗ hổng Log4Shell là gì?", "Heartbleed là lỗ hổng gì?",
    "Lỗ hổng Fortinet FortiOS path traversal?"): the CVE KB answers it, the model has to go and look."""
    if not _has_product_token(raw):
        return False
    t = _fold_text(raw)
    return bool(_KN_CVE_PHRASE_RE.search(t) or (_KN_CVEWORD_RE.search(t) and not _KN_STRONG_RE.search(t)))


def _wants_knowledge(raw: str) -> bool:
    """True for concept / how-to-fix questions that reference text can answer better."""
    from ..retrieval.vi_en import fold

    t = fold(raw)
    remediation = bool(_REMEDIATION_RE.search(t))
    if _is_cve_style(raw) and not remediation:
        return False
    if _KN_STRONG_RE.search(t):
        return True
    if _META_RE.search(t):
        return False
    if _KN_AMBIG_RE.search(t) and _KN_INTENT_RE.search(t):
        return True
    return bool(_KN_GENERIC_RE.search(t) and remediation)


@dataclass(frozen=True, slots=True)
class ChatGroundingPlan:
    """Deterministic route for one user prompt.

    ``knowledge`` asks the pre-fetch to ALSO consult the knowledge KB (cheat sheets / ASVS /
    mitigations). It never forces the model to call a tool (need_kb stays False for
    ``concept_kb``), so no enforcement hook can turn it into a retry loop."""

    route: str  # taxonomy | entity | id_lookup | product_howto | concept | concept_kb | cve_search
    need_web: bool
    need_kb: bool
    # "security" (cve_kb_hybrid via qdrant_search/neo4j_search) or "howto" (ias_howto_kb
    # via howto_search). Only "howto" ever skips the security KB/Neo4j entirely.
    kb_target: str = "security"
    knowledge: bool = False


def classify_chat_grounding(prompt: str, knowledge_enabled: bool = False,
                            cve_search_enabled: bool = False, discovery_enabled: bool = False) -> ChatGroundingPlan:
    """Decide which evidence tools chat must call before a final answer.

    ``knowledge_enabled`` / ``cve_search_enabled`` both False (default) reproduce the original routing
    exactly. ``cve_search`` is the route for open-ended CVE questions with no id ("Lỗ hổng Log4Shell là
    gì?", "Lỗ hổng Fortinet FortiOS path traversal?"): the server searches the KB with the question itself
    instead of hoping the model calls the tool (a measured 8/8 miss on alias questions)."""
    text = (prompt or "").strip()
    if not text:
        return ChatGroundingPlan("concept", False, False)

    # SAST / Code audit (rà soát mã nguồn, file đính kèm): trả lời bằng suy luận phân tích mã của mô hình, không ép tra Qdrant KB
    if bool(_CODE_AUDIT_RE.search(text)):
        return ChatGroundingPlan("concept", False, False)

    need_web = bool(_TAXONOMY_RE.search(text))
    has_entity = bool(_ENTITY_ID_RE.search(text))
    id_lookup = bool(_ID_LOOKUP_RE.search(text))
    cve_family = bool(_CVE_FAMILY_RE.search(text))

    if need_web:
        need_kb = has_entity or cve_family or id_lookup
        return ChatGroundingPlan("taxonomy", True, need_kb, knowledge=knowledge_enabled)
    if has_entity:
        return ChatGroundingPlan("entity", False, True, knowledge=knowledge_enabled and bool(_REMEDIATION_RE.search(_fold_text(text))))
    if id_lookup:
        return ChatGroundingPlan("id_lookup", False, True)
    if _is_ias_howto_question(text):
        return ChatGroundingPlan("product_howto", False, True, kb_target="howto")
    if knowledge_enabled and _TAXONOMY_LIST_RE.search(text):
        return ChatGroundingPlan("taxonomy", False, False, knowledge=True)
    if cve_search_enabled and (_is_cve_style(text) or (discovery_enabled and _is_cve_discovery(text))):
        return ChatGroundingPlan("cve_search", False, False, knowledge=knowledge_enabled and bool(_REMEDIATION_RE.search(_fold_text(text))))
    if knowledge_enabled and _wants_knowledge(text):
        return ChatGroundingPlan("concept_kb", False, False, knowledge=True)
    return ChatGroundingPlan("concept", False, False)


# ── Deterministic chat-final guarantees (anti-empty / meta / aggregate) ──────
# These REPLACE the model's final text via HookResult.mutate(final_text=...), which is
# NOT bounded by the before-final reentry budget (linch's _MAX_FINAL_ANSWER_REENTRIES=1
# is usually already spent by RequireChatGroundingHook's own force_continue). They are the
# floor that keeps chat from ever shipping an empty/closer-only answer, a bare meta
# deflection, or a false "highest CVSS" claim — the R2 failure modes prompt rules alone
# did not stop (q022/q025/q026/q045/q046 empty; q006/q008/q011/q012 meta; q039 aggregate).

# The closer line the chat persona ends substantive answers with. A final whose only
# non-whitespace content is this line (or nothing at all) is the "empty answer" bug.
_CHAT_CLOSER = "Bạn cần IAS.AI hỗ trợ thêm gì nữa không?"

# Bare/vague single-topic prompts ("cve", "lỗ hổng", "poc" …) → ask a clarifying question
# instead of a generic not-found. Whole-string match so real questions are untouched.
_VAGUE_PROMPT_RE = re.compile(
    r"^\W*(cve|cwe|lỗ\s*hổng|lô\s*hông|vuln(?:erability)?|exploit|poc|payload|"
    r"bảo\s*mật|an\s*toàn|giúp|help|hi|hello)\W*$",
    re.I,
)

# Meta prompt families whose canonical answer already lives in chat.md::about_system.
_META_UPDATE_RE = re.compile(
    r"cập\s*nhật.{0,25}(khi\s*nào|đến|tới|bao\s*giờ|ngày\s*nào)"
    r"|(từ\s*)?năm\s*nào.{0,25}(tới|đến|nay|hiện)"
    r"|dữ\s*liệu.{0,20}(từ\s*)?năm\s*nào"
    r"|độ\s*phủ|phạm\s*vi\s*(của\s*)?dữ\s*liệu"
    r"|(bao\s*nhiêu|mấy)\s*năm",
    re.I | re.S,
)
_META_MODEL_RE = re.compile(
    r"(mô\s*hình|model|llm|kiến\s*trúc|công\s*nghệ\s*nền|nền\s*tảng|nhà\s*cung\s*cấp)"
    r".{0,30}(nào|gì|chạy|dùng|sử\s*dụng)"
    r"|(chạy|vận\s*hành)\s*(trên|bằng).{0,20}(mô\s*hình|model|gì|nào|ai\b)"
    r"|bạn\s*(là|dùng|xài)\s*(con\s*)?(ai|gpt|model|mô\s*hình)",
    re.I | re.S,
)
_META_PRIVACY_RE = re.compile(
    r"(dữ\s*liệu|thông\s*tin|nội\s*dung).{0,40}(nhập|gõ|gửi|cung\s*cấp|tôi).{0,40}"
    r"(lưu|giữ|thu\s*thập|huấn\s*luyện|train|riêng\s*tư|chia\s*sẻ)"
    r"|(bị\s*)?(lưu|lưu\s*trữ|lưu\s*lại|ghi\s*lại)\b.{0,20}(không|chứ|lại)"
    r"|quyền\s*riêng\s*tư",
    re.I | re.S,
)
# Aggregate superlative narrowed to CVSS wording only, so year/count superlatives
# (q037 count, q038 newest/oldest — already correct) are NOT intercepted.
_AGG_CVSS_SUPERLATIVE_RE = re.compile(
    r"(điểm\s*)?cvss.{0,25}(cao|thấp)\s*nhất"
    r"|(cao|thấp)\s*nhất.{0,25}(điểm\s*)?cvss",
    re.I | re.S,
)
# Deflection/refusal markers the model emits when it fails to answer from about_system.
_DEFLECTION_RE = re.compile(
    r"không\s*có\s*thông\s*tin\s*xác\s*thực"
    r"|không\s*thuộc\s*phạm\s*vi"
    r"|không\s*tìm\s*thấy\s*thông\s*tin",
    re.I,
)
# Observed in production (2026-11-10, "CVE nào mới nhất" / a howto question with 0 KB hits):
# asked an aggregate/no-match question, the model invented a plausible-sounding "kết nối
# đang lỗi" excuse instead of reporting what the tool actually returned — a clean 200
# response, either real hits or an honest "Không tìm thấy..." string (see
# retrieval/qdrant.py / retrieval/howto.py's own empty-result ToolResult text). Verified
# against the gateway's own httpx logs for both incidents: qdrant_search and howto_search
# both logged 200 OK at the exact timestamp the model claimed a connection failure. The
# claim is never grounded in a real tool outcome — check it against ctx.result.is_error
# (set to True ONLY by linch's own exception/timeout wrapper, scheduler.py::_execute_one),
# not by trusting the model's self-report.
_FALSE_INFRA_ERROR_RE = re.compile(
    r"lỗi\s*kết\s*nối"
    r"|kết\s*nối.{0,20}(đang\s*)?(bị\s*)?(lỗi|gián\s*đoạn|thất\s*bại|không\s*ổn\s*định)"
    r"|mất\s*kết\s*nối"
    r"|không\s*kết\s*nối\s*được"
    r"|connection\s*(error|failed|timed?\s*out|refused)"
    r"|(server|hệ\s*thống|dịch\s*vụ)\s*(đang\s*)?(bị\s*)?lỗi\s*(tạm\s*thời)?",
    re.I,
)

_META_UPDATE_ANSWER = (
    "Dữ liệu được tổng hợp từ các nguồn công khai (NVD, MITRE, CAPEC, CISA KEV và catalog "
    "PoC). Các lỗ hổng CVE trải dài từ khoảng **năm 1999 đến hiện tại** và được **cập nhật "
    "hàng đêm**. Hệ thống chưa có công cụ đếm/thống kê chính xác tổng số bản ghi — nếu cần "
    "con số chính thức, bạn có thể tham khảo NVD (nvd.nist.gov).\n\n" + _CHAT_CLOSER
)
_META_MODEL_ANSWER = (
    "IAS.AI là trợ lý An toàn thông tin, phục vụ tra cứu và giải thích CVE/CWE/CAPEC/MITRE "
    "ATT&CK, thông tin PoC/mã khai thác và tư vấn khắc phục. Chi tiết về mô hình nền, hạ "
    "tầng hay nhà cung cấp không được công bố. Nếu bạn cần hỗ trợ tra cứu bảo mật cụ thể, "
    "tôi có thể giúp ngay.\n\n" + _CHAT_CLOSER
)
_META_PRIVACY_ANSWER = (
    "Nội dung bạn nhập được dùng để xử lý và trả lời truy vấn trong phiên hiện tại. Dữ liệu "
    "này KHÔNG tự động dùng để huấn luyện lại mô hình và không chia sẻ cho bên thứ ba vì mục "
    "đích không liên quan. Chính sách ghi nhật ký và thời hạn lưu trữ chi tiết do đơn vị vận "
    "hành quy định — nếu cần cam kết chính thức, bạn vui lòng liên hệ quản trị hệ thống.\n\n"
    + _CHAT_CLOSER
)
_AGG_CVSS_ANSWER = (
    "Hệ thống hiện **chưa có công cụ thống kê/xếp hạng toàn bộ kho** để xác định chính xác "
    "CVE có điểm CVSS cao nhất, nên tôi không thể kết luận một CVE cụ thể là 'cao nhất' từ "
    "một mẻ tra cứu mẫu (mẫu không đại diện cho toàn bộ dữ liệu). Trên thực tế kho chứa "
    "nhiều CVE ở mức tối đa **CVSS 10.0** — ví dụ Log4Shell (CVE-2021-44228), Zerologon "
    "(CVE-2020-1472) và PAN-OS (CVE-2024-3400). Để xếp hạng chính xác, bạn nên tham khảo NVD "
    "(nvd.nist.gov) với bộ lọc theo điểm CVSS.\n\n" + _CHAT_CLOSER
)
_CHAT_EMPTY_FALLBACK = (
    "Xin lỗi, tôi chưa tổng hợp được câu trả lời từ dữ liệu nội bộ cho yêu cầu này. Bạn thử "
    "nêu lại cụ thể hơn — ví dụ một mã CVE/CWE (như `CVE-2021-44228`, `CWE-89`), tên sản "
    "phẩm, hoặc một khái niệm ATTT — để tôi tra cứu lại nhé.\n\n" + _CHAT_CLOSER
)
_CHAT_FALSE_INFRA_ERROR_ANSWER = (
    "Tôi đã tra cứu nhưng không tìm thấy nội dung phù hợp cho câu hỏi này trong dữ liệu nội bộ — "
    "đây KHÔNG phải do lỗi kết nối hay lỗi hệ thống. Bạn thử nêu cụ thể hơn — ví dụ một mã "
    "CVE/CWE, tên màn hình/chức năng cụ thể, hoặc từ khóa khác — để tôi tra lại nhé.\n\n"
    + _CHAT_CLOSER
)
_CHAT_VAGUE_CLARIFY = (
    "Bạn muốn tra cứu về vấn đề gì cụ thể? Ví dụ: một mã CVE (`CVE-2021-44228`), một loại "
    "lỗ hổng/khái niệm (SQLi, SSRF, RCE), một mã CWE (`CWE-89`), hay lỗ hổng của một sản "
    "phẩm cụ thể. Bạn nêu rõ hơn giúp tôi nhé.\n\n" + _CHAT_CLOSER
)

# api/routes.py::_ground_chat_prompt() bắt đầu khối grounding này bằng marker khi pre-fetch
# KB trực tiếp (gọi _qdrant_search thẳng, không qua tool-call của model). Dùng để coi KB đã
# thỏa mãn — nếu không, has_kb chỉ bật qua on_post_tool_use (tool_result thật), pre-fetch
# không đi qua đường đó nên hook cứ tưởng "chưa tra" và ép force_continue 2-3 lần một cách
# lãng phí (phát hiện live 2026-09-21: UI "giật" giữa chừng generation vì client phải xóa
# buffer theo regenerate event) — tệ hơn nữa, hết max_retries thì hook SHIP
# _CHAT_EMPTY_FALLBACK đè lên câu trả lời pre-fetch vốn đã đúng.
#
# 2026-09-21 (chat-history-leak fix): TRƯỚC ĐÂY, cả khối này (marker+KB dump) bị chèn thẳng
# vào chuỗi truyền cho session.run() rồi lưu NGUYÊN VĂN vào session store — đây chính là bug
# rò rỉ prompt nội bộ khi user quay lại chatbot (linch persist đúng những gì session.run()
# nhận). Đã chuyển sang kênh ephemeral (EphemeralTurnContextHook, session.meta
# ["ephemeral_context"]) — KHÔNG còn nằm trong chuỗi persist nữa, nên ``session.meta
# ["last_user_prompt"]`` giờ LUÔN là prompt gốc sạch của user, không còn bắt đầu bằng marker
# này. RequireChatGroundingHook.on_before_final_answer vì vậy không còn sniff marker qua
# ``prompt.startswith(...)`` — dùng cờ tường minh ``session.meta["chat_prefetch_grounded"]``
# (đặt bởi agent/runner.py::_apply_profile) thay thế, cùng ý nghĩa "KB đã thỏa mãn qua
# pre-fetch" nhưng không phụ thuộc nội dung prompt nữa.
CHAT_PRE_FETCH_MARKER = "[Ngữ cảnh tra cứu từ KB nội bộ"


def _chat_body_is_empty(text: str) -> bool:
    """True when the final has no substantive body — empty, whitespace, or ONLY the closer
    line. This is the exact R2 empty-answer bug (the model emitted just the boilerplate
    closer). Does NOT treat a real greeting or a legitimate not-found sentence as empty."""
    stripped = (text or "").strip()
    if not stripped:
        return True
    return not stripped.replace(_CHAT_CLOSER, "").strip()


def _chat_final_guarantee(prompt: str, text: str, *, any_tool_called: bool = False,
                          had_real_tool_error: bool = False) -> HookResult | None:
    """Deterministic floor for a chat final answer. Returns a mutate-HookResult that
    REPLACES the text, or None to leave the model's answer as-is. Checked before the
    grounding enforcement so an empty/deflecting answer can never slip through.

    ``any_tool_called``/``had_real_tool_error`` ground the TOP-0 check below against what
    actually happened this turn (linch's own exception/timeout wrapper sets
    ``ToolResult.is_error``, not the model) — both default False so every OTHER caller of
    this function (there is exactly one today, but the signature is public within the
    module) keeps its prior behavior unchanged if it doesn't pass them.
    """
    p = (prompt or "").strip()
    body_empty = _chat_body_is_empty(text)
    deflected = body_empty or bool(_DEFLECTION_RE.search(text or ""))

    # (TOP-0) False infra-error claim — the model said "lỗi kết nối" (or equivalent) for a
    # turn where a tool actually ran and none of them failed. Whatever it found (hits or an
    # honest 0-hits message) is not a connection problem; ship the honest no-match answer
    # instead of an invented excuse. Checked first and unconditionally (not gated on
    # `deflected`): the false claim can ride along with otherwise-plausible prose, same as
    # the production incidents that motivated this (see _FALSE_INFRA_ERROR_RE comment).
    if any_tool_called and not had_real_tool_error and _FALSE_INFRA_ERROR_RE.search(text or ""):
        return HookResult.mutate(final_text=_CHAT_FALSE_INFRA_ERROR_ANSWER,
                                 reason="chat_false_infra_error_claim")

    # (TOP-3) Aggregate CVSS-superlative — no whole-KB stats tool exists, so any specific
    # "highest/lowest CVSS" claim from a retrieval sample is unreliable → answer honestly.
    if _AGG_CVSS_SUPERLATIVE_RE.search(p):
        return HookResult.mutate(final_text=_AGG_CVSS_ANSWER, reason="chat_agg_cvss_superlative")

    # (TOP-2) Meta direct-answer repair — canonical answers live in about_system; when the
    # small model deflects/refuses (or goes empty) on them, ship the canonical text.
    if deflected:
        if _META_UPDATE_RE.search(p):
            return HookResult.mutate(final_text=_META_UPDATE_ANSWER, reason="chat_meta_update")
        if _META_MODEL_RE.search(p):
            return HookResult.mutate(final_text=_META_MODEL_ANSWER, reason="chat_meta_model")
        if _META_PRIVACY_RE.search(p):
            return HookResult.mutate(final_text=_META_PRIVACY_ANSWER, reason="chat_meta_privacy")

    # (TOP-1) Anti-empty guarantee — never ship an empty / closer-only final.
    if body_empty:
        if _VAGUE_PROMPT_RE.search(p):
            return HookResult.mutate(final_text=_CHAT_VAGUE_CLARIFY, reason="chat_vague_clarify")
        return HookResult.mutate(final_text=_CHAT_EMPTY_FALLBACK, reason="chat_empty_fallback")
    return None


def chat_final_text(prompt: str, text: str) -> str:
    """Text-returning twin of ``_chat_final_guarantee`` — a redundant, unconditional backstop
    applied at the value layer after the run loop returns.

    CORRECTION (2026-09-26): this docstring used to claim ``RequireChatGroundingHook.
    on_before_final_answer`` (which ``_chat_final_guarantee`` lives on) is never dispatched by
    linch in production ("INERT"), based on a 2026-09 stderr probe that never printed. That was
    a misdiagnosis, not a fact about linch — the max_retries/"phantom regenerate" investigation
    (agent/hooks.py::RequireChatGroundingHook.__init__ docstring, same date) proves the opposite:
    ``on_before_final_answer`` DOES run in production and its ``force_continue`` DOES reach
    ``dispatch_before_final_answer``/``loop/finalize.py`` (that's the exact mechanism the
    max_retries bug lived in — a hook call that doesn't fire can't emit an SSE ``regenerate``
    frame, and live probes counted 2 of them for one Top-N question). Whatever caused the
    earlier probe to stay silent was specific to that probe (placement, stderr capture under
    the container's logging setup, etc.), not evidence the hook itself is dead code.
    ``chat_final_text`` is kept anyway — not because the hook is inert, but as a deliberate
    second, independent layer: it reuses the exact same constants/regex as
    ``_chat_final_guarantee`` and applies them to the run's FINAL text value regardless of
    whether the hook's own ``mutate`` already fired, so the anti-empty/meta/aggregate floor
    holds even if the hook chain, profile wiring, or dispatch behavior ever changes. It returns
    the deterministic replacement string for an empty/closer-only answer, a meta deflection, or
    a false CVSS-superlative claim — otherwise it returns ``text`` unchanged. The ONLY thing it
    changes is the answer TEXT value; the caller's ``(final, metadata)`` return shape, the
    metadata dict and every schema/SSE contract are untouched.

    Kept as a standalone ladder (not routed through ``_chat_final_guarantee``) so it never
    depends on ``HookResult``'s internal shape to recover the replacement text, and so the
    same source of truth for the answers/regex constants is shared with no data duplication.
    Decision order matches the hook: aggregate CVSS-superlative (unconditional) → meta
    canonical repair (only when the model deflected/emptied) → anti-empty fallback/clarify.

    Deliberately does NOT replicate ``_chat_final_guarantee``'s TOP-0 false-infra-error-claim
    check: that check needs per-run tool-call telemetry (``any_tool_called``/
    ``had_real_tool_error``), which this value-layer function has no access to by design (see
    "no data duplication" above — it only ever sees prompt/text). The hook layer is this
    check's only line of defense; acceptable because the "hook chain is inert" concern this
    backstop exists for was a misdiagnosis (see the CORRECTION note above) and
    RequireChatGroundingHook.on_before_final_answer is confirmed to run in production."""
    p = (prompt or "").strip()
    body_empty = _chat_body_is_empty(text)
    deflected = body_empty or bool(_DEFLECTION_RE.search(text or ""))

    # (TOP-3) Aggregate CVSS-superlative — no whole-KB stats tool exists, so any specific
    # "highest/lowest CVSS" claim from a retrieval sample is unreliable → answer honestly.
    if _AGG_CVSS_SUPERLATIVE_RE.search(p):
        return _AGG_CVSS_ANSWER

    # (TOP-2) Meta direct-answer repair — canonical answers live in about_system; when the
    # small model deflects/refuses (or goes empty) on them, ship the canonical text.
    if deflected:
        if _META_UPDATE_RE.search(p):
            return _META_UPDATE_ANSWER
        if _META_MODEL_RE.search(p):
            return _META_MODEL_ANSWER
        if _META_PRIVACY_RE.search(p):
            return _META_PRIVACY_ANSWER

    # (TOP-1) Anti-empty guarantee — never ship an empty / closer-only final.
    if body_empty:
        if _VAGUE_PROMPT_RE.search(p):
            return _CHAT_VAGUE_CLARIFY
        return _CHAT_EMPTY_FALLBACK

    return text or ""


class RequireChatGroundingHook:
    """Steer + enforce chat tool use: web for Top-N, KB for entities/ID lookup.

    - Pre-tool: block ``web_fetch`` URLs outside the allowlist (SSRF/host policy).
    - Post-tool: only successful calls satisfy the requirement (errors do not count).
    - Before-final: force_continue until required tools ran (bounded retries).
    Concept questions (no ID / no Top-N / no "CVE nào") use parametric knowledge.

    ``max_retries`` bug (found 2026-09-26, "regenerate giả"): this hook used to default to
    3 — its OWN counter of how many times it's willing to ask for a redo — but linch's run
    loop caps how many of those requests it will actually HONOR at
    ``_MAX_FINAL_ANSWER_REENTRIES = 1`` (``linch/loop/runner.py``, module-level constant,
    not configurable via ``Agent(...)``; threaded into ``FinalizeCtx.max_final_answer_
    reentries`` and checked in ``loop/finalize.py``:
    ``if hook_action == "retry" and ctx.final_answer_reentries[0] < ctx.max_final_answer_
    reentries``). With 3 > 1, this hook's 2nd/3rd ``force_continue`` still fired — and
    ``dispatch_before_final_answer`` (``loop/dispatch.py``) still yields the
    ``HookEventRecord(action="force_continue")`` for it BEFORE finalize.py's reentry-budget
    check runs — but linch's own counter was already exhausted, so it silently shipped the
    STALE draft instead of actually re-running the model. ``api/sse.py::event_to_sse`` maps
    any ``HookEventRecord`` with ``action in {"force_continue", "retry"}`` straight to an
    SSE ``regenerate`` frame regardless of whether linch went on to honor it — so the client
    reset its buffer for a "regeneration" that never happened (verified live: a Top-N
    question got 2 ``regenerate`` frames, the 2nd one pointless — same stale draft came back).
    Setting the default to 1 caps this hook's own budget at the SAME number linch will
    actually re-run (exactly the one force_continue the loop honors) — beyond that, on
    attempt >= max_retries the ``on_before_final_answer`` branch below falls through to
    ``HookResult.mutate(final_text=_CHAT_EMPTY_FALLBACK, ...)`` instead of another
    ``force_continue``: ``mutate`` is NOT in ``_REGENERATE_ACTIONS`` (api/sse.py), so no
    phantom ``regenerate`` SSE frame is ever sent for it. Net effect: at most ONE real
    regenerate per run (the anti-fabrication guarantee is unchanged — chat still gets
    exactly one forced grounding attempt before falling back), zero fake ones.
    """

    name = "require_chat_grounding"

    def __init__(self, profile: str = "chat", max_retries: int = 1) -> None:
        self.profile = profile
        self.max_retries = max_retries
        self._web: OrderedDict[str, bool] = OrderedDict()
        self._kb: OrderedDict[str, bool] = OrderedDict()
        self._attempts: OrderedDict[str, int] = OrderedDict()
        # Any-tool / real-error trackers for the false-infra-error-claim guard in
        # _chat_final_guarantee (see that function's TOP-0 check). Separate from
        # _web/_kb, which only mark specific tool NAMES — this marks EVERY call
        # regardless of which tool, and whether linch's own exception/timeout wrapper
        # (scheduler.py::_execute_one) actually set is_error=True on it.
        self._any_call: OrderedDict[str, bool] = OrderedDict()
        self._tool_error: OrderedDict[str, bool] = OrderedDict()

    @staticmethod
    def _prompt_of(ctx: Any) -> str:
        session = getattr(ctx, "session", None)
        meta = getattr(session, "meta", None)
        if isinstance(meta, dict):
            return str(meta.get("last_user_prompt") or "")
        return ""

    async def on_pre_tool_use(self, ctx: PreToolUseContext) -> HookResult | None:
        if _profile_of(ctx) != self.profile:
            return None
        if ctx.tool_name not in ("web_fetch", "fetch_reference"):
            return None
        url = str((ctx.input or {}).get("url") or "") if isinstance(ctx.input, dict) else ""
        try:
            from ..retrieval.http_fetch import validate_fetch_url

            if ctx.tool_name == "fetch_reference":
                from ..retrieval.reference_fetch import reference_hosts

                validate_fetch_url(url, allowed=reference_hosts())
            else:
                validate_fetch_url(url)
        except ValueError as exc:
            if ctx.tool_name == "fetch_reference":
                return HookResult.block(
                    f"fetch_reference rejected: {exc}",
                    feedback=(
                        f"fetch_reference URL không hợp lệ ({exc}). Chỉ dùng HTTPS trên "
                        "allowlist tham chiếu bảo mật (nvd.nist.gov, *.mitre.org, github.com, "
                        "exploit-db.com, cisa.gov, seclists.org, …)."
                    ),
                )
            return HookResult.block(
                f"web_fetch rejected: {exc}",
                feedback=(
                    f"web_fetch URL không hợp lệ ({exc}). Chỉ dùng HTTPS trên allowlist "
                    "(owasp.org, cwe.mitre.org, cisa.gov, nvd.nist.gov), ví dụ "
                    "https://owasp.org/Top10/2025/ hoặc "
                    "https://cwe.mitre.org/top25/archive/2025/2025_cwe_top25.html "
                    "(nếu 404, thử https://owasp.org/www-project-top-ten/ hoặc "
                    "https://cwe.mitre.org/top25/archive/ để tìm bản mới nhất)"
                ),
            )
        return None

    async def on_post_tool_use(self, ctx: PostToolUseContext) -> None:
        if _profile_of(ctx) != self.profile:
            return None
        RequireSearchBeforeAnswerHook._mark(self._any_call, ctx.run_id)
        if getattr(ctx.result, "is_error", False):
            RequireSearchBeforeAnswerHook._mark(self._tool_error, ctx.run_id)
            return None
        if ctx.tool_name == "web_fetch":
            RequireSearchBeforeAnswerHook._mark(self._web, ctx.run_id)
        elif ctx.tool_name in ("qdrant_search", "neo4j_search", "poc_search", "howto_search"):
            # poc_search counts as KB grounding: a "does CVE-X have a public PoC?" question
            # is grounded by the PoC catalog, so a redundant qdrant/neo4j retry is not forced
            # (that forced regenerate is what spent the single reentry and drove the empty
            # closer-only answer for q022/q025/q026). howto_search (route product_howto,
            # ias_howto_kb) added 2026-11-04 — same grounding contract, separate collection.
            RequireSearchBeforeAnswerHook._mark(self._kb, ctx.run_id)
        return None

    async def on_before_final_answer(self, ctx: BeforeFinalAnswerContext) -> HookResult | None:
        if _profile_of(ctx) != self.profile:
            return None
        prompt = self._prompt_of(ctx)

        # Deterministic floor FIRST: never ship an empty/closer-only answer, a bare meta
        # deflection, a false "highest CVSS" claim, or a false "lỗi kết nối" claim. Replaces
        # via mutate (unbounded by the single before-final reentry the grounding
        # force_continue below usually spends).
        run_id = ctx.run_id
        guard = _chat_final_guarantee(
            prompt, ctx.final_text or "",
            any_tool_called=RequireSearchBeforeAnswerHook._get(self._any_call, run_id, False),
            had_real_tool_error=RequireSearchBeforeAnswerHook._get(self._tool_error, run_id, False))
        if guard is not None:
            return guard

        plan = classify_chat_grounding(prompt)
        if plan.route == "concept":
            return None
        # KB đã được routes.py::_ground_chat_prompt() pre-fetch trực tiếp (không qua tool-call
        # của model) — xem CHAT_PRE_FETCH_MARKER ở trên. force_continue ở đây sẽ lãng phí (ép
        # model làm lại dù đã có context đúng) hoặc tệ hơn là ship _CHAT_EMPTY_FALLBACK đè lên
        # câu trả lời pre-fetch đã đúng khi hết max_retries. web_fetch KHÔNG được pre-fetch nên
        # missing_web vẫn phải kiểm tra bình thường.
        # 2026-09-21: cờ tường minh thay cho sniff marker trong prompt (prompt giờ luôn là
        # bản gốc sạch — xem comment dài ở CHAT_PRE_FETCH_MARKER phía trên).
        session = getattr(ctx, "session", None)
        session_meta = getattr(session, "meta", None)
        prefetch_grounded = bool(isinstance(session_meta, dict) and session_meta.get("chat_prefetch_grounded"))
        if plan.need_kb and prefetch_grounded:
            plan = replace(plan, need_kb=False)

        has_web = RequireSearchBeforeAnswerHook._get(self._web, run_id, False)
        has_kb = RequireSearchBeforeAnswerHook._get(self._kb, run_id, False)
        missing_web = plan.need_web and not has_web
        missing_kb = plan.need_kb and not has_kb

        # 2026-09-27 (🅱2 Lớp 2 fix): when web is unreachable (airgap → web_fetch_enabled=
        # False), agent/runner.py never even REGISTERS the web_fetch tool (`**({web_fetch.name:
        # web_fetch} if get_settings().web_fetch_enabled else {})`) — so the model has no way
        # to call it, ever. Enforcing missing_web here in that state is unsatisfiable by
        # construction: force_continue burns the single reentry (max_retries=1, see this
        # class's docstring) asking for a tool call that literally cannot happen, and once
        # exhausted this hook ships `_CHAT_EMPTY_FALLBACK` OVER an already-correct, already-
        # streamed draft — the exact "giật"/false-apology bug this fix targets. prompts/
        # chat.md already tells the model what to do in this state (answer Top-N/taxonomy from
        # parametric knowledge + an explicit "không lấy được trang nguồn hiện tại" disclaimer),
        # so once web is confirmed off, accept that answer instead of fighting it.
        #
        # Deliberately narrow: this ONLY clears the WEB leg of the requirement for THIS turn.
        # It does NOT touch missing_kb (id_lookup/entity KB grounding stays enforced exactly
        # as before — the KB IS reachable, so there's nothing unsatisfiable about asking for
        # it), and it does NOT change behavior at all when web_fetch_enabled=True (non-airgap):
        # missing_web enforcement below is byte-for-byte the same as before this fix in that
        # case, since this branch is simply never taken.
        if missing_web and not get_settings().web_fetch_enabled:
            missing_web = False

        if not missing_web and not missing_kb:
            return None

        attempts = RequireSearchBeforeAnswerHook._get(self._attempts, run_id, 0)
        if attempts >= self.max_retries:
            # Bug (found 2026-09-18): used to `return None` here, silently shipping the
            # model's ungrounded guess once retries ran out — verified live: "CVE-2022-29353
            # có trong KEV?" forced one regenerate (chat_kb_required) but the model never
            # actually called qdrant_search/neo4j_search on retry either, and this branch let
            # the same wrong "có" through as final. A confident ungrounded answer is worse
            # than an honest "chưa tra được" — reuse the anti-empty floor instead of shipping it.
            return HookResult.mutate(final_text=_CHAT_EMPTY_FALLBACK, reason="chat_kb_required_exhausted")
        self._attempts[run_id] = attempts + 1
        self._attempts.move_to_end(run_id)
        _trim(self._attempts)

        if missing_web:
            feedback = (
                "Route=taxonomy: call web_fetch on the current canonical page "
                "(https://owasp.org/Top10/2025/ or "
                "https://cwe.mitre.org/top25/archive/2025/2025_cwe_top25.html; "
                "if that 404s, try https://owasp.org/www-project-top-ten/ or "
                "https://cwe.mitre.org/top25/archive/ to find the latest edition). "
                "Do not list Top-N categories from memory."
            )
            reason = "chat_web_fetch_required"
        elif plan.route == "id_lookup":
            feedback = (
                "Route=id_lookup: call qdrant_search with the user's question "
                '(kind=["weakness"] when resolving a CWE). Do not invent CVE/CWE IDs.'
            )
            reason = "chat_id_lookup_required"
        elif plan.route == "product_howto":
            feedback = (
                "Route=product_howto: call howto_search with the user's question about "
                "IAS product usage. Do not call qdrant_search/neo4j_search for this "
                "(those are the CVE/CWE security KB, not the user guide). Do not invent "
                "steps/menus/features not returned by howto_search."
            )
            reason = "chat_howto_required"
        else:
            feedback = (
                "Route=entity/kb: call qdrant_search or neo4j_search for CVE/CWE/ATT&CK "
                "evidence in the internal knowledge base before answering."
            )
            reason = "chat_kb_required"
        return HookResult.force_continue(feedback, reason=reason)


# Tools whose result content is externally-sourced (KB text ingested from public feeds, live
# web/GitHub fetches, PoC catalog source/README, or scan/probe output from a user-specified,
# potentially hostile bughunt target) and must therefore be spotlighted as untrusted before it
# reaches the model — see security.wrap_untrusted_content and <untrusted_content_policy> in
# prompts/base_system.md.
#
# The bughunt.tools set (bughunt/tools.py::AGENT_TOOL_NAMES) shares this same gateway Agent
# (BughuntRunner builds it via make_gateway_agent too, agent/runner.py) — every probe/scan tool
# there returns response bodies/headers/reflected-payload evidence straight from the live
# target, so it belongs in this set just like web_fetch. read_recon_summary/read_findings_summary
# are included too even though they read PREVIOUSLY-captured local state (not a fresh live
# fetch): they re-surface the same target-derived evidence/findings text captured by an earlier
# probe tool (e.g. run_verify_leads's reflected-XSS/SQLi evidence snippets) in a NEW, otherwise-
# unwrapped ToolResult on a later turn — skipping them would let already-flagged untrusted
# content back in unwrapped. update_working_memory/finish are excluded: both only echo the
# MODEL's own input (its notes / its verdict), never anything target-controlled.
#
# NOTE: ``poc_search`` is deliberately EXCLUDED even though its catalog hits are externally-
# sourced metadata: RequireSearchBeforeAnswerHook.on_post_tool_use (above) does
# ``json.loads(ctx.result.content)`` on that exact tool's result to check for a non-empty
# ``hits`` list. Wrapping it in a delimiter would break that json.loads for any post_tool_use
# hook dispatched after this one (currently safe only because this hook is registered LAST in
# agent/factory.py — an ordering an unrelated future change could easily invalidate). The
# higher-value target — actual PoC source/README bytes — is ``poc_fetch``, which no hook
# re-parses as JSON, so it stays wrapped.
UNTRUSTED_CONTENT_TOOLS = (
    "qdrant_search", "neo4j_search", "web_fetch", "fetch_reference", "web_search", "poc_fetch",
    "bh_http_probe", "run_recon", "run_vuln_scan", "run_parallel_hunt_wave", "run_verify_leads",
    "run_js_analysis", "run_secret_hunt", "run_param_discovery", "run_post_param_discovery",
    "run_api_fuzz", "run_cors_check", "run_cms_exploit", "run_rce_scan", "run_sqlmap_targeted",
    "run_sqlmap_on_file", "run_jwt_audit", "read_recon_summary", "read_findings_summary",
)


class SanitizeToolResultHook:
    """Wrap untrusted-tool results in an explicit delimiter (indirect prompt-injection defense).

    Retrieved/fetched content can carry adversarial instructions riding along as data — this
    never touches the pre-agent chat gate (``sanitize/``), which only ever sees the user's own
    message. Mutating the tool result post-execution is the one place that reaches every
    profile/endpoint sharing the gateway's single Agent (see agent/factory.py), without
    touching the tool functions themselves (so their own unit tests, which assert on the raw
    formatted shape, are unaffected).

    Skips error results (system-generated feedback, not attacker-controlled content) and empty
    content. Idempotent in effect: re-wrapping already-wrapped content is only cosmetically
    redundant, never a correctness issue.
    """

    name = "sanitize_tool_result"

    async def on_post_tool_use(self, ctx: PostToolUseContext) -> HookResult | None:
        if ctx.tool_name not in UNTRUSTED_CONTENT_TOOLS:
            return None
        result = ctx.result
        if result is None or result.is_error or not result.content:
            return None
        wrapped = wrap_untrusted_content(result.content, source=ctx.tool_name)
        if wrapped == result.content:
            return None
        return HookResult.mutate(tool_result=ToolResult(
            content=wrapped,
            summary=result.summary,
            is_error=result.is_error,
            metadata=result.metadata,
            citations=result.citations,
            attachments=result.attachments,
            duration_ms=result.duration_ms,
            truncated=result.truncated,
            recovery_hint=result.recovery_hint,
        ))
