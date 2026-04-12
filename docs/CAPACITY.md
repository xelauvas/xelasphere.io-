# Post-Scale-Up Capacity (2026-04-12)

## What shipped

1. **Gunicorn workers:** `sync` → `gthread` (4 workers × 8 threads = 32 concurrent slots).
   - We tried `gevent` first; it deadlocked on Firestore gRPC calls at startup
     (`[Registry] Could not load Firestore state: Timeout of 300.0s exceeded`).
   - gRPC's C-level threading can't be monkey-patched cleanly by gevent. gthread
     uses real OS threads that are natively gRPC-compatible.
2. **Redis** for cross-worker state: rate limits, voice sessions, chat prime cache.
3. **Backpressure semaphores** with fail-fast 503 (LLM=8, TTS=4, Voice=3).
4. **`GET /status`** endpoint for health / slot visibility.
5. **`POST /chat/voice-exit`** endpoint wired into voice mode `exit()`.

## Load test results (2026-04-12)

### /status under 100 concurrent clients

```
ab -n 1000 -c 100 http://127.0.0.1:5000/status

Complete requests:      1000
Failed requests:        0
Requests per second:    695 [#/sec]
Time per request (concurrent): 1.4 ms
p50:                    222 ms
p99:                    286 ms
```

Headroom: can absorb bursts far beyond the expected steady-state load.
Redis handled 598 ops/sec during the test without measurable latency increase.

### End-to-end over HTTPS

`curl https://xelasphere.com/status` → 200 OK in ~125ms, nginx pass-through works.

## Tunables (env vars)

| Variable | Default | Purpose |
|----------|---------|---------|
| `BACKPRESSURE_LLM_SLOTS` | 8 | Max concurrent `/chat/stream` |
| `BACKPRESSURE_TTS_SLOTS` | 4 | Max concurrent `/tts/groq` |
| `BACKPRESSURE_VOICE_SLOTS` | 3 | Max concurrent voice sessions (tracked) |
| `REDIS_URL` | `redis://localhost:6379/0` | Shared Redis client |
| `RATELIMIT_STORAGE_URI` | `redis://localhost:6379/0` | flask-limiter storage |

Raise slot caps if observed load stays below 5s wait on semaphore acquire with no
obvious downstream pressure (orchestrator API limits, Kokoro GPU pinned, etc).

## Rollback

- **gthread misbehaving:** `worker_class = "sync"` in `gunicorn.conf.py`, restart.
- **Redis down:** flask-limiter falls back to memory automatically; voice session
  tracking returns 0 (doesn't block requests).
- **Backpressure too aggressive:** raise slot env vars, restart.

## What we did NOT do

- No Quart/FastAPI rewrite — would have cost weeks for marginal gain beyond gthread.
- No async Flask routes — Flask-under-WSGI wouldn't benefit anyway.
- No Uvicorn — gthread is the right tool for this codebase (Firestore gRPC heavy).

## Next capacity step (when needed)

When gthread's 32-slot ceiling is saturated in practice:
- Increase `threads` to 16 (→ 4×16 = 64 slots) if each worker has memory headroom.
- If that's still not enough, look at horizontal scale (second VPS) before
  Quart/FastAPI migration.
