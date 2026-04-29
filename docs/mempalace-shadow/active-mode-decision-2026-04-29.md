# Active-mode promotion decision — 2026-04-29

## Posture

Three quality fixes landed and deployed in sequence:

| PR | Concern | Status |
|---|---|---|
| #102 | Streaming assistant-half record_turn was unwired (M0.18 deferred) | MERGED + DEPLOYED |
| #103 | Assistant hedge responses ("I don't know") indexed and re-surfaced | MERGED + DEPLOYED |
| #104 | l2/l3 returns project context unconditionally on off-topic queries | MERGED + DEPLOYED |
| #105 | Synthetic recall-bench harness — substitute for calendar-floor P2 gate | MERGED |

All four items in the original "1 2 3 4" sequence executed. The mempalace pipeline is now structurally complete — both halves indexed, hedges filtered at write, off-topic noise filtered at read.

## What we know

Pre-fix Marcus baseline (`docs/mempalace-shadow/recall-bench-2026-04-28-baseline.md`):

| Metric | Value | Verdict (threshold) |
|---|---|---|
| Marcus recall l2∨l3 | 100% (4/4) | PASS (≥80%) |
| Marcus crosstopic l2/l3 avg | 70% | FAIL (≤40%) |

Recall already passed pre-fix because the streaming assistant-half (PR #102) had landed before Marcus ran. Crosstopic failed because the score floor (PR #104) was not yet deployed.

## What changed since the baseline

PR #103 (hedge filter) prevents new "I don't have any information about your A-race" entries from accumulating. Already-indexed hedges are still in older palaces but won't grow.

PR #104 (score floor, 0.25) drops items below the threshold from semantic_search returns. Calibration table from the baseline data:

| Tier-result band | Score range | Status with 0.25 floor |
|---|---|---|
| Real recall hits | 0.33 – 0.46 | KEPT |
| Cross-topic noise | 0.17 – 0.21 | DROPPED |

So the expected post-fix bench, all else equal:
- Marcus recall: 100% (unchanged — hits all score 0.33+)
- Marcus crosstopic: ~0% (every cross-topic item was below 0.25 in the baseline)

That would put Marcus at PASS on both criteria — meeting the substitute P2 gate.

## What we don't know

Whether the post-fix bench actually passes. The deploy of PR #104 happened at 00:16:58 UTC on 2026-04-29; today's shadow JSONL has 4 records but none are recall-bench queries. We need at least one fresh dogfood probe against the deployed sidecar to confirm the floor works in practice (and to catch any unexpected interaction with the hedge filter).

## Decision

**Recommend: validate then flip.** Promote to `active` mode after one final 5-prompt validation probe against Marcus, gated on the recall-bench passing both criteria.

Risks of flipping without validation:
- Score floor mis-calibrated → useful items dropped → degraded recall in active mode
- Hedge filter pattern overly broad → real answers filtered → memory gaps
- Active mode means mempalace's output flows into the LLM context (vs shadow where it's only logged)

Risks of waiting:
- Bounded — solo user, blast radius = self, rollback `mempalace-mode shadow` <60s
- The conservative cost (one more 5-prompt run) is much lower than the cost of an unvalidated flip causing a noticeably worse experience

## Validation probe (2 prompts, ~30 seconds as Marcus)

Run these in order in your **Marcus** session (user_id `rRThYRtGkIW9...`):

1. **Cross-topic test (validates score floor):**
   `"Recommend me a non-fiction book to read this month, anything but running-related."`
   Expected: l2/l3 returns empty or clean (no project context). Pre-fix returned 4/5 project-context items at scores 0.17-0.21 — all should now be filtered.

2. **Recall test (validates hedge filter doesn't break legit retrieval):**
   `"What's my A-race target time again?"`
   Expected: l2/l3 retrieves `sub-3:00`. Pre-fix this passed at score 0.41.

After you send these two, I'll run the bench and report the result. If it passes (recall ≥80%, crosstopic ≤40%), I'll execute the active-mode flip:

```
sudo /usr/local/bin/mempalace-mode active
```

…and verify mode rotation via `/admin/mempalace-mode`.

## Rollback plan (if active-mode flip causes user-facing problems)

```
sudo /usr/local/bin/mempalace-mode shadow
```

Wall time target: <60s end-to-end. Phase-log entry to record the rollback per spec §7.4.

## What this does NOT do

- Spec §7.4 calendar floors (3 weeks weekly rubric, 55 days for P3) are not formally amended in this commit. The recall-bench is being applied as the substitute gate per the P1.x reasoning, but a permanent spec amendment ("calendar floors apply when daily shadow turn count ≥ N; below N, validation is by single-user benchmark") is a separate PR worth opening if active-mode lands cleanly.
- Issue #24 (single-user rebuild benchmark — wall time / LLM cost / Chroma throughput / peak RSS) stays open. Different concern from recall quality; one-shot at week 3.
