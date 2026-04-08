# Image Intent Detection — Design Spec

**Date:** 2026-04-08
**Status:** Approved
**Problem:** Image generation triggers on pasted text, reviews, and other non-image prompts due to fragile keyword matching.

---

## Summary

Replace all keyword-based image intent detection with two clean paths:

- **Path A (Explicit):** `/image` command — instant, no detection needed
- **Path C (Natural Language):** `ACTION: generate_image` in the AI model's response — the model decides, so accuracy is 100%

Remove: `is_image_request()`, `_strong_image_rules()`, `classify_image_intent_llm()`, and the frontend `isImagePrompt()` keyword matching.

---

## Architecture

### Path A: Explicit Command

User types `/image sunset over mountains`.

```
Frontend                          Backend
────────                          ───────
1. Detect /image prefix           
2. Show "Creating image..."       
   placeholder immediately        
3. Send to /chat/stream           
                                  4. Detect /image prefix in prompt
                                  5. Strip prefix, call image generator
                                  6. Return JSON {image_url, ...}
7. Check Content-Type: json       
8. Replace placeholder with image 
```

No changes to this path. It works today.

### Path C: Natural Language via ACTION Protocol

User types "draw me a cat" or "create an image of it".

```
Frontend                          Backend (streaming)
────────                          ──────────────────
1. No image detection             
   (no placeholder)               
2. Send to /chat/stream           
                                  3. Model streams response
                                  4. StreamingActionBuffer captures:
                                     ACTION: generate_image
                                     description: a cat sitting on a wall
                                     source: explicit
                                  5. Buffer resolves → image path
                                  6. SSE event: {type: "image_generating",
                                     description: "a cat sitting on a wall"}
7. Show "Creating image..."       
   placeholder with description   
                                  8. Call image generator with description
                                  9. SSE done event: {image_url, ...}
10. Replace placeholder with image
```

### The Confidence Signal

The model emits a `source` field:

| Source | Meaning | Behavior |
|--------|---------|----------|
| `explicit` | User directly asked ("draw me X", "image of X") | Generate immediately |
| `inferred` | Model interpreted from context ("make one", "create it") | Show confirmation first |

**Explicit flow:** Image generation fires immediately. User sees placeholder → image.

**Inferred flow:**
1. Backend sends SSE: `{type: "image_confirm", description: "a cat sitting on a wall at sunset"}`
2. Frontend shows: *"Generating: a cat sitting on a wall at sunset — is that right?"* with Yes/No buttons
3. Backend sets `pending_confirmation: generate_image` on the session (see Section: Confirmation Turn State)
4. User clicks Yes → frontend sends confirmation → backend generates image
5. User clicks No or types a correction → treated as a new prompt

---

## Streaming Interception Detail

### Multi-Line ACTION Block

The `generate_image` action requires multi-line parsing (unlike `chat` or `show_chart` which are single-line). The format:

```
ACTION: generate_image\n
description: a cat sitting on a wall at sunset\n
source: explicit\n
\n
Here's your image of a cat at sunset...
```

The blank line terminates the header block. Everything after is chat text (shown alongside or after the image).

### StreamingActionBuffer Changes

Current behavior: buffer until first `\n`, parse single ACTION keyword, release remainder.

New behavior for `generate_image`:
1. First line: `ACTION: generate_image` → enter multi-line header mode
2. Continue buffering subsequent lines as key-value pairs
3. Blank line (or non-key-value line) → header complete, resolve
4. Release remainder as chat text

For all other actions (`chat`, `create_artifact`, `patch_artifact`, `show_chart`): behavior unchanged (single-line).

### Buffer Timeout Guard

**Problem:** If the model truncates mid-ACTION block or the network drops, the multi-line buffer waits for a blank line that never arrives.

**Solution:** 3-second timeout from when `ACTION: generate_image` is first detected.

```python
class StreamingActionBuffer:
    HEADER_TIMEOUT_MS = 3000  # 3 seconds

    def feed(self, chunk):
        ...
        if self._in_header_mode:
            elapsed = time.time() - self._header_start_time
            if elapsed > self.HEADER_TIMEOUT_MS / 1000:
                # Timeout: flush buffer as plain text, abort image path
                self.action = "chat"
                self.remainder = self.buffer
                self._resolved = True
                logger.warning("generate_image header timeout — falling back to chat")
                return self.action
```

On timeout: everything buffered so far is flushed as plain chat text. No hang, no dead state. The user sees the model's raw output (which may include `ACTION: generate_image` text, but that's better than hanging).

---

## Confirmation Turn State

### The Problem

When `source: inferred`, the backend shows a confirmation message and waits for the user's response. But "yes" or "looks good" in the next turn would normally hit the full routing logic (smart router, crypto detection, etc.) and could misfire.

### Solution: Session-Level Pending State

When the backend sends an inferred confirmation:

```python
# In chat_routes.py, after sending confirmation SSE
session['pending_confirmation'] = {
    'type': 'generate_image',
    'description': parsed_description,
    'timestamp': time.time()
}
```

On the next request to `/chat/stream`:

```python
# At the top of chat_stream(), before any other processing
pending = session.pop('pending_confirmation', None)
if pending and pending.get('type') == 'generate_image':
    # Check if not stale (expire after 5 minutes)
    if time.time() - pending.get('timestamp', 0) < 300:
        # Classify response: yes / no / correction
        # Use fast LLM call (Groq Llama 8B) — same pattern as classify_user_intent
        # Three buckets: "confirm", "reject", "correction"
        bucket = classify_confirmation_response(prompt, pending['description'])
        if bucket == 'confirm':
            return generate_and_return_image(user_id, pending['description'])
        elif bucket == 'correction':
            # User provided a better description — use it directly
            return generate_and_return_image(user_id, prompt)
        # bucket == 'reject' or classification failed: fall through to normal routing
```

**Confirmation classifier** (fast Groq Llama 8B call, ~200ms):

```python
def classify_confirmation_response(user_response: str, pending_description: str) -> str:
    """Classify user's response to an image confirmation.
    Returns: 'confirm' | 'reject' | 'correction'"""
    prompt = f'''The user was asked to confirm generating this image:
"{pending_description}"

They responded: "{user_response}"

Classify their response as exactly one of:
- CONFIRM: they agree (yes, sure, go ahead, perfect, do it, exactly, looks good, etc.)
- REJECT: they decline (no, nevermind, cancel, stop, etc.)
- CORRECTION: they want a different image (they described something else or modified the description)

Reply with ONLY one word: CONFIRM, REJECT, or CORRECTION'''

    resp, _ = send_prompt(prompt, model='llama-3.1-8b-instant')
    answer = resp.strip().upper().split()[0] if isinstance(resp, str) and resp.strip() else 'REJECT'
    return answer.lower() if answer in ('CONFIRM', 'REJECT', 'CORRECTION') else 'reject'
```

**Key properties:**
- `session.pop()` ensures the state is consumed exactly once
- 5-minute TTL prevents stale confirmations
- LLM classification handles natural language ("go ahead", "perfect", "that's the one")
- "correction" bucket lets user refine ("no, make it a watercolor painting instead")
- Classification failure defaults to `reject` (safe — falls through to normal routing)
- The pending state is server-side (Flask session), not client-side

### Frontend Confirmation UI

When the frontend receives `{type: "image_confirm", description: "..."}`:

```javascript
// Show inline confirmation with Yes/No buttons (canonical path)
// Text input also works — classified by LLM on backend
displayConfirmation({
    text: `Generating: "${description}" — is that right?`,
    onConfirm: () => sendMessage('yes'),   // fast path: hits 'confirm' bucket
    onCancel: () => sendMessage('no')       // fast path: hits 'reject' bucket
    // User can also type freely: "go ahead", "make it darker", etc.
});
```

The Yes/No buttons are the canonical path (guaranteed to classify correctly). Free-text input also works via the LLM classifier.

---

## System Prompt Changes

### Addition to ACTION Protocol

```
ACTION: generate_image
  description: <detailed, standalone description for the image generator>
  source: explicit | inferred

  Use when: the user wants you to CREATE/GENERATE an actual image.
  
  source values:
  - explicit: user directly asked ("draw me X", "image of X", "create a picture of X",
    "photo of X", "/image X")
  - inferred: you interpreted the intent from conversation context ("make one of those",
    "create it", "do that but as an image")
  
  The description MUST be a complete, standalone prompt for an image generator.
  Resolve ALL pronouns — "it", "that", "this" — into concrete descriptions using
  conversation context. Never pass pronouns to the image generator.
  
  NEVER use generate_image for:
  - Code, artifacts, or app creation ("create a React app" = create_artifact)
  - Charts or financial data ("show me bitcoin" = show_chart)
  - Explanations about images ("explain how DALL-E works" = chat)
  - Figurative language ("picture this scenario", "draw a conclusion" = chat)
  - Discussing existing images ("the image shows..." = chat)

EXAMPLE:
  User: "draw me a sunset over mountains with a lake reflection"
  →
  ACTION: generate_image
  description: a vibrant sunset over snow-capped mountains with a mirror-like lake reflecting orange and purple sky colors
  source: explicit

  [Your response text here, e.g. "Here's your sunset scene..."]

EXAMPLE (inferred):
  Previous context: user was discussing Japanese architecture
  User: "make an image of that"
  →
  ACTION: generate_image
  description: traditional Japanese pagoda temple surrounded by cherry blossom trees with Mount Fuji in the background
  source: inferred

  I'll generate an image based on what we discussed...
```

---

## What Gets Deleted

| File | Function/Code | Reason |
|------|--------------|--------|
| `app/utilities.py` | `is_image_request()` | Replaced by ACTION protocol |
| `app/utilities.py` | `_strong_image_rules()` | No longer needed |
| `app/utilities.py` | `classify_image_intent_llm()` | No longer needed (saves Groq API call per message) |
| `app/static/js/codeWindow.js` | `isImagePrompt()` | Reduced to only match `/image`, `/img` commands |
| `app/blueprints/chat_routes.py` | `is_image_request()` calls (lines 498, 1557) | Replaced by ACTION interception |

### What Stays

| Component | Reason |
|-----------|--------|
| `/image` command detection (frontend + backend) | Path A — explicit, zero-latency |
| `handle_image_request()` | Still generates images, now called from ACTION interception |
| `generate_image_result()` | Image generation logic unchanged |
| Frontend placeholder animation | Reused, now triggered by SSE `image_generating` event |

---

## Error Recovery

### Image Generation Fails After ACTION Detection

```python
try:
    result = generate_image_result(user_id, description)
    if result and result.get('image_url'):
        # Send image via SSE done event
        yield sse_event({'type': 'done', 'image_url': result['image_url'], ...})
    else:
        raise RuntimeError("No image URL returned")
except Exception as e:
    logger.error(f"Image generation failed after ACTION: {e}")
    # Fall back to chat text that the model already generated
    yield sse_event({
        'type': 'done',
        'content': chat_text + '\n\n*Image generation failed — try `/image ' + description[:50] + '` to retry.*',
        'render_mode': 'inline'
    })
```

The model's chat text (after the ACTION block) is always preserved. If image gen fails, the user sees the text response plus an actionable retry suggestion.

### Model Doesn't Emit ACTION Block

Nothing changes. The model responds with `ACTION: chat` (or no ACTION line), the streaming handler falls through to normal text rendering. No image is generated. No false positive.

### Malformed ACTION Block (timeout)

Buffer timeout (3s) fires. Everything buffered is flushed as plain chat text. User sees the raw model output. Recoverable — user can retry with `/image`.

---

## Migration Plan

Order is critical. The system prompt change is the **feature flag** — it activates
the new behavior. Everything else must be in place before it's flipped on.

**Phase 1: Remove old detection (safe — /image command still works)**
1. Remove `is_image_request()`, `_strong_image_rules()`, `classify_image_intent_llm()` from `utilities.py`
2. Remove `is_image_request()` calls from `chat_routes.py` (both streaming and non-streaming endpoints)
3. Remove streaming endpoint image pre-check (the temporary one we added earlier)
4. Reduce frontend `isImagePrompt()` to only match `/image`, `/img` commands
5. Add `classify_confirmation_response()` to utilities

**Phase 2: Build new interception (inactive — no model emits generate_image yet)**
6. Update `StreamingActionBuffer` for multi-line header mode + 3s timeout
7. Update `ActionParser` and `ParsedAction` to handle `generate_image`
8. Add SSE event types: `image_generating`, `image_confirm`
9. Add `pending_confirmation` session state handling in `chat_stream()`
10. Update frontend to handle new SSE events (placeholder on `image_generating`, confirmation UI on `image_confirm`)

**Phase 3: Activate (feature flag)**
11. Add `generate_image` to ACTION protocol in system prompt — models start emitting it

**Phase 4: Verify**
12. Test: explicit `/image` commands, natural language, pasted text, inferred context, confirmation flow, error recovery, buffer timeout
