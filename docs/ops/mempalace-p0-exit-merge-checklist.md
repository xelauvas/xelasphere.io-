# P0 exit merge checklist

One-time operational checklist for the P0 → main merge event on 2026-04-25 evening. Companion to `docs/mempalace-shadow/phase-log.md` (record-of-fact) and `docs/ops/mempalace-rollout-playbook.md` § Phase 0 exit gate (plan-of-action).

**Merge target:** [PR #12](https://github.com/xelauvas/xelasphere.io-/pull/12) — currently draft.
**Target execution window:** 2026-04-25 after 23:16:26 UTC. Budget ~40 min.
**Authoritative reference runs (Friday's CI compared against these):**
- First green: [run 24830281779](https://github.com/xelauvas/xelasphere.io-/actions/runs/24830281779)
- Post-cleanup green: [run 24830468197](https://github.com/xelauvas/xelasphere.io-/actions/runs/24830468197)

---

## Step 0. Cold re-read — 5 min

Before executing anything below, read this entire checklist top-to-bottom as if you didn't write it. Checklists drafted in-context omit things that felt obvious at authoring time but aren't 48 hours later. If anything on the cold re-read is ambiguous, underspecified, or assumes context you no longer have loaded — **fix the checklist first, commit the fix on main, then execute**. An extra 5 minutes of re-read is cheap; discovering mid-merge that step 4 is underspecified is expensive.

Specifically watch for:
- Commands that assume a shell state (directory, env vars) you haven't set.
- "Expected" outputs that were pattern-matched from day-2 state but may have drifted.
- References to issues / commits that have moved since authoring.
- Any step that reads as "do the obvious thing" without naming what that is.

If the checklist is clean on re-read, proceed to Last-minute abort conditions.

---

## Last-minute abort conditions

If **ANY** of the following is true on 2026-04-25 evening, **abort the merge** and document the abort reason in `docs/mempalace-shadow/phase-log.md` P0 section. These are binary — no judgment calls, no "just this one."

- [ ] Day-3 health check (`tools/mempalace-p0-daily-check.sh`) returns any mempalace-attributed error in either journal.
- [ ] `/readyz` is not `{"ok":true,...}` or `mempalace-sidecar.service` is not `active`.
- [ ] Flask is not serving. From prod: `curl -sf -o /dev/null -w "%{http_code}\n" http://localhost:5000/` should return `200`. If executing from a laptop with SSH access, `curl -sf https://xelasphere.com/` is the external form; if that one fails with a hairpin/DNS issue but localhost works, Flask is fine — the external probe is just the network view, the localhost probe is authoritative.
- [ ] PR #12's most recent CI run is not green, or its step shape differs from the two reference greens above (duration anomaly, step reordering, silently-skipped step).
- [ ] Hetzner snapshot #379196442 is no longer listed. **Verification is browser-only** via the Hetzner console → Snapshots. No `hcloud` CLI is installed on prod by default; if operator has set it up separately, `hcloud image describe 379196442` also works. Otherwise, browser.
- [ ] Any issue has been filed between 2026-04-23 and merge time that materially changes PR #12's "NOT in scope" list (i.e., the new issue should be in-scope or should defer the merge).
- [ ] The person executing this checklist has not read it end-to-end before starting.

Abort is not failure — it's a signal that one more day of observation or one more tracked issue is needed before the merge. Document, set a new target date, resume on the new day.

---

## Step 1. Pre-merge state verification — 5 min

1.1 [ ] Day-3 P0 health check, **saving output to a file for the phase-log entry at step 5**:
```bash
bash tools/mempalace-p0-daily-check.sh 2>&1 | tee /tmp/p0-daycheck-day3.log
```
Expected clean: sidecar journal empty at `--priority err` since P0 entry, Flask journal shows only known-INFO chatter (the four #11-repro entries at 2026-04-22 23:17:08 UTC plus any new mempalace-tagged INFO of the same shape), `/readyz` returns `{"ok":true,"embedding_loaded":true,"db_ok":true,"audit_ok":true,"schema_version":2}`, service `active`. New error-priority entries → abort per conditions above.

Day-1 and day-2 outputs were captured during the days they ran (session transcripts from 2026-04-23). If those transcripts aren't available, document in the phase-log entry that day-1 / day-2 summaries are "re-derivable from the P0-entry-anchored journal query" — the cumulative-since-P0-entry window means the day-3 output implicitly contains all prior days' signal.

1.2 [ ] PR #12 CI green:
```bash
gh pr checks 12
```
Every check should be `pass`. If anything is `fail` or `pending`, stop and resolve (either wait for pending to complete, or treat fail as blocking per the "red CI is blocking" discipline from day-2).

1.3 [ ] Snapshot confirmation — Hetzner console → Snapshots → confirm **#379196442** still present, "Ready" status.

---

## Step 2. Merge sequence — 5 min

Done on the machine where you run git commands (operator's choice — can be prod, can be local).

2.1 [ ] Sync local main:
```bash
git checkout main
git fetch origin
git pull origin main
```

2.2 [ ] Merge feature branch with `--no-ff` (playbook §P0-exit step 3 is non-optional on this — `--no-ff` preserves the merge commit as an auditable anchor):
```bash
git merge feature/mempalace-integration --no-ff
```
No conflicts expected — the feature branch has been rebased on main's auto-backup commits throughout the arc. If there ARE conflicts, stop and triage; don't auto-resolve under merge-event pressure.

2.3 [ ] Verify the merge landed. Expected parent^2 = current feature branch HEAD (which you can verify at merge time via `git rev-parse origin/feature/mempalace-integration` — that value should match `git log main -1 --format=%P | awk '{print $2}'`):
```bash
EXPECTED_FEATURE_HEAD=$(git rev-parse origin/feature/mempalace-integration)
ACTUAL_FEATURE_HEAD=$(git log main -1 --format=%P | awk '{print $2}')
[ "$EXPECTED_FEATURE_HEAD" = "$ACTUAL_FEATURE_HEAD" ] && echo "OK parents match" || echo "MISMATCH"
git log --oneline -2 main   # latest should be the merge commit
ls app/mempalace/tests/test_shadow_mode.py   # must exist
ls .github/workflows/deploy-smoke-test.yml   # must exist
ls tools/mempalace-p0-daily-check.sh         # must exist + executable
```

2.4 [ ] **Capture the merge commit SHA** to a file, so rollback (if needed later) doesn't require git archaeology under stress:
```bash
MERGE_SHA=$(git rev-parse HEAD)
echo "$MERGE_SHA" > /tmp/p0-merge-sha-$(date -I).txt
echo "Merge commit: $MERGE_SHA (saved to /tmp/p0-merge-sha-$(date -I).txt)"
```

2.5 [ ] Push:
```bash
git push origin main
```

2.6 [ ] Confirm PR #12 auto-closes and the five `Closes #N` trailers fire (#2, #3, #4, #8, #9). Check via `gh pr view 12 --json state` (expect `CLOSED`, `mergeCommit` populated) and `gh issue view 2 --json state closedAt` (etc. for 3/4/8/9 — all should flip to `CLOSED` within ~1 min of the merge push).

---

## Step 3. Post-merge host actions — 10 min

All on the **prod Flask host** (currently `/root/xelas.io/xelasphere.io-` — the ad-hoc deployment per M0.26 tombstone; FHS migration is post-P0 per issue #6).

3.1 [ ] Pull merged main on prod:
```bash
cd /root/xelas.io/xelasphere.io-
git fetch origin main
git checkout main
git pull origin main
```
Working tree should be clean of **tracked-file modifications**. Untracked state (`claude-code (untracked content)` submodule-like + any transient `mempalace/` etc.) does not block the pull — git won't touch untracked files. If `git status` shows any *modified* or *staged* tracked files, stop and investigate — those may be in-progress work that would be overwritten by the pull.

3.2 [ ] **Sidecar: no action required.** The sidecar wheel currently installed at `/opt/mempalace-sidecar/venv/` was built from `mempalace-sidecar/` source at its last-touching commit. That commit is **830ca85** (M0.27 — config-drift audit + check-script hardening). Nothing since then has touched `mempalace-sidecar/` on the feature branch; verify with:
```bash
git log 830ca85..HEAD -- mempalace-sidecar/    # must be empty
```
(Chose 830ca85 as the anchor, not M0.27c/5eeec9a, because 830ca85 IS the actual last-sidecar-touching commit — robust to future reorderings. M0.27c happens to also work as an anchor only because nothing between 830ca85 and 5eeec9a touched sidecar either.) Sidecar stays running, `/readyz` stays green, no restart needed.

3.3 [ ] **Flask restart** — chat_routes.py changed (today's #9 fix at commit `f6c6906`). Restart to pick up the new code:
```bash
systemctl restart xelasphere
sleep 5
systemctl is-active xelasphere  # expect: active
```

3.4 [ ] `/readyz` remains green post-Flask-restart (sidecar is independent of Flask, should be unchanged):
```bash
curl -sf --unix-socket /run/mempalace/sidecar.sock http://localhost/readyz
```

3.5 [ ] `/admin/mempalace-mode` returns `{"mode":"off"}`:
```bash
curl -sf http://localhost:5000/admin/mempalace-mode
```
`off` is the correct post-merge state per PR #12's "NOT in scope" section — shadow rotation gates on #10.

3.6 [ ] Smoke-test chat flow — operator judgment on exact shape. Simplest path: open xelasphere.com in a browser, send a message, confirm response arrives, confirm no error banners. Second-simplest: tail `journalctl -fu xelasphere.service` while doing the above; no error-level lines should appear.

---

## Step 4. Post-merge verification — 5-7 min

4.1 [ ] Run the daily check script once more (post-merge, post-Flask-restart):
```bash
bash tools/mempalace-p0-daily-check.sh
```
Expected delta vs step 1.1: same shape, possibly with new INFO-level mempalace chatter from Flask-startup lines (the mempalace wiring imports and mode probe fire at app boot). Any error-level lines, or any mempalace-attributed output that looks like an actual exception trace, is a triage trigger — stop here, don't proceed to step 5.

4.2 [ ] Tail Flask for 2-3 minutes while real user traffic comes in:
```bash
journalctl -fu xelasphere.service
```
Look for mempalace-attributed errors. Mode is `off` so the wiring short-circuits at line 2697 of chat_routes.py (or the streaming equivalent) — seeing any mempalace-attributed work is unexpected and worth triage before proceeding.

---

## Step 5. Phase-log entry — 15 min

Fill `docs/mempalace-shadow/phase-log.md` § "P0 — Dark deploy" with the exit record. Per task #16, the framing **must** be explicit:

> P0 cleared at 2026-04-25 <HH:MM:SS> UTC. Shadow rotation remains gated on #10 (Path A integration scaffold). Mode stays at `off` post-merge. Rotation to `shadow` is a separate gate, not implied by this exit.

Contents to paste inline (all three health checks for the record):
- Day-1 health check: full output.
- Day-2 health check: full output.
- Day-3 health check: full output.
- Final `/readyz` response post-Flask-restart.
- Merge commit SHA.
- Link to PR #12 (now closed) and to this checklist commit.

Commit on main:
```bash
git add docs/mempalace-shadow/phase-log.md
git commit -m "feat(mempalace): P0 exit — 3 days clean, merged to main at mode=off"
git push origin main
```

---

## Step 6. Cleanup — 2 min

6.1 [ ] Delete Hetzner snapshot #379196442 — Hetzner console → Snapshots → #379196442 → Delete. Served its purpose through the P0 window; keeping it indefinitely costs ~€5/mo.

6.2 [ ] Mark day-2 / day-3 session tasks completed (#15 this checklist, #16 phase-log entry, #17 CI smoke-test — all now delivered).

---

## Rollback path

If anything in steps 3-4 surfaces an error, here's the rollback tree by cost:

**Application-level (fastest, but likely irrelevant):** `/usr/local/bin/mempalace-mode off` + Flask restart. Mode is already `off` post-merge, so this is a no-op — the mempalace-mode valve doesn't relieve this phase because mempalace isn't doing work. Application-level rollback doesn't apply here.

**Code-level (main path):** Revert the merge commit on main. The SHA was saved to `/tmp/p0-merge-sha-$(date -I).txt` at step 2.4 — read it rather than hunting via `git log`:
```bash
MERGE_SHA=$(cat /tmp/p0-merge-sha-$(date -I).txt)
# or if the /tmp file is lost: MERGE_SHA=$(git log main -1 --format=%H)
git revert -m 1 "$MERGE_SHA"    # -m 1 selects the mainline parent
git push origin main

# Then on prod:
cd /root/xelas.io/xelasphere.io-
git fetch origin main
git checkout main
git reset --hard origin/main          # verify `git status` is clean FIRST
systemctl restart xelasphere
```
Expected outcome: Flask back to pre-merge state (main's last good commit before the merge). Sidecar unaffected (no sidecar code in this merge).

**System-level (last resort):** Hetzner snapshot #379196442 → Rollback. ~30 seconds. Flask comes back at M0.26a state. **Important: the snapshot is consumed by rollback** — it's no longer valid as a future rollback target. If you take this path, create a new snapshot BEFORE attempting the merge again.

---

## NOT in scope for this merge

These are explicit deferrals from PR #12's body. Do NOT action during the merge window — each has its own track:

- **Shadow mode rotation** (`mempalace-mode shadow`) — gates on [#10](https://github.com/xelauvas/xelasphere.io-/issues/10).
- **Flask FHS migration** (/root → /opt/xelasphere) — [#6](https://github.com/xelauvas/xelasphere.io-/issues/6).
- **Socket mode 0660** — [#7](https://github.com/xelauvas/xelasphere.io-/issues/7).
- **memory_v2 privacy logging** — [#11](https://github.com/xelauvas/xelasphere.io-/issues/11).
- **Auto-backup cron pollution** — [#13](https://github.com/xelauvas/xelasphere.io-/issues/13).

If any of these starts feeling urgent during the merge window, **pause the merge**, file the urgency as a new issue, and add it to the abort conditions list for a future attempt. Don't fold in-scope shifts into the merge-event commit.

---

## Time budget summary

| Step | Budget |
|---|---|
| 0. Cold re-read | 5 min |
| 1. Pre-merge state verification | 5 min |
| 2. Merge sequence | 5 min |
| 3. Post-merge host actions | 10 min |
| 4. Post-merge verification | 5-7 min |
| 5. Phase-log entry | 15 min |
| 6. Cleanup | 2 min |
| **Total** | **~47-49 min** |

**If any single step blows past its estimate by >50%, pause.** Document the pause reason in the phase-log entry (step 5), step back, investigate before pushing through. An estimate overrun is a signal the reality has diverged from the plan; it's cheaper to burn an hour understanding the divergence than to force completion and discover the problem post-merge when rollback is more expensive.
