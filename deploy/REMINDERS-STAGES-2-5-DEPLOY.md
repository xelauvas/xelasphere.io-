# Reminders M0 — Stages 2-5 Deploy Checklist

Companion to `REMINDERS-STAGE1-DEPLOY.md`. After Stage 1 is signed off
(backend works, admin received the smoke-test email), advance through
these stages.

All stages share the same deploy commands:

```bash
sudo -u xelasphere bash -lc '
  cd /opt/xelasphere
  git fetch origin
  git pull
'
sudo systemctl restart xelasphere
```

The feature flag stays `REMINDERS_ENABLED=true` throughout. If anything
goes wrong at any stage, flip to `false`, restart, investigate.

---

## Stage 2 — AI intent detection wired (admin only)

After M2 commits are deployed:

- [ ] As admin, open chat, send: `"remind me in 2 minutes to verify stage 2"`
  → Expect: assistant responds naturally; a reminder doc is created in Firestore.
  Check `journalctl -u xelasphere | grep "\[reminders\]"` for `persisted rem_...`.
  Email arrives ~2 minutes later.

- [ ] Send: `"remind me to call mom"` (no time)
  → Expect: NO reminder doc created. Log shows `clarification needed`.
  Assistant should respond conversationally asking when to remind.

- [ ] Send: `"I'm trying to remember the email address"` (false-positive bait)
  → Expect: NO reminder doc, NO clarification log. Regular chat response.

- [ ] Send: `"set a reminder for friday at 10am to pick up dry cleaning"`
  → Expect: doc created with `fireAt` matching next Friday 10am in your tz.

**False-positive audit:** scan recent logs for unexpected `[reminders] AI-created`.
If rate is >10%, tighten regex in `app/services/reminder_intent.py:_REMINDER_PATTERNS`
and redeploy before Stage 3.

- [ ] Append to PR: "Stage 2 verification: 4/4 expected, N false positives observed in M turns."

---

## Stage 3 — Inline card + edit modal (admin only)

- [ ] As admin, send a complete reminder: `"remind me at 18:00 today to verify card"`
  → Expect: a 🔔 reminder card appears in the assistant message, below the text.

- [ ] Click the card → modal opens prefilled with name, instructions, date, time.
- [ ] Change the name → Save → card updates without page reload.
- [ ] Click the card again → Reschedule for 5 minutes later → Save → email arrives at new time.
- [ ] Pause a future reminder → card greys out → email does NOT arrive at original time.
- [ ] Resume → email arrives at correct (still-paused-from) time.
- [ ] Delete a future reminder → card disappears → no email.
- [ ] Try Save with a past date/time → server returns 400 with "must be in the future".
- [ ] Reload page mid-flight → card re-renders correctly from the persisted message.
- [ ] Force a failure (set SENDGRID_API_KEY to a deliberately-wrong value temporarily,
      fire a reminder, restore the key) → card shows red `failed` state after 3 attempts.

- [ ] Append to PR: "Stage 3 UX QA: 10/10 paths verified."

---

## Stage 4 — Schedules tab + manual create

- [ ] Open Settings → Schedules → see "Upcoming" + "Past" sections populated.
- [ ] Click "+ New reminder" → modal opens empty with defaults (1h from now, browser tz).
- [ ] Fill name + future date/time → Save → reminder appears in Upcoming.
- [ ] Click any existing row → modal opens prefilled, click Save → row updates.
- [ ] Pause → row greys.
- [ ] Delete → row disappears.
- [ ] Wait for a reminder to fire → on next Schedules-tab open, it has moved from Upcoming to Past.
- [ ] Past section "show/hide" toggle works.

- [ ] Append to PR: "Stage 4 QA: 8/8 paths verified."

---

## Stage 5 — Open to all + 48h monitoring

This is the actual launch. Stage 5 is "flip nothing, just monitor."

- [ ] No code change. The feature is already on (`REMINDERS_ENABLED=true`).
- [ ] Existing safeguards are in place:
  - Rate limit on POST `/reminders`: 60/hour per user
  - SendGrid free tier cap: 100 emails/day total
  - 3-attempt retry then fail
  - Stuck-lock sweep every minute
- [ ] Optional pre-launch: confirm the reminders-rail-item in settings is wired
      so non-admin users can see "Schedules" (today it should already be visible —
      placeholder is gone).

### 48h monitoring queries

Run every ~6h for two days. Record results in the PR.

```bash
# Worker health — should be ~360 fetches per 6h (one per minute)
sudo journalctl -u xelasphere-reminders.service --since "6 hours ago" | grep -c '"fetched"'

# Delivery success rate
SENT=$(sudo journalctl -u xelasphere-reminders.service --since "6 hours ago" | grep -c '"reminder_sent"')
FAIL=$(sudo journalctl -u xelasphere-reminders.service --since "6 hours ago" | grep -c '"reminder_failed"')
echo "sent=$SENT failed=$FAIL ratio=$(awk "BEGIN{print $SENT/($SENT+$FAIL+0.0001)}")"
# Goal: ratio ≥ 0.99

# Stuck-lock incidents (lock_expired count)
sudo journalctl -u xelasphere-reminders.service --since "6 hours ago" | grep -c '"lock_expired"'
# Goal: 0 in steady state. Non-zero → worker is crashing somewhere, investigate.

# P99 worker run time
sudo systemctl show xelasphere-reminders.service -p ExecMainStartTimestamp,ExecMainExitTimestampMonotonic
# Eyeball — each invocation should finish in well under 5s typical.

# SendGrid daily quota
# Check the SendGrid dashboard. Goal: under 100/day on free tier.
```

### Definition of done (final)

- [ ] All 5 rollout stages green (1–4 done, 5 demonstrated)
- [ ] 48h post-launch: zero `lock_expired` log entries
- [ ] p99 worker run time < 5s
- [ ] SendGrid delivery rate ≥ 99%
- [ ] At least 3 real reminders scheduled and received without intervention
- [ ] The customer-payment use case from the spec works end-to-end:
      "remind my customer to pay £184 in the morning" → email arrives at 09:00 local.

When all checked: merge the PR.

---

## Kill switch (reminder)

```bash
sudo sed -i 's/^REMINDERS_ENABLED=true/REMINDERS_ENABLED=false/' /etc/xelasphere/xelasphere.env
sudo systemctl restart xelasphere
# Existing reminders untouched. CRUD on existing docs (GET/PATCH/DELETE) stays
# available so users can clean up. Only POST and the worker are gated.
```
