# Báo cáo test 20 CVE ngoài catalog PoC (2026) — Mục 4 "Quy trình tạo PoC"

**Ngày test:** 2026-10-12
**Môi trường:** Devspace `:5173` → gateway prod thật `:8131` (image `secai-agent-gateway:0.5.14-explain-poc`, chính container đang serve `ias.id.vn`)
**Công cụ:** Playwright MCP, giao diện web thật (màn "Sinh mã khai thác")
**Nguồn chọn CVE:** Lấy từ chatbot nội bộ (`Trợ lý ảo ATTT`), hỏi trực tiếp "Những CVE nào được cập nhật trong CSDL nội bộ ngày DD/MM/2026?" cho 4 ngày gần nhất (12/10, 11/10, 10/10, 09/10/2026) — mỗi câu trả lời có `Hoạt động công cụ: qdrant_search(...)` xác nhận danh sách lấy thật từ KB, không phải model tự bịa.

## Vì sao các CVE này chắc chắn "ngoài DB" (ngoài catalog PoC)

Đây là các CVE **vừa được đồng bộ metadata (NVD) vào KB trong 4 ngày gần nhất** — catalog PoC (exploit-db/GitHub/Metasploit…) luôn có độ trễ so với thời điểm công bố/đồng bộ CVE, nên chắc chắn chưa kịp có PoC thật nào được ingest cho các mã này tại thời điểm test.

## Kết quả tổng hợp — 20/20 case

| # | CVE ID | CVSS / Mức độ | Sản phẩm | Kết quả Mục 4 (`section1_tier`) | Ảnh |
|---|---|---|---|---|---|
| 1 | CVE-2026-106369 | 4.2 MEDIUM | Google Chrome (Translate) | `synthesized_cve` | 01-CVE-2026-106369.png |
| 2 | CVE-2026-98306 | 7.0 HIGH | Linux Kernel (SRv6) | `synthesized_cve` | 02-CVE-2026-98306.png |
| 3 | CVE-2026-104728 | 4.3 MEDIUM | AutomatorWP (WP Plugin) | `synthesized_cve` | 03-CVE-2026-104728.png |
| 4 | CVE-2026-19735 | 4.8 MEDIUM | Zephyr TCP Stack | `synthesized_cve` | 04-CVE-2026-19735.png |
| 5 | CVE-2026-108881 | 5.3 MEDIUM | JeecgBoot | `synthesized_cve` | 05-CVE-2026-108881.png |
| 6 | CVE-2026-66480 | 5.3 MEDIUM | YITH WooCommerce Add-Ons | `synthesized_cve` | 06-CVE-2026-66480.png |
| 7 | CVE-2026-108722 | 2.3 LOW | open-computer-use | `synthesized_cve` | 07-CVE-2026-108722.png |
| 8 | CVE-2026-94666 | 7.5 HIGH | Generate PDF (CF7 Plugin) | `synthesized_cve` | 08-CVE-2026-94666.png |
| 9 | CVE-2026-108740 | 7.2 HIGH | GoatCounter | `synthesized_cve` | 09-CVE-2026-108740.png |
| 10 | CVE-2026-103478 | 6.4 MEDIUM | Premium Packages (WP Plugin) | `synthesized_cve` | 10-CVE-2026-103478.png |
| 11 | CVE-2026-107821 | 8.0 HIGH | MariaDB Server | `synthesized_cve` | 11-CVE-2026-107821.png |
| 12 | CVE-2026-108163 | 6.9 MEDIUM | Pingvin Share X | `synthesized_cve` | 12-CVE-2026-108163.png |
| 13 | CVE-2026-108161 | 7.7 HIGH | FusionPBX | `synthesized_cve` | 13-CVE-2026-108161.png |
| 14 | CVE-2026-96327 | 9.3 CRITICAL | WPLMS (WP Plugin) | `synthesized_cve` | 14-CVE-2026-96327.png |
| 15 | CVE-2026-75346 | 7.5 HIGH | OpENer (EIPStackGroup) | `synthesized_cve` | 15-CVE-2026-75346.png |
| 16 | CVE-2026-107459 | — | — | `synthesized_cve` | 16-CVE-2026-107459.png |
| 17 | CVE-2026-95125 | — | — | `synthesized_cve` | 17-CVE-2026-95125.png |
| 18 | CVE-2026-103957 | — | — | `synthesized_cve` | 18-CVE-2026-103957.png |
| 19 | CVE-2026-84224 | — | — | `synthesized_cve` | 19-CVE-2026-84224.png |
| 20 | CVE-2026-5048 | — | — | `synthesized_cve` | 20-CVE-2026-5048.png |

**Tỷ lệ pass tiêu chí #0 (hiển thị Mục 4 trên UI thật): 20/20 = 100%**

## Câu Mục 4 hiển thị (giống nhau cho toàn bộ 20 case — đúng thiết kế tất định)

> 🔧 Quy trình hệ thống (tự động, không phải AI viết):
> Không tìm thấy mã khai thác công khai nào cho CVE này trong danh mục nội bộ. Hệ thống đã xác nhận CVE này tồn tại trong cơ sở dữ liệu nội bộ (dựa trên mô tả kỹ thuật/CWE/CVSS) và tự tổng hợp một đoạn minh hoạ dựa trên thông tin đó — KHÔNG phải mã đã kiểm chứng.

Đi kèm luôn là cảnh báo ở cuối Mục 3: *"Mã PoC/hướng dẫn ở trên là bản GỢI Ý do AI tự tổng hợp, KHÔNG phải PoC đã fetch và xác thực từ nguồn thật. Đây là bản nháp CHƯA được thực thi..."*

## Nhận xét

1. **Nhất quán tuyệt đối (20/20):** Mọi case đều rơi đúng nhánh `synthesized_cve` — không có sai lệch, không có case nào bị gán nhầm nhánh "real"/"not_found".
2. **Minh bạch 2 lớp cảnh báo** (Mục 3 cuối + Mục 4) hoạt động đồng bộ ở mọi case, không mâu thuẫn nhau.
3. **Thời gian xử lý:** phần lớn case ~30s; case #11 (CVE-2026-107821, PoC C 1114 dòng) mất ~90s do độ dài mã sinh ra lớn hơn — không phải lỗi, chỉ là model sinh nhiều token hơn.
4. **Giới hạn của test này:** 20 CVE đều thuộc năm 2026 (không có case 2025 cụ thể trong lần lấy mẫu này, vì 4 ngày gần nhất đều chỉ cập nhật CVE 2026). Để có case 2025 thật cần lấy ở ngày xa hơn.
5. Không phát hiện case nào vô tình rơi vào `real_*` (catalog không có PoC trùng ngẫu nhiên cho 20 mã này) — khớp kỳ vọng ban đầu.

## Vị trí ảnh

`module-map/explain-poc/results/batch20/01-CVE-2026-106369.png` → `20-CVE-2026-5048.png` (20 ảnh full-page screenshot).
