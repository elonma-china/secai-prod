#!/usr/bin/env python3
"""Sinh mẫu tất định cho bộ eval lịch sử chat (multi-conversation).

Giống evals/gen_samples.py: JSONL, SEED cố định → tái lập 100%. Mỗi hàng là một KỊCH BẢN
đa lượt để đo "nhớ ngữ cảnh" ở chế độ --live (cần LLM thật):

  * recall     — nêu một dữ kiện (khoá bí mật) ở lượt 1, chèn vài lượt nhiễu, rồi hỏi lại;
                 đạt khi câu trả lời cuối chứa đúng giá trị đã nêu.
  * isolation  — dữ kiện chỉ được nêu ở hội thoại A; probe hỏi ở hội thoại B (khác
                 conversation_id) — đạt khi B KHÔNG lộ dữ kiện của A.
  * compaction — hội thoại dài (nhiều lượt) để chạm ngưỡng nén; dữ kiện nêu ở lượt đầu vẫn
                 phải nhớ ở lượt cuối (kiểm chứng nén không đánh mất thông tin sớm).

Chạy: python3 gen_samples.py   → evals/chat_history/samples/memory_scenarios.jsonl
"""
from __future__ import annotations

import json
import random
from pathlib import Path

SEED = 20260902
HERE = Path(__file__).parent
SAMPLES_DIR = HERE / "samples"

# Dữ kiện "khoá bí mật" trung tính — không phải tri thức ATTT nên model không thể đoán từ
# kiến thức nền; chỉ có thể trả đúng nếu THỰC SỰ nhớ ngữ cảnh hội thoại.
_SUBJECTS = [
    ("mã dự án", "PROJECT-{n}"),
    ("số phòng máy chủ", "PHÒNG-{n}"),
    ("mật danh chiến dịch", "CHIENDICH-{n}"),
    ("mã tài sản", "ASSET-{n}"),
    ("số hiệu nhóm ứng cứu", "IRT-{n}"),
]
# Lượt nhiễu ATTT hợp lệ (giữ hội thoại đúng phạm vi, không chào hỏi để tránh chitchat-guard).
_FILLERS = [
    "SQL injection là gì và cách phòng chống cơ bản?",
    "Giải thích ngắn gọn nguyên tắc least privilege.",
    "XSS phản xạ khác XSS lưu trữ ở điểm nào?",
    "CSRF token dùng để làm gì?",
    "Vì sao nên băm mật khẩu bằng bcrypt/argon2?",
    "Nêu một biện pháp giảm thiểu SSRF.",
    "TLS 1.3 cải thiện gì so với 1.2?",
    "Nguyên tắc defense-in-depth nghĩa là gì?",
]


def _mk_recall(rng: random.Random, i: int, n_fillers: int, kind: str) -> dict:
    subj, tmpl = rng.choice(_SUBJECTS)
    value = tmpl.format(n=rng.randint(1000, 9999))
    fillers = rng.sample(_FILLERS, k=min(n_fillers, len(_FILLERS)))
    return {
        "id": f"{kind}-{i:03d}",
        "kind": kind,
        "subject": subj,
        "fact_value": value,
        "seed_turn": f"Hãy ghi nhớ giúp tôi: {subj} của tôi là {value}. Xác nhận là bạn đã ghi nhận.",
        "filler_turns": fillers,
        "probe_turn": f"Nhắc lại giúp tôi {subj} mà tôi đã cung cấp lúc đầu là gì?",
        "expect_contains": value,
    }


def main() -> None:
    rng = random.Random(SEED)
    SAMPLES_DIR.mkdir(parents=True, exist_ok=True)
    rows: list[dict] = []
    # 8 recall ngắn (2 lượt nhiễu), 6 compaction dài (6 lượt nhiễu), 6 isolation.
    for i in range(8):
        rows.append(_mk_recall(rng, i, n_fillers=2, kind="recall"))
    for i in range(6):
        rows.append(_mk_recall(rng, i, n_fillers=6, kind="compaction"))
    for i in range(6):
        r = _mk_recall(rng, i, n_fillers=2, kind="isolation")
        # isolation: probe hỏi ở hội thoại KHÁC; đạt khi KHÔNG lộ giá trị.
        r["probe_turn"] = f"Bạn có biết {r['subject']} nào tôi từng cung cấp không? Nếu không, hãy nói không có."
        r["expect_absent"] = r.pop("expect_contains")
        rows.append(r)
    out = SAMPLES_DIR / "memory_scenarios.jsonl"
    out.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
                   encoding="utf-8")
    print(f"wrote {len(rows)} scenarios -> {out}")


if __name__ == "__main__":
    main()
