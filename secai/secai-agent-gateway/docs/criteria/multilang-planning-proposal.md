> **Cập nhật mới nhất:** quality target khoảng70%, quy ước pass rate≥70% mỗi hướng; model runtime điều phối và dịch vẫn là DuyTa đang triển khai trên Vast. HF chỉ tham khảo/đối chiếu checkpoint. User đã chốt tối đa3 model hỗ trợ OCR/LID/QE tổng cộng, thử tuần tự; không cho phép thay model dịch. OCR giữ ownership task file/OCR, QE/judge offline, mọi model hỗ trợ không sinh/hậu chỉnh bản dịch. Xem [plan](../plans/multilang-translate.md). Entity/API/security gates giữ nguyên; draft chưa frozen.

# Đề xuất tiêu chí multilang — astra A, chỉ lập kế hoạch

Ngày: 2026-10-03. Trạng thái: PROPOSED; chưa nghiệm thu implementation hay chất lượng LLM. Tài liệu này để root quyết định thiết kế rồi phát hành bộ tiêu chí chính thức. Không chạy live benchmark, sửa mã, commit, merge hoặc mở agent trong lượt này.

## 1. Căn cứ và ranh giới

- Yêu cầu chính: attachment `c7170acf-e342-4a58-b7fc-49e67afada04-f746528f-5b65-4f96-950e-683fc271639a-txt.txt`, mục 1–9.
- Đã đọc `docs/api/translate-v2-contract.md` và `docs/loop/STATE.md` để bảo vệ công việc file/OCR. Không sửa hai file này.
- Working tree gốc thuộc monorepo `/home/ict/AIEngines`, branch `work`, HEAD `d00c82e`; có thay đổi/untracked của người khác. Không phải base triển khai được tự động chọn.
- Có HAI nhánh WIP cần bảo vệ: `feature/import-file-translate` và `feature/image-ocr-translate`, không chỉ một nhánh dịch. HEAD quan sát 4505c57/404f93a có thể tiếp tục đổi. Khi execution phải refresh refs, khóa SHA và lấy merge-base/diff mới.
- Existing 63 translation unit tests PASS do root cung cấp; astra A chưa chạy lại. Không suy ra toàn bộ suite PASS. STATE của task file/OCR ghi baseline riêng 925 tests, 905 pass/19 fail/1 skip; phải giữ provenance/base/harness riêng, không trộn thành baseline multilang.
- Dùng `docs/loop/multilang/STATE.md` và `docs/loop/multilang/LOG.md` cho task mới; `docs/loop/STATE.md` và `LOG.md` hiện thuộc file/OCR.
- Phạm vi mới: vi↔zh/ja/ko/fr/de/es/ru/ar (16 hướng); en↔vi (2 hướng hồi quy). Ngoại ngữ↔ngoại ngữ không bắt buộc. zh mặc định giản thể; ar mặc định MSA. Không cần hỏi lại các mặc định này nếu user không đổi.
- Chỉ incumbent LLM trên Vast sinh bản dịch. LoRA gắn chính model đó được xét có điều kiện; language-ID/QE/validator chỉ đánh giá hoặc chặn, không sinh hay hậu chỉnh bản dịch. Offline COMET/xCOMET/judge được tách khỏi production.

## 2. Các tài liệu tiêu chí nên phát hành sau quyết định của root

1. `docs/criteria/multilang-assessment.md`: model inventory, dữ liệu, baseline, metrics và protocol.
2. `docs/criteria/multilang-core.md`: registry, prompt/glossary/chunk/validator, API và flags.
3. `docs/criteria/multilang-fe.md`: UI text translation, RTL/font/accessibility, contract.
4. `docs/criteria/multilang-adapter.md`: điều kiện C và bằng chứng adapter; không tạo worktree/agent adapter trước C.
5. `docs/criteria/multilang-integration-security.md`: regressions, overlap, staging, vanguard và release gates.

Mỗi hàng tiêu chí có ID, tập test, điều kiện đạt, bằng chứng/SHA và trạng thái PASS/FAIL/BLOCKED/NOT RUN/N/A có lý do. Thiếu chứng cứ là BLOCKED/NOT RUN, không phải PASS. N/A chỉ khi tiêu chí thật sự không áp dụng, không dùng để bỏ yêu cầu chưa thực hiện.

## 3. Ma trận yêu cầu → tiêu chí → bằng chứng

| Yêu cầu gốc | ID/owner đề xuất | Điều kiện đạt / bằng chứng cần có |
|---|---|---|
| §2.1 16 hướng + 2 hồi quy | AS-01/core CORE-01 | Manifest đủ 18 hướng; kết quả từng hướng, không gộp macro-average để che fail |
| §2.2 chỉ incumbent sinh dịch | AS-02/CORE-02/AD-01 | Pin model revision, tokenizer, serving identity; trace provider/adapter cho mọi request; không alternative translator/fallback |
| §3.1 API cũ bất biến | CORE-03/INT-01 | Contract snapshots + behavior tests legacy với flags OFF/ON; cả JSON/SSE nếu endpoint hiện có hỗ trợ |
| §3.2 và §4 overlap/additive | INT-02 | Base SHA, merge-base và file diff với cả import/OCR; một hook nhỏ tối đa; không sửa shared chunking/chữ ký/file schemas/jobs |
| §3.3 baseline tests | INT-03 | Test cũ trước/sau trên cùng harness, exact node IDs; inherited failures phân biệt regressions |
| §3.4 FE | FE-01..05 | Canonical FE repo/path/base được pin; build + browser evidence đủ 18 hướng, RTL/scripts, real API smoke |
| §3.5 secrets/config | SEC-06/CORE-04 | Không secret/local hardcode; config validation; log canary scan; runtime fetch policy giữ nguyên |
| §3.6 flags theo ngôn ngữ | CORE-05/INT-04 | 8 flags server-side default OFF, request không vượt flag; rollback replay; en↔vi không phụ thuộc flags mới |
| §4.3–7 isolation/resources | INT-02/AS-03/AD-04 | Branch/worktree ownership, merge simulations cả 2 WIP + base; GPU queue/budget/preflight; không ảnh hưởng service |
| §6 loop/checkpoints | OPS-01 | STATE/LOG namespace riêng; counters/cost ledger/SHAs; structured feedback không mất dữ liệu |
| §7.1 inventory | AS-02 | Model/config/runtime facts đối chiếu; unknown ghi unknown; official model/serving references chỉ tham khảo |
| §7.2 metrics/data | AS-04..08 | DEV/TEST manifests, signatures/licensing, per-direction score/CI/errors/entities/latency/cost |
| §7.3 B optimization | CORE-06 | Ablations trên DEV, so paired baseline, giữ cải thiện thật; retry bounded; không tune TEST |
| §7.4 conditional adapter | AD-01..05 | Weight/license/LoRA support verified trước C; train/dev/test dedup; regression; checkpoint/cost/VRAM |
| §7.5 D decision | REL-01 | Từng hướng đạt/chưa đạt; flags giữ OFF khi thiếu gate; fail sau C/D trình user |
| §8.1 language specifics | CORE-07/FE-02 | CJK punctuation, Cyrillic, NFC prose, MSA/zh-Hans/register; literals không bị normalize phá |
| §8.3 independent review | INT-05 | Reviewer khác coder, criterion-by-criterion evidence; một tiêu chí fail thì review fail |
| §8.4 30×18×3 | AS-09 | Ít nhất 1.620 scheduled requests; retries/error không bị loại; per-round/per-direction báo cáo |
| §8.4.5 security 9 groups | SEC-01..09 | Vanguard độc lập, plan trước test, reproduction evidence, không còn Critical/High; Medium sửa hoặc user chấp nhận bằng văn bản |
| §8.5 integration | INT-06 | Astra B độc lập; exact composition SHAs; complete suite + imported branch tests + FE real smoke; user xác nhận main merge |
| §8.6 handoff docs | DOC-01 | Contract/examples thực chạy, vi→zh/ja→vi/vi→ar có CVE/IP; auth/error semantics chính xác |

## 4. ASSESS và protocol đo

AS-01: Mỗi hướng có row độc lập: model revision, prompt/config hash, dataset/version/split/hash, sample count, distinct source count, successes/errors, chrF++, BLEU signature, offline QE/reference score và coverage, off-target/script, copy/repeat/hallucination/truncation/preamble, entity rate, p50/p95, throughput và cost/1.000 ký tự. Giữ breakdown general/domain và các strata; không bỏ mẫu lỗi để cải thiện chất lượng tổng.

AS-02: Inventory tên/base-instruct/params/quant/context/output limit/tokenizer revision; stack/version/GPU/endpoint protocol/chat template/decoding; open-weight/license/tuning/LoRA ability. Giá trị config không chứng minh runtime serving giống config: cần metadata và request evidence tại execution. Không ghi API key hoặc endpoint chứa credential. Tham khảo công bố từ primary sources sau khi biết chính xác model; tuyệt đối không lấy điểm của model khác làm kết quả model hiện tại.

AS-03: Resource plan có instance dev riêng ưu tiên, concurrency/rate/cost caps, thời hạn, cost ledger, abort switch, incumbent service before/after evidence. Snapshot file/OCR ghi A10080GB chỉ còn khoảng 10GB tại một thời điểm và OCR đã có reservation riêng; không coi là tài nguyên multilang sẵn dùng. Dùng chung chỉ khi lịch và quota không ảnh hưởng file/OCR được điều phối; không tự tải model/train/restart/reconfigure incumbent. Chưa có ngân sách hoặc reservation: live ASSESS BLOCKED, vẫn làm inventory/static harness được sau criteria.

AS-04: DEV và TEST tách từ trước bằng dataset/source-document/group; cùng câu đa ngôn ngữ, đảo chiều và near-duplicate cùng nhóm để không rò qua split. TEST manifest/hash niêm phong bởi tester giữ độc lập; coders chỉ nhận DEV. Train phải decontaminate với cả evaluation corpus và FLORES devtest, không chỉ trùng exact hash. Public corpus version/license lưu trong manifest; domain anonymized/synthetic không chứa thông tin khách hàng thật. Reference test phải có nguồn tin cậy hoặc người đánh giá song ngữ; không dùng bản dịch do chính candidate tạo làm gold.

AS-05: Baseline A và vòng B đo DEV với prompt incumbent, decoding cố định đủ để tái lập. Sau baseline, astra A đề xuất threshold riêng từng hướng cùng biên hồi quy/hiệu năng để user xác nhận một lần, rồi đóng băng trước candidate selection cuối. TEST chỉ chạy báo cáo cuối: evaluator chạy baseline frozen và candidate frozen trên cùng TEST, giấu output/score khỏi coder đến khi candidate khóa. Cách này có paired TEST baseline mà không dùng TEST để thiết kế threshold.

AS-06: Metric tool/model/language coverage xác minh trước sử dụng. chrF++ là chính; BLEU tokenizer cấu hình thực tế phù hợp từng hướng và lưu signature, không ngầm coi mọi ngôn ngữ cùng tokenizer. COMET/xCOMET offline pin weights/license/support coverage; unsupported language ghi giới hạn và bổ sung thẩm định phù hợp, không bịa điểm tương đương. Language-ID/Unicode script không tự phân biệt chắc zh-Hans/zh-Hant, MSA/phương ngữ hoặc nguồn một từ; short/mixed ambiguous đưa vào adjudication theo rubric khóa trước. Không coi toàn bộ Arabic script là MSA, không coi chữ Han là tự động tiếng Trung thay vì tiếng Nhật.

AS-07: Entity 100% là hard gate trên corpus technical có CVE/IP và các protected annotations: CVE/CWE, IP/CIDR, URL/hash, số liệu/ngày, code/commands/path/version/proper-name khi yêu cầu giữ nguyên. So exact protected occurrences (không chỉ set), không missing/extra/changed/duplicated; integrity chunk restore theo position constraints đã định. Prose reorder tự nhiên không đồng nghĩa đổi thứ tự entity là sai nếu không có constraint ngữ nghĩa; corpus phải annotate rule này rõ. Nếu user yêu cầu nguyên văn ngày/số/tên thì không localization/transliteration chúng. Chỉ normalize NFC phần prose; protected spans giữ nguyên. Report both per-entity và per-sample, bất kỳ corrupt entity làm gate fail, không được che bằng rounding.

AS-08: Hard off-target ≤2% trên từng hướng; script errors báo riêng, không nhập ngầm cùng metric. Numeric chrF/BLEU/COMET, script-error tolerance, nontranslation/hallucination/truncation/preamble tolerance, quality regression margin, latency/throughput/memory/token inflation/cost thresholds: **TBD sau baseline, astra A đề xuất + user xác nhận một lần**. Invariant API/entity/flag/isolation không đợi baseline. Nếu chưa chốt threshold thì chỉ được báo đo đạc, chưa được đánh dấu quality PASS. CI/confidence method và multiple-directions policy cũng phải freeze trong cùng threshold package.

AS-09: Test ổn định tối thiểu 30 mẫu/hướng ×18 hướng ×3 lượt = **1.620 scheduled requests**, chưa tính retries, baseline paired runs, security hoặc extra statistical set. Mỗi lượt đủ strata: ngắn/dài, technical, CVE/IP/URL/code/số/ngày, list/table, formal/informal, mixed scripts, một từ và edge cases. Edge inputs rỗng/invalid đo expected validation result, không trộn vào denominator chất lượng translation; bổ sung valid examples để đủ kích thước denominator đã chốt. Báo min valid n thực tế. Shuffle cùng 30 mẫu không tạo thêm 90 nguồn độc lập; ghi unique sources và run seeds.

### Giới hạn thống kê bắt buộc nêu rõ

- 0 lỗi/30 mẫu độc lập cho upper bound nhị thức một phía 95% xấp xỉ `1 - 0.05^(1/30) = 9,50%`, không chứng minh ≤2%.
- 0 lỗi/90 mẫu độc lập cho upper bound khoảng 3,27%; nếu lặp cùng 30 source thì càng không được dùng n=90 như 90 source độc lập.
- Nếu chọn confidence một phía 95% theo từng hướng, ít nhất 149 source độc lập với 0 lỗi mới cho bound ≤2%. Có lỗi cần n lớn hơn; kiểm định đồng thời 18 hướng cần correction hoặc policy khác được chốt. Con số 149 là hệ quả của phương pháp minh họa, chưa tự động nâng thành ngân sách bắt buộc.
- Vì vậy tách “observed off-target rate ≤2% trong bộ đo” và “statistical confidence đáp ứng ≤2%”. Không dùng 30×3 để tuyên bố điều thứ hai. Root cần chốt với user mức bảo đảm và ngân sách sample expansion trong một lần threshold approval sau A.
- Paired bootstrap/CI phải resample theo source/document group, không coi retries/round repeats là independent. en↔vi so candidate với baseline cùng sample/harness và margin đã chốt; không suy từ p-value không có ý nghĩa thành bằng chứng tương đương.

AS-10: Vòng FIX/TEST dùng acceptance DEV/regression bank; sealed TEST chỉ cuối. Nếu TEST fail, status fail và dừng release; nếu user cho sửa, TEST đó bị retire thành known diagnostic, bộ TEST độc lập mới niêm phong trước vòng báo cáo mới. Không dùng failing sealed outputs để tune rồi rerun cùng bộ và gọi là unseen final. Loop counter vẫn tăng, không reset vì đổi dataset.

AS-11: Optional tối đa 20 câu/ngôn ngữ cho native review; rubric adequacy/fluency/register/script/technical meaning, include cả chiều vào/ra Vietnamese và sampling unbiased. Native check optional không đồng nghĩa tự động PASS khi thiếu reference support hoặc detector ambiguous.

## 5. CORE và FE

CORE-01: Registry một nguồn chuẩn, metadata code/display/script/dir/variant/allowed pairs; vi ở một đầu. Mã zh/zh-Hans alias/canonicalization phải chốt trong API mới, không thay enum file/OCR `auto|en|vi`, `en|vi`. Không tự bổ sung auto-detect vào legacy. Nếu API mới hỗ trợ auto, ambiguity và mismatch behavior phải có contract rõ.

CORE-02: Provider interface giữ nguyên; một adapter compatibility layer, prompt/glossary/chunk mới ở module riêng. Root đề xuất module `multilang/`, `api/multilang/` và ASGI composition `multilang_app.py` gọi `create_app` cũ rồi register router mới, tránh sửa `app.py`, `api/middleware.py`, `agent/runner.py`, `prompts/profiles.yaml`, `pyproject.toml`, `translation/`, `api/translate_v2/` mà file/OCR sở hữu. Chỉ tối đa một extension hook nhỏ vào legacy translation flow nếu thật cần; composition có thể không cần hook nào. Đăng ký router mới không lý do sửa lan shared clients/hooks/global sanitizer. JSON/structured output chỉ nếu incumbent stack hỗ trợ; fail không fallback model khác. No-tool translation verified tại provider payload/registry; không chỉ assert prompt có “không dùng tool”. Composition vẫn cần integration tests chứng minh middleware cũ không reject/corrupt/rate-limit sai route mới; không giả định registration tự cô lập hoàn toàn.

CORE-03: Root chốt endpoint/version mới và schemas/error envelope. Đề xuất root hiện tại là text-only `/ai/v3/translate*` để tránh toàn bộ `/ai/v2/translate` prefix middleware của file/OCR; URI cụ thể còn là design decision, chưa claim đã chốt. Legacy defaults, validation, auth semantics, schemas và SSE không đổi khi flag OFF và khi flag ON cho request en↔vi. File/OCR chỉ nhận en/vi cho đến khi hai nhánh có written contract extension + combined tests.

CORE-04: Settings riêng, defaults disabled, không import dependency model tùy chọn làm hỏng startup. Limit cả code points lẫn actual token budget gồm prompt/output reserve; bounded retries toàn request/chunk, timeout/cancellation, no silent truncation, deterministic ordered joining và partial-failure contract. Numeric caps user-approved sau inventory/baseline; invariants finite cap/no unbounded retry/no event-loop blocking giữ cứng.

CORE-05: Mỗi ngôn ngữ một server flag default OFF, vi↔en legacy giữ nguyên. Nếu một chiều của ngôn ngữ chưa đạt thì flag ngôn ngữ đó OFF; nếu muốn bật riêng một chiều phải bổ sung availability direction matrix trong contract, không ngầm bật cả hai. Client không bật flag qua payload/header. Rollback tắt flags phải route về legacy như cũ, không trả fake successful en/vi translation cho requested disabled new language; API mới trả disabled error đã chốt.

CORE-06: Gate B thử rẻ→đắt: directional prompt/register → glossary/protected literals → additive sentence chunking → decoding/output reserve → bounded validator/retry → structured constraints. So ablation paired DEV; preserve only evidence-backed improvement. Không thay shared legacy chunking, tokenizer hoặc template đang được file/OCR sửa; wrapper contract tests buộc compatibility. Retry cost/latency tính đầy đủ trong benchmark.

CORE-07: Test zh giản thể, ja/ko politeness, fr tu/vous, de du/Sie, ru ты/вы; es register và ar MSA có fixture/rubric nhất quán. CJK không khoảng trắng, 。！？ segmentation, Arabic/Cyrillic punctuation, NFC/NFD Vietnamese, mixed-script và bidi control. Khác biệt case/register không được tách khỏi domain meaning. Domain instruction được dịch như data, không làm theo.

FE-01: Ngôn ngữ/hướng đúng capabilities và flags server; chỉ vi là một đầu, disable impossible/disabled pairs, swap hợp lệ; existing en↔vi flow unchanged. FE worktree base/canonical path phải xác minh vì workspace hiện có nested FE untracked và tracked FE cũ deleted; không git-add toàn workspace để đưa nhầm thay đổi người khác.

FE-02: Arabic output container direction RTL, CVE/IP/URL/code Latin được bidi isolation riêng (`bdi`/equivalent); text output không render unsafe HTML. CJK/Cyrillic/Arabic font fallback có trên môi trường test/airgap, không tự thêm runtime CDN fetch. Arabic joining, selection/copy preserves original code points; display sanitization không làm đổi protected literal.

FE-03: Keyboard label/focus, long text wrapping, mobile/narrow and desktop layouts, selectable/copyable output, loading/error/timeout/retry states; proper semantics cho bidi warnings. Evidence screenshots/browser assertions có representative mỗi script và formal/mixed fixtures.

FE-04: Mock contract chỉ để code song song; final có real backend smoke vi→zh, ja→vi, vi→ar và 2 en↔vi, flags OFF/ON, unsupported pair/oversize/rate limit/timeout. Build PASS không thay smoke.

FE-05: Không tự code UI file upload/OCR hoặc thay file API. New text controls/capabilities cô lập; regression với translation screen cũ và integration branch file/OCR nếu UI tích hợp tồn tại.

## 6. Gates A/B/C/D

| Gate | Entry | Exit/evidence | Khi chưa đạt |
|---|---|---|---|
| A: inventory + baseline | Criteria protocol chốt; resources/budget/DEV ready; existing tests recorded | 18 hướng DEV complete; model/data/metrics identity; astra đề xuất threshold package, user xác nhận một lần trước quyết định B/C | Missing resource/identity = BLOCKED; baseline thấp là kết quả assessment, không đổi model hoặc tự giảm chuẩn |
| B: no-training improvement | Thresholds frozen; A evidence | Per-direction paired DEV score/error/cost + ablations đạt mọi hard/approved gate; en↔vi within margin | Chỉ các hướng còn yếu đủ điều kiện xem C; tránh train hướng đã đạt |
| C: same-model adapter conditional | B fail + open weights/license/tuning rights + supported LoRA stack + authorized dev compute/budget | Frozen adapter/data checksum, leakage audit, before/after metrics và regression/VRAM/latency/cost đạt | Không đủ điều kiện C: dừng báo user; không chọn translator khác; không tạo adapter branch trước đủ entry |
| D: honest final disposition | B hoặc C results, independent review/3-round tests/sealed final, security/integration evidence | Matrix direction/lang pass/fail/off; chỉ eligible language được đề xuất bật; main merge user quyết định | B/C/D fail không phát hành beta ngầm; flag OFF và trình user quyết định |

Gate quality B/C trước CODE được hiểu là experiments/harness trên DEV sau criteria; product implementation ở bước CODE sau lựa chọn giải pháp. Không yêu cầu hoàn tất implementation production trước khi biết có cần adapter. D provisional từ DEV không đồng nghĩa release approval; D final đợi sealed TEST + security/integration.

AD-01: Base model/revision của adapter chính incumbent; không sửa global served base hoặc merge adapter vào incumbent phục vụ en/vi để thử. Per-request routing adapter chỉ khi flag enabled và direction eligible; fallback về base không che failure chất lượng.

AD-02: Training provenance/license/dedup/filter checks; synthetic/backtranslation chỉ incumbent, public/anonymized data; sealed TEST/FLORES devtest excluded cả exact và near duplicate. Test leakage phát hiện sau train làm evidence invalid; retrain clean, không chỉ xóa báo cáo.

AD-03: Chống quên bằng en↔vi/general train mix; en↔vi paired regression giữ gate cũ; cùng prompt/serving budgets khi compare, mọi config khác ghi rõ. Adapter group/multilang choice từ measured DEV, không đoán.

AD-04: Isolated dev Vast, budget capped, checkpoint ngoài ephemeral host, safe format safetensors/checksum, least secret, stop/cleanup owned instance only. Không terminate shared incumbent/OCR instance. Đo peak/steady VRAM và incumbent availability/latency nếu tài nguyên liên quan.

AD-05: Tắt flag/adapter khôi phục observed baseline path/config; cold/hot adapter switch race tests, unsupported adapter error bounded, không cross-request wrong language hoặc adapter state.

## 7. Integration/security gates và identity gap

INT-01..06: Evidence gắn exact SHA/config/data/model. Mỗi cycle refresh base + cả import/OCR refs; overlap map file ownership; merge-tree phát hiện conflict, nhưng clean textual merge không đủ. Trong staging worktree tạm compose SHA của multilang và từng WIP/cả hai khi dependency yêu cầu, chạy imported test sets; không merge WIP vào feature branch hay checkout/commit/rebase WIP. Thay SHA làm invalid affected evidence và phải replay. Hợp nhất bởi astra B mới sau independent review + tester + vanguard; user xác nhận trước main merge. Baseline inherited fail phải trình exact comparison; không gọi complete suite PASS khi còn fail.

SEC-01: Multilingual injection zh/ja/ko/ru/ar/vi, Unicode-hidden và mixed script; verify provider tools disabled, no internal prompt/glossary leak, entities intact và no unintended instruction/action.

SEC-02: Bidi override/isolate, zero-width, homoglyph, NFC/NFD, malformed/tokenizer-stressing data; FE/log safe presentation, no accidental CVE/IP corruption. Acceptance dựa role của span, không strip mù mọi zero-width cần cho ngôn ngữ.

SEC-03: Auth/authorization matrix phải phản ánh contract thật. File/OCR contract frozen là anonymous capability ID: không token/cookie/user_id và không owner/tenant check; multilang không được retrofit 401/403 hay claim BOLA isolation trên endpoint đó. Root đã ghi legacy text API anonymous. User multilang lại yêu cầu invalid/expired token/IDOR/roles; đây là **decision gap**, không thể PASS cả hai bằng suy đoán. Đề xuất: giữ APIs cũ; API text mới stateless anonymous theo convention nếu user xác nhận scope, khi đó token/role owner tests N/A có giải thích và capability/session routes vẫn kiểm tra hợp đồng tương ứng. Nếu user cần authenticated API mới: phải có identity provider/trust boundary/ownership design trước implementation, chỉ áp dụng route mới; không tự dựng fake user_id header auth. Flag tamper, namespace separation, rate/CORS/data isolation luôn applicable.

SEC-04: SQL/command/template injection, SSRF nếu URL processing exists (không dereference URL chỉ để dịch), safe text rendering/XSS, mass assignment, CORS/headers, safe errors/no stack trace. Scope chỉ staging của dự án.

SEC-05: Character+token limits, chunks/retries/deadline/concurrency budgets, rate limit, cancellation, large/mixed inputs; verify budgets bound requests thật, no hidden retry multiplication. Numeric runtime/perf limits freeze qua package A, không nhập ngưỡng file/OCR thành ngưỡng mới.

SEC-06: Request/response/model/tool/access/error logs canary scan, secrets redaction, không persistence raw text/prompt/glossary vào session/history ngoài contract; external-host data purpose and destinations documented; datasets public/synthetic/anonymized; owned staging data cleanup.

SEC-07: Model/adapter/package checksum/license/safe format, appropriate dependency scans; findings severity + exploitability/remediation, không nói sạch nếu scanner chưa chạy. Existing environment findings không tự miễn nếu feature làm reachable.

SEC-08: Concurrent languages/adapters, nth chunk failure, ordered merge, format/protected token integrity, retry/idempotency behavior đúng contract, same-language/auto edge handling, flag flip during request with explicit snapshot semantics; rollback replay.

SEC-09: Legacy APIs/en↔vi, file/OCR composed tests và missing optional dependency startup; denied new language cannot bypass via auto/alias/case/header. Vanguard có plan trước run và report đầy đủ reproduction; Critical/High unresolved chặn production; Medium cần fix hoặc user written acceptance; Low recorded.

OPS-01: Max 5 fix/review cycles/task, max 3 returns từ vanguard. Same defect twice sau claimed fix → thay approach; defect count không giảm sau 2 cycles → pause task, chuyển independent work; global stop khi budget/loop/C-D language fail/unfixable Critical/legacy API change/forbidden overlap không tránh được. Structured feedback `ID | criterion/test | evidence | severity | fix suggestion`, chuyển nguyên văn qua root. Không tự reset counter hoặc raise budget.

## 8. Quyết định còn thiếu để root gom một lần

**Trước live ASSESS:** exact incumbent/runtime identity; Vast dev instance hoặc shared schedule/quota, rate cap, maximum spend + time cap, accounting method; capacity không mượn OCR reservation. Có thể inventory và thiết kế harness sau protocol freeze trong khi chờ.

**Trước code:** base chung SHA sau fetch, protected SHAs cả import và OCR; canonical FE Git ownership/base; URI/schema/BCP47 alias/disabled-language errors; new route identity choice như SEC-03. Chốt explicit-language-only là lựa chọn ít rủi ro nếu user không yêu cầu auto cho API mới, nhưng root phải quyết định contract.

**Sau baseline, user xác nhận một lần:** per-direction numeric quality/perf/cost thresholds và en↔vi margin; sample size/confidence/multiplicity; treatment ambiguous language-ID/metric coverage; optional native review; frozen final TEST protocol. Không hỏi user lặp lại những mặc định đã có (zh-Hans, MSA).

**Release:** Nếu per-language flag chỉ bật cả hai chiều, một chiều fail kéo cả ngôn ngữ OFF. Beta chỉ sau quyết định rõ của user; không dùng beta để vượt hard gate. Main merge và production enablement là quyết định riêng, không suy từ unit test PASS.

Không có ngưỡng numeric tự bịa hoặc tuyên bố feature PASS trong đề xuất này. Bằng chứng hiện có là tài liệu/working-tree observations và kết quả 63 unit tests được root cung cấp; model benchmark, 1.620-request stability run, sealed TEST, security và integration đều NOT RUN.
