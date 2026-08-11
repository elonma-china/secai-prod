"""Report draft — adapted from claude-bug-bounty ``skills/report-writing``.

Deterministic template (not LLM prose). Impact-first; no theoretical language.
Vietnamese labels for analyst UI; evidence/notes may remain as recorded.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .workdir import findings_dir

# Optional display names for common vul_class values
_VUL_VI = {
    "secrets_exposure": "Lộ secrets / file cấu hình",
    "debug_info_leak": "Lộ thông tin debug",
    "backup_exposure": "Lộ backup / dump",
    "reflected_xss": "XSS phản xạ",
    "sqli_error_based": "SQLi (lỗi SQL)",
    "idor": "IDOR (truy cập đối tượng không ủy quyền)",
    "idor_user_pii": "IDOR — lộ PII người dùng",
    "path_traversal": "Path traversal",
    "open_redirect": "Open redirect",
    "default_credentials": "Mật khẩu / tài khoản mặc định",
    "cors_with_sensitive_data": "CORS * kèm dữ liệu nhạy cảm",
    "cve_match": "Khớp CVE (có evidence scanner)",
    "nuclei_cve": "Nuclei CVE",
}


def _title(lead: dict[str, Any]) -> str:
    cve = lead.get("cve")
    vul = str(lead.get("vul_class") or "finding").strip()
    label = _VUL_VI.get(vul) or vul.replace("_", " ")
    if cve:
        return f"{cve} — {label}"
    return label[:100]


def _impact_line(lead: dict[str, Any]) -> str:
    evidence = str(lead.get("evidence") or "").strip()
    if evidence:
        return evidence[:400]
    return "Đã ghi evidence trong artifact scan; không phóng đại impact."


def build_report_markdown(
    *,
    target: str,
    scope_note: str,
    leads: list[dict[str, Any]],
    cves: list[dict[str, Any]],
    validation_summary: str,
    map_summary: str,
) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        f"# Báo cáo Bug Hunt — {target}",
        "",
        f"- Thời điểm: {now}",
        f"- Ghi chú scope: {scope_note or '(không)'}",
        f"- Validate: {validation_summary}",
        f"- MapCVE: {map_summary}",
        "",
        "## Quy tắc (report-writing)",
        "",
        "- Không dùng câu kiểu \"có thể / theoretically\".",
        "- Chỉ ghi CVE khi có evidence từ scanner/template.",
        "- Bản nháp này **không** khẳng định exploit đã thành công.",
        "",
        "## Tóm tắt",
        "",
        f"- Lead giữ lại / đã map: **{len(leads)}**",
        f"- CVE có evidence: **{len(cves)}**",
        "",
    ]
    if cves:
        lines.append("## CVE khớp (có evidence)")
        lines.append("")
        for c in cves:
            lines.append(f"### {c.get('cve')}")
            lines.append("")
            lines.append(f"- Mức độ: {c.get('severity') or 'n/a'}")
            lines.append(f"- Evidence: {c.get('evidence') or ''}")
            if c.get("matched_at"):
                lines.append(f"- Khớp tại: {c.get('matched_at')}")
            lines.append("")
    lines.append("## Các lead")
    lines.append("")
    if not leads:
        lines.append("_Không có lead nào vượt triage._")
        lines.append("")
    for i, lead in enumerate(leads, 1):
        lines.append(f"### {i}. {_title(lead)}")
        lines.append("")
        lines.append(f"- Lớp lỗi: `{lead.get('vul_class')}`")
        lines.append(f"- Độ tin cậy: {lead.get('confidence')}")
        if lead.get("path"):
            lines.append(f"- Đường dẫn: `{lead.get('path')}`")
        if lead.get("request"):
            lines.append(f"- Request: `{lead.get('request')}`")
        if lead.get("cve"):
            lines.append(f"- CVE: `{lead.get('cve')}`")
        lines.append(f"- Impact / evidence: {_impact_line(lead)}")
        if lead.get("note"):
            lines.append(f"- Ghi chú: {lead.get('note')}")
        lines.append("")
    lines.extend([
        "## Checklist trước khi submit",
        "",
        "- [ ] Mỗi lead có bước HTTP cụ thể",
        "- [ ] CVE đã đối chiếu nuclei/template (nếu có)",
        "- [ ] Đã loại nhiễu / out-of-scope",
        "- [ ] Không claim exploit nếu chưa chạy phase Exploit",
        "",
    ])
    return "\n".join(lines)


def write_report(
    workdir: Path,
    *,
    target: str,
    scope_note: str = "",
    leads: list[dict[str, Any]] | None = None,
    cves: list[dict[str, Any]] | None = None,
    validation_summary: str = "",
    map_summary: str = "",
) -> dict[str, Any]:
    leads = list(leads or [])
    cves = list(cves or [])
    safe = "".join(c if c.isalnum() or c in "._-" else "_" for c in target)[:80] or "target"
    folder = findings_dir(workdir) / f"report-{safe}"
    folder.mkdir(parents=True, exist_ok=True)
    body = build_report_markdown(
        target=target,
        scope_note=scope_note,
        leads=leads,
        cves=cves,
        validation_summary=validation_summary,
        map_summary=map_summary,
    )
    path = folder / "report.md"
    path.write_text(body, encoding="utf-8")
    checklist = folder / "pre_submit_checklist.md"
    checklist.write_text(
        "# Checklist trước khi submit\n\n"
        "- [ ] Đã qua cổng 7 câu hỏi (Validate)\n"
        "- [ ] Đính kèm evidence CVE (nếu có)\n"
        "- [ ] Không claim PoC trừ khi đã chạy phase Exploit\n",
        encoding="utf-8",
    )
    return {
        "ok": True,
        "path": str(path),
        "folder": str(folder),
        "lead_count": len(leads),
        "cve_count": len(cves),
        "markdown": body,
        "preview": body[:1500],
        "summary": f"Report: đã ghi {path.name} ({len(leads)} lead, {len(cves)} CVE)",
    }
