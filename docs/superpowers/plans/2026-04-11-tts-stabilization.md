# TTS Stabilization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Isolate Kokoro TTS into a sidecar service so CPU-intensive inference can't crash XelaSphere, plus clean up dead providers and watcher spam.

**Architecture:** Kokoro runs in a single-process FastAPI sidecar on port 8001 with a concurrency semaphore. Gunicorn workers call it over HTTP with a 45s timeout. Existing Gemini/Groq TTS fallback chain is unchanged — sidecar failure triggers fallbacks automatically.

**Tech Stack:** FastAPI + uvicorn (sidecar), requests (HTTP client in Flask), systemd (service management)

**Spec:** `docs/superpowers/specs/2026-04-11-tts-stabilization-design.md`

---

### Task 1: Install sidecar dependencies

**Files:**
- None (system packages only)

- [ ] **Step 1: Install FastAPI and uvicorn in the venv**

```bash
/root/xelas.io/xelasphere.io-/venv/bin/pip install fastapi uvicorn
```

Expected: Successfully installed fastapi, uvicorn, and their dependencies.

- [ ] **Step 2: Verify imports work**

```bash
/root/xelas.io/xelasphere.io-/venv/bin/python3 -c "from fastapi import FastAPI; from kokoro import KPipeline; print('OK')"
```

Expected: `OK`

---

### Task 2: Create the Kokoro sidecar service

**Files:**
- Create: `kokoro_sidecar.py` (project root)

- [ ] **Step 1: Create `kokoro_sidecar.py`**

```python
"""
Kokoro TTS Sidecar — single-process FastAPI service.
Loads Kokoro + spaCy + ONNX once. Max 2 concurrent inferences via semaphore.
XelaSphere workers call this over HTTP instead of loading models in-process.
"""
import asyncio
import io
import logging
import wave
import time

import numpy as np
from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel
from kokoro import KPipeline

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("kokoro-sidecar")

app = FastAPI()
semaphore = asyncio.Semaphore(2)

# ── Lazy-loaded pipelines (one per lang_code) ──
_pipelines = {}

LANG_CODES = {'a', 'b', 'e', 'f', 'h', 'i', 'j', 'z'}


def _get_pipeline(lang_code: str) -> KPipeline:
    """Get or create a KPipeline for the given language code."""
    if lang_code not in _pipelines:
        if lang_code not in LANG_CODES:
            lang_code = 'a'
        logger.info(f"Loading KPipeline for lang={lang_code}...")
        _pipelines[lang_code] = KPipeline(lang_code=lang_code, repo_id='hexgrad/Kokoro-82M')
        logger.info(f"KPipeline lang={lang_code} ready.")
    return _pipelines[lang_code]


class TTSRequest(BaseModel):
    text: str
    voice: str = "af_heart"
    speed: float = 1.0
    lang: str = "a"


def _run_tts(text: str, voice: str, speed: float, lang: str) -> bytes:
    """Run Kokoro inference synchronously. Called via run_in_executor."""
    pipeline = _get_pipeline(lang)

    if len(text) > 8000:
        text = text[:8000]

    start = time.time()
    audio_chunks = []
    for _, _, audio in pipeline(text, voice=voice, speed=speed):
        if audio is not None:
            audio_chunks.append(audio)

    if not audio_chunks:
        raise ValueError("No audio generated")

    combined = np.concatenate(audio_chunks)
    elapsed = time.time() - start
    duration = len(combined) / 24000

    logger.info(
        f"TTS lang={lang} voice={voice} speed={speed:.2f} "
        f"{len(text)}ch -> {duration:.1f}s audio in {elapsed:.1f}s "
        f"(RTF {elapsed / max(duration, 0.1):.2f}x)"
    )

    # Convert float32 -> 16-bit PCM WAV
    pcm_16 = (np.clip(combined, -1, 1) * 32767).astype(np.int16)
    buf = io.BytesIO()
    with wave.open(buf, 'wb') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(24000)
        wf.writeframes(pcm_16.tobytes())

    return buf.getvalue()


@app.post("/synthesize")
async def synthesize(req: TTSRequest):
    async with semaphore:
        loop = asyncio.get_event_loop()
        try:
            audio_bytes = await loop.run_in_executor(
                None, _run_tts, req.text, req.voice, req.speed, req.lang
            )
            return Response(content=audio_bytes, media_type="audio/wav")
        except Exception as e:
            logger.error(f"TTS failed: {e}")
            raise HTTPException(status_code=500, detail=str(e))


@app.get("/health")
async def health():
    return {"status": "ok"}


# Pre-load English pipeline at startup
logger.info("Pre-loading English pipeline...")
_get_pipeline('a')
logger.info("Sidecar ready.")
```

- [ ] **Step 2: Smoke-test the sidecar locally**

```bash
cd /root/xelas.io/xelasphere.io-
/root/xelas.io/xelasphere.io-/venv/bin/python3 -c "
from kokoro_sidecar import _run_tts
audio = _run_tts('Hello world.', 'af_heart', 1.0, 'a')
print(f'Generated {len(audio)} bytes')
"
```

Expected: `Generated NNNNN bytes` (some positive number, typically 30k-100k)

- [ ] **Step 3: Commit**

```bash
git add kokoro_sidecar.py
git commit -m "feat: add Kokoro TTS sidecar service"
```

---

### Task 3: Create systemd service for the sidecar

**Files:**
- Create: `/etc/systemd/system/kokoro-sidecar.service`

- [ ] **Step 1: Write the systemd unit file**

Write to `/etc/systemd/system/kokoro-sidecar.service`:

```ini
[Unit]
Description=Kokoro TTS Sidecar
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/root/xelas.io/xelasphere.io-
Environment=PATH=/root/xelas.io/xelasphere.io-/venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
ExecStart=/root/xelas.io/xelasphere.io-/venv/bin/uvicorn kokoro_sidecar:app --host 127.0.0.1 --port 8001 --workers 1
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 2: Enable and start the service**

```bash
sudo systemctl daemon-reload
sudo systemctl enable kokoro-sidecar
sudo systemctl start kokoro-sidecar
```

- [ ] **Step 3: Verify health endpoint (wait ~30s for model load)**

```bash
curl -s http://127.0.0.1:8001/health
```

Expected: `{"status":"ok"}`

- [ ] **Step 4: Test a real TTS request**

```bash
curl -s -X POST http://127.0.0.1:8001/synthesize \
  -H "Content-Type: application/json" \
  -d '{"text":"Hello, this is a test.","voice":"af_heart","speed":1.0,"lang":"a"}' \
  -o /tmp/test_sidecar.wav && \
  ls -la /tmp/test_sidecar.wav
```

Expected: A WAV file, 30-100KB in size.

---

### Task 4: Rewire `media_routes.py` to call the sidecar

**Files:**
- Modify: `app/blueprints/media_routes.py` (lines 21-110, 768-841)

- [ ] **Step 1: Remove in-process Kokoro globals and functions**

Remove these lines/blocks from `app/blueprints/media_routes.py`:

Lines 21-26 — the module-level globals:
```python
# ── Kokoro ONNX TTS (local, free, unlimited, 3x real-time on q4) ──
_kokoro_session = None
_kokoro_phonemizers = {}  # lang_code → KPipeline
_kokoro_vocab = None
_kokoro_voices = {}
_kokoro_loading = False
```

Replace with:
```python
# ── Kokoro TTS via sidecar (127.0.0.1:8001) ──
KOKORO_SIDECAR_URL = 'http://127.0.0.1:8001/synthesize'
KOKORO_SIDECAR_TIMEOUT = 45  # seconds — fail fast, let Gemini fallback handle it
```

Line 28 — remove KOKORO_MODEL_DIR:
```python
KOKORO_MODEL_DIR = '/root/xelas.io/xelasphere.io-/kokoro-onnx'
```

Lines 49-93 — remove entire `_get_kokoro()` function.

Lines 96-109 — remove entire `_load_kokoro_voice()` function.

- [ ] **Step 2: Rewrite `_generate_kokoro_tts()` as a thin HTTP call**

Replace lines 768-841 (the entire `_generate_kokoro_tts` function) with:

```python
def _generate_kokoro_tts(text, voice='Sulafat', lang='en'):
    """Generate TTS via Kokoro sidecar. Returns (audio_bytes, mime_type) or None."""
    import time as _time

    # Determine Kokoro language code
    lang_code = KOKORO_LANG_MAP.get(lang, 'a')

    # Map voice name to Kokoro voice ID
    if voice and '_' in voice and voice.split('_')[0] in ('af', 'am', 'bf', 'bm', 'ef', 'em', 'ff', 'jf', 'jm', 'zf', 'zm', 'hf', 'hm', 'if', 'im', 'pf', 'pm'):
        kokoro_voice = voice
    elif lang_code != 'a' and voice in ('auto', 'Sulafat', 'Achird', 'Puck'):
        kokoro_voice = KOKORO_LANG_DEFAULT_VOICE.get(lang_code, 'af_heart')
    else:
        kokoro_voice = KOKORO_VOICES.get(voice, 'af_heart')

    # Emotion-aware speed
    speed_val = _detect_speech_speed(text)

    try:
        start = _time.time()
        resp = requests.post(
            KOKORO_SIDECAR_URL,
            json={
                'text': text,
                'voice': kokoro_voice,
                'speed': speed_val,
                'lang': lang_code,
            },
            timeout=KOKORO_SIDECAR_TIMEOUT,
        )
        elapsed = _time.time() - start

        if resp.status_code == 200:
            audio_bytes = resp.content
            logger.info(f"Kokoro sidecar voice={kokoro_voice} speed={speed_val:.2f} {len(text)}ch -> {len(audio_bytes)} bytes in {elapsed:.1f}s")
            return audio_bytes, 'audio/wav'
        else:
            logger.warning(f"Kokoro sidecar returned {resp.status_code}: {resp.text[:200]}")
            return None
    except requests.Timeout:
        logger.warning(f"Kokoro sidecar timeout ({KOKORO_SIDECAR_TIMEOUT}s)")
        return None
    except requests.ConnectionError:
        logger.warning("Kokoro sidecar not reachable")
        return None
    except Exception as e:
        logger.warning(f"Kokoro sidecar error: {e}")
        return None
```

- [ ] **Step 3: Verify the caller code in `generate_groq_tts()` is unchanged**

Read lines 960-968 of `media_routes.py` and confirm this code is untouched — it already handles `_generate_kokoro_tts()` returning `None` by falling through to Gemini:

```python
    kokoro_result = _generate_kokoro_tts(text, voice, lang)
    if kokoro_result:
        audio_bytes, mime_type = kokoro_result
        ...
```

No change needed here — the function signature and return type are identical.

- [ ] **Step 4: Commit**

```bash
git add app/blueprints/media_routes.py
git commit -m "feat: rewire Kokoro TTS to call sidecar over HTTP"
```

---

### Task 5: Remove Kokoro warmup from `app/__init__.py`

**Files:**
- Modify: `app/__init__.py` (lines 244-253)

- [ ] **Step 1: Remove the warmup block**

Remove lines 244-253:

```python
    # ── Warm Kokoro TTS on startup (prevents 7s cold-start on first user request) ──
    import threading
    def _warm_kokoro():
        try:
            from app.blueprints.media_routes import _generate_kokoro_tts
            _generate_kokoro_tts('Hello.', 'Puck')
            app.logger.info('[Kokoro ONNX] Worker warmed up')
        except Exception as e:
            app.logger.warning(f'[Kokoro ONNX] Warmup failed: {e}')
    threading.Thread(target=_warm_kokoro, daemon=True).start()
```

The sidecar pre-loads on its own startup. No warmup needed in Gunicorn workers.

- [ ] **Step 2: Commit**

```bash
git add app/__init__.py
git commit -m "fix: remove Kokoro warmup thread — sidecar handles model loading"
```

---

### Task 6: Create `gunicorn.conf.py` and update systemd service

**Files:**
- Create: `gunicorn.conf.py` (project root)
- Modify: `/etc/systemd/system/xelasphere.service`

- [ ] **Step 1: Create `gunicorn.conf.py`**

```python
"""Gunicorn configuration for XelaSphere."""
import os

bind = "127.0.0.1:5000"
workers = 4
timeout = 120  # Monitor SSE streams — bump to 180 if streams get cut mid-response
max_requests = 500
max_requests_jitter = 50


def post_fork(server, worker):
    """Stagger worker startup to prevent CPU stampede on restart."""
    import time
    delay = (worker.age % 4) * 3  # 0s, 3s, 6s, 9s
    if delay:
        server.log.info(f"Worker {worker.pid} staggering startup by {delay}s")
        time.sleep(delay)
```

- [ ] **Step 2: Update the systemd service to use gunicorn.conf.py**

Edit `/etc/systemd/system/xelasphere.service`, change the `ExecStart` line from:

```
ExecStart=/root/xelas.io/xelasphere.io-/venv/bin/gunicorn -w 4 -b 127.0.0.1:5000 --timeout 300 run:app
```

To:

```
ExecStart=/root/xelas.io/xelasphere.io-/venv/bin/gunicorn -c gunicorn.conf.py run:app
```

- [ ] **Step 3: Reload and restart**

```bash
sudo systemctl daemon-reload
sudo systemctl restart xelasphere
sudo systemctl status xelasphere
```

Expected: `active (running)`, 4 worker PIDs visible.

- [ ] **Step 4: Commit**

```bash
git add gunicorn.conf.py
git commit -m "feat: add gunicorn.conf.py — staggered startup, timeout 120, worker recycling"
```

---

### Task 7: Remove Moonshot from orchestrator

**Files:**
- Modify: `app/orchestrator/config.py` (lines 15, 42, 51, 60, 72)
- Modify: `app/orchestrator/watcher.py` (lines 62, 182-222)
- Modify: `app/orchestrator/accountant.py`

- [ ] **Step 1: Remove moonshot from `config.py`**

In `app/orchestrator/config.py`:

Remove the moonshot entry from PROVIDER_ENDPOINTS (line 15):
```python
    "moonshot": None,   # no /models endpoint — use ping
```

Remove all `("moonshot", ...)` tuples from FALLBACK_CHAINS:
- Line 42 in coding: `("moonshot", "kimi-k2.5", "tier1"),`
- Line 51 in reasoning: `("moonshot", "kimi-latest", "tier1"),`
- Line 60 in general_chat: `("moonshot", "kimi-latest", "tier1"),`
- Line 72 in long_context: `("moonshot", "kimi-latest", "tier1"),`

Remove `"kimi"` from MODEL_FAMILIES (line 105):
```python
    "kimi":     ["kimi"],
```

- [ ] **Step 2: Remove moonshot from `watcher.py`**

In `app/orchestrator/watcher.py`:

Remove moonshot from the providers dict in `_check_all_providers()` (line 62):
```python
        "moonshot": getattr(Config, "MOONSHOT_API_KEY", ""),
```

Remove the entire `_ping_moonshot()` function (lines 202-222).

Remove the moonshot branch in `_fetch_live_models()` (lines 182-183):
```python
    if provider == "moonshot":
        return _ping_moonshot(api_key)
```

- [ ] **Step 3: Remove moonshot from `accountant.py`**

Moonshot is already absent from the accountant status loop (removed in prior commit — verify by reading the file). If it's still there, remove it.

- [ ] **Step 4: Commit**

```bash
git add app/orchestrator/config.py app/orchestrator/watcher.py app/orchestrator/accountant.py
git commit -m "fix: remove dead Moonshot provider — down for 957+ consecutive checks"
```

---

### Task 8: Add model watcher spam filter

**Files:**
- Modify: `app/orchestrator/watcher.py` (lines 141-150, 298-333)

- [ ] **Step 1: Add ignored model patterns constant**

Add near the top of `watcher.py` (after the imports, around line 18):

```python
# Models that should never be auto-registered as chat candidates
IGNORED_MODEL_PATTERNS = frozenset({
    'whisper', 'tts-', 'dall-e', 'davinci-', 'babbage-',
    'embedding', 'moderation', 'realtime', 'audio',
    'canopylabs/', 'allam-',
})
```

- [ ] **Step 2: Make `_classify_new_model()` return `None` for ignored models**

At the top of `_classify_new_model()` (line 299, after `name = model_id.lower()`), add:

```python
    # Skip non-chat models (TTS, whisper, embeddings, legacy, etc.)
    if any(pat in name for pat in IGNORED_MODEL_PATTERNS):
        return None
```

- [ ] **Step 3: Update the caller to skip `None` results**

In `_check_provider()`, change lines 144-150 from:

```python
    for model_id in new_ids:
        meta = live_models[model_id]
        new_model = _classify_new_model(model_id, meta, provider)
        registry.add_discovered(new_model)
        new_models_list.append(model_id)

        logger.info(f"🆕 NEW [{provider}]: {model_id} → {new_model.strengths}")
```

To:

```python
    for model_id in new_ids:
        meta = live_models[model_id]
        new_model = _classify_new_model(model_id, meta, provider)
        if new_model is None:
            continue
        registry.add_discovered(new_model)
        new_models_list.append(model_id)

        logger.info(f"🆕 NEW [{provider}]: {model_id} → {new_model.strengths}")
```

- [ ] **Step 4: Commit**

```bash
git add app/orchestrator/watcher.py
git commit -m "fix: filter non-chat models from watcher auto-discovery"
```

---

### Task 9: Clean up Ollama remnants in watcher

**Files:**
- Modify: `app/orchestrator/watcher.py` (lines 65-71, 225-242, 321)

- [ ] **Step 1: Remove Ollama check from `_check_all_providers()`**

Remove lines 65-71:

```python
    # Ollama is local — check separately
    ollama_url = getattr(Config, "OLLAMA_API_URL", None)
    if ollama_url:
        try:
            _check_ollama(ollama_url)
        except Exception as e:
            logger.warning(f"[Watcher] Ollama check failed: {e}")
```

- [ ] **Step 2: Remove the entire `_check_ollama()` function**

Remove lines 225-242 (the `_check_ollama` function).

- [ ] **Step 3: Remove `"ollama"` from cost_tier check**

In `_classify_new_model()` (line 321), change:

```python
    cost_tier = "free" if provider in ("groq", "cerebras", "ollama") else "paid"
```

To:

```python
    cost_tier = "free" if provider in ("groq", "cerebras") else "paid"
```

- [ ] **Step 4: Commit**

```bash
git add app/orchestrator/watcher.py
git commit -m "fix: remove Ollama remnants from model watcher"
```

---

### Task 10: Restart services and verify success criteria

**Files:**
- None (operational verification)

- [ ] **Step 1: Restart both services**

```bash
sudo systemctl daemon-reload
sudo systemctl restart kokoro-sidecar
sudo systemctl restart xelasphere
```

- [ ] **Step 2: Verify sidecar health**

```bash
curl -s http://127.0.0.1:8001/health
```

Expected: `{"status":"ok"}`

- [ ] **Step 3: Verify XelaSphere is running**

```bash
sudo systemctl status xelasphere | head -10
```

Expected: `active (running)`, 4 workers + 1 master.

- [ ] **Step 4: Test end-to-end TTS through XelaSphere**

```bash
# Check CPU baseline
cat /proc/loadavg

# Test TTS via the sidecar directly
time curl -s -X POST http://127.0.0.1:8001/synthesize \
  -H "Content-Type: application/json" \
  -d '{"text":"This is a test of the Kokoro sidecar service.","voice":"af_heart","speed":1.0,"lang":"a"}' \
  -o /tmp/test_final.wav

ls -la /tmp/test_final.wav
cat /proc/loadavg
```

Expected:
- WAV file generated, 30-100KB
- Completes in under 5 seconds
- Load average stays below 2.0

- [ ] **Step 5: Check logs are clean**

```bash
# No moonshot warnings
journalctl -u xelasphere --since "1 min ago" --no-pager | grep -i moonshot

# No model spam (should see far fewer �� NEW lines)
journalctl -u xelasphere --since "1 min ago" --no-pager | grep "🆕 NEW" | wc -l

# Sidecar logs
journalctl -u kokoro-sidecar --since "2 min ago" --no-pager | tail -10
```

Expected:
- No moonshot lines
- Fewer than 10 new model registrations per cycle (was 50+)
- Sidecar shows "Sidecar ready." and successful TTS logs

- [ ] **Step 6: Verify CPU is stable**

```bash
ps aux --sort=-%cpu | head -10
```

Expected: No process above 50% at idle. Total CPU well below 200%.
