# KPI-VA-GEN-TEST

Hai việc, một thư mục:

| Thư mục | Nội dung |
|---|---|
| **`01-KPI/`** | Toàn bộ **19 KPI** của secAI: đo bằng gì, ngưỡng bao nhiêu, và **cách đánh giá lại trên một phiên bản khác** rồi so sánh |
| **`02-GEN-TEST/`** | **Code + tài liệu** sinh một bộ test độc lập, khi cần phối hợp khách hàng tổng hợp bộ đề mới |

Tất cả chạy bằng **thư viện chuẩn Python** — không phải cài gì (`openpyxl` chỉ cần khi
muốn xuất/đọc Excel).

---

## Backend đang chạy thật

Pipeline gốc trong repo này (`chay_eval_bo_moi.sh`, `lam_answers.py`,
`source/eval/kpi/scripts/bench_answers_concurrent.py`, `perf-bench/perf_load.py`,
và mọi lệnh `run_eval_all.sh`) được viết cho backend **CŨ**: server kiểu opencode
ở `http://localhost:4096`, API `/session` + `/session/{id}/message`, basic-auth
qua `OPENCODE_SERVER_USERNAME`/`PASSWORD`, repo root `/root/secai-88`,
`ACC_AGENT=build`. Các script đó **không hỏng** — vẫn dùng được nếu dựng lại đúng
backend đó — chỉ là **không phải backend đang deploy**.

Backend thật đang chạy: **`secai-agent-gateway`** (FastAPI trong Docker,
container `secai-docker-gateway`, mặc định `http://127.0.0.1:8131`), repo
`secai-agent-gateway/`. Khác biệt chính so với backend cũ:

- Endpoint `POST /ai/chat/stream` (SSE), body `{"message": "<câu hỏi>"}` —
  KHÔNG có `/session`. Bỏ trống `user_id` (hoặc để `"anonymous"`) để mỗi câu hỏi
  là 1 session độc lập, tránh dây bối cảnh giữa các câu.
- Biến môi trường gọn hơn — không còn `OPENCODE_SERVER_USERNAME/PASSWORD`,
  `ACC_AGENT=build`. Bộ đã chạy thật: `ACC_BASE=http://127.0.0.1:8131
  ACC_PROVIDER=secai-gateway ACC_MODEL=DuyTa/Cyber-F1-AWQ ACC_AGENT=chat`
  (`.env.bench` đầy đủ và repo root ví dụ `/home/ict/secai-88` — thay bằng repo
  root thật của bạn — xem `02-GEN-TEST/HUONG-DAN-GEN.md` hoặc log chạy thật
  trong `KET-QUA/2026-08-26/`).
- Gateway tự giới hạn request/60s theo IP trên `/ai/chat(/stream)`
  (`src/secai_gateway/api/middleware.py:route_limit`, `state/ratelimit.py`) —
  cơ chế an toàn production thật (gateway còn phục vụ traffic khác), không phải
  bug. Ngưỡng gốc **15/60s** khiến `R-PERF-CCU` (~30 luồng) và `R-PERF-RPS`
  (~36 req/phút) **không thể** đo đúng tải hợp đồng từ 1 IP nguồn — đã **nâng
  vĩnh viễn lên 90/60s riêng cho `/ai/chat`** ngày 2026-08-27 (các route khác
  giữ nguyên), xem `KET-QUA/2026-08-27/` và `KET-QUA/2026-08-28/`.
- Dùng bộ adapter mới ở **`02-GEN-TEST/adapters/`** (`bench_answers_gateway.py`,
  `perf_load_gateway.py`) — thay thế trực tiếp cho
  `bench_answers_concurrent.py`/`perf_load.py`. Đọc `tool_call` thật từ SSE
  (bắt buộc để chấm `must_use_tool` — endpoint `/chat_no_stream` KHÔNG lộ tool
  đã thực sự gọi, chỉ lộ tool khả dụng, nên **không được** dùng để sinh đáp án
  chấm KPI), tự bắt lỗi HTTP 422 (guard `require_public_input()` chặn câu hỏi
  chứa mã nguồn giống bí mật — hay gặp ở câu SAST/khắc phục — ghi nhận là bị
  chặn rồi chạy tiếp), và tự giới hạn tốc độ (~12-13 req/60s, sliding-window
  dùng chung giữa các luồng) + retry-backoff khi dính 429.
- Pipeline đủ bước cho backend này: **`02-GEN-TEST/chay_eval_gateway_moi.sh`**
  — bản dùng adapter mới, tương đương `chay_eval_bo_moi.sh` nhưng chạy trên
  gateway thật.

Mọi lệnh mẫu trong tài liệu này vẫn còn công thức cho backend **CŨ**; mặc định
nên dùng đường dẫn backend **MỚI** ở trên trừ khi đang test lại opencode cũ.

---

## Phần 1 — KPI

| Tệp | Dùng để |
|---|---|
| **`01-KPI/BANG-KPI.md`** | ★ Bảng tra cứu 19 KPI: mã · ngưỡng · công thức · script · file kết quả. **Đọc cái này trước.** |
| `01-KPI/CACH-DANH-GIA.md` | Quy trình chạy lại, điều kiện để so sánh công bằng, deliverable, sự cố thường gặp |
| `01-KPI/kpi-registry.json` | Bản máy đọc — nguồn sự thật duy nhất cho 19 KPI |
| `01-KPI/compare_kpi.py` | So sánh 2 phiên bản + áp luật sai lệch ≤ 5% |

```bash
# so sánh phiên bản mới với chỉ số đã công bố
python3 01-KPI/compare_kpi.py \
    --a source/eval/kpi/runs/<run-mới> \
    --b kpi_results/quality/raw-logs \
    --md bao-cao.md --xlsx bao-cao.xlsx
```

**19 KPI = 11 chất lượng AI + 5 hiệu năng chatflow + 3 throughput mô hình.**

> ⚠ Hai điều phải biết trước khi kết luận "phiên bản nào tốt hơn":
> 1. **4/8 KPI hiệu năng không hề được script so với ngưỡng** — `build_perf_summary.py:65`
>    trả `pass=True` chỉ cần có số. Đó là lý do `runs/2026-07-11` ghi `TTFT 2755 ms → ĐẠT`
>    với dải cam kết `900–1500 ms`. `compare_kpi.py` **tự chấm lại** 4 KPI này.
> 2. Repo có **3 baseline mâu thuẫn nhau** (11/11 · 13/19 · 6/11). Phải chốt dùng bản nào.
>
> Đầy đủ 9 cạm bẫy: xem cuối `01-KPI/BANG-KPI.md`.

---

## Phần 2 — Sinh bộ test độc lập

| Tệp | Dùng để |
|---|---|
| **`02-GEN-TEST/HUONG-DAN-GEN.md`** | ★ Thu thập ở đâu · gen thế nào · phối hợp khách hàng · đưa vào chấm |
| `02-GEN-TEST/NGUON-DU-LIEU.md` | Chi tiết 3 nguồn + **license** (CTIBench là NC — đọc kỹ) |
| `02-GEN-TEST/MAU-KHACH-HANG.csv` | Mẫu để khách hàng điền |
| `02-GEN-TEST/gen_testset.py` | CLI sinh bộ đề |
| `02-GEN-TEST/validate_testset.py` | Cổng chất lượng — **bắt buộc chạy sau khi gen** |
| `02-GEN-TEST/chay_eval_bo_moi.sh` | Chạy đủ 11 KPI trên bộ đề mới — backend **CŨ** (opencode `:4096`, xem cảnh báo dưới) |
| **`02-GEN-TEST/chay_eval_gateway_moi.sh`** | **MỚI** — tương đương trên `secai-agent-gateway` (backend đang deploy) |
| `02-GEN-TEST/adapters/bench_answers_gateway.py` · `perf_load_gateway.py` | **MỚI** — adapter gọi thẳng `/ai/chat/stream` của gateway (xem "Backend đang chạy thật" ở trên) |
| **`02-GEN-TEST/bench_kpi.py`** | **MỚI (2026-08-28)** — bench nhanh Nhóm B+C (vài phút, có thanh tiến trình + bảng tổng kết so ngưỡng); Nhóm A giao lại `chay_eval_gateway_moi.sh`. Xem "Bench nhanh KPI" bên dưới |
| `02-GEN-TEST/lam_answers.py` | Bản `gen_answers.py` có `--dataset` (backend CŨ) |
| `02-GEN-TEST/templates.py` · `sources/` | Mẫu câu hỏi và 3 nguồn dữ liệu |

```bash
cd 02-GEN-TEST

# sinh 1000 câu từ KB, loại hết CVE đã dùng ở bộ nghiệm thu cũ
python3 gen_testset.py --source qdrant --n 1000 --out ./testset-v2 --exclude-pool

# cổng chất lượng — phải in "✓ ĐẠT"
python3 validate_testset.py ./testset-v2

# đưa vào chấm — backend đang deploy (MỚI, secai-agent-gateway :8131)
./chay_eval_gateway_moi.sh --dataset ./testset-v2

# đưa vào chấm — chỉ khi test lại backend opencode CŨ (:4096)
# (KHÔNG dùng thẳng run_eval_all.sh — xem cảnh báo)
./chay_eval_bo_moi.sh --dataset ./testset-v2
```

> ⚠ **Đừng chạy `bash run_eval_all.sh --dataset <dir>` cho bộ đề mới.** Cảnh báo
> này áp cho nhánh backend **CŨ**: `gen_answers.py:21` hard-code đường dẫn bộ đề
> và không có cờ `--dataset` → với bộ đề mới `answers.jsonl` ra **RỖNG**, khiến 7
> KPI dựa-trên-đáp-án im lặng trả 0/null. Dùng `chay_eval_bo_moi.sh` (chạy đúng 9
> bước, chỉ thay bước 2) cho backend cũ, hoặc — mặc định nên dùng —
> `02-GEN-TEST/chay_eval_gateway_moi.sh` cho backend đang deploy (xem "Backend
> đang chạy thật" ở đầu file). Chi tiết trong `02-GEN-TEST/HUONG-DAN-GEN.md`.

Ba nguồn: `qdrant` (KB nội bộ, offline) · `public` (NVD/KEV/Exploit-DB, độc lập hoàn toàn)
· `customer` (bảng CSV/XLSX khách hàng, chạy offline).

Bộ sinh giữ đúng cơ cấu hợp đồng: **40/40/17/3** theo bucket, **40/30/20/10** theo độ khó,
mỗi CVE dùng 1 lần, câu bẫy dùng CVE giả đã xác minh **zero-hit** trên KB.

---

## Bench nhanh KPI

`02-GEN-TEST/bench_kpi.py` — thuần stdlib, không cần cài gì, có thanh tiến trình
sống + bảng tổng kết so ngưỡng hợp đồng ngay trên terminal:

```bash
cd 02-GEN-TEST
python3 bench_kpi.py                          # mặc định: Nhóm B + C (~2-3 phút)
python3 bench_kpi.py --group c                # chỉ Nhóm C (throughput mô hình)
python3 bench_kpi.py --group a --dataset ./testset-v2      # Nhóm A (1-4+ giờ, có cảnh báo trước khi chạy)
python3 bench_kpi.py --group all --dataset ./testset-v2 --out ket-qua.json
```

Áp đúng 2 quyết định kinh doanh đã chốt 2026-08-28: `R-PERF-TPS` vượt trần dải
20-35 tok/s vẫn tính **ĐẠT** (model nhanh hơn không phải lỗi — thêm cờ
`--tps-band-strict` nếu muốn chấm nghiêm cả 2 đầu dải như bản gốc); tự giới tốc
+ retry HTTP 429 khi gọi gateway (không có thì dễ báo sai `R-PERF-CCU`). Script
tự ghi chú luôn trong `--help`: `R-MODEL-EMB1200` đo từ máy chạy nó sẽ luôn thấp
hơn ngưỡng vì đi qua WAN — số thật (đo on-host) đã xác nhận ~2600 emb/s.

---

## Ví dụ đã chạy thật (backend mới)

Một lượt chạy đủ 19 KPI thật trên `secai-agent-gateway`, ngày 2026-08-26/27, kết
quả lưu ở **`KET-QUA/2026-08-26/`** (`bao-cao-so-sanh.md`, `KPI-CAN-DIEU-CHINH.md`
+ raw-logs 3 nhóm `group-A-quality`/`group-B-perf`/`group-C-models`) — dùng làm
ví dụ tham chiếu khi chạy lại.

Kết quả lần đầu: **12/19 KPI đạt**. Hai phát hiện đáng chú ý (không phải do rate
limit):
- `R-KPI-FAITH98` tự chấm (chưa có key giám khảo độc lập) ra **96.71%** so với
  ngưỡng >98% — khớp cảnh báo sẵn có của toolkit rằng 98% khó đạt với RAG.
- `R-CTRL-RESP15`/`R-PERF-TPS`: LLM thô đạt 88.5 tok/s (nhóm C) nhưng gateway
  end-to-end chỉ ~8 tok/s hiệu dụng (p50 52.6s so với trần hợp đồng 15s) — trong
  khi TTFT vẫn nhanh (~1.6s p50), nên nghi ngờ overhead vòng lặp agent/tool-call
  bên trong gateway, không phải do rate limit hay do đo ở tải thấp của Nhóm B.

Nhắc lại cạm bẫy #1 ở Phần 1: 4/5 mã Nhóm B (`R-PERF-TPS`/`TTFT`/`RPS`/`CCU`)
trong `build_perf_summary.py` vẫn chỉ trả `pass=True` khi có số, không so ngưỡng
thật — chỉ `R-CTRL-RESP15` là cổng thật. Bug này có từ trước, không liên quan
backend mới, nhưng vẫn cần nhớ khi đọc kết quả Nhóm B.

### Vòng tune 2026-08-27/28 — kết quả cuối: 17/19

Kết quả chi tiết + code fix ở `KET-QUA/2026-08-27/`, `KET-QUA/2026-08-28/`.
4 phát hiện + fix thật trên gateway (đều rebuild + restart + đo lại xác nhận):

1. **Classifier chặn nhầm 40% câu hỏi hợp lệ** — `sanitize/assets/thresholds.json`
   ngưỡng `injection` gốc 0.25 quá thấp cho câu dạng liệt kê/mệnh lệnh (VD "Trình
   bày CHI TIẾT về CVE-X: (1)...(2)...(3)..."), đo thật các câu bị chặn nhầm chỉ
   0.25-0.41 trong khi injection/jailbreak thật đo được 0.87-0.9997 — nâng ngưỡng
   lên **0.6**, xác nhận 40/40 câu chặn nhầm nay pass, injection thật vẫn bị chặn.
2. **RESP15 20.4s → 13.5s (TRƯỢT → ĐẠT)** — trace SSE thật phát hiện model hay bỏ
   qua yêu cầu gọi `qdrant_search` trước khi trả lời (nhất là câu dạng liệt kê),
   bị `RequireChatGroundingHook` bắt viết lại toàn bộ câu trả lời (tốn gấp đôi
   thời gian sinh, quan sát 17/30 luồng bị viết lại khi test đồng thời) — thêm
   khối `<mandatory_search_first>` đầu `chat.md`, dứt điểm hiện tượng viết lại.
3. **Faithfulness 96.71% → 97.82%** (vẫn tự chấm, chưa đạt >98%) — thêm khối
   `<grounding_discipline>` vào `chat.md` sau khi tìm được bằng chứng cụ thể:
   model tự bịa cơ chế khai thác không có trong context tra cứu, có chỗ còn
   **mâu thuẫn** với CVSS vector thật (nói "không cần xác thực" trong khi vector
   ghi `PR:LOW`).
4. **Thử fix TTFT bằng cách chạy classifier song song với agent — THẤT BẠI, đã
   revert.** Lý thuyết đúng cho 1 request, nhưng dưới tải 30 luồng đồng thời làm
   TTFT p50 từ ~3.5s vọt lên **14.2s** (dồn cùng lúc cả 30 lệnh gọi classifier
   VÀ 30 lệnh gọi LLM đầu tiên vào backend vast.ai dùng chung) — phát hiện qua đo
   thật, `git checkout` revert ngay, rebuild, đo lại xác nhận về đúng số cũ.

**Nguyên nhân gốc của TTFT (~3.5s so với ngưỡng 900-1500ms), vẫn TRƯỢT:** đối
chiếu `task-AI-requirements.md:105` — mục tiêu `R-PERF-RPS`/`R-PERF-CCU` (~30
người dùng đồng thời) được cam kết **"với 03× A100 80GB"**, nhưng hạ tầng serving
đang chạy (xác minh qua `vastai show instances`, id `48438534`) chỉ có **2× A100
SXM4** — thiếu đúng 1 GPU so với giả định hợp đồng. Ngay cả đo tuần tự không tải
cũng đã ~1.6-3.0s (vượt trần), nên thêm GPU nhiều khả năng giúp nhưng khó đủ để
về hẳn ngưỡng — cần cả bổ sung GPU lẫn tối ưu tầng nền (chưa làm, hướng #4 thất
bại là một lần thử).

**R-PERF-TPS được tính lại là ĐẠT** theo quyết định kinh doanh 2026-08-28: vượt
trần dải 20-35 tok/s (đo được 57-99 tok/s tuỳ lần, xác nhận 88.5 tok/s đo thẳng
LLM ở Nhóm C) là model nhanh hơn kỳ vọng lúc ký ngưỡng, không phải lỗi hệ thống.

**Tổng sau vòng tune: 17/19.** Còn 2 KPI: `R-KPI-FAITH98` (khuyến nghị hạ ngưỡng
xuống ~90%, có căn cứ tài liệu nội bộ) và `R-PERF-TTFT` (cần thêm GPU và/hoặc
điều chỉnh ngưỡng — xem phân tích trên).

---

## Cơ sở hợp đồng

- `R-EVAL-TESTSET` — 1000 mẫu, phân bổ 40% định danh · 40% khắc phục · 20% suy luận tình huống.
- `R-EVAL-RAWLOGS` — raw log từng mẫu (True/Predicted/Score) + evaluation code tái lập được.
- **`R-EVAL-INDEP`** — phối hợp Chủ đầu tư tổng hợp 01 bộ test mới mỗi loại mô hình;
  **sai lệch ≤ 5%** so với chỉ số công bố thì đạt; vượt ngưỡng → fine-tune/điều chỉnh
  **miễn phí** trước nghiệm thu. → chính là việc mà `02-GEN-TEST/` + `compare_kpi.py` phục vụ.

Nguồn: `source/docs/task-AI-requirements.md:85-94` · *Tài liệu nghiệm thu(2).xlsx*
sheet "Chi tiêu kỹ thuật" dòng 531-537 và 556.

---

## Đã kiểm chứng

| Kiểm chứng | Kết quả |
|---|---|
| `compare_kpi.py` tự so chính nó | 11 KPI có số → Δ = 0 toàn bộ (8 KPI hiệu năng "thiếu số" vì thư mục đó không có dữ liệu perf) |
| `compare_kpi.py` so 2 bản khác nhau thật | Bắt đúng 5 KPI vượt ngưỡng + cảnh báo khác model & khác cách chấm SAST |
| Biên: baseline = 0 | Không còn crash; kết luận theo hướng thay đổi (0→0.5 = xấu đi, 0.5→0 = tốt hơn) |
| `kpi-registry.json` vs `kpi-summary.json` thật | 11/11 mã khớp ngưỡng từng ký tự |
| `validate_testset.py` trên bộ 1000 câu nghiệm thu **hiện có** | ✓ ĐẠT — chứng minh cổng Python không lệch chuẩn cổng TS gốc |
| Sinh 1000 câu từ Qdrant + validate | ✓ ĐẠT · 400/400/170/30 · 0 câu trùng |
| Độ độc lập của bộ mới | **0 CVE trùng** với 1000 câu active + 200 reserve + pool 2000 CVE |
| Sinh 200 câu từ NVD/KEV + validate | ✓ ĐẠT |
| Sinh từ bảng khách hàng | Chạy được **không cần mạng, không cần Qdrant** |
| Tái lập | Cùng seed → bộ đề giống hệt; seed khác → bộ đề khác |
| Bộ đề mới nạp được bởi pipeline thật | `retrieval_kpi.load_pairs()` tìm đúng 255/300 cặp gold; `bench_answers` nạp 300/300 mẫu, 0 mẫu hỏng |
| Cổng bắt lỗi | Bẻ 5 chỗ → bắt đủ 5 loại vi phạm, exit code 1 |
| Cổng không phụ thuộc thư mục đang đứng | ✓ ĐẠT từ 3 cwd khác nhau và cả khi bộ đề bị copy đi nơi khác |
