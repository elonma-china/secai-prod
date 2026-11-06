# Sửa và kiểm thử Devspace Reward feedback — 2026-10-05

Đã sửa mã nguồn và kiểm thử trên môi trường QA riêng. Đây là bằng chứng tại bước QA; commit và triển khai sau đó được ghi trong [báo cáo triển khai](review-reward-deploy-20261005.md).

## Thay đổi

- Review CRUD dùng hạn mức 120 request/60 giây/IP/path thay vì 15. API gọi model `live-pairs` và `report-templates/*` vẫn giữ hạn mức 15; sampling/cooldown mặc định vẫn 0.1/600 giây.
- Admin tải danh sách reviewer một lần theo identity; không tải lại toàn bộ campaign sau mỗi phân công/duyệt. Khi đóng/mở chiến dịch, UI dùng trạng thái đã được backend xác nhận, kể cả khi tải lại items/stats thất bại. Ẩn thống kê cũ nếu tải lại lỗi.
- Thêm `skip` (Bỏ qua) riêng cho phiếu và quyết định admin. Phiếu skip không bắt buộc nhận xét, trừ khi chọn nhãn lỗi `other`. Quyết định admin vẫn bắt buộc lý do. Export preference chỉ lấy winner đã duyệt, không lấy skip. Giữ `insufficient_context` tương thích dữ liệu cũ; không cần migration SQLite.
- CORS cho phép PUT và Authorization; giữ nguyên danh sách origin được cấu hình và kiểm tra Bearer/role.

## Kết quả

| Kiểm tra | Kết quả |
|---|---|
| Backend review/auth/store/export/live/template/proxy và middleware mới | 38 passed |
| Toàn bộ test frontend | 65 passed, 9 files |
| Build bản frontend đã sửa | PASS; còn cảnh báo chunk >500 kB |
| E2E admin: tạo/import/retry import/phân công, 6 loại phiếu, duyệt, export, đóng | PASS |
| Export | Raw 6 mẫu; preference 2 winner; manifest SHA-256 khớp; trước duyệt preference rỗng |
| E2E template với model thật | PASS: đề xuất, bản nháp chưa thay template, áp dụng, tạo và tải báo cáo |
| E2E chat với model thật | PASS: SSE, A/B, nộp phiếu; gọi lại trong cooldown trả offered=false |
| E2E ép lỗi 429 ở read sau đóng | PASS: UI hiện Mở lại đợt và khóa import, đồng thời báo lỗi tải lại |
| Bearer/PUT preflight HTTP trên backend mới | HTTP 200 |
| git diff --check | PASS |

QA backend `127.0.0.1:8197`, frontend `127.0.0.1:5197`, DB/credential riêng ở `/tmp/secai-devspace-fix-20261005`. Hai luồng sinh nội dung dùng Linch/model thật với thông số kết nối từ dịch vụ đang chạy. Riêng lượt chat QA cuối dùng sampling=1 để kiểm tra chắc chắn UI A/B, cooldown=600; không thay cấu hình production 0.1/600. Mock chỉ dùng trong unit tests và ca E2E cố ý gây 429 cho request tải items.

Dữ liệu nhập và phiếu đều ghi TECHNICAL QA ONLY, không phải nhãn chuyên gia, không dùng training. Các campaign nhập mẫu/kiểm tra lỗi refresh đã đóng; campaign live do hệ thống tạo giữ mở. Build kiểm thử đặt riêng; giao diện phục vụ hiện tại đã được giữ ở bản trước sửa để tránh ghép frontend mới với backend cũ.

## Toàn suite backend chưa đạt

Chạy toàn suite với DB tạm và `--maxfail=10`, chủ động dừng sau 319.54 giây: **508 passed, 7 failed, 1 skipped**, 2 warnings. Suite chưa chạy hết, không tuyên bố toàn suite pass.

- `tests/test_bughunt_phase0.py::test_bugbounty_root_and_arsenal`: thiếu `/home/ict/AIEngines/secai/claude-bug-bounty`.
- Năm test trong `tests/test_chat_poc_drilldown_regression.py`: `test_uc2a_search_log4shell_surfaces_log4j_scan_with_valid_ref`, `test_uc2b_fetch_same_run_returns_real_grounded_source_code`, `test_uc2c_ref_reused_in_a_different_turn_run_is_rejected`, `test_uc2d_resear_in_new_run_yields_a_fresh_fetchable_ref`, `test_uc4_fetch_budget_enforced_within_a_single_chat_run`: catalog QA rỗng, không có log4j-scan.
- `tests/multilang/test_api.py::test_composition_off_on_legacy_and_cors`: origin wildcard trong cấu hình hiện có. Đã chạy lại riêng test với middleware nguyên bản từ HEAD trong package tạm: cùng lỗi, 1 failed/1.85 giây; không phải do thêm PUT/Authorization.

Không đổi dữ liệu/catalog, guard hoặc test cũ để làm suite pass.

## Lệnh và bằng chứng

Backend liên quan:

```sh
POC_CATALOG_DB=/tmp/secai-review-fix-poc.db \
GATEWAY_DB_PATH=/tmp/secai-review-fix-gateway.db \
.venv/bin/python -m pytest -q \
  tests/test_review_middleware.py tests/test_review_api.py \
  tests/test_review_store.py tests/test_review_auth.py tests/test_review_export.py \
  tests/test_review_live.py tests/test_review_templates.py tests/test_fe_review_proxy.py
```

Frontend: `npm run test`, `npm run build`. E2E:

```sh
cd /tmp/secai-devspace-fix-20261005
E2E_TELEMETRY_DISABLED=1 npx e2e run tests/reward.e2e.ts tests/live.e2e.ts tests/recovery.e2e.ts
```

- Lượt kiểm tra cuối sau bổ sung ẩn thống kê cũ: 65 test frontend pass, build pass; chạy riêng recovery E2E với output `.e2e-recovery/`.
- Runner report, screenshot, video và trace: `/tmp/secai-devspace-fix-20261005/.e2e/report.json`, `.e2e/summary.md`, `.e2e/artifacts/`.
- ZIP: `/tmp/secai-devspace-fix-20261005/raw.zip`, `preference.zip`, `before-approval.zip`.
- Live/cooldown: `/tmp/secai-devspace-fix-20261005/live-result.json`.
- Full-suite log: `/tmp/secai-review-fix-suite/pytest.log`.
- Test hồi quy được thêm vào repo: `tests/test_review_middleware.py`, test skip trong `tests/test_review_api.py`, `secai-fe-devspace/src/screens/review/ReviewAdmin.test.tsx`.

Credential QA và model-env là file riêng 0600, không đưa vào Git hoặc báo cáo.
