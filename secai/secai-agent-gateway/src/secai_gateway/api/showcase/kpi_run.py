"""KPI Studio "Live" run — `/showcase-api/kpi-run/*`.

Khác hẳn `kpi_bench.py` (chỉ REPLAY lại 1 bản ghi hồ sơ gốc đã đóng băng, phát log theo nhịp
giả lập): module này chạy THẬT các script CLI bench (`KPI-VA-GEN-TEST/02-GEN-TEST/bench_kpi.py`
cho nhóm B/C, `KPI-VA-GEN-TEST/scripts/bench.sh` cho nhóm A), stream nguyên văn stdout/stderr
của subprocess qua SSE (không phát nhịp giả), rồi đọc kết quả THẬT của lần chạy đó (xem
`_kpi_results.py`). Đúng đặc tả wire-format ở `KPI-RUN-CONTRACT.md` (dùng chung cho cả FE lẫn
BE, không đổi thêm gì ở đây).

Chỉ cho phép 1 lần chạy LIVE tại một thời điểm trên toàn gateway (bench thật gọi LLM/embedding/
reranker thật — tốn tiền/tài nguyên, và 2 lần chạy chồng lấn sẽ ghi đè lẫn nhau file --out/
runs/latest của KPI-VA-GEN-TEST) — xem `_RunState`/`_STATE` bên dưới.
"""

from __future__ import annotations

import asyncio
import json as _json
import os
import shlex
import signal
import tempfile
import time
from collections.abc import AsyncIterator
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse

from ...config import get_settings
from ._common import bad_request, not_found
from ._kpi_results import (
    all_kpi_ids,
    build_a_results,
    build_bc_results,
    group_of,
    kpi_ids_for_group,
)

router = APIRouter(prefix="/showcase-api/kpi-run", tags=["showcase-kpi-run"])

_GROUP_LETTERS = {"A", "B", "C"}
_HISTORY_CAP = 100


def _conflict(message: str) -> HTTPException:
    """409 — cùng envelope {"error","message"} với `not_found`/`bad_request` (_common.py)."""
    return HTTPException(status_code=409, detail={"error": "already_running", "message": message})


# ─────────────────────────────────────────────────────────────────────────────
# Run-lock: chỉ 1 lần chạy LIVE tại một thời điểm. Toàn bộ chỉnh sửa `_STATE` diễn ra trong
# thân coroutine của CHÍNH lần chạy đang sở hữu nó (kể cả reset lúc xong) — /cancel chỉ gửi
# tín hiệu kill cho subprocess, KHÔNG tự reset _STATE, để tránh race: nếu /cancel reset xong
# rồi 1 request /stream MỚI chiếm lock trong lúc generator CŨ vẫn đang ở đoạn dọn dẹp cuối,
# generator cũ có thể ghi đè _STATE.proc của lần chạy MỚI. Vì handler chạy trên 1 event loop
# đơn luồng, việc "check running rồi set" trong `_try_acquire` (không có `await` ở giữa) là
# atomic — không cần thêm asyncio.Lock.
# ─────────────────────────────────────────────────────────────────────────────
class _RunState:
    def __init__(self) -> None:
        self.running = False
        self.run_id: str | None = None
        self.scope: str | None = None
        self.group: str | None = None
        self.profile: str | None = None
        self.targets: list[str] = []
        self.started_at_iso: str | None = None
        self._t0: float = 0.0
        self.proc: asyncio.subprocess.Process | None = None

    def start(self, run_id: str, scope: str, group: str | None, profile: str,
             targets: list[str]) -> None:
        self.running = True
        self.run_id = run_id
        self.scope = scope
        self.group = group
        self.profile = profile
        self.targets = targets
        self.started_at_iso = datetime.now().isoformat()
        self._t0 = time.monotonic()
        self.proc = None

    def reset(self) -> None:
        self.running = False
        self.run_id = None
        self.scope = None
        self.group = None
        self.profile = None
        self.targets = []
        self.started_at_iso = None
        self._t0 = 0.0
        self.proc = None

    def elapsed_ms(self) -> int:
        return int((time.monotonic() - self._t0) * 1000) if self._t0 else 0

    def snapshot(self) -> dict[str, Any]:
        if not self.running:
            return {"running": False}
        return {
            "running": True, "run_id": self.run_id, "scope": self.scope, "group": self.group,
            "profile": self.profile, "targets": self.targets,
            "started_at": self.started_at_iso, "elapsed_ms": self.elapsed_ms(),
        }


_STATE = _RunState()


def _try_acquire(run_id: str, scope: str, group: str | None, profile: str,
                 targets: list[str]) -> bool:
    if _STATE.running:
        return False
    _STATE.start(run_id, scope, group, profile, targets)
    return True


def _kill_proc() -> None:
    """Gửi SIGTERM cho cả process-group của subprocess hiện tại (bash bench.sh tự spawn
    nhiều python3 con tuần tự — kill mỗi bash để lại con orphan) rồi hẹn SIGKILL sau 5s nếu
    vẫn còn sống. `start_new_session=True` lúc spawn (xem `_spawn`) là điều kiện để có
    process-group riêng cho `os.killpg`."""
    proc = _STATE.proc
    if proc is None or proc.returncode is not None:
        return
    pid = proc.pid
    try:
        pgid = os.getpgid(pid)
    except ProcessLookupError:
        return
    try:
        os.killpg(pgid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError, OSError):
        pass

    async def _escalate() -> None:
        await asyncio.sleep(5.0)
        if proc.returncode is None:
            try:
                os.killpg(pgid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError, OSError):
                pass

    asyncio.create_task(_escalate())


# ─────────────────────────────────────────────────────────────────────────────
# Env merge — xem CONTRACT §Execution mapping. Thứ tự ưu tiên (sau ghi đè trước):
#   os.environ  →  parse .env.bench  →  Settings-derived pairs (chỉ khi Settings có giá trị).
# ─────────────────────────────────────────────────────────────────────────────
def _parse_env_file(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.is_file():
        return out
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
            val = val[1:-1]
        if key:
            out[key] = val
    return out


def _merged_env(settings: Any) -> dict[str, str]:
    scripts_dir = Path(settings.kpi_bench_scripts_dir)
    env_file = (Path(settings.kpi_bench_env_file) if settings.kpi_bench_env_file
                else scripts_dir / ".env.bench")
    env: dict[str, str] = dict(os.environ)
    env.update(_parse_env_file(env_file))

    provider = str(settings.llm_provider or "").lower()
    if provider == "deepseek":
        llm_base, llm_model, llm_key = (settings.deepseek_base_url, settings.deepseek_model,
                                         settings.deepseek_api_key)
    else:
        llm_base, llm_model, llm_key = (settings.qwen_base_url, settings.qwen_model,
                                         settings.qwen_api_key)

    overlay = {
        # Ép Python (bench_kpi.py + các script con của bench.sh) xả stdout theo dòng ngay,
        # nếu không CPython block-buffer khi stdout là pipe → SSE "line" dồn 1 cục ở cuối
        # (mất ý nghĩa "xem chạy trực tiếp", nhóm A full có thể im hàng giờ).
        "PYTHONUNBUFFERED": "1",
        "ACC_BASE": "http://127.0.0.1:8131",
        "LLM_BASE_URL": llm_base, "LLM_MODEL": llm_model, "LLM_API_KEY": llm_key,
        "EMBED_URL": settings.embedding_api_url, "EMBED_MODEL": settings.embedding_model,
        "EMBED_KEY": settings.embedding_api_key, "EMBEDDING_API_KEY": settings.embedding_api_key,
        "RERANK_URL": settings.reranker_api_url, "RERANK_MODEL": settings.reranker_model,
        "RERANK_KEY": settings.reranker_api_key, "RERANKER_API_KEY": settings.reranker_api_key,
        "QDRANT_URL": settings.qdrant_url, "QDRANT_COLLECTION": settings.qdrant_collection,
        "OPENAI_API_KEY": settings.deepseek_api_key,
        "RAGAS_JUDGE_BASE_URL": settings.deepseek_base_url,
        "RAGAS_JUDGE_MODEL": settings.deepseek_model,
    }
    for key, val in overlay.items():
        if val:  # không xoá giá trị đang chạy tốt trong .env.bench bằng 1 Settings rỗng
            env[key] = str(val)
    return env


def _write_tmp_env(env: dict[str, str], run_id: str) -> Path:
    """Ghi merged env ra file tạm cho `bench.sh --env` — bench.sh tự `. "$ENVF"` (source lại,
    xem CONTRACT), nên giá trị process env không đủ, PHẢI truyền qua file này. Mỗi value được
    `shlex.quote` để bash source an toàn (giá trị có khoảng trắng/ký tự đặc biệt)."""
    fd, path_str = tempfile.mkstemp(prefix=f"kpi-run-{run_id}-", suffix=".env")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        for key, val in env.items():
            f.write(f"{key}={shlex.quote(str(val))}\n")
    return Path(path_str)


def _sse(event: str, payload: dict[str, Any]) -> bytes:
    return f"event: {event}\ndata: {_json.dumps(payload, ensure_ascii=False)}\n\n".encode()


def _log_writer(settings: Any):
    logpath = Path(settings.showcase_kpi_log)
    logpath.parent.mkdir(parents=True, exist_ok=True)

    def _log(line: str) -> None:
        ts = datetime.now().strftime("%H:%M:%S")
        with logpath.open("a", encoding="utf-8") as f:
            f.write(f"[{ts}] {line}\n")

    return _log


# ─────────────────────────────────────────────────────────────────────────────
# History (khác localStorage phía FE) — settings.kpi_run_history_file, mảng JSON newest-first.
# ─────────────────────────────────────────────────────────────────────────────
def _history_path(settings: Any) -> Path:
    p = Path(settings.kpi_run_history_file)
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _load_history(settings: Any) -> list[dict[str, Any]]:
    p = _history_path(settings)
    if not p.is_file():
        return []
    try:
        data = _json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — file hỏng thì coi như rỗng, không sập /runs
        return []
    return data if isinstance(data, list) else []


def _append_history(settings: Any, record: dict[str, Any]) -> None:
    history = _load_history(settings)
    history.insert(0, record)
    history = history[:_HISTORY_CAP]
    p = _history_path(settings)
    tmp = p.with_name(p.name + f".tmp{os.getpid()}")
    tmp.write_text(_json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(p)  # ghi atomically, tránh hỏng file nếu tiến trình bị ngắt giữa chừng


def _read_json_or_none(path: Path) -> Any | None:
    if not path.is_file():
        return None
    try:
        return _json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


# ─────────────────────────────────────────────────────────────────────────────
# GET /stream — SSE, chạy subprocess thật.
# ─────────────────────────────────────────────────────────────────────────────
async def _gen(run_id: str, scope: str, group: str | None, profile: str, limit: int,
               targets: list[str], settings: Any) -> AsyncIterator[bytes]:
    log = _log_writer(settings)
    t0 = time.monotonic()
    tmp_paths: list[Path] = []
    scripts_dir = Path(settings.kpi_bench_scripts_dir)

    try:
        env = _merged_env(settings)

        need_bc = (scope in ("single", "group") and group in ("B", "C")) or scope == "all"
        need_a = (scope == "group" and group == "A") or scope == "all"

        plan: list[dict[str, Any]] = []
        bc_out_path: Path | None = None

        if need_bc:
            group_arg = "bc" if scope == "all" else group.lower()  # type: ignore[union-attr]
            fd, out_str = tempfile.mkstemp(prefix=f"kpi-run-{run_id}-bc-", suffix=".json")
            os.close(fd)
            bc_out_path = Path(out_str)
            tmp_paths.append(bc_out_path)
            bc_script = scripts_dir / "02-GEN-TEST" / "bench_kpi.py"
            bc_cmd = [settings.kpi_bench_python, "-u", str(bc_script), "--group", group_arg,
                      "--out", str(bc_out_path)]
            plan.append({"label": f"bench_kpi.py --group {group_arg}", "cmd": bc_cmd,
                        "cwd": scripts_dir / "02-GEN-TEST"})

        if need_a:
            if profile == "full":
                log(f"[CẢNH BÁO] run_id={run_id}: nhóm A profile=full có thể mất 1–4 giờ.")
            env_tmp_path = _write_tmp_env(env, run_id)
            tmp_paths.append(env_tmp_path)
            a_cmd = ["bash", "scripts/bench.sh"]
            if profile == "smoke":
                a_cmd += ["--limit", str(limit)]
            a_cmd += ["--env", str(env_tmp_path)]
            plan.append({"label": f"bench.sh (nhóm A, profile={profile})", "cmd": a_cmd,
                        "cwd": scripts_dir})

        cmd_summary = " && ".join(" ".join(p["cmd"]) for p in plan) if plan else "(không có lệnh)"

        log(f"===== BẮT ĐẦU KPI-RUN LIVE run_id={run_id} scope={scope} group={group} "
            f"profile={profile} targets={targets} =====")
        yield _sse("start", {"run_id": run_id, "scope": scope, "group": group, "profile": profile,
                             "targets": targets, "cmd_summary": cmd_summary,
                             "started_at": _STATE.started_at_iso})

        total_phases = max(1, len(plan))
        for i, step in enumerate(plan):
            log(f"----- phase {i + 1}/{len(plan)}: {step['label']} (cwd={step['cwd']}) -----")
            yield _sse("progress", {"pct": round(i / total_phases * 100, 1), "phase": step["label"]})

            try:
                proc = await asyncio.create_subprocess_exec(
                    *step["cmd"], cwd=str(step["cwd"]), env=env,
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
                    start_new_session=True,
                )
            except (FileNotFoundError, OSError) as exc:
                msg = f"Không chạy được lệnh cho phase '{step['label']}': {exc}"
                log(f"[LỖI] {msg}")
                yield _sse("error", {"message": msg, "run_id": run_id})
                return

            _STATE.proc = proc
            if proc.stdout is not None:
                async for raw in proc.stdout:
                    text = raw.decode(errors="replace").rstrip("\n")
                    log(text)
                    yield _sse("line", {"text": text})
            rc = await proc.wait()
            _STATE.proc = None

            if rc != 0:
                msg = f"Phase '{step['label']}' thoát lỗi (exit code {rc})."
                log(f"[LỖI] {msg}")
                yield _sse("error", {"message": msg, "run_id": run_id})
                return

        yield _sse("progress", {"pct": 95.0, "phase": "đang gom kết quả"})

        results: list[dict[str, Any]] = []
        run_dir: str | None = None

        if need_bc:
            flat = _read_json_or_none(bc_out_path) if bc_out_path else None
            if flat is None:
                msg = f"Không tìm thấy (hoặc không đọc được) file kết quả nhóm B/C: {bc_out_path}"
                log(f"[LỖI] {msg}")
                yield _sse("error", {"message": msg, "run_id": run_id})
                return
            bc_ids = [t for t in targets if group_of(t) in ("B", "C")]
            results += build_bc_results(bc_ids, flat)

        if need_a:
            latest = scripts_dir / "runs" / "latest"
            summary_paths = list(latest.rglob("kpi-summary.json")) if latest.exists() else []
            if not summary_paths:
                msg = (f"Không tìm thấy kpi-summary.json dưới {latest} sau khi bench.sh chạy "
                       f"xong (rc=0).")
                log(f"[LỖI] {msg}")
                yield _sse("error", {"message": msg, "run_id": run_id})
                return
            kpi_summary = _read_json_or_none(summary_paths[0]) or {}
            a_ids = [t for t in targets if group_of(t) == "A"]
            results += build_a_results(a_ids, kpi_summary)
            run_dir = str(latest.resolve())

        ok_count = sum(1 for r in results if r.get("pass"))
        total = len(results)
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        done_payload = {"run_id": run_id, "scope": scope, "results": results, "ok_count": ok_count,
                        "total": total, "run_dir": run_dir, "elapsed_ms": elapsed_ms}
        log(f"===== KẾT THÚC KPI-RUN LIVE run_id={run_id}: {ok_count}/{total} ĐẠT "
            f"(elapsed={elapsed_ms}ms) =====")
        _append_history(settings, {
            "run_id": run_id, "at": datetime.now().isoformat(), "scope": scope, "group": group,
            "profile": profile, "source": "live", "ok_count": ok_count, "total": total,
            "model": env.get("LLM_MODEL"), "results": results, "run_dir": run_dir,
        })
        yield _sse("done", done_payload)

    except asyncio.CancelledError:
        # Client ngắt kết nối (hoặc server bị cancel) giữa chừng — kill subprocess đang chạy
        # rồi để CancelledError lan tiếp (không nuốt) cho Starlette dọn dẹp response đúng cách.
        log(f"[HUỶ] run_id={run_id}: client ngắt kết nối hoặc bị huỷ — kill subprocess.")
        _kill_proc()
        raise
    except Exception as exc:  # noqa: BLE001 — không để lộ traceback thô ra SSE, nhưng vẫn báo lỗi
        log(f"[LỖI KHÔNG BẮT ĐƯỢC] run_id={run_id}: {exc!r}")
        try:
            yield _sse("error", {"message": f"Lỗi không mong đợi: {exc}", "run_id": run_id})
        except Exception:  # noqa: BLE001 — generator có thể đã đóng, không lan lỗi thứ 2
            pass
    finally:
        for p in tmp_paths:
            try:
                p.unlink(missing_ok=True)
            except OSError:
                pass
        _STATE.reset()


@router.get("/stream")
async def stream(
    scope: str = Query(..., description="single|group|all"),
    test_id: str | None = Query(None),
    group: str | None = Query(None),
    profile: str = Query("smoke", description="smoke|full — chỉ ảnh hưởng nhóm A"),
    limit: int | None = Query(None, description="cỡ mẫu smoke nhóm A"),
) -> StreamingResponse:
    settings = get_settings()
    scope = scope.strip().lower()
    if scope not in ("single", "group", "all"):
        raise bad_request(f"scope không hợp lệ: '{scope}' (chỉ nhận single|group|all).")
    profile = (profile or "smoke").strip().lower()
    if profile not in ("smoke", "full"):
        raise bad_request(f"profile không hợp lệ: '{profile}' (chỉ nhận smoke|full).")

    resolved_group: str | None = None
    targets: list[str] = []

    if scope == "single":
        if not test_id:
            raise bad_request("scope=single cần test_id.")
        kpi_group = group_of(test_id)
        if kpi_group is None:
            raise not_found(f"Không tìm thấy test_id '{test_id}' trong catalog KPI Studio.")
        if kpi_group == "A":
            raise bad_request(f"test_id '{test_id}' thuộc nhóm A — dùng scope=group&group=A "
                              f"cho nhóm A (nhóm A luôn chạy cả nhóm, không tách từng KPI).")
        targets = [test_id]
        resolved_group = kpi_group
    elif scope == "group":
        if not group:
            raise bad_request("scope=group cần group=A|B|C.")
        group = group.strip().upper()
        if group not in _GROUP_LETTERS:
            raise not_found(f"Không có nhóm KPI '{group}' (chỉ A|B|C).")
        targets = kpi_ids_for_group(group)
        resolved_group = group
    else:  # all
        targets = all_kpi_ids()

    run_limit = limit if limit is not None else settings.kpi_bench_smoke_limit

    run_id = uuid4().hex
    if not _try_acquire(run_id, scope, resolved_group, profile, targets):
        raise _conflict("Đã có 1 lần chạy KPI live đang thực thi trên gateway này — đợi xong "
                        "hoặc gọi POST /showcase-api/kpi-run/cancel rồi thử lại.")

    return StreamingResponse(
        _gen(run_id, scope, resolved_group, profile, run_limit, targets, settings),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/status")
async def status() -> dict[str, Any]:
    return _STATE.snapshot()


@router.post("/cancel")
async def cancel() -> dict[str, Any]:
    if not _STATE.running or _STATE.proc is None:
        return {"cancelled": False}
    run_id = _STATE.run_id
    _kill_proc()
    return {"cancelled": True, "run_id": run_id}


@router.get("/runs")
async def list_runs() -> dict[str, Any]:
    settings = get_settings()
    history = _load_history(settings)
    runs = [
        {"run_id": r.get("run_id"), "at": r.get("at"), "scope": r.get("scope"),
         "group": r.get("group"), "profile": r.get("profile"), "source": r.get("source", "live"),
         "ok_count": r.get("ok_count"), "total": r.get("total"), "model": r.get("model")}
        for r in history
    ]
    return {"runs": runs}


@router.get("/runs/{run_id}")
async def get_run(run_id: str) -> dict[str, Any]:
    settings = get_settings()
    history = _load_history(settings)
    rec = next((r for r in history if r.get("run_id") == run_id), None)
    if rec is None:
        raise not_found(f"Không tìm thấy run_id '{run_id}' trong lịch sử live.")
    return {
        "run_id": rec.get("run_id"), "at": rec.get("at"), "scope": rec.get("scope"),
        "group": rec.get("group"), "profile": rec.get("profile"),
        "results": rec.get("results", []), "ok_count": rec.get("ok_count"),
        "total": rec.get("total"), "run_dir": rec.get("run_dir"),
    }
