# Gevent Scale-Up: 4× Workers → 4,000 Concurrent SSE

**Date:** 2026-04-12
**Status:** Approved

## Goal

Raise the concurrent-SSE ceiling from ~95 streams (sync Gunicorn) to ~4,000 (gevent) without rewriting any route handlers, without touching Flask, and without risking the voice mode feature that just shipped.

## Non-Goals

- No migration to Quart / FastAPI / asyncio.
- No per-route rewrite.
- No new external infra beyond a local Redis server.

## Approach

Replace Gunicorn's `sync` worker class with `gevent`. Gevent monkey-patches Python's blocking I/O (`socket`, `ssl`, `time`, most of the stdlib) so cooperative green threads yield during network waits. Each worker can hold ~1,000 concurrent connections instead of 1.

All other changes are supporting infrastructure that's valuable regardless of sync/async:
- **Redis** for cross-worker state (flask_limiter storage, voice session tracking, prime cache).
- **Backpressure semaphores** with fail-fast 503 instead of 300s timeouts.
- **Nginx SSE tuning** verified/hardened.

---

## Architecture Changes

### 1. Gunicorn worker class

`gunicorn.conf.py`:

```python
worker_class = 'gevent'
worker_connections = 1000  # concurrent connections per worker
workers = 4                # same as before — gevent scales WITHIN a worker
# Keep existing: timeout = 120, preload_app, max_requests, post_fork staggering
```

gevent must monkey-patch **before any other imports**. Added at the top of `run.py` (the ASGI entrypoint):

```python
from gevent import monkey
monkey.patch_all(thread=False)  # thread=False for gRPC/Firestore compat
```

### 2. gRPC / Firestore compatibility

`google-cloud-firestore` uses gRPC. Under gevent, gRPC needs cooperative mode:

```bash
pip install gevent grpcio
# grpcio already supports gevent when GRPC_ENABLE_FORK_SUPPORT=0 and thread monkey-patching is disabled
```

Risk mitigation: Firestore session backend + memory_v2/memory_v3 must be validated under gevent. If Firestore grpc deadlocks, fall back to filesystem sessions (already supported via SESSION_TYPE env var) and use Redis for memory caches.

### 3. Redis for cross-worker state

Install:
```bash
apt-get install -y redis-server
pip install redis
```

Three migrations from in-process → Redis:

**a. flask_limiter storage.** Currently `storage_uri = os.getenv('RATELIMIT_STORAGE_URI', 'memory://')`. With 4 workers each having their own in-memory counter, rate limits are effectively 4× looser than configured. Fix: set `RATELIMIT_STORAGE_URI=redis://localhost:6379/0` in systemd env. Single-line change, `flask_limiter` handles the rest.

**b. Voice session tracking.** Currently not tracked globally; only per-request. Add a thin `app/voice_sessions.py` using Redis `INCR`/`DECR` + TTL keys, exposing `register()`, `release()`, `count()`. Used by new `/status` endpoint and backpressure.

**c. Chat prime cache.** Layer 5 of voice mode fires `/chat/prime` on VAD speech-start. Currently a no-op (just touches session). Move to Redis with 10s TTL so the pre-warmed history is actually available across workers when the follow-up `/chat/stream` lands on a different worker.

### 4. Backpressure semaphores

`app/backpressure.py`, gevent-compatible via standard `threading.BoundedSemaphore` (monkey-patched to cooperative):

```python
from threading import BoundedSemaphore
LLM_SLOTS = BoundedSemaphore(8)     # max concurrent LLM streams
VOICE_SLOTS = BoundedSemaphore(3)   # max concurrent voice sessions
TTS_SLOTS = BoundedSemaphore(4)     # TTS protection (Kokoro sidecar also has its own)
```

Wrap `/chat/stream` and `/tts/groq` with acquire-or-503 guards that wait max 5s for a slot. Returns `{"error": "Server busy", "retry_after": 5}` with HTTP 503 if saturated.

New `/status` endpoint returns current slot availability for frontend health UI.

### 5. Nginx SSE verification

Existing nginx config already has `proxy_buffering off` for `/chat/stream`. Verify:
- `proxy_buffering off` on `/chat/stream`, `/tts/*`, `/speech-to-text/*`
- `proxy_read_timeout 120s` (matches Gunicorn timeout)
- `proxy_http_version 1.1` + `Connection ""` (keep-alive)
- `X-Accel-Buffering: no` header sent by Flask responses

No structural nginx changes expected; just a config audit.

---

## Deploy Order

1. **Install:** `pip install gevent redis`, `apt-get install redis-server`.
2. **Start Redis:** `systemctl enable --now redis-server`, verify `redis-cli ping`.
3. **Add monkey-patch** to `run.py` at the very top.
4. **Smoke-test Firestore under gevent** locally (read/write a session, write a memory).
5. **Add Redis-backed rate limiter** (`RATELIMIT_STORAGE_URI`), verify counter increments.
6. **Add `app/voice_sessions.py` + `app/backpressure.py`**, wire into `/chat/stream` and `/tts/groq`.
7. **Add `/status` endpoint.**
8. **Switch `worker_class = 'gevent'`** in gunicorn.conf.py, `worker_connections = 1000`.
9. **Restart xelasphere service.**
10. **Audit nginx** (existing `/etc/nginx/sites-available/xelasphere`).
11. **Load test:** `ab -n 200 -c 40 http://localhost:5000/status`; manual SSE test with multiple concurrent voice sessions.

Each step is rollback-safe — the `worker_class` switch is the riskiest moment, guarded by Firestore smoke-test in step 4.

## Rollback Plan

- **gevent breaks Firestore:** revert `worker_class` to `sync` (1-line git revert), Redis/backpressure stay.
- **Redis down:** rate limiter falls back to memory via `RATELIMIT_STORAGE_URI` default; voice session tracking degrades gracefully (returns 0 count, doesn't block).
- **Backpressure too aggressive (false 503s):** raise `LLM_SLOTS`/`VOICE_SLOTS` in config, redeploy.

## Success Criteria

- `ab -n 500 -c 100 http://localhost:5000/status` returns 100% 200s with sub-50ms p95.
- 20 concurrent voice mode sessions without one blocking another.
- rate-limit counter increments cross-worker (hit same endpoint from 2 workers, see shared count).
- Voice mode barge-in still works (manual regression).
- Kokoro sidecar unchanged, still serving TTS as-is.

## Files Changed

- **Create:** `app/voice_sessions.py`, `app/backpressure.py`, `app/blueprints/status_routes.py`
- **Modify:** `gunicorn.conf.py` (worker class), `run.py` (monkey-patch at top), `app/__init__.py` (register status blueprint, backpressure init), `app/blueprints/chat_routes.py` (wrap `/chat/stream` with backpressure, real prime cache), `app/blueprints/media_routes.py` (wrap TTS with semaphore)
- **Env/config:** add `RATELIMIT_STORAGE_URI=redis://localhost:6379/0` to systemd env; optionally add `REDIS_URL`

## Out of Scope

- Async Flask routes (`async def`) — wouldn't help under WSGI anyway.
- Quart migration — defer until we hit gevent's ceiling.
- Database connection pooling — Firestore manages its own.
- Horizontal scaling / multi-VPS — one step at a time.
