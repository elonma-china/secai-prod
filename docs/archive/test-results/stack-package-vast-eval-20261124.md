# Nghiệm thu `stack/` trên máy Vast thuê mới — 2026-11-24

VM thuê: offer ~$0,13/giờ (RAM 98 GB, 100 GB đĩa), thuê qua `stack/eval-vast.sh`, huỷ bằng `stack/destroy-eval.sh`
(chứng minh biến mất theo ID). Image build **tại chỗ** từ `git archive` (commit `1afc7aaa`); gói dữ liệu nhỏ (238 MB: 338 điểm
Qdrant, dump Neo4j, `poc`/exploitdb tối giản); model qua endpoint prod trong `.env`; profile `translation,report,review`
(profile `exploit` tắt: lab 192.168.96.x không tới được từ máy thuê); `TUNNEL_MODE=off`.

## Kết quả

| Mục | Kết quả |
|---|---|
| init → nạp dữ liệu → preflight → qdrant → up → `check.sh all` | đạt (11 dịch vụ healthy; `check.sh`: env, config 6 tổ hợp, models, health + truy vấn RAG CVE-2021-44228) |
| UI-CHAT-1, UI-CHAT-2, UI-TR-1, UI-RPT-1 (qua API) | **đạt 4/4** |
| UI-TR-2, UI-TR-3, UI-POC-1/2/3 | **bỏ qua có chủ đích** (không có Hy-MT2/OCR; exploit tắt) — không tính là đạt |
| Khởi động lại Docker daemon trên VM | 11 dịch vụ (kể cả sidecar tunnel và worker chung netns) healthy lại trong ~100 s; `check.sh health` đạt |
| Review độc lập | **PASS cho phạm vi chạy được**; không kết luận đạt toàn bộ tiêu chí Task 8 |

`summary.json` ghi `verdict: pass` với 4/9 ca chạy, 5 ca bỏ qua. Hãy đọc là "pass (4/9 chạy, 5 bỏ qua)".

## Cần nói rõ
- **Không phải một lần chạy sạch liền mạch.** Nạp dữ liệu và khởi động lần đầu xảy ra ở lần chạy thứ nhất (hỏng vì script: preflight đứng trước nạp dữ liệu; sau đó `up --wait` lỗi do worker không có healthcheck). Lần chạy cuối (`results-…042052`) đạt nhưng dùng lại dữ liệu đã nạp. "Máy sạch" được chứng minh bằng hai lần chạy cộng lại; hai lỗi script đã sửa (commit `102475db` và trước đó).
- Tìm ra nhờ chạy thật (smoke cục bộ không lộ): SSH qua proxy `ssh5.vast.ai` không vào được trên các VM này, phải dùng cổng trực tiếp; thứ tự preflight/nạp dữ liệu; `--wait` với worker không healthcheck. Ba lần thuê đầu hỏng (offer không lên / lỗi script), bị huỷ tự động và chứng minh biến mất; tổng chi phí cả chặng ~$0,2.
- Toàn văn trả lời ca chat trước đây chỉ lưu 600 ký tự; đã sửa `run-api.sh` để lưu `<id>.txt`. Bản lưu lại UI-CHAT-1 có đủ `Log4Shell`, `10.0`, `2.15.0`/`2.16.0`.
- Trả lời đến từ RAG hay từ kiến thức của LLM **chưa tách được** (Log4Shell rất phổ biến); chỉ chứng minh Qdrant có 338 điểm và `/health` đạt.

## Chưa kiểm
- Điểm RAG 100 câu so prod (cần Qdrant đầy đủ ~22 GB).
- UI-TR-2/TR-3 với Hy-MT2/OCR thật (mới chạy với máy giả, ssh thật tới máy model chưa thử).
- Exploit-v2 N=24×3 xen kẽ prod/stack: lần chạy 2026-11-24 dừng theo luật abort (mạng tới host LLM mất gói); xem `stack-package-n24-20261124.md`.
- Gói dữ liệu thật ~49 GB; reboot cả máy; máy 2/4 GPU; UI Playwright trên stack.
- Khoá QWEN/EMBEDDING/RERANKER của prod đã được chép sang máy thuê (đã huỷ): **cần xoay khoá**.
