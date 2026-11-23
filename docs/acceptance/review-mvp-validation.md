# Kiểm chứng MVP đánh giá và template báo cáo

Ngày: 2026-10-04. Nhánh: `feat/expert-review-mvp`. Worktree: `/home/ict/secai-review-mvp-wt`.

Kết luận: PASS cho phạm vi MVP đã kiểm chứng bên dưới; chưa deploy hoặc kiểm chứng chất lượng model thật. Mức 70% do người dùng yêu cầu được hiểu là ưu tiên chức năng cốt lõi, không phải cho phép test chức năng thất bại.

## Kết quả tự động

Lượt cuối sau sửa cleanup: **118 passed**, 1 cảnh báo thư viện FastAPI/Starlette về httpx; 11,66 giây. Bộ kiểm tra:

```sh
POC_CATALOG_DB=/tmp/secai-review-final/poc.db \
GATEWAY_DB_PATH=/tmp/secai-review-final/gateway.db \
SESSION_DB_PATH=/tmp/secai-review-final/sessions.db \
PYTHONPATH=src \
/home/ict/AIEngines/secai/secai-agent-gateway/.venv/bin/python -m pytest -q \
  tests/test_review_store.py tests/test_review_auth.py tests/test_review_api.py \
  tests/test_review_export.py tests/test_review_live.py tests/test_review_templates.py \
  tests/test_projects_sessions.py tests/test_prompts.py tests/test_security.py tests/test_sse.py
```

Gồm 28 test review/live/template, 34 project/session và 56 prompt/security/SSE. Tests mock model/dịch vụ ngoài; không chạy training hoặc gọi model trả phí.

Frontend nested: `npm ci` và lượt cuối `npm run build` PASS. Vite cảnh báo JS chunk 625,84 kB; chưa tối ưu chia bundle. Npm báo 3 advisory ở dependency hiện có; không nâng dependency trong phạm vi này. `git diff --check` PASS.

## HTTP thật và trình duyệt

Đã chạy backend và frontend dev riêng với database, credential và fixture tổng hợp tại `/tmp/secai-review-smoke`, không dùng dữ liệu production.

| Luồng | Kết quả |
|---|---|
| API create/import/retry import | PASS, 5 mẫu nhập, retry bỏ qua 5 mẫu |
| API assignment và 5 kết quả đánh giá | PASS |
| IDOR, reviewer truy cập admin export | PASS, 404/403 |
| Nộp lại cùng payload, đóng campaign | PASS, không nhân phiếu; write campaign đóng trả 409 |
| Duyệt và export | PASS, đúng 2 winner từ 5 outcome và SHA-256 manifest khớp |
| UI admin tạo campaign, import file, phân công | PASS trong T3 collaborative browser |
| UI chuyên gia đăng nhập, chọn A/B, ghi chú, nộp | PASS |
| UI admin duyệt và tải preference ZIP | PASS |
| UI live chat hoàn tất -> sinh cặp -> chọn ngang nhau | PASS với fake runner, sample_rate=1 và cooldown=0 chỉ cho QA |
| UI template sửa bằng prompt -> xem nháp -> áp dụng -> render | PASS với fake runner |
| Token lưu trữ | Kiểm tra localStorage chỉ có user_id cũ, sessionStorage rỗng; token review ở memory |

API/live/template chạy thật; phần LLM trong hai luồng sinh nội dung được thay bằng fake runner rõ ràng. Kiểm chứng này chứng minh wiring, state và contract, không chứng minh chất lượng sinh nội dung/model hay hiệu quả RLHF/DPO.

## Review và sửa lỗi

Hai agent GPT-6.1-Sol medium triển khai backend/frontend và review chéo phần của nhau; phiên chính đọc diff và kiểm tra tích hợp. Các vấn đề đã xử lý:

- Không strip whitespace của prompt/candidate: giữ indentation và newline đến export; có test hồi quy FAIL -> PASS.
- Bản nháp template giới hạn 64 KiB như đầu vào; draft quá lớn bị từ chối, giữ bản gốc; có test hồi quy.
- Campaign live đóng được kiểm tra trước khi gọi model để tránh tốn công sinh B.
- Giải phóng session tạm bằng public Linch `release_session` trước xóa history; test registry Linch thật không gọi model xác nhận cleanup.

Kiểm chứng model thật được ghi riêng tại [Nghiệm thu live](review-live-acceptance.md); các kết quả fake runner bên dưới vẫn giữ nguyên phạm vi bằng chứng ban đầu.
- Hiển thị văn bản gốc của candidate bên cạnh Markdown; không chỉ dựa vào renderer định dạng.

Không còn lỗi chặn được phát hiện trong các đường đã review/test. Đây không phải khẳng định mọi tình huống đều đã được kiểm thử.

## Bảo toàn API cũ

Chỉ thêm endpoint dưới `/review-api/*`. `api/routes.py`, `api/projects.py`, `api/middleware.py`, `security.py` và prompt `report.md` không đổi so với base. `profiles.yaml` chỉ thêm hai profile template không tool; các profile cũ giữ nguyên. `app.py` đăng ký module theo cờ bật, `config.py` thêm settings riêng. Frontend `api.ts` giữ nguyên; UI chat/báo cáo chỉ bổ sung lời mời/tab dùng API mới, hàm tạo báo cáo và thống kê cũ giữ nguyên.

## Toàn suite nền chưa pass

Trước thay đổi, toàn suite không collect được khi thiếu `.env` vì default PoC DB trỏ `/srv/exploitdb`. Sau chuyển sang DB tạm, chạy được một phần; dừng sau gần 5 phút với **264 passed, 6 failed, 1 skipped**, chưa hoàn tất. Các failure:

- `tests/test_bughunt_phase0.py::test_bugbounty_root_and_arsenal`: thiếu cây/arsenal ngoài checkout.
- `tests/test_chat_poc_drilldown_regression.py::test_uc2a_search_log4shell_surfaces_log4j_scan_with_valid_ref`
- `tests/test_chat_poc_drilldown_regression.py::test_uc2b_fetch_same_run_returns_real_grounded_source_code`
- `tests/test_chat_poc_drilldown_regression.py::test_uc2c_ref_reused_in_a_different_turn_run_is_rejected`
- `tests/test_chat_poc_drilldown_regression.py::test_uc2d_resear_in_new_run_yields_a_fresh_fetchable_ref`
- `tests/test_chat_poc_drilldown_regression.py::test_uc4_fetch_budget_enforced_within_a_single_chat_run`

Năm test PoC yêu cầu catalog thật có log4j-scan; DB tạm không có. Không sửa dữ liệu, guard hoặc test để hợp thức hóa. Không tuyên bố toàn suite pass.

Lượt toàn suite sau sửa cleanup cũng tái hiện đúng 6 lỗi dependency/catalog nêu trên. Chủ động dừng sau 262,29 giây: **263 passed, 6 failed, 1 skipped**, chưa hoàn tất. Log: `/tmp/secai-review-full/results.log`. Các test feature/hồi quy riêng hoàn tất với 118 passed; không dùng kết quả đó để tuyên bố toàn suite pass.

## Giới hạn và phần tiếp theo

- Live invitation chỉ cho người đã đăng nhập token review; chưa tích hợp SSO/người dùng ẩn danh.
- Sampling 10%, cooldown600s là đề xuất khởi đầu. Cooldown/inflight theo process: dùng một worker MVP, chưa có hạn mức toàn cluster.
- Câu B tái sinh từ ngữ cảnh công khai; chưa chia sẻ chính xác snapshot RAG của câu A. Metadata ghi unknown, không bịa bằng chứng.
- Template lưu memory và nhập/xuất `.md`; chưa có thư viện template/version dùng chung.
- Người dùng xác nhận input sạch; không cung cấp detector prompt injection chuyên dụng. Giữ guard có sẵn, không coi checkbox là bảo đảm an toàn.
- Rubric v1, chất lượng/khối lượng nhãn, bộ hồi quy kiến thức và train/eval RLHF/DPO cần đợt làm tiếp. Chưa pilot với chuyên gia thật, chưa đo cải thiện model.
- Chưa tối ưu nhiều người chấm, stress test, backup restore thực tế hoặc accessibility toàn bộ giao diện.
- Code chưa commit/push/merge/deploy; giữ trong worktree riêng để người dùng kiểm tra trước khi tích hợp.

## Sửa ba finding review — 2026-10-04

- Live A/B: seed các lượt user/assistant trước đó vào session UUID tạm bằng Linch session store; chỉ truyền câu hỏi cuối cho `complete`. Không dùng history làm grounding, không sửa runner/API chat cũ. Test dùng runner/hook Linch thật và mock duy nhất kết quả generation xác nhận history có CVSS không ghi đè câu hỏi MFA, không đánh dấu prefetch grounded, cleanup store/cache vẫn hoạt động.
- UI live: reset `saving` khi turn đổi; regression trình duyệt giữ request nộp phiếu cũ pending, chuyển turn, nhận cặp mới rồi trả response cũ. PASS: cặp mới vẫn chọn/nộp được, response cũ không thay phiếu mới. Chạy qua Vite tại `/tests/live-review-regression.html`; test mock fetch, không gửi API/model request.
- Import: prompt phải bắt đầu và kết thúc bằng user, không chứa candidate answer ở cuối; batch có mẫu sai trả 422 và không nhập một phần. Live input dùng cùng ràng buộc đầu/cuối.

Lượt sau sửa: **122 passed**, 1 warning FastAPI/Starlette, 12.08s, cùng bộ feature + sessions/prompts/security/SSE đã liệt kê. Frontend `npm run build` PASS, cảnh báo chunk >500 kB còn như trước. `git diff --check` PASS. Không chạy lại toàn suite phụ thuộc catalog/arsenal thiếu; kết quả full-suite trước vẫn chưa pass.

Live retest trên gateway riêng: prompt history CVSS cao nhất → MFA; HTTP 200, offered=true, B giải thích đúng MFA, không bị thay bằng fallback CVSS. Evidence riêng `/tmp/secai-review-live-20261004/review-fix-live.json`; chỉ QA, không dùng training. Đã khởi động lại gateway/preview riêng để dùng code mới. Chưa merge/deploy production.
