# MemPalace M0 pre-P0 exit checklist

Run before flipping to P0 (dark deploy). This is a verification checklist, not a build list — everything named here should already exist from prior milestones. The checklist's job is to surface gaps, not to block.

## Pass criteria

**Every item either green or explicitly deferred-with-reason.** Some items are STUBs or known gaps; the checklist's job is to name them so they don't ship silently.

## Automated checks — run `tools/check-m0-exit.sh`

```
$ tools/check-m0-exit.sh
```

42 mechanical checks covering the repo-level M0 artifacts. Current state: **all 42 passing** (as of the M0.27 commit). Count grew from 27 → 42 at M0.27 when § 8 was rewritten to cross-reference the sidecar Settings class against template declarations, verify every `<TODO>` placeholder has a matching substitution in provision.sh, and guard against vestigial template vars. See `§ 8` in `tools/check-m0-exit.sh` for the hardening shape and the M0.27 commit body for the scoping rationale.

Covered categories:
1. Flask-side test suite (app/mempalace + tests/test_m023_5_smoke).
2. Grafana dashboard JSON validity.
3. Systemd unit file syntax via `systemd-analyze verify`.
4. Deploy-script syntax (bash + python).
5. CI legacy-import guard wired and passing in warn mode.
6. Runbook + logging-privacy doc present with all R-numbers (R1-R15) + explicit STUB markers on R7/R8/R13.
7. Shadow-rubric harness + docs/mempalace-shadow/ scaffolding.
8. Environment-file templates with correct defaults.
9. Sidecar install artifact: `SIDECAR_WHEEL` env points at an existing wheel, or `mempalace-sidecar/dist/*.whl` exists (added M0.26).

If `check-m0-exit.sh` exits non-zero, fix the failing items before proceeding to deployment checks.

## Deployment-environment checks (P0 entry, not verifiable from repo)

These require a target host with systemd + Flask install + Grafana access. Walked through at P0.1 (per plan line 4832).

- [ ] `deploy/mempalace/provision.sh` runs cleanly on a staging box from a clean state.
- [ ] Systemd unit `mempalace-sidecar.service` enables + starts without restart loop: `systemctl status mempalace-sidecar` shows `active (running)`.
- [ ] Systemd unit `mempalace-flusher.service` is enabled + `systemctl is-active` returns `active`, with zero restarts in the first 60s. **Scope: unit parses, process starts, stays up.** Does NOT verify functional drain (that's P1-entry — see `docs/ops/mempalace-p1-entry-checklist.md`).
- [ ] `/readyz` returns `{"ok": true, ...}` within 30s of sidecar start.
- [ ] Flask gunicorn picks up the `mempalace` group after `systemctl restart xelasphere` (post-check in provision.sh step 8 passes).
- [ ] `mempalace-mode off|shadow|active` round-trips correctly with Flask restart + `/admin/mempalace-mode` probe verification.
- [ ] `docs/ops/dashboards/mempalace-live.json` imports into Grafana without errors; panels render (even if "No data" for metrics not yet firing).
- [ ] `docs/ops/dashboards/mempalace-rollout.json` same.

### Why the flusher check was split at M0.26

The original M0.24 check — *"mempalace-flusher.service enables + starts without restart loop"* — conflated two different questions: **(a)** does the unit parse and the process start (M0-relevant — a syntactically broken unit file is an M0 bug); **(b)** does the flusher do useful work (P1-relevant — requires a non-empty outbox, which requires `MEMPALACE_MODE!=off`, which does not happen during P0 dark-deploy). At M0.26 the critical-path investigation confirmed the `(b)` half is inert at `MEMPALACE_MODE=off`:

- `app/mempalace/adapter.py:280` short-circuits `record_turn` before any `outbox.insert` when mode is `off` — the single Flask-side funnel to the outbox.
- `app/mempalace/flusher.py:73-74` docstring explicitly calls out "alive but idle" as a designed-for state: the loop polls, finds nothing, emits `flusher_tick`, sleeps.
- `docs/superpowers/plans/2026-04-20-mempalace-integration-deltas.md:67` (M0.18 stage) is prior precedent: *"default mode=off means no writes happen regardless."*

No hidden coupling surfaced (no side-effect at flusher startup that sidecar checks don't cover). Splitting the check lets M0-exit honestly reflect what was verified in this phase; functional-drain verification moves to P1-entry where it belongs. See the P1-entry checklist for the moved check.

## Sidecar-environment checks (separate venv)

The sidecar has its own Python environment under `mempalace-sidecar/` with its own dependencies (chromadb, etc.). The Flask venv doesn't have these. Run sidecar tests in the sidecar's environment:

- [x] `cd mempalace-sidecar && pytest -v` passes in the sidecar venv. — **118 passed, 1 skipped** (skip: `install_signal_handlers not implemented`, pre-existing) as of 2026-04-22 against locally-created venv (`python3 -m venv .venv && pip install -e '.[dev]'`). Staging-host sidecar venv must repeat this check as part of deployment verification.

If sidecar tests fail in the sidecar venv, that's a sidecar-build issue, not an M0.24 gap per se — but it blocks P0 entry either way (P0 requires sidecar running).

## Runbook STUB issues (P0.2 task, not M0.24)

The runbook has three STUB entries (R7, R8, R13) and one reserved entry (R14). Per plan P0.2, file GitHub issues for each STUB at P0 entry:

- [x] Issue: "Expand R7 — KG cost + queue runbook section." Target: P1 entry. — [#2](https://github.com/xelauvas/xelasphere.io-/issues/2)
- [x] Issue: "Expand R8 — Circuit breaker runbook section." Target: P1 entry. — [#3](https://github.com/xelauvas/xelasphere.io-/issues/3)
- [x] Issue: "Expand R13 — identify incident class + author response procedure." Target: P1 entry. — [#4](https://github.com/xelauvas/xelasphere.io-/issues/4)
- R14 is reserved (no alert currently targets it per spec §8.2); no issue needed until an alert is defined.

After filing, reference issue numbers in `docs/ops/mempalace-runbook.md` at each STUB location.

## Known gaps acknowledged-not-blocking

These are real gaps that don't block P0 (they'll surface at specific later gates). Documented here so they don't get lost.

1. **8 pre-existing test failures in `tests/`** (web_fetcher MagicMock encoding issue; smart_router / smart_suggestions / artifacts issues). Verified to predate M0.23.5 (see commit `85dfeac`). Separate concern; not blocking P0 since Flask-side mempalace test suite (app/mempalace/) is green.

2. **Streaming assistant-half `record_turn` not wired** (M0.18 finish commit `af7549a`). Streaming SSE path assembles assistant text across multiple exit branches; a clean wrapper pattern wasn't bolted on inline. Consequence: at mode=shadow+, streaming-path turns will produce orphan pair_ids (user-half recorded, assistant-half missing). Spec §4.4 tolerates orphans. Will surface as P1-entry work when shadow-mode testing is authored.

3. **`search_memory` tool not registered in any tools list** (M0.18 finish commit `af7549a`). Codebase has no LLM tool-calling infrastructure (`get_ai_response` is prompt-in / text-out). Registering the tool requires building tool-calling infrastructure first — its own project. Not blocking P0 since mempalace-side tool schema + handler already ship.

4. **Shadow-mode behavior testing deferred to P1 entry** (M0.23.5 Path A ruling). The M0.23.5 gate addressed mode=off preservation; shadow-mode behavior testing is P1's own testing concern, not a pre-P1 gate. P1 entry requires authoring tests against actual shadow-mode semantics (log_shadow payload shape, record_turn pair_id propagation, wake_up_sync call contract) — documented in `docs/mempalace-shadow/phase-log.md` P1 section.

5. **CI legacy-import guard in `warn` mode only.** Flip to `--mode block` at P3 entry per spec §7.5. One-line change in `.github/workflows/ci.yml`; allowlist is already complete (zero hits in both modes).

6. **Dashboards are skeletons.** `docs/ops/dashboards/README.md` frames these as "import, tune in Grafana, re-export." The JSON imports cleanly but visual polish / threshold tuning is operator work at deployment time.

7. **Runbook R14 reserved**; no alert in spec §8.2 currently targets it. Operator to author when a corresponding alert is defined.

## What M0 did NOT include

Explicit scope boundaries — these are P1+ concerns, NOT M0 gaps:

- Chat-flow end-to-end integration tests against real LLM response paths (P1 or later when shadow-mode testing is built).
- Per-provider LLM tool-call infrastructure (needed before `search_memory` can be meaningfully registered).
- Full Grafana dashboard polish (deployment-time iteration).
- R7/R8/R13 runbook deep-expansion (P0.2 files the issues; actual expansion at P1 entry).
- Capacity testing / load testing (P1+ via shadow traffic).

## Declaring M0 done

Sign off in the phase-log when the automated + deployment + sidecar checks are all green or explicitly-deferred. Template entry:

```markdown
## M0 complete → P0 entry

- Date: YYYY-MM-DD
- Automated checks: 26/26 passing (commit `<sha>`)
- Deployment checks: all green / deferred notes: <...>
- Sidecar checks: green / version: <...>
- P0.2 STUB issues filed: #<n>, #<n>, #<n>
- Entering P0 dark deploy at <timestamp>
```

Then copy this block to `docs/mempalace-shadow/phase-log.md` under the P0 section + commit. That commit + its message are the audit trail for M0-closed-out-P0-entered.
