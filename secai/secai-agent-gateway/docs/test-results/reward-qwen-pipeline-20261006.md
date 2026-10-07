# Qwen3 reward pipeline — kiểm chứng kỹ thuật 2026-10-06

## Phạm vi

Pipeline offline và API chấm điểm riêng: approved preference exports, trusted expert IDs, grouped train/validation/test, scalar head/LoRA, sealed evaluation, artifact SHA-256, authenticated private serving và admin gateway relay. Chat sampling mặc định 0.1, cooldown 600 được giữ nguyên. Không kích hoạt scorer production hoặc tự chọn đáp án chat.

Không có dataset chuyên gia thật đủ điều kiện. Không tải hoặc fine-tune trọng số Qwen3-1.7B. Test kiến trúc dùng Qwen3 rất nhỏ khởi tạo tại chỗ và dữ liệu synthetic, chạy CPU. Những test này không đo chất lượng tiếng Việt ATTT.

## Kiểm chứng liên quan

Môi trường test optional riêng `/tmp/secai-reward-qwen-env`, Transformers 4.57.1, tokenizers 0.22.1, PEFT 0.17.1 và PyTorch 2.14.1+cu130. Test chạy CPU, không thuê GPU. Môi trường overlay phục vụ test không phải môi trường serving production; nó còn kế thừa các gói unrelated của máy.

```sh
PYTHONPATH=src:tests /tmp/secai-reward-qwen-env/bin/python -m pytest -q \
  tests/test_reward_qwen.py tests/test_reward_pipeline.py tests/test_reward_service.py \
  tests/test_review_export.py tests/test_review_api.py tests/test_review_auth.py \
  tests/test_review_live.py tests/test_review_middleware.py tests/test_review_store.py \
  tests/test_review_templates.py tests/test_fe_review_proxy.py
```

Kết quả sau sửa bốn finding Important từ review độc lập: **64 passed in 17.29s**, không warning. Bao gồm:

- Dữ liệu QA/demo và customer-only judgment bị loại khỏi production.
- Export admin thật từ API, chosen đúng sau shuffle và manifest campaign_title tương thích loader.
- Dedup không làm mất liên kết source conversation; ba partition độc lập, tampering bị từ chối.
- Pairwise gradient học tăng điểm chosen, score head save/load nhất quán.
- Head và LoRA đi qua prepare → train → evaluate → verify artifact → reload; test bị niêm phong, demo không được serve production.
- Gateway gọi service ASGI thật sử dụng tiny Qwen3 đã huấn luyện; đáp án preferred đúng cho tình huống synthetic.
- Bearer auth, quyền admin, disabled mặc định, body/response limit, timeout, mismatch SHA và overflow margin được kiểm tra.

CLI help và compileall chạy thành công; `git diff --check` sạch tại thời điểm kiểm tra. Không thay frontend trong tác vụ pipeline này.

## Suite backend tổng

Đã chạy `.venv/bin/python -m pytest -q`, kết thúc sau 588.91s: **1765 passed, 3 failed, 1 skipped, 65 warnings**. Lần chạy này bắt đầu trước khi hoàn tất thay đổi signature expert_ids; không phải kiểm chứng snapshot cuối.

- `tests/multilang/test_api.py::test_composition_off_on_legacy_and_cors`: preflight trả origin `*`, trong khi test yêu cầu khác `*`.
- `tests/test_bughunt_phase0.py::test_bugbounty_root_and_arsenal`: thiếu `/home/ict/AIEngines/secai/claude-bug-bounty`.
- `tests/test_reward_qwen.py::test_preparation_checksum_three_disjoint_partitions_and_qa_rejection`: tiến trình đang chạy giữ module cũ trong khi file mới đã đổi signature; chạy lại riêng snapshot cuối **1 passed in 1.44s**, và suite reward/review cuối cũng pass.

Hai failure đầu đã tái hiện riêng: **2 failed in 1.91s**. Không sửa hai phần ngoài phạm vi để làm suite xanh. Không khẳng định toàn bộ suite pass.

Suite rộng sau các sửa đầu, loại `tests/test_gateway.py` (file này đã hoàn tất trong lần chạy tổng đầu): **1751 passed, 2 failed, 3 skipped, 64 warnings in 463.75s**. Hai failure vẫn là CORS và thiếu Bughunt nêu trên. Môi trường gateway `.venv` thiếu bộ optional Transformers/tokenizers tương thích nên bỏ qua ba test Qwen3; cả ba đã chạy thật và pass trong môi trường reward riêng. Sau sửa review cuối, toàn bộ 64 test liên quan đã được chạy lại; kiểm tra riêng bằng môi trường gateway cũng **25 passed, 3 skipped in 5.95s**.

## Giới hạn phát hành

Chưa push hoặc rollout production trong tác vụ này. API mới và runner là mã nguồn đã triển khai; gateway production đang chạy không được thay đổi bởi các test. Chưa có policy phát hành đã chốt trên dữ liệu chuyên gia thật, chưa có artifact Qwen3-1.7B đủ điều kiện. Operator guide: [reward-qwen-operator.md](../reward-qwen-operator.md).

## Review độc lập

Reviewer phát hiện bốn finding Important: expert không chọn winner, source ID unknown, QA chỉ lọc qua tên, metric ngoài miền giá trị vẫn vượt gate. Đã thêm regression và sửa: expert phải left/right cùng winner; source ID rõ ràng; campaign production được allowlist tường minh; gate kiểm type/range/count consistency và service không nhận báo cáo metric sai.
