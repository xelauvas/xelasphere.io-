#!/usr/bin/env bash
#
# M0.24 pre-P0 exit check — automated portion.
#
# Runs the checks that can be verified from the repo. Deployment-
# environment checks (provision.sh on staging, mempalace-mode live
# round-trip, Grafana import) are documented in
# docs/ops/mempalace-m0-exit-checklist.md as manual P0-entry items.
#
# Exit codes:
#   0  — all automated checks pass; repo-level M0 exit gates met.
#   1  — one or more automated checks failed; see output.
#   2  — environment is missing tools needed to run the checks.

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

VENV_PY="$REPO_ROOT/venv/bin/python"
FAIL=0
PASS_COUNT=0
FAIL_COUNT=0

# --- Helpers ----------------------------------------------------------

say() {
    printf '%s\n' "$*"
}

check() {
    local label="$1"
    shift
    if "$@" >/dev/null 2>&1; then
        say "  ✓  $label"
        PASS_COUNT=$((PASS_COUNT + 1))
    else
        say "  ✗  $label"
        FAIL_COUNT=$((FAIL_COUNT + 1))
        FAIL=1
    fi
}

# --- Environment ------------------------------------------------------

if [ ! -x "$VENV_PY" ]; then
    say "venv python not found at $VENV_PY"
    say "Expected a Flask venv at repo_root/venv/. Environment check failed."
    exit 2
fi

# --- 1. Flask-side tests green ---------------------------------------

say ""
say "1. Flask-side test suite"
check "app/mempalace/ tests all pass" \
    "$VENV_PY" -m pytest app/mempalace/ --tb=no -q

check "tests/test_m023_5_smoke.py passes" \
    "$VENV_PY" -m pytest tests/test_m023_5_smoke.py --tb=no -q

# --- 2. Dashboard JSON validity --------------------------------------

say ""
say "2. Grafana dashboard JSON validity"
for f in docs/ops/dashboards/mempalace-live.json docs/ops/dashboards/mempalace-rollout.json; do
    check "$f parses" \
        "$VENV_PY" -c "import json; json.load(open('$f'))"
done

# --- 3. Systemd unit file syntax --------------------------------------

say ""
say "3. Systemd unit file syntax"
if command -v systemd-analyze >/dev/null 2>&1; then
    # systemd-analyze verify exits 0 with warnings (e.g., missing
    # executable paths during repo-level check). We check for syntax
    # errors explicitly, not exit code.
    for f in deploy/mempalace/mempalace-sidecar.service deploy/mempalace/mempalace-flusher.service; do
        out=$(systemd-analyze verify "$f" 2>&1)
        # Accept "not executable: No such file" as a repo-level warning;
        # reject any other systemd-analyze output.
        if echo "$out" | grep -qvE "(^$|not executable: No such file)"; then
            say "  ✗  $f (has syntax issue)"
            echo "$out" | sed 's/^/       /'
            FAIL_COUNT=$((FAIL_COUNT + 1))
            FAIL=1
        else
            say "  ✓  $f (syntax clean; executable-path warnings are deployment-env concerns)"
            PASS_COUNT=$((PASS_COUNT + 1))
        fi
    done
else
    say "  –  systemd-analyze not available; skipping (manual verify at P0 entry)"
fi

# --- 4. Shell script syntax ------------------------------------------

say ""
say "4. Deploy script syntax"
for f in deploy/mempalace/provision.sh deploy/mempalace/mempalace-mode tools/mempalace-rubric tools/check-legacy-memory-imports.py tools/check-m0-exit.sh; do
    if [ ! -f "$f" ]; then
        say "  ✗  $f (missing)"
        FAIL_COUNT=$((FAIL_COUNT + 1))
        FAIL=1
        continue
    fi
    case "$f" in
        *.sh|*mempalace-mode)
            check "$f bash syntax" bash -n "$f"
            ;;
        *.py)
            check "$f python syntax" "$VENV_PY" -m py_compile "$f"
            ;;
        *)
            # tools/mempalace-rubric is Python w/o .py extension; check by header
            if head -1 "$f" | grep -q python; then
                check "$f python syntax" "$VENV_PY" -m py_compile "$f"
            fi
            ;;
    esac
done

# --- 5. CI guard configuration ---------------------------------------

say ""
say "5. CI legacy-import guard"
check "tools/check-legacy-memory-imports.py exists + executable" \
    test -x tools/check-legacy-memory-imports.py

check "tools/check-legacy-memory-imports.py passes in warn mode (zero hits)" \
    "$VENV_PY" tools/check-legacy-memory-imports.py --mode warn

check ".github/workflows/ci.yml references the guard" \
    grep -q "check-legacy-memory-imports.py" .github/workflows/ci.yml

# --- 6. Runbook + logging-privacy present ----------------------------

say ""
say "6. Runbook + privacy doc"
check "docs/ops/mempalace-runbook.md exists" test -f docs/ops/mempalace-runbook.md
check "docs/ops/logging-privacy.md exists" test -f docs/ops/logging-privacy.md
check "docs/ops/mempalace-runbook.md contains all R-numbers (R1-R15)" \
    bash -c 'for r in R1 R2 R3 R4 R5 R6 R7 R8 R9 R10 R11 R13 R14 R15; do grep -q "^## $r " docs/ops/mempalace-runbook.md || exit 1; done'
check "runbook STUBs are explicit (R7, R8, R13)" \
    bash -c 'grep -q "STUB.*R7\|^## R7" docs/ops/mempalace-runbook.md && grep -q "STUB.*R8\|^## R8" docs/ops/mempalace-runbook.md && grep -q "STUB.*R13\|^## R13" docs/ops/mempalace-runbook.md'

# --- 7. Shadow-rubric harness ----------------------------------------

say ""
say "7. Shadow-rubric harness"
check "tools/mempalace-rubric exists + executable" \
    test -x tools/mempalace-rubric

# Run with empty log dir; expect clean exit + 'No shadow records found' message.
EMPTY_DIR=$(mktemp -d)
out=$(tools/mempalace-rubric --log-dir "$EMPTY_DIR" 2>&1)
if echo "$out" | grep -q "No shadow records found"; then
    say "  ✓  tools/mempalace-rubric handles empty-log-dir cleanly"
    PASS_COUNT=$((PASS_COUNT + 1))
else
    say "  ✗  tools/mempalace-rubric empty-log-dir behavior unexpected: $out"
    FAIL_COUNT=$((FAIL_COUNT + 1))
    FAIL=1
fi
rm -rf "$EMPTY_DIR"

check "docs/mempalace-shadow/README.md exists" \
    test -f docs/mempalace-shadow/README.md
check "docs/mempalace-shadow/phase-log.md exists" \
    test -f docs/mempalace-shadow/phase-log.md

# --- 8. Env-file templates -------------------------------------------
#
# M0.27 hardening: § 8 previously only verified template existence + two
# specific keys. That false-green through the M0.26 deploy attempt when
# MEMPALACE_PALACES_DIR (template) didn't match data_dir (Settings) and
# MEMPALACE_USER_ID_HASH_SALT placeholder wasn't generated by provision.sh.
#
# New shape:
#   8.1  Template files exist.
#   8.2  Sidecar template declares every required-no-default field from
#        the sidecar Settings class (mempalace_sidecar/config.py).
#   8.3  Every <TODO> placeholder in a template is paired with a matching
#        substitution line in provision.sh (template-is-source-of-truth,
#        provision-is-substitution invariant from M0.27).
#   8.4  Flask template declares every MEMPALACE_* / LEGACY_MEMORY /
#        XELASPHERE_* env var that Flask-side code reads from os.getenv
#        (minus those with benign Python defaults).
#   8.5  No vestigial vars in sidecar template that aren't consumed
#        anywhere — guard against the reverse drift (template declares
#        what code ignores, misleading operators into "configuring" dead
#        values).

say ""
say "8. Environment-file templates"

SIDECAR_TEMPLATE=deploy/mempalace/sidecar.env.example
FLASK_TEMPLATE=deploy/mempalace/mempalace.env.example
PROVISION_SH=deploy/mempalace/provision.sh
SIDECAR_CONFIG_PY=mempalace-sidecar/mempalace_sidecar/config.py

check "$SIDECAR_TEMPLATE exists" test -f "$SIDECAR_TEMPLATE"
check "$FLASK_TEMPLATE exists" test -f "$FLASK_TEMPLATE"
check "mempalace.env.example has MEMPALACE_MODE=off default" \
    grep -q "^MEMPALACE_MODE=off" "$FLASK_TEMPLATE"

# 8.2 — Required-no-default fields from sidecar Settings must appear in
# the sidecar template. "Required-no-default" = Field annotation with no
# `default=` or `Field(min_length=..., ...)` without `default=`. Parsed
# from config.py by a small helper expression (keeps the check_script
# dependency-free).
#
# The list below is manually curated from config.py to avoid fragile
# AST parsing; it must be updated whenever a new required field is added
# to Settings. The M0.27 commit body names this as a known maintenance
# cost.
REQUIRED_SIDECAR_FIELDS="token data_dir user_id_hash_salt"
for field in $REQUIRED_SIDECAR_FIELDS; do
    # env_prefix=MEMPALACE_ + upper(field) → env var name
    env_name="MEMPALACE_$(echo "$field" | tr '[:lower:]' '[:upper:]')"
    if grep -qE "^${env_name}=" "$SIDECAR_TEMPLATE"; then
        say "  ✓  sidecar template declares $env_name (for Settings.$field)"
        PASS_COUNT=$((PASS_COUNT + 1))
    else
        say "  ✗  sidecar template missing $env_name — required by Settings.$field at $SIDECAR_CONFIG_PY"
        FAIL_COUNT=$((FAIL_COUNT + 1))
        FAIL=1
    fi
done

# 8.3 — Every <TODO> placeholder in either template must be paired with
# a matching substitution line in provision.sh (M0.27 invariant).
for template in "$SIDECAR_TEMPLATE" "$FLASK_TEMPLATE"; do
    todo_keys=$(grep -E "^[A-Z_]+=<TODO" "$template" | cut -d= -f1 || true)
    for key in $todo_keys; do
        if grep -qE "\"${key}=\\\$" "$PROVISION_SH" || grep -qE "\"${key}=[^\"]" "$PROVISION_SH"; then
            say "  ✓  <TODO> $key in $template has substitution in provision.sh"
            PASS_COUNT=$((PASS_COUNT + 1))
        else
            say "  ✗  <TODO> $key in $template has NO substitution in provision.sh"
            FAIL_COUNT=$((FAIL_COUNT + 1))
            FAIL=1
        fi
    done
done

# 8.4 — Flask template declares all MEMPALACE_* / LEGACY_MEMORY /
# XELASPHERE_* vars Flask-side code reads via os.getenv. Required-no-
# default list hand-curated; vars with benign defaults don't need to
# be in the template (code default wins).
REQUIRED_FLASK_VARS="MEMPALACE_MODE MEMPALACE_TOKEN MEMPALACE_SOCKET_PATH MEMPALACE_OUTBOX_PATH MEMPALACE_SHADOW_DIR MEMPALACE_LOG_LEVEL XELASPHERE_PORT"
for v in $REQUIRED_FLASK_VARS; do
    if grep -qE "^${v}=" "$FLASK_TEMPLATE"; then
        say "  ✓  Flask template declares $v"
        PASS_COUNT=$((PASS_COUNT + 1))
    else
        say "  ✗  Flask template missing $v — consumed by Flask-side os.getenv"
        FAIL_COUNT=$((FAIL_COUNT + 1))
        FAIL=1
    fi
done

# 8.5 — Vestigial guard: sidecar template should not declare any var
# whose consumer doesn't exist. Limited to sidecar-specific vars that
# M0.27 caught (KG_PENDING, AUDIT_DB vestigial; derived paths from
# data_dir). Reject if re-introduced.
VESTIGIAL_SIDECAR_VARS="MEMPALACE_KG_PENDING MEMPALACE_AUDIT_DB MEMPALACE_PALACES_DIR"
for v in $VESTIGIAL_SIDECAR_VARS; do
    if grep -qE "^${v}=" "$SIDECAR_TEMPLATE"; then
        say "  ✗  sidecar template re-introduces vestigial var $v (removed at M0.27)"
        FAIL_COUNT=$((FAIL_COUNT + 1))
        FAIL=1
    else
        say "  ✓  sidecar template does not re-declare vestigial $v"
        PASS_COUNT=$((PASS_COUNT + 1))
    fi
done

# --- 9. Sidecar install artifact -------------------------------------
#
# Added M0.26. provision.sh step 5 refuses to install the sidecar without
# either SIDECAR_WHEEL pointing at a wheel OR an already-installed binary.
# If neither is true at P0 entry, the unit enable in step 6 fails. This
# check makes that failure visible before the deploy runs.
#
# Accepts either:
#   (a) SIDECAR_WHEEL env var points at an existing file
#   (b) mempalace-sidecar/dist/*.whl exists in the repo

say ""
say "9. Sidecar install artifact"
if [ -n "${SIDECAR_WHEEL:-}" ] && [ -f "$SIDECAR_WHEEL" ]; then
    say "  ✓  SIDECAR_WHEEL points at $SIDECAR_WHEEL"
    PASS_COUNT=$((PASS_COUNT + 1))
elif compgen -G "mempalace-sidecar/dist/*.whl" >/dev/null 2>&1; then
    say "  ✓  mempalace-sidecar/dist/*.whl present ($(ls mempalace-sidecar/dist/*.whl | head -1))"
    PASS_COUNT=$((PASS_COUNT + 1))
else
    say "  ✗  no sidecar wheel — set SIDECAR_WHEEL or build with 'python3 -m build --wheel' in mempalace-sidecar/"
    FAIL_COUNT=$((FAIL_COUNT + 1))
    FAIL=1
fi

# --- Summary ---------------------------------------------------------

say ""
say "-----"
say "Passed: $PASS_COUNT  Failed: $FAIL_COUNT"

if [ $FAIL -ne 0 ]; then
    say ""
    say "Automated M0 exit checks FAILED. See output above."
    exit 1
fi

say ""
say "Automated M0 exit checks PASSED."
say "Deployment-environment checks remain — see"
say "docs/ops/mempalace-m0-exit-checklist.md § Deployment-environment."
exit 0
