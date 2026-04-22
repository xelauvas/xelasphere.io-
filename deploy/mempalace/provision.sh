#!/usr/bin/env bash
#
# MemPalace provisioning — idempotent.
#
# Implements spec §7.3 provisioning checklist. Safe to re-run; each step
# tests for its own completion before acting. Requires root.
#
# Spec references:
#   §7.1 Artifacts and FHS layout  — directories, ownership, modes
#   §7.2 systemd unit invariants   — mempalace-sidecar.service
#   §7.3 Provisioning checklist    — the steps this script implements
#   §7.4 Staged rollout            — phase semantics (off/shadow/active)

set -euo pipefail

# --- Locations (spec §7.1) ---
SIDECAR_ENV=/etc/mempalace/sidecar.env
FLASK_ENV=/etc/xelasphere/mempalace.env
PALACES_DIR=/var/mempalace/palaces
KG_PENDING=/var/mempalace/kg_pending.sqlite
AUDIT_DB=/var/mempalace/audit.sqlite
SHADOW_LOGS=/var/mempalace/shadow_logs
# /run/mempalace and /run/mempalace/prom are managed by systemd
# (RuntimeDirectory= in mempalace-sidecar.service), not this script.
LOG_DIR=/var/log/mempalace

# --- Install locations ---
SIDECAR_VENV=/opt/mempalace-sidecar/venv
SIDECAR_WHEEL=${SIDECAR_WHEEL:-}  # path to wheel if installing; optional
UNIT_SRC="$(dirname "$0")/mempalace-sidecar.service"
FLUSHER_UNIT_SRC="$(dirname "$0")/mempalace-flusher.service"
MODE_SCRIPT_SRC="$(dirname "$0")/mempalace-mode"

# Step 1 — mempalace system user (spec §7.3 step 1)
if ! id -u mempalace >/dev/null 2>&1; then
    echo "[provision] creating mempalace system user"
    useradd -r -s /usr/sbin/nologin mempalace
else
    echo "[provision] mempalace user exists; skip"
fi

# Step 2 — add xelasphere to mempalace group (spec §7.3 step 2)
if ! id -Gn xelasphere 2>/dev/null | grep -qw mempalace; then
    echo "[provision] adding xelasphere to mempalace group"
    usermod -aG mempalace xelasphere
else
    echo "[provision] xelasphere already in mempalace group; skip"
fi

# Step 3 — directories with ownership/mode from spec §7.1.
# /run/mempalace and /run/mempalace/prom are intentionally NOT created
# here: systemd's RuntimeDirectory=mempalace on the sidecar unit owns
# that tmpfs lifecycle (survives reboots). ExecStartPre in the unit
# creates /run/mempalace/prom.
echo "[provision] creating directory tree"
install -d -o mempalace -g mempalace -m 0750 "$PALACES_DIR"
install -d -o mempalace -g mempalace -m 0750 "$SHADOW_LOGS"
install -d -o mempalace -g mempalace -m 0750 "$LOG_DIR"
install -d -o mempalace -g mempalace -m 0750 "$(dirname "$KG_PENDING")"
install -d -o root      -g mempalace -m 0750 /etc/mempalace
install -d -o root      -g xelasphere -m 0750 /etc/xelasphere

# Step 4 — generate MEMPALACE_TOKEN and write to both env files atomically
if [ ! -f "$SIDECAR_ENV" ] || ! grep -q '^MEMPALACE_TOKEN=' "$SIDECAR_ENV"; then
    echo "[provision] generating MEMPALACE_TOKEN and writing env files"
    TOKEN=$(openssl rand -hex 32)

    # Sidecar env file
    TMP_SIDECAR=$(mktemp)
    if [ -f "$SIDECAR_ENV" ]; then
        grep -v '^MEMPALACE_TOKEN=' "$SIDECAR_ENV" > "$TMP_SIDECAR" || true
    fi
    echo "MEMPALACE_TOKEN=$TOKEN" >> "$TMP_SIDECAR"
    install -m 0640 -o root -g mempalace "$TMP_SIDECAR" "$SIDECAR_ENV"
    rm -f "$TMP_SIDECAR"

    # Flask env file
    TMP_FLASK=$(mktemp)
    if [ -f "$FLASK_ENV" ]; then
        grep -v '^MEMPALACE_TOKEN=' "$FLASK_ENV" > "$TMP_FLASK" || true
    fi
    echo "MEMPALACE_TOKEN=$TOKEN" >> "$TMP_FLASK"
    install -m 0640 -o root -g xelasphere "$TMP_FLASK" "$FLASK_ENV"
    rm -f "$TMP_FLASK"
else
    echo "[provision] MEMPALACE_TOKEN present in $SIDECAR_ENV; skip"
fi

# Step 5 — install sidecar wheel to /opt/mempalace-sidecar/venv (optional)
if [ -n "$SIDECAR_WHEEL" ] && [ ! -x "$SIDECAR_VENV/bin/mempalace-sidecar" ]; then
    echo "[provision] installing sidecar wheel: $SIDECAR_WHEEL"
    install -d -o root -g root -m 0755 /opt/mempalace-sidecar
    python3 -m venv "$SIDECAR_VENV"
    "$SIDECAR_VENV/bin/pip" install --upgrade pip
    "$SIDECAR_VENV/bin/pip" install "$SIDECAR_WHEEL"
else
    if [ -x "$SIDECAR_VENV/bin/mempalace-sidecar" ]; then
        echo "[provision] sidecar binary present; skip install"
    else
        echo "[provision] SIDECAR_WHEEL unset and no existing binary; manual install required"
    fi
fi

# Install systemd units + mode script
if [ -f "$UNIT_SRC" ]; then
    install -m 0644 "$UNIT_SRC" /etc/systemd/system/mempalace-sidecar.service
fi
if [ -f "$FLUSHER_UNIT_SRC" ]; then
    install -m 0644 "$FLUSHER_UNIT_SRC" /etc/systemd/system/mempalace-flusher.service
fi
if [ -f "$MODE_SCRIPT_SRC" ]; then
    install -m 0755 -o root -g root "$MODE_SCRIPT_SRC" /usr/local/bin/mempalace-mode
fi

# Step 6 — enable + start sidecar
echo "[provision] systemctl daemon-reload + enable sidecar"
systemctl daemon-reload
systemctl enable --now mempalace-sidecar

# Step 7 — wait for /readyz OK
echo "[provision] waiting for sidecar /readyz ..."
for i in $(seq 1 30); do
    if curl -sf --unix-socket /run/mempalace/sidecar.sock http://localhost/readyz >/dev/null 2>&1; then
        echo "[provision] sidecar /readyz OK"
        break
    fi
    sleep 1
    if [ "$i" -eq 30 ]; then
        echo "[provision] ERROR: sidecar /readyz did not respond within 30s" >&2
        exit 1
    fi
done

# Enable flusher (after sidecar is ready so flusher doesn't fight for the socket)
if [ -f /etc/systemd/system/mempalace-flusher.service ]; then
    systemctl enable --now mempalace-flusher
fi

# Step 8 — restart xelasphere; gunicorn picks up new group membership only
# on restart (not SIGHUP/SIGUSR2). Spec §7.3 step 8 is non-optional.
echo "[provision] restarting xelasphere to pick up mempalace group membership"
systemctl restart xelasphere

# Post-check: verify gunicorn master is now in the mempalace group.
GUNICORN_PID=$(pgrep -f 'gunicorn: master' || true)
if [ -z "$GUNICORN_PID" ]; then
    echo "[provision] WARN: gunicorn master not found; skipping group-membership post-check" >&2
else
    if [ "$(grep -c '^Groups:.*mempalace' "/proc/$GUNICORN_PID/status")" -eq 1 ]; then
        echo "[provision] verified: gunicorn master has mempalace group membership"
    else
        echo "[provision] ERROR: gunicorn master missing mempalace group; rollback required" >&2
        exit 2
    fi
fi

echo "[provision] done."
