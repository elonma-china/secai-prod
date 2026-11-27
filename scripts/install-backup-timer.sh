#!/usr/bin/env bash
# Cài `systemd --user` timer backup SQLite hằng ngày (scripts/backup-sqlite.py) lúc 01:30–01:45.
#
#   scripts/install-backup-timer.sh            # cài + bật secai-sqlite-backup.timer
#   scripts/install-backup-timer.sh --remove   # tắt + gỡ unit/runner (bản backup đã có giữ nguyên)
#
# Giờ chạy tránh 03:00–05:00 (cron đồng bộ CVE lúc 03:00). Persistent=false: máy bật lại lúc 03–05 thì không
# chạy bù vào khung đó; lỡ một đêm thì OPS-BAK-2 (≤ 26 giờ) báo FAIL, chạy tay:
# systemctl --user start secai-sqlite-backup.service.
# Runner (~/.local/bin/secai-sqlite-backup-run) mỗi lần chạy lấy scripts/backup-sqlite.py từ origin/main MỚI NHẤT
# (git archive) vào ~/.local/lib/secai-sqlite-backup/, nên không phụ thuộc checkout chính đang ở commit nào và
# không đụng file của nó; git fetch lỗi thì dùng origin/main đang có.
# Đích: SECAI_BACKUP_DIR lúc cài (mặc định ~/secai-backups, trùng mặc định của scripts/ops-check.py), DB vào <đích>/sqlite/.
# Chỉ cài được sau khi backup-sqlite.py đã nằm trên origin/main. Cần `loginctl enable-linger $USER` (đã bật) và
# user thuộc nhóm docker. Chi tiết: docs/operations/sqlite-backup.md.
set -euo pipefail

UNIT=secai-sqlite-backup
UNIT_DIR=$HOME/.config/systemd/user
RUNNER=$HOME/.local/bin/secai-sqlite-backup-run
LIB=$HOME/.local/lib/secai-sqlite-backup
BACKUP_DIR=${SECAI_BACKUP_DIR:-$HOME/secai-backups}

if [ "${1:-}" = "--remove" ]; then
    systemctl --user disable --now "$UNIT.timer" 2>/dev/null || true
    rm -f "$UNIT_DIR/$UNIT.service" "$UNIT_DIR/$UNIT.timer" "$RUNNER"
    rm -rf "$LIB"
    systemctl --user daemon-reload
    echo "đã gỡ $UNIT.timer (bản backup trong $BACKUP_DIR giữ nguyên)"
    exit 0
fi
if [ $# -gt 0 ]; then
    echo "dùng: $0 [--remove]" >&2
    exit 2
fi

case $BACKUP_DIR in
    /*) ;;
    *) echo "install-backup-timer: SECAI_BACKUP_DIR phải là đường dẫn tuyệt đối: $BACKUP_DIR" >&2; exit 2 ;;
esac
MAIN=$(git worktree list --porcelain | sed -n '1s/^worktree //p')
[ -n "$MAIN" ] || { echo "install-backup-timer: chạy trong repo" >&2; exit 2; }
git -C "$MAIN" cat-file -e origin/main:scripts/backup-sqlite.py 2>/dev/null \
    || { echo "install-backup-timer: origin/main chưa có scripts/backup-sqlite.py; merge PR trước" >&2; exit 2; }

mkdir -p "$UNIT_DIR" "$(dirname "$RUNNER")"

cat > "$RUNNER" <<EOF
#!/usr/bin/env bash
# Sinh bởi scripts/install-backup-timer.sh — chạy backup-sqlite.py của origin/main; repo: $MAIN.
set -euo pipefail
cd "$MAIN"
git fetch -q origin || echo "secai-sqlite-backup-run: git fetch lỗi, dùng origin/main đang có" >&2
rm -rf "$LIB.new"; mkdir -p "$LIB.new"
git archive origin/main scripts/backup-sqlite.py | tar -x -C "$LIB.new"
rm -rf "$LIB"; mv "$LIB.new" "$LIB"
export SECAI_BACKUP_DIR="$BACKUP_DIR"
exec python3 -I "$LIB/scripts/backup-sqlite.py" "\$@"
EOF
chmod 755 "$RUNNER"

cat > "$UNIT_DIR/$UNIT.service" <<EOF
[Unit]
Description=Backup SQLite hằng ngày của service secai (report/review/gateway/session/dịch)

[Service]
Type=oneshot
ExecStart=$RUNNER
Environment=SECAI_BACKUP_DIR=$BACKUP_DIR
TimeoutStartSec=3h
NoNewPrivileges=yes
Nice=10
EOF

cat > "$UNIT_DIR/$UNIT.timer" <<'EOF'
[Unit]
Description=Backup SQLite secai hằng ngày 01:30–01:45 (tránh 03:00–05:00)

[Timer]
OnCalendar=*-*-* 01:30:00
RandomizedDelaySec=15min
AccuracySec=1min
Persistent=false

[Install]
WantedBy=timers.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now "$UNIT.timer"
systemctl --user list-timers "$UNIT.timer" --no-pager | head -3
echo "đích: $BACKUP_DIR/sqlite/   chạy tay: systemctl --user start $UNIT.service   log: journalctl --user -u $UNIT.service"
