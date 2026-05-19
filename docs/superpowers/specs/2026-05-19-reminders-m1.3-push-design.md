# Reminders — M1.3 Design Spec (Web Push)

**Date:** 2026-05-19
**Status:** Approved (brainstorming complete; ready for implementation plan)
**Parent:** [Reminders M1 Design Spec](./2026-05-19-reminders-m1-design.md) — bundled M1 = push + recurrence + snooze. M1.1 (recurrence) shipped in PR [#177](https://github.com/xelauvas/issues/177); M1.2 (snooze) shipped in PR [#178](https://github.com/xelauvas/issues/178). M1.3 closes out M1.
**Problem:** Email reminders only fire when the user happens to check their inbox. The user might be heads-down in the app or on another device with the chat open. We want a notification that surfaces immediately, regardless of whether mail is being checked — without forcing the user to install a native app.

---

## Summary

M1.3 adds web push notifications. After the worker delivers the email, it also dispatches a push notification to every browser the user has subscribed. Each notification carries the reminder text plus two snooze action buttons that reuse the M1.2 backend.

- **Browser support:** Chrome / Firefox / Edge / Safari (macOS 13+) on desktop; Chrome / Firefox on Android. iOS Safari deferred — needs PWA install, scope-cut.
- **Subscription model:** per-device. A user with three browsers has three `pushSubscriptions` docs; all receive every push.
- **Permission UX:** inline banner shown once after the user creates their first reminder. Dismissible; re-shows after 30 days if no subscription exists.
- **Settings:** single ON/OFF toggle. OFF deletes ALL user's subscriptions. ON triggers the permission flow for the current browser.
- **Push action snooze:** notifications include "10m" and "1h" buttons. Tapping fires GET `/reminders/<id>/snooze?dur=10m&token=...` — the exact same route M1.2 wired for email links. Backend has no idea the request came from a notification.
- **Payload encryption:** standard Web Push spec via `pywebpush` + VAPID keys. Encrypted, ~4KB cap, server-side signed.
- **Kill switch:** `PUSH_ENABLED` (default off). When off: banner hidden, subscribe endpoint 503, fan-out no-op. Email still delivers.

---

## Architecture overview

```
M0 + M1.1 + M1.2 (shipped)             M1.3 adds
────────────────────────               ─────────────────────────────────
worker → SendGrid email                + worker → push_dispatcher fan-out
snooze route (POST + GET)              + push action snooze reuses GET route
                                       + new service worker (sw.js)
                                       + VAPID keypair, pywebpush dep
                                       + users/{uid}/pushSubscriptions/{id}
                                       + POST /push/subscribe (session)
                                       + DELETE /push/subscribe (session)
                                       + GET /push/vapid-public-key (public)
                                       + post-first-reminder push banner
                                       + Settings → Notifications toggle
                                       + PUSH_ENABLED kill switch
```

### New files / units

| Path | Responsibility |
|---|---|
| `app/services/push_dispatcher.py` | `send_push_for_reminder(reminder, user_id)`. Iterates user's subscriptions, builds payload, calls `pywebpush.send` per sub, handles 410/413/429 cleanly. No-op when `PUSH_ENABLED=false` or `VAPID_PRIVATE_KEY` missing |
| `app/blueprints/push_routes.py` | `POST /push/subscribe`, `DELETE /push/subscribe`, `GET /push/vapid-public-key`. Session-authed except for vapid-public-key which is by definition public |
| `app/static/js/sw.js` | Service worker. Handles `push` event (shows native notification) and `notificationclick` event (snooze action → backend, body → open chat). Self-contained, no module dependencies |
| `app/static/js/pushSetup.js` | Frontend lifecycle: detect browser support, register SW, request permission via banner click, subscribe with VAPID public key, POST subscription to backend |
| `app/templates/_push_permission_banner.html` | Inline banner partial rendered as part of the first reminder card. Mid-chat-message, dismissible |
| `app/templates/_settings_notifications_pane.html` | Settings → Notifications tab. Single toggle |
| `tests/test_push_dispatcher.py` | Payload building, truncation, 410/413/429 handling |
| `tests/test_push_routes.py` | Subscribe (happy + idempotent), unsubscribe-all, vapid-public-key, flag-off |

### Modified files

- `tools/reminders_worker.py` — after the existing SendGrid send success, call `push_dispatcher.send_push_for_reminder(reminder, user_id)`. Per-sub errors caught; one bad sub doesn't fail the worker tick
- `app/config.py` — add `PUSH_ENABLED`, `VAPID_PRIVATE_KEY`, `VAPID_PUBLIC_KEY`, `VAPID_CLAIMS_SUB` env flags
- `app/templates/index.html` — load `pushSetup.js`, inject `window.RemindersModule.config.pushEnabled` for frontend gate
- `app/templates/_reminders_modal.html` (or wherever the inline reminder card renders post-create) — conditionally include `_push_permission_banner.html` after the first reminder
- `app/static/js/remindersModule.js` — banner show/hide logic, "Not now" handler that POSTs to `/push/dismiss-prompt`
- `requirements-cpu.txt`, `requirements-gpu.txt` — add `pywebpush>=2.0` and `py-vapid>=1.9`

### Reuse / extend

- `signed_links.py` from M1.2 — notification payload includes a freshly-minted snooze token; action click POSTs to the same route email links GET
- `/reminders/<id>/snooze` GET handler from M1.2 — uses identical signed-token auth from notification payload data
- Settings modal pattern (existing) — Notifications tab is a sibling of Schedules
- Worker structured logging pattern — new event names per the observability section
- Email delivery is untouched; push is purely additive

---

## Data model

**Existing reminder docs unchanged.** No new fields on `users/{uid}/reminders/{rid}`.

**New collection** `users/{uid}/pushSubscriptions/{subId}`:

```jsonc
{
  "endpoint": "https://fcm.googleapis.com/fcm/send/...",
  "keys": {
    "p256dh": "BNc...",          // base64url, browser-generated
    "auth":   "tBHI..."          // base64url, browser-generated
  },
  "userAgent": "Mozilla/5.0 ... Chrome/...",  // diagnostic, for log enrichment
  "createdAt": "2026-05-19T...",
  "lastSeenAt": "2026-05-19T...",  // updated after each successful push
  "lastError": null               // populated on non-410 failures for diagnostics
}
```

**User doc additions** (`users/{uid}`):

```jsonc
{
  "pushPromptDismissedAt": null   // ISO-8601 UTC. Set when user clicks "Not now"
                                  // or ✕. Banner re-shows after 30 days IF still
                                  // no subscriptions.
}
```

**Indexes:** none new. The `pushSubscriptions` subcollection is queried as a simple `.stream()` per user — small N (≤5 expected per user) makes any index overkill.

### VAPID identity

VAPID keypair is generated ONCE per server install. Stored as env vars:

```
VAPID_PRIVATE_KEY=BIY...  # base64url-encoded EC P-256 private key
VAPID_PUBLIC_KEY=BNc...   # base64url-encoded EC P-256 public key (served via /push/vapid-public-key)
VAPID_CLAIMS_SUB=mailto:noreply@xelasphere.com
```

The `sub` claim is required by the Web Push spec — must be a contact URI (mailto: or https:). Push services may reach this address if our pushes start abusing rate limits.

Key rotation procedure (manual incident response):
1. Generate new keypair via `python -m py_vapid generate_keys`
2. Update env vars + restart Gunicorn
3. All existing subscriptions become invalid (sign-verify fails). On next reminder, each sub returns 401 → we surface as `push_send_error` log
4. Each user must Settings → toggle Off, then On to re-subscribe with the new key

Documented in `docs/ops/push-vapid-setup.md` (written during M1.3 task).

---

## End-to-end flows

### Flow A — User creates first reminder; we ask permission

```
1. User in chat: "remind me to call mom at 9pm"
2. AI creates reminder via M0 flow. Inline card renders.
3. Frontend gate (pushSetup.js):
   - Browser supports push? (Notification + serviceWorker + PushManager all present)
   - Not iOS Safari? (UA string filter)
   - User has no pushSubscriptions (GET /push/subscriptions/count or similar cheap check)
   - pushPromptDismissedAt is null OR >30 days ago
   All four yes → render banner inline below the new card:

      ┌─────────────────────────────────────────────┐
      │ 🔔  Want a browser ping too?                │
      │     Get notified even when this tab isn't   │
      │     open.                                   │
      │     [ Enable push ]   [ Not now ]    ✕     │
      └─────────────────────────────────────────────┘

4. "Not now" / ✕ → POST /push/dismiss-prompt → sets pushPromptDismissedAt = now.
   Banner removed from DOM.

5. "Enable push" → JavaScript:
   a. const perm = await Notification.requestPermission()
   b. if (perm !== 'granted') → show "Permission denied. Re-enable in browser settings."
      then hide banner. Done.
   c. const reg = await navigator.serviceWorker.register('/sw.js', {scope: '/'})
   d. const vapidPublic = await (await fetch('/push/vapid-public-key')).json()
   e. const sub = await reg.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: urlBase64ToUint8Array(vapidPublic.key)
      })
   f. await fetch('/push/subscribe', {
        method: 'POST',
        headers: {'Content-Type': 'application/json', 'X-CSRFToken': getCsrf()},
        body: JSON.stringify({
          endpoint: sub.endpoint,
          keys: sub.toJSON().keys,
          userAgent: navigator.userAgent
        })
      })
   g. Banner removed permanently (the subscription check from now on returns >0).
```

### Flow B — Reminder fires, push fans out alongside email

```
1. Worker tick. Reminder is due. process_reminder runs (M0).
2. After successful SendGrid send AND after M1.1 recurring-advance and
   M1.2 snooze-skip checks have NOT short-circuited the function:

   from app.services.push_dispatcher import send_push_for_reminder
   send_push_for_reminder(reminder, reminder["userId"])

3. push_dispatcher.send_push_for_reminder:

   from app.config import Config
   if not Config.PUSH_ENABLED:
       return
   if not Config.VAPID_PRIVATE_KEY:
       logger.warning("push_disabled_no_vapid", extra={...}); return

   subs = list(db.collection(f"users/{uid}/pushSubscriptions").stream())
   if not subs:
       return  # email-only user, common case

   from app.services.signed_links import make_snooze_token
   token = make_snooze_token(reminder["id"], uid)
   payload = json.dumps({
       "title": reminder["name"],
       "body":  _truncate(reminder.get("instructions") or reminder["name"], 2048),
       "data": {
           "rid":   reminder["id"],
           "chat":  reminder.get("sourceChatId"),
           "snoozeToken": token,
       },
       "actions": [
           {"action": "snooze_10m", "title": "10m"},
           {"action": "snooze_1h",  "title": "1h"},
       ],
   })

   successes = expired = errors = 0
   for snap in subs:
       sub = snap.to_dict() or {}
       try:
           pywebpush.send(
               subscription_info={
                   "endpoint": sub["endpoint"], "keys": sub["keys"]
               },
               data=payload,
               vapid_private_key=Config.VAPID_PRIVATE_KEY,
               vapid_claims={"sub": Config.VAPID_CLAIMS_SUB},
           )
           successes += 1
           snap.reference.update({"lastSeenAt": _now_iso()})
       except WebPushException as exc:
           status = exc.response.status_code if exc.response is not None else 0
           if status == 410:
               snap.reference.delete()
               expired += 1
           elif status == 413:
               # Payload too large — retry with body trimmed harder
               payload_small = json.dumps({**json.loads(payload),
                                            "body": _truncate(reminder["name"], 256)})
               try:
                   pywebpush.send(...)
                   successes += 1
               except Exception:
                   errors += 1
           elif status == 429:
               logger.info("push_send_error",
                   extra={"extra_fields": {"rid": reminder["id"], "sub": snap.id,
                                            "status": status, "retry": "next-tick"}})
               errors += 1
           else:
               snap.reference.update({"lastError": str(exc)[:200]})
               errors += 1
       except Exception as exc:
           errors += 1
   logger.info("push_dispatched",
       extra={"extra_fields": {"rid": reminder["id"],
                                "subCount": len(subs),
                                "successes": successes,
                                "expired": expired,
                                "failures": errors}})
```

### Flow C — User clicks notification body (open chat)

```
1. Notification fires on user's device. They tap the notification body
   (NOT an action button).
2. Service worker `notificationclick` handler runs:
   self.addEventListener('notificationclick', (event) => {
     event.notification.close();
     const {chat} = event.notification.data;
     if (event.action === 'snooze_10m' || event.action === 'snooze_1h') {
       // Flow D
       return;
     }
     event.waitUntil(
       clients.openWindow(chat ? `/c/${chat}` : '/')
     );
   });
3. Browser opens (or focuses an existing tab on) /c/{chatId}. User sees
   their chat with the reminder card visible.
```

### Flow D — User clicks "10m" or "1h" action on notification

```
1. User taps "10m" action button.
2. Service worker `notificationclick` handler:
   self.addEventListener('notificationclick', (event) => {
     event.notification.close();
     const {rid, snoozeToken} = event.notification.data;
     if (event.action === 'snooze_10m' || event.action === 'snooze_1h') {
       const dur = event.action === 'snooze_10m' ? '10m' : '1h';
       event.waitUntil(
         fetch(`/reminders/${rid}/snooze?dur=${dur}&token=${snoozeToken}`,
               {method: 'GET', credentials: 'omit'})
       );
     }
   });

3. Request hits the M1.2 GET /reminders/<id>/snooze route. Token-auth
   matches; the reminder's snoozedUntil is updated.

4. The GET route renders a confirmation HTML page, but the service worker's
   fetch is non-interactive — the page is never displayed. Side effect is
   what we wanted; render is discarded.

5. Worker honors snoozedUntil on next tick (M1.2 logic, unchanged).
```

---

## UX

### Inline push permission banner (post-first-reminder)

```
   AI: Done — I'll remind you at 9:00pm.

   ┌─ 🔔 reminder card ─────────────────────────┐
   │  Call mom                                   │
   │  Tonight 21:00 · London                     │
   └─────────────────────────────────────────────┘

   ┌─────────────────────────────────────────────┐
   │  🔔  Want a browser ping too?               │
   │      Get notified even when this tab isn't  │
   │      open.                                  │
   │      [ Enable push ]   [ Not now ]    ✕     │
   └─────────────────────────────────────────────┘
```

- Shows ONCE per user, ONLY after their first reminder creation
- "Enable" → permission flow (Flow A step 5)
- "Not now" / ✕ → `pushPromptDismissedAt = now`. Re-show after 30 days IF still no subscription
- Hidden in: unsupported browser, iOS Safari (UA filter), already-denied permission, already-subscribed

### Native push notification (rendered by OS, not us)

```
   ┌─────────────────────────────────────────┐
   │  xelasphere.com    now                  │
   │                                         │
   │  🔔  Call mom                           │
   │      Remember to call mom at 9pm        │
   │                                         │
   │           [ 10m ]   [ 1h ]              │
   └─────────────────────────────────────────┘
```

Browser/OS controls styling — we provide title, body, actions, data, icon. Two actions because browsers cap reliably at 2. Body click → chat; action click → snooze.

Browser-specific quirks:
- **Safari macOS 13+:** notifications render but action buttons appear under a "more options" overflow menu rather than inline. Acceptable.
- **Firefox:** full support including inline actions.
- **Chrome/Edge:** full support, also supports `tag` for replacement, `silent`, `requireInteraction`, etc. — we use defaults.

### Settings → Notifications (single toggle)

```
   Settings → Notifications

   Browser notifications:  ●─── On

   When ON: this browser receives push notifications for your reminders.
   When OFF: all subscribed devices are removed; no push is sent.
```

- Toggle state derived from "any pushSubscriptions docs exist for this user"
- ON: triggers permission flow + subscribes the current browser only
- OFF: confirm dialog ("Disable push and remove all subscribed devices?") → DELETE all subs → toggle visually flips

No per-device "Remove" buttons. Single toggle covers 99% of need; per-device cleanup deferred to M5+.

### Service worker lifecycle

`pushSetup.js` registered on DOMContentLoaded:

1. Detect browser support. If `!('Notification' in window) || !('serviceWorker' in navigator)` → exit silently. No banner.
2. If iOS Safari (UA test) → exit silently. No banner. (PWA install required for iOS push; deferred scope.)
3. If permission already denied (`Notification.permission === 'denied'`) → exit silently. Banner would be useless; user must change in browser settings.
4. If permission granted AND no `pushSubscriptions` exist (rare; site data cleared) → silently re-subscribe in the background.
5. Otherwise: hold for the banner show event when first reminder created.

---

## Failure, retry, dedup, observability

### Push delivery edge cases

| Case | Handling |
|---|---|
| `pywebpush.send()` 410 Gone | Delete `pushSubscriptions/{subId}` doc. Log `push_subscription_expired` |
| 413 Payload Too Large | Retry once with body truncated to 256 chars. If still 413, log + skip |
| 429 Too Many Requests | Log + skip. Next reminder retries |
| 5xx | Log + skip. Email already delivered, no retry of the push side |
| One sub fails out of N | Per-sub try/except. Tick succeeds with `successes < N` |
| Corrupt subscription doc (missing keys) | Wrap in try/except ValueError. Log + skip + delete the doc |
| User revokes permission after subscribing | First push 410 → silently delete sub |
| Worker concurrency | M0's transactional claim → only one tick reaches push fan-out per reminder |

### Subscription doc edge cases

| Case | Handling |
|---|---|
| User subscribes from 3 browsers | 3 separate `pushSubscriptions` docs. All receive every push |
| Site-data clear in one browser | That sub's endpoint becomes invalid → first push 410 → doc deleted |
| Sign out + sign back in | Subs persist (keyed by uid). Resume on next reminder |
| Settings toggle Off then On | OFF deletes ALL subs. ON re-prompts and subscribes the current browser |
| Stale sub (no fire in 90d) | Kept until first 410. No aggressive cleanup; storage cost is negligible |

### VAPID edge cases

| Case | Handling |
|---|---|
| `VAPID_PRIVATE_KEY` env unset | `send_push_for_reminder` logs `push_disabled_no_vapid` and returns. Email still delivers |
| Keys rotated (incident response) | All existing tokens invalid; next push attempts return 401 → logged as `push_send_error`. Users re-subscribe via Settings toggle Off+On |
| `pywebpush` version too old for new VAPID draft | Pinned `pywebpush>=2.0`; Dependabot if/when active |

### Observability

New structured log events (worker + routes):

```
push_dispatched              {rid, subCount, successes, expired, failures}
push_subscription_expired    {subId, userAgent, endpoint_host}
push_payload_too_large       {rid, originalBytes, truncatedBytes}
push_send_error              {rid, subId, status, error}
push_disabled_no_vapid       {rid}                # safety log if env misconfigured
```

Frontend (via existing analytics if present):

```
push_permission_granted      {ua, deviceType}
push_permission_denied       {ua, deviceType}
push_subscribed              {subId, ua}
push_unsubscribed_all        {uid, count}
```

No new dashboards. Grep via `journalctl -u xelasphere-reminders*`.

### Scale assumption

Same as M0/M1.1/M1.2: ≤1000 active users, ≤5 push subscriptions per user. Push fan-out adds at most ~1s per reminder (5 subs × ~200ms `pywebpush.send` each, sequential). Acceptable within the 5s worker p99 budget.

---

## Auth, scope, timezone

### Auth

- `POST /push/subscribe` — session-authenticated, CSRF-protected
- `DELETE /push/subscribe` — session-authenticated, CSRF-protected. Deletes ALL of the user's subscriptions
- `GET /push/vapid-public-key` — public. The key is meant to be discoverable; security relies on the private key never leaving the server
- `POST /push/dismiss-prompt` — session-authenticated. Sets `pushPromptDismissedAt`

### Scope

All subscription writes scoped to the authenticated user's uid. Cross-account writes impossible because:
1. Subscriptions live in `users/<uid>/pushSubscriptions/*`
2. Session uid is read server-side; client cannot inject

### Timezone

Notification timing is determined by the reminder's `fireAt` (UTC). Notification CONTENT shows the reminder body verbatim — no localized formatting required. Body text comes from the user's own instructions.

### Rate limiting

- `POST /push/subscribe` — 10/min per user (registrations are one-time per device; this is abuse protection)
- `DELETE /push/subscribe` — 10/min per user
- `POST /push/dismiss-prompt` — 10/min per user
- `GET /push/vapid-public-key` — 60/min per IP (frontend caches in memory)

---

## Testing & rollout

### Test coverage

- **Unit (`push_dispatcher`):**
  - `build_payload(reminder, snoozeToken)` → exact title/body/data/actions shape
  - `_truncate(text, limit)` → hits limit, doesn't break mid-emoji or mid-multibyte
  - 410 path: mock `pywebpush.send` raises WebPushException(status=410) → assert sub deleted
  - 413 path: mock first call 413 + second call OK → assert truncate-and-retry
  - VAPID missing: monkeypatch Config.VAPID_PRIVATE_KEY=None → assert no-op
- **Integration (routes):**
  - `POST /push/subscribe` happy path → 201 + Firestore write
  - `POST /push/subscribe` idempotent on duplicate endpoint → updates lastSeenAt, no second doc
  - `DELETE /push/subscribe` → all user's subs gone
  - `GET /push/vapid-public-key` → returns the env-loaded public key as JSON
  - All endpoints with `PUSH_ENABLED=false` → 503
- **Integration (worker):**
  - Process reminder with 2 subscriptions, one returns 410 → assert one sub deleted, one push sent, log `push_dispatched {successes:1, expired:1}`
  - Process with `PUSH_ENABLED=false` → assert no `pywebpush.send` call
  - Process with empty subs → assert no error, just no-op
- **Manual E2E (per browser):**
  - Chrome desktop: subscribe via banner → send test reminder → notification → click body opens chat → click 10m → snooze backend hit (worker log shows `reminder_snooze_skip`)
  - Chrome Android: same flow
  - Firefox desktop: subscribe + receive + snooze action
  - Safari macOS 13+: subscribe + receive (action buttons appear under "more options" menu rather than inline)
  - iOS Safari: confirm banner does NOT render

### Rollout — single stage with kill switch

1. Land PR with `PUSH_ENABLED=false`. Subscribe endpoint returns 503. Banner hidden via frontend config check
2. **On the server, one-time:** generate VAPID keypair via `python -m py_vapid generate_keys`. Save the private key as `VAPID_PRIVATE_KEY`, public as `VAPID_PUBLIC_KEY`, set `VAPID_CLAIMS_SUB=mailto:noreply@xelasphere.com`. Restart Gunicorn so env propagates
3. Flip `PUSH_ENABLED=true`. From own browser: click the banner on next reminder create. Verify notification arrives + body click + 10m action both work
4. Watch logs 24h. Any `push_send_error` with status != 410 → investigate before declaring done
5. Declare M1.3 done. M1 complete.

### Kill switch

`PUSH_ENABLED=false` causes:
- Banner hidden (frontend reads `window.RemindersModule.config.pushEnabled` injected at template render)
- `POST /push/subscribe`, `DELETE /push/subscribe`, `GET /push/vapid-public-key` all return 503
- `push_dispatcher.send_push_for_reminder` is a no-op; email still delivers
- Existing subscriptions are PRESERVED across flips — flipping On resumes immediately

### Definition of done

- At least one push notification delivers end-to-end on own device
- Push action snooze successfully delays a fire (verified via worker `reminder_snooze_skip` log + delivery at the new time)
- Zero unexpected `push_send_error` events (anything other than 410) in 24h post-flip
- Worker p99 still <5s
- Settings toggle Off/On round-trips cleanly (verified: subscriptions count is 0 after Off, 1 after On)

---

## Explicitly out of scope for M1.3

| Item | Why deferred | Target |
|---|---|---|
| iOS Safari (PWA install required) | Apple-specific friction; needs manifest.json + icon set + install prompt UX | M5+ when browser support stabilizes |
| Per-device list in Settings with "Remove" per device | Single toggle covers 99% of need | M5+ |
| Push notification grouping (multi-reminder bundle) | Rare with current worker p99 | M5+ |
| Server-Sent Events for live status updates | Push covers "tab closed"; SSE is "tab open + status changed" | M5+ |
| Snooze-from-push without internet (offline queue in SW) | Adds significant complexity for an edge case | won't fix |
| Web Push offline queueing (browser closed >7 days) | Browser-policy-dependent; can't engineer | won't fix |
| Token rotation automation | Manual ops procedure for now (documented) | M5+ if rotation becomes frequent |
| Custom per-reminder notification icons | Default uses xelasphere icon for all | M5+ |

---

## Open questions (deferred, not blockers)

- **`urlBase64ToUint8Array` helper.** Web Push spec requires converting the base64url VAPID public key into a `Uint8Array` for `pushManager.subscribe(applicationServerKey: ...)`. This is a 10-line utility every web-push tutorial includes. Bake it into `pushSetup.js`; not worth a library.
- **Notification icon path.** We need a 192×192 PNG at a stable URL (e.g., `/static/img/icon-192.png`). The reminder system already references `Open chat` styling in emails; reuse the same brand color (#10A37F). If no icon exists, the browser falls back to its tab favicon — acceptable but ugly.
- **Service worker scope conflicts.** Registering `/sw.js` at root scope means it intercepts ALL fetches at `/`. We only handle `push` and `notificationclick`; everything else passes through. No conflict expected, but verify on first deploy that fetch passthrough works (file uploads, streaming chat, etc.)
- **`pushManager.subscribe` rejection.** On some browsers (older Firefox), subscribe can reject with `NotAllowedError` even after permission granted, if user has push disabled at OS level. Log as `push_permission_granted_but_subscribe_failed`. Banner shows "Browser blocked push — check OS notification settings."

---

## Related future milestones (out of M1.3)

- **M2 — SMS via Twilio.** Phone capture + E.164 verify, Twilio SDK, channel preference per user
- **M3 — Google Calendar sync.** OAuth scope, one-way push to GCal as event, deletion handling
- **M4 — Contacts / customer attachment.** Net-new contacts data model, bind reminder to contact
- **M5+ — Polish & depth.** iOS Safari support (PWA install), per-device list, custom RRULE builder, custom snooze durations, per-reminder channel preference, notification icon polish, VAPID rotation automation
