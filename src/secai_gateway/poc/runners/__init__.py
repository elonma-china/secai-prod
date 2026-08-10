"""Executor runners — Mode A (ephemeral `docker run` vào secai-kali-executor).

TÁCH BIỆT hoàn toàn khỏi `LocalPocBackend` (giữ backend read-only). Toàn bộ đường "thực thi
PoC vào target" nằm ở gói này + `poc/verify.py`, bật/tắt bằng flag `verify_enabled` trong
config. Gỡ tính năng = tắt flag (hoặc xoá gói này + `poc/verify.py` và revert vài hook nhỏ).

Xem docs/poc-verify-architecture.md và ../../secai-kali-executor/.
"""
from .docker_exec import ExecConfig, ExecResult, run_executor, run_raw

__all__ = ["ExecConfig", "ExecResult", "run_executor", "run_raw"]
