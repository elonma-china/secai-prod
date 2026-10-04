# API dịch văn bản đa ngôn ngữ v3

Tài liệu này mô tả implementation tại core commit `f6b7834` và contract v1. Hai endpoint mới chỉ có trong ứng dụng ASGI `secai_gateway.multilang_app:app` khi `MULTILANG_ENABLED=true`. Mặc định cờ này **tắt**: ứng dụng trả nguyên legacy app và các đường dẫn v3 trả 404. Việc có route hoặc bật cờ không chứng nhận chất lượng dịch; chưa có kết quả suy luận live được trình bày ở đây. Các ví dụ dưới đây chỉ minh họa wire format.

API mới chỉ dịch **text JSON, không stream**, không nhận file/OCR, không tạo job, history hoặc session. Cặp hợp lệ là `vi` hai chiều với `zh-Hans`, `ja`, `ko`, `fr`, `de`, `es`, `ru`, `ar` (16 hướng). `zh` là alias của `zh-Hans`; Arabic dùng Modern Standard Arabic. `en↔vi` tiếp tục dùng `/ai/translate` và `/ai/translate/stream` của legacy. File/OCR cũng giữ API cũ; không tự có thêm ngôn ngữ từ v3.

## Khả năng và request

`GET /ai/v3/translate/languages` trả 200 JSON gồm `languages` (9 mục canonical), `pairs` (16 hướng) và `limits`. Mỗi language có `code`, `name`, `native_name`, `aliases`, `script`, `direction`; Arabic có `direction: "rtl"`. Mỗi pair có `source_lang`, `target_lang`, `enabled`, `status`. Pair tắt trả `enabled:false,status:"disabled"`; implementation hiện tại trả `stable` khi cờ pair bật. Giá trị `stable` ở đây chỉ phản ánh cờ trong code, **không** thay cho nghiệm thu chất lượng hoặc quyết định phát hành. Client đọc capability từ server, không tự suy cờ từ env.

`POST /ai/v3/translate` yêu cầu `Content-Type: application/json` và đúng ba field chuỗi `text`, `source_lang`, `target_lang`. Không có `model`, `adapter`, `user_id`, cờ hay URL trong request; field thừa bị từ chối. Mã ngôn ngữ được trim ASCII whitespace và canonicalize không phân biệt hoa/thường (`zh`/`zh-hans` thành `zh-Hans`). Không có `auto`, `en`, `zh-Hant`, region alias như `ar-SA`, cùng nguồn/đích hoặc foreign→foreign. Body rỗng, chỉ khoảng trắng, chỉ ký hiệu/protected literal hoặc sai kiểu đều bị từ chối.

Ví dụ gọi từ một gateway đã được phép bật cặp tương ứng (đặt `BASE_URL` bằng origin gateway của môi trường đang thử):

```sh
curl -sS "$BASE_URL/ai/v3/translate/languages"
curl -sS "$BASE_URL/ai/v3/translate" \
  -H 'Content-Type: application/json' \
  -d '{"text":"Kiểm tra CVE-2024-1234 và 192.0.2.1.","source_lang":"vi","target_lang":"zh-Hans"}'
```

Ba cặp request/response **minh họa, chưa được xác nhận bằng model live**:

| Request `text` | `source_lang` → `target_lang` | `translated_text` minh họa |
| --- | --- | --- |
| `Kiểm tra CVE-2024-1234 và 192.0.2.1.` | `vi` → `zh-Hans` | `检查 CVE-2024-1234 和 192.0.2.1。` |
| `この報告書で CVE-2024-1234 と 192.0.2.1 を確認してください。` | `ja` → `vi` | `Hãy kiểm tra CVE-2024-1234 và 192.0.2.1 trong báo cáo này.` |
| `Kiểm tra CVE-2024-1234 và 192.0.2.1.` | `vi` → `ar` | `تحقق من CVE-2024-1234 و 192.0.2.1.` |

Response thành công là một JSON atomic, không có bản dịch từng phần hoặc SSE:

```json
{
  "translated_text": "检查 CVE-2024-1234 和 192.0.2.1。",
  "source_lang": "vi",
  "target_lang": "zh-Hans",
  "warnings": ["SOURCE_LANGUAGE_UNCERTAIN"],
  "metadata": {"request_id": "illustrative-request-id", "chunks": 1, "attempts": 1}
}
```

`warnings` chỉ có thể chứa `SOURCE_LANGUAGE_UNCERTAIN` và `TARGET_LANGUAGE_UNCERTAIN`; mảng rỗng cũng hợp lệ. Cảnh báo cho biết bộ nhận diện chưa đủ chứng cứ, không có nghĩa bản dịch đã đạt chất lượng. `metadata.chunks` là số chunk; `attempts` gồm cả retry. Tối đa một retry cho lỗi kiểm tra output trên mỗi chunk; lỗi chunk nào cũng làm toàn request thất bại. Literal kỹ thuật như CVE/IP phải giữ chính xác; client hiển thị kết quả bằng text node, và khi copy dùng nguyên `translated_text`, đặc biệt với Arabic RTL và chuỗi Latin.

## Lỗi, giới hạn và response headers

Lỗi nghiệp vụ có dạng `{"error":{"code":"LANGUAGE_DISABLED","message":"Ngôn ngữ này chưa được bật.","request_id":"illustrative-request-id"}}`. `message` dành cho người đọc; client phân nhánh theo `code`. Master OFF có thể trả 404 theo legacy, không theo envelope này. Route/method sai có thể dùng 404/405 của framework.

| HTTP | `error.code` | Ý nghĩa |
| --- | --- | --- |
| 400 | `INVALID_REQUEST` | JSON/Content-Type/schema sai, field thừa, text không có prose. |
| 400 | `UNSUPPORTED_LANGUAGE` | Mã không thuộc registry hoặc alias. |
| 400 | `UNSUPPORTED_PAIR` | Cùng ngôn ngữ hoặc không có `vi` ở một đầu. |
| 403 | `LANGUAGE_DISABLED` | Cờ cho ngôn ngữ đích/nguồn mới chưa bật. |
| 413 | `TEXT_TOO_LONG`, `TOKEN_LIMIT_EXCEEDED` | Vượt byte/ký tự/chunk hoặc ngân sách token/context. |
| 422 | `SOURCE_LANGUAGE_MISMATCH`, `UNSAFE_UNICODE` | Nguồn sai có chứng cứ chắc, hoặc chứa Unicode control nguy hiểm. |
| 429 | `RATE_LIMITED` | Vượt quota hoặc sức chứa; kèm `Retry-After` giây. |
| 502 | `EMPTY_TRANSLATION`, `OUTPUT_VALIDATION_FAILED` | Provider trả rỗng hoặc output không qua kiểm tra sau retry cho phép. |
| 503 | `MODEL_UNAVAILABLE` | Model/config/tokenizer/transport chưa sẵn sàng hoặc model ID sai. |
| 504 | `TRANSLATION_TIMEOUT` | Hết deadline toàn request. |

Mọi JSON response v3 có `Cache-Control: no-store`, `X-Content-Type-Options: nosniff`, và `X-Request-ID`; ID khớp `metadata.request_id` hoặc `error.request_id` nếu có. CORS dùng danh sách origin từ cấu hình gateway hiện hữu, không wildcard; preflight cho GET/POST và `Content-Type` được xử lý trước quota POST. Không tự retry POST sau cancel, 502 hoặc 504: gửi lại có thể gọi model lại.

| Giới hạn mặc định | Giá trị |
| --- | ---: |
| Text (`MULTILANG_MAX_TEXT_CHARS`) | 24.000 code points |
| JSON body (`MULTILANG_MAX_BODY_BYTES`) | 262.144 bytes |
| Source (`MULTILANG_MAX_SOURCE_TOKENS`) | 8.192 tokens |
| Chunk (`MULTILANG_CHUNK_INPUT_TOKENS` / `MULTILANG_MAX_CHUNKS`) | 2.048 tokens / 16 chunks |
| Output reserve (`MULTILANG_MAX_OUTPUT_TOKENS`) | 4.096 tokens mỗi model call |
| Whole request (`MULTILANG_REQUEST_TIMEOUT_SECONDS`) | 120 giây |
| Admission / model calls (`MULTILANG_MAX_ACTIVE_REQUESTS` / `MULTILANG_MAX_CONCURRENT_MODEL_CALLS`) | 8 / 2 mỗi process |
| POST quota (`MULTILANG_RATE_LIMIT_PER_MINUTE`) | 10 mỗi peer IP / 60 giây |

Capabilities công bố `max_text_chars`, `max_source_tokens`, `max_chunks`, `request_timeout_seconds`; các budget khác là cấu hình server. GET capabilities có quota riêng ít nhất 60/phút. Giới hạn process-local không phải quota toàn cluster khi chạy nhiều worker. Proxy phải giữ peer IP đáng tin; client không được tự khai `X-Forwarded-For` để đổi quota.

## Identity, cấu hình và rollback

V3 là **anonymous, stateless** theo convention ingress hiện hữu: không cần token/cookie/`user_id`, không lưu source, output hay draft vào session/ledger. Không có role hay object thuộc người dùng trong v3. Kiểm tra token expired/invalid, role escalation và IDOR ở chính API này là **N/A**, không phải PASS. Ownership của project/session legacy vẫn phải được kiểm riêng. Request ID chỉ để đối chiếu log, không là quyền truy cập nội dung.

Triển khai thử nghiệm dùng ASGI target `secai_gateway.multilang_app:app`. `MULTILANG_ENABLED=false` là mặc định và trả nguyên legacy app; tám cờ `MULTILANG_ENABLE_ZH`, `JA`, `KO`, `FR`, `DE`, `ES`, `RU`, `AR` cũng mặc định false, mỗi cờ mở cả hai chiều với `vi`. Không bật cờ ngôn ngữ cho môi trường thật trước khi cả hai chiều qua quality, security và integration gates và có quyết định release của người dùng. Không cần migration dữ liệu cho rollback.

V3 dùng duy nhất incumbent `DuyTa/Cyber-F1-AWQ` qua cấu hình gateway hiện hữu `LLM_PROVIDER=vllm`, `QWEN_MODEL=DuyTa/Cyber-F1-AWQ`, `QWEN_BASE_URL` và credential server-side `QWEN_API_KEY`. Request không chọn model/endpoint. Model ID response phải khớp; chỉ ID không chứng minh checkpoint revision đang phục vụ. Trước khi bật cờ, operator phải xác minh checkpoint/serving revision, tokenizer cùng revision, chat template, context window và output reserve. Đặt `MULTILANG_TOKENIZER_JSON` tới **file local** `tokenizer.json`, `MULTILANG_TOKENIZER_SHA256` là SHA-256 của chính file đó, `MULTILANG_CONTEXT_TOKENS` là context serving đã xác minh, và `MULTILANG_CHAT_TEMPLATE_OVERHEAD_TOKENS` là upper bound đã chứng thực cho role/special tokens. Không để các giá trị này bằng 0 hoặc checksum sai: POST của cặp đã bật trả 503 `MODEL_UNAVAILABLE` trước inference. Tokenizer có truncation serialized cũng bị từ chối. App không tải tokenizer/model từ mạng khi khởi động chỉ để phục vụ flags OFF.

Rollback: dừng nhận request mới, để request đang chạy hoàn tất hoặc hủy theo quy trình vận hành, đặt `MULTILANG_ENABLED=false` rồi restart process dùng target mới; nếu cần có thể trở về `secai_gateway.app:app`. Để tắt riêng một ngôn ngữ, đặt `MULTILANG_ENABLE_<LANG>=false` và restart. Sau restart, xác nhận v3 trả 404 khi master OFF, hoặc pair trả `enabled:false,status:"disabled"` và POST 403 khi cờ riêng OFF; xác nhận legacy en↔vi JSON/SSE và file/OCR vẫn hoạt động. Cờ master được chọn khi app khởi tạo, nên chỉ sửa env mà không restart không rollback route đang chạy.

Giới hạn chứng cứ hiện có: test mock/fake-provider xác minh contract và bảo toàn literal, nhưng chưa chứng minh chất lượng dịch live, độ trễ/chi phí production, bản checkpoint thực tế hay quota nhiều worker. Bản dịch có `*_UNCERTAIN` cần đánh giá con người; kiểm tra literal nghiêm ngặt có thể từ chối cách đảo thứ tự hợp lệ về mặt ngôn ngữ. Không suy các gates này từ `/health` hoặc capability `stable`.
