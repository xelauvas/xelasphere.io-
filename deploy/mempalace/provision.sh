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

# --- Installed env-file locations ---
SIDECAR_ENV=/etc/mempalace/sidecar.env
FLASK_ENV=/etc/xelasphere/mempalace.env

# --- Template locations (source of truth; see M0.27) ---
SIDECAR_ENV_TEMPLATE="$(dirname "$0")/sidecar.env.example"
FLASK_ENV_TEMPLATE="$(dirname "$0")/mempalace.env.example"

# --- State directory locations (spec §7.1) ---
DATA_DIR=/var/mempalace
LOG_DIR=/var/log/mempalace
FLASK_STATE_DIR=/var/lib/xelasphere
# /run/mempalace and /run/mempalace/prom are managed by systemd
# (RuntimeDirectory= in mempalace-sidecar.service), not this script.
# /run/xelasphere and /run/xelasphere/prom likewise for the flusher unit.

# --- Runtime literals (change here, not inline) ---
SIDECAR_SOCKET=/run/mempalace/sidecar.sock

# --- Install locations ---
SIDECAR_VENV=/opt/mempalace-sidecar/venv
SIDECAR_WHEEL=${SIDECAR_WHEEL:-}  # path to wheel if installing; optional
UNIT_SRC="$(dirname "$0")/mempalace-sidecar.service"
FLUSHER_UNIT_SRC="$(dirname "$0")/mempalace-flusher.service"
MODE_SCRIPT_SRC="$(dirname "$0")/mempalace-mode"
HEALTH_SCRIPT_SRC="$(dirname "$0")/mempalace-shadow-health"
HEALTH_SERVICE_SRC="$(dirname "$0")/mempalace-shadow-health.service"
HEALTH_TIMER_SRC="$(dirname "$0")/mempalace-shadow-health.timer"

# Step 1 — mempalace system user (spec §7.3 step 1)
if ! id -u mempalace >/dev/null 2>&1; then
    echo "[provision] creating mempalace system user"
    useradd -r -s /usr/sbin/nologin mempalace
else
    echo "[provision] mempalace user exists; skip"
fi

# Step 2 — add xelasphere to mempalace group (spec §7.3 step 2).
# At M0.27 the xelasphere user is a precondition (pre-created by operator
# per the M0.26 tombstone — see #6 for the Flask FHS migration); guard so
# a missing xelasphere user doesn't fail the script silently.
if ! id -u xelasphere >/dev/null 2>&1; then
    echo "[provision] ERROR: xelasphere system user does not exist." >&2
    echo "[provision] Create it with:" >&2
    echo "[provision]   useradd --system --no-create-home --shell /usr/sbin/nologin xelasphere" >&2
    exit 3
fi
if ! id -Gn xelasphere | grep -qw mempalace; then
    echo "[provision] adding xelasphere to mempalace group"
    usermod -aG mempalace xelasphere
else
    echo "[provision] xelasphere already in mempalace group; skip"
fi

# Step 3 — directories with ownership/mode from spec §7.1.
echo "[provision] creating directory tree"
install -d -o mempalace  -g mempalace  -m 0750 "$DATA_DIR"
install -d -o mempalace  -g mempalace  -m 0750 "$LOG_DIR"
install -d -o root       -g mempalace  -m 0750 /etc/mempalace
install -d -o root       -g xelasphere -m 0750 /etc/xelasphere
# /var/lib/xelasphere — outbox.db home. xelasphere:xelasphere so the
# flusher (User=xelasphere) can open+write the SQLite DB post-FHS. Flask
# currently runs as root and bypasses DAC, so it can write regardless.
install -d -o xelasphere -g xelasphere -m 0750 "$FLASK_STATE_DIR"

# Step 4 — install env files from templates with secret substitution.
#
# M0.27 architecture change: provision.sh no longer owns the env-file
# schema; it cp's the templates and substitutes TODO placeholders only.
# Templates are the source of truth (deploy/mempalace/*.env.example);
# provision.sh is the substitution mechanism.
#
# Generated values are idempotent: if the installed env file already has
# a real (non-TODO) value for a placeholder, we keep it rather than
# regenerating (which would rotate secrets silently on every re-run).

_install_env_from_template() {
    # args: <template_path> <dest_path> <owner> <group> <key=value>...
    local template="$1" dest="$2" owner="$3" group="$4"
    shift 4

    if [ ! -f "$template" ]; then
        echo "[provision] ERROR: missing template $template" >&2
        exit 4
    fi

    # Start from the template
    local tmp
    tmp=$(mktemp)
    cp "$template" "$tmp"

    # Apply each key=value substitution, replacing lines of the form
    # `KEY=<TODO...>` with `KEY=VALUE`. If the installed file already has
    # a real value for KEY, prefer that (idempotent secrets).
    local pair key value existing
    for pair in "$@"; do
        key="${pair%%=*}"
        value="${pair#*=}"
        existing=""
        if [ -f "$dest" ]; then
            existing=$(grep -E "^${key}=" "$dest" | head -1 | cut -d= -f2- || true)
        fi
        if [ -n "$existing" ] && ! echo "$existing" | grep -q '^<TODO'; then
            value="$existing"
        fi
        # Escape / for sed delimiter (paths are fine with | as delim, tokens are hex)
        sed -i "s|^${key}=<TODO.*|${key}=${value}|" "$tmp"
    done

    install -m 0640 -o "$owner" -g "$group" "$tmp" "$dest"
    rm -f "$tmp"
}

# Generate secrets once. Re-runs preserve existing values.
TOKEN=$(openssl rand -hex 32)
USER_ID_HASH_SALT=$(openssl rand -hex 32)

echo "[provision] installing env files from templates"
_install_env_from_template "$SIDECAR_ENV_TEMPLATE" "$SIDECAR_ENV" root mempalace \
    "MEMPALACE_TOKEN=$TOKEN" \
    "MEMPALACE_USER_ID_HASH_SALT=$USER_ID_HASH_SALT"

_install_env_from_template "$FLASK_ENV_TEMPLATE" "$FLASK_ENV" root xelasphere \
    "MEMPALACE_TOKEN=$TOKEN"

# Post-check: required fields present with real values in the sidecar env.
for required in MEMPALACE_TOKEN MEMPALACE_DATA_DIR MEMPALACE_USER_ID_HASH_SALT; do
    val=$(grep -E "^${required}=" "$SIDECAR_ENV" | head -1 | cut -d= -f2- || true)
    if [ -z "$val" ] || echo "$val" | grep -q '^<TODO'; then
        echo "[provision] ERROR: $SIDECAR_ENV missing or has TODO for $required" >&2
        exit 5
    fi
done

# Step 5 — install sidecar wheel to /opt/mempalace-sidecar/venv.
if [ -n "$SIDECAR_WHEEL" ]; then
    if [ ! -f "$SIDECAR_WHEEL" ]; then
        echo "[provision] ERROR: SIDECAR_WHEEL=$SIDECAR_WHEEL does not exist" >&2
        exit 6
    fi
    echo "[provision] installing sidecar wheel: $SIDECAR_WHEEL"
    install -d -o root -g root -m 0755 /opt/mempalace-sidecar
    if [ ! -x "$SIDECAR_VENV/bin/python" ]; then
        python3 -m venv "$SIDECAR_VENV"
        "$SIDECAR_VENV/bin/pip" install --upgrade pip
    fi
    "$SIDECAR_VENV/bin/pip" install --upgrade --force-reinstall "$SIDECAR_WHEEL"
else
    if [ -x "$SIDECAR_VENV/bin/mempalace-sidecar" ]; then
        echo "[provision] sidecar binary present; skip install (SIDECAR_WHEEL unset)"
    else
        echo "[provision] ERROR: SIDECAR_WHEEL unset and no existing binary at $SIDECAR_VENV" >&2
        exit 7
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
# Daily P1 shadow health check (script + service + timer).
if [ -f "$HEALTH_SCRIPT_SRC" ]; then
    install -m 0755 -o root -g root "$HEALTH_SCRIPT_SRC" /usr/local/bin/mempalace-shadow-health
fi
if [ -f "$HEALTH_SERVICE_SRC" ]; then
    install -m 0644 "$HEALTH_SERVICE_SRC" /etc/systemd/system/mempalace-shadow-health.service
fi
if [ -f "$HEALTH_TIMER_SRC" ]; then
    install -m 0644 "$HEALTH_TIMER_SRC" /etc/systemd/system/mempalace-shadow-health.timer
fi

# Step 6 — enable + start sidecar
echo "[provision] systemctl daemon-reload + enable sidecar"
systemctl daemon-reload
systemctl enable mempalace-sidecar
systemctl restart mempalace-sidecar
# Enable the daily health timer if its unit installed cleanly.
if [ -f /etc/systemd/system/mempalace-shadow-health.timer ]; then
    echo "[provision] enabling mempalace-shadow-health.timer"
    systemctl enable --now mempalace-shadow-health.timer || true
fi

# Step 7 — wait for /readyz OK
echo "[provision] waiting for sidecar /readyz ..."
for i in $(seq 1 30); do
    if curl -sf --unix-socket "$SIDECAR_SOCKET" http://localhost/readyz >/dev/null 2>&1; then
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
    systemctl enable mempalace-flusher || true
    # Don't --now enable the flusher here unconditionally: on this host the
    # flusher's User=xelasphere cannot exec /opt/xelasphere/venv/bin/python
    # until the Flask FHS migration (#6) lands. systemctl enable installs
    # the wants-symlink; a later operator pass starts it post-migration.
    echo "[provision] flusher unit enabled (not started; see P1-entry checklist)"
fi

# Step 8 — restart xelasphere; gunicorn picks up new group membership only
# on restart (not SIGHUP/SIGUSR2). Spec §7.3 step 8 is non-optional.
echo "[provision] restarting xelasphere to pick up mempalace group membership"
systemctl restart xelasphere

# Post-check: verify gunicorn can reach the UDS after restart.
#
# INTENT: "Flask gunicorn can open /run/mempalace/sidecar.sock after the
# restart." The spec §7.3 step 8 mechanism assumes Flask runs as
# User=xelasphere (in group mempalace) and relies on group-membership
# granting access to a 0660 socket. On a host where Flask runs as root
# (ad-hoc deployment not yet migrated to spec §7.1 FHS layout), root's
# DAC bypass grants access regardless of group membership, and the
# group-membership check returns a false negative.
#
# M0.27c: post-check reads xelasphere.service's User= and branches.
#   - User=root     → DAC bypass, pass with note
#   - User=xelasphere → require mempalace in supplementary groups
#   - User=<other>  → unexpected; fail with explicit rationale
#
# This is honest-about-mechanism rather than mechanism-pinning, which
# preserves correctness through the FHS migration (#6) intermediate
# states as well as before and after.
sleep 2
FLASK_USER=$(systemctl show xelasphere.service --property=User --value 2>/dev/null)
GUNICORN_PID=$(pgrep -f 'gunicorn: master' || true)

if [ -z "$GUNICORN_PID" ]; then
    echo "[provision] WARN: gunicorn master not found; skipping post-check" >&2
elif [ "$FLASK_USER" = "root" ]; then
    echo "[provision] verified: xelasphere.service User=root — UDS accessible via DAC bypass"
    echo "[provision]   (group-membership check is moot for root; see spec §7.3 / FHS issue #6)"
elif [ "$FLASK_USER" = "xelasphere" ]; then
    if [ "$(grep -c '^Groups:.*\<mempalace\>' "/proc/$GUNICORN_PID/status")" -eq 1 ]; then
        echo "[provision] verified: gunicorn master has mempalace group membership"
    else
        echo "[provision] ERROR: gunicorn master (User=xelasphere) missing mempalace group; rollback required" >&2
        exit 2
    fi
else
    echo "[provision] ERROR: xelasphere.service User=$FLASK_USER not handled by post-check" >&2
    echo "[provision]   Expected root (legacy) or xelasphere (post-FHS). Refine the check." >&2
    exit 2
fi

echo "[provision] done."
