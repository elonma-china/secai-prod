# Multilang adapter — frozen conditional criteria v1

2026-10-03, **CRITERIA_READY; Gate C NOT ENTERED**. Không tạo worker/training job/adapter branch chỉ vì code scaffolding đã được cho phép. Chỉ DuyTa incumbent đang chạy trên Vast được sinh bản dịch; không model dịch thay thế.

| ID | Gate và evidence |
|---|---|
| AD-01 | Entry: Gate B DEV cho thấy hướng chưa đạt; exact base weights/revision/license cho phép train, engine+quant+LoRA load thực tế đã xác minh, dev compute/time/cost quota được phân bổ không trùng OCR. Thiếu bất kỳ mục nào→BLOCKED/báo user, không đoán từ HF repo name và không đổi model. |
| AD-02 | Train manifests license/provenance; exact+near-dedup theo source groups với DEV/TEST/FLORES devtest; public/synthetic/anonymized, backtranslation chỉ incumbent. Leak làm evidence invalid và cần train clean, không sửa report để đạt. |
| AD-03 | DEV đo adapter multilingual/group lựa chọn có evidence; en↔vi/general replay chống quên. So paired18 hướng cùng prompt/budgets/decoder và reference baseline; đủ70% rubric+entity100%+off-target≤2%+en↔vi margin frozen. Không tune sealed TEST. |
| AD-04 | Adapter alias gắn chính base đã pin; không merge vào incumbent shared để thử. Routing chỉ server eligible pair+flag. Cold/hot switch races không lẫn adapter giữa request; unsupported adapter fail bounded, không silent fallback che fail. FlagsOFF quay lại baseline observed. |
| AD-05 | Checkpoint safetensors/checksum/provenance ngoài Git và ngoài ephemeral host; resource/cost ledger+abort; unload owned worker only. Không restart/terminate incumbent/OCR. Report steady/peak VRAM, latency/cost, cleanup evidence; adapter không chiếm auxiliary slot giả để chạy GPU song song. |

Nếu B đạt thì Gate C **N/A với evidence B**, không gọi adapter PASS. Nếu C/D vẫn fail ngôn ngữ nào thì cả hai chiều OFF và báo user theo stop condition. [Assessment](multilang-assessment.md) + [release](multilang-integration-security.md) vẫn áp dụng đầy đủ, không giảm tiêu chí vì adapter tốn công.
