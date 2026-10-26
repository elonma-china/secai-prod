# Context tính năng "Giải thích quy trình tạo PoC" (explain-poc) — SecAI

Trạng thái: **CHƯA CODE** — thư mục này là tài liệu đặc tả (spec) + kế hoạch kiểm thử được chốt trong phiên trao đổi yêu cầu khách hàng, không phải tài liệu đối chiếu mã đã chạy như [module-map/poc/](../poc/readme.md). Cấu trúc mô phỏng [module-map/poc/](../poc/readme.md) theo đúng yêu cầu: cùng bộ file, cùng vai trò, để khi code xong chỉ cần đổi trạng thái "ĐÃ CODE" và chạy đúng quy trình kiểm thử đã viết sẵn ở đây — không phải viết lại từ đầu.

## Vì sao có tính năng này

Khách hàng yêu cầu bổ sung vào output của `/ai/exploit*`: **giải thích quy trình đã tạo ra PoC này** — ví dụ với case "có PoC trong DB": tìm kiếm trong catalog → tìm thấy → ưu tiên nguồn nào → lấy bao nhiêu artifact → có cần cắt gọn/hợp nhất không → chèn kết quả. Đây là yêu cầu lộ **quy trình nội bộ**, khác hẳn Mục 3 hiện tại ("Tổng quan lỗ hổng" — giải thích *lỗ hổng*, không giải thích *hệ thống đã làm gì*).

**Tiêu chuẩn nghiệm thu = kiểm trên giao diện web, không phải đọc response JSON.** Khách hàng chấm bằng cách mở trang `/ai/exploit`, bấm "Sinh mã khai thác", và **nhìn thấy trực tiếp trên màn hình** phần giải thích quy trình — không chấp nhận "có trong `metadata` nhưng không hiện ở đâu trên UI". Vì vậy toàn bộ 3 file spec/test (`01`, `02`, `03`) đã được viết/sửa lại theo đúng chuẩn này: [01](01-api-va-luong-xu-ly.txt) mục 4 chốt bắt buộc nối phần giải thích vào `text` (không chỉ field JSON) vì đó là cách DUY NHẤT tự hiển thị trên card "Phân tích / hướng dẫn" có sẵn của FE mà không cần sửa 1 dòng FE nào (cơ chế đã xác nhận bằng cách đọc `ExploitScreen.tsx`/`FileCode.tsx` thật); [02](02-test-giao-dien-tung-buoc.txt) mục 3 đặt bước "quan sát UI thật (snapshot/ảnh chụp)" làm bước kiểm chính, DevTools chỉ phụ trợ; [03](03-tieu-chi-pass.txt) mục 1 có tiêu chí #0 — hard gate đứng trước mọi tiêu chí khác — NOT PASS ngay nếu không thấy gì trên UI, bất kể dữ liệu JSON phía sau đúng đến đâu.

**Quyết định thiết kế quan trọng nhất** (lý do xem [01](01-api-va-luong-xu-ly.txt) mục 2): quy trình này phải do **server sinh tất định** từ các biến nhánh code đã có sẵn trong `_build_exploit_grounding()`/`exploit_prefetch()` (`src/secai_gateway/poc/prefetch.py`, `src/secai_gateway/api/routes.py`), **không giao cho LLM tự kể lại**. Lý do: (a) LLM kể quy trình đúng là thứ `exploit.md:46` đang cấm tuyệt đối (chống leak system-prompt/tool nội bộ), sửa ngược lại rule này rủi ro cao; (b) LLM kể có thể không khớp chính xác với nhánh code thật sự đã chạy (hallucinate bước không xảy ra); (c) Python code đã biết 100% chính xác đã đi qua nhánh nào — không cần đoán.

**Lưu ý đã bổ sung sau review Opus (xem `03-tieu-chi-pass.txt` mục 6 phần D):** về bản chất, tính năng này **CỐ Ý tiết lộ đúng loại thông tin mà `exploit.md:46` muốn giấu khỏi model** (tra catalog → ưu tiên nguồn nào → có cache/fetch thêm không → có cắt gọn/hợp nhất không) — chỉ khác là do server render có kiểm soát (từ vựng/độ chi tiết do sản phẩm quyết định, qua rào tiêu chí #5 ở [03](03-tieu-chi-pass.txt)) thay vì model tự kể tùy tiện. Đây KHÔNG phải mâu thuẫn với rule dòng 46 — đó vẫn đúng với Mục 1-3 (LLM viết); Mục 4 (giải thích quy trình) là một quyết định sản phẩm riêng, tách bạch hoàn toàn khỏi phạm vi rule đó. Người code cần hiểu rõ ranh giới này để không nhầm là đang "bật lại" thứ đang bị cấm.

## Đọc theo thứ tự

| File | Nội dung |
|---|---|
| [01-api-va-luong-xu-ly.txt](01-api-va-luong-xu-ly.txt) | Đặc tả field mới trong response, bảng ánh xạ nhánh-code → câu giải thích, vị trí cần sửa trong mã nguồn |
| [02-test-giao-dien-tung-buoc.txt](02-test-giao-dien-tung-buoc.txt) | Các bước bấm trên `ias.id.vn`/Devspace để kiểm tính năng SAU KHI code xong, tái dùng đúng màn hình/selector đã xác nhận ở [module-map/poc/02](../poc/02-test-giao-dien-tung-buoc.txt) |
| [03-tieu-chi-pass.txt](03-tieu-chi-pass.txt) | Tiêu chí PASS: tất định, khớp nhánh code thật, không leak tên tool, không phá Mục 1-3 hiện có, phủ đủ các tier |
| [04-test-data.json](04-test-data.json) | Bộ case theo từng nhánh pipeline (canonical / fixed / prune / consolidate / synthesis / not_found), tái dùng CVE đã xác minh ở [module-map/poc/04](../poc/04-test-data.json) khi trùng nhánh |
| [results/](results/) | Kết quả mỗi phiên kiểm thử sau khi code xong — hiện trống vì chưa có code để chạy |

## Phạm vi — chỉ 2 endpoint đang dùng để sinh PoC đọc-để-tham-khảo

Giống [module-map/poc](../poc/readme.md#chức-năng-đang-dùng): `POST /ai/exploit`, `POST /ai/exploit/stream`. KHÔNG đụng tới `/ai/exploit/run`, `/ai/exploit-advance*`, `/ai/verify` (khai thác target thật, phương pháp D).

## Tổ chức agent khi review/kiểm thử (3 vai trò)

- **Agent viết/sửa tài liệu + code** (vai trò của phiên tạo thư mục này): tạo/sửa 5 file theo mẫu [module-map/poc](../poc/readme.md), sau đó khi có code thật thì cập nhật [01](01-api-va-luong-xu-ly.txt) từ "spec" sang "đối chiếu mã đã chạy" đúng như `module-map/poc/01` đang làm.
- **Agent review (Opus)**: đọc toàn bộ 5 file + đối chiếu code nguồn thật, chấm folder này đã đủ chuẩn để dùng làm căn cứ code/test hay chưa — tiêu chí review ở cuối [03](03-tieu-chi-pass.txt). Nếu CHƯA đủ, agent viết tài liệu sửa lại, lặp lại review tới khi Opus duyệt.
- **Agent thực thi test giao diện (Haiku, dùng MCP)**: CHỈ chạy sau khi code xong — dùng `preview_navigate`/`preview_snapshot`/`preview_click`/`preview_type` theo đúng bước ở [02](02-test-giao-dien-tung-buoc.txt), không tự kết luận PASS/FAIL, chỉ ghi quan sát để agent review/tổng hợp chấm. Trước khi code xong, vai trò này chỉ làm được bước "dry-run xác nhận baseline UI/selector hiện tại còn đúng" (xem [02](02-test-giao-dien-tung-buoc.txt) mục 0).

## Nguồn context cần đọc khi cập nhật

Đường dẫn tính từ thư mục này:

- [Routes `/ai/exploit*`](../../src/secai_gateway/api/routes.py) — hàm `_exploit`/`_exploit_stream`, `exploit_prefetch`, `_build_exploit_grounding` (nơi các biến nhánh đã tồn tại sẵn: `coverage`, `cve_known`, `kb_status`, `canonical_entry`, `fixed`, `cached_hit`, `extra_real`, `web_real`, `is_multifile`, `consolidated`, `poc_strategy`).
- [Quyết định 4 tier Section 1](../../src/secai_gateway/poc/inject.py) (`enforce_section1`) — nơi banner tất định hiện tại đang được chèn, dùng làm **mẫu kỹ thuật** cho cách chèn mục mới (cùng cơ chế, không qua LLM).
- [Prefetch](../../src/secai_gateway/poc/prefetch.py), [cache tạm](../../src/secai_gateway/poc/ondemand_cache.py), [PoC đã fix/verify tay](../../src/secai_gateway/poc/fixed_pocs.py), [slice/prune](../../src/secai_gateway/poc/slicer.py), [LLM consolidate](../../src/secai_gateway/poc/consolidate.py).
- [Prompt profile hiện tại](../../src/secai_gateway/prompts/exploit.md) — đặc biệt dòng 46 (rule cấm LLM kể quy trình tra cứu) phải được giữ nguyên cho Mục 1-3; mục mới KHÔNG sửa rule này, chỉ thêm 1 khối tất định tách biệt.
- [Tài liệu gốc đã xác minh live](../poc/readme.md) — tái dùng danh sách CVE/lab/môi trường test đã xác minh 09/10/2026 khi trùng nhánh pipeline cần phủ.
