# Bảng KPI secAI — 19 tiêu chí

Bản tra cứu ngắn. Muốn biết **quy trình đo lại và so sánh 2 phiên bản** → đọc `CACH-DANH-GIA.md`.
Bản máy đọc → `kpi-registry.json`. So sánh 2 phiên bản → `compare_kpi.py`.

> **Ngưỡng trong bảng này trích từ NƠI ÁP THẬT trong code.**
> Đừng đọc `source/eval/kpi/config.py` — bảng `THRESHOLDS` + `check_threshold()` ở đó
> trông như nguồn sự thật nhưng **không nơi nào gọi** (đã kiểm cả 5 bản sao của file này).

Mức đạt của **từng câu hỏi** là `score ≥ 0.75` (`task_kpi.py:69,78,87` · `acceptance_runner.py:191`
· `grader/shared/matchers.ts:94`). KPI là tỉ lệ % số câu đạt, trừ các KPI ghi công thức riêng.

---

## Nhóm A — 11 KPI chất lượng AI

Một lệnh ra cả 11: `bash run_eval_all.sh` → `runs/<ngày-giờ>-evalall/raw-logs/kpi-summary.json`

| Mã | Tên | Ngưỡng | Gate thật? | Công thức | Script | Raw log |
|---|---|---|:--:|---|---|---|
| `R-KPI-EMB96` | Embedding Hit Rate@10 | > 96% | ✅ | gold `cve_id` ∈ top-10 đường id-aware (exact-CVE filter → RRF dense+BM25, K=60) | `retrieval_kpi.py:263` | `embedding-hit-rate-at-10.jsonl` |
| `R-KPI-RERANK90` | Reranking Precision@1 | > 90% | ✅ | top-1 sau rerank khớp gold | `retrieval_kpi.py:267` | `reranking-precision-at-1.jsonl` |
| `R-KPI-FAITH98` | Faithfulness (RAGAS) | > 98% | ✅ | judge NLI đếm mệnh đề được context chứng minh → `supported/total` | `faith_judge.py:56-93` | `llm-faithfulness.jsonl` |
| `R-KPI-RELEV90` | Answer Relevance | > 90% | ✅ | LLM-judge trả `{"score":0..1}`, lấy trung bình ×100 | `llm_kpi.py:197-224` | `llm-answer-relevance.jsonl` |
| `R-KPI-ENTITY100` | Entity Precision CVE/CWE | = 100% | ✅ | `(số id nêu ra − số id bịa) / số id nêu ra`, lấy trung bình | `llm_kpi.py:131-152` | `llm-entity-precision.jsonl` |
| `R-KPI-HALLU2` | Hallucination Rate | < 2% | ✅ | % câu bịa entity **hoặc** không từ chối câu bẫy | `llm_kpi.py:155-170` | `llm-hallucination.jsonl` |
| `R-LLM-SAST85` | SAST accuracy | ≥ 85% | ✅ | base_grade + nêu đúng CWE gold, **majority 2/3 lượt** | `task_kpi.py:61-69,97-125` | `sast.jsonl` |
| `R-LLM-EXPLOIT85` | Exploit Explanation | ≥ 85% | ✅ | base_grade + có nêu cơ chế (`EXPLOIT_KW`) | `task_kpi.py:72-78` | `exploit-explanation.jsonl` |
| `R-LLM-REMED60` | Auto-remediation | ≥ 60% | ✅ | base_grade thuần | `task_kpi.py:81-87` | `auto-remediation.jsonl` |
| `R-SEC-PII` | Sanitization PII/Secret F1 | > 90% | ✅ | micro F1 gộp 5 loại: `2PR/(P+R)` | `sanitization_kpi.py:96-118` | `sanitization.jsonl` |
| `R-KPI-RADAR90` | Radar coverage area | > 90% | ✅ | `diện tích ngũ giác(actual) / diện tích(cam kết)` | `build_reports.py:283-286` | `reports/radar-data.json` |

**Dữ liệu vào:** 1000 câu `datasets-acceptance/*.jsonl` (400 định danh · 400 khắc phục · 200 tình huống).
Riêng `R-SEC-PII` dùng 500 mẫu **sinh runtime** bởi `sanitizer/gen_pii_dataset.py --n 500 --seed 42`.
Riêng retrieval bỏ câu trap và câu nhiều CVE → còn ~735 câu.

**GOLD = `metadata.cve_id`** của mỗi câu — chính nó nối retrieval, entity, và faithfulness gold-contexts.

---

## Nhóm B — 5 KPI hiệu năng chatflow

`./bench-perf.sh` → `runs/<ngày>/perf/raw-logs/kpi-summary.json`

| Mã | Tên | Ngưỡng | Gate thật? | Công thức | Script |
|---|---|---|:--:|---|---|
| `R-PERF-TPS` | TPS mỗi luồng | 20–35 tok/s | ❌ | `tokens/(total−ttft)`, p50 | `perf_load.py` |
| `R-PERF-TTFT` | TTFT p50 | 900–1500 ms | ❌ | `min(part.start) − info.time.created` | `perf_load.py` |
| `R-PERF-RPS` | RPS ổn định | ~0.6 | ❌ | `số request OK / wall clock` | `perf_load.py` |
| `R-PERF-CCU` | CCU đồng thời | ~30 luồng | ⚠️ | **là tham số đầu vào `--ccu`**, không phải đại lượng đo | `perf_load.py` |
| `R-CTRL-RESP15` | Response p50 | ≤ 15000 ms | ✅ | p50 của `completed − created` | `perf_kpi.py` |

## Nhóm C — 3 KPI throughput mô hình

`./bench-models.sh` → `runs/<ngày>/models/raw-logs/kpi-summary.json`

| Mã | Tên | Ngưỡng | Gate thật? | Công thức | Script |
|---|---|---|:--:|---|---|
| `R-MODEL-EMB1200` | Embedding throughput | ≥ 1200 emb/s | ✅ | peak `embeddings/s` qua thang concurrency 8/16/32 | `tp_bench.py --mode embed` |
| `R-MODEL-RERANK180` | Reranker throughput | ≥ 180 docs/s | ✅ | peak `docs/s` (batch 50) | `tp_bench.py --mode rerank` |
| `R-LLM-TPS28` | LLM throughput | ≥ 28 tok/s | ✅ | `tokens/(e2e−ttft)`, p50, lọc luồng > 20 token | `llm_load.py` |

Chạy tất cả 19: `./bench-all.sh` (hoặc `./run-acceptance-full.sh`) → `runs/<ngày>/SUMMARY.md`.

---

## ⚠ 9 cạm bẫy khi so sánh 2 phiên bản

Đọc kỹ trước khi kết luận "phiên bản mới tốt/tệ hơn". Tất cả đã kiểm chứng trong mã nguồn.

**1 · 4/8 KPI nhóm B–C không hề so với ngưỡng.**
`build_perf_summary.py:65` viết `p = True if (code == "R-PERF-TPS" and val is not None) else bool(ok(val))`,
còn lambda `ok` của TTFT/RPS/CCU chỉ là `v is not None`. Hệ quả thật:
`runs/2026-07-11/SUMMARY.md` ghi **`TTFT 2755 ms → ĐẠT`** (dải cam kết 900–1500) và
**`RPS 0.252 → ĐẠT`** (ngưỡng ~0.6). → Chỉ `R-CTRL-RESP15`, `R-MODEL-*`, `R-LLM-TPS28` là gate thật.
`compare_kpi.py` **tự chấm lại** 4 KPI này theo ngưỡng hợp đồng thay vì tin trường `pass`.

**2 · Một ô báo cáo, ba đơn vị khác nhau.** `build_perf_summary.py:40` lần lượt lấy giá trị từ:
`perf_load.py` (token **thật** từ server) → `perf_kpi.py` (ước lượng `len(text)/4`, **sai với tiếng Việt**)
→ `llm_load.py` (đếm **số chunk SSE**, không phải token). So sánh 2 lần chạy khác nguồn fallback là vô nghĩa.

**3 · SAST có 3 con số mâu thuẫn trong repo: 85% / 100% / 21.43%.**
Pipeline nghiệm thu chấm **majority 2/3 lượt**, pipeline KPI chấm **1 lượt** (`bench.sh:118` không truyền
`--sast-runs`). **Không được trộn.** `compare_kpi.py` đọc trường `method` trong `sast.jsonl` và cảnh báo.

**4 · Throughput phụ thuộc vị trí đo.** Ngưỡng tính trên A100; đo từ máy bench là gồm cả độ trễ mạng
(`build_perf_summary.py:72`). Hai phiên bản phải đo **từ cùng một chỗ**.

**5 · `R-KPI-RELEV90` lấy chính model đang đo làm giám khảo** (`llm_kpi.py:208`).
Đổi model là đổi luôn thước đo → phải ghi chú khi so A/B. (Hợp đồng ghi "Cosine Similarity";
đường cosine cũ còn nằm chết ở `llm_kpi.py:186-194`.)
→ `R-KPI-FAITH98` dính **y hệt bệnh này** bất cứ khi nào không có judge key độc lập: bản chạy
2026-08-26 trên `secai-agent-gateway` không có `RAGAS_JUDGE_*` riêng, phải trỏ giám khảo về
chính model đang đo (`DuyTa/Cyber-F1-AWQ`) → đo được 96.71% so ngưỡng > 98% — khớp với ghi nhận
ở cạm bẫy #6 rằng 98% có thể không khả thi với RAG. Xem `KET-QUA/2026-08-26/`.

**6 · `R-KPI-FAITH98` đặt ngưỡng 98% nhưng chính tài liệu nội bộ nói không khả thi.**
`nghiem-thu-suite/README.md:23` ghi đo thật **88.8%** trên 822 mẫu và nhận định 98% là không đạt được
với RAG phân tích. Nếu phiên bản mới ra ~88% thì đó là **bình thường**, không phải hồi quy.

**7 · Có KPI thứ 12 không ngưỡng: `R-KPI-REASONING`** (`llm_kpi.py:173-175` — có marker suy luận
và ≥40 từ). Nó không nằm trong 19 KPI nhưng **là 1 trong 5 trục radar** → ảnh hưởng gián tiếp
`R-KPI-RADAR90`. Radar cũng tính metric thiếu thành 0, nên một bước lỗi kéo tụt cả radar.

**8 · Mã thứ 20 `R-CHATFLOW-TPS25` không tái lập được.** Nó có trong `kpi_results/kpi-summary.json`
nhưng không script sống nào sinh ra được — chỉ tồn tại trong `kpi_results/perf/tools/build_full_run.py`
(đã mồ côi, in `"✅ ĐẠT"` như chuỗi cứng và đặt `"pass": True` thẳng tay). Vì vậy bộ này ghi **19 KPI**,
không phải "20/20 ĐẠT".

**9 · Rate limit của gateway mới đè trực tiếp lên ngưỡng `R-PERF-CCU`/`R-PERF-RPS`, không cách nào
tune ra được cả hai cùng lúc.** Backend đang deploy thật (`secai-agent-gateway`, endpoint
`POST /ai/chat/stream`) tự giới hạn **15 request/60s theo IP** trên `/ai/chat(/stream)`
(`src/secai_gateway/api/middleware.py:route_limit`, `state/ratelimit.py`) — một cơ chế an toàn
production thật, vì gateway này còn phục vụ traffic khác. Trong khi đó hợp đồng đòi
`R-PERF-CCU` **~30 luồng đồng thời** và `R-PERF-RPS` **~0.6 RPS = 36 req/phút** — bản thân
36 req/phút **đã vượt** trần 15 req/60s **bất kể** chỉnh concurrency thế nào, nên không tồn tại
cấu hình client nào từ 1 IP thỏa mãn đồng thời rate limit **và** hai mục tiêu hợp đồng đó.
Ba cách xử lý khi đo lại nhóm B: (a) đo ở concurrency thấp hơn (vd `--concurrency 10`) và ghi rõ
`R-PERF-CCU`/`R-PERF-RPS` là "không đo ở tải hợp đồng, chỉ tuân thủ rate-limit" — đây là cách đã
làm thật trong lần chạy 2026-08-26; (b) tạm nâng rate limit của gateway cho đúng cửa sổ benchmark
rồi trả lại sau (thay đổi cấu hình production thật, cần xin phép rõ ràng mỗi lần, không bao giờ để
nâng vĩnh viễn); (c) bỏ qua đo sống nhóm B và chỉ ghi nhận đây là blocker. Xem chi tiết + số liệu
thật ở `KET-QUA/2026-08-26/group-B-perf/`.

---

## Bốn baseline đang tồn tại — phải chốt trước khi so sánh

| Baseline | Đường dẫn | Kết quả |
|---|---|---|
| **Bản công bố (đã ký)** ← mặc định | `kpi_results/quality/raw-logs/kpi-summary.json` | 11/11 ĐẠT |
| Bản chạy thật 2026-07-11 | `runs/2026-07-11/SUMMARY.md` | 13/19 ĐẠT |
| Baseline bge-large-EN cũ | `source/eval/kpi/raw-logs/kpi-summary.json` | 6/11 ĐẠT |
| Bộ test độc lập trên `secai-agent-gateway` (Cyber-F1-AWQ), 2026-08-26 | `KET-QUA/2026-08-26/group-A-quality/raw-logs/kpi-summary.json` | 10/11 ĐẠT (nhóm A) |

Hợp đồng ghi *"sai lệch ≤ 5% so với **chỉ số công bố**"* → mặc định đối chiếu **bản công bố**.

---

## Tiêu chí hợp đồng chưa có script đo (kiểm thủ công)

`R-PERF-CTX16K` (context 16k) · `R-PERF-EFFCTX12K` (effective 12k) · `R-DEPLOY-DOCKER` (image 15–20 GB)
· `R-CTRL-REPORT` · `R-CTRL-CHAT` · `R-CTRL-TRANSLATE` · `R-EVAL-0DAY` (quy trình cập nhật 0-day).
Hiện là mục checklist tay trong `source/eval/nghiem-thu-suite/KICH-BAN-NGHIEM-THU.md`.

`R-EVAL-INDEP` (kiểm chứng độc lập, sai lệch ≤ 5%) — **dùng `02-GEN-TEST/` + `compare_kpi.py` của bộ này**.
