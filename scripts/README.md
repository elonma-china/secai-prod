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
| Benchmark tải | `bench_ccu_gpus.sh`: locust 30 CCU, tạm ghim serving vào N GPU rồi trả nginx về như cũ |

```sh
./scripts/show-reasoning.sh --help
# Lệnh dưới gọi API/model thật và lưu lịch sử đánh giá:
python3 scripts/check_effective_context.py http://127.0.0.1:8131 20000
```

Kết quả benchmark có cấu trúc ở `evals/`, `kpi_bench/` hoặc `evidence/` theo runner. Log vận hành local ở `logs/`; các log root cũ không còn process sử dụng đã chuyển vào `logs/archive/`. Không commit env, log, dữ liệu runtime hay backup private.

## Worktree, release prod và kiểm ops

| Script | Việc | Đổi trạng thái? |
| --- | --- | --- |
| `dev.sh` | Stack dev riêng cho từng worktree (`init/up/down/health/test ...`); `dev.sh test` chạy pytest trên `src/` của worktree | Chỉ stack `secai-gw-dev-*` |
| `wt-new.sh`, `wt-rm.sh` | Tạo/xoá worktree kèm stack dev (giữ nhánh) | Worktree của mình |
| `wt-prune.sh`, `install-wt-prune-timer.sh` | Dọn worktree/nhánh/stack dev của PR đã merge (mặc định dry-run, `--yes` mới xoá); timer hằng giờ, kèm `release-prod.sh gc --yes` | Có, khi `--yes` |
| `release-prod.sh` | Release `origin/main` lên 3 container gateway: `plan` (xem diff), `gates` (test+build+smoke), `run --yes`, `rollback --yes`, `gc [--yes]`. Quy trình ở mục "Sau khi merge PR" trong [CLAUDE.md](../CLAUDE.md) | `run`/`rollback`/`gc --yes` đổi prod |
| `ops-check.py` | Chấm trạng thái ops thật theo [tiêu chí](../docs/criteria/ops.md): PASS/FAIL/WARN/SKIP, `--json`, mã thoát 1 khi có FAIL bắt buộc | Không, chỉ đọc |
| `versions.py` | Sổ phiên bản container: `status` (bản đang chạy và cờ ID trần, tag trôi, lệch image), `history`, `snapshot`, `record` (do `release-prod.sh` gọi), `serve` (trang chỉ xem trên 127.0.0.1:18190). Xem [quản lý phiên bản container](../docs/operations/container-versions.md) | `status`/`history`/`serve` không; `record`/`snapshot`/`backfill --yes` ghi sổ `~/secai-releases/versions.jsonl` |
| `backup-sqlite.py`, `install-backup-timer.sh` | Backup SQLite hằng ngày (report/review/gateway/session/dịch) bằng online backup API trong container tạm mount read-only, vào `~/secai-backups/sqlite/<YYYYMMDD-HHMM>/` + `SHA256SUMS`, giữ 7 ngày; `--dry-run` chỉ in kế hoạch. Timer `systemd --user` 01:30 (`--remove` để gỡ). Xem [backup SQLite](../docs/operations/sqlite-backup.md) | Ghi `~/secai-backups`, chỉ đọc DB prod; installer: unit systemd của user |
| `install-versions-ui.sh` | Cài `systemd --user` cho trang phiên bản (`secai-versions-ui.service`) và snapshot mỗi giờ (`secai-versions-snapshot.timer`), chạy `versions.py` của `origin/main`; gỡ bằng `--remove`. Xem [quản lý phiên bản container](../docs/operations/container-versions.md) | Có: unit systemd của user |

```sh
python3 -I scripts/ops-check.py --group VER,SEC     # chỉ đọc, không cần quyền root
scripts/release-prod.sh plan                         # xem trước, không đổi prod
scripts/dev.sh test tests/test_release_prod.py tests/test_ops_check.py tests/test_ops_repo.py
```
