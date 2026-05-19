# Reminders — M1 Design Spec

**Date:** 2026-05-19
**Status:** Approved (brainstorming complete; ready for implementation plan)
**Parent:** [Reminders M0 Design Spec](./2026-05-14-reminders-m0-design.md) — shipped 2026-05-14, PR [#173](https://github.com/xelauvas/xelasphere.io-/pull/173); polish closeout in PR [#175](https://github.com/xelauvas/xelasphere.io-/pull/175).
**Problem:** M0 ships only one-off, email-only reminders with no way to defer a fire that arrived at a bad moment. Real reminder usage looks recurring ("daily standup at 9"), needs more channels than email so users actually notice them, and needs a snooze affordance for the inevitable "5 more minutes" case.

---

## Summary

M1 layers three improvements on top of the M0 loop:

- **Recurrence** — five presets (`DAILY`, `WEEKDAYS`, `WEEKLY`, `MONTHLY`, `YEARLY`). After the worker delivers a recurring reminder, it computes the next fire in the reminder's IANA timezone, converts to UTC, and resets `status=scheduled`. No occurrence proliferation: a recurring series is a single Firestore doc whose `fireAt` advances.
- **Snooze** — three durations (10 minutes / 1 hour / Tomorrow at same time). Available from three surfaces, all converging on the same backend route: signed-token links in the delivered email, action buttons on the push notification, and a submenu in the inline-card kebab. Snooze affects the next fire only; a recurring series resumes its normal cadence afterwards.
- **Web push** — service worker scaffold, VAPID keypair, `pywebpush` from the worker, per-device subscription docs at `users/{uid}/pushSubscriptions/{subId}`. Encrypted payloads carry the reminder content. Permission prompt is an inline banner shown once after the user's first reminder.

**Ship strategy:** three vertical slices, each independently shippable:

| Slice | Contents | Target PR |
|---|---|---|
| **M1.1 — Recurrence** | presets dropdown, worker advance logic, DST-safe via `python-dateutil`, recurrence field on reminders | M1.1 PR |
| **M1.2 — Snooze** | `itsdangerous` signed-link infra, email snooze footer, in-app card snooze submenu, `snoozedUntil` field | M1.2 PR |
| **M1.3 — Push** | service worker, VAPID, push subscription doc + endpoints, permission banner, push action snooze (reuses M1.2 backend) | M1.3 PR |

Each slice ships to users on merge. M1.2 reuses field added in M1.1 (the `snoozedUntil` works on any reminder, recurring or not). M1.3 reuses the snooze backend route from M1.2.

---

## Architecture overview

```
M0 (already shipped)              M1 additions
─────────────────────             ─────────────────────────────────
chat → extract → Firestore        + recurrence enum on reminders doc
worker every 60s                  + worker advances fireAt after delivery
SendGrid email                    + signed-link snooze in email footer
inline card + modal               + snoozedUntil field (overrides next fire)
                                  + recurrence dropdown in modal
                                  + snooze submenu in card kebab
                                  + service worker (new file)
                                  + VAPID keypair + pywebpush
                                  + users/{uid}/pushSubscriptions/{id}
                                  + push permission banner (post-1st reminder)
                                  + Settings → Notifications pane
```

### New files / units (across all three slices)

**M1.1 — Recurrence**

- `app/services/recurrence.py` — `next_fire_at(reminder)` → datetime. Uses `python-dateutil.relativedelta`. Pure function, fully unit-testable.
- `tools/reminders_worker.py` — extend `process_reminder` to advance `fireAt` and reset `status=scheduled` after successful delivery of a recurring reminder.
- `app/blueprints/reminders_routes.py` — extend `_EDITABLE_FIELDS` to include `recurrence`. Add validation.
- Frontend: `_reminders_modal.html` adds the recurrence `<select>` (re-enables what M0.5 removed).
- `requirements.txt` — `python-dateutil>=2.9` (likely already in chain; verify).

**M1.2 — Snooze**

- `app/services/signed_links.py` — `make_snooze_token(rid, uid)` / `verify_snooze_token(token)`. Wraps `itsdangerous.URLSafeTimedSerializer` with a 24-hour max-age.
- `app/blueprints/reminders_routes.py` — `POST /reminders/<id>/snooze` (auth via session OR signed token). Handles `dur=10m|1h|tomorrow`.
- `tools/reminders_worker.py` — `_build_email_html` and `_build_email_body` extended to append snooze links (token freshly minted per send).
- Frontend: `remindersModule.js` adds Snooze submenu to the card kebab dropdown.
- `requirements.txt` — `itsdangerous` (already present via Flask).

**M1.3 — Push**

- `app/services/push_dispatcher.py` — `send_push_for_reminder(reminder, user)`. Iterates `users/{uid}/pushSubscriptions/*`, calls `pywebpush.send` for each, handles `410 Gone` by deleting the sub.
- `app/blueprints/push_routes.py` — new blueprint:
  - `POST /push/subscribe` — frontend sends subscription JSON after `Notification.requestPermission()` succeeds.
  - `DELETE /push/subscriptions/<subId>` — user removes one device.
  - `GET /push/vapid-public-key` — exposes the VAPID public key for service-worker subscription.
- `app/static/js/sw.js` — service worker. Handles `push` event (shows notification) and `notificationclick` (snooze action POSTs to `/reminders/<id>/snooze` with the signed token from payload data).
- `app/static/js/pushSetup.js` — frontend lifecycle: registers SW, requests permission, subscribes, posts to backend.
- `app/templates/index.html` — `<link rel="manifest">` if missing (PWA requirement for SW scope).
- `tools/reminders_worker.py` — after SendGrid send succeeds, also call `push_dispatcher.send_push_for_reminder`.
- `requirements.txt` — `pywebpush>=2.0`, `py-vapid>=1.9` (often bundled together via `pywebpush[full]`).
- Env additions: `VAPID_PRIVATE_KEY`, `VAPID_PUBLIC_KEY`, `VAPID_CLAIMS_SUB` (mailto: address SendGrid-style).

### Reuse / extend (no new patterns invented)

- Worker, retry policy, stuck-lock sweep, Firestore lifecycle — all unchanged.
- Snooze backend reuses M0 status machine (`scheduled` → fires when `effective_fire_at <= now`).
- Inline card + Schedules tab don't change shape — only the card kebab gains a Snooze submenu.
- Settings modal pattern (`_settings_modal.html`) gains a Notifications pane the same way the Schedules pane was added in M0.

---

## Data model

**Reminder doc** (`users/{uid}/reminders/{reminderId}`) — additive deltas only. Existing docs read missing fields as `null`/`0` and behave exactly like M0.

```jsonc
{
  // ... all M0 fields unchanged ...

  // NEW in M1.1 — recurrence
  "recurrence": "DAILY" | "WEEKDAYS" | "WEEKLY" | "MONTHLY" | "YEARLY" | null,
  "deliveryCount": 0,          // increments on each delivered occurrence
  "lastFiredAt": null,         // ISO-8601 UTC; set after each successful fire

  // NEW in M1.2 — snooze
  "snoozedUntil": null         // ISO-8601 UTC; if set + > now, used instead of fireAt
                               // for the NEXT fire only; cleared after that fire completes
}
```

**Push subscription doc** (`users/{uid}/pushSubscriptions/{subId}`) — new in M1.3:

```jsonc
{
  "endpoint": "https://fcm.googleapis.com/fcm/send/...",
  "keys": {
    "p256dh": "BNc...",          // base64url
    "auth":   "tBHI..."           // base64url
  },
  "userAgent": "Mozilla/5.0 ... Chrome/...",  // for "Chrome on Mac" labeling
  "createdAt": "2026-05-19T...",
  "lastSeenAt": "2026-05-19T..."
}
```

**User doc additions** (`users/{uid}`):

```jsonc
{
  "pushPromptDismissedAt": null   // ISO-8601 UTC; re-prompt after 30 days if still null subs
}
```

### Effective-fire-at semantics

The worker query stays the same (`status='scheduled' AND fireAt<=now`). But before sending, the worker computes:

```python
effective = reminder.snoozedUntil if reminder.snoozedUntil and reminder.snoozedUntil > now else reminder.fireAt
if effective > now:
    # snooze pushed it past now → leave it scheduled, this tick skips
    continue
```

This way, the existing index works. Snoozing a reminder past `fireAt` doesn't require updating `fireAt` itself — `snoozedUntil` acts as a one-time override layer.

When the reminder fires, `snoozedUntil` is cleared. If recurring, `fireAt` advances via `recurrence.next_fire_at()`. If one-off, `status` transitions to `delivered` (terminal).

### Indexes

- Existing collection-group `(status ASC, fireAt ASC)` covers the worker query — no new index.
- New per-user `users/{uid}/pushSubscriptions/*` query is just `.stream()` over a small collection — no index needed.

---

## End-to-end flows

### Flow A — Recurring reminder delivers and advances

```
T=0   user creates "Daily standup reminder at 09:00 London"
      → reminder {fireAt: 2026-05-20T08:00Z, recurrence: "DAILY", tz: "Europe/London"}

T+1d  worker tick at 08:00 UTC = 09:00 London
      → query finds reminder
      → effective fire = max(fireAt, snoozedUntil-if-set) = 08:00Z (no snooze)
      → txn: status → sending
      → send email (and push if subscribed) — content unchanged
      → on success:
          deliveryCount++, lastFiredAt = now
          if recurrence != null:
            nextLocal = current_local_fire_at + 1 day   ← dateutil; DST-safe
            fireAt = nextLocal.astimezone(utc)
            snoozedUntil = null
            status = "scheduled"
          else:
            status = "delivered"  (M0 terminal)
```

**DST handling** is delegated to `dateutil.relativedelta` operating on a timezone-aware datetime in the reminder's IANA tz. Spring-forward and fall-back are handled identically to how Google Calendar handles them: the local hour is preserved, the UTC offset shifts.

### Flow B — User clicks "Snooze 1 hour" in delivered email

```
1. Email body footer (in both HTML and plain text):
     Need more time?
     [Snooze 10 min] [Snooze 1 hour] [Snooze until tomorrow]

   Each href:
     https://xelasphere.com/reminders/{id}/snooze?dur=1h&token=<signed>

   Token = URLSafeTimedSerializer.dumps({"rid": id, "uid": uid})
   Validity: 24 hours from email send.

2. User clicks at 09:05 → route:
   - verify_snooze_token(token) → {rid, uid}; check expiry, owner
   - Snooze duration mapping (relative to *now*, not to original fireAt):
       dur=10m       → new_until = now + 10 minutes
       dur=1h        → new_until = now + 1 hour
       dur=tomorrow  → new_until = now + 24 hours
   - reminder.snoozedUntil = new_until
   - For recurring: fireAt unchanged (only THIS occurrence shifts)
   - For one-off (delivered): status flips back to "scheduled" so it re-fires
   - Return friendly HTML page: "Snoozed to 10:05 — we'll remind you then."

3. Next worker tick at 10:05 picks it up (snoozedUntil > now after the prior skip)
   Delivers, clears snoozedUntil, advances fireAt if recurring as in Flow A.
```

### Flow C — Push notification with snooze action

```
1. User accepted push permission via banner. pushSetup.js:
   - sw.register('/sw.js', { scope: '/' })
   - registration.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: <VAPID public> })
   - POST /push/subscribe { endpoint, keys: {p256dh, auth}, userAgent }
   - server creates users/{uid}/pushSubscriptions/{auto-id}

2. Worker delivers a reminder → email goes out → also calls push_dispatcher:
   for sub in db.collection(f"users/{uid}/pushSubscriptions").stream():
     try:
       pywebpush.send(
         subscription_info={"endpoint": sub.endpoint, "keys": sub.keys},
         data=json.dumps({
           "title": reminder.name,
           "body": reminder.instructions or reminder.name,
           "actions": [
             {"action": "snooze_10m", "title": "10m"},
             {"action": "snooze_1h",  "title": "1h"}
           ],
           "data": {
             "rid": reminder.id,
             "snoozeToken": make_snooze_token(rid, uid)
           }
         }),
         vapid_private_key=VAPID_PRIVATE_KEY,
         vapid_claims={"sub": VAPID_CLAIMS_SUB}
       )
       sub.update({"lastSeenAt": now})
     except WebPushException as e:
       if e.response.status_code == 410:
         sub.delete()
       elif e.response.status_code == 429:
         log + retryable
       else:
         log + skip (one bad sub doesn't fail the whole reminder)

3. Service worker sw.js receives push event:
   self.addEventListener('push', e => {
     const d = e.data.json();
     e.waitUntil(self.registration.showNotification(d.title, {
       body: d.body, actions: d.actions, data: d.data, icon: '/static/img/icon-192.png'
     }));
   });
   self.addEventListener('notificationclick', e => {
     const { rid, snoozeToken } = e.notification.data;
     e.notification.close();
     if (e.action === 'snooze_10m' || e.action === 'snooze_1h') {
       const dur = e.action.replace('snooze_', '');
       e.waitUntil(fetch(`/reminders/${rid}/snooze?dur=${dur}&token=${snoozeToken}`, {method:'POST'}));
     } else {
       // body click → open chat
       e.waitUntil(clients.openWindow(`/c/${chatId}`));
     }
   });

4. Snooze POST hits same handler as Flow B — backend unaware of source surface.
```

The convergence on one snooze handler (`POST /reminders/<id>/snooze`) is the architectural win of bundling all three M1 features. Email link, push action, and in-app dropdown all hit the same endpoint. Auth differs (session vs signed token), behavior is identical.

---

## UX

### Edit modal — recurrence dropdown (M1.1)

The disabled placeholder removed in M0 closeout returns enabled:

```
 ┌──────────────────────────────────────────────────────┐
 │  Edit reminder                                  ✕   │
 │  Name      [ Daily standup reminder              ]  │
 │  Instructions  [...]                                 │
 │  When  [ May 20, 2026 ▾ ] [ 09:00 ▾ ] London         │
 │                                                      │
 │  Repeat  [ Daily ▾                              ]    │
 │           ├─ Doesn't repeat                          │
 │           ├─ Daily                                   │
 │           ├─ Every weekday (Mon–Fri)                 │
 │           ├─ Weekly on Wednesday                     │
 │           ├─ Monthly on the 20th                     │
 │           └─ Yearly on May 20                        │
 │                                                      │
 │  [Pause] [Delete]              [Cancel] [Save]       │
 └──────────────────────────────────────────────────────┘
```

The dynamic preset labels ("Monthly on the 20th") are computed from the current `fireAt` — when the user changes the date, the labels refresh. Changing recurrence to `null` keeps the existing one-off behavior.

### Card kebab dropdown — Snooze submenu (M1.2)

Snooze appears only on `scheduled` or recently `delivered`+recurring reminders. A one-off `delivered` reminder has no useful snooze affordance (it's terminal).

```
  Kebab dropdown:
   Edit details
   Pause / Resume         ← scheduled only
 ▶ Snooze  ┐
   Delete  │
            └─→  10 minutes
                 1 hour
                 Tomorrow at this time
```

### Email footer — Snooze links (M1.2)

Inserted between the body and the signature line. Plain-text version uses bracketed URLs:

```
   Open chat: https://xelasphere.com/c/...
   
   ──────────────
   Need more time?
   [Snooze 10 min]  [Snooze 1 hour]  [Snooze until tomorrow]
   
   — XelaSphere Reminders · Scheduled for 09:00 London
```

### Push notification (M1.3)

```
  ┌─────────────────────────────────────────┐
  │ xelasphere.com                          │
  │                                         │
  │ 🔔  Daily standup reminder              │
  │     Standup is starting now             │
  │                                         │
  │           [ 10m ]   [ 1h ]              │
  └─────────────────────────────────────────┘
```

Two action buttons (browsers cap at 2 reliable actions across Chrome/Firefox/Safari). Body click opens the chat deeplink (same URL as the email "Open chat").

### Post-first-reminder push banner (M1.3)

Inline in the chat, shown ONCE per user after their first reminder is created:

```
   AI: Done — I'll remind you at 9am tomorrow.
   
   ┌─ 🔔 reminder card ────────────────────────────┐
   │  Pay accommodation  ·  Tomorrow 09:00 London  │
   └───────────────────────────────────────────────┘
   
   ┌───────────────────────────────────────────────┐
   │  Want a browser ping too?                     │
   │  [ Enable push ]  [ Not now ]   ✕             │
   └───────────────────────────────────────────────┘
```

- "Enable" → native `Notification.requestPermission()` → `pushManager.subscribe()` → POST `/push/subscribe`.
- "Not now" → sets `user.pushPromptDismissedAt = now`. Re-shows after 30 days if still no subscriptions.
- ✕ → same as "Not now" but explicit dismiss.

### Settings → Notifications pane (M1.3)

```
   Browser notifications:  ●─── On
   
   Active devices:
     Chrome on MacBook Pro       last used 5 min ago    [ Remove ]
     Firefox on Windows          last used 2 days ago   [ Remove ]
     
   Snooze durations:  [10 min ▾]  [1 hour ▾]  [Tomorrow ▾]
                       (locked in M1 — defaults; user-editable in M5+)
```

Toggle Off removes all subscriptions (DELETE on each `pushSubscriptions/*`). Toggling back On re-triggers the permission flow.

---

## Failure, retry, dedup, observability

### Recurrence edge cases

| Case | Handling |
|---|---|
| DST spring-forward (2:30am → no longer exists) | `dateutil` resolves to 3:30am — local hour preserved is impossible, gains an hour |
| DST fall-back (1:30am exists twice) | First occurrence (`fold=0`) — deterministic |
| Monthly on the 31st in a 30-day month | Falls back to the last day of the month |
| Yearly on Feb 29 in a non-leap year | Falls back to Feb 28 |
| Recurring fires while previous still in `sending` | Existing stuck-lock sweep handles — same as M0 |

All semantics from `dateutil.relativedelta`. No custom code.

### Snooze edge cases

| Case | Handling |
|---|---|
| Signed link >24h after issue | Expired token → friendly "link expired" page with a link back to the chat |
| Link clicked twice (two devices) | Idempotent: if `snoozedUntil` already equals the requested value, return success silently |
| `snoozedUntil` past next recurring fire | Snooze wins for next fire; series resumes after |
| Snooze on `delivered` one-off | Status flips back to `scheduled`; `deliveredAt` cleared (resurrection) |
| Token tampered/forged | `BadSignature` → 400 with no details |

### Push edge cases

| Case | Handling |
|---|---|
| `pywebpush.send()` 410 Gone | Delete the sub doc; continue with other subs |
| 413 Payload Too Large | Truncate body to 2KB and retry once; log if still fails |
| 429 Rate Limit | Backoff, surface in `lastError`, retry next tick |
| User revokes permission after subscribing | First 410 deletes the sub silently |
| User has push subs on N devices | Send to all; partial failure (1 of N 410s) deletes that one |

### Observability

Reuse M0's structured JSON log pattern from `reminders_worker.py`. New event names:

```
reminder_recurring_advanced   {rid, recurrence, nextFireAt, deliveryCount}
reminder_snoozed              {rid, source: "email"|"push"|"app", duration, until}
push_dispatched               {rid, subCount, successes, failures}
push_subscription_expired     {subId, userAgent}
push_payload_too_large        {rid, originalBytes, truncatedBytes}
```

No new dashboards — `journalctl -u xelasphere-reminders.*` is sufficient.

### Scale assumption

Same as M0: ≤1,000 active users, ≤100 due reminders per tick (the limit). Recurrence inflates total reminder count over time, but `(status, fireAt)` index keeps the query cheap (only scheduled+due are scanned). Push fan-out: ≤5 subscriptions per user is a reasonable upper bound for M1.

---

## Testing & rollout

### Test coverage

- **Unit (recurrence):** every preset → expected next-fire across DST spring-forward, fall-back, leap year, month-end. Pure function tests, fast, no mocks.
- **Unit (signed tokens):** sign + verify, expired token rejected, tampered token rejected, ownership mismatch rejected.
- **Unit (push payload):** build payload from reminder; truncate body if >2KB; ensure VAPID claims include `sub`.
- **Integration (worker recurring):** create `recurrence=DAILY` reminder due now → worker tick → assert Firestore: `fireAt` advanced 1 day, `deliveryCount=1`, `status=scheduled`, `snoozedUntil=null`.
- **Integration (snooze):** POST `/reminders/<id>/snooze` with valid signed token → assert `snoozedUntil` set. With expired token → 400. From session auth → also works.
- **Integration (push 410):** mock `pywebpush.send` to raise `WebPushException` with 410 → assert subscription doc deleted, reminder still marked delivered.
- **Integration (push payload too large):** payload > 2KB → assert truncation + log emitted.
- **E2E (manual, per slice):**
  - **M1.1:** Create DAILY reminder for 1 min from now. Wait. Confirm email. Wait 24h. Confirm second email at same local time (DST-safe if DST boundary crossed).
  - **M1.2:** Click "Snooze 10 min" in delivered email. Confirm friendly page. Wait 10 min. Confirm second email.
  - **M1.3:** Subscribe to push from banner. Trigger reminder. Confirm OS notification + body click → chat. Snooze via action button → confirm second push 10 min later.

### Rollout — 5 stages

| Stage | Action | Gated on |
|---|---|---|
| 1 | Land M1.1 (recurrence) behind `REMINDERS_RECURRENCE_ENABLED=false`. Self-test in shadow. | M1.1 PR merged |
| 2 | Flip flag on for own account; create 3 recurring reminders. Watch logs for 48h: zero stuck recurrings, zero `lock_expired`. | 48h clean |
| 3 | Land M1.2 (snooze). Test signed-link round-trip from own inbox. Confirm token expiry works. | M1.2 PR merged + Stage 2 clean |
| 4 | Land M1.3 (push). Subscribe own browser. Test full flow including notification action. | M1.3 PR merged + Stage 3 clean |
| 5 | Announce in CHANGELOG. Watch metrics 48h: zero push 5xx, zero `lock_expired`, p99 worker <5s. Declare M1 done. | 48h post-M1.3 clean |

### Kill switches

Each slice gets its own env flag for instant rollback:

- `REMINDERS_RECURRENCE_ENABLED=false` → worker treats `recurrence` field as `null`; all reminders behave one-off. Existing recurring docs in Firestore are untouched; flipping back on resumes them.
- `REMINDERS_SNOOZE_ENABLED=false` → snooze route returns 503; email footer omits snooze block; card kebab omits Snooze submenu.
- `PUSH_ENABLED=false` → banner doesn't show; `/push/subscribe` returns 503; `push_dispatcher.send_push_for_reminder` is a no-op.

### Definition of done

- At least one real recurring reminder fires **twice** without intervention (confirms worker advance logic)
- At least one snooze (any of the three surfaces) successfully delays a fire
- At least one push notification delivers and its snooze action successfully delays a fire
- No new `lock_expired` events in the 48h after M1.3 lands
- Worker p99 still <5s
- All three kill switches verified working in staging (flip off → feature absent, flip on → feature returns)

---

## Auth, scope, timezone

### Auth

- Reminders routes unchanged from M0 (session-based, CSRF-protected).
- Snooze route accepts BOTH session auth (for in-app calls) AND signed token (for email/push). The handler tries session first, falls back to token. Token-only callers don't need login.
- Push subscribe/unsubscribe routes are session-only (CSRF-protected). VAPID public key endpoint is unauthenticated (it's public by definition).

### Scope

- All routes scoped to `current_user.uid`. Signed tokens encode `uid` and the verify step asserts the reminder's owner matches the token's `uid` — defense against token reuse across accounts.

### Timezone

- M1.1 introduces meaningful TZ handling for the first time. `recurrence.next_fire_at(reminder)` operates on a tz-aware datetime in the reminder's `timezone`, then converts to UTC for storage. DST is handled correctly because `dateutil` understands IANA tz transitions.
- TZ travel edge case (M0 open question) is still deferred — M1 does not provide a UI to change `reminder.timezone` after creation.

### Rate limiting

- Existing limiter on POST `/reminders` covers manual create + chat-driven create.
- New POST `/reminders/<id>/snooze` → 20/min per user (same as PATCH).
- POST `/push/subscribe` → 10/min per user (registration is one-shot; this is just abuse protection).

---

## Explicitly out of scope for M1

| Item | Why deferred | Target |
|---|---|---|
| Custom RRULE builder (every N days, until date, on which weekdays) | Presets cover ~95% of use; revisit if real usage demands it | M5+ |
| Per-reminder channel preference (this one is email-only, that one is push-only) | Both fire is simpler; channel-off is a global setting | M5+ |
| Per-user snooze duration customization | Defaults are fine; revisit if users complain | M5+ |
| Snooze on `cancelled` reminders | UX unclear (delete → un-delete?) | M5+ |
| Snooze beyond Tomorrow (e.g., next week, custom date) | Adds picker UI; tomorrow is the natural escape hatch | M5+ |
| Edit-just-this-occurrence on a recurring (without snoozing) | Requires occurrence-doc model or exceptions list; not justified by demand | M5+ |
| Server-Sent Events for live status updates | M0 used poll-on-focus; M1 doesn't change this | M5+ |
| Web push offline queueing (if the browser is closed for a week, queue?) | Web Push spec already handles short-term offline; >24h is browser-dependent and not worth engineering around | won't fix |
| iOS Safari push (PWA install required) | Apple-specific friction; user can use email until iOS browser support improves | tracking only |

---

## Open questions (deferred, not blockers)

- **VAPID claims `sub`.** The Web Push spec requires a `mailto:` or `https:` URI as the `sub` claim, used by push services to identify the sending application. We'll use `mailto:noreply@xelasphere.com`. No DNS impact, but document it.
- **Service worker scope.** Will `/sw.js` at root scope cause conflicts with any existing JS? No existing SW in the codebase per investigation, so should be clean — but verify on first deploy.
- **Recurrence advance on a paused reminder.** If a user pauses a recurring reminder for a week, does it skip occurrences or fire the backlog on resume? **Decision:** skip — the worker query is `status='scheduled'`, paused reminders are excluded. On resume, the next fire is the natural `next_fire_at(now)`, not the missed ones.
- **Push permission denial recovery.** Browsers permanently remember "denied" — we can't re-prompt. UX is "go to browser settings to re-enable" with a link. Accept; can't engineer around browser policy.

---

## Related future milestones (out of M1)

- **M2 — SMS via Twilio.** Phone capture + E.164 verify, Twilio SDK, channel preference per user.
- **M3 — Google Calendar sync.** OAuth scope, one-way push to GCal as event, deletion handling.
- **M4 — Contacts / customer attachment.** Net-new contacts data model, bind reminder to contact.
- **M5+ — Snooze + recurrence depth.** Custom RRULE builder, custom snooze durations, edit-just-this-occurrence semantics.
