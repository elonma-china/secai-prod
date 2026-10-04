# Multilang frontend — frozen criteria v1

2026-10-03, **CRITERIA_READY; FE evidence NOT RUN**. [Wire contract](../api/multilang-contract.md) cho phép code mock trước backend; mock không là final integration. Sol FE chỉ sửa canonical FE snapshot trong worktree riêng, không chỉnh file/OCR UI hoặc FE đang dùng của user.

| ID | Test và điều kiện đạt |
|---|---|
| FE-01 | Màn dịch cũ mặc định giữ nguyên en↔vi và API/SSE cũ. Panel mới gọi capabilities; master404 thì ẩn/disable panel với lý do, không làm hỏng legacy. Show9 languages/16 directions theo registry, disabled/beta/stable đúng server, chỉ vi ở một đầu, swap hợp lệ. Không tự bật flag qua client. |
| FE-02 | POST duy nhất text/source_lang/target_lang, JSON atomic, không chờ SSE/job/poll. Thành công đọc translated_text/warnings/metadata; lỗi đọc error.code; xử lý400/403/413/422/429/502/503/504 và network/cancel. Payload escaping/code points không mất chữ. Giới hạn theo capabilities, server vẫn authoritative. |
| FE-03 | React plain text node, không dangerouslySetInnerHTML/unsafe Markdown. Arabic dir=rtl; Latin CVE/IP/URL/code ở bidi-isolated LTR spans khi hiển thị; copy lấy original response string nên nguyên code points/order. CJK/Cyrillic/Arabic local font fallback, không runtime CDN. ZWJ/ZWNJ joining, malicious HTML và bidi fixtures có browser evidence. |
| FE-04 | Labels/keyboard/tab/focus/aria loading/error/status, long text wrap, mobile+narrow/desktop không overflow; copy/select usable; cancel có AbortController; input đổi/new submission không bị stale response ghi đè. Không tự retry inference khi lỗi hoặc cancel. |
| FE-05 | Build và component tests trên dependency lock canonical. Mock đủ enabled/disabled/error/warning/stale/cancel states. Final real-backend smoke vi→zh, ja→vi, vi→ar có CVE/IP và cả2 en↔vi legacy; flagOFF/ON/rollback. Dịch thật cần quality/resource gates; mock screenshot không claim live quality. |
| FE-06 | File/OCR route/control giữ nguyên, không thêm ngôn ngữ mới vào upload/job. Composition integration khi file/OCR UI được ghép cần regression riêng. Không commit env/node_modules/dist/cache/assets không rõ license. |

Evidence lưu command/result, build/test SHA, desktop+mobile screenshots mỗi script đại diện, browser assertions và exact backend SHA/config. Beta label chỉ khi server status beta và user đã quyết định release beta; unit fixture beta được mock, không là quyền rollout. Source-language uncertainty/target uncertainty hiển thị lời cảnh báo dễ hiểu, không âm thầm đánh dấu chất lượng đạt.
