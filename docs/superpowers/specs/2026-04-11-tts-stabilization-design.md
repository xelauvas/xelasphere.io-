# TTS Stabilization — Kokoro Sidecar + Resource Fixes

**Date:** 2026-04-11
**Status:** Approved
**Context:** Server crashed at 1500%+ CPU from three compounding TTS pipeline bombs. Root cause: 4 Gunicorn workers each holding Kokoro ONNX + spaCy in-process, with no concurrency control. Ollama (removed separately) compounded the issue.

## Problem

When voice mode triggers TTS, each Gunicorn worker runs Kokoro inference in-process:
- Kokoro loads ONNX model (~1GB) + spaCy `en_core_web_sm` tok2vec + tagger
- ONNX configured for 4 threads per worker
- No concurrency limit — all 4 workers can run TTS simultaneously
- 4 workers x 4 ONNX threads + spaCy neural inference = 16+ CPU-hungry threads on 16 cores
- Result: 100% CPU saturation, worker timeouts, SIGKILL, full outage

Secondary issues: Moonshot dead for 957+ health checks, model watcher spamming dozens of irrelevant models, Ollama remnants in watcher code.

## Fixes

### Fix 2: Kokoro Sidecar (primary architectural fix)

**Architecture:**
```
Gunicorn worker → requests.post("127.0.0.1:8001/synthesize", timeout=45)
                    │
                    ├─ 200 → return audio/wav
                    └─ fail/timeout → return None → Gemini fallback → Groq fallback
```

**New file: `/root/xelas.io/kokoro_sidecar.py`**
- Single uvicorn worker, single process
- Loads KPipeline + KModel + spaCy + ONNX once at startup
- FastAPI: `POST /synthesize` and `GET /health`
- `asyncio.Semaphore(2)` — max 2 concurrent inferences via `run_in_executor`
- Accepts: `{text, voice, speed, lang}` — voice is already a Kokoro voice ID, lang is a Kokoro lang code
- Returns: `audio/wav` (200) or error (500)
- Multi-language: lazy-loads phonemizers per lang_code

**Changes to `media_routes.py`:**
- `_generate_kokoro_tts()` becomes a thin HTTP call to the sidecar
- Voice mapping logic (Gemini name -> Kokoro ID, emotion detection, speed adjustment) stays in media_routes
- Remove: `_get_kokoro()`, `_kokoro_session`, `_kokoro_phonemizers`, `_kokoro_vocab`, `_kokoro_voices`, `_kokoro_loading`, all ONNX/numpy/soundfile imports for TTS
- Remove: `_load_kokoro_voice()` function

**Changes to `app/__init__.py`:**
- Remove Kokoro warmup thread (`_warm_kokoro` and the `threading.Thread` call)

**New systemd service: `/etc/systemd/system/kokoro-sidecar.service`**
- `Type=simple`, `Restart=always`, `RestartSec=5`
- Uses project venv, port 8001, 1 worker

**Resource impact:**
- RAM: 7.4GB (4 x 1.85GB) -> 4.9GB (4 x 0.85GB + 1 x 1.5GB) = ~2.5GB saved
- CPU: spaCy + ONNX capped at 2 concurrent in 1 process vs 4x4 threads across workers
- Isolation: Kokoro crash doesn't take down XelaSphere

### Fix 3: Gunicorn hardening

**New file: `gunicorn.conf.py`**
- `post_fork` hook: stagger workers by 3s each (0s, 3s, 6s, 9s) to prevent startup stampede
- `timeout = 120` (down from 300 — monitor SSE streams, bump to 180 if streams get cut)
- `max_requests = 500` + `max_requests_jitter = 50` (recycle workers to prevent memory bloat)
- `workers = 4`, `bind = "127.0.0.1:5000"`

**Update systemd service** to use: `gunicorn -c gunicorn.conf.py run:app`

### Fix 4: Remove Moonshot

Down for 957+ consecutive health checks. Dead provider.

- Remove all `("moonshot", ...)` from `FALLBACK_CHAINS` in `config.py`
- Remove moonshot from `_check_all_providers()` in `watcher.py`
- Remove `_ping_moonshot()` function
- Remove from `PROVIDER_ENDPOINTS`
- Remove from accountant status loop
- Keep `MOONSHOT_API_KEY` env var (harmless, easy to re-add)

### Fix 5: Silence model watcher spam

`_classify_new_model()` dumps everything into `general_chat` by default. Whisper, TTS, embeddings, realtime, legacy models all get registered.

- Add `IGNORED_MODEL_PATTERNS` set in `watcher.py`
- Filter in `_classify_new_model()`: return `None` for non-chat models
- Caller skips `None` results

Patterns to ignore: `whisper`, `tts-`, `dall-e`, `davinci-`, `babbage-`, `embedding`, `moderation`, `realtime`, `audio`, `canopylabs/`, `allam-`

### Fix 6: Clean up Ollama remnants in watcher

- Remove `_check_ollama()` function and its call in `_check_all_providers()`
- Remove `"ollama"` from `cost_tier` check in `_classify_new_model()`

## Files touched

| File | Action |
|------|--------|
| `kokoro_sidecar.py` | New — sidecar service |
| `gunicorn.conf.py` | New — gunicorn config |
| `/etc/systemd/system/kokoro-sidecar.service` | New — systemd unit |
| `/etc/systemd/system/xelasphere.service` | Edit — use gunicorn.conf.py |
| `app/blueprints/media_routes.py` | Edit — replace in-process Kokoro with HTTP call |
| `app/__init__.py` | Edit — remove Kokoro warmup thread |
| `app/orchestrator/config.py` | Edit — remove moonshot from chains |
| `app/orchestrator/watcher.py` | Edit — remove moonshot, ollama, add model filter |
| `app/orchestrator/accountant.py` | Edit — remove moonshot from status |

## Not touched

- LLM routing logic, chat routes, voice mapping, artifact system, frontend code
- Gemini/Groq TTS fallback chain (unchanged — sidecar failure triggers existing fallbacks)

## Success criteria

1. `curl http://127.0.0.1:8001/health` returns `{"status":"ok"}`
2. CPU stays below 200% at idle
3. Test TTS request completes in under 5 seconds
4. `systemctl status xelasphere` shows active with no restarts
5. `systemctl status kokoro-sidecar` shows active
6. Watcher logs show no spam of irrelevant models
7. No moonshot health check warnings
