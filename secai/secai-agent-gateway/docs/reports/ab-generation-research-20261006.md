# Nghiên cứu sinh hai câu trả lời A/B có khác biệt hữu ích

Ngày: 2026-10-06. Agent nghiên cứu `research_ab_generation`, chỉ đọc mã nguồn và tài liệu; chưa thay đổi cơ chế sinh câu trả lời.

## Hiện trạng và giới hạn

`/review-api/live-pairs` lấy câu trả lời chat đã hoàn tất và gọi lại cùng profile trong session riêng để sinh B. Lịch sử hội thoại hiển thị được chuyển vào session; không có seed riêng cho từng candidate. Filter hiện chỉ loại B rỗng, lỗi, hoặc trùng nguyên văn với A.

B chưa được bảo đảm dùng cùng snapshot dữ liệu truy xuất/tool results như A. Vì thế không thể coi đây là experiment chỉ thay seed. Hai câu gần như diễn đạt lại nhau vẫn có thể lọt qua. Reward Qwen3-1.7B mới kiểm chứng bằng dữ liệu QA, chưa đủ cơ sở dùng làm cổng chất lượng live.

## Các phương án

| Phương án | Khác biệt | Chi phí | Nhận định |
|---|---|---|---|
| Cùng context, hai seed | Có thể đổi cách diễn đạt/lập luận | Thêm một lượt sinh | Seed không bảo đảm khác nghĩa; hữu ích cho kiểm soát/audit |
| Khác temperature/top-p | Tăng đa dạng | Thêm một lượt sinh | Randomness cao có thể làm tăng lỗi |
| Recipe giải quyết khác, cùng bằng chứng | Khác cách ưu tiên, lập luận hoặc kế hoạch hành động | Thêm một lượt sinh | Khuyến nghị bước đầu |
| Một request `n=2` | Hai mẫu từ cùng input | Khoảng hai phần output token | Serving có hỗ trợ, runner hiện chưa expose; cần spike |
| Best-of-3 và chọn cặp đa dạng đạt chuẩn | Tăng khả năng có cặp tốt, khác nhau | Ba lượt sinh và lọc/chấm | Làm sau khi có scorer đáng tin và ngân sách |
| Hai model khác nhau | Thường đa dạng hơn | Thêm hạ tầng/model | Phải kiểm soát khác policy, nguồn và phân phối dữ liệu |
| Viết lại A thành B | Dễ khác câu chữ | Một lượt phụ | Neo vào A; không nên làm mặc định |

vLLM có `seed`, `temperature`, `top_p`, `n`, nhưng reproducibility còn phụ thuộc runtime, batching và phần cứng. Không thể cam kết seed khác luôn tạo câu khác nghĩa. [SamplingParams](https://docs.vllm.ai/en/latest/api/vllm/sampling_params/), [Reproducibility](https://docs.vllm.ai/en/latest/usage/reproducibility/).

## Đề xuất bước đầu

1. Giữ cùng câu hỏi, hội thoại, chính sách an toàn và snapshot evidence ở phía server. Snapshot gồm cả tool results thực sự dùng cho A; lưu hash và nguồn gốc.
2. Giữ A hiện có; sinh B từ cùng evidence, với recipe khác về cách giải quyết. Ví dụ A ưu tiên kết luận/checklist, B giải thích đường tấn công và kế hoạch triển khai. Không để B truy xuất nguồn mới trong lượt synthesis.
3. Kiểm tra cả hai đúng, có căn cứ, phù hợp câu hỏi; không cố tình làm một câu kém để dễ chọn.
4. Lọc exact sau chuẩn hóa, overlap/ngram, rồi thử embedding similarity bằng `DuyTa/sec-embedding` nếu có ích. Hiệu chỉnh ngưỡng bằng nhãn chuyên gia; cosine cao không tự chứng minh hai phương án chỉ là rewrite.
5. Nếu B quá giống, thử lại tối đa một lần với recipe/seed khác. Nếu vẫn không đạt, bỏ lời mời; không ép phải có cặp.
6. Đảo vị trí A/B, giấu model/recipe với user nhưng lưu provenance. Giữ lựa chọn ngang nhau/cả hai kém/bỏ qua để tránh ép preference giả.

Embedding similarity có cơ sở từ [Sentence-BERT](https://arxiv.org/abs/1908.10084), nhưng ngưỡng cần kiểm chứng trên tiếng Việt ATTT. Không dùng LLM judge làm cổng duy nhất vì có thiên lệch vị trí. [Large Language Models are not Fair Evaluators](https://aclanthology.org/2024.acl-long.511/).

## Kiểm chứng trước khi chọn giải pháp

Pilot 200–500 prompt phân tầng, cùng evidence, so ba cách: seed-only, controlled recipe, best-of-3. Chuyên gia chấm blind:

- Cả hai đáp án có đúng, có căn cứ và an toàn không?
- Khác biệt có giúp user chọn cách giải quyết phù hợp không, hay chỉ đổi câu chữ/độ dài?
- Tỷ lệ gần trùng, ngang nhau, cả hai kém và bất đồng.
- Thiên lệch trái/phải, độ dài và recipe.
- Chi phí token/GPU, thời gian chờ p50/p95 và tỷ lệ tạo được cặp.

Chia tập theo conversation/source group để tránh leakage; giữ cả lý do không sinh được cặp. Click của user là sở thích, không tự là nhãn đúng/sai. Admin/chuyên gia cần duyệt trước export training.

## Điểm cần chốt trước triển khai

- Có thu được đầy đủ evidence/tool results của A một cách an toàn không?
- Runner/serving thực tế có cho override sampling theo request không?
- Mức tăng độ trễ và ngân sách GPU chấp nhận được?
- Mục tiêu là cải thiện model chính hay so sánh nhiều model?
- Cần nhận diện user thật để cooldown và dữ liệu không bị gộp như tài khoản demo hiện tại.

Đây là đề xuất nghiên cứu, chưa phải tính năng đã triển khai. Hướng khuyến nghị: **cùng evidence + recipe khác + lọc gần trùng/quality + retry có giới hạn**, sau đó mới mở rộng best-of-N.
