# Reminders M0 — Stage 1 Deploy Checklist

This is the **operator handoff** for Task 1.15 of the M0 plan. Do not advance to
Stage 2 (AI intent detection) until every checkbox here passes.

## Prerequisites

- [ ] SendGrid account created
- [ ] SendGrid API key generated with "Mail Send" permission scope
- [ ] xelasphere.io domain authenticated in SendGrid (DNS records propagated)
  - SendGrid → Settings → Sender Authentication → Authenticate Your Domain
  - Add the CNAME records SendGrid generates for SPF + DKIM
  - Wait for "verified" status (minutes to hours after DNS propagation)
- [ ] firebase CLI available (or operator can deploy rules + indexes via console)

## Steps

### 1. Pull code on prod

```bash
sudo -u xelasphere bash -lc '
  cd /opt/xelasphere
  git fetch origin
  git checkout feat/reminders-m0
  git pull
  ./venv/bin/pip install -r requirements-cpu.txt
'
```

### 2. Set env vars

Append to `/etc/xelasphere/xelasphere.env` (as root):

```
REMINDERS_ENABLED=true
SENDGRID_API_KEY=SG.your-real-key
SENDGRID_FROM_EMAIL=noreply@xelasphere.io
SENDGRID_FROM_NAME=XelaSphere Reminders
```

Permissions stay `root:xelasphere 0640` (matches existing convention).

### 3. Deploy Firestore rules + indexes

```bash
cd /opt/xelasphere
firebase deploy --only firestore:rules,firestore:indexes
```

Index builds take 1-5 min. If `firebase` CLI is unavailable, deploy via the
Firebase console:
- Rules: paste `firestore.rules` into Firebase console → Firestore → Rules
- Indexes: Firebase console → Firestore → Indexes → declare the two
  collection-group indexes from `firestore.indexes.json`

### 4. Restart Flask + install reminders timer

```bash
sudo systemctl restart xelasphere
sudo bash /opt/xelasphere/deploy/install-reminders-units.sh
```

Verify:
```bash
sudo systemctl status xelasphere-reminders.timer
sudo systemctl list-timers xelasphere-reminders.timer
sudo journalctl -u xelasphere-reminders.service -n 50
```

Within ~2 minutes the worker should run and log:
```json
{"ts":"...","level":"INFO","logger":"reminders_worker","msg":"fetched","count":0}
```

### 5. Smoke test as admin

Get your session + CSRF token from the live site (devtools → Application → Cookies),
then schedule a reminder 2 minutes in the future.

**Important — fireAtLocal format:** use a **naive** ISO datetime with **no timezone
suffix** (no `Z`, no `+00:00`). The string is interpreted in the `timezone` field
you supply alongside it. If you accidentally include a tz suffix, the server uses
the suffix and silently ignores the `timezone` field, producing a wrong fire time.

For Europe/London (`date -d` produces local time, which is what we want):

```bash
# Build a naive local ISO string for 'now + 2 minutes' in your *local* tz
LOCAL_FIRE=$(date -d '+2 minutes' +%Y-%m-%dT%H:%M:%S)
# Verify it has NO 'Z' or '+HH:MM' at the end:
echo "$LOCAL_FIRE"

curl -X POST https://xelasphere.io/reminders \
  -H "Content-Type: application/json" \
  -H "Cookie: session=<your-cookie>" \
  -H "X-CSRFToken: <your-csrf-token>" \
  -d "$(cat <<JSON
{
  "name": "M0 smoke test",
  "instructions": "If this arrives in my inbox, the full server loop works.",
  "fireAtLocal": "$LOCAL_FIRE",
  "timezone": "Europe/London"
}
JSON
)"
```

Should return `{"id":"rem_...", "reminder":{...}}` with HTTP 201.

### 6. Verify Firestore + index

Firebase console → Firestore → `users/<your-uid>/reminders/rem_...`.

Confirm:
- Doc exists with `status=scheduled`
- `fireAt` is in UTC, 2 minutes ahead of now

If the worker's first run logs a "missing index" Firestore error with a
console link, **click the link to create the index** and wait 1-5 min.

### 7. Wait for delivery (max 3 min)

Within 60-120 seconds of the scheduled `fireAt`:
- An email titled "Reminder: M0 smoke test" should land in your inbox
- Worker logs: `{"ts":"...","msg":"reminder_sent","name":"M0 smoke test","attempts":1,"sg_status":202}`
- Firestore doc now `status=delivered`, `deliveredAt` set

Check spam folder if it doesn't arrive — SendGrid domain auth without DMARC may
land in spam initially.

### 8. Stage 1 sign-off

If all 7 prior checks pass:

- [ ] Append to PR description: "Stage 1 smoke test: email received at HH:MM, doc status=delivered, worker run time <Xs, no stuck locks."
- [ ] Greenlight Stage 2 (M2 — AI intent detection)

## Rollback

If anything is wrong:

```bash
# Disable the feature (existing reminders untouched)
sudo sed -i 's/^REMINDERS_ENABLED=true/REMINDERS_ENABLED=false/' /etc/xelasphere/xelasphere.env
sudo systemctl restart xelasphere

# Stop the worker timer (no further sends; queue rebuilds when flag flips back)
sudo systemctl stop xelasphere-reminders.timer
```
