# Tiêu chí cải tiến A/B và dịch — đợt 1

Tác giả tiêu chí: Astra. Phạm vi được người dùng chấp thuận: sửa độ tin cậy đã review và chuẩn bị baseline chất lượng dịch. Code do Sol, test do Luna, đánh giá do Astra. Không có thay đổi API public, model, production flag hay deployment trong đợt này.

| ID | Điều kiện chấp nhận | Bằng chứng bắt buộc |
|---|---|---|
| Q01 | External cancellation truyền ra khỏi worker; job không succeeded thiếu result; đầu vào còn phục hồi được | Test hủy khi specialist đang chờ và reclaim lease |
| Q02 | User DELETE vẫn cancelled; stale generation/deadline không publish; finish không tạo succeeded nếu thiếu result | Existing tests + regression store |
| Q03 | Identity đến sau turn tạo tối đa một request A/B; đổi identity không dùng response/phiếu cũ; StrictMode không nuốt lời mời | Deferred identity/request tests |
| Q04 | Đổi source/status/search/page/tab hoặc unmount làm detail/bulk response cũ mất hiệu lực | Deferred response UI tests |
| Q05 | Escape/Đóng với bản nháp có xác nhận bỏ; từ chối giữ dữ liệu; đồng ý mới đóng; khi submit busy không mất dialog | UI tests, giữ focus/modal semantics |
| Q06 | Training events gateway chỉ trả telemetry theo contract; giữ seq/pagination; không trả unknown stdout/token | Synthetic upstream test thay expected-failure bằng pass |
| Q07 | Test CORS tự khai báo allowed origins, không lệ thuộc env máy chạy và không sửa policy production | Test trong env wildcard và explicit origin |
| Q08 | Baseline tái sử dụng harness/corpus hiện có; ghi rõ đường chạy/model/config/dataset; lỗi/timeout tính trong mẫu số | CLI offline contract tests; manifest/report mẫu |
| Q09 | Báo kết quả từng hướng; tách metric tự động khỏi semantic verdict; thiếu nhãn/holdout không được tự PASS chất lượng | Missing labels, direction failures và sample-count tests |
| Q10 | Không phát sinh inference khi validate/dry-run; không ghi secret/user payload vào log báo cáo ngoài artifact được chỉ định | Offline no-network tests |
| Q11 | Backend focused + full suite và frontend suite/build hoàn tất; failure/skip/xfail báo đúng | Luna command logs; baseline attribution nếu thiếu dependency |
| Q12 | Giữ toàn bộ sửa đổi người dùng, không đổi ngưỡng để hợp thức hóa, không commit/deploy ngoài yêu cầu | Astra diff review |

Các gate chất lượng 18 hướng/3 lượt trong tài liệu hiện có giữ nguyên. Chạy offline không chứng nhận nghĩa dịch, OCR thực, GPU hay production. Live run chỉ thực hiện sau khi có endpoint, manifest và giới hạn tài nguyên phù hợp; đợt này bàn giao harness có thể chạy lại và danh sách evidence còn thiếu.
