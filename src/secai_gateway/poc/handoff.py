"""Gói BÀN GIAO khi exploit-v2 không giải được: thông tin CVE, mức đã đạt, dấu vết target, code nháp (có nhãn),
lý do dừng, việc nên làm tiếp. Hàm thuần (trừ `fingerprint_target`); chung cho mọi lab/CVE.

FE prod chỉ hiện được Terminal (event `log`, giữ xuống dòng) + ô Reason (1 dòng) khi thất bại, nên gói được in vào
Terminal bằng event `log`; trường `cve_info`/`failure_code`/`handoff` trong `final` dành cho FE mới."""
from __future__ import annotations

import asyncio
import re

import httpx

FAILURE_LABELS: dict[str, str] = {
    "INVALID_INPUT": "đầu vào không hợp lệ (CVE hoặc target)",
    "TARGET_UNREACHABLE": "không kết nối được tới target",
    "TIMEOUT": "hết thời gian cho phép",
    "ENGINE_ERROR": "lỗi engine/kết nối giữa chừng",
    "AGENT_STUCK": "agent không có tiến triển",
    "TARGET_MISMATCH": "bằng chứng đến từ cổng khác, không phải target đã nhập",
    "NO_IMPACT": "chưa chứng minh được tác động của lỗ hổng",
}
_CODE_MAX_LINES = 60
_DESC_MAX = 600
_SESSION_PATH_RE = re.compile(r"/tmp/(?:ias/)?ev2_ses_[A-Za-z0-9]+")
_CTRL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")                       # giữ \n và \t
_SECRET_RE = re.compile(r"\b(Bearer|Basic)\s+[A-Za-z0-9._~+/=-]{6,}", re.I)
_CMD_MAX = 300
_TEXT_MAX = 16000


def classify_failure(*, stage: str | None, invalid_input: bool = False, unreachable: bool = False,
                     target_mismatch: int | None = None, watchdog: str | None = None,
                     session_error: str | None = None) -> str:
    if invalid_input:
        return "INVALID_INPUT"
    if unreachable:
        return "TARGET_UNREACHABLE"
    if target_mismatch:
        return "TARGET_MISMATCH"
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
    """Gỡ đường dẫn thư mục phiên nội bộ và token Bearer/Basic khỏi nội dung trả cho người dùng."""
    return _SECRET_RE.sub(lambda m: f"{m.group(1)} ***", _SESSION_PATH_RE.sub("<workdir>", text or ""))


def _stage_step(stage: str | None) -> str:
    if stage == "2":
        return "Đã kích hoạt được lỗ hổng nhưng chưa có tác động: giữ đúng kỹ thuật của CVE, chỉnh payload/offset/phiên bản cho khớp target."
    return "Đã có tiến triển một phần: hoàn tất bước tạo tác động (RCE/đọc file/bypass xác thực) và lưu output làm bằng chứng."


def next_steps(code: str, stage: str | None, has_code: bool, mismatch_port: int | None = None) -> list[str]:
    steps: list[str] = []
    if code == "TARGET_MISMATCH":
        steps.append(f"Bằng chứng đến từ cổng {mismatch_port}, không phải cổng bạn nhập: agent đã thấy sản phẩm của CVE ở đó. "
                     f"Nếu đó là target bạn muốn, chạy lại với cổng {mismatch_port}.")
        steps.append("Nếu cổng bạn nhập mới là target đúng, kiểm tra lại sản phẩm và phiên bản đang chạy ở cổng đó.")
    elif code == "INVALID_INPUT":
        steps.append("Sửa CVE (dạng CVE-YYYY-NNNNN) và target (host:port hoặc http(s)://host:port) rồi chạy lại.")
    elif code == "TARGET_UNREACHABLE":
        steps.append("Kiểm tra địa chỉ và port của target, lab đã khởi động chưa, và gateway có route tới target không.")
        steps.append("Khi truy cập được, chạy lại.")
    elif code in ("TIMEOUT", "AGENT_STUCK", "ENGINE_ERROR"):
        steps.append("Chạy lại: lỗi dạng này thường tạm thời (LLM/engine quá tải).")
        steps.append("Nếu vẫn lặp lại, thu hẹp mục tiêu bằng yêu cầu bổ sung (`extra_requirements`) hoặc tiếp tục thủ công theo phần 'Đã thử'.")
        if stage not in (None, "1"):   # đã có tiến triển thật trước khi bị cắt: vẫn nêu hướng đi tiếp theo STAGE
            steps.append(_stage_step(stage))
    else:  # NO_IMPACT
        if stage in (None, "1"):
            steps.append("Xác nhận target đúng sản phẩm và phiên bản bị ảnh hưởng của CVE (so dấu vết target với mô tả CVE ở trên).")
            steps.append("Lấy thêm banner/đường dẫn đặc trưng của sản phẩm rồi ưu tiên PoC có sẵn (exploitdb/nuclei) trước khi nghiên cứu thêm.")
        else:
            steps.append(_stage_step(stage))
    if has_code:
        steps.append("Chạy code nháp với target và đối chiếu output; sửa theo kết quả quan sát được.")
    return steps


def _clip(text: str, n: int) -> str:
    one = " ".join(text.split())
    return one if len(one) <= n else one[: n - 1] + "…"


def short_cve(c: dict, max_desc: int = 200) -> str:
    """Một dòng: 'CVE-… · CVSS x — <câu đầu của mô tả>' (cắt ở ranh giới câu/từ)."""
    head = " · ".join(x for x in (c["id"], f"CVSS {c['cvss']}" if c.get("cvss") is not None else "") if x)
    desc = " ".join((c.get("description") or "").split())
    if not desc:
        return head
    m = re.match(r"(.+?[.!?])(\s|$)", desc)
    first = m.group(1) if m and len(m.group(1)) >= 20 else desc
    if len(first) > max_desc:
        first = first[:max_desc].rsplit(" ", 1)[0].rstrip(",;:") + "…"
    return f"{head} — {first}"


def _fmt_cve(c: dict) -> list[str]:
    meta = " · ".join(x for x in (c["id"], f"CVSS {c['cvss']}" if c.get("cvss") is not None else "",
                                  f"công bố {c['published']}" if c.get("published") else "") if x)
    lines = [meta]
    if c.get("description"):
        lines.append(c["description"])
    return lines


def build_handoff(*, cve: dict, target: str, stage: str | None, stage_label: str, failure_code: str,
                  detail: str | None, fingerprint: str | None, attempted: list[str], code: str | None,
                  mismatch_port: int | None = None) -> str:
    L: list[str] = ["══════════ BÀN GIAO (chưa khai thác được) ══════════"]
    L += _fmt_cve(cve)
    L.append("")
    L.append(f"Đạt: STAGE {stage or '1'} — {stage_label}")
    why = FAILURE_LABELS.get(failure_code, failure_code)
    L.append(f"Dừng vì: {why} ({failure_code})" + (f"\n  {scrub_paths(detail).strip()}" if detail and detail.strip() else ""))
    if fingerprint:
        L += ["", f"Dấu vết target {target}: {fingerprint}", "  (đối chiếu với sản phẩm trong mô tả CVE ở trên)"]
    if attempted:
        L += ["", "Đã thử:"] + [f"  {i}. {_clip(scrub_paths(a), _CMD_MAX)}" for i, a in enumerate(attempted, 1)]
    L.append("")
    body = scrub_paths(code or "").strip("\n")
    if body:
        lines = body.split("\n")
        L.append("Code nháp (CHƯA chạy ra bằng chứng):")
        L += [f"  {ln[:200]}" for ln in lines[:_CODE_MAX_LINES]]
        if len(lines) > _CODE_MAX_LINES:
            L.append(f"  … (cắt {len(lines) - _CODE_MAX_LINES} dòng; đầy đủ ở trường code)")
    else:
        L.append("Chưa có code nháp.")
    L += ["", "Việc nên làm tiếp:"] + [f"  {i}. {s}" for i, s in enumerate(next_steps(failure_code, stage, bool(body), mismatch_port), 1)]
    L.append("══════════════════════════════════════════════════")
    text = "\n".join(L)
    if len(text) > _TEXT_MAX:   # chặn tổng (mỗi lệnh/dòng code dài); phần cuối (việc nên làm tiếp) luôn giữ
        tail = "\n".join(L[-(len(next_steps(failure_code, stage, bool(body), mismatch_port)) + 3):])
        text = text[: _TEXT_MAX - len(tail) - 40].rstrip() + "\n  … (cắt bớt)\n\n" + tail
    return text


_REASON_MAX = 900


def compact_reason(*, cve: dict, target: str, stage: str | None, stage_label: str, failure_code: str,
                   fingerprint: str | None, has_code: bool, mismatch_port: int | None = None) -> str:
    """Ô Reason của FE hiện tại (khối kết quả bên dưới Terminal) là MỘT đoạn chữ thường, không xuống dòng và không có
    khối CVE riêng khi thất bại. Nên gói vào đó: CVE là gì + mức đạt + lý do dừng + dấu vết target + việc nên làm tiếp.
    Bản đầy đủ có cấu trúc nằm ở trường `handoff` (Terminal chỉ dành cho log hệ thống)."""
    what = short_cve(cve).rstrip(".")
    parts = [f"{what}.",
             f"Chưa khai thác được trên {target} (đạt STAGE {stage or '1'}: {stage_label}): "
             f"{FAILURE_LABELS.get(failure_code, failure_code)}."]
    if fingerprint:
        parts.append(f"Dấu vết target: {fingerprint} — đối chiếu với sản phẩm của CVE.")
    steps = next_steps(failure_code, stage, has_code, mismatch_port)
    parts.append("Việc nên làm tiếp: " + " ".join(f"({i}) {x}" for i, x in enumerate(steps, 1)))
    if has_code:
        parts.append("Code nháp (CHƯA chạy ra bằng chứng) ở ô Code.")
    text = " ".join(" ".join(parts).split())
    return text if len(text) <= _REASON_MAX else text[: _REASON_MAX - 1].rstrip() + "…"


_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)


_FP_MAX_BYTES = 32768


def _clean(text: str, n: int) -> str:
    return _CTRL_RE.sub("", " ".join(text.split()))[:n]


async def fingerprint_target(target: str, *, timeout: float = 5.0, deadline: float = 8.0,
                             transport: httpx.AsyncBaseTransport | None = None) -> str | None:
    """Dấu vết nhận diện sản phẩm của target (tiêu đề trang, header Server/X-Powered-By) để người dùng tự đối chiếu
    với mô tả CVE. Một GET: KHÔNG theo redirect (tránh đi ra ngoài allowlist), đọc tối đa 32 KB, tổng thời gian
    ≤ `deadline`. Lỗi hoặc không có gì → None (không bao giờ làm hỏng kết quả)."""
    url = target if "://" in target else f"http://{target}"

    async def fetch() -> str | None:
        async with httpx.AsyncClient(timeout=timeout, verify=False, follow_redirects=False, transport=transport) as c:
            async with c.stream("GET", url) as r:
                body = b""
                async for chunk in r.aiter_bytes():
                    body += chunk
                    if len(body) >= _FP_MAX_BYTES:
                        break
                headers = r.headers
        parts: list[str] = []
        m = _TITLE_RE.search(body[:_FP_MAX_BYTES].decode("utf-8", "replace"))
        title = _clean(m.group(1), 120) if m else ""
        if title:
            parts.append(f'tiêu đề: "{title}"')
        for name in ("Server", "X-Powered-By"):
            if headers.get(name):
                parts.append(f"{name}: {_clean(headers[name], 80)}")
        return " · ".join(parts) or None

    try:
        return await asyncio.wait_for(fetch(), deadline)
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
