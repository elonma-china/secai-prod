"""Pool nhiều instance `@ias/server` để chạy nhiều `exploit-v2` ĐỒNG THỜI an toàn.

Lý do cần pool thay vì đẩy song song lên 1 instance duy nhất: `EngagementStore`
của pentestcode là 1 `Ref` GLOBAL trong process
(packages/core/src/engagement/store.ts dùng `makeGlobalNode`) — MỌI session
chạy trên CÙNG 1 process bun chia sẻ 1 state "hiện tại" (hosts/vulns/flags/
changelog...). 2 session chạy đồng thời trên 1 process sẽ ghi đè flag/host của
nhau. Sửa trực tiếp Effect/TypeScript của pentestcode để engagement theo từng
session là thay đổi sâu + rủi ro cao trên code bên thứ 3 (tương tự đánh giá đã
chốt với "stuck terminal" bug trước đây) — thay vào đó mỗi "slot" trong pool là
1 PROCESS bun riêng, có `HOME` riêng nên `~/.ias/engagements`,
`~/.local/share/ias/log`, `~/.local/share/ias/ias-local.db` tách biệt hoàn
toàn giữa các slot, không cần đụng 1 dòng code pentestcode nào.

Cấu hình qua env `IAS_SERVER_POOL` (Settings.ias_server_pool): danh sách
`url|home` cách nhau bằng dấu phẩy, ví dụ
    IAS_SERVER_POOL=http://127.0.0.1:4096|/home/ict,http://127.0.0.1:4097|/tmp/ias-pool/slot1
Rỗng + có cấu hình pool ĐỘNG (IAS_POOL_GPU_COUNT > 0) -> tự sinh N slot trỏ
service compose `pentestcode-1`..`pentestcode-N` (xem `_dynamic_pool`). Rỗng +
KHÔNG có pool động -> pool 1 slot duy nhất dùng `ias_server_url` (back-compat
với setup cũ, không có pool) + `home` suy từ env `HOME` hiện tại của gateway.
"""
from __future__ import annotations

import asyncio
import os

Slot = dict[str, str]


def _dynamic_pool() -> list[Slot]:
    """Sinh pool slot từ `IAS_POOL_GPU_COUNT`/`IAS_POOL_SLOTS_PER_GPU`/
    `IAS_POOL_CHATBOT_RESERVE` (Settings) — N = GPU_COUNT × SLOTS_PER_GPU − CHATBOT_RESERVE,
    kẹp tối thiểu 1 nếu GPU_COUNT > 0. Mỗi slot trỏ service compose
    `pentestcode-{i}:4096`, home riêng dưới `/ias-pool-home` (bind-mount chung host
    dir vào gateway + mọi replica pentestcode, xem docker-compose.yml) — tách biệt
    EngagementStore giữa các slot, cùng lý do nêu ở đầu file.
    """
    from ..config import get_settings
    s = get_settings()
    gpu_count = getattr(s, "ias_pool_gpu_count", 0)
    if gpu_count <= 0:
        return []
    slots_per_gpu = getattr(s, "ias_pool_slots_per_gpu", 4)
    chatbot_reserve = getattr(s, "ias_pool_chatbot_reserve", 0)
    n = max(1, gpu_count * slots_per_gpu - chatbot_reserve)
    return [
        {"url": f"http://pentestcode-{i}:4096", "home": f"/ias-pool-home/pentestcode-{i}"}
        for i in range(1, n + 1)
    ]


def _parse_pool(raw: str, fallback_url: str) -> list[Slot]:
    raw = (raw or "").strip()
    if not raw:
        dynamic = _dynamic_pool()
        if dynamic:
            return dynamic
        if not fallback_url:
            return []
        return [{"url": fallback_url, "home": os.environ.get("HOME", "")}]
    slots: list[Slot] = []
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        url, _, home = item.partition("|")
        slots.append({"url": url.strip(), "home": home.strip()})
    return slots


_initialized = False
_all_slots: list[Slot] = []
_queue: asyncio.Queue[Slot] | None = None
_run_to_slot: dict[str, Slot] = {}


def _init(pool_raw: str, fallback_url: str) -> None:
    global _initialized, _all_slots, _queue
    if _initialized:
        return
    _all_slots = _parse_pool(pool_raw, fallback_url)
    _queue = asyncio.Queue()
    for slot in _all_slots:
        _queue.put_nowait(slot)
    _initialized = True


def pool_size(pool_raw: str, fallback_url: str) -> int:
    _init(pool_raw, fallback_url)
    return len(_all_slots)


async def acquire_slot(pool_raw: str, fallback_url: str) -> Slot:
    """Chờ tới khi có 1 slot rảnh — block nếu mọi slot đang bận với run khác
    (yield lại cho event loop, không busy-poll)."""
    _init(pool_raw, fallback_url)
    assert _queue is not None
    return await _queue.get()


def release_slot(slot: Slot) -> None:
    assert _queue is not None
    _queue.put_nowait(slot)


def bind_run(run_id: str, slot: Slot) -> None:
    """Ghi nhớ slot nào đang phục vụ `run_id` (= session_id pentestcode) — cho
    `/ai/exploit-v2/reply` biết gửi câu trả lời tới ĐÚNG instance khi có >1 slot."""
    _run_to_slot[run_id] = slot


def unbind_run(run_id: str) -> None:
    _run_to_slot.pop(run_id, None)


def slot_for_run(run_id: str) -> Slot | None:
    return _run_to_slot.get(run_id)
