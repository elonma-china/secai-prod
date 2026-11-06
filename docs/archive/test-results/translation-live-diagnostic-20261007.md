# Đo thực chức năng dịch — 2026-10-07

**Luồng đa ngôn ngữ v3 chưa trả được bản dịch; luồng legacy Việt–Anh trả đủ bốn bản dịch và không phát hiện lỗi nghĩa trọng yếu trong bốn mẫu này.** Không gộp hai luồng thành một tỷ lệ chất lượng.

Người dùng đã yêu cầu chuyển từ offline sang đo thực. Sol chuẩn bị collector; Luna chạy kiểm chứng và request thật; Astra cố định tiêu chí trước inference và chấm kết quả. Không đổi model, cấu hình, giới hạn tài nguyên, dịch vụ hay triển khai bản vá trong lượt đo này. Kết quả phản ánh image đang chạy, không phải bằng chứng các bản vá offline đã được triển khai.

## Kết quả theo luồng

| Luồng | Phạm vi dự kiến | Đã gửi | Kết quả vận hành | Kết quả nghĩa |
|---|---:|---:|---|---|
| `/ai/v3/translate` | 36 mẫu, 18 hướng, 2 nhóm nguồn | 16 | Cả 16 trả HTTP 503 `MODEL_UNAVAILABLE`; dừng do lỗi lặp, giữ 20 `NOT_RUN` | **NOT_EVALUABLE**: không có bản dịch, không phải “độ chính xác 0%” |
| `/ai/translate` legacy | 4 mẫu, vi↔en, cùng 2 nhóm nguồn | 4 | 4 HTTP 200, 4 bản dịch, không retry | **4 PASS_OBSERVED** theo rubric Astra; không suy thành chất lượng tổng thể 100% |

V3 đã gửi một mẫu ở 16 hướng đầu; `ru>vi` và `ar>vi` chưa gửi. Mỗi hướng vẫn có mẫu số dự kiến 2. Không loại lỗi hoặc mẫu chưa chạy để tăng tỷ lệ. Dừng collector bằng SIGINT chỉ tác động tiến trình của lượt đo: metadata giữ `stop_reason=interrupted`, ghi chú vận hành riêng ghi nguyên nhân `persistent_MODEL_UNAVAILABLE`.

Legacy có 2/2 mẫu vi→en và 2/2 mẫu en→vi giữ các mệnh đề chính. Thời gian đáp ứng bốn request: thấp nhất **0,938 s**, trung vị **0,996 s**, cao nhất **1,282 s**. Snapshot GPU trước lượt này cho thấy A100 80 GB đang sử dụng 76.666 MiB và mức tải 100%; bốn phép đo không chứng minh p95/SLA. Thời gian các response lỗi v3 là 1,047–6,029 s, không phải latency dịch thành công.

## Nội dung đã chấm

- Nhóm `0017`: bằng chứng từ tài liệu; **14 ngân hàng**, khách hàng giàu có, tài sản trị giá **hàng tỷ USD**, mục đích tránh thuế và các quy định khác.
- Nhóm `0649`: phủ định **“không bao giờ”** phủ đủ bốn hoạt động; sửa xe, xây đài phun nước ở sân sau, đi Peru tìm hiểu di tích cổ, sửa nhà hàng xóm.

Astra không phát hiện bỏ/thêm ý trọng yếu, sai ngôn ngữ, số lượng, tiền tệ, địa danh hay phạm vi phủ định trong bốn output legacy. Có ghi chú từ vựng ở en→vi: `overhaul` thành “sửa chữa” giảm độ chuyên biệt; `examine ancient ruins` thành “khảo cổ các di tích cổ đại” chuyên biệt hơn nguồn. Cách trung tính hơn cho cụm sau là “tìm hiểu/khảo sát các di tích cổ đại”. Đây là review có hỗ trợ AI theo mệnh đề đã cố định, không phải chứng nhận bản ngữ hoặc đánh giá hoàn hảo về từ vựng.

## Model và tình trạng triển khai

- Gateway chính được gọi qua cổng local 8131; v3 chuyển tiếp qua dịch vụ dịch riêng. Health và capability trả 200, đủ 18 hướng bật, nhưng các POST thực đều 503. Health/capability không chứng minh provider dịch sẵn sàng.
- Cấu hình v3 chỉ định `tencent/Hy-MT2-7B`; các GET `/health` và `/v1/models` đã thử trên specialist trả 404. Tokenizer/config được mount khớp hash pin đã khai báo. Chưa có response model hợp lệ để xác nhận checkpoint đang phục vụ. Adapter gom nhiều lỗi upstream/auth/schema/model thành `MODEL_UNAVAILABLE`, nên mã này đơn lẻ không xác định nguyên nhân.
- `/v1/models` của endpoint LLM legacy đang được gateway sử dụng trả 200 và liệt kê `DuyTa/Cyber-F1-AWQ`. Mỗi response dịch chỉ có profile `translate`, không công bố model/checkpoint. Phân biệt evidence từ inventory/cấu hình với attestation từng response.
- Image gateway chính: `sha256:062b3ecabdff6408b612715c10c9894c8fece97d7bd56daa39f5e63017480a41`; image gateway dịch: `sha256:3e1d38125557f4b8894b231bdab4f80e184b1fdeb81576e7132a9eafd9744970`.

### Probe thành phần để phân biệt lỗi gateway/upstream

Sau hai lượt đo, Luna gửi đúng **một** request chẩn đoán trực tiếp đến specialist được cấu hình, dùng payload của adapter và thông tin xác thực hiện hành, không đổi budget hay guard. Câu nguồn công khai đầu tiên có 59 token, prompt sau template 84 token, nằm trong giới hạn chunk/prompt/context đã kiểm. Kết quả: **HTTP 503**, body chính xác `{"error":"POISONED"}`, khoảng 0,56 giây, không có model/output trả về. Đây là bằng chứng trạng thái lỗi của specialist, không phải lỗi nghĩa bản dịch; chưa xác định sự kiện ban đầu khiến service vào trạng thái này.

Probe có mẫu số riêng, không đưa vào 36 mẫu v3 hoặc bốn mẫu legacy. Tổng lượt POST thực tế của tác vụ: **16 v3 + 4 legacy + 1 probe = 21**; không retry ở collector. Không reset cờ lỗi, bỏ lock, restart hoặc thay model để làm phép đo pass. Body/request được lưu private, không lưu secret/header. SHA-256 body trả về: `40df9e8aab19b85ba087adc3832d931d6c0167195d2b0a0d6084a1689c39e1e9`.

Mã nguồn checkout tại `deploy/gpu_stage/coordinator.py`, `owner.py` và `mt_on_demand.py` hỗ trợ cách hiểu đây là trạng thái chặn admission khi queue bị poisoned hoặc chưa chứng minh cleanup an toàn. Chưa đối chiếu byte/version serving đang chạy, nên chưa xác định nhánh cụ thể hay sự kiện gây lỗi. Hướng dẫn `deploy/gpu_stage/MT_ON_DEMAND.md` yêu cầu chứng minh child đã dừng và đủ GPU reserve dưới shared lock trước khi xử lý evidence; không suy rằng restart/reset đơn thuần là cách khôi phục an toàn.

## Phương pháp và evidence

Nguồn là FLORES-101 **DEV công khai**, hai aligned item `0017` và `0649`; source/reference/nhóm tài liệu giữ nguyên, license khai báo CC-BY-SA-4.0, revision archive `49fa80207b09fcc0eca8253ed13303b3a0ae0f16081af862601c73ac76f2cba6`. Đây là dữ liệu DEV đã lộ, không phải holdout kín hoặc bằng chứng không trùng dữ liệu huấn luyện.

Collector chạy tuần tự, giãn tối thiểu 6,5 giây giữa request, không retry ở client và dừng gửi mới sau timeout/lỗi transport. V3 giới hạn tối đa 36 HTTP, 30 giây/request và 600 giây collector; legacy riêng tối đa 4 HTTP, 30 giây/request và 150 giây collector. Gateway có thể chia chunk/retry nội bộ; số HTTP không đồng nghĩa số lượt GPU inference và timeout client không chứng minh remote đã dừng. Raw responses được giữ nguyên; không giả số lần gọi model hay chi phí khi không có telemetry.

| Artifact | SHA-256 |
|---|---|
| Manifest nguồn | `ad1e0fd7f8068da49bee2255e64a8019ec5a2a840b0347e9403153c612a0f64e` |
| Manifest v3 36 mẫu | `09dbb2a9b55f183f555fa9637877e51880f71f7dccbb4311113a6491cc685538` |
| Raw v3 | `72693b2f1f48f9399126046f6904521ff9deaf0de9540d254456893fe6c42253` |
| Manifest legacy 4 mẫu | `e13f794b7e997872fa60bb81a076509b0bf94c47137f96e72d7d54e70769b0cd` |
| Raw legacy | `65e2bdd476240568183fc3fb4b2c6b61cd985af2ad13e6bc2a885baf806163a5` |

Evidence gốc của lượt này ở `/tmp/secai-live-quality-20261007/`: manifest, collector và hash, tiêu chí đóng băng, preflight, raw JSONL, metadata, log, ghi chú dừng và review từng dòng. Bản lưu lâu hơn đặt trong `data/evals/live-quality-20261007/` (ngoài Git). Không sửa output/reference sau khi chấm.

## Giới hạn và ưu tiên tiếp theo

Ưu tiên xác minh và khôi phục specialist đang trả `POISONED` trước khi đo chất lượng đủ 18 hướng, giữ nguyên cơ chế lock/cleanup tài nguyên. Bốn output legacy chỉ bao phủ văn bản phổ thông, không đo file/ảnh/OCR, CVE/IP/URL/code/version, chất lượng các ngôn ngữ còn lại hay A/B giữa model. Không dùng kết quả này để suy mức cải thiện trước/sau từ các bản vá offline.

Đánh giá đầy đủ vẫn cần corpus phù hợp, ít nhất 30 nhóm nguồn mỗi hướng × 18 hướng × 3 lượt, chấm nghĩa/holdout và evidence runtime theo tiêu chí hiện có. Phép chẩn đoán này không thay đổi hoặc vượt qua các gate nghiệm thu đó.
