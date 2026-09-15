# Quy ước file minh chứng theo dòng — `evidence/dong-<số dòng>.md`

Thay cho cách viết tay tóm tắt trước đây: **mỗi dòng trong sheet nghiệm thu = 1 file md**, sinh từ kịch bản nghiệm thu ở **cột F** ("Minh chứng/kịch bản chứng minh").

- Sheet: `13fIE_flxVnNu2jfteNAZWsfnC-MwUFEj`, tab `gid=1051641017`, phần mô-đun trí tuệ nhân tạo — **dòng 491 → 572**, trong đó **62 dòng có nội dung cột F**.
- Dữ liệu đã trích sẵn: `scripts/nghiem-thu-rows-ai.json` (mỗi phần tử: `dong`, `tt`, `dvt`, `thong_so_ky_thuat`, `kich_ban` = cột F, `ghi_chu`, `cac_buoc`).
- Tên file: **`dong-<số dòng>.md`** (vd `dong-497.md`). Đặt trong `evidence/`, API `/ai/evidence/*` tự đọc.
- Hồ sơ gốc: `/home/ict/AIEngines/secai/HO-SO-GOC-NGHIEM-THU-AI/` (4 thư mục `01. Ho_so_huan_luyen/`, `02. Bo_build_finetuning/`, `03. Kpi_results/`, `04. Huong_dan_cai_dat/`).

## Cấu trúc file md

File evidence chỉ ghi **KẾT QUẢ thực hiện**, KHÔNG chép lại kịch bản nghiệm thu (kịch bản đã có trong sheet, chép lại là thừa).

```
<dòng 1: nội dung chỉ tiêu — nguyên văn cột "Thông số kỹ thuật">
## Kết quả thực hiện
<với mỗi file kịch bản yêu cầu mở: đường dẫn + số dòng + trích nguyên văn nội dung thật>

## Kết luận
<một câu: đối chiếu được / không đối chiếu được, vì sao>
```

**Dòng 1 = tiêu đề chỉ tiêu**, lấy NGUYÊN VĂN dòng đầu của ô "Minh chứng/kịch bản chứng minh" (tiêu đề mục, trước phần `- Điều kiện`) — mỗi dòng một tiêu đề riêng. Với các dòng lead-in (ô bắt đầu bằng `Điều kiện`/`Kịch bản`/`(bên trên)`…), lấy tiêu chí ở cột "Thông số kỹ thuật". Đây là khoá mapping cho `GET /ai/evidence/match` (EQUALS). Nhiều dòng có thể cùng 1 tiêu chí (ô gộp hoặc 1 KPI tách nhiều dòng đo) → `match` trả về tất cả file khớp.

## Quy tắc bắt buộc

1. **Chỗ nào kịch bản bảo mở file → phải mở thật trong hồ sơ gốc và trích nguyên văn đúng khoảng dòng được nêu.** Trích trong code block, ghi rõ đường dẫn + khoảng dòng thật, ví dụ:
   ~~~
   **`01. Ho_so_huan_luyen/finetuning/reports/finetuning-summary.md`** — dòng 17–23:
   ```
   <nội dung thật, copy nguyên văn>
   ```
   ~~~
   Nếu số dòng trong kịch bản lệch so với file thật (file đã đổi), trích đúng đoạn nội dung mà kịch bản đang nói tới và ghi chú số dòng thực tế.
2. **KHÔNG bịa nội dung.** File nào không có trong hồ sơ gốc thì ghi rõ, ví dụ: *"`code/restore.sh` thuộc gói mã nguồn/triển khai, không nằm trong hồ sơ gốc phần AI (4 thư mục 01–04)."* Đã biết trước 15 tham chiếu thuộc nhóm này: `code/restore.sh`, `env.example`, `code/source/docker-compose.deploy.yml`, `ingest/**`, `bm25_sink.py`, `neo4j_sink.py`, `qdrant_sink.py`, `taxonomy.py`, `exploitdb.py`, `nvd_feed.py`, `cve_to_chunk.py`, `watermark.py`, `gen_answers.py`, `bench_answers_concurrent.py`, `run_eval_all.sh`.
3. **Kịch bản thao tác trên giao diện** (mở `Trợ lý ảo an toàn thông tin`, nhập câu hỏi, bấm `Gửi`…) thì **không có file để trích**: giữ nguyên văn kịch bản + ghi rõ đây là kịch bản demo trực tiếp trên giao diện, nêu endpoint tương ứng nếu có (`/ai/chat`, `/ai/translate`, `/ai/exploit`, `/ai/kpi-bench/...`).
4. **Không thêm nhận xét/diễn giải của người viết.** Chỉ: kịch bản (nguyên văn) + nội dung trích từ hồ sơ gốc + một câu kết luận đối chiếu được hay không. Không đánh giá "tốt/đạt kỳ vọng", không suy diễn thêm.
5. Số liệu phải **copy đúng** từ hồ sơ gốc, không làm tròn lại, không diễn giải lại.
