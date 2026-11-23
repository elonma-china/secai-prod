"""Gói BÀN GIAO khi exploit-v2 không giải được: thông tin CVE, mức đã đạt, dấu vết target, code nháp (có nhãn),
lý do dừng, việc nên làm tiếp. Hàm thuần (trừ `fingerprint_target`); chung cho mọi lab/CVE.

FE prod chỉ hiện được Terminal (event `log`, giữ xuống dòng) + ô Reason (1 dòng) khi thất bại, nên gói được in vào
Terminal bằng event `log`; trường `cve_info`/`failure_code`/`handoff` trong `final` dành cho FE mới."""
from __future__ import annotations

import re

import httpx

FAILURE_LABELS: dict[str, str] = {
    "INVALID_INPUT": "đầu vào không hợp lệ (CVE hoặc target)",
    "TARGET_UNREACHABLE": "không kết nối được tới target",
    "TIMEOUT": "hết thời gian cho phép",
    "ENGINE_ERROR": "lỗi engine/kết nối giữa chừng",
    "AGENT_STUCK": "agent không có tiến triển",
    "NO_IMPACT": "chưa chứng minh được tác động của lỗ hổng",
}
_CODE_MAX_LINES = 60
_DESC_MAX = 600
_SESSION_PATH_RE = re.compile(r"/tmp/(?:ias/)?ev2_ses_[A-Za-z0-9]+")


def classify_failure(*, stage: str | None, invalid_input: bool = False, unreachable: bool = False,
                     watchdog: str | None = None, session_error: str | None = None) -> str:
    if invalid_input:
        return "INVALID_INPUT"
    if unreachable:
        return "TARGET_UNREACHABLE"
    if watchdog:
        return "TIMEOUT" if "vượt quá" in watchdog else "AGENT_STUCK"
    if session_error:
        return "ENGINE_ERROR"
    return "NO_IMPACT"


def cve_summary(cve: str, cve_info: dict | None, kb_text: str | None) -> dict:
    """KB nội bộ trước (có cả CVE lab chưa lên NVD), NVD bù cvss/ngày/mô tả."""
    desc, source = "", None
    kb = (kb_text or "").strip()
    if kb:
        head = kb.split("\n", 1)[0]
        desc = (head.split("|", 1)[1] if "|" in head else head).strip()[:_DESC_MAX]
        source = "kb" if desc else None
    nvd = cve_info or {}
    if not desc and nvd.get("description"):
        desc, source = str(nvd["description"])[:_DESC_MAX], "nvd"
    return {"id": cve, "description": desc, "cvss": nvd.get("cvss"), "published": nvd.get("published"),
            "source": source}


def scrub_paths(text: str) -> str:
    """Gỡ đường dẫn thư mục phiên nội bộ khỏi nội dung trả cho người dùng."""
    return _SESSION_PATH_RE.sub("<workdir>", text or "")


def next_steps(code: str, stage: str | None, has_code: bool) -> list[str]:
    steps: list[str] = []
    if code == "INVALID_INPUT":
        steps.append("Sửa CVE (dạng CVE-YYYY-NNNNN) và target (host:port hoặc http(s)://host:port) rồi chạy lại.")
    elif code == "TARGET_UNREACHABLE":
        steps.append("Kiểm tra địa chỉ và port của target, lab đã khởi động chưa, và gateway có route tới target không.")
        steps.append("Khi truy cập được, chạy lại.")
    elif code in ("TIMEOUT", "AGENT_STUCK", "ENGINE_ERROR"):
        steps.append("Chạy lại: lỗi dạng này thường tạm thời (LLM/engine quá tải).")
        steps.append("Nếu vẫn lặp lại, thu hẹp mục tiêu bằng yêu cầu bổ sung (`extra_requirements`) hoặc tiếp tục thủ công theo phần 'Đã thử'.")
    else:  # NO_IMPACT
        if stage in (None, "1"):
            steps.append("Xác nhận target đúng sản phẩm và phiên bản bị ảnh hưởng của CVE (so dấu vết target với mô tả CVE ở trên).")
            steps.append("Lấy thêm banner/đường dẫn đặc trưng của sản phẩm rồi ưu tiên PoC có sẵn (exploitdb/nuclei) trước khi nghiên cứu thêm.")
        elif stage == "2":
            steps.append("Đã kích hoạt được lỗ hổng nhưng chưa có tác động: giữ đúng kỹ thuật của CVE, chỉnh payload/offset/phiên bản cho khớp target.")
        else:
            steps.append("Đã có tiến triển một phần: hoàn tất bước tạo tác động (RCE/đọc file/bypass xác thực) và lưu output làm bằng chứng.")
    if has_code:
        steps.append("Chạy code nháp bên dưới với target và đối chiếu output; sửa theo kết quả quan sát được.")
    return steps


def _fmt_cve(c: dict) -> list[str]:
    meta = " · ".join(x for x in (c["id"], f"CVSS {c['cvss']}" if c.get("cvss") is not None else "",
                                  f"công bố {c['published']}" if c.get("published") else "") if x)
    lines = [meta]
    if c.get("description"):
        lines.append(c["description"])
    return lines


def build_handoff(*, cve: dict, target: str, stage: str | None, stage_label: str, failure_code: str,
                  detail: str | None, fingerprint: str | None, attempted: list[str], code: str | None) -> str:
    L: list[str] = ["══════════ BÀN GIAO (chưa khai thác được) ══════════"]
    L += _fmt_cve(cve)
    L.append("")
    L.append(f"Đạt: STAGE {stage or '1'} — {stage_label}")
    why = FAILURE_LABELS.get(failure_code, failure_code)
    L.append(f"Dừng vì: {why} ({failure_code})" + (f"\n  {scrub_paths(detail).strip()}" if detail and detail.strip() else ""))
    if fingerprint:
        L += ["", f"Dấu vết target {target}: {fingerprint}", "  (đối chiếu với sản phẩm trong mô tả CVE ở trên)"]
    if attempted:
        L += ["", "Đã thử:"] + [f"  {i}. {scrub_paths(a)}" for i, a in enumerate(attempted, 1)]
    L.append("")
    body = scrub_paths(code or "").strip("\n")
    if body:
        lines = body.split("\n")
        L.append("Code nháp (CHƯA chạy ra bằng chứng):")
        L += [f"  {ln}" for ln in lines[:_CODE_MAX_LINES]]
        if len(lines) > _CODE_MAX_LINES:
            L.append(f"  … (cắt {len(lines) - _CODE_MAX_LINES} dòng; đầy đủ ở trường code)")
    else:
        L.append("Chưa có code nháp.")
    L += ["", "Việc nên làm tiếp:"] + [f"  {i}. {s}" for i, s in enumerate(next_steps(failure_code, stage, bool(body)), 1)]
    L.append("══════════════════════════════════════════════════")
    return "\n".join(L)


def one_line_reason(cve: str, target: str, failure_code: str) -> str:
    return (f"Chưa khai thác được {cve} trên {target}: {FAILURE_LABELS.get(failure_code, failure_code)}. "
            "Xem mục BÀN GIAO trong Terminal để làm tiếp.")


_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)


async def fingerprint_target(target: str, *, timeout: float = 5.0,
                             transport: httpx.AsyncBaseTransport | None = None) -> str | None:
    """Dấu vết nhận diện sản phẩm của target (tiêu đề trang, header Server/X-Powered-By) để người dùng tự đối chiếu
    với mô tả CVE. Một GET, timeout ngắn; lỗi hoặc không có gì → None (không bao giờ làm hỏng kết quả)."""
    url = target if "://" in target else f"http://{target}"
    try:
        async with httpx.AsyncClient(timeout=timeout, verify=False, follow_redirects=True, transport=transport) as c:
            r = await c.get(url)
        parts: list[str] = []
        m = _TITLE_RE.search(r.text[:20000]) if r.text else None
        title = " ".join(m.group(1).split())[:120] if m else ""
        if title:
            parts.append(f'tiêu đề: "{title}"')
        for name in ("Server", "X-Powered-By"):
            if r.headers.get(name):
                parts.append(f"{name}: {r.headers[name][:80]}")
        return " · ".join(parts) or None
    except Exception:
        return None


def reason_detail(reason: str | None) -> str | None:
    """Phần lý do thật trong `reason` dạng 'Đạt STAGE x — nhãn.\n\n<lý do>\n\nGợi ý: …' (bỏ khung và gợi ý)."""
    body = (reason or "").strip()
    if not body:
        return None
    body = re.sub(r"^Đạt\s+STAGE\s+\S+\s+—[^\n]*\n+", "", body)
    body = body.split("Gợi ý:")[0].strip()
    return body or None
