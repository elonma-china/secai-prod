# Pipeline MLOps trọn vòng đời

Điều phối (orchestration) toàn bộ vòng đời tự động của 3 model (embedding/reranker/llm):

```
Thu thập CVE ──▶ Synthetic Data Generation ──▶ Phát hiện suy giảm (Drift Detection)
 (đã có,daily)      (giai đoạn 2)                  (giai đoạn 3, đo model ĐANG SERVING)
                                                          │
                                    NO_FINETUNE_REQUIRED ─┤─ FINETUNE_REQUIRED
                                    (giữ nguyên, HẾT)     │
                                                          ▼
                                    Tự động Finetune/DPO (giai đoạn 4, 4b)
                                                          │
                                                          ▼
                                    Quantize AWQ (giai đoạn 5)
                                                          │
                                                          ▼
                                    Push candidate lên HF (giai đoạn 6)
                                                          │
                                                          ▼
                                    Requalify trên GPU thật (giai đoạn 6b, 6c)
                                                          │
                              PASS ────────────────────────────────── FAIL
                                │                                       │
                                ▼                                       ▼
                  Promote: approve baseline +                Hold: GIỮ NGUYÊN model
                  push production + redeploy                 hiện tại đang serving,
                  gateway (giai đoạn 7)                       không đụng gì production
```

Không tự triển khai lại thu thập dữ liệu hay serving — chỉ **gọi tới** các phân hệ đã có sẵn
trong repo qua `configs/pipeline.yml` (xem README gốc `../README.md`). Bảng dưới map từng câu
trong yêu cầu hợp đồng sang đúng script.

| Yêu cầu hợp đồng | Thực hiện ở |
|---|---|
| Thu thập dữ liệu CVE | `secai/scripts/sync_autoscan_to_kb.py` (cron 03:00 hàng ngày) — KHÔNG thuộc gói này, chỉ đọc |
| Tổng hợp evaluation dataset từ CVE mới kể từ lần finetune gần nhất (Synthetic Data Generation) | `scripts/01_collect_new_cves.py` + `scripts/02_build_eval_set.sh` |
| Đánh giá model trên dataset mới bằng metric hợp đồng | `scripts/03_run_drift_check.sh` → `finetune/scripts/{eval_embedding,eval_reranker,eval_llm_qa}.py` (metric = `R-KPI-EMB96`/`R-KPI-RERANK90`/`R-LLM-SAST85`/`R-KPI-RELEV90`/`R-KPI-ENTITY100`/`R-KPI-HALLU2`, xem `finetune/configs/finetune_thresholds.yml`) |
| Phát hiện suy giảm | `finetune/scripts/check_finetune_thresholds.py` (so `approved_metrics.json`, ngưỡng suy giảm tương đối 5%) |
| Tự động Finetune/DPO | `scripts/04_auto_finetune.sh` (SFT, gọi `train_llm.py`/`train_embedding.py`/`train_reranker.sh` có sẵn — TRƯỚC đó ghép CVE mới vào train qua `_lib/build_incremental_train_data.py`, xem docstring: không làm bước này thì SFT chỉ lặp lại đúng dữ liệu cũ, không học gì thêm về CVE gây suy giảm) + `scripts/05_dpo_llm.py` (DPO cho LLM, `finetune/src/secai_ft/trainers/unsloth_dpo.py`, preference pairs xây thẳng từ report drift-check nên tự nhiên đã học CVE mới) |
| Quality Gate | `check_finetune_thresholds.py` chạy lại lần 2 trên candidate (`scripts/09_run_drift_check_candidate.sh`) |
| Triển khai | `scripts/07_push_candidate_to_hf.py` (candidate) → `scripts/08_rent_candidate_serving.sh` (tải về GPU, requalify) → `scripts/10_promote_or_hold.sh` (push production + redeploy gateway) |
| CVE mới cập nhật KB theo tần suất hàng ngày, đã kiểm duyệt ở Thu thập dữ liệu | Đúng thực tế — pipeline này KHÔNG kiểm duyệt lại, chỉ đọc field `updated_at` mà `sync_autoscan_to_kb.py` đã ghi |
| Model sau retrain không đạt ngưỡng → giữ nguyên model đang serving | `scripts/10_promote_or_hold.sh` nhánh FAIL — không đụng HF production/gateway, chỉ log + huỷ instance tạm |

## Chạy nhanh

```bash
bash setup/install.sh                       # preflight — xem thiếu gì trước khi chạy thật
bash scripts/run_pipeline.sh                # DRY-RUN mặc định: chỉ stage 1-3 (đọc + đo, an toàn)
bash scripts/run_pipeline.sh --execute      # + train/DPO/quantize/push-candidate/requalify (tốn GPU-giờ + tiền vast.ai, CHƯA đụng production)
bash scripts/run_pipeline.sh --execute --allow-deploy   # + promote lên production nếu candidate đạt (đụng hệ thống live)
```

Mỗi lần chạy tạo `runs/<run_id>/` chứa toàn bộ artifact trung gian (CSV CVE mới, bộ eval tổng
hợp, report JSON, checkpoint train, log) — giữ lại để truy vết/kiểm toán, xoá tay khi không cần.

## Lịch tự động + API kích hoạt

- `setup/install_schedule.sh` — systemd `--user` timer, mặc định **hàng tháng ngày 15** (đúng
  chính sách đã duyệt `01. Ho_so_huan_luyen/finetuning/reports/retraining-policy.md`, căn cứ
  `R-RETRAIN-AUTO`/`R-RETRAIN-DRIFT5`/`R-RETRAIN-DETECT`). Đổi cadence ở `configs/pipeline.yml:schedule`.
- `trigger_api/` — API riêng (cổng/khoá riêng, tách khỏi gateway public) để kích hoạt bất kỳ lúc
  nào ngoài lịch cố định. Xem `trigger_api/README.md`.

## Giới hạn đã biết (nói rõ, không phóng đại)

- **Đã kiểm tra thật (read-only) trên Qdrant `cve_kb_hybrid` sống (744k điểm)**: field
  `updated_at` mà `01_collect_new_cves.py` dùng làm mốc "CVE mới" CHỈ tồn tại trên điểm có
  `source="autoscan-api-sync"` (~14,7k/744k điểm tính tới lúc kiểm tra) — toàn bộ dữ liệu nạp
  trước đó (bulk HF dataset, NVD-cron cũ, OpenCVE backfill) không có field này nên không bao giờ
  được tính là "mới". Đây đúng là tập mà pipeline thu thập CVE hàng ngày (`sync_autoscan_to_kb.py`)
  ghi ra, nên khớp đúng ý "CVE mới cập nhật theo tần suất hàng ngày" — chỉ lưu ý nếu ai đó đổi
  lại pipeline thu thập sang cách ghi khác thì phải cập nhật `AUTOSCAN_SOURCE` trong
  `scripts/01_collect_new_cves.py`. Đã test thật end-to-end 01→02→adapter với dữ liệu Qdrant
  sống (đọc-only, an toàn) — chạy đúng, ra đủ `embedding.jsonl`/`reranker.jsonl`/`llm_test.jsonl`.
- **2026-09-18 — đã smoke-test THẬT trên 1× A100 80GB thuê vast.ai (không chỉ dry-run)**: SFT
  (embedding, reranker, LLM 30B MoE), DPO (LLM, tiếp tục đúng trên adapter SFT vừa train), push
  HF (adapter SFT + DPO + model merge fp16 66GB) — cả 3 model train xong, checkpoint/adapter lưu
  và push lên HF thật (`autumn10/sec-embedding-smoke`, `autumn10/sec-rerank-smoke`,
  `autumn10/Cyber-F1-smoke`, `autumn10/Cyber-F1-smoke-dpo`, `autumn10/Cyber-F1-smoke-merged`).
  Phát hiện + sửa **12 bug thật** trong quá trình (đã vá thẳng vào code, không phải ghi chú
  suông): venv Unsloth/SWIFT xung đột (tách venv riêng), `huggingface-cli` bị khai tử (đổi sang
  `hf`), cột dữ liệu không đồng nhất trong `data/llm/train.jsonl` (chỉ đọc field `messages`),
  Unsloth 2026.9.6 làm rơi `eos_token`/`pad_token` thật (vá qua `convert_tokens_to_ids`), `trl`
  0.24.0 không tương thích (hạ `trl==0.22.2`, xem `requirements-train.txt`), eval cuối training
  luôn chạy trên full validation set → OOM trên 80GB (bỏ qua eval khi smoke), fragmentation bộ
  nhớ CUDA (`PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True`), tiếp tục train trên adapter có
  sẵn thay vì gắn LoRA lần 2, `model.warnings_issued` thiếu, DPOTrainer coi nhầm là vision model
  (alias `process_row`=`tokenize_row`), `mergekit`/`llm_blender` kéo theo lỗi import (stub), và
  **Xet timeout khi push model lớn** (`HF_HUB_DISABLE_XET=1`, đã vá vào `07_push_candidate_to_hf.py`).
  Chi tiết từng bug nằm trong comment tại đúng chỗ sửa (`unsloth_sft.py`, `unsloth_dpo.py`,
  `07_push_candidate_to_hf.py`). **Chưa test thật**: `06_quantize_candidate.sh` (bỏ qua theo yêu
  cầu — cần máy riêng có `quantization/requirements.txt`), giai đoạn 6b/6c (rent GPU
  requalify) và giai đoạn 7 (promote/hold) — logic đã viết và review kỹ nhưng chưa chạy trên GPU
  thật, nên kiểm thử trước khi bật `--allow-deploy` trên lịch tự động.
- Ground truth của bộ eval tổng hợp (giai đoạn 2) suy ra tự động từ mô tả CVE (distant
  supervision) — mềm hơn benchmark 1.000 mẫu do người soạn tay (xem
  `scripts/_lib/adapt_eval_set.py`). Đây là tín hiệu cảnh báo sớm giữa các kỳ đối soát chính
  thức, không thay thế benchmark đã duyệt.

**2026-09-19 — rà lại luồng dữ liệu test→train→retest, sửa RÒ RỈ train/test + phát hiện thêm:**
- **[ĐÃ SỬA] Rò rỉ train/test ở cổng requalify**: trước đây stage 4 train trên đúng bộ synthetic
  mà stage 6c (requalify) đem ra chấm lại → candidate học thuộc bộ đo → luôn "đạt" → promote sai.
  Sửa: **stage 6c requalify chấm trên BENCHMARK CỐ ĐỊNH** (`benchmarks/data/llm_qa_harness_v1.jsonl`
  1000 mẫu + `finetune/data/{embedding,reranker}/test.jsonl` 5000/4200) — candidate KHÔNG train
  trên đó (incremental chỉ ghép vào `train.jsonl`, không đụng `test.jsonl`) → đo tổng quát hoá thật
  + bắt regression/catastrophic-forgetting. Bộ synthetic CVE mới GIỜ chỉ dùng cho: phát hiện suy
  giảm (stage 3, đo production — production chưa train nên hợp lệ) + bổ sung train (stage 4) + cặp
  DPO (4b). Đường dẫn benchmark khai ở `configs/pipeline.yml:benchmark`.
  (Đã cân nhắc tách train/eval theo CVE nhưng BỎ: gen thực tế chỉ ra ~1 câu grounded/CVE — không
  có câu held-out/CVE để tách; đây là tác vụ tiêm-kiến-thức nên chấm trên benchmark cố định là
  đúng chuẩn hơn.)
- **[CÒN TỒN, cần quyết định] Stage 3 (detection) dễ báo FINETUNE_REQUIRED thừa**: `approved_metrics.json`
  đang RỖNG (`reports:{}`) → gate không so tương đối được, rơi về ngưỡng tuyệt đối + `min_examples`
  (100 cho embed/rerank, 1000 cho LLM — cỡ benchmark). Bộ synthetic nhỏ (~1 câu/CVE) + gold yếu →
  gần như LUÔN "thiếu mẫu" và/hoặc "correctness thấp" → stage 3 gần như luôn FINETUNE_REQUIRED →
  **retrain thừa gần như mỗi chu kỳ**. GIẢM NHẸ: cổng benchmark ở 6c chặn không cho promote model
  tệ, nên hậu quả xấu nhất chỉ là TỐN GPU cho retrain thừa (không promote sai). Để sửa triệt để
  cần: (a) nạp `approved_metrics.json` thật (đo baseline 1 lần) để dùng so-tương-đối, và/hoặc (b)
  policy detection riêng cho stage 3 với `min_examples` nhỏ + ngưỡng hợp với gold-yếu, và/hoặc (c)
  detection so tương đối production-trên-CVE-mới vs production-trên-tập-tham-chiếu. Cần calibration
  → chưa tự sửa (tránh đặt ngưỡng bừa).
- **[GHI CHÚ, không phải lỗi pipeline] Benchmark LLM trùng train 68.7% theo CVE** (đo thật:
  benchmark 1014 CVE, 697 nằm trong `finetune/data/llm/train.jsonl`); embedding chỉ trùng 5.6%.
  Đây là tính chất của DATASET GỐC (Cyber_F1_v2), không do pipeline. Với mục đích cổng requalify
  (chống regression / catastrophic-forgetting sau khi finetune thêm CVE mới) thì benchmark-trùng-
  train là ĐÚNG (kiểm model có GIỮ được kiến thức CVE đã học không). Nhưng lưu ý: benchmark LLM vì
  vậy là phép thử GHI-NHỚ/retention, KHÔNG phải tổng-quát-hoá-thuần trên CVE chưa thấy. Nếu muốn
  gate đo cả tổng quát hoá, cần một tập test LLM tách hẳn CVE khỏi train (việc của dataset gốc).
- `06_quantize_candidate.sh` chỉ chạy được trên máy đã cài `quantization/requirements.txt`
  (torch cu126 + llmcompressor) — thường là máy GPU chuyên dụng riêng, không phải máy điều phối
  thường.
- Một GPU A100 80GB đủ để SFT/DPO smoke (đã verified) nhưng **không đủ dư cho eval full-size
  cuối training** — khớp đúng khuyến nghị 96GB trong `finetune/README.md` cho full run thật;
  trên 80GB, chạy full (không smoke) rủi ro OOM ở bước eval nếu không tinh chỉnh thêm
  (`eval_accumulation_steps`, giảm `per_device_eval_batch_size`, hoặc dùng máy 96GB+ như tài
  liệu gốc đã khuyến nghị).

**2026-09-18 — review tĩnh sau smoke test (không cần GPU), 3 lỗi thật đã vá:**
1. `_lib/merge_lora.py` (bản đầu, chưa từng chạy) dùng
   `transformers.AutoModelForCausalLM`+`peft.PeftModel.merge_and_unload()` — rủi ro thật vì
   Cyber-F1 là kiến trúc tuỳ biến (MoE + vision tower nhỏ) mà Unsloth tự vá riêng khi load. Viết
   lại dùng đúng đường đã verified sống (`load_base_model` + `save_pretrained_merged`, xem
   docstring file) — `06_quantize_candidate.sh`/`run_pipeline.sh` cập nhật theo (bớt tham số
   `BASE_MODEL` không còn cần).
2. **"Tự động Finetune" (giai đoạn 4) trước đây train lại trên đúng dữ liệu tĩnh cũ**
   (`data/{llm,embedding,reranker}/train.jsonl`) — không hề đưa CVE mới (giai đoạn 2) vào, nên
   SFT sau khi phát hiện suy giảm sẽ không học được gì thêm về chính nguyên nhân gây suy giảm
   (DPO ở giai đoạn 4b thì có, vì preference pairs xây thẳng từ report drift-check). Vá bằng
   `_lib/build_incremental_train_data.py` (ghép CVE mới vào ĐUÔI file train, không sửa file
   tĩnh gốc) + override CLI/env cộng thêm không đổi hành vi mặc định
   (`train_llm.py --dataset-path`, `train_embedding.py --train` đã có sẵn, `train_reranker.sh`
   thêm `$TRAIN`/`$VAL` override mirror đúng pattern `$OUT`). Đã test thật (không cần GPU): cả 3
   converter chạy qua `read_rows()`/`prepare_reranker_swift_data.py` thật, JSON hợp lệ, ghép đúng
   số dòng.
3. `setup/install_schedule.sh` cài timer chạy `run_pipeline.sh --execute --allow-deploy` nhưng
   KHÔNG có cách nào để `HF_TOKEN`/`JUDGE_BASE_URL`/`JUDGE_MODEL` tới được tiến trình —
   `systemctl --user` không kế thừa biến môi trường đã `export` ở shell tương tác, nên timer tự
   bắn (không ai đang `export` sẵn) sẽ chết ngay ở `run_gate_eval`. Vá bằng
   `setup/mlops.env.example` (copy ra ngoài, xem CẢNH BÁO BẢO MẬT bên dưới) nạp vào service qua
   `EnvironmentFile=`; `install_schedule.sh` từ chối cài nếu file thiếu/thiếu giá trị.

**2026-09-18 (cùng ngày, sau khi có judge thật) — chạy stage 1→3 THẬT lần đầu (15 CVE mẫu thật,
production LLM thật, judge OpenRouter/DeepSeek thật) — tìm + vá thêm 5 lỗi/khoảng trống thật:**

4. **CẢNH BÁO BẢO MẬT (mức nghiêm trọng nhất từng gặp trong dự án này) — secret bị lộ công khai,
   không auth, qua gateway sống:** `mlops/setup/mlops.env` (chứa `HF_TOKEN` + `JUDGE_API_KEY`
   thật, plaintext) đặt trong `02. Bo_build_finetuning/mlops/setup/` — nhưng TOÀN BỘ cây
   `HO-SO-GOC-NGHIEM-THU-AI` được `secai-docker/docker-compose.yml` mount READ-ONLY vào gateway
   sống (`/srv/showcase/hosogoc`, cùng container phục vụ `ias.id.vn`), và endpoint
   `GET /showcase-api/finetune/bundle` (`secai-agent-gateway/src/secai_gateway/api/showcase/
   finetune.py`, **KHÔNG có auth**, gateway bind `0.0.0.0:8131`) zip TOÀN BỘ `02. Bo_build_finetuning/`
   để phục vụ công khai — kéo theo `mlops.env` thật. Xác nhận độc lập qua chính endpoint sống
   (`file_count` giảm đúng 1 sau khi xử lý). Xử lý 2 lớp, cả 2 đều đã verify live:
   - **Ngay lập tức (không cần rebuild/restart gateway)**: chuyển hẳn `mlops.env` ra ngoài cây bị
     mount — `~/.config/secai-mlops/mlops.env` (chmod 600). Vì `showcase_ft_build_dir` đọc thẳng
     filesystem sống, việc này có hiệu lực tức thì mà không cần đụng gateway đang chạy (gateway
     build từ Docker image đã COPY sẵn code — sửa `finetune.py` KHÔNG có tác dụng tới khi
     rebuild+redeploy image, đã xác nhận file_count không đổi sau khi chỉ sửa source).
   - **Vá gốc (cần rebuild image `secai-agent-gateway` mới có hiệu lực, CHƯA rebuild)**:
     `_bundle_members()` trong `finetune.py` thêm loại trừ `rel.parts[:1] == ("mlops",)` — sau
     lần rebuild+redeploy gateway kế tiếp (`docker compose build gateway && docker compose up -d
     gateway`, sẽ làm gián đoạn `ias.id.vn` vài giây), toàn bộ `mlops/` không còn nằm trong gói
     `.zip` nữa.
   - **`install_schedule.sh` giờ mặc định `~/.config/secai-mlops/mlops.env`** (override qua
     `MLOPS_ENV_FILE`), không còn đường mặc định nào trỏ vào bên trong cây dossier.
   - **Chưa làm, nên làm sớm**: xoay vòng (rotate/revoke) `HF_TOKEN` (`autumn10`) và
     `JUDGE_API_KEY` (OpenRouter) đã dùng trong phiên này — đã bị lộ công khai một khoảng thời
     gian không xác định trước khi phát hiện.
5. `03_run_drift_check.sh` đọc `gateway.env_file` (`secai-agent-gateway/.env`, **lỗi thời từ
   cutover Docker 2026-08-20** — trỏ 1 LLM instance vast.ai đã bị huỷ từ lâu, `curl` xác nhận
   "Connection refused" thật) thay vì `gateway.docker_env_file` (`secai-docker/.env`, file THẬT
   đang nạp cho gateway container "healthy" đang chạy). Vá: merge cả 2, `docker_env_file` đè lên
   nếu có giá trị — khớp đúng thực tế "đã cutover sang Docker" đã ghi ở nơi khác trong dự án.
6. `_lib/common.py:qdrant_base_url()` dùng `os.environ.get(key, default)` — 1 biến môi trường
   TỒN TẠI nhưng RỖNG (đúng cách `setup/mlops.env.example` hướng dẫn điền, vd `QDRANT_URL=` để
   trống nếu dùng default) vẫn được coi là "có giá trị" (chuỗi rỗng), không rơi về default, sinh
   URL hỏng `/collections//points/scroll` → `ValueError: unknown url type`. Vá bằng `or` thay vì
   `.get(k, default)` (khớp ngữ nghĩa `${VAR:-default}` của bash).
7. Máy điều phối (chạy stage 1-3, theo thiết kế KHÔNG cần GPU) vẫn cần
   `sentence-transformers`/`transformers`/`accelerate`/`torch` cho `eval_embedding.py`/
   `eval_reranker.py` — trước đó không được `setup/install.sh` kiểm tra (chỉ kiểm tra
   `unsloth`/`llmcompressor`, cả hai đều execute-only), dễ khiến người vận hành tưởng "stage 1-3
   không cần cài gì" (đúng cho GPU, sai cho python deps). Thêm `finetune/requirements-eval.txt`
   (nhẹ, CPU đủ dùng) + preflight check riêng trong `install.sh`.
8. `eval_llm_qa.py`'s judge request cố định `max_tokens=220` không tính tới model judge dạng
   "reasoning" (vd `deepseek/deepseek-v4.1-flash` qua OpenRouter) — model có thể tiêu hết ngân
   sách token vào phần suy luận ẩn trước khi trả `content`, trả về `content=None` +
   `finish_reason="length"`, làm `parse_judge_json` crash với `TypeError` không bắt được (sập
   toàn bộ lần chấm, không phải lỗi từng câu). Vá 2 bước, cả 2 đều bắt buộc (verify live: chỉ 1
   trong 2 không đủ — bước 1 một mình còn 14/15 lỗi vì prompt chấm thật dài hơn test tay):
   - Thêm `"reasoning": {"exclude": true}` vào request (ẩn token suy luận khỏi response — vô hại
     với model/provider không hỗ trợ reasoning).
   - Tăng `max_tokens: 220 → 1024` (`exclude` chỉ ẩn hiển thị, KHÔNG giảm số token model thật sự
     tiêu để suy luận trước khi trả JSON — 220 vẫn không đủ chỗ cho cả suy luận lẫn JSON với
     prompt chấm dài thật; 1024 rẻ, không đáng kể chi phí).
   - Mở rộng `except` bắt thêm `TypeError` để giảm-cấp về lỗi từng câu thay vì sập cả batch nếu
     vẫn còn trường hợp tương tự chưa lường hết.

**Kết quả stage 1→3 thật (15 CVE mẫu, 2026-09-18, sau khi vá đủ 8 bug trên):**
embedding recall@10=1.0/mrr@10=0.71, reranker pairwise_accuracy=1.0 — đo thật trên production,
số liệu hợp lý. LLM: 15/15 câu trả lời thật từ `DuyTa/Cyber-F1-AWQ` production, judge chấm
15/15 thành công (0 lỗi) — nhưng **kết quả đáng lo ngại thật**: `answer_correctness=0.067`,
`hallucination_rate=0.933`. Nguyên nhân cụ thể quan sát được trong câu trả lời thật: model từ
chối nhiều CVE-2026 có thật, khẳng định "năm 2026 chưa đến, hiện tại là năm 2024" — dấu hiệu
model chưa từng biết tới các CVE gần đây/có nhận thức thời gian lệch. `finetune-decision.json`
ra đúng `FINETUNE_REQUIRED` (cả 3 component) — nhưng gate tự ghi rõ `examples=15, need >= 100/1000`
nên đây là tín hiệu cảnh báo sớm từ mẫu nhỏ để verify wiring, KHÔNG phải kết luận chính thức thay
cho lần chạy đủ quy mô (xem `runs/manual-drift-test-*` để đối chiếu chi tiết).

**2026-09-18 (cùng ngày) — lớp điều khiển + quan sát THẬT trên trang nghiệm thu:**
- `run_pipeline.sh` giờ ghi `runs/<id>/status.json` (tiến độ + exit từng giai đoạn, qua
  `_lib/pipeline_status.py`) và `runs/<id>/logs/<NN>.log` (log riêng mỗi giai đoạn) — nền cho
  hiển thị realtime, không giả lập.
- `trigger_api` nâng thành service đầy đủ: `/schedule`, `/runs`, `/runs/{id}`,
  `/runs/{id}/stages/{sid}/log`, `/runs/{id}/stream` (SSE), `/smoke`, `/trigger {mode}` + CORS;
  giữ tương thích `/trigger {execute,allow_deploy}` + `/status/{id}` cũ. Dựng thành systemd --user
  service bằng `setup/install_trigger_service.sh` (đã cài + chạy live, `/health` OK).
- Trang nghiệm thu (`:8131/showcase-demo`) có tab **"Retraining tự động"**
  (`demo/showcase/tabs/retraining-auto.js`): xem lịch đối soát, nút Chạy smoke / Kích hoạt cả
  vòng đời (nhập key + xác nhận), log từng giai đoạn realtime (SSE). Gateway reverse-proxy
  `/showcase-api/retraining/*` → host :8877 (`showcase/retraining_proxy.py`). Đã test THẬT qua
  trình duyệt (trực tiếp :8877): kết nối, lịch, trigger smoke, SSE + log giai đoạn render đúng.
- **2 lỗi thật vá thêm khi test:**
  (a) systemd `ExecStart` chưa quote đường dẫn có dấu cách → `status=203/EXEC` (service không
  chạy). Đây cũng là bug tiềm ẩn trong `install_schedule.sh` (path `run_pipeline.sh` có dấu cách)
  → timer auto-retrain hằng tháng sẽ FAIL ÂM THẦM; đã quote `ExecStart` ở CẢ hai installer.
  (b) tab đọc `/runs` bị browser cache response cũ → thêm `cache: "no-store"`.
- **Gateway ĐÃ rebuild + redeploy (2026-09-18)** → proxy `/showcase-api/retraining/*` live +
  bản vá lộ secret `/bundle` có hiệu lực (verify: retrain-guide file_count 23927→42, 0 mlops).
  Tab kết nối same-origin out-of-the-box, không cần override base. trigger_api bind IP bridge
  `172.17.0.1` (host.docker.internal của container) — container tới được, KHÔNG lộ ra internet
  (khác 0.0.0.0); truy cập local dùng 172.17.0.1:8877, không phải 127.0.0.1.

## Cấu trúc

```
configs/pipeline.yml     đường dẫn tới mọi phân hệ liên quan + ngưỡng + lịch (1 chỗ khai báo)
state/last_finetune.json watermark + tag đã approve mỗi component
scripts/                 01..10 theo đúng thứ tự vòng đời (xem sơ đồ trên) + run_pipeline.sh
scripts/_lib/            helper dùng chung (common.py, adapt_eval_set.py, merge_lora.py, ...)
setup/                   preflight + cài lịch tự động
trigger_api/             API kích hoạt theo yêu cầu
tools/                   download_and_smoke_test.sh — tải/kiểm thử model thủ công trên 1 máy GPU
runs/                    output mỗi lần chạy (tạo tự động, không commit — thêm vào .gitignore)
```
