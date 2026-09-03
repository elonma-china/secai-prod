"""lane1 — vòng ReAct kiểu hacker cho `/ai/exploit-advance` khi CVE KHÔNG có primitive/pocsuite.

Cyber-F1 gãy native tool-calling → mỗi lượt LLM chỉ xuất MỘT action JSON có cấu trúc
`{thought, tool, args, done}`; code parse/validate/repair (reject-không-chạy), gate bằng
command_risk + scope, chạy tool trong sandbox (`lane1_tools`), đọc observation, kiểm oracle
tất định (nonce tự-inject / `uid=` / `root:` / success_signal), rồi lặp dưới phase-control.

Là async generator phát `_step`/`_log` (KHÔNG phát `final` — `advance.py` giữ `final` duy nhất).
Kết quả trả qua `Lane1Outcome` (mutable, caller đọc sau vòng lặp).
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

from . import primitives
from ._events import _log, _step
from .command_risk import classify
from .lane1_tools import TOOL_MENU, Observation, run_lane1_tool
from .phases import Lane1State, advance_phase, allowed_tools
from .skills import load_skill

logger = logging.getLogger(__name__)

_FENCE_JSON = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.S)
_HIST_KEEP = 3          # số observation gần nhất đưa vào prompt
_HIST_SLICE = 800       # ký tự mỗi observation trong prompt


@dataclass
class Lane1Action:
    thought: str
    tool: str
    args: dict[str, Any] = field(default_factory=dict)
    done: bool = False


@dataclass
class Lane1Outcome:
    verdict: str = "inconclusive"
    evidence: str = ""
    transcript_tail: str = ""
    final_action: str = ""
    steps_to_success: int | None = None


# ───────────────────────────── parse JSON action ─────────────────────────────

def _extract_json(text: str) -> dict[str, Any] | None:
    """Trích object JSON đầu tiên: ưu tiên trong ```fence```, fallback quét cân ngoặc (bỏ qua
    ngoặc trong chuỗi). Cyber-F1 hay bọc JSON trong prose/markdown → phải chịu được."""
    s = text or ""
    m = _FENCE_JSON.search(s)
    if m:
        try:
            return json.loads(m.group(1))
        except ValueError:
            pass
    start = s.find("{")
    while start != -1:
        depth = 0
        instr = False
        esc = False
        for i in range(start, len(s)):
            c = s[i]
            if esc:
                esc = False
                continue
            if c == "\\":
                esc = True
                continue
            if c == '"':
                instr = not instr
                continue
            if instr:
                continue
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(s[start:i + 1])
                    except ValueError:
                        break
        start = s.find("{", start + 1)
    return None


def parse_action(text: str) -> Lane1Action | None:
    """Trả Lane1Action hợp lệ hoặc None (reject-không-chạy — triết lý advance._suggest_command)."""
    obj = _extract_json(text)
    if not isinstance(obj, dict):
        return None
    tool = str(obj.get("tool") or "").strip()
    if tool not in TOOL_MENU:
        return None
    args = obj.get("args")
    if not isinstance(args, dict):
        args = {}
    return Lane1Action(thought=str(obj.get("thought") or "")[:300], tool=tool,
                       args=args, done=bool(obj.get("done")))


# ───────────────────────────── oracle tất định ─────────────────────────────

def check_success(obs_text: str, success_signal: str | None, nonce: str | None) -> tuple[bool, str]:
    """Thứ tự độ mạnh: (1) nonce tự-inject phản chiếu, (2) uid=/root: (indicator inject-probe),
    (3) success_signal của lab. Trả (thành công, bằng chứng)."""
    t = obs_text or ""
    if nonce and nonce in t:
        return True, f"nonce {nonce} phản chiếu → RCE tất định"
    if primitives._UID_RE.search(t):
        m = primitives._UID_RE.search(t)
        return True, f"chỉ dấu RCE: {t[m.start():m.start() + 40]!r}"
    if primitives._PASSWD_RE.search(t):
        return True, "chỉ dấu LFI: /etc/passwd (root:...:0:0:)"
    if success_signal and success_signal in t:
        return True, f"success_signal '{success_signal}' xuất hiện"
    return False, ""


# ───────────────────────────── prompt builder ─────────────────────────────

def _subst(value: Any, nonce: str, cmd: str) -> Any:
    """Thay {NONCE}/{CMD} trong mọi chuỗi của args (đệ quy)."""
    if isinstance(value, str):
        return value.replace("{NONCE}", nonce).replace("{CMD}", cmd)
    if isinstance(value, dict):
        return {k: _subst(v, nonce, cmd) for k, v in value.items()}
    if isinstance(value, list):
        return [_subst(v, nonce, cmd) for v in value]
    return value


def _hist_text(history: list[Observation]) -> str:
    out = []
    for o in history[-_HIST_KEEP:]:
        body = o.text
        if len(body) > _HIST_SLICE:
            body = body[: _HIST_SLICE // 2] + "\n…\n" + body[-_HIST_SLICE // 2:]
        out.append(f"[{o.tool} ok={o.ok}] {body}")
    return "\n---\n".join(out) if out else "(chưa có)"


def build_react_prompt(cve: str, host: str, port: str | None, skill: Any, state: Lane1State,
                       history: list[Observation], allowed: frozenset[str], nonce: str,
                       cmd: str) -> str:
    tools_desc = {
        "http_request": '{"method","path","headers"(dict),"body","tls"(bool)} — HTTP tới target (vũ khí chính, path nguyên văn)',
        "nmap": '{"target","ports"} — quét cổng',
        "sqlmap": '{"target"(url)} — dò SQLi tự động',
        "gobuster": '{"target"(url),"wordlist"} — brute thư mục',
        "searchsploit": '{"query"} — tra PoC offline',
        "run_cmd": '{"shell"} — chạy lệnh tuỳ ý trong sandbox (payload/msf); output trả về',
    }
    menu = "\n".join(f'  - {t}: {tools_desc[t]}' for t in tools_desc if t in allowed)
    templates = "\n".join(f"  - {p}" for p in (skill.payload_templates or [])) or "  (không có)"
    return (
        f"Bạn là chuyên gia khai thác, đang tấn công lab {cve} tại {host}:{port or 80} "
        f"(phase HIỆN TẠI = {state.phase.value}).\n"
        f"BỐI CẢNH LỖ HỔNG ({skill.vuln_class}): {skill.context}\n"
        f"CHUỖI GỢI Ý: {'; '.join(skill.suggested_sequence)}\n"
        f"TEMPLATE PAYLOAD (thay {{NONCE}}={nonce}, {{CMD}}={cmd} tự động):\n{templates}\n"
        f"THÀNH CÔNG khi: {skill.oracle_hint}\n\n"
        f"TOOL ĐƯỢC PHÉP (phase này):\n{menu}\n\n"
        f"QUAN SÁT GẦN NHẤT:\n{_hist_text(history)}\n\n"
        "NHIỆM VỤ: chọn HÀNH ĐỘNG TIẾP THEO. IN RA ĐÚNG MỘT object JSON, KHÔNG prose/markdown:\n"
        '{"thought":"<1 câu>","tool":"<tên tool>","args":{...},"done":false}\n'
        "Để chứng minh RCE, hãy chèn `echo NONCE; <lệnh>` (hoặc dùng {NONCE}/{CMD}) rồi đọc lại trong response."
    )


_REPAIR = ('CHỈ in đúng MỘT object JSON hợp lệ {"thought","tool","args","done"}. '
           "KHÔNG giải thích, KHÔNG markdown, KHÔNG backtick.")


# ───────────────────────────── driver ─────────────────────────────

async def _one_action(runner: Any, prompt: str, step_timeout_s: int) -> Lane1Action | None:
    """1 lượt LLM → action, có 1 lần repair. None nếu vẫn hỏng."""
    for attempt in (0, 1):
        p = prompt if attempt == 0 else (prompt + "\n\n" + _REPAIR)
        try:
            text, _ = await asyncio.wait_for(runner.complete("lane1_react", p),
                                             timeout=max(10, step_timeout_s))
        except Exception:
            return None
        act = parse_action(text or "")
        if act is not None:
            return act
    return None


def _dedup_key(action: Lane1Action) -> str:
    import hashlib
    blob = action.tool + "|" + json.dumps(action.args, sort_keys=True, ensure_ascii=False)
    return hashlib.sha1(blob.encode("utf-8", "replace")).hexdigest()


def _action_summary(action: Lane1Action) -> str:
    a = action.args or {}
    if action.tool == "http_request":
        return f"{a.get('method', 'GET')} {a.get('path', '/')}"
    if action.tool == "run_cmd":
        return f"sh: {str(a.get('shell', ''))[:80]}"
    return f"{action.tool} {a.get('target', '')}".strip()


async def run_lane1(settings: Any, runner: Any, *, cve: str, host: str, port: str | None,
                    vuln_class: str, success_signal: str | None, command: str, base_index: int,
                    run_id: str, max_steps: int, step_timeout_s: int, total_timeout_s: int,
                    outcome: Lane1Outcome) -> AsyncIterator[dict[str, Any]]:
    idx = base_index
    skill = load_skill(vuln_class, cve)
    net = getattr(settings, "lane1_net", "host") or "host"
    cmd = (command or "id").strip()
    nonce = primitives._nonce()
    state = Lane1State()
    history: list[Observation] = []
    seen: set[str] = set()

    yield _step(idx, "lane1", "running",
                f"ReAct recon→exploit {cve} (skill={skill.vuln_class}, nonce={nonce})")

    if runner is None:
        outcome.verdict = "error"
        yield _step(idx, "lane1", "failed", "không có LLM runner")
        return

    # Seed recon tất định: GET / và /actuator để mồi observation cho LLM yếu (không tốn bước max_steps).
    seed = await run_lane1_tool(settings, Lane1Action("seed", "http_request", {"method": "GET", "path": "/"}),
                                run_id=run_id, host=host, port=port, net=net, timeout_s=step_timeout_s)
    history.append(seed)
    if seed.ok:
        state.facts["alive"] = True
    yield _log(f"[seed http GET /] {seed.text.splitlines()[0] if seed.text else ''}")
    # Seed actuator endpoint discovery (Spring Boot / actuator-based apps) — tất định, không tốn bước.
    seed_act = await run_lane1_tool(settings, Lane1Action("seed", "http_request", {"method": "GET", "path": "/actuator"}),
                                    run_id=run_id, host=host, port=port, net=net, timeout_s=step_timeout_s)
    if seed_act.ok and "_links" in seed_act.body:
        history.append(seed_act)
        yield _log(f"[seed http GET /actuator] {seed_act.text.splitlines()[0] if seed_act.text else ''}")

    deadline = time.monotonic() + max(30, total_timeout_s)
    for step in range(1, max_steps + 1):
        if time.monotonic() > deadline:
            yield _step(idx, f"lane1#{step}", "done", "hết thời gian tổng (total_timeout)")
            break
        state.step = step
        allowed = allowed_tools(state)
        prompt = build_react_prompt(cve, host, port, skill, state, history, allowed, nonce, cmd)
        action = await _one_action(runner, prompt, step_timeout_s)
        if action is None:
            state.stuck += 1
            yield _step(idx, f"lane1#{step}", "done", "LLM không trả JSON hợp lệ (bỏ lượt)")
            advance_phase(state, None, None)
            continue

        risk = classify(action, state.phase, host, port, settings)
        if not risk.allow:
            state.stuck += 1
            yield _step(idx, f"lane1#{step}", "done", f"chặn: {risk.reason}")
            advance_phase(state, None, action)
            continue

        # điền {NONCE}/{CMD} vào payload
        action.args = _subst(action.args, nonce, cmd)
        key = _dedup_key(action)
        if key in seen:
            state.stuck += 1
            yield _log(f"[dedup] bỏ action trùng: {_action_summary(action)}")
            advance_phase(state, None, action)
            continue
        seen.add(key)

        yield _step(idx, f"lane1#{step}:{action.tool}", "running",
                    f"{action.thought[:110]} | {_action_summary(action)}")
        obs = await run_lane1_tool(settings, action, run_id=run_id, host=host, port=port,
                                   net=net, timeout_s=step_timeout_s)
        for line in obs.text.splitlines()[:6]:
            if line.strip():
                yield _log(line)
        history.append(obs)

        # Oracle CHỈ soi body (output server), KHÔNG soi obs.text (kèm request → tránh khớp nhầm payload).
        ok, why = check_success(obs.body, success_signal, nonce)
        if ok:
            outcome.verdict = "exploit_success"
            outcome.evidence = why
            outcome.final_action = _action_summary(action)
            outcome.steps_to_success = step
            yield _step(idx, f"lane1#{step}", "done", why, verdict="exploit_success")
            break

        # cập nhật fact + stuck cho phase-control
        if obs.ok:
            state.stuck = 0
            state.facts["alive"] = True
            if obs.meta.get("status") and 200 <= int(obs.meta.get("status") or 0) < 500:
                state.facts["endpoint"] = True
            from .phases import Phase
            if state.phase is Phase.EXPLOIT:
                state.facts["partial_hit"] = True
        else:
            state.stuck += 1
        yield _step(idx, f"lane1#{step}", "done",
                    f"{obs.tool} ok={obs.ok} → phase {state.phase.value}")
        advance_phase(state, obs, action)

    if outcome.verdict != "exploit_success":
        outcome.verdict = "exploit_failed" if history else "inconclusive"
    outcome.transcript_tail = _hist_text(history)[-1500:]
    yield _step(idx, "lane1", "done", f"kết thúc lane1: {outcome.verdict}", verdict=outcome.verdict)


# ───────────────────────────── dry-run offline ─────────────────────────────

if __name__ == "__main__":  # pragma: no cover
    import sys

    class _StubRunner:
        """Runner giả: đọc kịch bản action JSON (mỗi dòng 1 JSON) từ stdin, hoặc 1 action GET /."""
        def __init__(self, script: list[str]):
            self.script = list(script)
        async def complete(self, profile, prompt, session_id=None):
            if self.script:
                return self.script.pop(0), {}
            return '{"thought":"probe","tool":"http_request","args":{"method":"GET","path":"/"},"done":false}', {}

    if len(sys.argv) < 3:
        print("usage: python -m secai_gateway.poc.lane1 <host> <port> [vuln_class]")
        raise SystemExit(2)

    class _S:
        lane1_net = "host"; verify_allow_all = True; verify_enabled = False
        exec_enabled = False; lane1_use_raw_cmd = False; exec_privileged = False

    host, port = sys.argv[1], sys.argv[2]
    vc = sys.argv[3] if len(sys.argv) > 3 else ""
    script = [ln for ln in sys.stdin.read().splitlines() if ln.strip()] if not sys.stdin.isatty() else []

    async def _main():
        oc = Lane1Outcome()
        async for ev in run_lane1(_S(), _StubRunner(script), cve="CVE-DRYRUN", host=host, port=port,
                                  vuln_class=vc, success_signal="uid=", command="id", base_index=5,
                                  run_id="dryrun", max_steps=6, step_timeout_s=20, total_timeout_s=120,
                                  outcome=oc):
            print(json.dumps(ev, ensure_ascii=False))
        print("OUTCOME:", oc.verdict, "|", oc.evidence)

    asyncio.run(_main())
