"""Normalize agentic (tool-call) chat records into the typed-content format the
Qwen3-30B-A3B-Instruct-2507 multimodal processor can template. Flattens assistant tool_calls and tool
results into text so the whole conversation renders through the real chat template.
"""
import json


def normalize_messages(messages):
    out = []
    for m in messages:
        role = m.get("role")
        content = m.get("content")
        if role == "tool":
            name = m.get("name", "")
            txt = "[tool_result name=" + str(name) + "]\n" + (content or "")
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
        out.append({"role": role, "content": [{"type": "text", "text": txt}]})
    return out


if __name__ == "__main__":
    import sys
    import statistics
    from transformers import AutoProcessor

    model_id = sys.argv[1] if len(sys.argv) > 1 else "DuyTa/Cyber-F1"
    path = sys.argv[2] if len(sys.argv) > 2 else "/root/calib_cyber_vi.jsonl"
    proc = AutoProcessor.from_pretrained(model_id)

    recs = [json.loads(l) for l in open(path)]
    ok = fail = 0
    lens = []
    errs = {}
    for r in recs:
        try:
            out = proc.apply_chat_template(
                normalize_messages(r["messages"]),
                tokenize=True, return_dict=True, return_tensors="pt",
                padding=False, truncation=True, max_length=2048,
                add_special_tokens=False, add_generation_prompt=False,
            )
            lens.append(out["input_ids"].shape[-1])
            ok += 1
        except Exception as e:
            fail += 1
            errs[type(e).__name__] = str(e)[:140]
    print("normalized processor template: OK %d/%d fail %d" % (ok, len(recs), fail))
    if lens:
        print("token lens: min %d max %d mean %d | ==2048(truncated): %d"
              % (min(lens), max(lens), int(statistics.mean(lens)),
                 sum(1 for x in lens if x >= 2048)))
    if errs:
        print("errors:", errs)
