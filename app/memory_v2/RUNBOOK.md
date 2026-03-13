# Memory V2 Operations Runbook

> Use this during rollout and incidents. Keep it short.

---

## Quick Reference

```python
# Kill switches
from app.memory_v2 import MemoryFeatureFlags, ShadowMode

MemoryFeatureFlags.global_disable_writes()  # Emergency stop writes
MemoryFeatureFlags.global_disable_reads()   # Emergency stop reads (rare)
MemoryFeatureFlags.disable_for_user(user_id, "reason")  # Per-user disable

ShadowMode.enable()   # Simulate writes without committing
ShadowMode.analyze_shadow_log()  # Review what would have been stored
```

---

## Rollout Phases

| Phase | Duration | Config | Success Criteria |
|-------|----------|--------|------------------|
| Shadow | 3-7 days | reads=ON, writes=OFF, shadow=ON | No creepy items in top 50 |
| 1% | 48-72h | writes=ON for 1% | SLOs stable |
| 10% | 48-72h | writes=ON for 10% | SLOs stable |
| 25% | 48-72h | writes=ON for 25% | SLOs stable |
| 50% | 48-72h | writes=ON for 50% | SLOs stable |
| 100% | ongoing | Full rollout | Weekly review |

---

## SLO Thresholds

| Metric | Warning | Critical | Action |
|--------|---------|----------|--------|
| correction_rate | > 1% | > 2% | Disable writes, investigate |
| quarantine_rate | > 0.1% | > 1% | Check for attack wave |
| write_rate_per_1k | > 3x baseline | > 5x baseline | Check extractor |
| staleness_rate | > 20% | > 30% | Run maintenance job |
| cost_per_message | > 1.5x baseline | > 2x baseline | Switch to rules-only |

### Statistical Significance

Before rolling back on correction_rate:
- Minimum N: 5,000 sessions
- p-value < 0.01 (2-proportion z-test)
- Absolute delta > 0.5%

All three must be true. Don't rollback on noise.

---

## Red Button Conditions

### 1. Correction Rate Spike
```
IF correction_rate > 2% AND statistically_significant:
    → Disable writes
    → Keep reads ON
    → Analyze shadow logs
    → Identify top 3 problem keys
```

### 2. Quarantine Rate Spike
```
IF quarantine_rate spikes suddenly:
    → Likely attack wave or new jailbreak pattern
    → Disable writes
    → Keep reads ON
    → Update INJECTION_PATTERNS config
    → Run quarantine scan on existing data
```

### 3. Cost Spike
```
IF cost_per_message > threshold for 30 min:
    → Switch judge to rules-only mode
    → OR skip extraction for low-risk turns
```

---

## Incident Response Protocol

1. **Disable writes globally**
   ```python
   MemoryFeatureFlags.global_disable_writes()
   ```

2. **Keep reads ON** (unless retrieval bug suspected)

3. **Export diagnostics**
   - Last 24h shadow logs
   - Last 24h audit diffs
   - Top memory keys by write volume

4. **Identify root cause**
   - Top 3 memory keys causing issues
   - Check policy_version in logs
   - Compare against baseline cohort

5. **Patch and re-enter shadow**
   - Update config/thresholds
   - Enable shadow mode for 24h
   - Verify fix in shadow logs

6. **Ramp again**
   - Start at 1% cohort
   - Follow normal rollout phases

---

## Week 1 Success Criteria

After full rollout, these are green lights:

- [ ] correction_rate flat or down vs baseline
- [ ] opt-out frequency low and stable
- [ ] write_rate_per_1k within expected band
- [ ] quarantine_rate stable (small)
- [ ] staleness trending down after maintenance
- [ ] No increase in re-ask / follow-up loops

---

## Baseline Matching

When comparing metrics, ensure cohorts match on:
- Geographic region
- Language
- Device type (mobile/desktop)
- Traffic source
- Time of day/week

Mismatched cohorts cause false alarms.

---

## Log Queries

```
# Find all memory operations for a user
memory_pipeline="v2" AND user_id="xxx"

# Find correction events
action="user_correction" AND policy_version="v2.1.0"

# Find quarantine events
action="quarantine" AND timestamp > now-24h

# Find high-volume memory keys
GROUP BY memory_key ORDER BY count DESC LIMIT 20
```

---

## Contacts

| Role | Responsibility |
|------|----------------|
| On-call | First response, kill switch authority |
| Memory owner | Root cause analysis, config changes |
| Security | Quarantine spikes, attack patterns |

---

## Daily Ops (First 2 Weeks)

```python
# Run daily
from app.memory_v2 import run_full_maintenance, check_slo_alerts, get_memory_health_stats

# For each active user (or sample)
stats = run_full_maintenance(user_id)

# Check alerts
metrics = get_memory_health_stats(user_id)
alerts = check_slo_alerts(metrics)
if alerts:
    notify_oncall(alerts)
```

---

## Privacy Checklist

- [ ] Evidence snippets never logged outside protected stores
- [ ] Debug panel requires elevated access
- [ ] Log retention defined (14-30 days)
- [ ] PII redaction active in audit logs
- [ ] "Forget" command does hard delete after 30 days
