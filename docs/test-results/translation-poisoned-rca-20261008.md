# RCA specialist dịch Hy-MT trả `503 POISONED` — 2026-10-08

**Kết luận ngắn:** `POISONED` đến từ cờ chặn trong bộ nhớ của worker MT (PID 71025), không phải marker file hay cleanup evidence. Gốc vận hành là **VRAM trống trên A100 chỉ còn 4.384 MiB, thấp hơn ngưỡng preload 8.192 MiB**. Xóa cờ mà không trả lại VRAM thì request đầu tiên sẽ bị chặn lại và cờ được đặt lại. Không có inference, restart, reset, sửa code hay deploy nào trong lượt điều tra này; mọi thao tác trên host là đọc.

## Evidence (read-only, 2026-10-08 ~01:42–01:46 UTC)

| Kiểm tra | Kết quả |
|---|---|
| `poisoned-cleanup.json` trong `/workspace/secai-gpu-specialists/` | Không tồn tại |
| Evidence `gpu-run-*` | 58/58 có `clean_exit_attested: true`; không có bản ghi dở dang |
| Source đang chạy (`source/deploy/gpu_stage/*.py`, `evals/multilang/gpu_specialist_server.py`) | SHA-256 trùng checkout, nên đọc code checkout là hợp lệ |
| Worker `mt` | PID 71025, chạy từ ~2026-10-04 14:18 UTC, dưới supervisor 70582; `GPU_MT_ON_DEMAND_ENABLED=1`; lock = `admission.lock` |
| Child llama-server còn sống / PID lạ trên GPU | Không; 3 PID trên GPU khớp đúng `expected_incumbents` |
| VRAM | used 76.666 MiB / 81.920 MiB, **free 4.384 MiB** (3 mẫu liên tiếp, ổn định) |
| Resident vLLM | LLM util 0,80 + embedding 0,05 + rerank 0,06 = 91% VRAM, đã chạy từ ~2026-10-02 |
| Log child MT 2026-10-04/05 (47 file) | Mỗi lần thấy **8.351 MiB free** lúc nạp, offload 33/33 layer, listening, cleanup sạch |
| Lần chạy child MT gần nhất của PID 71025 | 2026-10-05 03:42 UTC; không có child nào sau đó |

## Phân biệt các nguồn `POISONED`

Trong code có hai đường trả `POISONED`:

1. `owner.py:108-118` (`_check_history`): cần marker file hoặc evidence chưa attested. **Loại trừ**, vì không có marker và 58/58 evidence đã attested.
2. `coordinator.py:103,149`: cờ `GpuStageQueue.poisoned` trong bộ nhớ, đặt khi một stage ném `StageFailure(poison=True)` và không bao giờ tự xóa. **Đây là đường duy nhất còn lại** (mức chắc chắn: cao).

Mọi lỗi poison xảy ra sau khi spawn child (`GPU_OWNER` trong lúc chạy, `GPU_CLEANUP`) đều để lại evidence chưa attested (`owner.py:311`), mà ở đây không có. Vậy cờ được đặt bởi lỗi **trước khi spawn**: `GPU_RESERVE`, `GPU_OWNER` hoặc `GPU_SAMPLE` trong `check_preload`/`observe`.

Trạng thái GPU hiện tại khớp `GPU_RESERVE`: `check_preload` yêu cầu `free ≥ 6144 + 2048 = 8192 MiB` (`coordinator.py:60`), thực tế 4.384 MiB, thiếu **3.808 MiB**. Ở các lần chạy 10-04/05, child còn thấy 8.351 MiB free; khoảng 3,9 GiB đã bị chiếm kể từ đó.

**Mức chắc chắn:** cao cho "cờ in-memory của queue"; trung bình-cao cho "kích hoạt bởi `GPU_RESERVE` trước spawn". Chưa loại hẳn `GPU_SAMPLE` (nvidia-smi timeout 3 s) hay `GPU_OWNER` do PID lạ xuất hiện thoáng qua. Không có log nào ghi mã lỗi đầu tiên vì `access_log=False` và gateway gộp lỗi thành `MODEL_UNAVAILABLE`.

## Chưa xác định

- Tiến trình nào làm VRAM trống giảm từ ~8,3 GiB xuống ~4,4 GiB. Ba PID resident không đổi từ 10-04, nhưng không có lịch sử dùng VRAM theo thời gian.
- Thời điểm chính xác cờ bị đặt (không có log).
- OCR dùng cùng cơ chế admission nên có thể bị chặn tương tự; chưa kiểm chứng và không gửi request OCR.
- Service `reward.training_service` (PID 79531) dùng chung `admission.lock` và đã ghi 2 evidence ngày 10-06; không thấy nó giữ GPU lúc này, nhưng nên báo chủ sở hữu trước khi thao tác.

## Vì sao chưa sửa code / khôi phục

- Xóa cờ cần restart worker 71025 (supervisor sẽ tự dựng lại). Ràng buộc cấm restart để làm request pass, và việc này không giải quyết gốc vì preload gate vẫn fail.
- Trả lại ≥3.808 MiB VRAM nghĩa là đổi cấu hình resident vLLM (ví dụ hạ `--gpu-memory-utilization` của LLM) và restart LLM. Đây là thay đổi dịch vụ đang phục vụ RAG/chat, ảnh hưởng ngoài phạm vi dịch, và CLAUDE.md cấm hạ guard hoặc dừng resident để qua kiểm tra.
- Chưa có bằng chứng đủ để Sol sửa code: đề xuất bên dưới là cải tiến độ bền, không phải nguyên nhân đã chứng minh.

## Đề xuất (cần quyết định)

1. **Khôi phục vận hành (cần phê duyệt):** hạ VRAM resident để free ≥ 8.192 MiB có biên an toàn (cần ≥ +3.808 MiB), rồi restart đúng worker `mt` bằng supervisor (không đụng OCR/resident khác); sau đó Luna chạy **một** probe có timeout. Chỉ nếu probe trả bản dịch hợp lệ mới chạy 36 request.
2. **Cải tiến code (tùy chọn, sau khi chọn 1):** `GPU_RESERVE` ở `check_preload` xảy ra trước khi có child nên không cần poison vĩnh viễn; trả `503 GPU_RESERVE` (retryable) sẽ biến cạn VRAM tạm thời thành lỗi tự hồi phục và để lại mã lỗi chẩn đoán. Giữ nguyên poison cho `GPU_OWNER`, `GPU_SAMPLE`, `GPU_CLEANUP`. Cần test hồi quy và review độc lập (Luna/Astra) trước khi triển khai.
3. Thêm log mã `StageFailure` đầu tiên (không chứa dữ liệu người dùng) để lần sau xác định ngay nguyên nhân.

## Trạng thái đo chất lượng

Chưa chạy. Theo ràng buộc, không gửi inference khi specialist còn `POISONED`; 36 request cho 18 hướng vẫn là `NOT_RUN`. Không có tuyên bố nào về availability hay chất lượng v3 ngoài báo cáo `translation-live-diagnostic-20261007.md`.

---

## Cập nhật: khôi phục và đo lại (2026-10-08, sau phê duyệt của người dùng)

### Đã thực hiện trên host A100 (192.165.134.28:10963)
1. Sao lưu lệnh khởi động LLM và `config.json` vào `/root/llm-resize-20261008/` (thư mục 0700, chứa key nên chỉ ở host).
2. `bootstrap.py stop`: drain hai worker MT/OCR; ba resident giữ nguyên; evidence vẫn 58/58 attested.
3. Restart **chỉ LLM Cyber-F1** với `--gpu-memory-utilization 0.80 → 0.74`, dưới `flock admission.lock`, có rollback tự động (script `swap.py`). Thời gian gián đoạn LLM: ~2 phút 40 giây (02:23:35 → 02:26:18 UTC); probe có xác thực trả 200. VRAM trống 4.384 → 14.828 MiB, sau tải thật ổn định ở **12.608 MiB**.
4. PID host của LLM đổi (3203145 → 3759513) nên `bootstrap.py boot` chụp lại baseline incumbent dưới shared lock rồi dựng lại worker. Cờ POISONED trong bộ nhớ biến mất cùng worker cũ.
5. Không thay model, không đụng guard, không xóa evidence, không reset marker (không có marker nào).

### Kết quả đo (corpus FLORES-101 DEV công khai, 2 nhóm nguồn × 18 hướng)
- Probe trực tiếp: HTTP 200, model `tencent/Hy-MT2-7B`, 8,7 s.
- Availability: **36 gửi / 36 HTTP 200 / 0 lỗi / 0 timeout / 0 NOT_RUN**; 95/95 evidence attested sau lượt chạy; không còn child llama-server; `GPU_RESERVE`/`POISONED` không xuất hiện. Độ trễ 9,1–11,5 s (trung vị ~9,7 s), đo khi GPU còn tải LLM, không phải SLA.
- Chất lượng ngữ nghĩa (Astra, rubric đóng băng 02:28:19Z trước inference, SHA `ac97ae9e…492b`): **30 PASS_OBSERVED, 4 FAIL, 2 UNCERTAIN** trên 36 mẫu; nếu chấp nhận “hàng chục tỷ” là cách nói lỏng thì 32/2/2.
  - FAIL: zh>vi, ja>vi, ko>vi (nhóm 0017) nới “vài tỷ” thành “hàng chục tỷ”, ja>vi còn sai số lượng ngân hàng; de>vi (nhóm 0649) dịch “überholen”/“Brunnen” thành hành động lái xe/xây giếng.
  - UNCERTAIN: fr>vi (số “14” đặt sai chỗ), vi>de (“Sprinkler” thay cho đài phun nước).
  - Tất cả FAIL nằm ở hướng X>vi; mọi hướng vi>X (trừ một UNCERTAIN vi>de) đạt.
  - Cảnh báo gateway `SOURCE_LANGUAGE_UNCERTAIN` (36/36) và `TARGET_LANGUAGE_UNCERTAIN` (26/36) không tương ứng lỗi ngôn ngữ thật; có vẻ là heuristic theo chữ viết Latin.
- Attribution: Hy-MT2-7B Q4_K_M (hash file model khớp pin, 33/33 layer GPU). Response v3 không có trường model nên đây là bằng chứng gián tiếp, không gắn theo từng request. Không gán cho DuyTa.
- Artifact private: `data/evals/live-quality-20261008/` (`review-completed.jsonl` SHA `5f744c13…0693`, `astra-summary.md`, raw, host-runtime-evidence).

### Giới hạn
36 mẫu từ 2 câu nguồn, dữ liệu DEV đã lộ, không holdout, review có hỗ trợ AI, không đo literal kỹ thuật (CVE/IP/URL/code), không p95/SLA. **Không đạt hay trượt gate tổng thể**; nghiệm thu đầy đủ vẫn cần ≥30 nhóm nguồn × 18 hướng × 3 lượt. Chưa production-ready.

### Còn mở / rủi ro
1. **Bản vá code chưa deploy.** Host vẫn chạy code cũ đã attest. Gói `runtime.tar.gz` thuộc repo `secai-serving-vast` (riêng, đang có thay đổi `lib.sh` chưa commit) cần dựng lại + manifest + installer `--reuse-artifacts`. Diff và test (14 test mới, 164 test liên quan đạt, Astra APPROVE) nằm ở working tree gateway, chưa commit. Cho tới khi deploy, một lần thiếu VRAM nữa vẫn latch `POISONED` đến khi restart worker.
2. **VRAM drift.** Trước đây trống giảm từ ~8,7 GiB (smoke 10-04) xuống 4,4 GiB sau ~4 ngày ở util 0,80; hiện margin so với ngưỡng 8.192 MiB là ~4,4 GiB. Nên theo dõi `nvidia-smi` định kỳ; nếu cần thêm biên, hạ util LLM thêm một bước.
3. **`reward.training_service` (PID 79531) đọc `config.json` một lần lúc khởi động**, nên đang giữ danh sách incumbent cũ. Job tiếp theo của nó sẽ bị chặn `GPU_OWNER` cho tới khi service đó được restart; tôi không restart vì không thuộc phạm vi. Hai job hiện có đều đã `succeeded`.
4. OCR dùng cùng gate; worker OCR đã dựng lại và qua readiness nhưng **chưa smoke OCR**.
5. Boot hook: nếu host reboot, `on_start_cyber_f1.sh` dùng `LLM_GPU_UTIL` mặc định 0,80 và sẽ lại làm VRAM trống thấp. Cần đặt `LLM_GPU_UTIL=0.74` (hoặc thấp hơn) trong cấu hình boot của instance trước khi reboot; chưa làm.
6. Rollback LLM: chạy lại `swap.py 0.80 <pid-api-server>` (cmdline gốc lưu ở `/root/llm-resize-20261008/`), rồi `bootstrap.py stop` → `boot` để chụp lại baseline.
