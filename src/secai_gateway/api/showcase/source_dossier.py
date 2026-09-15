"""Nhóm S — `/showcase-api/source-dossier/*`: duyệt HỒ SƠ GỐC nguyên bản trên web.

Chỉ đọc, gói trong `settings.showcase_hosogoc_dir`. Mọi đường dẫn đều được resolve và kiểm
tra nằm trong thư mục gốc trước khi mở (chặn path traversal `../`).

File text (md/json/csv/log/py/…) trả nội dung để FE render; file quá lớn hoặc nhị phân chỉ
trả metadata + vài dòng đầu — không nhồi hàng trăm MB vào response (hồ sơ gốc có dataset
`.jsonl` tới 231MB).
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Query
from fastapi.responses import FileResponse

from ...config import get_settings
from ._common import bad_request, not_found

router = APIRouter(prefix="/showcase-api/source-dossier", tags=["showcase-source-dossier"])

# Đuôi file xem được trực tiếp trên web (text). Ảnh xử lý riêng qua `image_url`.
_TEXT_EXT = {".md", ".txt", ".json", ".jsonl", ".csv", ".log", ".py", ".sh", ".yml", ".yaml",
             ".ipynb", ".example", ".cfg", ".ini", ".toml", ".html"}
_IMAGE_EXT = {".png", ".jpg", ".jpeg", ".svg", ".gif", ".webp"}
_MAX_INLINE_BYTES = 2 * 1024 * 1024   # 2MB: trên mức này chỉ xem trước phần đầu
_PREVIEW_LINES = 200


def _root() -> Path:
    return Path(get_settings().showcase_hosogoc_dir).resolve()


def _safe_path(rel: str) -> Path:
    """Ghép rel vào thư mục gốc, từ chối mọi đường dẫn thoát ra ngoài."""
    root = _root()
    target = (root / rel).resolve() if rel else root
    if target != root and root not in target.parents:
        raise bad_request("Đường dẫn không hợp lệ (ngoài phạm vi hồ sơ gốc).")
    return target


def _kind(path: Path) -> str:
    ext = path.suffix.lower()
    if ext in _IMAGE_EXT:
        return "image"
    return "text" if ext in _TEXT_EXT else "binary"


@router.get("/tree")
def tree(path: str = Query("", description="Đường dẫn tương đối trong hồ sơ gốc")) -> dict:
    target = _safe_path(path)
    if not target.is_dir():
        raise not_found(f"Không tìm thấy thư mục: {path or '/'}")

    entries = []
    for child in sorted(target.iterdir(), key=lambda p: (p.is_file(), p.name.lower())):
        rel = str(child.relative_to(_root()))
        if child.is_dir():
            entries.append({"name": child.name, "path": rel, "type": "dir"})
        else:
            st = child.stat()
            entries.append({
                "name": child.name, "path": rel, "type": "file",
                "kind": _kind(child),
                "size_bytes": st.st_size,
                "modified_at": datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat(),
            })
    return {"path": path, "entries": entries}


@router.get("/file")
def file(path: str = Query(..., min_length=1)) -> dict:
    target = _safe_path(path)
    if not target.is_file():
        raise not_found(f"Không tìm thấy file: {path}")

    st = target.stat()
    kind = _kind(target)
    out: dict = {
        "path": path, "name": target.name, "kind": kind,
        "size_bytes": st.st_size,
        "modified_at": datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat(),
    }

    if kind == "image":
        out["image_url"] = f"/showcase-api/source-dossier/raw?path={path}"
        return out
    if kind == "binary":
        out["content"] = None
        out["reason"] = "binary"
        return out

    if kind == "text" and st.st_size > _MAX_INLINE_BYTES:
        with target.open("r", encoding="utf-8", errors="replace") as f:
            head = [next(f, "") for _ in range(_PREVIEW_LINES)]
        out["truncated"] = True
        out["preview_lines"] = _PREVIEW_LINES
        out["content"] = "".join(head)
    else:
        out["truncated"] = False
        out["content"] = target.read_text(encoding="utf-8", errors="replace")
    return out


@router.get("/raw")
def raw(path: str = Query(..., min_length=1)) -> FileResponse:
    """Trả file thô (dùng cho ảnh chart trong hồ sơ gốc)."""
    target = _safe_path(path)
    if not target.is_file():
        raise not_found(f"Không tìm thấy file: {path}")
    if _kind(target) != "image":
        raise bad_request("Chỉ phục vụ file ảnh qua endpoint này.")
    return FileResponse(target)
