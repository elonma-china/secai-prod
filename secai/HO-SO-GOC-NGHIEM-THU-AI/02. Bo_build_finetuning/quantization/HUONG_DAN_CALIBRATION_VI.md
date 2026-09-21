# Hướng dẫn tạo calibration data (tiếng Việt)

Tài liệu này hướng dẫn tạo bộ **calibration data** cho quantize AWQ model
`DuyTa/Cyber-F1` (experts-only, xem [README.md](README.md)).

## 1. Calibration data là gì và tại sao quan trọng?

AWQ (Activation-aware Weight Quantization) quyết định cách nén weight 4-bit dựa trên
**phân bố activation** khi cho data chạy qua model. Vì vậy:

- Data calib **càng giống workload thật** (CTI tiếng Việt, tra CVE/CWE/MITRE, gọi tool,
  đọc code…) thì scale 4-bit càng khớp → **chất lượng model sau nén tốt hơn trên đúng domain đó**.
- Data generic (vd. ultrachat tiếng Anh) vẫn chạy được nhưng activation lệch khỏi workload
  → chất lượng domain kém hơn.
- Calib **không** dạy thêm kiến thức cho model — nó chỉ tối ưu việc nén *trong* giới hạn 4-bit.

**Nguyên tắc:** độ liên quan domain > số lượng. 256 mẫu domain tốt hơn 2000 mẫu generic.

## 2. Định dạng file

Một file **JSONL** — mỗi dòng là một hội thoại JSON hợp lệ. Bắt buộc phải có key `messages`;
các key khác (`record_id`, `cve`, `task`, `lang`…) là tùy chọn, script sẽ bỏ qua.

```json
{"messages": [ {"role": "...", "content": "..."}, ... ]}
```

### Các role hỗ trợ

| role | content | Ghi chú |
|---|---|---|
| `system` | string | Prompt hệ thống (persona/quyền hạn của trợ lý). |
| `user` | string | Câu hỏi người dùng. |
| `assistant` | string | Câu trả lời của model. |
| `assistant` (gọi tool) | `null` + `tool_calls` | Turn model gọi function; nội dung nằm trong `tool_calls`. |
| `tool` | string | Kết quả trả về từ tool (kèm `tool_call_id`, `name`). |

> Script `quantize_experts_only.py` có sẵn hàm `normalize_messages` tự động **làm phẳng**
> `tool_calls` và role `tool` thành text để template được. Bạn **không cần** tự xử lý —
> cứ để nguyên format agentic như trên. (Đã test: 256/256 record template OK.)

## 3. Mẫu 1 record thật (từ chính dataset của bạn)

File [`calib_sample.jsonl`](calib_sample.jsonl) chứa đúng record này (dạng 1 dòng JSONL):

```json
{
  "messages": [
    { "role": "system",
      "content": "Bạn là trợ lý chuyên gia An toàn thông tin (ATTT), trả lời bằng tiếng Việt. Năng lực: tra cứu/giải thích CVE-CWE-MITRE ATT&CK, tư vấn pentest, audit mã nguồn tĩnh (SAST)... " },
    { "role": "user",
      "content": "CVE-2019-15363 ảnh hưởng đến sản phẩm/phiên bản nào?" },
    { "role": "assistant",
      "content": null,
      "tool_calls": [
        { "id": "chatcmpl-tool-901c55c9b59ea188", "type": "function",
          "function": { "name": "neo4j_search",
                        "arguments": "{\"query\": \"CVE-2019-15363 ảnh hưởng đến sản phẩm/phiên bản nào?\"}" } }
      ] },
    { "role": "tool",
      "tool_call_id": "chatcmpl-tool-901c55c9b59ea188",
      "name": "neo4j_search",
      "content": "### Row 1\n**cve:** CVE-2019-15363\n**severity:** MEDIUM\n**cvss:** 5.5\n**products:**\n  - power_5_firmware\n  - power_5" },
    { "role": "assistant",
      "content": "## CVE-2019-15363\n\nCVE-2019-15363 là lỗ hổng mức **MEDIUM** (CVSS 5.5)... ảnh hưởng đến Power Systems Firmware (Power 5)..." }
  ],
  "task": "single_tool", "cve": "CVE-2019-15363", "lang": "vi", "family": "A2_self_distill"
}
```

## 4. Tỷ lệ trộn khuyến nghị (cho Cyber-F1)

| Nhóm | Tỷ lệ | Ví dụ |
|---|---:|---|
| CTI / CVE / CWE / MITRE ATT&CK Q&A | 30% | "CVE-XXXX ảnh hưởng gì?", ánh xạ ATT&CK |
| Tiếng Việt kỹ thuật + hội thoại | 25% | giải thích khái niệm bảo mật, tư vấn |
| Code Python/Bash/PowerShell/YAML/JSON | 20% | audit SAST, viết PoC, cấu hình |
| Tool-call / function-call (agentic) | 15% | `qdrant_search`, `neo4j_search` |
| General English reasoning/chat | 10% | giữ năng lực tổng quát |

- **Số lượng:** 256 mẫu là điểm khởi đầu tốt; 512 tốt hơn chút; **>512 lợi ích giảm dần**.
- **Độ dài:** mỗi hội thoại nên rơi vào ~1–2K token (script cắt ở `MAX_SEQUENCE_LENGTH=2048`).

## 5. Cách sinh data (gợi ý)

1. **Reuse từ training data:** lấy ngẫu nhiên N mẫu từ chính SFT dataset của bạn — tự nhiên khớp workload nhất.
2. **Self-distill:** cho model teacher (hoặc chính model gốc) trả lời một loạt câu hỏi CTI/VN thật, lưu lại hội thoại (giống `family: A2_self_distill` trong data của bạn).
3. **Từ nguồn có sẵn:** CVE/CWE/MITRE (NVD, MITRE) → sinh cặp hỏi–đáp tiếng Việt; thêm vài phiên có gọi tool.
4. **Dịch:** dịch một phần data kỹ thuật tiếng Anh sang tiếng Việt (giống `family: C_external_translated`).

> Không cần nhãn "đúng/đẹp" như training — calib chỉ cần **đại diện phân bố activation**.
> Nội dung hợp lý, đúng domain là đủ.

## 6. Kiểm tra nhanh file calib

```bash
# đếm record + kiểm JSON hợp lệ
wc -l calib.jsonl
python3 -c "import json; [json.loads(l) for l in open('calib.jsonl')]; print('JSON OK')"

# thử template thật với processor (đảm bảo tool-call không lỗi)
python3 calib_preprocess.py DuyTa/Cyber-F1 calib.jsonl
# -> in: normalized processor template: OK N/N fail 0
```

## 7. Dùng khi quantize

Chỉ cần trỏ biến môi trường `CALIB_JSONL` tới file, không phải sửa code:

```bash
export CALIB_JSONL="/root/calib_cyber_vi.jsonl"
export NUM_CALIBRATION_SAMPLES=256        # smoke thì để 16
bash run_experts_only.sh
```

Nếu `CALIB_JSONL` để trống → script tự dùng bộ generic `neuralmagic/calibration`.

## 8. Checklist chất lượng

- [ ] Mỗi dòng JSON hợp lệ, có key `messages`.
- [ ] Có đủ các nhóm domain (mục 4), không lệch 100% về một loại.
- [ ] Có một phần record agentic (tool-call) để khớp workload gọi tool.
- [ ] Chủ yếu tiếng Việt (đúng ngôn ngữ suy luận của model).
- [ ] `python3 calib_preprocess.py ...` báo `fail 0`.
- [ ] ≥ 256 record cho bản production.
