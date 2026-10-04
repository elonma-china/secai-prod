# REPORT MVP — Phạm vi ưu tiên mới nhất

Tài liệu này ghi nhận chỉ đạo mới của người dùng: “làm nhanh, test đủ, đạt yêu cầu
ở ngưỡng 70% thì pass, review rồi báo tôi”. **Ưu tiên hơn các stage gates và phạm vi
release đầy đủ trong các file report-*.md trước đây.** Giữ những file đó làm đặc tả
đích; không coi hạng mục hoãn là PASS. Không cần chờ ASSESS A/B/C/D, baseline live,
hoặc adapter để bắt đầu code MVP; baseline local có thể chạy song song triển khai.

## Phạm vi tối thiểu và điều kiện cứng

| ID | Hạng mục | Điều kiện PASS và bằng chứng |
|---|---|---|
| MVP-01 | Template lưu DB | Tạo bằng form hoặc nhập DOCX/Markdown/TXT; chuyển thành structured draft, người dùng xem/sửa trước lưu sử dụng; cùng schema validation ở hai đường; lưu bền sau restart; không tự thêm legacy DOC. Kho lưu file gốc và workflow xuất bản nhiều bước hoãn khỏi MVP |
| MVP-02 | Sinh báo cáo | Chọn template và nhập dữ liệu; dùng LLM hiện tại, template trong context; output theo template, parse được; thiếu dữ liệu ghi rõ; không bịa CVE/IP/hash/URL/số liệu quan trọng; invalid output lỗi rõ, bounded retry, không trả draft sai |
| MVP-03 | Prompt chỉnh báo cáo | Edit instruction đi qua cùng validator/grounding/limits; tạo report revision mới trong DB, giữ original và provenance template/prompt; không sửa shared template; stale/concurrent edit không overwrite mất lịch sử; positive edit thực sự hoạt động |
| MVP-04 | Quyền nội bộ MVP | Tất cả endpoint mới, kể cả list/get/revision/import/generate/edit, được chặn bằng REPORT_API_KEY riêng, không đưa ADMIN_API_KEY toàn hệ thống cho FE; thiếu/sai key bị reject trước đọc dữ liệu hoặc gọi LLM. Cấu hình key rỗng không mở endpoint. Chỉ phạm vi workspace nội bộ dùng chung khóa, không tuyên bố cách ly multiuser/tenant |
| MVP-05 | FE đầy đủ flow | Form + upload→draft review→save/use; generate→view→prompt edit→revision history. Key nhập runtime, không hardcode/build env/public bundle; không commit/log/key trong URL, không persist localStorage mặc định; lỗi 401/403/413/429/validator/upstream có phản hồi rõ |
| MVP-06 | Chống input/file/render abuse | Giới hạn upload/decompressed archive/char/token/time/retry; reject DOCX macro/XXE/zip bomb/traversal/spoof formats; không SSTI/code execution/ngoài ý muốn network; Markdown/HTML render chống XSS; prompt edit/injection không biến instruction thành evidence |
| MVP-07 | Tương thích | Feature flag mặc định OFF; old API/schema/status/SSE không đổi; module additive, shared hook tối thiểu, không ghi đè nhánh khác; preserve sanitizer/redaction/airgap. Không phơi MVP ra public hoặc coi key chung là danh tính từng user |
| MVP-08 | Tests và review | Unit/integration liên quan phải PASS toàn bộ; baseline local và full regression trước/sau khi khả thi, báo dependency fail/skip thật; FE build PASS; independent reviewer kiểm diff, hard criteria, negative auth/file/XSS và happy paths. 70% không cho phép 30% test fail |

Ngưỡng cứng từ report-common RC-01..05 vẫn áp dụng: output trả thành công phải đúng
cấu trúc/parse 100%, hallucination thực thể quan trọng = 0 trên tập kiểm, API cũ giữ
nguyên, revision cập nhật đúng. Fail một hard gate không được bù bằng điểm nội dung.
Lịch sử MVP ghi revision/thời gian/provenance, không giả người dùng cá nhân hoặc
tuyên bố audit immutable. Full audit workflow hoãn khỏi MVP theo yêu cầu không
cầu toàn. Có lỗi bảo mật Critical/High đã biết chưa sửa thì không PASS MVP review.

## Ngưỡng nội dung 70% và phép đo riêng

- Người dùng đã chốt ngưỡng nội dung **70%**; không cần chờ baseline để xác nhận lại
  con số này. Đây là ngưỡng nội dung, không phải test coverage hoặc phần trăm test chạy.
- Khóa danh sách cases trước run; ghi template/input IDs, provenance tổng hợp hoặc
  thực được duyệt, model/config/revision, kết quả và rubric. Fixtures tổng hợp dùng
  test không được gọi là corpus khách hàng hoặc bằng chứng inference thật.
- Rubric từng báo cáo: đủ ý theo input/template, chính xác về ý nghĩa, rõ ràng, đúng
  văn phong; mỗi chiều 0 (không đạt), 1 (một phần), 2 (đạt). Điểm nội dung của case
  = tổng điểm/8. Đánh giá bởi người/reviewer độc lập có rationale, hoặc judge offline
  được ghi rõ và kiểm chéo; không suy điểm từ HTTP 200 hay validator mock.
- `N_valid` = mọi request hợp lệ trong tập đã khóa, bao gồm timeout/error/refusal/
  hết retry; `N_success` = report/revision hoàn tất và qua validator. Điểm nội dung
  end-to-end = tổng điểm case / (8 × N_valid), request không ra report hợp lệ có
  điểm 0. **PASS content khi ≥70%**, mọi hard gate PASS và có bằng chứng inference
  thật hợp lệ. Báo thêm conditional score trên N_success và completion/acceptance
  N_success/N_valid; N_valid=0 hoặc N_success=0 không PASS.
- Generation và edit chấm riêng, báo per-template và tổng; không dùng template tốt
  hoặc generation tốt che template/edit fail. Chưa có đủ evidence/template/runtime
  thì content PENDING/BLOCKED, dù code/tests/build/review đã PASS; báo user đúng phạm vi.
- Bảo lưu phê duyệt dữ liệu trước khi gửi host ngoài và ngân sách/quyền có sẵn; yêu
  cầu tăng tốc không tự cấp quyền gửi dữ liệu nhạy cảm, thuê GPU hoặc đổi model.

## Hoãn minh bạch, không cản code MVP

Hoãn advanced prompt catalog lifecycle/publish regression automation/cross-worker
cache/rollback orchestration đầy đủ; auth identity từng user/tenant; immutable audit
beyond internal MVP boundary; adapter/LoRA và full gates A/B/C/D; campaign 30 mẫu mỗi
template × ít nhất 3 rounds; full production vanguard campaign. Các mục này là
DEFERRED, không PASS, không thuộc tuyên bố “production multiuser ready”.

Vẫn cần test tối thiểu các flow bắt buộc, lỗi và biên của MVP; reviewer độc lập xác
nhận coverage gắn từng MVP ID, không bỏ kiểm bảo mật vì lịch nhanh. Việc không có
template khách hàng không cản code/tests với synthetic fixtures minh bạch; không
được tạo điểm chất lượng live từ mock. Sau review, báo scope đạt, hard checks,
content score/evidence hoặc blocker, known risks và deferred work; không tự merge
main hoặc triển khai public chỉ vì MVP code hoàn tất.
