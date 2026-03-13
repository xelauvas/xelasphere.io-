# V3 Rollout Runbook

**Status**: Ship-grade
**Last Updated**: 2026-01-12

---

## Phase 0 — Shadow

### Config
```bash
MEMORY_V3_ENABLED=true
V3_ROLLOUT_PHASE=shadow
V3_ROLLOUT_PERCENT=0
V3_INTENSITY=0.6  # doesn't matter much in shadow, but keep it set
```

### Daily Checks

#### Signal Distribution Sanity
- `context_mode` shouldn't be 90% one bucket
- `pace`: rushed/normal/deep should look believable
- `certainty`: frustrated should be relatively low

#### Anti-Gaming
- Triggers rate should be low and stable
- If it spikes → you're being gamed or patterns are too sensitive

#### Shadow "Would Inject" Rate
- If nearly 0% → detector is too conservative
- If near 100% → detector is too aggressive

### Exit Criteria
- `check_shadow_exit_criteria()` passes
- No obvious misclassification clusters in the "top 50 shadow logs"

---

## Phase 1 — 1% Injection

### Config
```bash
V3_ROLLOUT_PHASE=1pct
V3_ROLLOUT_PERCENT=1
V3_INTENSITY=0.6
```

### Compare Treatment vs Control (Same Period)

Watch these deltas:

| Metric | Good Direction |
|--------|----------------|
| `follow_up_turns` | ↓ |
| `re_ask_count` | ↓ |
| `conversation_turns` | ↓ or same |
| `anti_gaming_triggers` | not ↑ sharply |
| correction-like messages | not ↑ |

*Correction-like: "not what I meant", "no", "wrong"*

### Exit Criteria
- `check_injection_exit_criteria()` passes for 48–72 hours
- No red button triggers

### Intensity Tuning
- If responses feel too **chatty** → drop intensity to `0.5`
- If responses feel too **robotic/terse** → raise to `0.7`
- Keep changes small: **±0.1**

---

## Phase 2 — Ramp

### Config
```bash
V3_ROLLOUT_PHASE=ramp
V3_ROLLOUT_PERCENT=10  # then 25 → 50 → 100
```

### Schedule
Hold each step **48–72 hours**:
1. 10%
2. 25%
3. 50%
4. 100%

### What You Want to See
- Improvements persist
- Costs stable or slightly down (often down for rushed/execute)
- No drift in signal distributions

---

## Phase 3 — Full

### Config
```bash
V3_ROLLOUT_PHASE=full
V3_ROLLOUT_PERCENT=100
```

### Weekly Review
- Staleness symptoms (does it "stick" to wrong modes?)
- Anti-gaming health
- Top failure cases (misclassified context/state)

---

## Red Buttons

Keep them simple. Know these by heart.

### Confusion Spike
**Signal**: Correction-like messages spike significantly
**Action**:
```bash
V3_ROLLOUT_PERCENT=0  # or MEMORY_V3_ENABLED=false
```

### Attack Spike
**Signal**: Anti-gaming triggers spike
**Action**:
```bash
V3_INTENSITY=0.3  # context-only, no behavior hints
# or stop injection entirely
```

### Cost Spike
**Signal**: Token/cost per conversation jumps
**Action**:
```bash
V3_INTENSITY=0.5  # reduce hint verbosity
# or fall back to shadow
```

---

## Early Tuning Rules

**Don't touch rules too much during rollout.**

Only tune:
1. `V3_INTENSITY`
2. A couple of thresholds for rushed/frustrated if clearly off

Everything else should wait until you have real data.

---

## Quick Reference

### Environment Variables
| Variable | Values | Default |
|----------|--------|---------|
| `MEMORY_V3_ENABLED` | true/false | true |
| `V3_ROLLOUT_PHASE` | shadow/1pct/ramp/full | shadow |
| `V3_ROLLOUT_PERCENT` | 0-100 | 0 |
| `V3_INTENSITY` | 0.0-1.0 | 0.6 |

### Intensity Scale
| Value | Effect |
|-------|--------|
| 0.0 | No hints (empty block) |
| 0.2 | Context info only |
| 0.5 | + Verbosity/tone hints |
| 0.6 | + Clarifying/alternatives (default) |
| 0.7 | + Reassuring/tradeoffs |
| 1.0 | All hints active |

### Key Functions
```python
from app.memory_v3 import (
    should_inject_v3,      # Check if user gets V3
    get_v3_cohort,         # Returns "treatment" or "control"
    get_rollout_manager,   # Access metrics
    check_shadow_exit_criteria,
    check_injection_exit_criteria,
)

# Get current metrics
manager = get_rollout_manager()
metrics = manager.get_metrics()
manager.log_metrics()  # Logs to stdout
```

### KPIs to Track
1. **Turns to task completion** - avg turns until user stops asking follow-ups
2. **Re-ask rate** - same question within N minutes

Both should go **down** with V3 working correctly.
