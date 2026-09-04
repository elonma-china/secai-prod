# Nguồn dữ liệu để sinh bộ test

Ba đường lấy dữ liệu, chọn theo mục đích. Có thể **trộn** cả ba trong một đợt nghiệm thu.

| | Nguồn | Độc lập với KB? | Cần mạng? | Ground truth | Dùng khi |
|---|---|:--:|:--:|---|---|
| **A** | Qdrant `cve_kb` (KB nội bộ) | ❌ Không | Không | Chính xác nhất — lấy thẳng payload | Cần bộ lớn (1000 câu), nhanh, offline |
| **B** | NVD · CISA KEV · Exploit-DB | ✅ Có | Có | Từ bên thứ ba | Khách nghi ngờ "tự ra đề tự chấm" |
| **C** | Bảng CSV/XLSX khách hàng | ✅ Có | Không* | Khách chốt | Khách có danh mục CVE riêng |

`*` chỉ cần mạng khi bật `--enrich` để bổ sung trường còn thiếu.

---

## A · KB nội bộ — Qdrant `cve_kb`

```bash
python3 gen_testset.py --source qdrant --n 1000 --out ./testset-v2 --exclude-pool
```

Lấy mẫu **phân tầng** (giữ nguyên chiến lược của `export_pool.py`):
KEV 15% · severity CRITICAL 25 / HIGH 35 / MEDIUM 30 / LOW 10 ·
năm 2017-2020 20% / 2021-2023 40% / 2024-2026 40% ·
lọc chất lượng: phải có `cwe_refs` + `products` + mô tả > 400 ký tự (LOW nới xuống 250).

> **Bắt buộc dùng `--exclude-pool`.** Cờ này loại mọi CVE đã xuất hiện trong bộ nghiệm thu
> hiện hành (1000 câu active + 200 câu reserve + pool 2000 CVE sinh ra chúng) → thoả điều kiện
> *"không trùng bộ KPI chính"* của quy trình `R-EVAL-INDEP`. KB có ~327k CVE nên vẫn thừa dữ liệu.

**Hạn chế cần nói thẳng với khách:** đề lấy từ chính kho tri thức mà hệ thống tra cứu.
Nó đo được *hệ thống có dùng đúng tri thức nó có hay không*, nhưng **không** phản bác được
lập luận "tự ra đề tự chấm". Muốn phản bác thì dùng nguồn B.

Khác bản gốc: `export_pool.py` chạy qua `ssh secai`; bản này gọi HTTP thẳng tới `QDRANT_URL`
(mặc định `http://localhost:6333`) nên chạy được ở bất kỳ máy nào thấy Qdrant.

---

## B · Nguồn công khai

```bash
python3 gen_testset.py --source public --n 300 --feeds nvd,kev --out ./testset-v2 --exclude-pool
```

| Feed | Nguồn | Ghi chú |
|---|---|---|
| `nvd` | `https://services.nvd.nist.gov/rest/json/cves/2.0` | mô tả, CVSS, CWE, sản phẩm |
| `kev` | CISA Known Exploited Vulnerabilities (JSON) | ~1.4k CVE **đang bị khai thác thật** |
| `exploitdb` | `files_exploits.csv` (GitLab) | chỉ **metadata**, đánh dấu CVE có PoC |
| `ctibench` | TSV tải sẵn (**không tự tải**) | xem cảnh báo license bên dưới |

### Tốc độ / rate limit
NVD giới hạn **5 request / 30 giây** khi không có khoá. Xin khoá miễn phí tại
<https://nvd.nist.gov/developers/request-an-api-key> rồi đặt:

```bash
export NVD_API_KEY=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx   # 50 request/30s
```

Công cụ duyệt NVD theo **cửa sổ ngày công bố lùi dần từ hiện tại**, không phải từ đầu danh sách —
vì CVE cũ (1999-2015) phần lớn thiếu CVSS v3 và CWE nên sẽ bị bộ lọc chất lượng loại hết.

### ⚠ License — bắt buộc tuân thủ
- **NVD, CISA KEV**: dữ liệu chính phủ Hoa Kỳ, dùng tự do.
- **Exploit-DB**: GPLv2 → công cụ chỉ lấy **metadata** (tiêu đề, EDB-ID), **không tải và không
  phát tán mã khai thác**.
- **CTIBench** (NeurIPS'24, HF `AI4Sec/cti-bench`): **CC-BY-NC-SA-4.0** → **chỉ được dùng đánh giá
  nội bộ**, **KHÔNG đóng gói lại, KHÔNG bàn giao kèm sản phẩm**. Vì vậy công cụ **không tự tải**
  CTIBench; muốn dùng phải tự tải TSV về rồi trỏ đường dẫn vào.

---

## C · Tài liệu khách hàng

```bash
python3 gen_testset.py --source customer --in bang-khach-hang.csv --out ./testset-v2 --enrich
```

Khách điền theo mẫu `MAU-KHACH-HANG.csv`:

| Cột | Bắt buộc | Ý nghĩa |
|---|:--:|---|
| `cve_id` | nên có | `CVE-2024-3272`. Có thì ground truth tự suy ra được |
| `cau_hoi` | tuỳ | Câu hỏi tiếng Việt khách tự viết. Bỏ trống → sinh theo template |
| `nang_luc` | tuỳ | `cve_qa` `sast` `exploit` `remediation` `translation` `report` `poc_search` `pentest` |
| `do_kho` | tuỳ | `de` `trung_binh` `kho` `tu_duy` |
| `tu_bat_buoc` | tuỳ | Từ khoá bắt buộc có trong đáp án, phân tách bằng `;` |
| `ghi_chu` | tuỳ | Ground truth khách nắm (CWE, CVSS, ghi chú) |

Đọc được cả `.csv` và `.xlsx`; tên cột nhận cả tiếng Việt có dấu (`câu hỏi`, `độ khó`, `mã CVE`…).
Ít nhất một trong hai cột `cve_id` / `cau_hoi` phải có, nếu không dòng đó bị bỏ qua kèm cảnh báo.

`--enrich` sẽ tra Qdrant trước (nhanh, offline), phần còn thiếu mới hỏi NVD.

**Lưu ý số lượng:** mỗi CVE chỉ dùng **1 lần**. Muốn đủ 1000 câu thì bảng phải có
tương ứng ~850 CVE. Ít hơn thì công cụ **tự thu nhỏ đều các nhóm** để giữ đúng tỉ lệ
40/40/17/3 và in cảnh báo rõ ràng.
