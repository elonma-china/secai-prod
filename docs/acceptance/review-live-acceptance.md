# Nghiệm thu kỹ thuật với model thật — 2026-10-04

Nhánh `feat/expert-review-mvp`, worktree `/home/ict/secai-review-mvp-wt`. Kết luận: PASS cho luồng MVP trong phạm vi kiểm tra dưới đây. Chưa nghiệm thu chất lượng nhãn bằng chuyên gia; chưa train hoặc deploy.

## Môi trường và phương pháp

- Gateway thật `secai_gateway.app:app`, LinchRunner thật; không dùng fake runner. Backend riêng `127.0.0.1:8187`, Vite riêng `http://100.74.146.12:5187` trên Tailnet.
- LLM `DuyTa/Cyber-F1-AWQ`; embedding `DuyTa/sec-embedding`; reranker `DuyTa/sec-rerank`. Probe thật: models HTTP 200, embeddings HTTP 200/vector 1024 chiều, rerank HTTP 200/có score. Đây là model ID serving, chưa xác minh hash checkpoint.
- AIRGAP giữ bật, guard hiện có giữ nguyên. Review/session/gateway/catalog DB riêng trong `/tmp/secai-review-live-20261004`. Catalog tạm không có dữ liệu PoC; không dùng lượt này để nghiệm thu RAG/CVE/PoC.
- Sampling = 1, cooldown = 0 chỉ trong phiên nghiệm thu, timeout B = 45s, một worker. Code mặc định vẫn sampling 0.1 và cooldown 600s.
- 10 câu hỏi ngắn: mật khẩu, MFA, phishing, xác thực/phân quyền, đặc quyền tối thiểu, bản vá, backup/ransomware, mã hóa, log đăng nhập, thu hồi quyền. Mỗi câu có session riêng. A gọi API chat hiện tại, B gọi API review mới; so sánh toàn bộ response history trước/sau B.
- Phiếu do script chọn để kiểm chứng lưu/mapping, ghi rõ TECHNICAL SMOKE ONLY. Cả dữ liệu script và thao tác UI đều là QA, không dùng training hoặc tuyên bố chuyên gia đã chấm.

## Kết quả

| Kiểm chứng | Kết quả |
|---|---|
| 10 câu A từ chat thật | 10/10 HTTP 200 |
| Sinh cặp B thật | 10/10 offered, HTTP 200 |
| History trước/sau sinh B | 10/10 không đổi |
| Lưu phiếu có comment kỹ thuật | 10/10 HTTP 200 |
| Duyệt winner, xuất raw/preference | PASS; kiểm tra schema, mapping chosen/rejected và hash manifest |
| Độ trễ B của 10 mẫu | Min 2.645s; median 4.3995s; max 6.077s |
| Sửa template thật | HTTP 200, giữ 3 placeholder; 0.968s |
| Render template thật | HTTP 200, report không rỗng; 1.794s |
| UI chat SSE -> A/B -> nộp -> admin duyệt -> export | PASS trong T3 browser, model thật; thêm 1 câu HTTPS/phân quyền ngoài 10 mẫu |
| UI template -> prompt -> nháp -> áp dụng -> render | PASS; giữ placeholder, bản nháp chỉ thay template sau áp dụng; log tổng hợp 3 API phản ánh đúng trong report mẫu |
| Chạy lại sau restart với bản sửa cleanup cuối | B offered/HTTP 200 (4.48s), history không đổi; revise/render HTTP 200 |
| Regression sau sửa cleanup | 118 passed, 1 warning; 11.66s |

Regression gồm review/store/auth/export/live/template, project/session, prompt/security/SSE. Test cleanup riêng dùng registry/session store của Linch cài thật nhưng không gọi model. Không phát hiện blocker trong review chéo của hai agent Sol medium và kiểm tra tích hợp của phiên chính. Full suite nền chưa pass; xem [báo cáo kiểm chứng chung](review-mvp-validation.md).

## Bằng chứng và tái kiểm tra

Dữ liệu vận hành và token không đưa vào Git. Thư mục QA riêng:

- `/tmp/secai-review-live-20261004/live_acceptance.py`: script 10 mẫu; chạy lại tạo dữ liệu QA mới và gọi model thật.
- `evidence.json`: prompt, nội dung A/B, timing, trạng thái, hash history và export. SHA-256: `36a245e0686a30d38602b9ae21abeed3a9c21633512ee200767cfa16213714d1`.
- `post-cleanup-evidence.json`: lượt xác nhận code cuối sau restart.
- `technical-smoke-preference.zip`, `technical-smoke-raw.zip`: chỉ dữ liệu QA, không dùng huấn luyện.
- `tokens.json`: credential QA, quyền file 0600/thư mục 0700. Đăng nhập tại menu Đánh giá A/B; token chỉ ở memory. Không chia sẻ file này.

Preview đang chạy để giám sát, không phải production. Có thể dùng token reviewer tạo câu chat mới và chọn A/B; dùng admin xem phiếu/duyệt/export. Refresh sẽ cần đăng nhập lại. Không restart/down stack production.

## Giới hạn quyết định ship

PASS này xác nhận chức năng thu thập/duyệt/xuất phản hồi và template, không chứng minh model tốt hơn. Cần chuyên gia chấm thật, hiệu chỉnh rubric và kiểm tra đồng thuận trước khi tạo tập train. Chưa chạy training hoặc so sánh chất lượng trước/sau.

Trainer DPO nhận core schema tương thích; pipeline hiện chưa có ingest review và chưa có bằng chứng Reward Model riêng được train. Xem [kiểm chứng consumer](review-dpo-consumer-validation.md). B được tái sinh từ ngữ cảnh công khai, chưa dùng chung snapshot RAG của A; provenance ghi rõ giới hạn. Người dùng chịu trách nhiệm input template sạch; checkbox không phải detector prompt injection.

Không sửa API cũ, không commit/push/merge/deploy. Bước tiếp theo do người giám sát thực hiện là chấm vài mẫu thật và xác nhận rubric; sau đó mới quyết định tích hợp nhánh và nối đường ingest riêng.
