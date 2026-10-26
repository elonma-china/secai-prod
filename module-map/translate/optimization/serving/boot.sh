#!/usr/bin/env bash
# Khởi động MT (vLLM BF16, 18770 -> adapter 18868) + OCR (vLLM, 18771 -> adapter 18083) và LÀM ẤM bằng request thật
# (nhiều mức đồng thời) TRƯỚC khi ghi /workspace/fastmt/READY. Request đầu tiên của người dùng không phải chờ nạp/biên dịch kernel.
# Idempotent: chạy lại sẽ khởi động lại cả hai dịch vụ.
set -u
cd /workspace/fastmt
rm -f READY
export PATH=/usr/local/bin:/usr/bin:/bin:$PATH
bash /workspace/fastmt/start_vllm.sh
bash /workspace/fastmt/start_ocr_vllm.sh
/workspace/secai-specialist-services/venv/bin/python - <<'PY' || echo "warmup FAILED"
import asyncio, base64, io, json, time, httpx
async def main():
    async with httpx.AsyncClient(timeout=120) as c:
        t0 = time.time()
        async def mt(n):
            r = await c.post("http://127.0.0.1:18770/v1/completions", json={"model": "hymt", "prompt": f"Translate to Vietnamese: warmup sentence number {n}, with some technical words like firewall and server.", "max_tokens": 48, "temperature": 0})
            r.raise_for_status()
        for conc in (1, 8, 64, 128):
            await asyncio.gather(*[mt(i) for i in range(conc)])
        print("mt warm", round(time.time() - t0, 1), flush=True)
        from PIL import Image, ImageDraw
        im = Image.new("RGB", (900, 300), "white"); d = ImageDraw.Draw(im)
        for i in range(6): d.text((20, 20 + i * 40), f"Warmup line {i}: password P@ss!w0rd#2026 ip 192.168.10.25:8443", fill="black")
        buf = io.BytesIO(); im.save(buf, "PNG"); b64 = base64.b64encode(buf.getvalue()).decode()
        async def ocr(n):
            r = await c.post("http://127.0.0.1:18771/v1/chat/completions", json={"model": "ocr", "max_tokens": 64, "temperature": 0, "messages": [{"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": "data:image/png;base64," + b64}}, {"type": "text", "text": "Read all text."}]}]})
            r.raise_for_status()
        t1 = time.time()
        for conc in (1, 4, 16):
            await asyncio.gather(*[ocr(i) for i in range(conc)])
        print("ocr warm", round(time.time() - t1, 1), flush=True)
asyncio.run(main())
PY
curl -s -m 5 http://127.0.0.1:18868/health; echo; curl -s -m 5 http://127.0.0.1:18083/health; echo
date -Is > /workspace/fastmt/READY
echo "READY $(cat /workspace/fastmt/READY)"
