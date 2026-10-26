"""Locust load test — 30 CCU thật, qua toàn bộ pipeline production (/ai/chat).
Dùng đúng bộ 90 câu hỏi bảo mật KHÁC NHAU đã ĐÓNG BĂNG tại data/gold_questions_91.json
(cùng file, cùng bộ câu hỏi với bench_fullpipeline.py — không tự suy ra từ testset-v2
mỗi lần chạy, nên tái lập được y hệt dù testset-v2 gốc có đổi sau này). Chạy: đúng
3×A100 (nginx đã giới hạn), rate-limit /ai/chat = 150 (đủ cho 30 CCU, xem middleware.py).

  .venv/bin/locust -f locustfile_30ccu.py --headless -u 30 -r 6 -t 90s \
    --host http://localhost:8131 --csv=load_30ccu --html=load_30ccu_report.html
"""
import json
import random
from pathlib import Path
from locust import HttpUser, task, between

DATA_FILE = Path(__file__).parent / "data" / "gold_questions_91.json"
QUESTIONS = json.loads(DATA_FILE.read_text(encoding="utf-8"))["questions"]


class ChatUser(HttpUser):
    wait_time = between(0, 0)  # bắn liên tục, không nghỉ giữa request — đúng kịch bản stress test

    @task
    def ask(self):
        q = random.choice(QUESTIONS)
        self.client.post(
            "/ai/chat",
            json={"message": q, "user_id": f"locust-{random.randint(0, 999999)}"},
            headers={"Content-Type": "application/json"},
            timeout=90,
            name="/ai/chat",
        )
