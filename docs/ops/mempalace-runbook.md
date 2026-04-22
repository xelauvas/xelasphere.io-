# MemPalace runbook

Operator-facing notes for the mempalace integration. Growing as milestones ship operational behaviors.

## Orphan pair_ids are expected (M0.18)

A `pair_id` is generated before the LLM call in the orchestrator. If the LLM call fails or the assistant turn is never written to the outbox, the palace will contain a user-half drawer with a `pair_id` that has no assistant companion. This is **not** a bug — it's an accurate record of the conversation state.

Orphans are filterable:

```sql
SELECT pair_id FROM drawers GROUP BY pair_id HAVING COUNT(*) = 1
```

No auto-cleanup. If operators want to distinguish orphans-from-failures vs. orphans-from-streaming-assistant-half-not-wired-yet, note that M0.18 shipped streaming user-half record_turn but deferred streaming assistant-half to a follow-up commit. Until that follow-up lands, streaming-path turns will systematically produce orphans in shadow/active modes. At mode=off (default) no drawers get written regardless.
