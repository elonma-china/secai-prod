# Báo cáo thông minh — yêu cầu bổ sung phía FE

Bối cảnh: khách hàng nghiệm thu tính năng **Nền tảng AI → Báo cáo thông minh** phản hồi
"chưa đáp ứng": đầu ra hiện tại sơ sài, chỉ có thông tin lỗ hổng chung chung và thống kê số
lượng xuất hiện của các lỗ hổng — không có số máy đã quét, không có danh sách máy/IP/ngày
quét/mức độ.

**Nguyên nhân đã xác nhận trong code (`src/screens/ReportScreen.tsx`):** cả 2 tab hiện tại
đều không gửi dữ liệu số lượng/máy/IP nào lên backend, nên LLM không có gì để tổng hợp
ngoài suy diễn chung chung. Xem chi tiết ở mục 1.

---

## 1. Hiện trạng (không cần sửa gì ở đây, chỉ để đối chiếu)

| Tab | UI hiện có | Payload gửi backend |
|---|---|---|
| Tạo báo cáo | 1 textarea tự do, placeholder *"Dán log, kết quả quét hoặc dữ liệu cần tổng hợp…"* | `{ logs: <text người dùng gõ>, user_id }` |
| Thống kê kỳ | Chọn kỳ (Tuần/Tháng/Quý) + nút | `{ period, from_date, to_date, message: "Tạo thống kê an toàn thông tin cho kỳ: ...", user_id }` — **không có số liệu thật nào** |

→ Cả hai đường đều không có chỗ mang "số máy đã quét / IP / ngày quét / mức độ" lên backend.

---

## 2. Yêu cầu khách hàng (trích nguyên văn, để đối chiếu khi nghiệm thu lại)

> 1. Tổng quan: số lượng máy đã quét; số lượng lỗ hổng đã ghi nhận, thống kê (biểu đồ, bảng)
>    lỗ hổng phân loại theo mức độ.
> 2. Danh sách chi tiết: tên máy, IP, ngày quét, lỗ hổng, mức độ, mô tả lỗ hổng.

---

## 3. Field bắt buộc FE phải thu thập/gửi thêm

Thay payload tự do bằng dữ liệu **có cấu trúc**, theo từng máy:

```ts
interface ScanFinding {
  name: string;         // tên/mã lỗ hổng, vd "CVE-2021-44228 (Log4Shell)"
  severity: "critical" | "high" | "medium" | "low";
  description: string;
}

interface ScanMachine {
  name: string;          // tên máy
  ip: string;
  scan_date: string;     // ISO date, vd "2026-09-12"
  vulnerabilities: ScanFinding[];
}

interface ScanReportRequest {
  from_date: string;     // ISO date
  to_date: string;       // ISO date
  machines: ScanMachine[];
  user_id: string;
}
```

Đây là field **tối thiểu** để backend tính đúng "Tổng quan" bằng code (số máy = `machines.length`,
tổng lỗ hổng = tổng `vulnerabilities` mọi máy, phân loại mức độ = group theo `severity`) —
xem chi tiết tính toán ở file `BAO-CAO-THONG-MINH-SUA-TEMPLATE.md` (phía backend).

---

## 4. Cần quyết định trước khi code UI: dữ liệu `machines` lấy từ đâu

Đây là điểm mấu chốt quyết định UI cụ thể — **cần chốt trước khi implement**, 2 hướng khác hẳn nhau:

### Hướng A — Chưa có hệ quét thật nối vào (nhập tay / upload)
Sửa tab "Tạo báo cáo": thay ô textarea tự do bằng 1 trong 2:
- **Form nhập tay**: bảng cho phép thêm dòng máy (tên, IP, ngày quét) → mỗi máy thêm dòng lỗ hổng
  con (tên, mức độ chọn từ dropdown, mô tả).
- **Upload file**: nhận CSV/JSON kết quả quét có sẵn từ công cụ khác, FE parse ra đúng schema mục 3
  rồi gửi lên — nên hiển thị bảng preview trước khi submit để người dùng soát lại.

### Hướng B — Đã có hệ quét thật (vd bughunt hoặc hệ khác)
FE gọi API của hệ quét đó lấy kết quả theo khoảng ngày, tự map sang đúng schema mục 3, **không**
cần người dùng nhập tay gì thêm — tab "Thống kê kỳ" chỉ cần period picker như hiện tại, nhưng khi
bấm nút thì FE fetch dữ liệu thật trước khi gọi report thay vì gửi message rỗng như bây giờ.

*(Việc chọn A hay B phụ thuộc hệ thống sản xuất thật đang/sẽ có gì — chưa chốt trong repo này.)*

---

## 5. Yêu cầu hiển thị kết quả (render)

- Bảng "phân loại theo mức độ" và bảng "Danh sách chi tiết": dùng `Markdown` component hiện có
  (`react-markdown` + `remark-gfm` đã có sẵn trong `package.json`) — bảng GFM render được ngay,
  **không cần thêm thư viện**.
- Nếu khách hàng đòi đúng nghĩa **biểu đồ** (cột/tròn, không chỉ bảng số liệu): hiện
  `package.json` **chưa có** thư viện chart nào (không recharts/chart.js/...). Cần:
  1. Backend trả thêm số liệu severity dạng JSON thô (không nhúng trong text Markdown).
  2. FE thêm 1 thư viện chart + component vẽ từ JSON đó.
  Đây là việc bổ sung riêng, ước lượng thêm effort — nêu rõ với khách hàng nếu chỉ làm kịp bảng
  trước, chart sau.

---

## 6. Checklist nghiệm thu lại (đối chiếu đúng yêu cầu khách hàng)

- [ ] Tab tạo báo cáo có cách nhập/lấy được danh sách máy (tên, IP, ngày quét, lỗ hổng, mức độ, mô tả)
- [ ] Request gửi backend có `machines` đúng schema mục 3 (không còn gửi text tự do làm nguồn số liệu)
- [ ] Kết quả hiển thị có: số máy đã quét, tổng lỗ hổng, bảng/biểu đồ phân loại theo mức độ
- [ ] Kết quả hiển thị có bảng chi tiết đủ 6 cột: tên máy, IP, ngày quét, lỗ hổng, mức độ, mô tả
- [ ] Số liệu tổng quan khớp 100% với dữ liệu chi tiết bên dưới (không lệch do LLM tự làm tròn/bịa)
