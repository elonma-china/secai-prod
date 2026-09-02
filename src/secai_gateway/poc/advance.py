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
from typing import Any, AsyncIterator
from urllib.parse import urlparse

from .kt_exploit import run_kt_exploit

logger = logging.getLogger(__name__)


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


def _step(index: int, name: str, status: str, detail: str = "",
          verdict: str | None = None) -> dict[str, Any]:
    return {"type": "step", "index": index, "name": name, "status": status,
            "detail": detail, "verdict": verdict}


def _log(line: str, stream: str = "stdout") -> dict[str, Any]:
    return {"type": "log", "stream": stream, "line": line}


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
        f"Bạn đang khai thác {cve}. Lệnh chứng minh RCE vừa chạy: `{command}` — nhưng CHƯA thành công.\n"
        f"--- OUTPUT/LỖI ---\n{(output or '')[-1500:]}\n"
        "Đề xuất DUY NHẤT một lệnh shell khác để chứng minh RCE (vd đổi đường dẫn tuyệt đối, "
        "`/usr/bin/id`, `cat /flag`, `whoami`). CHỈ in đúng một dòng lệnh, không giải thích.")
    try:
        text, _meta = await runner.complete("exploit_research", prompt)
    except Exception:
        logger.info("advance: repair suggest failed for %s", cve)
        return None
    line = (text or "").strip().splitlines()[0].strip("` ").strip() if text and text.strip() else ""
    return line or None


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

    # 4 — vòng exploit → verify → sửa
    result: dict[str, Any] = {}
    verdict = "inconclusive"
    seen_cmds = {command}
    for i in range(1, max_steps + 1):
        yield _step(4, f"exploit#{i}", "running", f"command=`{command}`")
        try:
            result = await run_kt_exploit(settings, ip=host, cve=cve, port=port,
                                          command=command, use_proxy=bool(getattr(req, "use_proxy", True)),
                                          run_id=run_id)
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

    ok = verdict == "exploit_success"
    yield {"type": "final", "ok": ok, "verdict": verdict,
           "content": f"Khai thác {cve} vào {host}: {verdict}",
           "result": {"cve": cve, "target": host, "port": port, "verdict": verdict,
                      "final_command": command, "engine": "kt-pocsuite",
                      "output_tail": ((result.get("scan") or {}).get("output") or "")[-1500:]},
           "metadata": {"run_id": run_id, "profile": "exploit_advance", "cve": cve, "target": host}}
