# Hướng dẫn sinh bộ test độc lập

Bộ công cụ này phục vụ tiêu chí hợp đồng **`R-EVAL-INDEP` — Kiểm chứng độc lập**
(`source/docs/task-AI-requirements.md:93-94`, chi tiết ở *Tài liệu nghiệm thu(2).xlsx*
sheet "Chi tiêu kỹ thuật" dòng 537 và 556):

> Phối hợp Chủ đầu tư tổng hợp **01 bộ test mới cho mỗi loại mô hình** (Embedding, Reranking,
> LLM), **không trùng tập huấn luyện và không trùng bộ KPI chính**; chạy pipeline KPI tương ứng;
> xuất raw logs + KPI summary + **báo cáo so sánh với baseline đã nghiệm thu**; tính sai lệch;
> **≤ 5% → đạt**; **> 5% → nhà thầu fine-tune/điều chỉnh miễn phí rồi chạy lại**.

---

## (a) Thu thập từ nguồn nào

Ba nguồn — chi tiết, license và cách lấy ở **`NGUON-DU-LIEU.md`**. Tóm tắt:

| Nguồn | Độc lập với KB? | Cần mạng? | Dùng khi |
|---|:--:|:--:|---|
| `qdrant` — KB nội bộ `cve_kb` | ❌ | Không | Cần bộ lớn (1000 câu), nhanh, offline |
| `public` — NVD · CISA KEV · Exploit-DB | ✅ | Có | Khách nghi ngờ "tự ra đề tự chấm" |
| `customer` — CSV/XLSX khách hàng | ✅ | Không | Khách có danh mục CVE riêng |

**Khuyến nghị cho một đợt nghiệm thu độc lập:** lấy **nền là `customer`** (khách chốt phạm vi),
**bù cho đủ số bằng `public`** (bên thứ ba, phản bác được cáo buộc tự ra đề), và chỉ dùng
`qdrant --exclude-pool` khi cần nhanh hoặc không có mạng.

---

## (b) Hướng dẫn gen

### Bước 1 — sinh bộ đề

```bash
# Đường dẫn dưới đây (/home/ict/secai-88) là ví dụ đã kiểm chứng chạy được thật; đường dẫn
# thật của bạn tuỳ nơi checkout repo eval — bộ công cụ này phải được copy vào NẰM TRONG repo đó,
# vì gen_testset.py có `REPO = HERE.parents[1]` phải trỏ đúng root repo eval thì --exclude-pool
# mới tìm thấy source/eval/datasets-acceptance.
cd /home/ict/secai-88/KPI-VA-GEN-TEST/02-GEN-TEST

# A · từ KB nội bộ, loại hết CVE đã dùng trong bộ nghiệm thu cũ
python3 gen_testset.py --source qdrant --n 1000 --out ./testset-v2 --exclude-pool --seed 7

# B · từ nguồn công khai (nên đặt NVD_API_KEY để không bị giới hạn 5 req/30s)
export NVD_API_KEY=...
python3 gen_testset.py --source public --n 300 --feeds nvd,kev --out ./testset-v2 --exclude-pool

# C · từ bảng khách hàng (chạy được offline; --enrich mới cần mạng)
python3 gen_testset.py --source customer --in bang-khach-hang.csv --out ./testset-v2 --enrich
```

Tham số chính:

| Cờ | Ý nghĩa |
|---|---|
| `--n` | Tổng số câu. Hợp đồng yêu cầu **1000** (`R-EVAL-TESTSET`) |
| `--out` | Thư mục đầu ra |
| `--exclude-pool` | **Nên luôn bật.** Loại mọi CVE đã dùng ở bộ nghiệm thu hiện hành |
| `--seed` | Cùng seed + cùng nguồn → **sinh lại y hệt** (tái lập được) |
| `--enrich` | (customer) tra Qdrant rồi NVD để bù ground truth còn thiếu |
| `--use-llm` | Diễn đạt lại câu hỏi cho tự nhiên hơn (xem cảnh báo dưới) |
| `--skip-verify` | Bỏ xác minh zero-hit cho CVE giả (khi không có Qdrant) |
| `--feeds` | (public) `nvd,kev,exploitdb` |

Đầu ra:

```
testset-v2/
├── acceptance_indep_dinh_danh.jsonl    câu định danh/phân loại CVE  (40%)
├── acceptance_indep_khac_phuc.jsonl    câu tư vấn khắc phục         (40%)
├── acceptance_indep_tinh_huong.jsonl   câu suy luận + câu bẫy       (17% + 3%)
├── vuln-samples/                       mã nguồn có lỗi cho câu SAST (ghi thật ra đĩa)
└── _meta/
    ├── pool.jsonl                      bản ghi CVE nguồn (validate tự tìm)
    └── manifest.json                   truy vết: nguồn, seed, phân bố, số CVE
```

> **Tiền tố `acceptance_` là bắt buộc, không được đổi.** `retrieval_kpi.py:123` chỉ quét
> `acceptance_*.jsonl`; đặt tên khác thì `R-KPI-EMB96` và `R-KPI-RERANK90` **lặng lẽ ra 0 mẫu**
> mà không báo lỗi. Tương tự, `pool.jsonl` phải nằm trong `_meta/` chứ không để cạnh bộ đề,
> vì `bench_answers_concurrent.py:84` quét `*.jsonl` và sẽ nuốt nhầm nó thành câu hỏi.

Cơ cấu luôn bám hợp đồng: bucket **40/40/17/3**, difficulty **40/30/20/10** (tính theo từng
bucket), mỗi CVE dùng tối đa **1 lần**, câu bẫy dùng CVE giả **đã xác minh zero-hit** trên KB.

> **Về `--use-llm`:** chỉ **viết lại câu chữ**, tuyệt đối không đổi ground truth. Bản viết lại
> chỉ được nhận nếu **mọi mã định danh bắt buộc còn nguyên**; không thì giữ câu gốc. Cần
> `LLM_BASE_URL` + `LLM_MODEL`. Mặc định TẮT vì template tất định thì tái lập được và dễ giải trình.

### Bước 2 — cổng chất lượng (BẮT BUỘC)

```bash
python3 validate_testset.py ./testset-v2
```

(`--pool` là tuỳ chọn — cổng tự tìm `_meta/pool.jsonl`.)

Phải in `✓ ĐẠT` mới được dùng. Exit code ≠ 0 nghĩa là chưa đạt.

Cổng kiểm 21 mục: trường bắt buộc · giá trị hợp lệ · trùng id · trùng nội dung câu hỏi ·
độ dài tối thiểu · định dạng CVE · câu SAST có file thật + có `must_find_cwe` · PoC query không
chứa ký tự shell · có `template_id` · **mọi CVE nhắc tới phải có thật** · **CVE trong câu bẫy
phải KHÔNG có thật** · câu tư vấn không gắn `capability=remediation` · phủ ≥5 câu/capability ·
phân bố bucket ±2 điểm · phân bố difficulty ±4 điểm · ≥10 template/bucket · `must_use_tool`
chỉ đặt cho câu có CVE-id.

> Đây là bản **Python port** của cổng gốc `source/eval/self-eval/acceptance-quality.ts`.
> Cổng gốc chạy bằng `bun` mà **`bun` không được cài trên máy giao hàng** nên hiện không chạy được.
> Bản Python giữ nguyên bộ kiểm tra — đã đối chứng: nó **PASS đúng bộ 1000 câu nghiệm thu hiện có**.

---

## (c) Phối hợp khách hàng — quy trình 6 bước theo xlsx dòng 556

| # | Bước | Ai làm | Sản phẩm |
|---|---|---|---|
| 1 | Thống nhất **01 bộ test mới**, không trùng tập huấn luyện và bộ KPI chính | Hai bên | Biên bản chốt phạm vi + số lượng |
| 2 | Khách điền `MAU-KHACH-HANG.csv` (hoặc chốt dùng nguồn công khai) | Chủ đầu tư | `bang-khach-hang.csv` |
| 3 | Sinh + qua cổng chất lượng | Nhà thầu | `testset-v2/` + `manifest.json` + log `✓ ĐẠT` |
| 4 | **Khách rà soát và chốt ground truth** trước khi chạy | Chủ đầu tư | Bộ đề đã ký |
| 5 | Chạy pipeline KPI trên bộ mới | Nhà thầu | raw logs + `kpi-summary.json` |
| 6 | So sánh với baseline, tính sai lệch | Hai bên | `bao-cao-so-sanh.md/.xlsx` |

**Kết luận:** sai lệch **≤ 5%** → đạt. **> 5%** → nhà thầu fine-tune/điều chỉnh **miễn phí**
rồi chạy lại từ bước 5.

Lưu ý để tránh tranh cãi về sau:
- **Bước 4 là bắt buộc.** Ground truth do công cụ suy ra từ nguồn; khách phải xác nhận thì con số
  cuối mới có giá trị pháp lý.
- Hợp đồng đòi **01 bộ cho mỗi loại mô hình** (Embedding, Reranking, LLM). Cùng một bộ 1000 câu
  phục vụ được cả ba, vì `metadata.cve_id` là gold dùng chung cho retrieval (Embedding/Reranking)
  và cho chấm đáp án (LLM). Nếu khách muốn tách riêng thì sinh 3 lần với `--seed` khác nhau.
- Chốt trước **baseline nào** dùng để so (xem `../01-KPI/BANG-KPI.md` — repo đang có **3 baseline
  mâu thuẫn nhau**). Hợp đồng ghi *"so với chỉ số công bố"* → mặc định là `kpi_results/`.

---

## (d) Đưa bộ mới vào chấm và so sánh

Backend đang chạy thật hiện nay là **secai-agent-gateway** (FastAPI, container
`secai-docker-gateway`, `POST /ai/chat/stream`) — dùng nhánh **gateway** dưới đây làm mặc định.
Nhánh **backend cũ** (opencode server `:4096`, `/session` API) chỉ còn dùng nếu ai đó dựng lại
đúng backend đó; script của nó vẫn chạy tốt cho backend cũ, không hỏng gì, chỉ là không còn khớp
với hạ tầng đang deploy.

### Nhánh mới — gateway (mặc định, khuyến nghị)

```bash
# 1. chạy pipeline KPI trên bộ test mới, qua secai-agent-gateway
cd /home/ict/secai-88/KPI-VA-GEN-TEST/02-GEN-TEST
./chay_eval_gateway_moi.sh --dataset ./testset-v2

# 2. so sánh với baseline đã công bố + áp luật ≤5% (giống hệt bước so sánh của nhánh cũ)
cd /home/ict/secai-88
python3 KPI-VA-GEN-TEST/01-KPI/compare_kpi.py \
    --a source/eval/kpi/runs/<run-vừa-chạy> \
    --b kpi_results/quality/raw-logs \
    --ten-a "bộ test độc lập" --ten-b "chỉ số công bố" \
    --md bao-cao-so-sanh.md --xlsx bao-cao-so-sanh.xlsx
```

`chay_eval_gateway_moi.sh` dùng `02-GEN-TEST/adapters/bench_answers_gateway.py` thay cho
`source/eval/kpi/scripts/bench_answers_concurrent.py` (bản cũ nói ở dưới) — adapter này gọi thẳng
`POST /ai/chat/stream` của gateway (SSE, body `{"message": ...}`, bỏ trống `user_id` để mỗi câu
là 1 session độc lập, không lẫn lịch sử hội thoại) và đọc `tool_call` thật trong luồng SSE để
chấm `must_use_tool` — endpoint `/chat_no_stream` không dùng được cho việc này vì nó chỉ liệt kê
tool nào profile *có sẵn*, không phải tool nào *thực sự được gọi*. Group B (perf) tương tự dùng
`02-GEN-TEST/adapters/perf_load_gateway.py` thay `perf-bench/perf_load.py`.

> **Rate limit tự áp:** gateway giới hạn **15 request/60s theo IP client** trên `/ai/chat(/stream)`
> (`src/secai_gateway/api/middleware.py:route_limit`, `state/ratelimit.py` — cơ chế bảo vệ thật vì
> gateway còn phục vụ traffic khác). Cả hai adapter tự giãn nhịp về **~12-13 req/60s** (sliding
> window dùng chung giữa các worker thread) và tự retry-with-backoff khi vẫn dính 429. Vì vậy
> **chạy hết 1000 câu tốn khoảng 1-4+ giờ** — phần lớn thời gian là giãn nhịp/sinh câu trả lời,
> **không phải lỗi treo**. Trước khi thêm giãn nhịp này, một smoke test đã dính ~25% lỗi thuần vì
> rate limit — xem ví dụ thật đã chạy ở `../KET-QUA/2026-08-26/`.
>
> **R-PERF-CCU (~30 luồng đồng thời) và R-PERF-RPS (~0.6 RPS = 36 req/phút) đều vượt trần 15
> req/60s theo cấu trúc** — không cách nào chỉnh concurrency phía client để vừa thoả rate limit
> vừa đạt cả hai ngưỡng hợp đồng này từ 1 IP nguồn. Ba cách xử lý khi chạy lại Group B:
> (a) đo ở concurrency thấp hơn (vd. `--concurrency 10`) và ghi rõ R-PERF-CCU/RPS là "chưa đo ở
> tải hợp đồng, chỉ tuân thủ rate-limit" — đây là cách đã làm thật trong lần chạy 2026-08-26;
> (b) tạm nâng rate limit của gateway cho cửa sổ benchmark rồi trả lại sau — đây là thay đổi
> cấu hình production thật, **mỗi lần phải xin phép rõ ràng**, không được để nâng quên; (c) bỏ đo
> Group B trực tiếp, chỉ ghi lại đây là điểm nghẽn.

Ví dụ thật đã chạy đủ 19 KPI trên backend mới nằm ở **`../KET-QUA/2026-08-26/`**
(`bao-cao-so-sanh.md`, `KPI-CAN-DIEU-CHINH.md`, cùng raw logs ở `group-A-quality/`,
`group-B-perf/`, `group-C-models/`) — dùng làm ví dụ tham chiếu, **không phải con số đảm bảo tái
lập y hệt**. Kết quả lần đó: **12/19 KPI đạt ngưỡng hợp đồng thật**. Hai phát hiện đáng chú ý
(không phải do rate limit):
- `R-KPI-FAITH98` tự chấm bằng chính model đang test (không có key giám khảo độc lập), ra
  **96.71%** so ngưỡng >98% — khớp với ghi chú sẵn có trong tài liệu KPI rằng 98% có thể phi thực
  tế cho hệ RAG.
- `R-CTRL-RESP15`/`R-PERF-TPS` lộ khoảng cách **~11 lần** giữa throughput endpoint LLM thô
  (Group C, 88.5 tok/s gọi trực tiếp) và tốc độ hoàn tất mỗi câu hỏi qua gateway
  (~8 tok/s hiệu dụng đầu-cuối, p50 response time **52.6s** so trần hợp đồng 15s) — nhiều khả
  năng do overhead vòng lặp agent/tool-call trong gateway (TTFT vẫn nhanh, ~1.6s p50), không phải
  do rate limit và không phải do concurrency thấp của Group B (concurrency thấp hơn không lẽ làm
  latency từng request chậm đi). Đây là đầu mối tối ưu hiệu năng thật cho team gateway, tách biệt
  với phát hiện rate-limit ở trên.

> ⚠ **Lỗi chấm có sẵn, không liên quan backend mới:** `build_perf_summary.py` cho
> R-PERF-TPS/TTFT/RPS/CCU (4/5 mã Group B) `pass=True` chỉ vì **có giá trị**, chưa hề so với
> ngưỡng hợp đồng thật — trong 5 mã Group B chỉ `R-CTRL-RESP15` là gate thật. Luôn tự so số Group
> B với ngưỡng ở `../01-KPI/BANG-KPI.md`, đừng tin thẳng cột `pass`.

### Nhánh cũ — backend opencode (backend cũ, chỉ dùng nếu dựng lại đúng hạ tầng đó)

```bash
# 1. chạy pipeline KPI trên bộ test mới
cd /home/ict/secai-88/KPI-VA-GEN-TEST/02-GEN-TEST
./chay_eval_bo_moi.sh --dataset ./testset-v2

# 2. so sánh với baseline đã công bố + áp luật ≤5%
cd /home/ict/secai-88
python3 KPI-VA-GEN-TEST/01-KPI/compare_kpi.py \
    --a source/eval/kpi/runs/<run-vừa-chạy> \
    --b kpi_results/quality/raw-logs \
    --ten-a "bộ test độc lập" --ten-b "chỉ số công bố" \
    --md bao-cao-so-sanh.md --xlsx bao-cao-so-sanh.xlsx
```

> ### ⚠ Vì sao KHÔNG dùng thẳng `bash run_eval_all.sh --dataset <dir>`
>
> `gen_answers.py` (bước 2 của `run_eval_all.sh`) **hard-code đường dẫn bộ đề**
> (`gen_answers.py:21`) và **không có cờ `--dataset`**. Nó nối kết quả với bộ đề theo `id`,
> gặp id lạ thì bỏ qua. Với bộ đề mới, **không id nào khớp → `answers.jsonl` RỖNG**, kéo theo
> **7 KPI dựa-trên-đáp-án** (Faithfulness · Relevance · Entity · Hallucination · SAST ·
> Exploit · Remediation) im lặng trả 0/null mà không báo lỗi.
>
> Đã kiểm chứng: đưa `results.jsonl` chứa id `ind_dd_0000` vào `gen_answers.py` → in
> `answers.jsonl: 0 dòng`.
>
> `chay_eval_bo_moi.sh` chạy đúng 9 bước như `run_eval_all.sh`, chỉ thay bước 2 bằng
> `lam_answers.py` (bản có `--dataset`), các bước còn lại gọi nguyên script gốc. Nó cũng
> **chạy cổng chất lượng trước** để không tốn tiền gọi LLM trên bộ đề hỏng.
>
> Đây là lỗi có sẵn của repo, không phải của bộ công cụ này. Muốn sửa tận gốc thì thêm
> `--dataset` vào `gen_answers.py` — nhưng file đó nằm ngoài phạm vi bàn giao nên **không sửa**.

`compare_kpi.py` in bảng 19 KPI kèm `Δ%`, tự kết luận `CHẤP NHẬN` / `TỐT HƠN` /
`VƯỢT NGƯỠNG SAI LỆCH`, và **cảnh báo khi hai lần chạy không so sánh được công bằng**
(khác model, khác endpoint, hoặc SAST một bên chấm majority-2/3 còn bên kia 1 lượt).

> ⚠ Nhớ đọc mục **"9 cạm bẫy khi so sánh 2 phiên bản"** trong `../01-KPI/BANG-KPI.md` trước khi
> kết luận. Đáng chú ý nhất: **4/8 KPI hiệu năng không hề được script so với ngưỡng**, và
> **SAST có 3 con số mâu thuẫn trong repo (85 / 100 / 21.43)** tuỳ cách chấm.

---

## Sự cố thường gặp

| Triệu chứng | Nguyên nhân & cách xử |
|---|---|
| `Qdrant KHÔNG kết nối được` | Đặt `QDRANT_URL`, hoặc chuyển `--source public` / `--source customer` |
| Sinh ra ít câu hơn `--n`, có dòng *CẢNH BÁO … thu nhỏ các nhóm* | Nguồn không đủ CVE (mỗi CVE dùng 1 lần). Tăng `--n` ở nguồn hoặc bổ sung CVE |
| `public` chỉ ra vài bản ghi đạt chất lượng | Thiếu `NVD_API_KEY` → bị giới hạn 5 req/30s nên lấy được ít trang |
| `bucket_distribution` / `difficulty_distribution` không đạt | Nguồn quá ít bản ghi; bổ sung dữ liệu rồi sinh lại |
| `template_diversity` < 10 | Bộ quá nhỏ nên không đủ template. Tăng `--n` |
| Cổng báo `grounded_cve` | Quên `--pool`, hoặc CVE trong câu không có ở nguồn |
| `sast_file_exists` | Đã xoá/di chuyển thư mục `vuln-samples/` — sinh lại |
| Chạy `chay_eval_gateway_moi.sh` thấy vài câu SAST/khắc phục bị `HTTP 422` | Bình thường — câu hỏi nhúng mã nguồn lỗi khiến cổng `require_public_input()` của gateway nghi là secret/dữ liệu nhạy cảm. `bench_answers_gateway.py` tự bắt, ghi câu đó là "blocked" rồi chạy tiếp — không phải bug cần săn |
