# Devspace — demo người dùng và Admin, duyệt phản hồi A/B

Ngày kiểm thử: 2026-10-06. Giao diện thật tại Devspace cổng 5173; dùng skill Playwright CLI để điều khiển Chromium headless vì T3 preview status/open báo không có automation host. Luồng lưu/duyệt dùng API thật. Riêng kiểm tra xử lý lỗi kết nối dùng fault injection 503 trong browser, sau đó bỏ interception và thử lại với backend thật.

## Thiết kế đã triển khai

- Bộ chuyển **Demo → Người dùng thường / Admin** trong header. Đây là mô phỏng, không phải đăng nhập hoặc hai tài khoản production độc lập. Proxy tiếp tục sử dụng credential reviewer/admin chung; backend vẫn kiểm quyền.
- Người dùng thường chỉ thấy các chức năng thông thường; không có tab quản trị A/B. Identity reviewer được khởi tạo để nhận lời mời phản hồi chatbot theo cơ chế sampling/cooldown hiện có.
- Nút **Thử đánh giá A/B** mở ví dụ QA được phân công từ dữ liệu đã lưu, không ép sampling 10% thành 100% hoặc bỏ cooldown. Chọn đáp án, thêm nhận xét và gửi qua API thật. Nhãn lỗi ở phần mở rộng tùy chọn.
- Admin có tab A/B, mặc định **Chờ duyệt**, danh sách hiển thị câu hỏi thay cho UUID. Xem hai đáp án, phản hồi đã lưu, chọn đáp án để duyệt hoặc loại mẫu.
- Admin dùng candidate ID đã chuẩn hóa; A/B trên màn admin theo thứ tự candidate gốc, không suy từ left/right bị shuffle khi user đánh giá.
- Chỉ đếm **cặp đã duyệt có winner**; tie/skip không được tính là cặp preference. Con số này chưa bảo đảm chất lượng hoặc eligibility production.
- Nhập/xuất, quản lý đợt và phân công được chuyển vào công cụ nâng cao. Giữ điều kiện hoàn tất assignment, lý do bắt buộc, campaign đang mở và quyết định bất biến.
- Nút **Huấn luyện model chấm điểm** bị khóa và giải thích chưa kết nối dịch vụ job huấn luyện. Chưa thêm API train-job hoặc giả lập trạng thái training.

## Flow demo cho người mới

1. Chọn **Người dùng thường** ở góc trên.
2. Bấm **Thử đánh giá A/B**, chọn đáp án và gửi nhận xét.
3. Chọn **Admin**; tab A/B mở ra.
4. Trong **Chờ duyệt**, nhấn câu hỏi vừa đánh giá.
5. Xem lựa chọn đã lưu, chọn đáp án cần dùng và ghi lý do.
6. Bấm **Duyệt dùng huấn luyện** hoặc **Loại mẫu**.
7. Kiểm tra danh sách **Đã duyệt** và số cặp đã duyệt.

Các ví dụ QA chỉ dùng kiểm thử giao diện. Không chuyển chúng thành nhãn chuyên gia hoặc dataset production.

## Kết quả kiểm chứng

- `npm test`: **12 file, 71 test pass**. Bao gồm kiểm tra ẩn tab theo vai trò, đổi lại user không giữ màn admin, filter chờ duyệt, loại tie khỏi count, canonical candidate ID, lý do bắt buộc và loại mẫu bằng skip.
- `npm run build`: thành công. Vite có cảnh báo chunk >500 kB; không phải lỗi build.
- Browser workflow chạy thật **2 lần**: user gửi → Admin xem đúng nhận xét → duyệt → tải lại trang → dữ liệu vẫn còn → đổi về user không thấy tab A/B.
- Kiểm tra API trực tiếp bằng role reviewer: thao tác adjudication trả **403**.
- Backend xác nhận **2 phản hồi đã nộp, 2 mẫu đã duyệt**, còn 1 ví dụ chưa đánh giá để người dùng thử. Campaign QA: `3a1ec1f3-59fa-4082-992c-011bca68ee5a`.
- Kiểm tra viewport 390×844: bộ chuyển vai trò nằm trong header, không tràn ngang toàn trang; mở/đóng dialog bằng thao tác thật. Không gửi thêm phiếu ở lượt kiểm tra bố cục.
- Kiểm tra trực quan screenshot: sửa dialog vô tình hiển thị khi chưa mở, màu chữ dialog và header/nav bị che trên màn nhỏ. Kiểm tra lại sau sửa đã pass.
- Rà soát độc lập phát hiện và đã sửa hai lỗi: nguồn identity bị điều khiển bởi hai selector, hướng dẫn Vite không tương thích đăng nhập demo. `App` hiện là nguồn identity duy nhất; README chỉ rõ demo qua static proxy. Reviewer kiểm lại không còn Critical/Important.
- Browser smoke sau sửa identity: user không có tab A/B, Admin tải được đúng approval đã lưu, không còn selector chuyên gia thứ hai. Fault injection `/me` 503 hiện lỗi; bỏ interception rồi bấm “Thử lại” kết nối được backend thật.
- `git diff --check`: pass. Không thay backend trong lần này; không chạy lại toàn bộ suite backend.

## Ảnh và bằng chứng

Ảnh thật nằm ngoài Git trong `output/playwright/`:

- `demo-user-chat.png`: giao diện user, không có tab A/B quản trị.
- `demo-user-ab.png`: user chọn đáp án trong ví dụ chatbot.
- `demo-admin-pending.png`: Admin xem phản hồi chờ duyệt.
- `demo-admin-approved.png`: Admin thấy kết quả duyệt đã lưu.
- `demo-user-mobile.png`, `demo-user-mobile-ab.png`: bố cục 390×844.

Log runtime ngoài Git: `/tmp/reward-browser-final.log`, `/tmp/reward-final-layout.log`, `/tmp/reward-ui-verified-tests.log`, `/tmp/reward-ui-verified-build.log`, `/tmp/reward-central-role-browser.log`, `/tmp/reward-demo-retry.log`, `/tmp/devspace-demo-ui-persistence.json`. File /tmp và ảnh runtime không đảm bảo lưu lâu dài. Không đưa token hoặc database vào Git.

Bản build mới đã được static server 5173 phục vụ trực tiếp. Không rebuild/restart Docker 5179 trong lần này. Đây là demo trên Devspace với tài khoản chung, chưa phải rollout phân quyền người dùng thật.
