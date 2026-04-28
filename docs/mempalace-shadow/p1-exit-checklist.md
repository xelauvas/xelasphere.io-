# P1 → P2 Exit Checklist

Operator runbook for the P1 shadow phase exit and rotation to P2. Reference at week 3 (~2026-05-18, calendar floor: P1 entered 2026-04-27 21:48:20 UTC).

Spec source: `docs/mempalace-shadow/README.md` §7.4. Phase log: `docs/mempalace-shadow/phase-log.md` §P1.

## Pre-flight

Don't even start the exit gate evaluation until all of these are true:

- [ ] At least 3 calendar weeks have elapsed since P1 entry (2026-04-27 21:48 UTC). Minimum exit date: **2026-05-18**.
- [ ] Daily health check (`sudo cat /var/log/mempalace/shadow-health.last`) has been GREEN for the last 7+ consecutive days. YELLOW days within the 3-week soak are OK if rationally explained (e.g., one-off restart spike); RED days must be investigated and resolved before counting toward the 3-week clock.
- [ ] `systemctl --failed` shows no mempalace-related failures (excluding the daily health check, which exits non-zero on YELLOW by design — see `mempalace-shadow-health.service` SuccessExitStatus comment).
- [ ] All three core services are still active:
  ```
  for s in xelasphere mempalace-sidecar mempalace-flusher; do systemctl is-active $s; done
  ```
- [ ] Production mode is still `shadow`:
  ```
  curl -s http://127.0.0.1:5000/admin/mempalace-mode
  # expect: {"mode":"shadow"}
  ```

## Gather exit-criteria evidence

Five criteria from spec §7.4 / phase-log P1 `Exit criterion` line. Each needs concrete data.

### (1) Outbox steady ≤1k rows p95

```
sudo -u xelasphere sqlite3 /var/lib/xelasphere/outbox.db \
  "SELECT COUNT(*) AS total, SUM(CASE WHEN status='pending' THEN 1 ELSE 0 END) AS pending FROM outbox;"
```

Read total + pending. If pending > 1000 at any point during the 3-week soak (check via daily `.last` archive), criterion fails — investigate per runbook R4 before re-evaluating. Steady = no growing trend in pending count over the last 7 days.

### (2) Ingest success ≥99.5%

```
journalctl -u mempalace-flusher.service --since '21 days ago' --no-pager \
  | grep -cE 'POST.*200|POST.*5[0-9][0-9]|POST.*4[0-9][0-9]'
```

Compute: `2xx_count / total_count`. Must be ≥0.995. If <99.5%, check daily error-scan output for ingest 4xx/5xx clustering — likely sidecar or token issue per runbook R5.

### (3) Shadow log populated

```
sudo wc -l /var/log/mempalace/shadow/shadow-*.jsonl
```

Should show one file per day for the last 21 days, each with line count roughly proportional to daily chat traffic. Zero-line days are only acceptable if `chat_events_today` was also zero (verifiable via `journalctl -u xelasphere.service --since 'YYYY-MM-DD 00:00:00 UTC' --until 'YYYY-MM-DD 23:59:59 UTC' | grep -c 'Memory V3: Shadow mode'`).

### (4) Disk growth within forecast

```
sudo cat /var/log/mempalace/disk-growth.csv
```

The daily check (PR #23) records one row per day. After 8+ rows, the check itself fires YELLOW if today's total exceeds 3× rolling 7-day median. If no anomaly fired during the soak, criterion passes. Eyeball: total bytes should grow roughly linearly with chat volume.

### (5) Single-user rebuild benchmark measured (issue #24)

**Status as of 2026-04-28: harness NOT yet built.** Issue #24 deferred to week 3 because:

- Needs assumptions about real data shape that only emerge after weeks of soak
- `mempalace rebuild --user <uid> --from-conversations` referenced in runbook is currently aspirational — only the sidecar HTTP route `/users/{uid}/rebuild` exists (with a 100-conversation cap requiring batching)
- Conversation enumeration must come from xelasphere's source-of-truth (Firestore)

**Build steps at week 3:**

1. Pick representative user: highest message count from outbox by then.
   ```
   sudo -u xelasphere sqlite3 /var/lib/xelasphere/outbox.db \
     "SELECT user_id, COUNT(*) AS n FROM outbox GROUP BY user_id ORDER BY n DESC LIMIT 5;"
   ```
2. Enumerate that user's conversations from Firestore (use existing `xelasphere/services/firestore_*.py` patterns).
3. Chunk into ≤100-conversation batches per sidecar `/users/{uid}/rebuild` POST.
4. Wrap with `/usr/bin/time -v` for wall + peak RSS.
5. Capture pre/post Groq accountant token deltas.
6. Compute Chroma throughput from drawer-count delta / wall time.
7. Write output to `docs/mempalace-shadow/rebuild-benchmark-<uid>-<date>.md` per issue #24 schema.

## Document in phase-log

Edit `docs/mempalace-shadow/phase-log.md` P1 section. Replace:

```
- Exit verified: `<YYYY-MM-DD, link to data>`
```

with:

```
- Exit verified: 2026-05-XX. Evidence:
  - Outbox p95 ≤1k: <number>
  - Ingest success rate: <percent>
  - Shadow log days populated: <X/21>
  - Disk growth: <bytes>/day, no anomaly
  - Rebuild benchmark: docs/mempalace-shadow/rebuild-benchmark-<uid>-2026-05-XX.md
```

Commit with `docs(phase-log): P1 exit verified 2026-05-XX`.

## Rotate to P2

P2 = active mode. Both reads AND writes go to mempalace. Legacy v3 still on (`LEGACY_MEMORY=on`) as fallback.

```
sudo mempalace-mode active
```

The script:

1. Atomically rewrites `MEMPALACE_MODE=active` in `/etc/xelasphere/mempalace.env`
2. Restarts xelasphere.service
3. Polls `/admin/mempalace-mode` for up to 10s, exits 0 on `{"mode":"active"}`

Total wall time target: <60s. If the script returns "WARN: could not verify mode change", roll back immediately:

```
sudo mempalace-mode shadow
```

…then investigate before retrying.

## P2 entry verification

Within 5 minutes of rotation:

- [ ] `curl -s http://127.0.0.1:5000/admin/mempalace-mode` returns `{"mode":"active"}`
- [ ] All three services still active
- [ ] Trigger a manual chat as the test user via xelasphere.io and verify the response uses mempalace context (look for `🧠 [Stream] Memory V3: Active mode` or equivalent in `journalctl -u xelasphere.service -f`)
- [ ] No spike in `xelasphere_mempalace_wakeup_skipped_total{reason="circuit_open"}` (would indicate sidecar is failing reads)
- [ ] No 500s in xelasphere journal during the test chat

If any check fails, immediately `sudo mempalace-mode shadow` and file a P2-entry incident.

## P2 entry — phase-log update

Edit `docs/mempalace-shadow/phase-log.md` P2 section. Set:

```
- Entered: 2026-05-XX HH:MM:SS UTC (`mempalace-mode active` round-trip verified)
- Config: MEMPALACE_MODE=active, LEGACY_MEMORY=on
- Approver: <self or named reviewer>
```

## P2 monitoring switch

After rotation, the daily check still runs but the criteria shift:

- Shadow JSONL count is no longer the primary signal (writes still go there for audit, but reads from mempalace are now what matters)
- Watch for: rubric weekly tally `mempalace_only_hit >= v3_only_hit` over 3 consecutive weeks
- Per spec §7.4 the P2 exit gate is `mempalace_only_hit >= v3_only_hit` for 3 consecutive weeks AND `(both_hit + mempalace_only) >= 85%` of rated samples

The rubric harness (`/opt/xelasphere/tools/mempalace-rubric --sample 20`) runs weekly during P2 and writes to `docs/mempalace-shadow/YYYY-WW.md`.

## Backout plan

If anything goes wrong at any point during P2:

```
sudo mempalace-mode shadow
```

…or even safer, if the sidecar itself is causing chat failures:

```
sudo mempalace-mode off
```

`off` is the universal relief valve and is safe through P3 while `LEGACY_MEMORY=on`. Both transitions are <60s and verified.
