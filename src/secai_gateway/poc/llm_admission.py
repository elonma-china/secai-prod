"""Điều tiết số run `exploit-v2` đồng thời theo tải thật của LLM.

Pool `ias_pool` là trần cứng (số container pentestcode). Mỗi run là 1 agent gọi LLM liên tục
hàng chục phút, dùng CHUNG endpoint với chat/dịch/báo cáo, nên chạy đầy pool khi LLM đang
nghẽn chỉ làm mọi API cùng chậm. Module này chỉ nhận thêm run mới khi:

- LLM trả lời `GET /models` (không thì run báo lỗi sau `exploit_v2_llm_down_grace_s` thay vì
  treo trong hàng chờ);
- hàng chờ của engine (`vllm:num_requests_waiting` / `sglang:num_queue_reqs`) không quá
  `exploit_v2_llm_max_waiting` và KV cache (`vllm:kv_cache_usage_perc` / `sglang:token_usage`)
  dưới `exploit_v2_llm_kv_high_water`;
- đã qua `exploit_v2_admit_spacing_s` kể từ run vừa nhận, để metrics kịp phản ánh tải mới.

Luôn cho tối thiểu `exploit_v2_min_active` run chạy khi LLM còn sống để exploit-v2 không bị
bỏ đói khi tải khác chiếm LLM. Không đọc được metrics thì giới hạn ở
`exploit_v2_unknown_max_active`. Hàng chờ là FIFO, có timeout `exploit_v2_queue_timeout_s`.
"""
from __future__ import annotations

import asyncio
import itertools
import logging
import math
import re
import time
from dataclasses import asdict, dataclass
from typing import Any, Awaitable, Callable

import httpx

logger = logging.getLogger(__name__)

_RUNNING = frozenset({"vllm:num_requests_running", "sglang:num_running_reqs"})
_WAITING = frozenset({"vllm:num_requests_waiting", "sglang:num_queue_reqs"})
_KV = frozenset({"vllm:kv_cache_usage_perc", "vllm:gpu_cache_usage_perc", "sglang:token_usage"})
_LINE_RE = re.compile(r"^([A-Za-z_:][A-Za-z0-9_:]*)(?:\{[^}]*\})?\s+(\S+)")
_PROBE_TIMEOUT_S = 3.0


@dataclass(frozen=True)
class LlmState:
    reachable: bool
    running: float | None = None
    waiting: float | None = None
    kv_usage: float | None = None
    detail: str = ""

    @property
    def has_metrics(self) -> bool:
        return self.waiting is not None or self.kv_usage is not None

    def as_dict(self) -> dict[str, Any]:
        return {**asdict(self), "has_metrics": self.has_metrics}


@dataclass(frozen=True)
class Limits:
    max_active: int
    enabled: bool = True
    max_waiting: int = 2
    kv_high_water: float = 0.85
    unknown_max_active: int = 2
    min_active: int = 1
    admit_spacing_s: float = 15.0
    probe_ttl_s: float = 5.0
    down_grace_s: float = 30.0
    queue_timeout_s: float = 900.0


@dataclass(frozen=True)
class Decision:
    admitted: bool
    reason: str
    position: int
    state: LlmState | None


def limits_from_settings(settings: Any, pool_size: int) -> Limits:
    return Limits(
        max_active=max(1, pool_size),
        enabled=settings.exploit_v2_llm_admission,
        max_waiting=settings.exploit_v2_llm_max_waiting,
        kv_high_water=settings.exploit_v2_llm_kv_high_water,
        unknown_max_active=settings.exploit_v2_unknown_max_active,
        min_active=settings.exploit_v2_min_active,
        admit_spacing_s=settings.exploit_v2_admit_spacing_s,
        probe_ttl_s=settings.exploit_v2_llm_probe_ttl_s,
        down_grace_s=settings.exploit_v2_llm_down_grace_s,
        queue_timeout_s=settings.exploit_v2_queue_timeout_s,
    )


def metrics_urls(base_url: str, raw: str) -> list[str]:
    raw = (raw or "").strip()
    if raw.lower() == "off":
        return []
    if raw:
        return [u.strip() for u in raw.split(",") if u.strip()]
    base = (base_url or "").strip().rstrip("/")
    if base.endswith("/v1"):
        base = base[:-3]
    return [base + "/metrics"] if base else []


def parse_metrics(text: str) -> dict[str, float]:
    """Cộng running/waiting trên mọi series (nhiều engine/replica), lấy KV cao nhất."""
    out: dict[str, float] = {}
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        m = _LINE_RE.match(line)
        if not m:
            continue
        name, raw = m.groups()
        try:
            value = float(raw)
        except ValueError:
            continue
        if math.isnan(value):
            continue
        if name in _RUNNING:
            out["running"] = out.get("running", 0.0) + value
        elif name in _WAITING:
            out["waiting"] = out.get("waiting", 0.0) + value
        elif name in _KV:
            out["kv"] = max(out.get("kv", 0.0), value)
    return out


async def probe_llm(base_url: str, api_key: str, urls: list[str]) -> LlmState:
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    async with httpx.AsyncClient(timeout=_PROBE_TIMEOUT_S) as client:
        try:
            resp = await client.get(base_url.rstrip("/") + "/models", headers=headers)
        except httpx.HTTPError as exc:
            return LlmState(reachable=False, detail=f"/models lỗi {type(exc).__name__}")
        if resp.status_code != 200:
            return LlmState(reachable=False, detail=f"/models HTTP {resp.status_code}")
        merged: dict[str, float] = {}
        seen = 0
        for url in urls:
            try:
                r = await client.get(url, headers=headers)
            except httpx.HTTPError:
                continue
            if r.status_code != 200:
                continue
            parsed = parse_metrics(r.text)
            if not parsed:
                continue
            seen += 1
            for key in ("running", "waiting"):
                if key in parsed:
                    merged[key] = merged.get(key, 0.0) + parsed[key]
            if "kv" in parsed:
                merged["kv"] = max(merged.get("kv", 0.0), parsed["kv"])
    return LlmState(reachable=True, running=merged.get("running"), waiting=merged.get("waiting"),
                    kv_usage=merged.get("kv"),
                    detail=f"metrics {seen}/{len(urls)} nguồn" if urls else "metrics tắt")


def decide(state: LlmState, active: int, limits: Limits, since_last_admit: float) -> tuple[bool, str]:
    """Quyết định thuần (không I/O) có nhận thêm 1 run khi đang có `active` run hay không."""
    if not state.reachable:
        return False, f"LLM không phản hồi ({state.detail})"
    if active < limits.min_active:
        return True, "ok"
    if since_last_admit < limits.admit_spacing_s:
        return False, "chờ LLM nhận tải của run vừa bắt đầu"
    if not state.has_metrics:
        if active >= limits.unknown_max_active:
            return False, f"không đọc được metrics LLM, giới hạn {limits.unknown_max_active} run"
        return True, "ok"
    if state.waiting is not None and state.waiting > limits.max_waiting:
        return False, f"LLM bận: {state.waiting:.0f} request đang chờ"
    if state.kv_usage is not None and state.kv_usage >= limits.kv_high_water:
        return False, f"LLM bận: KV cache {state.kv_usage:.0%}"
    return True, "ok"


class Admission:
    """Hàng chờ FIFO + bộ đếm run đang chạy, dùng chung cho mọi request trong process."""

    def __init__(self, probe: Callable[[], Awaitable[LlmState]],
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._probe = probe
        self._clock = clock
        self._lock = asyncio.Lock()
        self._ids = itertools.count(1)
        self._queue: list[int] = []
        self._state: LlmState | None = None
        self._state_at = -math.inf
        self._last_admit = -math.inf
        self.active = 0

    def enqueue(self) -> int:
        ticket = next(self._ids)
        self._queue.append(ticket)
        return ticket

    def cancel(self, ticket: int) -> None:
        if ticket in self._queue:
            self._queue.remove(ticket)

    def release(self) -> None:
        self.active = max(0, self.active - 1)

    @property
    def queued(self) -> int:
        return len(self._queue)

    async def _fresh_state(self, ttl: float) -> LlmState:
        if self._state is None or self._clock() - self._state_at >= ttl:
            try:
                self._state = await self._probe()
            except Exception as exc:  # probe không được làm hỏng hàng chờ
                logger.warning("exploit-v2 LLM probe failed: %s", exc)
                self._state = LlmState(reachable=False, detail=f"probe lỗi {type(exc).__name__}")
            self._state_at = self._clock()
        return self._state

    async def state(self, ttl: float) -> LlmState:
        async with self._lock:
            return await self._fresh_state(ttl)

    def since_last_admit(self) -> float:
        return self._clock() - self._last_admit

    async def try_admit(self, ticket: int, limits: Limits) -> Decision:
        async with self._lock:
            if ticket not in self._queue:
                raise ValueError(f"ticket {ticket} không còn trong hàng chờ")
            position = self._queue.index(ticket) + 1
            if position > 1:
                return Decision(False, f"còn {position - 1} run xếp trước", position, self._state)
            if self.active >= limits.max_active:
                return Decision(False, f"cả {limits.max_active} engine đang bận", position, self._state)
            if limits.enabled:
                state = await self._fresh_state(limits.probe_ttl_s)
                ok, reason = decide(state, self.active, limits, self.since_last_admit())
            else:
                state, ok, reason = self._state, True, "ok"
            if not ok:
                return Decision(False, reason, position, state)
            self._queue.pop(0)
            self.active += 1
            self._last_admit = self._clock()
            self._state_at = -math.inf  # run sau phải nhìn metrics mới
            logger.info("exploit-v2 admitted active=%d queued=%d llm=%s",
                        self.active, len(self._queue), state.as_dict() if state else None)
            return Decision(True, reason, 0, state)


_admission: Admission | None = None


async def _probe_from_settings() -> LlmState:
    from ..config import get_settings
    s = get_settings()
    return await probe_llm(s.qwen_base_url, s.qwen_api_key,
                           metrics_urls(s.qwen_base_url, s.exploit_v2_llm_metrics_urls))


def get_admission() -> Admission:
    global _admission
    if _admission is None:
        _admission = Admission(_probe_from_settings)
    return _admission
