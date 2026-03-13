# Memory V2 Policy Document

> Internal reference for engineers working on the memory system.
> Last updated: 2025-01-11 | Policy version: v2.1.0

---

## 1. What Can Be Stored

| Type | Examples | Confidence Threshold | TTL |
|------|----------|---------------------|-----|
| **Profile** | Name, timezone, location, role | 0.85+ (or explicit) | Permanent |
| **Preference** | Response style, code format, language | 0.70+ | Permanent (decays after 180d) |
| **Project** | Current work, tech stack, goals | 0.65+ | 90 days inactive |
| **Constraint** | Allergies, "no emoji", safety rules | 0.90+ | Permanent |
| **Temporary** | Working context, task state | 0.60+ | 24 hours |

### Confidence Requirements
- **Profile facts** (name, timezone, location): Require 0.85+ confidence OR explicit statement ("My timezone is...")
- **High-stakes keys** on new devices: Require confirmation prompt
- **Provisional items**: Stored at lower confidence, expire in 14 days unless confirmed

---

## 2. What Must NEVER Be Stored

### Hard Blocks (Enforced by Code)
- Anything from `evidence.source != "user"` (assistant text, system prompts)
- Values containing injection patterns (see `INJECTION_PATTERNS` in store.py)
- Constraint keys not in `ALLOWED_CONSTRAINT_KEYS`
- Values exceeding length limits (280 chars for profile/preference)

### Policy Blocks (Enforce via Review)
- Passwords, API keys, credentials
- Full credit card numbers, SSNs
- Health information beyond allergies/dietary restrictions
- Information about third parties without consent
- Anything the user says "off the record" or "don't remember"

### Opt-Out Triggers
These phrases block ALL extraction for the message:
- "don't save this", "don't remember this"
- "off the record"
- "forget what I just said"
- "this is just between us"

---

## 3. Confidence Thresholds by Type

```
Profile:     0.85  (high-stakes, permanent)
Preference:  0.70  (medium-stakes, decays)
Project:     0.65  (context-dependent)
Constraint:  0.90  (safety-critical)
Temporary:   0.60  (short-lived)
```

### When to Store as Provisional
- Confidence below threshold but above 0.55
- Inferred (not explicitly stated)
- First mention of a fact

### When to Promote Provisional → Active
- User restates the fact 2+ times (`repeated_by_user >= 2`)
- User explicitly confirms ("yes", "correct", "that's right")
- **NOT** based on retrieval/usage count

---

## 4. Handling User Requests

### "Forget X" / "Delete my memory about X"
1. Find matching memory by key
2. Set `status = DELETED` (soft delete)
3. After 30 days, hard delete via cleanup job
4. Log audit event with reason

### "Don't remember anything" / "Disable memory"
1. Set `MemoryFeatureFlags.disable_for_user(user_id)`
2. Existing memories remain but are not retrieved
3. No new memories are stored
4. User can re-enable later

### "Show me what you remember"
1. Return formatted list with:
   - Key, value, confidence
   - When stored, last used
   - "Why" explanation
   - Fix commands ("Say: forget X")

### "Export my data"
1. Call `store.get_all(include_expired=True)`
2. Redact audit logs if needed
3. Return JSON with all memory items

---

## 5. When Memory is Wrong

### Low-Confidence Action Check
When memory will be used for an action AND confidence < 0.75:
```
Instead of: "I've scheduled your meeting for 9am London time."
Ask: "Is that 9am your local time (London)?"
```

### User Correction Flow
1. User says "That's wrong" or "I'm not in London"
2. Old memory is superseded (not deleted)
3. New memory created with higher confidence
4. Supersede chain maintained for audit

### New Device Confirmation
When `session.is_new_device == True` and memory is high-stakes profile:
```
Ask: "I have your timezone as Europe/London. Is that still correct?"
```

---

## 6. Operational Procedures

### Daily
- Run `run_scheduled_cleanup(user_id)` for active users
- Monitor `correction_rate` SLO (alert if > 2%)
- Check `quarantine_rate` for spikes

### Weekly
- Review shadow mode logs (if enabled)
- Sample 100 stored memories for quality
- Update injection patterns if needed

### On Incident
1. Enable kill switch: `MemoryFeatureFlags.global_disable_writes()`
2. Investigate via audit logs
3. Run quarantine scan if poisoning suspected
4. Re-enable with shadow mode first

---

## 7. Code Integration Rules

### DO
```python
from app.memory_v2 import MemoryOrchestrator

orchestrator = MemoryOrchestrator(user_id, session)
context = orchestrator.get_context(user_message, state)
result = orchestrator.post_turn(user_message, assistant_message, state, context.eval_metadata)
```

### DON'T
```python
# NEVER do this - bypasses safety gates
from app.memory_v2.store import MemoryStoreV2
store = MemoryStoreV2(user_id)
store.save(memory)  # NO! Use orchestrator

# NEVER extract from assistant messages
extractor.extract(assistant_message)  # NO! Only user_message
```

---

## 8. Version History

| Version | Date | Changes |
|---------|------|---------|
| v1.0.0 | - | Initial memory system |
| v2.0.0 | - | Confirmation-based promotion, quarantine, safeguards |
| v2.1.0 | 2025-01-11 | Orchestrator, SLOs, shadow mode, PII redaction |

---

## Quick Reference

```
Promotion: repeated_by_user >= 2 OR explicit_confirmed (NOT usage)
Source: ONLY "user" (never "assistant" or "system")
Retrieval: ACTIVE or PROVISIONAL, not expired, not quarantined
Pinned: Must be ACTIVE status, never auto-deleted
Supersede: Bidirectional links, old status = SUPERSEDED
Rate Limit: 3/message, 10/day
Value Length: 280 chars (1000 for projects)
```
