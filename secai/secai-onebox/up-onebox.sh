#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════════
#  up-onebox.sh — thuê 1 máy vast.ai VM (Ubuntu 22.04, 2×A100 80GB) rồi bung CẢ KHỐI
#  secai-onebox (serving + DB + gateway) bằng docker compose bên trong VM.
#
#  Tái dùng helper vast từ ../secai-agent-gateway/secai-serving-vast/lib.sh
#  (ensure_vastai / ensure_apikey / list_instances).
#
#  Vì sao VM (không phải Docker template): chỉ VM mode có systemd → chạy được
#  `docker` + `docker compose` với NVIDIA toolkit bên trong. Docker template mặc
#  định của vast KHÔNG bảo đảm docker-in-docker.
#
#  Dùng:
#     ./up-onebox.sh --dry-run                 # tìm offer + in lệnh create (KHÔNG thuê)
#     ./up-onebox.sh --yes --template <HASH>   # thuê + provision + up
#     ./up-onebox.sh --offer <ID> --template <HASH> --yes
#  Cờ: --max-price(3.5) --disk(160) --min-ram(128) --gpus(2) --key <ssh_privkey>
#      --seed (chạy seed-data.sh sau khi VM sẵn sàng) --no-provision
#
#  ⚠ --template <HASH>: hash của template "Ubuntu 22.04 VM" trong tài khoản vast của
#    bạn. Lấy ở https://cloud.vast.ai/templates/ (chọn VM template Ubuntu 22.04 →
#    Copy template hash), hoặc tạo từ image docker.io/vastai/kvm. VM-create CLI khác
#    Docker-create nên PHẢI truyền hash này; script không đoán hộ.
# ═══════════════════════════════════════════════════════════════════════════════
set -uo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"; cd "$ROOT"
LIB="$ROOT/../secai-agent-gateway/secai-serving-vast/lib.sh"
[ -f "$LIB" ] || { echo "thiếu $LIB (cần helper vast)"; exit 1; }
. "$LIB"

OFFER=""; YES=0; DRY=0; MAX_PRICE="3.5"; DISK=160; MIN_RAM=128; GPUS=2
TEMPLATE="${VAST_VM_TEMPLATE:-98fff0d0a96048e83c19da789fe7a979}"   # Ubuntu 22.04 VM
SSH_KEY="${VAST_SSH_KEY:-$HOME/.ssh/id_ed25519}"
DO_SEED=0; PROVISION=1; LABEL="secai-onebox"
while [ $# -gt 0 ]; do case "$1" in
  --offer) OFFER="$2"; shift 2;;
  --yes) YES=1; shift;;
  --dry-run) DRY=1; shift;;
  --template) TEMPLATE="$2"; shift 2;;
  --max-price) MAX_PRICE="$2"; shift 2;;
  --disk) DISK="$2"; shift 2;;
  --min-ram) MIN_RAM="$2"; shift 2;;
  --gpus) GPUS="$2"; shift 2;;
  --key) SSH_KEY="$2"; shift 2;;
  --seed) DO_SEED=1; shift;;
  --no-provision) PROVISION=0; shift;;
  -h|--help) sed -n '2,26p' "$0"; exit 0;;
  *) die "tham số lạ: $1" "xem ./up-onebox.sh --help";;
esac; done

say "══ Preflight ══"; ensure_tools; ensure_vastai; ensure_apikey

# ── đọc port 8131 (gateway) đã map + ip ──────────────────────────────────────
gw_read(){ # <instance_id> → "ip port state"
  $VASTAI show instance "$1" --raw 2>/dev/null | python3 -c '
import json,sys
try: d=json.load(sys.stdin)
except Exception: print("- - GONE"); raise SystemExit
if not isinstance(d,dict) or d.get("error"): print("- - GONE"); raise SystemExit
p=d.get("ports") or {}
def hp(x):
    for k,v in p.items():
        if k.split("/")[0]==x and v: return v[0].get("HostPort","-")
    return "-"
print(d.get("public_ipaddr") or "-", hp("8131"), d.get("actual_status") or d.get("cur_state") or "unknown")'
}

# ── chọn offer 2×A100 VM-capable, RAM/disk cao ───────────────────────────────
QUERY="num_gpus=$GPUS gpu_name in [A100_SXM4,A100_PCIE] gpu_ram>=80 disk_space>=$DISK cpu_ram>=$((MIN_RAM*1024)) dph_total<=$MAX_PRICE inet_down>=3000 rentable=true verified=true"
search_table(){
  $VASTAI search offers "$1" -o dph_total --raw 2>/dev/null | python3 -c '
import json,sys
try: d=json.load(sys.stdin)
except Exception: raise SystemExit
d=d if isinstance(d,list) else d.get("offers",[])
for i,o in enumerate(d[:10],1):
    print("%2d) id=%-9s %-10s x%s ram=%4.0fG disk=%4.0fG ↓%5.0f $%.3f/h rel=%.3f %s"%(
      i,o["id"],o.get("gpu_name",""),o.get("num_gpus"),(o.get("cpu_ram") or 0)/1024,
      o.get("disk_space") or 0,o.get("inet_down") or 0,o.get("dph_total") or 0,
      o.get("reliability2") or 0,str(o.get("geolocation"))[:18]))'
}
if [ -z "$OFFER" ]; then
  say "══ Search offer ${GPUS}×A100 80GB · RAM≥${MIN_RAM}G · disk≥${DISK}G · ≤\$$MAX_PRICE/h ══"
  TABLE="$(search_table "$QUERY")"
  [ -z "$TABLE" ] && { warn "0 offer verified — thử KHÔNG verified..."; TABLE="$(search_table "${QUERY/ verified=true/}")"; }
  [ -n "$TABLE" ] || die "không có offer khớp" "nới --max-price / --min-ram / --disk"
  echo "$TABLE"
  OFFER="$(echo "$TABLE" | head -1 | sed 's/.*id=\([0-9]*\).*/\1/')"
fi
say "offer chọn: $OFFER"

# ── lệnh create VM (⚠ CẦN --template hash VM Ubuntu 22.04) ────────────────────
if [ -z "$TEMPLATE" ]; then
  warn "chưa có --template <VM_TEMPLATE_HASH>. VM-create bắt buộc có template hash."
  warn "lấy tại https://cloud.vast.ai/templates/ (VM Ubuntu 22.04) rồi truyền --template <hash>."
fi
CREATE_CMD="$VASTAI create instance $OFFER --template_hash ${TEMPLATE} --disk $DISK --label $LABEL --ssh --direct --env '-p 8131:8131'"

if [ "$DRY" = "1" ] || [ "$YES" != "1" ]; then
  say "══ DRY-RUN — lệnh create (KHÔNG thuê) ══"; echo "  $CREATE_CMD"
  echo
  say "sau khi VM 'running' + SSH sẵn sàng, provision sẽ:"
  echo "  1) rsync $ROOT + ../secai-agent-gateway → VM:/opt/secai/"
  echo "  2) ssh: sudo bash /opt/secai/secai-onebox/onstart-onebox.sh   (cài docker+toolkit, up)"
  echo "  3) (tuỳ chọn --seed) ./seed-data.sh đẩy ~23GB data lên Local Volume"
  echo "  4) đọc PUBLIC_IP:PORT của 8131 → URL gateway"
  [ "$YES" != "1" ] && say "chạy lại với --yes --template <HASH> để thực thi."
  exit 0
fi

[ -n "$TEMPLATE" ] || die "cần --template <VM_TEMPLATE_HASH> để thuê VM" "xem --help"
say "══ CREATE (đang TÍNH TIỀN) ══"; echo "  $CREATE_CMD"
IID="$($CREATE_CMD --raw 2>/dev/null | python3 -c 'import json,sys; print(json.load(sys.stdin).get("new_contract",""))')"
[ -n "$IID" ] || die "create thất bại — kiểm tra template hash / offer / số dư."
ok "instance id=$IID"

# ── chờ SSH sẵn sàng ─────────────────────────────────────────────────────────
say "chờ VM boot + SSH..."
for _ in $(seq 1 60); do
  read -r ip port st <<<"$(gw_read "$IID")"
  [ "$st" = "running" ] && [ "$ip" != "-" ] && break
  sleep 15
done
read -r IP PORT ST <<<"$(gw_read "$IID")"
[ "$IP" != "-" ] || die "VM chưa có IP sau khi chờ" "kiểm tra console vast"
SSH="ssh -p ${PORT:-22} -i $SSH_KEY -o StrictHostKeyChecking=accept-new root@$IP"
say "SSH: $SSH"
for _ in $(seq 1 40); do $SSH true 2>/dev/null && break; sleep 10; done

if [ "$PROVISION" = "1" ]; then
  say "rsync onebox + gateway source lên VM:/opt/secai/..."
  $SSH "mkdir -p /opt/secai" || die "không SSH được vào VM"
  rsync -ah --delete -e "ssh -p ${PORT:-22} -i $SSH_KEY -o StrictHostKeyChecking=accept-new" \
    --exclude '.git' --exclude '__pycache__' --exclude '.venv' \
    "$ROOT/" "root@$IP:/opt/secai/secai-onebox/"
  rsync -ah -e "ssh -p ${PORT:-22} -i $SSH_KEY -o StrictHostKeyChecking=accept-new" \
    --exclude '.git' --exclude '__pycache__' --exclude '.venv' --exclude 'data' --exclude '*.log' \
    "$ROOT/../secai-agent-gateway/" "root@$IP:/opt/secai/secai-agent-gateway/"
  say "chạy onstart trên VM (cài docker+toolkit, build+up)..."
  $SSH "sudo bash /opt/secai/secai-onebox/onstart-onebox.sh"
fi

if [ "$DO_SEED" = "1" ]; then
  say "seed data (~23GB) lên VM..."
  bash "$ROOT/seed-data.sh" --host "$IP" --port "${PORT:-22}" --key "$SSH_KEY"
  $SSH "cd /opt/secai/secai-onebox && docker compose restart qdrant neo4j gateway" || true
fi

read -r IP2 P2 _ <<<"$(gw_read "$IID")"
ok "XONG. Gateway (đợi model healthy) → http://$IP2:$P2/health"
say "theo dõi model load: $SSH 'cd /opt/secai/secai-onebox && docker compose logs -f secai-serving'"
