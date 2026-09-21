import os
import time
import torch
from compressed_tensors.offload import dispatch_model
from datasets import load_dataset
from transformers import AutoProcessor, Qwen3MoeForCausalLM

from llmcompressor import oneshot
from llmcompressor.modifiers.quantization import QuantizationModifier
from llmcompressor.modifiers.transform.awq import AWQModifier
from llmcompressor.utils import load_context

MODEL_ID = os.environ.get("MODEL_ID", "DuyTa/Cyber-F1")
SAVE_DIR = os.environ.get("SAVE_DIR", "/root/Cyber-F1-AWQ-W4A16")
NUM_CALIBRATION_SAMPLES = int(os.environ.get("NUM_CALIBRATION_SAMPLES", "256"))
MAX_SEQUENCE_LENGTH = int(os.environ.get("MAX_SEQUENCE_LENGTH", "2048"))
GROUP_SIZE = int(os.environ.get("GROUP_SIZE", "128"))

print(f"[cfg] MODEL_ID={MODEL_ID} SAVE_DIR={SAVE_DIR} samples={NUM_CALIBRATION_SAMPLES} "
      f"seqlen={MAX_SEQUENCE_LENGTH} group_size={GROUP_SIZE}", flush=True)

# Load model onto CPU (device_map=None). llmcompressor sequential pipeline
# onloads one decoder layer at a time to the single A100, keeping peak VRAM low.
with load_context(Qwen3MoeForCausalLM):
    model = Qwen3MoeForCausalLM.from_pretrained(
        MODEL_ID, dtype=torch.bfloat16, device_map=None
    )
processor = AutoProcessor.from_pretrained(MODEL_ID)

# Vetted general calibration set used by the official Qwen3-VL AWQ example.
DATASET_ID = os.environ.get("DATASET_ID", "neuralmagic/calibration")
ds = load_dataset(DATASET_ID, name="LLM", split=f"train[:{NUM_CALIBRATION_SAMPLES}]")
ds = ds.shuffle(seed=42)


def preprocess_function(example):
    messages = [
        {"role": m["role"], "content": [{"type": "text", "text": m["content"]}]}
        for m in example["messages"]
    ]
    return processor.apply_chat_template(
        messages,
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


# AWQ (0.12 auto-detects full/linear-attention + MoE dynamic mappings) + W4A16 sym.
recipe = [
    AWQModifier(duo_scaling=True),
    QuantizationModifier(
        ignore=[
            "re:.*embed_tokens",
            "re:.*input_layernorm$",
            "re:.*post_attention_layernorm$",
            "re:.*mlp[.]gate$",
            "re:.*shared_expert_gate$",
            "re:.*norm$",
            "re:model[.]visual.*",
            "re:visual.*",
            "lm_head",
        ],
        config_groups={
            "group_0": {
                "targets": ["Linear"],
                "weights": {
                    "num_bits": 4,
                    "type": "int",
                    "symmetric": True,
                    "group_size": GROUP_SIZE,
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

# Save artifact FIRST so it is never lost, then run an optional sanity generation.
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
