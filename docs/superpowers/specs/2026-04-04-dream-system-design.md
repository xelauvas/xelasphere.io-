# Dream System: Background Memory Consolidation

**Date:** 2026-04-04
**Status:** Approved

## Overview

A background memory consolidation system inspired by Claude Code's Dream System, adapted for XelaSphere's multi-user Flask web app with Firestore-backed Memory V2. Periodically reviews a user's accumulated memories across sessions, merging duplicates, pruning stale entries, resolving conflicts, and synthesizing higher-level insights.

## Goals

- Reduce memory bloat from overlapping/duplicate entries
- Remove stale memories that no longer reflect reality
- Synthesize patterns across individual facts into higher-level insights
- Improve retrieval quality by keeping only high-value memories active

## Architecture: Hybrid Two-Phase Consolidation

**Phase 1 (Algorithmic):** Handles 80% of work — prune expired, merge near-duplicates, detect conflicts, flag low-value items. No LLM needed. Runs even if Kimi is down.

**Phase 2 (Kimi LLM):** Handles the remaining 20% — resolve ambiguous conflicts, promote provisionals with corroborating evidence, synthesize new pattern-based memories. All decisions pass through V2 judge before execution.

---

## 1. Trigger & Gate System

Per-user, checked after each successful chat response in `chat_routes.py`. Fire-and-forget via `threading.Thread(daemon=True)` — never blocks the user's response. Daemon thread ensures cleanup on worker shutdown.

### Gate 1 — Time Gate (cheapest, checked first)
- Firestore field: `users/{userId}/dream_state.last_consolidated_at`
- Minimum 24 hours between consolidations (user-configurable, 6h–168h)
- One Firestore read per chat turn, cached in session for the request duration

### Gate 2 — Session Count Gate
- Count sessions where `updated_at > last_consolidated_at` for this user
- Minimum 5 new sessions since last consolidation (user-configurable, 3–20)
- Only evaluated if Gate 1 passes

### Gate 3 — Lock Gate (mutual exclusion)
- Firestore field: `users/{userId}/dream_state.lock_holder` (worker ID + timestamp)
- Prevents concurrent duplicate dreams for the same user
- Stale lock recovery: if lock older than 1 hour, reclaim it
- On failure: release lock and rewind `last_consolidated_at`

### Additional Guards
- Skip if user has fewer than 10 active memories
- Skip if `dream_enabled = false` in user preferences
- Hard rate limit floor: max 1 consolidation per user per 6 hours

---

## 2. Phase 1 — Algorithmic Consolidation

No LLM. Direct Firestore operations using existing V2 store.

### Step 1 — Prune Expired
- Query memories where `status = active|provisional` and `lifecycle.expires_at < now`
- Provisional memories older than 14 days with no confirmation -> expire
- Temporary memories older than 24 hours -> expire
- Mark all as `status = expired`

### Step 2 — Merge Near-Duplicates
- Load all active memories for the user
- Compute pairwise embedding similarity for memories with the same `type`
- If similarity > 0.92 AND same normalized `key` -> auto-merge:
  - Keep the one with higher confidence
  - Combine `use_count`, take higher `repeated_by_user`
  - Mark loser as `superseded_by` winner
  - Update winner's `lifecycle.updated_at`

### Step 3 — Detect Conflicts
- Group active memories by `key`
- If multiple share a key with contradictory values (using `conflict.py` contradiction detection) -> flag for Phase 2
- Complementary values (accumulate types like constraints) -> skip

### Step 4 — Score & Flag Low-Value
- Memories with `use_count = 0` AND `last_used_at` older than 60 days -> flag for Phase 2
- Memories with `confidence < 0.5` never promoted -> flag

### Output: ConsolidationReport
```python
@dataclass
class ConsolidationReport:
    pruned: List[str]                    # Memory IDs expired
    merged: List[Tuple[str, str]]        # (winner_id, loser_id) pairs
    flagged_conflicts: List[List[str]]   # Groups of conflicting memory IDs
    flagged_low_value: List[str]         # Memory IDs to review
    all_active: List[MemoryItem]         # Remaining active memories for Phase 2
```

---

## 3. Phase 2 — Kimi LLM Consolidation

Only runs if Phase 1 flagged anything OR 15+ active memories remain (pattern synthesis opportunity). Skipped entirely otherwise.

### LLM: Kimi (Moonshot)
- Large context window ideal for ingesting full memory collection
- Max 4000 input tokens for the prompt; summarize older memories if exceeded

### Input Prompt Contains:
1. Flagged conflicts with full memory details (key, value, confidence, timestamps)
2. Flagged low-value memories with usage stats
3. Summary of all remaining active memories (type, key, value)

### Kimi Outputs Structured JSON:
```json
{
  "resolve_conflicts": [
    {
      "keep_id": "mem_abc",
      "supersede_ids": ["mem_xyz"],
      "reason": "User changed timezone from PST to GMT based on newer evidence"
    }
  ],
  "prune": [
    {
      "memory_id": "mem_old",
      "reason": "Referenced a project not mentioned in 3 months"
    }
  ],
  "promote": [
    {
      "memory_id": "mem_prov",
      "reason": "Consistent with 3 other active memories about user role"
    }
  ],
  "synthesize": [
    {
      "type": "preference",
      "key": "iteration_style",
      "value": "Prefers iterative design over many turns before committing",
      "evidence_ids": ["mem_a", "mem_b", "mem_c"],
      "confidence": 0.75
    }
  ]
}
```

### Four Decision Types:
- **resolve_conflicts** — pick winner from flagged conflicts, supersede losers
- **prune** — expire flagged low-value memories Kimi agrees are stale
- **promote** — upgrade provisional -> active with corroborating evidence
- **synthesize** — create new higher-level memories from patterns (max 3 per cycle)

### Safety: All Kimi decisions pass through V2 judge before execution.
- Synthesized memories enter as `provisional` (confidence capped at 0.8)
- Conflict resolutions use existing `conflict.py` supersede logic
- Prune decisions only apply to already-flagged items
- Kimi cannot delete unflagged memories

---

## 4. Execution & Persistence

All mutations within a single Firestore batch write for atomicity.

### Execution Order:
1. **Phase 1 direct actions:**
   - Expired memories -> `status = expired`
   - Merged duplicates -> winner updated, loser `status = superseded`

2. **Phase 2 judge-reviewed actions:**
   - Each Kimi decision validated by `judge.py`
   - Approved conflict resolutions -> supersede via `conflict.py`
   - Approved prunes -> `status = expired`
   - Approved promotions -> `status = active`, confidence updated
   - Approved syntheses -> new `MemoryItem` as `provisional`, embedding generated async

3. **Consolidation report saved:**
   - `users/{userId}/dream_logs/{timestamp}`
   - Fields: `started_at`, `completed_at`, `phase1_actions`, `phase2_actions`, `kimi_raw_response`, `errors`, `before_snapshots`

4. **Lock released:**
   - `dream_state.last_consolidated_at = now`
   - `dream_state.lock_holder = null`

### Error Handling:
- Phase 1 succeeds, Phase 2 fails (Kimi down) -> commit Phase 1 only, log failure, release lock
- Batch write fails -> release lock, rewind `last_consolidated_at`, log error
- Lock stale-reclaimed mid-dream -> abort, discard pending actions
- Max 50 mutations per cycle; remainder handled next cycle

### Kimi Circuit Breaker:
- 3 consecutive Kimi failures for any user -> disable Phase 2 globally for 1 hour
- Phase 1 continues running independently

---

## 5. User-Facing Notifications & Activity Log

### Toast Notification (in chat)
- After consolidation, set flag: `dream_state.pending_notification`
- Next chat response includes toast data, clears the flag
- Format: "Memory improved: 3 merged, 2 pruned, 1 new insight"
- Auto-dismisses after 5 seconds, non-blocking

### Memory Activity Log (in settings)
New section under user settings / memory management:

```
Memory Activity
---
Apr 4, 2026 — 3:12 AM
  Merged 2 duplicate preferences (response_style)
  Pruned 1 expired project memory
  Resolved conflict: timezone PST -> GMT (kept newer)
  New insight: "Prefers iterative design over many turns"
  [Undo all] [View details]

Mar 28, 2026 — 11:45 PM
  Pruned 3 expired temporary memories
  Promoted 1 provisional -> active (role: backend engineer)
  [View details]
```

### Undo Capability
- Each dream log stores `before_snapshots` of modified memories
- "Undo all" restores pre-consolidation state from the dream log
- Individual undo per action via "View details" expansion
- Undo window: 7 days (after that, dream logs archive and undo disabled)

### Implementation:
- Toast: addition to chat response payload + frontend JS toast component
- Activity log: `GET /user/memory-activity` reading from `dream_logs` collection
- Undo: `POST /user/memory-activity/{log_id}/undo` restoring snapshots
- Frontend: new section in existing settings/memory page

---

## 6. Configuration & Safeguards

### User-Configurable (stored in user preferences):
| Setting | Default | Range | Description |
|---------|---------|-------|-------------|
| `dream_enabled` | `true` | bool | Opt out entirely |
| `dream_frequency_hours` | `24` | 6–168 | Min hours between consolidations |
| `dream_min_sessions` | `5` | 3–20 | Sessions needed to trigger |

### System Safeguards:
| Safeguard | Value | Purpose |
|-----------|-------|---------|
| Min active memories | 10 | Skip if nothing meaningful to consolidate |
| Kimi token budget | 4000 | Cap Phase 2 prompt size |
| Synthesis cap | 3/cycle | Prevent runaway memory creation |
| Hard rate floor | 6 hours | Absolute minimum between runs |
| Kimi circuit breaker | 3 failures | Disable Phase 2 for 1 hour |
| Batch mutation limit | 50/cycle | Cap Firestore writes per run |

### Monitoring:
- Log each dream: duration, phase1/phase2 action counts, Kimi latency, errors
- Track per-user dream frequency and action counts
- Alert if dream failure rate exceeds 10% over 1 hour

---

## File Structure (New Files)

```
app/
  memory_v2/
    dream/
      __init__.py
      orchestrator.py      # Gate checks, background thread launch, coordination
      gates.py             # Time gate, session count gate, lock gate
      phase1.py            # Algorithmic: prune, merge, detect conflicts, flag
      phase2.py            # Kimi LLM: conflict resolution, synthesis, promotion
      prompt.py            # Build Phase 2 prompt for Kimi
      executor.py          # Batch write execution, snapshot capture, undo support
      report.py            # ConsolidationReport dataclass and dream log persistence
      config.py            # User settings, system safeguards, defaults
  blueprints/
    memory_routes.py       # Add: GET /user/memory-activity, POST .../undo (extend existing)
  static/
    js/
      dreamToast.js        # Toast notification component
    css/
      dream-activity.css   # Memory activity log styles
  templates/
    (extend existing settings/memory template with activity log section)
```

---

## Integration Points

| Existing File | Change |
|---------------|--------|
| `blueprints/chat_routes.py` | After chat response, call `dream_orchestrator.maybe_consolidate(user_id)` in background thread |
| `blueprints/memory_routes.py` | Add memory-activity and undo endpoints |
| `memory_v2/store.py` | No changes — dream uses existing store operations |
| `memory_v2/judge.py` | No changes — Phase 2 decisions pass through existing judge |
| `memory_v2/conflict.py` | No changes — dream uses existing conflict resolution |
| `memory_v2/schema.py` | Add `dream_state` fields to user model |
| `static/js/script.js` | Add toast display logic for dream notifications |
| `templates/index.html` | Add toast container element |
| Settings template | Add memory activity log section + dream settings toggle |
