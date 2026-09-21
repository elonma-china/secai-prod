#!/usr/bin/env python3
"""Merge 1 LoRA adapter (output của train_llm.py SFT hoặc 05_dpo_llm.py DPO — cả hai chỉ
`model.save_pretrained()` phần adapter, không merge) vào base model thành 1 checkpoint fp16
đầy đủ — bước bắt buộc trước khi quantize AWQ (quantize_experts_only.py/quantize_cyberf1_awq.py
tải model qua `MODEL_ID` từ HF Hub / local dir đầy đủ, không hiểu adapter LoRA rời).

Verified live (2026-09-18): dùng Unsloth (`load_base_model` + `save_pretrained_merged`) thay vì
vanilla `transformers.AutoModelForCausalLM` + `peft.PeftModel.merge_and_unload()` — bản đầu
(vanilla) CHƯA BAO GIỜ được test thật và có rủi ro thật: Cyber-F1 là kiến trúc tuỳ biến
(`Qwen3_5MoeForConditionalGeneration`, MoE + vision tower nhỏ), Unsloth tự có patch/tuỳ chỉnh
riêng để load đúng (backend Triton MoE, meta-tensor guard — xem `unsloth_sft.load_base_model`);
`AutoModelForCausalLM.from_pretrained` thường không đi qua các patch đó. Dùng lại đúng đường đã
verified chạy thật (SFT + DPO + merge 66GB đều qua `load_base_model`) thay vì một đường tải
model khác chưa ai chạy thử.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

FT_ROOT = Path(__file__).resolve().parents[3] / "finetune"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-model", required=False, default=None,
                        help="THỪA/tuỳ chọn: merge dùng thẳng --adapter làm base (load_base_model tự "
                             "phát hiện adapter_config.json). Giữ cờ để tương thích ngược. Trước đây "
                             "merge tự phát hiện có LoRA sẵn "
                             "(adapter_config.json) trong đây thì tiếp tục trên adapter đó, "
                             "không cần base model gốc riêng — xem load_base_model.")
    parser.add_argument("--adapter", type=Path, required=True,
                        help="Thư mục/HF repo adapter LoRA (output_dir của train hoặc DPO)")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--max-seq-length", type=int, default=8192,
                        help="Chỉ ảnh hưởng bộ nhớ lúc load, không ảnh hưởng trọng số merge ra")
    args = parser.parse_args()

    sys.path.insert(0, str(FT_ROOT / "src"))
    from secai_ft.trainers.unsloth_sft import load_base_model

    # `load_base_model` tự phát hiện base_model đã có adapter_config.json (LoRA) thì KHÔNG gắn
    # LoRA mới — dùng thẳng args.adapter làm base_model để đi đúng nhánh "tiếp tục adapter có
    # sẵn" (xem log "already carries a LoRA adapter" khi chạy).
    config = {
        "base_model": str(args.adapter),
        "max_seq_length": args.max_seq_length,
        "load_in_4bit": False,
        "fast_inference": False,
        "device_map": "cuda:0",
        "moe_backend": "unsloth_triton",
        # Chỉ dùng nếu base_model KHÔNG có sẵn adapter (nhánh này sẽ không chạy khi merge một
        # checkpoint LoRA đã train, nhưng load_base_model cần key này tồn tại trong config).
        "peft": {
            "r": 8, "alpha": 16, "dropout": 0.0,
            "target_modules": ["q_proj", "k_proj", "v_proj", "o_proj",
                                "gate_proj", "up_proj", "gate_up_proj", "down_proj"],
        },
    }

    print(f"[merge_lora] load (Unsloth) + attach adapter: {args.adapter}")
    model, tok = load_base_model(config)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    print(f"[merge_lora] saving merged 16-bit checkpoint -> {args.output_dir}")
    model.save_pretrained_merged(str(args.output_dir), tok, save_method="merged_16bit")
    print(f"[merge_lora] done -> {args.output_dir}")


if __name__ == "__main__":
    main()
