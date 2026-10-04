# Kiểm chứng consumer preference và giới hạn tích hợp

Ngày 2026-10-04. Chỉ đọc source và kiểm chứng offline bằng dữ liệu tổng hợp; không gọi model, train, trigger pipeline hoặc deploy. Không sửa bộ build/bằng chứng nghiệm thu gốc.

## Consumer thực tế đã tìm thấy

Bộ build nằm tại `/home/ict/AIEngines/secai/HO-SO-GOC-NGHIEM-THU-AI/02. Bo_build_finetuning/` (dữ liệu ngoài checkout; tìm với `rg --files --no-ignore`, bỏ `.venv`, `runs`, `setup` và dataset).

- `finetune/src/secai_ft/trainers/unsloth_dpo.py:8`: contract JSONL gồm `prompt` là danh sách message trước final assistant; `chosen`, `rejected` là chuỗi nội dung assistant.
- Cùng file dòng 99–112: `load_dataset("json", data_files={"train": ..., "validation": ...})`; hàm `fmt(ex)` gọi `tok.apply_chat_template(ex["prompt"], tokenize=False, add_generation_prompt=True, enable_thinking=False)` rồi giữ `chosen/rejected`. Metadata review bổ sung không được dùng trong mapping này.
- Cùng file: `DPOTrainer(...).train()` là consumer huấn luyện DPO thực tế. Không tìm thấy trainer Reward Model riêng trong `finetune/src` hoặc `mlops/scripts`; DPO không đồng nghĩa đã huấn luyện một Reward Model riêng.

## Schema tương thích, pipeline chưa nhập dữ liệu review

`mlops/scripts/05_dpo_llm.py:49–64` nhận `--llm-qa-report` và `--test`, không nhận preference JSONL từ review. `build_pairs` ở dòng 24–44 tạo chosen từ gold answer và rejected từ câu trả lời serving bị judge đánh giá sai.

`mlops/scripts/run_pipeline.sh:231–237` gọi bước DPO với report drift và `llm_test.jsonl`. Pipeline hiện không đọc ZIP/preference export của review.

`mlops/trigger_api/app.py:158–183` chỉ có trigger request `mode` hoặc `execute/allow_deploy`; không có endpoint upload/ingest review. Gateway `api/showcase/retraining_proxy.py` chỉ proxy tới service này, không thêm consumer.

Vì vậy dữ liệu preference review có core schema phù hợp trainer; chưa có bằng chứng rằng gửi export vào pipeline hiện tại sẽ được sử dụng. Không gọi `/trigger`, `/smoke` hoặc tự thay dataset để chứng minh tích hợp. Nếu bổ sung ingest sau này, cần adapter chọn trường, cấu hình train/validation riêng và chia theo `source_group`, giữ truy vết adjudication/manifest; không thay gold/drift dataset hiện tại bằng dữ liệu review.

## Kiểm chứng offline đã thực hiện

PASS: một mẫu tổng hợp được import → phân công → nộp → admin duyệt candidate A → xuất ZIP preference. Đọc JSONL thật từ ZIP rồi chạy **hàm `fmt` trích từ AST source consumer thực tế**; không import/call trainer hay model loader.

Tokenizer dùng double offline có tên rõ ràng, kiểm tra đúng các cờ `tokenize=False`, `add_generation_prompt=True`, `enable_thinking=False` và prompt kết thúc bằng user. Mapping giữ đúng chosen/rejected của mẫu đã duyệt và chấp nhận các metadata bổ sung. Không dùng tokenizer checkpoint thực tế, không đo token limit, loss, chất lượng hay training; đây là chứng cứ schema/mapping, không phải chạy pipeline.

Lần chạy đầu dừng khi import gateway do mặc định catalog `/srv/exploitdb` không có quyền. Chạy lại với `POC_CATALOG_DB`, `GATEWAY_DB_PATH` tách trong `/tmp` và `SESSION_STORE_BACKEND=memory` thành công. Review database tổng hợp dùng `TemporaryDirectory` và được xóa sau kiểm chứng.

## Linch: các điểm cần theo dõi khi nghiệm thu thật

- `_apply_profile` trong `agent/runner.py` đặt registry theo profile; hai profile template có `tools=[]`, `base=false`, không sửa profile report cũ.
- Linch giữ cache session live trong `Agent._sessions`. Chỉ `session_store.delete(UUID)` chưa giải phóng cache. Module review mới gọi public `agent.release_session(UUID, force=True)` trước khi xóa durable history; UUID của chat gốc không được sử dụng. Cleanup có timeout và best effort; lỗi cleanup không được xem là bằng chứng xóa thành công.
- Runner hiện thêm date context cho mọi profile trừ translate. Profile template mới có thể nhìn thấy date note; nghiệm thu model thật cần kiểm tra không có nội dung date/context ngoài yêu cầu trong draft/report.
- `LinchRunner.complete` xử lý final text rồi `chat_final_text` có thể thay kết quả chat rỗng bằng fallback xác định. Fake runner tests không chứng minh mọi lỗi provider ở Linch thật được phân loại. B live phải được chuyên gia/admin đánh giá; không coi một phản hồi fallback là bằng chứng model đã tạo candidate chất lượng.
- `_session_locks` của runner không giữ UUID sau complete: context manager ở `agent/runner.py:255–275` xóa entry trong `finally`, kể cả khi request timeout/cancel. Không cần sửa private dictionary khi release session.
- Sampling/cooldown/in-flight hiện theo process. MVP chạy một worker; chưa có distributed budget giữa nhiều worker. Không claim có tự học hoặc tự cập nhật reward.
