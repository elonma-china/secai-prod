#!/usr/bin/env bash
# Cài `systemd --user` cho trang phiên bản container (chỉ xem) và snapshot sổ phiên bản mỗi giờ.
#
#   scripts/install-versions-ui.sh            # cài + bật: secai-versions-ui.service, secai-versions-snapshot.timer
#   scripts/install-versions-ui.sh --remove   # tắt + gỡ
#
# Runner (~/.local/bin/secai-versions-run) mỗi lần chạy lấy scripts/versions.py + deploy/services.json từ
# origin/main MỚI NHẤT (git archive) vào ~/.local/lib/secai-versions/<lệnh>/, nên không phụ thuộc checkout chính
# đang ở commit nào và không đụng file của nó. Cờ behind-main dùng git của checkout chính (SECAI_REPO).
# UI chỉ bind 127.0.0.1:18190; xem từ máy khác: ssh -L 18190:127.0.0.1:18190 <máy>. Chi tiết: docs/operations/container-versions.md.
# Chỉ cài được sau khi versions.py đã nằm trên origin/main. Cần `loginctl enable-linger $USER` (đã bật).
set -euo pipefail

UNIT_DIR=$HOME/.config/systemd/user
RUNNER=$HOME/.local/bin/secai-versions-run
LIB=$HOME/.local/lib/secai-versions
UNITS="secai-versions-ui.service secai-versions-snapshot.service secai-versions-snapshot.timer"

if [ "${1:-}" = "--remove" ]; then
    systemctl --user disable --now secai-versions-ui.service secai-versions-snapshot.timer 2>/dev/null || true
    for u in $UNITS; do rm -f "$UNIT_DIR/$u"; done
    rm -rf "$RUNNER" "$LIB"
    systemctl --user daemon-reload
    echo "đã gỡ secai-versions-ui và secai-versions-snapshot (sổ versions.jsonl giữ nguyên)"
    exit 0
fi

MAIN=$(git worktree list --porcelain | sed -n '1s/^worktree //p')
[ -n "$MAIN" ] || { echo "install-versions-ui: chạy trong repo" >&2; exit 2; }
git -C "$MAIN" cat-file -e origin/main:scripts/versions.py 2>/dev/null \
    || { echo "install-versions-ui: origin/main chưa có scripts/versions.py; merge PR trước" >&2; exit 2; }

mkdir -p "$UNIT_DIR" "$(dirname "$RUNNER")"

cat > "$RUNNER" <<EOF
#!/usr/bin/env bash
# Sinh bởi scripts/install-versions-ui.sh — chạy versions.py của origin/main; repo: $MAIN.
set -euo pipefail
cmd=\${1:?thiếu lệnh}
cd "$MAIN"
git fetch -q origin || echo "secai-versions-run: git fetch lỗi, dùng origin/main đang có" >&2
dest=$LIB/\$cmd
rm -rf "\$dest.new"; mkdir -p "\$dest.new"
git archive origin/main scripts/versions.py deploy/services.json | tar -x -C "\$dest.new"
rm -rf "\$dest"; mv "\$dest.new" "\$dest"
export SECAI_REPO="$MAIN"
exec python3 -I "\$dest/scripts/versions.py" "\$@"
EOF
chmod 755 "$RUNNER"

cat > "$UNIT_DIR/secai-versions-ui.service" <<EOF
[Unit]
Description=Trang phiên bản container secai-agent-gateway (chỉ xem, 127.0.0.1:18190)

[Service]
ExecStart=$RUNNER serve --port 18190
Restart=on-failure
RestartSec=10s
NoNewPrivileges=yes
Nice=10

[Install]
WantedBy=default.target
EOF

cat > "$UNIT_DIR/secai-versions-snapshot.service" <<EOF
[Unit]
Description=Snapshot sổ phiên bản container secai-agent-gateway (baseline/observed)

[Service]
Type=oneshot
ExecStart=$RUNNER snapshot
NoNewPrivileges=yes
Nice=10
EOF

cat > "$UNIT_DIR/secai-versions-snapshot.timer" <<'EOF'
[Unit]
Description=Snapshot sổ phiên bản container mỗi giờ (secai-agent-gateway)

[Timer]
OnBootSec=5min
OnUnitActiveSec=1h
RandomizedDelaySec=2min

[Install]
WantedBy=timers.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now secai-versions-ui.service secai-versions-snapshot.timer
systemctl --user --no-pager status secai-versions-ui.service | head -5 || true
systemctl --user list-timers secai-versions-snapshot.timer --no-pager | head -3
echo "UI: http://127.0.0.1:18190/ (từ máy khác: ssh -L 18190:127.0.0.1:18190 <máy>); snapshot tay: systemctl --user start secai-versions-snapshot.service"
