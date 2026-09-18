#!/usr/bin/env python3
"""Kiểm chứng "Effective Context giữ chính xác đến 12k tokens" của chatbot SecAI.

Cách đo: needle-in-haystack — chèn 1 dữ kiện ở ĐẦU hội thoại, bơm filler cho ngữ
cảnh phình dần, rồi hỏi lại dữ kiện đó tại từng mốc token. Nếu câu trả lời vẫn
chứa giá trị đúng => ngữ cảnh còn nguyên (chưa bị nén/cắt) => nhớ chính xác.

Dùng /ai/chat: lịch sử hội thoại được linch giữ trong Redis theo user_id, nên các
lượt sau thấy được dữ kiện của lượt đầu.

Chạy:  python3 check_effective_context.py [BASE_URL] [MAX_TOKENS]
Ví dụ: python3 check_effective_context.py http://127.0.0.1:8131 20000
"""
import json, sys, time, urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8131"
MAX_TOKENS = int(sys.argv[2]) if len(sys.argv) > 2 else 17000
UID = f"ctxcheck-{int(time.time())}"

# Needle: dữ kiện kỹ thuật tự nhiên (KHÔNG dùng "mã bí mật/chỉ thị/chỉ trả lời..."
# vì sẽ bị bộ lọc injection chặn, làm sai phép đo).
SRV, PORT = "HELIOS-42", "9787"
NEEDLE_Q = (f"Trong hạ tầng của tôi có một máy chủ tên {SRV} đang chạy dịch vụ web "
            f"ở cổng {PORT}. Bạn ghi nhận thông tin này nhé.")
RECALL_Q = f"Máy chủ {SRV} tôi nhắc ở đầu cuộc trò chuyện đang chạy dịch vụ web ở cổng số mấy?"
FILLER = ("Cho tôi hỏi về phòng thủ chiều sâu: phân đoạn mạng, kiểm soát truy cập "
          "tối thiểu, giám sát SIEM, quản lý bản vá và ứng phó sự cố hoạt động ra "
          "sao trong doanh nghiệp? " * 55)
MARKS = [4000, 8000, 12000, 16000]          # các mốc token cần kiểm
est_tokens = lambda chars: chars // 3        # ước lượng thô (tiếng Việt ~3 ký tự/token)


def chat(msg: str) -> str:
    req = urllib.request.Request(
        f"{BASE}/ai/chat",
        data=json.dumps({"user_id": UID, "message": msg}).encode(),
        headers={"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req, timeout=120)).get("response", "")


def main() -> int:
    chars = 0
    r = chat(NEEDLE_Q); chars += len(NEEDLE_Q) + len(r)
    print(f"Đã chèn needle: {SRV} -> cổng {PORT}\n")
    done, ok_all, turn = set(), True, 0
    while est_tokens(chars) < MAX_TOKENS + 1000:
        turn += 1
        r = chat(FILLER); chars += len(FILLER) + len(r)
        tok = est_tokens(chars)
        for m in MARKS:
            if tok >= m and m not in done:
                done.add(m)
                ans = chat(RECALL_Q); chars += len(RECALL_Q) + len(ans)
                ok = PORT in ans; ok_all &= ok
                print(f"~{tok:>6} tokens | recall cổng {PORT}: "
                      f"{'ĐÚNG ✅' if ok else 'SAI ❌'} | model: {ans[:70].strip()!r}")
    print("\nKẾT LUẬN:", "GIỮ CHÍNH XÁC tại mọi mốc ✅" if ok_all else "CÓ MỐC SAI ❌")
    return 0 if ok_all else 1


if __name__ == "__main__":
    sys.exit(main())
