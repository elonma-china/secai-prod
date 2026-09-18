# KPI cần điều chỉnh — kiểm chứng độc lập secAI (R-EVAL-INDEP)

Ngày chạy: 2026-08-26/27. Bộ đề: `KPI-VA-GEN-TEST/02-GEN-TEST/testset-v2` — 1000 câu tự sinh từ Qdrant sống
(850 CVE thật loại trừ trùng với `datasets-acceptance` gốc, overlap=0; seed=7; 43 template;
bucket định danh 400 / khắc phục 400 / tình huống 170 / ngoại lệ 30; capability cve_qa 590,
translation 60, poc_search 60, sast 60, remediation 60, pentest 59, exploit 51, report 60).
So sánh dùng `01-KPI/compare_kpi.py` đối chiếu **bản công bố đã ký**
(`kpi_results/quality/raw-logs`, 11/11 ĐẠT) theo ngưỡng sai lệch ±5% của hợp đồng.

**Kết quả tổng: 12/19 KPI đạt theo ngưỡng hợp đồng thật** (đếm lại bằng tay cho 4 mã Nhóm B có
lỗi chấm điểm trong script, không lấy trường `pass` gốc — xem mục Nhóm B). 5 KPI cần điều chỉnh:
`R-KPI-FAITH98`, `R-PERF-CCU`, `R-PERF-RPS`, `R-PERF-TPS`, `R-PERF-TTFT` — cùng 3 KPI Nhóm A
vượt ngưỡng sai lệch ±5% so với bản công bố (`R-LLM-EXPLOIT85`, `R-LLM-REMED60`, `R-KPI-RADAR90`,
vẫn PASS ngưỡng hợp đồng nhưng lệch baseline > 5%, thuộc diện phải fine-tune/chạy lại theo hợp đồng
so sánh — liệt kê riêng ở cuối).

**Ghi chú R-CHATFLOW-TPS25 (mã thứ 20):** KHÔNG tính trong báo cáo này. Theo BANG-KPI.md cạm bẫy #8,
mã này chỉ tồn tại như chuỗi cứng `"pass": True` trong một tool đã mồ côi
(`kpi_results/perf/tools/build_full_run.py`), không có script sống nào sinh ra được — hệ thống
thực sự có 19 KPI, không phải 20.

---

## Nhóm A — 11 KPI chất lượng AI (10/11 PASS)

| Mã | Đo thực tế | Ngưỡng | PASS? | Δ% vs baseline |
|---|---:|---|:---:|---:|
| R-KPI-EMB96 | 100% | > 96% | ✅ | -1.24 |
| R-KPI-RERANK90 | 99.41% | > 90% | ✅ | -1.2 |
| **R-KPI-FAITH98** | **96.71%** | **> 98%** | **❌** | +1.8 |
| R-KPI-RELEV90 | 91.12% | > 90% | ✅ (sát ngưỡng) | +1.89 |
| R-KPI-ENTITY100 | 100% | = 100% | ✅ | 0 |
| R-KPI-HALLU2 | 0% | < 2% | ✅ | -100 (tốt hơn) |
| R-LLM-SAST85 | 85.0% | ≥ 85% | ✅ (zero margin) | 0 |
| R-LLM-EXPLOIT85 | 94.12% | ≥ 85% | ✅ | +5.88 ⚠ vượt ±5% |
| R-LLM-REMED60 | 83.33% | ≥ 60% | ✅ | +16.67 ⚠ vượt ±5% |
| R-SEC-PII | 98.11% | > 90% | ✅ | 0 |
| R-KPI-RADAR90 | 97.9% | > 90% | ✅ | +5.77 ⚠ vượt ±5% |

### R-KPI-FAITH98 — FAIL, cần điều chỉnh NGƯỠNG HỢP ĐỒNG (không phải bug hệ thống)

Đo được 96.71% (n=754), ngưỡng >98%. **Nhưng số 96.71% này tự-chấm-điểm**: `.env.bench` trỏ
`RAGAS_JUDGE_BASE_URL/MODEL` về đúng model đang được đo (`DuyTa/Cyber-F1-AWQ`) vì môi trường này
không có key giám khảo độc lập — model tự chấm bài của chính nó nên số có xu hướng bị thổi cao.

Tài liệu nội bộ đã đo **độc lập bằng DeepSeek** (giám khảo khác hệ, gold-contexts, chuẩn RAGAS,
n=822 câu — lớn hơn mẫu ở đây): kết quả **88.8%**
(`/home/ict/secai-88/source/eval/nghiem-thu-suite/README.md:19,21`), và tài liệu này tự kết luận
"98% bất khả thi cho RAG có-phân-tích", đề xuất hạ ngưỡng có căn cứ về **~85%** (quyết định nghiệm
thu, không tự đổi ngưỡng). Số 96.71% đo ở đây cao hơn hẳn con số DeepSeek độc lập 88.8% — chênh
lệch ~8 điểm phần trăm đúng theo hướng dự đoán của thiên lệch tự-chấm (giám khảo cùng họ với hệ
thống được chấm luôn khoan dung hơn).

**Nguyên nhân gốc + con trỏ:**
- Ngưỡng 98% tự nó đã được tài liệu nội bộ xác nhận không khả thi với kiến trúc RAG-có-phân-tích
  hiện tại (`nghiem-thu-suite/README.md:21`).
- `faith_judge.py` hard-require biến `OPENAI_API_KEY` (KeyError nếu thiếu) thay vì fallback qua
  `config.py`'s `RAGAS_JUDGE_API_KEY` như các script khác — không phải nguyên nhân của số liệu thấp,
  nhưng là lý do môi trường buộc phải tự-chấm (không set được key OpenAI/DeepSeek thật cho bước này).
- **Hướng xử lý:** không fix bằng cách "tune lại model" — số liệu độc lập (DeepSeek, 88.8%) đã cho
  thấy đây là giới hạn kiến trúc, không phải lỗi cấu hình. Cần renegotiate ngưỡng hợp đồng xuống
  ~85% (theo đúng đề xuất đã có sẵn trong tài liệu nghiệm thu nội bộ), HOẶC cấp key giám khảo độc
  lập thật cho lần đo tiếp theo để loại bỏ nghi vấn tự-chấm trước khi bàn ngưỡng.

### R-KPI-RELEV90 — PASS nhưng cùng thiên lệch tự-chấm

91.12% > 90%, đạt nhưng biên rất mỏng (1.12 điểm). `llm_kpi.py:208` dùng chính model đang đo làm
giám khảo (giống FAITH98) — BANG-KPI.md cạm bẫy #5 đã ghi nhận việc này. Hợp đồng gốc ghi phương
pháp "Cosine Similarity" (đường code cũ vẫn còn ở `llm_kpi.py:186-194` nhưng không được gọi). Vì
biên PASS quá mỏng và cùng cơ chế thiên lệch với FAITH98, số 91.12% nên được đọc như "PASS nhưng
không chắc sẽ giữ được nếu đổi giám khảo độc lập" — khuyến nghị đo lại với giám khảo khác họ mô
hình trước khi coi đây là số ổn định.

### R-LLM-SAST85 — PASS đúng ngưỡng, đổi phương pháp so với baseline

85.0% (51/60, majority 2/3 qua 3 lượt) — đạt ngưỡng ≥85% nhưng **biên = 0**, một câu đổi kết quả là
fail ngay. **Thay đổi phương pháp quan trọng:** profile chat hiện tại không có tool đọc file, nên mã
nguồn SAST phải nhúng thẳng vào prompt thay vì qua tool call — khác với cách baseline được sinh ra
(baseline không ghi rõ profile/tool dùng, cần đối chiếu thêm nếu muốn so sánh công bằng). Trong toàn
bộ 1000+120 lượt gọi (1000 câu chính + 60×2 lượt SAST bổ sung), **0 lượt bị chặn HTTP422** — tức việc
nhúng code trực tiếp vào prompt (thay vì qua tool) không kích hoạt bất kỳ guard `require_public_input`
nào của gateway. Khuyến nghị: mở rộng bộ mẫu SAST (hiện chỉ 60 câu, biên 0%) trước khi coi 85% là con
số ổn định lâu dài.

### R-LLM-EXPLOIT85 / R-LLM-REMED60 / R-KPI-RADAR90 — PASS ngưỡng hợp đồng, nhưng VƯỢT ±5% so baseline

Cả 3 đều đạt ngưỡng tuyệt đối của hợp đồng (94.12%≥85%, 83.33%≥60%, 97.9%>90%) nhưng lệch so với
bản công bố quá 5%: EXPLOIT +5.88%, REMED +16.67%, RADAR +5.77% (theo hướng "tốt hơn" — model dưới
test làm tốt hơn baseline). Theo văn bản `R-EVAL-INDEP` (xlsx dòng 556), lệch >5% dù tốt hơn hay tệ
hơn đều thuộc diện "phải fine-tune/điều chỉnh rồi chạy lại trước nghiệm thu" — **đây là điều khoản
hợp đồng cứng nhắc theo cả hai chiều**, không phải bug đo lường. Cần lưu ý: so sánh này vốn đã khập
khiễng (xem mục fairness bên dưới — model/embed/rerank khác hoàn toàn baseline), nên độ lệch lớn một
phần phản ánh khác biệt stack chứ không thuần là "cải thiện". Khuyến nghị bàn lại với bên ký hợp đồng
liệu điều khoản ±5% có nên áp cho trường hợp "tốt hơn baseline" hay chỉ áp cho "tệ hơn baseline".

### R-KPI-EMB96 — PASS 100%, nhưng con số này chỉ phản ánh đường id-aware, không phải retrieval ngữ nghĩa thuần

Bóc tách từ raw log `embedding-hit-rate-at-10.jsonl` (n=849, mọi câu đều có `has_id_in_query=true`
vì gold = `metadata.cve_id` và câu hỏi luôn chứa mã CVE):

| Đường đo | Hit-rate@10 |
|---|---:|
| **id-aware exact-CVE filter (chỉ số được chấm, `score`)** | **100%** |
| Hybrid RRF dense+BM25 thuần (không có exact-filter, `score_hybrid`) | 58.78% |
| Dense-only thuần (`score_dense`) | 11.78% |

Đây là **tín hiệu chất lượng retrieval thật, đáng lo**, dù không phải là chỉ số được chấm điểm:
100% chỉ đạt được vì mọi câu hỏi trong bộ đề đều có sẵn mã CVE tường minh trong text, kích hoạt
đường tắt exact-match ở `retrieval_kpi.py:263`. Nếu người dùng thật hỏi bằng ngôn ngữ tự nhiên
không kèm mã CVE (rất phổ biến trong thực tế), hệ thống rơi về đường hybrid thuần chỉ đạt **58.78%**
— cách xa ngưỡng >96% — còn dense-only gần như vô dụng (11.78%). Không fail KPI nào (đúng công thức
được ký), nhưng nên báo cho requester: KPI này đo đúng như định nghĩa hợp đồng, song không đại diện
cho trải nghiệm truy vấn semantic-only thực tế. Khuyến nghị: bổ sung một biến thể benchmark loại bỏ
mã CVE khỏi câu hỏi để đo retrieval ngữ nghĩa thuần, tách bạch khỏi KPI hợp đồng hiện tại.

---

## Nhóm B — 5 KPI hiệu năng chatflow (0/5 PASS thật — mode=reduced)

**PHÁT HIỆN HẠ TẦNG QUAN TRỌNG NHẤT của toàn bộ đợt kiểm chứng này: rate limiter của chính gateway
chặn không cho đo đúng ngưỡng hợp đồng của Nhóm B.**

`src/secai_gateway/api/middleware.py:19-20,33` — hàm `route_limit()` trả về **15** cho mọi path
không nằm trong danh sách ngoại lệ (`/ai/exploit`→20, `/admin/refresh-models`→5,
`/ai/translate`/`/ai/generate-report`/`/ai/generate-statistical-report`→10); `/ai/chat` và
`/ai/chat/stream` rơi vào nhánh mặc định = **15 request/60 giây**, và middleware áp giới hạn này
**theo từng client IP** (`client = request.client.host`, dòng 33) — tức PER SOURCE IP, không phải
per-key hay global.

Ngưỡng hợp đồng Nhóm B đòi: **R-PERF-CCU ~30 luồng đồng thời** và **R-PERF-RPS ~0.6 RPS (=36
req/phút)**. Cả hai đều **vượt cấu trúc** giới hạn 15 req/60s/IP của chính gateway — không có cách
nào từ một IP nguồn vừa tuân thủ rate limiter vừa đạt cả hai ngưỡng cùng lúc. Đây là mâu thuẫn nội
tại giữa cơ chế bảo vệ gateway và mục tiêu benchmark, không phải lỗi client.

**Phương án đã chọn cho lần đo này (trong 3 phương án khả dĩ):**
1. ~~Sửa cấu hình rate limiter của gateway để nới giới hạn khi benchmark~~ — KHÔNG làm, vì thay đổi
   cấu hình production để "qua bài" benchmark làm sai lệch ý nghĩa của phép đo.
2. ~~Bỏ qua rate limiter / đấu trực tiếp bypass~~ — KHÔNG làm, vì mục tiêu là đo hệ thống thật đang
   chạy, không phải đo một cấu hình giả lập.
3. **→ ĐÃ CHỌN: mode=reduced.** Đo ở concurrency=10 (self-pace ~11.8 req/phút, giữ margin dưới trần
   15/60s/IP) — tuân thủ rate limiter thật của gateway, chấp nhận rằng R-PERF-CCU và R-PERF-RPS
   *theo định nghĩa* sẽ không đạt ngưỡng hợp đồng, và ghi rõ đây là **giới hạn phương pháp đo do rate
   limiter**, không phải giới hạn năng lực xử lý của hệ thống.

| Mã | Đo (mode=reduced) | Ngưỡng hợp đồng | Script báo | **PASS thật** | Vì sao |
|---|---:|---|:--:|:--:|---|
| R-PERF-TPS | p50 8.1 tok/s (mean 19.9, chỉ 2.1% rơi vào dải) | 20-35 tok/s | ✅ (bug) | ❌ | Số **THẤP hơn** dải — thiếu hụt tốc độ sinh token thật, KHÔNG liên quan tới concurrency giảm |
| R-PERF-TTFT | p50 1664ms (p95=3886, chỉ 9% trong dải) | 900-1500ms | ✅ (bug) | ❌ | Vượt trần — WAN round-trip + queueing tới vast.ai, KHÔNG được cải thiện bởi giảm tải |
| R-PERF-RPS | 0.182 | ~0.6 | ✅ (bug) | ❌ | Đo cố ý ở tải giảm để tuân thủ rate limiter — giới hạn phương pháp, không phải năng lực |
| R-PERF-CCU | 10 (= chính tham số `--ccu` đầu vào) | ~30 | ✅ (bug) | ❌ | Đo cố ý ở tải giảm để tuân thủ rate limiter — giới hạn phương pháp, không phải năng lực |
| R-CTRL-RESP15 | p50 52644ms (p95=119311ms, chỉ 23% ≤15s) | ≤15000ms | ❌ (đúng) | ❌ | Lệch nghiêm trọng nhất nhóm — không giải thích được bởi giảm tải (tải thấp hơn phải nhanh hơn, không chậm hơn) |

**Lỗi chấm điểm trong script (đã xác nhận, không phải phát hiện của lần chạy này mà đã có trong
BANG-KPI.md cạm bẫy #1):** `build_perf_summary.py:65` gán `pass=True` cho TPS/TTFT/RPS chỉ cần có
giá trị (không so ngưỡng gì cả), và R-PERF-CCU chỉ kiểm "có giá trị VÀ 0 lỗi" (không bao giờ so với
~30). Script tự báo "4/5 KPI đạt" — **sai**. Chấm lại đúng ngưỡng hợp đồng: **0/5 KPI Nhóm B đạt**.

**Hai phát hiện KHÔNG nằm trong phạm vi dự kiến ban đầu (không giải thích được bằng lý do
rate-limit/reduced-load):** R-PERF-TPS (8.1 tok/s thực đo, thấp hơn hẳn dải 20-35) và R-PERF-TTFT
(1664ms, vượt trần 1500ms) là hai thiếu hụt hiệu năng thật — giảm concurrency đáng lẽ phải CẢI THIỆN
hai chỉ số này (ít tranh chấp tài nguyên hơn), không làm chúng tệ đi, nên đây không phải tác dụng phụ
của phương pháp đo giảm tải. Nhiều khả năng phản ánh tốc độ sinh token thực của backend LLM qua
đường WAN tới vast.ai. **R-CTRL-RESP15 là phát hiện nghiêm trọng nhất của cả nhóm B**: p50=52.6s,
p95=119.3s, chỉ 23/100 (23%) yêu cầu hoàn thành trong 15s — đây là gate DUY NHẤT trong nhóm B được
script chấm đúng thật (không có bug), và nó fail rõ ràng, nghiêm trọng.

**⚠ Cảnh báo bổ sung cho requester (ngoài phạm vi benchmark, nhưng quan trọng để biết):** nếu traffic
thật của ias.id.vn có bao giờ đi qua NAT/proxy dùng chung 1 IP nguồn để tới gateway này, thì đúng
giới hạn 15 req/60s/IP nói trên **cũng sẽ chặn/làm chậm người dùng thật** theo cùng cơ chế đã quan
sát ở đây — nên kiểm tra độc lập với benchmark này xem hạ tầng production có đi qua NAT/proxy dùng
chung IP hay không.

---

## Nhóm C — 3 KPI throughput mô hình (2/3 PASS, đo qua WAN)

| Mã | Đo | Ngưỡng | PASS | Ghi chú |
|---|---:|---|:---:|---|
| R-MODEL-EMB1200 | 366.9 emb/s @conc32 | ≥1200 | ❌ | Xem dưới |
| R-MODEL-RERANK180 | 1640.1 docs/s @conc32 | ≥180 | ✅ | 9x ngưỡng, không nghi ngờ |
| R-LLM-TPS28 | 88.5 tok/s p50 (TTFT 754ms) | ≥28 | ✅ | 3x ngưỡng, không nghi ngờ |

### R-MODEL-EMB1200 — FAIL, nghi ngờ bị giới hạn RTT-mạng chứ chưa chắc là regression thật

366.9 emb/s đo tại peak concurrency=32, cách ngưỡng ≥1200 khá xa (chỉ đạt ~31%). Theo BANG-KPI.md
cạm bẫy #4, "ngưỡng tính trên A100 tại chỗ; đo từ máy bench gồm cả độ trễ mạng" — và thực tế đo được
đúng dấu hiệu này: throughput **scale gần tuyến tính** theo concurrency ở cả 3 mức (110.7 → 209.2 →
366.9 emb/s tại conc 8/16/32), **không có dấu hiệu bão hòa (plateau)** — đây là chữ ký kinh điển của
hệ thống bị giới hạn bởi round-trip-time (RTT) mạng WAN, không phải giới hạn bởi GPU đã bão hòa (nếu
GPU bão hòa, đường cong phải phẳng dần ở concurrency cao). Reranker và LLM đo trên cùng đường WAN
nhưng vượt ngưỡng thoải mái (9x và 3x) nên không nghi ngờ hạ tầng đo nói chung — chỉ riêng embedding
có dấu hiệu chưa đạt trần thật của nó.

**Chưa thể kết luận** từ lần đo này liệu embedding model có thực sự dưới ngưỡng hay chỉ cần quét thêm
concurrency cao hơn (64/128) hoặc đo tại chỗ (localhost trên chính máy vast.ai, loại bỏ RTT mạng)
trước khi coi đây là regression thật. **Hướng xử lý:** không renegotiate ngưỡng ngay — trước tiên đo
lại on-host để tách bạch giới hạn mạng khỏi giới hạn model, vì đây khả năng cao là fixable-bằng-đo-lại
(đổi phương pháp đo) chứ không phải fixable-bằng-tune-hệ-thống.

---

## Giới hạn của bản thân phép so sánh ≤5% (fairness caveat)

`compare_kpi.py` tự động cảnh báo: **model khác nhau hoàn toàn** giữa bản đang đo (A) và bản công bố
(B) — A = `DuyTa/Cyber-F1-AWQ` + `DuyTa/sec-embedding` + `DuyTa/sec-rerank`, B = `Qwen/Qwen3-30B-A3B`
+ `BAAI/bge-m3` + `Qwen/Qwen3-Reranker-0.6B`. Đây **không phải bug của phép đo** mà là **giới hạn cấu
trúc**: toàn bộ chuỗi suy luận (LLM sinh câu trả lời) VÀ toàn bộ chuỗi retrieval (embedding + rerank)
đều đổi model so với baseline được ký, nên bất kỳ Δ% nào quan sát được — dù trong hay ngoài ±5% —
đều là tổng hợp của (a) thay đổi thật về chất lượng hệ thống và (b) khác biệt vốn có giữa hai stack
mô hình, không thể tách bạch hai thành phần này chỉ từ báo cáo này. Khuyến nghị: nếu mục tiêu là đo
"cùng hệ thống, hai lần chạy" theo đúng tinh thần điều khoản ±5%, cần một lần đo baseline lại trên
CHÍNH stack model hiện tại trước khi áp điều khoản fine-tune-bắt-buộc cho các KPI vượt ±5%.

---

## Giới hạn phương pháp Sanitization (R-SEC-PII — PASS nhưng không đo hệ thống thật đang chạy)

98.11% > 90%, PASS thoải mái. Nhưng `scripts/sanitization_kpi.py` import
`from sanitizer import detector` — một bản **triển khai lại độc lập, đứng riêng**
(`eval-standalone/sanitizer/detector.py`), KHÔNG chạy qua sanitizer thật của gateway đang deploy tại
`secai-agent-gateway/src/secai_gateway/security.py` + `sanitize/gate.py`. Vì vậy con số 98.11% xác
nhận chất lượng của bộ detector độc lập dùng để benchmark, **không nói trực tiếp điều gì về hành vi
sanitization của hệ thống production**. Đây là khoảng trống phương pháp cần vá trước khi dùng con số
này làm căn cứ nghiệm thu cho hành vi bảo mật thật của gateway — khuyến nghị viết lại
`sanitization_kpi.py` để gọi endpoint sanitize thật của gateway (hoặc import trực tiếp
`secai_gateway.security`/`sanitize.gate`) thay vì bản triển khai lại.

---

## Tỉ lệ chặn HTTP422 / HTTP429 thật trên 1000 câu (không phải ước tính smoke 20 câu)

- **HTTP422 (`require_public_input` guard):** 0/1000 câu chính + 0/120 lượt SAST bổ sung = **0 lần
  bị chặn** trong toàn bộ lần chạy đầy đủ.
- **HTTP429 (rate limit):** 0 lần fail cuối cùng (`n_429_blocked=0`); có một số 429 tạm thời được
  adapter tự retry-with-backoff hấp thụ trong suốt quá trình (self-pace ~12 req/60s, dưới trần 15/60s
  của gateway), không ảnh hưởng tới số câu hoàn thành.
- **Lỗi khác:** 8/1000 (0.8%) `SSE_error_event_seen_degraded`, toàn bộ thuộc capability `cve_qa` —
  gateway phát sự kiện lỗi giữa luồng SSE nhưng vẫn trả về câu trả lời cuối dùng được (không phải câu
  hỏi bị mất, chỉ là degraded-nhưng-vẫn-trả-lời).
- **Bị chặn bởi input classifier:** 22/1000 (2.2%) — trong đó 11/22 là bẫy ngoài chủ đề (đúng như
  thiết kế), nhưng **11/22 là câu hỏi bảo mật hợp lệ bị chặn nhầm** (nhãn `injection` sai, điểm
  injection chỉ ~0.26-0.28 trong khi điểm security ~0.93-0.99) — phát hiện riêng từ pha đo Group A,
  đáng báo cho người phụ trách ngưỡng classifier trong `security.py`, không thuộc 19 KPI hợp đồng
  nhưng ảnh hưởng trực tiếp tới trải nghiệm người dùng thật.

---

## Tổng kết đếm PASS (đếm lại theo ngưỡng hợp đồng thật, không lấy trường `pass` gốc của script cho 4 mã Nhóm B lỗi)

| Nhóm | Tổng | PASS thật |
|---|---:|---:|
| A — Chất lượng AI | 11 | 10 (chỉ FAITH98 fail) |
| B — Hiệu năng chatflow | 5 | 0 (rate-limit + genuine TPS/TTFT/RESP15 shortfalls) |
| C — Throughput mô hình | 3 | 2 (chỉ EMB1200 fail, nghi RTT-limited) |
| **Tổng 19 KPI** | **19** | **12** |

Danh sách 7 mã cần điều chỉnh/nghiên cứu thêm (bao gồm cả 3 mã PASS-ngưỡng-nhưng-vượt-±5%-baseline):

1. `R-KPI-FAITH98` — renegotiate ngưỡng (~85%, có căn cứ tài liệu nội bộ) hoặc đo lại với giám khảo độc lập
2. `R-PERF-CCU` — mâu thuẫn cấu trúc với rate limiter gateway, không fix bằng tune hệ thống
3. `R-PERF-RPS` — mâu thuẫn cấu trúc với rate limiter gateway, không fix bằng tune hệ thống
4. `R-PERF-TPS` — thiếu hụt thật (8.1 vs dải 20-35), cần điều tra backend LLM
5. `R-PERF-TTFT` — thiếu hụt thật (1664ms vs trần 1500ms), cần điều tra WAN/queueing
6. `R-CTRL-RESP15` — thiếu hụt nghiêm trọng nhất (p50=52.6s vs trần 15s), cần điều tra ưu tiên cao nhất
7. `R-MODEL-EMB1200` — nghi RTT-limited, cần đo lại on-host trước khi kết luận
