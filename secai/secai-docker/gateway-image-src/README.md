# gateway-image-src — nguồn build reproducible cho image production chatbot

Đây là **bản nguồn đã kiểm thử** của gateway đang chạy production (chat-optimized, điểm 94.3/100),
tách ra để image `secai-agent-gateway:0.2.4-chatopt-repro` **tái tạo được bằng `docker build`**
(thay cho snapshot `docker commit` trước đó).

## Vì sao có thư mục này (two-scopes)
Monorepo `../secai-agent-gateway/src` (nhánh feat/exploit-advance) THIẾU input-classifier
(`sanitize/*.py` + wiring app.py/routes.py) → `docker build` từ đó sẽ MẤT classifier.
Bản `/app/src` đang chạy (nhánh classifier + chat-opt) mới đầy đủ. Thư mục này = chính bản đó,
đã verify: classifier + guard chống-rỗng/meta/aggregate + chat.md tối ưu + q036 override.

## Build & deploy
    docker build -t secai-agent-gateway:0.2.4-chatopt-repro .
    # hoặc: cd secai-docker && docker compose build gateway && docker compose up -d gateway

## Nội dung fix so với 0.2.0 gốc
- Guard `chat_final_text` (runner.complete): chống câu rỗng/deflect, meta canonical, aggregate honest — CHỈ đổi text, GIỮ API shape.
- prompt chat.md/base_system.md: meta direct-answer, output-hygiene, PoC rule.
- (Data q036/CVSS/alias nằm ở Qdrant/Neo4j, không trong image.)

## LƯU Ý
Bản này KHÔNG có exploit-advance/lane1/bughunt (nằm ở nhánh monorepo). Muốn có CẢ HAI cần
merge 2 nhánh (classifier ⋈ exploit-advance) — việc lớn, làm riêng có test.
