#!/usr/bin/env bash
# P0 dark-deploy daily health check.
#
# Anchored to the P0 entry timestamp so each day's check covers the
# entire P0 window (not a rolling 24h). The exit criterion per spec
# §7.4 P0 is "no mempalace-attributed errors for 3 days" — which is
# evaluated against the cumulative P0 window, not any single day in
# isolation. Rolling 24h would silently forget a day-1 failure on the
# day-2 check.
#
# Usage (from the repo root, on the prod host):
#     bash tools/mempalace-p0-daily-check.sh
#
# Clean = all four sections return expected output:
#   1. Sidecar journal: empty (no --priority err since P0 entry).
#   2. Flask mempalace-tagged: only known INFO-level chatter.
#      Currently includes entries at 2026-04-22 23:17:08 UTC from the
#      memory_v2 full-message-logging bug tracked at #11 — known,
#      filed, not mempalace-attributed. New unexpected entries trigger
#      triage per runbook.
#   3. /readyz: {"ok":true,...}.
#   4. mempalace-sidecar.service: active.
#
# Anything else: triage before rotating MEMPALACE_MODE. Per phase-log
# P0 section, a real mempalace-attributed error restarts the 3-day
# clock from zero.
#
# Post-P0 this script is discard-worthy; the P0_ENTRY timestamp is
# hardcoded for this instance of the rollout.

set -u

P0_ENTRY="2026-04-22 23:16:26"
SIDECAR_SOCK="/run/mempalace/sidecar.sock"

echo "=== P0 daily health check (anchored to ${P0_ENTRY} UTC) ==="
echo

echo "--- 1. Sidecar journal errors since P0 entry ---"
journalctl -u mempalace-sidecar.service --since "${P0_ENTRY}" --priority err --no-pager

echo
echo "--- 2. Flask mempalace-tagged journal since P0 entry ---"
journalctl -u xelasphere.service --since "${P0_ENTRY}" --no-pager \
  | grep -iE "mempalace|MEMPALACE_" \
  | head -40

echo
echo "--- 3. Sidecar /readyz ---"
curl -sf --unix-socket "${SIDECAR_SOCK}" http://localhost/readyz || echo "(readyz request failed)"

echo
echo "--- 4. mempalace-sidecar.service state ---"
systemctl is-active mempalace-sidecar.service

echo
echo "=== Done. See header for what 'clean' means. ==="
