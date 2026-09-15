# TensorBoard — chạy dạng systemd service (auto-start sau reboot)

TensorBoard phục vụ log finetuning tại `:6006` với `--path_prefix=/showcase-tb`, để gateway
reverse-proxy `/showcase-tb/*` sang. Trước đây nó chạy dạng process rời (mất khi reboot). Unit
`tensorboard-showcase.service` cho nó tự chạy lại.

## Cài (chạy một lần, cần sudo)

```bash
# 1. Cài unit
sudo cp /home/ict/AIEngines/secai/secai-agent-gateway/deploy/tensorboard-showcase.service /etc/systemd/system/
sudo systemctl daemon-reload

# 2. Dừng TB rời đang chạy để nhường cổng 6006 (lệnh này không cần sudo)
pkill -f "tensorboard.*showcase-tb"

# 3. Bật + chạy (tự khởi động khi reboot)
sudo systemctl enable --now tensorboard-showcase
```

## Kiểm tra

```bash
systemctl status tensorboard-showcase --no-pager
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:6006/showcase-tb/     # 200
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8131/showcase-tb/     # 200 (qua gateway)
```

## Ghi chú

- logdir: `/home/ict/AIEngines/secai/FINETUNE/ho_so_ft-finetuning/dashboards/tensorboard`.
- Đổi logdir/port thì sửa `ExecStart` trong unit rồi `sudo systemctl daemon-reload && sudo systemctl restart tensorboard-showcase`.
- Gỡ: `sudo systemctl disable --now tensorboard-showcase`.
