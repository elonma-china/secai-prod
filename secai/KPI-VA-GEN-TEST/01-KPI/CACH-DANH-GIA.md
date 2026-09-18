# Cách đánh giá lại 19 KPI trên một phiên bản khác

Mục tiêu: chạy lại đúng các tiêu chí trong `BANG-KPI.md` trên **một phiên bản khác** của hệ thống
(đổi model / đổi KB / sau fine-tune / bộ test mới) rồi **so sánh công bằng** với bản cũ.

---

## 1. Điều kiện cần

| Thành phần | Bắt buộc | Ghi chú |
|---|:--:|---|
| LLM service (`/v1/chat/completions`) | ✅ | sinh đáp án **và** làm giám khảo relevance |
| Embedding service | ✅ | **PHẢI cùng dòng bge-m3 đã index `cve_kb`** — sai model thì recall ≈ 0 |
| Reranker service (`/v1/rerank`) | ✅ | |
| Qdrant (`cve_kb`) | ✅ | thường `http://localhost:6333` |
| BM25 artifacts | ✅ | `$DATA_ROOT/knowledge_base/bm25_cve_kb/` — thiếu thì tụt về dense-only |
| Backend agent | ✅ | **secai-agent-gateway `:8131`** (đang deploy — dùng mặc định) hoặc backend cũ opencode `:4096` (basic auth bật) — xem 2 bộ biến bên dưới |
| Key judge faithfulness | ✅ | đặt vào `OPENAI_API_KEY` (giá trị là key DeepSeek) |

Khai báo trong `source/eval/kpi/.env.bench` (copy từ `.env.bench.example`). **Không commit file này.**

### 1a. Bộ biến cho backend hiện đang deploy — secai-agent-gateway (dùng mặc định)

FastAPI service chạy Docker (container `secai-docker-gateway`), endpoint SSE
`POST /ai/chat/stream` với body `{"message": "<câu hỏi>"}` (**không phải** `/session` như
opencode) — bỏ `user_id` (hoặc gửi `"anonymous"`) để mỗi câu hỏi có session độc lập, tránh
lẫn lịch sử hội thoại. Không cần `OPENCODE_SERVER_USERNAME`/`PASSWORD`, không cần
`ACC_AGENT=build`.

```
LLM_BASE_URL=http://<vast-host>:<port>/v1   LLM_API_KEY=sk-secai2026   LLM_MODEL=DuyTa/Cyber-F1-AWQ
EMBED_URL=http://<vast-host>:<port>         EMBED_MODEL=DuyTa/sec-embedding
RERANK_URL=http://<vast-host>:<port>        RERANK_MODEL=DuyTa/sec-rerank
QDRANT_URL=http://127.0.0.1:6333            QDRANT_COLLECTION=cve_kb
ACC_BASE=http://127.0.0.1:8131              ACC_PROVIDER=secai-gateway
ACC_MODEL=DuyTa/Cyber-F1-AWQ                ACC_AGENT=chat
RAGAS_JUDGE_BASE_URL=<giống LLM_BASE_URL>   RAGAS_JUDGE_API_KEY=sk-secai2026   RAGAS_JUDGE_MODEL=DuyTa/Cyber-F1-AWQ
DATA_ROOT=/home/ict/secai-88/source/data
```

`ACC_BASE` là `http://127.0.0.1:8131` (hoặc host:port thật của bạn), trỏ vào repo
`secai-agent-gateway`. Lưu ý: `RAGAS_JUDGE_*` trỏ **cùng model** đang được đánh giá là một lỗ hổng
phương pháp luận thật sự (không có key/giám khảo độc lập) — chỉ làm vậy khi không có lựa chọn khác,
và **phải ghi rõ trong báo cáo mỗi lần**.

### 1b. Bộ biến cho backend cũ — opencode `:4096` (legacy)

> ⚠ **(backend cũ — opencode :4096)** — chỉ dùng nếu bạn dựng lại server opencode này; các
> script `bench_answers_concurrent.py`, `gen_answers.py`, `perf_load.py` gốc vẫn chạy đúng cho
> backend này, không có gì hỏng — chỉ là **không phải backend đang được deploy** nữa.

Tên biến: `LLM_BASE_URL` `LLM_API_KEY` `LLM_MODEL` `EMBED_URL` `EMBED_MODEL` `RERANK_URL` `RERANK_MODEL`
`QDRANT_URL` `QDRANT_COLLECTION` `ACC_BASE` `ACC_PROVIDER` `ACC_MODEL` `ACC_AGENT=build`
`OPENCODE_SERVER_USERNAME` `OPENCODE_SERVER_PASSWORD` `RAGAS_JUDGE_BASE_URL` `RAGAS_JUDGE_MODEL`
`OPENAI_API_KEY` `DATA_ROOT`. API là `/session` + `/session/{id}/message`, repo root ví dụ
`/root/secai-88`.

---

## 2. Chạy đo

### 2a. Backend hiện đang deploy — secai-agent-gateway (dùng mặc định)

```bash
cd /home/ict/secai-88   # ví dụ — thay bằng repo root thật của bạn

# nhóm A · 11 KPI chất lượng — dùng adapter gateway (SSE /ai/chat/stream, trích tool_call thật)
../02-GEN-TEST/chay_eval_gateway_moi.sh --dataset <dir>
```

`chay_eval_gateway_moi.sh` gọi `02-GEN-TEST/adapters/bench_answers_gateway.py` thay cho
`source/eval/kpi/scripts/bench_answers_concurrent.py` gốc — adapter này nói chuyện với
`POST /ai/chat/stream` của gateway, trích **tool_call thật đã được gọi** (bắt buộc để chấm
`must_use_tool`; endpoint `/chat_no_stream` của gateway **không** dùng được cho việc này vì nó chỉ
lộ ra các tool *khả dụng* của profile, không phải tool *thực sự đã gọi*).

Ví dụ đầy đủ một lần chạy 19 KPI thật trên gateway này (2026-08-26) nằm ở
`KPI-VA-GEN-TEST/KET-QUA/2026-08-26/` (`bao-cao-so-sanh.md`, `KPI-CAN-DIEU-CHINH.md`, cộng raw-logs
theo `group-A-quality/`, `group-B-perf/`, `group-C-models/`) — xem đó làm ví dụ tham chiếu.

Nhóm B (hiệu năng) dùng `02-GEN-TEST/adapters/perf_load_gateway.py` thay cho `perf-bench/perf_load.py`
gốc. **Lưu ý quan trọng khác opencode**: gateway tự giới hạn **15 request/60s theo IP** trên
`/ai/chat(/stream)` (src/secai_gateway/api/middleware.py:route_limit, state/ratelimit.py — cơ chế an
toàn production thật, vì gateway còn phục vụ traffic khác). Hai target hợp đồng của nhóm B,
`R-PERF-CCU` (~30 luồng đồng thời) và `R-PERF-RPS` (~0.6 RPS = 36 req/phút), **cả hai đều vượt trần
15 req/60s theo cấu trúc** — không có cách chỉnh concurrency phía client nào thỏa được rate limit
**và** cả hai target cùng lúc từ một IP nguồn. Ba cách xử lý:
  - **(a) đo ở concurrency giảm** (vd. `--concurrency 10`) và ghi rõ `R-PERF-CCU`/`R-PERF-RPS` là
    "chưa đo ở tải hợp đồng, chỉ tuân thủ rate limit" — đây là cách đã làm thật trong lần chạy
    2026-08-26;
  - (b) tạm nâng rate limit của gateway cho cửa sổ benchmark rồi trả lại sau (thay đổi cấu hình
    production thật, cần xin phép rõ ràng mỗi lần, không bao giờ để nâng vĩnh viễn);
  - (c) bỏ qua đo trực tiếp nhóm B, chỉ ghi lại đây là điểm nghẽn.

Adapter đã tự pace ~12-13 req/60s (sliding-window dùng chung giữa các worker thread) cộng
retry-with-backoff khi vẫn dính 429 — xem mục 7 bên dưới.

> ⚠ **Lỗi chấm điểm có sẵn, không liên quan gateway mới**: `build_perf_summary.py` cho
> `R-PERF-TPS`/`R-PERF-TTFT`/`R-PERF-RPS`/`R-PERF-CCU` (4 trong 5 mã nhóm B) `pass=True` chỉ vì
> có giá trị, **không** thực sự so với ngưỡng hợp đồng (chỉ `R-CTRL-RESP15` là gate thật). Lỗi
> này có từ trước, tồn tại độc lập với backend nào — đừng tin trường `pass` thô của nhóm B, tự
> so `--md`/`.json` với `BANG-KPI.md`.

**Ví dụ kết quả thật từ lần chạy 2026-08-26** (KET-QUA/2026-08-26/, coi là ví dụ minh hoạ chứ
không phải con số đảm bảo tái lập được): 12/19 KPI đạt ngưỡng hợp đồng thật. Hai phát hiện thật
đáng chú ý (không phải do rate-limit):
  - `R-KPI-FAITH98` tự chấm (không có key giám khảo độc lập), đo được 96.71% so ngưỡng >98% —
    khớp với ghi chú sẵn có của bộ KPI rằng 98% có thể không thực tế cho hệ RAG.
  - `R-CTRL-RESP15`/`R-PERF-TPS` lộ khoảng cách ~11x giữa throughput endpoint LLM thô (nhóm C,
    88.5 tok/s trực tiếp) và tốc độ hoàn thành mỗi câu hỏi đầu-cuối qua gateway (~8 tok/s hiệu
    dụng, p50 response time 52.6s so ngưỡng hợp đồng 15s) — nhiều khả năng do overhead vòng lặp
    agent/tool-call bên trong gateway, **không phải** do rate limit (TTFT vẫn nhanh, ~1.6s p50)
    và không giải thích được bởi concurrency thấp của phương pháp nhóm B (concurrency thấp hơn
    không lẽ ra làm chậm latency của từng request riêng lẻ). Đây là một đầu mối tối ưu hiệu năng
    thật cho team gateway, tách biệt với phát hiện rate-limit ở trên.

### 2b. Backend cũ — opencode `:4096` (legacy)

> ⚠ **(backend cũ — opencode :4096)**: giữ lại vì có người vẫn có thể đang chạy backend này;
> không có gì hỏng ở các lệnh dưới đây, chỉ là chúng không trỏ vào backend đang deploy nữa.

```bash
cd /root/secai-88

# thử nhanh trước (vài phút) — xác nhận wiring, KHÔNG dùng làm bằng chứng
bash run_eval_all.sh --limit 20

# nhóm A · 11 KPI chất lượng, full 1000 câu (nhiều giờ)
bash run_eval_all.sh

# nhóm B · 5 KPI hiệu năng   |   nhóm C · 3 KPI throughput
./bench-perf.sh
./bench-models.sh

# hoặc cả 19 trong một lệnh
./bench-all.sh
```

Kết quả: `source/eval/kpi/runs/<ngày-giờ>-evalall/` (nhóm A) và `runs/<ngày>/{perf,models}/` (B, C).
Cuối nhóm A in bảng `X/11 ĐẠT` + kiểm đủ deliverable.

Cờ hữu ích: `--limit N` · `--workers N` (mặc định 2; cao hơn dễ làm backend treo) ·
`--src RAW_DIR` (**chấm lại** trên đáp án có sẵn, không gọi lại LLM) · `--no-faith`
(bỏ faithfulness khi chưa có key).

> ⚠ **`--dataset DIR` KHÔNG dùng được cho bộ đề mới.** `gen_answers.py:21` hard-code đường dẫn
> bộ đề và không có cờ `--dataset`; với bộ đề khác thì `answers.jsonl` ra **RỖNG** và 7 KPI
> dựa-trên-đáp-án im lặng trả 0/null. Muốn chạy trên bộ đề mới (kiểm chứng độc lập), dùng
> `../02-GEN-TEST/chay_eval_bo_moi.sh --dataset <dir>` — chạy đúng 9 bước, chỉ thay bước 2.

---

## 3. So sánh 2 phiên bản

```bash
python3 KPI-VA-GEN-TEST/01-KPI/compare_kpi.py \
    --a source/eval/kpi/runs/<run-mới> \
    --b kpi_results/quality/raw-logs \
    --ten-a "v2 sau fine-tune" --ten-b "bản công bố" \
    --md bao-cao-so-sanh.md --xlsx bao-cao-so-sanh.xlsx
```

Công cụ sẽ:
- In bảng `mã | ngưỡng | A | B | Δ | Δ% | đạt A | kết luận` theo 3 nhóm.
- Áp **luật sai lệch ≤ 5%** của `R-EVAL-INDEP`: `CHẤP NHẬN` / `TỐT HƠN ▲` / `VƯỢT NGƯỠNG SAI LỆCH`.
  `Δ%` tính theo **hướng xấu đi** — với Hallucination/TTFT/Response thì giảm mới là tốt,
  công cụ tự đảo dấu theo `higher_is_better` trong registry.
- **Tự chấm lại** 4 KPI mà script gốc không ép ngưỡng (cạm bẫy #1) thay vì tin trường `pass`.
- **Cảnh báo so sánh khập khiễng**: khác model / endpoint / dataset / commit, hoặc SAST một bên
  majority-2/3 còn bên kia 1 lượt.
- Xuất `.xlsx` đúng hình dạng `kpi_results/quality/reports/baseline-comparison.xlsx` để nộp khách.

Thêm `--strict` để exit code ≠ 0 khi có KPI vượt ngưỡng (dùng trong CI).

---

## 4. Điều kiện để phép so sánh là công bằng

Đổi bất kỳ mục nào dưới đây thì **phải ghi vào báo cáo**, nếu không con số không so được:

| Yếu tố | Vì sao ảnh hưởng |
|---|---|
| **Model embedding** | Lệch dòng bge-m3 đã index `cve_kb` → Hit@10 sập, kéo theo P@1 và faithfulness |
| **Model LLM** | Đổi luôn **giám khảo** của `R-KPI-RELEV90` (cạm bẫy #5) |
| **Model judge faithfulness** | `RAGAS_JUDGE_MODEL` khác → thang điểm khác |
| **Nội dung KB** | Ingest hằng ngày làm `cve_kb` đổi → gold-contexts và entity grounding đổi theo |
| **Bộ test** | Bộ khác thì Δ phản ánh **độ khó của đề**, không phải chất lượng hệ thống |
| **Cách chấm SAST** | majority-2/3 vs 1 lượt — chênh tới 4× (cạm bẫy #3) |
| **Vị trí máy đo** | Throughput gồm độ trễ mạng (cạm bẫy #4) |
| **Số mẫu** | `--limit`/`--faith-sample` khác nhau → sai số khác nhau |

`run-manifest.json` của mỗi lần chạy ghi lại model + endpoint + commit — `compare_kpi.py` đọc file này
để đối chiếu tự động.

---

## 5. Chấm lại nhanh, không gọi lại LLM

Khi chỉ sửa **cách chấm** (không đổi hệ thống), đừng sinh lại đáp án — vừa tốn vừa gây nhiễu:

```bash
bash run_eval_all.sh --src source/eval/kpi/runs/<run-cũ>/raw-logs   # chấm lại toàn bộ
bash RUN-KPI-CHUAN/02-cham-lai-nhanh-task-kpi.sh                    # chỉ SAST/Exploit/Remediation
```

---

## 6. Sản phẩm bàn giao mỗi lần chạy

`run_eval_all.sh` tự kiểm ở bước 10 và in `thiếu N deliverable`:

- **Báo cáo**: `reports/{ai-task-kpi,embedding,llm-kpi,reranking,performance,sanitization}-report.md`
- **Radar**: `reports/radar-data.json` + `radar.svg` (stdlib) hoặc `radar.png` (cần matplotlib)
- **Raw logs** (True/Predicted/Score từng mẫu — yêu cầu `R-EVAL-RAWLOGS`): 9 file `.jsonl`
- **Tổng hợp**: `raw-logs/kpi-summary.json` · `kpi-summary.xlsx` · `run-manifest.json`
- **Tóm tắt**: `summary.md`
- **Phân tích lỗi**: `error-analysis-and-improvement-plan.md` · `false-positive-analysis.md`
  · `false-negative-analysis.md` · `improvement-plan.md` · `failed-samples.xlsx`

Máy thiếu `matplotlib`/`openpyxl` vẫn đủ deliverable nhờ `make_deliverables.py` (thuần stdlib,
tạo `.xlsx` thật + `radar.svg` thật).

---

## 7. Sự cố thường gặp

| Triệu chứng | Nguyên nhân |
|---|---|
| `Connection refused` ở bước answer-gen | Service GPU chết — bước 0 preflight in mã HTTP từng endpoint |
| Hit@10 thấp bất thường | `EMBED_MODEL` không phải dòng bge-m3 đã index |
| Faithfulness toàn `null`/0% | Thiếu `OPENAI_API_KEY` (key DeepSeek) |
| SAST lệch 4× so với lần trước | Trộn majority-2/3 với 1 lượt (cạm bẫy #3) |
| Backend treo khi nhiều luồng | Giảm `--workers`; answer-gen có resume, chạy lại bỏ qua câu đã xong |
| `radar.png`/`.xlsx` lỗi | Thiếu `matplotlib`/`openpyxl` → vẫn có `.md` + `.json` + `.svg` |
| Nhiều câu lỗi `HTTP 429` / rate limit exceeded (**gateway**) | Rate limiter của secai-agent-gateway: **15 request/60s theo IP** trên `/ai/chat(/stream)`. Fix: dùng `02-GEN-TEST/adapters/` (đã có self-pacing ~12-13 req/60s + retry-with-backoff sẵn), hoặc giảm `--workers`/`--concurrency` |
| Nhiều câu lỗi `HTTP 422` (**gateway**) | `require_public_input()` của gateway chặn message có pattern giống secret — câu SAST/remediation nhúng source code lỗ hổng giả lập hay dính. Fix: adapter tự bắt 422, ghi câu đó là "blocked" rồi chạy tiếp; không phải lỗi wiring |
