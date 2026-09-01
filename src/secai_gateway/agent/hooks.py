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
from dataclasses import dataclass
from typing import Any

from linch.hooks.contexts import BeforeFinalAnswerContext, PostToolUseContext, PreToolUseContext
from linch.hooks.types import HookResult, ToolResult

from ..poc.inject import has_real_code_evidence, has_synthesis_evidence, section1_has_body
from ..poc.prefetch import (
    COVERAGE_CLOSES_SEARCH_HOOK, get_session_coverage, get_session_cve_known,
    get_session_real_code_evidence, get_session_synthesis_evidence,
)
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


class RequireKbSearchBeforeSynthesisHook:
    """Require an exploit-research run to have called `qdrant_search` or `neo4j_search` at
    least once before answering — UNLESS deterministic prefetch already confirmed real
    fetched PoC code (coverage == "enough": Mục 1 will be the real-code tier regardless of
    what the model looks up, see poc/inject.py::enforce_section1's T1).

    Closes a gap `RequireSearchBeforeAnswerHook` (poc_search-only) doesn't cover: when the
    PoC catalog is empty, `prefetch_evidence`'s own deterministic search already satisfies
    THAT hook's requirement (coverage in COVERAGE_CLOSES_SEARCH_HOOK), so a model that skips
    its own tool calls entirely was free to finalize straight from parametric memory. That
    was safe under the old binary Section-1 contract (always deterministic — real code or a
    fixed sentence, never the model's own words). It stopped being safe once
    `enforce_section1` started trusting the model's own Section 1 text whenever the CVE is
    confirmed real (T3, synthesize-from-CVE-definition) — observed live: with nothing
    forcing a KB lookup, the model fabricated CWE/CVSS content in Mục 3 without calling ANY
    tool. This hook is the floor for that: it doesn't verify the model's synthesized content
    is faithful to what the KB actually said (same trust boundary as everywhere else in this
    module — see evidence_policy in prompts/exploit.md), only that it looked at all."""

    name = "require_kb_search_before_synthesis"

    def __init__(self, tools: tuple[str, ...], profile: str, max_retries: int = 2) -> None:
        self.tools = set(tools)
        self.profile = profile
        self.max_retries = max_retries
        self._searched: OrderedDict[str, bool] = OrderedDict()
        self._attempts: OrderedDict[str, int] = OrderedDict()

    async def on_pre_tool_use(self, ctx: PreToolUseContext) -> None:
        if ctx.tool_name in self.tools:
            self._searched[ctx.run_id] = True
            self._searched.move_to_end(ctx.run_id)
            _trim(self._searched)
        return None

    async def on_before_final_answer(self, ctx: BeforeFinalAnswerContext) -> HookResult | None:
        if _profile_of(ctx) != self.profile:
            return None
        if get_session_coverage(_session_id_of(ctx)) == "enough":
            return None  # real fetched PoC code already confirmed — T1 applies regardless

        run_id = ctx.run_id
        if run_id in self._searched:
            self._searched.move_to_end(run_id)
            return None

        attempts = self._attempts.get(run_id, 0)
        if attempts >= self.max_retries:
            return None
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


@dataclass(frozen=True, slots=True)
class ChatGroundingPlan:
    """Deterministic route for one user prompt."""

    route: str  # taxonomy | entity | id_lookup | concept
    need_web: bool
    need_kb: bool


def classify_chat_grounding(prompt: str) -> ChatGroundingPlan:
    """Decide which evidence tools chat must call before a final answer."""
    text = (prompt or "").strip()
    if not text:
        return ChatGroundingPlan("concept", False, False)

    need_web = bool(_TAXONOMY_RE.search(text))
    has_entity = bool(_ENTITY_ID_RE.search(text))
    id_lookup = bool(_ID_LOOKUP_RE.search(text))
    cve_family = bool(_CVE_FAMILY_RE.search(text))

    if need_web:
        need_kb = has_entity or cve_family or id_lookup
        return ChatGroundingPlan("taxonomy", True, need_kb)
    if has_entity:
        return ChatGroundingPlan("entity", False, True)
    if id_lookup:
        return ChatGroundingPlan("id_lookup", False, True)
    return ChatGroundingPlan("concept", False, False)


class RequireChatGroundingHook:
    """Steer + enforce chat tool use: web for Top-N, KB for entities/ID lookup.

    - Pre-tool: block ``web_fetch`` URLs outside the allowlist (SSRF/host policy).
    - Post-tool: only successful calls satisfy the requirement (errors do not count).
    - Before-final: force_continue until required tools ran (bounded retries).
    Concept questions (no ID / no Top-N / no "CVE nào") use parametric knowledge.
    """

    name = "require_chat_grounding"

    def __init__(self, profile: str = "chat", max_retries: int = 3) -> None:
        self.profile = profile
        self.max_retries = max_retries
        self._web: OrderedDict[str, bool] = OrderedDict()
        self._kb: OrderedDict[str, bool] = OrderedDict()
        self._attempts: OrderedDict[str, int] = OrderedDict()

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
        if getattr(ctx.result, "is_error", False):
            return None
        if ctx.tool_name == "web_fetch":
            RequireSearchBeforeAnswerHook._mark(self._web, ctx.run_id)
        elif ctx.tool_name in ("qdrant_search", "neo4j_search"):
            RequireSearchBeforeAnswerHook._mark(self._kb, ctx.run_id)
        return None

    async def on_before_final_answer(self, ctx: BeforeFinalAnswerContext) -> HookResult | None:
        if _profile_of(ctx) != self.profile:
            return None
        plan = classify_chat_grounding(self._prompt_of(ctx))
        if plan.route == "concept":
            return None

        run_id = ctx.run_id
        has_web = RequireSearchBeforeAnswerHook._get(self._web, run_id, False)
        has_kb = RequireSearchBeforeAnswerHook._get(self._kb, run_id, False)
        missing_web = plan.need_web and not has_web
        missing_kb = plan.need_kb and not has_kb
        if not missing_web and not missing_kb:
            return None

        attempts = RequireSearchBeforeAnswerHook._get(self._attempts, run_id, 0)
        if attempts >= self.max_retries:
            return None
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
        else:
            feedback = (
                "Route=entity/kb: call qdrant_search or neo4j_search for CVE/CWE/ATT&CK "
                "evidence in the internal knowledge base before answering."
            )
            reason = "chat_kb_required"
        return HookResult.force_continue(feedback, reason=reason)


# Tools whose result content is externally-sourced (KB text ingested from public feeds, live
# web/GitHub fetches, PoC catalog source/README, scan output from a user-specified target) and
# must therefore be spotlighted as untrusted before it reaches the model — see
# security.wrap_untrusted_content and <untrusted_content_policy> in prompts/base_system.md.
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
