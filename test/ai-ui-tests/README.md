# AI UI Tests — Playwright smoke-test toàn bộ chức năng AI

Test tự động 6 tab AI trên https://secai.coremind.io.vn/ bằng Playwright:

| # | Tab | Test | Timeout |
|---|-----|------|---------|
| 1 | **Trợ lý ảo ATTT** (Chatbot) | Chat 1 câu CVE-2021-44228, verify phản hồi chứa "Log4j" | 90s |
| 2 | **Dịch văn bản** | Dịch 1 đoạn Việt → Anh, verify output có tiếng Anh | 60s (skip nếu service tắt) |
| 3 | **Sinh mã khai thác (v1)** | Tra CVE-2021-44228, verify có kết quả JNDI/LDAP | 120s |
| 4 | **Sinh PoC (v2)** | Nhập CVE+target, verify log stream bắt đầu (AC-2.1) | 5 phút |
| 5 | **Báo cáo thông minh** | Nhập Nmap log, verify có report markdown | 120s |
| 6 | **Đánh giá A/B** (Review) | Mở tab admin, verify giao diện hiện | 30s |

## Chạy

```bash
cd test/ai-ui-tests
npx playwright test              # tất cả 6 tab
npx playwright test -g "Chatbot" # chỉ chatbot
npx playwright test -g "Dịch"    # chỉ dịch
```

Thay URL: `PLAYWRIGHT_BASE_URL=http://localhost:8131 npx playwright test`

## Kết quả

- `results/*.json` — kết quả chi tiết từng tab
- `results/*.png` — screenshot
- `results/report.json` — Playwright JSON report

## Phụ thuộc

Dùng chung node_modules với exploit-v2 test:
```bash
ln -s ../../exploit-v2/playwright/node_modules node_modules
```
