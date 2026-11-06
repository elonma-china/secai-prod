# Report MVP — baseline pytest local (chưa hoàn tất)

Ngày 2026-10-04. Checkout `chore/report-warden` ở `a2f71e4c8fdff6c53d0cead55915ccb5508a473f` (base `ce9cd47`). Python 3.12.3, pytest 9.1.1 từ `.venv` có sẵn ở checkout gốc; `PYTHONPATH` trỏ tới `src` của worktree WARDEN. Không sửa test, product code hay shared venv. Lượt baseline này chạy song song triển khai MVP theo chỉ đạo mới của người dùng; **không phải bằng chứng suite PASS hoặc bằng chứng không hồi quy**.

## Cách chạy và giới hạn môi trường

- Workdir: `/home/ict/secai-report-warden-wt/secai/secai-agent-gateway`; không có `.env` trong worktree. `tests/conftest.py` đặt Redis/session vào memory và tránh classifier gọi embedding thật; nó tự đặt `AIRGAP=false` để test network feature, nên không thể chỉ dựa vào fixture để chặn outbound.
- Đặt `PYTHONPATH=/tmp/secai-report-warden-baseline:$PWD/src` và `PYTHONNOUSERSITE=1`. File `/tmp/secai-report-warden-baseline/sitecustomize.py` chặn DNS ngoài local và kết nối TCP IPv4/IPv6 qua `socket` Python. Đây là guard ở tầng Python, **không phải network namespace của OS**; `unshare -n` và `bwrap --unshare-net` bị hệ thống từ chối. Không chạy model API hay live inference có chủ đích; guard không chứng minh mọi subprocess/native library đều không thể ra mạng.
- Lượt đầu: `timeout 600 env PYTHONPATH=/tmp/secai-report-warden-baseline:$PWD/src PYTHONNOUSERSITE=1 <shared-venv>/bin/python -m pytest -q --tb=short --junitxml=/tmp/secai-report-warden-baseline/junit.xml tests`. Exit 2: **21 collection errors**, tất cả do ứng dụng tạo DB mặc định dưới `/srv/exploitdb` và bị `PermissionError`; chưa chạy một test nào. Đây là lỗi cấu hình môi trường baseline, không phải 21 test fail.
- Lượt chính: thêm `POC_CATALOG_DB=/tmp/secai-report-warden-baseline/poc-catalog.db`, `GATEWAY_DB_PATH=/tmp/secai-report-warden-baseline/gateway.db`, `SESSION_DB_PATH=/tmp/secai-report-warden-baseline/sessions.db` vào lệnh trên, JUnit đích `junit-run2.xml`. Không sửa file cấu hình. Pytest thu thập **925 test**. Ngắt bằng SIGINT sau 255.27 giây vì nhóm test exploit cuối file tiến rất chậm; exit 2: **265 passed, 6 failed, 1 skipped**. Test thứ 273 bị ngắt trước kết quả; JUnit là dữ liệu từng phần.
- Lượt tiếp nối chỉ chọn các file bắt đầu từ `tests/test_fetch_reference.py` trở về sau trong thứ tự thu thập (danh sách ở `/tmp/secai-report-warden-baseline/remainder-files.txt`), giữ cùng env và guard; `timeout 300 ... python -m pytest -q --tb=short --junitxml=/tmp/secai-report-warden-baseline/junit-remainder.xml $(cat /tmp/secai-report-warden-baseline/remainder-files.txt)`. Ngắt bằng SIGINT để nhường review sau 62.18 giây; exit 2: **38 passed**, chưa có fail mới được quan sát. Đây không phải lần chạy lại toàn suite.

Tổng cộng **310 test phân biệt đã kết thúc**: 303 pass, 6 fail, 1 skip. Một test đang chạy khi ngắt (`tests/test_exploit_relevance_gate.py::test_exploit_genuine_freetext_no_cve_still_tiers_real`) và **614 test chưa có kết quả**. Không thể lấy tỉ lệ pass toàn suite từ phần đã hoàn tất. Các lượt bị ngắt cũng không thỏa yêu cầu full regression của MVP.

## Sáu failure đã quan sát

| Test | Bằng chứng ngắn |
|---|---|
| `tests/test_bughunt_phase0.py::test_bugbounty_root_and_arsenal` | `resolve_bugbounty_root()` trả `/home/ict/AIEngines/secai/claude-bug-bounty`; thư mục đó không tồn tại (`assert root.is_dir()` fail). |
| `tests/test_chat_poc_drilldown_regression.py::test_uc2a_search_log4shell_surfaces_log4j_scan_with_valid_ref` | Real PoC catalog không có hit cho CVE-2021-44228 (`assert hits`, nhận `[]`). |
| `tests/test_chat_poc_drilldown_regression.py::test_uc2b_fetch_same_run_returns_real_grounded_source_code` | Không tìm thấy hit `log4j-scan` (`StopIteration`). |
| `tests/test_chat_poc_drilldown_regression.py::test_uc2c_ref_reused_in_a_different_turn_run_is_rejected` | Không tìm thấy hit `log4j-scan` (`StopIteration`). |
| `tests/test_chat_poc_drilldown_regression.py::test_uc2d_resear_in_new_run_yields_a_fresh_fetchable_ref` | Không tìm thấy hit `log4j-scan` (`StopIteration`). |
| `tests/test_chat_poc_drilldown_regression.py::test_uc4_fetch_budget_enforced_within_a_single_chat_run` | Không tìm thấy hit `log4j-scan` (`StopIteration`). |

`test_uc3_license_gated_or_unfetched_poc_returns_metadata_only_not_fabricated` được skip. Catalog DB được chuyển sang `/tmp` để tránh lỗi permission và không được seed bằng dữ liệu ngoài; vì vậy năm failure PoC catalog là **dependency/fixture chưa có trong baseline này**, chưa đủ chứng cứ quy lỗi cho report feature hoặc kết luận product bug. Không đổi test/tiêu chí để hợp thức hóa kết quả.

## Log gốc ngoài repo

| Log | SHA-256 |
|---|---|
| `/tmp/secai-report-warden-baseline/pytest.log` (collection error) | `8f0952ceb2910628cb6b9e89312bd923df83af689ac95b1645e09676e841df52` |
| `/tmp/secai-report-warden-baseline/pytest-run2.log` | `11ab8a0138c1998453fdc9d1f33317c34b260cd9088b3ce528ae936c91030c7a` |
| `/tmp/secai-report-warden-baseline/pytest-remainder.log` | `870c641f76b2e3a3a57021f51840ebe7b55cf70b42da0c59bee95353e3685fd4` |

Hệ quả cho review MVP: các test mới liên quan report vẫn phải pass toàn bộ; so sánh hồi quy với baseline này phải nêu rõ 6 failure cũ, 1 skip và phạm vi chưa chạy. Cần một full suite hoàn tất trong môi trường có dependency PoC phù hợp để khẳng định không hồi quy toàn backend. Ngưỡng nội dung 70% của MVP là phép đo riêng, không biến baseline test chưa hoàn tất thành PASS.
