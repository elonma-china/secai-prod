# E2E — exploit-v2

Test thật (không mock) cho luồng "Sinh PoC (v2)": nhập CVE+target trên UI → gateway →
pentestcode (@ias/server) tự khai thác → kết quả hiện trên UI. Cần 4 process chạy sẵn trước khi
`npx playwright test`:

1. **pentestcode / @ias/server** (sidecar, xem `secai-agent-gateway` plan
   `merry-yawning-quasar.md`):
   ```bash
   cd /home/ict/AIEngines/secai/ias.ai/pentestcode
   OPENCODE_ENABLE_QUESTION_TOOL=true bun run --cwd packages/opencode src/index.ts serve --port 4096 --hostname 0.0.0.0
   ```
2. **Gateway** (test instance, KHÔNG phải port production 8131):
   ```bash
   cd /home/ict/AIEngines/secai/secai-agent-gateway
   IAS_SERVER_URL=http://127.0.0.1:4096 VERIFY_ALLOW_ALL=true \
     .venv/bin/python3 -m uvicorn secai_gateway.app:app --host 127.0.0.1 --port 8199
   ```
3. **Lab target** (lỗi command-injection tối giản, chỉ stdlib, không cần CVE thật):
   ```bash
   python3 /tmp/exploit-v2-lab/app.py   # 127.0.0.1:9191, flag tại /tmp/exploit-v2-lab/flag.txt
   ```
   Nếu file lab không còn, xem nội dung mẫu trong `e2e/fixtures/lab-app.py` (copy sang
   `/tmp/exploit-v2-lab/app.py` rồi chạy).
4. **FE dev server**, trỏ về gateway test instance ở bước 2:
   ```bash
   cd /home/ict/AIEngines/secai/secai-agent-gateway/secai-fe-devspace
   VITE_API_BASE=http://127.0.0.1:8199 npx vite --host 0.0.0.0 --port 5555
   ```

Rồi chạy:
```bash
npx playwright test
# hoặc trỏ base URL khác:
PLAYWRIGHT_BASE_URL=http://127.0.0.1:5555 npx playwright test
```

## Vì sao không mock pentestcode

Model (`orca-local/orcarouter/flashnext-uncensored`) đôi khi flaky — dừng sau "reasoning" mà
không hành động (quan sát thực tế 2026-10-31, lần chạy đầu fail, lần 2 thành công). Test
`exploit-v2.spec.ts` set `test.describe.configure({ retries: 2 })` cho đúng case gọi model thật,
và assert theo 2 nhánh hợp lệ (ok HOẶC fail-với-lý-do-rõ-ràng) thay vì chỉ assert "luôn thành
công" — phản ánh đúng bản chất non-deterministic của LLM, không che giấu nó.
