Cấu hình phần cứng:

Các lệnh `nvidia-smi`, `free -h`, `lsblk`, `df -h` là **kịch bản chạy trực tiếp trên máy chủ nghiệm thu**; output của các lệnh này **không có dưới dạng file trong hồ sơ gốc phần AI** (ghi chú của dòng: "phần này nên cho hình ảnh chứng minh của cấu hình phần cứng").

Yêu cầu phần cứng lớp serving được ghi trong hồ sơ bàn giao tại **`04. Huong_dan_cai_dat/HUONG-DAN-CAI-DAT.md`** — dòng 28–35:

```
### 1.2. Serving GPU (thuê trên vast.ai)

| Hạng mục | Yêu cầu |
|---|---|
| GPU | 2 × NVIDIA A100 80 GB |
| Dung lượng ổ đĩa | Khoảng 100 GB (image và trọng số mô hình ~24 GB) |
| Chi phí tham khảo | Khoảng 1,8–2 USD mỗi giờ |
| Máy điều khiển (chạy script thuê) | Chỉ cần `python3` và `curl`; công cụ vastai được cài tự động |
```

Hạ tầng đo thực tế cũng được ghi trong báo cáo hiệu năng **`03. Kpi_results/Perf/perf/reports/performance-report.md`** — dòng 2:

```
> Hạ tầng: 2× NVIDIA A100 80GB (80GB) | LLM DuyTa/Cyber-F1-AWQ (vLLM, FP16) @ip-vps-gpu | Embedding DuyTa/sec-embedding @63483 | Reranker DuyTa/sec-rerank @49356 | backend secAI localhost:4096
```

**Kết luận:** Kịch bản chạy lệnh trực tiếp trên máy chủ nghiệm thu (không có file output trong hồ sơ gốc, cần ảnh chụp màn hình); cấu hình phần cứng lớp serving 2× NVIDIA A100 80GB được ghi trong hướng dẫn cài đặt (dòng 28–35) và báo cáo hiệu năng (dòng 2).
