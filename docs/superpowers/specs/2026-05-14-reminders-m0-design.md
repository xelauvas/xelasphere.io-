# Reminders — M0 Design Spec

**Date:** 2026-05-14
**Status:** Approved (brainstorming complete; ready for implementation plan)
**Problem:** XelaSphere users cannot ask the assistant to nudge them later. ChatGPT and Claude both ship a "set a reminder" feature that turns a chat utterance into a scheduled email + in-app notification; XelaSphere has no equivalent.

---

## Summary

M0 ships the smallest end-to-end loop:

- User says "remind me to X tomorrow at 9am" in chat
- Server detects the intent (regex pre-filter), extracts structured fields via a cheap LLM call
- A reminder is persisted to Firestore and rendered as an inline card in the assistant's reply
- User can click the card to edit/pause/delete in a modal that matches ChatGPT's shape
- A systemd-timer-driven Python poller picks up due reminders every 60 seconds and sends an email via SendGrid
- All reminders are also manageable from the existing settings → Schedules tab (currently "Coming Soon")

M0 is **email-only**, **English-only intent detection**, **one-off (no recurrence)**, **login-required**. Web push, SMS, Google Calendar sync, contacts attachment, and recurrence are explicit follow-up milestones (M1–M4).

---

## Architecture overview

```
┌─────────────────────────────────────────────────────────────────┐
│  Flask app (Gunicorn, existing)                                  │
│                                                                  │
│  chat_routes ──▶ is_reminder_request(prompt)  ── regex pre-filter│
│       │             │                                            │
│       │             └─▶ extract_reminder()    ── 2nd LLM call,   │
│       │                    returns JSON         JSON-mode, cheap │
│       │                                          model (Groq)    │
│       │                                                          │
│       └──▶ reminders_routes.py (new blueprint)                   │
│              GET/POST/PATCH/DELETE /reminders                    │
│              + writes Firestore: users/{uid}/reminders/{id}      │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼  fireAt (UTC), status, channels
                  ┌──────────────────────────┐
                  │ Firestore                │
                  │ users/{uid}/reminders/   │
                  └──────────────────────────┘
                              ▲
                              │ poll every 60s for due
                              │
┌─────────────────────────────────────────────────────────────────┐
│  reminders_worker.py (new CLI, run by systemd timer)             │
│   1. collection_group('reminders').where(status=scheduled,       │
│      fireAt<=now).limit(100)                                     │
│   2. for each due reminder:                                      │
│        - transaction: status='scheduled' → 'sending' + lockedAt  │
│        - send via SendGrid                                       │
│        - transaction: 'sending' → 'delivered' (or back to        │
│          'scheduled' with attemptCount++, or 'failed')           │
│   3. exit                                                        │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
                       SendGrid API ──▶ user's inbox
```

### New files / units

- `app/blueprints/reminders_routes.py` — REST CRUD (mirrors `email_routes.py` shape)
- `app/services/reminder_intent.py` — `is_reminder_request()` + `extract_reminder()`
- `app/services/email_sender.py` — thin SendGrid wrapper (reusable for future transactional mail: verification, password reset)
- `tools/reminders_worker.py` — CLI poller, idempotent, exits in <5s typical
- `deploy/xelasphere-reminders.service` + `deploy/xelasphere-reminders.timer` — systemd units, `OnCalendar=*:0/1` (every minute)
- `app/static/js/remindersModule.js` — inline card render + edit modal + Schedules tab list
- `app/static/css/reminders.css` — card + modal styles
- `firestore.rules` updates — owner-only read/write on `users/{uid}/reminders/{rid}`
- Composite index on `reminders` collection-group: `(status ASC, fireAt ASC)`

### Reuse / extend

- Firestore pattern from `email_routes.py`: `users/{uid}/email_drafts/{id}` → `users/{uid}/reminders/{id}`
- `login_required` + `csrf_required` decorators from `app/blueprints/shared.py`
- Settings modal "Schedules" tab placeholder in `_settings_modal.html` (currently `<p class="settings-pane-empty">Coming Soon.</p>`) → becomes the management UI
- Existing `limiter` (Flask-Limiter) for rate-limiting POST /reminders
- Stream-renderer sentinel-tag mechanism already used for artifacts → new `<reminder id="..." />` tag

---

## Data model

**Firestore collection:** `users/{uid}/reminders/{reminderId}`

```jsonc
{
  // Identity
  "id": "rem_abc123",                 // also the doc ID
  "userId": "uid_xxx",                // denormalized for collection-group queries

  // Content
  "name": "Remind customer to pay accommodation £184.41",
  "instructions": "Collect £184.41 accommodation payment from customer.",
  // ^ Optional richer body; AI fills from extract_reminder().
  //   Rendered as the email body. Falls back to `name` if empty.

  // Timing
  "fireAt": "2026-05-15T08:00:00Z",   // UTC, ISO-8601; worker indexes on this field
  "timezone": "Europe/London",        // IANA tz used to compute fireAt; shown in UI
  "createdAt": "2026-05-14T22:31:00Z",
  "updatedAt": "2026-05-14T22:31:00Z",

  // Lifecycle
  "status": "scheduled",              // scheduled | sending | paused | delivered | failed | cancelled
  "deliveredAt": null,                // set when status flips to delivered
  "attemptCount": 0,                  // incremented on every send attempt
  "lastError": null,                  // last SendGrid error string, for debugging
  "lockedAt": null,                   // set when worker flips to 'sending'; sweep clears if stale

  // Provenance
  "sourceChatId": "chat_xxx",         // chat where AI created it (nullable: manual create is null)
  "sourceMessageId": "msg_xxx",       // the assistant message that owns the inline card (nullable)
  "createdBy": "ai",                  // "ai" | "user"

  // Channels (just email in M0, but field is plural so M1/M2 don't migrate)
  "channels": ["email"]
}
```

**User doc additions** (`users/{uid}`):

```jsonc
{
  "defaultTimezone": "Europe/London"  // IANA, captured from browser on first reminder create
}
```

**Indexes:**

- Collection-group on `reminders`: `(status ASC, fireAt ASC)` — worker's hot query
- Per-user on `users/{uid}/reminders`: `(status ASC, fireAt DESC)` — Schedules tab "Upcoming" list

**Worker query (the hot path, runs every minute):**

```python
db.collection_group('reminders') \
  .where('status', '==', 'scheduled') \
  .where('fireAt', '<=', now_utc) \
  .order_by('fireAt') \
  .limit(100) \
  .stream()
```

Page size 100 is a soft cap — if more than 100 are due in one tick, the next tick picks the rest up.

### Status state machine

```
                          ┌──────────────────┐
            create ──────▶│    scheduled     │◀──── retry (worker; attemptCount<3)
                          └──────────────────┘
                            │            │
                  user pause│            │worker lock
                            ▼            ▼
                       ┌────────┐   ┌─────────┐
                       │ paused │   │ sending │──── send 4xx ──▶ failed
                       └────────┘   └─────────┘
                            │            │
                  user resume            │ send 2xx
                            │            ▼
                            │      ┌───────────┐
                            └─────▶│ delivered │
                       (only from  └───────────┘
                        paused)
                       
                       cancelled — from any state via user delete
                       failed    — terminal; manual retry via modal re-arms
```

---

## End-to-end flow

Walking through the use case from the original prompt — "leave a reminder for one of my customer to pay in the morning for accommodation £184.41" entered at ~22:30 London time on May 14:

```
1. USER types into chat:
   "leave a reminder for one of my customer to pay in the
    morning for accommodation £184.41"

2. chat_routes.py /chat handler:
   a. is_reminder_request(prompt)  ──▶  True (matched /\bremind\b/)
   b. Fork into two parallel jobs:
      ├─ NORMAL chat completion (streams to UI as usual)
      └─ extract_reminder(prompt, now, user_tz)
            └─▶ 2nd LLM call (Groq llama-3-8b, JSON mode):
                  system: "Extract reminder fields. Output JSON only.
                           {name, instructions, fireAtLocal, timezone}.
                           If 'morning' assume 09:00 local. If no date,
                           assume next occurrence. Return null fields
                           you can't infer."
                  user: <prompt> + current_time + user_tz
            ──▶ {
                  "name": "Remind customer to pay accommodation £184.41",
                  "instructions": "Collect £184.41 accommodation payment from customer.",
                  "fireAtLocal": "2026-05-15T09:00:00",
                  "timezone": "Europe/London"
                }

3. SERVER persists reminder (status='scheduled'):
   - Compute fireAt UTC from local + tz
   - Direct Firestore write (no internal HTTP roundtrip)
   - Returns reminder_id

4. SERVER emits an inline-card token in the chat stream:
   - Sentinel tag: <reminder id="rem_abc123" />
   - Frontend streamRenderer sees the tag, swaps it for the
     ReminderCard component, fetches GET /reminders/rem_abc123.
   - Matches the existing artifact-tag swap idiom.

5. ASSISTANT text continues streaming after the card:
   "Done — I'll remind you tomorrow morning at 9:00am London
    time to collect the £184.41 accommodation payment."

6. USER (optional) clicks the card:
   - Edit modal opens (see UX section)
   - Save  ──▶ PATCH /reminders/{id} ──▶ Firestore write
   - Pause ──▶ PATCH with {status: 'paused'}
   - Delete──▶ DELETE /reminders/{id}

7. T+10.5 hours, May 15 08:00 UTC = 09:00 London:
   reminders_worker.py wakes (systemd timer tick):
   ├─ collection_group('reminders').where(status=scheduled, fireAt<=now)
   ├─ Finds rem_abc123
   ├─ Transaction: scheduled → sending, lockedAt=now
   ├─ SendGrid.send(
   │     to=user.email,
   │     subject="Reminder: Remind customer to pay accommodation £184.41",
   │     body=instructions + footer linking to chat
   │   )
   ├─ On 2xx: transaction sending → delivered, deliveredAt=now
   ├─ On 5xx: transaction sending → scheduled, attemptCount+=1,
   │          lastError=str(e), lockedAt=null
   └─ On 4xx: transaction sending → failed, attemptCount+=1, lastError=...

8. USER's inbox at 09:00:
   ┌─────────────────────────────────────────┐
   │ XelaSphere Reminders <noreply@xelas...>  │
   │ Reminder: customer £184.41 payment       │
   │                                          │
   │ Collect £184.41 accommodation payment    │
   │ from customer.                           │
   │                                          │
   │ Open chat: https://xelasphere.io/c/...  │
   └─────────────────────────────────────────┘
```

**Subtleties:**

- **Parallel extract vs serial.** Running `extract_reminder()` in parallel with the main completion costs ~$0.0001 per reminder turn but keeps perceived latency the same. Serializing would noticeably delay the assistant's first token. Pay the parallel cost.
- **`extract_reminder()` may return null fields.** "Remind me to call mom" has clear intent but no `fireAtLocal`. M0 behavior is the simplest possible: **the server does not emit a card and does not persist a reminder.** Instead, the system prompt for the chat completion is augmented (only on this turn) with a hint like: "The user wants a reminder but didn't specify when. Ask them naturally for the missing detail (time, day, or both)." The assistant streams a natural-language follow-up — "Sure — what time should I remind you?" — and the user's reply runs through `is_reminder_request` + `extract_reminder` again. When all fields are present, the card renders and the reminder persists.

  This avoids the "draft card in chat with no Firestore record" problem entirely. Cost: a reminder needing clarification takes two turns instead of one. Acceptable for M0; revisit if it feels clunky in usage.

---

## UX

### Inline card (rendered in assistant message)

```
 ┌──────────────────────────────────────────────────────┐
 │ 🔔  Remind customer to pay accommodation £184.41     │
 │     Tomorrow at 09:00  ·  London                     │
 └──────────────────────────────────────────────────────┘
```

- Single rounded card, click target = whole card
- Bell icon (match existing icon set)
- Line 1: `name`. Line 2: relative time + tz abbreviation
- States:
  - `scheduled` → normal card
  - `paused` → muted/grey + "Paused" badge
  - `delivered` → grey + ✓ "Delivered 9:00am" (view-only)
  - `failed` → red border + ⚠ "Delivery failed — tap to retry"
  - `cancelled` → struck through, dimmed
- Live updates: **poll-on-page-focus only in M0**. When the user returns to the tab, frontend re-fetches cards visible in viewport. SSE/WebSocket push is M1+.

### Edit modal

```
 ┌──────────────────────────────────────────────────────────┐
 │  Edit reminder                                       ✕   │
 │  ──────────────────────────────────────────────────────  │
 │                                                          │
 │  Name                                                    │
 │  [ Remind customer to pay accommodation £184.41       ]  │
 │                                                          │
 │  Instructions  (sent as email body)                      │
 │  ┌────────────────────────────────────────────────────┐  │
 │  │ Collect £184.41 accommodation payment from         │  │
 │  │ customer.                                          │  │
 │  └────────────────────────────────────────────────────┘  │
 │                                                          │
 │  When                                                    │
 │  [ May 15, 2026  ▾ ]   [ 09:00 ▾ ]   London (auto)      │
 │                                                          │
 │  Doesn't repeat   ▾                                      │
 │   └─ disabled in M0 — tooltip "Recurrence coming soon"   │
 │                                                          │
 │  ──────────────────────────────────────────────────────  │
 │  [ Pause ]  [ Delete ]              [ Cancel ] [ Save ]  │
 └──────────────────────────────────────────────────────────┘
```

- Field order matches ChatGPT's modal (muscle memory carries over)
- Recurrence selector **visible but disabled** in M0 (tooltip: "Recurrence coming soon"). Cheaper than hiding and re-adding.
- "Pause" toggles to "Resume" when `status='paused'`
- "Delete" uses a small confirm popover (matches XelaSphere's delete-chat confirm pattern), not a second modal
- Timezone shown as auto-detected from browser; not user-editable in M0 (we display it, IANA value fixed at create time)

### Schedules tab (settings modal)

Replaces today's "Coming Soon" placeholder:

```
 ┌──────────────────────────────────────────────────────────┐
 │  Schedules                                               │
 │  ──────────────────────────────────────────────────────  │
 │                                                          │
 │  Upcoming (3)                                            │
 │  ┌────────────────────────────────────────────────────┐  │
 │  │ 🔔 Remind customer to pay accommodation £184.41    │  │
 │  │    Tomorrow at 09:00 · London                      │  │
 │  └────────────────────────────────────────────────────┘  │
 │  ...                                                     │
 │                                                          │
 │  Past (12)                                  show all ▾   │
 │  ┌────────────────────────────────────────────────────┐  │
 │  │ ✓ Pick up groceries                                │  │
 │  │    Delivered May 13 at 18:00 · London              │  │
 │  └────────────────────────────────────────────────────┘  │
 │                                                          │
 │  [ + New reminder ]                                      │
 └──────────────────────────────────────────────────────────┘
```

- Two sections: Upcoming, Past (collapsed)
- Click any row → same edit modal
- "+ New reminder" opens the modal empty (manual create, no AI involved) — escape valve for when intent detection misses

### Implementation notes

- Reuse existing settings-modal infrastructure (`_settings_modal.html` + `settingsModal.js`). Don't invent a new modal stack — the project's idiom already fits.
- Card lives in the assistant message stream as a sentinel tag (`<reminder id="..." />`). Page reloads re-render the conversation, the tag is in the message text, and the frontend swaps it on every render — same as artifacts do today.

---

## Failure, retry, dedup, observability

### Retry policy

```
attemptCount  outcome                next action
─────────────────────────────────────────────────────────
0 → 1         SendGrid 5xx/network   status=scheduled (will retry next tick)
1 → 2         SendGrid 5xx/network   status=scheduled
2 → 3         SendGrid 5xx/network   status=failed (give up, notify in-app)
any           SendGrid 4xx           status=failed immediately
                                     (bad email — retrying won't help)
any           SendGrid 2xx           status=delivered, deliveredAt=now
```

Cap at 3 attempts. After that the card flips to red `failed` state with `lastError` visible, and the edit modal exposes a manual "Retry" button (resets `attemptCount=0`, `lastError=null`, `status='scheduled'`).

### Dedup / idempotency

Two failure modes to defend against:

- **Worker crashes after sending but before flipping status** → next tick re-sends.
- **Two systemd timers fire concurrently** (shouldn't happen with default systemd, but belt-and-suspenders).

**Mitigation:** before SendGrid call, a Firestore transaction flips `scheduled → sending` and writes `lockedAt`. Only the worker that wins the transaction sends. After send, a second transaction moves `sending → delivered` (or back to `scheduled` on retry). A stuck-lock sweep at the start of every worker run reverts `sending AND lockedAt < now-5min` to `scheduled`. **No Redis needed — Firestore transactions are the lock.**

### Time-skew / late firing

If the timer was paused (server down, machine sleeping) for 6 hours, the worker wakes to 6 hours of due reminders. **Policy: fire them all immediately, oldest first.** Add a one-line banner to the email body — "(This reminder was scheduled for {originalTime} but delivery was delayed.)" — only if delay > 10 minutes. Keeps normal emails clean.

Better than dropping late reminders (user might still need the nudge), better than rescheduling for tomorrow (worse than late).

### SendGrid down

All sends bumped to `attemptCount+1, status=scheduled`. After 3 ticks (3 minutes) they go to `failed`. M0 accepts this — SendGrid p99 uptime is ~99.95%. A multi-hour outage means a manual retry from the Schedules tab. **Fallback transport (Gmail SMTP) is a hook left for M1**, not built.

### Observability

- Worker logs structured JSON: `{ts, reminderId, userId, action: 'sent'|'retry'|'failed'|'lock_expired', attempt, latencyMs, error?}`. Tail with `journalctl -u xelasphere-reminders.service -f`.
- Counters for a future dashboard: `reminders_delivered_total`, `reminders_failed_total`, `reminders_lock_expired_total` (last one near zero in steady state — non-zero means worker crashes).
- Frontend toast on next page load if any reminder failed since last visit: "1 reminder failed to deliver. Open Schedules to see why."

### Scale assumption

Design assumes ~hundreds of reminders per user, not tens of thousands. If a user hits 50k, the collection-group query still works but you'd want per-user sharding. M0 does not optimize for that — explicitly flagged for later.

---

## Auth, scope, timezone

### Auth

- All reminder routes: `@login_required` (no `guest_allowed`). Guests have no email on file.
- POST/PATCH/DELETE: `@csrf_required` — match `email_routes.py`.
- Firestore security rule: `users/{uid}/reminders/{rid}` readable/writable only by `request.auth.uid == uid`. Worker uses admin SDK (already in use elsewhere) and bypasses the rule — worker is the only non-user writer.
- The intent-detection LLM call only fires if `g.user` is logged in. Guests typing "remind me to..." get the normal chat response with no card.

### Timezone

- Captured once, at first reminder create, from `Intl.DateTimeFormat().resolvedOptions().timeZone`.
- Stored on user doc as `users/{uid}.defaultTimezone`.
- Each reminder also gets `timezone` denormalized onto its own doc (so changing the default doesn't shift existing reminders).
- M0 ships no UI to edit the default TZ. Travel edge case is not handled — flagged for M1+.

### Rate limiting

- `@limiter.limit("60 per hour")` on POST /reminders. Prevents runaway loops.
- Worker has no rate limit — processes whatever's due. SendGrid free tier is 100/day across all users; M0 trusts that's enough. Add a per-user cap (e.g., 50 reminders/day delivered) only if it becomes a problem.

### Explicitly out of scope for M0

Each becomes its own milestone spec:

| Out | Why deferred |
|-----|-------------|
| Recurrence (daily/weekly/cron) | Modal shows it disabled. Adds rrule library + worker logic to enqueue next occurrence. → M1 |
| Web push | Needs service worker, VAPID keys, push subscription doc, permission UX. Whole milestone. → M1 |
| SMS via Twilio | New dep, phone capture/verify, channel preference per user, cost. → M2 |
| Google Calendar sync | OAuth scope, sync direction, conflict handling. → M3 |
| Contacts/customer attachment | Net-new data model. → M4 |
| Snooze from email/notification | Needs signed unsubscribe-style links from email body. Small but real. → M1 alongside push |
| Shared/team reminders | Permission model. Not on the immediate roadmap. |
| Multi-language reminder intent regex | M0 ships English-only regex. Extend the multilingual pattern from `is_email_request` in a follow-up. |
| Sub-minute precision | Worker ticks once a minute. Reminders may fire up to 60s late. Acceptable. |
| In-app live push of card status changes | Poll-on-focus only. SSE/WebSocket = M1+. |

---

## Testing & rollout

### Test coverage

| Layer | Test | What it proves |
|-------|------|----------------|
| `is_reminder_request` | Unit, ~15 prompts | "remind me to call mom" → True; "I'm trying to remember the email" → False |
| `extract_reminder()` | Unit + mocked LLM, ~10 prompts | Returns valid JSON for typical cases; returns `null` fields for ambiguous; never throws on garbage LLM output (JSON parse errors → return all-null) |
| Reminder CRUD routes | Integration, real Firestore (emulator if available, else `_test_` user prefix) | POST creates, GET retrieves, PATCH partial updates, DELETE removes, authz blocks cross-user reads |
| Worker query | Unit, mocked Firestore | Picks up due+scheduled, ignores paused/delivered, respects fireAt boundary |
| Worker lock | Integration, real Firestore | Two workers racing on same reminder → only one sends. Stuck-lock sweep recovers a `sending` doc after timeout |
| Worker → SendGrid | Integration, mocked SendGrid | 2xx → delivered; 5xx → attemptCount++; 4xx → failed immediately; 3 attempts → failed |
| Late-fire | Unit | Worker handed reminders from 6h ago → fires them, sets `wasLate` flag for email template |
| End-to-end | Manual (M0 — no Playwright in repo) | Type "remind me in 2 minutes to test"; verify card appears, modal opens, edit saves, email arrives. Do this in dev *and* prod before declaring shipped. |

No Playwright/Selenium in M0. Standing it up is a separate project. Manual end-to-end is the bar.

### Rollout — 5 stages, each gated on the prior

1. **Backend-only, no UI, admin user only.** Deploy reminders blueprint, worker, systemd timer. Create reminders via `curl` as admin. Confirm email arrives. **Proves: scheduler + transport + Firestore round-trip.** Rollback: `systemctl stop xelasphere-reminders.timer`.
2. **AI intent detection wired, no inline card.** Enable `is_reminder_request` + `extract_reminder` in chat_routes. Reminders auto-create, no card renders. Admin reviews Firestore writes for false positives. **Proves: extract LLM is accurate enough.** Rollback: feature flag.
3. **Inline card + edit modal, admin only.** Wire the sentinel tag + ReminderCard component. Test full happy path as admin. **Proves: UX is OK.** Rollback: feature flag.
4. **Schedules tab + manual create.** Replace "Coming Soon" with management UI. Manual `+ New reminder` works. **Proves: usable without AI.** Rollback: revert template.
5. **Open to all logged-in users.** Feature flag flipped on. Monitor SendGrid quota (100/day) and worker logs for 48h. **Proves: works at small scale.**

Each stage = one deploy. If stages 1–4 pass within a week, stage 5 is the launch. Fix-and-redeploy any failing stage before progressing.

### Kill switch

A single env var `REMINDERS_ENABLED=false` that:

- Skips regex check in chat_routes (no extract LLM call, no card)
- Makes POST /reminders return 503
- Worker checks it at startup and exits if false (timer keeps firing but does nothing)

Existing reminders in Firestore are untouched. Flip back to `true` → catches up on whatever's due, no data loss. One flip, one service to think about.

### Definition of done for M0

- All 5 rollout stages green
- 48h post-launch: zero `lock_expired` log entries, p99 worker run time < 5s, SendGrid delivery rate ≥ 99%
- At least 3 real reminders scheduled and received without intervention
- The customer-payment use case from the original prompt works end-to-end

---

## Open questions (deferred, not blockers)

- **SendGrid sender domain.** xelasphere.io DNS needs SPF/DKIM/DMARC for SendGrid. Coordinate with whoever owns DNS during rollout stage 1.
- **Email template branding.** First version is plain text + signature. A real HTML template is M0.5 polish.
- **Where the email "Open chat" link lands.** Needs the chat-deeplink URL pattern, which exists (`/c/{chatId}`). Reuse, no design needed.

---

## Related future milestones (out of M0)

- **M1 — Web push + recurrence + snooze.** Service worker, VAPID, push subscription doc, rrule library, signed snooze links in email.
- **M2 — SMS via Twilio.** Phone capture + E.164 verify, Twilio SDK, channel preference per user.
- **M3 — Google Calendar sync.** OAuth scope, one-way push to GCal as event, deletion handling.
- **M4 — Contacts / customer attachment.** Net-new contacts data model, bind reminder to contact, surface in chat context.

Each gets its own brainstorm → spec → plan cycle.
