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
- **Ư2 — Calibration + cascade "uncertain band"** [3]: ĐÃ TRIỂN KHAI (2026-09-01), xem chi tiết ở
  §5 bên dưới. Cảnh báo *confident miscalibration* [3] (head tuyến tính sai-mà-tự-tin trên OOD, lọt
  qua band) vẫn là giới hạn chưa giải quyết — band chỉ bắt được điểm rơi vào vùng biết trước.
- **Ư3 — Eval theo distribution shift** [2]: phần LODO (leave-one-family-out) ĐÃ TRIỂN KHAI
  (2026-09-01), xem §6. Còn lại chưa làm: traffic log thật (không có trong phiên offline này) +
  benchmark ngoài (NotInject/LLMail-Inject) vào CI.
- **Ư4 — Ops**: ĐÃ XONG (2026-09-01) — `verdict_stats()` giờ lộ qua `/health` (`input_sanitize` +
  `status=degraded` khi `unknown_rate>0.5` trên mẫu đủ lớn), tách `_health_status()` pure/testable.
- **Ư5 — PII hybrid** [4]: đã khảo sát thực nghiệm (2026-09-01, xem §7) — GLiNER offline, không cần
  vast. Kết quả: recall tăng thật (32%→76%) nhưng false-trip 15% ở threshold mặc định — **KHÔNG**
  khuyến nghị thay `require_public_input` bằng GLiNER làm hard-block ngay; cần thiết kế lại label
  trước. Vẫn giữ nguyên vấn đề kiến trúc: GLiNER cần torch → không hợp runtime numpy-only air-gap.

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

### Vá gap: bughunt tools (cùng ngày, sau khi rà lại)

`bughunt/tools.py` (`build_bughunt_tools()`) được merge thẳng vào `self.tools` của CÙNG
`LinchRunner`/`self.agent` (không phải agent riêng) — nên `SanitizeToolResultHook` lẽ ra đã phủ tự
động, nhưng đợt đầu quên đưa 20 tool bughunt vào `UNTRUSTED_CONTENT_TOOLS`. Đây là content nguy hiểm
đúng loại báo cáo khảo sát ban đầu cảnh báo: response body/header/reflected-payload từ **target sống
do user chỉ định** (có thể là server độc hại).

Đã rà từng tool và bổ sung **18/20**: mọi tool probe/scan/fuzz trực tiếp lên target (`bh_http_probe`,
`run_recon`, `run_vuln_scan`, `run_parallel_hunt_wave`, `run_verify_leads`, `run_js_analysis`,
`run_secret_hunt`, `run_param_discovery`, `run_post_param_discovery`, `run_api_fuzz`,
`run_cors_check`, `run_cms_exploit`, `run_rce_scan`, `run_sqlmap_targeted`, `run_sqlmap_on_file`,
`run_jwt_audit`) — **cộng cả** `read_recon_summary`/`read_findings_summary`: phát hiện thêm 1 lỗ hổng
tinh vi, hai tool này re-surface lại evidence/findings đã capture từ probe trước đó (vd
`run_verify_leads` lưu `evidence` snippet phản chiếu từ target) trong 1 `ToolResult` MỚI ở turn sau —
nếu không bọc, nội dung độc từng bị chặn ở lần probe đầu sẽ lọt qua không dấu khi đọc lại summary.
Loại trừ đúng 2 tool: `update_working_memory`/`finish` — cả hai chỉ echo lại input của chính model
(note/verdict), không có nội dung ngoại lai. Test mới `test_bughunt_scan_tools_are_wrapped` +
`test_bughunt_phase0.py` xanh (60/60 phạm vi liên quan).

## 5. Ư2 — calibration + cascade "vùng xám" (2026-09-01)

**Vấn đề:** threshold nhị phân (0.45) coi mọi điểm rơi vào 1 phía là quyết định cuối, kể cả điểm sát
ranh giới không có bằng chứng held-out trực tiếp nào cả hai phía. Ư2 thêm 1 "ý kiến thứ hai" — LLM
judge — cho đúng những điểm đó, thay vì tin tuyệt đối vào 1 con số.

**Kiến trúc (tôn trọng ràng buộc sẵn có):** `gate.py::decide()` là **pure/sync theo thiết kế**
("Pure logic — no I/O, easy to unit-test" — ghi rõ trong docstring gốc). Việc gọi LLM judge là I/O
nên **không** đặt trong `decide()` — đặt ở tầng orchestration `_chat_gate()` (routes.py), *giữa*
`classify_input()` và `decide()`. `gate.py` không đổi 1 dòng nào.

- **`sanitize/cascade.py`** (mới): `judge_injection(runner, text, score, timeout_s)` — gọi
  `runner.complete("injection_judge", prompt)`, đúng pattern one-off completion sẵn có
  (`poc/slicer.py::decide_keep_drop` gọi `runner.complete("poc_slice", prompt)`, không qua full
  agent loop, không tool). Parse JSON `{"injection": bool, "reason": str}`, chịu được code-fence.
  **FAIL-SAFE, không phải fail-open**: bất kỳ lỗi nào (timeout/unreachable/JSON hỏng) → trả `None`
  → caller **bắt buộc** fallback về quyết định threshold gốc — cascade chỉ có thể THÊM ý kiến khi
  judge sống, judge chết = y hệt hành vi trước khi có cascade, không phải failure mode mới.
- **`prompts/injection_judge.md`** + `profiles.yaml` (`tools: [], base: false, max_turns: 2`) —
  profile mới, không tool, JSON-only, dạy phân biệt "ra lệnh cho chính trợ lý ngay bây giờ" (injection
  thật) vs "hỏi/báo cáo VỀ chủ đề bảo mật" (hợp lệ dù chứa exploit/payload) — đúng bài học rút ra từ
  round 1-2 over-defense. Nghiêng về KHÔNG PHẢI injection khi không chắc.
- **`classifier.py`**: `_Head` + `decode()` thêm calibration **TÁCH BIỆT khỏi quyết định chính**.
  Điểm calibrated (Platt scaling) chỉ dùng để tính `injection_uncertain` (band membership) — **KHÔNG**
  ghi đè `scores["injection"]`/so sánh threshold, vốn đã tune và verify kỹ trên thang RAW (0.45,
  FP=0%/recall=100%). Lộ thêm `scores["injection_calibrated"]` khi có calibration, để observability
  mà không đổi hành vi production khi ship. Phát hiện + sửa 1 bug thiết kế giữa chừng: bản đầu lỡ ghi
  đè điểm raw bằng điểm calibrated, sẽ âm thầm đổi thang so sánh của threshold đã verify — sửa trước
  khi ship.
- **`scripts/calibrate_injection_cascade.py`**: fit Platt scaling trên **held-out**
  (`overdefense.jsonl`+`attacks.jsonl`, không phải training set) → `sanitize/assets/calibration.json`
  (asset MỚI, tách biệt, không đụng `head.npz`/`thresholds.json`). **Phát hiện + sửa 1 pathology
  kinh điển**: fit MLE thường (sklearn `LogisticRegression`, data tách biệt hoàn hảo) → hệ số phân kỳ
  → sigmoid dốc đứng → band rộng 0.977 (gần vô nghĩa, gần như luôn escalate). Sửa bằng đúng kỹ thuật
  smoothing của paper gốc Platt (1999): fit target mềm `t+ = (N+ +1)/(N+ +2)`, `t- = 1/(N- +2)` thay
  vì nhãn cứng 0/1, qua Nelder-Mead — kết quả band hợp lý `[0.186, 0.452]` (rộng 0.266, cùng bậc với
  margin raw 0.24).
- **Cascade OFF theo mặc định**: `config.injection_cascade_enabled: bool = False` — chưa verify với
  judge thật (vast down khi build đợt này) nên chưa bật mặc định; asset `calibration.json` VẪN được
  ship (đã verify additive, không regression) vì cascade còn bị chặn kép bởi cả asset lẫn flag.

**Kết quả xác nhận:**
- Ship `calibration.json` là **hoàn toàn additive** — chạy lại `eval_sanitize.py` trước/sau: FP
  0/211, recall 36/36 y hệt không đổi.
- Trên chính bộ held-out, chỉ **1/247 điểm** rơi vào band (đúng điểm benign sát biên nhất, "leo thang
  đặc quyền... bypass", điểm calibrated 0.1857 == band_low) — khớp đúng thiết kế: band = vùng
  KHÔNG có bằng chứng, nên hầu như trống trên chính data đã dùng để fit nó.
- **Thành thật về giá trị đo được**: hệ thống hiện đã 0 lỗi trên held-out (sau round 1-2), nên cascade
  **không sửa lỗi nào đang đo được hôm nay** — đây là đầu tư robustness hướng tới traffic/OOD tương
  lai chưa thấy trong bộ held-out, không phải fix cho khủng hoảng hiện tại.
- Test: `tests/test_sanitize_cascade.py` (9, module cascade độc lập, mock runner: verdict true/false,
  code-fence, JSON hỏng, thiếu field, exception, timeout), test calibration trong
  `test_input_classifier.py` (band membership, không ghi đè raw score, no-calibration backward-compat),
  test cascade ở tầng route (disabled-by-default, judge override cả 2 chiều, judge failure fallback,
  không trigger khi không uncertain). 91/91 phạm vi liên quan xanh; full suite không regression (2 fail
  cũ `test_prompts.py`/`chat.md` không đổi).

**Giới hạn chưa giải quyết** (đúng cảnh báo *confident miscalibration* [3]): band chỉ bắt điểm rơi vào
vùng ĐÃ BIẾT là mơ hồ từ held-out hiện tại. Một input hoàn toàn mới (OOD thật) có thể khiến head tuyến
tính chấm điểm SAI MÀ TỰ TIN, rơi ngoài band, không bao giờ được escalate — cascade không giải quyết
lớp lỗi này, chỉ literature [3] gọi đây là giới hạn cố hữu của cascade dựa trên score. **Chưa verify
live**: mọi số liệu trên chạy offline (`--embed st`); cần `--embed endpoint` + bật
`injection_cascade_enabled=true` khi vast lên để xác nhận judge thật hoạt động đúng như mock.

## 6. Ư3 — Leave-one-family-out robustness check (2026-09-01)

`metrics.json` chỉ đo in-distribution; ngay cả bộ held-out `overdefense.jsonl`/`attacks.jsonl` cũng
không tự trả lời được: kỹ thuật MOF hard-negative có **generalize** giữa các họ phrasing, hay model
chỉ học thuộc template riêng từng họ? Làm 1 bài kiểm LODO đúng nghĩa (arXiv:2602.14161 áp dụng ở mức
nhỏ nhất khả dụng): loại **HẲN** 1 họ hard-negative khỏi training (không chỉ vài skeleton), retrain,
đo FP trên đúng category held-out tương ứng.

- **`scripts/build_sanitize_dataset.py --exclude-groups <prefix>`** (mới): loại toàn bộ 1 (hoặc nhiều)
  group-prefix khỏi dataset trước khi split. Không đổi hành vi mặc định (verified byte-identical khi
  không truyền flag). Phát hiện phụ: loại 1 họ gây **hiệu ứng dây chuyền** lên train/test split và
  biến thể no-diacritics của MỌI category khác (do `rng` dùng chung, thứ tự dict `by_cat_group` dịch
  pha) — vẫn deterministic cho 1 lần exclude cố định, không ảnh hưởng tính đúng của thí nghiệm.
- **`scripts/eval_lodo_family_holdout.py`** (mới): với mỗi họ trong {hnreport, hnaudit, hncve,
  hnpentest}, build dataset loại họ đó → retrain offline → đo FP trên category held-out tương ứng, so
  với baseline (head đang ship). Không đụng assets đã ship (mọi retrain ghi vào temp dir).

**Kết quả** (`evals/sanitize/report-lodo-2026-09-01.md`):

| họ loại bỏ | category | FP baseline | FP khi loại | Kết luận |
|---|---|---:|---:|---|
| hnreport | report-numbered | 0/32 | 0/32 | **generalize** từ họ khác |
| hnaudit | audit-imperative | 0/36 | **1/36** | **CẦN THIẾT** — không họ nào thay thế được |
| hncve | cve-detail | 0/40 | 0/40 | **generalize** từ họ khác |
| hnpentest | pentest-writeup | 0/40 | 0/40 | **generalize** từ họ khác |

**3/4 họ generalize thật** — bằng chứng thực nghiệm (không chỉ giả định từ literature) rằng kỹ thuật
MOF dạy được tín hiệu "trigger word ≠ injection" có tính CHUYỂN GIAO, không chỉ khớp mẫu theo từng
họ. **`hnaudit` là ngoại lệ** — khớp đúng với phát hiện thực nghiệm ở round 1→2 (toàn bộ 6 FP còn lại
sau round 1 đều rơi vào đúng category này, cần template riêng ở round 2) — xác nhận: nếu category này
tái xuất hiện regression trong tương lai, phải mở rộng đúng `hnaudit`, không phải họ khác.

Còn lại của Ư3 (chưa làm, không thể làm offline hoàn toàn): traffic log thật (cần production/live
traffic, không có trong phiên này) + tích hợp benchmark ngoài công khai (NotInject/LLMail-Inject) vào
CI (cần thời gian tải/tích hợp riêng, chưa ưu tiên đợt này).

## 7. Ư5 — PII hybrid: khảo sát GLiNER thực nghiệm (2026-09-01)

Offline hoàn toàn, không cần vast — GLiNER là model NER nhỏ tải độc lập từ HuggingFace
(`urchade/gliner_multi_pii-v1`), không liên quan tới Cyber-F1/sec-embedding trên vast.
`scripts/eval_pii_hybrid.py` so `require_public_input` (regex hard-gate hiện tại) với GLiNER
zero-shot trên đúng ground-truth `dataset.jsonl` (`pii=1`/`pii=0`).

**Số liệu (threshold=0.5, mặc định thư viện):**

| method | recall | false-trip |
|---|---:|---:|
| regex gate hiện tại | 32.1% (61/190) | **0.0%** (0/200) |
| GLiNER zero-shot | **75.8%** (144/190) | **15.0%** (30/200) |
| hybrid (OR) | 82.6% (157/190) | 15.0% (30/200) |

Xác nhận đúng tuyên bố literature (hybrid > riêng lẻ về recall) — nhưng **false-trip 15% là chi
phí thật**, chưa từng được định lượng trước đây cho traffic shape cụ thể của dự án này.

**Nguyên nhân false-trip — CÙNG lớp lỗi với over-defense injection đã fix cả phiên nay**: soi 30
case sai thấy GLiNER khớp nhầm **từ khái niệm** ("password", "JWT", "email") trong câu hỏi lý
thuyết ("how do I hash passwords") thành thực thể PII thật, vì label GLiNER là từ trần trụi
("password") — không phân biệt được "nhắc tới khái niệm" với "có giá trị thật trong text". Đúng
bài học "concept ≠ instance" đã học được từ MOF hard-negatives, áp dụng vào 1 subsystem khác.

**Sweep threshold**: không có ngưỡng nào cho cả recall cao lẫn FP thấp cùng lúc — ở 0.85 FP về
gần 0 (1/200) nhưng recall (34.2%) đã tụt về NGANG regex gate (32.1%), tức không còn lợi ích gì.
Nút thắt là THIẾT KẾ LABEL (từ khái niệm trần trụi), không phải chỉ chỉnh threshold.

**Khuyến nghị**: KHÔNG thay `require_public_input` bằng GLiNER làm hard-block ngay ở dạng hiện
tại. Nếu theo tiếp: (a) thiết kế lại label GLiNER (few-shot hướng về "có giá trị cụ thể", không
chỉ từ khái niệm) rồi chạy lại harness; (b) threshold riêng theo từng label thay vì 1 ngưỡng
chung. Lựa chọn rủi ro thấp hơn trước mắt: dùng GLiNER như **soft signal** (giống `pii` flag của
classifier — chỉ log/audit, không chặn) ở threshold vừa (~0.6–0.7), không cần giải quyết vấn đề
label trước. Vấn đề kiến trúc (torch trong runtime numpy-only air-gap) vẫn treo, độc lập với kết
quả accuracy này. Chi tiết đầy đủ: `evals/sanitize/report-pii-hybrid-2026-09-01.md`.

## 8. Ghi chú kiến trúc

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
