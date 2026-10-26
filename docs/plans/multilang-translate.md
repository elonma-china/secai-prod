# SecAI Multilang Translation — Implementation Plan

> **For agentic workers:** Use `superpowers:subagent-driven-development` task-by-task. Điều phối chỉ quản lý plan, agent, feedback và trạng thái; sol mới viết feature code. Không dùng native execution thay cho mô hình multi-agent đã yêu cầu.

**Goal:** Thêm 16 hướng `zh/ja/ko/fr/de/es/ru/ar ↔ vi`, giữ nguyên dịch `en ↔ vi` và API hiện có, làm song song an toàn với dịch file/OCR.

**Architecture:** Module/API đa ngôn ngữ cộng thêm, dùng đúng LLM hiện tại; lớp tương thích prompt/provider và bộ bảo toàn literal riêng. Đăng ký qua ASGI composition mới để không sửa app/router/profile đang thuộc task file. Benchmark năng lực model và benchmark toàn pipeline là hai phép đo khác nhau.

**Tech Stack:** Python/FastAPI/Pydantic/Linch/httpx; React/TypeScript/Vite; pytest; bộ đánh giá offline tách dependency khỏi runtime.

**Spec:** [Yêu cầu gốc, giữ nguyên văn](multilang-source-requirements.txt). Ngày khảo sát: 2026-10-03. Trạng thái: **PLAN, chưa triển khai feature, chưa benchmark live, chưa kết luận năng lực LLM**.

## Global Constraints

- 16 hướng mới: `vi↔zh`, `vi↔ja`, `vi↔ko`, `vi↔fr`, `vi↔de`, `vi↔es`, `vi↔ru`, `vi↔ar`. Hai hướng hồi quy: `vi↔en`. Không yêu cầu dịch trực tiếp giữa hai ngôn ngữ ngoài tiếng Việt.
- Người dùng xác nhận `main`, Trung giản thể (`zh-Hans`), Ả Rập Modern Standard Arabic. `zh` là alias của `zh-Hans`; không mở `zh-Hant` hay phương ngữ trong task này.
- Chỉ LLM hiện tại sinh bản dịch. Không model dịch thứ hai, fallback sang model khác, ensemble hay dịch-then-postedit bằng model khác. LoRA/QLoRA chỉ trên cùng base model, sau Gate C. Language-ID/QE/rules không được sinh bản dịch. COMET/xCOMET/judge chỉ offline.
- API/schema/hành vi cũ, prompt, chunker, client LLM, config mặc định cũ giữ nguyên. Feature flag server-side cho từng ngôn ngữ mới mặc định OFF; không được bật bằng request.
- Không sửa nhánh/worktree file/OCR; không checkout, commit, rebase, force-push hay xóa trên các nhánh đó. Mọi mã nguồn mới được viết trong worktree riêng từ base chung.
- Không chạy benchmark/training ảnh hưởng Vast đang phục vụ hay OCR đang đánh giá. GPU xếp hàng. Không thuê instance, tải model, thay serving hoặc chi tiền khi chưa có ngân sách/resource gate.
- Off-target **≤2%**, bảo toàn thực thể **100%** trên corpus có CVE/IP; API cũ không đổi. Biên hồi quy en↔vi và ngưỡng chất lượng từng hướng do astra A đề xuất sau DEV baseline, người dùng chốt một lần.
- Tối đa **5 vòng sửa/review mỗi task**, **3 lần quay lại từ vanguard**. Lỗi tái xuất hiện hai lần sau báo sửa phải đổi cách; số lỗi không giảm hai vòng phải dừng task và ghi lý do. Không hạ tiêu chí hoặc bỏ case để đạt.
- Mỗi lượt acceptance **30 mẫu/hướng ×18 hướng**, ít nhất **3 lượt**: tối thiểu **1.620 lần dịch** chưa tính retry/baseline/DEV. Fail là fail, không dùng trung bình toàn bộ ngôn ngữ để che hướng yếu.

## Review Focus

1. CJK bị xóa ở JSON final **và token SSE** dù model dịch đúng: T2/T3/T4 phải chứng minh đường mới giữ chữ, đường cũ giữ hành vi.
2. Credential/code lồng nhau, placeholder do người dùng tự nhập và literal cắt qua chunk: T3 kiểm tra nguyên vẹn, số lần xuất hiện, thứ tự; thiếu token phải fail closed.
3. Lỗi chunk N, retry, client disconnect hoặc timeout: T3/T4 không trả bản dịch thiếu và không để các request LLM còn chạy vô hạn.
4. Arabic pha Latin/số, ZWJ/ZWNJ hợp lệ và bidi override độc hại: T3/T5/T8 phân biệt và kiểm tra cả hiển thị lẫn copy; không xóa toàn bộ ký tự nối chữ.
5. Merge Git sạch nhưng v2 middleware/job lifecycle hoặc global flag bị đổi: T0/T7/T9 chạy test trên cây ghép thực sự, so với baseline từng nhánh và snapshot API.

## 0. Bằng chứng hiện trạng và công cụ

### Git, code và test

| Mục | Đã quan sát | Ý nghĩa cho plan |
|---|---|---|
| Base chung | `main` = `origin/main` = `ce9cd47ee1273018fec1c5cbab944a86d68d6ed5` sau fetch | Khóa lại SHA ở lúc execution; không lấy nhánh WIP làm base |
| Dịch file | `feature/import-file-translate`, snapshot `4505c570167ebd0e8ddfba752308b1811f88383b` | Đang code; SHA sẽ thay đổi, phải refresh mỗi chu kỳ |
| OCR | `feature/image-ocr-translate`, snapshot `404f93a90958027fe0d2b386c45477b724adffc6` | Cũng là vùng bảo vệ, dù user nhắc riêng dịch file |
| Merge-base | Cả hai nhánh có merge-base với main là `ce9cd47` | Có thể tạo task mới độc lập từ main |
| FE canonical | `secai-fe-devspace/` bên trong gateway, hiện untracked; main còn track FE ở sibling | T5 cần snapshot nguồn FE có manifest; không commit deletion/move tại workspace người dùng |
| Bộ lọc cũ | `api/routes.py:756,804`; `api/sse.py:57-62`; `security.py:414-422` | Xóa CJK; không thể chỉ thêm option ngôn ngữ vào UI |
| Prompt/chunk cũ | `messages.py:108-142`, `literal_mask.py:72-116`, `text_chunk.py` | Prompt thiên vi/en; chunk theo ký tự trước mask; không đổi trong task này |
| LLM khai báo local | provider `vllm`, model `DuyTa/Cyber-F1-AWQ`, context cấu hình `65536` | Chưa xác minh model revision, GPU/engine live, lượng tử thực tế hay max output |
| Decode khai báo | temperature `0.3`, top_p `.95`, top_k `20`, min_p `0`, presence penalty `1.1`, repetition penalty `1.0` | Baseline ghi đúng cấu hình; chạy thử thay decode trên dev, không đổi globals |
| Dependency local | Linch `1.1.0`; pyproject yêu cầu `>=1.2.1,<2` | T0 lập môi trường riêng đúng dependency; không nâng cấp venv đang dùng chung |
| Test vừa chạy khi rà dịch | 63 passed, 17 deselected trong tập lựa chọn; fake runner, không live LLM | Không coi là full-suite PASS hoặc chứng minh chất lượng đa ngôn ngữ |
| Full baseline của task file | Tài liệu `docs/test-results/translate-baseline.md`: 905 pass, 19 fail, 1 skip /925 | Bằng chứng của task khác với harness timeout 10s; T0 chạy baseline riêng và ghi từng node ID, không tái nhãn thành PASS |

Model family/size nêu trong comment provider chỉ là manh mối; T2 phải kiểm tra model card, revision và serving thực tế. Không suy ra hỗ trợ LoRA từ tên AWQ hoặc từ việc vLLM nói chung có LoRA.

### Inventory khả dụng và mục đích

| Công cụ/skill/plugin | Sử dụng |
|---|---|
| Collaboration MCP/harness (`spawn_agent`, message, followup, interrupt) | astra A/B, sol, reviewer, tester, vanguard, warden; mọi feedback đi qua điều phối |
| `exec`, `apply_patch`, git, uv, python, node/npm, Docker, nvidia-smi, vastai CLI | Khảo sát; worktree riêng; test/build; preflight tài nguyên. Có CLI không đồng nghĩa có quyền/daemon/endpoint |
| `writing-plans`, `brainstorming`, `ponytail`, `audit-context-building` | Thiết kế/impact map, giữ thay đổi nhỏ, rà hợp đồng helper |
| `llm-evaluation`, `python-testing-patterns`, `property-based-testing`, `async-python-patterns` | Corpus, metric/CI, literal/chunk invariant, async timeout/cancel |
| Superpowers worktrees/TDD/debugging/review/verification | Áp dụng đúng giai đoạn khi execution; không thay quy trình/role user chỉ định |
| Web search/open | T2 tra model card, license/revision, benchmark công bố, vLLM LoRA/tokenizer và metric từ nguồn gốc; ghi ngày truy cập |
| T3 `preview_*` | FE CJK/Arabic/RTL và smoke staging; status rồi open. Chỉ fallback Playwright nếu T3 không khả dụng/không hỗ trợ |
| T3 PR linking | Link tất cả PR của task nếu tạo; chưa tạo PR trong lượt plan |
| Security/differential-review, Semgrep/CodeQL/SARIF | Vanguard chọn scan theo surface; kiểm tra CLI/license trước. Chưa thấy pip-audit/trivy trong PATH |
| Nodeterm CLI | Chỉ khi NODETERM_NODE_ID có; hiện không thấy session canvas. Dùng collaboration harness |
| Google Drive/Pages/Desktop Commander/plugin tools và skill Figma/deploy/Pets… | Có trong catalog nhưng không cần; không gửi dữ liệu dự án ra connector |

Giới hạn: **4 agent đồng thời tính cả điều phối**, không hứa 4 sol + warden cùng chạy. Astra dùng model `gpt-6-astra`, sol dùng `gpt-6-sol` theo yêu cầu; vanguard/warden là **vai trò**, không phải tên model có sẵn. Không có model đúng yêu cầu thì báo, không thay âm thầm.

## 1. Bản đồ chồng lấn và nguyên tắc cô lập

Đường dẫn dưới đây tương đối với gateway; worktree Git luôn chứa monorepo, nên chạy lệnh app từ `secai/secai-agent-gateway` trong worktree.

| Vùng đang có owner khác — không sửa | Lý do |
|---|---|
| `src/secai_gateway/app.py`, `api/middleware.py`, `pyproject.toml` | File branch đang thêm router, chính sách v2 và dependencies |
| `src/secai_gateway/api/translate_v2/**`, `translation/**` | API file/image/jobs, worker, service, types và technical-token helper của file/OCR |
| `src/secai_gateway/prompts/profiles.yaml`, `prompts/ocr_correction.md`; dự kiến `agent/runner.py` | OCR profile/điều phối đang dùng; bảo vệ cả ownership dự kiến, không chỉ diff hiện có |
| `deploy/translate-v2/**`, `deploy/ocr/**`, `evals/translation_ocr/**`, `tests/test_translate_v2_*` | Runtime, benchmark, tests của task kia |
| `docs/loop/STATE.md`, `docs/loop/LOG.md`, criteria/contract/plan import-OCR | Tài liệu điều phối đang được cập nhật liên tục |
| `api/routes.py`, `api/messages.py`, `api/literal_mask.py`, `api/text_chunk.py`, `api/sse.py`, `api/schemas.py`, `security.py`, `config.py`, `prompts/translate.md`, `agent/providers.py` | Legacy/shared: chỉ đọc/tái sử dụng interface; không sửa để thêm đa ngôn ngữ |

**Ngoại lệ đường dẫn STATE/LOG:** dùng `docs/loop/multilang/{STATE,LOG}.md`, vì đường dẫn gốc trong prompt đang thuộc task file. Đây là cách giữ yêu cầu bền trạng thái mà không ghi đè task đang chạy. Điều phối là người ghi duy nhất; warden gửi cập nhật qua điều phối.

T0 xuất danh sách cụ thể từ `git diff --name-only BASE...BRANCH`, cộng tracked WIP và source untracked trong worktree kia (không quét venv/build). Snapshot kèm SHA/hash; không giả định Git diff thấy được thay đổi chưa commit. Làm lại mỗi chu kỳ.

### Nhánh và ownership

| Agent | Nhánh/worktree riêng từ BASE_SHA | Sở hữu |
|---|---|---|
| astra A | `chore/multilang-criteria` | criteria từng task + contract; không feature code |
| sol-1 | `feature/multilang-assessment` | `evals/multilang/**`, `docs/test-results/multilang/**` |
| sol-2 | `feature/multilang-core` | `src/secai_gateway/multilang/**`, `api/multilang/**`, `multilang_app.py`, `tests/multilang/**`, `deploy/multilang/**` |
| sol-3, chỉ Gate C | `feature/multilang-adapter` | `training/multilang/**`, adapter manifest/config mới; checkpoint ngoài repo/instance |
| sol-4 riêng | `feature/fe-devspace-multilang` | FE canonical mới snapshot, các file UI/API multilang; không sửa FE đang ở workspace user |
| astra B riêng | `integration/multilang-translate` | Chỉ tích hợp SHA đã qua gates; không tự thêm tính năng |

Chưa tạo các nhánh/worktree này trong lượt lập plan. Không tạo adapter branch trước Gate C. Reviewer/tester/vanguard mỗi lần dùng worktree review độc lập ở SHA cụ thể, context mới, không sửa feature code.

## 2. Thiết kế đề xuất và hợp đồng để astra A freeze

### Lựa chọn kiến trúc

1. Sửa trực tiếp prompt/validator/sanitizer cũ: ít file nhưng đổi behavior cũ, va chạm task file — loại.
2. **Chọn:** namespace `multilang/` + router mới + ASGI composition mới; cùng model/provider, config riêng. Cô lập tối đa, kiểm chứng merge được, không cần hook vào luồng dịch cũ.
3. Service dịch riêng hoàn toàn: giảm coupling nhưng thêm deploy/network/hạ tầng; chưa cần. Không chọn model khác dưới bất kỳ phương án nào.

`multilang_app.py:create_app()` gọi `app.create_app()` hiện có; master flag OFF trả nguyên app legacy, không thêm router/middleware. ON đăng ký router mới và bộ giới hạn chỉ áp dụng prefix mới. Dùng entrypoint composition trong staging/overlay triển khai mới; giữ nguyên entrypoint/Docker stack cũ. Nếu khi tích hợp bắt buộc thêm hook vào `app.py`, chỉ astra B phối hợp owner file, tối đa một điểm đăng ký nhỏ, flag OFF giữ behavior; unresolved overlap là STOP, không tự sửa.

Không dùng `api.routes.complete`, `api.routes.stream` hay `event_to_sse` cũ cho output đa ngôn ngữ vì bộ lọc CJK. Không dùng mutable global PROFILES để thêm profile. Wrapper LLM dùng giao diện provider hiện có trong context/session memory riêng, tools rỗng, system prompt riêng; không persist source/output vào session/ledger cũ. Wrapper giữ riêng sampling/max tokens, không mutate provider dùng chung. T2/T3 phải chứng minh cùng endpoint + model revision đã khóa; mismatch hoặc provider fallback thì fail closed.

### API cộng thêm (đề xuất, freeze ở T1)

- `GET /ai/v3/translate/languages`: capability registry; từng hướng có canonical code, aliases, script, direction, status `disabled|beta|stable`. Beta chỉ được bật khi user có quyết định sau Gate D.
- `POST /ai/v3/translate`: JSON text, `source_lang` và `target_lang` bắt buộc; chỉ nhận 16 hướng mới. `zh` canonicalize thành `zh-Hans`; `ar` nghĩa MSA. Không nhận `auto` trong API mới ở v1; API cũ giữ auto như hiện tại.
- Request: `{"text":"...","source_lang":"vi","target_lang":"zh"}`. Extra fields bị từ chối; không có field model, adapter path, endpoint, enable flag, role hay user_id.
- Success: `{"translated_text":"...","source_lang":"vi","target_lang":"zh-Hans","warnings":[],"metadata":{"request_id":"...","chunks":1,"attempts":1}}`. Không trả system prompt, secrets, provider endpoint hay raw usage trace.
- 400 `INVALID_REQUEST|UNSUPPORTED_LANGUAGE|UNSUPPORTED_PAIR`; 403 `LANGUAGE_DISABLED`; 413 `TEXT_TOO_LONG|TOKEN_LIMIT_EXCEEDED`; 422 `SOURCE_LANGUAGE_MISMATCH|UNSAFE_UNICODE`; 429 `RATE_LIMITED` + Retry-After; 502 `EMPTY_TRANSLATION|OUTPUT_VALIDATION_FAILED`; 503 `MODEL_UNAVAILABLE`; 504 `TRANSLATION_TIMEOUT`. Master flag OFF: routes không tồn tại (404).
- API mới v1 trả **JSON sau khi tất cả chunk đã validate**; không thêm job/file/OCR hoặc SSE mới. Legacy en↔vi giữ JSON/SSE cũ. FE giữ màn cũ và chỉ dùng API mới cho 16 hướng. Không định tuyến en↔vi qua prompt mới.
- Không mở ngôn ngữ cho `/ai/v2/translate/files|images|jobs` trong đợt này. Sau khi hai nhánh ổn định, muốn thêm vào file phải có contract phối hợp riêng và test hai bên.
- Guard đề xuất để T1 freeze: tối đa 24.000 ký tự, 8.192 source tokens/request, 16 chunk/request, 1 retry/chunk, 120s deadline toàn request, 2 model call đang chạy/process; input chunk ≤2.048 tokens, output ceiling ≤4.096 và luôn nằm trong context đã xác minh. Mọi giá trị qua config riêng; không đổi giới hạn cũ. Thêm global admission và trusted-IP rate cap, không chỉ semaphore từng request.
- API text mới không lưu job/history. Hiện hệ thống dùng private ingress, legacy/file API anonymous. **Identity policy phải được T1 ghi rõ trước API CODE:** giữ anonymous stateless theo convention thì test token/role/IDOR là N/A có lý do, không ghi PASS giả; nếu cần auth riêng thì dùng integration auth hiện hữu đã xác định, không tự xây login hay thay API cũ. Không để ambiguity này trôi tới vanguard.

### Pipeline và rollout

Registry canonical → feature/pair/input guards → nhận diện ngôn ngữ/chữ viết → bảo vệ literal theo span không chồng lấn → chunk theo token/câu của ngôn ngữ → prompt pair + gọi **cùng LLM** → validator → tối đa 1 retry → ghép đúng thứ tự → kiểm literal và đầu ra toàn văn → JSON plain text.

- NFC chỉ áp dụng phần prose; literal phải khớp nguyên bản. CJK nhận `。！？`, Arabic nhận dấu câu tương ứng; context overlap chỉ cung cấp context, không được lặp nội dung trong kết quả.
- Literal giữ CVE/IP/CIDR/URL/hash/số/ngày/code/tên riêng bằng map có nonce; collision/nested fences/cross-chunk/drop/duplicate/reorder đều có test. Không sửa hoặc cherry-pick technical_tokens WIP từ nhánh file.
- Nhận diện source một từ/mixed-script phải xử lý uncertainty; không khẳng định sai nguồn chỉ vì ít ký tự. Glossary chọn đúng chiều; lịch sự Nhật/Hàn, vous/Sie/vy và MSA được ghi trong registry/prompt config.
- Output plain text Unicode; FE dùng React text node, không raw HTML. Kiểm prompt leakage, lặp/cắt cụt/off-target và literal. Không tái sử dụng bộ lọc VI/EN; không bỏ chống XSS/injection.
- Master flag + allowlist ngôn ngữ đều do server cấu hình, các ngôn ngữ mới OFF. Chỉ bật một ngôn ngữ khi **cả hai chiều** đạt và được rollout; nếu một chiều fail thì giữ ngôn ngữ OFF, báo Gate D.

## 3. Thứ tự, song song và decision gates

```text
T0 overlap/preflight → T1 astra A criteria & contract
  → T2 ASSESS + Gate A DEV baseline → user chốt ngưỡng một lần
  → Gate B thí nghiệm DEV trên cùng LLM → [Gate C nếu cần] → Gate D kết luận
  → T3/T4 core + T5 FE + T2 hoàn thiện benchmark (độc lập, theo slot)
  → T7 review/fix → acceptance 3 lượt → T8 vanguard
  → T9 astra B integration + simulated merges/tests → user duyệt merge main
  → T10 tài liệu API và báo cáo cuối
```

Để tránh vòng phụ thuộc “phải code mới đo Gate B nhưng CODE chỉ sau gates”: Gate B chỉ có harness/prompt/thí nghiệm nằm trong assessment worktree, chưa đăng ký API hoặc đổi production. Sau khi có hướng đáp ứng, sol-2 hiện thực cấu hình thắng trong module sản phẩm. FE có thể làm mock sau khi contract/criteria freeze và bước ASSESS+gate kết luận, không phải chờ API thật.

Lịch slot: điều phối + tối đa 3 agent. Khi CODE: sol-core + sol-FE + sol-assessment; warden chạy tại checkpoint khi một slot rảnh. Khi Gate C chạy GPU, sol-FE/CPU tests có thể chạy song song; không benchmark GPU cạnh OCR hoặc training. Reviewer/vanguard thay slot coder đã idle, không vượt giới hạn.

## 4. Task thực thi có kiểm chứng

### T0 — Khóa base, ownership, môi trường và baseline (điều phối + sol preflight)

**Files:** tạo `docs/loop/multilang/{STATE,LOG}.md`, `docs/reviews/multilang-overlap.md`, baseline artifacts dưới `docs/test-results/multilang/`; không sửa file task kia.

- [ ] Fetch main; ghi BASE_SHA, SHA file/OCR, merge-base và danh sách overlap + ownership dự kiến. Chạy `git merge-tree --write-tree MAIN_SHA FILE_SHA` và tương tự OCR để có snapshot hiện trạng; conflict giữa các nhánh kia chỉ báo owner, không tự sửa.
- [ ] Tạo worktrees criteria/assessment/core/FE/integration từ BASE_SHA; mỗi agent môi trường riêng, không `pip install` vào venv chung. Chưa tạo adapter branch.
- [ ] Khóa dependency đúng pyproject và ghi version; kiểm `secai_gateway.__file__` thuộc đúng worktree (editable install trỏ sai cây là FAIL).
- [ ] Chạy `.venv/bin/python -m pytest -q` trên base cô lập, bảo vệ data paths/network. Nếu cần timeout harness, chạy trước/sau cùng cấu hình và lưu full JUnit/node IDs. Không deselect test lỗi để tuyên bố xanh.
- [ ] Preflight Vast chỉ metadata read-only: instance/model/engine/GPU/workloads, token budget, khả năng LoRA thực tế. Ghi `GPU_BUDGET_USD`, endpoint dev, owner và lịch reservation. Thiếu số/endpoint: chặn GPU jobs, vẫn làm docs/CPU tasks đã đủ criteria.
- [ ] Checkpoint tài liệu preflight; warden kiểm đường dẫn, resource ledger và không có thay đổi ở worktree khác.

### T1 — Astra A freeze tiêu chí và hợp đồng, làm trước code/benchmark

**Files:** `docs/criteria/multilang-{assessment,core,fe,adapter,integration-security}.md`, `docs/api/multilang-contract.md`.

- [ ] Mỗi requirement trong spec gắn criterion ID, task, phép đo, bằng chứng và PASS/FAIL. Chốt signatures/interfaces dưới đây, input guard và policy identity; ghi các ngưỡng quality/performance còn phụ thuộc Gate A là `PENDING_BASELINE`, không đặt số đẹp tùy ý.
- [ ] Khóa recipe dataset/DEV/TEST, rule chống leakage, reference policy, tokenizer/metric version, decoder/seed và phương pháp paired non-inferiority. Quality thresholds freeze sau A và trước B.
- [ ] Chốt assertion API unchanged, flags OFF unchanged, no new failures so với baseline, và test file/OCR phải chạy trên cây ghép.
- [ ] Checkpoint `CRITERIA_READY` cho assessment. Không đánh đồng với quality PASS hoặc production readiness; user review plan hoàn chỉnh trước triển khai.

### T2 — Sol-1 ASSESS, benchmark và gates A/B/C/D

**Files:** `evals/multilang/{run,score,manifest,validate_corpus}.py`, `evals/multilang/requirements.lock`, manifest/data đã kiểm license, reports `docs/test-results/multilang/`; test harness dưới `tests/multilang_assessment/`.

**Interfaces:** manifest gồm `id, direction, source, reference, split, stratum, protected_spans, source_revision, license`; result gồm `sample_id, phase, model_revision, prompt_hash, raw_output, validated_output, latency_ms, input_tokens, output_tokens, cost, error`. Chỉ corpus công khai/tổng hợp đã ẩn danh được lưu raw output.

- [ ] Test đỏ `test_split_leakage_rejected`, `test_all_18_directions_required`, `test_failed_rows_stay_in_denominator`, `test_shuffle_is_not_new_sample`: assert DEV/TRAIN/TEST không trùng normalized-hash/n-gram, đủ 18 hướng, timeout không bị loại khỏi mẫu số, sample IDs lặp không tăng n độc lập.
- [ ] Implement CLI có mode `model-only|legacy-api|multilang-api`; chỉ model ID/revision đã freeze được gọi. Chạy test xanh trước live. Checkpoint harness.
- [ ] Khảo sát model card/model config/serving: base vs instruct, total/active params, tokenizer revision, quant format, context/max output, chat template, decode, engine+version, GPU, license và khả năng nạp LoRA với chính quant/checkpoint này. Mục không tìm được phải ghi UNKNOWN/blocker.
- [ ] Gate A: DEV baseline đủ 18 hướng với prompt hiện tại và decode cố định thấp; thêm regression control dùng decode hiện tại để không che regression. Đo raw model và legacy API riêng: CJK mất sau sanitizer là pipeline failure, không phải chứng cứ LLM không biết dịch.
- [ ] Dữ liệu: kiểm license/revision/coverage FLORES/NTREX/WMT/Tatoeba trước chọn; thêm domain an ninh mạng từng hướng, reference đáng tin. Đo token/ký tự từng ngôn ngữ. TEST giữ kín; không dùng FLORES devtest làm train/dev.
- [ ] Report từng hướng: chrF++ chính, BLEU có tokenizer/signature phù hợp, COMET/xCOMET hoặc metric tương đương đã kiểm language coverage (offline), off-target/script, copy/repetition/hallucination/truncation/preamble, entity exact match, p50/p95, throughput, USD/1.000 ký tự. Không bịa cost khi thiếu tariff/token usage.
- [ ] Astra A dùng DEV baseline + tham khảo model cùng cỡ để đề xuất ngưỡng từng hướng, biên không suy giảm en↔vi, latency/VRAM/cost limits và CI; user xác nhận **một lần**. Chưa xác nhận thì không chạy B/C theo ngưỡng tùy đặt.
- [ ] Gate B: thử lần lượt pair prompt/few-shot → glossary/literal → language-aware chunk/NFC → decode/token budgets → validator+retry → guided decoding nếu engine xác nhận hỗ trợ. Mỗi ablation dùng DEV, giữ chỉ cải thiện đo được, không đổi model.
- [ ] Gate C: chỉ tạo T6 khi B chưa đạt và prerequisites đáp ứng. Gate D: bảng đạt/fail theo hướng; còn hướng fail thì giữ OFF và hỏi user, không tự gắn beta/bật một chiều.
- [ ] Xuất tối đa 20 câu/ngôn ngữ cho đánh giá bản ngữ nếu có người; trạng thái không có reviewer không được ghi human-PASS.

### T3 — Sol-2 core cộng thêm, phát triển test trước code

**Files:** `multilang/{types,settings,registry,provider_adapter,legacy_prompt_adapter,literals,chunking,validation,service}.py`, `multilang/prompts/`, `multilang/glossaries/`, `tests/multilang/test_{registry,literals,chunking,validation,service}.py` dưới package/tests tương ứng.

**Interfaces:** `LanguageSpec(code, aliases, script, direction)`; `resolve_pair(source:str,target:str)->LanguagePair`; `protect(text:str)->ProtectedText`; `restore(text:str, protected:ProtectedText)->str`; `plan_chunks(text:ProtectedText, pair:LanguagePair, budget:TokenBudget)->tuple[Chunk,...]`; `LlmPort.generate(request:GenerationRequest)->GenerationResult` async; `translate(request:TranslationRequest, *, llm:LlmPort, settings:MultilangSettings)->TranslationResult` async. GenerationRequest chứa system/user/decode/output budget; GenerationResult chứa raw text, stop reason, tokens, revision; không chứa tool actions.

- [ ] Test đỏ: đủ 16 pair, alias zh, reject fr→de/zh-Hant/auto; flag OFF reject trước LLM; fake model khác revision bị chặn; old config/profile không mutate.
- [ ] Test property/literal: `restore(protect(x).text, protect-map)==x` với cùng map; nested fenced code chứa password, user placeholder, CVE/IP/CIDR/URL/hash/số/ngày/tên riêng qua ranh giới chunk. Drop/duplicate/reorder phải error, không trả text thiếu.
- [ ] Test chunk: CJK không dấu cách, Arabic/Russian, NFC/NFD prose vs literals; token+character ceilings, overlap không duplicated output, stop_reason length không được success.
- [ ] Test validator: sai source/target, mixed-script/1 từ, nguồn rỗng/chỉ symbols, lỗi decode, prompt injection đa ngôn ngữ, bidi override vs ZWJ/ZWNJ hợp lệ. False-positive/unknown LID phải có policy trong criteria, không giả confidence.
- [ ] Test async: chunk N fail → request fail atomically; thứ tự kết quả cố định; retry≤1/chunk; whole request timeout/cancel hủy công việc còn lại; concurrency không vượt config trên nhiều request.
- [ ] Implement tối thiểu theo cấu hình thắng Gate B, không sửa helper WIP/legacy. Chạy `.venv/bin/python -m pytest -q tests/multilang`; review diff ownership; checkpoint commit.

### T4 — Sol-2 API/ASGI composition và giới hạn tài nguyên

**Files:** `src/secai_gateway/api/multilang/{routes,schemas}.py`, `src/secai_gateway/multilang_app.py`, `deploy/multilang/{README.md,env.example,compose.staging.yml}`, `tests/multilang/test_{api,compatibility,limits}.py`. Không sửa root dependencies đang thuộc agent file; dependency mới nếu cần nằm trong overlay đã pin/review.

**Interfaces:** `build_router(service, settings)->APIRouter`; `multilang_app.create_app()` trả FastAPI đã compose. New-only guards nhận request-scoped context; không thay middleware legacy.

- [ ] Test đỏ: master OFF có OpenAPI/legacy response/SSE như base; per-language OFF không call model; cả chiều đi/về bị chặn; schema strict chống mass assignment, URL/model/adapter injection.
- [ ] Test tất cả success/error/status/header trong contract; rate/global limits, CORS, body/token cap, no-store, không raw text/secrets/stack trace trong log. Đảm bảo envelope lỗi mới không bị generic middleware legacy thay trước guard; test burst thực tế.
- [ ] Test bộ code cũ không đổi và file v2 không bị prefix mới intercept. Identity tests theo policy đã freeze T1, không thêm auth vào API cũ.
- [ ] Implement router/composition; smoke qua httpx và server staging riêng. `.venv/bin/python -m pytest -q tests/multilang tests/test_translate_validation.py tests/test_translate_mask.py tests/test_translate_glossary.py`; checkpoint.

### T5 — Sol-4 FE riêng, chạy song song core khi contract đã freeze

**Files:** `secai-fe-devspace/src/screens/MultilangTranslatePanel.tsx`, `src/api/multilang.ts`, `src/styles/multilang.css`, tests FE; sửa tối thiểu `src/screens/TranslateScreen.tsx` để mount mode mới. `package.json`/lockfile chỉ khi cần test runner đã chọn, không thêm dependency UI tùy tiện.

**Interfaces:** consume `/ai/v3/translate/languages` + POST v3 contract; legacy tab vẫn dùng `translateStream` hiện có. Mode mới disabled/ẩn khi master OFF hoặc capability endpoint 404.

- [ ] Snapshot source FE canonical vào worktree từ manifest allowlist (src, public/assets cần thiết, config, package/lockfile). Diff đối chiếu bản đang ở workspace; loại `.env`, node_modules, dist/cache/log. Không stage deletion sibling hay chép file không rõ nguồn.
- [ ] Test component/mock: 8 ngôn ngữ mới và vi, đủ hướng đi/về; disabled/beta/stable; legacy mặc định y cũ; không ép en↔vi qua API mới; lỗi 400/403/413/429/502/503/504 và cancel hiển thị đúng.
- [ ] Text an toàn: `dir=rtl` cho output ar, code/CVE/IP isolated LTR; font fallback CJK/Cyrillic/Arabic, wrap/mobile/keyboard/accessibility. Render plain text; copy trả đúng chuỗi, không đảo byte theo hướng hiển thị.
- [ ] Implement UI với mock trước, nối API staging thật sau. `npm ci && npm run build`, chạy tests UI và T3 preview ở desktop/mobile. Checkpoint + ảnh/bằng chứng hướng RTL.

### T6 — Sol-3 conditional adapter (không tạo trước Gate C)

**Files:** `training/multilang/` configs/scripts/README; `docs/test-results/multilang/adapter-report.md`; model artifacts/checkpoints ngoài Git và ngoài instance thuê.

- [ ] Ghi điều kiện: chính LLM có weights/license trainable, engine+quant thực tế load adapter được, có dev GPU/budget riêng. Bất kỳ điều kiện thiếu → STOP, không tự đổi model.
- [ ] Test data pipeline trước train: license manifest, LID/length filters, dedup/hash/n-gram chống TEST leak, en↔vi replay chống quên; synthetic/back-translation chỉ cùng LLM.
- [ ] Chọn một adapter đa ngôn ngữ hay theo nhóm bằng DEV ablation; benchmark chỉ hướng chưa đạt và hồi quy 18 hướng. Ghi alias adapter→cùng base revision; raw text không đi qua model sinh khác.
- [ ] Train có cost ceiling/auto-stop, checkpoint upload/checksum ngoài instance; safetensors, image/model checksum, dữ liệu public/ẩn danh; report before/after, adapter size, VRAM/latency/cost.
- [ ] Hủy đúng instance do task thuê sau khi xác nhận artifact off-instance; không hủy instance shared/OCR. Nếu D fail: tắt flag và báo user.

### T7 — Sol review độc lập và acceptance nhiều lượt

**Files:** `docs/reviews/multilang/`, `docs/test-results/multilang/`; report `ID | criterion/test | evidence | severity | suggested fix`.

- [ ] Reviewer context mới đọc spec/criteria/diff/overlap; tự chạy test, audit API/model identity/flags/security. Một criterion fail → FAIL. Chuyển feedback nguyên văn qua điều phối, coder sửa theo ID, reviewer mới xác nhận tại SHA mới.
- [ ] Mỗi checkpoint/chu kỳ: `git fetch`; refresh ownership; `git merge-tree --write-tree NEW_SHA MAIN_SHA`, với FILE_SHA, với OCR_SHA; lưu SHA/exit/tree/conflict. Sau đó detached disposable worktree dựng cây ghép **main+new+file+OCR**, không merge WIP vào nhánh feature.
- [ ] Trên cây ghép: dependency union resolved riêng; kiểm import path; chạy legacy suite, multilang suite và `tests/test_translate_v2_*` của hai nhánh. Lưu JUnit/node IDs, baseline file-alone/OCR-alone/cây ghép; no new failures. Git sạch không thay thế test runtime.
- [ ] Tester context mới: 3 lượt ×30 mẫu×18 hướng, phân tầng đủ nhóm mục 8 Bước 4; config/model/prompts freeze trong 3 lượt, không tuning giữa lượt. Báo mọi failure, metrics, CI, chi phí và sample IDs từng lượt.
- [ ] **DEV vs TEST:** lặp fix/review/test trên DEV acceptance packs; TEST sealed chỉ mở khi candidate freeze để báo cuối. Nếu TEST fail, giữ báo cáo fail; sample đã xem thành regression, cần TEST holdout mới trước báo cáo final lần sau, không sửa theo rồi chấm lại cùng TEST thành thắng.
- [ ] Với n=30, 1 off-target là 3,33% nên fail; n=90 độc lập, 2 là 2,22% nên fail. Đảo lại cùng 30 mẫu không thành 90 độc lập. Báo CI; không dùng 0/30 để khẳng định tỉ lệ dân số≤2%. Astra quyết định thêm sample nếu cần độ tin cậy, trong ngân sách đã chốt.

### T8 — Vanguard độc lập, chỉ kịch bản/test/report

**Files:** `docs/security-tests/multilang-test-plan.md`, `docs/security-tests/multilang-report.md`; harness `tests/multilang_security/` khi cần. Không sửa feature code.

- [ ] Viết case ID/mục tiêu/cách làm/kỳ vọng/severity trước chạy. Kiểm staging thuộc dự án, dữ liệu tổng hợp.
- [ ] Chạy đủ 9 nhóm: multilingual prompt injection; Unicode/bidi/homoglyph/NFC; auth/permissions/flag bypass; OWASP/XSS/SSRF/injection/CORS/headers/mass assignment; rate/token/timeout/DoS/cost; privacy/log/egress/retention; supply-chain/license/checksum/safetensors/dependency scan; race/retry/order/idempotency/partial failure; legacy và file/OCR regression.
- [ ] N/A phải có scope/bằng chứng: text stateless không có job/history/role store, không tự gọi test IDOR là PASS. Không nhận input URL để fetch, nên kiểm provider endpoint không thể bị request thay vẫn bắt buộc.
- [ ] Gate: 0 Critical/High unresolved; Medium phải fix hoặc user nhận rủi ro bằng văn bản; Low ghi nhận. Feedback đi điều phối→coder→reviewer→vanguard; tối đa 3 returns, re-run toàn bộ nhóm liên quan và regression.

### T9 — Astra B mới review tích hợp, chuẩn bị merge

- [ ] Context mới, đọc criteria và nguyên report T7/T8; không dùng kết luận của coder thay evidence. Xác nhận gate mới merge các SHA vào `integration/multilang-translate`.
- [ ] Refresh main/file/OCR và chạy lại mô phỏng ghép/test như T7; không commit thay đổi vào nhánh task file/OCR. Cập nhật main vào nhánh mình khi cần; ai xong trước merge trước, bên sau đồng bộ và test lại.
- [ ] Chạy toàn bộ suite với baseline cùng env, smoke FE→backend thật, legacy en↔vi và cặp vi→zh, ja→vi, vi→ar có CVE/IP. Chưa giải quyết environment failures thì ghi chính xác, không báo toàn bộ xanh.
- [ ] Báo user một integration SHA có thể review, file shared đã chạm (kỳ vọng 0 ở backend cũ), conflict report, residual risks và đề xuất thứ tự merge. **Chỉ merge vào main sau user xác nhận**, đúng spec mục 8 Bước 5. Không tự deploy production.

### T10 — Sol tài liệu và bàn giao

**Files:** `docs/api/multilang-translate.md`, `docs/test-results/multilang/final-report.md`.

- [ ] API docs khớp runtime: languages/directions/status/RTL, endpoint/headers/limits/errors, auth policy, 401 nếu có auth (không bịa nếu anonymous), 403/413/429; curl/JSON thật vi→zh, ja→vi, vi→ar có CVE/IP; ghi nguồn private ingress và không raw HTML.
- [ ] Báo cáo branch/SHA/files/overlap exceptions, model+serving evidence, gates A/B/C/D, bảng18 hướng baseline/B/C/threshold/status, 3 rounds, costs, loop counts, vanguard findings, regression+merge simulation và links toàn bộ artifacts.
- [ ] Warden kiểm release flags, resource cleanup/cost ledger, checkpoint off-instance và các câu chưa có evidence. Final không tuyên bố ngôn ngữ đạt khi TEST chưa chạy.

## 5. Trạng thái bền, ngân sách và điều kiện dừng

STATE ghi phase, task/owner/branch/worktree/SHA, loop/review/vanguard counters, test manifests/config hashes, pass/fail/node IDs, language rollout status, simulated merge SHAs/results, GPU reservations, cost used/ceiling và next action. LOG append 5–10 dòng sau mỗi giai đoạn; feedback giữ nguyên theo ID, không tóm tắt mất thông tin. Mất context đọc STATE/LOG và frozen contract trước tiếp tục.

Warden chạy checkpoint ở mỗi transition và trước/sau GPU job: không tự sửa criteria hoặc feature code. Cùng lỗi tái phát2 lần → đổi cách tiếp cận; lỗi không giảm2 vòng → dừng task, chuyển việc độc lập; vượt5 fix/review hoặc3 vanguard returns → báo global stop. Cũng stop khi vượt budget, chưa đạt Gate D, Critical không sửa được, cần đổi API cũ/LLM, không hỗ trợ LoRA hay bắt buộc chạm file owner khác mà chưa phối hợp được.

**Nguồn lực chưa được cấp:** user “oke” xác nhận defaults nhưng chưa nêu budget USD, endpoint dev riêng/lịch shared hay reviewer bản ngữ. `GPU_BUDGET_USD=UNSET`, `DEV_ENDPOINT=UNSET`, `NATIVE_REVIEW=UNCONFIRMED`; không quy đổi “oke” thành ngân sách vô hạn. Không benchmark/training live trước khi có budget và resource gate. OCR đang dùng Vast theo STATE của task kia, không coi GPU đó là rảnh.

## 6. Checklist đối chiếu yêu cầu

| Spec gốc | Nơi đáp ứng |
|---|---|
| §1, §5, §9 điều phối/agent/feedback | Ownership + lịch4 slots, T1/T7/T8/T9/T10 |
| §2 đủ16 hướng,2 hồi quy,cùng LLM | Global constraints, registry/API, T2/T3/T6 |
| §3 giữ API,test,FE,flags,config | Impact map, T0/T3/T4/T5/T7 |
| §4 nhánh song song,overlap,merge,shared GPU | T0 + ownership + composition + T7/T9 + resource gate |
| §6 loop,state,checkpoint,cost,stops | §5 + T6/T7/T8 |
| §7 khảo sát và gates A/B/C/D | T1/T2/T6 + sealed TEST policy |
| §8 thứ tự criteria→assess→code→review→test→vanguard→integration→docs | §3 DAG + T0–T10 |
| §8 test30×18×≥3,security9 groups,prod gates | T7/T8/T9 |
| §9 báo cáo đầy đủ | T10 |

Plan không bao gồm sửa lỗi legacy vừa nghi ngờ trong lượt audit trước, đổi contract file/OCR, thêm model dịch khác, mở beta tự động hoặc triển khai production. Các điều kiện cần dữ liệu baseline/nguồn lực được ghi thành gate; không thay bằng dự đoán chất lượng.
