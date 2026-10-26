# Đánh giá bổ sung: tài liệu thực và 18 hướng dịch

Trạng thái: đang chuẩn bị corpus và chạy đánh giá theo yêu cầu người dùng. Người dùng sau đó đã cho phép chủ động hoàn tất toàn bộ plan; hai finding High được mở lại để sửa bằng cách tiếp cận mới, không bỏ qua cổng review.

## Phương pháp cố định trước khi chạy

- Model OCR giữ Qwen/Qwen3-VL-2B-Instruct revision89644892e4d85e24eaac8bacfd4f463576704203, prompt18b11ec; không thêm checkpoint thứ tư hoặc chỉnh prompt theo kết quả.
- Ảnh thực lấy từ nguồn công khai được phép sử dụng, kèm bản chép có nguồn; ghi rõ trang tài liệu/ảnh hiện trường/crop. Ảnh thiếu transcript tin cậy không được gán gold tự sinh. Mục tiêu tối thiểu4ảnh mỗi10ngôn ngữ; báo rõ thiếu nếu nguồn không cung cấp.
- Dịch sử dụng câu nguồn và tham chiếu song ngữ công khai có provenance, tối thiểu4câu mỗi18hướng vi↔en/zh/ja/ko/fr/de/es/ru/ar, 3lượt cố định thứ tự bằng seed. Cố định câu, hash và các mệnh đề trước inference.
- OCR: chấm raw và corrected bằng CER/accuracy micro và macro theo từng ngôn ngữ; giữ nguyên lỗi đọc thứ tự và dấu câu. Chấm từng lớp token có mẫu tham chiếu; lớp không có mẫu là N/A, không phải100%.
- Dịch: chạy profile/pipeline dịch hiện có, báo HTTP/lỗi, ngôn ngữ đích, độ bảo toàn token và chrF tham khảo. Reviewer độc lập so từng mệnh đề với bản dịch tham chiếu; chrF hoặc backtranslation không tự chứng minh đúng nghĩa. Ghi rõ giới hạn của đánh giá hỗ trợ bởi AI, không tự nhận người bản ngữ.
- Nhóm chuẩn bị dữ liệu, nhóm gọi model và nhóm chấm nghĩa là các instance SOL riêng, worktree riêng. Root chuyển artifact giữa nhóm. Không sửa gold hoặc bỏ mẫu khó sau khi xem kết quả.
- Dùng Vast hiện có, GPUgiới hạn giữ nguyên; OCR và LLM chạy tuần tự theo giai đoạn để tránh tranh tài nguyên. Chỉ dừng service/tunnel do task tạo sau đánh giá.

## Phạm vi kết luận

Kết quả component với dịch vụ thật không thay thế nghiệm thu toàn bộ API/job, review bảo mật, Vanguard hay integration. Không coi khác biệt cách diễn đạt với tham chiếu là lỗi nếu nghĩa giữ nguyên; mọi bỏ sót/phủ định sai/số sai/đổi điều kiện đều phải ghi finding. Sẽ báo từng ngôn ngữ/hướng đạt, thất bại hoặc thiếu bằng chứng; không chỉ đưa điểm gộp.

## Nhân sự và artifact

- Corpus: /home/ict/secai-translate-real-corpus-wt — test/translate-real-quality-corpus.
- Runtime/results: /home/ict/secai-translate-quality-runner-wt — test/translate-real-quality.
- Proposition/independent review: /home/ict/secai-translate-quality-semantics-wt — test/translate-quality-semantics.
- Manifest, nguồn, checksum, kết quả từng mẫu và nhận xét sẽ được liên kết khi đóng băng/chạy xong.

## Kết quả dịch lần đầu — chưa sửa profile

- 216/216 lượt thực tế hoàn tất, dùng translate_blocks + LinchRunner profile translate_v2, model DuyTa/Cyber-F1-AWQ trên importbfab8eb. Không phải HTTPAPI/imagepipeline acceptance.
- Reviewer độc lập9ebb9b5a:154PASS,56FAIL,6BLOCKED theo từng mệnh đề trên tham chiếu công khai. Các lỗi: sốnhiều→sốít, nhầmloàichuột, vaccine→thuốc, thêmkhẳngđịnhkhỏihoàntoàn, đổi lượngtừ/phủđịnh. Sáu trườnghợp nguồnHàn cần phân xửsắcnghĩa; khôngtựnhậnchuyêngiabảnngữ.
- [Bảng18hướng và bằng chứng](/home/ict/secai-translate-quality-semantics-wt/secai/secai-agent-gateway/docs/test-results/translate-real-semantic-review.md). Không sửa dữliệu/mệnhđề đểđạt.
- Agentmới translation_quality_fix đangphântíchluồng/promptmới đểsửatổngquát; khônghardcode4câuFLORES, khôngđổiAPIcũ. Live rerun đợilượtGPU sauOCR và cầnthêmheldoutpublicreferences trướcđochốngoverfit.
