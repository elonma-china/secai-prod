# Công cụ vận hành và đánh giá

Chạy các lệnh từ root `secai-agent-gateway/`. Code runtime ở `src/`; script hỗ trợ ở đây. Script triển khai Docker nằm trong `deploy/`, script thuê GPU nằm trong repository riêng `secai-serving-vast/`.

| Nhóm | Script |
| --- | --- |
| Debug model | `show-reasoning.sh`: gọi model theo `.env` ở root; `--help` không gọi inference |
| Kiểm context hội thoại | `check_effective_context.py`: needle-in-haystack qua API chat, tạo session/user đánh giá riêng |
| Classifier | `train_sanitize_head.py`, `calibrate_injection_cascade.py`, `bench_classification.py`, `build_classification_benchmark.py` |
| Dataset/eval sanitize | `build_sanitize_dataset.py`, `build_sanitize_evalsets.py`, `eval_sanitize.py`, `eval_pii_hybrid.py`, `eval_lodo_family_holdout.py` |
| Hồ sơ nghiệm thu | `audit_hosogoc.py`, `import_hosogoc_zip.py`; file JSON cùng thư mục là đầu vào cho các công cụ này |
| Dịch file local | `run_translate_v2_local.py`; xem [hướng dẫn](../docs/examples/translate-v2-local-operator.md) |

```sh
./scripts/show-reasoning.sh --help
# Lệnh dưới gọi API/model thật và lưu lịch sử đánh giá:
python3 scripts/check_effective_context.py http://127.0.0.1:8131 20000
```

Kết quả benchmark có cấu trúc ở `evals/`, `kpi_bench/` hoặc `evidence/` theo runner. Log vận hành local ở `logs/`; các log root cũ không còn process sử dụng đã chuyển vào `logs/archive/`. Không commit env, log, dữ liệu runtime hay backup private.
