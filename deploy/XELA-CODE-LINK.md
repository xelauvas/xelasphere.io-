# Xela Code link — one account for xelasphere.com and the Xela CLI, one local model

Mirrors claude.com + Claude Code: the web chat and the terminal client share the user's
XelaSphere account and the same model, a Qwen3.6-27B served by llama-server on a GPU
machine that is NOT this server.

## Pieces

| Where | What |
|---|---|
| GPU machine | `llama-server` on :8080 with `--api-key`, plus `xela-link` (reverse SSH tunnel) that exposes it on the server as `127.0.0.1:11434` |
| nginx `api.xelasphere.com` | `/v1/*` → 127.0.0.1:11434 after `auth_request` to the app; `/cli/*` → app `/api/cli/*` (deploy/nginx/api.xelasphere.com.conf) |
| app `app/blueprints/cli_routes.py` | device-code sign-in (`/api/cli/device`, `/api/cli/token`), `/cli/auth` page, `/api/cli/verify` for nginx, `/api/cli/me`, key list/revoke |
| app catalog | model `qwen3.6-27b` (provider `ollama`, i.e. the OpenAI-compatible URL `OLLAMA_API_URL`) |
| Firestore `cli_api_keys` | one doc per issued key, id = sha256(key); Redis caches verifications for 5 min |

## Server env (`/etc/xelasphere/xelasphere.env`)

```
OLLAMA_API_KEY=<llama-server --api-key>      # app → model server
OLLAMA_MODEL=qwen3.6-27b
XELA_API_BASE_URL=https://api.xelasphere.com
XELA_API_LEGACY_KEY=<old static token>       # still accepted by /api/cli/verify
```

nginx needs the same llama key in place of `__LLAMA_KEY__` in deploy/nginx/api.xelasphere.com.conf.

## GPU machine

```
ssh-keygen -t ed25519 -N '' -f ~/.ssh/xela_link
# on the server, for user xela:  restrict,port-forwarding,permitlisten="127.0.0.1:11434" <pubkey>
xela-link --daemon      # keeps the tunnel up, starts llama-server if needed
```

## Client (any computer)

```
npm install -g @xelauvas/xela-cli
xela-local --login      # opens https://xelasphere.com/cli/auth, saves the key in ~/.xela-local/server.env
```
