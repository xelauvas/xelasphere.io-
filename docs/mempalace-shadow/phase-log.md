# Phase log

Running log of mempalace rollout phase progression. Per spec §7.4, entering P3 requires written approval committed here; post-P4 proceeds by written approval as well.

Template for each phase entry below; fill in as phases progress.

---

## M0 complete → P0 entry

- Date: 2026-04-22
- Automated checks: **42/42 passing** (commit `5eeec9a`, `tools/check-m0-exit.sh`). Count grew 26 → 42 at M0.27 when § 8 was rewritten as a bidirectional template-vs-code cross-check after the M0.26 deploy attempt surfaced systemic config drift.
- Deployment checks: 7 planned, **5 verified-green**, **1 narrowed-and-deferred** (flusher functional drain → P1 per M0.26b amendment), **1 pass-with-note** (Flask-as-root DAC bypass path verified at M0.27c; mempalace-group path deferred to post-FHS-migration). See narrative below.
- Sidecar checks: green. **118 passed + 1 skipped** (pre-existing `install_signal_handlers` — tombstoned at issue #5) in the sidecar venv. Version 0.1.0 (now read from `importlib.metadata` rather than the previous duplicate literal in `main.py`).
- P1-entry issues filed: **#2** (R7), **#3** (R8), **#4** (R13) from M0.25; **#6** (Flask FHS migration) filed at M0.26; **#7** (socket mode 0666 tombstone), **#8** (containerized deploy-smoke-test as CI) filed at M0.27.
- **Entering P0 dark deploy**: this commit's timestamp (`git log -1 --format=%ci docs/mempalace-shadow/phase-log.md`). P0 exit target: commit-timestamp + 3 days.

### Deployment-check narrative (2026-04-22)

Seven checks attempted on the production Hetzner host (snapshot **#379196442** taken pre-provision, intact — no prod state invalidated through the session):

1. **provision.sh clean.** After three repo-side commits (M0.26a deploy-artifact fixes, M0.26b scope correction, M0.27 config-drift audit + check-script hardening) plus three follow-up commits (ExecStartPre order, sidecar `HOME=` for chromadb, Step 8 post-check Flask-user-aware), `provision.sh` completed cleanly with all required env fields populated.
2. **Sidecar unit active.** `/readyz` returns `{"ok":true,"embedding_loaded":true,"db_ok":true,"audit_ok":true,"schema_version":2}` within 30s of start. UDS socket present at `/run/mempalace/sidecar.sock` owned `mempalace:mempalace`. Mode is `0666` (uvicorn default) rather than the spec-expected `0660` — tombstoned at #7 for P1 entry; functionally moot under current Flask-as-root DAC bypass.
3. **Flusher unit.** Narrowed at M0.26b to "unit enabled + is-active active + zero restarts in 60s." Unit enabled; not started. Blocked by #6 (Flask FHS migration) — flusher's `User=xelasphere` cannot traverse `/root` mode `0700` to reach the Flask venv. Documented as P1-entry gate at `docs/ops/mempalace-p1-entry-checklist.md` § "Flusher functional drain."
4. **`/readyz` < 30s.** Met on first successful start; sub-second after full unit warmup.
5. **Gunicorn-reaches-UDS-after-restart.** Pass-with-note. `provision.sh` Step 8 post-check was spec-predicated on Flask running as `User=xelasphere` (in group `mempalace`) and used group membership as the UDS-access mechanism. M0.27c made the post-check Flask-user-aware: under `User=root` (current deployment), pass-with-note because root's DAC bypass grants UDS access regardless of group membership; under `User=xelasphere` (post-FHS), require `mempalace` in supplementary groups. The check verifies the mechanism that matches the deployment, not a fixed mechanism that only happens to match one shape.
6. **`mempalace-mode` round-trip.** `mempalace-mode off` → Flask restart → `/admin/mempalace-mode` probe verified `"mode":"off"` via the env-file-driven `${XELASPHERE_PORT}` (M0.26a fix; previously hardcoded `:8080` which never matched Flask's actual `:5000` bind).
7. **Dashboard JSON imports.** `mempalace-live.json` and `mempalace-rollout.json` provisioned into Grafana via the file-based provisioning path (`/var/lib/grafana/dashboards/`; provider `Xela Dashboards`, folder `Xela`). Both dashboards registered in Grafana 12.4.2's unified-storage `resource` table within the scan interval; no provisioning errors in `journalctl -u grafana-server`. Panel-render verification deferred per M0-exit checklist framing ("even if 'No data' for metrics not yet firing"); panel-datasource errors will surface at P1 when metrics start firing, which is the correct time to iterate on JSON.

### Deploy-time drift classes surfaced this session

M0.27's commit body named three. The full deploy attempt surfaced **six**:

1. Unit file vs tmpfs lifecycle (closed by M0.26a `RuntimeDirectory=`).
2. Deploy-script vs environment reality — wheel presence + `mempalace-mode` port probe (closed by M0.26a).
3. Template vs code contract — `MEMPALACE_PALACES_DIR` vs `data_dir`, missing salt generation, partial provision.sh env writes (closed by M0.27 template rewrite + check-script § 8 hardening).
4. Shell-semantics drift — `ExecStartPre=/bin/rm -rf /dir/` with trailing slash removed the just-created directory (closed by M0.27 follow-up #1).
5. Implicit-environment-assumption drift — chromadb's HOME-derived cache path assumes HOME is writable, but `useradd -r` leaves HOME pointing at unwritable `/home/<user>` (closed by M0.27 follow-up #2 via `CacheDirectory=mempalace` + `Environment=HOME=`).
6. Deploy-script post-check vs current deployment shape — Step 8 group-membership check assumed Flask-as-xelasphere, returned false-negative under Flask-as-root (closed by M0.27c).

Classes 4, 5, and 6 are categorically hard for a static check script to catch — they manifest only when the unit actually runs on a real host with a specific user/environment shape. `tools/check-m0-exit.sh § 8` hardening (M0.27) catches (3) fully and partially catches (2). **Containerized deploy-smoke-test as a CI step** — runs `provision.sh` against a clean `debian:stable` container and asserts `/readyz` response — would catch all six drift classes at PR time. Tracked at issue **#8** with label `p1-entry`; natural scheduling window is P1-entry alongside #6 and #7 since all three require a wheel rebuild.

### Rollback path (live through P0)

Snapshot **#379196442** (Hetzner, taken 2026-04-22 pre-provision) remains intact. No sidecar restart or code change has occurred after the final successful `/readyz` — the snapshot represents a valid pre-mempalace-integration rollback point through the entire P0 window. If anything goes wrong in the next 3 days that `mempalace-mode off` + Flask restart cannot resolve, rollback is a Hetzner console click, not a debug session. Delete the snapshot after P0 exit once the 3-day clean window is confirmed.

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
