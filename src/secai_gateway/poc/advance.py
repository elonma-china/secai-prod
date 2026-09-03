"""Điều phối khai thác ĐA-BƯỚC vào target thật cho `/ai/exploit-advance` (v1 pragmatic).

Pipeline: authz → seed (PoC catalog, air-gap) → recon → vòng {run (kt pocsuite) → verify → LLM sửa}
≤ max_steps → final. Là **async generator** phát ra các event dict (`step`/`log`/`final`) để endpoint
bọc thành SSE — nhờ vậy FE thấy ĐANG Ở BƯỚC NÀO + KẾT QUẢ.

Engine khai thác = `kt_exploit.run_kt_exploit` (pocsuite qua `poc_exploit_api_url`); v1 chỉ tinh chỉnh
`command` giữa các vòng (kt chạy module cố định cho CVE, không viết lại PoC tuỳ ý — đó là phần
task-graph/kali-executor để pha sau, xem docs/method-c-upgrade.md). Air-gap: seed + LLM + pocsuite đều
nội bộ; chỉ TARGET là bên ngoài (đúng mục đích). Không raise ra ngoài — lỗi thành event `final(ok=False)`.
"""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Any, AsyncIterator
from urllib.parse import urlparse

from ._events import _log, _step
from .backend import PocNotFound
from .kt_exploit import run_kt_exploit

logger = logging.getLogger(__name__)

# Tên công cụ nội bộ của gateway — KHÔNG phải lệnh shell. LLM yếu hay nhầm "command chứng minh RCE"
# thành một tool-call (quan sát live: `poc_search(query=...)`), làm vòng repair chạy rác. Chặn ở đây.
_TOOL_WORDS = ("poc_search", "qdrant_search", "neo4j_search", "web_search", "web_fetch",
               "poc_fetch", "poc_verify", "poc_scan", "poc_exec", "fetch_reference")
_CMD_FIRST_TOKEN = re.compile(r"^/?[A-Za-z0-9_][A-Za-z0-9_./+-]*$")  # lệnh hoặc path tuyệt đối (/usr/bin/id)
_FUNC_CALL = re.compile(r"\b\w+\s*\(")  # dạng gọi-hàm foo(...) → không phải shell


def _valid_shell_command(s: str) -> bool:
    """True khi `s` trông như MỘT lệnh shell hợp lệ (id, whoami, `cat /flag`, `uname -a`…), KHÔNG
    phải tool-call / lời giải thích. Chống repair-mù của LLM yếu (design: method-c-upgrade.md)."""
    s = (s or "").strip()
    if not s or len(s) > 200 or "\n" in s:
        return False
    if _FUNC_CALL.search(s):                       # foo(...) / poc_search(...) → loại
        return False
    if any(t in s for t in _TOOL_WORDS):           # tên tool nội bộ → loại
        return False
    toks = s.split()
    if not toks or not _CMD_FIRST_TOKEN.match(toks[0]):  # từ lệnh đầu phải là command/path
        return False
    return True


def parse_target(target: str) -> tuple[str, str | None]:
    """(host, port|None) từ host | host:port | url."""
    raw = (target or "").strip()
    if "://" in raw:
        p = urlparse(raw)
        return (p.hostname or raw), (str(p.port) if p.port else None)
    if raw.count(":") == 1 and not raw.startswith("["):
        host, right = raw.rsplit(":", 1)
        if right.isdigit():
            return host, right
    return raw.split("/", 1)[0], None


async def _probe(host: str, port: str | None, timeout: float = 5.0) -> bool:
    """Recon nhẹ: mở được TCP tới host:port là coi như target sống. Fail-open = False."""
    p = int(port) if (port and str(port).isdigit()) else 80
    try:
        fut = asyncio.open_connection(host, p)
        reader, writer = await asyncio.wait_for(fut, timeout=timeout)
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
        return True
    except Exception:
        return False


async def _suggest_command(runner: Any, cve: str, command: str, output: str) -> str | None:
    """LLM nội bộ đề xuất `command` khác cho vòng sau dựa trên output lần trước (air-gap). Trả None/
    trùng command cũ → dừng lặp. Fail-open: lỗi → None."""
    if runner is None:
        return None
    prompt = (
        f"Đang khai thác {cve}. Lệnh chứng minh RCE vừa chạy: `{command}` — CHƯA thành công.\n"
        f"--- OUTPUT/LỖI ---\n{(output or '')[-1500:]}\n"
        "Nhiệm vụ: đề xuất MỘT LỆNH SHELL LINUX khác để chứng minh RCE. QUY TẮC BẮT BUỘC:\n"
        "- CHỈ in đúng MỘT dòng là lệnh shell (vd: id | whoami | /usr/bin/id | cat /flag | "
        "cat /etc/passwd | uname -a | hostname).\n"
        "- TUYỆT ĐỐI KHÔNG phải lời gọi hàm/tool (không `poc_search(...)`, không tên công cụ), "
        "KHÔNG giải thích, KHÔNG markdown, KHÔNG dấu backtick.")
    try:
        text, _meta = await runner.complete("exploit_research", prompt)
    except Exception:
        logger.info("advance: repair suggest failed for %s", cve)
        return None
    line = (text or "").strip().splitlines()[0].strip("`* ").strip() if text and text.strip() else ""
    if not _valid_shell_command(line):
        logger.info("advance: repair suggestion rejected (không phải lệnh shell): %r", line[:80])
        return None  # dừng loop sạch thay vì chạy rác
    return line


async def run_exploit_advance(settings: Any, runner: Any, req: Any, *,
                              run_id: str, seed_hint: str = "") -> AsyncIterator[dict[str, Any]]:
    """Chạy pipeline, yield event dict. Caller (endpoint) đã kiểm authz (poc_exploit_enabled +
    target_allowed) trước khi gọi — ở đây tái khẳng định + chạy các bước."""
    host, parsed_port = parse_target(req.target)
    port = str(req.port) if getattr(req, "port", None) not in (None, "") else parsed_port
    cve = (req.cve or "").strip().upper()
    command = (getattr(req, "command", None) or "id").strip()
    max_steps = int(getattr(req, "max_steps", 5) or 5)

    yield _step(1, "authz", "done", f"cve={cve} target={host}" + (f":{port}" if port else ""))

    # 2 — seed (air-gap): chỉ báo nguồn PoC; kt tự chọn module pocsuite cho CVE.
    yield _step(2, "seed", "done",
                seed_hint or "Dùng PoC/module nội bộ cho CVE (air-gap; không internet).")

    # 3 — recon
    yield _step(3, "recon", "running", f"probe {host}:{port or '80'}")
    alive = await _probe(host, port)
    yield _step(3, "recon", "done" if alive else "failed",
                "target phản hồi TCP" if alive else "không mở được TCP (vẫn thử khai thác)")

    # 3.5 — lane PRIMITIVE tất định (tham khảo PentestCode inject-probe): hệ thống TỰ tra CVE →
    # có primitive thì thử TRƯỚC; strong-success → xong; không → fallback pocsuite. Không cherry-pick.
    from .primitives import has_primitive, run_primitive
    result: dict[str, Any] = {}
    verdict = "inconclusive"
    engine = "kt-pocsuite"
    seen_cmds = {command}
    if has_primitive(cve):
        base = f"http://{host}:{port}" if port else f"http://{host}"
        yield _step(4, "primitive", "running", f"primitive tất định cho {cve}")
        pres = None
        try:
            pres = await asyncio.to_thread(run_primitive, cve, base, command)
        except Exception as exc:
            yield _step(4, "primitive", "failed", f"primitive lỗi: {exc}")
        if pres is not None:
            for line in (pres.output or "").splitlines():
                if line.strip():
                    yield _log(line)
            yield _step(4, "primitive", "done",
                        f"{pres.primitive}: {pres.evidence[:76]} (lead={pres.lead})",
                        verdict=pres.verdict)
            if pres.ok:
                verdict, engine = "exploit_success", "primitive"
                result = {"scan": {"output": pres.output}}

    # Cổng lane1 (ReAct kiểu hacker) + phát hiện "CVE KHÔNG có trong pocsuite".
    lane1_on = bool(getattr(settings, "lane1_enabled", False))
    pocsuite_on = bool(getattr(settings, "poc_exploit_enabled", False))
    extra = getattr(req, "extra", None) or {}
    route_lane1 = False
    # Fast-path: CVE mới (không primitive) + engine pocsuite TẮT → vào thẳng lane1, tránh 404 vô ích.
    if verdict != "exploit_success" and not has_primitive(cve) and not pocsuite_on and lane1_on:
        route_lane1 = True

    # 4 — vòng exploit → verify → sửa (pocsuite). PocNotFound (engine 404) = không có module cho CVE
    # → KHÔNG coi là lỗi; chuyển sang lane1. Chỉ chạy khi engine bật.
    if pocsuite_on:
        for i in range(1, max_steps + 1):
            if verdict == "exploit_success":
                break
            yield _step(4, f"exploit#{i}", "running", f"command=`{command}`")
            try:
                result = await run_kt_exploit(settings, ip=host, cve=cve, port=port,
                                              command=command, use_proxy=bool(getattr(req, "use_proxy", True)),
                                              run_id=run_id)
            except PocNotFound:
                yield _step(4, f"exploit#{i}", "skipped",
                            "engine không có module cho CVE — chuyển lane1")
                route_lane1 = lane1_on
                break
            except Exception as exc:
                yield _step(4, f"exploit#{i}", "failed", f"engine lỗi: {exc}")
                verdict = "error"
                break
            for line in ((result.get("scan") or {}).get("output") or "").splitlines():
                if line.strip():
                    yield _log(line)
            verdict = result.get("exploit_verdict") or "inconclusive"
            yield _step(4, f"verify#{i}", "done", f"verdict={verdict}", verdict=verdict)
            if verdict == "exploit_success":
                break
            # repair: LLM đề xuất command khác; trùng/None → dừng (tránh phí vòng)
            nxt = await _suggest_command(runner, cve, command, (result.get("scan") or {}).get("output") or "")
            if not nxt or nxt in seen_cmds:
                yield _step(4, f"repair#{i}", "done", "không có hướng sửa mới — dừng.")
                break
            seen_cmds.add(nxt)
            yield _step(4, f"repair#{i}", "done", f"đổi command → `{nxt}`")
            command = nxt

    # 5 — lane1 (ReAct recon→exploit kiểu hacker): CVE không có primitive/pocsuite module.
    if route_lane1 and lane1_on and verdict != "exploit_success":
        from .lane1 import Lane1Outcome, run_lane1
        lane1_steps = max(int(max_steps or 0), int(getattr(settings, "lane1_max_steps", 12) or 12))
        outcome = Lane1Outcome()
        async for ev in run_lane1(
                settings, runner, cve=cve, host=host, port=port,
                vuln_class=str(extra.get("vuln_class") or ""),
                success_signal=(extra.get("success_signal") or None),
                command=command, base_index=5, run_id=run_id,
                max_steps=lane1_steps,
                step_timeout_s=int(getattr(req, "step_timeout_s", 180) or 180),
                total_timeout_s=int(getattr(req, "total_timeout_s", 900) or 900),
                outcome=outcome):
            yield ev
        verdict = outcome.verdict or verdict
        engine = "lane1"
        if outcome.transcript_tail:
            result = {"scan": {"output": outcome.transcript_tail}}
        command = outcome.final_action or command

    ok = verdict == "exploit_success"
    yield {"type": "final", "ok": ok, "verdict": verdict,
           "content": f"Khai thác {cve} vào {host}: {verdict}",
           "result": {"cve": cve, "target": host, "port": port, "verdict": verdict,
                      "final_command": command, "engine": engine,
                      "output_tail": ((result.get("scan") or {}).get("output") or "")[-1500:]},
           "metadata": {"run_id": run_id, "profile": "exploit_advance", "cve": cve, "target": host}}
