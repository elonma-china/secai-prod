#!/usr/bin/env bash
# on_start_cyber_f1.sh khởi các tiến trình vLLM/nginx bằng nohup rồi TỰ THOÁT khi xong
# (nó được viết cho vast.ai "on-start script" chạy trên 1 host đã sống sẵn — không phải
# để làm PID 1 của container). Nếu dùng thẳng nó làm ENTRYPOINT, PID 1 exit ngay sau khi
# setup xong → Docker coi container đã thoát → `restart: unless-stopped` khởi động lại
# từ đầu → load lại toàn bộ model vô hạn, không bao giờ "healthy". Wrapper này giữ
# container sống sau khi setup xong, không đổi logic bên trong on_start_cyber_f1.sh.
set -e
/opt/secai-serving/vast/on_start_cyber_f1.sh
echo "── entrypoint: setup xong, giữ container sống (PID 1) ──"
exec tail -f /dev/null
