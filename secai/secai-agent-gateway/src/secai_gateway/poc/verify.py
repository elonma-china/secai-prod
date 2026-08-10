"""Orchestration đường verify (Mode A): resolve (cve|ref) → poc → chọn runner theo source →
chạy trong container secai-kali-executor → verdict. Giữ verify-only. Gỡ = tắt flag
`verify_enabled`.

Phạm vi runnable (đúng bảng §4): source ``nuclei`` (mount template) và ``metasploit`` (module =
source_ref). Các source khác trả ``unsupported`` — không cố chạy PoC không map được vào engine."""
from __future__ import annotations

import asyncio
import ipaddress
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .backend import LocalPocBackend, PocBadRequest, PocForbidden, PocNotFound
from .runners import ExecConfig
from .runners import msf as msf_runner
from .runners import nuclei as nuclei_runner

_RUNNABLE = {"nuclei", "metasploit"}


# ── authz ───────────────────────────────────────────────────────────────────
def _target_host(target: str) -> str:
    t = (target or "").strip()
    if "://" in t:
        return urlparse(t).hostname or ""
    return t.split("/", 1)[0].split(":", 1)[0]


def target_allowed(settings: Any, target: str) -> bool:
    """Fail-closed: khi verify bật, chỉ cho target thuộc allowlist (host chính xác hoặc CIDR)
    trừ khi ``verify_allow_all`` = True. Allowlist rỗng + allow_all False → chặn tất cả."""
    if getattr(settings, "verify_allow_all", False):
        return True
    host = _target_host(target)
    if not host:
        return False
    for entry in getattr(settings, "verify_targets", []):
        if host == entry:
            return True
        try:
            if ipaddress.ip_address(host) in ipaddress.ip_network(entry, strict=False):
                return True
        except ValueError:
            pass
    return False


def _exec_cfg(settings: Any) -> ExecConfig:
    return ExecConfig(
        image=getattr(settings, "kali_executor_image", "secai-kali-executor:latest"),
        timeout_s=int(getattr(settings, "verify_timeout_s", 120)),
        net=getattr(settings, "verify_net", "bridge"),
        mem=getattr(settings, "verify_mem", "1g"),
        cpus=str(getattr(settings, "verify_cpus", "2")),
        pids=int(getattr(settings, "verify_pids", 256)),
        max_concurrent=int(getattr(settings, "verify_max_concurrent", 4)),
    )


def _template_host_path(poc_backend: LocalPocBackend, poc: dict[str, Any]) -> str:
    """Đường tuyệt đối tới file template nuclei, có path-escape guard (mirror backend)."""
    root = poc_backend.roots.get("nuclei")
    if root is None or not poc.get("artifact_path"):
        raise PocNotFound("Không có template cục bộ cho poc nuclei này")
    candidate = (root / poc["artifact_path"]).resolve()
    try:
        candidate.relative_to(Path(root).resolve())
    except ValueError as exc:
        raise PocForbidden("Template path escapes source root") from exc
    if not candidate.is_file():
        raise PocNotFound("Template không tồn tại trên đĩa")
    return str(candidate)


def _pick_runnable(rows: list[dict[str, Any]], source_pref: str | None) -> dict[str, Any] | None:
    runnable = [r for r in rows if r.get("source") in _RUNNABLE]
    if source_pref:
        pref = [r for r in runnable if r.get("source") == source_pref]
        runnable = pref or runnable
    # verified trước; nuclei ưu tiên (turn-key, non-destructive) rồi metasploit.
    runnable.sort(key=lambda r: (not r.get("verified"), r.get("source") != "nuclei"))
    return runnable[0] if runnable else None


async def verify_poc(poc_backend: LocalPocBackend, settings: Any, *, target: str,
                     run_id: str, cve: str | None = None, ref: str | None = None,
                     source: str | None = None, rport: int | None = None) -> dict[str, Any]:
    """Chạy verify cho (cve HOẶC ref) vào target. Không raise ra ngoài trừ authz/bad-request —
    lỗi engine trả về trong verdict. KHÔNG ghi ledger ở v1 (giữ tối giản/reversible)."""
    if not getattr(settings, "verify_enabled", False):
        raise PocForbidden("verify đang tắt (đặt VERIFY_ENABLED=true để bật)")
    if not target:
        raise PocBadRequest("Thiếu 'target'")
    if not target_allowed(settings, target):
        raise PocForbidden(f"Target '{target}' ngoài allowlist (đặt VERIFY_ALLOWED_TARGETS hoặc VERIFY_ALLOW_ALL)")

    # Chọn poc: ref (cùng run) hoặc tra catalog theo cve.
    if ref:
        poc_id = poc_backend._resolve_ref(ref, run_id)
        poc = await asyncio.to_thread(poc_backend.catalog.get, poc_id)
        if poc is None:
            raise PocNotFound("ref không trỏ tới poc nào")
        if poc.get("source") not in _RUNNABLE:
            return {"verdict": "unsupported", "reason": f"source '{poc.get('source')}' không chạy được ở verify",
                    "source": poc.get("source"), "target": target}
        chosen = poc
    elif cve:
        rows = await asyncio.to_thread(poc_backend.catalog.search, cve, 20)
        chosen = _pick_runnable(rows, source)
        if chosen is None:
            return {"verdict": "unsupported",
                    "reason": f"Không có PoC chạy được (nuclei/metasploit) cho '{cve}' trong catalog",
                    "target": target, "cve": cve, "hits": len(rows)}
    else:
        raise PocBadRequest("Cần 'cve' hoặc 'ref'")

    cfg = _exec_cfg(settings)
    src = chosen["source"]
    if src == "nuclei":
        result = await nuclei_runner.verify(cfg, template_host_path=_template_host_path(poc_backend, chosen),
                                            target=target, run_id=run_id)
    elif src == "metasploit":
        # Catalog lưu source_ref dạng "exploit_linux/http/x"; msfconsole `use` cần
        # "exploit/linux/http/x" → đổi dấu "_" đầu tiên (sau loại module) thành "/".
        module = chosen["source_ref"].replace("_", "/", 1)
        result = await msf_runner.verify(cfg, module=module, target=target, rport=rport, run_id=run_id)
    else:  # đã lọc _RUNNABLE nên không tới đây
        return {"verdict": "unsupported", "reason": f"source '{src}'", "target": target}

    return {**result, "target": target, "source": src,
            "source_ref": chosen.get("source_ref"), "cve_ids": chosen.get("cve_ids") or []}
