"""Lab exploit-v2 handoff → chat grounding.

poc-runner ``read_handoff`` đóng gói ``exploit.*``; session cũng hay ghi
``findings.md`` / ``final_report.md``. Chat CVE trước đây chỉ thấy KB/catalog nên
trả lời generic dù lab vừa CONFIRM cùng CVE. Module này:

1. Ghi ``IAS_LAB_HANDOFF.md`` + index ``/tmp/ias/cve-handoff/<CVE>.json`` khi
   exploit-v2 kết thúc.
2. Lookup cho chat pre-fetch (index trước, rồi quét ``ev2_*`` gần nhất).
"""
from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

IAS_ROOT = Path("/tmp/ias")
INDEX_DIR = IAS_ROOT / "cve-handoff"
HANDOFF_FILE = "IAS_LAB_HANDOFF.md"
# Agent/poc-runner thường tự viết các tên này — dùng làm fallback khi chưa có index.
_FALLBACK_NAMES = (HANDOFF_FILE, "findings.md", "final_report.md", "REPORT.md")
_CVE_RE = re.compile(r"CVE-\d{4}-\d{4,7}", re.IGNORECASE)
_MAX_SCAN_DIRS = 48
_DEFAULT_MAX_CHARS = 4500


def _norm_cve(cve: str) -> str:
    m = _CVE_RE.search(cve or "")
    return m.group(0).upper() if m else (cve or "").strip().upper()


def _session_dir(session_id: str | None) -> Path | None:
    sid = (session_id or "").strip()
    if not sid:
        return None
    # run_id từ exploit-v2 đã là "ses_…" — workdir = /tmp/ias/ev2_{session_id}
    if sid.startswith("ev2_"):
        return IAS_ROOT / sid
    return IAS_ROOT / f"ev2_{sid}"


def write_lab_handoff(
    *,
    cve: str,
    session_id: str | None,
    ok: bool,
    stage: str | None = None,
    reason: str | None = None,
    attempted_steps: str | None = None,
    run_instructions: str | None = None,
    recommend: str | None = None,
    code: str | None = None,
) -> Path | None:
    """Persist a compact lab handoff after exploit-v2 final. Fail-open."""
    cve_id = _norm_cve(cve)
    work = _session_dir(session_id)
    if not cve_id or work is None:
        return None
    try:
        work.mkdir(parents=True, exist_ok=True)
        INDEX_DIR.mkdir(parents=True, exist_ok=True)
    except OSError:
        logger.exception("lab_handoff: cannot create dirs for %s", work)
        return None

    code_preview = ""
    if code and code.strip():
        body = code.strip()
        if len(body) > 1800:
            body = body[:1800].rstrip() + "\n# …(cắt bớt)"
        code_preview = f"\n## PoC code (đã handoff / đóng gói)\n```\n{body}\n```\n"

    text = (
        f"# Lab handoff — {cve_id}\n"
        f"- session: `{work.name}`\n"
        f"- result: {'SUCCESS' if ok else 'FAILED'}\n"
        f"- stage: {stage or '?'}\n"
        f"- written_at: {int(time.time())}\n\n"
        f"## Reason\n{(reason or '').strip() or '(none)'}\n\n"
        f"## Recommend\n{(recommend or '').strip() or '(none)'}\n\n"
        f"## Attempted / progress\n{(attempted_steps or '').strip() or '(none)'}\n\n"
        f"## Run instructions\n{(run_instructions or '').strip() or '(none)'}\n"
        f"{code_preview}"
    )
    # Nếu agent đã ghi findings.md giàu hơn — nối tóm tắt (không đè file agent).
    findings = work / "findings.md"
    if findings.is_file():
        try:
            extra = findings.read_text(encoding="utf-8", errors="replace").strip()
            if extra:
                text += f"\n## findings.md (poc-runner)\n{extra[:2500]}\n"
        except OSError:
            pass

    out = work / HANDOFF_FILE
    try:
        out.write_text(text, encoding="utf-8")
        index = {
            "cve": cve_id,
            "session": work.name,
            "path": str(out),
            "ok": bool(ok),
            "stage": stage,
            "mtime": int(time.time()),
        }
        (INDEX_DIR / f"{cve_id}.json").write_text(
            json.dumps(index, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        logger.info("lab_handoff written cve=%s session=%s ok=%s stage=%s",
                    cve_id, work.name, ok, stage)
        return out
    except OSError:
        logger.exception("lab_handoff: write failed cve=%s", cve_id)
        return None


def _read_capped(path: Path, max_chars: int) -> str | None:
    try:
        raw = path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return None
    if len(raw) < 40:
        return None
    if len(raw) > max_chars:
        return raw[: max_chars - 20].rstrip() + "\n…(cắt bớt)"
    return raw


def _scan_recent_for_cve(cve_id: str) -> Path | None:
    """Fallback when index missing (lab chạy trước khi có write_lab_handoff)."""
    if not IAS_ROOT.is_dir():
        return None
    dirs = sorted(
        (p for p in IAS_ROOT.glob("ev2_*") if p.is_dir()),
        key=lambda p: p.stat().st_mtime if p.exists() else 0,
        reverse=True,
    )[:_MAX_SCAN_DIRS]
    needle = cve_id.upper()
    for d in dirs:
        for name in _FALLBACK_NAMES:
            p = d / name
            if not p.is_file():
                continue
            try:
                head = p.read_text(encoding="utf-8", errors="replace")[:4000]
            except OSError:
                continue
            if needle in head.upper():
                return p
    return None


def lookup_lab_handoff(cve: str, *, max_chars: int = _DEFAULT_MAX_CHARS) -> str | None:
    """Return markdown handoff body for chat grounding, or None."""
    cve_id = _norm_cve(cve)
    if not cve_id:
        return None
    path: Path | None = None
    idx = INDEX_DIR / f"{cve_id}.json"
    if idx.is_file():
        try:
            meta: dict[str, Any] = json.loads(idx.read_text(encoding="utf-8"))
            cand = Path(str(meta.get("path") or ""))
            if cand.is_file():
                path = cand
            else:
                sess = _session_dir(str(meta.get("session") or ""))
                if sess:
                    for name in _FALLBACK_NAMES:
                        if (sess / name).is_file():
                            path = sess / name
                            break
        except (OSError, json.JSONDecodeError, TypeError):
            logger.exception("lab_handoff: bad index %s", idx)
    if path is None:
        path = _scan_recent_for_cve(cve_id)
    if path is None:
        return None
    body = _read_capped(path, max_chars)
    if not body:
        return None
    return (
        f"[Lab exploit-v2 handoff — {cve_id}] Đây là kết quả lab/poc-runner ĐÃ CHẠY "
        f"(file `{path.name}` dưới `{path.parent.name}`). "
        f"Khi trả lời CVE này, PHẢI dùng các chi tiết dưới đây (stage, evidence, gadget, "
        f"reproduce, auth posture) làm phần thông tin thực nghiệm — không viết lại "
        f"như chưa từng khai thác.\n"
        f"{body}"
    )


def lab_handoff_blocks(cves: list[str], *, max_chars: int = _DEFAULT_MAX_CHARS) -> str | None:
    parts: list[str] = []
    seen: set[str] = set()
    for raw in cves:
        cve_id = _norm_cve(raw)
        if not cve_id or cve_id in seen:
            continue
        seen.add(cve_id)
        block = lookup_lab_handoff(cve_id, max_chars=max_chars)
        if block:
            parts.append(block)
    return "\n\n".join(parts) if parts else None
