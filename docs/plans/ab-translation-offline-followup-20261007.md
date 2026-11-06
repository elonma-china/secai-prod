# Hoàn thiện kiểm chứng offline A/B và dịch

Người dùng yêu cầu tiếp tục sau đợt sửa độ tin cậy và đã chọn offline. Đợt này xử lý tính tái lập của bảy failure phụ thuộc môi trường và bổ sung lệnh diễn tập baseline. Không đo chất lượng model thực.

## Tiêu chí chấp nhận — Astra

| ID | Điều kiện | Bằng chứng |
|---|---|---|
| F01 | Test hành vi PoC chạy với catalog và artifact tạm, không cần dữ liệu trên máy | Focused tests với catalog scratch |
| F02 | Dùng backend đọc file thật; giữ kiểm tra nội dung/hash, ref cùng run/khác run, budget và metadata-only | Diff test và kết quả regression; không mock search/fetch để tạo source giả |
| F03 | SSE vẫn phát preview trước final và DONE; fixture chỉ chứa nội dung vô hại | Test đi qua prefetch/ledger/route hiện có |
| F04 | Kiểm tra cây Bughunt và snapshot catalog ngoài checkout giữ thành integration có opt-in rõ | Mặc định báo chưa chạy; khi bật mà thiếu prerequisite phải fail với thông báo cụ thể |
| F05 | Không đổi policy production, allowlist, license hay cổng execution để làm test xanh | Review diff độc lập |
| F06 | Tên test và tài liệu phân biệt artifact giả lập với dữ liệu PoC thật | Không dùng fixture để chứng nhận external integration |
| F07 | Diễn tập baseline chạy lại được, không gọi model; lỗi/thiếu vẫn tính trong mẫu số và verdict giữ BLOCKED | Luna chạy đúng lệnh tài liệu, kiểm dry-run và scorer |

## Phân công

- Luna: điều tra, sửa fixture/test và thực thi focused/full suite; lưu lệnh, kết quả và trạng thái integration riêng.
- Sol: kiểm tra harness hiện có và viết lệnh diễn tập offline đầy đủ; chỉ sửa product code nếu tìm thấy nguyên nhân cụ thể cần sửa.
- Astra: tiêu chí, review độc lập, đối chiếu bằng chứng và tích hợp về checkout hiện tại, giữ nguyên sửa đổi người dùng.

## Trình tự

1. Tái sử dụng attribution bảy failure đã được xác minh ở đợt trước; đọc đường đi dữ liệu và xác định ranh giới fixture/integration.
2. Sửa test theo đúng ranh giới, chạy focused và review diff; không hạ assertion hay đổi failure thành skip chung.
3. Chạy full suite trong môi trường offline đã kiểm soát và diễn tập baseline bằng harness sẵn có.
4. Báo kết quả thực tế, các integration chưa chạy và giới hạn semantic; kiểm diff rồi tích hợp các file thuộc tác vụ.

Không commit, push, triển khai hoặc chạy GPU trong đợt này. Workspace: `/home/ict/secai-quality-improvement-20261007-wt/secai/secai-agent-gateway`. Evidence local: `/tmp/secai-quality-followup-20261007/`.
