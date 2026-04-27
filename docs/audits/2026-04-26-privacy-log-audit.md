# Privacy log audit — Flask-side adjacent paths

**Date:** 2026-04-26
**Scope:** `app/memory_v2/`, `app/memory_v3/`, `app/services/chat_engine.py`,
`app/blueprints/chat_routes.py`, `app/smart_router.py`
**Trigger:** Issue #11 meta — *"applying [the logging-privacy] rules to
adjacent Flask code may surface similar gaps. Worth a broader privacy
audit as a follow-on."*
**This is an audit, not a fix.** Each finding is filed for a future
session that decides log-level / redact / delete per-site. Decisions
are deliberate per the discipline established in `docs/ops/logging-privacy.md`.

## Discipline being applied

Per `docs/ops/logging-privacy.md` § "What this implies":

> Logs never contain drawer text, ingest bodies, or wake-up results.
> These are always-personal-data payloads; pseudonymization of the
> user_id does nothing to protect them.

User chat messages, search queries derived from user input, and URLs
extracted from user messages are all "always-personal-data payloads"
by the same logic. Log statements that include them in their format
string are flagged.

## Findings — HIGH severity (full content at INFO)

These log unbounded user content verbatim to journald. journald default
retention is host-policy (typically 4 weeks); readers are root +
`systemd-journal` group. Under `User=root` Flask (today), effectively
just root.

### F-H1 · `app/memory_v2/extractor.py:204`
```python
logger.info(f"🔍 MemoryExtractor.extract() called with message: '{user_message}'")
```
Already filed at issue #11 as the primary offender. Fires on every
chat request. Full message content, no length cap.

### F-H2 · `app/memory_v2/extractor.py:249`
```python
logger.info(f"🔍 Pattern extraction on text: '{text}'")
```
`text` here is user-derived (pattern extractor input). Same risk
class as F-H1. Should land in the same fix session as F-H1.

### F-H3 · `app/blueprints/chat_routes.py:414`
```python
logger.info(f"Detected image request from user {user_id}: {user_prompt}")
```
Full `user_prompt` at INFO when an image-generation request is
detected. user_id is also pseudonymizable (per the logging-privacy
doc, that doesn't protect the prompt).

## Findings — MEDIUM severity (truncated content at INFO)

These show ~30-100 chars of user content per fire. Truncation reduces
exposure but does not eliminate it — short prompts ("how do I cure
this rash" type) fit entirely within 80 chars. Logging-privacy
discipline treats truncated content as still-personal-data.

### F-M1 · `app/memory_v2/orchestrator.py:477`
```python
logger.info(f"🔍 Extracting memories from: '{user_message[:100]}...' (len={len(user_message)})")
```
Already filed at issue #11. Truncated to 100; still embeds user
content. The `len=` part is fine alone.

### F-M2 · `app/services/chat_engine.py:639`
```python
logger.info(f"🧠 Intent classified: {result.get('intent_type')} for '{prompt[:50]}...'")
```
First 50 chars of user prompt for every intent classification.

### F-M3 · `app/services/chat_engine.py:1014`
```python
logger.info(f"🔍 HISTORY DEBUG: previous_messages count={len(previous_messages)}, last 4: {[m.get('prompt', '')[:30] for m in previous_messages[-4:]]}")
```
First 30 chars × last 4 conversation turns. Multiplies exposure across
turns. Marker `HISTORY DEBUG` suggests debug-era; should review
whether it should still be at INFO.

### F-M4 · `app/blueprints/chat_routes.py:1348`
```python
logger.info(f"[Stream] MODE=search forced by UI. type={_forced_search_type} query={user_prompt_raw[:80]!r}")
```
First 80 chars of raw user prompt in search-forced path.

### F-M5 · `app/blueprints/chat_routes.py:2534`
```python
logger.info(f"Pointer resolved: '{original_prompt[:50]}' → '{user_prompt[:80]}'")
```
TWO truncated user prompts per fire (before + after pointer
resolution). Logs the conversational rewrite chain.

### F-M6 · `app/blueprints/chat_routes.py:2836`
```python
logger.info(f"🍳 Injected recipe format reminder for: {user_prompt[:80]}...")
```
80 chars per recipe-detected message.

### F-M7 · `app/smart_router.py:258`
```python
content_preview = content[:100] if content else "(empty)"
logger.info(f"🔍 History msg[{i}]: content_len={len(content)}, preview='{content_preview}...'")
```
First 100 chars per history message during smart-routing analysis.
Loop body — fires once per recent message per `smart_route()` call.
The `🔍` debug-marker shape is the same as the other extractor logs;
likely debug-era.

## Findings — MEDIUM severity (user-derived search queries)

Search queries are user input, just one transform removed (the model
generates the query from the prompt, often verbatim or near-verbatim).

### F-M8 · `app/blueprints/chat_routes.py:1497`
```python
logger.info(f"[Stream] web_search: query='{ws_query}', type={ws_type}, reason='{ws_reason}'")
```
Full search query + reason at INFO. The reason is model-generated
and can include user-prompt fragments.

### F-M9 · `app/blueprints/chat_routes.py:1534`
```python
logger.info(f"[Stream] web_search (flushed): query='{ws_query}', type={ws_type}")
```
Same, flushed branch.

## Findings — adjacent risk classes

These don't log user message content directly but share the
"derived-from-user-input" risk shape that the logging-privacy
discipline cares about.

### F-A1 · `app/blueprints/chat_routes.py:2419`
```python
logger.info(f"Detected {len(detected_urls)} URL(s) in message: {detected_urls}")
```
URLs in user messages are personal data by the spec's logic —
they reveal what the user is reading / browsing. Full URL list
at INFO.

### F-A2 · `app/blueprints/chat_routes.py:2367`
```python
logger.warning(f"🛡️ Blocked prompt injection from user {user_id}: {injection_check.matched_patterns}")
```
matched_patterns are regex-derived, not full content — but a
hostile reader can reconstruct rough intent from which patterns
matched. Probably acceptable for a security-sensitive WARNING;
worth confirming.

### F-A3 · `app/memory_v2/embeddings.py:61`
```python
logger.warning(f"Jina API error {response.status_code}: {response.text[:200]}")
```
Third-party API error body. Jina typically doesn't echo input,
but a malformed-input error path could include a snippet. Low
risk but worth a one-line check during the fix session.

### F-A4 · `app/blueprints/chat_routes.py:1796`
```python
logger.info(f"🩹 Current file='{cf.get('path')}' content_len={len(cf.get('content',''))}")
```
File path of an in-flight artifact. User-named file paths can
leak project structure / personal naming. Length-only for content
is fine.

## Out of scope (not flagged)

- Length-only (`len(content)`, `len(reasoning)`) — these are
  diagnostic shapes the logging-privacy discipline explicitly allows.
- Model-generated content snippets at WARNING for failure paths
  (e.g. recipe format validation, search synthesis errors) —
  the model's output isn't user input, so the privacy concern is
  one degree removed. Worth a separate audit pass for "model output
  privacy" (different discipline — risk is RAG leakage, not user
  input exposure).
- DEBUG-level statements — journald default policy on the prod host
  does not capture DEBUG. Confirmed via the prior #11 issue body.
  If DEBUG capture changes, this audit needs a re-run.
- `chat_engine.py:1014` HISTORY DEBUG was marked as MEDIUM above
  even though shape suggests "DEBUG" — the actual call uses
  `logger.info`, so it's in scope. Verified.

## Disposition

- **Not P1-entry blocking.** Per the same logic as #11.
- **One issue or many?** The pattern-of-debug-leftover-INFO is
  consistent across F-H1, F-H2, F-M1, F-M7 (all `🔍`-marked, all
  in memory_v2 / smart_router heritage). One umbrella issue
  tracking the debug-era class + several issue-per-call-site for
  the chat_routes one-offs may scope better than a single mega-issue.
- **Fix session is its own session.** Per #11's guidance. This
  audit's job is finding, not deciding.

## Recommended next steps

1. Operator decides: file an umbrella issue + linking to this audit
   doc, or close this audit out as the artifact?
2. The eventual fix session decides per-call-site between three
   options enumerated in #11: redact / demote-to-DEBUG / delete.
3. Re-run this audit after any change to `docs/ops/logging-privacy.md`
   or after journald default-capture policy on the prod host
   changes.

## Audit method

```
grep -rnE 'logger\.(info|warning|error|debug)\(.*\b(prompt|user_message|user_prompt|message|query|content|text|extracted_text|display_prompt)\b' \
  app/memory_v2/ app/memory_v3/ app/services/chat_engine.py \
  app/blueprints/chat_routes.py app/smart_router.py
```

Plus targeted reads of `smart_router.py:250-265` and adjacent files.
50+ matches; ~13 flagged after triage. Truthful audit boundary: this
sweep covers the explicit message-content variable names; it doesn't
catch logs that build payloads through intermediate vars (e.g. `f"{x}"`
where `x` was assigned `user_prompt[:N]` ten lines up). A static-
analysis pass with taint tracking would cover that gap; out of scope
for this hand audit.
