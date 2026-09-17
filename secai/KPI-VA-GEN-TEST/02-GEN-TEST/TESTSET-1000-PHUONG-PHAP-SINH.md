# Phương pháp sinh Bộ test nghiệm thu 1000 câu (khách quan · khoa học · đúng hợp đồng)

> Trả lời câu hỏi của Chủ đầu tư: *"Bộ 1000 câu được sinh như thế nào? Có khách quan không, hay tự ra đề tự chấm?"*
> Bằng chứng: `KPI-VA-GEN-TEST/02-GEN-TEST/` — bộ sinh `gen_testset.py`, cổng chất lượng `validate_testset.py`,
> tài liệu nguồn `NGUON-DU-LIEU.md`, bản ghi sinh `testset-v2/_meta/manifest.json`, dữ liệu `testset-v2/*.jsonl`.

## 1. Tóm tắt (một dòng)
Bộ 1000 câu được **sinh tự động, có seed cố định, KHÔNG dùng LLM để ra đề/đáp án**, lấy mẫu **phân tầng** từ KB CVE thật (Qdrant `cve_kb`), **loại trừ toàn bộ CVE đã dùng ở bộ KPI chính**, giữ đúng **cơ cấu hợp đồng R-EVAL-TESTSET (bucket 40/40/17/3, độ khó 40/30/20/10, mỗi CVE ≤ 1 lần)**, và **qua cổng kiểm chất lượng** trước khi dùng.

## 2. Bản ghi sinh khách quan — `testset-v2/_meta/manifest.json`
| Trường | Giá trị | Ý nghĩa |
|---|---|---|
| `sinh_boi` | `gen_testset.py` | Sinh bằng script tất định, không thủ công |
| `nguon` | `qdrant` (KB `cve_kb`, ~327k CVE) | Ground truth lấy thẳng từ payload CVE |
| `seed` | `7` | **Tái lập 100%** — chạy lại ra đúng bộ cũ |
| `use_llm` | `false` | **Đề & đáp án KHÔNG do model sinh** → không "tự ra đề" bằng AI |
| `exclude_pool` | `true`, `loai_tru_cve = 2176` | Loại 2176 CVE đã dùng ở bộ KPI chính → **độc lập** |
| `tong_so_cau` | `1000` | Đúng quy mô hợp đồng |
| `so_cve_dung` | `850` (distinct) | Mỗi CVE dùng ≤ 1 lần → **0 trùng CVE** (đã kiểm) |
| `so_template` | `43` | 43 khuôn câu hỏi tiếng Việt cho 8 năng lực |

## 3. Lấy mẫu phân tầng (stratified) — chống thiên lệch
Chiến lược trong `NGUON-DU-LIEU.md` (giữ nguyên `export_pool.py`):
- **Mức độ khai thác:** KEV (đang bị khai thác thật) **15%**.
- **Độ nghiêm trọng:** CRITICAL 25% · HIGH 35% · MEDIUM 30% · LOW 10%.
- **Theo năm:** 2017–2020 **20%** · 2021–2023 **40%** · 2024–2026 **40%** (bám 05 năm gần nhất theo hợp đồng).
- **Lọc chất lượng nguồn:** mỗi CVE phải có `cwe_refs` **và** `products` **và** mô tả **> 400 ký tự** (LOW nới ≥ 250) → loại CVE thiếu dữ liệu.

## 4. Cơ cấu đúng hợp đồng R-EVAL-TESTSET (số thực đã đếm trên `testset-v2/`)
- **Bucket 40/40/17/3:** `dinh_danh 400` · `khac_phuc 400` · `tinh_huong 170` · `ngoai_le 30` (2 nhóm sau gộp vào file `acceptance_indep_tinh_huong.jsonl` = 200).
- **Độ khó 40/30/20/10:** `de 388` · `trung_binh 291` · `kho 209` · `tu_duy 112`.
- **8 năng lực:** `cve_qa 590` · `translation 60` · `poc_search 60` · `sast 60` · `remediation 60` · `pentest 59` · `exploit 51` · `report 60`.
- **Ground truth khách quan:** 850 câu có `gold cve_id`, 970 câu có `gold cwe` — lấy thẳng từ KB payload, **không do model**.

## 5. Câu SAST có MÃ NGUỒN thật, nhiều ngôn ngữ
- 60 câu SAST kèm **tệp mã nguồn thật** tại `testset-v2/vuln-samples/` — **52 tệp `.py` + 8 tệp `.js`** (2 ngôn ngữ được hỗ trợ: **Python, JavaScript**).
- Mỗi câu trỏ đúng tên tệp (vd `ind_sast_000_cwe89.py`) và chấm theo `expected.must_find_cwe` (vd `CWE-89`) → đối chiếu mã CWE hệ thống phát hiện.
- Ví dụ tệp `ind_sast_050_cwe79.js` = XSS thật (`res.send("<h1>Xin chao " + name)`), `ind_sast_000_cwe89.py` = SQLi nối chuỗi.

## 6. Cổng kiểm chất lượng — `validate_testset.py`
Chạy `python3 validate_testset.py testset-v2` (exit 0 = ĐẠT). Kiểm: `valid_bucket`, `valid_capability`, `duplicate_id`, `duplicate_input` (trùng nội dung), `min_input_length`, `cve_format`, `grounded_cve` (CVE phải có trong pool KB), `tool_needs_cve` (câu khắc phục phải kèm `cve_id`), tối thiểu 5 mẫu/năng lực.

## 7. Tính độc lập — nói thẳng với Chủ đầu tư (không tô hồng)
- **Nguồn A (đang dùng, offline):** lấy từ chính KB hệ thống tra cứu → đo *"hệ thống có dùng ĐÚNG tri thức nó có hay không"*, nhưng **không tự phản bác** hoàn toàn lập luận "tự ra đề". Bù lại: đề **không do LLM sinh**, ground truth từ payload gốc, loại trừ CVE của bộ KPI chính.
- **Nguồn B (độc lập tuyệt đối):** `gen_testset.py --source public --feeds nvd,kev` → lấy đề từ **NVD / CISA KEV / Exploit-DB** (bên thứ ba). Dùng khi khách yêu cầu chứng minh không "tự ra đề tự chấm".
- **Nguồn C (khách chủ động):** `gen_testset.py --source customer --in bang-khach-hang.csv` → khách **tự nộp danh mục CVE**, hệ thống sinh đề theo đó.
→ Đúng tinh thần **R-EVAL-INDEP**: Chủ đầu tư có thể tự chọn nguồn độc lập, hệ thống chạy lại pipeline chấm; chấp nhận khi sai lệch ≤ 5% so với số công bố.

## 8. Lệnh tái lập (chạy trực tiếp trên máy)
```bash
cd KPI-VA-GEN-TEST/02-GEN-TEST
# sinh lại đúng bộ cũ (seed cố định) từ KB nội bộ:
python3 gen_testset.py --source qdrant --n 1000 --out ./testset-v2 --exclude-pool
# hoặc sinh bộ ĐỘC LẬP từ nguồn công khai (cần mạng + NVD_API_KEY):
python3 gen_testset.py --source public --n 300 --feeds nvd,kev --out ./testset-indep --exclude-pool
# kiểm chất lượng:
python3 validate_testset.py testset-v2      # exit 0 = ĐẠT
```
