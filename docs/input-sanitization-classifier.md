# Input Sanitization — Model-based Classification (chat)

Bổ sung cho phần **Sanitization** của chức năng chat: một **classifier dựa trên embedding**
gán *taxonomy tổng hợp* cho mỗi câu hỏi **trước khi vào agent**, để từ chối off-topic và chặn
prompt-injection một cách **tất định, đo được** — thay vì chỉ phó mặc `<scope_policy>` của LLM.

## Kiến trúc

```
POST /ai/chat[/stream]
  → require_public_input()            # redact secret/PII cứng (đã có, giữ nguyên; 422)
  → _chat_gate()  ─┐                  # LỚP MỚI (api/routes.py)
                   ├─ classify_input()  (secai_gateway/sanitize/classifier.py)
                   │     embed(sec-embedding)  →  numpy head (3 sub-head độc lập)
                   │        intent    = MLP 1 lớp ẩn  → softmax: security | chitchat | offtopic
                   │        injection = logistic       → sigmoid: prompt-injection/jailbreak
                   │        pii        = logistic       → sigmoid: lộ secret/PII
                   └─ decide()          (secai_gateway/sanitize/gate.py)
        injection → chặn + câu từ chối an toàn        (không vào agent)
        offtopic  → câu <scope_policy> tất định       (không vào agent)
        chitchat  → PASS (mặc định; LLM lo lời chào)
        pii       → PASS + cờ mềm (audit/redact)
        PASS      → chạy agent như bình thường
```

- **Runtime numpy-only** (không torch/transformers) — head là 3 sub-head lai (`kind="mixed"`):
  intent = MLP 1 lớp ẩn (128) cho ranh giới chitchat/offtopic tốt hơn, injection/pii = logistic
  class-balanced (giữ precision cao). Inference = vài matmul, sub-ms. Ship tại
  `src/secai_gateway/sanitize/assets/head.npz` (+ `head.json`, `thresholds.json`, `metrics.json`),
  nạp 1 lần theo mẫu singleton+lock của `retrieval/bm25.py`. Loader còn nhận head thuần linear
  (5-row) để tương thích ngược.
- **Tận dụng model đã có**: embed bằng chính endpoint `sec-embedding` (`retrieval/embedding.py::embed`).
- **FAIL-OPEN tuyệt đối**: thiếu head / head chưa train / lệch `embed_model` / lệch chiều / embed
  lỗi/timeout → verdict `unknown` → **PASS**. Classifier chỉ có thể *thêm* một lần chặn, không bao
  giờ làm sập chat. Backstop vẫn là `<scope_policy>` trong LLM.

## Cấu hình (`config.Settings`, đọc từ `.env`)

| Env | Mặc định | Ý nghĩa |
|---|---|---|
| `INPUT_CLASSIFIER_ENABLED` | `true` | Bật/tắt toàn bộ lớp |
| `INPUT_CLASSIFIER_WEIGHTS_PATH` | `""` | Thư mục head ngoài; rỗng → asset đóng gói |
| `INPUT_CLASSIFIER_BLOCK_INJECTION` | `true` | Chặn injection (đặt `false` = shadow/log-only) |
| `INPUT_CLASSIFIER_BLOCK_OFFTOPIC` | `true` | Chặn off-topic (đặt `false` = shadow/log-only) |
| `INPUT_CLASSIFIER_SHORTCIRCUIT_CHITCHAT` | `false` | Trả lời chào ngắn thay vì để LLM lo |
| `INPUT_CLASSIFIER_*_THRESHOLD` | `0` (=dùng `thresholds.json`) | Ngưỡng injection/pii/offtopic |
| `INPUT_CLASSIFIER_EMBED_TIMEOUT_S` | `5.0` | Trần embed thêm vào chat (timeout → fail-open) |

**Shadow mode**: mọi request chat đều log `input-sanitize ... verdict=<label> action=<...>` (kể cả PASS),
nên có thể đặt `BLOCK_* = false` để đo verdict trên traffic thật trước khi bật chặn.

Điều kiện kích hoạt head ở production: `EMBEDDING_MODEL` phải khớp `head.json:embed_model`
(hiện `DuyTa/sec-embedding`). Lệch model → fail-open, không chặn nhầm.

## Huấn luyện lại head (offline, không đụng runtime)

`scikit-learn`/`sentence-transformers` chỉ dùng lúc train (`.[train]` extra), **không** vào image.

```bash
# 1) Dựng/mở rộng dataset (song ngữ VI/EN, 5 nhãn) — deterministic
python scripts/build_sanitize_dataset.py            # -> evals/sanitize/dataset.jsonl

# 2a) Head PRODUCTION — embed qua chính endpoint đang deploy (khuyến nghị cho nghiệm thu)
AIRGAP=false python scripts/train_sanitize_head.py --embed endpoint

# 2b) Tái lập offline khi endpoint không sẵn — kéo đúng model từ HF (cần .[train] + torch)
python scripts/train_sanitize_head.py --embed st --model DuyTa/sec-embedding
```

Ghi ra `src/secai_gateway/sanitize/assets/{head.npz,head.json,thresholds.json,metrics.json}`.
Ngưỡng được tự tinh chỉnh trên holdout (injection ưu tiên Recall ≥ 0.95).

## Chỉ tiêu (KPI) — đề xuất để chốt nghiệm thu

Đo bằng grader tất định trên holdout (`metrics.json`): P/R/F1 mỗi nhãn + macro-F1 + confusion
matrix. Đề xuất: **injection Recall ≥ 0.95**, Precision ≥ 0.85; security/off-topic F1 ≥ 0.90;
**macro-F1 ≥ 0.90**; độ trễ phần thêm p95 ≤ 200ms (chủ yếu 1 hop embed; head numpy sub-ms).

**Phương pháp chống thổi phồng KPI (quan trọng):** split được chia **theo skeleton/template**,
không theo dòng — mọi biến thể của một template nằm cùng một phía, và ~25% template được **giữ
riêng cho test** (không xuất hiện khi train). Vì vậy F1 đo **khả năng tổng quát hoá sang cách diễn
đạt CHƯA thấy**, không phải nhớ template. `build_sanitize_dataset.py` tự in `shared=0` để chứng minh
không rò rỉ train↔test.

> Dataset hiện ~1388 mẫu song ngữ (holdout ~383, leakage skeleton = 0; injection stratify theo
> kỹ thuật → cả 9 kỹ thuật + costume VI/EN đều có ở test). Phủ: câu sạch, không dấu/teencode, dán
> code + **log dài >200 ký tự**, code-mix VI-EN, PII thật (email/SĐT/CCCD/JWT), borderline
> security↔offtopic, **hard-negative** (từ khoá bảo mật nhưng off-topic), injection 9 kỹ thuật.
> Head **mixed** (intent MLP + injection/pii logistic class-balanced, C chọn bằng CV-trên-train).
> KPI trung thực (đã qua nhiều vòng audit độc lập): intent acc **0.987**, macro-F1 **0.986**,
> và **MỌI lớp F1 ≥ 0.98**: security **0.990** · chitchat **0.983** · offtopic **0.982** ·
> injection **0.983 (R 1.0)** · pii **0.994 (R 1.0)**. Overfit gap intent acc ~0.013 (lành mạnh).
>
> **Giới hạn đã biết:** SQL/exploit dán inline đôi khi bị gắn cờ injection (ngưỡng đặt thấp để
> R=1.0). Ngưỡng injection/pii/offtopic cấu hình được lúc chạy.

## Test

```bash
pytest tests/test_input_classifier.py -q        # head decode, fail-open, gate, route short-circuit
```
