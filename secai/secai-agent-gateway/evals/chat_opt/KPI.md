# KPI mục tiêu — chatbot `/ai/chat` (vòng lặp tối ưu 5 vòng)

Chuẩn chấm cho từng vòng cải thiện. Tái dùng **rubric.md** (7 tiêu chí / 100đ) — KHÔNG đổi cách chấm.
Baseline vòng trước: **89.66/100** (customer 91.31, secpro 88.00); `accuracy_grounded` kẹt **~82%** vì KB SAI (đã fix data → kỳ vọng vượt trần).
Đơn vị "%" của mỗi tiêu chí = điểm đạt / điểm tối đa của tiêu chí đó (vd `accuracy_grounded 90%` = 18/20).

---

## 1. KPI chất lượng câu trả lời (rubric 7 tiêu chí)

**Tổng (bắt buộc đạt cuối vòng 5):**
| Chỉ số | Baseline | Mục tiêu | Stretch |
|---|---|---|---|
| `mean_total` toàn bộ | 89.66 | **≥ 92.0** | 94 |
| customer | 91.31 | **≥ 93.0** | — |
| secpro | 88.00 | **≥ 91.0** | 93 |

**Sàn từng tiêu chí (% điểm tối đa, không tiêu chí nào được thủng):**
| # | Tiêu chí (max) | Baseline | Sàn |
|---|---|---|---|
| 1 | answer_attitude (25) | ~86% | **≥ 92%** (≥23/25) |
| 2 | tool_grounding (20) | — | **≥ 90%** (≥18/20) |
| 3 | **accuracy_grounded (20)** | **82%** | **≥ 90%** (≥18/20) ← KPI then chốt |
| 4 | idk_handling (10) | — | **≥ 90%** (≥9/10) |
| 5 | scope_discipline (10) | — | **≥ 95%** (≥9.5/10) |
| 6 | robustness (10) | — | **≥ 90%** (≥9/10) |
| 7 | language_format (5) | — | **≥ 96%** (≥4.8/5) |

**Sàn theo loại câu (mean_total/100 của category) + luật cứng:**
- **system/meta** (system_overview, capability, data_coverage, usage, meta_self): **≥ 93** — `wrong_refusal` = **0 câu** (cấm từ chối oan "ngoài phạm vi").
- **CVE/CWE/PoC/remediation** (cve_lookup, cve_detail, poc_availability, remediation, cwe_capec, relation): **≥ 90** — phải grounded KB; `hallucination` ≤ 1/nhóm.
- **edge_unknown**: **≥ 90** — **0 câu bịa CVE không tồn tại**; phải nói rõ "không tìm thấy".
- **aggregate_stats**: **≥ 85** — chưa có tool đếm → honest/ước lượng-có-nêu-rõ, cấm bịa số như thật.
- **concept ≥ 92** · **random_offtopic ≥ 90** (từ chối lịch sự) · **greeting_chitchat ≥ 95** · **vague_messy ≥ 88** (hỏi làm rõ / đoán ý, không deflection).

---

## 2. KPI truy hồi (retrieval — bộ 139 query)

| Chỉ số | Baseline | Mục tiêu | Stretch |
|---|---|---|---|
| recall@10 (tổng) | 0.675 | **≥ 0.72** | 0.75 |
| MRR | ~0.60 | **≥ 0.65** | 0.70 |

**Sàn theo nhóm query (recall@10):**
- exact_id = **1.00** (giữ) · alias **≥ 0.80** · product **≥ 0.80** · description **≥ 0.70** · multi_hop **≥ 0.70** · cwe **≥ 0.20** (điểm nghẽn reranker/nhãn hẹp).
- Ràng buộc: **không nhóm nào regress** so với baseline (depth-tuning từng làm description 0.64→0.56 — cấm đánh đổi kiểu này).

---

## 3. KPI chính xác đối chiếu internet (web-verify)

Mỗi vòng: grader Opus web-verify ~25–50 câu factual (CVSS/severity/fixed-version/CWE-name/ATT&CK map).

| Chỉ số | Baseline (quy đổi /50) | Mục tiêu | Stretch |
|---|---|---|---|
| Hallucination web-contradicted / 50 câu | ~3 (12/200) | **≤ 2** | **0** (vòng 5) |

**Định nghĩa loại lỗi (gán vào `failure_bucket`):**
- **hallucination** — fact bị web/NVD phản bác (bịa/sai CVSS, phiên bản vá, sản phẩm, T####/CAPEC, mapping).
- **retrieval-miss** — dữ liệu ĐÚNG có trong KB nhưng không được truy hồi → bot trả sai/"không có".
- **wrong-refusal** — câu hợp lệ (nhất là meta/hệ thống) bị từ chối oan "ngoài phạm vi".
- **bad-idk** — KB CÓ đáp án mà bot nói "không biết"; HOẶC KB KHÔNG có mà bot bịa cho có (thay vì nói không tìm thấy).

---

## 4. Tiêu chí "vòng này có cải thiện" + luật dừng/giữ

**Một vòng được tính CẢI THIỆN khi cả 3 đồng thời:**
1. `mean_total` tăng **≥ +0.3** so với vòng trước, VÀ
2. **KHÔNG tiêu chí nào regress > 1%** (điểm tối đa của tiêu chí đó), VÀ
3. Hallucination/50 **không tăng** (lý tưởng giảm).

**Luật GIỮ/BỎ thay đổi:** giữ patch nếu vòng cải thiện; **revert** nếu tổng giảm hoặc có tiêu chí/nhóm regress vượt ngưỡng (đổi 1 lever/lần để quy trách nhiệm).

**Luật DỪNG SỚM (bão hoà):** dừng trước vòng 5 nếu **2 vòng liên tiếp** đều: Δmean_total < +0.3 **VÀ** `accuracy_grounded` phẳng (<+1%) **VÀ** hallucination không đổi **VÀ** >80% câu byte-identical với vòng trước (dấu hiệu prompt hết dư địa — như vòng trước 162/200 y hệt). Ngược lại chạy đủ 5 vòng.

---

## 5. Hướng ưu tiên khi KPI trượt (fix ở tầng nào)

| KPI trượt | Chẩn đoán → tầng sửa |
|---|---|
| `accuracy_grounded` / hallucination cao | KB có giá trị đúng không? **Sai → [DATA]** hiệu đính ledger từ NVD (prefer `nvd@nist.gov`). **Đúng mà không tra ra → [RETRIEVAL]**. **Đúng + tra ra mà vẫn sai → [PROMPT]** siết hard-grounding (cấm dùng trí nhớ tham số). |
| recall@10 / MRR thấp, retrieval-miss | **[DATA/RETRIEVAL]**: alias enrichment (đã +0.16), graph-fusion cho class query, HyDE cho concept, reranker mạnh hơn. |
| wrong_refusal / scope_confusion (meta bị chặn) | **[PROMPT/CONFIG]**: `<about_system>`, `INPUT_CLASSIFIER_BLOCK_OFFTOPIC=false`, nới ngưỡng injection false-positive. |
| bad_idk | **[PROMPT]** luật idk; nếu data đã có → **[RETRIEVAL]**. |
| aggregate_stats thấp / bịa số | **[CODE]** tool `kb_stats` (Neo4j count/min/max); tạm thời **[PROMPT]** honest. |
| not_robust / language_format | **[PROMPT]** (interaction_policy, giọng/format). |
