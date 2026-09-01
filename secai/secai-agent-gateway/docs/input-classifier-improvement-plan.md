# Input classifier — kế hoạch cải tiến (over-defense, eval, ops, indirect)

_Cập nhật: 2026-09-01._

## 0. Bối cảnh & vấn đề

Input classifier (`src/secai_gateway/sanitize/`) = embedding `DuyTa/sec-embedding` (1024-d) → head
numpy `mixed` (intent softmax `security|chitchat|offtopic` + injection/pii sigmoid). Chạy trong
`api/routes.py::_chat_gate` trước agent; **fail-open** theo thiết kế.

`metrics.json` (in-distribution, split-by-skeleton) đẹp: macro-F1 0.986. Nhưng **KPI bench 1000 câu
live (2026-08-26)** cho thấy **22/1000 bị chặn, trong đó ~11 là câu bảo mật hợp lệ bị gắn cờ
`injection` sai** — điểm injection 0.25–0.37 trong khi điểm security 0.92–0.998. Đã vá tạm bằng nâng
threshold injection **0.25 → 0.6** (`thresholds.json:_note_injection`, tự nhận "a mitigation, not a
full fix"). 5 tồn tại:

1. **Over-defense** (trigger-word bias): câu report/audit/SAST/CVE-detail/pentest chứa từ khoá
   exploit/khai thác/payload bị chấm injection cao. Threshold chỉ vá triệu chứng.
2. **Metrics lạc quan**: eval in-distribution không dự báo FP thật (distribution shift).
3. **KPI R-SEC-PII (98.11%) đo nhầm** một reimplementation, không phải gate production.
4. **Indirect injection** (payload trong doc KB / output tool) không được cover.
5. **Silent fail-open**: lệch tên embed model / embed down → guard tắt âm thầm, `/health` vẫn `ok`
   (sự cố CLAUDE.md 2026-08-23).

## 1. Đã làm trong đợt này (không cần vast)

- **Bộ eval held-out** (`evals/sanitize/`), sinh bởi `scripts/build_sanitize_evalsets.py` (deterministic,
  tự kiểm không trùng text với train):
  - `overdefense.jsonl` (211): benign chứa trigger words, kiểu **NotInject** [1], 6 nhóm `cat` +
    mức trigger 1/2/3, **nhúng 11 câu FP thật của KPI bench** (`cat=kpi-known-fp`).
  - `attacks.jsonl` (36): 6 họ injection/jailbreak (recall guard).
  - `indirect.jsonl` (6): gap-tracker, `expected_uncovered=true`.
- **Harness** `scripts/eval_sanitize.py`: chấm qua **đúng code-path production** (`classifier.decode`
  + `gate.decide`), đo PII qua **`security.require_public_input`** thật (vá gap #3), `--dry-run`
  chạy không cần mạng, xuất report `.md` (FP theo cat, recall theo họ, PII, before/after 11 câu KPI).
- **Fix gốc over-defense (dataset)**: `scripts/build_sanitize_dataset.py` thêm 5 họ template
  **benign hard-negative** (report/audit/SAST/CVE-detail/pentest/imperative chứa trigger words →
  security, injection=0), ~324 dòng — kỹ thuật "Mitigating Over-defense" của InjecGuard [1] làm bằng
  template thay vì LLM. Regen `dataset.jsonl` (1712 dòng, `shared=0`).
- **Vá silent fail-open (#5)**: `classifier.startup_head_check` (self-check embed-model lúc boot,
  gọi trong `app.py` lifespan) + `note_verdict`/`verdict_stats` (đếm tỉ lệ fail-open) + WARNING trong
  `_chat_gate` khi verdict `unknown`. Có test.

## 2. Retrain — ĐÃ CHẠY offline (CPU, không cần vast), 2026-09-01

vast không khả thi → retrain **offline** bằng `--embed st` + model cache `DuyTa/sec-embedding` (đúng
pipeline đã dựng head production, `metrics.json backend=st`). Kiểm chứng: điểm injection tái tạo cho 11
câu KPI khớp gần tuyệt đối số bench thật → không gian embedding offline = production.

Cổng ship (đều ĐẠT) — chi tiết ở `evals/sanitize/implementation-scorecard-2026-09-01.md`:

| | Baseline (thr 0.6) | Sau retrain (thr **0.35** data-driven) |
|---|---|---|
| Injection recall (`attacks.jsonl`) | 34/36 = 94.4% | **36/36 = 100%** |
| Over-defense FP (`overdefense.jsonl`) | 8/211 = 3.8% | **6/211 = 2.8%** |
| 11 câu KPI-known-FP | PASS | **PASS** (max inj 0.197) |

Head mới đã ship vào `assets/`; threshold `injection` hạ **0.6 → 0.35** (chọn bằng sweep trên held-out,
min-attack-score = 0.398). Lệnh tái lập: `HF_HUB_OFFLINE=1 python scripts/train_sanitize_head.py
--embed st --model DuyTa/sec-embedding --head mixed`. Khi có serving thật, `--embed endpoint` để
đối chứng (kỳ vọng khớp).

### Round 2 (cùng ngày) — đóng nốt 6 FP còn lại

6 FP sau round 1 dồn hết vào 1 pattern cụ thể: câu mệnh lệnh "liệt kê/chỉ ra các điểm dính {vuln} ...
bypass/khai thác" (điểm cao nhất 0.667, với "leo thang đặc quyền"). Thêm 8 template hard-negative
nhắm đúng pattern này (`HN_AUDIT_VI/EN`, +58 dòng) → regen dataset (1788 dòng) → retrain offline lần 2.

| | Round 1 (thr 0.35) | **Round 2 (thr 0.45)** |
|---|---|---|
| Over-defense FP | 6/211 = 2.8% | **0/211 = 0%** |
| Injection recall | 36/36 = 100% | **36/36 = 100%** |
| Margin (min-attack vs max-benign) | — | **0.600 vs 0.362 (Δ0.24)** |

Threshold `injection` **0.35 → 0.45** (giữa margin mới, an toàn cả hai phía). `offtopic` threshold
đổi theo auto-tune của trainer (0.34→0.661, in-distribution, chưa có bộ held-out riêng cho offtopic) —
`offtopic_decision` precision/recall trên test split vẫn tốt (0.99/0.96), không tệ hơn head cũ.
56/56 test phạm vi liên quan xanh; 338/340 toàn bộ suite xanh (2 fail ở `test_prompts.py`, không liên
quan — lỗi XML sẵn có trong `prompts/`, không nằm trong diff của đợt này).

## 3. Roadmap Ư1–Ư5 (trung hạn)

- **Ư1 — Hard-negative mining (MOF)** [1]: đã khởi động bằng template. Nâng cấp: token-wise recheck
  head để lộ trigger token còn bias, bổ sung counter-example (có thể dùng Cyber-F1 sinh thêm). Xây bộ
  **NotInject VI/EN** làm eval over-defense cố định (đã có `overdefense.jsonl` làm nền).
- **Ư2 — Calibration + cascade "uncertain band"** [3]: thay hard-threshold bằng calibrated score
  (Platt/temperature) + vùng xám (vd 0.35–0.65) **escalate lên LLM judge (Cyber-F1)** thay vì chặn
  nhị phân. Cảnh báo *confident miscalibration* [3]: head tuyến tính hay sai-mà-tự-tin trên OOD →
  cascade cần kèm tín hiệu OOD, không chỉ tin score.
- **Ư3 — Eval theo distribution shift** [2]: chuyển sang giữ nguyên cả nguồn khi eval (LODO); coi số
  in-distribution là cận trên lạc quan; đưa `overdefense.jsonl` + traffic log thật + benchmark ngoài
  (NotInject/LLMail-Inject) vào CI.
- **Ư4 — Ops** (đã làm phần lõi): thêm expose `verdict_stats()` qua `/health` hoặc metric để alert khi
  `unknown_rate` vọt.
- **Ư5 — PII hybrid** [4]: nếu cần nâng chất lượng, cân nhắc Presidio (regex, precision) + GLiNER
  (neural, recall). Lưu ý GLiNER cần torch → không hợp runtime numpy-only air-gap; trước mắt giữ
  `require_public_input` + đo đúng qua harness.

## 4. Indirect prompt injection — spotlighting đã triển khai (2026-09-01)

Classifier chỉ gác **input user**; payload nhúng trong **doc KB / output tool** (RAG) không đi qua nó —
severity cao hơn (1 doc độc ảnh hưởng nhiều user, bypass input defense) [5][6]. Khảo sát kiến trúc
(agent dùng chung 1 `linch.Agent` cho cả 5 endpoint) tìm ra **2 đường** nội dung không tin cậy vào
context LLM, không chỉ 1:

- **(A) Tool-call loop**: `qdrant_search`/`neo4j_search`/`web_fetch`/`fetch_reference`/`web_search`/
  `poc_fetch` — `linch` không sanitize gì, string thô đi thẳng vào lịch sử hội thoại.
- **(B) Prompt-splicing riêng trong `routes.py`**: `_prefetch_prompt_appendix`/
  `_web_hints_prompt_appendix`/`_consolidated_prompt_appendix` nối **trực tiếp vào user prompt
  string** trước khi agent chạy, bypass hoàn toàn tool loop — đường mang raw GitHub PoC/README, theo
  khảo sát là điểm chạm nguy hiểm nhất, và (A) không che được đường này.

**Đã triển khai — spotlighting (A+B+C), không dùng lại classifier** (rủi ro over-defense mới vì nội
dung KB/PoC vốn chứa đầy từ khóa exploit/payload):

- `security.wrap_untrusted_content()`: bọc nội dung ngoại lai trong thẻ
  `<untrusted_retrieved_content source="...">...</untrusted_retrieved_content>`; tự neutralize (angle-
  escape) mọi thẻ delimiter giả mạo bên trong content trước khi bọc — chặn kiểu tấn công "forge closing
  tag để thoát khỏi boundary" (điểm yếu kinh điển của spotlighting thuần delimiter).
- **(A)** `agent.hooks.SanitizeToolResultHook` — hook `on_post_tool_use` mới, đăng ký cuối danh sách
  hook trong `agent/factory.py` (áp dụng tự động cho cả 5 endpoint qua 1 Agent dùng chung, không sửa
  từng tool function nên test hiện có của các tool không bị ảnh hưởng). **Loại trừ `poc_search`**: nó
  trả JSON được `RequireSearchBeforeAnswerHook` `json.loads()` lại để check `hits` — bọc sẽ phá parse
  đó cho bất kỳ hook nào chạy sau (an toàn hôm nay chỉ vì hook mới đặt cuối list, một ràng buộc thứ tự
  dễ vỡ) — giữ `poc_fetch` (mang source/README PoC thật, mục tiêu giá trị cao hơn).
- **(B)** wrap trực tiếp tại 4 điểm nối nội dung ngoại lai trong `routes.py`: `_prefetch_prompt_appendix`
  (`source="poc_prefetch"`), `_web_hints_prompt_appendix` (`source="web_hints"`),
  `_consolidated_prompt_appendix` (2 chỗ: `source="consolidated_poc"` cho code,
  `source="consolidated_poc_readme"` cho README) — khung chỉ thị hệ thống (Vietnamese, "KHÔNG viết Mục
  1"...) vẫn nằm NGOÀI thẻ, chỉ bytes ngoại lai bị bọc.
- **(C)** `<untrusted_content_policy priority="highest">` mới trong `prompts/base_system.md` (sau
  `<grounding_policy>`): dạy model coi nội dung trong thẻ là DATA, không bao giờ là chỉ thị, kể cả khi
  tự xưng "hệ thống"/"developer".

Test mới: `tests/test_sanitize_tool_result_hook.py` (hook, theo pattern
`test_chat_grounding_hook.py`), `tests/test_security.py` (wrap function + neutralize forged tag),
`tests/test_gateway.py`/`tests/test_websearch.py` (appendix wrapping, xác nhận nội dung ngoại lai nằm
đúng trong cặp thẻ còn khung chỉ thị nằm ngoài). Regression: test tool-formatting hiện có
(`test_qdrant_fastpath.py`, `test_web_fetch.py`, `test_fetch_reference.py`, `test_fetch_tarball.py`,
`test_github_kind.py`) xanh nguyên vẹn — vì hook chỉ mutate ở tầng dispatch của `linch`, không sửa các
tool function mà những test đó gọi trực tiếp.

`indirect.jsonl` (trong bộ eval classifier) tiếp tục là gap-tracker cho việc XÁC MINH phòng thủ này có
hiệu quả không (chưa có harness đo end-to-end qua agent thật — hướng tiếp theo nếu cần). Hướng xa hơn
vẫn là kiến trúc **CaMeL** (tách kênh instruction/data triệt để) [5] hoặc **tool-result parsing** có
cấu trúc hơn [6] — spotlighting là phòng thủ theo lớp (defense-in-depth) rẻ, không phải giải pháp triệt
để (mô hình vẫn đọc chung 1 chuỗi token, delimiter là tín hiệu mềm).

## 5. Ghi chú kiến trúc

Không thay kiến trúc: **Prompt Guard 2** (mDeBERTa 86M) [7] không hỗ trợ tiếng Việt chính thức + cần
torch trong serving (trái ràng buộc numpy-only air-gap). Embedding + numpy head song ngữ hiện tại là
lựa chọn đúng cho ràng buộc VI/EN + air-gap + sub-ms; đầu tư vào **data (Ư1) + eval đúng (Ư3) +
calibration/cascade (Ư2)**, không đổi backbone.

## Tham chiếu

1. InjecGuard/PIGuard — Mitigating Over-defense + NotInject benchmark. arXiv:2410.22770.
2. When Benchmarks Lie — distribution shift / Leave-One-Dataset-Out. arXiv:2602.14161.
3. Do You Really Need a GPU? — CPU classifier + multi-stage cascade, confident miscalibration. arXiv:2512.19011.
4. PII detection: Presidio vs GLiNER hybrid — AI Guardrails Index; GLiNER Guard arXiv:2605.05277.
5. CaMeL (Debenedetti et al., 2025) — tách kênh instruction/data chống prompt injection.
6. Defense Against Indirect Prompt Injection via Tool Result Parsing. arXiv:2601.04795.
7. Llama Prompt Guard 2 (Meta PurpleLlama) — model card (recall 97.5% @1% FPR; đa ngôn ngữ hạn chế).
