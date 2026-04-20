# MemPalace Integration — Design

**Date:** 2026-04-20
**Status:** Design frozen, awaiting user approval before implementation plan.
**Scope:** Integrate the MemPalace library as XelaSphere's long-term memory backend, running as a localhost sidecar over a Unix domain socket. Existing `memory_v2` and `memory_v3` are kept preserved-but-dormant through a staged rollout, then deleted after a 60-day clean active-mode window.

---

## 1. Problem statement and goals

XelaSphere currently ships two internal memory subsystems (`app/memory_v2/` and `app/memory_v3/`) that are buggy and not reliably useful at retrieval time. MemPalace is a local-first, verbatim memory library with strong retrieval benchmarks (LongMemEval R@5 96.6% raw, no LLM required) and an architecture (wings / rooms / drawers + knowledge graph + AAAK index) that matches the access patterns XelaSphere needs.

**Core principle (load-bearing everywhere below):**

> **Conversations in the app's primary DB are the system of record. The palace is a derived index.**
> Palace loss = re-index, not data loss. Every recovery, backup, and debugging decision is derived from this.

**Goals**

- Replace the practical function of `memory_v2`/`memory_v3` with MemPalace, staged over five named phases.
- Serve multiple users with per-user palace isolation from day one. Real users will show up.
- Degrade gracefully: a broken or slow palace never breaks a chat. `mempalace-mode off` is a one-command relief valve.
- Preserve the legacy memory modules as dormant-but-callable for a documented window, and delete them on a clear criterion.

**Non-goals for v1**

- Cross-region / multi-host backup, HA failover, palace sharing, external API access to the palace, at-rest encryption beyond what the filesystem already provides, distributed tracing, SLO error-budget accounting.

---

## 2. Architecture

```
                     ┌────────────────────────────────────────┐
                     │        XelaSphere Flask (gunicorn)     │
                     │                                        │
   user turn ──▶ orchestrator ──▶ mempalace_client (UDS) ─────┼──┐
                     │                                        │  │
                     │        memory_v2 (dormant, kept)       │  │
                     │        memory_v3 (dormant, kept)       │  │
                     └────────────────────────────────────────┘  │
                                                                 │
                              /run/mempalace/sidecar.sock (UDS)  │
                                                                 ▼
                     ┌────────────────────────────────────────┐
                     │     mempalace-sidecar (systemd unit)   │
                     │     FastAPI wrapping mempalace.Palace  │
                     │                                        │
                     │     POST /palace/{uid}/ingest          │
                     │     POST /palace/{uid}/wake-up         │
                     │     POST /palace/{uid}/search          │
                     │     GET  /palace/{uid}/drawers         │
                     │     POST /palace/{uid}/rebuild         │
                     │     DEL  /palace/{uid}                 │
                     │     POST /palace/{uid}/kg/query        │
                     │     /healthz /readyz /metrics          │
                     │                                        │
                     │     one ChromaDB, one embedding model  │
                     └──────────────┬─────────────────────────┘
                                    │
                     /var/mempalace/palaces/<user_id>/
                         ├── chroma/        (vector store)
                         ├── kg.sqlite      (knowledge graph)
                         └── meta.json      (schema_version, created_at)
```

**Transport:** Unix domain socket at `/run/mempalace/sidecar.sock`, mode `0660`, group `mempalace`. Gunicorn's user is added to that group. The sidecar binds only to the UDS; TCP is never opened.

**Authentication:** `X-Sidecar-Token` header checked by FastAPI middleware. Token is 32 random bytes, generated once at provisioning, stored in `/etc/mempalace/sidecar.env` and XelaSphere's env file. **Tokens do not rotate in v1**; rotation requires a scheduled ~60s outage window. This is a design constraint, not an oversight.

**Per-user isolation:** the sidecar dispatches by `user_id` in the URL. Each user gets a `Palace` instance backed by their own directory. Palaces are cached in a size-weighted LRU, capped at `MEMPALACE_CACHE_MAX_BYTES` (default 2 GB resident). Cold palaces close their ChromaDB handle after `MEMPALACE_IDLE_TTL_SEC` (default 14400 = 4h). Cold-wake-up rate is monitored; if it exceeds 5% of wake-ups during P2, TTL is raised to 8h.

**Failure model:** Flask wraps every call in a timeout — 500ms for hot reads, 2s for cold reads, fire-and-forget for writes (durable outbox picks up slack). On timeout or error, memory injection is skipped and chat continues. Circuit breaker trips after N consecutive failures, closes after cooldown. Breaker state is per-worker (each gunicorn worker has its own view of sidecar health); this is known and accepted for v1.

---

## 3. Flask-side components

All new code lives under `app/mempalace/`. The rest of the app imports only `app/mempalace/adapter.py`; everything else is an implementation detail.

```
app/mempalace/
├── __init__.py
├── client.py           # UDS HTTP client → sidecar. Timeouts, retries, circuit breaker.
├── adapter.py          # The only module the rest of the app imports. Stable API.
├── outbox.py           # Durable write queue (SQLite outbox + background flusher).
├── wakeup.py           # Provider-specific formatters for WakeupContext.
├── tool.py             # `search_memory` tool schema + handler for LLM tool-calls.
├── shadow_sink.py      # Non-blocking async writer for shadow-mode logs.
├── redaction/
│   ├── __init__.py
│   ├── rules.py
│   ├── README.md       # The four-step feedback loop lives here.
│   └── tests/
├── feature_flag.py     # Reads MEMPALACE_MODE env: off|shadow|active. Also LEGACY_MEMORY.
├── metrics.py          # Light counters + timing for Flask side.
└── tests/
```

**Adapter API — the only public surface:**

```python
class MempalaceAdapter:
    def wake_up(user_id, query) -> WakeupContext | None
    def search(user_id, query, k=5) -> list[Drawer]
    def record_turn(user_id, session_id, turn_id, pair_id, role, text,
                    attachments=None, request_id=None)
    def delete_palace(user_id, requested_by, reason) -> DeletionReceipt
    def list_drawers(user_id, wing=None, limit=100, cursor=None)
    def health() -> dict
```

`WakeupContext` is a structured object (`l0_recent`, `l1_session`, `l2_topic`, `l3_semantic`, `meta`). Provider-specific formatters in `app/mempalace/wakeup.py` turn it into a system prompt block for each model family (`to_anthropic_system`, `to_openai_system_msg`, `to_gemini_contents`, `to_local_prompt_block`). Orchestrator's per-model adapter picks the right formatter — formatting does not live inside the palace adapter.

**Orchestrator integration points (three touch sites):**

1. **Pre-model hook** — `wake_up()` before the LLM call. Result injected as a system block (active mode) or enqueued to the shadow sink (shadow mode). Shadow-mode enqueue is non-blocking via `asyncio.Queue.put_nowait()`; a dedicated writer task drains to disk.
2. **Tool registry** — register `search_memory(query, wing?, k=5)` tool. On failure, returns `{"results": [], "note": "no results"}` — indistinguishable from a genuine empty search. Never the string "memory unavailable," which causes some models to apologize to the user.
3. **Post-turn hook** — `record_turn()` for both user and assistant messages, sharing a `pair_id`.

**Dormancy of `memory_v2`/`memory_v3`:** no code changes inside those directories. Each existing call site is wrapped:

```python
if feature_flag.legacy_memory_active():
    result = memory_v3.retrieve(...)
else:
    result = None
```

`DEPRECATED.md` added to each directory at P3 entry with deletion target date, rollback procedure, and the grep command (`rg -n 'memory_v2|memory_v3' app/`) to find call sites.

**Feature flag — `MEMPALACE_MODE`:**

- `off` — **no reads, no writes.** New `record_turn()` calls are dropped silently with `xelasphere_mempalace_inserts_dropped_total{reason="mode_off"}` increment. Pre-flip outbox rows drain best-effort for 10 minutes, then are marked `abandoned` (metric increment). If you need to preserve writes during an outage, use `shadow`, not `off`.
- `shadow` — writes happen (outbox + sidecar), reads happen, results logged via `shadow_sink` but **not** injected into the prompt.
- `active` — writes + reads + inject.

**Cutover safety:** startup check in `app/__init__.py` — if `MEMPALACE_MODE=off` AND `LEGACY_MEMORY=off`, Gunicorn refuses to boot. A SIGHUP reload with both-off also refuses, keeping the old config serving. Prevents "no memory at all" incidents.

---

## 4. Data flow

### 4.1 Write path

```
Flask request                  SQLite outbox              Flusher thread                    Sidecar                 Palace
─────────────                  ─────────────              ──────────────                   ─────────               ──────
orchestrator.handle_turn()
 ├─ redaction.scrub(text)
 ├─ adapter.record_turn(..) ─▶ INSERT (turn_id uuidv7, pair_id, request_id, status=pending)
 ├─ LLM call
 ├─ redaction.scrub(asst)
 └─ adapter.record_turn(..) ─▶ INSERT (same pair_id)
 return 200                        ↑ commit + fsync
                                   │
                          SELECT WHERE status='pending' ORDER BY id LIMIT batch
                                   │
                          POST /palace/{uid}/ingest
                          Idempotency-Key: <turn_id>
                          X-Sidecar-Token: ...
                          X-Request-ID: ...
                          body: {session_id, pair_id, role, text, ts, attachments}
                                                                                    ─▶ acquire per-user write lock
                                                                                       SELECT 1 FROM ingest_log
                                                                                         WHERE turn_id=?
                                                                                       INSERT (unique on turn_id)
                                                                                       append drawer
                                                                                       update wing/room index
                                                                                       enqueue KG extraction
                                                                                       200 {status: accepted|duplicate}
                          UPDATE outbox SET status='sent' WHERE id=?
```

- **At-least-once delivery + idempotency key → effectively exactly-once drawer.** The sidecar's `ingest_log` table is disk-backed with `UNIQUE(turn_id)`. In-memory idempotency would fail the named crash-recovery test in §5.
- **Ordering guarantee**: single flusher thread in v1. If sharded later, shard key is `hash(user_id)`. Never round-robin.
- **Retries**: exponential backoff 1s, 4s, 15s, 60s, 300s. Row stays `pending` between attempts.
- **Sent-row retention**: 7 days. Forensics only — the app DB is the system of record, not the outbox.
- **Backpressure**: outbox depth > 50k → `record_turn` returns without inserting, `xelasphere_mempalace_outbox_rejected_total` increments. No user-visible error.
- **Redaction**: runs before the outbox insert. Regex corpus includes OpenAI/Anthropic/Google/Slack/AWS API-key prefixes, Luhn-valid credit cards, US SSN, JWT. Rules in `redaction_rules.yaml`, feedback loop in `redaction/README.md`.
- **Graceful shutdown**: SIGTERM drains flusher for up to 10s; in-flight batch rows revert cleanly to `pending` so the next restart picks up.

### 4.2 Read path A — pre-model wake-up (every turn)

Query construction: `{latest_user_message}\n{preceding_assistant_message}`, embedded once, reused across L2 and L3 inside the sidecar.

```
adapter.wake_up(uid, query)
  timeout 500ms hot / 2s cold, 1 retry, circuit-breaker open → None
       │
       ▼
  sidecar: asyncio.gather(L0, L1, L2, L3) — parallel, single embedding shared
       │
       ▼
  WakeupContext { l0_recent, l1_session, l2_topic, l3_semantic, meta }
       │
orchestrator:
  if MEMPALACE_MODE == "shadow":
      shadow_sink.enqueue({query, v3_result, mempalace_result})   # non-blocking
  if MEMPALACE_MODE == "active":
      system_prompt.append(wakeup.to_<provider>(ctx))
```

Latency budgets:
- Hot palace: p95 ≤ 150ms target, 500ms hard cap.
- Cold palace (first open after idle): up to 2s. First turn after idle gets no memory injection — acknowledged UX cost. Mitigated by pre-warming.

**Pre-warming:** WebSocket open or first HTTP session-start fires `POST /palace/{uid}/wake-up?warmup=true`. Fire-and-forget, hydrates the palace handle before the first real turn.

### 4.3 Read path B — tool-call search

```
LLM emits: search_memory(query, wing?, k=5)
  → orchestrator.dispatch_tool("search_memory", args)
  → adapter.search(uid, query, wing, k)
  → POST /palace/{uid}/search
  → [Drawer] with {text, wing, room, ts, score}
```

On failure: `{"results": [], "note": "no results"}`. Never the word "unavailable."

### 4.4 Per-user palace lifecycle

```
first ingest for U        → create /var/mempalace/palaces/U/ + meta.json v1
                            cache Palace handle in LRU
idle 4h                   → flush + evict
next request for U        → re-open (triggers lazy migration check)
account deletion          → DELETE /palace/U: two-phase
                              (1) mark user pending_palace_deletion in app DB
                              (2) purge outbox rows for uid
                              (3) sidecar DELETE → close handles, rm -rf, audit row
                              bounded 30s; failures retried by nightly reaper
sidecar schema upgrade    → on next open: if meta.version < current, lazy migrate;
                              if meta.version > current, refuse with 500
```

### 4.5 Failure-mode matrix

| Failure                      | Write path                                  | Wake-up                     | Tool call                    |
|------------------------------|---------------------------------------------|-----------------------------|------------------------------|
| Sidecar crash                | Outbox retains; flusher retries             | `None` → skip injection     | `{results: [], note: ...}`   |
| Sidecar slow (> timeout)     | Flusher waits, row stays `pending`          | Skip injection this turn    | Partial or empty result      |
| UDS permission drift         | Outbox retains; logs alert                  | `None`                      | Empty result                 |
| Disk full on sidecar host    | Sidecar 507; outbox retries                 | Skip injection              | Empty result                 |
| Palace corruption (one user) | That user's writes fail; others unaffected  | That user: no injection     | That user: empty             |
| App outbox DB down           | `record_turn` log-only fallback + metric    | Unaffected                  | Unaffected                   |
| Flask circuit breaker open   | Writes still queue to outbox                | Skip injection              | Empty result                 |

**Shadow → active flip is a one-line env change + gunicorn restart — zero code deploy.**

---

## 5. Sidecar internals

### 5.1 Package layout

```
mempalace-sidecar/                 (subdir within xelasphere.io-; see §8 for split trigger)
├── pyproject.toml
├── mempalace_sidecar/
│   ├── main.py              # FastAPI app, lifespan (warm embedding, open audit db, sd_notify READY=1)
│   ├── routes/
│   │   ├── palace.py        # ingest, wake-up, search, drawers
│   │   ├── kg.py            # kg/query
│   │   ├── lifecycle.py     # DELETE, rebuild
│   │   └── ops.py           # healthz, readyz, metrics
│   ├── auth.py              # X-Sidecar-Token middleware
│   ├── registry.py          # PalaceRegistry: size-weighted LRU + per-user aiorwlock
│   ├── wakeup.py            # Concurrent L0/L1/L2/L3 composition
│   ├── kg_queue.py          # Durable kg_pending queue + batched worker
│   ├── audit.py             # Append-only audit SQLite
│   ├── migrations/          # atomic-rename runner + per-version modules
│   ├── metrics.py           # Prometheus multiprocess-safe collectors
│   ├── config.py            # Pydantic settings
│   └── shutdown.py          # SIGTERM handler
└── tests/
```

### 5.2 API surface

| Method | Path | Body | Returns | Notes |
|---|---|---|---|---|
| POST | `/palace/{uid}/ingest` | `{turn_id, session_id, pair_id, role, text, ts, attachments?}` | `{status: accepted\|duplicate}` | `Idempotency-Key` = turn_id. Write lock. |
| POST | `/palace/{uid}/wake-up?warmup=0\|1` | `{query, k?, layers?}` | `WakeupContext` | Read lock. 500ms hot / 2s cold. |
| POST | `/palace/{uid}/search` | `{query, wing?, k=5}` | `[Drawer]` | Read lock. Tool calls. |
| GET  | `/palace/{uid}/drawers?wing=&limit=&cursor=` | — | `{drawers, next_cursor}` | Admin + GDPR access. |
| POST | `/palace/{uid}/rebuild` | `{conversations: [...]}` or `{from_path}` | `{ingested, skipped, job_id}` | Long-running, streaming. |
| DELETE | `/palace/{uid}` | `{requested_by, reason}` | `{status, bytes_removed}` | Atomic dir rename → rm → audit row. |
| POST | `/palace/{uid}/kg/query` | `{subject?, predicate?, object?, at?}` | `[Triple]` | KG reads. |
| GET | `/healthz` | — | `{ok, version, uptime_s}` | Liveness. |
| GET | `/readyz` | — | `{ok, embedding_loaded, db_ok, audit_ok}` | Readiness — fails if model not loaded. |
| GET | `/metrics` | — | Prometheus text | Multi-process safe. |

OpenAPI spec is auto-generated by FastAPI from Pydantic models; CI snapshots it and fails any PR that changes the spec without bumping `schema_version` in `/healthz`. No hand-maintained pact file.

### 5.3 PalaceRegistry — dispatch and caching

- `_cache: dict[user_id, PalaceEntry]` — hot palaces.
- `_lru_keys: OrderedDict` — access-order.
- `_total_bytes: int` — sum of resident palace sizes. Size sampled on open + on-write.
- `_max_bytes: int` = `MEMPALACE_CACHE_MAX_BYTES` (default 2 GB).
- `_locks: dict[user_id, aiorwlock.RWLock]` — per-user RWLock. Reads parallel, writes exclusive.
- Registry-level dict mutations guarded by a single `asyncio.Lock`.

**Eviction** is async and acquires the user's write-lock first (drains in-flight ops). Idle TTL 4h — longer than typical chat gaps, deliberately, to mitigate cold wake-ups.

### 5.4 KG extraction — durable queue

```sql
CREATE TABLE kg_pending (
  id INTEGER PRIMARY KEY,
  user_id TEXT,
  drawer_ids TEXT,        -- JSON array
  status TEXT,            -- pending | running | done | failed
  attempts INTEGER,
  last_error TEXT,
  enqueued_at, updated_at,
  UNIQUE(user_id, drawer_ids_hash)
);
```

Single `asyncio` worker. Every 30s (or signal from ingest): pulls batches, runs **one** LLM NER+RE call per batch of **N=8 drawers**, writes triples into the user's `kg.sqlite` with `valid_from=drawer_ts`. Retries/backoff identical to the Flask outbox. Cost ceiling from `MEMPALACE_KG_DAILY_USD_CAP`; cap hit → worker pauses (metric alert); queue keeps growing until next day. KG metadata stays inside each palace directory, so it's included in daily backups (not regenerated on restore).

### 5.5 Migrations — atomic

Sibling-directory pattern:
1. `copytree U/ → U.migrating/` (hardlinks when on same fs).
2. Apply migrations in order.
3. `fsync`, then `rename(U.migrating, U)` after moving old to `U.replaced_<ts>`.
4. On crash: leftover `*.migrating`/`*.replaced_*` older than 24h reaped on next open. The original `U/` directory is never mutated until verification passes — rollback is always `rm -rf U.migrating/`.
5. Schema too new → refuse with 500, "sidecar too old."

### 5.6 Shutdown protocol (SIGTERM)

1. FastAPI lifespan stops accepting new connections.
2. Signal flusher + KG worker to drain.
3. Wait up to **10s** for in-flight handlers.
4. Cancel stragglers; writes roll back cleanly so outbox retries pick up.
5. Close all Palace handles (fsync Chroma + KG).
6. Exit. `TimeoutStopSec=15s` in systemd (10s drain + 5s grace).

### 5.7 Config (`/etc/mempalace/sidecar.env`)

```
MEMPALACE_SOCKET=/run/mempalace/sidecar.sock
MEMPALACE_SOCKET_GROUP=mempalace
MEMPALACE_TOKEN=<32 random bytes, base64>
MEMPALACE_DATA_DIR=/var/mempalace
MEMPALACE_CACHE_MAX_BYTES=2147483648
MEMPALACE_IDLE_TTL_SEC=14400
MEMPALACE_EMBED_MODEL=all-MiniLM-L6-v2
MEMPALACE_KG_LLM_PROVIDER=ollama|groq|anthropic
MEMPALACE_KG_DAILY_USD_CAP=5.00
MEMPALACE_LOG_LEVEL=info
PROMETHEUS_MULTIPROC_DIR=/var/run/mempalace/prom
MEMPALACE_SHADOW_SAMPLE_RATE=0.20
MEMPALACE_USER_ID_HASH_SALT=<random>
```

---

## 6. Testing strategy

Five-layer pyramid.

**Layer 1 — Flask unit tests** (`app/mempalace/tests/`, ≤60s)

- `test_client.py` — UDS connect, timeout behavior, retries, circuit-breaker open/close, `ChaosTransport` for in-process fault injection.
- `test_outbox.py` — commit/fsync, flusher batching, backoff, idempotency-key persistence, 50k-row rejection, 7-day sent purge, graceful-shutdown resume.
- `test_redaction.py` — table-driven positive + negative cases for every rule in `redaction_rules.yaml`.
- `test_wakeup_formatters.py` — structural assertions (exact N entries in L0, token budget, monotonic timestamps, pair_id present) — **not** byte-exact snapshots.
- `test_feature_flag.py` — `off`/`shadow`/`active` branching; startup check refuses both-off.
- `test_adapter.py` — all six public methods with fake client; documented fallback on each failure mode.

Coverage target: 90% on `app/mempalace/`.

**Layer 2 — Sidecar unit** (`mempalace-sidecar/tests/unit/`)

- `test_auth.py`, `test_registry.py` (Hypothesis property test on byte accounting), `test_migrations.py` (crash-mid-migration via `pytest-forked`), `test_kg_queue.py`, `test_shutdown.py`, `test_wakeup_concurrency.py` (asserts single `gather` + shared embedding).

**Layer 3 — Integration** (`@pytest.mark.integration`, ≤5min)

~95% via `httpx.AsyncClient(transport=ASGITransport(app))` in-process with real Chroma + real SQLite on `tmp_path`. One dedicated test (`test_socket_e2e.py`) spawns the actual sidecar subprocess over the actual UDS to cover socket permissions, signal handling, and the UDS peer-check.

Scenarios: ingest→search round-trip (with cross-user isolation assertion), pair_id integrity, wake-up layer composition with seeded palace, hot vs cold latency separation, idempotency replay, migration end-to-end, delete, rebuild.

**Layer 4 — Contract** — FastAPI-generated OpenAPI as single source. `schemathesis` (Flask side) + snapshot comparison (sidecar CI). Schema change without `schema_version` bump = PR failure.

**Layer 5 — End-to-end** (nightly, ≤15min)

Full Flask + live sidecar + stubbed LLM. Scenarios:
- Cold-user first turn: no injection, background write succeeds, next turn injects.
- **`test_flusher_crash_idempotency_persistence`**: send 10 turns, SIGKILL sidecar between ACK of row 8 and ACK of row 9, restart, Flask retries, assert palace contains exactly 10 distinct `turn_id`s. Disk-backed `ingest_log` with `UNIQUE(turn_id)` is the invariant; in-memory idempotency fails this test.
- Disk-full on sidecar: 507s, Flask continues, alerts fire.
- Shadow parity: 1000 synthetic turns, request latency delta with shadow on vs off ≤ 2ms.
- GDPR delete: admin panel → sidecar DELETE → audit row → `list_drawers` returns empty.
- Graceful-degradation smoke: `MEMPALACE_MODE=active` + dead sidecar returns same response shape as `LEGACY_MEMORY=on` baseline.

**Chaos / failure injection (weekly)** — no toxiproxy, no socat. `kill -STOP`/`CONT` for latency bursts, `SIGKILL` + `systemctl restart` for crash recovery, `fallocate` for disk-full. Client-side behavior via in-process `ChaosTransport`.

**Performance (pre-active-flip + monthly)**

- Wake-up: 1000 concurrent users, 50 hot palaces. Hot p50 ≤100ms, p95 ≤150ms, p99 ≤500ms, cold p95 ≤2s. **p99.9 and max measured and documented** — circuit breaker threshold sized from measured tail, not SLA target.
- Ingest throughput: 10k turns/min sustained, outbox ≤1k rows steady state.
- Flusher-during-bounce: sidecar bounced every 30s for 1h, outbox depth ≤ 2× normal, catches up within 5 min of last bounce.
- Memory stability under eviction for 30 min.

**Property-based invariant** — single Hypothesis test on the outbox: for any interleaving of `insert`, `flush_batch`, `sidecar_5xx`, `sidecar_timeout`, `flask_crash_restart`, final palace state = input sequence ordered by insertion time per user, zero duplicates, zero drops.

**Security** — socket perms (`mode & 0o007 == 0`), redaction corpus (500 strings, 0 leaks), user_id path traversal rejected, `rebuild from_path` confined to `DATA_DIR`.

**What we deliberately do not test** — ChromaDB internals, mempalace library internals, gunicorn/FastAPI machinery, embedding model semantic quality (covered by shadow rubric).

**Shadow rubric harness** — terminal-only `click` + `input()` script. Reads `/var/log/mempalace/shadow/*.jsonl.gz`, prompts for 20 random turns/week, writes 2×2 tally to `docs/mempalace-shadow/YYYY-WW.md`. Rater: Xela. Web UI deferred until >1 rater.

CI wall-clock budgets enforced via `pytest-timeout`: unit ≤60s, integration ≤5min, E2E ≤15min.

---

## 7. Rollout, deployment, and dormancy

### 7.1 Artifacts and FHS layout

```
/opt/mempalace-sidecar/venv/           # versioned; previous two kept for rollback
/etc/mempalace/sidecar.env             # mode 0640, root:mempalace
/etc/xelasphere/mempalace.env          # dedicated file for mempalace flags (one-file-one-purpose)
/var/mempalace/palaces/<uid>/
/var/mempalace/kg_pending.sqlite
/var/mempalace/audit.sqlite
/var/mempalace/shadow_logs/            # 20 GB cap via logrotate
/run/mempalace/sidecar.sock            # 0660, mempalace:mempalace
/run/mempalace/prom/                   # PROMETHEUS_MULTIPROC_DIR, wiped on each start
/var/log/mempalace/                    # journald primary; 14-day file sink, copytruncate
```

### 7.2 systemd unit — design-relevant invariants

```ini
[Unit]
Description=MemPalace sidecar for XelaSphere
After=network-online.target
Wants=network-online.target

[Service]
Type=notify                    # sidecar MUST sd_notify(READY=1) after embedding model loads
User=mempalace
Group=mempalace
EnvironmentFile=/etc/mempalace/sidecar.env
ExecStartPre=/bin/mkdir -p /run/mempalace/prom
ExecStartPre=/bin/rm -rf /run/mempalace/prom/*
ExecStart=/opt/mempalace-sidecar/venv/bin/mempalace-sidecar
KillSignal=SIGTERM
TimeoutStopSec=15s              # 10s drain budget from §4 + 5s grace
MemoryHigh=2G                   # matches MEMPALACE_CACHE_MAX_BYTES
MemoryMax=3G                    # cache cap + 1G working set headroom
Restart=on-failure
RestartSec=2s
```

Hardening flags (`PrivateTmp`, `ProtectSystem=strict`, `NoNewPrivileges`, `ReadWritePaths`) are owned by the ops runbook, not the design spec.

### 7.3 Provisioning checklist

1. `useradd -r -s /usr/sbin/nologin mempalace`
2. `usermod -aG mempalace xelasphere`
3. Create dirs with the ownership/mode from §7.1.
4. Generate `MEMPALACE_TOKEN`; write to both `sidecar.env` and `xelasphere`'s env file as one step.
5. Install wheel to `/opt/mempalace-sidecar/venv`.
6. `systemctl daemon-reload && systemctl enable --now mempalace-sidecar`.
7. Wait for `/readyz` OK.
8. **Non-optional**: `systemctl restart xelasphere` (gunicorn picks up new group membership only on restart). Post-check: `grep -c '^Groups:.*mempalace' /proc/$(pgrep -f 'gunicorn: master')/status == 1`.

### 7.4 Staged rollout — five named phases

| Phase | `MEMPALACE_MODE` | `LEGACY_MEMORY` | Duration | Entry check | Exit criterion | Approver |
|---|---|---|---|---|---|---|
| **P0 Dark deploy** | `off` | `on` | 3 days | Sidecar running, `/readyz` OK, zero app-side errors | No mempalace-attributed errors in 3 days | Unilateral |
| **P1 Shadow writes** | `shadow` | `on` | 14 days | P0 clean | Outbox steady ≤1k rows p95; ingest success ≥99.5%; shadow log populated; disk growth within forecast; single-user rebuild benchmark measured | Unilateral |
| **P2 Shadow reads** | `shadow` | `on` | 21 days | P1 clean | Rubric (2×2) `mempalace_only_hit ≥ v3_only_hit` for 3 consecutive weeks AND `(both + mempalace_only) ≥ 85%` | Unilateral with rubric math attached |
| **P3 Active, legacy standby** | `active` | `on` | 60 days | P2 flip criterion met + written approval in `docs/mempalace-shadow/phase-log.md` | Zero rollbacks; hot p95 ≤150ms sustained; **day-30 and day-55 rubric re-run confirms mempalace_only ≥ v3_only at both checkpoints**; two monthly capacity reviews | Written approval, committed to phase log |
| **P4 Legacy removed** | `active` | `off` (deleted) | terminal | P3 clean | N/A | Written approval, committed |

**Rollback** — `/usr/local/bin/mempalace-mode {off|shadow|active}` is a named script that atomically rewrites `/etc/xelasphere/mempalace.env`, runs `systemctl restart xelasphere` (not reload — gunicorn's HUP/USR2 does not re-read systemd EnvironmentFile), and verifies via probe. **No inline sed, ever.** Target rollback wall time < 60s.

### 7.5 Dormancy → deletion

1. Every existing call to `memory_v2.*` / `memory_v3.*` wrapped by `feature_flag.legacy_memory_active()`.
2. `DEPRECATED.md` in each module at P3 entry: target date, rollback, grep command.
3. CI guard: non-blocking warn on new `memory_v2`/`memory_v3` imports through P2; **blocking** at P3 entry.
4. At P4: one PR deletes both directories, removes `LEGACY_MEMORY` flag, removes call-site guards.

### 7.6 Release mechanics

1. Git tag on sidecar subdir.
2. CI builds wheel, runs full pyramid + OpenAPI snapshot.
3. Ansible: rsync wheel, `pip install` into fresh venv dir, atomic `ln -sfn new old`, `systemctl restart mempalace-sidecar`.
4. Circuit breaker absorbs the 2–5s blip.
5. Rollback: `ln -sfn /opt/mempalace-sidecar/venv-<prev-tag> /opt/mempalace-sidecar/venv && systemctl restart mempalace-sidecar`. Previous two wheels always on disk.
6. Pre-upgrade: `mempalace-sidecar --check-migration <wheel>` dry-runs any schema change.

### 7.7 Repo layout — subdir, with explicit split trigger

Stay a subdir inside xelasphere.io- **until any of**:
- A second service imports the sidecar client.
- Sidecar release cadence exceeds XelaSphere's by >2× for a full quarter.
- A contributor needs write access to sidecar but not XelaSphere.

Split is then `git subtree split`, no history loss.

### 7.8 Data protection & recovery

DR hierarchy:
1. **Primary: restic restore** from `/var/mempalace/palaces/` daily snapshot to object storage (30-day retention). Fast, includes KG (kg.sqlite is inside each palace dir, so it's included). RTO 1 hour.
2. **Secondary: `mempalace rebuild --user <uid> --from-conversations`** — slow, requires KG re-extraction LLM spend. Used when restic unavailable or for targeted single-user recovery.
3. **Backup excludes**: `shadow_logs/`, `kg_pending.sqlite`, `/run/mempalace/prom/`.
4. **Cross-region / multi-host backup is out of scope for v1.**
5. Backup sizing threshold: palaces dir > 50 GB → switch from daily-full to weekly-full + daily-incremental.
6. P1 exit requires one representative user's full rebuild measured end-to-end (wall time, LLM cost, Chroma throughput, peak RSS) and extrapolated to fleet size in the runbook.

---

## 8. Observability and ops

### 8.1 Metrics catalog

**Sidecar** (`/metrics`, multiprocess-safe):

| Metric | Type | Labels | Purpose |
|---|---|---|---|
| `mempalace_ingest_total` | counter | `status={accepted,duplicate,error}` | Write throughput, idempotency hit rate |
| `mempalace_wakeup_seconds` | histogram | `layer={l0,l1,l2,l3,total}`, `hot={0,1}` | Hot vs cold budget tracking |
| `mempalace_search_seconds` | histogram | `hot={0,1}` | Tool-call search latency |
| `mempalace_embedding_seconds` | histogram | — | Embedding cost |
| `mempalace_palace_cache_size` | gauge | — | Number of hot palaces |
| `mempalace_palace_cache_bytes` | gauge | — | Resident bytes vs cap |
| `mempalace_palace_cache_hits_total` | counter | — | Hot-hit (ratio computed at query time, not exported as gauge) |
| `mempalace_palace_cache_misses_total` | counter | — | Cold opens |
| `mempalace_palace_cache_evictions_total` | counter | `reason={size,idle}` | Eviction pressure |
| `mempalace_kg_queue_depth` | gauge | `status={pending,running,failed}` | KG backlog |
| `mempalace_kg_daily_usd_spent` | gauge | — | Cost vs cap |
| `mempalace_kg_extraction_total` | counter | `status={success,failure,skipped_cap}` | Worker outcomes |
| `mempalace_schema_migration_total` | counter | `from`, `to`, `status` | Migration surface |
| `mempalace_open_palace_errors_total` | counter | `reason={corrupt,schema_too_new,perm}` | Open failure modes |
| `mempalace_delete_total` | counter | — | GDPR deletes (cross-ref `audit.sqlite`) |
| `mempalace_readyz` | gauge | — | 1 if all subsystems good |

**Flask side**:

| Metric | Type | Labels | Purpose |
|---|---|---|---|
| `xelasphere_mempalace_outbox_depth` | gauge | `status={pending,sent}` | Backpressure |
| `xelasphere_mempalace_outbox_age_seconds` | histogram | — | Staleness |
| `xelasphere_mempalace_outbox_rejected_total` | counter | — | 50k cap hits |
| `xelasphere_mempalace_inserts_dropped_total` | counter | `reason={mode_off,outbox_db_down}` | Silent drops |
| `xelasphere_mempalace_flush_batch_seconds` | histogram | — | Flusher performance |
| `xelasphere_mempalace_flusher_last_tick_seconds` | gauge | — | Flusher liveness (deadlock detection) |
| `xelasphere_mempalace_client_request_seconds` | histogram | `route`, `status_code` | Round-trip latency |
| `xelasphere_mempalace_circuit_state` | gauge | `worker_pid` | 0=closed, 1=half-open, 2=open |
| `xelasphere_mempalace_circuit_transitions_total` | counter | `from`, `to`, `worker_pid` | Flap detection |
| `xelasphere_mempalace_redaction_hits_total` | counter | `rule` | Scrub counts (never the text) |
| `xelasphere_mempalace_wakeup_skipped_total` | counter | `reason={timeout,circuit_open,shadow_mode,error}` | Injection-skipped reason |
| `xelasphere_mempalace_mode` | gauge | — | Dashboard indicator (no alert) |

### 8.2 Alerts

All alerts link to a runbook section.

| Alert | Condition | Sev | Runbook |
|---|---|---|---|
| `MempalaceSidecarDown` | `up{job="mempalace-sidecar"} == 0` for 5m | page | R1 |
| `MempalaceReadyzFailing` | `mempalace_readyz == 0` for 5m | page | R2 |
| `MempalaceWakeupHotP95High` | `histogram_quantile(0.95, mempalace_wakeup_seconds_bucket{hot="1",layer="total"}) > 0.3` for 10m | page | R3 |
| `MempalaceOutboxBacklogWarn` | `xelasphere_mempalace_outbox_depth{status="pending"} > 10000` for 5m | warn | R4 |
| `MempalaceOutboxBacklogPage` | `xelasphere_mempalace_outbox_depth{status="pending"} > 30000` for 5m | page | R4 |
| `MempalaceIngestFailureRate` | `rate(ingest_total{status="error"}[10m]) / rate(ingest_total[10m]) > 0.01 AND rate(ingest_total[10m]) > 0.1` | page | R5 |
| `MempalaceCacheThrashing` | `rate(evictions[10m]) > rate(ingest[10m]) AND rate(evictions[10m]) > 1` | warn | R6 |
| `MempalaceKGCostApproachingCap` | `kg_daily_usd_spent / CAP > 0.8` | warn | R7 |
| `MempalaceKGCostProjectionBust` | `predict_linear(kg_daily_usd_spent[1h], 14400) > CAP` | warn | R7 |
| `MempalaceKGQueueStuck` | `kg_queue_depth{pending} > 1000 AND rate(kg_extraction_total{success}[30m]) == 0` | page | R7 |
| `MempalaceKGQueueSlowlyDying` | `kg_queue_depth{pending} > 5000 for 30m` | warn | R7 |
| `MempalaceCircuitOpen` | `circuit_state == 2` on any worker for 5m | warn | R8 |
| `MempalaceCircuitFlapping` | `increase(circuit_transitions_total[30m]) > 20` | warn | R8 |
| `MempalaceMigrationFailed` | `increase(schema_migration_total{status="fail"}[1h]) > 0` (auto-suppressed if wheel rollback within 30m) | page | R9 |
| `MempalacePalaceCorrupt` | `increase(open_palace_errors_total{reason="corrupt"}[15m]) > 0` | page | R10 |
| `MempalacePalacePermError` | `increase(open_palace_errors_total{reason="perm"}[15m]) > 0` | warn | R2 |
| `MempalaceDiskLow` | `node_filesystem_avail_bytes{mountpoint=~"/var/mempalace.*"} / node_filesystem_size_bytes < 0.15` | warn | R11 |
| `MempalaceFlusherStalled` | `time() - xelasphere_mempalace_flusher_last_tick_seconds > 60` | page | R4 |
| `MempalaceRedactionBroken` | `rate(redaction_hits_total[24h]) == 0 for 24h AND rate(ingest_total[24h]) > 0` | warn | R15 |

### 8.3 Runbook

Lives at `docs/ops/mempalace-runbook.md`. R1/R4/R10/R11 are inline-detailed at spec authoring; R2/R3/R5/R6/R9/R11/R14/R15 are inline. R7/R8/R13 are **STUB — expanded in P0**, tracked in issue that gets filed at P0 entry.

Key inline-detailed entries:

- **R1 SidecarDown** — `systemctl status` + `journalctl`. Crash loop → look for corrupt-palace signature; isolate offending palace, restart, file R10. While down, Flask should be fine; verify `wakeup_skipped_total` grows but error rate flat.
- **R4 Outbox backlog** — `mempalace-mode shadow` to reduce sidecar read pressure, inspect flusher logs. **Never `DELETE FROM outbox`** — conversations are system of record; rebuild is the recovery. Flusher stalled → check for DB lock; direct thread dump via `py-spy`.
- **R10 Palace corruption** — (1) `mv palaces/<uid> palaces/<uid>.corrupt.<ts>`; (2) `mempalace rebuild --user <uid> --from-conversations`; (3) retain `.corrupt` dir **30 days** (matches restic retention) for upstream bug reports; (4) audit log ensures timeline is clear. **Never mutate the original `U/` during migration until verification passes.**
- **R11 DiskLow** — shadow logs first, then palaces. `du -sh /var/mempalace/palaces/* | sort -h | tail` to find offenders. One user >5GB usually means a bad attachment ingest; check `redaction_hits_total` for that period.
- **R15 Redaction broken** — compare current rule file to last-known-good in git; check if a test run exploded; redeploy.

### 8.4 Logging

- Structured JSON on both sides. `journald` primary, file sinks in `/var/log/mempalace/` for 14 days (logrotate with `copytruncate` — survives wheel rollback file-pointer weirdness).
- Fixed schema: `ts, service, level, event, user_id, turn_id, route, latency_ms, err, request_id`.
- `user_id` is **always** `sha256(user_id + salt)[:12]`. Salt in `sidecar.env`. **Logs are pseudonymous, not anonymous** — documented in `docs/ops/logging-privacy.md`.
- Never log drawer text, ingest bodies, or wake-up results. Redaction logs record rule name + count only.
- `request_id` generated per incoming chat turn; propagates through outbox (as a column), through the flusher, to the sidecar via `X-Request-ID`. End-to-end correlation survives async.

### 8.5 Dashboards

Committed to `docs/ops/dashboards/`:
- `mempalace-live.json` — realtime health + capacity row (collapsed): ingest rate, wake-up latency hot/cold, outbox depth, circuit state, cache pressure, readyz, top palaces by bytes, outbox age, open-error class trend.
- `mempalace-rollout.json` — phase indicators: mode gauge, shadow log volume, 2×2 rubric tally, KG daily spend vs cap, disk growth.

### 8.6 Maintenance procedures

- **Wheel upgrade**: pre-dry-run migration, deploy to new venv dir, flip symlink, restart, watch `/readyz` + wake-up p95 for 15 min.
- **Palace rebuild (single user)**: `--dry-run` first, verify drawer count, then real run, spot-check via `/palace/<uid>/drawers`.
- **Full-fleet rebuild (disaster)**: primary path is restic restore, not rebuild. Rebuild fallback is batched, off-peak, rate-limited to KG cost budget.
- **GDPR reconciliation**: nightly cron — `mempalace_delete_total` vs. `audit.sqlite` row count. Drift → warn.
- **Monthly review (P3)**: rubric re-run (days 30 and 55), capacity dashboard sweep, runbook drift check.
- **On-call (v1)**: single rotation (Xela). Pages only on `page` severity. Warns reviewed next business day. `mempalace-mode off` is the universal relief valve, safe through P3 while `LEGACY_MEMORY=on`.

### 8.7 What deliberately does not ship in v1 observability

- Distributed tracing.
- Per-user quality alerting.
- Anomaly detection on palace growth.
- Secondary on-call.
- SLO / error-budget accounting.
- Cross-region / multi-host backup.

---

## 9. Glossary

- **Palace** — a per-user directory containing a ChromaDB collection, a SQLite knowledge graph, and metadata. `/var/mempalace/palaces/<uid>/`.
- **Drawer** — a verbatim chunk of text (e.g. one message) stored in the palace.
- **Wing / Room** — palace organizational hierarchy; wings are broad categories (people, projects), rooms are time- or topic-based groupings.
- **AAAK** — MemPalace's compact symbolic index format; a shorthand an LLM can scan to find which drawer to open.
- **Wake-up** — the L0–L3 layered context assembly returned before each chat turn.
- **Sidecar** — the FastAPI process that owns all palaces on the host. One per VPS in v1.
- **Shadow mode** — writes happen, reads happen, results logged but not injected into the prompt.
- **System of record** — the app's primary conversation DB. Palace is derived; never the source of truth.

---

## 10. Open questions deferred to implementation

Things that are known-unknowns today and will be resolved during P0/P1 rather than re-opened in brainstorming:

- Exact embedding model pin (`all-MiniLM-L6-v2` is the default in the env; final choice validated by P1 latency measurement).
- KG LLM provider default (Groq vs Ollama vs Anthropic) — pick based on P1 cost measurement.
- Whether cold-palace TTL should rise above 4h (decision driven by `hot=0` label fraction during P2).
- Exact `MEMPALACE_KG_DAILY_USD_CAP` value (measured at P1).
- Flask's circuit-breaker consecutive-failure threshold (sized from measured p99.9 + max during perf tests).

These are explicitly deferred. The design does not depend on any specific value.
