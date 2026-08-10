# secai-kali-executor

**Executor container** cho kiến trúc PoC-Verify (`secai-agent-gateway/docs/poc-verify-architecture.md`).
Là **"môi trường thực thi câm"** (§5–§6): chỉ chứa **engine + runner mỏng**, chạy 1 lệnh verify vào
target rồi trả output thô. **Không** chứa điều phối / authz / đánh giá — những phần đó thuộc gateway
(linch).

> Trạng thái: **đã build & verify (selftest 6/6). Standalone, CHƯA nối vào `secai-agent-gateway`** —
> xem mục *Kết nối với dự án*.

## Khả năng (đã kiểm chứng)

Image `secai-kali-executor:latest` ≈ **1.03 GB**. `make selftest` → **verify engine 6/6 OK**;
`docker run --rm secai-kali-executor tools` → in kho công cụ đầy đủ.

**Verify engines (bảng §4):**

| Engine | Version | Vai trò |
|---|---|---|
| **nuclei** | v3.11.1 | verify turn-key — JSONL matched |
| **metasploit** (`msfconsole`/`msfvenom`) | 6.5.2 | `check` turn-key (KHÔNG exploit) |
| **pocsuite3** | 2.1.0 | verify PoC dạng POCBase |
| **sqlmap** | 1.7.2 | verify SQLi |
| **searchsploit** (Exploit-DB) | 47k+ exploit | tra cứu exploit/shellcode **offline** (kho baked-in tại `/usr/share/exploitdb`) |

**Bộ pentest cốt lõi (đã thêm):**

| Nhóm | Công cụ |
|---|---|
| Recon / web | `nmap` `masscan` `nikto` `whatweb` `wafw00f` `gobuster` `dirb` `ffuf` `wfuzz` `dnsrecon` `nbtscan` `onesixtyone` |
| Brute / crack | `hydra` `medusa` `john` `hashcat` `hashid` |
| SMB / AD | `smbclient` `rpcclient` `enum4linux-ng` `ldapsearch` `responder` `snmpwalk` + impacket (`secretsdump.py` `psexec.py` `wmiexec.py` `GetNPUsers.py` `GetUserSPNs.py` `ntlmrelayx.py`…) |
| Net utils | `ncat` `nc` `socat` `tcpdump` `dig` `whois` `python3` `git` `curl` |

**Chưa có:** `netexec`/`nxc` (build từ git cần **Rust compiler** cho dep `aardwolf`/RDP → bỏ để
image gọn; impacket đã phủ AD/SMB). Muốn có: thêm `rustc cargo` vào Dockerfile rồi cài
`git+https://github.com/Pennyw0rth/NetExec.git`.

⚠️ Công cụ cần **raw socket / root** (`masscan`, `responder`, `tcpdump`, một phần `nmap -sS`)
KHÔNG chạy dưới `run.sh` hardened (`--cap-drop ALL`, non-root) — có trong image nhưng cần cấp
`--cap-add NET_RAW`/`NET_ADMIN` riêng khi dùng. Đúng nguyên tắc verify-only §5.

## Nền tảng image (lưu ý môi trường build)

Máy build hiện tại **chặn Docker Hub** (`registry-1.docker.io` timeout) → **không kéo được
`kalilinux/kali-rolling`**. Vì vậy image là **"Kali-equivalent trên Debian"**, lấy từ các nguồn tới được:

| Thành phần | Nguồn |
|---|---|
| base `debian:bookworm-slim` | AWS ECR mirror (`public.ecr.aws/docker/library/...`) |
| `nmap`, `sqlmap`, python, tini… | apt Debian (`deb.debian.org`) |
| `metasploit-framework` | apt repo Rapid7 (`apt.metasploit.com`, dist `lucid`, omnibus + bundled ruby) |
| `nuclei` | binary release GitHub (`projectdiscovery/nuclei`) |
| `pocsuite3` | PyPI, trong venv `/opt/pocsuite` (tránh PEP 668) |

Bộ engine **giữ nguyên theo bảng §4**. Ở môi trường có Docker Hub, chỉ cần đổi dòng `FROM` trong
`Dockerfile` về `kalilinux/kali-rolling` và cài `nuclei`/`metasploit-framework` qua apt Kali.

## Build & kiểm tra

```bash
make build         # docker build -t secai-kali-executor:latest .
make selftest      # in version mọi engine — phải 6/6 OK
make size
make shell         # bash debug bên trong image (không hardening)
```

## Chạy verify (ephemeral + hardening §5)

`run.sh` bọc `docker run` với: `--read-only`, tmpfs `mode=1777` cho `/tmp /work /home/runner`
(đủ quyền để `msfconsole` ghi `~/.msf4`), `--cap-drop ALL`, `--security-opt no-new-privileges`,
`--pids-limit`, giới hạn `--memory/--cpus`, timeout trong container, `--rm` (huỷ sau mỗi run),
chạy bằng user không đặc quyền `runner` (uid 10001).

```bash
# nuclei: nạp template read-only rồi verify
MOUNTS="-v /path/to/tpl.yaml:/input/tpl.yaml:ro" \
  ./run.sh nuclei --template /input/tpl.yaml --target http://TARGET

# metasploit check (verify, KHÔNG exploit)
./run.sh msf-check --module exploit/linux/http/some_module --target TARGET --rport 8080

# pocsuite3 (PoC dạng POCBase)
MOUNTS="-v /path/to/poc.py:/input/poc.py:ro" \
  ./run.sh pocsuite --poc /input/poc.py --target http://TARGET

# selftest qua đường hardened (không cần mạng)
NET=none ./run.sh selftest
```

Biến môi trường (đầu `run.sh`): `IMAGE NET EXEC_TIMEOUT MEM CPUS PIDS MOUNTS`.

### Lệnh trong container

Do `entrypoint.sh` điều phối (mỗi lệnh engine bị bọc `timeout ${EXEC_TIMEOUT}s`):

| Lệnh | Cú pháp |
|---|---|
| `selftest` | in version verify engine (gate 6/6) |
| `tools` | liệt kê toàn bộ kho công cụ đã cài |
| `nuclei` | `nuclei --template <path> --target <url> [extra]` |
| `msf-check` | `msf-check --module <fullname> --target <host> [--rport N] [--set 'K V']` |
| `pocsuite` | `pocsuite --poc <path> --target <url> [extra]` |
| `searchsploit` | `searchsploit <term>` — tra cứu Exploit-DB offline |
| `raw` | passthrough để debug (`raw <cmd...>`) |

## Mạng (điểm cần ops quyết định)

`run.sh` mặc định `NET=bridge` → container tới được **target VÀ internet**. Yêu cầu §5
*"egress chỉ tới target"* **không** ép được thuần bằng cờ `docker run` (Docker không lọc theo IP đích).
Hai cách đạt được:

1. **Firewall host** trên bridge của container (iptables/nftables): DROP mọi egress trừ `<target>` —
   cách đúng cho production. (Ops task, chưa script hoá ở đây.)
2. **`NET=none` + reverse proxy/socat** chỉ mở đường tới target — phức tạp hơn.

Tạm thời cho lab nội bộ: `NET=bridge`. **Không** phơi ra target ngoài phạm vi được phép.
Phần hardening còn lại của §5 đã bật sẵn trong `run.sh`.

## Ngoài phạm vi (đúng doc §8)

- attack / getshell / lateral movement (runner chỉ verify).
- Session bền nhiều bước của msf.
- Chạy PoC không map được vào engine có CLI (exploitdb `.py` rời / github tuỳ ý).

## Kết nối với dự án (để bạn thống nhất sau)

Hiện **decoupled**. Hai kiểu tích hợp vào `secai-agent-gateway`:

- **(A) Ephemeral exec — khuyến nghị, khớp §5.** `poc/runners/*` (nuclei/msf/pocsuite) trong gateway
  gọi `docker run … secai-kali-executor <engine> …` mỗi lần verify; container sinh–huỷ theo run.
  Cô lập tốt nhất, không có state chung.
- **(B) Microservice thường trú.** Container chạy nền, mở API nhỏ (HTTP/gRPC); gateway gọi qua mạng.
  Ít overhead khởi động nhưng cô lập kém hơn và cần thêm authz ở tầng API.

Điểm nối phía gateway (chưa làm — doc §7): `poc/runners/`, `poc/dispatch.py`,
tool `poc_verify(ref, target)`, endpoint `POST /ai/verify`.

## Cấu trúc

```
secai-kali-executor/
  Dockerfile          # image: base Debian(ECR) + nuclei/msf/pocsuite3/sqlmap/nmap
  entrypoint.sh       # dispatcher trong container (bọc timeout mỗi lệnh)
  runners/
    selftest.sh       # in version mọi engine
    run-nuclei.sh     # nuclei -jsonl -duc -ni
    run-msf.sh        # msfconsole -q -n -x "use…; check; exit"
    run-pocsuite.sh   # pocsuite -r … -u … --verify
  run.sh              # wrapper host: ephemeral + hardening §5
  Makefile            # build / selftest / shell / size / clean
```
