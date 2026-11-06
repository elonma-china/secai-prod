# Kết quả kiểm chứng dịch file và OCR — bị chặn

Ngày 2026-10-04. Chưa hoàn tất nghiệm thu; không merge main hoặc triển khai production.

## Kết quả đã xác nhận

- Backend `fix/file-ocr-stability`: mã sản phẩm/test `bef2e05`, tài liệu `efcc355`. TXT/DOCX/DOC đã qua API → worker → kết quả với agent dịch hiện có. PNG/JPEG nhỏ đã qua luồng đó trên runtime trước; chưa kiểm tra lại trên runtime cuối.
- Full suite cuối: **1161 pass, 7 fail kế thừa, 1 skip**, exit 1. So sánh đủ 925 ID baseline: không mất ID, không đổi trạng thái, không có lỗi mới. Không gọi full suite là pass.
- Vanguard lượt 2/3 `3e69458`: **256 pass** (14 ca Vanguard + 242 hồi quy translate-v2). VG-F01 DOCX mã hóa và VG-F02 SQLite WAL đồng thời đã đóng, gồm test hai web process thật trên loopback. Không có finding Critical/High mới trong phạm vi chạy.
- OCR dùng Qwen/Qwen3-VL-2B-Instruct, revision89644892e4d85e24eaac8bacfd4f463576704203. Candidate64d869b, evidence3a2691a. Một trang tiếng Nhật thật HTTP200 trong64.425s,1811ký tự; GPU peak4908MiB,free thấp nhất3859MiB,193mẫu đo. Đây là đối chứng chức năng, không phải chứng nhận chất lượng toàn bộ ngôn ngữ.

## Điểm chặn và điều kiện dừng

- Chất lượng tài liệu thật chưa đạt. Checkpoint40ảnh×3lượt:18lần đã thử,12HTTP200,6timeout,102chưa chạy. HTTP200 không được coi là chất lượng đạt. Ba phương án cải thiện đã dùng hết: chống lặp token không đạt, tăng độ phân giải chỉ khôi phục một số trang, chỉnh hình học chẩn đoán cho ảnh Trung đạt56.64% (1−CER) <65%, ảnh Ả Rập vẫn timeout. Không thay gold hoặc hạ ngưỡng.
- Đã dùng tối đa3checkpoint OCR; không thử model thứ4. Không làm thêm task lựa chọn model dịch của phiên khác.
- Runtime đã qua5vòngreview; R5 `1d11e88` còn **OCR-R7 Medium**: sau khi xác minh owner, nhánh cleanup có thể bỏ qua sample dưới mức reserve2048MiB khi child còn sống. Khóa vẫn giữ tới khi bộ nhớ hồi, nhưng điều đó không xóa vi phạm guard đã xảy ra. Dừng sửa tự động theo giới hạn đã chốt.
- Các khoảng trống Vanguard: upload bị ngắt và biên multipart còn thiếu; worker kill/restart và success-first race; log triển khai/canary; audit dependency. LowVG-L01: môi trường test olefile0.46 khác yêu cầu>=0.47. Khóa SQLite kéo dài có thể làm startup chờ khoảng10phút.
- Chất lượng dịch downstream được tách riêng: đã quan sát một đoạn DOC “Footer” giữ nguyên. Không kết luận đạt đủ18hướng dịch chỉ từ routing/testHTTP.

## Trạng thái triển khai

Dịch vụ OCR riêng PID54546 đã được dừng sau khi xác minh idle; GPU trống8772MiB, ba tiến trình sẵn có giữ nguyên. Endpoint OCR riêng hiện không sẵn sàng. Chưa ghép nhánh tích hợp chính thức, chưa merge main, chưa chạy smoke ảnh trên runtime cuối. Không có tuyên bố sẵn sàng production.

## Nhánh và phạm vi thay đổi

- `feature/import-file-translate` / `fix/translate-import-lifecycle-tokens`: nhập file gốc và sửa vòng đời/token.
- `feature/image-ocr-translate`: OCR backend gốc.
- `fix/file-ocr-stability`: sửa documents/jobs/worker/ocr_client/ocr_correction, launcher, test, tài liệu.
- File dùng chung chạm trong đợt ổn định: `src/secai_gateway/agent/runner.py`, thêm chế độ lỗi strict tùy chọn; mặc định cũ giữ nguyên. Không thay API cũ.
- `test/translate-real-quality`: chứa runtime và bằng chứng; lịch sử còn code model dịch ngoài phạm vi, **không merge cả nhánh**. Manifest file OCR cần bàn giao nằm trong evidence runtime.
- Các nhánh review/Vanguard riêng được giữ làm bằng chứng. FE không sửa theo phạm vi BE mới nhất. Main không được merge.

## Bằng chứng

- [Tiêu chí](/home/ict/secai-file-ocr-stability-wt/secai/secai-agent-gateway/docs/criteria/file-ocr-stability.md)
- [Backend và full suite](/home/ict/secai-file-ocr-stability-wt/secai/secai-agent-gateway/docs/test-results/file-ocr-stability-backend.md)
- [Vanguard lượt2](/home/ict/secai-file-ocr-vanguard-r2-wt/secai/secai-agent-gateway/docs/security-tests/file-import-stability-report-r2.md)
- [Review OCR cuối](/home/ict/secai-ocr-cleanup-review-wt/secai/secai-agent-gateway/docs/reviews/ocr-runtime-stability-r5.md)
- [Evidence, model/hash và manifest runtime](/home/ict/secai-translate-quality-runner-wt/secai/secai-agent-gateway/evals/translation_ocr/real_quality/OCR_RUNTIME_EVIDENCE.md)
- [Hướng dẫn local API](/home/ict/secai-file-ocr-stability-wt/secai/secai-agent-gateway/docs/examples/translate-v2-local-operator.md)
- [STATE](STATE.md), [LOG](LOG.md).

Tài liệu API bàn giao cuối và review tích hợp Astra B chưa được đánh dấu hoàn tất vì các cổng nghiệm thu còn bị chặn. Muốn tiếp tục sửa OCR cần quyết định mở lại giới hạn vòng lặp; không tự coi việc dừng là hoàn thành.
