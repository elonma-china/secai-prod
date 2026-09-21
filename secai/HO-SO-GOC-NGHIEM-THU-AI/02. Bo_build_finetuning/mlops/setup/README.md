# Cài đặt cho pipeline mlops/

Pipeline chạy trên **1 máy điều phối** (không cần GPU cho stage 01-03; cần GPU 80GB+ cho stage
04-06 khi `--execute`) + rent GPU **tạm thời qua vast.ai** cho stage 08-09 (requalify) và
production (stage 10). Máy serving GPU tự nó **không cần cài gì tay** — nó là container
ephemeral dựng từ image `vastai/vllm_*` qua `serving-up-cyber-f1.sh` (xem
`04. Huong_dan_cai_dat/HUONG-DAN-CAI-DAT.md`).

> **Lưu ý quan trọng trước khi chạy (đọc trước bước 1):**
> - **`.venv-eval`**: máy điều phối chạy stage 1-3/9 + `trigger_api` trong venv `mlops/.venv-eval`.
>   Venv này **bị gitignore + không relocatable** → máy mới KHÔNG có sẵn; `install.sh` mục 1b sẽ
>   tự tạo (venv + `requirements-eval.txt` + fastapi/uvicorn/httpx/pyyaml). Đừng copy `.venv-eval`
>   từ máy khác sang (đường dẫn tuyệt đối trong `pyvenv.cfg` sẽ sai).
> - **Product gate là MẶC ĐỊNH (`PRODUCT_GATE=1`)**: stage 3/9 đo qua **full pipeline thật**
>   (gateway `/ai/chat` + embed/rerank + Qdrant). Vì vậy **cả `run_pipeline.sh` dry-run cũng cần
>   stack phục vụ (`secai-docker`: gateway :8131 + serving vast + qdrant) ĐANG CHẠY** — không phải
>   "chỉ đọc, không đụng gì". Đặt `PRODUCT_GATE=0` nếu muốn gate benchmark cũ (không gọi stack sống).
> - **`CANDIDATE_GATEWAY_BASE`** (bắt buộc khi requalify bằng product gate): gateway trỏ LLM
>   *candidate* cho stage 6c. Thiếu → `run_pipeline.sh` dừng cứng. Khai trong `mlops.env` (vd sandbox
>   `:8132`), hoặc dùng `PRODUCT_GATE=0`.
> - **Judge**: chọn model **non-reasoning** (vd `deepseek/deepseek-chat-v3.1`) cho gate ~1000 mẫu —
>   model reasoning (deepseek-flash…) chậm nhiều giờ. Xem `mlops.env.example`.

## 1. Chạy preflight

```bash
bash setup/install.sh
```

Kiểm tra & cài (nếu thiếu):
- `hf` CLI (huggingface_hub>=1.x — `huggingface-cli` cũ đã bị khai tử) + `$HF_TOKEN` (**quyền write** lên `DuyTa/*` — bắt buộc để push candidate/
  production, xem `configs/pipeline.yml:hf`)
- `vastai` CLI + API key (`vastai set api-key <KEY>`, dùng `--url https://cloud.vast.ai` nếu
  `console.vast.ai` 403 — xem ghi chú trong `install.sh`)
- GPU (`nvidia-smi`) — chỉ cần trên máy sẽ chạy `--execute` (train/DPO); máy chỉ chạy drift-check
  (stage 01-03, không `--execute`) không cần GPU
- `.venv-eval` (mục 1b) — cài `requirements-eval.txt` (sentence-transformers/transformers/torch
  CPU) + fastapi/uvicorn/httpx/pyyaml; **cần cả ở dry-run mặc định** (stage 3 đo embed/rerank).
  Máy điều phối sạch chạy `run_pipeline.sh` không cờ vẫn chết nếu thiếu — dễ nhầm "stage 1-3 không
  cần cài gì" (đúng cho GPU, sai cho python deps)
- `finetune/requirements-train.txt` (unsloth/trl/…) — chỉ cần trên máy chạy `--execute` (train/DPO).
  Reranker (ms-swift) phải ở **venv RIÊNG** (`datasets==4.8.4`, xung đột với unsloth `datasets<4.4`)
- `quantization/requirements.txt` (llmcompressor/…) — chỉ cần trên máy chạy `06_quantize_candidate.sh`
- `secai-docker/.env` đọc được `EMBEDDING_API_URL`/`RERANKER_API_URL`/`QDRANT_URL` + gateway `:8131`
  `/health` sống + judge (`JUDGE_*`) reachable — các tiền đề của product gate (mục 7-8 của
  `install.sh`). (Nhánh cũ đọc `secai-agent-gateway/.env`+`QWEN_*` đã stale, không còn dùng mặc định.)

## 2. Biến môi trường cần export trước khi chạy `--execute`/`--allow-deploy` bằng tay

| Biến | Dùng ở |
|---|---|
| `HF_TOKEN` | push/pull model HF (07, 10, tools/download_and_smoke_test.sh) |
| `JUDGE_BASE_URL`, `JUDGE_MODEL`, `JUDGE_API_KEY` | `03_run_drift_check.sh`/`09_...` — judge PHẢI độc lập với model đang đo. Mặc định đã dùng (2026-09-18): OpenRouter, `JUDGE_BASE_URL=https://openrouter.ai/api/v1`, `JUDGE_MODEL=deepseek/deepseek-chat-v3.1` |
| `QDRANT_URL`, `QDRANT_COLLECTION` | tuỳ chọn, mặc định lấy từ `configs/pipeline.yml:qdrant` (`cve_kb_hybrid`) |
| `MLOPS_TRIGGER_API_KEY` | nếu chạy `trigger_api/app.py` — xem `trigger_api/README.md` |

`vastai` API key và `HF_TOKEN` **không** để trong `configs/pipeline.yml` (file có thể commit) —
luôn qua biến môi trường/`vastai set api-key`.

**Chạy qua `run_pipeline.sh` trực tiếp trong shell của mình:** `export` các biến trên trước là
đủ. **Chạy qua timer tự động (mục 3):** KHÔNG đủ — `systemctl --user` chạy service trong một
tiến trình nền không kế thừa export của shell, xem mục 3.

## 3. Lịch chạy tự động

Timer tự bắn không có shell nào đứng sau để `export` giúp — `HF_TOKEN`/`JUDGE_BASE_URL`/
`JUDGE_MODEL`/`JUDGE_API_KEY` phải nằm sẵn trong 1 file, nạp vào service qua `EnvironmentFile=`:

```bash
mkdir -p ~/.config/secai-mlops
cp setup/mlops.env.example ~/.config/secai-mlops/mlops.env
chmod 600 ~/.config/secai-mlops/mlops.env
$EDITOR ~/.config/secai-mlops/mlops.env    # điền HF_TOKEN, JUDGE_BASE_URL, JUDGE_MODEL, JUDGE_API_KEY

bash setup/install_schedule.sh --dry-run   # xem trước, không cài gì
bash setup/install_schedule.sh             # cài systemd --user timer, không cần sudo
                                            # (từ chối cài nếu ~/.config/secai-mlops/mlops.env thiếu/thiếu giá trị)
```

**KHÔNG đặt file `mlops.env` thật (đã điền secret) bên trong `02. Bo_build_finetuning/`** — sự
cố thật 2026-09-18: gateway sống mount thư mục này READ-ONLY và endpoint
`/showcase-api/finetune/bundle` (không auth, bind `0.0.0.0:8131`) zip toàn bộ cây thư mục để
phục vụ công khai; `mlops.env` từng bị lộ `HF_TOKEN`+`JUDGE_API_KEY` plaintext qua đó trước khi
phát hiện và dọn. `install_schedule.sh` mặc định dùng `~/.config/secai-mlops/mlops.env`
(override bằng `MLOPS_ENV_FILE=<path>` nếu cần) — luôn ở NGOÀI mọi cây thư mục có thể bị
mount/bundle bởi service khác.

Mặc định **hàng tháng, ngày 15** — lấy nguyên giá trị đã duyệt ở
`01. Ho_so_huan_luyen/finetuning/reports/retraining-policy.md` (căn cứ `R-RETRAIN-AUTO`,
`R-RETRAIN-DRIFT5`, `R-RETRAIN-DETECT`). Đổi cadence ở `configs/pipeline.yml:schedule` rồi chạy
lại `install_schedule.sh`. Timer chạy `run_pipeline.sh --execute --allow-deploy` — **tự động
train + deploy nếu phát hiện suy giảm**, không cần người bấm nút (đọc cảnh báo trong
`install_schedule.sh` nếu muốn chỉ tự động drift-check còn train/deploy làm tay).

`vastai` không cần trong `mlops.env` — API key của nó nằm ở `~/.config/vastai` (file, đọc được
từ bất kỳ tiến trình nào của cùng user, kể cả service systemd --user), không phải biến môi
trường.

## 4. Kích hoạt theo yêu cầu (API)

Ngoài lịch cố định, có thể gọi pipeline bất kỳ lúc nào qua `trigger_api/` — xem
`trigger_api/README.md`.
