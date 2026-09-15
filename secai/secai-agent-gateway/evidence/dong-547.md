Requests Per Second (RPS): ~0,6 RPS

**`03. Kpi_results/Perf/perf/ccu_rps.json`** — dòng 1–7 (các trường dòng 2–7 mà kịch bản nêu: concurrency, n, ok, errors, wall_s, rps):

```
{
  "concurrency": 30,
  "n": 90,
  "ok": 90,
  "errors": 0,
  "wall_s": 128.3,
  "rps": 0.701,
```

**Kết luận:** Đối chiếu được: `ccu_rps.json` ghi 30 luồng đồng thời, 90/90 yêu cầu thành công, 0 lỗi và RPS = 0.701, đạt ngưỡng ~0,6 RPS.
