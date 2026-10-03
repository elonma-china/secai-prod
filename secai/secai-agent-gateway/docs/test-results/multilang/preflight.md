# Multilang T0 preflight và baseline

Ngày kiểm tra: 2026-10-03/04 (Asia/Ho_Chi_Minh). Worktree `feature/multilang-assessment`, base `ce9cd47ee1273018fec1c5cbab944a86d68d6ed5`. Chỉ đọc metadata Vast; **chưa gọi inference, benchmark, tải model, đổi process hoặc restart dịch vụ**. Tác vụ OCR đang giữ lượt GPU độc quyền theo `docs/loop/STATE.md` của task file/OCR; live assessment đang **BLOCKED chờ lịch**.

## Môi trường và baseline CPU

- Venv riêng tại `.venv`, tạo bằng `uv venv` và `uv pip install --python .venv/bin/python -e '.[dev]' pytest-timeout`; cài thêm `redis==8.0.1` vào venv riêng vì hai fixture `tests/test_session_store.py` import Redis dù `redis` chỉ là optional extra. Không sửa `pyproject.toml`, shared venv hoặc source của task khác.
- Python 3.12.3, uv 0.11.12, pytest 9.1.1, pytest-timeout 2.4.0, Linch 1.2.1, Redis 8.0.1. Import `secai_gateway.__file__` trỏ đúng `/home/ict/secai-multilang-assessment-wt/secai/secai-agent-gateway/src/secai_gateway/__init__.py`.
- Harness chạy bằng `env -i`; `HOME`, `XDG_CACHE_HOME`, `HF_HOME`, `GATEWAY_DB_PATH`, `SESSION_DB_PATH`, `POC_CATALOG_DB`, `EXPLOITDB_ROOT`, `POC_SOURCES_ROOT`, `POC_ARTIFACT_STORE`, `INPUT_CLASSIFIER_WEIGHTS_PATH` đều trỏ vào `.venv/baseline-home` của worktree; `PYTHONPATH=$PWD/src`, `SESSION_STORE_BACKEND=memory`, `REDIS_URL=`, `HF_HUB_OFFLINE=1`, `TRANSFORMERS_OFFLINE=1`. HTTP(S)/ALL proxy cả chữ hoa/thường trỏ `http://127.0.0.1:9`; `RES_OPTIONS=attempts:1 timeout:1`. Không copy `.env`.
- Lệnh: `timeout 600s .venv/bin/python -m pytest -q --tb=short --timeout=10 --timeout-method=signal --junitxml=docs/test-results/multilang/baseline.junit.xml` trong môi trường trên. **925 test: 905 pass, 19 fail, 1 skip, 0 error**, pytest 152.04 giây, exit 1. Đúng số lượng và 19 node lỗi của baseline task file/OCR tại cùng base; 13 lỗi timeout 10 giây, 6 lỗi phụ thuộc dữ liệu/cây ngoài checkout. Đây là baseline **FAILED**, không phải full PASS.
- [baseline.json](baseline.json) ghi đủ 19 node ID lỗi, category, phiên bản, SHA và count; [baseline.junit.xml](baseline.junit.xml) giữ đủ 925 testcase/outcome nhưng đã bỏ traceback, stdout, failure message và hai giá trị tham số trong tên test bảo mật có dạng token. Mọi node ID lỗi vẫn nguyên vẹn. Log/JUnit gốc chỉ ở `.venv` bị Git ignore, không stage.
- Lượt chẩn đoán đầu dừng collection với 21 error do default `/srv/exploitdb` không ghi được trong worktree sạch. Sau khi cô lập writable paths, lượt kế chạy 903 pass/19 fail/1 skip/2 setup error; hai error do thiếu Redis optional. Lượt cuối sau khi cài Redis riêng là kết quả so sánh ở trên. Không bỏ/deselect test lỗi.

## Vast read-only inventory

| Hạng mục | Quan sát | Giới hạn |
|---|---|---|
| Instance | Vast 53846446 running, NVIDIA A100 SXM4 80 GiB; SSH được resolve qua `vastai ssh-url` | Không thuê/mở instance khác |
| GPU tại một thời điểm | 81.920 MiB tổng, 73.463 MiB dùng, 7.586 MiB rảnh, GPU util 41%; bốn GPU process dùng 64.078/1.892/5.050/2.418 MiB | Snapshot, không phải reservation/headroom bảo đảm |
| Dịch vụ hiện hữu | Authenticated read-only `GET /v1/models`: proxy 8000 → `DuyTa/sec-embedding` (backend 18001), 8001 → `DuyTa/Cyber-F1-AWQ` (18101), 8002 → `DuyTa/sec-rerank` (18201) | Không gửi completion/embedding/rerank request |
| Public mapping | Instance port 10466/10739/10563 lần lượt embed/LLM/rerank; cấu hình gốc `secai-docker/.env` khớp host/port và ba model ID | `secai-agent-gateway/.env` và `deploy/.env` gốc dùng endpoint cũ; không copy hoặc in key |
| LLM snapshot | `HF_HOME=/workspace/hf-cache`; sole cached `DuyTa/Cyber-F1-AWQ` snapshot và `refs/main`: `7e7e6718ce268d2b00adc6e3ea43fcd33452f516` | Serve process không có `--revision`; revision được suy từ cache duy nhất, chưa xác nhận trực tiếp loaded path |
| LLM config | `qwen3_5_moe`, `compressed-tensors`/`pack-quantized`; tokenizer `Qwen2Tokenizer`, tokenizer max length 262.144, chat template có SHA-256 `a4aee8afcf2e0711942cf848899be66016f8d14a889ff9ede07bca099c28f715` | Serving `--max-model-len=65536` là giới hạn thực tế; output ceiling và LoRA compatibility chưa xác nhận |
| Process/engine | LLM process `--model DuyTa/Cyber-F1-AWQ --port 18101 --gpu-memory-utilization 0.80 --max-model-len 65536`; vLLM 0.25.1, torch 2.11.0+cu130, transformers 5.13.1 | Không có `--enable-lora` ở process hiện hữu; không suy engine có thể nạp adapter này |
| Phụ trợ | Embed cache revision `f39e9e539aa3ba319e6c2924ec623502ef5e1c29`; rerank `a51af89b8a93b11beb4c4231fa63df94f5a38d62`; listener OCR riêng cổng 18083 | Không đụng OCR/PID hoặc dịch vụ khác |
| License | Inventory HF của plan ghi `apache-2.0` tại cùng Cyber-F1 revision | README card không nằm trong cached snapshot; cần nguồn gốc trực tiếp trước Gate C/license decision |

Không dùng `/health` để suy embedding/reranker hoạt động; `/v1/models` chỉ chứng minh ID được công bố, không chứng minh inference hay chất lượng. Gate A/B/D, dataset 18 hướng, ≥70% rubric, off-target và entity acceptance đều **NOT RUN**. OCR đang benchmark trên cùng GPU nên không có quyền chạy live.
