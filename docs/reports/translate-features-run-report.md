# Báo cáo lượt triển khai dịch file và OCR

Trạng thái: **STOPPED/BLOCKED — chưa đủ điều kiện tích hợp hoặc đưa lên production.** Benchmark OCR độc lập đã hoàn tất; các cổng nghiệm thu đầy đủ vẫn bị chặn.

## Phạm vi đã chốt

Backend trên base `main` tại `ce9cd47ee1273018fec1c5cbab944a86d68d6ed5`; không sửa FE, không merge main. API mới `/ai/v2/translate` gọi trực tiếp, không đăng nhập. Hỗ trợ file TXT/DOCX/DOC và ảnh PNG/JPEG, từ chối PDF. Phạm vi ngôn ngữ: `vi, en, zh, ja, ko, fr, de, es, ru, ar`, 18 hướng `vi ↔ ngôn ngữ còn lại`; giữ identity. OCR chạy cô lập trên Vast hiện có, tối đa 3 checkpoint.

## Lý do dừng

Warden áp dụng đúng quy tắc người dùng: “Nếu số lỗi không giảm sau 2 vòng liên tiếp, dừng task đó, ghi nguyên nhân và các lựa chọn, chuyển sang task khác nếu độc lập được.” Review import vòng 1 có 2 lỗi High; vòng 2 vẫn còn 2 lỗi High thuộc cùng nhóm hợp đồng. Các ca tái hiện cũ đã được sửa nhưng chưa xử lý hết nhóm lỗi. Không tiếp tục sửa import hoặc helper dùng chung để đi vòng điều kiện dừng. Đánh giá model OCR độc lập được tiếp tục trong giới hạn đã duyệt.

Bằng chứng: [review vòng 1](../reviews/import-file-translate-round-1.md), [review vòng 2](../reviews/import-file-translate-round-2.md), [quyết định warden](../loop/warden-review-round-2.md).

## Nhánh và checkpoint

| Nhánh | SHA | Trạng thái |
|---|---|---|
| `feature/import-file-translate` | `bfab8ebc96e25b23325fd72a1d3bf034e9aea315` | Dừng sau review 2 FAIL |
| `feature/image-ocr-translate` | ff311c12f97a1dd453495b0ff2b9ca269a49aba2 | Benchmark và self-test hoàn tất, chưa review độc lập |
| `chore/translate-criteria` | `3f209b8507c2edcf948c3b5aada77298600fec8f` | Tiêu chí và phân chia bằng chứng theo giai đoạn |
| `integration/translate-features` | Chưa tạo | Chặn bởi review import |
| `feature/fe-devspace-translate` | Không tạo | FE ngoài phạm vi theo quyết định người dùng |

Các worktree review độc lập được tạo riêng. Do giới hạn thread của công cụ collaboration, reviewer/warden dùng các phiên Codex CLI mới, không nhận context agent code, chạy trong worktree riêng. Lần warden đầu bị lỗi sandbox không được tính là audit; lần chạy sau đọc được file và có báo cáo bằng chứng.

## Thay đổi backend

Import đã có router mới, kiểm tra upload, TXT encoding, trích DOCX, converter DOC cô lập, chia đoạn, profile dịch riêng, nhận diện 10 ngôn ngữ, hàng đợi SQLite, idempotency, timeout/cancel/expiry và worker độc lập. Đây là các phần đã viết, **không phải kết luận tất cả yêu cầu đều đạt**.

Các file dùng chung đã sửa tối thiểu trong nhánh import:

| File | Lý do |
|---|---|
| `src/secai_gateway/app.py` | Gắn router/store và bộ lọc access log v2 |
| `src/secai_gateway/api/middleware.py` | Rate limit/CORS theo phạm vi v2 |
| `src/secai_gateway/agent/runner.py` | Bỏ date context cho các profile mới |
| `src/secai_gateway/prompts/profiles.yaml` | Thêm profile riêng, giữ profile cũ |
| `pyproject.toml`, `uv.lock` | Khai báo dependency; sửa lệch lock Linch 1.1 với yêu cầu có sẵn `>=1.2.1,<2` |

Danh sách đầy đủ từng file nằm trong [inventory checkpoint](translate-features-file-inventory.md). Workspace và thay đổi FE có sẵn của người dùng được giữ nguyên.

## Kiểm thử và hồi quy

| Lượt | Kết quả | Giới hạn kết luận |
|---|---|---|
| Baseline | 925 collected: 905 pass, 19 fail, 1 skip, 0 errors | 6 lỗi thiếu dữ liệu/dependency ngoài repo và 13 timeout quan sát được; không chứng minh tất cả đều vô hại |
| Import candidate review 1 | 999 collected: 979 pass, 19 fail, 1 skip, 0 errors | 19 node ID fail trùng baseline |
| Import candidate review 2 | 1014 collected: 994 pass, 19 fail, 1 skip, 0 errors | Giữ đủ 925 node ID cũ, thêm 89; fail/skip cũ không đổi, không có lỗi mới trong harness này |
| Reviewer độc lập vòng 1 | 73 test mới pass | Vẫn phát hiện 2 High qua ca kiểm tra bổ sung |
| Reviewer độc lập vòng 2 | 137 test liên quan pass | Vẫn phát hiện 2 High còn sót |
| DOC và worker cục bộ | 3 smoke test thực tế pass | Container DOC cô lập và worker process riêng; không thay thế kiểm thử bảo mật đầy đủ |
| Tương thích API cũ | 69 path/15 schema OpenAPI bằng nhau với hash seed cố định; JSON/SSE dịch cũ bằng nhau | Chỉ phạm vi probe đã chạy, dùng runner giả lập; không phải kiểm chứng mọi API live |
| OCR regression cuối | 966 collected: 946 pass, 19 fail, 1 skip, 0 errors; 39 focused pass | Giữ đủ 925 testcase ID baseline, thêm 41; 19 failure ID trùng baseline; full suite vẫn exit 1 |
| Independent TEST 62 mẫu × 3 | **Chưa chạy** | Không tính self-test của agent code thành TEST độc lập |
| Vanguard | **Chưa chạy** | Không có báo cáo bảo mật đạt |
| Integration và tài liệu API bàn giao cuối | **Chưa thực hiện** | Chưa qua các cổng trước đó |

## Finding còn mở

| ID | Mức độ | Vấn đề | Trạng thái |
|---|---|---|---|
| R2-01 | High | Khi worker/reaper dừng, trạng thái job đã thành failed nhưng file đầu vào không được dọn đúng hạn; quota lưu trữ có thể tiếp tục chặn job mới | Mở, dừng sửa theo warden |
| R2-02 | High | URL/hash/tên phần mềm kèm phiên bản sát chữ CJK/Ả Rập có thể không được nhận diện là literal; provider có thể đổi URL mà kết quả vẫn được chấp nhận | Mở, dừng sửa theo warden |

Đây là finding của **reviewer**, không phải finding Vanguard. Các ca lỗi CVE cạnh CJK, placeholder bị cắt và terminalize job quá hạn đã được sửa trong phạm vi ca tái hiện cũ; không vì thế đóng các lỗi còn sót ở trên.

## Vòng lặp

Import: 2/5 vòng review, 1 lượt sửa giữa hai review; dừng vì số lỗi High không giảm sau hai vòng. Chưa có hai lần tái phát liên tiếp *sau khi đã sửa*, nên không ghi sai rằng điều kiện đó đã kích hoạt. OCR: chưa qua review độc lập. Independent TEST và Vanguard: 0 lượt; chưa có vòng return Vanguard. Model OCR: đủ 3/3, không thử model thứ 4.

## Kết quả OCR: 3/3 model đã thử

| Model | Phép đo | Accuracy ký tự micro / macro | CVE F1 / IP F1 | Kết luận |
|---|---|---|---|---|
| PaddleOCR-VL-1.6 | 62 ảnh × 3 lượt, trực tiếp và có tiền xử lý | 98,373% / 98,149%; Ả Rập 71,56% / 77,83% | 100% / 100% | Có lỗi thứ tự đọc Ả Rập; 3 biến thể prompt trên bộ hiệu chuẩn riêng không khắc phục |
| Qwen3.5-2B | 3 ảnh hiệu chuẩn Ả Rập × 3 prompt | 26,72% / 17,56% / 18,32% micro theo prompt | Chưa đo trên corpus đầy đủ; có IP sai | Loại sau hiệu chuẩn, không chạy 62 × 3 |
| Qwen3-VL-2B-Instruct | 62 ảnh × 3 lượt, có tiền xử lý; 186/186 HTTP 200 | **99,148% / 98,908%**; Ả Rập **91,74% / 92,32%**, Việt **96,90% / 97,15%** | **100% / 97,5%** mỗi lượt | Ứng viên tốt nhất trong phép đo này; chưa chọn production |

Accuracy micro = `1 − tổng khoảng cách chỉnh sửa / tổng số ký tự tham chiếu`; macro = trung bình accuracy từng ảnh. Scorer giữ lỗi thứ tự đọc, tính dự đoán IP sai định dạng thành FP và thiếu IP đúng thành FN. Mỗi lượt Qwen3-VL có CVE TP/FP/FN = 40/0/0, IP = 39/1/1. Ảnh `a62` vẫn đọc sai `192.0.2.18` thành `18.192.0.2.1`; không sửa đáp án hoặc đầu ra để che lỗi. 100% bảo toàn token ở bước hậu xử lý/dịch là tiêu chí riêng, chưa được chứng minh bằng F1 OCR này.

Qwen3-VL đạt ngưỡng số học ≥65% mỗi ngôn ngữ và F1 ≥95% trên **bộ mẫu tổng hợp do agent soạn và render**, với prompt cố định trước lượt đánh giá. Tám ngôn ngữ còn lại đạt 100% trên tập nhỏ; không suy rộng thành chất lượng thực tế. Chưa có kiểm định bản chép bởi người thành thạo/người bản ngữ, tài liệu/ảnh điện thoại thực tế, hoặc đánh giá chất lượng dịch đủ 18 hướng. Đây là benchmark của agent code, **không thay thế giai đoạn TEST độc lập**.

Qwen3-VL p95 ba lượt: **4,681 / 4,720 / 4,709 giây**. Theo 955 mẫu đo từ trước load đến hết benchmark, GPU riêng cao nhất **4.720 MiB**, GPU trống thấp nhất **5.285 MiB**, không ghi nhận vi phạm hạn mức tại các thời điểm đo. Paddle p95 hai lượt tiền xử lý không bị tranh GPU: 5,872 / 5,832 giây; lượt đầu có LLM chạy đồng thời nên không dùng để so sánh tốc độ cô lập.

Báo cáo [lựa chọn model và bằng chứng OCR](/home/ict/secai-translate-ocr-wt/secai/secai-agent-gateway/docs/benchmarks/ocr-model-selection.md) lưu nguồn Hugging Face, giấy phép, tổng tham số gồm vision, độ quan tâm cộng đồng tại thời điểm tra cứu, revision/checksum trọng số, prompt cố định, từng đầu ra, latency và GPU trace. Không thử model thứ tư. Dịch vụ OCR thử nghiệm đã dừng, trọng số/script giữ riêng để tái lập; GPU trống trở lại 10.010 MiB và các PID dịch vụ hiện hữu không đổi. Chưa triển khai dịch vụ production.

## Phương án nếu mở lại task

Cần quyết định mở lại sau điều kiện dừng, với cách tiếp cận khác thay vì thêm bản vá cho từng ca:

1. Rà toàn bộ vòng đời artifact khi worker/web/reaper dừng; chọn cơ chế bảo trì có giám sát và chứng minh cleanup/quota/fencing cùng nhau.
2. Lập ma trận tất cả lớp token × hệ chữ × vị trí, kiểm tra occurrence và mutation bằng provider giả; xử lý nhất quán toàn bộ ranh giới Unicode thay vì chỉ CVE/IP.
3. Giao agent sửa mới hoặc thực hiện phân tích nguyên nhân gốc trước khi code; chạy lại review độc lập trên SHA mới.
4. Chỉ sau đó mới chạy TEST đa ngôn ngữ, Vanguard, tích hợp và tài liệu API có ví dụ live.

## Tài liệu

- [Plan](../plans/translate-import-ocr.md)
- [Tiêu chí import](../criteria/import-file-translate.md), [tiêu chí OCR](../criteria/image-ocr-translate.md)
- [Hợp đồng API mới](../api/translate-v2-contract.md) — chưa phải tài liệu bàn giao đã nghiệm thu
- [STATE](../loop/STATE.md), [LOG](../loop/LOG.md)
- [Kiểm thử import](/home/ict/secai-translate-import-wt/secai/secai-agent-gateway/docs/test-results/translate-v2-r1-code-evidence.md), [kiểm thử OCR](/home/ict/secai-translate-ocr-wt/secai/secai-agent-gateway/docs/test-results/translate-ocr-final.md)
- [Lệnh baseline chính xác](../test-results/translate-baseline-command.md)
- Tài liệu API bàn giao cuối `docs/api/translate-import-ocr.md` và báo cáo Vanguard chưa được tạo vì các cổng trước chưa đạt.

Bằng chứng pytest OCR nguyên bản được lưu bền tại `/home/ict/secai-ocr-evidence/final-pytest-349df3c/`; SHA-256 JUnit `48895e2487a1a4cea8fee6c53d32afe0dea5b0f16b84cf76ac2d76c969e163e0`, stdout `d17f39e3d8aa8ee013836e529c5954a5627bde077c0a1932121817093de0c9d5`. Final OCR checkpoint `ff311c12f97a1dd453495b0ff2b9ca269a49aba2`; tracked worktree sạch, chỉ còn AGENTS.md do điều phối cung cấp chưa theo dõi.
