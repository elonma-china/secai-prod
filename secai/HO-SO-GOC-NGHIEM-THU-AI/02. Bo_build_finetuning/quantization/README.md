# AWQ W4A16 quantization — DuyTa/Cyber-F1

Quantize the `DuyTa/Cyber-F1` finetune (architecture: `Qwen3MoeForCausalLM`
— a Qwen3-30B-A3B-Instruct-2507 **hybrid linear/full-attention MoE** with a vision tower) to
**AWQ W4A16** (4-bit weights, 16-bit activations) in `compressed-tensors` format for
vLLM serving on a single **A100 80GB**.

Model facts: 40 decoder layers, 256 experts (8 active), `moe_intermediate_size=512`,
`shared_expert_intermediate_size=512`, `layer_types` = every 4th layer `full_attention`,
the rest `linear_attention`. bf16 checkpoint ≈ **70 GB**; W4A16 output ≈ **19 GB**.

## Files

| File | Purpose |
|---|---|
| `quantize_cyberf1_awq.py` | **Full** AWQ: quantizes experts **+ attention + shared experts** (int4/gs128/**sym**). Smallest output (~19–20 GB). |
| `run_all.sh` | Orchestrator for the full variant. |
| `quantize_experts_only.py` | **Experts-only** AWQ: quantizes **only routed experts** (int4/gs32/**asym**/mse), everything else fp16 — matches the `DuyTa/Cyber-F1-AWQ` upload (~24 GB). Uses the exact ignore list. |
| `run_experts_only.sh` | Orchestrator for the experts-only variant. |
| `ignore_experts_only.json` | 502-entry ignore list (everything that is NOT a routed expert), taken verbatim from `DuyTa/Cyber-F1-AWQ`. |
| `HUONG_DAN_CALIBRATION_VI.md` | **Vietnamese guide** for building calibration data (format, domain mix, how to generate, checklist). |
| `calib_sample.jsonl` | One real calibration record (agentic CVE lookup via `neo4j_search`) as a format example. |
| `calib_preprocess.py` | `normalize_messages` helper + a standalone tester: `python calib_preprocess.py <model_id> <calib.jsonl>` → reports how many records template cleanly. |
| `requirements.txt` | Exact pinned versions verified working. |

### Full vs experts-only

| | Full (`quantize_cyberf1_awq.py`) | Experts-only (`quantize_experts_only.py`) |
|---|---|---|
| Quantized | experts + linear/full attention + shared experts | **routed experts only** |
| Scheme | int4, group_size **128**, **symmetric** | int4, group_size **32**, **asymmetric**, mse |
| Attention / shared / vision | 4-bit | **fp16/bf16** (higher quality, larger) |
| Output size | ~19–20 GB | ~24 GB (matches reference `Cyber-F1-AWQ`) |

Both keep the router `mlp.gate` in high precision (it is not an `nn.Linear` in Qwen3-30B-A3B-Instruct-2507,
so it is never quantized regardless).

## Target environment

- **1× A100-SXM4-80GB**, driver `525.147.05` (CUDA 12.0-era), 295 GB RAM, ~130 GB disk.
- Single GPU → **no DDP/torchrun**. Model loads to CPU RAM (`device_map=None`); llmcompressor's
  sequential pipeline streams **one decoder layer at a time to the GPU** for the AWQ math.
  Peak VRAM stays ~5 GB — the compute is on the GPU, the CPU is just a weight parking lot.

## Setup (on the server)

```bash
python3 -m venv /root/quant-venv
source /root/quant-venv/bin/activate
pip install --upgrade pip setuptools wheel

# torch MUST be a CUDA 12.x build (cu126). cu130/CUDA-13 will NOT run on driver 525.
# cu126 runs on driver 525.147 via CUDA minor-version compatibility (verified).
pip install torch==2.10.0 torchvision==0.25.0 --index-url https://download.pytorch.org/whl/cu126

# llmcompressor 0.12 pulls transformers 5.10, compressed-tensors 0.17.1, accelerate, datasets.
# The [qwen] extra + torchvision are REQUIRED — AutoProcessor loads Qwen3VLVideoProcessor
# which imports torchvision (otherwise it crashes right before saving).
pip install "llmcompressor[qwen]==0.12.0" hf_transfer
```

Sanity check before the long run:

```bash
python -c "import torch; assert torch.cuda.is_available(); print(torch.randn(8,8,device='cuda').sum())"
python -c "from transformers import AutoProcessor; AutoProcessor.from_pretrained('DuyTa/Cyber-F1'); print('processor OK')"
```

## Run

Copy the two scripts to `/root/` on the server, then run inside `tmux` so it survives disconnects:

```bash
tmux new-session -d -s quant "bash /root/run_all.sh > /root/logs/run_all.log 2>&1"
tmux attach -t quant     # or: tail -f /root/logs/run_all.log
```

Tune via env vars in `run_all.sh` (or export before running the .py directly):

| Var | Smoke | Production | Notes |
|---|---|---|---|
| `NUM_CALIBRATION_SAMPLES` | 32 | 256–512 | Only speeds the calibration forward pass, **not** the per-expert grid search. |
| `MAX_SEQUENCE_LENGTH` | 2048 | 2048 | |
| `GROUP_SIZE` | 128 | 128 | W4A16 symmetric, MSE observer. |

Output → `$SAVE_DIR` (default `/root/Cyber-F1-AWQ-W4A16`).

## The recipe (why it's correct for a linear-attention MoE)

```python
recipe = [
    AWQModifier(duo_scaling=True),                 # 0.12 auto-detects linear vs full
    QuantizationModifier(                          # attention + MoE dynamic mappings
        config_groups={"group_0": {"targets": ["Linear"],
            "weights": {num_bits:4, type:int, symmetric:True,
                        group_size:128, strategy:group, observer:mse}}},
        ignore=[... lm_head, mlp.gate, shared_expert_gate, *norm, embed_tokens, visual ...]),
]
```

- **Experts ARE quantized** (they are ~95% of params — that's where 70→19 GB comes from).
  All 256 experts per layer get an AWQ grid search; only the **router `mlp.gate`** and
  **`shared_expert_gate`** are kept in high precision (matches the official Qwen3-Next /
  GLM-4.7 / Qwen-MoE examples).
- **Linear attention is safe**: its sensitive internals (`conv1d`, `A_log`, `dt_bias`,
  q/k norms) are **not `nn.Linear`**, so `targets=["Linear"]` never touches them. Only the
  in/out projections are quantized, and AWQ smoothing compensates — exactly what the official
  `qwen3_next_example.py` does. llmcompressor 0.12's dynamic mappings (issue #2514 fix) handle
  both `linear_attention` and `full_attention` layers automatically from `text_config.layer_types`.
- The vision tower is ignored (text-only calibration).

## Calibration data → quality

> Full Vietnamese how-to (format, tool-call handling, domain mix, generation, checklist):
> **[HUONG_DAN_CALIBRATION_VI.md](HUONG_DAN_CALIBRATION_VI.md)** — with a real sample in
> [`calib_sample.jsonl`](calib_sample.jsonl). Agentic (tool-call) records are handled
> automatically by `normalize_messages` in the quantize scripts.

AWQ computes activation-aware scales from the calibration set, so **domain relevance matters
more than raw sample count**:

- Point `CALIB_JSONL` at a chat JSONL (`{"messages":[{"role","content"}, ...]}` per line)
  weighted toward the real workload — e.g. ~30% CTI/CVE/MITRE, 25% Vietnamese technical,
  20% code, 15% tool-calls, 10% general — for scales tuned to *this* model's use.
- Samples: 256 is a good start, 512 slightly better, **diminishing returns beyond ~512**.
- Calibration only optimizes *within* the 4-bit budget — it cannot recover what 4-bit
  fundamentally drops. The bigger quality levers are coverage/precision choices (experts-only
  keeps attention in fp16; smaller `group_size`), not endless calibration data.

## Gotchas learned the hard way

1. **Disable Xet.** `HF_XET_HIGH_PERFORMANCE=1` stalled and buffered ~18 GB into RAM with 0
   disk writes. `export HF_HUB_DISABLE_XET=1` + classic downloader = ~185 MB/s, reliable.
2. **`HF_HOME` on this box is `/workspace/.hf_home`**, not `/root/.cache/huggingface`.
3. **torchvision is mandatory** even for text-only calibration (the multimodal `AutoProcessor`
   imports the Qwen3VL video processor). Missing it crashes *after* the ~13 min load+linearize.
4. **No checkpointing** — `oneshot` restarts from scratch on any failure. Get the env right first.
5. **cu126 torch, never cu130.** Driver 525 is CUDA 12.0; CUDA 13 needs driver ≥580.

## Serve with vLLM (separate env)

```bash
pip install "vllm==0.25.1"
vllm serve /root/Cyber-F1-AWQ-W4A16 \
  --dtype bfloat16 --gpu-memory-utilization 0.90 \
  --max-model-len 32768 --enable-prefix-caching \
  --served-model-name cyber-f1-awq
```

`compressed-tensors` is auto-detected from `config.json` — no `--quantization` flag needed.
