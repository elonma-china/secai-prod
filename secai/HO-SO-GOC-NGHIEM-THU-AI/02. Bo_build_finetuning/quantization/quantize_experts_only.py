"""AWQ W4A16 quantization of DuyTa/Cyber-F1 — ROUTED EXPERTS ONLY.

Replicates the coverage/config of the reference DuyTa/Cyber-F1-AWQ upload:
only `...mlp.experts.N.{gate,up,down}_proj` are quantized; ALL attention
(linear + full), shared experts, vision tower, mtp, lm_head stay in fp16/bf16.

Scheme (matches reference exactly): int4, group_size=32, ASYMMETRIC, mse observer,
pack-quantized. The ignore list is taken verbatim from the reference repo's
config.json so coverage is identical.
"""
import os
import time
import json
import urllib.request
import torch
from compressed_tensors.offload import dispatch_model
from datasets import load_dataset
from transformers import AutoProcessor, AutoTokenizer, Qwen3_5MoeForConditionalGeneration

from llmcompressor import oneshot
from llmcompressor.modifiers.quantization import QuantizationModifier
from llmcompressor.modifiers.transform.awq import AWQModifier
from llmcompressor.utils import load_context

MODEL_ID = os.environ.get("MODEL_ID", "DuyTa/Cyber-F1")
SAVE_DIR = os.environ.get("SAVE_DIR", "/root/Cyber-F1-AWQ-experts-only")
NUM_CALIBRATION_SAMPLES = int(os.environ.get("NUM_CALIBRATION_SAMPLES", "256"))
MAX_SEQUENCE_LENGTH = int(os.environ.get("MAX_SEQUENCE_LENGTH", "2048"))
IGNORE_JSON = os.environ.get("IGNORE_JSON", "/root/ignore_experts_only.json")
REF_MODEL = os.environ.get("REF_MODEL", "DuyTa/Cyber-F1-AWQ")

# Exact ignore list = "everything that is NOT a routed expert", taken from the
# reference repo (local snapshot first, else fetched from the hub).
if os.path.exists(IGNORE_JSON):
    IGNORE = json.load(open(IGNORE_JSON))
else:
    url = f"https://huggingface.co/{REF_MODEL}/raw/main/config.json"
    IGNORE = json.load(urllib.request.urlopen(url))["quantization_config"]["ignore"]

print(f"[cfg] EXPERTS-ONLY AWQ | ignore={len(IGNORE)} entries | int4/gs32/asym/mse | "
      f"samples={NUM_CALIBRATION_SAMPLES} seqlen={MAX_SEQUENCE_LENGTH} -> {SAVE_DIR}", flush=True)

with load_context(Qwen3_5MoeForConditionalGeneration):  # full multimodal (giữ vision fp16 — khớp production)
    model = Qwen3_5MoeForConditionalGeneration.from_pretrained(
        MODEL_ID, dtype=torch.bfloat16, device_map=None
    )
processor = AutoTokenizer.from_pretrained(MODEL_ID)  # smoke: text tokenizer (Qwen3.5 AutoProcessor cần preprocessor_config vision — không có)

# Calibration source. Point CALIB_JSONL at a domain (cyber/CTI/Vietnamese/code/tool-call)
# chat JSONL — each line {"messages":[{"role","content"}, ...]} — for workload-matched AWQ
# scales. Falls back to the general neuralmagic/calibration set.
CALIB_JSONL = os.environ.get("CALIB_JSONL", "")
if CALIB_JSONL:
    ds = load_dataset("json", data_files=CALIB_JSONL, split="train").shuffle(seed=42)
    ds = ds.select(range(min(NUM_CALIBRATION_SAMPLES, len(ds))))
    print(f"[calib] custom JSONL {CALIB_JSONL} -> {len(ds)} samples", flush=True)
else:
    ds = load_dataset(
        "neuralmagic/calibration", name="LLM", split=f"train[:{NUM_CALIBRATION_SAMPLES}]"
    ).shuffle(seed=42)
    print(f"[calib] neuralmagic/calibration (general) -> {len(ds)} samples", flush=True)


def normalize_messages(messages):
    # Flatten agentic records (assistant tool_calls with content=None, `tool` role
    # results) into typed-text content the multimodal processor can template.
    # Plain string-content messages pass straight through.
    out = []
    for m in messages:
        role = m.get("role")
        content = m.get("content")
        if role == "tool":
            txt = "[tool_result name=" + str(m.get("name", "")) + "]\n" + (content or "")
            role = "user"
        elif content is None and m.get("tool_calls"):
            parts = []
            for tc in m["tool_calls"]:
                fn = tc.get("function", {})
                parts.append("[tool_call " + str(fn.get("name")) + "] " + str(fn.get("arguments")))
            txt = "\n".join(parts)
        elif content is None:
            continue
        else:
            txt = content
        out.append({"role": role, "content": txt})  # string content (tokenizer text template)
    return out


def preprocess_function(example):
    return processor.apply_chat_template(
        normalize_messages(example["messages"]),
        return_tensors="pt",
        padding=False,
        truncation=True,
        max_length=MAX_SEQUENCE_LENGTH,
        tokenize=True,
        add_special_tokens=False,
        return_dict=True,
        add_generation_prompt=False,
    )


ds = ds.map(preprocess_function, batched=False, remove_columns=ds.column_names)


def data_collator(batch):
    assert len(batch) == 1
    return {
        key: (
            torch.tensor(value)
            if key != "pixel_values"
            else torch.tensor(value, dtype=torch.bfloat16).squeeze(0)
        )
        for key, value in batch[0].items()
    }


# AWQModifier (0.12) is smoothing-only and has no `ignore`; it smooths all mappings
# (output-preserving for layers QuantizationModifier does not quantize). The IGNORE
# list on QuantizationModifier is what restricts quantization to routed experts —
# int4/gs32/ASYM/mse group quantization, identical scheme to DuyTa/Cyber-F1-AWQ.
recipe = [
    AWQModifier(duo_scaling=True),
    QuantizationModifier(
        ignore=IGNORE,
        config_groups={
            "group_0": {
                "targets": ["Linear"],
                "weights": {
                    "num_bits": 4,
                    "type": "int",
                    "symmetric": False,
                    "group_size": 32,
                    "strategy": "group",
                    "dynamic": False,
                    "actorder": None,
                    "observer": "mse",
                },
            }
        },
    ),
]

torch.cuda.reset_peak_memory_stats()
t0 = time.time()

oneshot(
    model=model,
    processor=processor,
    recipe=recipe,
    dataset=ds,
    max_seq_length=MAX_SEQUENCE_LENGTH,
    num_calibration_samples=NUM_CALIBRATION_SAMPLES,
    data_collator=data_collator,
)

print(f"[done] quantization took {(time.time()-t0)/60:.1f} min, "
      f"peak GPU {torch.cuda.max_memory_allocated()/1024**3:.1f} GB", flush=True)

model.save_pretrained(SAVE_DIR, save_compressed=True)
processor.save_pretrained(SAVE_DIR)
print(f"[saved] {SAVE_DIR}", flush=True)

try:
    dispatch_model(model)
    ids = processor(text="A common web vulnerability is", return_tensors="pt").input_ids.to("cuda")
    out = model.generate(ids, max_new_tokens=40)
    print("[sample]", processor.decode(out[0]), flush=True)
except Exception as e:
    print("[sample] generation skipped:", repr(e), flush=True)
