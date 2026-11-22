---
name: serving-vast-check
description: Kiểm kế hoạch và test offline cho secai-serving-vast (5 model, A100 1..4 GPU) mà không thuê GPU.
---

# Kiểm chứng serving Vast (offline)

`secai-serving-vast/` là Git repo riêng — commit riêng, không stage private config vào repo gateway.

```sh
cd secai-serving-vast
./serving-up-cyber-f1.sh --dry-run --gpus 1
./serving-up-cyber-f1.sh --dry-run --gpus 2 --specialist-gpu 1
./serving-up-cyber-f1.sh --dry-run --gpus 4 --specialist-gpu 3
python3 -m unittest discover -s tests -v
bash -n serving-up-cyber-f1.sh serving-specialists-install.sh vast/on_start_cyber_f1.sh
git diff --check
```

Quy tắc: chỉ chạy `--yes` khi đã được phép thuê và có ngân sách; không thay embedding/rerank bằng model stock; không hạ guard 8192 MiB free GPU phụ; `/health` không chứng minh model hoạt động.
