"""netns_watch.py CMD...: chạy CMD; thoát 1 khi netns chung đã mất (chỉ còn lo).

translation-worker dùng network_mode service:translation-gateway. Docker khởi động lại translation-gateway thì
netns cũ chỉ còn `lo`: worker không tới được converter hay tunnel mà vẫn "running". Thoát để restart policy
cho worker nhập netns mới. Chuyển SIGTERM/SIGINT cho CMD, trả mã thoát của CMD.
"""
import os
import signal
import subprocess
import sys
import time

NET = os.environ.get("NETNS_WATCH_DIR", "/sys/class/net")
INTERVAL = float(os.environ.get("NETNS_WATCH_INTERVAL", "5"))


def main(cmd):
    p = subprocess.Popen(cmd)
    for s in (signal.SIGTERM, signal.SIGINT):
        signal.signal(s, lambda *_: p.send_signal(signal.SIGTERM))
    while p.poll() is None:
        if set(os.listdir(NET)) <= {"lo"}:
            print("netns_watch: netns của translation-gateway đã mất; thoát để Docker nối lại", file=sys.stderr, flush=True)
            p.terminate()
            try:
                p.wait(30)
            except subprocess.TimeoutExpired:
                p.kill()
            return 1
        time.sleep(INTERVAL)
    return p.returncode


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
