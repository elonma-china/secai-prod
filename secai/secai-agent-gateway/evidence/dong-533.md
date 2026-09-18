Bộ test đánh giá cho Embedding/Reranking/LLM

**`03. Kpi_results/Model/Datasets/README.md`** — dòng 1–16:
```
# Bộ eval nghiệm thu secAI — 1000 câu tiếng Việt

## Files

| File | Số câu | Nội dung |
|------|--------|----------|
| `acceptance_dinh_danh.jsonl` | 400 | Định danh/phân loại CVE — `cve_qa` 280 · `translation` 60 · `poc_search` 60 |
| `acceptance_khac_phuc.jsonl` | 400 | Tư vấn khắc phục — `cve_qa` 280 · `sast` 60 · `remediation` 60 |
| `acceptance_tinh_huong.jsonl` | 200 | Suy luận tình huống — `pentest` 70 · `exploit` 60 · `report` 40 · trap 30 |
| `reserve/*.jsonl` | 200 | Dự phòng cùng cơ cấu (80/80/40) — thay câu lỗi khi điều chỉnh |
| `_pool/cve_pool.jsonl` | 2000 | Pool CVE dùng để sinh câu hỏi |
| `_pool/smoke_ids.txt` | 35 | Subset smoke test — 4 câu đầu mỗi capability |

**ID**: đánh số liên tục toàn bộ 1000 câu — `acc_dd_0001..0400`, `acc_kp_0401..0800`, `acc_th_0801..1000`. Reserve dùng tiền tố `accr_` (`accr_dd_0001`, …).
```

Ba file dữ liệu thực có trong thư mục và số dòng khớp:
```
03. Kpi_results/Model/Datasets/acceptance_dinh_danh.jsonl  — 400 dòng
03. Kpi_results/Model/Datasets/acceptance_khac_phuc.jsonl  — 400 dòng
03. Kpi_results/Model/Datasets/acceptance_tinh_huong.jsonl — 200 dòng
```

**Kết luận:** Đối chiếu được: `03. Kpi_results/Model/Datasets/README.md` (dòng 1, 7–9) và ba file `.jsonl` thực tế xác nhận bộ test 1.000 mẫu tiếng Việt phân bổ 400 (định danh/phân loại CVE) + 400 (tư vấn khắc phục) + 200 (suy luận tình huống) = 40% + 40% + 20%, đúng cơ cấu hợp đồng.
