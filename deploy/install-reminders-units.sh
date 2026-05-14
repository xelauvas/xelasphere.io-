#!/usr/bin/env bash
# Install/refresh the reminders worker systemd units.
# Run on the server as root.
set -euo pipefail

SRC_DIR="$(cd "$(dirname "$0")" && pwd)"
DEST="/etc/systemd/system"

install -m 0644 "$SRC_DIR/xelasphere-reminders.service" "$DEST/"
install -m 0644 "$SRC_DIR/xelasphere-reminders.timer"   "$DEST/"

systemctl daemon-reload
systemctl enable --now xelasphere-reminders.timer

echo "Installed. Verify:"
echo "  systemctl status xelasphere-reminders.timer"
echo "  systemctl list-timers xelasphere-reminders.timer"
echo "  journalctl -u xelasphere-reminders.service -f"
