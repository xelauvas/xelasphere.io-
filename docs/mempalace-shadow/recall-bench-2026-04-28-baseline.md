# Mempalace recall-bench — 2026-04-29

Source: `/var/log/mempalace/shadow/shadow-2026-04-28.jsonl`

Pass criteria per persona:
- `recall_l2_or_l3` ≥ 0.80
- `crosstopic_l2_l3_avg` ≤ 0.40

## Summary

| Persona | Recall hits (l2∨l3) | Cross-topic l2/l3 noise | Verdict |
|---|---|---|---|
| Marathoner training for Boston 2027 | 100% (4/4) | 70% | recall OK · crosstopic FAIL |
| Backend engineer building a private reading tracker | 75% (4/4) | 70% | recall FAIL · crosstopic FAIL |
| Illustrator pitching a children's book | 67% (3/3) | n/a (no tests) | recall FAIL |

## Marathoner training for Boston 2027 (`rRThYRtGkIW93Jv9Pn...`)

Recall queries matched: 4/4 | l2∨l3 hit rate: **100%**

| Test | Legacy | l0 | l1 | l2 | l3 |
|---|---|---|---|---|---|
| target time | ✗ | ✗ | ✗ | ✓ | ✓ |
| foot issue | ✗ | ✗ | ✗ | ✓ | ✓ |
| coach name | ✗ | ✓ | ✓ | ✓ | ✓ |
| long-run pivot | ✗ | ✗ | ✗ | ✓ | ✓ |
| **HIT RATE** | 0/4 | 1/4 | 1/4 | 4/4 | 4/4 |

Cross-topic queries (lower = cleaner):

| Test | l0 | l1 | l2 | l3 |
|---|---|---|---|---|
| book rec | 2/5 | 2/4 | 4/5 | 4/5 |
| NYC coffee | 1/5 | 1/5 | 3/5 | 3/5 |

## Backend engineer building a private reading tracker (`m6xGfa7pULePm7kbxx...`)

Recall queries matched: 4/4 | l2∨l3 hit rate: **75%**

| Test | Legacy | l0 | l1 | l2 | l3 |
|---|---|---|---|---|---|
| deadline | ✗ | ✓ | ✗ | ✓ | ✓ |
| beta user | ✓ | ✓ | ✗ | ✗ | ✗ |
| deploy box | ✗ | ✗ | ✗ | ✓ | ✓ |
| launch date | ✗ | ✗ | ✗ | ✓ | ✓ |
| **HIT RATE** | 1/4 | 2/4 | 0/4 | 3/4 | 3/4 |

Cross-topic queries (lower = cleaner):

| Test | l0 | l1 | l2 | l3 |
|---|---|---|---|---|
| book rec | 5/5 | 4/4 | 4/5 | 4/5 |
| time of day | 4/5 | 4/5 | 3/5 | 3/5 |

## Illustrator pitching a children's book (`UNsBoTIjafPvQj17ly...`)

Recall queries matched: 3/3 | l2∨l3 hit rate: **67%**

| Test | Legacy | l0 | l1 | l2 | l3 |
|---|---|---|---|---|---|
| character name | ✗ | ✓ | ✗ | ✓ | ✓ |
| pitch date | ✗ | ✓ | ✗ | ✗ | ✗ |
| pitch venue | ✗ | ✓ | ✓ | ✓ | ✓ |
| **HIT RATE** | 0/3 | 3/3 | 1/3 | 2/3 | 2/3 |

