# Mô tả dataset Cyber-F1 v2

Nguồn chính thức: [DuyTa/Cyber_F1_v2 trên Hugging Face](https://huggingface.co/datasets/DuyTa/Cyber_F1_v2).
Tài liệu này tóm tắt dataset card của release đóng băng đó để dùng cùng gói fine-tune hiện tại.

## Mục đích

Cyber-F1 v2 là dataset supervised fine-tuning song ngữ Việt/Anh cho trợ lý an toàn thông tin. Nó
huấn luyện cả trả lời security trực tiếp lẫn agentic RAG: chọn đúng tool (kể cả không gọi tool),
truyền JSON argument đúng schema, đọc tool result, xử lý không có kết quả và hội thoại nhiều lượt.

Release v2 thay thế v1 vì v1 dùng harness ba tool và schema argument cũ. v2 được distill qua
live runner của harness mới bốn tool: `qdrant_search`, `neo4j_search`, `poc_search`, `poc_fetch`.
Dataset là frozen SFT release, không phải tuyên bố độc lập về độ đúng, an toàn hay production
readiness.

## Split và quy tắc sử dụng

| Split | Số bản ghi | Tỷ lệ | Chỉ dùng cho |
|---|---:|---:|---|
| train | 47.034 | 80% | SFT/training |
| validation | 5.877 | 10% | early stopping, chọn model |
| test | 5.885 | 10% | đánh giá IID đóng băng |

Không train, calibrate, prompt-tune hoặc chọn model bằng `test`. Theo dataset card, đây là grouped
IID holdout chứ không phải benchmark deployment/temporal độc lập; CVE/CWE có thể xuất hiện ở cả
train và test. Claim trước production phải dùng challenge set riêng đã khoá.

## Quy mô và coverage

Theo release card: 58.796 records; 53.891 tiếng Việt (91,7%), 4.905 tiếng Anh (8,3%), 27.260
trajectory có tool-use (46,4%), 1.943 multi-turn (3,3%). Dataset tham chiếu 14.942 CVE và 246 CWE.
Không có overlap first-user turn giữa train/validation/test theo quality gate của release.

Các task lớn gồm `external_real` (41%), `poc_lookup` (9,1%), `multiturn_followup` (8,3%),
`translation` (7,9%), `exploit` (7,1%), `multiturn_dialogue` (6,7%), cùng các nhóm CVE/CWE Q&A,
single-tool, multi-hop, tool-selection, no-result, concept và report.

## Nguồn gốc dữ liệu

- 51,5% `self_distill_runner`: trace chạy qua agent runner thật, giáo viên Cyber-F1 và DeepSeek
  V4 Pro được route theo batch/độ khó;
- 29,8% `external_defense_translated`: mẫu defensive-security từ ba dataset Apache-2.0, được
  step-sample và dịch Anh→Việt;
- 18,7% `family_b_reuse`: phần zero-tool phù hợp được tái sử dụng từ v1.

Tool trajectory chạy trên KB nội bộ tương đương `DuyTa/cve-kgrag-db` và PoC catalog cục bộ.
KB/tool services không được đóng gói vào dataset; khi deploy phải nối harness với KB tương thích.

## Schema

Mỗi dòng JSONL chứa `messages` kiểu OpenAI và metadata như `record_id`, `family`, `profile`,
`task`, `lang`, `source`, `teacher_model`, `n_tool_calls`, `n_turns`, `cve`, `cwe`.

Trong SFT, system prompt, user message và tool result là context. Loss chỉ tính trên assistant
turn, bao gồm assistant tool call và assistant final answer. `assistant.content: null` chỉ hợp lệ
khi turn đó có `tool_calls`.

## Lưu ý licence và an toàn

Release gắn licence `other` trong khi chờ review redistribution/licence tổng thể. Cần tôn trọng
licence, điều khoản và provenance của từng upstream source. Dataset có thể mô tả kỹ thuật offensive
hoặc PoC trong ngữ cảnh phòng thủ; chỉ dùng cho R&D phòng thủ, đánh giá trong sandbox và bổ sung
policy enforcement, tool authorization, audit cùng human escalation khi triển khai.
