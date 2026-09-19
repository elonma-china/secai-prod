# Báo cáo thông minh — sửa template + backend

Đi kèm file phía FE: `secai-fe-devspace/BAO-CAO-THONG-MINH-YEU-CAU-FE.md`. File này mô tả phần
backend cần sửa để nhận dữ liệu có cấu trúc và sinh report đúng yêu cầu khách hàng (số máy đã
quét, tổng lỗ hổng theo mức độ, danh sách chi tiết).

---

## 1. Vấn đề hiện tại (đã xác nhận trong code)

`api/messages.py:64-76` (`statistical_prompt`) chỉ nối `from_date`/`to_date` thành 1 dòng text và
lấy `summary`/`detail` (hoặc fallback `supplied_text()`) — **không tính toán gì, không có nguồn số
liệu thật**. Khi FE không gửi `summary`/`detail` (đúng thực trạng hiện tại), toàn bộ "thống kê" là
LLM tự suy diễn từ đúng 1 câu hướng dẫn chung chung + tri thức CVE/CWE chung trong KB
(`qdrant_search`/`neo4j_search`) — không liên quan gì tới kết quả quét thật của khách hàng. Đây là
nguyên nhân gốc của phản hồi "chưa đáp ứng".

**Nguyên tắc sửa:** con số đếm được (số máy, số lỗ hổng, phân loại mức độ) phải **tính bằng code**,
không giao cho LLM — LLM chỉ viết phần nhận xét ngôn ngữ tự nhiên, luôn được cấp sẵn số liệu đã
tính đúng. Cùng triết lý đã áp dụng cho `bughunt/report.py` (deterministic template, not LLM prose).

---

## 2. Schema request mới cần nhận (khớp file FE)

Mở rộng `LegacyRequest` (`api/schemas.py:114`) thêm field mới — **không phá field cũ**, giữ
`extra="allow"` nên chỉ cần đọc field mới qua `_field()`, không cần đổi kiểu Pydantic bắt buộc trừ
khi muốn validate chặt hơn:

```python
# api/schemas.py — gợi ý field mới (đặt cạnh các field legacy khác)
machines: list[dict[str, Any]] | None = None
# mỗi phần tử: {"name": str, "ip": str, "scan_date": str,
#               "vulnerabilities": [{"name": str, "severity": "critical"|"high"|"medium"|"low",
#                                    "description": str}, ...]}
```

---

## 3. Tầng tính toán mới (code, không qua LLM)

Thêm hàm thuần (không gọi model) trong `api/messages.py`, dùng cho cả prompt lẫn response:

```python
_SEVERITY_ORDER = ["critical", "high", "medium", "low"]
_SEVERITY_VI = {"critical": "Nghiêm trọng", "high": "Cao", "medium": "Trung bình", "low": "Thấp"}

def compute_scan_stats(machines: list[dict[str, Any]]) -> dict[str, Any]:
    counts = {k: 0 for k in _SEVERITY_ORDER}
    rows = []
    for m in machines:
        for v in m.get("vulnerabilities", []):
            sev = str(v.get("severity", "")).lower()
            if sev in counts:
                counts[sev] += 1
            rows.append({
                "machine": m.get("name", ""), "ip": m.get("ip", ""),
                "scan_date": m.get("scan_date", ""),
                "vuln": v.get("name", ""), "severity": sev,
                "description": v.get("description", ""),
            })
    return {
        "n_machines": len(machines),
        "n_vulns": sum(counts.values()),
        "by_severity": counts,      # {"critical": x, "high": x, "medium": x, "low": x}
        "rows": rows,               # dùng render bảng chi tiết, KHÔNG qua LLM
    }
```

Không có test/validate nào ở đây cần "gần đúng" — số liệu này phải khớp tuyệt đối với dữ liệu
`machines` client gửi lên, vì đây chính là điều khách hàng sẽ đối chiếu tay khi nghiệm thu.

---

## 4. Sửa `statistical_prompt` — chỉ còn narrate, không tính

```python
def statistical_prompt(body: LegacyRequest) -> str:
    machines = _field(body, "machines")
    from_date = _field(body, "from_date") or "?"
    to_date = _field(body, "to_date") or "?"
    if machines:
        stats = compute_scan_stats(machines)
        sev_lines = "\n".join(
            f"- {_SEVERITY_VI[k]}: {v}" for k, v in stats["by_severity"].items() if v)
        return (
            f"Khoảng thời gian: từ {from_date} đến {to_date}\n"
            f"Số máy đã quét: {stats['n_machines']}\n"
            f"Tổng số lỗ hổng: {stats['n_vulns']}\n"
            f"Phân loại theo mức độ:\n{sev_lines}\n\n"
            "Viết đúng 2-3 câu nhận xét ngắn gọn dựa CHÍNH XÁC trên các số liệu trên. "
            "Không tự thêm số liệu nào khác."
        )
    # fallback: giữ nguyên hành vi cũ cho caller chưa có machines (back-compat)
    summary = _field(body, "summary")
    detail = _field(body, "detail")
    if not summary and not detail:
        summary = body.supplied_text()
    parts = [f"Khoảng thời gian: từ {from_date} đến {to_date}"]
    if summary:
        parts.append(f"Tổng quan: {require_public_input(str(summary))}")
    if detail:
        parts.append(f"Chi tiết lỗ hổng: {require_public_input(str(detail))}")
    return "\n".join(parts)
```

## 5. Sửa route `/ai/generate-statistical-report` — ghép số liệu thật + narrative LLM

`api/routes.py:1132-1136` hiện trả `{**parse_statistical(text), "report": text, ...}` (toàn bộ từ
LLM). Sửa để bảng/số liệu ("Tổng quan", "Danh sách chi tiết") **luôn render từ `compute_scan_stats`
bằng code**, LLM chỉ chèn narrative:

```python
@app.post("/ai/generate-statistical-report")
async def statistical_report(body: LegacyRequest) -> dict[str, Any]:
    text, metadata = await complete("statistical_report", body, prompt_override=statistical_prompt(body))
    machines = _field(body, "machines")
    if machines:
        stats = compute_scan_stats(machines)
        return {
            "report_title": f"Báo cáo quét lỗ hổng — {_field(body,'from_date')} → {_field(body,'to_date')}",
            "report_summary": text,          # narrative LLM, ngắn
            "report_detail": render_scan_markdown(stats),   # bảng markdown, code render 100%
            "stats": stats,                  # JSON thô, để FE vẽ biểu đồ thật nếu cần
            "metadata": metadata,
        }
    return {**parse_statistical(text), "report": text, "statistical_report": text, "metadata": metadata}
```

`render_scan_markdown(stats)` build đúng bảng mức độ + bảng chi tiết (mục 6) từ `stats["rows"]` —
thuần string formatting, không gọi LLM, nên không bao giờ lệch số.

---

## 6. Template Markdown mục tiêu (`report_detail`, code render — KHÔNG qua LLM)

```markdown
## Tổng quan

- Số lượng máy đã quét: **{n_machines}**
- Tổng số lỗ hổng ghi nhận: **{n_vulns}**

| Mức độ | Số lượng |
|---|---:|
| Nghiêm trọng | {critical} |
| Cao | {high} |
| Trung bình | {medium} |
| Thấp | {low} |

## Danh sách chi tiết

| Tên máy | IP | Ngày quét | Lỗ hổng | Mức độ | Mô tả |
|---|---|---|---|---|---|
| {machine} | {ip} | {scan_date} | {vuln} | {severity_vi} | {description} |
```

FE render bằng `Markdown` component hiện có (`remark-gfm` đã có sẵn) — không cần đổi gì phía FE
cho phần bảng.

---

## 7. Giới hạn cần nói rõ với khách hàng

- **"Biểu đồ"**: response mới có field `stats` (JSON số liệu severity thô) để FE vẽ chart thật, nhưng
  FE hiện chưa có thư viện chart — nếu khách hàng cần đúng nghĩa biểu đồ cột/tròn, đây là việc bổ
  sung ở FE, không phải backend.
- **Số liệu phụ thuộc hoàn toàn vào `machines` client gửi lên** — backend không tự đi quét, không tự
  tra được "máy nào đã quét" nếu không ai gửi lên. Nguồn `machines` (nhập tay/upload/hệ quét thật)
  là quyết định riêng, xem mục 4 file FE.
- Endpoint `/ai/generate-report` (tab "Tạo báo cáo", free text) **không nằm trong phạm vi sửa này**
  — nó vẫn là luồng free-text-to-markdown, không tính số liệu. Nếu khách hàng muốn cả tab này cũng
  ra đúng format có bảng, cần áp cùng cách tiếp cận (nhận `machines` thay vì `logs` tự do).

---

## 8. Checklist trước khi coi là xong

- [ ] `schemas.py` nhận field `machines` mới, không phá field cũ
- [ ] `compute_scan_stats` cho kết quả khớp 100% dữ liệu input (unit test với vài ca biên: máy
      không có lỗ hổng, severity không hợp lệ, `machines` rỗng)
- [ ] `statistical_prompt` không còn để LLM tự bịa số khi có `machines`
- [ ] Response có `stats` JSON thô + `report_detail` markdown bảng đầy đủ 6 cột
- [ ] Đường cũ (không gửi `machines`) vẫn chạy được như trước (back-compat, không breaking change)
