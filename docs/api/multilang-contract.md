# Multilang text API — contract v1 + English extension

Ngày 2026-10-03. **CRITERIA_READY** cho implementation với flags OFF và test mock; không chứng nhận chất lượng hay cho phép rollout. Contract này thay các quyết định còn mở trong planning proposal. [Core criteria](../criteria/multilang-core.md), [assessment](../criteria/multilang-assessment.md) và [integration/security](../criteria/multilang-integration-security.md) là gates riêng. User đã cho triển khai code trong khi ASSESS tiếp tục.

## Scope và identity

- Có `GET /ai/v3/translate/languages`, `POST /ai/v3/translate` và (bổ sung 05/10/2026) `POST /ai/v3/translate/stream`. Không thêm file, OCR, job, history hoặc session.
- Cập nhật 05/10/2026 theo yêu cầu người dùng: 18 hướng, gồm 16 hướng ban đầu và Anh–Việt: `vi ↔ zh-Hans/ja/ko/fr/de/es/ru/ar/en`. Anh–Việt bật riêng bằng `MULTILANG_ENABLE_EN=true` (mặc định false). `zh` alias cho `zh-Hans`; Arabic là MSA. Không `auto`, `zh-Hant`, cùng nguồn/đích hay foreign→foreign ở API mới. Legacy en↔vi JSON/SSE và file/OCR giữ contract cũ.
- Anonymous stateless, cùng convention ingress hiện hữu. Không cần token, cookie, `user_id`; không gán ownership từ header do client tự khai. Token invalid/expired, role escalation và object-owner/IDOR ở API mới là **N/A vì không có identity/object store**, không phải PASS. CORS, flags, limits, data isolation và legacy ownership tests vẫn bắt buộc.
- Không persist source/output, prompt, glossary hay retry drafts vào session/ledger. Request ID chỉ correlation ngẫu nhiên, không phải capability truy xuất nội dung.
- `MULTILANG_ENABLED=false` mặc định: composition trả nguyên legacy app; hai routes mới trả 404 theo legacy. Khi master ON, 8 flags riêng `MULTILANG_ENABLE_{ZH,JA,KO,FR,DE,ES,RU,AR}` mặc định false. Một flag điều khiển cả hai chiều. Request không được bật flag/model/adapter.
- Capability/status đọc từ server settings snapshot; không làm suy luận về chất lượng chỉ từ việc dev bật flag. Production `stable` chỉ sau release gates; `beta` chỉ khi user quyết định rõ sau Gate D. Không thêm field beta request hoặc tự bật beta.

## Schemas

JSON schema dưới đây là hợp đồng wire; tất cả object đóng (`additionalProperties=false`), field trong `required` luôn hiện diện, không null. Enum code là case-sensitive sau canonicalization ngôn ngữ: trim ASCII whitespace, case-insensitive `vi`, `ja`, ...; `zh`/`zh-hans` thành `zh-Hans`. Không suy alias region như `ar-SA`/`fr-FR`. Registry luôn trả canonical form.

```json
{
  "$defs": {
    "LanguageCode": {"type":"string","enum":["vi","zh-Hans","ja","ko","fr","de","es","ru","ar","en"]},
    "Status": {"type":"string","enum":["disabled","beta","stable"]},
    "Language": {
      "type":"object","additionalProperties":false,
      "required":["code","name","native_name","aliases","script","direction"],
      "properties":{
        "code":{"$ref":"#/$defs/LanguageCode"},"name":{"type":"string"},"native_name":{"type":"string"},
        "aliases":{"type":"array","items":{"type":"string"}},
        "script":{"type":"string","enum":["Latn","Hans","Jpan","Kore","Cyrl","Arab"]},
        "direction":{"type":"string","enum":["ltr","rtl"]}
      }
    },
    "Pair": {
      "type":"object","additionalProperties":false,
      "required":["source_lang","target_lang","enabled","status"],
      "properties":{
        "source_lang":{"$ref":"#/$defs/LanguageCode"},"target_lang":{"$ref":"#/$defs/LanguageCode"},
        "enabled":{"type":"boolean"},"status":{"$ref":"#/$defs/Status"}
      }
    },
    "Capabilities": {
      "type":"object","additionalProperties":false,"required":["languages","pairs","limits"],
      "properties":{
        "languages":{"type":"array","minItems":10,"maxItems":10,"items":{"$ref":"#/$defs/Language"}},
        "pairs":{"type":"array","minItems":18,"maxItems":18,"items":{"$ref":"#/$defs/Pair"}},
        "limits":{
          "type":"object","additionalProperties":false,
          "required":["max_text_chars","max_source_tokens","max_chunks","request_timeout_seconds"],
          "properties":{
            "max_text_chars":{"type":"integer","minimum":1},"max_source_tokens":{"type":"integer","minimum":1},
            "max_chunks":{"type":"integer","minimum":1},"request_timeout_seconds":{"type":"integer","minimum":1}
          }
        }
      }
    },
    "TranslationRequest": {
      "type":"object","additionalProperties":false,"required":["text","source_lang","target_lang"],
      "properties":{
        "text":{"type":"string","minLength":1},
        "source_lang":{"type":"string","minLength":1,"maxLength":16},
        "target_lang":{"type":"string","minLength":1,"maxLength":16}
      }
    },
    "TranslationResponse": {
      "type":"object","additionalProperties":false,
      "required":["translated_text","source_lang","target_lang","warnings","metadata"],
      "properties":{
        "translated_text":{"type":"string","minLength":1},
        "source_lang":{"$ref":"#/$defs/LanguageCode"},"target_lang":{"$ref":"#/$defs/LanguageCode"},
        "warnings":{"type":"array","uniqueItems":true,"items":{"type":"string","enum":["SOURCE_LANGUAGE_UNCERTAIN","TARGET_LANGUAGE_UNCERTAIN"]}},
        "metadata":{
          "type":"object","additionalProperties":false,"required":["request_id","chunks","attempts"],
          "properties":{
            "request_id":{"type":"string","minLength":1,"maxLength":128},
            "chunks":{"type":"integer","minimum":1},"attempts":{"type":"integer","minimum":1}
          }
        }
      }
    },
    "ErrorResponse": {
      "type":"object","additionalProperties":false,"required":["error"],
      "properties":{"error":{
        "type":"object","additionalProperties":false,"required":["code","message","request_id"],
        "properties":{
          "code":{"type":"string","enum":["INVALID_REQUEST","UNSUPPORTED_LANGUAGE","UNSUPPORTED_PAIR","LANGUAGE_DISABLED","TEXT_TOO_LONG","TOKEN_LIMIT_EXCEEDED","SOURCE_LANGUAGE_MISMATCH","UNSAFE_UNICODE","RATE_LIMITED","EMPTY_TRANSLATION","OUTPUT_VALIDATION_FAILED","MODEL_UNAVAILABLE","TRANSLATION_TIMEOUT"]},
          "message":{"type":"string","minLength":1},"request_id":{"type":"string","minLength":1,"maxLength":128}
        }
      }}
    }
  }
}
```

`GET` trả `Capabilities` (200). `POST` nhận `TranslationRequest`, trả `TranslationResponse` (200) hoặc `ErrorResponse`. Message là văn bản thân thiện, không phải khóa máy đọc; FE dùng `error.code`. Các lỗi route không tồn tại/method không hỗ trợ có thể giữ 404/405 framework; master OFF không bắt buộc envelope mới. Không 401 mới. Không Pydantic error body có input gốc.

Registry order cố định: vi, zh-Hans, ja, ko, fr, de, es, ru, ar, en. Mỗi language một entry duy nhất; aliases `zh-Hans:["zh"]`, entry khác `[]`. `name`: Tiếng Việt/Trung giản thể/Nhật/Hàn/Pháp/Đức/Tây Ban Nha/Nga/Ả Rập; `native_name`: Tiếng Việt/简体中文/日本語/한국어/Français/Deutsch/Español/Русский/العربية. Script tương ứng Latn/Hans/Jpan/Kore/Latn/Latn/Latn/Cyrl/Arab; chỉ ar RTL. `pairs` có hai chiều liên tiếp theo thứ tự ngôn ngữ mới, vi→language rồi language→vi. Disabled luôn `enabled=false,status=disabled`; enabled có status stable hoặc beta và hai chiều giống nhau. FE không đọc env trực tiếp.

Ví dụ success minh họa schema, **không phải evidence model live**:

```json
{"translated_text":"检查 CVE-2024-1234 和 192.0.2.1。","source_lang":"vi","target_lang":"zh-Hans","warnings":[],"metadata":{"request_id":"example-1","chunks":1,"attempts":1}}
```

```json
{"error":{"code":"LANGUAGE_DISABLED","message":"Ngôn ngữ này chưa được bật.","request_id":"example-2"}}
```

## Validation, errors và atomicity

| HTTP | code | Điều kiện |
|---|---|---|
| 400 | INVALID_REQUEST | JSON lỗi, sai Content-Type, thiếu/sai type/extra field, whitespace-only, chỉ symbols hoặc chỉ protected literals không có prose để dịch |
| 400 | UNSUPPORTED_LANGUAGE | Mã không thuộc registry/alias, bao gồm auto/zh-Hant |
| 400 | UNSUPPORTED_PAIR | Cùng ngôn ngữ hoặc vi không ở một đầu |
| 403 | LANGUAGE_DISABLED | Một trong hai chiều của ngôn ngữ chưa bật; trước mọi inference |
| 413 | TEXT_TOO_LONG | Vượt byte body cap, 24.000 code points mặc định hoặc quá số chunk |
| 413 | TOKEN_LIMIT_EXCEEDED | Source/request, chunk, system+prompt+output reserve vượt budget/context; literal quá lớn để giữ nguyên một chunk |
| 422 | SOURCE_LANGUAGE_MISMATCH | Detector có bằng chứng chắc nguồn khai sai; không suy từ Han/ít ký tự/mixed-script đơn thuần |
| 422 | UNSAFE_UNICODE | NUL, unpaired surrogate hoặc bidi embedding/override/isolate control nguy hiểm trong input |
| 429 | RATE_LIMITED | Per-IP quota hoặc global admission capacity hết; có Retry-After integer seconds >=1 |
| 502 | EMPTY_TRANSLATION | Provider trả rỗng sau retry được phép |
| 502 | OUTPUT_VALIDATION_FAILED | Literal thiếu/đổi/thừa/reorder, off-target chắc chắn, truncate, tool call, prompt leak hoặc đầu ra invalid khác sau retry |
| 503 | MODEL_UNAVAILABLE | Transport/endpoint lỗi, model identity/config không khớp, provider không sẵn sàng |
| 504 | TRANSLATION_TIMEOUT | Deadline toàn request hết (kể cả admission/chunk/retry) |

Trước inference: size/schema → canonical languages/pair → server flag snapshot → Unicode/source/budget guards. Rate/body admission có thể chặn trước schema. Không đảm bảo ưu tiên giữa nhiều lỗi độc lập; test một lỗi mỗi case. Mọi lỗi input/disabled/quota có zero model calls. Bidi policy từ chối U+202A–U+202E và U+2066–U+2069 do client nhập; ZWJ/ZWNJ hợp lệ và chữ Arabic không bị xóa. Không strip bừa LRM/RLM, NFC chỉ prose, protected spans giữ nguyên.

Input explicit là nguồn chuẩn khi detector unknown: tiếp tục + SOURCE_LANGUAGE_UNCERTAIN. Với target rất ngắn/mixed/protected-heavy không đủ bằng chứng, không giả khẳng định sai: TARGET_LANGUAGE_UNCERTAIN; đánh giá offline phải adjudicate, không tự tính pass. Rules-only không được tuyên bố phân biệt chắc fr/de/es/vi hoặc zh-Hans/MSA. Một mẫu copy toàn prose rõ ràng chưa dịch, hay script chắc sai vẫn fail; warning không thay hard validator.

Trả plain text Unicode trong JSON. Không dùng sanitizer VI/EN cũ hoặc raw HTML render. Raw HTML-like source vẫn là dữ liệu dịch, không được execute/fetch; frontend dùng text node. Đường JSON không có partial success: chunk N fail hủy request, không trả draft/các chunk trước. Stream thử nghiệm chỉ phát từng chunk đã kiểm tra; chunk sau lỗi phát error và không có final thành công, client phải coi bản tạm là chưa hoàn tất. Chunk order và separator deterministic; `metadata.chunks` là số chunk của bản dịch, `attempts` là tổng lần provider generate, bao gồm retries; `chunks <= attempts <= 2*chunks`. Nội dung chưa validate không thoát qua stream/log/history.

Mọi response mới (kể cả errors/capabilities) có `Cache-Control: no-store`, `X-Content-Type-Options: nosniff`, `X-Request-ID` khớp ID body nếu có; Content-Type JSON. Không echo request ID tùy ý chưa validate. Cancel/client disconnect hủy queued/in-flight awaits và HTTP calls, không tiếp tục retry. POST không có idempotency persistence: gửi lại có thể inference lại. FE không tự retry 502/504 hay tự submit lại sau cancel.

## Operational defaults (guardrails, không phải quality/performance claims)

Config riêng của multilang, không thay legacy Settings/defaults. Dùng các default đã đề xuất trong plan:

| Setting suffix (prefix MULTILANG_) | Default |
|---|---:|
| MAX_TEXT_CHARS | 24000 |
| MAX_BODY_BYTES | 262144 |
| MAX_SOURCE_TOKENS | 8192 |
| MAX_CHUNKS | 16 |
| CHUNK_INPUT_TOKENS | 2048 |
| MAX_OUTPUT_TOKENS | 4096 |
| MAX_RETRIES | 1 |
| REQUEST_TIMEOUT_SECONDS | 120 |
| MAX_CONCURRENT_MODEL_CALLS | 2 |
| MAX_ACTIVE_REQUESTS | 8 |
| RATE_LIMIT_PER_MINUTE | 10 |

MAX_BODY_BYTES đủ JSON escaping cho text cap; enforce cả streaming body, không chỉ Content-Length. Token budget tính cả prompt/system/output reserve với context incumbent đã xác minh; không cắt im lặng. Tokenizer đúng revision là chuẩn; conservative upper bound chỉ chấp nhận nếu có chứng cứ upper-bound cho tokenizer thực tế, và báo đó là estimate, không phải actual usage. Không tải model/tokenizer mới khi process startup chỉ để flags OFF. Chưa có tokenizer/bound đáng tin: không cho inference thiếu guard.

Semaphore chia sẻ toàn process, không phải từng request; mỗi admitted request tối đa 16 chunks và 32 provider attempts. MAX_ACTIVE_REQUESTS là admission hữu hạn bao gồm request đang chờ semaphore; full trả429. Fixed window 60s, POST quota keyed theo peer IP đáng tin; không tin X-Forwarded-For tùy ý. GET capabilities có quota riêng tối thiểu60/minute để không tiêu quota dịch. Worker replicas phải có ingress/global budget khi release; process-local limits không được claim cluster-global.

Retry chỉ một lần/chunk cho output validation; transport/timeout không hidden retry. `stop_reason=length`, tool calls hoặc malformed provider response không coi thành công. Retry dùng cùng incumbent và cùng deadline; SDK retry mặc định phải tắt hoặc tính trong budget. Không gọi URL/model/adapter nhận từ request.

## Core/provider interface và composition

Interface Python semantic contract; implementer có thể dùng dataclass/Pydantic tương đương, không thay wire contract:

```python
class LlmPort(Protocol):
    async def generate(self, request: GenerationRequest) -> GenerationResult: ...

# GenerationRequest: system:str, user:str, max_output_tokens:int,
# temperature:float, top_p:float. Deadline/cancellation được service bọc.
# GenerationResult: text:str, stop_reason:str, model:str,
# model_revision:str|None, input_tokens:int|None, output_tokens:int|None.
# TranslationRequest: text:str, source_lang:str, target_lang:str.
# TranslationResult: translated_text:str, source_lang:str, target_lang:str,
# warnings:tuple[str,...], chunks:int, attempts:int.
async def translate(request, *, llm: LlmPort, settings) -> TranslationResult: ...
```

Provider wrapper mới dùng endpoint/model DuyTa incumbent đã xác minh; không gọi legacy `complete`/runner output path có VI/EN sanitizer, không mutate PROFILES/provider/shared settings. Một isolated OpenAI-compatible async HTTP client là lựa chọn hợp lệ khi adapter Linch không cung cấp raw no-tool completion sạch. Vẫn cùng endpoint/checkpoint, adapter compatibility nằm một module; đây không phải dịch vụ/model mới. Không kế thừa nhánh fallback DeepSeek hoặc default model Qwen từ code khi runtime chưa được đối chiếu. Config expected model/revision phải từ inventory, không hardcode HF latest. Unknown revision ghi unknown và chặn live quality claim; model mismatch fail503 trước/trong inference, không fallback.

No-tool payload: chỉ system/user messages, `stream=false`, tools omitted hoặc `[]`, không tool registry/session/agent loop; reject mọi tool_calls/function_call trả về. Không gửi parallel tool flag true. Sampling riêng deterministic/low-temperature được khóa cho benchmark, không mutate sampling incumbent legacy. Prompt nói input là data chưa đủ chứng minh no-tool; test payload và không có outbound tool action. Không persist source/drafts vào Linch session/ledger. DuyTa là runtime orchestrator+translator; LID/QE không được sinh/hậu chỉnh dịch.

Composition option được chấp nhận: master OFF trả đúng `legacy_create_app()`; ON dùng outer ASGI dispatcher cho **exact** `/ai/v3/translate` và prefix `/ai/v3/translate/`, delegate tới FastAPI nhỏ với guards/error handlers/CORS riêng; mọi path khác chuyển tới legacy app không sửa. Outer lifespan phải forward legacy startup/shutdown và lifecycle client mới; legacy docs/routes giữ nguyên, OpenAPI mới có thể expose ở `/ai/v3/translate/openapi.json` chỉ phục vụ tài liệu, không thêm business API. Có thể dùng FastAPI outer shell/mount nhưng phải giữ path/scope, root_path, client và lifespan đúng; không mount kiểu đánh mất legacy lifespan.

Không chỉ add_router vào app cũ rồi nghĩ đã cô lập: generic rate middleware cũ sẽ trả envelope khác sau15 hits và sanitizer/hook đường cũ có thể xóa CJK. Alternative composition chấp nhận nếu tests chứng minh request mới không bị guards cũ xử lý, rate429 đúng schema sau burst, CORS đúng, legacy paths giữ nguyên. Không sửa app.py/api/middleware.py hoặc source file/OCR để làm điều đó. CORS của app mới đọc origins cấu hình hiện hữu, allow_credentials=false, methods GET/POST, headers Content-Type; expose Retry-After và X-Request-ID; không wildcard origins mở rộng. Preflight được xử lý trước POST quota.

No flags may be enabled for production until both directions of that language pass assessment, security and integration gates. Bật mock flags trong isolated tests không là rollout.

## SSE extension (05/10/2026)

`POST /ai/v3/translate/stream` nhận cùng JSON/flags/quota/budget như endpoint JSON. Phát `chunk` với `{delta,index,total,warnings}` sau validation, rồi `final` cùng TranslationResponse và `data: [DONE]`. Lỗi trước chunk đầu là HTTP/JSON; lỗi sau đó là event `error` cùng ErrorResponse, không có final. Gateway hủy task và trả admission slot khi disconnect/hoàn tất. Không phát token chưa kiểm tra. Xem [ví dụ ngắn gọn](dich-api-tom-tat.md).
