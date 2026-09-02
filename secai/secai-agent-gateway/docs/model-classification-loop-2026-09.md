# Cập nhật Model-based Input Classification — 09/2026 (loop engine)

## 1. Kiến trúc
- **Embedding**: `DuyTa/sec-embedding` (1024-d, serving vast) → **head numpy** (`head.npz`), không cần torch lúc infer (~0.05ms/câu).
- **5 heads**: intent MLP `1024→128→3` (security/chitchat/offtopic); injection sigmoid; PII sigmoid.
- **Ngưỡng** (`thresholds.json`, head cuối): injection **0.6**, pii 0.3, offtopic 0.34.
- **Language gate** (mới): `security.is_unsupported_language` — chặn 15 script không-Latin (CJK/Nhật/Hàn/Nga/Ả-rập/Thái/…), tỉ lệ chữ ≥ 0.5.
- **Cascade** (`calibration.json`, band [0.186, 0.452]): điểm injection vùng-xám → LLM judge (`Cyber-F1`) phân xử.
- **Gate** fail-open: verdict `unknown` → PASS.

## 2. Luồng xử lý (`/ai/chat`)
```
message → require_public_input (PII/secret → 422)
        → is_unsupported_language (non-Latin → refuse_language)
        → embed → decode (5 scores) → gate.decide:
              injection≥0.6 → block_injection
              offtopic≥0.34 → refuse_offtopic
              else → PASS
        → nếu injection_uncertain (in band) & cascade ON → LLM judge → block/pass
```
Precedence: **PII > language > injection > offtopic > pass**. Chỉ áp dụng `/ai/chat`(+stream); translate không bị chặn.

## 3. Chức năng & nhãn
| function | expected_action |
|---|---|
| security hợp lệ | pass |
| off-topic | refuse_offtopic |
| chitchat | pass (greet OFF mặc định) |
| injection/jailbreak | block_injection |
| over-defense (security dạng hiểm) | pass |
| PII/secret | pii_block (422) |
| ngôn ngữ ≠ vi/en | refuse_language |

## 4. Data
- **Train** `evals/sanitize/dataset.jsonl`: **1770 → 3039** dòng (loop thêm ~1270), split theo skeleton chống leak (shared=0), vi/en.
- **Held-out gốc**: `overdefense.jsonl` (211), `attacks.jsonl` (36, 6 họ), `indirect.jsonl`.
- **Benchmark** `benchmark_1000.jsonl` (1000, 8 nhóm chức năng, deterministic, 0 leak) + `benchmark_loop.jsonl` (tích luỹ **2448**, khó dần mỗi vòng).
- Sinh bằng template deterministic (`build_sanitize_dataset.py`, `build_classification_benchmark.py`), không dùng LLM.

## 5. Cách thức cải tiến — loop engine (5 vòng)
Mỗi vòng = 4 agent độc lập (khách quan), tuần tự:
1. **generate** — nghiên cứu internet + sinh 200–300 case mới (đủ 8 nhóm + họ tấn công mới).
2. **bench** — chạy head hiện tại qua đúng precedence production trên serving thật.
3. **evaluate** — verify độc lập, metric per-function, phân loại lỗi thật vs nhãn-mềm, chấm điểm/100.
4. **fix** — thêm data train nhắm lỗi genuine → retrain (`--embed endpoint`) → **cổng chống hồi quy** (chỉ commit nếu held-out giữ FP 0/211 + recall 36/36; head hỏng bị khôi phục).
- **Điều kiện dừng**: score ≥ 95/100 (không đạt → chạy đủ 5 vòng).

## 6. Kết quả các lần bench
| Vòng | Test-set | +case | Bench acc | Score/100 | Lỗi genuine | Commit | Held-out |
|---|---|---|---|---|---|---|---|
| 1 | 1239 | +239 | 97.74% | 84 | 19 | `f9c425c` | FP 0/211 · 36/36 |
| 2 | 1561 | +322 | 92.06% | 69 | 119 | `6c8b7a1` | FP 0/211 · 36/36 |
| 3 | 1857 | +296 | 87.40% | 61 | 120 | `0587b66` | + PII 56→76 |
| 4 | 2152 | +295 | 88.75% | 85 | 233 | `203b3ce` | + PII 76→98, FP-benign 0/200 |
| 5 | 2448 | +296 | 89.26% | 85 | 86 | `134b741` | FP 0/211 · 36/36 |
| post-fix | — | — | — | — | — | `d9f87a3` | FP 0/211 · 36/36 |

- Bench accuracy **giảm** (97.7%→89%) do bộ test **lớn dần & khó dần** — nâng chuẩn, KHÔNG phải hồi quy.
- Vòng 1 sửa 2 miss an ninh thật: tool-abuse `inj-0511` 0.21→0.98, `inj-0496` 0.41→0.99.

## 7. Head cuối (`d9f87a3`) trên benchmark_1000 cố định (apples-to-apples)
| | Baseline | Head cuối |
|---|---|---|
| Overall | 97.8% (978/1000) | 98.9%* → 98.6% (threshold-only) |
| Over-defense (pass) | 565/581 | 575/581 |
| PII gate | 55/55 | 55/55 (recall guard 55→98) |
| Language gate | — | 72/72, FP 0 |
| Injection | 170/172 | threshold 169/172 + **cascade 3/3 residual → hiệu quả 172/172** |

Held-out bất biến toàn bộ: **FP 0/211, injection recall 36/36**.

## 8. Verify live (serving thật)
1. Retrain endpoint khớp offline (FP 0/211, 36/36, ±0.001). 2. Cascade judge đúng 5/5 + end-to-end. 3. Spotlighting A/B Cyber-F1 kháng 0/11. 4. 11 KPI-FP live 0/11 bị chặn.

## 9. Giới hạn (trung thực)
- Injection **tinh vi** không tách được bằng ngưỡng head tuyến tính (đảo margin) → phải để **cascade LLM-judge** xử (band rộng, KHÔNG retighten).
- Chi phí head cuối: 2 benign bị chặn thêm — đều jailbreak-adjacent (tranh cãi, hợp lý).

## 10. Vị trí & triển khai
- Nhánh: `feat/input-sanitization-classifier` (9 commit, đã push origin).
- **Merged main**: PR #7 (merge `017d514`) — main đã có language gate + head threshold 0.6.
- Bộ dữ liệu HF: `/home/ict/secai-classifier-hf/` (train + test + results 5 vòng + head + scripts).
