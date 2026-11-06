# Kết quả cải tiến A/B và dịch — 2026-10-07

Kết quả lượt tiếp theo về bảy failure phụ thuộc môi trường và diễn tập baseline được ghi riêng tại [kiểm chứng offline follow-up](ab-translation-offline-followup-20261007.md). Các số liệu bên dưới giữ nguyên lịch sử của đợt đầu.

Phân công theo yêu cầu: Sol sửa product code, Luna tạo/chạy test, Astra tạo tiêu chí và review độc lập. Base `98d2b72874f325a3d7e9e7c1b4e6f0cff2289220`, working tree có các sửa đổi frontend của người dùng được giữ nguyên. Đợt này sửa độ tin cậy; không đổi model, chạy inference, triển khai hoặc tuyên bố tăng chất lượng ngôn ngữ.

## Thay đổi

- Worker phân biệt dừng job nội bộ và cancellation bên ngoài. Cancellation ngoài được truyền ra; job running thiếu kết quả không bị ghi thành công/xoá input. Job có thể reclaim sau hết lease. DELETE và generation/deadline fences giữ nguyên.
- Gateway training events chỉ trả field telemetry hợp lệ; bỏ field ngoài contract như stdout/token, giữ cursor và pagination.
- A/B xử lý identity đến muộn từ caller màn chat tới widget; request hiện tại được dùng lại khi StrictMode replay, response/submit kiểm đúng identity và turn. Opt-out đã biết vẫn được tôn trọng.
- Đổi filter/status/query/page/tab/unmount làm response chi tiết/duyệt hàng loạt hết hiệu lực; response cũ không mở lại dialog hoặc ghi lỗi/trạng thái vào ngữ cảnh mới.
- Escape/Đóng bản nháp yêu cầu xác nhận bỏ thay đổi. Từ chối giữ lựa chọn/nhận xét, busy không đóng, backdrop dirty và focus handling giữ nguyên.
- Fixture CORS của multilang khai báo policy rõ ràng, không thay cấu hình production.

## Evidence

| Kiểm chứng | Kết quả |
|---|---|
| Backend regression focused Q01/Q02/Q06/Q07 | 10 passed; gồm external cancel/reclaim, DELETE, OCR cancel, fence và telemetry/CORS |
| A/B widget/admin + chat caller regression | 19 passed |
| Frontend toàn bộ sau caller fix | 21 files, 175 tests passed |
| Frontend build | Thành công; cảnh báo bundle >500 kB còn |
| Evaluation/multifile offline | 222 passed trong 41,99s |
| CORS với env ngoài wildcard và explicit origin | Cả hai lượt pass |
| Backend full suite | 2.128 passed, 7 failed, 4 skipped, 7 xfailed, 64 warnings; 819,98s, exit 1 — không PASS |
| Astra review source cuối | APPROVE, không còn blocker trong 7 file production đã đọc |

Số focused/evaluation là tập con của các suite khác; không cộng thành tổng số test độc lập. Bộ test mới xác nhận bug trước sửa. Riêng caller chat đã tái chạy RED trong bản frontend scratch với caller nguyên bản từ HEAD và mock đường dẫn đúng, rồi GREEN trên source cuối; không sửa source production để chiều fixture.

Lệnh frontend: `npm test -- --reporter=dot`, `npm run build -- --outDir /tmp/secai-quality-20261007/frontend-build-final`. Lệnh full backend: `POC_CATALOG_DB=/tmp/secai-quality-20261007/poc-catalog.db PYTHONPATH=$PWD/src .venv/bin/python -m pytest -q`. Lệnh evaluation và bằng chứng synthetic xem [baseline readiness](../reports/translation-baseline-readiness-20261007.md).

Log/runtime evidence local: `/tmp/secai-quality-20261007/full-backend.log`, `test-verification.md`, `astra-review.md`, `eval-verification.md`. Build output và dữ liệu runtime không đưa vào Git.

## Các lỗi full suite đã đối chiếu baseline

Các test dưới đây phụ thuộc cây Bughunt hoặc nội dung catalog/mã nguồn PoC ngoài fixture synthetic. Trong worktree kiểm chứng với catalog scratch, thiếu `log4j-scan` gây các `StopIteration`, còn exploit không có source code để phát preview. Luna đã replay đúng bảy node trên checkout gốc với cùng cấu hình catalog scratch: **cả bảy đều fail tương ứng**, 31,70s. Log `/tmp/secai-quality-20261007/original-attribution-same-env.log`. Không có failure mới trong phép đối chiếu này; full suite vẫn không PASS và không chuyển các lỗi này thành skip.

- `tests/test_bughunt_phase0.py::test_bugbounty_root_and_arsenal`
- `tests/test_chat_poc_drilldown_regression.py::test_uc2a_search_log4shell_surfaces_log4j_scan_with_valid_ref`
- `tests/test_chat_poc_drilldown_regression.py::test_uc2b_fetch_same_run_returns_real_grounded_source_code`
- `tests/test_chat_poc_drilldown_regression.py::test_uc2c_ref_reused_in_a_different_turn_run_is_rejected`
- `tests/test_chat_poc_drilldown_regression.py::test_uc2d_resear_in_new_run_yields_a_fresh_fetchable_ref`
- `tests/test_chat_poc_drilldown_regression.py::test_uc4_fetch_budget_enforced_within_a_single_chat_run`
- `tests/test_gateway.py::test_exploit_stream_section1_preview_arrives_before_final`

Bảy xfail còn lại là bốn malformed-JSON/auth-order cases, hai cooldown cases và duplicate JSON keys; xfail telemetry đã được thay bằng assertion phải pass. Không thay đổi các kỳ vọng còn lại để làm suite xanh.

## Phạm vi kết luận

Test offline và source review chứng minh các hành vi phần mềm trong phạm vi được kiểm. Chưa có baseline live mới cho 18 hướng, chưa chấm OCR thực, chưa đo latency/cost/GPU và chưa kiểm browser/screen reader thực. Giữ các gate chất lượng gốc; không đổi corpus/reference/ngưỡng để hợp thức hoá kết quả. Live A/B vẫn là thu thập preference, chưa phải thí nghiệm kiểm soát model/evidence và chưa mở production fine-tune.

Người dùng đã xác nhận phạm vi offline. Root tích hợp đúng snapshot source/test đã review về checkout người dùng, kiểm checksum và giữ nguyên các file dirty ngoài phạm vi; không commit/push/deploy. Worktree và log được giữ lại để đối chiếu.
