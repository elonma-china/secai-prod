"""HTTP client cho phân hệ PoC Exploit API (port 5568).

Credentials (apiKey) sống phía 5568 (`/tmp/proxy_node_credentials.json`) — gateway chỉ gọi
register/status/rotate/exploits_cve, không giữ key.

Xem docs/poc-api-guide.md."""
from __future__ import annotations

from typing import Any

import httpx

from .backend import PocBadRequest, PocError, PocForbidden, PocNotFound


class KtClientError(PocError):
    """Lỗi vận hành khi gọi 5568 (timeout, 5xx, JSON hỏng)."""


class KtClient:
    def __init__(self, base_url: str, *, timeout_s: float = 180.0,
                 node_name: str = "secai-gateway",
                 client: httpx.AsyncClient | None = None) -> None:
        self.base_url = (base_url or "").rstrip("/")
        if not self.base_url:
            raise PocBadRequest("POC_EXPLOIT_API_URL trống")
        self.timeout_s = timeout_s
        self.node_name = node_name
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

    async def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        http = await self._http()
        try:
            return await http.request(method, path, **kwargs)
        except httpx.TimeoutException as exc:
            raise KtClientError(f"timeout gọi {path}: {exc}") from exc
        except httpx.HTTPError as exc:
            raise KtClientError(f"HTTP error {path}: {exc}") from exc

    async def health(self) -> dict[str, Any]:
        r = await self._request("GET", "/health")
        r.raise_for_status()
        return r.json()

    async def status(self) -> dict[str, Any]:
        r = await self._request("GET", "/proxy/status")
        if r.status_code >= 400:
            raise KtClientError(f"proxy/status HTTP {r.status_code}: {r.text[:300]}")
        return r.json()

    async def register(self, name: str | None = None) -> dict[str, Any]:
        r = await self._request("POST", "/proxy/register",
                                json={"name": name or self.node_name})
        if r.status_code >= 400:
            raise KtClientError(f"proxy/register HTTP {r.status_code}: {r.text[:300]}")
        return r.json()

    async def rotate(self) -> dict[str, Any]:
        r = await self._request("POST", "/proxy/rotate")
        if r.status_code >= 400:
            raise KtClientError(f"proxy/rotate HTTP {r.status_code}: {r.text[:300]}")
        return r.json()

    async def ensure_registered(self) -> dict[str, Any]:
        """GET /proxy/status; nếu chưa sẵn sàng → POST /proxy/register rồi status lại."""
        try:
            st = await self.status()
        except KtClientError:
            st = {}
        if st.get("registered") and st.get("api_key_available"):
            return st
        await self.register()
        return await self.status()

    async def exploits_cve(
        self,
        *,
        ip: str,
        cve: str,
        port: str | None = None,
        command: str = "id",
        use_proxy: bool = True,
        max_retries: int = 3,
        sticky_seconds: int = 300,
        use_socks5: bool = False,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "ip": ip,
            "cve": cve,
            "command": command or "id",
            "use_proxy": "1" if use_proxy else "0",
            "max_retries": max_retries,
            "sticky_seconds": sticky_seconds,
            "use_socks5": "1" if use_socks5 else "0",
        }
        if port is not None and str(port).strip() != "":
            params["port"] = str(port).strip()

        r = await self._request("GET", "/exploits_cve", params=params)
        try:
            body = r.json()
        except ValueError:
            body = {"error": r.text[:500], "raw": True}

        if r.status_code == 404:
            raise PocNotFound(body.get("error") or f"No PoC/MSF for {cve}")
        if r.status_code == 503:
            raise PocForbidden(body.get("error") or "Proxy node not registered")
        if r.status_code == 400:
            raise PocBadRequest(body.get("error") or "bad request to exploits_cve")
        if r.status_code >= 400:
            raise KtClientError(body.get("error") or f"exploits_cve HTTP {r.status_code}")
        return body if isinstance(body, dict) else {"result": body}
