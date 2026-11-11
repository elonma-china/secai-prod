"""HTTP client cho `@ias/server` (pentestcode / IAS.AI), sidecar riêng phục vụ `/ai/exploit-v2`.

Server chạy cục bộ, không expose ra ngoài (xem `IAS_SERVER_URL` trong config.py). Client này KHÔNG
giữ state phiên — mỗi exploit-v2 run tạo 1 session mới, đọc xong là xong (không resume giữa 2 lần
gọi HTTP khác process). `events()` mở 1 kết nối SSE dùng chung cho TOÀN BỘ run (step/log/question/
final đều đọc qua đây) — exploit_v2.py lọc theo `sessionID` trong payload của từng event.

Theo đúng style `kt_client.py` (cùng thư mục)."""
from __future__ import annotations

import json
from typing import Any, AsyncIterator

import httpx

from .backend import PocBadRequest, PocError


class IasClientError(PocError):
    """Lỗi vận hành khi gọi @ias/server (timeout, 5xx, SSE hỏng)."""


class IasClient:
    def __init__(self, base_url: str, *, timeout_s: float = 600.0,
                 client: httpx.AsyncClient | None = None) -> None:
        self.base_url = (base_url or "").rstrip("/")
        if not self.base_url:
            raise PocBadRequest("IAS_SERVER_URL trống")
        self.timeout_s = timeout_s
        self._client = client
        self._owns_client = client is None

    async def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(base_url=self.base_url, timeout=self.timeout_s)
        return self._client

    async def aclose(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    async def create_session(self, *, title: str | None = None,
                             agent: str = "poc_runner") -> str:
        """Tạo session mới, trả `sessionID`.
        agent="poc_runner" (2026-11-10): gọi thẳng subagent có workflow ExploitDB→nuclei.
        Không đi qua agent "pentest" + tool `task` — đường đó treo trên HTTP (2026-11-02).
        "webapp" solo không nạp poc-runner.txt nên không gọi EDB.
        """
        http = await self._http()
        payload: dict[str, Any] = {"agent": agent}
        if title:
            payload["title"] = title
        try:
            resp = await http.post("/session", json=payload)
        except httpx.HTTPError as exc:
            raise IasClientError(f"create_session: {exc}") from exc
        if resp.status_code >= 400:
            raise IasClientError(f"create_session HTTP {resp.status_code}: {resp.text[:500]}")
        data = resp.json()
        session_id = data.get("id") or data.get("sessionID")
        if not session_id:
            raise IasClientError(f"create_session: response thiếu id: {data!r}")
        return str(session_id)

    async def prompt_async(self, session_id: str, text: str, *, agent: str = "poc_runner") -> None:
        """Gửi prompt, KHÔNG chờ model trả lời — theo dõi tiến độ qua `events()`.

        `agent` phải truyền ở ĐÂY, không phải chỉ lúc `create_session` — pentestcode resolve
        agent cho mỗi turn từ `PromptInput.agent` của chính request này (session không nhớ lại
        agent đã khai báo lúc tạo cho mục đích này), nên thiếu field này mọi turn sẽ rơi về
        agent mặc định "pentest" (coordinator) → kích hoạt anti-solo-grind rail/`<delegate-now>`.
        """
        http = await self._http()
        payload = {"agent": agent, "parts": [{"type": "text", "text": text}]}
        try:
            resp = await http.post(f"/session/{session_id}/prompt_async", json=payload)
        except httpx.HTTPError as exc:
            raise IasClientError(f"prompt_async: {exc}") from exc
        if resp.status_code >= 400:
            raise IasClientError(f"prompt_async HTTP {resp.status_code}: {resp.text[:500]}")

    async def events(self) -> AsyncIterator[dict[str, Any]]:
        """Mở SSE `/event` toàn instance (không filter theo session ở server) — generator yield
        từng event dict `{id, type, properties}`. Caller (exploit_v2.py) tự lọc theo sessionID
        nằm trong `properties` của mỗi loại event (mỗi event-type có field khác nhau — xem
        exploit_v2.py._session_id_of)."""
        http = await self._http()
        try:
            async with http.stream("GET", "/event") as resp:
                if resp.status_code >= 400:
                    raise IasClientError(f"events HTTP {resp.status_code}")
                buf = ""
                async for chunk in resp.aiter_text():
                    buf += chunk
                    while "\n\n" in buf:
                        raw, buf = buf.split("\n\n", 1)
                        data_lines = [ln[len("data:"):].strip() for ln in raw.splitlines()
                                      if ln.startswith("data:")]
                        if not data_lines:
                            continue
                        try:
                            yield json.loads("".join(data_lines))
                        except json.JSONDecodeError:
                            continue
        except httpx.HTTPError as exc:
            raise IasClientError(f"events: {exc}") from exc

    async def abort_session(self, session_id: str) -> None:
        """`POST /session/:id/abort` — dừng session đang chạy. Best-effort: lỗi ở đây không nên
        chặn luồng gateway (gọi khi đã quyết định session bị kẹt/quá giờ, không còn gì để mất)."""
        http = await self._http()
        try:
            await http.post(f"/session/{session_id}/abort")
        except httpx.HTTPError:
            pass

    async def reply_question(self, request_id: str, answers: list[list[str]]) -> None:
        """`answers`: 1 list theo THỨ TỰ câu hỏi, mỗi phần tử là list nhãn đã chọn (free-text
        trả lời 1 câu → `[[answer_text]]`)."""
        http = await self._http()
        try:
            resp = await http.post(f"/question/{request_id}/reply", json={"answers": answers})
        except httpx.HTTPError as exc:
            raise IasClientError(f"reply_question: {exc}") from exc
        if resp.status_code >= 400:
            raise IasClientError(f"reply_question HTTP {resp.status_code}: {resp.text[:500]}")

    async def list_messages(self, session_id: str) -> list[dict[str, Any]]:
        """`GET /session/:id/message` — toàn bộ lịch sử message (info+parts) của session."""
        http = await self._http()
        try:
            resp = await http.get(f"/session/{session_id}/message")
        except httpx.HTTPError as exc:
            raise IasClientError(f"list_messages: {exc}") from exc
        if resp.status_code >= 400:
            raise IasClientError(f"list_messages HTTP {resp.status_code}: {resp.text[:500]}")
        data = resp.json()
        return data if isinstance(data, list) else []

    async def file_content(self, path: str, *, directory: str | None = None) -> str | None:
        """Đọc nội dung file agent đã `write` trong working directory của session. `None` nếu
        không tồn tại/lỗi (best-effort — caller fallback sang parse code từ message cuối nếu
        write không thành công)."""
        http = await self._http()
        params = {"path": path}
        if directory:
            params["directory"] = directory
        try:
            resp = await http.get("/file/content", params=params)
        except httpx.HTTPError:
            return None
        if resp.status_code >= 400:
            return None
        try:
            data = resp.json()
        except ValueError:
            return resp.text or None
        if isinstance(data, dict):
            return data.get("content") or data.get("text")
        return None
