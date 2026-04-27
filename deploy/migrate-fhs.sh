#!/bin/bash
#
# deploy/migrate-fhs.sh — cutover script for issue #6 (Flask FHS migration).
#
# Moves Flask from /root/xelas.io/xelasphere.io- (User=root) to
# /opt/xelasphere (User=xelasphere) per spec §7.1. Idempotent at the
# stage boundaries; bails on first failure so partial state is
# inspectable rather than auto-rolled back.
#
# RUN AS root, FROM A SHELL THAT IS NOT INSIDE THE SOURCE TREE
# (e.g. cd /root before invoking; the source tree gets moved out
# from under your cwd otherwise and subsequent commands break).
#
# Usage:
#   sudo bash /root/xelas.io/xelasphere.io-/deploy/migrate-fhs.sh
#
# Stages:
#   1. Pre-flight verification (no state changes)
#   2. Stop xelasphere.service (production downtime begins)
#   3. Rsync working tree to /opt/xelasphere
#   4. Chown to xelasphere:xelasphere
#   5. Install env file at /etc/xelasphere/xelasphere.env (with path rewrite)
#   6. Install new systemd unit
#   7. Update nginx robots.txt alias
#   8. Start xelasphere.service + verify HTTP 200
#   9. Optionally restart mempalace-flusher (now that User=xelasphere can
#      traverse paths it actually needs)
#
# Rollback: see deploy/CUTOVER.md "Rollback procedure".

set -euo pipefail

# --check-only: run Stage 1 (pre-flight) and exit before any state
# changes happen. Useful for the operator to confirm the environment
# is ready (xelasphere user exists, /etc/xelasphere/ in place,
# /opt/xelasphere/ empty/absent, current shell isn't inside the source
# tree) without committing to the production downtime window. Safe to
# run as many times as you like.
CHECK_ONLY=0
case "${1:-}" in
  --check-only)
    CHECK_ONLY=1
    shift
    ;;
  -h|--help)
    sed -n '1,/^set -e/p' "$0" | sed 's|^# \?||;1d;$d'
    exit 0
    ;;
esac

OLD_DIR="/root/xelas.io/xelasphere.io-"
NEW_DIR="/opt/xelasphere"
SERVICE_USER="xelasphere"
SERVICE_GROUP="xelasphere"
ENV_TARGET="/etc/xelasphere/xelasphere.env"
UNIT_TARGET="/etc/systemd/system/xelasphere.service"
NGINX_CONF="/etc/nginx/sites-available/xelasphere.conf"
LISTEN_PORT="${XELASPHERE_PORT:-5000}"
HEALTH_TIMEOUT_SEC=30

log() { printf '\033[1;36m[migrate-fhs]\033[0m %s\n' "$*"; }
err() { printf '\033[1;31m[migrate-fhs ERROR]\033[0m %s\n' "$*" >&2; }

# ---------------------------------------------------------------------------
# Stage 1 — pre-flight
# ---------------------------------------------------------------------------
log "Stage 1: pre-flight checks"

if [[ "${EUID}" -ne 0 ]]; then
  err "must run as root (got EUID=${EUID})"
  exit 1
fi

if [[ "${PWD#${OLD_DIR}}" != "${PWD}" ]]; then
  err "current working directory is inside the source tree (${PWD})"
  err "rsync would race with your shell. cd elsewhere (e.g. cd /root) first."
  exit 1
fi

if ! id "${SERVICE_USER}" &>/dev/null; then
  err "user ${SERVICE_USER} does not exist — create it first (M0.26a forward-prep)"
  exit 1
fi

if [[ ! -d "${OLD_DIR}" ]]; then
  err "source dir ${OLD_DIR} not found"
  exit 1
fi

if [[ -d "${NEW_DIR}" ]]; then
  if [[ -n "$(ls -A "${NEW_DIR}")" ]]; then
    err "${NEW_DIR} already exists and is non-empty"
    err "if a prior run failed, inspect it manually before re-running"
    exit 1
  fi
fi

if [[ ! -d /etc/xelasphere ]]; then
  err "/etc/xelasphere/ does not exist (the flusher unit is supposed to own it)"
  err "create it manually: install -d -m 0750 -o root -g xelasphere /etc/xelasphere"
  exit 1
fi

# Snapshot the running PID + listening socket so we can compare post-restart.
PRE_PID=""
if systemctl is-active --quiet xelasphere.service; then
  PRE_PID="$(systemctl show -p MainPID --value xelasphere.service)"
  log "current xelasphere PID: ${PRE_PID}"
else
  log "xelasphere.service is not currently running (cutover will still proceed)"
fi

log "pre-flight OK"

if [[ "${CHECK_ONLY}" -eq 1 ]]; then
  log "--check-only: environment is ready for cutover. No state changed."
  log "Re-run without --check-only to perform the actual migration."
  exit 0
fi

# ---------------------------------------------------------------------------
# Stage 2 — stop service (production downtime begins here)
# ---------------------------------------------------------------------------
log "Stage 2: stopping xelasphere.service (downtime begins NOW)"
systemctl stop xelasphere.service || true
# Wait for the process to actually exit; systemctl stop returns immediately.
for i in $(seq 1 10); do
  if ! systemctl is-active --quiet xelasphere.service; then
    break
  fi
  sleep 1
done
if systemctl is-active --quiet xelasphere.service; then
  err "xelasphere.service did not stop within 10s"
  exit 1
fi
log "service stopped"

# ---------------------------------------------------------------------------
# Stage 3 — rsync working tree
# ---------------------------------------------------------------------------
log "Stage 3: rsync ${OLD_DIR} -> ${NEW_DIR}"
mkdir -p "${NEW_DIR}"
# -aHAX: archive + hardlinks + ACLs + xattrs. --delete left OFF intentionally
# so a partial run doesn't nuke the destination if it has stragglers from
# a manual debug step. Excludes: build artifacts, no-state-loss to drop.
rsync -aHAX \
  --exclude='__pycache__/' \
  --exclude='*.pyc' \
  --exclude='.pytest_cache/' \
  --exclude='node_modules/.cache/' \
  "${OLD_DIR}/" "${NEW_DIR}/"
log "rsync complete"

# ---------------------------------------------------------------------------
# Stage 4 — chown
# ---------------------------------------------------------------------------
log "Stage 4: chown ${NEW_DIR} to ${SERVICE_USER}:${SERVICE_GROUP}"
chown -R "${SERVICE_USER}:${SERVICE_GROUP}" "${NEW_DIR}"
# /opt/xelasphere itself: 0750 so /opt traversal works for everyone but
# tree contents are only readable by owner+group.
chmod 0750 "${NEW_DIR}"
log "chown complete"

# ---------------------------------------------------------------------------
# Stage 4b — venv shebang rewrite
# ---------------------------------------------------------------------------
# Python venvs hardcode the interpreter path in script shebangs at
# venv-creation time. After rsync those shebangs still point at OLD_DIR;
# any service running as SERVICE_USER (which can't traverse /root 0700)
# fails to exec gunicorn/pip/etc. with status 203/EXEC. Sweep all venv
# bin/ subtrees uniformly. Surfaced during the 2026-04-27 cutover.
log "Stage 4b: rewrite venv script shebangs (${OLD_DIR} -> ${NEW_DIR})"
hits="$(grep -rl "${OLD_DIR}" "${NEW_DIR}" 2>/dev/null | grep -E '/(\.?venv)/' || true)"
if [[ -n "${hits}" ]]; then
  printf '%s\n' "${hits}" | xargs sed -i "s|${OLD_DIR}|${NEW_DIR}|g"
  log "  rewrote $(printf '%s\n' "${hits}" | wc -l) venv file(s)"
else
  log "  no stale venv shebangs found"
fi

# ---------------------------------------------------------------------------
# Stage 4c — tighten firebase-creds.json mode
# ---------------------------------------------------------------------------
# rsync preserves source mode; the in-tree firebase-creds.json was 0644
# (world-readable). With the new in-tree path becoming the canonical
# credentials location (see Stage 5 sed), tighten to 0640.
if [[ -f "${NEW_DIR}/firebase-creds.json" ]]; then
  chmod 0640 "${NEW_DIR}/firebase-creds.json"
  log "Stage 4c: tightened firebase-creds.json to 0640"
fi

# ---------------------------------------------------------------------------
# Stage 4d — chown state files created under the old root deployment
# ---------------------------------------------------------------------------
# State files in /var/lib/xelasphere/ and /var/log/mempalace/ were created
# when Flask ran as root (pre-cutover). After the user flip, xelasphere
# can read them but not write — outbox.db opens read-only (record_turn
# silently fails) and the shadow log JSONL hits PermissionError on rotate.
# Surfaced 2026-04-27 ~30min after rotation to shadow.
if [[ -f /var/lib/xelasphere/outbox.db ]]; then
  chown "${SERVICE_USER}:${SERVICE_GROUP}" \
    /var/lib/xelasphere/outbox.db \
    /var/lib/xelasphere/outbox.db-wal \
    /var/lib/xelasphere/outbox.db-shm 2>/dev/null || true
  chmod 0640 /var/lib/xelasphere/outbox.db 2>/dev/null || true
  log "Stage 4d: chowned outbox.db (+wal/shm) to ${SERVICE_USER}:${SERVICE_GROUP}"
fi
if [[ -d /var/log/mempalace/shadow ]]; then
  # Owner xelasphere (writes via shadow_sink); group mempalace (readable
  # by sidecar / rubric harness running as mempalace).
  chown "${SERVICE_USER}:mempalace" /var/log/mempalace/shadow
  chmod 0750 /var/log/mempalace/shadow
  log "Stage 4d: chowned /var/log/mempalace/shadow to ${SERVICE_USER}:mempalace"
fi

# ---------------------------------------------------------------------------
# Stage 5 — env file relocation + path rewrite
# ---------------------------------------------------------------------------
log "Stage 5: install ${ENV_TARGET} (root:${SERVICE_GROUP} 0640)"
# Source .env from the NEW location (already rsynced) so we don't hit a
# race where the operator edited the old .env between Stage 3 and now.
SRC_ENV="${NEW_DIR}/.env"
if [[ ! -f "${SRC_ENV}" ]]; then
  err "${SRC_ENV} not found (rsync should have copied it)"
  exit 1
fi
# Path rewrites:
#   1. ${OLD_DIR} → ${NEW_DIR} (catches any source-tree-relative paths).
#   2. /root/.config/xelas/firebase.json → ${NEW_DIR}/firebase-creds.json
#      The user-home creds path is unreachable by xelasphere (root home
#      is 0700); the in-tree copy is md5-identical and reachable. This
#      regression broke production for ~2.5h on the 2026-04-27 cutover
#      until the in-tree path was hand-applied — bake into the script.
sed -e "s|${OLD_DIR}|${NEW_DIR}|g" \
    -e "s|/root/.config/xelas/firebase.json|${NEW_DIR}/firebase-creds.json|g" \
    "${SRC_ENV}" > "${ENV_TARGET}.new"
chmod 0640 "${ENV_TARGET}.new"
chown "root:${SERVICE_GROUP}" "${ENV_TARGET}.new"
mv "${ENV_TARGET}.new" "${ENV_TARGET}"
log "env file installed"

# Verify mode + ownership landed right
ENV_INFO="$(stat -c '%U:%G %a' "${ENV_TARGET}")"
if [[ "${ENV_INFO}" != "root:${SERVICE_GROUP} 640" ]]; then
  err "env file ownership/mode wrong: got '${ENV_INFO}', expected 'root:${SERVICE_GROUP} 640'"
  exit 1
fi

# ---------------------------------------------------------------------------
# Stage 6 — install systemd unit
# ---------------------------------------------------------------------------
log "Stage 6: install ${UNIT_TARGET}"
NEW_UNIT="${NEW_DIR}/deploy/xelasphere.service"
if [[ ! -f "${NEW_UNIT}" ]]; then
  err "${NEW_UNIT} not found in the migrated tree"
  exit 1
fi
# Backup the current unit so rollback can restore it.
if [[ -f "${UNIT_TARGET}" ]]; then
  cp -a "${UNIT_TARGET}" "${UNIT_TARGET}.pre-fhs-$(date -u +%Y%m%dT%H%M%SZ).bak"
fi
install -m 0644 -o root -g root "${NEW_UNIT}" "${UNIT_TARGET}"
systemctl daemon-reload
log "unit installed + daemon reloaded"

# ---------------------------------------------------------------------------
# Stage 7 — nginx robots.txt alias
# ---------------------------------------------------------------------------
log "Stage 7: update nginx robots.txt alias"
if [[ -f "${NGINX_CONF}" ]]; then
  if grep -q "${OLD_DIR}" "${NGINX_CONF}"; then
    cp -a "${NGINX_CONF}" "${NGINX_CONF}.pre-fhs-$(date -u +%Y%m%dT%H%M%SZ).bak"
    sed -i "s|${OLD_DIR}|${NEW_DIR}|g" "${NGINX_CONF}"
    if nginx -t; then
      systemctl reload nginx
      log "nginx config updated + reloaded"
    else
      err "nginx config test failed after path rewrite — check ${NGINX_CONF}"
      exit 1
    fi
  else
    log "nginx config has no ${OLD_DIR} references — skipping"
  fi
else
  log "nginx config ${NGINX_CONF} not found — skipping (verify manually)"
fi

# ---------------------------------------------------------------------------
# Stage 8 — start + verify
# ---------------------------------------------------------------------------
log "Stage 8: start xelasphere.service"
systemctl start xelasphere.service

# Wait for HTTP 200 on the listening port. /healthz preferred; fallback /.
for i in $(seq 1 ${HEALTH_TIMEOUT_SEC}); do
  if curl -sf -o /dev/null -w '%{http_code}' "http://127.0.0.1:${LISTEN_PORT}/healthz" 2>/dev/null | grep -q '200'; then
    log "Flask serving on 127.0.0.1:${LISTEN_PORT} (/healthz returns 200)"
    break
  fi
  if curl -sf -o /dev/null "http://127.0.0.1:${LISTEN_PORT}/" 2>/dev/null; then
    log "Flask serving on 127.0.0.1:${LISTEN_PORT} (/ returns 2xx)"
    break
  fi
  sleep 1
  if [[ $i -eq ${HEALTH_TIMEOUT_SEC} ]]; then
    err "Flask did not start serving on 127.0.0.1:${LISTEN_PORT} within ${HEALTH_TIMEOUT_SEC}s"
    err "journalctl -u xelasphere.service --since '1 min ago' for diagnostics"
    exit 1
  fi
done

POST_USER="$(ps -o user= -p "$(systemctl show -p MainPID --value xelasphere.service)" | tr -d ' ')"
if [[ "${POST_USER}" != "${SERVICE_USER}" ]]; then
  err "xelasphere.service is running as '${POST_USER}', expected '${SERVICE_USER}'"
  exit 1
fi
log "running as ${POST_USER}"

# ---------------------------------------------------------------------------
# Stage 9 — restart flusher
# ---------------------------------------------------------------------------
if systemctl list-unit-files mempalace-flusher.service &>/dev/null; then
  log "Stage 9: restart mempalace-flusher.service (now that User=xelasphere can traverse)"
  systemctl restart mempalace-flusher.service || log "(flusher restart failed — non-fatal; check status manually)"
fi

# ---------------------------------------------------------------------------
# Done
# ---------------------------------------------------------------------------
log "================================================================"
log "FHS migration complete."
log ""
log "Next steps (manual):"
log "  1. Verify production traffic is healthy (load a real page)"
log "  2. Confirm \`systemctl status xelasphere\` is green"
log "  3. After 24h soak, optionally archive ${OLD_DIR} (do NOT delete yet)"
log "  4. Bundle #7 sidecar deploy: rebuild wheel + reinstall +"
log "     systemctl restart mempalace-sidecar.service"
log "  5. Once #7 deployed, rotate: \`mempalace-mode shadow\` (P1 entry)"
log "================================================================"
