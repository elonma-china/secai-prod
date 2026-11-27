# Đóng gói + khôi phục dữ liệu THẬT trên VM Vast — 2026-11-24

`stack/eval-vast.sh packtest` (VM Vast KVM Ubuntu 22.04, đĩa 150 GB, Thuỵ Điển, ~$0,175/giờ). Nguồn: `secai-data` thật (đọc chỉ-đọc); pack và
restore đều chạy trên VM; Qdrant prod chỉ bị chạm qua proxy chặn (`stack/qdrant-guard-proxy.py`: chỉ đọc + tạo đúng 1 snapshot + xoá đúng snapshot đó).

## Kết quả: **ĐẠT** (`verdict: pass`, riêng neo4j_nodes `not_verified`)
| Mục | Kết quả |
|---|---|
| Qdrant `cve_kb_hybrid` | khôi phục **842.928/842.928 điểm**; cấu hình vector (dense 1024 Cosine + sparse `bm25` idf) và 20 payload index khớp prod; 20 điểm mẫu khớp giá trị vector/payload |
| Neo4j | dump nạp được (491.776 node); **chưa so với số kỳ vọng** (không có `PACKTEST_NEO4J_NODES`) |
| `poc/catalog.db` | mọi bảng trùng số dòng (vd. poc 134.038, artifact 72.849) |
| exploitdb / mcp-server / poc-sources / poc/store | số file và byte **khớp nguồn** (poc-sources 644.325 file / 10,09 GB) |
| SHA256SUMS gói | khớp; quyền `poc`, `poc/store` = 10001:10100 775 |
| Snapshot prod | proxy chỉ tạo và xoá đúng 1 snapshot của lần này; 2 snapshot cũ (2026-08-23, 2026-09-04) và 842.928 điểm prod nguyên vẹn; không còn proxy/đường hầm chạy ngầm |

## Số đo thật
- Chép nguồn lên VM: 11,3 GB trong 1.910 s (~5,9 MB/s; băng thông đo 5,4 MB/s từ máy này tới VM).
- `pack.sh data` trên VM: 18 phút 33 giây; gói **13,7 GB** (poc-sources 8,24 GB, snapshot Qdrant 4,85 GB, poc 0,24 GB, exploitdb 0,17 GB, neo4j 0,16 GB, mcp 0,03 GB).
- `restore-data.sh`: **2 phút 37 giây**. Đỉnh đĩa VM +25,3 GB so với lúc bắt đầu.
- Tổng một lượt (không tính thuê/cài): ~55 phút.

## Phát hiện
- `poc-sources` nén 8,2 GB vì **gói mang cả thư mục `.git` của các repo clone**; mã chạy chỉ đọc file trong cây làm việc. Loại `.git` có thể giảm gói đáng kể (đề xuất, chưa làm).
- Cổng băng thông của `packtest` ước tính nguồn 3,5 GB (vì `du --exclude=.git`) trong khi thực chép 11,3 GB; số đo cho thấy vẫn kịp nhưng ước tính sai (cần sửa).
- `trickest-cve`, `priority`, `_manual-untracked-backup-*` không được catalog tham chiếu (tàn dư); 3 file `catalog.db.bak-*` (~850 MB) không được đóng gói.
- `src/secai_gateway/retrieval/bm25.py:28` hard-code `/home/ict/.../_state/bm25_local_model`; máy đích offline sẽ không mã hoá BM25 được (chưa kiểm).
- Neo4j: gói dùng `neo4j.dump` ngày 2026-06-30, không phải store đang chạy (`_state/neo4j_data`).
- Hai lần thuê đầu (Quebec) bị gate băng thông chặn (0,2 MB/s) trước khi chuyển byte nào; máy huỷ và xác nhận đã mất.

## Chưa kiểm
Tải lại các thành phần trên máy đích chạy đủ stack (chỉ restore + kiểm số liệu); RAG parity 100 câu (cần LLM/embedding); đo `neo4j_nodes` so với kỳ vọng; offline hoàn toàn (BM25 model); Docker Hub bị chặn.
