# Báo cáo rà soát & hiệu đính — ChuyenDe9.docx và ChuyenDe10.docx

Ngày rà soát: 2026-09-16 · Bản gốc được giữ tại `ChuyenDe9.docx.bak` và `ChuyenDe10.docx.bak`.

**Nguyên tắc hiệu đính:** giữ nguyên toàn bộ số liệu mô phỏng trong bảng; mọi mâu thuẫn được gỡ
bằng cách sửa câu chữ, đổi nhãn hoặc bổ sung quy tắc/chú thích cho khớp con số đang có.

---

## 0. Kết luận chung

Phần lớn số liệu trong hai tài liệu **đóng rất chặt**: tổng ngân sách, tỷ lệ phần trăm, p95,
số ca kiểm thử, thống kê lỗi đều khớp; 30/30 tài liệu tham khảo mỗi bên đều được trích dẫn thật
và ghi đúng nguồn, năm, hội nghị/nhà xuất bản.

Vấn đề tập trung ở bốn nhóm, đều đã sửa xong:

| Nhóm | CD9 | CD10 | Tổng |
|---|---:|---:|---:|
| Lỗi logic / số liệu | 12 | 14 | **26** |
| Thể thức | 10 | 11 | **21** |
| Hình vẽ vẽ lại | 2 | 2 | **4** |
| Câu khuôn mẫu viết lại | 64 | 64 | **128** |

**Kiểm chứng sau sửa:** 50/50 phép kiểm số liệu tự động đạt; 0 dấu vết tự tham chiếu còn lại;
zip toàn vẹn; số bảng và số ảnh không đổi; cấu trúc trường TOC và trường PAGE hợp lệ.

---

## 1. ChuyenDe9 — Kiến trúc hệ thống RAG

### 1A. Lỗi logic và số liệu

**9-1. §5.1 — sai số lượng chế độ suy giảm**
Bảng 3.7 có D0–D6, nhưng D0 là "chạy đầy đủ bảy pha", không phải chế độ suy giảm. §4.7 đã ghi đúng
"sáu mức suy giảm", riêng §5.1 ghi bảy.
- Trước: "…kích hoạt một trong **bảy** chế độ suy giảm đã khai báo trước."
- Sau: "…kích hoạt một trong **sáu** chế độ suy giảm đã khai báo trước."

**9-2. §3.8 — câu tự mâu thuẫn**
"Bảy khoản… trong đó hai pha đầu dùng chung một khoản" → nếu gộp thì chỉ còn sáu khoản.
- Trước: "Trần 10 giây được phân bổ thành **bảy khoản** gắn với bảy pha… **trong đó hai pha đầu dùng chung một khoản** vì cùng nằm trước bước truy xuất."
- Sau: "Trần 10 giây được phân bổ **cho bảy pha** xử lý của luồng trực tuyến cùng một khoản dự phòng, như Bảng 3.3 trình bày. **Bảng 3.5 gộp hai pha đầu thành một khoản duy nhất** vì cả hai cùng nằm trước bước truy xuất, nên bảng ngân sách rút gọn còn sáu khoản xử lý và một khoản dự phòng."

**9-3. §4.4 — phép cộng hở**
"Chín bất khớp, trong đó sáu + ba" đã kín chỗ, nhưng câu sau lại nói thêm "hai bất khớp đơn vị thời gian" —
mà chính đoạn đó khẳng định lỗi đơn vị *không* bị lược đồ bắt khi kiểu dữ liệu giống nhau, nên hai lỗi này
không thể nằm trong nhóm "kiểu". Bảng 4.3 ghi ba loại: trường bắt buộc, kiểu, **đơn vị**.
- Trước: "…**sáu** thuộc loại trường bắt buộc thiếu… và **ba** thuộc loại kiểu dữ liệu không tương thích. Đáng chú ý là hai bất khớp liên quan tới đơn vị thời gian:"
- Sau: "…**bốn** thuộc loại trường bắt buộc thiếu…, **ba** thuộc loại kiểu dữ liệu không tương thích và **hai thuộc loại sai đơn vị đo**. Đáng chú ý là cả hai bất khớp đơn vị đều rơi vào đơn vị thời gian:" (tổng vẫn là 9, khớp Bảng 4.3)

**9-4. §3.9 — hai nhóm giải pháp không được mô tả ở đâu cả**
Văn bản chỉ mô tả **năm** nhóm rồi kết luận "điểm chung của **bảy** nhóm"; Bảng 3.6 có 7 dòng.
→ Đã chèn một đoạn mới mô tả **nhóm thứ sáu (Tối ưu tầng lưu trữ)** và **nhóm thứ bảy
(Tính toán trước ngoại tuyến)**, bám đúng cơ chế / pha chịu tác động / rủi ro đã ghi ở Bảng 3.6.

**9-5. Hình 3.1 không khớp Bảng 3.1** *(lỗi nặng nhất về nội dung)*
- Hình thiếu hẳn **M02 Bộ chuẩn hóa nguồn** (L2 chỉ vẽ 3 hộp thay vì 4).
- L1 trong hình là "Hàng đợi và sự kiện / Quan trắc / Định danh", còn Bảng 3.1 là M17 + M18.
- Hình không ghi mã mô-đun nên không đối chiếu được với bảng.
- Hình viết "từ **khoá**", thân bài viết "từ **khóa**".
→ **Đã vẽ lại** `system_architecture.png`: đủ 18 mô-đun đúng theo Bảng 3.1, có mã M01–M18,
L7 ghi rõ "(Chuyên đề 8)", thống nhất "từ khóa", giữ nguyên bảng màu và bố cục bản gốc.

**9-6. M17 ở L1 phá vỡ nguyên tắc phụ thuộc một chiều**
Bảng 3.1 đặt M17 "Cổng vào và xác thực" ở L1, trong khi Bảng A.1 ghi M14 (L6) "được gọi bởi M17"
→ lời gọi L1 → L6, mâu thuẫn với nguyên tắc thứ nhất ở §3.1.
→ Đã bổ sung vào §3.2: quy tắc một chiều áp dụng cho chuỗi **L2–L7**; **L1 là lớp dịch vụ xuyên suốt**
nằm ngoài chuỗi tuyến tính; riêng chức năng cổng vào của M17 đứng ở biên trên, nên quan hệ M17→M14
không phải lời gọi ngược lớp.

**9-7. Bảng A.1 chỉ có 10/18 mô-đun** dù tiêu đề là "Danh mục mô-đun và ma trận trách nhiệm"
→ Đã bổ sung đủ **18 dòng** theo đúng thứ tự M01→M18 (thêm M02, M04, M08, M09, M11, M15, M17, M18),
nội dung suy ra nhất quán từ Bảng 3.1, 3.3, 3.4.

**9-8. Tên lược đồ lệch nhau giữa ba nơi**
Bảng 3.4 dùng `retrieve.vector.v2` / `retrieve.lexical.v2`; Phụ lục B và Phụ lục E (IC-012) dùng
`retrieve.request.v2`.
→ Thống nhất: Bảng 3.4 nay ghi `retrieve.request.v2 (vector)` và `retrieve.request.v2 (lexical)`.

**9-9. Bảng 3.7 — ngưỡng D2 (45% tại P4) thấp hơn D3 (55% tại P5)**, không nói rõ các mức loại trừ
nhau hay cộng dồn.
→ Đã thêm ghi chú dưới bảng: tỷ lệ tính trên ngân sách còn lại **tại thời điểm bắt đầu từng pha**,
nên ngưỡng pha sau không so trực tiếp được với pha trước; các mức **có thể cộng dồn**; Bảng 4.7 kiểm
từng mức độc lập.

**9-10. Bảng 4.5 — dòng "Tổng" ghi 48 ca nhưng 0,88 và 0,95 chỉ tính trên 42 ca** (G5 ghi "Không áp dụng").
→ Đã thêm ghi chú: hai cột đó trung bình trên 42 ca có áp dụng chỉ số; cột trích dẫn đúng tính trên đủ 48 ca.

**9-11. §3.8 trích sai nguồn**
Dùng `[9]` (*The Tail at Scale* — nói về tail latency) để chống lưng luận điểm về **thời gian cảm nhận**.
→ Đã bổ sung `[31] Nielsen, J. (1993). Usability Engineering. Morgan Kaufmann.` vào danh mục và trích
`[31]` tại vị trí này. (ChuyenDe10 vốn đã dùng đúng nguồn Nielsen cho cùng luận điểm.)

**9-12. §3.2 mô tả L7 có 3 thành phần nhưng Bảng 3.1 không có dòng L7 nào**
→ Đã thêm câu: mười tám mô-đun thuộc L1–L6; ba thành phần lớp trình bày đặc tả tại Chuyên đề 8.

### 1B. Thể thức

| Mã | Nội dung | Xử lý |
|---|---|---|
| 9-13 | **Không có số trang** (footer rỗng, không có trường PAGE) | Chèn trường `PAGE` canh giữa vào footer |
| 9-14 | **Mục lục không có số trang** (danh sách tĩnh, không có trường TOC) | Bọc thành trường `TOC \o "1-2" \h \z \u` + bật `updateFields`; nội dung hiện tại giữ làm bản đệm |
| 9-15 | Mục lục ghi "CHƯƠNG 1**:**" còn thân bài "CHƯƠNG 1**.**" (5/5 chương) | Thống nhất dấu chấm |
| 9-16 | Danh mục bảng thiếu Bảng 0.1, A.1, E.1, F.1 | Bổ sung 4 mục |
| 9-17 | Danh mục từ viết tắt có 9 mục **chưa từng xuất hiện** trong bài: LLM, ANN, API, SLO, SLA, QPS, CI/CD, KPI, OCR | Bỏ (20 → 11 mục) |
| 9-18 | `D1--D2` (Bảng 4.6) — gạch nối đôi kiểu LaTeX | → `D1–D2` |
| 9-19 | Hình 3.5 viết "1200 ms", "4200 ms" trong khi thân bài dùng "1 200 ms" | Vẽ lại với dấu cách hàng nghìn |
| 9-20 | Bảng 3.2 cột "Mô-đun" trộn mã mô-đun với mã lớp ("M17, L1"; "L1") | → "M17, hàng đợi thu nạp" và "M05, M06, kênh sự kiện" |
| 9-21 | Bảng 3.5 cột "Mục tiêu p95 **đo được** (ms)" lẫn mục tiêu với đo được | → "Mục tiêu p95 (ms)" |
| 9-22 | Metadata: `subject` = "Prompt Engineering, Faithfulness" (sót từ tài liệu khác), `description` = "generated by python-docx" | Làm sạch |

### 1C. Văn phong

**9-23.** 64 câu khuôn mẫu (2 câu cuối mỗi tiểu mục), nhắc lại nguyên số + tên mục, ví dụ:

> *Trước:* "Điểm cần phòng ngừa là kiến trúc được vẽ đẹp trên sơ đồ nhưng không kèm điều kiện kiểm chứng;
> **trong 1.1. Bối cảnh và động lực nghiên cứu**, kiểm soát phải được thể hiện bằng một phép thử tích hợp
> có thể tái lập."
>
> *Sau:* "Rủi ro thường gặp là một kiến trúc được vẽ đẹp trên sơ đồ nhưng không kèm bất kỳ điều kiện kiểm
> chứng nào. Để tránh điều đó, mỗi khẳng định về kiến trúc trong chuyên đề đều phải đi kèm một phép thử
> tích hợp chạy lại được."

→ Toàn bộ 64 câu đã viết lại thành văn xuôi, **giữ nguyên ý** (giải pháp + bằng chứng đánh giá; rủi ro +
điều kiện thất bại), bỏ hoàn toàn phần tự tham chiếu. 10 mở đầu câu bị lặp nhiều nhất đã được đa dạng hóa
thêm một lượt.

---

## 2. ChuyenDe10 — Phần mềm trợ lý ảo, thử nghiệm và đánh giá

### 2A. Lỗi logic và số liệu

**10-1. Mâu thuẫn song song ⇄ tuần tự ở K5–K6** *(lỗi số liệu nặng nhất)*
§3.6 và Bảng 3.4 nói K6 "chạy **song song** với K5". Nhưng Bảng 3.4 cộng tuần tự cả 7 khoản
(26 000 + 4 000 = 30 000), Hình 3.3 vẽ K5–K6 nối tiếp trên một trục, và §4.5 khẳng định
"trung vị toàn trình lớn hơn **tổng** trung vị của bảy giai đoạn khoảng 0,3 giây" (16 400 so với 16 120)
— tức bảy giai đoạn được cộng tuần tự.
- Sau: K6 **chồng lấn một phần** với K5; khoản 5 500 ms là **phần thời gian còn tăng thêm sau khi đã trừ
  phần chồng lấn**, nên các khoản vẫn cộng tuần tự thành trần 30 giây. Sửa đồng bộ ở §3.6, cột kỹ thuật
  của K6 trong Bảng 3.4 ("Chạy song song với K5" → "Chồng lấn một phần với K5"), và ghi chú trong Hình 3.3.

**10-2. §4.6 — ngưỡng cảnh báo không thể chạm tới**
"Ba máy ghi nhận **ngưỡng cảnh báo bộ nhớ** ở khoảng giờ thứ năm" — ngưỡng này (Bảng 3.5) là **tăng quá 8%**,
nhưng mức tăng cao nhất đo được chỉ **5,9%** (Bảng 4.5) và đường cong được mô tả "gần như phẳng" sau 2 giờ.
- Sau: "Ba máy chạm **ngưỡng cảnh báo kích thước bộ nhớ đệm**, tức vượt 70% trần của đệm, ở khoảng giờ thứ
  năm; đây cũng là thời điểm cơ chế thu hồi theo tuổi được kích hoạt. **Không máy nào chạm ngưỡng cảnh báo
  bộ nhớ thường trú ở mức tăng 8%**, và do đó cũng không máy nào chạm ngưỡng hành động."
  (Khớp dòng cuối Bảng 3.5 và khớp luôn với "ba lần giảm nhẹ" đã mô tả ở đoạn trên.)

**10-3. BUG-052 xếp S3 nhưng biểu hiện lại vi phạm tiêu chí bắt buộc**
"Bộ nhớ tăng **14%** sau 6 giờ" vi phạm X3 (≤ 10%) nhưng bị xếp **S3 = không chặn phát hành**, trong khi
BUG-033 (vượt trần khởi động, vi phạm X1) lại được xếp S2 → phân loại không nhất quán.
- Sau (giữ nguyên Bảng 4.6): biểu hiện BUG-052 đổi thành "**Bộ nhớ tăng nhanh, chạm ngưỡng cảnh báo 8%
  sau 6 giờ chạy**" — đúng ngữ nghĩa S3, và ăn khớp với sửa 10-2.
- Đồng thời bổ sung **quy tắc nâng mức** vào §2.8 và ghi chú dưới Bảng D.1: *"lỗi làm vi phạm một tiêu chí
  xuất xưởng bắt buộc được xếp tối thiểu ở mức hai"* — quy tắc này biện minh đúng cho BUG-033 = S2.

**10-4. Con số "1 380 lượt kiểm" ở Bảng 4.8 (X7) không có nguồn gốc**
Xuất hiện đúng một lần, không được định nghĩa ở §4.2 hay §4.3, không có trong Bảng 4.1.
→ Đã bổ sung ở §4.3: nhóm G3 gồm **46 ca**, mỗi ca kiểm trung bình **30 lượt** trích dẫn hiển thị
→ **1 380 lượt**.

**10-5. Ràng buộc "câu hỏi đầu tiên ≤ 1,5 lần" nêu ở §3.6 nhưng Chương 4 không báo cáo kết quả**
→ Đã bổ sung số đo ở §4.5 (**1,18 lần**, dưới trần 1,5 lần) và thêm ca `TC-G2-021` vào Bảng A.1.

**10-6. Mâu thuẫn liên tài liệu với Chuyên đề 9**
CD9 đặt kho vector, chỉ mục từ khóa và bước **sinh vector truy vấn** ở máy chủ (M05, M06, M08); CD10 lại cho
máy trạm có chỉ mục cục bộ 9 s và làm nóng mô hình nhúng 5,5 s khi khởi động.
→ Đã bổ sung ở §3.1: chỉ mục cục bộ là **chỉ mục từ khóa kèm bản sao kho siêu dữ liệu nguồn**, phục vụ
tra cứu nhanh và chế độ ngoại tuyến; kho vector và bước sinh vector truy vấn của đường hỏi đáp trực tuyến
**vẫn nằm ở máy chủ** theo Chuyên đề 9; mô hình nhúng cục bộ là bản rút gọn dùng khi mất kết nối.

**10-7. Beta: 3 090 giờ / 18 người / 21 ngày ≈ 8,2 giờ mỗi người mỗi ngày, kể cả cuối tuần**
→ Đổi thành **"21 ngày làm việc"** (đồng bộ ở §4.4, Bảng 4.3, Tóm tắt, §4.8, §5.1, §5.2 và
`duration_working_days` ở Phụ lục E), và nói rõ 3 090 giờ là **thời gian ứng dụng mở tích lũy**, gồm cả
những khoảng người dùng không thao tác.

**10-8. "Số phiên có sự cố = 29" trùng số với "29 báo cáo lỗi hợp lệ"** — hai đại lượng khác nhau.
→ Thêm chú thích phân biệt ngay trong Bảng 4.3.

**10-9. Tên chỉ số X11 lệch nhau ở bốn nơi** (trung bình / trung vị / MTTR)
→ Thống nhất **"trung vị"** (khớp số liệu thực ở §4.7): sửa Bảng 3.7 và §2.9; mục MTTR đã bỏ khỏi bảng
viết tắt vì không dùng.

**10-10. Mã C.2 dùng một chu kỳ 30 s cho mọi chỉ số** trong khi Bảng 3.5 quy định mạch đập giao diện lấy
mẫu mỗi **5 giây**.
→ Đã viết lại script: tách `MEM_SAMPLE_INTERVAL_S = 30` và `UI_SAMPLE_INTERVAL_S = 5`, vòng lặp chạy nhịp
5 s và lấy mẫu tài nguyên mỗi 6 nhịp; nhân tiện sửa lỗi tiềm ẩn `samples[-1]` khi danh sách rỗng.
Mã mới đã được kiểm tra hợp lệ cú pháp Python.

**10-11. Mốc "hiển thị khung giao diện" (trung vị 2,9 s) nhỏ hơn tổng trung vị K1+K2 (3 590 ms)** —
mâu thuẫn với chính lập luận "trung vị cộng dồn ≈ toàn trình" ở cùng mục.
→ Đã thêm câu giải thích: mốc này rơi vào **giữa** giai đoạn K2 vì khung được dựng theo phần.

**10-12. §4.2 liệt kê chiều chức năng chỉ nêu 6 nhóm**, bỏ sót **G2 (Khởi động và trạng thái sẵn sàng)**
→ Đã bổ sung.

**10-13. §4.7 gọi thành phần là "bộ quản lý bộ nhớ đệm"** — Bảng 3.1 không có tên này (C08 là
"Bộ nhớ đệm cục bộ") → Đã dùng đúng tên.

**10-14. Phụ lục B ghi `version: 1.0.0` / `source_tag: v1.0.0` nhưng bản đang thử nghiệm là `1.0.0-rc3`**
(Phụ lục D và E) → Thống nhất `1.0.0-rc3`.

### 2B. Thể thức

Đã xử lý đúng các mục 9-13 → 9-22 tương ứng, cộng thêm:

| Mã | Nội dung | Xử lý |
|---|---|---|
| 10-15 | `S1--S4` (Bảng 0.1) — gạch nối đôi | → `S1–S4` |
| 10-16 | 11 từ viết tắt **chưa từng xuất hiện** trong bài: LLM, CI/CD, MSI, TTR, MTBF, MTTR, RSS, GUI, API, KPI, UAT | Bỏ (18 → 7 mục) |
| 10-17 | Hình 3.3 ghi "Làm nóng **bộ nhúng**" còn Bảng 3.4 ghi "Làm nóng **mô hình nhúng**" | Vẽ lại, thống nhất tên + dấu cách hàng nghìn + ghi chú chồng lấn K5/K6 |
| 10-18 | Hình 3.1 đúng nội dung nhưng thiếu mã C01–C15 | Vẽ lại, bổ sung mã |
| 10-19 | Danh mục bảng thiếu Bảng 0.1, A.1, D.1, F.1 | Bổ sung 4 mục |

### 2C. Văn phong

**10-20.** 64 câu khuôn mẫu, xử lý cùng cách với 9-23; 12 mở đầu câu bị lặp nhiều nhất đã được đa dạng hóa.

---

## 3. Đồng nhất giữa hai tài liệu

- **X-1.** CD9 dùng "đầu – cuối" (11 lần), CD10 dùng "đầu cuối" (3 lần) → thống nhất **"đầu – cuối"**.
- **X-2.** Số trong hình nay dùng cùng quy ước với thân bài (dấu cách hàng nghìn).
- **X-3.** Tên chỉ số dùng chung đã thống nhất (trung vị, p95).
- **X-4.** CD10 §3.1 nay dẫn đúng phạm vi kiến trúc của CD9 (xem 10-6).
- **X-5.** `[28] ISO/IEC 17025:2017` ghi giống hệt ở cả hai tài liệu — đã đúng từ đầu, không sửa.

---

## 4. Kết quả kiểm chứng

**Kiểm tra số liệu tự động — 50/50 đạt:**
- CD9: Bảng 3.3 tổng = 10 000 · Bảng 3.5 tổng khoản = 10 000, tổng % = 100 · Bảng 4.1 tổng ca = 180,
  tổng thời lượng = 135 phút · Bảng 4.2 tổng ca = 48, tổng lượt = 1 080 · Bảng 4.3 mọi dòng thỏa
  "đạt + vấn đề = số ca", tổng 180/153/27 · Bảng 4.4 mọi ô "mức dùng ngân sách" = p95 ÷ ngân sách ·
  Bảng 4.6 mọi dòng thỏa "biên còn lại = 10 000 − p95".
- CD10: Bảng 3.4 tổng = 30 000, tổng % = 100 · Bảng 4.1 tổng ca = 240, bắt buộc = 176 · Bảng 4.2 mọi dòng
  thỏa "đạt + thất bại = số ca", tổng 240/214/26 · Bảng 4.4 mọi ô mức dùng ngân sách · Bảng 4.5 mức tăng %
  từng máy và trung bình · Bảng 4.6 tổng hàng và cột = 55/47/8 · 240 + 47 = 287 · 46 × 30 = 1 380.

**Kiểm tra dấu vết còn sót — 0 lỗi:** không còn tự tham chiếu "N.N. Tên mục", không còn câu SOL khuôn mẫu,
không còn `--`, không còn "CHƯƠNG n:", không còn "khoá", không còn tên lược đồ cũ, không còn "bảy chế độ
suy giảm", không còn "Chạy song song với K5", không còn "trung bình khắc phục".

**Toàn vẹn tệp:** zip OK · bảng 22/22 và 23/23 không đổi · ảnh 7/7 mỗi tệp · paragraph +8 (CD9) và +5 (CD10)
đúng bằng số đoạn chủ ý thêm · trường TOC `[begin, separate, end]` hợp lệ · trường PAGE trong footer hợp lệ.

---

## 5. Việc bạn cần làm sau khi nhận tệp

1. **Mở bằng Microsoft Word một lần** để trường TOC và số trang được điền. Word sẽ tự cập nhật nhờ
   `updateFields`; nếu không, bấm vào Mục lục rồi nhấn **F9** (hoặc Ctrl+A, F9 để cập nhật toàn bộ).
   Trên LibreOffice: **Tools ▸ Update ▸ Indexes and Tables**.
2. **Rà lại hai chỗ mang tính quyết định chuyên môn**, vì tôi chọn hướng giữ nguyên số liệu:
   - **10-1**: cách diễn giải "khoản K6 là phần thời gian tăng thêm sau khi trừ chồng lấn". Nếu thực tế
     K5 và K6 chạy song song hoàn toàn thì phải tính lại Bảng 3.4, Bảng 4.4 và §4.5.
   - **10-3**: biểu hiện BUG-052 đổi từ "tăng 14%" thành "chạm ngưỡng cảnh báo 8%". Nếu muốn giữ con số 14%
     thì phải nâng BUG-052 lên S2 và cập nhật Bảng 4.6 (S2: 11→12, S3: 22→21) cùng §4.7 và §5.1.
3. **Số liệu bổ sung mới đưa vào** (đều là số minh họa, nhất quán với phần còn lại, nhưng bạn nên xác nhận):
   - §4.3 CD10: "mỗi ca kiểm trung bình **30 lượt** trích dẫn" (để 46 × 30 = 1 380).
   - §4.5 CD10: tỷ số câu hỏi đầu tiên = **1,18 lần**.
