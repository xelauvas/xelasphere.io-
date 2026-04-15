# Background Sub-Agents — Design Spec

**Date:** 2026-04-14
**Status:** Approved design, pending implementation plan
**Scope:** v1 — research + read-only code agents dispatched from chat

## Summary

Add background sub-agents to XelaSphere that run as `xela` subprocesses while the user continues chatting. Sub-agents are dispatched either autonomously by the main chat model or manually by the user, gated by per-dispatch tool approval, visualized as inline cards in the chat message stream, and persisted so they survive tab close and server restart (with graceful failure). Sub-agent output flows through the existing artifacts system for review and apply.

## Goals

- Users can dispatch one or more sub-agents from a chat without blocking the main conversation.
- Sub-agents have hard-capped tool access, approved per dispatch, defaulting to read-only research + code analysis.
- Live activity (tool calls, partial text, events) is visible in an inline card.
- Sub-agents survive tab close; badges surface terminal state on return.
- Code output routes through existing artifact review/apply — zero new write safety to build.

## Non-Goals (v1)

- No sub-agent recursion (sub-agents cannot dispatch further sub-agents).
- No disk writes or shell execution by sub-agents (`Write`, `Edit`, `Bash` hard-denied).
- No sandbox/container isolation (relies on tool allowlist).
- No automatic retry of failed agents (user re-dispatches).
- No multi-user sharing of agents.
- No cross-chat agents.

## User Requirements (answered during brainstorming)

| Q | Answer |
|---|---|
| Agent model | Sub-agents inside one chat, parallel workers |
| Dispatch trigger | Both — main model auto-dispatch AND user manual dispatch |
| Tool scope | User-configurable per dispatch with approval gate |
| UI placement | Inline cards in chat (Claude Code style) |
| Tab-close behavior | Agent keeps running + notifies on return |

## Architecture

```
Browser (chat)
  └─ Inline AgentCard ← SSE ← Flask /agents/<id>/events
                                   ↑
                        Redis pub/sub: agent:<id>:events
                                   ↑
                   AgentSupervisor (standalone daemon, systemd)
                          ├─ BLPOP agent:jobs  (Redis queue)
                          ├─ spawn `xela` subprocess
                          ├─ parse NDJSON from stdout
                          ├─ publish events to Redis (pub/sub + capped list)
                          └─ persist events + final state to Postgres
```

**Key choices:**

- **Executor:** `xela` CLI (published as `@xelauvas/xela-cli`, source in `./claude-code/`). Fork of Claude Code supporting free models (Groq, OpenRouter, Ollama). Same flag surface as official `claude` CLI; swap escape hatch available.
- **Supervisor:** standalone daemon (systemd unit `xelasphere-agents.service`), not embedded in gunicorn. Agents survive web-tier restarts. Single owner per agent enforces monotonic seq.
- **Event transport:** Redis pub/sub for live fan-out, Redis capped list (2000 events, 24h TTL post-terminal) for SSE reconnect replay, Postgres `agent_events` table as permanent record.

## Components

### 1. AgentSupervisor (`app/agents/supervisor.py` — runs under systemd)

- `BLPOP agent:jobs` on Redis work queue.
- For each job: re-read DB status; if not `queued`, discard + emit `agent_cancelled`.
- Spawn `xela` subprocess via `subprocess.Popen(..., start_new_session=True)`.
- Write context JSON to subprocess stdin, close stdin (fallback to `--append-system-prompt-file` with `try/finally` unlink if stdin path fails).
- Background read loop per agent: parse NDJSON, validate, assign monotonic seq, `LPUSH` (`LTRIM` to 2000), `PUBLISH`, batch-append to Postgres.
- Crash handling: `process.wait()` non-zero → flush pending DB batch + stderr tail synchronously → emit `agent_error`.
- Timeout: 15min hard cap; idle (no stdout) warning at 60s, SIGTERM at 5min idle.
- Cancel: `SIGTERM` → 5s grace → `SIGKILL`.
- Orphan sweep on daemon startup: `os.kill(pid, 0)` for every `running` row; dead PIDs marked `failed` with `cancel_reason='orphaned'`.
- Seq counter per agent: initialized from `SELECT COALESCE(MAX(seq),0) FROM agent_events WHERE agent_id=?` on start/restart.

### 2. AgentEventStore (Redis + Postgres)

**Redis:**
- `agent:<id>:events` — pub/sub channel, ephemeral.
- `agent:<id>:events:list` — capped list, 2000 events, 24h TTL applied after terminal event.

**Postgres:**

```sql
CREATE TABLE agents (
  id UUID PRIMARY KEY,
  chat_id UUID NOT NULL REFERENCES chats(id),
  user_id UUID NOT NULL,
  dispatch_message_id UUID,               -- anchor for card render position
  dispatched_at TIMESTAMPTZ NOT NULL,     -- fallback sort for orphaned cards
  task TEXT NOT NULL,
  model TEXT,
  tools_requested JSONB NOT NULL,
  tools_granted JSONB,
  status TEXT NOT NULL,                   -- awaiting_approval | queued | running | done | failed | cancelled | denied | expired
  cancel_reason TEXT,                     -- user | timeout | supervisor_restart | budget_exceeded | orphaned | null
  pid INT,
  cost_usd NUMERIC,
  cost_approximate BOOLEAN DEFAULT false,
  user_dismissed_at TIMESTAMPTZ,
  chat_deleted_at TIMESTAMPTZ,            -- set on cascade unlink, not immediate delete
  started_at TIMESTAMPTZ,
  terminated_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE agent_events (
  agent_id UUID NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
  seq INT NOT NULL,
  type TEXT NOT NULL,
  payload JSONB NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (agent_id, seq)
);
```

Retention:
- Redis: 24h TTL applied after terminal event.
- Postgres: `agents` rows persist after chat deletion (unlinked via `chat_deleted_at`). A daily cleanup job hard-deletes rows where `chat_deleted_at < now() - interval '7 days'`. `agent_events` cascades via `ON DELETE CASCADE`. Artifacts produced by agents are **never** deleted by this cleanup — only unlinked.

### 3. AgentDispatcher (`app/blueprints/agent_routes.py`)

- `POST /agents/dispatch` — `{chat_id, task, tools_requested, model}` → creates row in `awaiting_approval`, returns `agent_id`.
- `POST /agents/<id>/approve` — `{tools_granted}` — **idempotent** via conditional `UPDATE ... WHERE status='awaiting_approval'`. Rowcount=0 → 409. Push to Redis `agent:jobs`.
- `POST /agents/<id>/cancel` — conditional update across valid states; null-PID case handled directly (mark `cancelled` without signal).
- `POST /agents/<id>/dismiss` — sets `user_dismissed_at`.
- `GET /agents/<id>/events?after=<seq>` — SSE. Replays from Redis list for `seq > after`; if `after < oldest_in_list`, emit synthetic `{type:"stream_gap","missed_from":after,"resumed_at":oldest}` first. Then subscribes to pub/sub. Heartbeat `:ping\n\n` every 15s. Closes after 60s of no client reads.
- `GET /chats/<id>/agents` — list of agents for chat with `last_seq` per agent for reconnect bootstrap.

### 4. AgentContextBuilder (`app/agents/context.py`)

Builds the JSON passed via stdin at spawn:

```json
{
  "parent_chat_title": "...",
  "task": "...",
  "recent_messages": [/* last 10 turns, each ≤4k chars, truncation oldest-first */],
  "cited_artifacts": [{"id": "...", "title": "..."}]
}
```

- Hard cap 32k chars total. Truncation oldest-first on `recent_messages`. Emits `context_truncated` event if applied.
- Never includes: user secrets, API keys, other chats, memory files.

### 5. Orchestrator hook (patch to `app/orchestrator/`)

- Register synthetic tool `dispatch_agent(task, tools, model?)` in main chat's tool list.
- When main model invokes it: short-circuit (do not forward to executor); create `awaiting_approval` agent row; emit `agent_dispatched` into main chat SSE; return synthetic tool result `"Agent <id> dispatched; awaiting user approval."` so main model can continue the turn.
- Reject dispatch with denied tool: synthetic error `"Tool '<name>' not permitted for sub-agents in v1."` Main model retries with allowed tools.
- **Pending-agent awareness injected into system prompt each turn:** `"Pending agents in this chat: [...]"` — rebuilt from DB, lets main model naturally nudge user about unfinished approval cards. Zero special-casing.

### 6. Frontend — AgentCard (`app/static/js/agentCard.js`)

Inline card rendered in chat stream. Anchored to `dispatch_message_id`; if anchor deleted, renders at bottom with prefix `"(dispatched earlier)"`, ordered by `dispatched_at`.

**States:** `awaiting_approval` | `queued` | `running` | `done` | `failed` | `cancelled` | `denied` | `expired`.

Approval-state actions: **Approve** (grants as-requested), **Edit tools** (can only remove, not add), **Deny**.

Running-state actions: **Cancel**, **Expand** (full event timeline). Collapsed shows current tool call OR latest partial text.

Terminal-state: summary + cost + expand. Linked artifact renders inline if `artifact != null`.

Reconnect: uses `last_seq` from `GET /chats/<id>/agents` bootstrap; never `?after=0` unless truly new.

## Data Flow (auto-dispatch lifecycle)

1. Main model streaming; emits `dispatch_agent(...)` tool call.
2. Orchestrator intercepts → insert `agents` row (`awaiting_approval`) → emit `agent_dispatched` → return synthetic tool result.
3. Main model finishes turn; `AgentCard` rendered in approval state.
4. User clicks Approve → `POST /agents/<id>/approve` → conditional DB update → push `{agent_id}` to `agent:jobs`.
5. Supervisor `BLPOP`s → re-reads status → spawns `xela`.
6. Event loop: NDJSON line → seq → Redis LPUSH/PUBLISH → DB batch append.
7. Subprocess exits:
   - exit 0 → `result` event → `agent_done` + artifact creation (if `artifact != null`).
   - exit ≠ 0 → `agent_error` with stderr tail + cost.
   - SIGTERM'd → `agent_cancelled` with reason.
   - All terminal events flush batch + stderr tail to DB **synchronously before** emitting Redis event.
8. Tab close: daemon keeps running; events persist.
9. Tab reopen: `GET /chats/<id>/agents` → bootstrap cards with `last_seq`; non-terminal agents reconnect via SSE.
10. Badge surfaces if terminal state reached with `terminated_at > last_seen_at` AND `user_dismissed_at IS NULL` AND (status=`done`/`failed` OR involuntary cancel).

## NDJSON Event Schema

Emitted by `xela` (plus supervisor-synthetic events). All events carry `type`, payload varies.

**xela-emitted:** `system`, `assistant` (partial/final), `user` (tool result), `tool_use`, `tool_result`, `result`.

**Supervisor-synthetic:** `agent_dispatched`, `agent_started`, `agent_done`, `agent_error`, `agent_cancelled`, `agent_denied`, `agent_expired`, `context_truncated`, `stream_gap`, `stream_corruption`.

**Terminal `result` event:**
```json
{
  "type": "result",
  "text": "final assistant message",
  "cost_usd": 0.03,
  "artifact": null
  // OR artifact object:
  // "artifact": {
  //   "kind": "code" | "markdown" | "patch",
  //   "title": "...",
  //   "content": "...",
  //   "target_path": "app/auth.py",   // REQUIRED for kind="patch", FORBIDDEN otherwise
  //   "base_sha": "a1b2c3d"           // optional for drift detection on patches
  // }
}
```

**`agent_error` event — required fields:**
```json
{
  "type": "agent_error",
  "reason": "crashed" | "executor_missing" | "invalid_model" | "hung" | "stream_corruption" | "supervisor_restart",
  "exit_code": -1,
  "stderr_tail": "...",
  "cost_usd": 0.01,
  "cost_approximate": false
}
```

If xela does not emit cost on non-zero exit, supervisor approximates from intermediate `assistant` usage fields or falls back to `0.0` with `cost_approximate: true`. DB always has a number.

## Tool Allowlist (v1)

**Permitted:** `WebFetch`, `WebSearch`, `Read`, `Glob`, `Grep`, `read_artifact`.

**Hard-denied (rejected at orchestrator, not surfaced in approval dialog):** `Write`, `Edit`, `Bash`, `send_email`, any mutating tool. Extension to mutating tools is a separate scoped v2 change with its own review.

User can remove tools from the requested set during approval (not add).

## Code Output via Artifacts

Agents never write to disk. Code output flows through existing artifact system:

- `kind: "code"` — self-contained rewritten file, rendered in code artifact panel.
- `kind: "markdown"` — notes/explanations/summaries.
- `kind: "patch"` — unified diff with required `target_path` and optional `base_sha` for drift detection. Applied via existing patch-apply flow.

Existing artifact review/edit UX is the safety gate. No new apply path to build.

## Persistence & Resume

Terminal-state rules:
- All terminal events flush pending DB batch + stderr tail **synchronously before** Redis emit — crash durability guarantee.
- Redis list TTL (24h) applied only **after** terminal event.
- Postgres is source of truth; Redis is a hot cache.

Supervisor restart policy: any agent in `running` state at restart gets `SIGTERM` + marked `cancelled` with `cancel_reason='supervisor_restart'`. Re-attach to existing subprocess stdout is a v2 concern.

Chat delete: SIGTERM running agents; set `agents.chat_deleted_at`; **never cascade-delete artifacts** — unlink via `source_agent_id=NULL, source_chat_id=NULL`. Applied code belongs to the user, not the chat.

## Error Handling

| Failure | Detection | Recovery |
|---|---|---|
| xela binary missing | `Popen` raises | `agent_error reason=executor_missing` |
| Invalid model | xela exits early | `agent_error reason=invalid_model` |
| Budget exceeded | xela signals | `cancelled reason=budget_exceeded`, cost recorded |
| Tool error | Tool event with error | Rendered in timeline; agent continues |
| Bad NDJSON line | Parse exception | Synthetic `stream_corruption`, skip line, continue |
| Subprocess hang | 60s idle warning, 5min SIGTERM | `cancelled reason=timeout` |
| Redis down | PUBLISH/LPUSH raises | 3× retry with backoff; events still hit DB |
| Postgres down | DB write raises | **Supervisor retries indefinitely at 5s backoff; agent continues running; writes queued in memory (cap 5000, drop-oldest with warning log).** Killing agent because DB is flaky is wrong trade-off. |
| Client SSE disconnect | Heartbeat write fails | Server closes; client reconnects with `?after=<last_seq>` |
| Approval pending >24h | Cleanup job | Mark `expired`, emit synthetic event |

## Safety Invariants

1. Agent never writes to disk outside tool allowlist — enforced by `xela` command-line allowlist.
2. Terminal state reached exactly once — conditional DB update; duplicates dropped + logged.
3. Seq monotonic per agent — single-owner supervisor + DB unique `(agent_id, seq)`.
4. No agent outlives its parent chat alive — SIGTERM on delete; `chat_deleted_at` set so orphan sweep can distinguish legitimate-orphan vs chat-delete-orphan.
5. Every terminal state flushes event batch + stderr tail before emitting Redis terminal event.

## Testing Strategy

**Unit (pytest, no network):**
- NDJSON parser — malformed, partial, schema violations.
- Supervisor state machine — approve/cancel/timeout/crash, idempotency, TOCTOU.
- Context builder — truncation, secret redaction.
- Tool allowlist — denied tool rejection.
- Artifact schema — `kind`/`target_path` constraints.
- Seq counter — MAX init, monotonic under concurrent append.
- Badge query — dismissal, involuntary-cancel inclusion.

**Integration (ephemeral Postgres + Redis):**
- Dispatch → approve → spawn (xela faked via scripted-NDJSON fake subprocess) → events → terminal → artifact row.
- Cancel during queued (pre-spawn) — never spawns.
- Cancel during running — SIGTERM grace → SIGKILL.
- Orphan sweep — dead PIDs in `running` → failed.
- Resume — tab close, events continue, reopen replays correctly.
- Stream gap — reconnect with stale `after` → synthetic `stream_gap` emitted.
- **Slow-emitter fake** — deliberate delays between lines to exercise idle timer + heartbeat paths.

**E2E (Playwright or manual against real `xela`):**
- Auto-dispatch → approval → Groq free-tier run → markdown artifact.
- Real cancel mid-run.
- Tab close/reopen.
- Patch artifact → existing apply flow → file updated.

**Non-goals v1:** load testing, chaos testing, multi-user concurrency beyond two-tab case.

## Observability

**Structured logs per agent:** `agent_id`, `chat_id`, `user_id`, event type, duration.

**Metrics:**
- Agents dispatched / hour
- Approval rate = approved / (approved + denied)
- **Tool removal rate** = how often users edit the tools list before approving — product signal for whether default allowlist is well-calibrated.
- Deny-reason distribution (if captured)
- Terminal status distribution
- p50/p95 agent duration
- Cost per agent
- Supervisor queue depth

v1 queries are ad-hoc DB; dashboard in v2.

## Known v1 Limitations

- No recursion (sub-agents can't spawn sub-agents). Nested workflows (research → analyze → report) must be flat — main model sequences them.
- No disk writes (safe by design; produces artifacts for user to apply).
- No sandbox (relies on tool allowlist).
- No re-attach after supervisor restart (clean fail + user re-dispatch).
- No automatic retry on failure.
- Per-user cost cap is v2 (per-agent `--max-budget-usd` is v1).

## Open Questions / Deferred

- v2: sandboxed code agents with `Write`/`Bash` via Docker-per-agent with branch isolation.
- v2: per-user monthly spend cap + UI.
- v2: supervisor restart re-attach via `/proc/<pid>/fd/1` stdout recovery.
- v2: agent recursion with depth limits.
- v2: cross-chat and cross-user agent sharing.

## Implementation Scope Estimate

~3 days for v1 if focused (research + code artifacts path only, leveraging existing artifact UX). Breakdown and concrete tasks land in the implementation plan.
