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
                     │     FastAPI wrapping PalaceHandle      │
                     │     (library isolation; see §5.3)      │
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

**Per-user isolation:** the sidecar dispatches by `user_id` in the URL. Each user gets a `PalaceHandle` instance (§5.3) backed by their own directory. PalaceHandles are cached in a size-weighted LRU (§5.4), capped at `MEMPALACE_CACHE_MAX_BYTES` (default 2 GB resident). Cold palaces close their ChromaDB handle after `MEMPALACE_IDLE_TTL_SEC` (default 14400 = 4h). Cold-wake-up rate is monitored; if it exceeds 5% of wake-ups during P2, TTL is raised to 8h.

The underlying `mempalace.backends.chroma.ChromaBackend` singleton keeps its own `{palace_path → PersistentClient}` cache with no eviction API. Any PalaceHandle eviction MUST also pop the corresponding entry from that backend dict; the cache-eviction contract is stated with teeth in §5.3 and verified by test. Without that discipline the sidecar leaks one ChromaDB client per palace ever opened, and a memory-capped systemd unit OOMs after the Nth cold user opens.

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
    def wake_up(user_id, query, session_id=None) -> WakeupContext | None
    def search(user_id, query, k=5) -> list[Drawer]
    def record_turn(user_id, session_id, turn_id, pair_id, role, text,
                    attachments=None, request_id=None)
    def delete_palace(user_id, requested_by, reason) -> DeletionReceipt
    def list_drawers(user_id, wing=None, limit=100, offset=0)
    def health() -> dict
```

`WakeupContext` is a structured object (`l0_recent`, `l1_session`, `l2_topic`, `l3_semantic`, `meta`). Provider-specific formatters in `app/mempalace/wakeup.py` turn it into a system prompt block for each model family (`to_anthropic_system`, `to_openai_system_msg`, `to_gemini_contents`, `to_local_prompt_block`). Orchestrator's per-model adapter picks the right formatter — formatting does not live inside the palace adapter.

**Orchestrator integration points (three touch sites):**

1. **Pre-model hook** — `wake_up()` before the LLM call. Result injected as a system block (active mode) or enqueued to the shadow sink (shadow mode). Shadow-mode enqueue is non-blocking via `queue.Queue.put_nowait()` (stdlib thread-safe queue); a dedicated daemon writer thread drains to disk. Pre-model hooks run in sync gunicorn workers, so the sink is thread-based, not asyncio-based.
2. **Tool registry** — register `search_memory(query, wing?, k=5)` tool. On failure, returns `{"results": [], "note": "no results"}` — indistinguishable from a genuine empty search. Never the string "memory unavailable," which causes some models to apologize to the user. **The tool stays registered in every mode, including `off`** — in `off` mode it returns the same empty-no-results payload. Never mutate the advertised tool list based on feature-flag state; some providers break on mid-conversation tool-list changes.
3. **Post-turn hook** — `record_turn()` for both user and assistant messages, sharing a `pair_id`. **`pair_id` is generated by the orchestrator** (not the adapter) so the same id is known at both call sites of the same turn.

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

**Cutover safety (pre-P4):** startup check in `app/__init__.py` — if `MEMPALACE_MODE=off` AND `LEGACY_MEMORY=off`, Gunicorn refuses to boot. A SIGHUP reload with both-off also refuses, keeping the old config serving. Prevents "no memory at all" incidents.

**Post-P4 (after `LEGACY_MEMORY` is deleted):** `off` must be a bootable state because it is the outage-mode relief valve — an unrelated gunicorn restart during a sidecar incident must not refuse to boot. Post-P4 behaviour: `MEMPALACE_MODE=off` boots with a loud startup `WARN` logged and the `xelasphere_mempalace_mode` gauge set correctly. The P4 PR that removes `LEGACY_MEMORY` must also remove the both-off boot refusal — do not ship P4 without this change, or the first post-P4 incident will turn a single failure into a whole-app outage.

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
                          Idempotency-Key: <turn_id>      (advisory; see below)
                          X-Sidecar-Token: ...
                          X-Request-ID: ...
                          body: {turn_id, session_id, pair_id, role, text, ts, attachments}
                                                                                    ─▶ acquire per-user write lock
                                                                                       handle.add_drawer(turn_id=..., …)
                                                                                         ├─ SELECT drawer_id FROM turns
                                                                                         │    WHERE turn_id=? (§5.3 turns.sqlite)
                                                                                         ├─ if present → status="duplicate"
                                                                                         ├─ miner.add_drawer(collection, …,
                                                                                         │    source_file=f"turn:{turn_id}")
                                                                                         ├─ INSERT OR IGNORE INTO turns (...)
                                                                                         └─ return status="accepted"
                                                                                       enqueue KG extraction
                                                                                       200 {status: accepted|duplicate}
                          UPDATE outbox SET status='sent' WHERE id=?
```

- **Idempotency authority — `turns.sqlite`, period.** `turn_id` appears in two places on the wire: the `Idempotency-Key` HTTP header and the request body. **`turns.sqlite` (spec §5.3) is the single authoritative deduplication store.** The header is advisory — the sidecar logs it for request correlation (X-Request-ID cross-reference) and MAY assert header-vs-body equality on parse, but dedup decisions are always made by `PRIMARY KEY(turn_id)` on the handle-owned `turns.sqlite` table. A single source of truth forces the library's internal drawer-ID derivation (currently `sha256(source_file + chunk_index)[:24]`) to be a downstream implementation detail rather than a parallel idempotency mechanism that could silently disagree with ours after a library upgrade.
- **At-least-once delivery + `turns.sqlite` PK → effectively exactly-once drawer.** The handle's `turns.sqlite` is disk-backed with `PRIMARY KEY(turn_id)`. In-memory idempotency would fail the named crash-recovery test in §6.
- **Ordering guarantee**: single flusher thread in v1. If sharded later, shard key is `hash(user_id)`. Never round-robin.
- **Retries**: exponential backoff 1s, 4s, 15s, 60s, 300s. Row stays `pending` between attempts.
- **Sent-row retention**: 7 days. Forensics only — the app DB is the system of record, not the outbox.
- **Backpressure**: outbox depth > 50k → `record_turn` returns without inserting, `xelasphere_mempalace_outbox_rejected_total` increments. No user-visible error.
- **Redaction**: runs before the outbox insert. Regex corpus includes OpenAI/Anthropic/Google/Slack/AWS API-key prefixes, Luhn-valid credit cards, US SSN, JWT. Rules in `redaction_rules.yaml`, feedback loop in `redaction/README.md`.
- **Graceful shutdown**: SIGTERM drains flusher for up to 10s; in-flight batch rows revert cleanly to `pending` so the next restart picks up.

### 4.2 Read path A — pre-model wake-up (every turn)

Query construction: `{latest_user_message}\n{preceding_assistant_message}`, passed as a `str` to the sidecar. Embedding is handled inside ChromaDB's default embedding function at `col.query` time (L3 only; L0/L1/L2 don't embed). See §5.3 "Concurrency model inside PalaceHandle" for how the four layer calls are composed.

```
adapter.wake_up(uid, query)
  Flask-side timeout = 2s always (upper bound; Flask cannot know hot-vs-cold
  before the call). Hot/cold is a sidecar-side metric label, not a Flask branch.
  1 retry; circuit-breaker open → None
       │
       ▼
  sidecar: asyncio.gather(
       asyncio.to_thread(L0),  -- metadata scan, sorted by filed_at
       asyncio.to_thread(L1),  -- turns.sqlite SELECT + col.get(ids=...)
       asyncio.to_thread(L2),  -- searcher.search_memories
       asyncio.to_thread(L3),  -- searcher.search_memories
  )  -- real thread-pool parallelism (measurement-gated in M0.5.3)
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
│   ├── palace_handle.py     # PalaceHandle: library isolation boundary (see §5.3)
│   ├── registry.py          # PalaceRegistry: size-weighted LRU + per-user aiorwlock
│   ├── wakeup.py            # L0/L1/L2/L3 composition (see §5.3 for the layer calls)
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
| POST | `/palace/{uid}/ingest` | `{turn_id, session_id, pair_id, role, text, ts, attachments?}` | `{status: accepted\|duplicate}` | `Idempotency-Key` header ADVISORY (correlation only); authoritative dedup is `turns.sqlite` PK (§4.1, §5.3). Write lock. |
| POST | `/palace/{uid}/wake-up?warmup=0\|1` | `{query, k?, layers?}` | `WakeupContext` | Read lock. 500ms hot / 2s cold. |
| POST | `/palace/{uid}/search` | `{query, wing?, k=5}` | `[Drawer]` | Read lock. Tool calls. |
| GET  | `/palace/{uid}/drawers?wing=&limit=&offset=` | — | `{drawers, total, next_offset}` | Admin + GDPR access. Offset-based (ChromaDB has no cursor). |
| POST | `/palace/{uid}/rebuild` | `{conversations: [...]}` or `{from_path}` | `{ingested, skipped, job_id}` | Long-running, streaming. |
| DELETE | `/palace/{uid}` | `{requested_by, reason}` | `{status, bytes_removed}` | Atomic dir rename → rm → audit row. |
| POST | `/palace/{uid}/kg/query` | `{subject?, predicate?, object?, at?}` | `[Triple]` | KG reads. |
| GET | `/healthz` | — | `{ok, version, uptime_s}` | Liveness. |
| GET | `/readyz` | — | `{ok, embedding_loaded, db_ok, audit_ok}` | Readiness — fails if model not loaded. |
| GET | `/metrics` | — | Prometheus text | Multi-process safe. |

OpenAPI spec is auto-generated by FastAPI from Pydantic models; CI snapshots it and fails any PR that changes the spec without bumping `schema_version` in `/healthz`. No hand-maintained pact file.

### 5.3 PalaceHandle — library isolation boundary

**Purpose.** PalaceHandle is the sidecar-owned OOP wrapper around the `mempalace` 3.3.1 functional API. Every route (`ingest`, `wake-up`, `search`, `drawers`, `kg/*`, `rebuild`, `DELETE`) goes through a PalaceHandle. The library is referenced from exactly one file (`palace_handle.py`) and nowhere else. Swapping to a future version, a fork, or a rewrite is an edit contained to this boundary.

**Why not bridge to `mempalace.mcp_server`.** The library ships 29 MCP tool handlers (`TOOLS` dict, `handle_request` dispatcher) that cover every drawer/KG/tunnel operation. Not used. Every handler calls `_get_collection(create=False)` which reads a module-level `_config: MempalaceConfig` and a module-level `_collection_cache` (see `mcp_server.py:214` in `mempalace==3.3.1`). Every `tool_*` operates on a single global palace bound at process start. Invoking two handlers for two users from the same process targets whichever palace was configured last — a race, not a bug. In a multi-tenant sidecar the first such race is a cross-user data leak, and in shadow-mode logs it turns a P1 rollout phase into a security incident. We bridge to the path-threaded primitives (`searcher.search_memories(query, palace_path, ...)`, `miner.add_drawer(collection, ...)`, `mempalace.palace.get_collection(palace_path, ...)`, `knowledge_graph.KnowledgeGraph(db_path=...)`) instead. Note: `mempalace.layers.Layer0/1/2/3` also accept `palace_path`, but we don't use them — see the "Naming collision, semantically distinct" paragraph directly below for why our L0-L3 semantics differ from the library's. Upstream issue to file: [upstream issue not yet filed — target: before P0 entry]. Until that issue lands, `mcp_server.*` is strictly forbidden in the sidecar; a unit test greps the package and fails if an import appears.

**Naming collision, semantically distinct.** The library also has `mempalace.layers.MemoryStack.wake_up()`, `Layer0/Layer1/Layer2/Layer3`, and the letters "L0-L3" — but its semantics are not ours.

- Library's `wake_up()` returns a pre-composed string of L0 (identity text from `~/.mempalace/identity.txt`) + L1 (top-15-drawers-by-importance summary). Synchronous. Covers only L0+L1. Shape: `str`.
- Library's `Layer0.render()` is identity text, `Layer1.generate()` is importance-sorted drawers, `Layer2.retrieve()` is wing/room metadata filter, `Layer3.search()` is vector search.
- Our wake-up is a four-layer dict composition covering recent-drawers / session-drawers / topic-filtered / semantic — a different product built on top of the library's retrieval primitives. We do not call `MemoryStack.wake_up()` and we do not consume `Layer0.render()` as our L0. We reimplement each of our layers on top of the library's retrieval primitives because our L0 is "recent drawers," not "identity.txt."

**State held per handle.**

```
PalaceHandle(palace_path):
  palace_path: str                              # /var/mempalace/palaces/<user_id>/
  _collection: ChromaCollection                 # cached via mempalace.palace.get_collection(palace_path);
                                                # reused across all calls on this handle
  kg: KnowledgeGraph                            # db_path = f"{palace_path}/kg.sqlite";
                                                # library class, has .add_triple/.query_entity/.timeline/.close
  config: MempalaceConfig                       # config_dir = f"{palace_path}/config/";
                                                # per-user people_map/topic_wings/entity_languages,
                                                # not the library's ~/.mempalace/ default
  turns: sqlite3.Connection                     # db_path = f"{palace_path}/turns.sqlite";
                                                # OWNED BY THE SIDECAR — idempotency + session lookup
  meta: dict                                    # parsed meta.json (schema_version, created_at)
  size_bytes: int                               # refreshed on ingest + on eviction sweep
```

**`turns.sqlite`** is a sidecar-owned table that `mempalace` does not know about. Purpose:

```sql
CREATE TABLE turns (
  turn_id TEXT PRIMARY KEY,                     -- idempotency key from ingest
  drawer_id TEXT NOT NULL,                      -- derived deterministically; stored for reverse lookup
  session_id TEXT NOT NULL,
  pair_id TEXT NOT NULL,
  role TEXT NOT NULL CHECK(role IN ('user','assistant')),
  ts REAL NOT NULL,
  filed_at REAL NOT NULL
);
CREATE INDEX turns_session_ts ON turns(session_id, ts);
CREATE INDEX turns_pair ON turns(pair_id);
```

Two things we can't get from the library alone and need for M0.4/M0.5:
- **Idempotency:** `PRIMARY KEY(turn_id)` rejects duplicate ingests of the same `turn_id`. Drawer metadata in Chroma can't carry `turn_id` because `miner.add_drawer` generates its own metadata and discards unknown fields.
- **Session retrieval:** `handle.session_drawers(session_id, limit)` does `SELECT drawer_id FROM turns WHERE session_id=? ORDER BY ts DESC LIMIT ?`, then `self._collection.get(ids=drawer_ids)`. mempalace has no concept of "session" — that's a XelaSphere concept.

**Method surface (v1 — M0.3 through M0.8 only).** PalaceHandle v1 is defined by the methods our M0.3-M0.8 call sites need, plus one escape hatch for direct Chroma access. Nothing beyond.

| Method | Bridges to | Notes |
|---|---|---|
| `PalaceHandle.open(palace_path) -> PalaceHandle` | opens: `mempalace.palace.get_collection(palace_path, create=True)`, `KnowledgeGraph(db_path=…/kg.sqlite)`, `MempalaceConfig(config_dir=…/config/)`, sqlite3 connect to `turns.sqlite`; reads or writes `meta.json` | classmethod factory. Creates palace dir + all child state if missing. Cheap (~1-5 ms) assuming Chroma client already cached; ~50-200 ms first-ever open for a palace. |
| `handle.add_drawer(*, turn_id, session_id, pair_id, role, text, ts, wing="conversations", room="session", agent="xelasphere") -> AddDrawerResult` | `miner.add_drawer(self._collection, wing, room, text, source_file=f"turn:{turn_id}", chunk_index=0, agent)` + `INSERT OR IGNORE INTO turns ...` | Drawer ID derived from `sha256(f"turn:{turn_id}" + "0")[:24]` — idempotent per `turn_id`. If `turn_id` already in `turns.sqlite`, returns `AddDrawerResult(status="duplicate", drawer_id=existing)` without calling the library. |
| `handle.recent_drawers(limit=10) -> list[DrawerDict]` | `self._collection.get(limit=limit*4, include=["documents","metadatas"])` + Python-side sort by `filed_at` desc, truncate to `limit` | Our L0. We over-fetch 4× then sort because Chroma has no `order_by`. For palaces >10k drawers this gets expensive; M0.5 integration test measures p95 and flags if over budget. |
| `handle.session_drawers(session_id, limit=10) -> list[DrawerDict]` | `SELECT drawer_id FROM turns WHERE session_id=? ORDER BY ts DESC LIMIT ?` → `self._collection.get(ids=drawer_ids, include=["documents","metadatas"])` | Our L1. Cheap — sqlite index lookup + single Chroma `get` by IDs. **Reshuffle library return to match sqlite order** — Chroma's `col.get(ids=...)` does not preserve input-ID order; it returns rows in storage order. Without a reshuffle step the session turns come back out-of-time. |
| `handle.topic_search(query, k=5) -> list[DrawerDict]` | `searcher.search_memories(query, self.palace_path, n_results=k)["results"]` | Our L2. v1 behaves identically to `semantic_search`; topic-filter logic deferred to a future milestone (see Open Question §10.x added below). |
| `handle.semantic_search(query, k=5) -> list[DrawerDict]` | `searcher.search_memories(query, self.palace_path, n_results=k)["results"]` | Our L3. The library's `search_memories` is a hybrid BM25 + vector + closet-boost rerank — strictly better than anything we'd write. |
| `handle.search(query, *, wing=None, k=5) -> list[DrawerDict]` | `searcher.search_memories(query, self.palace_path, wing=wing, room=None, n_results=k)["results"]` | Tool-call search (M0.6). |
| `handle.list_drawers(*, wing=None, room=None, limit=20, offset=0) -> ListDrawersResult` | `self._collection.get(where=build_where_filter(wing, room), limit=limit, offset=offset, include=["documents","metadatas"])` — where `build_where_filter` is imported from `mempalace.searcher`. **Pass `where` only when non-empty**: `build_where_filter(None, None) == {}` and Chroma raises `ValueError: Expected where to have exactly one operator, got {}` on empty dicts. | GDPR admin listing. Returns `{drawers, total, next_offset}`. **Honest-totals contract: `total` is the real `col.count()` ONLY when no filter is applied; under any wing/room filter, `total` is `None`** — Chroma's `collection.count()` has no filter argument, and returning an unfiltered count while drawers are filtered produces user-visible lies ("showing 10 of 2843" when the real filtered set has 47 rows). Flask adapter renders `total=None` as "showing N of many" or omits the count entirely. `next_offset` signals end-of-pagination by returning `None` when the filtered page came back short (fewer rows than `limit`). M0.6 admin-listing may introduce a second filtered `col.get` to compute real totals if a concrete UX requirement surfaces. |
| `handle.kg_add_triple(...)` | *(deferred to M0.7)* — see below | Callers between M0.3 and M0.6 use `self.kg.add_triple(...)` directly. |
| `handle.kg_query_entity(...)` | *(deferred to M0.7)* | Callers use `self.kg.query_entity(...)` directly. |
| `handle.kg_timeline(...)` | *(deferred to M0.7)* | Callers use `self.kg.timeline(...)` directly. |

> **kg_* methods deferred to M0.7.** An earlier draft had them as `*args, **kwargs` passthroughs on `PalaceHandle`. Quality review on M0.3.0 flagged: untyped passthroughs occupy the boundary namespace without isolating — if the library renames a `KnowledgeGraph` parameter, callers break as hard as if they'd imported `KnowledgeGraph` directly. M0.7 (KG queue worker) introduces these methods with **explicit typed signatures** mirroring the current library shape, so a future rename shows up in one file (here) and can be absorbed by updating one file. Between now and M0.7, the three callers that need KG access use `handle.kg.<method>` directly — acceptable because no milestone M0.3-M0.6 needs them.
| `handle.close()` | `self.kg.close()` → `self.turns.close()` → **`_client = _DEFAULT_BACKEND._clients.pop(self.palace_path, None); _client.close()`** (close-before-gc; see amendment below) | **CACHE EVICTION CONTRACT — see "contract with teeth" below.** |
| `handle.col` (property) | returns `self._collection` | Escape hatch for routes that need raw Chroma access (e.g. M0.8 `DELETE /palace/{uid}` enumerates + deletes). Prefer a named method; only use when one doesn't exist. |

**Cache-eviction contract (with teeth).** The library's `ChromaBackend._DEFAULT_BACKEND._clients: dict[palace_path, PersistentClient]` grows every time any path in the sidecar calls `mempalace.palace.get_collection(path, ...)` and has **no public eviction API**. If we open 10 000 palaces over a month and never pop this dict, the sidecar holds 10 000 long-lived PersistentClient instances with open ChromaDB file handles. On a memory-capped systemd unit this OOMs.

Therefore:

1. **Every `PalaceHandle.close()` MUST call `mempalace.palace._DEFAULT_BACKEND._clients.pop(self.palace_path, None)` as the final step, AFTER closing our own state.** This is the boundary between our LRU's view of "evicted" and reality.
2. **A unit test (`test_palace_handle_close_pops_backend_cache`) MUST assert `palace_path not in mempalace.palace._DEFAULT_BACKEND._clients` after `close()`.** This is the test that fails if a future well-meaning refactor "simplifies" the close path and silently reintroduces the leak.
3. **An integration test (`test_registry_eviction_bounds_backend_cache`) MUST open N palaces exceeding the registry's size cap, force evictions, and assert `len(_DEFAULT_BACKEND._clients) <= registry_cache_cap` throughout.** This is the test that fails if the registry evicts the PalaceHandle but the backend cache grows anyway.
4. **Upstream issue to file: [upstream issue not yet filed — target: before P0 entry]** requesting `mempalace.palace.close_backend(palace_path)` as a proper public API. When that lands, we swap our `._clients.pop(...)` for the public call and remove the `_`-prefix access in palace_handle.py. This keeps the library-isolation-boundary story intact.

**Per-`turn_id` idempotency: the source_file encoding trick.** `miner.add_drawer` generates drawer IDs as `f"drawer_{wing}_{room}_{sha256(source_file + str(chunk_index))[:24]}"` and does not accept arbitrary metadata. We encode our `turn_id` into `source_file` as `f"turn:{turn_id}"` with `chunk_index=0`, which produces a deterministic drawer ID per turn. Same idempotency guarantee as "turn_id-as-primary-key" via a different mechanism.

Two consequences that must be written into the tests, not just the prose:

- **If the library ever changes how it derives drawer IDs from source_file** (path normalization, extension handling, length truncation, dropping the `chunk_index` from the hash), our idempotency breaks silently. Therefore: pin `mempalace==3.3.1` exactly (not `>=3.3.1`) in `pyproject.toml`, and `tests/unit/test_idempotency.py::test_same_turn_id_produces_same_drawer_id` must call `miner.add_drawer` twice with the same `turn_id` and assert `collection.count() == 1`. If the upstream ever bumps the hashing scheme, CI fails before the bad version reaches prod.
- **`file_already_mined` in `mempalace.palace` checks `where={"source_file": source_file}`** (exact match, not prefix/suffix). Collision across `turn:<uuid_A>` and `turn:<uuid_B>` is not possible via prefix — each distinct UUID gives a distinct source_file string. Safe.

**Concurrency model inside PalaceHandle.** Every library call we bridge to is synchronous Python. `miner.add_drawer`, `searcher.search_memories`, `col.get`, `col.query`, `kg.add_triple` — all sync. Three consequences:

- **PalaceHandle methods are plain sync `def`, not `async def`.** The class is a sync wrapper over a sync library. Async routes (M0.4+ FastAPI handlers) call PalaceHandle methods via `await asyncio.to_thread(handle.add_drawer, ...)` when they need to release the event loop for real parallelism, or invoke them directly when the method is cheap (sqlite lookups, in-memory state). Making the class `async def` would have forced every method body to be `async` while doing nothing asynchronous — giving callers no benefit and pushing `to_thread` wrapping to every internal library call instead of one wrapper at the route boundary.
- If a caller composes multiple layer calls with `asyncio.gather(l0, l1, l2, l3)` over sync bodies, **the gather does not parallelize** — it runs them sequentially on the event loop. To get real parallelism, wrap each call in `asyncio.to_thread(...)` and gather the thread-pool futures. M0.5.2's `build_wakeup` does exactly this.
- **Whether we need concurrency is a measurement question, not a spec one.** On modest palaces (≤1 k drawers) four sync layer calls might total <100 ms serial, and the thread-pool plumbing is overhead we'd pay for nothing. On larger palaces (L0 `recent_drawers` is O(N) in drawer count for Python-side sort; L3 is an embedding + Chroma query ~30-100 ms) we may need real concurrency to hold p95. M0.5.3's `test_wakeup_concurrent_faster_than_sum_of_parts` mechanically enforces the decision: if parallelism regresses to serial, the test fails red.

**Embedding-model scope — process-global, warmed once (review fix #6, 2026-04-20).** ChromaDB's default embedding function (`chromadb.utils.embedding_functions.DefaultEmbeddingFunction`) wraps `sentence-transformers` for the `all-MiniLM-L6-v2` model. `sentence-transformers` caches loaded model weights by model name in module-level state — a second `SentenceTransformer("all-MiniLM-L6-v2")` in the same process reuses the already-loaded weights. Consequence: **the model is process-global, not per-palace.** Calling `prime_embedding_function(warmup_palace_path)` at sidecar startup (M0.5.1) loads the weights once; every subsequent palace open (including cold opens after LRU eviction) reuses the same in-memory weights and does NOT pay the ~2-5 s cold-model-load tax. Per-palace cold opens pay only the Chroma PersistentClient init cost (~50-200 ms), not the embedding-model load.

The integration test `test_cold_palace_open_reuses_embedding_model` asserts this: open a fresh palace after startup-warmup, time a `col.query` against it, assert the query completes within ~500 ms. If the test fails, either the warmup didn't fire or the weights aren't process-cached — both are regressions against this spec.

**Lifecycle.**

- **Construct:** `PalaceHandle.open(palace_path)` creates the palace directory, writes `meta.json` if absent, opens Chroma via `get_collection`, opens KG sqlite, opens `turns.sqlite`, constructs per-user `MempalaceConfig`. Cheap for a warm Chroma client (~1-5 ms); first-ever open for a new palace pays the Chroma init cost (~50-200 ms) once. Embedding model weights are process-global (see above); cold palace opens do NOT re-load them.
- **Warmup:** process-level warmup via `prime_embedding_function` at sidecar startup (M0.5.1). No per-palace warmup needed.
- **Eviction:** two-phase (§5.4). Phase 1 removes from registry cache inside `_registry_lock`; Phase 2 acquires the evicted user's write lock, then calls `handle.close()` which pops the library backend cache.
- **Close order:** KG sqlite → `turns.sqlite` → `_DEFAULT_BACKEND._clients.pop` → **explicit `client.close()` on the popped client**. **Amendment 2026-04-21 (caused by M0.8 finding):** relying on GC to close the Chroma PersistentClient after popping from `_clients` is INSUFFICIENT on chromadb 1.5.8+. The library's rust-side sqlite layer retains file locks until `client.close()` is called explicitly; without it, a subsequent `PersistentClient` opened for the same path within the same process hits `SQLITE_READONLY_DBMOVED` (error code 1032) on its first write. chromadb's own docstring calls out this requirement: *"This is particularly important for PersistentClient to avoid SQLite file locking issues."* The pop from `_clients` is preserved (prevents the backend-cache-grows-unboundedly failure mode spec'd above); the `client.close()` call comes AFTER the pop and is guarded by try/except for forward-compatibility with chromadb versions that may deprecate the method. This applies to EVERY close path — LRU eviction, idle-sweep, shutdown drain, DELETE — uniformly. The original phrasing ("removing it from the dict plus letting GC finish is the library's documented lifecycle") was valid for chromadb 0.6.x and was verified against that version at M0.3 implementation time; the contract drifted in chromadb 1.5.x. See deltas `2026-04-21 M0.8 chromadb close-before-pop drift discovery` for the diagnostic trail and `M0.3.x-M0.7 were silently incorrect` implication.
- **`close()` is idempotent by contract.** Second and subsequent calls are no-ops — checked via a `self._closed: bool` guard set on the first successful close. Reason: eviction-during-eviction is a real race (size-driven eviction and idle-sweep can target the same user concurrently across two async tasks). The library's `_DEFAULT_BACKEND._clients.pop(..., None)` is idempotent, but `KnowledgeGraph.close()` on an already-closed sqlite connection may raise. Test `test_close_is_idempotent` (M0.3.0) asserts two sequential `close()` calls don't raise.

### 5.4 PalaceRegistry — dispatch and caching

- `_cache: OrderedDict[user_id, PalaceEntry]` — hot palaces with access-order semantics. `OrderedDict` is load-bearing: `move_to_end(user_id)` on every hit makes Phase 1 of size eviction pop from the front (LRU victim). An earlier draft listed a separate `_lru_keys: OrderedDict` — consolidated into `_cache` itself since the dict already preserves insertion/access order as of Python 3.7.
- `_max_bytes: int` = `MEMPALACE_CACHE_MAX_BYTES` (default 2 GB).
- `_locks: dict[user_id, aiorwlock.RWLock]` — per-user RWLock. Reads parallel, writes exclusive. **Never cleaned up on eviction in v1**: one RWLock per user_id ever seen persists for the lifetime of the process. This is a deliberate trade for simplicity — a mutex is ~100 bytes and the alternative (cleanup on Phase 2) introduces a race where an in-flight writer's lock could be deleted mid-operation. Accepted grow-forever at scale (10k users × 100 bytes ≈ 1 MB); revisit if a sidecar-per-process ever runs for months with millions of unique users.
- `_registry_lock: asyncio.Lock` — guards all mutations of `_cache` (NOT `_locks`; see `_lock_for` note below).
- `_lock_for(user_id) -> RWLock` — lazy-creates the per-user lock via `dict.setdefault(user_id, RWLock())`, which is GIL-atomic on CPython. Two racing callers each construct their own RWLock, but `setdefault` ensures only one wins the install and both receive the same reference back. No `_registry_lock` acquire needed on this path — important because route handlers call `_lock_for` on every request and wrapping it in the registry lock would serialise all ingest/search/wake-up.

**`_total_bytes() -> int` is a method, not a cached field** (spec-to-implementation alignment 2026-04-20). Sum-on-demand over `_cache.values()` is O(n) where n is resident palace count (dozens at most). The alternative — caching a running sum and updating on every write path — requires every caller that mutates `PalaceEntry.size_bytes` to remember to update the cache, and silently drifts the day someone forgets. The Hypothesis property test `test_total_bytes_accurate` asserts the invariant after every op; with sum-on-demand it holds by construction.

**Eviction is two-phase** (review-fix 2026-04-20):
- **Phase 1** runs inside `_registry_lock`: select eviction candidates (by LRU order for size-driven eviction, by `last_used_at` for idle TTL) and remove them from `self._cache` so no new requests find them.
- **Phase 2** runs AFTER `_registry_lock` is released: for each evicted user, acquire their per-user `writer_lock`, then call `handle.close()` (which pops the library backend cache per §5.3). The write-lock acquire blocks until any in-flight readers drain, so the close() never races against a handle method whose stack frame still holds a reference to `handle._collection` or `handle.kg`.

Splitting the phases is load-bearing. If `close()` ran inside `_registry_lock`, a concurrent reader that acquired its per-user reader_lock before the evicting request grabbed `_registry_lock` would still be mid-call when `_DEFAULT_BACKEND._clients.pop(...)` fires. The Chroma collection in the reader's stack frame would reference a client that got garbage-collected underneath it. Idle TTL 4h — longer than typical chat gaps, deliberately, to mitigate cold wake-ups. Integration tests `test_registry_eviction_bounds_backend_cache` (backend-cache pop) and `test_eviction_drains_readers` (concurrency hole) together verify the contract.

**Layered locks.** The sidecar holds two independent lock scopes. The per-user RWLock in this registry serializes writes and lets reads run parallel at the palace level. Inside `miner.add_drawer` (and any library code that enters `mempalace.palace.mine_lock(source_file)`), the library acquires an fcntl file lock on `~/.mempalace/locks/<sha>.lock` keyed by `source_file`. The two lock scopes compose — our registry lock is per-palace, the library lock is per-source_file across palaces. For turn-ingest with `source_file=f"turn:{turn_id}"` the library lock is effectively per-turn and benign.

### 5.5 KG extraction — durable queue

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

### 5.6 Migrations — atomic

**Three version dimensions, all orthogonal.** When a palace opens, the sidecar may need to upgrade one, two, or all three. Knowing which is which saves hours in month 14 when someone hits a migration error and has to grep for what version means what.

```
┌────────────────────────┬──────────────────────┬────────────────────┬────────────────────┬──────────────────────────┐
│ Dimension              │ What it tracks       │ Who manages        │ Trigger            │ Failure mode             │
├────────────────────────┼──────────────────────┼────────────────────┼────────────────────┼──────────────────────────┤
│ Chroma on-disk format  │ ChromaDB release     │ library            │ library upgrade    │ corrupt index on open;   │
│                        │ compatibility        │ mempalace.migrate  │ (pip bump)         │ call library.migrate()   │
│                        │                      │                    │                    │ before any collection op │
├────────────────────────┼──────────────────────┼────────────────────┼────────────────────┼──────────────────────────┤
│ Drawer normalize_      │ How noise-stripping, │ library            │ library upgrade    │ stale drawers look OK    │
│ version                │ chunking, and tagging│ mempalace.palace   │ (pip bump)         │ but index mismatches;    │
│                        │ work at write time   │ .NORMALIZE_VERSION │                    │ library auto-heals on    │
│                        │                      │                    │                    │ next mine (see           │
│                        │                      │                    │                    │ file_already_mined)      │
├────────────────────────┼──────────────────────┼────────────────────┼────────────────────┼──────────────────────────┤
│ Sidecar schema_version │ meta.json fields,    │ us (this sidecar)  │ sidecar release    │ we refuse to open with   │
│                        │ turns.sqlite shape,  │ mempalace_sidecar  │ (systemd deploy)   │ 500; migration runner    │
│                        │ routes/schemas.py    │ .migrations/       │                    │ runs atomic sibling-dir  │
│                        │ contract             │                    │                    │ pattern below            │
└────────────────────────┴──────────────────────┴────────────────────┴────────────────────┴──────────────────────────┘
```

**Open-palace sequence** (on every `PalaceHandle.open(palace_path)`):

1. **Check Chroma on-disk format.** `mempalace.migrate.detect_chromadb_version` takes the path to the chroma sqlite file (not the palace dir), opens it via `sqlite3.connect`, and returns one of the coarse labels `"1.x" | "0.6.x" | "unknown"`. `mempalace.backends.chroma.ChromaBackend.backend_version()` returns a full semver like `"1.5.8"`. A naive `on_disk != installed` test would always either raise (fresh palace: no sqlite file yet → `OperationalError`) or trigger on every 1.x open (label `"1.x"` never equals semver `"1.5.8"`). The correct trigger is: **`chroma.sqlite3` exists AND `detect_chromadb_version(chroma_sqlite_path)` returns `"0.6.x"`** — the library's migrator only knows how to migrate the legacy 0.6.x layout to the current 1.x format. If so, call `mempalace.migrate.migrate(palace_path, dry_run=False, confirm=True)` BEFORE any collection open. Fails early with structured error if migration can't proceed.
2. Check `meta.json.schema_version` (our dimension). If lower than current sidecar's `CURRENT_SCHEMA_VERSION`, run our migration (below). If higher, refuse with 500 "sidecar too old." **Corrupt `meta.json` (JSONDecodeError or missing schema_version field)** raises `RuntimeError` with a clear message — routes map this to 500. Silent overwrite is explicitly forbidden: a corrupt meta may be the only evidence that a previous migration crashed mid-write, and overwriting destroys that evidence.
3. Drawer `normalize_version` heals lazily. `file_already_mined(collection, source_file)` returns False for stale drawers and the next mine pass silently rebuilds. No action needed on open.

**Our sidecar's meta.json migration — sibling-directory pattern:**

1. `copytree U/ → U.migrating/` (hardlinks when on same fs).
2. Apply migrations in order.
3. `fsync`, then `rename(U.migrating, U)` after moving old to `U.replaced_<ts>`.
4. On crash: leftover `*.migrating`/`*.replaced_*` older than 24h reaped on next open. The original `U/` directory is never mutated until verification passes — rollback is always `rm -rf U.migrating/`. **Note: `.replaced_<ts>` is a disaster-window *visibility* mechanism (operator can inspect pre-migration state for up to 24h), NOT a guaranteed rollback target — post-migration normal operations propagate writes through hardlinks and gradually corrupt the snapshot. See the hardlink-snapshot-integrity amendment below for the full invariant and the M1-decision open-question on whether to add a post-migration "seal" step.**
5. Schema too new → refuse with 500, "sidecar too old."

**M1-decision open-question (2026-04-21).** Should apply_migrations add a post-migration "seal" step that iterates primary/ and breaks all remaining hardlinks via copy-over semantics? Cost: ~100MB duplicate disk + ~500ms-2s wall time per migration for typical palaces. Benefit: true 24h rollback integrity (sibling snapshot genuinely preserves pre-migration state for the full retention window, not just "until the next primary-side write"). Deferred to M1 scope review. Until the decision lands, operators relying on rollback from `.replaced_<ts>` must perform the rollback BEFORE any post-migration user traffic hits the palace — typically a maintenance-window operation.

**Hardlink-snapshot-integrity invariant (amendment 2026-04-21, M0.9).** `apply_migrations` uses `copytree(..., copy_function=os.link)` for same-filesystem efficiency. After the sibling rename, the primary palace and its `.replaced_<ts>` snapshot share inodes for every file that wasn't overwritten during migration. **Linux does NOT implement copy-on-write for hardlinked files:** any in-place modification writes through the hardlink, silently mutating the snapshot.

**Rule.** Before any in-place modification to a palace file (including but not limited to: dim1 chromadb format migration via `_maybe_migrate_chromadb_format`; the runner's own `meta.json` finalise write; future SQLite VACUUM, compaction, or GC operations), verify `os.stat(file).st_nlink == 1`. If `> 1`, the file is hardlinked to a `.replaced_<ts>` snapshot. Options:

- **(a) Unlink + fresh write.** `file.unlink()` removes the primary-side hardlink (snapshot's copy survives with `st_nlink` decremented by one), then create a fresh file via `write_text` / `open(..., 'w')` / etc. Simplest; works for small files. Used by the runner for its meta.json finalise.
- **(b) Write-to-temp + rename-over.** Textbook copy-on-write: write new content to a temp file in the same directory, then `os.rename(temp, target)` atomically replaces. Breaks the hardlink via the rename. Appropriate when atomic replacement matters (partial writes on crash must not be visible).
- **(c) Defer.** If the modification isn't urgent, wait until `reap_stale` has cleaned the `.replaced_<ts>` snapshot (default 24h after migration). At that point `st_nlink` drops to 1 automatically and in-place writes are safe.

**Teeth-bearing tests:**
- `test_atomic_rename_happy_path` — regression catch for the runner's own meta.json write; asserts `.replaced_<ts>/meta.json` preserves pre-migration `schema_version`.
- `test_hardlink_rename_shares_inodes` — verifies inode identity between primary and `.replaced_<ts>` post-rename; without this, the `st_nlink > 1` guard wouldn't fire.
- `test_dim1_does_not_corrupt_replaced_snapshot_via_hardlink` — verifies the invariant class applies beyond the runner (demonstrates the reverse-order bug the sibling-rename sequence explicitly prevents when dim1 runs before apply_migrations).

**Known v1 limitation.** The st_nlink guard covers known callers (dim1's chromadb migration; the runner's meta.json finalise). However, **post-migration writes to primary files by chromadb / turns.sqlite / kg.sqlite happen inside the library/sidecar app-flow code**, not inside migration-time code paths — those writes have NO st_nlink guard and WILL propagate through hardlinks to `.replaced_<ts>` during the 24h retention window. Implication: `.replaced_<ts>` is a valid rollback target ONLY immediately after migration (before any primary-side write). Normal operations gradually corrupt the snapshot via hardlinks. The 24h retention is therefore a disaster-window *visibility* mechanism (operator can see what state existed at migration time), NOT a guaranteed rollback mechanism.

A future milestone (M1 candidate) may add a post-migration "seal" step that iterates primary/ and breaks all remaining hardlinks via copy-over. Cost: ~100MB duplicate disk + ~500ms-2s wall time per migration for typical palaces. Benefit: true 24h rollback integrity. Decision deferred to M1 scope review.

Discovered via M0.9 Stage 3 GREEN when `test_atomic_rename_happy_path` caught plan-sketch code writing `meta.json` through the hardlink. See deltas entry `2026-04-21 M0.9 runner meta.json write-through-hardlink bug` for the full trail.

### 5.7 Shutdown protocol (SIGTERM)

1. FastAPI lifespan stops accepting new connections.
2. Signal flusher + KG worker to drain.
3. Wait up to **10s** for in-flight handlers.
4. Cancel stragglers; writes roll back cleanly so outbox retries pick up.
5. Close all Palace handles (fsync Chroma + KG) via `PalaceRegistry.close_all()` — reuses the same two-phase eviction pattern as `sweep_idle` / `_evict_to_fit_unlocked`: Phase 1 snapshots + clears `_cache` inside the registry lock; Phase 2 acquires each user's writer_lock and calls `handle.close()`.
6. Exit. `TimeoutStopSec=15s` in systemd (10s drain + 5s grace).

### 5.8 Config (`/etc/mempalace/sidecar.env`)

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

- `test_auth.py`, `test_registry.py` (Hypothesis property test on byte accounting; plus concurrency-hole test `test_eviction_drains_readers`), `test_migrations.py` (crash-mid-migration via `pytest-forked`), `test_kg_queue.py`, `test_shutdown.py`, `test_wakeup_concurrency.py` (asserts `build_wakeup` populates all four layers; the parallelism claim is tested in `test_wakeup_concurrent_faster_than_sum_of_parts` — see §6 measurement gates).

**Layer 3 — Integration** (`@pytest.mark.integration`, ≤5min)

~95% via `httpx.AsyncClient(transport=ASGITransport(app))` in-process with real Chroma + real SQLite on `tmp_path`. One dedicated test (`test_socket_e2e.py`) spawns the actual sidecar subprocess over the actual UDS to cover socket permissions, signal handling, and the UDS peer-check.

Scenarios: ingest→search round-trip (with cross-user isolation assertion), pair_id integrity, wake-up layer composition with seeded palace, hot vs cold latency separation, idempotency replay, migration end-to-end, delete, rebuild.

**Layer 4 — Contract** — FastAPI-generated OpenAPI as single source. `schemathesis` (Flask side) + snapshot comparison (sidecar CI). Schema change without `schema_version` bump = PR failure.

**Layer 5 — End-to-end** (nightly, ≤15min)

Full Flask + live sidecar + stubbed LLM. Scenarios:
- Cold-user first turn: no injection, background write succeeds, next turn injects.
- **`test_flusher_crash_idempotency_persistence`**: send 10 turns, SIGKILL sidecar between ACK of row 8 and ACK of row 9, restart, Flask retries, assert palace contains exactly 10 distinct `turn_id`s (queried via `turns.sqlite`). Disk-backed `turns.sqlite` with `PRIMARY KEY(turn_id)` is the invariant; in-memory idempotency fails this test.
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
8. **Non-optional**: `systemctl restart xelasphere` (gunicorn picks up new group membership only on restart). Post-check: `[ "$(grep -c '^Groups:.*mempalace' /proc/$(pgrep -f 'gunicorn: master')/status)" -eq 1 ]`.

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
4. At P4: one PR deletes both directories, removes `LEGACY_MEMORY` flag, removes call-site guards, **and removes the both-off boot refusal from `app/__init__.py`** (see §3 "Post-P4"). Post-P4, `MEMPALACE_MODE=off` must boot — it is the outage relief valve.

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

DR hierarchy — derived from §1's "palace is a derived index, conversations are source of truth":

> **Restic snapshots are a recovery-time optimization, not a durability layer. Losing all snapshots is an RTO degradation, not a data loss.** Backup alerting is therefore warn, not page.

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
| `mempalace_chroma_query_seconds` | histogram | `layer={l2,l3}` | ChromaDB embed+search time (library-internal). Embedding and vector search are fused inside `col.query(query_texts=...)`; separating them requires switching to `col.query(query_embeddings=[vec])` which is a measurement-driven follow-up, not v1. |
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
| `xelasphere_mempalace_flusher_last_tick_seconds` | gauge | — | Flusher liveness (deadlock detection). **Updated every loop iteration, including the no-work path.** Empty-outbox idle must still tick — otherwise `MempalaceFlusherStalled` false-pages overnight. |
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
