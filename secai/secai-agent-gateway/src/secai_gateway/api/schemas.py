"""Request models accepted by the gateway HTTP API."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class VerifyRequest(BaseModel):
    """POST /ai/verify — verify a catalog PoC against an authorized target."""

    model_config = ConfigDict(extra="allow")

    target: str
    cve: str | None = None
    ref: str | None = None
    source: str | None = None
    rport: int | None = None
    user_id: str | int | None = None


class ToolRequest(BaseModel):
    """POST /ai/tool — a scoped, validated pentest tool invocation."""

    model_config = ConfigDict(extra="allow")

    tool: str
    target: str | None = None
    ports: str | None = None
    query: str | None = None
    wordlist: str | None = None
    threads: int | None = None
    service: str | None = None
    username: str | None = None
    password: str | None = None
    user_id: str | int | None = None


class ExecRequest(BaseModel):
    """POST /ai/exec — deliberately privileged raw executor API."""

    model_config = ConfigDict(extra="allow")

    cmd: list[str] | None = None
    shell: str | None = None
    timeout_s: int | None = None
    privileged: bool | None = None
    user_id: str | int | None = None


class ExploitRunRequest(BaseModel):
    """POST /ai/exploit/run — deterministic 5568 exploit runner."""

    model_config = ConfigDict(extra="allow")

    target: str
    cve: str
    port: str | int | None = None
    command: str | None = "id"
    use_proxy: bool = True
    user_id: str | int | None = None


class ExploitAdvanceRequest(BaseModel):
    """POST /ai/exploit-advance[/stream] — khai thác ĐA-BƯỚC một CVE vào TARGET thật, có vòng
    verify→sửa và stream tiến trình. Chỉ hoạt động khi ops bật POC_EXPLOIT_ENABLED + target thuộc
    allowlist (VERIFY_ALLOWED_TARGETS / VERIFY_ALLOW_ALL). Mọi field optional có mô tả để FE dựng form
    (đọc qua /openapi.json hoặc GET /ai/exploit-advance/fields)."""

    model_config = ConfigDict(extra="allow")

    # ── Bắt buộc ──
    cve: str = Field(description="Mã CVE cần khai thác, ví dụ CVE-2017-5638.")
    target: str = Field(description="Target: host | host:port | URL (vd 10.0.0.5, 10.0.0.5:8080, http://10.0.0.5:8080).")
    # ── Optional (đều có mặc định; FE nên hiển thị rõ) ──
    port: str | int | None = Field(default=None, description="Cổng target (nếu không suy ra được từ `target`).")
    command: str = Field(default="id", description="Lệnh chứng minh RCE khi khai thác thành công (vd `id`, `cat /flag`).")
    auth: dict[str, Any] | None = Field(default=None, description="Xác thực cho target cần đăng nhập: {headers?, cookies?, token?}.")
    scope_note: str | None = Field(default=None, description="Ghi chú phạm vi/uỷ quyền (audit).")
    max_steps: int = Field(default=5, ge=1, le=20, description="Số vòng exploit→verify→sửa tối đa.")
    step_timeout_s: int = Field(default=180, ge=10, le=1800, description="Timeout mỗi bước (giây).")
    total_timeout_s: int = Field(default=900, ge=30, le=7200, description="Timeout toàn cục (giây).")
    use_proxy: bool = Field(default=True, description="Định tuyến qua proxy khai thác nội bộ (kt).")
    extra: dict[str, Any] | None = Field(default=None, description="Tham số phụ (rport/path/payload override) cho engine.")
    user_id: str | int | None = Field(default=None, description="Định danh người gọi (audit).")
    run_id: str | None = Field(default=None, description="Gán run_id cố định để truy vết; bỏ trống sẽ tự sinh.")


class ExploitAdvanceReplyRequest(BaseModel):
    """POST /ai/exploit-advance/reply — cung cấp thông tin bổ sung cho lane1 đang paused."""

    model_config = ConfigDict(extra="allow")

    run_id: str = Field(description="run_id từ event `question` (adv:xxx).")
    answer: str = Field(description="Câu trả lời/thông tin bổ sung từ user (endpoint, protocol, credential...).")
    user_id: str | int | None = Field(default=None, description="Định danh người gọi (audit).")


class BughuntRequest(BaseModel):
    """POST /ai/bughunt/stream — a target-only BugHunter run."""

    model_config = ConfigDict(extra="allow")

    target: str
    scope_note: str | None = None
    user_id: str | int | None = None
    run_id: str | None = None
    # Opt-in Exploit phase (also requires POC_EXPLOIT_ENABLED + API URL).
    enable_exploit: bool = False
    # Run BB cve_scan.sh during MapCVE (disable for faster lab smoke).
    run_cve_sweep: bool = True


class LegacyRequest(BaseModel):
    """Permissive adapter for deployed IAS v1 clients with field-name drift."""

    model_config = ConfigDict(extra="allow")

    user_id: str | int | None = None
    message: str | None = None
    question: str | None = None
    prompt: str | None = None
    content: str | None = None
    text: str | None = None
    messages: list[dict[str, Any]] | None = None
    language: str | None = None
    data: Any = None
    vulnerability_type: str | None = None
    target_system: str | None = None
    context: str | None = None
    target: str | None = None
    cve: str | None = None
    port: str | int | None = None
    command: str | None = None
    # Per-request PoC strategy override for /ai/exploit A/B: "prune" (new, keep multi-file
    # structure) | "consolidate" (legacy LLM merge) | "auto". None → Settings.poc_strategy.
    # Lets ONE gateway serve both the new and old paths for side-by-side comparison.
    poc_strategy: str | None = None

    def supplied_text(self) -> str:
        """Return the first non-empty user text across legacy request shapes."""
        for candidate in (self.message, self.question, self.prompt, self.content, self.text):
            if isinstance(candidate, str) and candidate.strip():
                return candidate

        for item in reversed(self.messages or []):
            if item.get("role") != "user":
                continue
            content = item.get("content")
            if isinstance(content, str) and content.strip():
                return content

        form_parts = (
            self.vulnerability_type,
            self.target_system,
            self.context,
            self.target,
            self.cve,
            self.command,
        )
        form_text = " | ".join(str(part).strip() for part in form_parts if str(part or "").strip())
        if form_text:
            return form_text
        if isinstance(self.data, str) and self.data.strip():
            return self.data
        return ""
