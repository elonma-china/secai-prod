# Nhật ký tối ưu chatbot IAS.AI — vòng lặp eval-driven

Mục tiêu: chatbot trả lời tốt câu hệ thống + CVE/PoC, đúng tool, "không biết→nói không biết",
từ chối đúng câu ngoài phạm vi, chiều được câu linh tinh. Đo bằng rubric 0-100 (Opus chấm), ≤10 vòng.
Nhánh: `feat/chat-harness-optimize`. Serving: 1×A100 `125.249.86.45`. Gateway `:8131`.

Cơ chế áp mỗi vòng = **hotpatch prompt** (docker cp chat.md/base_system.md → restart), KHÔNG rebuild.

---

## Round 0 — Setup (2026-09-04)
- Tạo nhánh `feat/chat-harness-optimize` + workspace `evals/chat_opt/`.
- Áp cấu hình baseline: `INPUT_CLASSIFIER_ENABLED=true` + `INPUT_CLASSIFIER_BLOCK_OFFTOPIC=false` (offtopic PASS xuống LLM, injection/PII vẫn chặn) + hotpatch `chat.md` (có `<about_and_coverage>`).
- Verify: `:8131` healthy; serving mới `:11088/:11461/:11370` 200; câu meta "dữ liệu từ năm nào" → TRẢ LỜI (short_circuit=None), hết bị từ chối.
- Rubric: `rubric.md` (7 tiêu chí, 100đ). Bộ câu hỏi: 2 agent Sonnet sinh 200 câu (customer 100 + secpro 100).

_(các vòng tiếp theo ghi bên dưới: điểm, top failure bucket, thay đổi đã áp, research)_

## Round 1 — Baseline eval + tối ưu prompt v2 (2026-09-04)
**Điểm baseline (Opus chấm 200 câu): mean = 87.98/100** (customer 88.17, secpro 87.79). 151/200 ≥90; 13 hỏng (<50).
- Failure bucket: hallucination 11, deflection 9 (scan cơ học: 35 câu tail-only), no_grounding 8, wrong_refusal 3, tool_leak 1, empty 2.
- Tiêu chí yếu nhất: accuracy_grounded 82%, answer_attitude 86%. Category tệ nhất: aggregate_stats 69, vague_messy 74, relation 76.
- Rate-limit: eval bị 429 (chat 15/60s) → hotpatch tạm `route_limit` nâng /ai/chat lên cao (localhost eval; khôi phục sau).

**Tối ưu áp (chat.md v2, hotpatch):**
1. Thêm `<about_system>` — mục đích + 6 năng lực + nguồn/độ phủ + cách dùng + giới hạn → câu overview/capability/usage được trả lời thực chất (hết deflection).
2. `<answer_first_rule>` — CẤM trả lời chỉ bằng câu chốt; câu năng lực "làm được X không" trả lời thẳng.
3. `<grounding_and_honesty>` — cấm bịa CVSS/severity/version/ánh xạ ATT&CK-CAPEC/sự cố; cấm viết cú pháp gọi tool ra text (chống leak); không rõ → nói không tìm thấy.
4. `<aggregate_stats_rule>` — câu đếm/cũ-mới nhất: nói trung thực "chưa có công cụ đếm chính xác + gợi ý NVD", cấm bịa số.
5. `<interaction_policy>` — vague/typo → hỏi làm rõ hoặc đoán ý, không deflection.
- Research (web) xác nhận: only-from-context + say-don't-know + tool-routing rõ ràng.

**Verify nhanh (3 case từng fail):** overview→trả lời thực chất ✓; aggregate→honest, hết leak ✓; vague→hỏi làm rõ ✓.

## Round 2 — Eval v2 + grader web-verify + tối ưu v3 (2026-09-04)
**Điểm (Opus + WEB VERIFY): mean = 89.36/100** (customer 90.72, secpro 88.01). **Δ +1.38 vs R1.**
- deflection 9→1 (diệt gọn), no_grounding 8→2. Nhưng **hallucination vẫn 11**, tool_leak 1→2, wrong_refusal 4.
- **Web bắt 12 câu sai fact** (trên ~25 verify): Zerologon CVSS 5.5→10.0; vá XZ 5.6.1→phải 5.6.2 (nguy hiểm); bịa tên kỹ thuật ATT&CK (sec-055); ProxyLogon↔ProxyShell (sec-083); PaperCut vá 22.0.6→20.1.7/21.2.11/22.0.9; Log4j flag sai; "max CVSS 8.8" vô lý.
- **Chẩn đoán:** cùng fact lúc đúng lúc sai (5.6.1 vs 5.6.2) ⇒ model dùng TRÍ NHỚ THAM SỐ thay vì KB ⇒ accuracy_grounded kẹt 82.4% (≈R1). Đây là điểm nghẽn secpro.
- Grader chuyên môn mới (web-verify) đã tạo & dùng: `grader_spec.md`.

**Tối ưu v3 (chat.md, hotpatch):** siết `<grounding_and_honesty>`:
1. Con số/định danh (CVSS/severity/phiên bản/T####/CAPEC/mapping) CHỈ nêu nếu có trong kết quả tra KB câu đó; thiếu → "không rõ"; CẤM lấy từ trí nhớ.
2. Phiên bản vá: chép đúng từ dữ liệu, CẤM đoán số version (đoán sai → nâng cấp vẫn dính lỗ hổng).
3. Cấm gán CVSS đồng loạt + cấm "trang trí" mã T####/CAPEC không có trong dữ liệu.
- (wrong_refusal cust-100 "bạn có phải người thật" = false-positive classifier injection → follow-up [CODE/CONFIG], không sửa bằng prompt.)

## Round 3 — Eval v3 + grader web-verify + KẾT LUẬN (2026-09-04)
**Điểm (Opus + web-verify): mean = 89.66/100** (customer 91.31, secpro 88.00). Δ +0.29 vs R2, +1.67 vs R1.
- **accuracy_grounded 82.3% — BẤT ĐỘNG** (R1 82.2 → R2 82.4 → R3 82.3). Điểm tăng chỉ từ answer_attitude/scope (phía customer).
- **162/200 câu y hệt R2**; rule hard-grounding v3 chỉ chạm 38 câu (customer). **0/12 hallucination R2 được sửa.**
- **Chẩn đoán gốc (chứng minh bằng query Qdrant + web):** bot báo TRUNG THỰC dữ liệu KB, nhưng KB có ô SAI (Zerologon 5.5 thay vì 10.0; PaperCut/XZ fixed-version sai; ProxyLogon↔ProxyShell; CVE tương lai 2026-xxxx). ⇒ điểm nghẽn = **DATA + retrieval [DATA/CODE]**, prompt hết dư địa.

## KẾT LUẬN & DỪNG (sau 3 vòng)
Tối ưu prompt **bão hoà** (162/200 y hệt, accuracy phẳng). Dừng đúng tiêu chí (grader ổn định + đã bắt & root-cause nhiều case). Xem **HANDOVER.md** cho tổng kết + hướng tiếp theo ưu tiên ([DATA] hiệu đính CVSS/version từ NVD là cao nhất).

**Kết quả cuối:** 87.98 → 89.36 → 89.66. Customer 88→91.3 (đạt). Secpro 87.8→88.0 (chặn bởi data).

## FIX DATA — hiệu đính CVSS/severity từ NVD (2026-09-04, sau khi user gõ "fix")
**Root cause xác định:** backfill (`extract_cvss`) lấy CVSS metric ĐẦU TIÊN — thường là CNA/secondary (vd Microsoft cho Zerologon = 5.5) — thay vì nguồn `nvd@nist.gov` (10.0) khi CVE có nhiều metric.
**Cơ chế fix (mở rộng, tái dùng):** `apply_cve_override` + `patch_cve_kb_overrides.py` giờ hỗ trợ `cvss_score/severity` (ghi payload Qdrant 2 collection + text "CVSS v3: X SEV" + Neo4j SET). Sửa cả `changed`-flag để payload cập nhật cả chunk core+context.
**Audit test-set vs NVD** (ưu tiên Primary→nvd@nist.gov→max): 31 CVE → **4 lệch**, đã patch live:
- Zerologon CVE-2020-1472: **5.5 MEDIUM → 10.0 CRITICAL**
- Fortinet CVE-2018-13379: 9.1 → **9.8 CRITICAL**
- ProxyLogon CVE-2021-26855: 9.1 → **9.8 CRITICAL**
- Citrix Bleed CVE-2023-4966: 9.4 → **7.5 HIGH** (NVD base thật)
**Verify:** Qdrant(2 chunk)+Neo4j+chatbot đều trả đúng NVD cho cả 4. Chatbot: "Zerologon CVSS 10.0 CRITICAL" ✓.
**Còn lại (systemic):** đây mới sửa 4 CVE trong test-set. Toàn KB (~65k CVE) cần re-source CVSS từ NVD (prefer nvd@nist.gov) — dùng chính cơ chế audit này mở rộng cho toàn bộ (lớn hơn, pace NVD). Re-embed bị skip (URL embed trong secai-agent-gateway/.env đã cũ → nên trỏ lại `125.249.86.45:11461`); payload+BM25 đã đúng nên đáp án đúng.
