# MemPalace shadow rubric

Weekly 2×2 quality rubric for comparing mempalace output vs legacy (memory_v3) output during P1-P3 rollout.

## What this is

During P1 (Shadow writes) through P2 (Shadow reads), both the mempalace system and the legacy memory_v3 system run in parallel on live traffic. The legacy system's output is what the user actually sees; the mempalace system's output is logged-but-not-shown. The shadow rubric compares them manually, weekly, to answer: "does mempalace retrieve context that is at least as good as legacy?"

Rater convention (spec §6): single rater (Xela, v1). Web UI deferred until >1 rater.

## How the rubric works

Each sample is one shadow-log record — a pair of (legacy_result, mempalace_result) for the same user query. The rater reviews both and assigns one of four outcomes:

|                    | v3 retrieved relevant | v3 missed |
| ------------------ | --------------------: | --------: |
| mempalace relevant |            `both_hit` | `mempalace_only` |
| mempalace missed   |             `v3_only` | `both_miss` |

Plus `skip` for samples where the rater can't determine relevance (insufficient context, ambiguous query, rater fatigue).

## How to run

```
./tools/mempalace-rubric
```

With defaults: samples 20 random records from `/var/log/mempalace/shadow/`, prompts for each, writes the tally to `docs/mempalace-shadow/YYYY-WW.md` (ISO week).

Options:

- `--log-dir PATH` — shadow log directory (default `/var/log/mempalace/shadow`).
- `--sample N` — number of records to sample (default 20).
- `--output DIR` — output directory for weekly tally (default `docs/mempalace-shadow/`).

## Phase gates this feeds

**P2 exit criterion (spec §7.4):**

- `mempalace_only_hit >= v3_only_hit` for 3 consecutive weeks
- AND `(both_hit + mempalace_only) >= 85%` of rated samples

**P3 mid-phase checkpoints:**

- Day-30 rubric re-run must confirm `mempalace_only >= v3_only`
- Day-55 rubric re-run must confirm the same

The weekly tally file records one week's data. `docs/mempalace-shadow/phase-log.md` is the operator's running log across weeks, including phase-progression decisions and written approvals.

## Interpreting a weekly tally

Each `YYYY-WW.md` output file lists:

- `mempalace_only_ge_v3_only: true` → this week meets the per-week threshold of the P2 criterion. Three consecutive such weeks plus 85% coverage → P2 can exit.
- `hit_coverage (both_hit + mempalace_only) / rated: N/M = P%` → the 85% figure in the P2 criterion. Falling below 85% doesn't disqualify the week's `mempalace_only_ge_v3_only` flag but does block P2 exit regardless.
- `Skipped: N` — skipped samples don't count toward rated; high skip rates suggest sample size should increase or rating protocol needs tightening.

## Phase progression

Entering P3 requires written approval committed to `docs/mempalace-shadow/phase-log.md`. Don't phase-progress without the written record — the phase log is the audit trail if post-P3 rollbacks happen and someone asks "what was the evidence at P2 exit?"
