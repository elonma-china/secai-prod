from __future__ import annotations

import json
import re
from collections import OrderedDict

from linch.hooks.contexts import BeforeFinalAnswerContext, PostToolUseContext, PreToolUseContext
from linch.hooks.types import HookResult, ToolResult

from ..security import wrap_untrusted_content

_MAX_TRACKED_RUNS = 20000  # evict oldest (run_id, tool_name) counters once exceeded


class ToolCallBudgetHook:
    """Blocks a tool once its per-run call count exceeds a configured cap.

    ``limits`` maps tool_name -> max calls per run_id. Each run gets its own fresh budget.
    Blocking here (PreToolUse) means the model sees a normal tool-error result and can
    still write its final answer from whatever evidence it already gathered — it is not a
    hard crash, just a hard stop on that one tool."""

    name = "tool_call_budget"

    def __init__(self, limits: dict[str, int]) -> None:
        self.limits = limits
        self._counts: "OrderedDict[tuple[str, str], int]" = OrderedDict()

    async def on_pre_tool_use(self, ctx: PreToolUseContext) -> HookResult | None:
        cap = self.limits.get(ctx.tool_name)
        if cap is None:
            return None
        key = (ctx.run_id or "", ctx.tool_name)
        n = self._counts.get(key, 0)
        if n >= cap:
            return HookResult.block(
                f"{ctx.tool_name} call limit reached ({cap} per run) — stop calling it and "
                f"answer from the evidence already gathered.")
        self._counts[key] = n + 1
        while len(self._counts) > _MAX_TRACKED_RUNS:
            self._counts.popitem(last=False)
        return None


class RequireSearchBeforeAnswerHook:
    """Blocks a profile from finishing its turn until it has (1) attempted ``search_tool``
    and (2), if that search returned real hits, attempted ``fetch_tool`` too — the floor
    to ToolCallBudgetHook's ceiling.

    ``prompts/exploit.md``'s ``required_tool_sequence`` is always the same fixed steps
    regardless of the question, not agentic judgment — but prompt wording alone isn't
    reliably followed: live testing showed the same CVE, same request, alternating
    between searching correctly and skipping straight to an empty answer, independent of
    load (a solo re-run reproduced it too — confirmed live via streamed event order: the
    model generated a full ungrounded draft, invented CWEs, and asked the USER for the
    CVE-ID, all before any tool call). This makes the floor non-optional instead of
    advisory, mirroring how ToolCallBudgetHook already makes the ceiling non-optional.

    The two-stage check closes a second gap found after the first version shipped: a
    model can satisfy "called poc_search" yet still never call poc_fetch even when the
    search found real hits — leaving Section 1 on the "chưa có thông tin" fallback despite
    a real, findable PoC. ``on_post_tool_use`` inspects the actual search result (parses
    the same `{"hits": [...]}` JSON `poc_search` returns) to know whether a fetch is
    actually owed, so a legitimately empty search still passes with search alone.

    Scoped to one profile (checked via ``ctx.session.meta['profile']``, set by
    ``runner.py``'s ``agent.session(meta={"profile": ...})``) since the gateway shares one
    ``Agent`` across all profiles — this must not fire for chat/report/translate, which
    have no such mandate.

    Bounded (``max_retries``): if the model still doesn't comply after being nudged, the
    answer is allowed through anyway. This hook only widens EVIDENCE COVERAGE — it never
    gates correctness, because the deterministic Section-1 placement in poc/inject.py
    (``force_no_poc_notice``) already guarantees no fabrication reaches the user even with
    zero evidence. Complementary to the deterministic pre-fetch in poc/prefetch.py: that
    covers most requests without ever touching the model's turn; this hook is the
    fallback for whatever prefetch's own search misses (e.g. its raw-prompt-text keyword
    query finds nothing but the model's own better-phrased query would have)."""

    name = "require_search_before_answer"

    def __init__(self, search_tool: str, fetch_tool: str, profile: str, max_retries: int = 2) -> None:
        self.search_tool = search_tool
        self.fetch_tool = fetch_tool
        self.profile = profile
        self.max_retries = max_retries
        self._searched: "OrderedDict[str, bool]" = OrderedDict()   # run_id -> search attempted
        self._has_hits: "OrderedDict[str, bool]" = OrderedDict()   # run_id -> search found >=1 hit
        self._fetched: "OrderedDict[str, bool]" = OrderedDict()    # run_id -> fetch attempted
        self._attempts: "OrderedDict[str, int]" = OrderedDict()    # run_id -> forced-retry count

    def _profile_of(self, ctx: BeforeFinalAnswerContext) -> str | None:
        session = getattr(ctx, "session", None)
        meta = getattr(session, "meta", None) or {}
        return meta.get("profile")

    def _mark(self, store: "OrderedDict[str, bool]", run_id: str) -> None:
        store[run_id] = True
        store.move_to_end(run_id)
        while len(store) > _MAX_TRACKED_RUNS:
            store.popitem(last=False)

    async def on_pre_tool_use(self, ctx: PreToolUseContext) -> HookResult | None:
        run_id = ctx.run_id or ""
        if ctx.tool_name == self.search_tool:
            self._mark(self._searched, run_id)
        elif ctx.tool_name == self.fetch_tool:
            self._mark(self._fetched, run_id)
        return None

    async def on_post_tool_use(self, ctx: PostToolUseContext) -> HookResult | None:
        if ctx.tool_name != self.search_tool or ctx.result is None or ctx.result.is_error:
            return None
        try:
            hits = json.loads(ctx.result.content).get("hits") or []
        except (ValueError, AttributeError, TypeError):
            hits = []
        if hits:
            self._mark(self._has_hits, ctx.run_id or "")
        return None

    async def on_before_final_answer(self, ctx: BeforeFinalAnswerContext) -> HookResult | None:
        if self._profile_of(ctx) != self.profile:
            return None
        run_id = ctx.run_id or ""
        if self._fetched.get(run_id):
            return None
        searched = self._searched.get(run_id, False)
        if searched and not self._has_hits.get(run_id, False):
            return None  # searched, legitimately found nothing — nothing to fetch
        attempts = self._attempts.get(run_id, 0)
        if attempts >= self.max_retries:
            return None  # give up — the deterministic no-evidence fallback handles this honestly
        self._attempts[run_id] = attempts + 1
        self._attempts.move_to_end(run_id)
        while len(self._attempts) > _MAX_TRACKED_RUNS:
            self._attempts.popitem(last=False)
        if not searched:
            feedback = (f"Bạn chưa gọi {self.search_tool} lần nào trong lượt này. Hãy gọi công cụ "
                       f"tra cứu trước — không được viết câu trả lời khi chưa có bằng chứng từ công cụ.")
        else:
            feedback = (f"Bạn đã tìm thấy kết quả từ {self.search_tool} nhưng chưa gọi "
                       f"{self.fetch_tool} để lấy mã nguồn thật. Hãy gọi {self.fetch_tool} với "
                       f"ref phù hợp trước khi trả lời.")
        return HookResult.force_continue(reason="search_before_answer_unsatisfied", feedback=feedback)


# Real CLI/usage signals in fetched source_code — argparse/optparse/click definitions or a
# literal "usage:"/"--help" string mean the artifact documents its own invocation, same as
# a README would. Deliberately NOT an LLM judgment call — see RequireRunInstructionsHook's
# docstring for why this stays regex-only.
_RUN_INFO_SIGNAL = re.compile(r"argparse|optparse|ArgumentParser|click\.command|usage:|--help", re.I)
# The exact phrase prompts/exploit.md's evidence_policy mandates the model title this
# subsection — a heading match, not semantic judgment, because we control both sides of
# this contract (what the model is told to write, what this hook looks for).
_RUN_SECTION_HEADING = re.compile(r"cách chạy", re.I)


class RequireRunInstructionsHook:
    """When real run-info evidence exists (a `role: "readme"` file, or clear argparse/CLI
    signals in `source_code`) but the model's own final text never wrote a "Cách chạy
    PoC" section, force it to add one instead of silently leaving real, fetched
    information unused.

    Deterministic on both sides, no LLM call inside the hook — a design choice, not an
    oversight. An LLM-judged version would need every exploit_research turn to pay for an
    extra model round-trip just to find out whether one was needed (most turns don't need
    it), breaking the fast/synchronous/side-effect-free invariant every other hook in this
    module keeps, and adds a second place a small model can misjudge on top of the one
    already being corrected. We control the prompt's exact wording — `evidence_policy`
    mandates the literal phrase "Cách chạy PoC" — so a heading/regex match on that exact
    contract is precise, not a fuzzy guess requiring semantic judgment.

    Needs `ledger` (unlike ToolCallBudgetHook/RequireSearchBeforeAnswerHook, which only
    track their own in-memory per-run state) because the evidence to check against lives
    in the evidence ledger, populated by both the agentic poc_fetch tool and the
    deterministic prefetch (poc/prefetch.py) under its own scratch run_id — reading
    `ledger.evidence_for_run(ctx.run_id)` only sees the agentic run's own rows, which is
    fine here: this hook fires on the model's own final_text, so it only needs to know
    what evidence THIS run itself gathered, not prefetch's separate contribution (routes.py
    merges both before the final Section-1 injection regardless).

    Bounded (`max_retries`): if the model still skips it after being nudged, the answer is
    allowed through as-is — this is a completeness improvement, not a safety gate, so
    giving up eventually is fine. Matches RequireSearchBeforeAnswerHook's default of 2 —
    live testing with 1 retry showed the model doesn't always comply on the first nudge
    (observed: 1/3 sampled runs still missing the section after one force_continue)."""

    name = "require_run_instructions"

    def __init__(self, ledger, profile: str, max_retries: int = 2) -> None:
        self.ledger = ledger
        self.profile = profile
        self.max_retries = max_retries
        self._attempts: "OrderedDict[str, int]" = OrderedDict()

    def _profile_of(self, ctx: BeforeFinalAnswerContext) -> str | None:
        session = getattr(ctx, "session", None)
        meta = getattr(session, "meta", None) or {}
        return meta.get("profile")

    def _has_run_info_evidence(self, run_id: str) -> bool:
        for ev in self.ledger.evidence_for_run(run_id):
            if any(f.get("role") == "readme" for f in (ev.get("source_files") or [])):
                return True
            if _RUN_INFO_SIGNAL.search(ev.get("source_code") or ""):
                return True
        return False

    async def on_before_final_answer(self, ctx: BeforeFinalAnswerContext) -> HookResult | None:
        if self._profile_of(ctx) != self.profile:
            return None
        run_id = ctx.run_id or ""
        if not self._has_run_info_evidence(run_id):
            return None  # nothing real to require a run-instructions section from
        if _RUN_SECTION_HEADING.search(ctx.final_text or ""):
            return None  # already there
        attempts = self._attempts.get(run_id, 0)
        if attempts >= self.max_retries:
            return None  # give up — a missing "Cách chạy PoC" is a completeness gap, not fabrication
        self._attempts[run_id] = attempts + 1
        self._attempts.move_to_end(run_id)
        while len(self._attempts) > _MAX_TRACKED_RUNS:
            self._attempts.popitem(last=False)
        return HookResult.force_continue(
            reason="run_instructions_missing",
            feedback="Bằng chứng đã fetch có thông tin cách chạy PoC thật (README/hướng dẫn hoặc "
                     "tham số dòng lệnh trong mã nguồn) nhưng câu trả lời chưa có phần \"Cách chạy "
                     "PoC\". Hãy bổ sung, dịch/tóm tắt đúng nội dung thật đã có trong bằng chứng — "
                     "không suy đoán thêm bước hay tham số không có căn cứ.")


# Tools whose result content is externally-sourced (KB text ingested from public feeds, PoC
# catalog source/README) and must therefore be spotlighted as untrusted before it reaches the
# model — see security.wrap_untrusted_content and <untrusted_content_policy> in
# prompts/base_system.md.
#
# Scoped to what this deployment's tool registry (agent/runner.py::LinchRunner.__init__)
# actually wires up today: qdrant_search, neo4j_search, poc_fetch. If web_fetch/fetch_reference/
# web_search or a bughunt tool surface are ever added here, add their tool names to this tuple
# too — a prior pass on a sibling branch shipped this hook without bughunt coverage at first and
# had to patch it in as a follow-up fix; don't repeat that gap.
#
# NOTE: ``poc_search`` is deliberately EXCLUDED even though its catalog hits are externally-
# sourced metadata: RequireSearchBeforeAnswerHook.on_post_tool_use (above) does
# ``json.loads(ctx.result.content)`` on that exact tool's result to check for a non-empty
# ``hits`` list. Wrapping it in a delimiter would break that json.loads for any post_tool_use
# hook dispatched after this one (currently safe only because this hook is registered LAST in
# agent/factory.py — an ordering an unrelated future change could easily invalidate). The
# higher-value target — actual PoC source/README bytes — is ``poc_fetch``, which no hook
# re-parses as JSON, so it stays wrapped.
UNTRUSTED_CONTENT_TOOLS = ("qdrant_search", "neo4j_search", "poc_fetch")


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
