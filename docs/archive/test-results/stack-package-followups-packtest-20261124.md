# Packtest lần 2 trên VM Vast: gói gọn, Neo4j cùng ngày, image, BM25 offline (2026-11-24)

Chạy `stack/eval-vast.sh packtest` trên nhánh `feat/stack-package` (HEAD `aecc22ed`), với `PACKTEST_IMAGES_ENV` (9 image)
và `PACKTEST_NEO4J_PASSWORD_FILE`. Nguồn là `secai-data` thật, chỉ đọc. Neo4j prod được xuất chỉ đọc qua HTTP trên máy này;
mật khẩu không rời máy. Qdrant prod chỉ bị chạm qua `qdrant-guard-proxy.py`.

VM: KVM Ubuntu 22.04, Anh, $0,242/giờ, đĩa 200 GB, instance 62322821. Lần thuê trước đó (Quebec, $0,146/giờ, instance
62319942) đo được 0,3 MB/s. Cổng băng thông chặn trước khi chuyển byte nào và máy bị huỷ. Các offer Quebec đã được loại bằng
`EVAL_EXCLUDE_OFFERS`. Tổng tiền thuê hai máy dưới $0,5.

## Kết quả: ĐẠT, mọi kiểm `pass`, không có `not_verified`

| Kiểm | Kết quả |
|---|---|
| Qdrant `cve_kb_hybrid` | khôi phục 842.928/842.928 điểm. Cấu hình vector (dense 1024 Cosine, sparse `bm25` idf) khớp prod; 20/20 payload index khớp; 20 điểm mẫu khớp vector và payload |
| Neo4j | `neo4j_counts` pass: khôi phục (587.393 node, 5.414.825 quan hệ) bằng đúng số đã xuất. `neo4j-admin import` 13 s. Schema (6 ràng buộc unique) áp lại từ `schema.cypher` |
| Image | `images_load` pass: 9 image nạp từ gói, id theo layer khớp |
| BM25 offline | `bm25_offline` pass: image gateway mới, `--network none --pull never`, `HF_HUB_OFFLINE=1`, mã hoá truy vấn ra 3 chỉ số |
| Cây dữ liệu | số tệp và byte khớp nguồn: poc-sources 443.389 tệp / 1,01 GB, poc/store 17.932 / 297 MB, exploitdb 47.783 / 183 MB, mcp-server 3.010 / 98 MB. `catalog.db`: mọi bảng trùng số dòng |
| Gói | SHA256SUMS khớp. Quyền `poc` và `poc/store` = 10001:10100 775; `neo4j_data` = 7474 |
| Prod | proxy chỉ tạo và xoá đúng một snapshot của lần này. Hai snapshot cũ (2026-08-23, 2026-09-04) và 842.928 điểm còn nguyên. Container Neo4j và Qdrant prod vẫn healthy, không bị dừng hay restart |

## Số đo

| Hạng mục | Lần 1 (gói cũ) | Lần 2 (gói mới) |
|---|---|---|
| Gói dữ liệu, chưa tính image | 13,7 GB | **5,50 GB** (snapshot Qdrant 4,85 GB, poc-sources 0,31 GB, poc 0,24 GB, exploitdb 0,05 GB, mcp 0,03 GB, neo4j 0,02 GB) |
| Chép nguồn lên VM | 11,3 GB, 1.910 s | 4,48 GB (nguồn + 9 image nén), 688 s, 7,5 MB/s |
| `pack.sh data` trên VM | 18 phút 33 giây | 12 phút 18 giây (phần lớn là tải snapshot Qdrant qua tunnel) |
| `restore-data.sh` | 2 phút 37 giây | 2 phút 48 giây, gồm import Neo4j, schema và so số đếm |
| Neo4j trong gói | dump 2026-06-30 (491.776 node) | xuất cùng ngày (587.393 node, 5.414.825 quan hệ) |

`poc-sources` giảm từ 8,24 GB xuống 0,31 GB nén vì bỏ `.git` và hai nguồn chỉ bộ dựng catalog dùng: `trickest-cve`,
`PoC-in-GitHub`. `_manual-untracked-backup-*` cũng bị bỏ.

Sửa một kết luận sai ở báo cáo lần 1: `priority/` **không phải tàn dư**. Có 3 PoC canonical của team trỏ vào
`poc-sources/priority/`, nên gói vẫn giữ thư mục này.

## Dọn sau chạy

- Cả hai instance (62319942, 62322821) đều đã được chứng minh biến mất: `show instance <ID>` trả `instances: null`.
- Tài khoản còn 0 instance và 0 timer; không còn proxy hay tunnel chạy ngầm.
- Bản xuất Neo4j và gói image tạm trên máy này đã tự xoá (`on_exit_packtest`).

## Chưa kiểm

- Chạy `compose up` toàn bộ stack từ image vừa nạp trên máy sạch. Lần này mới kiểm image nạp đúng id và BM25 chạy trong image gateway.
- Máy đích dùng kho image Docker kiểu cũ (overlay2): định danh image theo layer được viết để không phụ thuộc kho, nhưng chưa chạy thử trên kho đó.
- Symlink trong nguồn: rsync dùng `--no-links`.
