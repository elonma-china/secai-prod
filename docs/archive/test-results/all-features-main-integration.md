# Kiểm tra tích hợp 5 chức năng vào main — 2026-10-04

## Phạm vi đã đối chiếu

Base local main: `0650f9d7d0b40f9eefb94f3059e1516d842ac112`.
Remote main trước cập nhật: `ce9cd47ee1273018fec1c5cbab944a86d68d6ed5`.

| Chức năng | Mã/API và giao diện | Nhánh đã tích hợp |
|---|---|---|
| Báo cáo theo mẫu, chỉnh bằng prompt, lưu báo cáo và revisions | `report_v2/api.py`, `report_v2/store.py`, `ReportMvpPanel.tsx`, `/ai/v2/reports` | `integration/report-feature` (`9fac0ac`) |
| Upload và dịch tệp | `api/translate_v2/routes.py`, `translation/worker.py`, `TranslateScreen.tsx`, `/ai/v2/translate/files` | `feature/import-file-translate`, `fix/translate-import-lifecycle-tokens` |
| OCR ảnh và pipeline dịch | `translation/ocr_client.py`, `translation/ocr_preprocess.py`, worker, `/ai/v2/translate/images` | `feature/image-ocr-translate`, `fix/file-ocr-stability`, `integration/file-ocr-main-final` (`0650f9d`) |
| Dịch đa ngôn ngữ và batch tệp | `multilang_app.py`, `api/multilang/routes.py`, `TranslateScreen.tsx`, `/ai/v3/translate` | `integration/multilang-final-merge` (`6f954d0`), `feature/multilang-file-batch-ui` |
| Expert review, A/B feedback, đánh giá, export và template | `api/reviews.py`, `state/review_store.py`, `ReviewScreen.tsx`, `/review-api` | `integration/review-main` (`13efa54`), `feat/expert-review-mvp` |

Các feature cần cấu hình server và quyền truy cập theo contract hiện có.
Multilingual dùng entrypoint `secai_gateway.multilang_app:app` và feature flags;
entrypoint legacy `secai_gateway.app:app` vẫn được giữ tương thích.

## Phần bổ sung trong lượt tích hợp này

- Merge `fix/translate-v2-semantic-fidelity` bằng three-way merge, giữ nguyên
  strict transformation errors và translation specialist orchestration trên main.
  Đây là sửa profile Linch `translate_v2`; worker file/ảnh hiện dùng HTTP specialist,
  nên không suy ra chất lượng dịch file tăng từ thay đổi này.
- Đưa proxy báo cáo đã chạy trên devspace vào Git: `REPORT_GATEWAY_PROXY` hoặc
  `.report-upstream`, fallback gateway chung, giới hạn đúng prefix `/ai/v2/reports`.
- Bổ sung upstream dịch/OCR riêng cho Devspace qua `TRANSLATION_GATEWAY_PROXY`
  hoặc `.translation-upstream`, giới hạn đúng hai prefix v2/v3 translate. Đây là
  phần nối giao diện chính tới stack đã bật OCR và đa ngôn ngữ.
- Hai regression test HTTP thực qua server local kiểm tra upstream riêng, fallback,
  query string và các prefix dịch/review không bị chuyển sang backend báo cáo.
- Sửa test mock từ nhánh fidelity để có `strict_transform_errors` như runner hiện tại.
- Sửa fixture batch file/ảnh: polling nhanh cần lookup quota 1000 như các test batch
  tương tự. Quota runtime không đổi. Run cũ có đủ 4 job succeeded trong DB nhưng hai
  lượt polling/result hết thời gian sau khi chạm quota mặc định.

## Nhánh riêng không phải phần thiếu của 5 luồng

- `feature/document-ocr-agent`: checkpoint CPU với backend tiêm qua Protocol, chưa
  có concrete OCR backend. Không thay thế luồng OCR ảnh đã hoạt động qua v2.
- `feature/shared-gpu-stage-broker` và phần broker của `feature/mt-on-demand-gpu-lock`:
  runtime thay thế chưa vượt gate live. On-demand MT và shared GPU admission được
  chọn đã có trên main; không chuyển sang broker thử nghiệm trong lượt này.
- Các nhánh review/eval/quality chứa checkpoint độc lập không cần merge toàn bộ
  lịch sử để sử dụng các chức năng đã được tích hợp.

## Kiểm chứng

- Review độc lập: không còn finding Important trong diff tích hợp cuối.
- Backend liên quan: **378 passed**, gồm reports, project/session, expert review,
  multilingual core/assessment/security, worker/OCR, sampling và Devspace proxy.
- Frontend: `npm ci`, **47 tests passed**, `VITE_REPORT_FEATURE_ENABLED=true npm run build`
  thành công; cảnh báo bundle lớn hơn 500 kB.
- `git diff --check`: pass.
- HTTP read-only trên host: `/health` cổng 8131 trả 200; expert review trả 401 khi
  chưa xác thực; reports cổng 18183 và proxy devspace 5173 trả 401 khi thiếu key;
  multilingual capabilities cổng 18185 và devspace riêng 5179 trả 200.
- Đọc template với key hiện có qua cả 18183 và devspace 5173: 200. Đọc lại hai
  báo cáo đã lưu trước rollout: mỗi báo cáo còn đủ 2 revisions; không sửa dữ liệu.
- Chưa gọi inference mới hoặc thuê GPU trong lượt kiểm tra này. Các kiểm chứng
  trên xác nhận chức năng/routing, không thay thế đánh giá chất lượng ngữ nghĩa.

Full suite được chạy với interpreter `/tmp/secai-multilang-final-env/bin/python`,
`PYTHONPATH=src`, session memory và các đường dẫn PoC/artifact riêng trong `/tmp`.
Run rộng trước sửa fixture batch: **1718 passed, 8 failed, 1 skipped**, 578.89 s.
Bảy failure là các fixture Bughunt/PoC ngoài checkout đã được ghi nhận trước đó;
failure còn lại là lookup quota trong fixture batch đã sửa ở trên. Run này không
phải full-suite PASS. Log/JUnit nằm ngoài Git tại `/tmp/secai-allfeatures-full.log`
và `/tmp/secai-allfeatures-full.xml`.

Sau sửa, `tests/test_devspace_proxy.py tests/test_multifile_batch.py
tests/test_translate_v2_sampling.py`: **26 passed**. Run chức năng rộng cuối chỉ
deselect đúng 7 test thiếu fixture ngoài checkout; danh sách node ID ở
`/tmp/secai-allfeatures-excluded.json`, log và JUnit ở
`/tmp/secai-allfeatures-functional.log` và `/tmp/secai-allfeatures-functional.xml`.

Kết quả run chức năng cuối: **1719 passed, 1 skipped, 7 deselected**, 541.83 s.
Đây là run đã loại fixture ngoài checkout, không gọi là full-suite PASS.
Sau khi đối chiếu/giải quyết merge proxy: **81 regression tests passed**; frontend
build bật cả `VITE_REPORT_FEATURE_ENABLED`, `VITE_TRANSLATE_V2_FILES_ENABLED` và
`VITE_TRANSLATE_V2_IMAGES_ENABLED`. Image candidate import đủ năm nhóm module và
`pip check` không có dependency lỗi.
