# Nghiệm thu AI — Tóm tắt (bản viết lại 06/09)

## File
- **`Nghiem-thu-AI.xlsx`** — bản điền minh chứng (viết lại theo khuôn mới). Cột G gốc giữ nguyên; minh chứng nằm ở **cột vàng cạnh G**.
- **`Nghiem-thu-AI-tomtat.md`** — file này.
- **`KPI-VA-GEN-TEST/02-GEN-TEST/TESTSET-1000-PHUONG-PHAP-SINH.md`** — cách sinh bộ 1000 câu.

## Màu nhóm minh chứng (giúp người hỗ trợ nghiệm thu nhìn là biết cách làm)
Ô cột minh chứng được **tô 6 màu theo loại** (chú giải nằm ở ô tiêu đề cột):
- 🔴 **Đỏ** = mở **file kết quả KPI** trong thư mục `KPI-VA-GEN-TEST` — 36 ô.
- 🟡 **Vàng** = **test trực tiếp trên giao diện** (hỏi 1 câu) — 26 ô.
- 🟢 **Xanh lá** = mở **hồ sơ trong thư mục `FINETUNE`** — 17 ô.
- 🟠 **Cam** = **phần cứng / máy serving** (SSH chạy `nvidia-smi/free -h/df -h`, show 3 dịch vụ AI) — 9 ô.
- 🟣 **Tím** = **bản quyền / hợp đồng** (cần văn bản HĐ / ảnh HF model card, không có file kỹ thuật) — 4 ô.
- 🔵 **Xanh dương** = **triển khai/Docker/CSDL tri thức/quy trình** — 8 ô.
- ⛑ **Dấu đầu ô** = **cần chụp/đo trực tiếp tại buổi nghiệm thu** (không mở file là thấy): R6, R41, R68, R69–R72, R76, R99, R101 — vd chụp `nvidia-smi` trên box 2×A100, ảnh HF card, đo lại embedding/latency.

> **SAST — file đầu vào (mã nguồn cần quét) ở đâu?** 60 tệp tại `/home/ict/AIEngines/secai/KPI-VA-GEN-TEST/02-GEN-TEST/testset-v2/vuln-samples/` (52 `.py` + 8 `.js`); mỗi câu hỏi trỏ đúng 1 tệp, vd `ind_sast_000_cwe89.py` (SQLi CWE-89), `ind_sast_050_cwe79.js` (XSS CWE-79). Đã ghi rõ trong ô R27/R28/R29/R30.

## Khuôn trình bày mới (cho người đi nghiệm thu, không rành dự án)
- **Chức năng test trên giao diện** (chat, dịch, sinh mã khai thác, báo cáo, hỏi-đáp CWE): chỉ nêu **menu + câu hỏi + kết quả mong đợi** — không có câu lệnh.
- **Yêu cầu nhiều vế** (dữ liệu, fine-tuning, cập nhật): **tách từng vế**, mỗi vế chỉ 1 **đường dẫn tuyệt đối** tới file minh chứng.
- **Tiêu chí có ngưỡng (KPI)**: nêu **mã KPI + kết quả đo + ĐẠT/KHÔNG ĐẠT + file kết quả**.
- Lệnh chứng minh (khi cần) dùng **đường dẫn tuyệt đối** `/home/ict/AIEngines/secai/…` (chạy được ở mọi nơi trên máy). Bỏ số dòng file, tên biến, giải thích code.

## Cách làm
- 7 agent Sonnet viết lại 100 ô theo khuôn → 1 agent **Opus đánh giá** (đối chiếu SPEC + repo) → **95 ô nguyên, 5 ô sửa** (R8, R13, R16, R76, R84); cả 7 nhóm ĐẠT, không nhóm nào phải viết lại.
- Đối chiếu 88 đường dẫn (đều tồn tại) + số KPI khớp file kết quả gốc 100%. File .xlsx verify PASS, giữ nguyên ảnh/merge/3 sheet kia.

## Kết quả KPI chính (đã đối chiếu file)
| Tiêu chí | Kết quả | |
|---|---|---|
| SAST ≥85% | 85.0% | ĐẠT (sát ngưỡng) |
| Exploit-explanation ≥85% | 94.12% | ĐẠT |
| Auto-remediation ≥60% | 83.33% | ĐẠT |
| Embedding Hit@10 >96% | 100% | ĐẠT |
| Rerank P@1 >90% | 99.41% | ĐẠT |
| Entity Precision =100% | 100% | ĐẠT |
| Hallucination <2% | 0% | ĐẠT |
| Radar ≥90% | 97.9% | ĐẠT |
| Sanitization F1 ≥90% | 98.11% | ĐẠT |
| **Faithfulness ≥98%** | **96.71%** | **KHÔNG ĐẠT** |

## ⚠ Điểm cần Chủ đầu tư quyết (giữ trung thực trong ô)
- **Faithfulness 96,71% < 98%** (công bố trước 98,48%, judge/mẫu khác).
- **Chatbot <15s**: 1 request 5,15s ✓; dưới tải p50 11,7–52,6s — cần đo lại trên **2×A100**.
- **Embedding ≥1200/s**: on-host box hiện tại (1×A100) chỉ **867/s**; ~2662/s chỉ đạt trên **2×A100** cam kết (cần chạy lại + lưu file).
- **TTFT** 900–1500ms: đo 30-CCU = 3702ms (vượt trần); **Relevance** đo bằng llm-judge (không phải cosine).
- **Không có Helm/k8s** (chỉ Docker Compose); image gateway 580MB.
- **Đặt lịch báo cáo (R97)**: Gateway AI chỉ sinh báo cáo on-demand, tính năng lập lịch thuộc phân hệ khác.
- **Loss/epoch huấn luyện** là số minh hoạ ("smoke"); **License Apache-2.0** cần đính kèm ảnh HF model card.

## Đã kiểm LIVE 05/09 (hệ thống chạy thật)
Chat 5,15s (đúng CVE) · Dịch 0,43s · Báo cáo 3,10s · Nhớ ngữ cảnh 2 lượt ✓ · Retrieval recall@10=0,675 · Sanitizer over-defense 0% + injection 100% · Phần cứng box serving: **A100 80GB, RAM 503GB, disk 1.8T + pool 23T**.
