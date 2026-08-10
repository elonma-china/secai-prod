"""Client gọi container executor bằng `docker run`.

Hai chế độ:
- `run_executor` — HARDENED (§5): ephemeral, read-only + tmpfs, cap-drop ALL, no-new-privileges,
  giới hạn pids/mem/cpu. Dùng cho verify + scoped tools.
- `run_raw` — RAW/PRIVILEGED: chạy lệnh BẤT KỲ (kể cả tấn công) với `--privileged` + root, rootfs
  ghi được, đầy đủ cap/devices. KHÔNG sandbox. Chỉ dùng qua endpoint /ai/exec (gated exec_enabled +
  admin API key). Đây là RCE có chủ đích theo yêu cầu — bật = tin tưởng hoàn toàn caller.
KHÔNG có logic verdict — chỉ chạy rồi trả output thô.

── Nhiều người dùng đồng thời ──────────────────────────────────────────────────
Mỗi lệnh là 1 `docker run --rm` biệt lập (container mới, tự huỷ) → KHÔNG có state/container dùng
chung giữa các request, nên không có đường trộn lẫn output giữa 2 user. Hai rủi ro còn lại được xử
lý ở đây:
  1. Quá tải host khi nhiều container chạy cùng lúc → `_semaphore()` giới hạn số `docker run` chạy
     song song trên toàn tiến trình (không phải per-user) — vượt trần thì CHỜ, không rớt request.
  2. Truy vết đúng request nào ra container/kết quả nào → mỗi container được đặt `--name` từ
     `run_id` (nếu có) + hậu tố ngẫu nhiên, để `docker ps`/log phân biệt được ngay cả khi 1 run_id
     gọi nhiều lệnh (multi-turn) hoặc nhiều run_id chạy đồng thời."""
from __future__ import annotations

import asyncio
import re
import uuid
from dataclasses import dataclass

_NAME_SAFE = re.compile(r"[^a-zA-Z0-9_.-]")

_sem: asyncio.Semaphore | None = None
_sem_size: int | None = None


def _semaphore(max_concurrent: int) -> asyncio.Semaphore:
    """Semaphore DÙNG CHUNG cho cả tiến trình (không phải theo settings instance — Settings được
    dựng mới mỗi lần get_settings(), xem config.py). Đổi kích cỡ giữa các lần gọi (hiếm, thường
    chỉ khi test đổi env) sẽ dựng lại semaphore mới."""
    global _sem, _sem_size
    if _sem is None or _sem_size != max_concurrent:
        _sem = asyncio.Semaphore(max_concurrent)
        _sem_size = max_concurrent
    return _sem


def _container_name(run_id: str) -> list[str]:
    if not run_id:
        return []
    safe = _NAME_SAFE.sub("-", run_id)[:40]
    return ["--name", f"secai-{safe}-{uuid.uuid4().hex[:8]}"]


@dataclass(frozen=True)
class ExecConfig:
    image: str = "secai-kali-executor:latest"
    timeout_s: int = 120
    net: str = "bridge"
    mem: str = "1g"
    cpus: str = "2"
    pids: int = 256
    max_concurrent: int = 4


@dataclass(frozen=True)
class ExecResult:
    rc: int
    stdout: str
    stderr: str
    timed_out: bool = False


async def _run(cmd: list[str], timeout_s: int, max_concurrent: int) -> ExecResult:
    async with _semaphore(max_concurrent):
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        except FileNotFoundError:
            return ExecResult(rc=127, stdout="", stderr="docker CLI không có trên host gateway")
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout_s + 30)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return ExecResult(rc=124, stdout="", stderr="executor timed out (host backstop)", timed_out=True)
    return ExecResult(
        rc=proc.returncode if proc.returncode is not None else 0,
        stdout=out.decode("utf-8", "replace"),
        stderr=err.decode("utf-8", "replace"),
    )


def _hardened_flags(cfg: ExecConfig) -> list[str]:
    return [
        "--read-only",
        "--tmpfs", "/tmp:rw,size=256m,mode=1777",
        "--tmpfs", "/work:rw,size=256m,mode=1777",
        "--tmpfs", "/home/runner:rw,size=128m,mode=1777",
        "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges",
        "--pids-limit", str(cfg.pids),
    ]


async def run_executor(cfg: ExecConfig, args: list[str],
                       mounts: list[tuple[str, str]] | None = None, *, run_id: str = "") -> ExecResult:
    """HARDENED: `docker run <hardened> IMAGE <args>`. args = subcommand entrypoint + tham số;
    mounts luôn read-only. `run_id` (tùy chọn) gắn vào tên container để truy vết/đối soát khi
    nhiều người dùng gọi đồng thời — không ảnh hưởng hành vi lệnh."""
    cmd = ["docker", "run", "--rm", *_container_name(run_id), "--network", cfg.net, *_hardened_flags(cfg)]
    for host, cont in (mounts or []):
        cmd += ["-v", f"{host}:{cont}:ro"]
    cmd += ["--memory", cfg.mem, "--cpus", cfg.cpus, "-e", f"EXEC_TIMEOUT={cfg.timeout_s}", cfg.image, *args]
    return await _run(cmd, cfg.timeout_s, cfg.max_concurrent)


async def run_raw(cfg: ExecConfig, *, argv: list[str] | None = None, shell: str | None = None,
                  privileged: bool = True, mounts: list[tuple[str, str]] | None = None,
                  run_id: str = "") -> ExecResult:
    """RAW: chạy lệnh bất kỳ trong container. `privileged=True` → `--privileged` + root + rootfs
    ghi được (KHÔNG sandbox). `shell` chạy qua `bash -lc`; hoặc `argv` chạy exec trực tiếp. Vẫn giữ
    timeout + mem/cpu + semaphore + tên container theo `run_id` như `run_executor`."""
    cmd = ["docker", "run", "--rm", *_container_name(run_id), "--network", cfg.net]
    if privileged:
        cmd += ["--privileged", "--user", "0:0"]     # root + toàn bộ cap/devices, rootfs ghi được
    else:
        cmd += _hardened_flags(cfg)
    for host, cont in (mounts or []):
        cmd += ["-v", f"{host}:{cont}"]              # raw: mount ghi được (không ép :ro)
    cmd += ["--memory", cfg.mem, "--cpus", cfg.cpus, "-e", f"EXEC_TIMEOUT={cfg.timeout_s}", cfg.image]
    if shell is not None:
        cmd += ["raw", "bash", "-lc", shell]
    elif argv:
        cmd += ["raw", *argv]
    else:
        raise ValueError("cần 'argv' hoặc 'shell'")
    return await _run(cmd, cfg.timeout_s, cfg.max_concurrent)
