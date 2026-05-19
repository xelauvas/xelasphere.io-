# Reminders — M1.2 Design Spec (Snooze)

**Date:** 2026-05-19
**Status:** Approved (brainstorming complete; ready for implementation plan)
**Parent:** [Reminders M1 Design Spec](./2026-05-19-reminders-m1-design.md) — bundled M1 = push + recurrence + snooze. M1.1 (recurrence) shipped in PR [#177](https://github.com/xelauvas/xelasphere.io-/pull/177).
**Problem:** A reminder fires at exactly the wrong moment. The user is mid-meeting, on a phone call, eating dinner. M0+M1.1 give them no way to push it 10 minutes without opening the chat. Email recipients have it even worse — no way to defer without a round-trip through the app.

---

## Summary

M1.2 wires the snooze behavior the M1 spec defined. Three durations, two surfaces, one backend.

- **Surfaces:** email signed-link (works without login) + in-app card kebab submenu (session auth). Push action snooze ships with M1.3.
- **Durations:** 10 minutes, 1 hour, Tomorrow (= now + 24 hours). All measured from click time, not from the original fireAt.
- **Backend:** `POST /reminders/<id>/snooze` (session auth) and `GET /reminders/<id>/snooze` (signed-token auth from emails). Both set `snoozedUntil` identically.
- **Data:** `snoozedUntil` field already exists on every reminder doc (initialized in M1.1). M1.2 just reads + writes it. No schema change.
- **Worker:** new effective-fire-at gate. After claiming the doc but before sending, the worker checks `snoozedUntil`. If it's in the future, release the lock and skip this tick.
- **Eligibility:** only `scheduled`, `sending`, or `delivered+recurring` reminders can be snoozed. Paused/failed/cancelled/delivered-one-off hide the option and reject the route.
- **Kill switch:** `REMINDERS_SNOOZE_ENABLED` (default off) gates the routes, email link block, and in-app dropdown entry.

Token lifetime is 24 hours from email send via `itsdangerous.URLSafeTimedSerializer`. Re-clicking the same link inside that window is idempotent in effect (last click wins).

---

## Architecture overview

```
M1.1 already shipped                       M1.2 adds
────────────────────────                   ─────────────────────────────────
snoozedUntil field exists                  + app/services/signed_links.py
worker query: status=scheduled             + worker computes effective_fire_at
inline card kebab dropdown                 + 'Snooze' submenu in kebab
HTML + plain text emails                   + snooze link block in both bodies
                                           + POST /reminders/<id>/snooze (session)
                                           + GET  /reminders/<id>/snooze?token=... (email)
                                           + _snoozed_confirmation.html template
                                           + REMINDERS_SNOOZE_ENABLED kill switch
```

### New files / units

- `app/services/signed_links.py` — `make_snooze_token(rid, uid)` + `verify_snooze_token(token)`. Thin wrapper over `itsdangerous.URLSafeTimedSerializer` with salt `"reminder-snooze"`. 24-hour max-age. Token payload: `{"rid": id, "uid": uid}`. Verify raises `SignatureExpired` or `BadSignature`; callers translate to HTTP status.
- `app/templates/_snoozed_confirmation.html` — Self-contained HTML page (inline CSS, no JS) rendered after a successful or failed snooze link click. Branches on status passed from the route.
- `tests/test_signed_links.py` — token round-trip, expiry, tampered, wrong-uid.

### Modified files

- `app/blueprints/reminders_routes.py` — Two new handlers:
  - `POST /reminders/<id>/snooze` — session auth, JSON body `{"dur": "10m"|"1h"|"tomorrow"}`. Returns updated reminder JSON. Uses the existing `@login_required` + CSRF middleware.
  - `GET /reminders/<id>/snooze` — accepts `?dur=...&token=...` query params, no session required. Renders `_snoozed_confirmation.html`. Both handlers share `_apply_snooze(reminder_id, dur, user_id) -> dict` that does the actual state check + Firestore write.
- `tools/reminders_worker.py` — Two changes:
  - Email builders (`_build_email_body`, `_build_email_html`) get a new snooze link block (3 links/buttons), each with a fresh token minted at send time. Kill-switch-gated.
  - `process_reminder`: after claiming the doc (`scheduled → sending`), check `snoozedUntil`. If in the future, release the lock (`lockedAt = None`), log `reminder_snooze_skip`, return. Otherwise proceed with normal delivery. The existing recurring-advance branch (M1.1) already clears `snoozedUntil` on success.
- `app/static/js/remindersModule.js` — Add Snooze submenu to the existing kebab dropdown. Visible only on `scheduled`, `sending`, or `delivered+recurring`. Three submenu items POST to `/reminders/<id>/snooze` and call `applyReminderToCards` on the response. Card subtitle shows `… (Snoozed)` when `snoozedUntil > now`.
- `app/config.py` — Add `REMINDERS_SNOOZE_ENABLED` flag.
- `tests/test_reminders_routes.py` — Add `TestSnoozeRoute` covering POST/GET happy paths, invalid duration, expired/tampered token, wrong-state rejection, ownership mismatch.
- `tests/test_reminders_worker.py` — Add tests for the effective-fire-at skip and the snooze-link inclusion in email bodies.

### Reuse / extend

- `itsdangerous` is already a transitive dep via Flask; importable.
- `Config.APP_BASE_URL = https://xelasphere.com` already in prod env; the spec uses it for token-link URLs.
- Existing `login_required` + CSRF decorators apply to the POST handler.
- Email builders already exist; just append a block.
- The kebab dropdown already exists from M0 polish; just add a submenu.
- Existing structured-log pattern (`extra={"extra_fields": {...}}`) for new event names.

---

## Data model

**No schema changes.** The reminder doc already includes:

```jsonc
{
  // ... all M0 + M1.1 fields ...
  "snoozedUntil": null   // ISO-8601 UTC string OR null
}
```

M1.1 initialized this field to `null` on every new reminder and clears it after a successful recurring delivery. M1.2 just adds the writers (snooze route handlers) and the reader (worker effective-fire-at check).

### Effective fire-at semantics

The worker's hot query is unchanged from M0/M1.1:

```python
db.collection_group("reminders")
  .where("status", "==", "scheduled")
  .where("fireAt", "<=", now_iso)
  .order_by("fireAt")
  .limit(100)
```

This intentionally still matches snoozed reminders (status stays `scheduled` while snoozed, fireAt is unchanged in M1.1). The "is it actually due?" check moves to `process_reminder`, after the transactional claim:

```python
def process_reminder(doc_ref, reminder, *, user_email):
    # ... existing claim logic: scheduled → sending, lockedAt=now ...
    snoozed_until = reminder.get("snoozedUntil")
    if snoozed_until:
        snoozed_dt = datetime.fromisoformat(snoozed_until.replace("Z", "+00:00"))
        if snoozed_dt.tzinfo is None:
            snoozed_dt = snoozed_dt.replace(tzinfo=timezone.utc)
        if snoozed_dt > datetime.now(timezone.utc):
            # Snoozed past now — release the claim, skip this tick.
            doc_ref.update({
                "status": "scheduled",
                "lockedAt": None,
                "updatedAt": now_iso,
            })
            logger.info("reminder_snooze_skip",
                extra={"extra_fields": {"name": reminder.get("name"), "until": snoozed_until}})
            return
    # ... existing SendGrid call + recurring advance logic ...
```

Why claim-then-skip (rather than filter at the query): Firestore doesn't allow disjunctive `WHERE` clauses cleanly (`snoozedUntil IS NULL OR snoozedUntil <= now` needs two queries or a client-side filter). Claim-then-skip is simpler and the cost (one Firestore round-trip per snoozed doc) is negligible at our scale.

### snoozedUntil lifecycle

| Trigger | What happens to snoozedUntil |
|---|---|
| Reminder created | `null` (M1.1 init) |
| User snoozes (any surface) | Set to `now + duration` (UTC ISO) |
| Worker tick when `snoozedUntil > now` | Unchanged; worker releases claim and skips |
| Worker tick when `snoozedUntil <= now` | Worker delivers; on success path, set to `null` |
| Recurring reminder advance | Already `null` after delivery (M1.1) |
| User clicks snooze again before fire | Overwritten with new `now + duration` |

---

## End-to-end flows

### Flow A — User clicks "Snooze 1 hour" in delivered email

```
1. Reminder fires at 09:00. Worker delivers email.
   _build_email_html / _build_email_body include a snooze block:

   Subject: Reminder: Standup
   Body: ...reminder content...

      Need more time?
      [ Snooze 10 min ]  [ Snooze 1 hour ]  [ Snooze until tomorrow ]

   Each button:
      href = https://xelasphere.com/reminders/{id}/snooze?dur=1h&token={t}
      token = URLSafeTimedSerializer(SECRET, salt="reminder-snooze").dumps({"rid": id, "uid": uid})
      Validity = 24 hours from email send.

2. At 09:05 user clicks "Snooze 1 hour":
   GET /reminders/{id}/snooze?dur=1h&token=<signed>

3. Route handler:
   a. Kill switch off → 503 + page "Snooze isn't available right now."
   b. verify_snooze_token(token) → {rid, uid}.
      - SignatureExpired → 410 + "link expired" page.
      - BadSignature     → 400 + "invalid link" page.
   c. URL's id ≠ token.rid → 400 (defense in depth).
   d. Fetch reminder doc.
      - Not found → 404 + "Reminder not found".
      - reminder.userId ≠ token.uid → 403 (cross-account guard).
   e. Validate state:
      - scheduled, sending, or (delivered AND recurrence != null) → proceed.
      - paused, failed, cancelled, delivered-one-off → 409 + state-specific page.
   f. Compute snoozedUntil = now + timedelta(...) per dur.
   g. doc_ref.update({"snoozedUntil": snoozedUntil_iso, "updatedAt": now_iso}).
   h. Log reminder_snoozed {rid, source: "email", duration: "1h", until}.
   i. Render _snoozed_confirmation.html with the formatted local time string and a link to /c/{sourceChatId}.

4. Next worker tick at 09:06:
   fetch_due_reminders matches the doc (status=scheduled, fireAt=09:00<=now).
   process_reminder claims it. snoozedUntil=10:05 > now=09:06 → release lock, skip.
   This repeats every minute until 10:05.

5. Worker tick at 10:05:
   process_reminder claims it. snoozedUntil=10:05 <= now=10:05 → proceed with delivery.
   On success: snoozedUntil cleared (recurring advance path) or doc becomes terminal delivered.
```

### Flow B — User clicks "Snooze 10m" in the in-app kebab dropdown

```
1. Kebab dropdown built by remindersModule.js. For a scheduled or
   delivered+recurring reminder, shows:
      Edit details
      Pause / Resume
    ▶ Snooze ───┐
      Delete    │
                └─→ 10 minutes
                    1 hour
                    Tomorrow

2. Click "10 minutes":
   POST /reminders/{id}/snooze
   Body: {"dur": "10m"}
   Headers: X-CSRFToken, Cookie (session)

3. Route handler:
   a. Kill switch off → 503 JSON {"error": "snooze disabled"}.
   b. @login_required + CSRF (existing middleware).
   c. Fetch reminder doc; ownership = session.user.uid == reminder.userId else 403.
   d. Validate state (same as Flow A step e). 409 JSON if not allowed.
   e. Compute snoozedUntil + write (same as Flow A step f-g).
   f. Log reminder_snoozed {source: "app", ...}.
   g. Return 200 {"reminder": <updated doc>}.

4. Frontend:
   On 200, call applyReminderToCards(data.reminder). Card subtitle updates to show
   the new effective time + "(Snoozed)" suffix.

5. Worker behavior identical to Flow A step 4-5.
```

### Flow C — User clicks expired link (>24h after email send)

```
1. GET /reminders/{id}/snooze?dur=1h&token=<old>

2. verify_snooze_token raises SignatureExpired.

3. Route returns 410 Gone, renders _snoozed_confirmation.html with:
      "This snooze link has expired. Open the reminder in chat to reschedule."
   + [ Open chat ] link (if sourceChatId can be looked up without the token; if not, link to /).

4. No Firestore write; no log of reminder_snoozed (just reminder_snooze_rejected with reason).
```

---

## UX

### Kebab dropdown — Snooze submenu

```
Inline-card kebab dropdown (and schedules-tab row kebab):

   Edit details
   Pause / Resume         ← scheduled/sending only
 ▶ Snooze  ──┐             ← scheduled / sending / delivered+recurring only
   Delete    │
             └─→  10 minutes
                  1 hour
                  Tomorrow at this time
```

- Click "Snooze" to open the submenu (no hover-trigger — mobile-safe).
- Snooze entry hidden when:
  - status is paused / failed / cancelled
  - status is delivered AND recurrence is null (terminal one-off)
- After a successful in-app snooze, the dropdown closes and the card subtitle updates.

### Card subtitle

```
Before snooze:   May 20, 09:00 · London · Daily
After snooze:    May 20, 10:00 · London · Daily   (Snoozed)
```

`formatWhen` extends: if `reminder.snoozedUntil` is set AND in the future, display the snoozed time + append ` (Snoozed)`. When snoozedUntil clears post-delivery, the next-fire reverts to natural fireAt.

### Email — Snooze link block

Inserted between the body and the signature line. HTML version uses inline-styled buttons matching the existing "Open chat" CTA color (`#10A37F`):

```
   Open chat: https://xelasphere.com/c/...
   
   ──────────────
   Need more time?
   [ Snooze 10 min ]   [ Snooze 1 hour ]   [ Snooze until tomorrow ]

   — XelaSphere Reminders · Scheduled for 09:00 London
```

Plain-text version uses bullets with raw URLs:

```
   Need more time?
   - Snooze 10 min:         https://xelasphere.com/reminders/{id}/snooze?dur=10m&token=...
   - Snooze 1 hour:         https://xelasphere.com/reminders/{id}/snooze?dur=1h&token=...
   - Snooze until tomorrow: https://xelasphere.com/reminders/{id}/snooze?dur=tomorrow&token=...
```

When `REMINDERS_SNOOZE_ENABLED=false`, the block is omitted from both bodies entirely.

### Confirmation page (`_snoozed_confirmation.html`)

```
   ┌──────────────────────────────────────────┐
   │                                          │
   │  XelaSphere Reminders                    │
   │                                          │
   │  ✓ Snoozed                               │
   │  We'll remind you at 10:05 London time.  │
   │                                          │
   │  [ Open chat ]                           │
   │                                          │
   └──────────────────────────────────────────┘
```

Self-contained HTML with inline CSS. Branches on status (passed from the route):

| Status | Heading | Body |
|---|---|---|
| `ok` | ✓ Snoozed | "We'll remind you at {time} {tz}." |
| `expired` | Link expired | "This snooze link has expired. Open the reminder in chat to reschedule." |
| `invalid` | Invalid link | "This link looks invalid. Open the reminder in chat to reschedule." |
| `wrong_state` | Can't snooze | "This reminder can't be snoozed — it's been {state}. Open chat to manage." |
| `not_found` | Reminder not found | "We couldn't find this reminder. It may have been deleted." |
| `flag_off` | Unavailable | "Snooze isn't available right now. Open chat to reschedule." |

The "Open chat" button goes to `/c/{sourceChatId}` if available (route handler looks it up before erroring if possible), else `/`.

---

## Failure, retry, dedup, observability

### Token edge cases

| Case | Handling |
|---|---|
| Token expired (>24h) | `SignatureExpired` → 410 + "link expired" page |
| Token tampered / wrong signature | `BadSignature` → 400 + "invalid link" page |
| Token `rid` doesn't match URL `id` | 400 + "invalid link" page |
| Token `uid` doesn't match `reminder.userId` | 403 + "invalid link" page |
| Same link clicked twice (Gmail prefetch + user) | Both succeed; second overwrites snoozedUntil. Last-click-wins by design |
| Mail-safety scanner prefetch | Snoozes the reminder. Acceptable: worst case is a 10-min unintended delay; user can re-snooze |

### State transition edge cases

| Case | Handling |
|---|---|
| Snooze on `scheduled` | Set snoozedUntil; worker skips until then. Standard path |
| Snooze on `sending` (race: snoozing as worker claims) | Token route's read sees `sending`; route accepts (sending is eligible). Set snoozedUntil. Worker's in-flight delivery completes regardless; the next occurrence (if recurring) honors the new snooze. For one-off, becomes `delivered` and snoozedUntil is moot |
| Snooze on `delivered+recurring` | Set snoozedUntil. M1.1's recurring-advance branch already set fireAt to the next natural occurrence; effective-fire-at = max(snoozedUntil, fireAt) when the worker next picks it up. Snooze wins if it's later |
| Snooze on `paused`/`failed`/`cancelled`/`delivered-one-off` | 409 + state-specific page. UI hides the option entirely so this only reaches the server via stale email links |
| Two rapid snoozes (5 seconds apart) | Last-wins. Both writes succeed; the second value wins. Acceptable |

### Worker edge cases

| Case | Handling |
|---|---|
| Worker claims `scheduled → sending`, sees `snoozedUntil > now` | Update `status=scheduled, lockedAt=null`; log `reminder_snooze_skip`; return. Next tick re-evaluates |
| Stuck-lock sweep on a snoozed doc | Same M0 logic: 5-min stale-lock detection reverts `sending → scheduled`. The next process_reminder re-checks snoozedUntil. No special handling needed |
| Snooze made the worker query miss the doc | Can't happen — query is `fireAt <= now`, snoozedUntil isn't in the index. Doc is still matched; skip logic catches it |

### Observability

New structured log events (greppable via `journalctl -u xelasphere-reminders*`):

```
reminder_snoozed          {rid, source: "email"|"app", duration: "10m"|"1h"|"tomorrow", until, uid}
reminder_snooze_skip      {rid, name, until, now}                # worker skipped a snoozed doc
reminder_snooze_rejected  {rid, reason: "token_expired"|"token_invalid"|
                           "wrong_state"|"ownership"|"flag_off"|"not_found"}
```

No new dashboards. Existing M0 pattern.

### Scale assumption

Same as M0/M1.1: ≤1,000 active users, ≤100 due reminders per tick. Snooze adds at most one extra `update()` per snoozed-skipped tick (negligible).

---

## Auth, scope, timezone

### Auth

- `POST /reminders/<id>/snooze` — session-authenticated, CSRF-protected (existing middleware).
- `GET /reminders/<id>/snooze` — signed-token authenticated. No session required. The token encodes `uid` and `rid`; the handler verifies both before any state mutation.
- Both share `_apply_snooze(reminder_id, dur, user_id) -> (status, payload)` internal helper. The two route handlers only differ in how they extract `user_id` (session vs token).

### Scope

- All snooze writes scoped to `reminder.userId`. Token-mismatched UIDs reject with 403 before any read of the doc.
- Tokens are minted per-send (not stored). Server can validate without lookup.

### Timezone

- The confirmation page renders the snooze target in the reminder's local timezone, not UTC. e.g., "We'll remind you at 10:05 London time."
- "Tomorrow" duration is `now + 24 hours` UTC. Wall-clock interpretation: same time of day tomorrow (approximately, ignoring DST shifts inside the 24h window — acceptable for an "rough later" affordance).

### Rate limiting

- `POST /reminders/<id>/snooze` — 20/min per user (same as PATCH).
- `GET /reminders/<id>/snooze` — 60/min per IP. Higher because legitimate retries from email previews + scanners are normal.

---

## Testing & rollout

### Test coverage

- **Unit (`signed_links.py`):** sign-then-verify round trip; expired token raises SignatureExpired; tampered token raises BadSignature; wrong-uid mismatch detected by caller helper.
- **Unit (`_apply_snooze` helper):** valid states proceed; invalid states return 409; valid durations map to correct timedeltas; "tomorrow" = now + 24h exactly.
- **Unit (worker `process_reminder` snooze skip):** mocked reminder with `snoozedUntil = now + 10m` → asserts `update({"status": "scheduled", "lockedAt": null, ...})` is called and no SendGrid send happens; mocked reminder with `snoozedUntil = now - 1m` → asserts SendGrid send happens.
- **Integration (routes):**
  - POST happy path → 200 + Firestore updated
  - POST with `"dur": "banana"` → 400
  - POST on paused → 409
  - POST with flag off → 503
  - GET with valid token → 200 + HTML page
  - GET with expired token → 410 + HTML page
  - GET with token from different uid → 403 + HTML page
- **Integration (email builders):**
  - `_build_email_html` with snooze flag on includes 3 anchor tags with `/reminders/<id>/snooze?dur=...&token=...` hrefs
  - With flag off, the snooze block is absent
- **E2E (manual):**
  - Snooze via email link: create one-off reminder for 2 min from now, wait for delivery, click "Snooze 10 min" in email, verify worker logs `reminder_snooze_skip` for 10 minutes, then delivers
  - Snooze via in-app dropdown: open kebab on a scheduled reminder, pick "Snooze 1 hour", verify card subtitle updates and worker honors the new time

### Rollout — single stage

M1.2 is small enough for a single rollout stage (not the 5-stage M0 pattern):

1. Land PR with `REMINDERS_SNOOZE_ENABLED=false`.
2. Flip on for own account. Test both surfaces end-to-end.
3. Watch logs 24h for `reminder_snooze_rejected` events. Any rejections of type `token_invalid` would indicate a token bug — fix before declaring done.
4. Declare M1.2 done.

### Kill switch

`REMINDERS_SNOOZE_ENABLED=false` causes:
- `POST /reminders/<id>/snooze` → 503 JSON `{"error": "snooze disabled"}`
- `GET /reminders/<id>/snooze` → 503 + page "Snooze isn't available right now"
- Email builders omit the snooze block from both bodies
- In-app dropdown omits the Snooze submenu (frontend reads `window.RemindersModule.config.snoozeEnabled` injected at page render)

### Definition of done

- At least one snooze via email signed-link successfully delays a fire (`reminder_snooze_skip` log + delivery at the new time)
- At least one snooze via in-app dropdown does the same
- Zero `reminder_snooze_rejected` events with reason `token_invalid` in 24h
- Worker p99 still < 5s
- All kill-switch states verified (flag off → no link, no dropdown entry, route 503; flag on → all present)

---

## Explicitly out of scope for M1.2

| Item | Why deferred | Target |
|---|---|---|
| Push notification action snooze | Push infra not built yet | M1.3 |
| Per-user custom snooze durations | Defaults fine for now | M5+ |
| Custom "snooze until time X" picker | Tomorrow is the natural escape hatch | M5+ |
| "Snooze entire recurring series" (vs this occurrence) | Per-occurrence is the safer default; series-shift would be confusing UX | M5+ |
| Snooze on cancelled/failed reminders | Use Retry/Recreate instead | won't fix |
| Email-side undo ("oops snoozed by accident") | Just re-snooze with a different duration | won't fix |
| Snooze beyond Tomorrow (e.g., next week, next month) | Edit modal supports rescheduling for distant times | M5+ |

---

## Open questions (deferred, not blockers)

- **Mail-safety scanner prefetch.** Google's link-safety scanner may prefetch snooze URLs and unintentionally snooze the reminder. Mitigation chosen: accept the risk — worst case is a 10-min unintended delay, easily recovered by another click. Alternative (double-click confirmation page) considered and rejected for friction reasons. Document for revisit if reports come in.
- **Confirmation-page tracking.** Should the confirmation page emit a `page_view` log so we can see snooze-link click-through rates? M1.2 does not — only the route handler logs. Easy to add later.
- **Token rotation if secret leaks.** Tokens are signed with `Config.SECRET_KEY`. If that ever rotates, all in-flight email tokens become invalid (existing recipients see "link expired"). Acceptable — they can use the in-app surface.

---

## Related future milestones (out of M1.2)

- **M1.3 — Web push + push-action snooze.** Service worker, VAPID, pywebpush. Push notification has snooze action buttons that POST to the same `/reminders/<id>/snooze` route (token from payload).
- **M2 — SMS via Twilio.** SMS snooze via short-code reply ("S10" for 10 min) is a stretch; phone number capture comes first.
- **M3 — Google Calendar sync.** Snoozing a synced reminder would push the calendar event's start time; needs OAuth scope.
- **M5+ — Snooze depth.** Custom durations, occurrence-vs-series shift, picker UI.
