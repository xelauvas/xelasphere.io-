# Web Push — VAPID Keypair Setup

Web Push requires a VAPID keypair so push services (FCM, Mozilla, Apple) can verify our sends. One-time setup per server install. Document this in your password manager / ops notebook — if `VAPID_PRIVATE_KEY` leaks, you'll need to rotate (which invalidates every user's subscription).

## Generate the keypair

On the server (or any Python environment with `py_vapid` installed):

```bash
/opt/xelasphere/venv/bin/python <<'PY'
from py_vapid import Vapid01
from py_vapid.utils import b64urlencode
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
v = Vapid01()
v.generate_keys()
priv_pem = v.private_pem().decode()
pub_bytes = v.public_key.public_bytes(
    encoding=Encoding.X962,
    format=PublicFormat.UncompressedPoint,
)
print("=== VAPID_PRIVATE_KEY (PEM, paste in env with quotes) ===")
print(priv_pem)
print("=== VAPID_PUBLIC_KEY (base64url, single line) ===")
print(b64urlencode(pub_bytes).decode())
PY
```

Copy both outputs.

## Install in env

Edit `/etc/xelasphere/xelasphere.env`:

```
PUSH_ENABLED=false
VAPID_PRIVATE_KEY="-----BEGIN PRIVATE KEY-----
MII...
-----END PRIVATE KEY-----"
VAPID_PUBLIC_KEY=BNc...
VAPID_CLAIMS_SUB=mailto:noreply@xelasphere.com
```

The private key is multi-line PEM — quote it. The public key is single-line base64url.

## Restart Gunicorn

```bash
sudo systemctl restart xelasphere
```

`HUP` alone is not enough — new env vars need a full restart (see memory `feedback_systemd_env_reload.md`).

## Verify

```bash
curl -s https://xelasphere.com/push/vapid-public-key
```

Expected: `{"key": "BNc..."}` matching the value you set. If you get `503`, `PUSH_ENABLED` is still `false`.

## Flip the kill switch on

Once VAPID keys are loaded and verified, set `PUSH_ENABLED=true` in the env file and `systemctl restart xelasphere` again.

## Key rotation (incident response)

If `VAPID_PRIVATE_KEY` leaks or you suspect compromise:

1. Generate a new keypair (above).
2. Replace `VAPID_PRIVATE_KEY` and `VAPID_PUBLIC_KEY` in env.
3. `sudo systemctl restart xelasphere`.
4. All existing user subscriptions become invalid. The next reminder for each subscribed user surfaces as `push_send_error` with status 401.
5. Users must Settings → Notifications → Off, then On to re-subscribe with the new key.
6. Communicate the rotation (email blast or in-app banner).

No automated rotation flow in M1.3. Defer to M5+ if rotation becomes frequent.

## Why VAPID

`VAPID_CLAIMS_SUB` (a `mailto:` URI) is a contact address push services may reach if our sends start hitting their abuse thresholds. Keep it monitored or forwardable.
