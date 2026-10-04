# Multilang — trạng thái hiện tại

Cập nhật 2026-10-04. **Đã code, review độc lập và kiểm thử tích hợp trên CPU. Warden cuối cho phép bàn giao implementation CPU, không có blocker mới trong phạm vi này. Chất lượng thực tế và phát hành vẫn BLOCKED.** Toàn bộ suite chưa xanh vì còn lỗi baseline.

## Phạm vi đã chốt

- Giữ DuyTa đang phục vụ làm model điều phối và dịch; không thay model, fallback hay post-edit bằng model khác.
- Thêm 16 hướng vi↔zh-Hans/ja/ko/fr/de/es/ru/ar (Arabic MSA); en↔vi cũ giữ nguyên.
- Yêu cầu chất lượng ≥70% từng hướng/từng vòng; off-target ≤2%; protected entities 100%; tối thiểu 30 nguồn riêng biệt ×18 hướng ×3 vòng. Chưa chạy đánh giá thật; không suy tỷ lệ chất lượng từ unit test.
- Tối đa 3 model hỗ trợ dùng tuần tự. Owner OCR đã dùng 3/3; task này không thêm lượt thử. Không đổi dịch vụ hoặc công việc GPU của owner khác.
- Vast.ai đã thuê; không thuê thêm hoặc huấn luyện adapter. Giá thuê/chi phí tích lũy chưa có bằng chứng; không khai chi phí bằng 0.
- API v3 JSON stateless/anonymous và module composition riêng. Flags mới mặc định OFF. Không sửa backend cũ, file/OCR hoặc workspace đang có WIP.

## Snapshot và quyền sở hữu

| Phần | Nhánh / worktree | SHA và trạng thái |
|---|---|---|
| Main | main | ce9cd47ee1273018fec1c5cbab944a86d68d6ed5; chưa merge task này |
| Điều phối | feature/multilang-translate, /home/ict/secai-multilang-wt | Root sở hữu STATE/LOG và bản gốc yêu cầu, criteria, review artifacts |
| Backend | feature/multilang-core, /home/ict/secai-multilang-core-wt | f6b7834d5fb215075cb0cdbc448e3b538d93f7b3; review 4 PASS |
| Frontend | feature/fe-devspace-multilang, /home/ict/secai-multilang-fe-wt | 788da975cf9eed0e0bf72089fd137e00fd8ad2b2; review 2 PASS |
| Assessment | feature/multilang-assessment, /home/ict/secai-multilang-assessment-wt | f38801385321b07fd4bc210f5e2d751d1d727795; review 5 PASS |
| Security | test/multilang-security, /home/ict/secai-multilang-security-wt | 11ec898bd712ba1add2bf56c0ac4f9918c5b1bf8; CPU checkpoint, không phải release PASS |
| Tích hợp | integration/multilang-translate, /home/ict/secai-multilang-integration-wt | Code/tests thực thi tại 877e619514ed2a5ed7dcd5a2353c02b03ca295b4; các commit báo cáo sau đó chỉ tài liệu |
| Cây ghép mô phỏng | /tmp/secai-multilang-combined-runtime | e6856eda87bbb10668d06a3f2b71896216abb673; không nhập file/OCR vào nhánh sản phẩm |
| Import được bảo vệ | /home/ict/secai-translate-import-wt | bfab8ebc96e25b23325fd72a1d3bf034e9aea315; STOPPED/BLOCKED, không sửa |
| OCR được bảo vệ | /home/ict/secai-translate-ocr-wt | ff311c12f97a1dd453495b0ff2b9ca269a49aba2; giữ nguyên HEAD/WIP |

## Kết quả kiểm chứng

- Baseline cùng môi trường: 925 cases = 905 pass, 19 fail, 1 skip.
- Tích hợp cuối 877e619: 1.140 cases = **1.120 pass, 19 fail, 1 skip**, không error; bao gồm 152 assessment và 17 Vanguard pass.
- Cây ghép e6856ed: 1.247 cases = **1.227 pass, 19 fail, 1 skip**, không error. Mọi ID/kết quả baseline/import/OCR/prior runtime được giữ nguyên; không có lỗi mới.
- 19 lỗi cũ gồm thiếu cây Bughunt (1), dữ liệu PoC riêng không sẵn có (5), timeout legacy (13); 1 skip liên quan catalog. Không bỏ test hoặc nới timeout để đạt.
- Môi trường riêng /tmp/secai-multilang-final-env: 67 packages, 57 phiên bản baseline giữ nguyên và 10 dependency bổ sung; 17 yêu cầu project và 7 pin metric hợp lệ, uv pip check PASS. Không sửa venv của dịch vụ.
- FE 21 tests/build PASS. HTTP/browser tại 43e5f22 dùng fake LLM: vi→zh-Hans, ja→vi, vi→ar, CVE/IP, RTL, desktop/mobile. Copy API dùng stub, chưa kiểm OS clipboard. Toàn bộ source/lock/config không đổi ở candidate cuối; tái sử dụng bằng chứng theo hash, không nhận là lượt chạy browser mới.
- 7 composition probes và fingerprint 69 legacy paths/15 schemas, JSON/SSE giữ nguyên ở checkpoint cùng source/dependency. Thêm import/config/metric coexistence probes ở cả hai snapshot cuối.
- Semgrep OSS 1.179.0: runtime 14 files/9 units (7 complete, 2 partial); assessment f388013 7 files/6 units (4 complete, 2 partial), không finding. Lỗi compile rules bên thứ ba được giữ nguyên trong báo cáo; không phải security PASS toàn diện. CodeQL chưa chạy.
- Main/runtime, runtime/import và runtime/OCR merge-tree sạch. File+OCR có 2 conflict kế thừa ở profiles.yaml và translation/settings.py, chỉ giữ minimal union trong cây mô phỏng. Hai High của task file vẫn tồn tại; không sửa hoặc nhận nghiệm thu thay owner.

## Bộ đếm và gates

| Task | Fix cycles | Review / kết quả | Vanguard returns |
|---|---:|---|---:|
| Core T3/T4 | 3/5 | 4 lượt, findings 8→4→1→0, PASS | 0/3 |
| FE T5 | 1/5 | 2 lượt, CDN finding đã đóng, PASS | 0/3 |
| Assessment T2 | 4/5 | 5 lượt, findings 9→6→5→1→0, PASS | 0/3 |
| Integration T9 | 0/5 | Fresh Astra B, exact-SHA CPU checkpoint hoàn tất | 0/3 |
| Adapter T6 | 0/5 | Gate C chưa vào; không tạo/huấn luyện | 0/3 |

Các criterion tái phát đã đổi cách xử lý: source language dùng pure contrary-script rule thay ratio; protocol dùng ma trận marker/location thay kiểm từng field rời. Không reset bộ đếm hoặc vượt giới hạn; review task file khác dừng theo Warden của họ.

Gate A còn thiếu attestation revision/tokenizer/template/context, corpus/reference có quyền sử dụng, evaluator/adjudication và custody TEST. Metadata có model ID DuyTa/Cyber-F1-AWQ; cache snapshot7e7e6718… không chứng minh revision đang load. Gate B DEV ablation chưa chạy; Gate C chưa vào; Gate D BLOCKED. Chưa có bằng chứng 18 hướng/3 vòng, noninferiority en↔vi, latency/throughput/cost, privacy/egress/rollback production hoặc quota toàn cluster. Mọi ngôn ngữ mới OFF.

## Tài nguyên, sự cố và bàn giao

Private staging đã dừng sau khi đối chiếu PID/cmd/cwd: 1153543 (5178), 1128637 (5177), 1407822 (5180); ss xác nhận không còn listener. Browser multilang-root đã đóng. Giữ worktree, screenshot và môi trường test để review; không chạm Vast hoặc dịch vụ của task khác. Xem resource-closeout.md.

Sự cố preflight: raw tool output trước đó đã lộ credential. Không ghi giá trị vào Git/báo cáo, chưa xoay khóa, không tuyên bố đã xóa transcript. Xem docs/reviews/multilang/preflight-output-incident.md.

Các report nguyên văn ở docs/reviews/multilang/. Báo cáo cuối, toàn bộ case IDs, commands/config/dependency hashes và minh chứng nằm tại docs/test-results/multilang/ trong worktree integration. Root STATE/LOG là trạng thái điều phối hiện hành; báo cáo cũ chỉ có giá trị tại SHA ghi trong đó.

Bàn giao CPU đã được Warden chấp nhận; xem docs/reviews/multilang/warden-final.md. Tài liệu cuối được nhập vào nhánh integration; mã thực thi/test vẫn đúng 877e619. Các bước tiếp theo thuộc nghiệm thu live và quyết định merge/rollout. **Chưa merge main, push, deploy hoặc bật feature.** Chỉ merge main sau xác nhận tiếp theo theo yêu cầu; rollout và chất lượng là quyết định riêng.
