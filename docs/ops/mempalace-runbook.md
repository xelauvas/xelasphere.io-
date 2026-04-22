# MemPalace runbook

Operator-facing response procedures for the mempalace integration. Keyed by R-number; each `R<n>` corresponds to one or more alerts in spec §8.2.

Alerts fire at two severities — `page` (on-call response, 24×7) and `warn` (next business day, inspect). The `mempalace-mode off` command is the universal relief valve and is safe through P3 while `LEGACY_MEMORY=on`.

---

## R1 — SidecarDown

**Alert:** `MempalaceSidecarDown` (page).

**Response:**

1. `systemctl status mempalace-sidecar` — check process state and last exit reason.
2. `journalctl -u mempalace-sidecar --since '15 min ago'` — look for crash trace.
3. If crash-looping: check for corrupt-palace signature in the trace (e.g., `RuntimeError: corrupt palace dir /var/mempalace/palaces/<uid>/`). Isolate the offending palace per R10, restart the service, and file an R10 follow-up.
4. If socket-permission drift: `ls -la /run/mempalace/sidecar.sock` should show `mempalace:mempalace` mode `0660`. Wrong ownership breaks Flask's reach.
5. While down: Flask itself stays up. Verify `xelasphere_mempalace_wakeup_skipped_total{reason="circuit_open"}` grows but `rate(chat_requests_failed_total[5m])` stays flat — confirms failure isolation is holding.

**Do not** `mempalace-mode off` unless the sidecar's own recovery is taking >10 minutes. The flag is for sustained outage, not flapping.

---

## R2 — ReadyzFailing / PalacePermError

**Alerts:** `MempalaceReadyzFailing` (page), `MempalacePalacePermError` (warn).

**Response:**

1. `curl -s --unix-socket /run/mempalace/sidecar.sock http://localhost/readyz` — direct probe.
2. If `503 not ready` with `embed=loading`: embedding model is still loading. Wait up to 2 min from service start (sd_notify READY is gated on model load).
3. If `503 not ready` with `embed=error`: check journalctl for `sentence-transformers` import / download failures. Usually a network/disk issue on the sidecar host.
4. For `PalacePermError`: a specific user's palace directory has wrong ownership. `chown -R mempalace:mempalace /var/mempalace/palaces/<uid>/` if the ownership drifted; investigate how.

---

## R3 — Wake-up hot p95 high

**Alert:** `MempalaceWakeupHotP95High` (page): `histogram_quantile(0.95, mempalace_wakeup_seconds_bucket{hot="1",layer="total"}) > 0.3` for 10m.

**Response:**

1. Confirm baseline: cold-palace p95 has a larger budget (500ms hard cap per spec §4.2); hot-palace p95 should be <150ms. If the alert is firing on `hot="1"`, the handle is in the LRU cache and something at the sidecar is slow.
2. Check `mempalace_wakeup_seconds` split by layer (`l0_recent`, `l1_session`, `l2_topic`, `l3_semantic`) — which layer is dominating?
3. If L3 (semantic search) is slow: likely embedding-cache cold for this query vocabulary. Temporary; resolves as cache warms.
4. If L0/L1 (metadata scan + turns.sqlite): check disk IO on the sidecar host. `/var/mempalace/` on a slow volume.
5. If all layers are slow: check CPU pressure on the sidecar host (`top`, `uptime`). Memory pressure + swap is the common cause.

---

## R4 — Outbox backlog / Flusher stalled

**Alerts:** `MempalaceOutboxBacklogWarn` (warn, >10k pending), `MempalaceOutboxBacklogPage` (page, >30k pending), `MempalaceFlusherStalled` (page, tick older than 60s).

**Response:**

1. **Never `DELETE FROM outbox`.** Conversations are system of record; rebuild is the recovery.
2. `mempalace-mode shadow` (if currently `active`) — reduces sidecar read pressure while backlog drains. If already `shadow` or `off`, skip.
3. `systemctl status mempalace-flusher` + `journalctl -u mempalace-flusher --since '10 min ago'` — is the flusher running? Is it getting 5xx from the sidecar?
4. If flusher is running but `flusher_last_tick_seconds` is stale: check for DB lock. `fuser /var/lib/xelasphere/outbox.db`. If another process has it, investigate.
5. If flusher is stuck on an unresponsive batch (no progress): `py-spy dump --pid $(pgrep -f mempalace-flusher)` for a live thread dump.
6. If sidecar is healthy but ingest rate is throttled: ingest rate is `max_retries × (timeout + backoff)` per row. Increase `MEMPALACE_FLUSHER_BATCH_SIZE` temporarily via the flusher unit's environment.
7. Once backlog is drained, re-run `mempalace-mode active` if appropriate.

---

## R5 — Ingest failure rate

**Alert:** `MempalaceIngestFailureRate` (page): `rate(ingest_total{status="error"}[10m]) / rate(ingest_total[10m]) > 0.01` AND rate is nontrivial.

**Response:**

1. Check ingest error distribution: `rate(mempalace_ingest_total{status="error"}[10m])` by error class (5xx vs 4xx vs client-side).
2. 5xx dominant: sidecar health issue. Check R1 first.
3. 4xx dominant (especially 400 Idempotency-Key mismatch): Flask flusher sending mismatched headers. Deploy-regression signal; check recent mempalace-flusher wheel version.
4. 401 dominant: `MEMPALACE_TOKEN` rotation incomplete. Re-run provision.sh step 4 to atomically rewrite both env files.
5. 503 dominant: sidecar is shutting down (SIGTERM received). Check if a deploy is in progress.

---

## R6 — Cache thrashing

**Alert:** `MempalaceCacheThrashing` (warn): eviction rate exceeds ingest rate.

**Response:**

1. Verify palace-cache sizing: `mempalace_cache_bytes` should be well under 2G (MemoryHigh). If it's at the cap, cache is constantly evicting to stay under.
2. Check top palaces: `topk(20, mempalace_palace_bytes)`. A user with >500MB of drawers forces the cache to evict smaller users repeatedly.
3. If one user is dominating: consider whether that user should be rate-limited at ingest time, or whether their drawers are bloated by bad redaction (R15).
4. Short-term: raise `MEMPALACE_CACHE_MAX_BYTES` + restart sidecar if memory headroom allows. Long-term: capacity planning (memory or sharding).

---

## R7 — KG cost + queue

**Alerts:** `MempalaceKGCostApproachingCap` (warn), `MempalaceKGCostProjectionBust` (warn), `MempalaceKGQueueStuck` (page), `MempalaceKGQueueSlowlyDying` (warn).

**STUB — expansion tracked in [#2](https://github.com/xelauvas/xelasphere.io-/issues/2), target P1 entry.**

Initial fallback (until P0 expansion): treat cost alerts as 24-hour observations (warn only, no page unless stuck). Queue-stuck page → check `mempalace-kg-worker` service health (separate unit if deployed) + verify Anthropic API status via external dashboard.

---

## R8 — Circuit breaker

**Alerts:** `MempalaceCircuitOpen` (warn), `MempalaceCircuitFlapping` (warn, >20 transitions in 30m).

**STUB — expansion tracked in [#3](https://github.com/xelauvas/xelasphere.io-/issues/3), target P1 entry.**

Initial fallback: `MempalaceCircuitOpen` for 5m → escalate to R1 (sidecar is likely the common cause). Flapping → check R5 (intermittent 5xx from sidecar).

---

## R9 — Migration failed

**Alert:** `MempalaceMigrationFailed` (page), auto-suppressed if wheel rollback within 30m.

**Response:**

1. `journalctl -u mempalace-sidecar | grep migration` — find the failing migration and schema version it was attempting.
2. Check the `.replaced_<ts>/` directory for the affected palace — per spec §4.4, the pre-migration `meta.json` is preserved there with its original schema_version.
3. If migration crashed mid-run: the atomic-rename-only-on-verification-passes invariant (spec §4.4) should mean the primary palace is untouched. Confirm via `meta.json` schema_version match.
4. If the migration has a known-bad wheel version: wheel rollback is the fix. Rollback within 30m suppresses the alert.
5. Post-rollback, file an issue with the migration code + the `.replaced_` artifact for forensics.

---

## R10 — Palace corruption

**Alert:** `MempalacePalaceCorrupt` (page).

**Response:**

1. `mv /var/mempalace/palaces/<uid> /var/mempalace/palaces/<uid>.corrupt.$(date -Is)` — isolate the corrupt palace. Don't delete.
2. `mempalace rebuild --user <uid> --from-conversations` — rebuild from the app's conversation history (the system of record). May take 10-30 minutes per user depending on drawer count.
3. Retain the `.corrupt.*` directory for **30 days** (matches restic retention) for upstream bug reports.
4. Audit log at `audit.sqlite` ensures the timeline is clear: "corrupted-at-ts, isolated-at-ts, rebuilt-at-ts."
5. **Never mutate the original `U/` during migration until verification passes.** If you find yourself tempted to "just fix the meta.json," stop and rebuild instead.

---

## R11 — Disk low

**Alert:** `MempalaceDiskLow` (warn): mempalace volume <15% free.

**Response:**

1. **Shadow logs first**, then palaces. `du -sh /var/mempalace/shadow_logs/*.jsonl*`. If logrotate hasn't rotated recently, manually rotate.
2. If shadow logs are within forecast but palaces are ballooning: `du -sh /var/mempalace/palaces/* | sort -h | tail -20` — find offenders.
3. One user with >5GB of drawers usually means a bad attachment ingest. Check `xelasphere_mempalace_redaction_hits_total{rule="*"}` for that user's time range — if redaction fired a lot, likely no leak but lots of large-text attachments. If redaction fired rarely on the bloat period, investigate R15.
4. `mempalace-kg-pending` can also bloat if KG worker is stuck — R7.
5. Short-term: increase volume size (growable disk) or offload shadow logs to object storage with a lifecycle policy.

---

## R13 — (reserved STUB)

**STUB — definition + expansion tracked in [#4](https://github.com/xelauvas/xelasphere.io-/issues/4), target P1 entry.** Per spec §8.3 R13 is reserved for an incident class to be identified from P0/P1 observation.

---

## R14 — (reserved)

Per spec §8.3 R14 is noted as "inline" but §8.2 has no alert pointing at it. Reserved R-number; operator to author when the corresponding alert is defined. Until then, no response procedure.

---

## R15 — Redaction broken

**Alert:** `MempalaceRedactionBroken` (warn): `rate(redaction_hits_total[24h]) == 0` for 24h AND `rate(ingest_total[24h]) > 0`.

**Response:**

1. Compare `app/mempalace/redaction/redaction_rules.yaml` on the current deploy against the last-known-good in git: `git diff <last-working-sha> -- app/mempalace/redaction/redaction_rules.yaml`.
2. Check test-run history: did redaction tests fail during the last deploy but the deploy shipped anyway? (Shouldn't happen with proper CI; verify.)
3. If the rules file regressed: redeploy the last-known-good version.
4. If the rules file is correct but hits are still zero: verify `scrub()` is actually being called in the record_turn path. `grep 'scrub' app/mempalace/adapter.py` should show the call; a regression that removed it would stop redaction silently.
5. Consider that zero hits is legitimate if traffic is mode=off (no writes happening at all). Cross-check `rate(xelasphere_mempalace_inserts_dropped_total{reason="mode_off"}[24h])` — if mode-off drops are high and ingest_total is from pre-flip drain, the alert is noise and should be silenced until mode=shadow.

---

## Universal relief valve

`mempalace-mode off` via `/usr/local/bin/mempalace-mode`. Safe through P3 while `LEGACY_MEMORY=on`. Target wall time <60s. Always followed by `systemctl restart xelasphere` (baked into the script).

Post-P4 (legacy removed), `mempalace-mode off` still boots (per spec §3 Post-P4 section) but the chat flow has no memory system. Treat post-P4 mode=off as an outage-relief state only, not steady-state.
