# Changelog

## Unreleased

### Fixed — Reminders snooze for one-off reminders

Snooze was broken for one-off (non-recurring) reminders — the common case.

- Every reminder email carries "Snooze 10m / 1h / tomorrow" buttons, but for a one-off reminder the button was dead the moment the email landed: once the reminder fires it becomes `delivered`, and `_apply_snooze` only accepted `scheduled`, `sending`, or `delivered`+recurring. Clicking it returned 409 → the `wrong_state` confirmation page ("This reminder can't be snoozed in its current state").
- Fix: a `delivered` reminder is now snoozeable. Snoozing resurrects it back to `scheduled` so the worker re-fires it; for a one-off the snooze target also becomes the new `fireAt` (no future natural fire time exists). The in-app kebab now offers Snooze on delivered reminders too. Only `paused`/`failed`/`cancelled` are still rejected.
- The `reminder_snoozed` log gains a `resurrected` field.

### Fixed — Rescheduling a delivered reminder

Editing an already-fired reminder failed with "Could not save reminder: fireAtLocal must be in the future".

- The edit modal pre-filled the date/time inputs from the reminder's stored `fireAt` even when that time was already in the past (a delivered or overdue reminder). Saving then sent that past time straight back, which the PATCH route correctly rejected.
- Frontend fix: `openModal` now defaults the date/time to one hour from now whenever the reminder's `fireAt` is missing or in the past (the new-reminder default already did this). The default is computed in the reminder's own timezone so it matches what the save sends.
- Backend fix: rescheduling a `delivered` reminder to a future time now resurrects it back to `scheduled`, so the worker actually re-fires it. Without this the PATCH would succeed but the reminder would never fire. Mirrors the existing `_retry` path (failed → scheduled).

### Added — Reminders M1.3 (web push)

Browser push notifications for reminders (behind `PUSH_ENABLED=true` + VAPID keypair installed). **M1 complete** — recurrence + snooze + push all shipped.

- After the worker sends the email, it also fans out an encrypted push notification to every browser the user has subscribed.
- Two snooze action buttons ("10m", "1h") in each notification; tapping fires the M1.2 `/reminders/<id>/snooze` GET route with the same token system email links use.
- Browser support: Chrome / Firefox / Edge / Safari (macOS 13+) on desktop; Chrome / Firefox on Android. iOS Safari deferred (needs PWA install).
- Per-device subscription model at `users/{uid}/pushSubscriptions/{subId}`. 410 Gone deletes the sub automatically. 413 Payload Too Large retries once with a shorter body.
- Inline permission banner shown once after the user's first reminder. Dismissible; re-shows after 30 days if still no subscription. `pushPromptDismissedAt` field added to user doc.
- Settings → Notifications has a single ON/OFF toggle. OFF deletes all subscribed devices.
- Service worker at `/static/js/sw.js` handles `push` (renders native notification) and `notificationclick` (snooze action → backend, body → chat deeplink).
- VAPID keypair generated once per server install per `docs/ops/push-vapid-setup.md`. Manual rotation procedure documented.
- Default `PUSH_ENABLED=false` hides the banner, returns 503 from all push routes, and makes the worker fan-out a no-op. Email still delivers.

### Added — Reminders M1.2 (snooze)

Snooze shipped to xelasphere.com (behind `REMINDERS_SNOOZE_ENABLED=true`).

- Three durations: **Snooze 10 min**, **Snooze 1 hour**, **Snooze until tomorrow** (= now + 24h).
- Two surfaces sharing one backend: signed-link buttons in delivered email + Snooze items in the inline-card kebab dropdown.
- Email tokens signed with `itsdangerous.URLSafeTimedSerializer` (24h max-age, salt `reminder-snooze`).
- Worker honors `snoozedUntil`: claims the doc, sees future timestamp, releases the lock and skips. Logs `reminder_snooze_skip` per skipped tick.
- Eligible states: `scheduled`, `sending`, and `delivered`+recurring. Paused/failed/cancelled/delivered-one-off hide the option and return 409 if attempted.
- Confirmation page (`app/templates/_snoozed_confirmation.html`) handles all 6 outcomes: ok / expired / invalid / wrong_state / not_found / flag_off.
- Card subtitle replaces fire time with `snoozedUntil` and appends `(Snoozed)` until the snooze elapses.
- Default `REMINDERS_SNOOZE_ENABLED=false` hides the kebab items, omits the email link block, returns 503 from both routes.

### Added — Reminders M1.1 (recurrence)

Recurring reminders shipped on xelasphere.com (behind `REMINDERS_RECURRENCE_ENABLED=true`).

- Five recurrence presets in the edit modal: **Daily**, **Every weekday (Mon–Fri)**, **Weekly on \<day\>**, **Monthly on the \<N\>th**, **Yearly on \<Month N\>**. Dynamic labels reflect the reminder's current `fireAt`.
- Worker advances `fireAt` in the reminder's local timezone after each delivery (DST-safe via `python-dateutil`). DAILY/WEEKLY/MONTHLY/YEARLY use `relativedelta`; WEEKDAYS skips Sat/Sun. Leap-year and month-end (e.g. Jan 31 → Feb 28) fall back correctly.
- Reminder cards now show a recurrence label in the subtitle: `May 20, 09:00 · London · Daily`.
- New Firestore fields on `users/{uid}/reminders/{id}`: `recurrence`, `deliveryCount`, `lastFiredAt`, `snoozedUntil` (latter used by M1.2 snooze, not yet wired).
- Live E2E verified: DAILY test reminder fired, worker logged `reminder_recurring_advanced`, `fireAt` advanced exactly +24h, `deliveryCount=1`.
- 36 new unit + integration tests (`tests/test_recurrence.py` 16 + `tests/test_reminders_worker.py` 1 + `tests/test_reminders_routes.py` 4 + worker baseline).

M1.2 (snooze: signed-link infra + email/push/in-app surfaces) and M1.3 (web push + service worker + VAPID) are separate brainstorms — see `docs/superpowers/specs/2026-05-19-reminders-m1-design.md`.

### Added — M0 pre-P0 completion

MemPalace integration M0 milestone completed. Flask app can now be deployed alongside the mempalace sidecar with all infrastructure, observability, and rollout scaffolding in place. Default shipped state is `MEMPALACE_MODE=off` + `LEGACY_MEMORY=on`, which preserves pre-M0 chat flow behavior byte-identically (mempalace branch is untaken at mode=off; wrapping template's try/except ensures legacy path is unaffected under any mempalace exception).

**Flask-side (M0.13-M0.18):**
- `app/mempalace/feature_flag.py` — MEMPALACE_MODE + LEGACY_MEMORY parsing, startup sanity check.
- `app/mempalace/client.py` — async SidecarClient + sync SyncSidecarClient with circuit breaker.
- `app/mempalace/outbox.py` + `app/mempalace/flusher.py` — durable outbox + background flusher thread.
- `app/mempalace/redaction/` — regex-based scrubbing for API keys / PII before ingest.
- `app/mempalace/adapter.py` — stable public surface (wake_up / search / record_turn / log_shadow / delete_palace / list_drawers). Both async and sync variants for Flask sync workers per M0.14 loop-binding doctrine.
- `app/mempalace/shadow_sink.py` — non-blocking shadow-log writer (daemon thread + queue).
- `app/mempalace/wakeup.py` — provider-specific formatters (Anthropic / OpenAI / Gemini / local).
- `app/mempalace/tool.py` — `search_memory` tool schema + handler (infrastructure not yet used in chat flow; gets registered when orchestrator grows tool-calling).
- `app/mempalace/__init__.py` — get_adapter() singleton + reset_adapter() test hook + _metrics_cache for prometheus REGISTRY reuse across rebuilds.
- `app/blueprints/chat_routes.py` — M0.18 wrapping template at two memory-context sites (streaming + non-streaming). Read-path wiring (wake_up_sync + log_shadow) complete. Write-path wiring (record_turn user-half both paths + assistant-half non-streaming path). Streaming assistant-half + search_memory tool registration deferred (see M0 exit checklist known gaps).

**Deployment (M0.19):**
- `deploy/mempalace/mempalace-sidecar.service` — sidecar systemd unit.
- `deploy/mempalace/mempalace-flusher.service` — single-instance flusher systemd unit (enforces spec §3 "single flusher thread in v1" via unit-level single-PID semantics).
- `deploy/mempalace/provision.sh` — idempotent provisioning per spec §7.3.
- `deploy/mempalace/mempalace-mode` — mode-rotation script with `/admin/mempalace-mode` probe verification.
- `deploy/mempalace/{sidecar,mempalace}.env.example` — env-file templates.
- `app/mempalace/flusher_main.py` — flusher systemd-unit entrypoint.

**Observability (M0.20, M0.21):**
- `docs/ops/dashboards/mempalace-{live,rollout}.json` — Grafana skeletons with PromQL queries targeting spec §8.1 metrics. Import + tune + re-export pattern per dashboard README.
- `docs/ops/mempalace-runbook.md` — R1-R15 runbook per spec §8.3. R7/R8/R13 STUB with P0.2 issues-to-file boilerplate; R14 reserved.
- `docs/ops/logging-privacy.md` — pseudonymous-not-anonymous statement per spec §8.4.

**Validation (M0.22, M0.23, M0.23.5):**
- `tools/mempalace-rubric` — weekly 2×2 rubric harness consuming the adapter.log_shadow payload shape.
- `docs/mempalace-shadow/{README,phase-log}.md` — rubric docs + phase-progression template.
- `tools/check-legacy-memory-imports.py` — CI guard (warn mode through P0-P2; flip to block at P3 per spec §7.5). Zero hits currently.
- `.github/workflows/ci.yml` — guard wired as step 4.
- `tests/test_m023_5_smoke.py` — 8 smoke tests covering Flask test-client infra + app boot + /admin/mempalace-mode probe.
- `conftest.py` — firebase_admin.exceptions mock addition (unblocks Flask test_client).

**Exit verification — mechanical gates (M0.24):**
- `docs/ops/mempalace-m0-exit-checklist.md` — human-facing pre-P0 checklist + known-gaps acknowledgment.
- `tools/check-m0-exit.sh` — 26 automated checks for the repo-level exit gates.
- Status at M0.24 commit: 26/26 mechanical checks passing. Remaining M0 work = evidence items (sidecar pytest, STUB issues) + deployment-host checks.

**Exit verification — evidence + pin (M0.25):**
- GitHub issues filed for runbook STUBs R7/R8/R13 ([#2](https://github.com/xelauvas/xelasphere.io-/issues/2), [#3](https://github.com/xelauvas/xelasphere.io-/issues/3), [#4](https://github.com/xelauvas/xelasphere.io-/issues/4)), all targeting P1 entry. Runbook text updated to cross-reference.
- Sidecar pytest run against a fresh venv from `mempalace-sidecar/pyproject.toml[dev]`: **118 passed, 1 skipped**. Skip is `test_install_signal_handlers_wires_sigterm_sigint` — permanently skipped per M0.10 close decision (uvicorn owns signal handling via `capture_signals()`; duplicate in-process handlers would risk double invocation).
- `mempalace-sidecar/requirements.lock` committed — full dependency tree from the venv that passed tests, so staging-host install resolves to the same chromadb and transitive versions.
- Remaining M0 gate: 8 deployment-environment checks on a staging host (systemd unit starts, `/readyz`, `mempalace-mode` rotation, Grafana dashboard imports). Not completable from a dev workstation; your-turn work.

### Spec amendments

- `docs/superpowers/specs/2026-04-20-mempalace-integration-design.md`:
  - §8.1 metrics catalog: added `duplicate_turn_id` reason on `inserts_dropped_total`; added `shadow_dropped_total` + `shadow_writer_error_total` with reason-label taxonomies.
  - §7.4 phase-gate table + rationale: M0.23.5 chat-flow integration-coverage gate named at M0.18, re-examined and closed at Phase 2 via structural argument (mempalace branch untaken at mode=off + wrapping-template try/except).

### Known gaps at P0 entry (not blocking, documented)

See `docs/ops/mempalace-m0-exit-checklist.md § Known gaps`:
1. 8 pre-existing `tests/` failures predate M0; unrelated to mempalace.
2. Streaming assistant-half record_turn deferred (orphan pair_ids tolerated per spec §4.4).
3. `search_memory` tool registration deferred (requires LLM tool-calling infrastructure build-out).
4. Shadow-mode behavior testing is a P1-entry requirement, not a pre-P1 gate.
5. CI import guard in warn mode; block mode at P3.
6. Dashboards are skeletons; polish at deployment-time import.
7. Runbook R14 reserved (no alert currently targets it).

### Rollout phases ahead

- **P0** Dark deploy (3 days, mode=off + legacy=on). Entry when M0 exit-checklist deployment-environment items clear on the staging host (M0.25 closed the repo-side evidence items).
- **P1** Shadow writes (14 days).
- **P2** Shadow reads (21 days).
- **P3** Active, legacy standby (60 days).
- **P4** Legacy removed.

Phase-gating in `docs/mempalace-shadow/phase-log.md` + spec §7.4.
