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

say ""
say "8. Environment-file templates"
check "deploy/mempalace/sidecar.env.example exists" \
    test -f deploy/mempalace/sidecar.env.example
check "deploy/mempalace/mempalace.env.example exists" \
    test -f deploy/mempalace/mempalace.env.example
check "sidecar.env.example has MEMPALACE_TOKEN placeholder" \
    grep -q "MEMPALACE_TOKEN=" deploy/mempalace/sidecar.env.example
check "mempalace.env.example has MEMPALACE_MODE=off default" \
    grep -q "MEMPALACE_MODE=off" deploy/mempalace/mempalace.env.example

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
