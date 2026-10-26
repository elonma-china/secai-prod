# Tối ưu tốc độ + độ chính xác dịch tệp/ảnh (11/10/2026)

Mục tiêu người dùng: **~1-3 giây/trang** (tài liệu 20 trang <= 60 giây), giữ nguyên thông tin (mật khẩu, IP, email, ...), dịch được mọi loại tệp/PDF, model luôn nạp sẵn (không tắt), dùng tối đa 1 GPU A100 80GB.

**Trạng thái: đo trên STACK THỬ NGHIỆM CÔ LẬP, CHƯA deploy lên production.** Không có thay đổi nào chạm container `secai-translation-*`, `secai-docker-gateway` hay cổng 8131/18185.

## Kết quả đo (qua API `/ai/v2/translate/*`, 1x A100 80GB, instance Vast 55235250)

Mốc thời gian chính xác (poll 0,3 giây; hàng đợi < 1 giây), cấu hình cuối ngày 11/10/2026:

| Đầu vào | Baseline (serving on-demand, dịch tuần tự) | Sau tối ưu |
|---|---|---|
| DOCX 20 trang (221 đoạn) | ~1.370 giây (ước tính từ 39/221 đoạn; job bị dừng) | **~4,5 giây (0,22 giây/trang)** |
| DOCX/PDF/XLSX ngắn (1 trang) | — | **2,3–2,6 giây** |
| PDF có lớp chữ 4 trang | chưa hỗ trợ | 4,2 giây (~1,1 giây/trang), literal 7/7 |
| TXT 1000 từ | — | 3,5 giây |
| PDF scan 10 trang (2 trang trắng + 1 nhiễu) | chưa hỗ trợ | 18,3 giây (~1,8 giây/trang) |
| PDF scan 20 trang | chưa hỗ trợ | ~36 giây (~1,8 giây/trang) |
| PDF scan 1 trang | chưa hỗ trợ | 6,7 giây |
| Ảnh dày chữ A4 (1 trang) | 36,4 giây, literal 3/7 | **9,6–12,1 giây** (OCR 6–8 giây), literal 7/7 ở 16/20 ảnh |
| Ảnh ngắn (1 trang) | — | 7,1 giây (OCR 4,1 giây) |
| XLSX 300 dòng (1.807 ô) | chưa hỗ trợ | ~18 giây (trước khi bỏ commit SQLite/ô trùng; đo lại) |

Kết luận so với tiêu chí ~1-3 giây/trang: **đạt** cho tài liệu chữ (DOCX/PDF/XLSX/TXT, ngắn và dài) và PDF scan nhiều trang (~1,8 giây/trang). **Chưa đạt** cho ảnh/PDF scan 1 trang (7–12 giây): OCR bằng VLM 2B giải mã tuần tự ~700 token mỗi dải là phần chính; cách còn lại là OCR model nhanh hơn/nhỏ hơn, hoặc dịch song song với OCR từng dải (pipeline), chưa làm.

Vòng kiểm 30 mẫu ngẫu nhiên (15 dài + 15 ngắn, `scripts/loop30.py`): vòng 1 (seed 20261011) **27/30** thành công, literal đủ 21/30 (3 lỗi là chunk CJK quá dài); vòng 2 (seed 20261012) **30/30**, literal đủ 25/30, 0 marker lạ, 0 đoạn rỗng — số đo vòng 2 TRƯỚC khi bỏ commit SQLite và chunk thích ứng nên thời gian cao hơn bảng trên. Vòng 3 (seed 20261013, cấu hình cuối): **29/30** thành công, literal đủ 23/30, 0 marker lạ/đoạn rỗng. Tài liệu chữ: DOCX dài 0,7–1,0 giây/trang, XLSX dài 1,3–1,4, PDF chữ dài 1,0–1,6, tài liệu ngắn 2,3–3,3 giây; 17/30 mẫu vượt 3 giây/trang, tất cả là ảnh/PDF scan 1–3 trang (OCR) trừ 2 tài liệu ngắn (PDF chữ Trung 4,8 giây, DOCX Ả Rập 3,3 giây). **1 lỗi mới:** `png-short-zh-Hans` (ảnh chữ Trung -> Việt) `TOKEN_INTEGRITY_FAILED` sau 14 giây — lớp kiểm literal của dịch vụ khi OCR chữ Trung dính literal ASCII; chưa điều tra. **1 ngoại lệ tốc độ:** `pdf-scan-long-ar` 26,8 giây (dải Ả Rập bị model lặp, thử lại tốn thêm).

Chất lượng (chrF theo gold `04-test-data.json`, 18 hướng, BF16): trung bình **69,9** (về tiếng Việt 72,2; từ tiếng Việt 67,6; thấp nhất vi->zh 45,4 do chấm theo ký tự). **Chưa có số của bản Q4 cũ để so sánh.**

Hồi quy 20 ca P0 đợt 1 (`fixtures/formats-extra`, `scripts/p0run.py`): mọi ca giữ-nguyên-thông-tin PASS (UC-C02 32/32, C01 60/60, C07 18/18, C24 100/100, A01 33/33, E10 7/7, A08, B08, B10, B13, B02, A07, E18, E07); 4 ca tệp độc hại bị từ chối đúng (`ARCHIVE_LIMIT_EXCEEDED`, `UNSAFE_DOCUMENT`). **Lỗi còn tồn: UC-C12** (JSON dịch xong không còn parse được: model đổi `"` thành `“ ”`). Ghi nhận một lần UC-B10 mất 121 giây (treo thoáng qua 1 lệnh gọi MT, nghi tunnel SSH của môi trường thử; chạy lại 1,4 giây; đã đặt timeout 30 giây/lệnh gọi MT).

## Những gì đã thay đổi (và vì sao nhanh hơn)

1. **Dịch tuần tự -> song song** (`patches/service.py.patch`, `worker.py.patch`): `translate_blocks` dịch từng khối/chunk một (mỗi lệnh gọi MT ~4-6 giây). Nay chạy song song có giới hạn (`TRANSLATE_V2_MAX_LLM_CALLS`, dùng 128), giữ thứ tự, retry, kiểm literal, kiểm chất lượng như cũ. Ô/đoạn giống hệt chỉ dịch **một lần**.
2. **Serving MT: GGUF Q4 nạp theo request -> vLLM BF16 luôn nạp** (`serving/fast_mt_vllm.py`): giữ NGUYÊN `TranslationRuntime` đã duyệt (prompt, giới hạn token, kiểm EOS); chỉ thay backend: `/apply-template`, `/tokenize` dùng tokenizer HF cục bộ (đã kiểm trùng khớp byte với llama.cpp trên 4 mẫu, gồm literal/CJK/xuống dòng), `/completion` dùng vLLM `/v1/completions`. llama.cpp bị giới hạn ~1.085 token/giây (GPU chỉ 67% tải, nhiều replica không giúp); vLLM đạt ~35 request/giây ở 128 đồng thời. BF16 không lượng tử hóa nên không tệ hơn Q4 về nguyên tắc (chưa có số so).
3. **Serving OCR: transformers batch=1 -> vLLM Qwen3-VL-2B luôn nạp** (`serving/fast_ocr_vllm.py`): cùng hợp đồng `POST /v1/ocr`, cùng prompt (đọc nguyên văn từ module OCR đã duyệt), nhận nhiều request song song, ngân sách pixel 3 Mpx (bản cũ bóp xuống 1 Mpx làm chữ nhỏ bị đọc sai: literal 3/7 -> 6/7). **Cổng kiểm chữ**: trang trắng không gọi model; kết quả ngắn/lặp thì hỏi lại "ảnh có chữ đọc được không" — trước đó model **bịa ra mã CVE** trên trang trắng và lặp vô hạn trên trang nhiễu làm cả job fail.
4. **Hỗ trợ PDF** (có lớp chữ, scan, trộn, nhiều cột, trang xoay, trang trắng) và **XLSX** (nhánh `feat/translate-pdf-support-2026-10-10`, commit `3c6225d`, `7b27313`; patch trong `patches/`).
5. **Không mất dữ liệu**: ô không dịch (số, ngày, công thức, chuỗi không có chữ như `192.168.10.25:8443`) vẫn xuất ra dưới dạng khối `literal` đúng vị trí (trước đó biến mất khỏi bản dịch); token ASCII đơn lẻ có chữ số/ký tự đặc biệt (mật khẩu, khóa, hostname) được giữ nguyên, không đưa qua model (trước đó `P@ss!w0rd...` bị dịch thành "Mật khẩu...").
6. **Sửa lỗi gián đoạn**: `unable to open database file` là hết file descriptor khi chạy song song (mỗi lệnh gọi mở 1 kết nối SQLite mỗi 0,5 giây). Nay 1 watcher dùng chung cho cả job; container chạy `--ulimit nofile=65536`.
7. **Bỏ cổ chai ghi tiến độ**: mỗi lệnh dịch và mỗi khối đều commit SQLite (~50 lần/job, xếp hàng trên đĩa host gần đầy) = +3 giây cố định. Nay ghi tối đa ~2 lần/giây/giai đoạn (vẫn ghi khi đổi giai đoạn/khi xong): docx 20 trang 10,3 -> 4,5 giây.
8. **Chunk thích ứng**: độ trễ bị chi phối bởi chunk DÀI NHẤT (giải mã tuần tự ~100 token). Tài liệu nhỏ dùng chunk >= 160 ký tự (đủ ~96 chunk song song), tài liệu lớn giữ tối đa (384); chunk chỉ tách ở ranh giới câu. Nguồn CJK dùng chunk <= 160 ký tự (bản dịch tiếng Việt dài ~2-3 token/ký tự; chunk 384 làm đầu ra vượt 512 token -> 502 -> `TRANSLATION_UNAVAILABLE`). Tài liệu ngắn 3,3 -> 2,4 giây.
9. **OCR chia dải + phóng**: trang cao một cột được cắt ở khoảng trắng giữa các dòng thành tối đa 12 dải, mỗi dải phóng ~x2 (đến ~4,5 Mpx) và OCR song song; trang 2 cột không cắt (giữ thứ tự đọc). Thử nghiệm: phóng x2 sửa lỗi OCR rớt ký tự `&` trong `$%^&*()_+`; 12 dải: trung vị 5,7 giây, tối đa 6,6 giây, 16/20 ảnh dài đủ 7/7 (6 dải: 6,9 giây, tối đa 20,2 giây, 12/20). Dải bị model lặp: thử lại 1 lần với phạt lặp, cắt đuôi lặp, không làm hỏng cả job.

## Quyết định thiết kế cần người chịu trách nhiệm duyệt

- **Bỏ qua khóa admission của bundle trên instance chuyên dụng.** Bundle dùng một khóa chung nên MT và OCR không thể cùng resident trên 1 GPU. Stack này chạy MT (vLLM) và OCR (vLLM) ngoài bundle, không qua lớp admission/lock/checksum/cleanup-veto. Chỉ dùng cho instance KHÔNG có LLM/embed/rerank. Muốn đưa vào bundle chính thức: làm "nhóm resident dùng chung một khóa" (xem câu trả lời trong lịch sử) trong repo `secai-serving-vast`.
- **Bản vá CUDA compat**: config bundle ép `LD_LIBRARY_PATH` có `/usr/local/cuda-13.0/compat` (libcuda 580.95) trong khi driver host là 580.173 -> torch/llama.cpp không khởi tạo được CUDA ("unsupported display driver"). Đã bỏ mục compat khỏi `config.json` trên máy thuê; installer ghi đè lại mỗi lần chạy (`apply_mode.sh` tự bỏ lại). **Chưa sửa trong bundle của repo.**
- Giới hạn chunk/ô: để XLSX thực tế dùng được đã nâng `TRANSLATE_V2_MAX_CHUNKS=20000`, `XLSX_MAX_CELLS=20000`, `MAX_BLOCKS=100000` trong stack thử nghiệm (mặc định 600/300/10000 sẽ từ chối bảng tồn kho 300 dòng). Cần chốt giá trị cho production.
- `TRANSLATE_V2_UPLOAD_RATE_PER_MINUTE=600` chỉ để chạy test; production mặc định 5 upload/phút/IP.
- Mã trong image `translate-noacronym-20261010` KHÁC working tree của repo, nên các bản vá trong `patches/` là so với mã **trong image**. Cần chọn nhánh nền rồi port (nhánh PDF/XLSX đã có commit riêng trên nền repo HEAD).

## Còn tồn / chưa làm

- `TOKEN_INTEGRITY_FAILED` khi OCR ảnh chữ Trung có literal dính chữ Hán (vòng 3, png-short-zh-Hans): cần điều tra (OCR đọc sai literal hay MT đổi dấu ngoặc placeholder cạnh CJK).
- ~~UC-C12~~ ĐÃ SỬA 2026-10-11: `.txt` parse được JSON thì chỉ dịch giá trị chuỗi (khóa/cấu trúc giữ nguyên, bọc lại bằng `json.dumps`), JSON một dòng vẫn parse được (chèn xuống dòng giữa các token). Patch: `patches/txt-json-values.patch` + `patches/service-json-affix.patch`. YAML (UC-C13) đã đạt sẵn. Hồi quy P0 lần 2: 16/16 PASS, 4 tệp độc hại bị từ chối đúng.
- ~~TOKEN_INTEGRITY_FAILED ở ảnh chữ Trung~~ ĐÃ SỬA 2026-10-11: `_occurs_as_token` coi `:` đứng trước (nhãn `账号:svc_x`, OCR Trung dùng `:` ASCII không khoảng trắng) và `:cổng` đứng sau (`192.168.10.25:8443`) là mảnh của token khác → sai. Patch `patches/occurs-as-token-colon.patch`. `png-short-zh-Hans` 3/3 lần lỗi → 2/2 thành công. Còn nhỏ: model dịch nhãn `Key:` thành "Mật khẩu:" (giá trị vẫn nguyên).
- OCR literal chưa 100%: còn ~20% ảnh dài rớt đúng 1 ký tự `&` trong `$%^&*()_+` (hay gặp ở JPG, tiếng Ả Rập/Hàn). Hướng: model OCR lớn hơn (Qwen3-VL 4B/8B; còn ~12 GB VRAM) hoặc đọc lại riêng dòng nghi là mật khẩu ở độ phóng lớn hơn; cần quyết định vì CLAUDE.md ghim Qwen3-VL-2B.
- Ảnh/PDF scan 1 trang 7–12 giây (OCR là phần chính), chưa đạt ngưỡng 3 giây/trang cho ca 1 trang.
- `.doc`: ĐÃ CHẠY trên stack thử nghiệm (2026-10-11): `run_stack.sh` gắn docker.sock + CLI + thư mục tạm cùng đường dẫn host cho gateway/worker; 3 tệp `.doc` 4,8–6,0 giây, đầu ra tương đương bản docx (0 token mất). **Lưu ý production:** worker/gateway production KHÔNG có docker → `.doc` trả `DEPENDENCY_UNAVAILABLE`. Gắn docker.sock = quyền root tương đương; đề xuất an toàn hơn: nhúng LibreOffice vào image worker hoặc dịch vụ converter sidecar. Cần quyết định.
- OCR/MT không tự khởi động lại khi VM reboot (chạy bằng `serving/start_*.sh`, chưa có supervisor/onboot).
- Chưa nối production vào instance mới; token bootstrap khác token trong env container hiện tại.
- Chưa so chất lượng với Q4, chưa chạy 169 ca (mới 20 ca P0), mới chạy vòng 30 mẫu số 1.

## Cách chạy lại

Máy: instance Vast 1xA100 80GB có `/workspace/hymt2-bf16` (HF `tencent/Hy-MT2-7B`) và `/workspace/secai-translation-ocr/qwen3vl-model`. Token private nằm trong `/home/ict/secai-opt/{mt,ocr}.token` (KHÔNG đưa vào Git).

```sh
# 1) trên VM: MT (vLLM :18770 + service :18868) rồi OCR (vLLM :18771 + service :18083)
bash serving/start_vllm.sh ; bash serving/start_ocr_vllm.sh
# 2) trên host: tunnel 28083->18083, 28868->18868 (ssh -L), rồi stack cô lập (cổng 18285)
scripts/run_stack.sh TRANSLATE_V2_MAX_LLM_CALLS=128 TRANSLATE_V2_PDF_OCR_CONCURRENCY=16 TRANSLATE_V2_MAX_OCR_CALLS=4 \
  TRANSLATE_V2_XLSX_MAX_CELLS=20000 TRANSLATE_V2_MAX_CHUNKS=20000 TRANSLATE_V2_MAX_BLOCKS=100000 TRANSLATE_V2_UPLOAD_RATE_PER_MINUTE=600
# 3) đo
python3 scripts/bench.py --label tX --cases docx20,image,pdfscan --repeat 3
python3 scripts/p0run.py --label p0X            # 20 ca P0 + check_output
python3 scripts/loop30.py --seed N --label loopN # 1 vòng 30 mẫu (15 dài + 15 ngắn)
python3 scripts/quality.py --label qX            # chrF theo gold
```
(`scripts/*.py` mong đợi thư mục làm việc `/home/ict/secai-opt`; chỉnh đường dẫn nếu chuyển chỗ.)

## Cập nhật 2026-10-11 (đợt 2)
- **Sẵn sàng ngay, không warm-up:** `serving/boot.sh` dựng MT + OCR (vLLM) rồi làm ấm bằng request thật ở nhiều mức đồng thời (MT 1/8/64/128, OCR 1/4/16) TRƯỚC khi ghi `/workspace/fastmt/READY`; gắn vào `/root/onstart.sh` của instance (bản cũ: `/root/onstart.sh.bak-bundle`). Đã thử khởi động nguội đầy đủ (dừng rồi dựng lại cả hai vLLM) tới READY; request đầu tiên sau đó: tệp ngắn 2,6 s, ảnh ngắn 8,0 s (bằng lúc ấm — không phạt nguội).
- **Treo 30 s ngẫu nhiên (PDF tiếng Nga 3,9 s ↔ 31 s):** nguyên nhân là kết nối qua tunnel SSH của môi trường thử bị đứng (~1/360 lệnh gọi; chạy 720 lệnh ngay trên VM không lần nào treo). Vá `patches/specialist-hedge.patch`: sau `4 + len/40` giây chưa có phản hồi thì gửi thêm 1 lệnh giống hệt song song, lấy kết quả đến trước → lần treo chỉ tốn ~2 s (5,8 s thay vì 31 s). Có lợi cả ở production (kết nối treo).
- **OCR ảnh ngắn:** (1) cho phép cắt dải cả ảnh NGANG một cột (`OCR_WIDE_STRIPS=1`); (2) cổng "có chữ không" chỉ chạy khi dải <60 ký tự (trước: <200, nên mỗi dải ngắn tốn thêm 1 lệnh gọi nối tiếp). Trên VM OCR ảnh ngắn 1,4 s; qua tunnel thử 2,7 s (3,3 s trước). Độ chính xác không đổi: 16/20 ảnh dài đủ 7/7 (4 ca còn lại vẫn rớt `&` trong mật khẩu); P0 E07/E18/E10 PASS. Ảnh dài tối đa 6,6 s (trước 12,5 s).
- **Tồn:** OCR rớt `&` (4/20) chưa giải quyết; số đo ảnh 1 trang ở stack thử còn gồm ~1,3 s overhead tunnel + ~2 s dịch.

## Converter `.doc` chạy riêng (2026-10-11)
- `serving/doc-converter/` (Dockerfile + `server.py`): dịch vụ HTTP bọc LibreOffice headless, `POST /convert` (Bearer token) nhận `.doc`, trả `.docx`. Mỗi yêu cầu có thư mục + profile LibreOffice riêng (song song được, mặc định 4), timeout 60 s, macro tắt, giới hạn 25 MB, kiểm chữ ký OLE. Chạy container: `--read-only --cap-drop ALL --no-new-privileges --memory 3g --pids-limit 256 --tmpfs /tmp`, nên đặt trong mạng nội bộ không ra Internet.
- Worker/gateway cấu hình `TRANSLATE_V2_CONVERTER_URL` + `TRANSLATE_V2_CONVERTER_TOKEN` (patch `patches/doc-remote-converter.patch`); bỏ trống thì vẫn dùng `docker run` cũ. Stack thử nghiệm KHÔNG còn gắn docker.sock.
- Đo: chuyển 1,3–1,5 s/tệp; dịch `.doc` 4,1 s (ngắn), 5,1 s (dài), 5,3 s (báo cáo pentest); nội dung tương đương bản docx (0 token mất); `.doc` giả (không phải OLE) bị từ chối `FILE_TYPE_MISMATCH`.
- Để dùng ở production: dựng image `secai-doc-convert-svc`, chạy sidecar, đặt hai biến môi trường trên cho gateway VÀ worker (gateway cũng kiểm converter lúc nhận tệp). Chưa triển khai.

## Sửa OCR rớt/đảo ký tự đặc biệt (agent A, 2026-10-11)
- Hai cơ chế: (1) model 2B rớt `&` / đổi `0`→`O` khi dải có cả đoạn văn dài; (2) ảnh RTL (ar) đẩy đuôi mật khẩu `^&*()_+` lên đầu dòng và lật ngoặc.
- Bản sửa `serving/fast_ocr_amp.py` (hàm `refine_special`): chỉ khi dải có token nghi ngờ (≥8 ký tự, ≥4 ký tự đặc biệt) mới cắt riêng dòng, phóng ×2/×3, bỏ phiếu giữa các lần đọc, với RTL đưa về thứ tự logic; lỗi thì giữ kết quả gốc; tắt bằng `OCR_REREAD=0`. Không hardcode chuỗi.
- Kiểm độc lập bằng `fixtures/tools/ocr_special_char_check.py` trên bản thử: **40/40 ảnh đủ 7/7 (PASS 1b.7)**; trước đó ~16/20 ảnh dài, 17/20 ảnh ngắn. Thời gian OCR +0,1–0,4 s; hồi quy trang trắng/nhiễu (15 trang) không bịa chữ. Báo cáo gốc: `results/agentA-report.md`. CHƯA áp vào cổng 18083 (chờ agent B đo xong để không làm nhiễu số đo).
