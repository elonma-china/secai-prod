# Rà plan import file và OCR — 2026-10-03

Phạm vi: rà tài liệu và đối chiếu cấu trúc code đã đọc; không phải review implementation, astra A criteria, benchmark hay xác minh live Vast.

Kết luận: plan đã được hợp nhất theo quyết định cuối: BE-only, base main, OCR trên Vast hiện có, API anonymous. Không còn câu hỏi phạm vi bắt buộc. Sẵn sàng làm preflight và giao astra A; chưa sẵn sàng tuyên bố code/test/deploy đạt.

| ID | Tiêu chí / bằng chứng trước rà | Mức độ đối với plan | Cách xử lý / trạng thái |
|---|---|---|---|
| PLAN-01 | Bảng ownership còn sol-3, criteria FE, snapshot/build/UI test | High | Bỏ task/branch/worktree/criteria/test FE; chỉ giữ hướng dẫn bàn giao — resolved |
| PLAN-02 | Checklist còn auth/owner check và JWT test trái quyết định anonymous | High | Bỏ auth product gate; ghi ID access, anonymous limitations và test leakage/expiry — resolved |
| PLAN-03 | STATE còn chờ người dùng cho phép execution dù đã cho phép | Medium | Ghi authorization đã có, lượt hiện tại chỉ rà/báo; next action preflight→astra A — resolved |
| PLAN-04 | Nhánh OCR cần router/job/types của nhánh import trước integration | High | Chốt owner types/interfaces; staging composition theo hai SHA, không duplicate modules/merge main — resolved |
| PLAN-05 | LLM correction chưa chỉ rõ profile/registry và owner file dùng chung | High | Profile ocr_correction mới tools rỗng do sol-2 sở hữu; entry cũ giữ nguyên — resolved |
| PLAN-06 | Default limiter 15/phút chặn polling; CORS global có thể đổi legacy | High | Bucket v2 riêng IP+route group/shared store, middleware prefix-only, CORS v2 riêng và legacy regression — resolved |
| PLAN-07 | Ví dụ UUID không bảo đảm yêu cầu 128 bit entropy; log URL làm lộ ID | Medium | 16 byte CSPRNG/32 hex; redaction access log, no-store, không list API — resolved |
| PLAN-08 | Cancel terminal/result failure/TTL/idempotency lifetime chưa rõ | Medium | Bổ sung status/error/CAS, cleanup TTL/tombstone; lease fencing chống stale worker — resolved |
| PLAN-09 | Giới hạn file chưa chặn multipart overhead/many-part flood | High | File 10MiB, request 11MiB, 8 part/4KiB text field; test actual streamed limits — resolved |
| PLAN-10 | Raw whitespace policy CER còn để mở; 3 config dễ bị báo thành 3 model | Medium | Khóa NFC/CRLF policy, giữ whitespace; phân biệt model benchmark và remediation configs — resolved |
| PLAN-11 | Model trên Vast bị gọi OCR local; remote OCR bị hiểu là distributed job queue | Medium | Vast rõ ràng; queue theo vị trí job worker, OCR remote không tự ép Redis — resolved |
| PLAN-12 | Masking helper cũ không đảm bảo literal bị cắt/mất/nhân đôi | High | Criterion occurrence integrity, input placeholder, nested masking và literal block dài — resolved |

## Điều kiện cần kiểm tra khi execution

- Fetch/xác minh main rồi khóa SHA; không reset workspace bẩn.
- Xác minh kết nối/quota Vast và tác động workload; chưa kết nối remote trong lượt rà.
- Baseline toàn suite và dependency/tool readiness; chưa chạy trong lượt rà.
- Astra A tạo hai criteria và API contract: các budget/performance/token metrics đề xuất phải freeze trước CODE.
- Model/license/accuracy/VRAM chỉ chốt từ evidence thật; chưa có benchmark.
- API không login là quyết định scope; ai giữ job ID có thể truy cập/hủy, không tuyên bố tenant isolation.

## Kiểm tra tài liệu

Đã đọc lại các mục scope, ownership, contract, implementation, review/test/vanguard/integration/docs và STATE; kiểm tra không còn task FE/auth cũ hoạt động, đủ các branch BE/integration và gate 30×3/OCR≥65%. Kiểm tra script cho heading/code fences/JSON examples/trailing whitespace và các chuỗi mâu thuẫn bị loại. Chỉ xác nhận chất lượng tài liệu; không suy ra test sản phẩm PASS.

## Astra A freeze decisions — 2026-10-03

Criteria/contract files now define CODE targets; they are not implementation PASS. Review references BASE_SHA `ce9cd47ee1273018fec1c5cbab944a86d68d6ed5`. Root approved additive date-context exemption in runner owned by sol-1 and 7GiB OCR operating budget with 2GiB headroom. Root preflight 10,010MiB free cannot safely reserve 8GiB+2GiB. CP1258 uses implementable strict stdlib decode/round-trip plus explicit Vietnamese heuristic warning, without unsupported confidence API assumptions. Standalone same-host worker with isolated memory sessions prevents reusing legacy persisted sessions; no distributed queue. Shared technical token helper comes from early sol-2 checkpoint, acceptance uses composition SHA. Full details and test IDs live in `docs/api/translate-v2-contract.md` and `docs/criteria/`; no source changes made in this freeze.
