# MemPalace P1 pre-entry checklist

Run before flipping `MEMPALACE_MODE=off → shadow` (P0 → P1 transition). Created at M0.26 to house checks that M0-exit was incorrectly scoped to cover. Scope discipline: everything in this file verifies behavior that requires `MEMPALACE_MODE != off` or a deployment shape the M0 phase did not require.

## Pass criteria

**Every item either green or explicitly deferred-with-reason.** Same framing as the M0-exit checklist.

## Moved from M0-exit at M0.26

### Flusher functional drain

- [ ] With `MEMPALACE_MODE=shadow`, generate a turn via a chat request. Confirm `MEMPALACE_OUTBOX_PATH` SQLite shows a row added (`SELECT COUNT(*) FROM outbox`).
- [ ] Within `poll_interval_s + post_json budget` (default 1s + 6.3s worst-case = ~8s), confirm the row's `status` transitions to `sent` (or `failed` with a specific error — that's still the flusher doing work, not dormancy).
- [ ] Confirm `xelasphere_mempalace_flush_batch_seconds` Prometheus metric fires (flusher's per-batch histogram observation, only emitted when rows are processed).

Why this lives here and not M0-exit: flusher functional drain requires a non-empty outbox, which requires `mode != off`, which does not happen during P0 dark-deploy. See the M0.26 amendment in `docs/ops/mempalace-m0-exit-checklist.md` § "Why the flusher check was split at M0.26" for the reasoning chain.

## Prerequisite: Flask FHS migration

P1 entry is blocked on moving Flask from the ad-hoc `/root/xelas.io/xelasphere.io-` deployment to spec §7.1's `/opt/xelasphere` layout with `User=xelasphere`. The flusher unit's `User=xelasphere` cannot execute anything under `/root` (0700 mode, non-root users cannot traverse), so flusher-as-xelasphere is infeasible without the migration.

- [ ] Codebase relocated to `/opt/xelasphere/` with appropriate ownership.
- [ ] State directories under `/var/lib/xelasphere/` (outbox, etc.) chowned to `xelasphere:xelasphere`.
- [ ] `xelasphere.service` updated: `User=xelasphere`, `WorkingDirectory=/opt/xelasphere`, `ExecStart=/opt/xelasphere/venv/bin/gunicorn ...`, `EnvironmentFile=/etc/xelasphere/xelasphere.env` (or equivalent FHS location).
- [ ] `xelasphere` user already exists from M0.26 forward-prep; confirm it is a member of `mempalace` group (provision.sh step 2 handles this).
- [ ] Flask boots cleanly from the new layout; all M0.25 app-side tests green against the new venv path.
- [ ] Flusher starts as `User=xelasphere` without `exec: permission denied`.

Tracked in GitHub issue: *(TBD — filed at M0.26 per `p1-entry` label)*.

## Shadow-mode behavior authoring (from M0-exit known-gaps #4)

- [ ] Tests authored against actual shadow-mode semantics (`log_shadow` payload shape, `record_turn` pair_id propagation, `wake_up_sync` call contract). M0.23.5 Path A ruling deferred this authoring to P1 entry.

## Streaming assistant-half `record_turn` (from M0-exit known-gaps #2)

- [ ] Decision: either wire assistant-half `record_turn` on the streaming SSE path (closes orphan pair_id window), or accept orphan assistant-halves per spec §4.4 tolerance and document the acceptance decision.

## STUB runbook expansions (P0.2 → P1 entry target, existing work)

- [ ] R7 KG cost + queue runbook section expanded (issue #2).
- [ ] R8 Circuit breaker runbook section expanded (issue #3).
- [ ] R13 Incident class identified + author response procedure (issue #4).

## Sign-off

Template entry for `docs/mempalace-shadow/phase-log.md`:

```markdown
## P1 entry

- Date: YYYY-MM-DD
- P0 dark-deploy duration: N days (started YYYY-MM-DD)
- Flusher functional-drain: verified / deferred with reason: <...>
- Flask FHS migration: completed at commit <sha>
- Shadow-mode tests authored: yes / deferred with reason: <...>
- Streaming assistant-half record_turn: wired / accepted-as-orphan per spec §4.4
- STUB runbook expansions: #2/#3/#4 closed
- Rotating MEMPALACE_MODE=shadow at <timestamp>
```
