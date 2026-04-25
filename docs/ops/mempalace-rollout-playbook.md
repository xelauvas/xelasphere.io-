# MemPalace rollout playbook (P0 → P4)

**Authoritative for: 2026-04-22 through ~2026-08-05** (full rollout arc, projected).

Living document. Updated at each phase transition with actuals. The
**phase-log** at `docs/mempalace-shadow/phase-log.md` remains the
single source of truth for *what has actually happened*; this playbook
is the plan of *what to do next*.

---

## 0. Preamble — how to use this document

### For the operator (Xela)

Read top-to-bottom once. Then work one phase at a time, in order. Every
phase has:

1. **Entry gate** — the state that must be true before starting the phase.
2. **Daily / weekly actions** — routine operational work during the phase.
3. **Exit gate** — the criteria that must be true before advancing.
4. **Rollback path** — the safe undo if something goes wrong.

The phases are serialized. Do not skip. A delay at any phase pushes
everything downstream by the same amount.

### For a future agent session

If you're picking up this work in a new conversation, **read `docs/mempalace-shadow/phase-log.md` first** to see what's already done. Then find the current phase below and start from its entry gate.

Key context the agent needs that isn't obvious from code:

- **Six P1-entry issues** (label `p1-entry`): #2 (R7 runbook), #3 (R8 runbook), #4 (R13 runbook), #6 (Flask FHS migration), #7 (socket mode 0660), #8 (CI smoke-test).
- **M0 → P0 entry commit**: `98e23ef`, 2026-04-22 23:16:26 UTC. P0 clock ticks from that timestamp.
- **Rollback snapshot** during P0: Hetzner snapshot #379196442. Delete after P0 exits.
- **The dashboards are at**: https://xelauvas.dev/status/grafana/ behind HTTP basic auth (`xela` + htpasswd password) then Grafana login (`admin` / as set).
- **Flask currently runs as `User=root`** on this host. Spec §7.1 calls for `User=xelasphere`. Issue #6 closes this gap. Until then, Flusher unit cannot start.
- **Template-is-source-of-truth invariant** (M0.27): `deploy/mempalace/*.env.example` declares schema, `deploy/mempalace/provision.sh` substitutes secrets. Do not edit `/etc/mempalace/*.env` or `/etc/xelasphere/mempalace.env` in-place.

---

## Phase 0 — Dark deploy (IN PROGRESS)

- **Entered**: 2026-04-22 23:16:26 UTC (commit `98e23ef`)
- **Config**: `MEMPALACE_MODE=off`, `LEGACY_MEMORY=on`
- **Duration**: 3 days minimum, per spec §7.4

### Why this phase exists

Installed code on a real host can fail in ways that no test suite catches: reboot-survival, permission drift, third-party library HOME assumptions, shell-semantics in unit files, tmpfs lifecycle mismatches. The six drift classes surfaced at M0.26/M0.27 are evidence the deploy-time layer is a distinct category. P0 exists to let time-dependent failures (memory leaks, FD creep, long-running resource exhaustion) surface against real traffic while mempalace does zero work — so rollback is free and legacy still serves every user.

### Daily action (once per day through 2026-04-25)

```bash
# 1. Sidecar journal — errors only, last 24h
journalctl -u mempalace-sidecar.service --since "24 hours ago" --priority err --no-pager

# 2. Flask journal — anything mempalace-tagged, last 24h
journalctl -u xelasphere.service --since "24 hours ago" --no-pager | grep -iE "mempalace|MEMPALACE_"

# 3. Sidecar liveness
curl -sf --unix-socket /run/mempalace/sidecar.sock http://localhost/readyz
```

**Clean** = (1) empty, (2) only INFO-level mempalace chatter, (3) returns `{"ok":true,...}`.

If *any* output surprises you: do NOT rotate the mode. Triage first. If the triage concludes "this is a real mempalace-attributed issue," the 3-day clock restarts from zero after the fix.

### Exit gate (run Friday 2026-04-25 evening)

Run the daily check one final time. If clean for 3 consecutive days:

1. Edit `docs/mempalace-shadow/phase-log.md`, P0 section, fill the `Exit verified:` line with the date and a paste of the final journal + `/readyz` output.
2. Commit:
   ```bash
   git add docs/mempalace-shadow/phase-log.md
   git commit -m "feat(mempalace): P0 exit — 3 days clean, entering P1-entry work"
   ```
3. Merge feature branch to main:
   ```bash
   git checkout main
   git pull
   git merge feature/mempalace-integration --no-ff
   git push origin main
   ```
4. **Delete Hetzner snapshot #379196442** via the Hetzner console. It's served its purpose; keeping it costs ~£5/mo indefinitely.

### Rollback (during P0)

- **Application-level**: already at `MEMPALACE_MODE=off`. No mempalace behavior to unwind.
- **System-level**: Hetzner snapshot #379196442 — console rollback, ~30 seconds.
- **Code-level**: `git revert 98e23ef` (and preceding M0.26a/b/M0.27/M0.27c commits as needed). Only relevant if a bug surfaces before P0 exits.

---

## P0 → P1-entry work (projected 2026-04-26 through 2026-04-30)

P0 exit clears the *dark-deploy* gate. Before mode rotation to `shadow`, the six P1-entry issues must be closed. Two are architectural blockers; four are strongly-recommended-but-not-blocking.

### Why these specific issues

- **#6 Flask FHS migration** — hard blocker. Flusher unit has `User=xelasphere`. On this host, xelasphere user exists (M0.26 forward-prep) but Flask's venv is at `/root/xelas.io/xelasphere.io-/venv`. `/root` is mode `0700 root:root` — non-root users cannot traverse. Flusher cannot exec the venv until Flask is relocated out of `/root`. Shadow mode writes without the flusher draining = outbox grows unbounded.

- **Shadow-mode behavior tests** — hard blocker. The M0.23.5 Path A ruling deferred authoring shadow-behavior tests to P1 entry. You cannot verify at the application level that `log_shadow` payload shape matches rubric parser expectations, that `record_turn` correctly propagates `pair_id`, or that `wake_up_sync` is called with the right contract, without these tests. Running shadow mode blind is a no-go.

- **#7 socket mode 0660** — ride-along. Sidecar code change + wheel rebuild. Already going to rebuild wheel for #6's redeploy, so marginal cost is near-zero. Closes the spec-drift tombstone.

- **#8 CI smoke-test** — ride-along. Prevents the next drift class from shipping at merge time. Structural insurance against repeated P0 deploy pain.

- **#2/#3/#4 runbook expansions** — documentation. Before shadow traffic hits and ops events start happening, you need runbook entries for KG cost/queue (R7), circuit breaker states (R8), and incident classification (R13). Otherwise first P1 incident = 3am triage with no playbook.

### Step-by-step

#### Step 1: #6 Flask FHS migration (~1 full focused day)

**Why first**: blocks everything else. Flusher cannot start, mode rotation cannot safely proceed.

```bash
# Plan in a feature branch
git checkout -b feature/fhs-migration

# Preconditions — xelasphere user already exists from M0.26; verify
id xelasphere

# Create destination tree
install -d -o xelasphere -g xelasphere -m 0755 /opt/xelasphere
install -d -o xelasphere -g xelasphere -m 0750 /var/lib/xelasphere
install -d -o root -g xelasphere -m 0750 /etc/xelasphere

# Stop Flask cleanly (this IS a brief maintenance window — ~30-60 seconds)
systemctl stop xelasphere

# Move code tree
cp -a /root/xelas.io/xelasphere.io-/. /opt/xelasphere/
chown -R xelasphere:xelasphere /opt/xelasphere
# Fix venv internal paths if symlinked (test: /opt/xelasphere/venv/bin/python --version)

# Move env file
mv /root/xelas.io/xelasphere.io-/.env /etc/xelasphere/xelasphere.env
chown root:xelasphere /etc/xelasphere/xelasphere.env
chmod 0640 /etc/xelasphere/xelasphere.env

# Move state
# (outbox.db etc — whatever Flask writes under /root/xelas.io/... today)
find /opt/xelasphere -name "outbox.db" -o -name "*.sqlite" | xargs -r ls
# Move any stateful files to /var/lib/xelasphere/ and update MEMPALACE_OUTBOX_PATH in env

# Update systemd unit
systemctl edit --full xelasphere.service
# Change:
#   User=xelasphere
#   Group=xelasphere
#   WorkingDirectory=/opt/xelasphere
#   EnvironmentFile=/etc/xelasphere/xelasphere.env
#   ExecStart=/opt/xelasphere/venv/bin/gunicorn -c gunicorn.conf.py run:app
# Add:
#   RuntimeDirectory=xelasphere
#   RuntimeDirectoryMode=0750

systemctl daemon-reload
systemctl start xelasphere
sleep 3
systemctl is-active xelasphere  # expect: active

# Smoke test
curl -sf https://xelasphere.com/ | head -5
# Basic chat request flow to confirm app is serving

# Now start the flusher — this is the first time it can run
systemctl start mempalace-flusher
sleep 3
systemctl is-active mempalace-flusher  # expect: active
journalctl -u mempalace-flusher --since "1 minute ago" | grep -i "flusher started"

# Idempotency verification — re-run provision.sh, the M0.27c post-check should now take the xelasphere branch
SIDECAR_WHEEL=/opt/xelasphere/mempalace-sidecar/dist/mempalace_sidecar-0.1.0-py3-none-any.whl bash /opt/xelasphere/deploy/mempalace/provision.sh
# Expected: "[provision] verified: gunicorn master has mempalace group membership"

# Close issue
gh issue close 6 --comment "Migrated 2026-MM-DD, commit <sha>"

# Remove the legacy path AFTER verification the new one works
# rm -rf /root/xelas.io/xelasphere.io-/   # ONLY after confirming everything is happy in /opt
```

**Rollback if this fails midway**:
- `systemctl stop xelasphere mempalace-flusher`
- Restore Flask's old unit file pointing back at `/root/xelas.io/xelasphere.io-/venv`
- `systemctl daemon-reload && systemctl start xelasphere`
- Close the feature branch without merging, investigate what broke

**For an agent**: this step is the one with the largest blast radius in the entire rollout. If you're unsure about any substep, stop and ask the operator before running destructive commands (`rm -rf`, `chown -R`, unit-file edits). The user-approved path is: migrate, verify, only then remove the `/root/` tree.

#### Step 2: #7 socket mode 0660 (~1 hour, piggybacks on #6's wheel rebuild)

**Why**: spec-drift tombstone from M0.27. Defense-in-depth for when Flask is non-root and relies on group-membership rather than DAC bypass.

Edit `mempalace-sidecar/mempalace_sidecar/main.py`, inside the lifespan hook after the app starts up (but before `_sd_notify_ready()`):

```python
from pathlib import Path as _Path
_sock = _Path(settings.socket_path)
if _sock.exists():
    os.chmod(_sock, 0o660)
```

Add a test in `mempalace-sidecar/tests/integration/` that asserts `stat.S_IMODE(os.stat(socket_path).st_mode) == 0o660` after the app starts.

```bash
cd mempalace-sidecar
rm -f dist/*.whl
.venv/bin/python -m build --wheel
.venv/bin/pytest tests/ -q   # 118 passed + 1 skipped + new mode test
```

Rebuild + redeploy via provision.sh (same wheel-rebuild flow as in Step 1). Verify:

```bash
ls -la /run/mempalace/sidecar.sock
# expect: srw-rw---- (0660, not 0666)
```

Close issue #7.

#### Step 3: Shadow-mode behavior tests (~half day)

**Why**: validates the application-level shadow contract before any shadow traffic flows.

Author `app/mempalace/tests/test_shadow_mode.py`:

- **Test 1**: `log_shadow` payload shape matches rubric parser expectations (keys: `ts`, `user_id`, `query`, `legacy`, `mempalace`). Parse with `tools/mempalace-rubric`'s parser to confirm.
- **Test 2**: `record_turn` called for both user-half and assistant-half with shared `pair_id`. Mock the outbox and verify two inserts with the same pair_id per turn.
- **Test 3**: `wake_up_sync` called with the correct user_id / query / session_id at `chat_routes.py:945`. Patch the adapter, send a chat request at mode=shadow, verify.
- **Test 4**: Streaming-path user-half `record_turn` fires at the expected point in the SSE flow. (Streaming assistant-half is separately deferred per M0.18 finish commit `af7549a` — acknowledge in a test skip.)

```bash
venv/bin/python -m pytest app/mempalace/tests/test_shadow_mode.py -v
# Expected: all pass
```

#### Step 4: #8 CI smoke-test (~half day)

**Why**: structural insurance against the next drift class. The M0.27 commit body + phase-log narrative flagged three drift classes (shell semantics, implicit env assumptions, post-check deploy-shape) as categorically uncatchable by static analysis. A CI job that actually runs `provision.sh` against a clean container catches all three at PR time.

Create `.github/workflows/deploy-smoke-test.yml`:

```yaml
name: Deploy smoke test
on:
  pull_request:
    paths:
      - 'deploy/mempalace/**'
      - 'mempalace-sidecar/**'
      - 'tools/check-m0-exit.sh'
      - 'app/mempalace/**'

jobs:
  smoke:
    runs-on: ubuntu-latest
    container:
      image: debian:stable
      options: --privileged  # for systemd
    steps:
      - uses: actions/checkout@v4
      - name: Install baseline
        run: |
          apt-get update
          apt-get install -y systemd python3 python3-venv python3-pip openssl curl build-essential sudo
      - name: Create xelasphere user
        run: useradd --system --no-create-home --shell /usr/sbin/nologin xelasphere
      - name: Build sidecar wheel
        run: |
          cd mempalace-sidecar
          python3 -m venv .venv
          .venv/bin/pip install --upgrade pip build
          .venv/bin/python -m build --wheel
      - name: Run provision.sh
        run: |
          SIDECAR_WHEEL=mempalace-sidecar/dist/mempalace_sidecar-*.whl bash deploy/mempalace/provision.sh
      - name: Verify /readyz
        run: |
          for i in $(seq 1 30); do
            curl -sf --unix-socket /run/mempalace/sidecar.sock http://localhost/readyz && break
            sleep 1
          done
          curl -sf --unix-socket /run/mempalace/sidecar.sock http://localhost/readyz | grep -q '"ok":true'
```

**Validation**: open a seeded-drift PR. Revert M0.27 follow-up #1's ExecStartPre order as a test. Confirm CI fails. Close without merging. This is the "does the gate actually catch drift" test.

Close issue #8.

#### Step 5: #2/#3/#4 runbook STUB expansions (~3 hours total)

**Why**: before shadow traffic starts flowing, ops incidents can happen. Without runbook entries for R7/R8/R13, first incident = no playbook.

Edit `docs/ops/mempalace-runbook.md`:

- **R7 KG cost + queue** (~1 hr): how to check `xelasphere_kg_daily_spend_usd` Prometheus metric, what the $5/day default cap means, procedure when queue depth `/var/mempalace/kg_pending.sqlite` grows > 1k rows, how to pause KG worker if spend is out of control.
- **R8 Circuit breaker** (~1 hr): three states (CLOSED → OPEN → HALF_OPEN-reprobe-window) per M0.14's design. What OPEN looks like in logs, what triggers it, how to manually close if stuck, what to investigate in breaker-trip aftermath.
- **R13 Incident class + response** (~1 hr): classify incidents — (a) sidecar-down (b) flusher-stuck (c) chromadb-corrupted (d) palace-write-denied (e) UDS permission drift. For each, the first 5 diagnostic commands and the rollback-vs-investigate decision.

Close issues #2, #3, #4.

### P1 entry gate (Thursday 2026-04-30)

- All 6 P1-entry issues closed
- Shadow-mode tests green
- Flusher unit `active (running)`, zero restarts
- Create the rubric weekly template:
  ```bash
  cat > docs/mempalace-shadow/template-YYYY-WW.md <<'EOF'
  # Rubric tally: <YYYY-WW>

  Generated via: `tools/mempalace-rubric --log-dir /var/log/mempalace/shadow --week YYYY-WW`

  |                | v3_hit | v3_miss |
  |----------------|--------|---------|
  | mempalace_hit  | <X>    | <Y>     |
  | mempalace_miss | <Z>    | <W>     |

  - `mempalace_only_hit`: Y
  - `v3_only_hit`: Z
  - `(both + mempalace_only) / total`: (X+Y)/(X+Y+Z+W) = <%>

  **P2 exit criteria check**:
  - mempalace_only_hit ≥ v3_only_hit? <YES/NO>
  - (both + mempalace_only) ≥ 85%? <YES/NO>

  Notes: <any relevant context — user segment, spike days, anomalies>
  EOF
  ```

### Rotate to shadow

```bash
/usr/local/bin/mempalace-mode shadow
# Script restarts xelasphere.service; probes /admin/mempalace-mode for confirmation
```

Verify:
```bash
curl -sf http://localhost:5000/admin/mempalace-mode
# expect: {"mode":"shadow"}
```

Commit phase-log P1 entry:
```bash
# Edit docs/mempalace-shadow/phase-log.md, P1 section: fill "Entered: <date>"
git commit -m "feat(mempalace): P1 entry — shadow writes active at $(date -Iseconds)"
```

**P0 clock stops. P1 clock starts.**

---

## Phase 1 — Shadow writes (projected ~2026-04-30 → 2026-05-08)

- **Config**: `MEMPALACE_MODE=shadow`, `LEGACY_MEMORY=on`
- **Duration**: ≥1 week to stabilize

### Why this phase exists

First time mempalace does actual work on real traffic. Writes happen (outbox fills, flusher drains, sidecar ingests, palaces accumulate content, shadow logs record comparisons). Reads do NOT go into the prompt — legacy still drives the user-facing chat. Zero user-visible change; full infrastructure validation.

### Daily action (~5 min)

- Open Grafana → **MemPalace — Live**. Panels now showing actual data. Watch:
  - Ingest rate — should ramp as traffic comes in
  - Wake-up latency — p95 should be < 150ms once warm
  - Outbox depth — should stay bounded (p95 ≤ 1k rows is spec target)
  - Circuit breaker — should be CLOSED always
  - Cache pressure — should be < 2G cap
  - `/readyz` — should be green
- Open **MemPalace — Rollout**. Mode gauge now shows "shadow." Shadow log volume panel now has data.
- `journalctl -u mempalace-sidecar -u mempalace-flusher --since "24h" --priority warning --no-pager`

### Exit gate (~2026-05-08, ~1 week after entry)

Per spec §7.4 P1:
- Outbox depth p95 ≤ 1k rows sustained
- Ingest success ≥ 99.5% (measurable via `xelasphere_mempalace_ingest_total` success/total)
- Shadow log populated (1+ week of data in `/var/log/mempalace/shadow/`)
- Disk growth within forecast (palace storage growth panel on rollout dashboard)
- Single-user rebuild benchmark measured: pick a test user, run `mempalace rebuild <user_id>`, record duration. Record in phase-log.

Fill phase-log P1 section, commit. P1 → P2 is a criterion change, not a mode change. Still `MEMPALACE_MODE=shadow`.

### Rollback from P1

`mempalace-mode off` — ~60 second Flask restart. Outbox content remains on disk (purge with `mempalace outbox purge` after confirming no ongoing use).

---

## Phase 2 — Shadow reads + rubric math (projected ~2026-05-08 → ~2026-05-29)

- **Config**: `MEMPALACE_MODE=shadow`, `LEGACY_MEMORY=on` (same as P1)
- **Duration**: **≥3 weeks** (minimum for 3 consecutive weeks of rubric data)

### Why this phase exists

Shadow mode logs a 2×2 comparison every retrieval: was the relevant context found by mempalace, v3, both, or neither? Weekly rubric math asks: is mempalace actually doing a better job than legacy v3 for YOUR users' query patterns? This is the decision gate for whether to proceed to P3 (active) or stay at shadow.

### Weekly action (~30 min each Friday)

```bash
# Generate the rubric tally for this ISO week
WEEK=$(date +%G-W%V)
tools/mempalace-rubric --log-dir /var/log/mempalace/shadow --week "$WEEK" > docs/mempalace-shadow/"$WEEK".md
```

Review the output. Fill in the P2 exit criteria checks inline. Commit:

```bash
git add docs/mempalace-shadow/"$WEEK".md
git commit -m "rubric(mempalace): week $WEEK tally"
```

### Exit criteria (spec §7.4 P2)

**3 consecutive weeks** where:
- `mempalace_only_hit ≥ v3_only_hit`
- `(both + mempalace_only) ≥ 85%`

If any week fails, the 3-week counter resets. You're looking for 3 weeks in a row of clean wins, not 3 out of N.

### Handling rubric failure

If after 4-5 weeks the rubric isn't converging:
1. Is the issue ingestion? Check that shadow writes are actually populating palaces. Compare `/var/mempalace/palaces/*` sizes against expected per-user-turn growth.
2. Is the issue retrieval? Mempalace might be finding technically-correct-but-semantically-wrong matches. Inspect a sample of "mempalace_only_miss" entries in shadow logs.
3. Is the issue the rubric itself? Verify `tools/mempalace-rubric` is classifying hits correctly. Sample 10 hits manually.
4. Is this just not a mempalace-shaped problem? If your users' queries are mostly short-window recent-context (recency-biased), legacy v3 might genuinely be adequate. That's a legitimate finding. At that point, stay at shadow indefinitely (sidecar alive, no user impact), or roll back to off.

**Do not advance to P3 without the rubric math passing.** The phase-log P3 section requires a rubric-math approval block; if you can't write it honestly, don't rotate.

### P2 → P3 transition

If rubric passes 3 consecutive weeks:

1. Fill phase-log P2 section with:
   - Links to the 3 passing weekly rubric files
   - Final rubric math summary
2. Write approval block in P3 section:
   ```
   Approved: <your name> 2026-MM-DD
   Rationale: 3-week rubric confirmed 2026-W<X>/W<Y>/W<Z>, 
   mempalace_only_hit = <A> vs v3_only_hit = <B>, 
   (both + mempalace_only) = <C>%.
   ```
3. Commit both.

### Rollback from P2

Same as P1: `mempalace-mode off`. No user impact to unwind.

---

## Phase 3 — Active, legacy standby (projected ~2026-05-30 → ~2026-07-30)

- **Config**: `MEMPALACE_MODE=active`, `LEGACY_MEMORY=on`
- **Duration**: ≥55 days + two monthly capacity reviews

### Why this phase exists

First phase with user-visible impact. Mempalace output replaces legacy v3 in the prompt. Users experience conversation continuity across sessions, context that survives compaction, structured retrieval across their full palace history. Legacy remains on standby for rollback — code paths are still present, just not primary.

### Rotate

```bash
mempalace-mode active
```

Verify `/admin/mempalace-mode` returns `"mode":"active"`. Commit phase-log P3 entry.

### Daily action (~5 min)

- Hot-path latency check: `xelasphere_mempalace_wakeup_latency_seconds{quantile="0.95"}`. Spec target ≤ 150ms hot.
- Error check: `journalctl -u xelasphere -u mempalace-sidecar --since "24h" --priority err --no-pager`
- User-visible signal: any support/feedback mentioning chat memory? (This is operator judgment, not automated.)

### Weekly action (~30 min)

- Capacity: disk growth rate trending against `MEMPALACE_CACHE_MAX_BYTES=2G`, KG daily spend trending against the $5/day cap, palace storage growth rate vs forecast.
- Continue weekly rubric data accumulation (not exit criterion at P3 but useful as a trend).

### Day-30 gate (~2026-06-29)

Run rubric re-run: does `mempalace_only_hit ≥ v3_only_hit` still hold at day 30? Record in phase-log P3 section. If regression, investigate — user patterns may have shifted, or ingestion may have drifted.

### Day-55 gate (~2026-07-24)

Same rubric re-run. Plus: two monthly capacity reviews must be signed off by this point.

**Capacity review template** (for each of the two):

```markdown
# P3 capacity review — 2026-MM

- Palace storage total: <GB> (rate: <MB/day>)
- KG daily spend: avg <$>, p95 <$>, cap <$5>
- Outbox depth p95: <rows>
- Wake-up latency p95 hot: <ms> (target ≤ 150ms)
- Cache hit rate: <%> (embedding cache)
- Cap adjustment needed: <yes/no — if yes, what>

Signed off: <name> 2026-MM-DD
```

### Exit gate

All of:
- Zero rollbacks in the 55+ day window
- Hot p95 ≤ 150ms sustained
- Day-30 rubric re-run passed
- Day-55 rubric re-run passed
- Two monthly capacity reviews committed

### Rollback from P3

`mempalace-mode shadow` (retain infrastructure) or `mempalace-mode off` (pure relief). Because legacy code paths are still present, rollback is at the same cost as P1/P2. This is the last phase where rollback is cheap.

---

## Phase 4 — Legacy removed (projected ~2026-07-30 → ~2026-08-05)

- **Config**: `MEMPALACE_MODE=active`, `LEGACY_MEMORY` env var **deleted**
- **Duration**: PR review + merge, ~1 week

### Why this phase exists

Remove the now-unused legacy code paths (`memory_v2/`, `memory_v3/`) so future contributors see one memory system, not two. Codebase simplification is the point. User-visible behavior is unchanged from P3.

### The P4 PR

```bash
git checkout -b feature/p4-legacy-removal

# Delete subsystems
rm -rf app/memory_v2/ app/memory_v3/

# Grep for legacy call sites in chat_routes.py and other blueprints
grep -rn "memory_v2\|memory_v3\|LEGACY_MEMORY" app/ --include="*.py"
# Edit each call site: remove the legacy branch, keep only the mempalace path

# Remove LEGACY_MEMORY gate in app/__init__.py
# Current: refuses to boot if both MEMPALACE_MODE=off AND LEGACY_MEMORY=off (unless XELASPHERE_POST_P4=1)
# New: mode=off is a valid outage-relief state; no both-off refusal needed
grep -n "LEGACY_MEMORY\|XELASPHERE_POST_P4" app/__init__.py app/mempalace/feature_flag.py

# Remove escape hatch (now default behavior)
# Edit app/mempalace/feature_flag.py: remove XELASPHERE_POST_P4 logic

# Spec + runbook + changelog updates
# Spec §3: amend the "Post-P4" section to describe final state
# Runbook: remove any references to legacy v2/v3 subsystems
# CHANGELOG: announce P4 — legacy memory removed
```

### Pre-merge tests

**Critical: mode=off must still boot.** This is the outage-relief invariant.

```bash
# On a test branch deploy (NOT prod):
MEMPALACE_MODE=off systemctl start xelasphere
# Expect: active (running), serves requests with no memory context (degraded gracefully)

# Then:
MEMPALACE_MODE=active systemctl restart xelasphere  
# Expect: normal operation
```

Full test suite must be green:
```bash
venv/bin/python -m pytest app/ tests/ -q
# Sidecar unchanged but re-verify:
cd mempalace-sidecar && .venv/bin/pytest tests/ -q
```

Rubric one more time for completeness (single-week snapshot as evidence of continued operation).

### Approval block

In phase-log P4 section:

```
Approved: <your name> 2026-MM-DD
Pre-merge verification:
  - mode=off boot test: passed
  - mode=active boot test: passed
  - Full test suite: <N> passed
  - Final rubric snapshot: <link to week file>
  - Sidecar tests: 118 passed + 1 skipped (unchanged)
```

### Merge

```bash
git checkout main && git pull
git merge feature/p4-legacy-removal --no-ff
git push
# Deploy via the same pattern as #6 / #7 rebuilds
```

Post-merge: confirm the same mode-rotation round-trip still works, confirm /readyz still green, confirm boot test at mode=off still works on live prod.

### Rollback from P4

More expensive than prior phases because code is gone. If a regression surfaces post-merge:

```bash
git revert <p4-pr-merge-commit> --no-edit
# Redeploy
```

This is why the P3 observation window is 55+ days — to be confident before doing the removal.

---

## Appendix A: Quick reference commands

### Rotate mode

```bash
/usr/local/bin/mempalace-mode {off|shadow|active}
```

### Check sidecar health

```bash
curl -sf --unix-socket /run/mempalace/sidecar.sock http://localhost/readyz
```

### Check current mode

```bash
curl -sf http://localhost:5000/admin/mempalace-mode
```

### Tail relevant journals

```bash
journalctl -fu mempalace-sidecar.service -u mempalace-flusher.service -u xelasphere.service
```

### Reinstall units after edit

```bash
install -m 0644 deploy/mempalace/mempalace-sidecar.service /etc/systemd/system/
install -m 0644 deploy/mempalace/mempalace-flusher.service /etc/systemd/system/
systemctl daemon-reload
systemctl restart mempalace-sidecar
systemctl restart mempalace-flusher  # only post-FHS migration
```

### Full provision.sh rerun (idempotent)

```bash
SIDECAR_WHEEL=/opt/xelasphere/mempalace-sidecar/dist/mempalace_sidecar-*.whl bash /opt/xelasphere/deploy/mempalace/provision.sh
```

---

## Appendix B: Dashboard URLs

- **Grafana** — `https://xelauvas.dev/status/grafana/` (HTTP basic auth: `xela` + htpasswd password, then Grafana login: `admin` + password)
- **MemPalace — Live** — Dashboards → Xela folder → `mempalace-live`
- **MemPalace — Rollout** — Dashboards → Xela folder → `mempalace-rollout`

---

## Appendix C: Key file paths

| Path | Purpose |
|---|---|
| `docs/mempalace-shadow/phase-log.md` | Authoritative state of rollout |
| `docs/ops/mempalace-m0-exit-checklist.md` | M0 exit gate (closed) |
| `docs/ops/mempalace-p1-entry-checklist.md` | P1 entry gate |
| `docs/ops/mempalace-runbook.md` | Incident response (R1-R15) |
| `docs/ops/mempalace-rollout-playbook.md` | This file |
| `docs/mempalace-shadow/<YYYY-WW>.md` | Weekly rubric math files (P2+) |
| `deploy/mempalace/provision.sh` | Idempotent deploy script |
| `deploy/mempalace/*.env.example` | Env-file templates (source of truth) |
| `deploy/mempalace/mempalace-sidecar.service` | Sidecar systemd unit |
| `deploy/mempalace/mempalace-flusher.service` | Flusher systemd unit |
| `tools/check-m0-exit.sh` | Automated gate script (42 checks) |
| `app/mempalace/` | Flask-side integration code |
| `app/mempalace/adapter.py:280` | Mode-off short-circuit for record_turn |
| `app/mempalace/flusher.py:76` | Flusher main loop |
| `mempalace-sidecar/` | Sidecar source tree |
| `/etc/mempalace/sidecar.env` | Installed sidecar env (generated by provision.sh) |
| `/etc/xelasphere/mempalace.env` | Installed Flask env (generated by provision.sh) |
| `/var/mempalace/palaces/<user_id>/` | Per-user palace storage |
| `/var/log/mempalace/shadow/` | Shadow log jsonl files (P1+) |
| `/var/lib/xelasphere/outbox.db` | Flask-side outbox SQLite |

---

## Appendix D: Tracked issues reference

| Issue | Title | Phase closed at |
|---|---|---|
| #2 | R7 KG cost + queue runbook | P1-entry work |
| #3 | R8 Circuit breaker runbook | P1-entry work |
| #4 | R13 Incident class + response | P1-entry work |
| #5 | Signal-handler tombstone (M0.10) | Closed at M0.25 |
| #6 | Flask FHS migration | P1-entry work (Step 1) |
| #7 | Socket mode 0660 tombstone | P1-entry work (Step 2) |
| #8 | Containerized CI smoke-test | P1-entry work (Step 4) |

---

## Appendix E: "If something surprises you" decision tree

Mid-phase, if a surprise surfaces:

1. **Is it a mempalace-attributed error?** If no — unrelated, handle independently. If yes, go to 2.
2. **Is it user-impacting?** If yes — `mempalace-mode off` immediately, investigate after. If no, go to 3.
3. **Is it at mode=off?** (P0 phase, or mid-rollback.) Something is deeply wrong. Revert the most recent deploy and investigate on a snapshot.
4. **Is it at mode=shadow?** (P1/P2 phase.) No user impact. Investigate in place. Only rotate to off if the issue is growing (e.g., outbox unbounded).
5. **Is it at mode=active?** (P3 phase.) Assess severity. For degraded-but-serving: consider rotating to shadow (falls back to legacy for reads, mempalace continues writing). For broken-and-serving-wrong: rotate to off.
6. **After the rotate**: write a phase-log Rollback entry with timestamp + reason + R-number. Spec §7.4 requires rollback logging.

---

## Appendix F: Agent handoff notes

If a new session is picking up this work:

1. Read `docs/mempalace-shadow/phase-log.md` to see current state.
2. Read this playbook's current-phase section.
3. Check GitHub issues filter `label:p1-entry` to see open P1-entry work.
4. Run `tools/check-m0-exit.sh` — confirms repo-level state (42/42 green).
5. Run the daily health check commands from the P0 section.
6. Confirm with the operator before any destructive command (`rm -rf`, `systemctl stop xelasphere`, `chown -R`, `git revert`, force-push).

Previous session artifacts (reference only, not authoritative):
- M0 full arc: commits `33906c0` → `98e23ef` → `4c0406c` (M0.26a through P0 health-check doc)
- 42/42 check script green as of `830ca85`
- Six drift classes documented in `830ca85` commit body + phase-log M0→P0 entry

---

*End of playbook. Update this file at each phase transition with actuals.
Keep it alongside the phase-log as a team-readable single-source-of-truth
for where the rollout is and what comes next.*
