# Phase log

Running log of mempalace rollout phase progression. Per spec §7.4, entering P3 requires written approval committed here; post-P4 proceeds by written approval as well.

Template for each phase entry below; fill in as phases progress.

---

## P0 — Dark deploy

- Entered: `<YYYY-MM-DD>`
- Config: `MEMPALACE_MODE=off`, `LEGACY_MEMORY=on`
- Entry check: sidecar running, `/readyz` OK, zero app-side errors.
- Exit criterion: no mempalace-attributed errors in 3 days.
- Approver: Unilateral.
- Exit verified: `<YYYY-MM-DD, link to logs / dashboard screenshot>`

---

## P1 — Shadow writes

- Entered: `<YYYY-MM-DD>`
- Config: `MEMPALACE_MODE=shadow`, `LEGACY_MEMORY=on`
- Entry check: P0 clean. (The M0.23.5 gate added 2026-04-22 was re-examined and closed at Phase 2 — see spec §7.4 "M0.23.5 re-examination.")
- **P1 entry testing requirement (separate from M0.23.5):** before flipping any user to shadow mode, author tests against actual shadow-mode semantics. Specifically:
  - `log_shadow` payload shape matches the M0.22 shadow-rubric harness's parser expectations (keys: ts, user_id, query, legacy, mempalace).
  - `record_turn` is called for both user + assistant halves with a shared pair_id per spec §3 line 122.
  - `wake_up_sync` is called with the right args and its result is threaded correctly (though not injected in shadow mode — only logged).
  - Streaming path's user-half `record_turn` fires at the expected point in the SSE flow. (Streaming assistant-half is separately deferred per M0.18-finish commit `af7549a`; P1 may surface that as its own item.)
  Design the tests against actual shadow-mode behavior as it ships, not pre-imagined behavior.
- Exit criterion: outbox steady ≤1k rows p95; ingest success ≥99.5%; shadow log populated; disk growth within forecast; single-user rebuild benchmark measured.
- Approver: Unilateral.
- Exit verified: `<YYYY-MM-DD, link to data>`

---

## P2 — Shadow reads

- Entered: `<YYYY-MM-DD>`
- Config: `MEMPALACE_MODE=shadow`, `LEGACY_MEMORY=on`
- Entry check: P1 clean.
- Exit criterion: weekly rubric `mempalace_only_hit ≥ v3_only_hit` for 3 consecutive weeks AND `(both + mempalace_only) ≥ 85%` (see `docs/mempalace-shadow/YYYY-WW.md` files).
- Approver: Unilateral with rubric math attached.
- Weekly rubric tally files: `<list of YYYY-WW.md files and the 3-week rolling state>`
- Exit verified: `<YYYY-MM-DD, attach rubric math>`

---

## P3 — Active, legacy standby

- Entered: `<YYYY-MM-DD>`
- Config: `MEMPALACE_MODE=active`, `LEGACY_MEMORY=on`
- Entry check: P2 flip criterion met + **written approval here** (this section).
- Exit criterion: zero rollbacks; hot p95 ≤150ms sustained; day-30 and day-55 rubric re-run confirms `mempalace_only ≥ v3_only` at both checkpoints; two monthly capacity reviews.
- Approver: Written approval, committed to this phase log.
- **Approval text (required before P3 entry):**
  ```
  <paste approver name + date + the rubric math summary that justified P3 entry>
  ```
- Day-30 rubric re-run: `<link to YYYY-WW.md>`
- Day-55 rubric re-run: `<link to YYYY-WW.md>`
- Capacity reviews: `<links to review commits>`
- Exit verified: `<YYYY-MM-DD>`

---

## P4 — Legacy removed

- Entered: `<YYYY-MM-DD>`
- Config: `MEMPALACE_MODE=active`, `LEGACY_MEMORY` deleted from code + env
- Entry check: P3 clean.
- P4 PR: `<link to the PR that removes memory_v2 + memory_v3 directories, the LEGACY_MEMORY flag, the call-site guards, AND the both-off boot refusal in app/__init__.py per spec §3 "Post-P4">`
- Post-P4 discipline: `MEMPALACE_MODE=off` must boot (outage relief valve); verify via boot test after the P4 PR lands.
- Approver: Written approval committed here.
- **Approval text (required before P4 PR merge):**
  ```
  <approver name + date>
  ```

---

## Rollback log

Per spec §7.4, rollbacks are via `mempalace-mode {off|shadow|active}`. Target <60s wall time. Record each rollback event here:

- `<YYYY-MM-DD HH:MM:SS UTC>` — rolled back from `<from_mode>` to `<to_mode>`. Reason: `<runbook R-number + brief>`. Recovery action: `<what was done>`. Re-entered `<mode>` at `<time>`.
