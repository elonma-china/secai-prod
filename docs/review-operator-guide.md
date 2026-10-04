# Vận hành MVP đánh giá A/B

Công cụ thu thập lựa chọn từ các cặp chuẩn bị sẵn và lời mời A/B tùy chọn sau một câu trả lời chat mới. Nó không học trực tiếp khi bấm chọn và không kích hoạt retraining. Giao diện nằm trong `secai-agent-gateway/secai-fe-devspace`.

## Cấu hình riêng cho review

Mặc định `REVIEW_ENABLED=false`. Khi bật cần `REVIEW_DB_PATH` và `REVIEW_CREDENTIALS_FILE` hợp lệ. File credential chứa hash SHA-256 của token ngẫu nhiên, actor ID và role. Không sử dụng mật khẩu dễ đoán làm token; không commit credential, token, database hay export thật.

Tạo credential bằng Python, chạy từ gateway. Lệnh chỉ tạo file mới, từ chối ghi đè:

```python
import hashlib
import json
import os
import secrets
from pathlib import Path

directory = Path.home() / '.config/secai-review'
directory.mkdir(mode=0o700, parents=True, exist_ok=True)
credentials, tokens = [], []
for actor_id, role in [('admin', 'admin'), ('expert-1', 'reviewer'), ('expert-2', 'reviewer')]:
    token = secrets.token_urlsafe(32)
    credentials.append({'actor_id': actor_id, 'role': role,
                        'token_sha256': hashlib.sha256(token.encode()).hexdigest()})
    tokens.append({'actor_id': actor_id, 'token': token})
for name, value in [('credentials.json', {'credentials': credentials}), ('tokens.json', tokens)]:
    fd = os.open(directory / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as f:
        json.dump(value, f, indent=2)
```

Phân phối từng token qua kênh riêng cho đúng người khi dùng API trực tiếp; `tokens.json` chỉ phục vụ cấp phát và không được đưa lên web. Backend chỉ đọc `credentials.json`. Đổi/thu hồi token bằng cập nhật credential file và restart dịch vụ review theo quy trình vận hành. HTTPS bắt buộc khi truy cập ngoài localhost. Reverse proxy cần ẩn header Authorization khỏi log và không cache API review. Module chỉ thêm `/review-api/*`; không thay đổi API, CORS hoặc rate limit hiện có.

## Devspace nội bộ: vào thẳng không nhập token

Devspace tĩnh (`serve_static.py`) có thể dùng hai tài khoản **dùng chung** để vào thẳng màn “Đánh giá A/B”. Đặt file `secai-fe-devspace/.review-devspace-tokens.local` ở phía server, chỉ tài khoản chạy frontend được đọc, với JSON `{"admin":"<token quản trị>","reviewer":"<token chuyên gia>"}`. File này phải nằm ngoài Git; không đưa token vào bundle JavaScript, HTML, URL hay log. Proxy chỉ tự gắn token cho request `/review-api/*` có vai trò `admin` hoặc `reviewer` khi cả địa chỉ nguồn và địa chỉ cổng nhận thuộc Tailnet (`100.64.0.0/10`). Truy cập qua tunnel công cộng hoặc cổng không thuộc Tailnet không được dùng đường tắt. API gateway vẫn đòi bearer token và giữ nguyên kiểm tra role. Sau khi cấu hình file, khởi động lại `serve_static.py` để dùng bản proxy mới.

Đây là lối vào nhanh cho Devspace, **không phải đăng nhập cá nhân**: mọi người có quyền vào Devspace qua Tailnet đều có thể chọn cả hai vai trò; phiếu của họ được ghi dưới actor dùng chung. Không dùng dấu vết này để quy trách nhiệm từng chuyên gia. Khi cần phân quyền và truy vết cá nhân, thay lối vào này bằng SSO/identity proxy và credential riêng theo người. Frontend Vite `npm run dev` không có proxy cấp role này; dùng Devspace tĩnh hoặc API bearer trực tiếp để thử review local.

## Chạy local ở worktree riêng

Tạo venv/dependencies theo AGENTS.md nếu chưa có. Chạy từ gateway, chọn cổng chưa dùng:

```sh
REVIEW_ENABLED=true \
REVIEW_CREDENTIALS_FILE="$HOME/.config/secai-review/credentials.json" \
REVIEW_DB_PATH=./data/reviews-local.db \
GATEWAY_DB_PATH=./data/gateway-local.db \
SESSION_DB_PATH=./data/sessions-local.db \
POC_CATALOG_DB=./data/poc-local.db \
AIRGAP=true \
.venv/bin/python -m uvicorn secai_gateway.app:app --host 127.0.0.1 --port 8187
```

Không sao chép `.env` production để chạy demo. Cấu hình còn lại phải phù hợp môi trường; luồng review không yêu cầu gọi model/embedding. Không gọi các chức năng chat/exploit trong phiên demo khi chưa cấu hình dịch vụ của chúng.

Tại frontend lồng trong gateway:

```sh
npm ci
VITE_GATEWAY_PROXY=http://127.0.0.1:8187 npm run dev -- --host 127.0.0.1 --port 5187
```

Vào trang “Đánh giá A/B”. Backend phải được bật rõ ràng; menu không có nghĩa dịch vụ đã bật. Khi chạy Vite đơn lẻ, lối vào không token chưa được cấu hình và màn sẽ báo chưa kết nối. Với frontend build production, reverse proxy cần chuyển `/review-api` tới gateway, giống các API hiện có.

## Chấm thử

1. Đăng nhập bằng token quản trị, tạo campaign với rubric `security_qa_v1`.
2. Import `tests/fixtures/review_pairs.jsonl`. Đây là 5 mẫu tổng hợp, không dùng làm bằng chứng chất lượng model.
3. Phân công cho expert-1, expert-2; người chấm dùng phiên trình duyệt riêng.
4. Chuyên gia xem ngữ cảnh, bằng chứng và hai câu trả lời. Tên model được ẩn. A/B có thể khác thứ tự giữa người chấm nhưng ổn định cho mỗi assignment.
5. Chọn tốt hơn/ngang nhau/cả hai sai/thiếu ngữ cảnh. Hai lựa chọn cuối hoặc nhãn `other` phải có lý do. Không có điểm số reward tự động.
6. Sau tất cả phiếu nộp, quản trị xem kết quả đã chuẩn hóa theo candidate ID, xử lý bất đồng và duyệt với lý do.
7. Export raw hoặc preference. Kiểm tra JSONL và manifest trong ZIP; preference chỉ gồm các mẫu đã duyệt có người thắng rõ ràng.

Ưu tiên đúng kỹ thuật và có bằng chứng, sau đó đủ ý và phù hợp câu hỏi; không ưu tiên chỉ vì câu trả lời dài. Nếu chưa có thông tin xác minh, chọn thiếu ngữ cảnh thay vì đoán. Một câu trả lời tốt hơn câu còn lại vẫn có thể chứa lỗi; cần giữ nhãn lỗi và quyết định quản trị.

## Dữ liệu và giới hạn

- Upload JSONL UTF-8, tối đa 10 MiB/500 mẫu, tối đa 1 MiB/mẫu và 64 KiB/candidate. Toàn bộ đợt import rollback nếu có lỗi; ID cùng nội dung được bỏ qua, khác nội dung bị 409.
- Ngữ cảnh chỉ dùng role user/assistant được phép chia sẻ; không nhập system prompt, thinking, secret hoặc dữ liệu chưa được phép cho chuyên gia xem. Redaction không thay thế việc kiểm duyệt dataset đầu vào.
- Phiếu đã nộp và quyết định đã duyệt bất biến trong MVP. Gửi lại giống nhau an toàn; payload khác bị 409.
- Có thể bỏ assignment chưa nộp trước khi duyệt. Campaign đóng ngăn thay đổi nhưng vẫn cho xem/export; quản trị có thể mở lại.
- Nhãn duyệt sai: ngừng sử dụng export của campaign đó và tạo đợt thay thế. Công cụ chưa có quy trình sửa phiên bản quyết định hoặc tự động thu hồi dataset đã gửi ra ngoài.
- Không có tự chia train/test. Giữ `source_group` trong raw để bên ML chia theo hội thoại/tài liệu. Bên ML cần adapter đúng trainer/tokenizer/chat template và kiểm chứng tiếp nhận trước khi huấn luyện.

## Backup và restore

Dùng SQLite online backup API để có snapshot nhất quán; không chỉ copy file DB đang chạy cùng WAL. Ví dụ dưới đây chạy dưới tài khoản có quyền đọc database và chỉ tạo backup mới:

```python
import sqlite3
from pathlib import Path
source = Path('data/reviews-local.db')
backup = Path('data/reviews-backup.db')
if not source.is_file() or backup.exists():
    raise RuntimeError('Nguồn không tồn tại hoặc backup đã tồn tại')
with sqlite3.connect(f'file:{source}?mode=ro', uri=True) as src:
    with sqlite3.connect(backup) as dst:
        src.backup(dst)
        assert dst.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
```

Để restore, dừng instance ghi review, kiểm tra backup bằng `PRAGMA integrity_check`, cấu hình `REVIEW_DB_PATH` trỏ tới bản phục hồi ở đường dẫn mới rồi khởi động lại. Giữ bản cũ cho đến khi xác nhận số campaign/phiếu và export đúng. Backup chứa dữ liệu nhạy cảm như database gốc, cần giới hạn quyền truy cập và chính sách lưu giữ phù hợp.

## Checklist nghiệm thu UI

- [ ] Token sai bị từ chối; reviewer không vào quản trị hoặc đọc assignment người khác.
- [ ] Import fixture, retry import không nhân đôi; lỗi dòng được hiển thị.
- [ ] Phân công hai người, A/B không đổi khi reload, không thấy model hay phiếu người khác.
- [ ] Kiểm tra đủ năm kết quả, required reason, tag lỗi và nhận xét tiếng Việt.
- [ ] Lỗi mạng giữ phiếu để retry; double submit không tạo hai phiếu.
- [ ] Duyệt bị chặn khi còn phiếu chờ; chốt winner đúng candidate dù A/B đảo vị trí.
- [ ] Export preference chỉ gồm winner đã duyệt; raw giữ hòa/cả hai sai/thiếu ngữ cảnh.
- [ ] Logout xóa dữ liệu/token trong UI; không lưu token vào localStorage/URL.
- [ ] Bàn phím, focus, màn hình hẹp, Markdown dài và code/script hiển thị an toàn.
- [ ] Restart giữ dữ liệu; chat/session hiện tại không bị thay đổi.

Checklist này là hướng dẫn kiểm chứng, không phải tuyên bố các mục đã được chạy.


## Lời mời A/B trong chat

Đăng nhập ở “Đánh giá A/B”, sau đó chuyển sang “Trợ lý ảo ATTT”. Token vẫn chỉ ở memory trong cùng tab; reviewer được bật lời mời mặc định, admin có thể bật checkbox. B chỉ được sinh sau câu trả lời hoàn tất mới; không sinh khi đọc lại lịch sử, chưa đăng nhập, tắt toggle, stream lỗi/hủy hoặc vượt giới hạn ngữ cảnh. Câu trả lời chat chính vẫn hiển thị bình thường.

- `REVIEW_LIVE_SAMPLE_RATE=0.1`: xác suất mời 10% mỗi lượt đủ điều kiện; đặt 0 để ngừng, 1 chỉ để QA.
- `REVIEW_LIVE_COOLDOWN_SECONDS=600`: tối đa một lần thử sinh B/người trong 10 phút; giới hạn một request đang sinh/người.
- `REVIEW_LIVE_TIMEOUT_SECONDS=45`: lỗi/timeout/đầu ra trống hoặc trùng không hiện cặp.
- Cooldown/inflight hiện ở memory từng process. MVP chạy một worker; nhiều worker cần giới hạn dùng chung trước khi coi đây là trần chi phí toàn hệ thống.
- Sinh B dùng session UUID riêng và cleanup; không thêm B/phiếu vào hội thoại gốc. B dùng ngữ cảnh công khai, không bảo đảm cùng snapshot truy hồi RAG với A. Provenance ghi rõ `live_chat`, `original_chat_final`, `review_generation`, `retrieved_context=unknown`.
- Lựa chọn trực tiếp trong chat chỉ là phản hồi; quản trị vẫn phải duyệt trước khi có preference export. Không tự quy đổi thành reward số hoặc tự huấn luyện.

10% là lựa chọn khởi đầu vận hành, không phải thông số đã tối ưu bằng thực nghiệm. Theo dõi số lời mời, tỷ lệ phản hồi và công sinh B trước khi điều chỉnh. Không mặc định phản hồi trong chat đáng tin ngang nhãn chuyên gia.

## Chuẩn hóa và chất lượng nhãn

Bộ rubric v1 chỉ là điểm khởi đầu. Dùng các mẫu chấm chung và ví dụ đúng/sai để hiệu chỉnh cách hiểu; dành một phần dữ liệu cho hai chuyên gia chấm độc lập, xử lý bất đồng trước khi đưa vào tập huấn luyện. Ít nhãn hoặc nhãn nhiễu không bảo đảm model tốt lên. RLHF/DPO và nguy cơ mất năng lực cũ nằm ngoài phạm vi công cụ: cần kiểm chứng tập giữ riêng, bộ hồi quy kiến thức cũ, phiên bản dataset/model và quy trình quay lại model trước đó khi triển khai ML.


## Template báo cáo bằng prompt

Sau khi vào mục Đánh giá A/B trên Devspace để khởi tạo vai trò, vào “Báo cáo thông minh” -> “Template báo cáo”. Dán hoặc nhập file Markdown (64 KiB), nhập yêu cầu sửa, kiểm tra đầu vào rồi xác nhận không chứa prompt injection. Bấm “Đề xuất bản nháp bằng prompt”, kiểm tra bản gốc/bản nháp và bấm “Áp dụng bản nháp” nếu phù hợp. Thao tác đề xuất không tự thay bản gốc; lỗi không làm mất template hiện tại.

Template và bản nháp chỉ giữ trong memory của màn hình MVP. Xuất `.md` để dùng lại; có thể nhập lại file sau. Sau áp dụng/chỉnh input, cần xác nhận sạch lại trước khi gọi model. Nhập logs và chọn “Tạo báo cáo theo template”; xem và tải `.md`. Hai nút tạo báo cáo/thống kê cũ giữ nguyên API và hành vi.

Người dùng chịu trách nhiệm đảm bảo prompt, template và logs sạch. Chức năng không cung cấp bộ kiểm soát prompt injection chuyên dụng. Checkbox không phải bộ phát hiện; guard và chống XSS hiện có vẫn được giữ. Profile mới không chạy tool, mã hay template engine. Backend từ chối bản sửa làm mất placeholder cũ `{{...}}`; cần kiểm tra thêm tính đúng đắn/ngữ nghĩa của kết quả model.
