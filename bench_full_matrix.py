"""3 kịch bản test trên container TEST (18195, không phải production):
A) File dài (123 đoạn thật, long-30p.docx) dịch sang 1 ngôn ngữ, tuần tự.
B) File dài dịch sang NHIỀU ngôn ngữ (cùng nội dung, 5 lang) để test đa ngữ.
C) Nhiều câu/nhiều "user" gọi đồng thời (mix ngôn ngữ khác nhau cùng lúc).
"""
import asyncio
import json
import time

import httpx

BASE = "http://127.0.0.1:18195/ai/v3/translate"
BLOCKS_PATH = ("/tmp/claude-1000/-home-ict-AIEngines-secai-secai-agent-gateway/"
               "f5647c66-87a3-4622-8082-3e95b9d130a9/scratchpad/translate-perf/blocks.json")


async def translate_one(client: httpx.AsyncClient, text: str, source: str, target: str):
    t0 = time.time()
    try:
        r = await client.post(BASE, json={"text": text, "source_lang": source, "target_lang": target},
                              timeout=60)
        d = r.json()
        ok = "translated_text" in d
        return ok, d.get("error", {}).get("code") if not ok else None, time.time() - t0
    except Exception as e:
        return False, str(e), time.time() - t0


async def case_a_long_file(client, texts, target="ja"):
    print(f"\n=== A) File dài: {len(texts)} đoạn thật -> {target}, tuần tự ===")
    t0 = time.time()
    ok_count, fail_codes = 0, []
    for i, t in enumerate(texts, 1):
        ok, err, dur = await translate_one(client, t, "vi", target)
        ok_count += ok
        if not ok:
            fail_codes.append(err)
        if i % 10 == 0 or dur > 10:
            print(f"  [{i}/{len(texts)}] ok={ok_count} last_dur={dur:.1f}s elapsed={time.time()-t0:.0f}s",
                  flush=True)
    total = time.time() - t0
    print(f"OK={ok_count}/{len(texts)} fail={fail_codes[:5]} total={total:.1f}s "
          f"throughput={len(texts)/total:.2f} blocks/s")


async def case_b_multilang_file(client, texts, langs=("en", "ja", "ko", "zh-Hans", "ar")):
    print(f"\n=== B) File dài: {len(texts)} đoạn -> {len(langs)} ngôn ngữ song song (đa ngữ) ===")
    t0 = time.time()
    results = {}
    for lang in langs:
        sem = asyncio.Semaphore(8)
        async def one(t):
            async with sem:
                return await translate_one(client, t, "vi", lang)
        outs = await asyncio.gather(*(one(t) for t in texts))
        ok = sum(o[0] for o in outs)
        results[lang] = ok
    total = time.time() - t0
    for lang, ok in results.items():
        print(f"  {lang:10} OK={ok}/{len(texts)}")
    print(f"total={total:.1f}s for {len(langs)} languages x {len(texts)} blocks "
          f"= {len(langs)*len(texts)} calls, {len(langs)*len(texts)/total:.2f} calls/s")


async def case_c_concurrent_users(client, n_users=20):
    print(f"\n=== C) {n_users} 'user' gọi dịch đồng thời, mix ngôn ngữ khác nhau ===")
    samples = [
        ("Lỗ hổng bảo mật nghiêm trọng được phát hiện hôm nay.", "ja"),
        ("Khuyến nghị cập nhật bản vá ngay lập tức.", "en"),
        ("Hệ thống bị xâm nhập qua lỗ hổng xác thực.", "ko"),
        ("CVE-2026-20127 ảnh hưởng tới Cisco Catalyst SD-WAN.", "zh-Hans"),
        ("Nhà cung cấp đã phát hành bản vá chính thức.", "ar"),
        ("Giám sát log để phát hiện dấu hiệu bất thường.", "ru"),
        ("Hạn chế truy cập quản trị từ Internet.", "fr"),
        ("Điểm CVSS của lỗ hổng này là 9.8.", "de"),
        ("Kẻ tấn công có thể chiếm quyền kiểm soát hoàn toàn.", "es"),
        ("Áp dụng xác thực đa yếu tố cho tài khoản quản trị.", "ja"),
    ]
    t0 = time.time()
    tasks = [translate_one(client, samples[i % len(samples)][0], "vi", samples[i % len(samples)][1])
             for i in range(n_users)]
    results = await asyncio.gather(*tasks)
    total = time.time() - t0
    ok = sum(r[0] for r in results)
    latencies = [r[2] for r in results]
    print(f"OK={ok}/{n_users} total_wall={total:.1f}s "
          f"avg_latency={sum(latencies)/len(latencies):.2f}s max_latency={max(latencies):.2f}s")
    for r in results:
        if not r[0]:
            print("  FAIL:", r[1])


async def main():
    with open(BLOCKS_PATH) as f:
        cases = json.load(f)
    long_texts = cases["long30p"]
    async with httpx.AsyncClient() as client:
        await case_a_long_file(client, long_texts, target="ja")
        await case_b_multilang_file(client, long_texts[:30])  # 30 đoạn x 5 lang để đỡ lâu
        await case_c_concurrent_users(client, n_users=20)


if __name__ == "__main__":
    asyncio.run(main())
