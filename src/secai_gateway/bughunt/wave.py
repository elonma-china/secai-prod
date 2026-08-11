"""Parallel hunt wave — fan-out independent post-recon tools.

Runs verify + vuln_scan + cors + secrets + api_fuzz concurrently so wall-clock
is approx max(children) instead of sum(children).
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from typing import Any

from . import arsenal
from .engine import _is_ip_lab_target
from .memory import BughuntRunState
from .verify import leads_to_findings_log, verify_high_signal
from .workdir import findings_dir, recon_dir

logger = logging.getLogger(__name__)


def _wave_concurrency(settings: Any) -> int:
    return max(1, int(getattr(settings, "bughunt_wave_concurrency", None) or 4))


async def _record(st: BughuntRunState, lock: asyncio.Lock, name: str, obs: str) -> None:
    async with lock:
        st.memory.record_step(name, obs)


async def _run_verify(st: BughuntRunState, lock: asyncio.Lock) -> dict[str, Any]:
    result = await verify_high_signal(st.target, recon_dir=recon_dir(st.workdir))
    leads = list(result.get("leads") or [])
    async with lock:
        # Avoid double-append if already present from agent tool
        existing = {
            (str(f.get("vul_class")), str(f.get("path") or ""))
            for f in st.memory.findings_log
            if f.get("tool") == "verify_high_signal" or f.get("source") == "verify_high_signal"
        }
        for item in leads_to_findings_log(leads):
            key = (str(item.get("vul_class")), str(item.get("path") or ""))
            if key in existing:
                continue
            st.memory.findings_log.append({**item, "ts": datetime.now().isoformat()})
            existing.add(key)
        out = findings_dir(st.workdir) / "verify_high_signal.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        st.memory.save()
    lines = [result.get("summary") or "verify_high_signal done"]
    for lead in leads[:12]:
        lines.append(
            f"- [{lead.get('severity')}] {lead.get('vul_class')} @ {lead.get('path')}"
        )
    obs = "\n".join(lines)
    await _record(st, lock, "run_verify_leads", obs)
    return {"name": "run_verify_leads", "ok": True, "obs": obs, "leads": len(leads)}


async def _run_vuln(
    st: BughuntRunState,
    lock: asyncio.Lock,
    settings: Any,
    *,
    quick: bool,
) -> dict[str, Any]:
    obs, _ran = await arsenal.vuln_scan(
        st.workdir,
        quick=quick,
        full=not quick,
        settings=settings,
        on_log=st.on_log,
    )
    await _record(st, lock, "run_vuln_scan", obs)
    return {"name": "run_vuln_scan", "ok": True, "obs": obs[:500]}


async def _run_cors(st: BughuntRunState, lock: asyncio.Lock, settings: Any) -> dict[str, Any]:
    obs, _ran = await arsenal.cors_check(
        st.workdir, cookies=st.cookies, settings=settings, on_log=st.on_log,
    )
    await _record(st, lock, "run_cors_check", obs)
    return {"name": "run_cors_check", "ok": True, "obs": obs[:500]}


async def _run_secrets(st: BughuntRunState, lock: asyncio.Lock, settings: Any) -> dict[str, Any]:
    obs, _ran = await arsenal.secret_hunt(
        st.workdir, settings=settings, on_log=st.on_log,
    )
    await _record(st, lock, "run_secret_hunt", obs)
    return {"name": "run_secret_hunt", "ok": True, "obs": obs[:500]}


async def _run_api_fuzz(st: BughuntRunState, lock: asyncio.Lock, settings: Any) -> dict[str, Any]:
    obs, _ran = await arsenal.api_fuzz(
        st.workdir, settings=settings, on_log=st.on_log,
    )
    await _record(st, lock, "run_api_fuzz", obs)
    return {"name": "run_api_fuzz", "ok": True, "obs": obs[:500]}


async def run_parallel_hunt_wave(
    st: BughuntRunState,
    *,
    settings: Any | None = None,
    include_vuln: bool = True,
    include_cors: bool = True,
    include_secrets: bool = True,
    include_api_fuzz: bool = True,
) -> dict[str, Any]:
    """Fan-out independent post-recon scanners + verify."""
    if settings is None:
        from ..config import get_settings
        settings = get_settings()

    lab = _is_ip_lab_target(st.target)
    sem = asyncio.Semaphore(_wave_concurrency(settings))
    lock = asyncio.Lock()
    if st.on_log:
        st.on_log(
            "sys",
            f"parallel_hunt_wave start concurrency={_wave_concurrency(settings)} lab={lab}",
        )

    async def _wrap(name: str, coro):
        async with sem:
            if st.on_log:
                st.on_log("sys", f"wave ▶ {name}")
            try:
                return await coro
            except Exception as exc:  # noqa: BLE001
                logger.warning("wave %s failed: %s", name, exc)
                err = f"{name} error: {type(exc).__name__}: {exc}"
                await _record(st, lock, name, err)
                return {"name": name, "ok": False, "obs": err}

    tasks = [
        _wrap("run_verify_leads", _run_verify(st, lock)),
    ]
    if include_vuln:
        tasks.append(_wrap(
            "run_vuln_scan",
            _run_vuln(st, lock, settings, quick=lab),
        ))
    if include_cors:
        tasks.append(_wrap("run_cors_check", _run_cors(st, lock, settings)))
    if include_secrets:
        tasks.append(_wrap("run_secret_hunt", _run_secrets(st, lock, settings)))
    if include_api_fuzz and not lab:
        # api_fuzz (zero_day + nuclei) is expensive; skip on tiny lab by default
        tasks.append(_wrap("run_api_fuzz", _run_api_fuzz(st, lock, settings)))
    elif include_api_fuzz and lab:
        # still run fuzzer on lab — often fast and useful
        tasks.append(_wrap("run_api_fuzz", _run_api_fuzz(st, lock, settings)))

    results = await asyncio.gather(*tasks)
    ok_n = sum(1 for r in results if r.get("ok"))
    summary = (
        f"parallel_hunt_wave: {ok_n}/{len(results)} ok; "
        + ", ".join(f"{r.get('name')}={'ok' if r.get('ok') else 'fail'}" for r in results)
    )
    if st.on_log:
        st.on_log("sys", summary)
    async with lock:
        if "run_parallel_hunt_wave" not in st.memory.completed_steps:
            st.memory.completed_steps.append("run_parallel_hunt_wave")
        st.memory.save()
    return {
        "ok": ok_n > 0,
        "summary": summary,
        "results": results,
        "lab": lab,
    }
