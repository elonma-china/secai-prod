# Reward model Qwen3-1.7B — luồng đã chốt

Ngày chốt: 2026-10-05. Người dùng chọn Qwen/Qwen3-1.7B làm nền, thêm đầu ra điểm số, huấn luyện bằng chosen/rejected đã được chuyên gia đánh giá và admin duyệt, kiểm tra đồng thuận với chuyên gia trước khi đưa vào sản phẩm.

## Luồng dữ liệu

1. Chatbot tạo hai câu trả lời cho cùng prompt; người dùng chọn câu tốt hơn trong giao diện chat.
2. Phản hồi được đưa vào quy trình expert review hiện có. Lựa chọn của người dùng là tín hiệu ban đầu; chỉ kết quả chuyên gia được admin duyệt mới đủ điều kiện cho tập huấn luyện này.
3. Xuất `prompt`, `chosen`, `rejected` cùng ID nguồn, phiên bản rubric và adjudication. Đối chiếu bản raw và manifest SHA-256; chỉ nhận export từ nguồn quản trị tin cậy. SHA-256 kiểm tra toàn vẹn, không chứng minh người ký hay nguồn gốc.
4. Loại bản ghi QA/demo, nhãn chưa duyệt và nhãn không có bên thắng rõ ràng. Các nhãn ngang nhau/cả hai kém/bỏ qua vẫn được lưu để phân tích nhưng không ép thành chosen/rejected.
5. Chia train/validation/test theo nhóm prompt và hội thoại nguồn; giữ các bản trùng và các nhóm liên thông trong cùng partition để tránh rò rỉ. Test được giữ kín trong quá trình lựa chọn cấu hình.
6. Qwen3-1.7B nhận prompt + một câu trả lời, trả về một scalar qua sequence-classification head (`num_labels=1`). Huấn luyện pairwise Bradley–Terry để điểm chosen cao hơn rejected. Ưu tiên khảo sát LoRA, lưu cả adapter và score head; pin revision model và phiên bản thư viện khi triển khai.
7. Đo tỷ lệ thứ tự điểm trùng lựa chọn chuyên gia trên test chưa thấy; báo số mẫu, khoảng tin cậy, số hòa điểm và kết quả theo nhóm ATTT. Kiểm thêm thiên lệch độ dài, thứ tự A/B, câu trả lời sai nhưng trôi chảy và câu trả lời giống nhau.
8. Chỉ đưa artifact được đánh giá đạt tiêu chí đã xác lập vào dịch vụ chấm điểm. Ngưỡng đạt cụ thể sẽ được xác lập trước khi mở tập test, sau khi biết quy mô/chất lượng dữ liệu thật. Chưa có ngưỡng thì chưa đủ điều kiện phát hành.

## Ý nghĩa điểm và vận hành

- Điểm là mức ưu tiên học được, không tự động là xác suất đúng hay thang 0–10. API trả điểm từng câu, chênh lệch điểm và phiên bản artifact.
- Model chấm điểm là dịch vụ riêng gọi qua HTTP; gateway CPU tiếp tục điều phối. Chọn phần cứng serving bằng benchmark thực tế, không giả định CPU đáp ứng độ trễ sản phẩm.
- Huấn luyện offline; tài nguyên GPU, dung lượng dữ liệu và cấu hình cuối cùng được xác định khi chuẩn bị training. Không tự động tải model hoặc thuê GPU khi chỉ chốt thiết kế.
- Giai đoạn đầu dùng scorer để đánh giá và đối chiếu chuyên gia. Tự động chọn đáp án đưa cho khách hoặc fine-tune chatbot là bước riêng, chưa nằm trong luồng đã chốt.
- Mỗi artifact giữ provenance dataset, checksum, base-model revision, cấu hình huấn luyện và báo cáo đánh giá; có thể quay lại artifact trước.

## Trạng thái hiện tại

Chưa có dataset chuyên gia thật đủ điều kiện đã xác nhận. Các campaign QA/demo hiện có không được dùng để huấn luyện reward model sản phẩm. Mã lexical CPU prototype đang xây dựng chỉ phục vụ smoke test kỹ thuật; không phải model đã chọn và không phải bằng chứng chất lượng Qwen3.

Chưa huấn luyện, benchmark hoặc triển khai Qwen3 reward model. Tài liệu này ghi nhận quyết định và phạm vi, không khẳng định các bước trên đã được triển khai.

## Nguồn kỹ thuật

- Model card: https://huggingface.co/Qwen/Qwen3-1.7B
- RewardTrainer, scalar head và pairwise loss: https://huggingface.co/docs/trl/main/en/reward_trainer

Tài liệu TRL `main` có thể thay đổi; khi viết runner cần dùng tài liệu tương ứng phiên bản được pin.
