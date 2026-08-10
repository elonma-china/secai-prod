# Executor "môi trường thực thi câm" cho PoC-Verify (docs/poc-verify-architecture.md §5-§6).
# Chỉ chứa TOOLS + runner mỏng; điều phối/authz/đánh giá ở gateway (linch), KHÔNG ở đây.
#
# LƯU Ý MÔI TRƯỜNG BUILD: Docker Hub (registry-1.docker.io) bị CHẶN ở máy này → không kéo được
# `kalilinux/kali-rolling`. Đây là bản "Kali-equivalent trên Debian": base Debian lấy qua mirror
# AWS ECR (pull được), engine cài từ nguồn tới được (deb.debian.org, apt.metasploit.com,
# GitHub releases, PyPI). Bộ engine giữ NGUYÊN theo bảng §4. Ở nơi có Docker Hub, có thể đổi
# dòng FROM về `kalilinux/kali-rolling` và cài nuclei/metasploit-framework qua apt Kali.
FROM public.ecr.aws/docker/library/debian:bookworm-slim

ENV DEBIAN_FRONTEND=noninteractive \
    LANG=C.UTF-8 \
    PIP_NO_CACHE_DIR=1

# apt: tool + runtime (deb.debian.org).
RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates curl git gnupg unzip tini \
      python3 python3-venv python3-pip \
      nmap sqlmap \
 && rm -rf /var/lib/apt/lists/*

# metasploit-framework: apt repo chính thức Rapid7 (apt.metasploit.com; omnibus, bundled ruby).
# msfinstall không còn tải được (404/403) → tự thêm repo như msfinstall vẫn làm. Dist `lucid`.
RUN curl -fsSL https://apt.metasploit.com/metasploit-framework.gpg.key \
      | gpg --dearmor -o /usr/share/keyrings/metasploit.gpg \
 && echo "deb [signed-by=/usr/share/keyrings/metasploit.gpg] https://apt.metasploit.com/ lucid main" \
      > /etc/apt/sources.list.d/metasploit.list \
 && apt-get update \
 && apt-get install -y --no-install-recommends metasploit-framework \
 && rm -rf /var/lib/apt/lists/*

# nuclei: binary release mới nhất từ GitHub.
RUN set -eux; \
    VER="$(curl -fsSL https://api.github.com/repos/projectdiscovery/nuclei/releases/latest \
           | grep -oE '"tag_name": *"v[^"]+"' | grep -oE 'v[0-9.]+' | head -1)"; \
    VNUM="${VER#v}"; \
    curl -fsSL -o /tmp/nuclei.zip \
      "https://github.com/projectdiscovery/nuclei/releases/download/${VER}/nuclei_${VNUM}_linux_amd64.zip"; \
    unzip -o /tmp/nuclei.zip -d /usr/local/bin nuclei; \
    rm -f /tmp/nuclei.zip; \
    /usr/local/bin/nuclei -version -duc

# pocsuite3: venv riêng (tránh PEP 668 externally-managed).
RUN python3 -m venv /opt/pocsuite \
 && /opt/pocsuite/bin/pip install --upgrade pip \
 && /opt/pocsuite/bin/pip install pocsuite3
ENV PATH="/opt/pocsuite/bin:${PATH}"

# ── Bộ pentest cốt lõi ───────────────────────────────────────────────────────
# apt (deb.debian.org): recon/web · brute-force · SMB/AD · network utils.
RUN apt-get update && apt-get install -y --no-install-recommends \
      perl samba-common-bin \
      gobuster dirb wfuzz whatweb wafw00f \
      hydra john hashcat medusa hashid \
      smbclient ldap-utils dnsrecon nbtscan onesixtyone snmp \
      masscan ncat netcat-traditional socat tcpdump dnsutils whois iputils-ping \
 && rm -rf /var/lib/apt/lists/*

# venv /opt/pentools: impacket (PyPI, bắt buộc) + enum4linux-ng (git-only → clone+wrapper,
# bắt buộc) + netexec (git-only, fork crackmapexec → best-effort, không làm fail build).
RUN python3 -m venv /opt/pentools \
 && /opt/pentools/bin/pip install --upgrade pip \
 && /opt/pentools/bin/pip install impacket ldap3 pyyaml \
 && git clone --depth=1 https://github.com/cddmp/enum4linux-ng /opt/enum4linux-ng \
 && printf '#!/bin/sh\nexec /opt/pentools/bin/python3 /opt/enum4linux-ng/enum4linux-ng.py "$@"\n' \
      > /usr/local/bin/enum4linux-ng \
 && chmod +x /usr/local/bin/enum4linux-ng \
 && ( /opt/pentools/bin/pip install "git+https://github.com/Pennyw0rth/NetExec.git" \
      || echo "[skip] netexec: build từ git thất bại — dùng impacket cho AD/SMB" )
ENV PATH="/opt/pentools/bin:${PATH}"

# ffuf: binary release GitHub (không có trong apt bookworm).
RUN set -eux; \
    VER="$(curl -fsSL https://api.github.com/repos/ffuf/ffuf/releases/latest \
           | grep -oE '"tag_name": *"v[^"]+"' | grep -oE 'v[0-9.]+' | head -1)"; \
    VNUM="${VER#v}"; \
    curl -fsSL -o /tmp/ffuf.tgz \
      "https://github.com/ffuf/ffuf/releases/download/${VER}/ffuf_${VNUM}_linux_amd64.tar.gz"; \
    tar -xzf /tmp/ffuf.tgz -C /usr/local/bin ffuf; \
    rm -f /tmp/ffuf.tgz; ffuf -V

# nikto + Responder: git clone (không có trong apt Debian).
RUN git clone --depth=1 https://github.com/sullo/nikto /opt/nikto \
 && ln -s /opt/nikto/program/nikto.pl /usr/local/bin/nikto \
 && git clone --depth=1 https://github.com/lgandx/Responder /opt/Responder \
 && printf '#!/bin/sh\ncd /opt/Responder && exec python3 Responder.py "$@"\n' > /usr/local/bin/responder \
 && chmod +x /usr/local/bin/responder

# Exploit-DB: searchsploit + kho exploit/shellcode (repo chính thức, không có trong apt).
# /usr/share/exploitdb là đường DB mặc định searchsploit tìm → symlink về bản clone.
RUN git clone --depth=1 https://gitlab.com/exploit-database/exploitdb.git /opt/exploitdb \
 && ln -sf /opt/exploitdb/searchsploit /usr/local/bin/searchsploit \
 && ln -sf /opt/exploitdb /usr/share/exploitdb \
 && test -f /opt/exploitdb/files_exploits.csv

# Người dùng không đặc quyền để chạy engine.
RUN useradd -m -u 10001 -s /bin/bash runner

COPY entrypoint.sh /usr/local/bin/entrypoint.sh
COPY runners/ /usr/local/lib/executor/
RUN chmod +x /usr/local/bin/entrypoint.sh /usr/local/lib/executor/*.sh

WORKDIR /work
USER runner

# tini làm PID 1 (reap zombie khi engine spawn tiến trình con).
ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/entrypoint.sh"]
CMD ["selftest"]
