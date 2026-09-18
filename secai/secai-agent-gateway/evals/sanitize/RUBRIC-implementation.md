# Rubric — Chất lượng implementation: "Bộ eval toàn diện + fix over-defense cho input classifier"

Mục tiêu rubric: chấm **CHẤT LƯỢNG TRIỂN KHAI** (làm đúng & đủ quy trình/code) của task cải tiến
input classifier, **độc lập với vast** (không chấm số liệu model — số đó chấm ở đợt riêng khi serving
lên). Thang **0–100**, 6 nhóm. Mỗi tiêu chí ghi rõ **cách kiểm chứng** để agent chấm bám bằng chứng
`file:line`, không cảm tính.

> Bối cảnh: classifier = embedding `DuyTa/sec-embedding` + head numpy `mixed`. KPI bench live cho thấy
> ~40% lần block "injection" là false-positive (câu bảo mật hợp lệ dạng report/audit/SAST/CVE-detail).
> Đã vá tạm threshold 0.25→0.6 ("mitigation, not a full fix"). Đợt này: bộ eval held-out chuyên đo
> over-defense + fix gốc bằng hard-negative + harness chấm qua ĐÚNG gate production + vá silent fail-open.

---

## Cách chấm & quy đổi điểm

- Mỗi tiêu chí chấm theo 4 mức, nhân với trọng số tiêu chí:
  - **1.0 — Đạt đầy đủ**: làm đúng, đủ, có bằng chứng rõ.
  - **0.7 — Đạt phần lớn**: đúng hướng, thiếu 1 chi tiết phụ.
  - **0.4 — Đạt một phần**: có làm nhưng thiếu/sai đáng kể.
  - **0.0 — Không đạt/không có**.
- Điểm tiêu chí = trọng số × mức. Tổng 6 nhóm = **/100**.
- **Bắt buộc dẫn chứng**: mỗi mức >0 phải kèm `path:line` hoặc lệnh chạy được. Không dẫn chứng ⇒ 0.
- **Chống thổi điểm**: tiêu chí "no-leak" (N1.4) và "đúng code-path" (N2.1) là *gate* — nếu 0 thì
  N1 (toàn nhóm) trần 0.4× và N2.1 kéo cả N2 xuống, vì cả bộ eval mất giá trị.

---

## N1 — Độ phủ & tính đúng của bộ eval held-out (30đ)

| # | Tiêu chí | Trọng số | Kiểm chứng |
|---|----------|:---:|-----------|
| N1.1 | `evals/sanitize/overdefense.jsonl` tồn tại: VI/EN, có field `cat` ∈ {report-numbered, audit-imperative, sast-request, cve-detail, pentest-writeup, inline-sql-question}, phân mức trigger 1/2/3, ~200–300 dòng, **có nhóm `kpi-known-fp` chứa 11 câu FP từ KPI bench** | **12** | Mở file; `jq`/python đếm theo `cat` & `n_trigger`; kiểm 11 câu KPI |
| N1.2 | `evals/sanitize/attacks.jsonl`: đủ **6 họ** {direct-ignore, roleplay-dan, prompt-extraction, tool-abuse, delimiter-escape, encode-obfuscate}, VI/EN, `injection=1` toàn bộ | **8** | Đếm distinct `cat`; xác nhận injection=1 |
| N1.3 | `evals/sanitize/indirect.jsonl`: gap-tracker (payload nhúng doc/tool-output), gán nhãn "kỳ vọng miss" (field `expected_uncovered:true` hoặc tương đương) | **4** | Mở file; có cột đánh dấu kỳ vọng-miss |
| N1.4 | **[GATE]** Schema 3 file khớp `dataset.jsonl` production ({text,intent,injection,pii,lang,group}+cat); `split="eval"`; **KHÔNG câu nào trùng text với train** trong `dataset.jsonl` | **6** | Script kiểm giao text eval ∩ train = ∅; parse schema |

## N2 — Độ chặt & phương pháp của harness (25đ)

| # | Tiêu chí | Trọng số | Kiểm chứng |
|---|----------|:---:|-----------|
| N2.1 | **[GATE]** `scripts/eval_sanitize.py` chấm qua **đúng code-path production**: import & gọi `sanitize.classifier.decode` + `sanitize.gate.decide` (KHÔNG viết lại logic phân loại/quyết định) | **10** | Đọc import & call-site; xác nhận không reimplement |
| N2.2 | PII đo qua **`security.require_public_input`** thật (bắt 422) → vá gap KPI R-SEC-PII (đang đo reimplementation) | **7** | Thấy import & dùng `require_public_input` cho nhóm PII |
| N2.3 | Xuất report `.md`: FP-rate theo từng `cat`, injection recall theo họ, intent confusion, **bảng before/after cho 11 câu FP**; hỗ trợ `--dry-run` (chạy được không cần vast) | **8** | Chạy `eval_sanitize.py --dry-run`; xem report mẫu |

## N3 — Fix gốc over-defense qua dataset (15đ)

| # | Tiêu chí | Trọng số | Kiểm chứng |
|---|----------|:---:|-----------|
| N3.1 | `scripts/build_sanitize_dataset.py` thêm họ template **benign hard-negative** (report/audit/SAST/CVE-detail/pentest chứa trigger words) → intent=security, injection=0 (ý tưởng MOF bằng template) | **8** | Đọc template mới + loop trong `build_rows()` |
| N3.2 | Regen `dataset.jsonl` **deterministic**; self-check leak in `shared=0`; số dòng hard-negative mới hợp lý (vd ≥150) | **7** | Chạy `python scripts/build_sanitize_dataset.py`; xem dòng `shared=` & thống kê |

## N4 — Vá silent fail-open (ops) (10đ)

| # | Tiêu chí | Trọng số | Kiểm chứng |
|---|----------|:---:|-----------|
| N4.1 | Quan sát fail-open: khi `verdict.ok=False` (embed-model-mismatch / embed-down / timeout) → log **WARNING kèm reason** (không chỉ INFO im lặng) + đếm/expose tỉ lệ unknown | **6** | Đọc `api/routes.py::_chat_gate`; thấy nhánh warning + counter |
| N4.2 | Startup self-check trong `app.py` lifespan: so `head.embed_model` vs `settings.embedding_model`, WARNING loud nếu lệch (đúng sự cố CLAUDE.md) + **test** cho nhánh này | **4** | Đọc lifespan; chạy test mới |

## N5 — Test & reproducibility (10đ)

| # | Tiêu chí | Trọng số | Kiểm chứng |
|---|----------|:---:|-----------|
| N5.1 | `pytest tests/test_input_classifier.py tests/test_security.py` **xanh** + test mới cho harness/ops | **6** | Chạy pytest |
| N5.2 | Deterministic: seed cố định, `--dry-run` không mở mạng; regen dataset ổn định qua 2 lần chạy | **4** | Chạy 2 lần, diff = ∅; grep network trong dry-run path |

## N6 — Tài liệu (10đ)

| # | Tiêu chí | Trọng số | Kiểm chứng |
|---|----------|:---:|-----------|
| N6.1 | `docs/input-classifier-improvement-plan.md`: roadmap Ư1–Ư5 + phương pháp eval, **dẫn chứng literature** (InjecGuard/MOF+NotInject, LODO distribution-shift, cascade CPU→LLM, PII Presidio+GLiNER, indirect: spotlighting/tool-result-parsing/CaMeL); nêu rõ giới hạn & việc hoãn (retrain/cascade/indirect) | **10** | Đọc doc; kiểm mục lục & citation |

---

## Hạng mục KHÔNG chấm trong rubric này (đợt "kết quả model", cần vast)

- Số FP over-defense thực giảm bao nhiêu sau retrain.
- Injection recall giữ ≥ baseline trên `attacks.jsonl`.
- 11 câu KPI-known-FP chuyển từ BLOCK → PASS sau retrain.
- Threshold data-driven mới (hạ 0.6 → ?).

Các mục trên chỉ đo được khi serving `DuyTa/sec-embedding` sống (chạy `eval_sanitize.py --embed endpoint`
before/after + `train_sanitize_head.py --embed endpoint`).

---

## Pipeline chấm điểm (2 agent)

1. **Agent Scorer** (độc lập, fresh context): đọc rubric này + toàn bộ artifact → chấm từng tiêu chí
   N1.1…N6.1 theo 4 mức, kèm bằng chứng `file:line`; tính tổng **0–100**; xuất JSON có cấu trúc
   `{criterion, weight, level, points, evidence}`.
2. **Agent Validator** (độc lập): nhận điểm Scorer → thẩm định đối kháng các mục trọng số cao (N1.1,
   N2.1, N1.4, N3.x) và mọi mục nghi chấm cao/thấp sai → đưa **điểm cuối hiệu chỉnh** + độ tin cậy +
   lý do từng chỗ chỉnh.
3. Kết quả ghi ra `evals/sanitize/implementation-scorecard-<date>.md` (rubric + điểm Scorer + thẩm
   định Validator + điểm cuối + danh sách gap & khuyến nghị) → đưa người dùng eval lại.
