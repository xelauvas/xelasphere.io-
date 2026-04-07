# Kimi Circuit Breaker & Failure Rate Tracking

**Date:** 2026-04-05
**Status:** Draft
**Parent spec:** [Dream System Design](2026-04-04-dream-system-design.md)

## Problem

Phase 2 of the dream system calls the Kimi API. When Kimi is down or a user has malformed memories, the system blindly retries every consolidation cycle. This wastes time, generates noise in logs, and delays Phase 1 work (which doesn't need Kimi at all).

## Solution

Two-layer circuit breaker that disables Phase 2 when Kimi calls fail repeatedly, while Phase 1 continues unaffected.

### Layer 1: Per-User Breaker

Protects against one user's bad data poisoning their own dream cycles.

- **Threshold:** 3 consecutive Kimi failures for the same user
- **Effect:** Phase 2 disabled for that user for 1 hour
- **Scope:** Only affects the user whose calls are failing
- **Reset:** Automatic after cooldown expires, or immediate on next successful Kimi call

### Layer 2: Global Breaker

Protects against Kimi API outages. When the API is down, no point having every user fail individually.

- **Threshold:** 5 consecutive Kimi failures across any users
- **Effect:** Phase 2 disabled for all users for 1 hour
- **Scope:** All users
- **Reset:** Automatic after cooldown expires, or immediate on next successful Kimi call

### Why Both Layers

| Scenario | Per-user only | Global only | Both |
|----------|--------------|-------------|------|
| User has bad data | Contained to that user | Shuts down everyone | Contained to that user |
| Kimi is down | Every user fails 3x before their own breaker trips | Caught after 5 failures total | Caught after 5 failures total |

---

## Data Model

### Storage

Firestore collection: `dream_circuit_breaker`

Two document types:

**Per-user document** — ID: `user_{userId}`
```json
{
  "consecutive_failures": 2,
  "last_failure_at": "2026-04-05T12:00:00Z",
  "disabled_until": null
}
```

**Global document** — ID: `global`
```json
{
  "consecutive_failures": 4,
  "last_failure_at": "2026-04-05T12:01:00Z",
  "disabled_until": null
}
```

When a breaker trips, `disabled_until` is set to `now + 1 hour`. When it hasn't tripped, `disabled_until` is `null`.

---

## Behavior

### Before Phase 2

Check both breakers (two Firestore reads, batched):

1. Read `dream_circuit_breaker/global`
2. Read `dream_circuit_breaker/user_{userId}`

If either document has `disabled_until > now`, skip Phase 2. Log at WARNING level which breaker blocked it. Phase 1 proceeds normally.

If neither document exists yet, Phase 2 proceeds (no failures recorded).

### On Kimi Success

Reset both counters:

1. Set `dream_circuit_breaker/user_{userId}` → `{consecutive_failures: 0, disabled_until: null}`
2. Set `dream_circuit_breaker/global` → `{consecutive_failures: 0, disabled_until: null}`

### On Kimi Failure

Increment both counters atomically:

1. Increment `dream_circuit_breaker/user_{userId}.consecutive_failures`
2. Set `last_failure_at` to now
3. If `consecutive_failures >= 3`, set `disabled_until` to `now + 1 hour`
4. Same for `dream_circuit_breaker/global` with threshold of 5

### Breaker Expiry

No background job needed. The check before Phase 2 simply compares `disabled_until` against `now`. Once the time passes, Phase 2 is allowed again. The stale `disabled_until` value remains in Firestore until the next success or failure overwrites it.

---

## Integration Points

### Where It Lives

New file: `app/memory_v2/dream/circuit_breaker.py`

Three public functions:

- `check_breaker(db, user_id) -> bool` — returns True if Phase 2 is allowed
- `record_success(db, user_id)` — resets both counters
- `record_failure(db, user_id, config) -> bool` — increments counters, returns True if a breaker just tripped

### Where It's Called

In `app/memory_v2/dream/__init__.py`, inside `_run_consolidation()`:

```python
# Phase 2 (conditional)
if phase2.should_run_phase2(report, config.pattern_synthesis_threshold):
    if not circuit_breaker.check_breaker(db, user_id):
        dream_log.errors.append('Phase 2 skipped: circuit breaker open')
    else:
        # ... existing Phase 2 logic ...
        if p2_decisions is not None:
            circuit_breaker.record_success(db, user_id)
            # ... execute decisions ...
        else:
            tripped = circuit_breaker.record_failure(db, user_id, config)
            if tripped:
                logger.warning("[dream] Circuit breaker tripped for %s", user_id)
            dream_log.errors.append('Phase 2 Kimi call failed')
```

### Configuration

Already exists in `DreamConfig` (config.py:18-19):

```python
circuit_breaker_max_failures: int = 3      # per-user threshold
circuit_breaker_cooldown_hours: int = 1     # cooldown duration
```

Add one new field:

```python
circuit_breaker_global_max_failures: int = 5  # global threshold
```

---

## Logging & Monitoring

All structured via Python logging, no external alerting infrastructure.

| Event | Level | Message format |
|-------|-------|---------------|
| Phase 2 skipped (breaker open) | WARNING | `[dream] Phase 2 skipped for {user_id}: {breaker_type} circuit breaker open until {disabled_until}` |
| Breaker tripped (per-user) | WARNING | `[dream] Per-user circuit breaker tripped for {user_id}: {n} consecutive failures` |
| Breaker tripped (global) | WARNING | `[dream] Global circuit breaker tripped: {n} consecutive failures` |
| Kimi call succeeded | INFO | `[dream] Phase 2: Kimi responded ({n} chars)` (existing) |
| Kimi call failed | ERROR | `[dream] Phase 2: Kimi API call failed — {error}` (existing) |
| Counters reset on success | DEBUG | `[dream] Circuit breaker counters reset for {user_id}` |

Failure rate can be derived from these logs by counting ERROR vs INFO lines in the `[dream] Phase 2` namespace over any time window.

---

## Testing

### Unit Tests (`tests/test_dream_circuit_breaker.py`)

1. **check_breaker returns True** when no documents exist (fresh state)
2. **check_breaker returns True** when `disabled_until` is in the past (expired)
3. **check_breaker returns False** when per-user breaker is active (`disabled_until` in future)
4. **check_breaker returns False** when global breaker is active
5. **record_failure increments counter** and does not trip below threshold
6. **record_failure trips per-user breaker** at 3 consecutive failures
7. **record_failure trips global breaker** at 5 consecutive failures
8. **record_success resets** both counters and clears `disabled_until`
9. **Integration: Phase 2 skipped** when breaker is open, Phase 1 still runs
10. **Integration: breaker auto-expires** after cooldown period

---

## Edge Cases

- **Race condition on global counter:** Two workers increment simultaneously. Use Firestore's `firestore.Increment(1)` for atomic counter updates. Worst case the counter jumps by 2 in a race. This is acceptable — it may trip the breaker one failure early, which is conservative and safe.
- **Success during cooldown:** If a user manually triggers a test consolidation and Kimi succeeds, the breaker resets immediately. No need to wait out the full hour.
- **First-ever run:** No breaker documents exist. `check_breaker` treats missing documents as "no failures" and allows Phase 2.
- **Config changes:** If `circuit_breaker_max_failures` is raised from 3 to 5, existing tripped breakers with `disabled_until` set still expire naturally. The new threshold only affects future trips.

---

## Files Changed

| File | Change |
|------|--------|
| `app/memory_v2/dream/circuit_breaker.py` | New — three public functions |
| `app/memory_v2/dream/__init__.py` | Import and call circuit breaker around Phase 2 |
| `app/memory_v2/dream/config.py` | Add `circuit_breaker_global_max_failures` field |
| `tests/test_dream_circuit_breaker.py` | New — 10 test cases |
