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

## Bench result — addendum 2026-04-29 08:30 UTC

**Decision: HOLD in shadow.** The validation probes were sent ~01:12 UTC; the bench harness ran against the resulting shadow records. Both criteria failed.

| Metric | Result | Threshold | Verdict |
|---|---|---|---|
| Marcus recall l2∨l3 | 0% (1/4 — only target-time probe ran) | ≥80% | FAIL |
| Marcus crosstopic l2/l3 avg | 60% (book-rec only — NYC-coffee not run) | ≤40% | FAIL |

The pre-flip theoretical analysis — "noise scored 0.17-0.21, real hits 0.33+, floor is 0.25, therefore crosstopic ~0% post-fix" — was wrong on observation. Calibration breakdown:

| Tier-result band | Theoretical (pre-flip) | Observed (bench) |
|---|---|---|
| Real recall hits | 0.33 – 0.46 | 0.32 – 0.46 ✓ |
| Cross-topic noise | 0.17 – 0.21 | **0.34 – 0.42** ✗ |

Three root causes, in priority order:

1. **Pre-deploy hedge contamination at retrieval.** PR #103 filters hedges at write time but doesn't retroactively clean already-indexed entries. Marcus's seed run was 2026-04-28 23:48 — ~80 minutes before PR #103 deployed at 01:04 UTC. Pre-deploy hedges sit in the palace at similarities 0.705 and 0.509 (well above the 0.25 floor) and outrank real establishment turns. **Fixed by PR #107 (read-time hedge filter).** Verified post-deploy via direct sidecar probe: short pre-deploy hedges no longer surface; one longer hedge variant (~280 chars, 0.71) survives the length gate by design.

2. **Score floor 0.25 underestimates project bleed.** Cross-topic project context (Boston Marathon training, Elena/marathoner-partner, low-carb running diet) scores 0.34-0.42 on completely off-topic queries (book recommendations). The "real hits 0.33+" / "noise 0.17-0.21" calibration assumed noise is semantically unrelated; in practice, *any* item from a strong topical wing scores ≥ 0.33 even on unrelated queries. **Open: needs higher floor or query-context-aware filtering.**

3. **Question-vs-answer ranking inversion + retrieval-depth truncation.** Past identical/near-identical questions and "Full recap: ..." prompts score higher than the answer turns that contain the data. On the recall probe, the question itself returned at 0.993 and 0.907; the actual establishment turn ("sub-3:00 goal") scored 0.316 — above the 0.25 floor but at chroma's internal rank 4-5. With `k=5` sidecar fetch, two of those top-5 slots are short hedges that PR #107 now filters out — but the slots aren't backfilled from rank 6+, so the final result is truncated to 3 items and `sub-3:00` never surfaces. **Open: needs Q→A pair-aware retrieval or buffer-fetch (retrieve k×N, filter, trim to k).**

## Decision: HOLD until calendar week-3 (2026-05-18)

- Mode stays `shadow`. PR #106 stays open as the record of what was tried.
- The "validate then flip" framing was a reasonable bet that didn't pay out. The original spec §7.4 calendar floor is the right gate after all — three weeks of soak makes the rubric statistically meaningful, and the integration earns the slope of weekly observation rather than a single-user inference.
- PR #107 (read-time hedge filter) is the structural fix from this round and ships even though the flip doesn't.
- Follow-up issues filed for items 2 and 3 above. Issue #24 (rebuild benchmark) still applies at week 3.
