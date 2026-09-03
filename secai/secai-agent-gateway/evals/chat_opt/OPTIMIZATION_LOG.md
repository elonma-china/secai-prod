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
