Triển khai: Hệ thống hỗ trợ on-premise, yêu cầu GPU NVIDIA A100 80GB, RAM ≥128GB, SSD ≥2TB. Hỗ trợ air-gapped, vận hành không cần kết nối Internet.

Kịch bản là kịch bản triển khai/vận hành chạy trực tiếp trên hệ thống thật (kiểm tra on-premise, ngắt Internet, khởi động lại, kiểm tra service, truy vấn qua giao diện `Trợ lý ảo an toàn thông tin`, lưu log/ảnh). Endpoint tương ứng: `/health`, `/ai/chat`. Tài liệu triển khai on-premise bằng Docker trong hồ sơ gốc phần AI được trích dưới đây.

**`04. Huong_dan_cai_dat/HUONG-DAN-CAI-DAT.md`** — dòng 8–11 (kiến trúc: gateway + qdrant + neo4j + redis chạy bằng Docker):
````
```
[Máy Gateway]  ──HTTP──>  [Serving GPU 2×A100 (vast.ai)]
  Docker: gateway + qdrant + neo4j + redis
```
````

**`04. Huong_dan_cai_dat/HUONG-DAN-CAI-DAT.md`** — dòng 17–26 (yêu cầu máy Gateway, gồm kết nối mạng đầu ra) và dòng 30–33 (Serving GPU 2× A100 80GB):
````
### 1.1. Máy Gateway (máy đích, nơi backend vận hành)

| Hạng mục | Yêu cầu |
|---|---|
| Hệ điều hành | Linux (Ubuntu 22.04 trở lên) |
| GPU | Không yêu cầu |
| CPU / RAM | Tối thiểu 4 nhân / 8 GB (neo4j sử dụng tối đa 2 GB heap, cùng qdrant) |
| Dung lượng ổ đĩa trống | Khoảng 20 GB (dữ liệu ~7 GB, cơ sở dữ liệu sau phục hồi ~7 GB, image ~2 GB) |
| Kết nối mạng đầu ra | Docker Hub, Hugging Face, địa chỉ IP của lớp serving, và api.deepseek.com (nếu dùng mô hình dự phòng) |
| Phần mềm | Docker và Docker Compose v2, `git`, công cụ `huggingface_hub` |
````
````
| Hạng mục | Yêu cầu |
|---|---|
| GPU | 2 × NVIDIA A100 80 GB |
| Dung lượng ổ đĩa | Khoảng 100 GB (image và trọng số mô hình ~24 GB) |
````

**`04. Huong_dan_cai_dat/HUONG-DAN-CAI-DAT.md`** — dòng 119–128 (phục hồi cơ sở dữ liệu từ dữ liệu/model đã có + khởi động gateway) và dòng 137–142 (kiểm tra sau cài đặt):
````
**Bước 9. Phục hồi cơ sở dữ liệu** (nạp neo4j và khôi phục qdrant):
```bash
./restore.sh
```

**Bước 10. Tải image và khởi động gateway:**
```bash
docker compose pull gateway
docker compose up -d gateway
```
````
````
## 4. Kiểm tra sau cài đặt

```bash
docker compose ps                             # bốn dịch vụ ở trạng thái healthy
curl http://localhost:8131/health             # phản hồi {"status":"ok", ..., "llm":true}
```
````

**Kết luận:** Kịch bản đối chiếu được một phần từ hồ sơ gốc: hướng dẫn triển khai on-premise bằng Docker (gateway + qdrant + neo4j + redis), phục hồi từ dữ liệu/DB đã nạp sẵn và lớp serving 2× A100 80GB đều được ghi nhận; các bước ngắt Internet / khởi động lại / truy vấn giao diện là demo trực tiếp trên hệ thống đang chạy, không có file để trích. Lưu ý thông số trong hướng dẫn: máy Gateway RAM tối thiểu 8 GB và đĩa ~20 GB, có liệt kê kết nối mạng đầu ra ở bước cài đặt (dòng 25).
