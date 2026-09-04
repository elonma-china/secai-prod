# BÀN GIAO — Tối ưu chatbot IAS.AI (vòng lặp eval-driven, 2026-09-04)

## TL;DR
Vòng tối ưu tự động (multi-agent, eval + Opus chấm có **web-verify**) chạy **3 vòng** rồi **dừng vì đã bão hoà tầng prompt**. Điểm tổng **87.98 → 89.36 → 89.66/100**.
- **Phía KHÁCH THƯỜNG (customer): 88.17 → 91.31** ✓ đạt mục tiêu — đã giải quyết dứt điểm deflection, câu hệ thống/năng lực, câu linh tinh.
- **Phía CHUYÊN GIA (secpro): 87.79 → 88.00** gần như đứng yên — **bị chặn bởi CHẤT LƯỢNG DỮ LIỆU KB**, không phải prompt.

## Phát hiện quan trọng nhất (định hướng bước sau)
`accuracy_grounded` **bất động 82.2%→82.4%→82.3%** qua 3 vòng dù siết rule chống bịa. Nguyên nhân (đã chứng minh bằng truy vấn Qdrant trực tiếp + web-verify):
- **Chatbot KHÔNG bịa — nó báo TRUNG THỰC dữ liệu KB, mà một số ô trong KB SAI.** Ví dụ đối chiếu KB vs NVD:
  - Zerologon **CVE-2020-1472**: KB ghi **5.5 MEDIUM** → thực **10.0 CRITICAL** (sai nặng).
  - vá XZ **CVE-2024-3094**: KB/model gợi ý **5.6.1** → thực phải **5.6.2** (5.6.1 vẫn dính backdoor — nguy hiểm).
  - PaperCut **CVE-2023-27350** phiên bản vá; ProxyLogon↔ProxyShell (34473); một số CVSS lệch (26855 9.1 vs thực 9.8).
  - KB còn nhiễm **CVE tương lai** (2026-xxxx) và bảng CVSS gán 10.0 đồng loạt.
  - (Đa số CVE nổi tiếng ĐÚNG: Log4Shell 10.0, BlueKeep 9.8 — nên đây là lỗi rải rác, không sập toàn bộ.)
- ⇒ Prompt "chỉ nêu khi có trong KB / thiếu thì nói không rõ" bị **thoả mãn bởi rác trong KB** → không cứu được. **Grader web-verify bắt 12 câu sai fact, ~8/12 gốc là DATA.**

## Đã làm được (tối ưu PROMPT — đã commit + hotpatch live trên :8131)
`chat.md` v3 (nhánh `feat/chat-harness-optimize`):
1. `<about_system>` — mục đích + 6 năng lực + nguồn/độ phủ + cách dùng + giới hạn → câu overview/capability/usage **trả lời thực chất** (deflection **35→~1**).
2. `<answer_first_rule>` — cấm trả lời chỉ bằng câu chốt; câu "làm được X không" trả lời thẳng.
3. `<grounding_and_honesty>` — cấm bịa CVSS/version/mapping từ trí nhớ; cấm đoán phiên bản vá; **cấm leak cú pháp gọi tool** ra text.
4. `<aggregate_stats_rule>` — câu đếm/cũ-mới nhất: trung thực "chưa có công cụ đếm chính xác + gợi ý NVD", cấm bịa số.
5. Xử lý vague/typo → hỏi làm rõ (không deflection); câu meta/hệ thống = trong phạm vi.
- Cấu hình gateway: `INPUT_CLASSIFIER_ENABLED=true` + `INPUT_CLASSIFIER_BLOCK_OFFTOPIC=false` (câu hệ thống xuống được LLM, injection/PII vẫn chặn).

## Việc grader ("bank") bắt được (đã root-cause)
- Behavioral (đã fix bằng prompt): deflection, overview né, aggregate leak tool-call, vague deflection, not-found sai chỗ.
- Factual (cần DATA/CODE): 12 hallucination web-contradicted → phần lớn là KB ghi sai; false-positive injection (cust-100 "bạn có phải người thật"); tool-call leak còn 2 (lỗi model tool-calling).

## HƯỚNG TIẾP THEO (ưu tiên theo tác động)
1. **[DATA] (cao nhất)** Backfill/hiệu đính **CVSS · severity · fixed-version** vào KB ledger từ **NVD** (gốc của ≥8/12 hallucination + kẹt accuracy 82%). Có sẵn scripts `secai/scripts/backfill_cve_from_opencve.py`, `patch_cve_kb_overrides.py`.
2. **[DATA]** Lọc **CVE tương lai (2026-xxxx)** + các bản ghi CVSS 10.0 gán hàng loạt khỏi KB.
3. **[CODE]** Tool **`kb_stats`** (đếm/cũ-mới nhất/lọc năm) cho câu aggregate — "single source of truth" (Neo4j `count/min/max` — CVE.year có sẵn). (Thiết kế đã có trong plan.)
4. **[CODE]** Lớp **verify-sau-sinh** cho fact số (CVSS/fixed-version/CWE-name) cross-check ledger/NVD → lệch thì thay "không rõ".
5. **[CODE/CONFIG]** Giảm **false-positive injection** (nới ngưỡng `input_classifier_injection_threshold`) để câu "bạn có phải người thật / kiểm tra domain X" không bị chặn oan.
6. **[CODE]** Chặn triệt để **leak tool-call** ở tầng sanitize output (Cyber-F1 tool-calling gãy — strip mạnh hơn).

## Trạng thái hệ thống & lưu ý
- Serving: 1×A100 PCIE 80GB (`125.249.86.45`, $0.83/h) — LLM/embed(`DuyTa/sec-embedding`)/rerank(`DuyTa/sec-rerank`). 4×A100 cũ stopped.
- Gateway `:8131` (Docker `secai-docker-gateway`) đang chạy **chat.md v3 (hotpatch)** + rate-limit đã khôi phục.
- ⚠️ **chat.md v3 là HOTPATCH (ephemeral)** — mất nếu `docker compose up`/recreate. Nguồn đã commit ở `feat/chat-harness-optimize`. Để bền: đưa vào image (nhưng ⛔ KHÔNG rebuild từ nhánh hiện tại — mất classifier; cần reconcile với `feat/input-sanitization-classifier` như plan).
- Eval từng bị rate-limit chat (15/60s) → đã nới tạm để chạy, **đã khôi phục**.

## Deliverables (trong `evals/chat_opt/`)
- `questions_customer.jsonl` (100) + `questions_secpro.jsonl` (100) — bộ test tiếng Việt.
- `rubric.md` (7 tiêu chí 0-100) + `grader_spec.md` (grader Opus + web-verify, **tái dùng**).
- `round_{1,2,3}/{answers,scores}.jsonl` + `summary.md` (có mục fact-check + URL).
- `OPTIMIZATION_LOG.md` (nhật ký từng vòng) + `HANDOVER.md` (file này).
- `chat.md` v3 tối ưu (đã commit).

## Vì sao dừng ở 3 vòng (không phải 10)
Round 3: **162/200 câu byte-identical** với Round 2, accuracy phẳng, 0/12 hallucination được sửa → tối ưu prompt đã cạn dư địa. Chạy thêm vòng prompt chỉ tốn tài nguyên. Dư địa còn lại nằm ở [DATA/CODE] — kiến nghị làm ở bước sau (đã liệt kê ưu tiên).
