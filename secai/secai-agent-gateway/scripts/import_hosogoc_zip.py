#!/usr/bin/env python3
"""Nhập hồ sơ gốc từ file .zip tải thủ công trên Drive vào HO-SO-GOC-NGHIEM-THU-AI/.

    python3 scripts/import_hosogoc_zip.py /home/ict/Downloads/hosogoc.zip

Việc script làm:
  1. Giải nén (xử lý tên file tiếng Việt/UTF-8 của zip Drive) ra thư mục tạm.
  2. Tìm 4 thư mục gốc (01. Ho_so_huan_luyen / 02. Bo_build_finetuning / 03. Kpi_results /
     04. Huong_dan_cai_dat) dù zip có bọc thêm 1 lớp thư mục cha hay không.
  3. Trộn vào HO-SO-GOC-NGHIEM-THU-AI/ (file đã có sẽ bị GHI ĐÈ bằng bản trong zip —
     zip là bản chính thức từ Drive).
  4. Bỏ 149 ảnh chụp màn hình rời ở gốc (Trí tuệ nhân tạo NN - ....png) theo đúng thoả thuận
     "không lấy ảnh"; ẢNH CHART trong dashboards/figures/ thì GIỮ vì API dùng.
  5. In thống kê để chạy tiếp scripts/audit_hosogoc.py.
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

DEST = Path("/home/ict/AIEngines/secai/HO-SO-GOC-NGHIEM-THU-AI")
WANTED_TOP = ["01. Ho_so_huan_luyen", "02. Bo_build_finetuning", "03. Kpi_results", "04. Huong_dan_cai_dat"]


def _is_root_screenshot(rel: Path) -> bool:
    """Ảnh chụp màn hình rời nằm NGAY ở gốc hồ sơ (không thuộc thư mục con nào)."""
    return len(rel.parts) == 1 and rel.suffix.lower() in {".png", ".jpg", ".jpeg"}


def extract(zip_path: Path, workdir: Path) -> Path:
    with zipfile.ZipFile(zip_path) as zf:
        for info in zf.infolist():
            # Zip của Drive dùng UTF-8; khi cờ UTF-8 không bật, Python đọc bằng cp437 -> sửa lại.
            if not info.flag_bits & 0x800:
                try:
                    info.filename = info.filename.encode("cp437").decode("utf-8")
                except (UnicodeEncodeError, UnicodeDecodeError):
                    pass
            zf.extract(info, workdir)
    return workdir


def find_root(workdir: Path) -> Path:
    """Trả về thư mục chứa 4 thư mục gốc (zip Drive thường bọc thêm 1 lớp tên bộ hồ sơ)."""
    if any((workdir / name).is_dir() for name in WANTED_TOP):
        return workdir
    for child in workdir.iterdir():
        if child.is_dir() and any((child / name).is_dir() for name in WANTED_TOP):
            return child
    return workdir


def merge(src_root: Path) -> tuple[int, int, int]:
    copied = skipped_img = 0
    DEST.mkdir(parents=True, exist_ok=True)
    for path in sorted(src_root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(src_root)
        if _is_root_screenshot(rel):
            skipped_img += 1
            continue
        target = DEST / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        copied += 1
    total_after = sum(1 for p in DEST.rglob("*") if p.is_file())
    return copied, skipped_img, total_after


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    zip_path = Path(sys.argv[1])
    if not zip_path.is_file():
        print(f"Không thấy file zip: {zip_path}")
        return 2

    print(f"Zip: {zip_path}  ({zip_path.stat().st_size / 1e6:.1f} MB)")
    with tempfile.TemporaryDirectory(prefix="hosogoc-") as tmp:
        workdir = Path(tmp)
        print("1. Giải nén...")
        extract(zip_path, workdir)
        root = find_root(workdir)
        print(f"2. Thư mục gốc trong zip: {root.relative_to(workdir) or '.'}")
        for name in WANTED_TOP:
            mark = "có" if (root / name).is_dir() else "KHÔNG THẤY"
            print(f"     - {name}: {mark}")
        print("3. Trộn vào", DEST)
        copied, skipped_img, total = merge(root)

    print()
    print(f"   Đã chép      : {copied} file")
    print(f"   Bỏ ảnh chụp  : {skipped_img} file (ảnh rời ở gốc)")
    print(f"   Tổng hiện có : {total} file trong hồ sơ gốc")
    print()
    print("Chạy tiếp:  python3 scripts/audit_hosogoc.py --api http://127.0.0.1:8199")
    return 0


if __name__ == "__main__":
    sys.exit(main())
