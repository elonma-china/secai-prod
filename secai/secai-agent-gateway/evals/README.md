# Eval suite — secai-agent-gateway

Bộ eval black-box, gọi thẳng HTTP API của gateway đang chạy (`:8131`), chấm điểm bằng
grader tất định dựa trên **contract thật của hệ thống** (vd `metadata.section1_tier`),
không phải "LLM tự chấm LLM".

## Chạy

```bash
python3 gen_samples.py --n 100          # sinh lại samples/*.jsonl (deterministic, seed cố định)
python3 run_eval.py exploit --limit 100                       # 1 chức năng
python3 run_eval.py exploit chat translate report statistical_report bughunt --limit 100  # tất cả, SONG SONG
```

Kết quả: `results/<function>_<ts>.jsonl` (raw response + grade từng sample) +
`results/<function>_<ts>_summary.json` (pass rate, breakdown theo category, top lý do fail).

## Bộ `chat_history/` — lịch sử chat (multi-conversation)

Eval riêng cho tính năng `/ai/conversations/*` (xem `docs/chat-history-api-guide.md`), chấm
**3 tiêu chí thang 0–100**: `do_chinh_xac` (độ chính xác), `on_dinh` (ổn định), `nho_ngu_canh`
(nhớ ngữ cảnh). Hai chế độ:

```bash
python3 chat_history/gen_samples.py                 # sinh samples/memory_scenarios.jsonl (seed cố định)
python3 chat_history/run_eval.py --offline          # in-process, KHÔNG cần LLM/Redis (plumbing + hợp đồng cũ)
python3 chat_history/run_eval.py --live http://127.0.0.1:8131 --limit 40   # gateway thật: đủ 3 điểm
```

- **`--offline`** boot app qua `httpx.ASGITransport` với runner giả tất định (giống
  `tests/test_conversations.py::ConvFakeRunner`): kiểm hợp đồng `/ai/chat` + `/ai/chat/stream`
  cũ **byte-identical** (cổng CỨNG = 100 hoặc FAIL), superset output mới, cô lập chủ sở hữu,
  ownership→404, transcript bền + đúng thứ tự, đa lượt tích luỹ, anonymous parity,
  blocked-không-lưu. Không cần dịch vụ ngoài.
- **`--live`** gọi gateway thật: replay `samples/chat.jsonl` qua `/ai/conversations/chat` chấm
  bằng `graders.grade_chat` và so với `/ai/chat` cũ (accuracy, không tụt); kịch bản đa lượt
  recall/isolation/compaction (nhớ ngữ cảnh). Cần gateway + serving embed/LLM biến thể
  fine-tuned (xem CLAUDE.md).

Kết quả: `results/chat_history_<mode>_<ts>_summary.json` (3 điểm 0–100 + `by_check` + `failures`).

## Chức năng & cỡ mẫu

| Chức năng | Endpoint | Số sample | Ghi chú |
|---|---|---|---|
| **exploit** | `/ai/exploit` | 100 | **Ưu tiên cao nhất** — xem "Anti-hallucination" bên dưới |
| chat | `/ai/chat` | 100 | grounding CVE/CWE + không rò tool nội bộ |
| translate | `/ai/translate` | 100 | đúng ngôn ngữ đích, không echo nguyên văn |
| report | `/ai/generate-report` | 100 | đủ section (chấp nhận bản dịch tiếng Việt) |
| statistical_report | `/ai/generate-statistical-report` | 100 | đúng JSON contract 3 field |
| bughunt | `/ai/bughunt/stream` | **12** (cố ý, xem bên dưới) | contract-only |

### Vì sao bughunt chỉ 12 sample, không phải 100

`bughunt` chạy scan/recon THẬT (nmap, ffuf, sqlmap...) vào `target`. Bắn 100 lần vào host
tuỳ ý/không được uỷ quyền là hành vi rủi ro (blast radius thật, có thể bị coi là quét trái
phép). Eval này CHỦ ĐỘNG scope xuống: chỉ nhắm **hạ tầng cục bộ do chính secai sở hữu**
(`127.0.0.1:8131/6333/7474`) và chỉ kiểm tra **tính đúng đắn của pipeline** (stream không
crash, có event `final`, không có event `error`) — KHÔNG phải "độ chính xác tìm lỗ hổng
trên internet thật". Muốn eval bughunt ở quy mô lớn hơn cần bộ target lab được uỷ quyền rõ
ràng (ngoài phạm vi bộ eval này).

## Anti-hallucination cho exploit (trọng tâm)

`enforce_section1` (poc/inject.py) quyết định **tier** của Mục 1 từ bằng chứng đo được,
không phải lời model tự nhận — đây là bất biến quan trọng nhất của hệ thống PoC. Sample
`exploit.jsonl` được chia category CÓ CHỦ ĐÍCH để test đúng bất biến này:

- `fake_cve` (10 mẫu, CVE hợp lệ cú pháp nhưng KHÔNG tồn tại) → **PHẢI** `section1_tier ==
  "not_found"` và Mục 1 không được chứa code block. Đây là check nghiêm ngặt nhất (fail cứng).
- `curated_known` (25 mẫu CVE nổi tiếng có PoC công khai) / `catalog_with_artifact` (18 mẫu,
  lấy random từ `poc-catalog.db` có sẵn artifact) → kỳ vọng tier `real`/`real_consolidated`,
  ghi warning (không fail cứng) nếu miss — vì phụ thuộc hạ tầng (mạng, GitHub rate-limit)
  chứ không hẳn lỗi logic.
- `catalog_desc_only` (17 mẫu, catalog có mô tả nhưng không có code) → tier bất kỳ hợp lệ,
  nếu `synthesized_*` bắt buộc phải có banner `⚠️ SYNTHESIZED`.
- `invalid_format` (5), `chitchat` (5) → không gọi model, kiểm tra guard hoạt động đúng.
- `natural_sentence` / `multi_cve` / `english_input` (20) → robustness của CVE-extraction.

Check áp cho MỌI sample đã qua pipeline đầy đủ: không rò tên tool nội bộ
(`poc_search`, `qdrant_search`, ...), không rò URL/internet ngoài Mục 1 (air-gap), đủ 3
heading Mục 1/2/3 (trừ `not_found` — model text bị loại bỏ hoàn toàn theo thiết kế).

## Kết quả mới nhất (2026-08-23)

| Chức năng | Pass rate | Ghi chú |
|---|---|---|
| **exploit** | **100/100 (100%)** | sau 2 vòng vá — xem "Bug đã tìm & sửa" |
| chat | 100/100 (100%) | |
| translate | 100/100 (100%) | |
| bughunt | 12/12 (100%) | scope-down, xem trên |
| report | 99/100 (99%) | |
| statistical_report | 90/100 (90%) | **chưa đạt 98% — xem finding tồn đọng** |

### Bug đã tìm & sửa (trong `agent/hooks.py`, không phải trong eval)

`RequireSection1SynthesisHook` — khi PoC là code thật (T1), hook `return None` sớm, bỏ qua
hẳn việc kiểm tra Mục 2/3 có đầy đủ không (chỉ áp dụng logic đó cho tier T2/T3 tổng hợp).
Model thỉnh thoảng dừng sau Mục 2, bỏ mất Mục 3 → phát hiện lúc exploit ở 89%.

**Vòng vá 1**: mở rộng check Mục 2/3 sang cả tier T1. Kết quả: 89% → 92% — nhưng lộ ra bug
thứ 2, tinh vi hơn: `linch`'s `force_continue` giữ draft cũ trong lịch sử hội thoại cho model
xem, nhưng **câu trả lời cuối cùng chỉ lấy từ completion mới nhất** (không gộp qua các lượt
retry). Feedback message đầu tiên viết "giữ nguyên phần đã viết, chỉ bổ sung phần thiếu" khiến
model hiểu nhầm và chỉ sinh phần bổ sung → phần đã viết trước (Mục 2) biến mất khỏi kết quả
cuối. Xác nhận qua 8/8 case fail đều cùng dấu hiệu: Mục 2 mất, Mục 3 còn.

**Vòng vá 2**: đổi mọi feedback retry (cả 3 nhánh, bao gồm nhánh T2/T3 có sẵn từ trước — cùng
rủi ro) thành yêu cầu model viết lại **toàn bộ nội dung cần thiết cho tier đó**, không dựa vào
việc gì "được giữ nguyên". Kết quả: 92% → **100%**. Test mới `test_t1_blocks_when_*` (3 case) +
sửa 2 assertion cũ theo thiết kế mới — 16/16 pass; full suite 334 pass (2 fail pre-existing ở
`test_bughunt_phase0.py`, không liên quan, xác nhận bằng git-stash-diff trước khi sửa).

### Finding tồn đọng — statistical_report (chưa sửa, ngoài phạm vi ưu tiên phiên này)

7/100 case model trả completion không hợp lệ, chia 2 dạng khác nhau:
- **3 case** (~10s, nhanh): model viết văn xuôi thay vì JSON 3-field — `parse_statistical`
  fallback về `report_summary=text` nên vẫn còn nội dung, chỉ sai format hiển thị legacy.
- **4 case** (~55–65s, CHẬM bất thường): completion **hoàn toàn rỗng** — không có gì trả về.
  Thời gian xử lý dài gấp 5-10× bình thường gợi ý có vòng lặp/retry nội bộ nào đó trước khi
  bỏ cuộc, nhưng chưa root-cause được nguyên nhân chính xác trong phiên này.

Đã xác nhận KHÔNG phải lỗi tất định (retry thủ công cùng input 3/3 lần đều thành công) — là
flake không thường xuyên (~7%), cần điều tra sâu hơn phần completion pipeline/sampling nếu
muốn đạt 98% cho chức năng này. Ngoài ra 2/100 case ăn HTTP 429 do chính eval harness chạy 6
chức năng song song aggressive (không phải bug sản phẩm — production không có tải kiểu này).

## Giới hạn đã biết

- Hằng số banner/guide text COPY thủ công từ source (không `import` trực tiếp module gateway
  vì `linch` chỉ có trong venv container, không có ở host chạy eval) — xem đầu `graders.py`.
  Nếu đổi prompt/constant mà eval fail hàng loạt ở đúng chỗ đó, kiểm tra đồng bộ trước.
- `curated_known`/`catalog_with_artifact` không fail cứng khi miss tier `real` — vì phụ thuộc
  tình trạng mạng/GitHub lúc chạy, không phải bug tất định. Xem `warnings` trong kết quả để
  biết hit-rate thực tế (KPI đáng theo dõi, không phải gate pass/fail).
- Rate-limit phía client cố tình thấp hơn server (`ROUTE_LIMITS` trong `run_eval.py`) để
  không bao giờ ăn 429 — nếu server đổi `route_limit()` (middleware.py), cập nhật theo.
