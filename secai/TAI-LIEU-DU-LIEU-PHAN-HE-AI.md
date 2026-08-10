# Tài liệu Dữ liệu — Phân hệ AI (SecAI)

> Cập nhật: 2026-08-08 · Phạm vi: KB tri thức CVE dùng cho RAG của gateway `:8131`

## 1. Kho lưu trữ

| Store | Vai trò | Quy mô (2026-08-07) |
|-------|---------|----------------------|
| **Qdrant** `cve_kb_hybrid` | Vector KB (dense + sparse) cho semantic/hybrid search | 685.927 điểm (~343k CVE × 2 chunk) |
| **Neo4j** | Đồ thị tri thức CVE↔CWE↔CAPEC↔ATT&CK↔Product | 343k CVE, 166k Product, 811 CWE, 443 CAPEC, 177 Technique, 41 Tactic; ~5,2M quan hệ |
| **SQLite** `poc/catalog.db` | Catalog PoC/exploit | 116.175 PoC |
| **SQLite** `cve_intel.db` | DB trung gian của cron (staging) | ~116k CVE |

## 2. Nguồn dữ liệu

| Nguồn | Nội dung | Dùng ở lớp |
|-------|----------|------------|
| **NVD API v2** | CVE, CVSS, CWE, mô tả | Nền + Incremental |
| **MITRE** CVE List / CWE / CAPEC / ATT&CK | Chuẩn hóa định danh, đồ thị | Nền |
| **CISA KEV** | Cờ `is_in_kev` | Nền (⚠ cron đang lỗi 403) |
| **OpenCVE** | Products, EPSS, vendors, KEV (giàu metadata) | Backfill thủ công |
| exploitdb, PoC-in-GitHub, GHSA, nuclei, metasploit, vulhub | PoC/exploit | Catalog PoC |

## 3. Cơ chế cập nhật

**Nền (base):** snapshot thủ công 1 lần từ VPS prod `103.130.219.238` (clone 2026-07-13, `secai-data/restore.sh`).

**Incremental (tự động):** cron `0 3 * * *` → `run_sync.sh`, 2 bước:

```
[03:00] Bước 1  sync_nvd.py    NVD API (cửa sổ ~30 ngày, lastModStartDate) → cve_intel.db
[03:03] Bước 2  sync_nvd_to_kb.py  cve_intel.db → embed dense(bge-m3)+sparse(bm25)
                                    → upsert Qdrant cve_kb_hybrid + MERGE Neo4j
```

- Chỉ chạy Bước 2 khi Bước 1 exit 0.
- Watermark `_state/nvd_to_kb.json` (theo `last_updated`, so sánh `>=`) → mỗi đêm chỉ xử lý CVE thay đổi.
- Log: `sync_cve.log`, `kb_push.log`.
- ⚠ Hạn chế: CVE incremental chỉ có CVE+CVSS+CWE+mô tả; products/CAPEC/ATT&CK/KEV rỗng (làm giàu bằng OpenCVE backfill). CISA KEV trong cron hiện bị chặn 403.

## 4. Cấu trúc dữ liệu khi nạp vào DB

### 4.1 Qdrant `cve_kb_hybrid`

- **Point ID** (tất định): `uuid5(namespace, "{CVE}::{kind}")`.
- **Named vectors:** `dense` = bge-m3, 1024d, Cosine · `bm25` = sparse `{indices[], values[]}`.
- **Mỗi CVE = 2 chunk:** `core` (CVE/Severity/CVSS/Vector/CWE/Description) và `context` (CVE/Severity/Weaknesses/OWASP/Published/Summary). Trường `text` là chuỗi pipe-delimited được đem đi embed + rerank.
- **Payload:**

```jsonc
{
  "cve_id": "CVE-2026-XXXXX",
  "chunk_id": "CVE-2026-XXXXX::core",     // hoặc ::context
  "chunk_kind": "core | context",
  "kind": "core | context",
  "year": "2026",                          // str
  "cvss_score": 9.8,                        // float | null
  "severity": "CRITICAL|HIGH|MEDIUM|LOW|UNKNOWN",
  "cwe_refs": ["CWE-89"],
  "capec_entries": [], "mitre_techniques": [], "mitre_tactics": [],
  "products": [], "is_in_kev": false,
  "source": "nvd-cron-sync",
  "text": "CVE ID: ... | Severity: ... | CVSS: ... | ..."
}
```

### 4.2 Neo4j

```
(:CVE {id})-[:HAS_WEAKNESS]->(:CWE {id})
(:CWE)-[:...]->(:CAPEC)-[:...]->(:Technique)-[:...]->(:Tactic)
(:CVE)-[:...]->(:Product)
```
Cron dùng `MERGE` cho `CVE` và `HAS_WEAKNESS→CWE`; các cạnh CWE→CAPEC→ATT&CK dùng lại đồ thị nền.

### 4.3 Catalog PoC (`catalog.db`)
Bảng chính: `poc_cve` (111.049), `poc_file` (5.399); mỗi PoC gắn `source` (exploitdb/poc_github/ghsa/nuclei/metasploit/vulhub/vendor) + `provenance`.

## 5. Lọc trùng (dedup) — 3 tầng, idempotent

1. **SQLite:** `INSERT OR IGNORE` + `UPDATE` theo khóa `cve_id`.
2. **Watermark:** chỉ quét dòng `last_updated >= watermark`.
3. **ID tất định + existence-check:** point ID `uuid5` ⇒ cùng CVE luôn cùng ID (upsert đè, không nhân đôi); trước khi embed kiểm tra Qdrant `count(cve_id)` → chỉ nhúng CVE **thật sự thiếu**. Neo4j dùng `MERGE` (không `CREATE`).

> Ví dụ run 2026-08-08: 11.217 ứng viên → 386 thiếu được nạp, 114.971 bỏ qua, 0 lỗi.
