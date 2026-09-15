"""Nhóm E (Loại 2 — curated evidence, /showcase-api/evidence/*) — xem docs/nghiem-thu-showcase/
01-API-DESIGN.md §Nhóm E và 02-EVIDENCE-FILE-CONVENTION.md.

Mỗi file .md/.html trong ``settings.showcase_evidence_dir``: dòng 1 = nguyên văn điều
khoản hợp đồng (khoá so khớp EQUALS cho E3), phần còn lại = nội dung (phải có dòng
"Nguồn: ..." trích hồ sơ gốc — không enforce ở code, chỉ là quy ước cho người viết file).
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Query

from ...config import get_settings
from ._common import not_found

router = APIRouter(prefix="/showcase-api/evidence", tags=["showcase-evidence"])


def _evidence_dir() -> Path:
    return Path(get_settings().showcase_evidence_dir)


def _iter_files() -> list[Path]:
    d = _evidence_dir()
    if not d.is_dir():
        return []
    return sorted([*d.glob("*.md"), *d.glob("*.html")])


def _read_split(path: Path) -> tuple[str, str]:
    """Line 1 (contract_text) + the rest (content), per the convention doc."""
    text = path.read_text(encoding="utf-8")
    first, _, rest = text.partition("\n")
    return first.strip(), rest.lstrip("\n")


def _format_of(path: Path) -> str:
    return "html" if path.suffix.lower() == ".html" else "md"


def _safe_evidence_file(d: Path, filename: str) -> Path | None:
    """Resolve filename against d and reject anything that escapes it (e.g. an
    evidence_id containing an encoded ``../``) — same containment check as
    source_dossier._safe_path, needed here because evidence_id comes straight
    from an unauthenticated path param."""
    root = d.resolve()
    target = (d / filename).resolve()
    if target != root and root not in target.parents:
        return None
    return target


@router.get("")
def list_evidence() -> dict:
    items = []
    for f in _iter_files():
        contract_text, _ = _read_split(f)
        items.append({
            "id": f.stem,
            "contract_text": contract_text,
            "format": _format_of(f),
            "updated_at": datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc).isoformat(),
        })
    return {"items": items}


@router.get("/match")
def match_evidence(contract_text: str = Query(..., min_length=1)) -> dict:
    """Khớp EQUALS theo dòng 1. Một chỉ tiêu trong sheet thường trải trên nhiều dòng (ô gộp),
    mỗi dòng là 1 file kết quả — nên trả về TẤT CẢ file khớp, không chỉ file mới nhất."""
    needle = contract_text.strip()
    items = []
    for f in sorted(_iter_files()):
        first, rest = _read_split(f)
        if first == needle:
            items.append({"id": f.stem, "format": _format_of(f), "content": rest})
    if not items:
        raise not_found("no_match")  # keeps the {"error","message"} envelope
    return {"contract_text": needle, "count": len(items), "items": items}


@router.get("/{evidence_id}")
def get_evidence(evidence_id: str) -> dict:
    d = _evidence_dir()
    for ext in (".md", ".html"):
        f = _safe_evidence_file(d, f"{evidence_id}{ext}")
        if f is not None and f.is_file():
            contract_text, content = _read_split(f)
            return {"id": evidence_id, "contract_text": contract_text, "format": _format_of(f), "content": content}
    raise not_found(f"Không tìm thấy evidence: {evidence_id}")
