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

**Tombstone (from the former STUB text):** the KG worker is **not** a separate systemd unit. It runs as an asyncio background task inside the sidecar lifespan (`mempalace_sidecar/main.py:168` → `run_kg_worker`, `app.state.kg_worker_task`). Earlier STUB fallback that said "check `mempalace-kg-worker` service health" was wrong — there is no such service. Worker health is sidecar health (R1) plus the metrics below.

**Response:**

1. **Classify which alert fired.** Cost alerts (`ApproachingCap`, `ProjectionBust`) and queue alerts (`QueueStuck`, `QueueSlowlyDying`) have different diagnostic paths. Don't conflate — cap exhaustion legitimately drops queue throughput to zero and will trigger queue alerts downstream; work the cost path first in that case.
2. **Current spend vs cap.** `mempalace_kg_daily_usd_spent` gauge; cap is `kg_daily_usd_cap` (default $5.0/day, config-driven). When spend ≥ cap, the worker increments `mempalace_kg_extraction_total{status="skipped_cap"}` instead of extracting. That's the correct behavior — cost is bounded. The queue will resume draining at UTC midnight when the daily counter rolls over.
3. **Queue depth breakdown.** `mempalace_kg_queue_depth{status}` — gauge labeled `pending | running | failed`. Healthy shape: pending > 0 during ingest bursts but draining steadily; running ≤ `kg_worker_concurrency` (config, small single-digit); failed is sticky — see step 5.
4. **Extraction outcome breakdown.** `rate(mempalace_kg_extraction_total{status}[10m])` labeled `success | failure | skipped_cap`:
   - `skipped_cap` dominating → cap is exhausted. Confirm with step 2; if intended, wait for UTC midnight. If spending rate was unexpected, see step 6 (cost diagnosis).
   - `failure` dominating → check sidecar journal for KG provider errors (default `kg_llm_provider=ollama`; if switched to Anthropic, check API status externally). Failures increment the `attempts` column on the `kg_pending` row; at 5 attempts the row flips to `status='failed'` and stops being claimed.
   - `success` but queue not shrinking → ingest rate exceeds extraction rate. Not a bug, capacity question.
5. **Queue stuck vs slowly dying.**
   - `QueueStuck` (page, no progress in N minutes): worker task likely dead or blocked. `systemctl status mempalace-sidecar` + `journalctl -u mempalace-sidecar --since '10 min ago' | grep -i kg`. If the worker task crashed silently (asyncio task exceptions inside the sidecar process don't take the whole sidecar down), the sidecar itself will still be `active` and `/readyz` green — but extraction stops. Restart the sidecar to respawn the worker task: `systemctl restart mempalace-sidecar`.
   - `QueueSlowlyDying` (warn): queue drain rate trailing ingest rate by a widening margin. Pre-emptive. Could be Anthropic API latency elevated, could be capacity, could be rows piling up in `failed` (check `kg_queue_depth{status="failed"}` separately).
6. **Cost diagnosis (ApproachingCap / ProjectionBust).** When cap is being consumed faster than expected:
   - Check extraction volume: `rate(mempalace_kg_extraction_total{status="success"}[1h])` — any single-user spike? Heavy ingest from a single palace → bulk extraction session that wasn't anticipated in the cap sizing.
   - Provider cost per call: if `kg_llm_provider=anthropic` (non-default), per-call costs are higher than `ollama` (local, free). A recent provider flip + same traffic pattern = cap bust.
   - **Temporary cap adjustment:** edit `kg_daily_usd_cap` in `/etc/mempalace/sidecar.env` + `systemctl restart mempalace-sidecar`. Prefer temporary increase over cap removal — the cap's purpose is to bound surprise-spending from a runaway ingest.
   - **Temporary worker pause:** set cap to `0.0` instead of removing/stopping the worker. With cap 0, worker runs but every tick reports `skipped_cap` and no API calls fire. Queue retains pending rows for eventual drain. Cleaner than stopping the sidecar (which takes down the whole mempalace read path).
7. **Never `DELETE FROM kg_pending`** for queue relief. Same invariant as R4 — the conversation is the system of record, KG extraction is downstream; losing a pending row means that conversation's KG is permanently incomplete. Let failed rows accumulate (they're bounded by attempts-cap) and investigate offline.

---

## R8 — Circuit breaker

**Alerts:** `MempalaceCircuitOpen` (warn), `MempalaceCircuitFlapping` (warn, >20 transitions in 30m).

**Model.** `SidecarClient` / `SyncSidecarClient` each maintain their own circuit breaker per gunicorn worker process. Three states (`app/mempalace/client.py:70-72`): `CLOSED` → `OPEN` (after N=5 consecutive outer-call failures) → `HALF_OPEN` (after 30s cooldown, auto-transition). HALF_OPEN is a **re-probe window**, not a single-probe gate — it tolerates up to N-1 failures before re-tripping; one success anywhere in the window transitions back to CLOSED. `_consecutive_failures` resets on OPEN→HALF_OPEN so reprobe starts with a fresh N-count window. Failures that count: `ConnectError`, `TimeoutException`, 5xx `HTTPStatusError`, malformed JSON on 2xx. Failures that don't: 4xx (including 429) — deterministic, no retry, no breaker impact.

**Metric.** `xelasphere_mempalace_circuit_state{worker_pid}` — gauge per gunicorn worker. `0=closed 1=half 2=open`. Multiple worker PIDs → multiple series. One worker's breaker going OPEN doesn't affect the others' requests; each worker's state is independent.

**Response:**

1. **Which alert fired.** `CircuitOpen` is "sustained unavailability"; `CircuitFlapping` is "intermittent unavailability with recovery attempts." Different root causes.
2. **State per worker.** `xelasphere_mempalace_circuit_state` broken out by `worker_pid`. If *all* workers are OPEN simultaneously, the root cause is shared (sidecar down, UDS socket broken, network partition) — jump to step 3. If *one* worker is OPEN while others are CLOSED, the root cause is worker-local (transport stuck, httpx client wedged, file descriptor exhausted in that process) — jump to step 5.
3. **All-workers-OPEN → route to R1.** The 5-consecutive-failures threshold is hit by every worker trying and failing the same sidecar. `curl -sf --unix-socket /run/mempalace/sidecar.sock http://localhost/readyz` — if that fails too, the sidecar itself is the problem; follow R1.
4. **Flapping across workers → route to R5.** 20+ transitions in 30m = sidecar is responding intermittently, not cleanly down. Breaker trips, cools down, one reprobe succeeds, a couple later fail, trips again. Check `rate(mempalace_ingest_total{status="error"}[10m])` split by error class. Intermittent 5xx is the diagnosis; proceed as R5.
5. **Single-worker-OPEN while sidecar is healthy.** Worker's httpx transport has wedged (rare but possible — long-lived async connection pool with a dead socket). No manual reset API exists for the breaker. Restart the Flask service: `systemctl restart xelasphere`. All workers get fresh breakers in CLOSED state. If the single-worker-OPEN pattern recurs after restart on the *same* PID, the worker is being assigned requests that systematically fail (e.g., long-lived user session with bad state) — investigate request-level correlation, don't chase the breaker.
6. **Manual close / bypass: there isn't one.** The breaker has no exposed close-by-force endpoint, by design — "force closed despite failures" is the exact shape of "operator self-DOS." Cooldown is 30s; wait for auto-HALF_OPEN and let reprobes converge. If sidecar truly will not come back and mempalace reads must be bypassed: `mempalace-mode off` (universal relief valve). That skips the sidecar call entirely at the adapter level and legacy reads serve the chat path; breakers stay OPEN harmlessly until the next restart.
7. **Multi-worker interaction.** Because each worker has its own breaker, a sidecar that's up-and-healthy after a blip will see per-worker recovery at different wall-clock times — the metric series "heal" independently over the 30s cooldowns. That's expected; don't interpret "some workers still OPEN at T+30s" as a partial outage. Alert tuning in spec §8.2 accounts for this (alert fires on `max(...) == 2` sustained, not any worker OPEN).

**Authoritative-runbook resolution when multiple alerts fire.**

- If `SidecarDown` + `CircuitOpen` both firing → **R1 is authoritative.** Circuit open is consequent; fixing the sidecar fixes the breaker via cooldown.
- If `IngestFailureRate` + `CircuitFlapping` both firing → **R5 is authoritative.** Flapping is the breaker correctly oscillating around intermittent sidecar failure; fix the failure rate and flapping resolves.
- If `CircuitOpen` alone fires with no other alert → this section is authoritative. Almost always means one-off worker-local issue (step 5).

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
