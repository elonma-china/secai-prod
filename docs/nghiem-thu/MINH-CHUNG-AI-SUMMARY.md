# Tóm tắt: Điền cột Minh chứng — Nghiệm thu AI (05/09/2026)

## 1) Sản phẩm
- `Tùng - TeamAI - Tài liệu nghiệm thu - FILLED.xlsx` (đã điền, giữ nguyên bản gốc).
- `MINH-CHUNG-AI-SUMMARY.md` (file này).
- `KPI-VA-GEN-TEST/02-GEN-TEST/TESTSET-1000-PHUONG-PHAP-SINH.md` (cách sinh 1000 câu).

**Đọc file .xlsx:** sheet "Chi tiêu kỹ thuật" → **cột mới cạnh G, tô vàng** ("Minh chứng bổ sung"). Cột G gốc **giữ nguyên 100%**. Mỗi ô: các bước **B1, B2, B3…** + `✅ LIVE 05/09` + `⚠ Lưu ý`. Lệnh dùng **localhost** (chạy trực tiếp trên máy).

## 2) Quy trình đã làm
- B1. 3 agent khảo sát → lập bản đồ minh chứng thật trong repo.
- B2. 7 agent Sonnet song song → điền 89 dòng, tự `ls`/`grep` verify path.
- B3. 2 vòng Opus rà "từng từ một" → sửa các ô sai, giữ ⚠ trung thực.
- B4. Chạy eval LIVE hôm nay → tái lập số.
- B5. Điền nốt 11 dòng (tiêu đề + bản quyền) → **100/100 dòng**; rút gọn theo bước B1/B2.
- B6. Ghi file bằng sửa XML trực tiếp → giữ nguyên 92 merge, 25 hyperlink, ảnh, 3 sheet kia (byte-for-byte). Verify PASS.

## 3) Căn cứ (nguồn thật trong repo)
- API: `secai-agent-gateway/src/secai_gateway/api/routes.py` + `docs/api-guide.md`.
- KPI cam kết: `KPI-VA-GEN-TEST/KET-QUA/2026-08-26/{group-A/B/C}/raw-logs/kpi-summary.json`.
- Eval chạy lại: `secai-agent-gateway/evals/*`, `KPI-VA-GEN-TEST/02-GEN-TEST/bench_kpi.py`, `scripts/eval_sanitize.py`.
- Serving/ingest/FT: `secai-serving-vast/`, `scripts/sync_autoscan_to_kb.py`+cron, `FINETUNE/ho_so_ft-finetuning/`.

## 4) ✅ Kiểm LIVE hôm nay (localhost) — hệ thống đang chạy thật
`curl http://localhost:8131/health` → `ok, llm:true, qwen:true`. Docker stack healthy.

| Tính năng | Kết quả LIVE |
|---|---|
| Chatbot (R3/R6/R10/R92) | 200 · **5.15s** (<15s) · đúng CVE-2025-0282 (Ivanti) |
| Dịch (R4/R96) | 200 · **0.43s** |
| Báo cáo (R94) | 200 · **3.10s** |
| Retrieval (R50/R58) | **recall@10=0.675** (n=117) |
| Sanitizer (R80–83) | over-defense **0/211 (0%)** · injection **36/36 (100%)** · intent **95.3%** |
| 3 model (R5/R39) | serve thật `Cyber-F1-AWQ`+`sec-embedding`+`sec-rerank` (fine-tuned) |

> ⚠ Không refresh perf/throughput/phần cứng: box đang chạy là **1×A100 PCIE tạm**, không phải **2×A100** cam kết → giữ artifact 26/8+28/8; muốn số chuẩn cần bật instance 2×A100.

## 5) Bộ 1000 câu sinh thế nào (khách quan · khoa học · đúng HĐ)
Chi tiết: `TESTSET-1000-PHUONG-PHAP-SINH.md`. Bằng chứng `testset-v2/_meta/manifest.json`:
- B1. Sinh tự động `gen_testset.py`, **seed=7** (tái lập), **use_llm=false** → đề & đáp án **KHÔNG do model sinh**.
- B2. Lấy mẫu **phân tầng**: KEV 15% · severity C25/H35/M30/L10 · năm 20/40/40% · lọc chất lượng (cwe_refs+products+mô tả>400).
- B3. Đúng **R-EVAL-TESTSET**: bucket 40/40/17/3, khó 40/30/20/10, **mỗi CVE ≤1 lần → 850 CVE, 0 trùng**.
- B4. `--exclude-pool` loại **2176 CVE** của bộ KPI chính → độc lập.
- B5. SAST có mã nguồn thật: `testset-v2/vuln-samples/` = **52 `.py` + 8 `.js`**.
- B6. Cổng chất lượng `python3 validate_testset.py testset-v2` → **✓ ĐẠT (0 vi phạm)**.
- B7. Phản bác "tự ra đề": có `--source public` (NVD/KEV) và `--source customer` (CVE của khách).

## 6) Rà lại các ô ⚠ — phân loại

### 6a) ✅ ĐÃ SỬA bằng bằng chứng LIVE hôm nay
| Ô | Trước | Sau (đã kiểm live) |
|---|---|---|
| R95, R3 | "API đa hội thoại chưa có" | **Nhớ ngữ cảnh ĐẠT** — test 2 lượt: hỏi CVE-2025-32463 → hỏi "khắc phục lỗ hổng đó" (không nhắc mã) → trả đúng Sudo 1.9.17p1 |
| R70 | chưa có số | **A100 80GB PCIe** (nvidia-smi trên box serving) — ĐẠT |
| R71 | "chỉ đo máy dev" | **RAM 503GB** (box serving) — ĐẠT ≥128GB |
| R72 | "ước tính <2TB" | **docker-lv 1.8T + pool 23T** (box serving) — có ≥2TB ở mức pool |
| R69 | chưa chụp | đã chụp `nvidia-smi/free/df` LIVE trên box serving vast 49763626 |

### 6b) ✅ Thực ra ĐẠT/VƯỢT — đã sửa wording (không phải điểm trừ)
| Ô | Làm rõ |
|---|---|
| R66, R67 | Context **64k > 16k** → "16k" là mức tối thiểu, hệ thống VƯỢT 4× |
| R68 | TPS **62.8 ≥ 20** ĐẠT (số run 28/8) |
| R73 | RPS **1.328 ≥ 0.6** ĐẠT |
| R74 | **30 luồng, errors=0** ĐẠT |

### 6c) ⚠ GAP THẬT — Chủ đầu tư phải quyết (không sửa được bằng viết)
| Ô | Sheet | Thực tế |
|---|---|---|
| R52,R57 | Faithfulness ≥98% | **96.71% (FAIL)** |
| R56 | Reasoning 80% | **73.7%** |
| R53 | Relevance = Cosine | thực = **llm-judge** (91.12%, đạt ngưỡng nhưng lệch phương pháp) |
| R6 | Chatbot <15s | 1-request **5.15s ✓**; p50 dưới tải 11.7–52.6s — cần đo lại trên 2×A100 |
| R68(TTFT) | 900–1500ms | 30-CCU đo **3702ms** (TPS đạt, TTFT vượt trần) |
| R76 | Embedding ≥1200/s | on-host box 1×A100 = **867/s (batch), 187/s (req)** — chỉ đạt ~2662 trên **2×A100** cam kết |
| R85 | image 15–20GB + Helm | gateway **580MB**; **KHÔNG có Helm/k8s** |
| R97 | đặt lịch báo cáo+email | gateway chỉ **on-demand**; **không có cron/SMTP** trong src (thuộc phân hệ khác) |
| R44,R45 | training logs thật | loss/epoch là số **"smoke/minh hoạ"** |
| R41 | License Apache-2.0 | không có chuỗi trong repo — cần **ảnh HF card** khi nghiệm thu |

### 6d) ⚠ chỉ là ghi chú làm rõ / sửa path sai của sheet (GIỮ, không phải lỗi)
Các path sheet không tồn tại (`eval/kpi/…task_kpi.py`, `03. Kpi_results/…`, `nvd_feed.py`, `docker-compose.deploy.yml`, `run_eval_all.sh`) → **đã thay path thật in-repo**; model serve là bản **fine-tuned** (sheet ghi tên base); phân bổ capability nhóm tình huống lệch nhẹ (tổng vẫn 200)… — các ⚠ này giúp người rà, nên giữ.

## 7) File phụ trợ (scratchpad)
`evidence_v4.json` (bản chốt, đã rút gọn B1/B2) · `review_v3_notes.md` · `live_*.txt/.md` (capture LIVE) · `apply_evidence.py`+`verify_xlsx.py`.
