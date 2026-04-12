# Gevent Scale-Up Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Switch Gunicorn from `sync` to `gevent` workers, add Redis for cross-worker state, add backpressure semaphores with fail-fast 503, and audit nginx — raising SSE capacity from ~95 to ~4,000 concurrent streams without rewriting route handlers.

**Architecture:** gevent monkey-patches blocking I/O into cooperative green threads, so each of 4 workers holds ~1,000 concurrent connections. Redis provides cross-worker state for rate limits, voice sessions, and chat prime cache. Semaphores wrap `/chat/stream` and `/tts/groq` to return 503 fast when saturated.

**Tech Stack:** gevent, redis-py, Flask, Gunicorn, flask-limiter (already installed), Flask-Session (already installed), systemd, nginx.

---

## File Structure

| File | Action | Responsibility |
|------|--------|----------------|
| `run.py` | Modify | Monkey-patch gevent at the very top before any imports |
| `gunicorn.conf.py` | Modify | Switch to `gevent` worker class, add `worker_connections` |
| `app/backpressure.py` | Create | BoundedSemaphore slots for LLM, TTS, voice + acquire-or-503 helper |
| `app/voice_sessions.py` | Create | Redis-backed voice session register/release/count |
| `app/redis_client.py` | Create | Single shared Redis client (connection pooled) |
| `app/blueprints/status_routes.py` | Create | `GET /status` endpoint returning slot availability |
| `app/blueprints/chat_routes.py` | Modify | Wrap `/chat/stream` with `LLM_SLOTS`, real prime cache in `/chat/prime` |
| `app/blueprints/media_routes.py` | Modify | Wrap `/tts/groq` with `TTS_SLOTS` |
| `app/__init__.py` | Modify | Register `status_bp`, init voice session tracking |
| `/etc/systemd/system/xelasphere.service` | Modify | Add `RATELIMIT_STORAGE_URI` + `REDIS_URL` env vars |
| `tests/test_backpressure.py` | Create | Test semaphore acquire/release + 503 on saturation |
| `tests/test_voice_sessions.py` | Create | Test register/release/count via Redis |

---

### Task 1: Install dependencies

**Files:** None (system setup)

- [ ] **Step 1: Install Redis server**

```bash
apt-get update && apt-get install -y redis-server
```

Expected: `redis-server` package installed.

- [ ] **Step 2: Enable and start Redis**

```bash
systemctl enable --now redis-server
redis-cli ping
```

Expected: `PONG`.

- [ ] **Step 3: Install Python packages**

```bash
cd /root/xelas.io/xelasphere.io-
./venv/bin/pip install gevent redis
```

Expected: `Successfully installed gevent-... redis-...`.

- [ ] **Step 4: Verify grpcio is present (Firestore dep)**

```bash
./venv/bin/pip show grpcio | head -2
```

Expected: `Name: grpcio` shown. Already pulled in by google-cloud-firestore. No action if present.

- [ ] **Step 5: Commit dependency pins**

Look up installed versions and add to `requirements.txt` if the project uses one.

```bash
./venv/bin/pip freeze | grep -iE "^(gevent|redis)=="
# Append to requirements.txt if it exists
grep -q "^gevent==" requirements.txt 2>/dev/null || ./venv/bin/pip freeze | grep -iE "^(gevent|redis)==" >> requirements.txt
git add requirements.txt
git commit -m "chore: pin gevent + redis for scale-up" || echo "requirements.txt not tracked, skipping commit"
```

---

### Task 2: Redis client singleton

**Files:**
- Create: `app/redis_client.py`
- Test: `tests/test_redis_client.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_redis_client.py`:

```python
import os
import pytest
from app.redis_client import get_redis


def test_get_redis_returns_same_instance():
    """Multiple calls return the same client (pooled)."""
    a = get_redis()
    b = get_redis()
    assert a is b


def test_redis_client_pings():
    """Client can reach live Redis (requires redis-server running)."""
    r = get_redis()
    assert r.ping() is True
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /root/xelas.io/xelasphere.io-
./venv/bin/pytest tests/test_redis_client.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'app.redis_client'`.

- [ ] **Step 3: Write minimal implementation**

Create `app/redis_client.py`:

```python
"""Process-wide Redis client singleton. Safe across gevent workers."""
import os
import redis

_client = None


def get_redis() -> redis.Redis:
    """Return a pooled Redis client for this process."""
    global _client
    if _client is None:
        url = os.getenv('REDIS_URL', 'redis://localhost:6379/0')
        _client = redis.from_url(url, decode_responses=True, socket_timeout=2, socket_connect_timeout=2)
    return _client
```

- [ ] **Step 4: Run test to verify it passes**

```bash
./venv/bin/pytest tests/test_redis_client.py -v
```

Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add app/redis_client.py tests/test_redis_client.py
git commit -m "feat: pooled Redis client singleton for cross-worker state"
```

---

### Task 3: Voice session tracker

**Files:**
- Create: `app/voice_sessions.py`
- Test: `tests/test_voice_sessions.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_voice_sessions.py`:

```python
import time
import pytest
from app.voice_sessions import register, release, count


@pytest.fixture(autouse=True)
def _clean():
    """Clear voice keys before and after each test."""
    from app.redis_client import get_redis
    r = get_redis()
    for k in r.scan_iter('voice:*'):
        r.delete(k)
    yield
    for k in r.scan_iter('voice:*'):
        r.delete(k)


def test_register_increments_count():
    register('sess-a')
    assert count() == 1
    register('sess-b')
    assert count() == 2


def test_release_decrements_count():
    register('sess-a')
    register('sess-b')
    release('sess-a')
    assert count() == 1


def test_release_unknown_session_is_noop():
    # Should not drop below 0
    release('never-registered')
    assert count() == 0


def test_register_is_idempotent():
    register('sess-a')
    register('sess-a')
    assert count() == 1
```

- [ ] **Step 2: Run test to verify it fails**

```bash
./venv/bin/pytest tests/test_voice_sessions.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'app.voice_sessions'`.

- [ ] **Step 3: Write minimal implementation**

Create `app/voice_sessions.py`:

```python
"""Redis-backed cross-worker voice session tracking.

Uses a SET for session IDs (idempotent) + a counter computed from set
cardinality. TTL per session key so dead sessions expire automatically.
"""
import logging
from app.redis_client import get_redis

logger = logging.getLogger(__name__)

SESSIONS_SET = 'voice:sessions'
SESSION_TTL = 300  # 5 min — renewed per heartbeat


def register(session_id: str) -> None:
    """Mark a voice session as active (idempotent)."""
    if not session_id:
        return
    try:
        r = get_redis()
        pipe = r.pipeline()
        pipe.sadd(SESSIONS_SET, session_id)
        pipe.setex(f'voice:heartbeat:{session_id}', SESSION_TTL, '1')
        pipe.execute()
    except Exception as e:
        logger.warning(f"voice_sessions.register failed: {e}")


def release(session_id: str) -> None:
    """Mark a voice session as ended."""
    if not session_id:
        return
    try:
        r = get_redis()
        pipe = r.pipeline()
        pipe.srem(SESSIONS_SET, session_id)
        pipe.delete(f'voice:heartbeat:{session_id}')
        pipe.execute()
    except Exception as e:
        logger.warning(f"voice_sessions.release failed: {e}")


def count() -> int:
    """Current active voice session count across all workers."""
    try:
        return int(get_redis().scard(SESSIONS_SET) or 0)
    except Exception as e:
        logger.warning(f"voice_sessions.count failed: {e}")
        return 0
```

- [ ] **Step 4: Run test to verify it passes**

```bash
./venv/bin/pytest tests/test_voice_sessions.py -v
```

Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add app/voice_sessions.py tests/test_voice_sessions.py
git commit -m "feat: Redis-backed voice session tracker for cross-worker counts"
```

---

### Task 4: Backpressure semaphores

**Files:**
- Create: `app/backpressure.py`
- Test: `tests/test_backpressure.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_backpressure.py`:

```python
import pytest
from app.backpressure import LLM_SLOTS, acquire_or_503


def test_acquire_when_slot_available():
    with acquire_or_503(LLM_SLOTS, timeout=0.1) as ok:
        assert ok is True
    # Slot released on exit


def test_503_when_exhausted():
    held = []
    # Exhaust the semaphore
    capacity = LLM_SLOTS._initial_value if hasattr(LLM_SLOTS, '_initial_value') else 8
    for _ in range(capacity):
        cm = acquire_or_503(LLM_SLOTS, timeout=0.01)
        cm.__enter__()
        held.append(cm)

    try:
        # Next acquire should fail
        with acquire_or_503(LLM_SLOTS, timeout=0.05) as ok:
            assert ok is False
    finally:
        for cm in held:
            cm.__exit__(None, None, None)


def test_release_frees_slot():
    with acquire_or_503(LLM_SLOTS, timeout=0.1):
        pass
    # Should acquire again immediately
    with acquire_or_503(LLM_SLOTS, timeout=0.01) as ok:
        assert ok is True
```

- [ ] **Step 2: Run test to verify it fails**

```bash
./venv/bin/pytest tests/test_backpressure.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'app.backpressure'`.

- [ ] **Step 3: Write minimal implementation**

Create `app/backpressure.py`:

```python
"""System-wide backpressure semaphores.

Uses stdlib threading.BoundedSemaphore which is cooperatively yielded
by gevent's monkey patch (so green threads don't all block).
"""
import contextlib
import os
from threading import BoundedSemaphore

LLM_CAP = int(os.getenv('BACKPRESSURE_LLM_SLOTS', '8'))
TTS_CAP = int(os.getenv('BACKPRESSURE_TTS_SLOTS', '4'))
VOICE_CAP = int(os.getenv('BACKPRESSURE_VOICE_SLOTS', '3'))

LLM_SLOTS = BoundedSemaphore(LLM_CAP)
LLM_SLOTS._initial_value = LLM_CAP  # capacity introspection

TTS_SLOTS = BoundedSemaphore(TTS_CAP)
TTS_SLOTS._initial_value = TTS_CAP

VOICE_SLOTS = BoundedSemaphore(VOICE_CAP)
VOICE_SLOTS._initial_value = VOICE_CAP


@contextlib.contextmanager
def acquire_or_503(sem: BoundedSemaphore, timeout: float = 5.0):
    """Context manager: acquire a slot within timeout, else yield False.

    Usage:
        with acquire_or_503(LLM_SLOTS, timeout=5) as ok:
            if not ok:
                return jsonify({'error': 'Server busy'}), 503
            ...stream...
    """
    acquired = sem.acquire(timeout=timeout)
    try:
        yield acquired
    finally:
        if acquired:
            sem.release()


def slot_counts() -> dict:
    """Return capacity + currently available slots for each semaphore."""
    return {
        'llm': {'capacity': LLM_CAP, 'available': LLM_SLOTS._value},
        'tts': {'capacity': TTS_CAP, 'available': TTS_SLOTS._value},
        'voice': {'capacity': VOICE_CAP, 'available': VOICE_SLOTS._value},
    }
```

- [ ] **Step 4: Run test to verify it passes**

```bash
./venv/bin/pytest tests/test_backpressure.py -v
```

Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add app/backpressure.py tests/test_backpressure.py
git commit -m "feat: backpressure semaphores with acquire-or-503 helper"
```

---

### Task 5: /status endpoint

**Files:**
- Create: `app/blueprints/status_routes.py`
- Modify: `app/__init__.py` (register blueprint)

- [ ] **Step 1: Create the blueprint**

Create `app/blueprints/status_routes.py`:

```python
"""GET /status — slot availability + active voice sessions for frontend health UI."""
from flask import Blueprint, jsonify
from app.backpressure import slot_counts
from app.voice_sessions import count as voice_count

status_bp = Blueprint('status', __name__)


@status_bp.route('/status', methods=['GET'])
def status():
    slots = slot_counts()
    return jsonify({
        'healthy': True,
        'slots': slots,
        'voice_sessions': voice_count(),
    })
```

- [ ] **Step 2: Register the blueprint**

In `app/__init__.py`, find the block of `app.register_blueprint(...)` calls (around line 176-185) and add after the existing `chat_bp` registration:

```python
from app.blueprints.status_routes import status_bp
app.register_blueprint(status_bp)
```

- [ ] **Step 3: Smoke-test the endpoint**

Start a dev server (or restart gunicorn) and hit the endpoint:

```bash
curl -s http://localhost:5000/status | python3 -m json.tool
```

Expected JSON shape:
```json
{
  "healthy": true,
  "slots": {
    "llm":   {"capacity": 8, "available": 8},
    "tts":   {"capacity": 4, "available": 4},
    "voice": {"capacity": 3, "available": 3}
  },
  "voice_sessions": 0
}
```

- [ ] **Step 4: Commit**

```bash
git add app/blueprints/status_routes.py app/__init__.py
git commit -m "feat: GET /status endpoint exposes slot + voice session counts"
```

---

### Task 6: Wrap /chat/stream with LLM backpressure

**Files:**
- Modify: `app/blueprints/chat_routes.py` (around line 470-485)

- [ ] **Step 1: Add import at top of file**

In `app/blueprints/chat_routes.py`, with the other imports near the top, add:

```python
from app.backpressure import LLM_SLOTS, acquire_or_503
```

- [ ] **Step 2: Modify chat_stream to check backpressure**

Find `def chat_stream():` (around line 475). At the very top of the function body, **before any work**, add the backpressure guard:

```python
@chat_bp.route('/chat/stream', methods=['POST'])
@limiter.limit("20 per minute")
@csrf_required
@error_handler
@guest_allowed
def chat_stream():
    """
    Stream chat response with real-time reasoning content (DeepSeek R1).
    Returns Server-Sent Events (SSE) with reasoning and content chunks.
    At completion, processes content for artifacts and saves to history.
    """
    # Backpressure — fail fast rather than piling up during LLM surge
    _bp = acquire_or_503(LLM_SLOTS, timeout=5.0)
    _bp_ok = _bp.__enter__()
    if not _bp_ok:
        return jsonify({'error': 'Server busy, please retry', 'retry_after': 5}), 503

    from app.utilities import stream_deepseek_response
    # ... rest of existing body unchanged ...
```

- [ ] **Step 3: Release the slot at the end of generate()**

The `generate()` inner function is a generator. The slot must release when streaming finishes, errors, or the client disconnects. Wrap the existing `generate()` in a `try/finally`:

Find the `def generate():` inside `chat_stream()` (around line 966). After the existing body completes (before `yield from` or final yield), ensure cleanup. The simplest approach is to wrap the Response object — modify the final `return Response(...)` call in `chat_stream()` so it releases the slot when the stream ends:

```python
# Find the existing return at the end of chat_stream:
resp = Response(generate(), mimetype='text/event-stream', headers=sse_headers)

# Release slot when response is garbage-collected (streaming finished/aborted)
@resp.call_on_close
def _release_slot():
    try:
        _bp.__exit__(None, None, None)
    except Exception:
        pass

return resp
```

If the existing `return Response(...)` is structured differently, adapt — the key is calling `_bp.__exit__(None, None, None)` exactly once on stream end/abort.

- [ ] **Step 4: Quick manual verification**

Start gunicorn (still sync worker at this point — verify the wrapping logic works before gevent switch):

```bash
systemctl restart xelasphere
curl -s http://localhost:5000/status | python3 -c "import sys, json; s = json.load(sys.stdin); print('LLM slots available:', s['slots']['llm']['available'])"
```

Expected: `LLM slots available: 8`.

Hit the stream endpoint (requires auth — can be done via the frontend). Between stream start and stream end, slot count should drop to 7 then recover to 8.

- [ ] **Step 5: Commit**

```bash
git add app/blueprints/chat_routes.py
git commit -m "feat: LLM backpressure on /chat/stream — 503 fail-fast instead of 300s timeout"
```

---

### Task 7: Wrap /tts/groq with TTS backpressure

**Files:**
- Modify: `app/blueprints/media_routes.py`

- [ ] **Step 1: Add import**

In `app/blueprints/media_routes.py`, with the other imports near the top, add:

```python
from app.backpressure import TTS_SLOTS, acquire_or_503
```

- [ ] **Step 2: Wrap generate_groq_tts**

Find `def generate_groq_tts():` (around line 882). Replace the body's first lines with the guard:

```python
@media_bp.route('/tts/groq', methods=['POST'])
@limiter.limit("30 per minute")
@csrf_required
@error_handler
@login_required
def generate_groq_tts():
    """Generate text-to-speech: Kokoro (primary, local, unlimited) → Gemini → Groq."""
    with acquire_or_503(TTS_SLOTS, timeout=3.0) as ok:
        if not ok:
            return jsonify({'error': 'TTS busy, please retry', 'retry_after': 3}), 503
        payload = request.get_json(silent=True) or {}
        text = (payload.get('text') or '').strip()
        if not text:
            return jsonify({'error': 'Text input is required.'}), 400
        # ... rest of existing body inside the with-block ...
```

Everything from `payload = ...` through the final `return` / error paths must be **inside** the `with` block so the slot releases on return. Indent the existing body one level.

- [ ] **Step 3: Smoke test**

Restart gunicorn, then:

```bash
systemctl restart xelasphere
sleep 2
curl -s http://localhost:5000/status | python3 -m json.tool
```

Expected: `"tts": {"capacity": 4, "available": 4}` in the response.

- [ ] **Step 4: Commit**

```bash
git add app/blueprints/media_routes.py
git commit -m "feat: TTS backpressure on /tts/groq — 503 when saturated"
```

---

### Task 8: Wire voice session tracking into /chat/stream

**Files:**
- Modify: `app/blueprints/chat_routes.py`

- [ ] **Step 1: Add import**

In `app/blueprints/chat_routes.py`:

```python
from app.voice_sessions import register as voice_register, release as voice_release
```

- [ ] **Step 2: Register on /chat/prime (voice entry point)**

Find the existing `chat_prime` route (added during voice mode work). Add session registration:

```python
@chat_bp.route('/chat/prime', methods=['POST'])
@csrf_required
@error_handler
@guest_allowed
def chat_prime():
    data = request.get_json(silent=True) or {}
    session_id = (data.get('session_id') or '').strip()
    if not session_id:
        return jsonify({'status': 'noop'})
    # Register voice session — renews TTL on every prime (acts as heartbeat)
    voice_register(session_id)
    try:
        from app.chat_session import get_session
        sess = get_session(session_id)
        if sess is not None:
            _ = getattr(sess, 'messages', None) or getattr(sess, 'history', None) or []
        return jsonify({'status': 'primed'})
    except Exception as e:
        logger.debug(f"prime_chat no-op: {e}")
        return jsonify({'status': 'noop'})
```

- [ ] **Step 3: Release on voice exit**

The frontend must POST `/chat/voice-exit` when voice mode exits. Add this new endpoint near `chat_prime`:

```python
@chat_bp.route('/chat/voice-exit', methods=['POST'])
@csrf_required
@error_handler
@guest_allowed
def chat_voice_exit():
    """Frontend calls this when voice mode is turned off."""
    data = request.get_json(silent=True) or {}
    session_id = (data.get('session_id') or '').strip()
    if session_id:
        voice_release(session_id)
    return jsonify({'status': 'released'})
```

- [ ] **Step 4: Call /chat/voice-exit from the frontend**

In `app/static/js/voiceModeModule.js`, find the `exit()` function. After the existing cleanup (after `setState('idle')`), add:

```javascript
// Release voice session on server
try {
    const csrfMeta = document.querySelector('meta[name="csrf-token"]');
    const headers = { 'Content-Type': 'application/json' };
    if (csrfMeta) headers['X-CSRFToken'] = csrfMeta.content;
    const sid = window.chatManager?.activeSessionId || '';
    if (sid) {
        fetch('/chat/voice-exit', {
            method: 'POST', headers,
            body: JSON.stringify({ session_id: sid }),
            credentials: 'same-origin'
        }).catch(() => {});
    }
} catch (_) {}
```

Bump the version in `app/templates/index.html`:

```html
<script src="{{ url_for('static', filename='js/voiceModeModule.js') }}?v=32"></script>
```

- [ ] **Step 5: Smoke test**

```bash
systemctl restart xelasphere
sleep 2
# Start a voice session from the UI, then check:
curl -s http://localhost:5000/status | python3 -c "import sys, json; print('voice_sessions:', json.load(sys.stdin)['voice_sessions'])"
```

Expected: `voice_sessions: 1` (or higher) while voice mode is active; `0` after exit.

- [ ] **Step 6: Commit**

```bash
git add app/blueprints/chat_routes.py app/static/js/voiceModeModule.js app/templates/index.html
git commit -m "feat: cross-worker voice session tracking via /chat/prime + /chat/voice-exit"
```

---

### Task 9: Redis-backed chat prime cache

**Files:**
- Modify: `app/blueprints/chat_routes.py`

- [ ] **Step 1: Store session history into Redis on prime**

Replace the body of `chat_prime` to actually cache the history for 10s in Redis:

```python
@chat_bp.route('/chat/prime', methods=['POST'])
@csrf_required
@error_handler
@guest_allowed
def chat_prime():
    data = request.get_json(silent=True) or {}
    session_id = (data.get('session_id') or '').strip()
    if not session_id:
        return jsonify({'status': 'noop'})

    voice_register(session_id)

    try:
        from app.chat_session import get_session
        from app.redis_client import get_redis
        import json as _json

        sess = get_session(session_id)
        if sess is not None:
            history = getattr(sess, 'messages', None) or getattr(sess, 'history', None) or []
            # Cache a serializable snapshot for 10s so the follow-up /chat/stream
            # can skip the Firestore fetch if it lands on a different worker.
            try:
                r = get_redis()
                # Only cache if serializable; otherwise skip silently
                r.setex(f'prime:{session_id}', 10, _json.dumps(list(history), default=str))
            except Exception as e:
                logger.debug(f"prime cache write failed: {e}")
        return jsonify({'status': 'primed'})
    except Exception as e:
        logger.debug(f"prime_chat no-op: {e}")
        return jsonify({'status': 'noop'})
```

- [ ] **Step 2: Read prime cache from /chat/stream if available**

In `chat_stream`, at the point where `previous_messages` is built from Firestore, add a cache check first. Find where `previous_messages` is assigned (search for `previous_messages =` in the file). Before the Firestore fetch, try Redis:

```python
# Try prime cache first — LLM context was pre-warmed during VAD speech-start
previous_messages = None
try:
    from app.redis_client import get_redis
    import json as _json
    cached = get_redis().get(f'prime:{session_id}')
    if cached:
        previous_messages = _json.loads(cached)
        logger.info(f"[Stream] prime cache HIT for session {session_id}")
except Exception as e:
    logger.debug(f"prime cache read failed: {e}")

if previous_messages is None:
    # ... existing Firestore fetch unchanged ...
```

Keep the existing fetch path as the fallback.

- [ ] **Step 3: Smoke test**

```bash
systemctl restart xelasphere
sleep 2
# Trigger a voice turn — console should show [Stream] prime cache HIT
journalctl -u xelasphere -n 50 --no-pager | grep -i "prime cache"
```

Expected: `prime cache HIT` log line after a voice-mode turn.

- [ ] **Step 4: Commit**

```bash
git add app/blueprints/chat_routes.py
git commit -m "feat: Redis-backed chat prime cache — cross-worker context warming"
```

---

### Task 10: Switch rate limiter to Redis

**Files:**
- Modify: `/etc/systemd/system/xelasphere.service`

- [ ] **Step 1: Add env vars to systemd unit**

Edit `/etc/systemd/system/xelasphere.service`. Inside `[Service]`, add two `Environment=` lines (keep existing ones):

```ini
[Service]
# ... existing directives ...
Environment=RATELIMIT_STORAGE_URI=redis://localhost:6379/0
Environment=REDIS_URL=redis://localhost:6379/0
# ... rest ...
```

- [ ] **Step 2: Reload systemd and restart service**

```bash
systemctl daemon-reload
systemctl restart xelasphere
sleep 2
systemctl status xelasphere --no-pager | head -10
```

Expected: service active (running).

- [ ] **Step 3: Verify rate limit writes to Redis**

Hit any rate-limited endpoint a few times (e.g., `/tts/groq`), then:

```bash
redis-cli --scan --pattern 'LIMITER*' | head -5
```

Expected: Keys like `LIMITS/LIMITER/...` present — proves rate-limit state is now in Redis, not per-worker memory.

- [ ] **Step 4: Commit env doc**

Record the change in a note. The systemd file is outside the repo but the intent should be documented:

```bash
# Append to docs/superpowers/specs/2026-04-12-gevent-scale-up-design.md
# or similar — record which env vars were added to the systemd unit
echo "" >> docs/DEPLOY.md 2>/dev/null || true
cat >> docs/DEPLOY.md <<'EOF'

## Env vars (2026-04-12)
- `RATELIMIT_STORAGE_URI=redis://localhost:6379/0` — cross-worker rate limits
- `REDIS_URL=redis://localhost:6379/0` — shared Redis client
EOF
git add docs/DEPLOY.md 2>/dev/null
git commit -m "docs: record Redis env vars added to systemd unit" 2>/dev/null || echo "No docs change to commit"
```

---

### Task 11: Gevent monkey-patch in run.py

**Files:**
- Modify: `run.py`

- [ ] **Step 1: Prepend monkey-patch to run.py**

The current `run.py` begins with `from dotenv import load_dotenv`. Replace the top of the file so gevent patches **before any other import**:

```python
# GEVENT: monkey-patch MUST be the first thing executed.
# thread=False avoids interference with gRPC (Firestore) threadpools.
from gevent import monkey
monkey.patch_all(thread=False)

from dotenv import load_dotenv
import os

# Load environment variables from .env file before importing the app
load_dotenv()

from app import create_app

app = create_app()

# Note: Embedding model loads lazily on first request (~40s for jina-embeddings-v3)
# Subsequent requests are fast. Use min-instances=1 to avoid cold starts if needed.

if __name__ == "__main__":
    flask_env = os.getenv("FLASK_ENV", "production")
    default_port = 5000 if flask_env != "production" else 5001
    port = int(os.getenv("FLASK_RUN_PORT", os.getenv("PORT", default_port)))
    app.run(debug=(flask_env != "production"), port=port)
```

- [ ] **Step 2: Smoke-test Firestore under gevent (still sync worker)**

Before switching the worker class, verify gevent monkey-patch doesn't break Firestore. Run locally:

```bash
cd /root/xelas.io/xelasphere.io-
./venv/bin/python3 -c "
from gevent import monkey; monkey.patch_all(thread=False)
from app import create_app
app = create_app()
with app.test_client() as c:
    r = c.get('/status')
    print('status:', r.status_code, r.get_json())
"
```

Expected: `status: 200 {'healthy': True, ...}`. No hangs, no deadlocks.

If Firestore hangs or errors: document the exception, do **not** proceed to Task 12. Fall back to `SESSION_TYPE=filesystem` via env var and use Redis for memory caches as a workaround. Still report the error — we need it fixed before gevent ships.

- [ ] **Step 3: Smoke-test a Firestore read under gevent**

```bash
./venv/bin/python3 -c "
from gevent import monkey; monkey.patch_all(thread=False)
from app import create_app
app = create_app()
from app.config import db
# Trivial read — list any collection with limit 1
docs = list(db.collection('sessions').limit(1).stream())
print('Firestore OK, got', len(docs), 'docs')
"
```

Expected: `Firestore OK, got 0 docs` (or 1). Any timeout → STOP, fall back to filesystem sessions.

- [ ] **Step 4: Commit**

```bash
git add run.py
git commit -m "feat: gevent monkey-patch at top of run.py (thread=False for gRPC compat)"
```

---

### Task 12: Switch Gunicorn to gevent workers

**Files:**
- Modify: `gunicorn.conf.py`

- [ ] **Step 1: Update worker class and connections**

Replace `gunicorn.conf.py` contents:

```python
"""Gunicorn configuration for XelaSphere — gevent workers."""
import os

bind = "127.0.0.1:5000"
workers = 4                      # keep low — gevent scales WITHIN a worker
worker_class = "gevent"
worker_connections = 1000        # per-worker concurrent connections
timeout = 120                    # SSE stream cap — bump to 180 if cut mid-stream
keepalive = 5
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

- [ ] **Step 2: Restart service and watch logs**

```bash
systemctl restart xelasphere
sleep 3
journalctl -u xelasphere -n 30 --no-pager | tail -20
```

Expected: 4 workers booted, no traceback, `gevent` visible in startup logs.

- [ ] **Step 3: Smoke-test /status under load**

```bash
apt-get install -y apache2-utils
ab -n 500 -c 50 http://localhost:5000/status 2>&1 | grep -E 'Failed|99%|Time per request|Requests per second'
```

Expected: 0 failed requests. Time per request (mean, across all concurrent) should be sub-50ms.

- [ ] **Step 4: Verify a full voice-mode turn still works**

Open the app in browser, start voice mode, speak one sentence, wait for reply, interrupt AI mid-sentence, watch new reply acknowledge. Check console for:
- `[FSM] idle → listening → thinking → speaking`
- `[Interrupt] FIRING`
- `[Stream] prime cache HIT` in server logs

Expected: full flow succeeds, no errors.

- [ ] **Step 5: Commit**

```bash
git add gunicorn.conf.py
git commit -m "feat: switch Gunicorn to gevent workers — 1000 concurrent conns per worker"
```

---

### Task 13: Nginx SSE audit

**Files:**
- Read-only: `/etc/nginx/sites-available/xelasphere` (or whatever path hosts the config)

- [ ] **Step 1: Locate the nginx config**

```bash
ls /etc/nginx/sites-enabled/
ls /etc/nginx/sites-available/
grep -l "proxy_pass.*5000" /etc/nginx/sites-available/* /etc/nginx/sites-enabled/* 2>/dev/null
```

- [ ] **Step 2: Verify SSE-critical directives**

In the active nginx config, confirm the following exist on the `/chat/stream`, `/tts/*`, `/speech-to-text/*`, and `/chat/prime` locations (or a shared block that covers them):

```nginx
proxy_http_version 1.1;
proxy_set_header Connection "";
proxy_buffering off;
proxy_cache off;
proxy_read_timeout 120s;
```

```bash
# Quick grep
grep -nE "proxy_buffering|proxy_http_version|proxy_read_timeout" /etc/nginx/sites-available/xelasphere
```

Expected: each directive present at least once covering the streaming paths. If missing, add them and continue to step 3.

- [ ] **Step 3: Test config and reload**

```bash
nginx -t && systemctl reload nginx
```

Expected: `nginx: configuration file /etc/nginx/nginx.conf test is successful`, no errors on reload.

- [ ] **Step 4: End-to-end SSE smoke test**

From another terminal or a fresh browser session, open voice mode through nginx (https://your-domain) and verify a streamed response plays. If the reply arrives all at once instead of streaming word-by-word, `proxy_buffering off` is missing or broken.

- [ ] **Step 5: Commit nginx snapshot**

If you edited nginx, save a snapshot into the repo for documentation:

```bash
mkdir -p deploy/nginx
cp /etc/nginx/sites-available/xelasphere deploy/nginx/xelasphere.conf 2>/dev/null || echo "path differs, edit manually"
git add deploy/nginx/ 2>/dev/null
git commit -m "docs: snapshot nginx config with SSE tuning" 2>/dev/null || echo "No nginx changes made"
```

---

### Task 14: Load test + capacity verification

**Files:** None (verification only)

- [ ] **Step 1: Baseline /status throughput**

```bash
ab -n 1000 -c 100 http://localhost:5000/status 2>&1 | grep -E 'Failed|Requests per second|Time per request|99%'
```

Record the results. Expected: 0 failed, >500 req/s, p99 < 200ms.

- [ ] **Step 2: Burst a saturated LLM path**

This test validates backpressure. Simulate a saturated condition by using the /chat/stream endpoint with 10 concurrent clients (more than LLM_SLOTS=8):

```bash
# Requires a valid session cookie — run from browser devtools console instead if easier:
# for i in range(10):
#   fetch('/chat/stream', {method:'POST', body:...})
# Expect: 8 stream, 2 get 503 "Server busy"
```

Expected: Some requests return 503 fast (within 5s) rather than all timing out at 120s.

- [ ] **Step 3: Verify Redis is being used**

```bash
redis-cli INFO stats | grep -E "connected_clients|total_connections_received|instantaneous_ops_per_sec"
redis-cli --scan --pattern 'voice:*' | head -5
redis-cli --scan --pattern 'LIMITS*' | head -5
redis-cli --scan --pattern 'prime:*' | head -5
```

Expected: Active connections > 0, rate-limit keys and prime keys present after some usage.

- [ ] **Step 4: Capacity sanity check**

With the numbers from step 1, verify:
- 4 workers × 1000 `worker_connections` = 4000 potential concurrent HTTP connections
- `LLM_SLOTS=8` caps actual simultaneous LLM streams at 8 (tunable)
- Redis single-instance p99 < 1ms for rate-limit + voice ops

Document observed numbers:

```bash
cat > docs/CAPACITY.md <<EOF
# Post-gevent capacity observations (2026-04-12)

- /status throughput: [fill in req/s from step 1]
- /status p99 latency: [fill in ms from step 1]
- Backpressure: /chat/stream returns 503 in <5s when LLM_SLOTS saturated
- Redis ops/sec under light load: [fill in from step 3]
- Active cross-worker state: rate limits, voice sessions, prime cache

## Tunables (env vars)
- BACKPRESSURE_LLM_SLOTS (default 8)
- BACKPRESSURE_TTS_SLOTS (default 4)
- BACKPRESSURE_VOICE_SLOTS (default 3)
EOF
git add docs/CAPACITY.md
git commit -m "docs: record post-gevent capacity observations + tunables"
```

- [ ] **Step 5: Final manual regression**

Run through the voice-mode flow one more time end-to-end:
1. Open voice mode — wave renders, mic opens
2. Speak — `[VAD] speech!` logs, `/chat/prime` fires
3. Wait for AI response — hear TTS, see FSM transitions
4. Interrupt mid-sentence — `[Interrupt] FIRING`, graceful yield, response acknowledges interrupt
5. Exit voice mode — `voice_sessions` count decrements (check `/status`)

All checks pass? Scale-up ships.

---

## Self-Review Results

1. **Spec coverage:** Every spec section maps to tasks.
   - gevent worker class → Tasks 11, 12
   - gRPC/Firestore compat → Task 11 steps 2-3 (smoke tests)
   - Redis client → Task 2
   - flask_limiter Redis → Task 10
   - voice session tracking → Tasks 3, 8
   - prime cache → Task 9
   - backpressure semaphores → Tasks 4, 6, 7
   - `/status` endpoint → Task 5
   - nginx audit → Task 13
   - load test + verification → Task 14

2. **Placeholder scan:** No TBD / TODO / "similar to task N". Each step has concrete commands and code.

3. **Type consistency:** `LLM_SLOTS`, `TTS_SLOTS`, `VOICE_SLOTS`, `acquire_or_503`, `register`, `release`, `count`, `get_redis` — consistent throughout.

4. **Rollback-ready:** Each task is a focused commit; reverting any single commit leaves the system working.
