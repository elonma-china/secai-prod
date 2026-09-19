#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════════
#  show-reasoning.sh — STREAM chain-of-thought (reasoning trace) của Cyber-F1 LLM
#
#  Gọi thẳng model THẬT trên vast (QWEN_BASE_URL trong .env), BẬT chế độ suy luận
#  (chat_template_kwargs.enable_thinking=true) rồi in real-time, tách 2 luồng:
#     🧠 SUY LUẬN  — reasoning trace (delta.reasoning), in mờ, token-by-token
#     💬 TRẢ LỜI    — câu trả lời cuối (delta.content), in sáng
#  (Gateway 8131 chạy production để enable_thinking=FALSE cho nhanh — tool này nói
#   chuyện với CÙNG con model đó nhưng bật thinking để lộ mạch suy nghĩ, dùng demo.)
#
#  Dùng:
#     ./show-reasoning.sh "9.11 và 9.9 số nào lớn hơn?"
#     ./show-reasoning.sh --system "Bạn là chuyên gia bảo mật." "CVE-2021-44228 khai thác thế nào?"
#     ./show-reasoning.sh --file prompt.txt         # đọc prompt từ file
#     ./show-reasoning.sh -m 1200 -t 0.6 "..."      # max-tokens / temperature
#     ./show-reasoning.sh --no-think "..."          # tắt suy luận (chỉ answer, để so sánh)
#     ./show-reasoning.sh --no-color "..."          # bỏ màu (khi ghi ra file)
#     echo "..." | ./show-reasoning.sh --file -      # prompt qua stdin
# ═══════════════════════════════════════════════════════════════════════════════
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
PY="$ROOT/.venv/bin/python"; [ -x "$PY" ] || PY="$(command -v python3 || true)"
[ -n "$PY" ] || { echo "[reasoning] [FAIL] không tìm thấy python (.venv/bin/python)" >&2; exit 1; }
exec env SECAI_ROOT="$ROOT" "$PY" - "$@" <<'PYEOF'
import sys, os, re, time, json

# ── args ───────────────────────────────────────────────────────────────────────
a = sys.argv[1:]
system = None; max_tokens = 1024; temperature = 0.6; think = True
color = sys.stdout.isatty(); model = None; pfile = None; parts = []
i = 0
def need(name):
    global i
    if i+1 >= len(a): sys.exit(f"[reasoning] thiếu giá trị cho {name}")
    i += 1; return a[i]
while i < len(a):
    x = a[i]
    if   x in ("-h","--help"):
        import subprocess; subprocess.run(["sed","-n","2,27p", __file__ if os.path.exists(__file__) else os.environ["SECAI_ROOT"]+"/show-reasoning.sh"]); sys.exit(0)
    elif x == "--system":            system = need(x)
    elif x in ("-m","--max-tokens"): max_tokens = int(need(x))
    elif x in ("-t","--temp","--temperature"): temperature = float(need(x))
    elif x == "--no-think":          think = False
    elif x == "--model":             model = need(x)
    elif x == "--file":              pfile = need(x)
    elif x == "--no-color":          color = False
    elif x == "--":                  parts += a[i+1:]; break
    elif x.startswith("-") and x != "-": sys.exit(f"[reasoning] tham số lạ: {x}  (xem --help)")
    else:                            parts.append(x)
    i += 1

# prompt: --file (‘-’ = stdin) ưu tiên, else phần còn lại của argv, else demo mặc định
if pfile == "-":     prompt = sys.stdin.read().strip()
elif pfile:          prompt = open(pfile, encoding="utf-8").read().strip()
elif parts:          prompt = " ".join(parts).strip()
else:                prompt = "CVE-2021-44228 (Log4Shell) là gì và cơ chế khai thác ra sao? Suy nghĩ từng bước."

# ── .env của gateway ─────────────────────────────────────────────────────────────
root = os.environ.get("SECAI_ROOT", ".")
env = {}
for ln in open(os.path.join(root, ".env"), encoding="utf-8"):
    m = re.match(r'\s*([A-Z0-9_]+)\s*=\s*(.*?)\s*$', ln)
    if m and not ln.lstrip().startswith("#"): env[m.group(1)] = m.group(2)
base = env.get("QWEN_BASE_URL"); key = env.get("QWEN_API_KEY"); model = model or env.get("QWEN_MODEL")
if not (base and key and model): sys.exit("[reasoning] thiếu QWEN_BASE_URL/QWEN_API_KEY/QWEN_MODEL trong .env")

# ── màu ──────────────────────────────────────────────────────────────────────────
DIM="\033[2;37m"; GRY="\033[90m"; GRN="\033[0;32m"; CYN="\033[0;36m"; BLD="\033[1m"; RST="\033[0m"
def c(s, code): return (code + s + RST) if color else s
def w(s):       sys.stdout.write(s); sys.stdout.flush()

# ── gọi model ────────────────────────────────────────────────────────────────────
from openai import OpenAI
cli = OpenAI(base_url=base, api_key=key, timeout=120)
print(c(f"● model  : {model}", CYN))
print(c(f"● server : {base}   (vast — thinking {'ON' if think else 'OFF'})", CYN))
print(c(f"● prompt : {prompt}", CYN))
print(c("─"*78, GRY))

def get_reason(d):   # SDK/vLLM đặt tên khác nhau tuỳ version → thử hết
    return (getattr(d, "reasoning", None)
            or getattr(d, "reasoning_content", None)
            or (getattr(d, "model_extra", None) or {}).get("reasoning_content"))

t0 = time.time(); tR = tA = None; nR = nA = 0; phase = None; usage = None
try:
    stream = cli.chat.completions.create(
        model=model,
        messages=([{"role":"system","content":system}] if system else []) + [{"role":"user","content":prompt}],
        stream=True, temperature=temperature, max_tokens=max_tokens,
        stream_options={"include_usage": True},
        extra_body={"chat_template_kwargs": {"enable_thinking": think}},
    )
    for ch in stream:
        if getattr(ch, "usage", None): usage = ch.usage
        if not ch.choices: continue
        d = ch.choices[0].delta
        r = get_reason(d)
        if r:
            if phase != "R":
                phase = "R"; tR = tR or time.time()-t0
                w("\n" + c("🧠 SUY LUẬN (reasoning trace)", BLD+GRY) + "\n")
            nR += len(r); w(c(r, DIM))
        if getattr(d, "content", None):
            if phase != "A":
                phase = "A"; tA = tA or time.time()-t0
                w("\n\n" + c("💬 TRẢ LỜI (answer)", BLD+GRN) + "\n")
            nA += len(d.content); w(d.content)
    w("\n")
except KeyboardInterrupt:
    w("\n" + c("(đã dừng)", GRY) + "\n"); sys.exit(130)
except Exception as e:
    sys.exit("\n[reasoning] [FAIL] gọi model lỗi: %s: %s" % (type(e).__name__, str(e)[:300]))

# ── thống kê ─────────────────────────────────────────────────────────────────────
dt = time.time()-t0
print(c("─"*78, GRY))
line = f"⏱  {dt:.1f}s tổng"
if tR is not None: line += f" · reasoning bắt đầu @{tR:.2f}s ({nR} ký tự)"
if tA is not None: line += f" · answer bắt đầu @{tA:.2f}s ({nA} ký tự)"
print(c(line, GRY))
if usage:
    ct = getattr(usage, "completion_tokens", None); pt = getattr(usage, "prompt_tokens", None)
    if ct: print(c(f"🔢 tokens: prompt={pt} completion={ct} · {ct/max(dt,1e-9):.1f} tok/s (gồm cả reasoning)", GRY))
PYEOF
