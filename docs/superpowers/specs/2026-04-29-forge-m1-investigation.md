# Forge M1 — Investigation: Integration Surface

**Date:** 2026-04-29
**Status:** Investigation only — pre-spec. No implementation in this artifact.
**Branch:** `feat/forge-m1-investigation` (off `origin/main` @ `f53b22d`)
**Scope:** Map the integration surface for **Forge M1 — the validation loop**.
M1 is the smallest unit that solves the Netflix-clone-class bug class: after the
model produces an artifact, the system runs an in-browser parser/lint check
against it; if validation fails, feed the error back to the model and retry,
with multi-model escalation (V4-Flash → V4-Pro → Kimi-K2.5 → GPT-4.1) up to 4
attempts. **No new tools, no Plan, no Snapshot — just `parse → if fail retry →
escalate per cascade`.**

Companion to the upcoming `2026-04-29-forge-coding-infrastructure-design.md`
(the architectural spec). This doc is the input the spec is written from, not
the spec itself.

All file:line citations verified in `/root/xelas.io/xelasphere.io-` working
tree at branch HEAD. `/opt/xelasphere` is the deployed copy and is **not** the
source of truth here — DeepSeek V4 was deployed there directly via file-copy
but the corresponding commit lives on `feat/deepseek-v4-swap`, not yet on
`main`. Several findings below depend on this distinction.

---

## Required reading — what each spec contributes

### `2026-04-06-live-patch-streaming-design.md`

Status: shipped (commit landed; UI live). What we learn:

- The frontend already runs an **incremental-parse-during-stream** state
  machine inside `app/static/js/codeWindow.js::processLine()` and
  `artifactPanel.js::openPatchStreaming()` / `applyLivePatch()` /
  `finalizePatchStreaming()`. Each `done` chunk hands the panel the
  authoritative server-applied artifact.
- **Important contract:** "server result is authoritative; frontend animation
  is cosmetic." Forge M1 must respect this — validator runs server-side OR
  client-side, but the source of truth on `done` is whatever the server sent.

### `2026-04-15-structure-intelligence-v1-design.md`

Status: approved, not yet implemented. What we learn:

- Pre-stream classification path already exists (`classify_user_intent()` in
  `chat_engine.py`). It returns structured JSON (`intent_type` / `confidence` /
  `scaffold`) and can be augmented additively.
- **Pattern reused by Forge:** SSE first-event delivery. Structure-intel sends
  a `scaffold` event before any model tokens. Forge M1 can use the same
  envelope shape for `forge_retry` events.
- **Fallback discipline:** every layer falls back gracefully. Forge M1 must
  carry the same property — if the validator itself errors, the user sees the
  artifact uncritiqued, never a stuck stream.

### `2026-04-14-background-sub-agents-design.md`

Status: approved, not implemented. **Mostly NOT relevant to M1** (sub-agents
are out-of-process via the `xela` CLI, with Postgres + Redis pub/sub and a
systemd supervisor; Forge M1 is in-process inside the existing `/chat/stream`
endpoint). What we learn:

- The "inline card in chat stream" UX pattern (`agentCard.js`) is the design
  precedent for how transcript blocks could render inside the chat. M1 only
  needs a single "Retry — escalating to V4-Pro" line, but the visual idiom is
  established.
- **Tool allowlist concept** — for later Forge milestones (M2 has explicit
  tool palette). M1 has none.

### `2026-04-12-replay-convergence-design.md`

Status: proposed. What we learn:

- Replay routes AI text through `new StreamRenderer(container, {replay:true})`
  with `.append(fullContent).finalize()`. DOM is byte-identical to live stream.
- **Persistence contract:** what gets saved is `display_content` /
  `chat_summary` (raw markdown, ACTION line stripped). Replay shows that
  content.
- **Critical for M1:** save happens *once* per user turn at
  `chat_routes.py:2257` (`save_interaction(...)`), AFTER all retries complete.
  Failed retry attempts are not persisted at all. This means the "Retry —
  escalating to V4-Pro" transcript block needs to be saved INTO the persisted
  message content — otherwise it disappears on replay.

### `2026-04-07-fenced-artifact-format-design.md` *(not in user's list, but in scope)*

Status: shipped. What we learn:

- Wire format is fenced (`artifact-meta` JSON block + `artifact-file:<path>:<lang>`
  raw-content blocks). Already streamed-detectable.
- Server parser entry: `app/artifacts.py::extract_artifact_any_format()`
  (`:723`) → tries fenced first (`extract_fenced_artifact` `:677`) then old
  JSON (`extract_artifact_block` `:572`).
- **Important to M1:** the existing parse already catches malformed
  *artifact-level* JSON (e.g. truncated meta). What it does NOT catch is
  *content-level* syntax errors inside the file bodies — broken JSX inside an
  otherwise-valid `artifact-file:App.jsx:jsx` fence parses fine at this layer.
  That's the gap M1 fills.

### `2026-04-06-incremental-patching-ux-reference.md` *(not in list, but adjacent)*

Status: reference doc. What we learn:

- `apply_patches()` (`artifacts.py:773`) does exact + fuzzy matching with
  fall-back to full replace.
- **Existing failure pattern that M1 mirrors:** when patch application fails,
  the server already runs a synthetic regen with the original prompt against
  cascade models (`chat_routes.py:1956-2002`). M1 reuses this exact shape but
  with a different trigger (validator failure, not patch failure) and a
  different prompt augmentation (validator error, not patch error).

### Other relevant specs that exist but were not load-bearing here

`2026-04-10-stream-renderer-design.md` (rendering pipeline — relevant only if
M1 introduces a new block type, which it shouldn't), `2026-04-12-streaming-organism-design.md`
(stream block AST — same caveat). `2026-04-06-server-python-sandbox-design.md`
is the foundation for M3 (server-tier sandbox) but not M1.

---

## Q1. Artifact-streaming completion point — where the validator hooks

### Primary path: `/chat/stream` (the only user-facing path)

The streaming generator's `chunk_type == 'done'` branch begins at
`app/blueprints/chat_routes.py:1675`:

```
1675   elif chunk_type == 'done':
1676       # Flush normalizer for models that don't emit trailing \n
```

The downstream sequence inside that branch:

| Line | Step |
|---|---|
| `:1696` | `full_content = ''.join(content_buffer)` — raw model text assembled. |
| `:1710` | `parsed_action = ActionParser.parse(full_content)` — produces the `ParsedAction` (Q2). |
| `:1729-1751` | **Existing cascade #1** — for `create_artifact` with `payload is None` (malformed artifact JSON), iterates `[stream_model] + ARTIFACT_MODEL_CASCADE`, calls `get_ai_response(enhanced_prompt, cascade_model)`, re-parses each. |
| `:1775-1831` | **Existing cascade #2** — patch-application failure regen (path A). |
| `:1956-2002` | **Existing cascade #3** — patch-application failure regen (path B, full file context). |
| `:2049-2065` | `artifact_data` dict assembled from `routing.artifact_payload`. |
| `:2166` | `Stream done payload: ...` log line. |
| `:2177` | `artifact_store.persist_decision(...)` — **Firestore write**. |
| `:2185` | `✅ Stream artifact persisted` log. |
| `:2257` | `save_interaction(...)` — chat history record (single-call invariant from Q4). |
| `:2346` | `orchestrator_record_usage(stream_model, ...)` — accountant (single-call gotcha, see Q6). |
| `:2382` | `yield f"data: {json.dumps(done_payload)}\n\n"` — final SSE event. |

**Forge M1 validator hook = between line `:1751` (end of existing cascade
#1) and line `:2042` (start of `if routing.artifact_payload:` artifact_data
build).** This is the *one* point in the stream lifecycle where:

1. `routing.artifact_payload` is populated with the final parsed dict, AND
2. nothing has been persisted yet (so a retry decision is still cheap), AND
3. all existing cascade behaviors have already fired (validator becomes the
   *next* discriminator after JSON-parse + patch-apply succeed).

### Non-streaming path (`get_ai_response`)

`app/services/chat_engine.py::get_ai_response()` at `:1133` is a thin wrapper
around `send_prompt`. It returns `(response_text, is_code_response,
reasoning_content)`. It does **not** parse. Callers (the three cascade loops
above) call `ActionParser.parse(regen_response)` themselves.

**This path is never hit standalone by users** — it's only used for the
internal regen retries inside the streaming `done` handler. There is no
secondary "non-streaming" user-facing entry point that needs its own validator
hook for M1.

### Verification

Lines 1675, 1710, 2177 verified in current working tree. No agent extrapolation
on this point.

---

## Q2. Artifact representation at completion

At line `:1710` (after `ActionParser.parse`), the in-memory shape is
`ParsedAction.artifact_payload` — a Python dict produced by
`normalize_artifact_payload()` at `app/artifacts.py:920`:

```python
{
    "title": str,
    "entrypoint": str,           # path of first/designated entry file
    "files": [
        {
            "path": str,
            "language": str,     # e.g. "jsx", "tsx", "ts", "js", "py", "css"
            "content": str,      # raw text content — the thing M1 validates
            "mime": Optional[str],
            "isBinary": bool,
            "byteSize": int,
        },
        ...
    ],
    "meta": dict,                # arbitrary metadata from model
    "summary": str,              # first 400 chars of entrypoint content
    "primaryLanguage": str,
    "fileCount": int,
    "totalBytes": int,
}
```

This is then wrapped in an `ArtifactDecision` dataclass (`artifacts.py:1196`)
held by the local `routing` variable. The post-persistence `artifact_data`
dict (assembled at `:2049-2065`) adds `artifactId`, `activeVersion`,
`versions[]` — but those are populated *after* `persist_decision`, so the
validator only sees the pre-persistence shape above.

**Plain dict; no AST. The thing M1 needs to validate is each
`files[i].content` string against its `files[i].language`.**

---

## Q3. SHA-256 state-tracking interaction

All in `app/context/artifact_state_tracker.py` (266 lines, fully read):

### Hash lifecycle (verified)

| Event | Method | Line | Effect |
|---|---|---|---|
| New artifact created | `sync_from_files(files)` | `:53` | Every file gets `content_hash = sha256(content)`, `last_sent_hash = None`. All dirty. |
| Context built (sent to model) | `mark_sent(file_paths)` | `:98` | `last_sent_hash := content_hash`. Now clean. |
| Patch applied | `mark_mutated(file_path, new_content)` | `:105` | `content_hash := sha256(new)`. Dirty again. |
| New version (regen) | `sync_from_files(files_v2, preserve_sent_hashes=True)` | `:53` | Unchanged files keep their `last_sent_hash`. Changed files get `last_sent_hash = None` (dirty). |

`is_dirty()` definition at `:36` is plain `content_hash != last_sent_hash`.
There is no third state — no "pending", no "superseded", no "validation-failed".

### What M1 should do on a failed-validation intermediate

The tracker is **session-keyed in an LRU dict** (`_SESSION_STATES` at `:215`).
All retries within one user HTTP request share the same tracker instance —
session_id doesn't change between retries.

**Recommended behavior (to honor existing invariants from
`tests/test_artifact_state_tracker.py`):**

1. **Do not call `sync_from_files`** for the *intermediate* failed artifact.
   The intermediate was never sent to the model, never persisted to Firestore,
   never displayed — there's no reason to register it.
2. On final success (after retry), call `sync_from_files(final_files)` —
   **without** `preserve_sent_hashes=True`. That flag is for "version 2 of an
   artifact the user actually saw"; the failed intermediate doesn't qualify.
3. The existing invariant from test line 21+59 holds: the final artifact's
   files are dirty until the next `mark_sent`, which happens during the next
   ContextBuilder pass.

**No code changes to the tracker required for M1.** The tracker already
handles "an artifact appeared, then was overwritten before being sent" — it's
just a `sync_from_files` call replacing the previous state. The
session-keyed cache means there's no leak; the failed intermediate is GC'd
as soon as the next sync happens.

### Invariants to preserve (from test file)

- A file is dirty from creation until `mark_sent` is explicitly called.
  → M1 must not accidentally `mark_sent` a failed-validation file.
- `mark_mutated` always dirties, regardless of prior state.
  → M1 should not call `mark_mutated` on the intermediate.
- `preserve_sent_hashes=True` is the only mechanism that carries clean state
  across version transitions.
  → M1's retry path must NOT pass `preserve_sent_hashes=True` (we want a
  full reset because the prior state was never sent).

---

## Q4. Replay-convergence interaction

### What gets persisted vs reconstructed

The streaming `done` handler calls `save_interaction(...)` exactly once at
`:2257`, **after** all cascade retries have completed. Failed retry attempts
write zero records.

The persisted shape (per the live-patch-streaming spec and verified at
`:2257`) is the assistant message with `display_content` / `chat_summary` —
**stripped raw markdown, ACTION line removed**. This is what `loadSession`
hands to the renderer on replay.

### Replay rendering path

Per `2026-04-12-replay-convergence-design.md`, AI-text replay routes through
`new StreamRenderer(container, { replay: true }).append(fullContent).finalize()`.
**This spec is "Proposed" — not yet shipped.** The current implementation
likely still uses `appendTextMessage(container, text)` for AI text (the spec's
"current state" diagram). Either way, the contract is: replay reads `content`
verbatim from Firestore.

### What M1 implies for replay

If Forge M1 wants the "Retry — escalating to V4-Pro" transcript line to
**survive page reload**, it must be embedded in the persisted message
content. Three implementation shapes (to be picked in spec phase):

1. **Inline-in-prose**: emit the retry line as part of `chat_summary` /
   `chat_text` so it lives in the saved markdown. Cheapest, but prose-stream
   pollution.
2. **New SSE event type + new persisted field**: emit `forge_attempts: int`
   in the `done_payload`, save it into the message record alongside `content`,
   and have the replay renderer reconstruct the breadcrumb from the count
   ("retried 1×"). Cleaner separation, requires schema change.
3. **No replay support in M1**: the retry visualization is live-only;
   reloading the page shows only the final clean artifact, no breadcrumb.
   Smallest blast radius.

**This is a spec-phase decision, flagged for §Open Questions.**

---

## Q5. Error-context feedback to model for retry

### Existing prior art (verified)

**For malformed artifact JSON (cascade #1, `:1729-1751`):**
The retry calls `get_ai_response(enhanced_prompt, cascade_model)` with
`enhanced_prompt` **verbatim** — same prompt that produced the broken
response, just sent to a different model. **No error augmentation.** The
implicit assumption is that a different model with the same prompt will
succeed where the first failed.

**For patch-apply failure (cascades #2 and #3, `:1775-1831` and `:1956-2002`):**
A *new synthetic prompt* is built from scratch:

```python
regen_parts = ["The artifact_patch could not be applied. "
               "Please regenerate the COMPLETE artifact with the changes applied.\n\n"
               "Current files:\n"]
for cf in current_files:
    regen_parts.append(f"\n{cf['path']} ({lang}):\n```{lang}\n{cf['content']}\n```\n")
regen_parts.append(f"\nUser request: {prompt}\n\n"
                   "Respond with ACTION: create_artifact and regenerate ALL files...")
regen_prompt = "\n".join(regen_parts)
```

This includes:
1. The error context as a leading instruction.
2. All current files inline (post-strip code blocks, not JSON-escaped).
3. The original user request.
4. Format instructions (fenced format).

It is then sent via `get_ai_response` (not streaming). `ActionParser.parse`
consumes the response.

### What this means for M1

There is **no existing pattern** for "validation error → augment prompt → retry"
where the validation is at the artifact-content level (JS/JSX/TS syntax). The
patch-regen pattern (cascades #2/#3) is the closest analogue and is the
template Forge M1 should follow:

- **Build a synthetic retry prompt** that includes:
  1. The validator error message (parser line + column + message + the broken
     line excerpt).
  2. The full broken artifact (all files, fenced format).
  3. The original user request.
  4. Format instructions.
- **Send via** `get_ai_response(retry_prompt, cascade_model)` — same call
  shape as the existing regens.
- **Re-parse via** `ActionParser.parse(retry_response)`.
- **Re-validate** via the same validator. On success → break loop. On failure
  → escalate to next model in cascade.

### Where the error slots in message history

There is **no pattern for synthetic assistant messages** in the existing retry
paths. The regens build a single user-style prompt string and send it. They do
not synthesize prior assistant turns ("here's what you said before") or feed
back into ContextBuilder.

`extract_turn_metadata()` at `app/context/context_builder.py:82` accepts
`had_error: bool` and `error_detail: str`, but those fields are used only in
**Tier 2 conversation summaries of past turns** (cross-turn) — not in
in-flight retry context. They wouldn't help M1 directly.

### Recommended retry-prompt shape (for spec phase)

Modeled exactly on the patch-regen pattern at `:1956-1976`. Roughly:

```python
retry_parts = [
    f"Your previous response produced an artifact that failed validation.\n",
    f"Validator: in-browser parser ({validator_kind})\n",
    f"Error: {error.message}\n",
    f"Location: {error.file_path}:{error.line}:{error.column}\n",
    f"Excerpt:\n```\n{error.excerpt}\n```\n\n",
    "Here is your previous artifact (unchanged):\n\n",
]
for f in broken_artifact_files:
    retry_parts.append(f"```artifact-file:{f['path']}:{f['language']}\n{f['content']}\n```\n")
retry_parts.append(
    f"\nUser request: {original_prompt}\n\n"
    "Regenerate the COMPLETE artifact with the syntax error fixed. "
    "Use ACTION: create_artifact and the fenced format."
)
retry_prompt = "".join(retry_parts)
```

Slot location: directly inside the new validator-driven cascade loop
(between `:1751` and `:2042`). Same iteration shape as the existing regens.

---

## Q6. Orchestrator gotchas blocking the loop

### Gotcha #1: accountant only debits the primary stream model

`orchestrator_record_usage(stream_model, _input_est, _output_est)` is called
**exactly once** per user turn at `chat_routes.py:2346`. This call uses
`stream_model` — the *initial* streaming model, not any cascade model.

The cascade retry calls (`get_ai_response` at `:1734`, `:1806`, `:1982`) have
**no `orchestrator_record_usage` wrapping them**. Their token usage is
currently invisible to the accountant. **Daily budget is not debited for
cascade retries.**

**This is a pre-existing bug, not introduced by M1.** But Forge M1 will
*amplify* it — the existing cascades fire only on hard failures (malformed
JSON, patch-apply fail, both rare); M1's validator-driven cascade will fire
on every JSX syntax error, of which the user has had at least two in this
session alone (Netflix `(<></>) =>` bug, the regen at 18:23:03).

**Recommendation for spec phase:** wrap each `get_ai_response` cascade call in
its own `orchestrator_record_usage(<retry_model>, ...)` call. This is a
non-M1-specific fix, but M1 should not ship without it — otherwise rate-limit
visibility degrades.

### Gotcha #2: `ARTIFACT_MODEL_CASCADE` on `main` is stale relative to deploy

In the `/root/xelas.io/xelasphere.io-` working tree on `main` HEAD (`f53b22d`):

```python
# app/services/chat_engine.py:154-160
ARTIFACT_MODEL_CASCADE = [
    "deepseek-chat",          # ← deprecated by DeepSeek; not in catalog
    "gpt-4.1",
    "qwen/qwen3-32b",
    "kimi-k2.5",
    "llama-3.3-70b-versatile",
]
```

The DeepSeek V4 swap is on `feat/deepseek-v4-swap` (commit `537050c`), already
deployed to `/opt/xelasphere/` via file-copy but **not yet merged to main**.

**Implications for M1:**
- M1's spec must define the validator-driven cascade explicitly (V4-Flash →
  V4-Pro → Kimi-K2.5 → GPT-4.1) rather than reusing `ARTIFACT_MODEL_CASCADE`
  as-is.
- When M1 ships, either `feat/deepseek-v4-swap` must merge first OR M1 must
  carry its own `FORGE_VALIDATION_CASCADE` constant. The cascades have
  different purposes (M1 is a syntax-failure cascade specifically; existing
  cascade is a JSON-malform / patch-apply cascade) — keeping them as separate
  named constants is probably right architecturally regardless.
- The catalog has `MODEL_ALIASES` mapping `deepseek-chat → deepseek-v4-flash`
  on the V4 branch, so even if M1 references the legacy ID, alias resolution
  would route to the right place. Not relying on this is cleaner.

### Gotcha #3: registry not consulted by cascade

The cascade loops iterate `ARTIFACT_MODEL_CASCADE` directly without consulting
`app/orchestrator/registry.py`. If a cascade model is rate-limited, deprecated,
or disappeared from the provider's `/models` endpoint, the cascade loop still
calls it. The HTTP failure is caught by `except Exception as cascade_err`
at `:1750` and the loop continues. **No silent substitution occurs**, but
also no awareness of which models are healthy ahead of time.

For M1: acceptable for v1 (cascade has built-in fallback). Worth noting in
the spec as a future optimization (registry-aware cascade).

### Gotcha #4: `watcher.py` is silent on rate limits

The deprecation watcher at `:155-180` only fires on
"model disappeared from provider's /models list." A model returning 429
(rate-limited) is still in the list and never triggers a watcher event.
**Rate limiting is invisible to the watcher.** M1's cascade will encounter
429s and need to handle them via the catch-all exception block.

For spec: catch the specific 429 case in the cascade loop and short-circuit
to the next model immediately (no retry-with-same-model on rate limit).

### Non-gotchas (verified clean)

- **`stream_normalizer.py`:** instantiated fresh per request, no per-turn
  state assumption, multiple cascade calls within one turn don't break it.
- **Transcript persistence:** `save_interaction` called exactly once per
  turn (`:2257`). No risk of duplicate / partial records.
- **`StreamingActionBuffer`** (`action_parser.py:258`): per-stream instance,
  used during live token streaming for early ACTION detection. Doesn't
  participate in `done`-handler logic. Untouched by M1.

---

## Q7. Minimum new code surface for M1

### New files

**1. `app/services/artifact_validator.py` (~150 lines)**

Module containing:

- `ValidationError` dataclass — `{kind, file_path, line, column, message, excerpt}`.
- `ValidationResult` — `{ok: bool, errors: list[ValidationError]}`.
- `validate_artifact_payload(payload: dict) -> ValidationResult` — orchestrator
  function. Walks `payload['files']`, dispatches each to a per-language
  validator based on `language` field.
- `_validate_jsx(content: str) -> ValidationResult` — runs the in-browser
  parser (see §browser-side below) OR a server-side fallback.
- (M1 scope: only JSX/TSX/JS/TS validators. Python/HTML/CSS deferred — they
  rarely fail the same class of bug.)

**2. `app/static/js/forgeValidator.js` (~80 lines)**

Browser-side parser using esbuild-WASM. Parses each file, reports first
syntax error per file. Communicates result to server via the SSE round-trip
mechanism chosen in spec (see §Open Questions below).

**3. `tests/test_artifact_validator.py` (~120 lines)**

Unit tests for the validator. The Netflix-clone-bug case is the canonical
fixture: input artifact has `return (<></>) => window.removeEventListener(...)`
on line 45, expected result is `ValidationResult(ok=False, errors=[
ValidationError(kind='jsx-parse', line=45, column=12,
message='Binding invalid left-hand side in function parameter list')])`.

### Existing files touched

**`app/blueprints/chat_routes.py`** — single insertion of validator-driven
cascade between `:1751` and `:2042`:

```python
# After existing cascade #1 (malformed JSON), if we still have a payload:
if routing and routing.artifact_payload:
    validation = validate_artifact_payload(routing.artifact_payload)
    if not validation.ok:
        # Forge M1 cascade — escalating model retry
        for attempt, retry_model in enumerate(FORGE_VALIDATION_CASCADE, start=1):
            yield f"data: {json.dumps({'type': 'status', 'subtype': 'forge_retry', 'attempt': attempt, 'model': retry_model, 'errors': [...]})}\n\n"
            retry_prompt = build_validation_retry_prompt(routing.artifact_payload, validation, prompt)
            try:
                retry_response, _, _ = get_ai_response(retry_prompt, retry_model)
                orchestrator_record_usage(retry_model, ...)  # ← gotcha #1 fix
                retry_parsed = ActionParser.parse(retry_response)
                if retry_parsed.artifact_payload:
                    re_validation = validate_artifact_payload(retry_parsed.artifact_payload)
                    if re_validation.ok:
                        routing.artifact_payload = retry_parsed.artifact_payload
                        break
                    validation = re_validation  # carry forward latest error for next retry
            except Exception as e:
                logger.warning(f"Forge retry {retry_model} error: {e}")
                continue
        # If all retries failed, surface the broken artifact with a warning flag
        # (do NOT silently fail; user sees what we got + the validator complaint)
```

**`app/services/chat_engine.py`** — add a new cascade constant:

```python
FORGE_VALIDATION_CASCADE = [
    "deepseek-v4-flash",
    "deepseek-v4-pro",
    "kimi-k2.5",
    "gpt-4.1",
]
```

Or, after `feat/deepseek-v4-swap` merges, reuse the FALLBACK_CHAINS["coding"]
ordering. Decision deferred to spec phase.

### Where the in-browser parser lives — open question

The agent's investigation flagged this as architecturally underspecified. Two
shapes:

**Option A — Server-side validation** (simpler, no SSE workaround):
- Use `esprima-python` or call `node --check` / `tsc --noEmit` via subprocess
  for JSX/TS validation, server-side.
- Validator runs synchronously in the existing `done` handler.
- No new endpoint, no SSE round-trip. Fits cleanly into the existing flow.
- Cost: ~10-50ms per file for syntax check (esprima); subprocess overhead
  if using node/tsc.
- **Trade-off:** "in-browser" was the spec language; server-side is
  technically a different architecture but functionally equivalent for the
  syntax-check use case.

**Option B — Client-side esbuild-WASM** (matches original spec language):
- Server emits `done` SSE event including the artifact.
- Client parses with esbuild-WASM; on failure, POSTs a new request to
  `/artifact/validate-retry` with the error context.
- Server runs the cascade, yields a new artifact via second SSE stream.
- Cost: split user turn into two HTTP requests; transcript spans both;
  message-record schema needs the retry trail.

**Recommendation for spec phase:** Option A for M1. Lower complexity,
single-turn semantics, faster ship. Option B becomes interesting at M2 when
we want runtime checks (esbuild bundle, React SSR snapshot) that are
genuinely browser-shaped. M1's syntax-only check doesn't need the browser.

### Minimum transcript block

The "Retry — escalating to V4-Pro" line slots cleanly into the existing
status-event pattern (`chat_routes.py:1778`, `:1958`). Server emits:

```json
{"type": "status", "subtype": "forge_retry", "attempt": 2, "model": "deepseek-v4-pro", "reason": "validator: jsx-parse @ App.jsx:45"}
```

Client (`startNewChatModule.js` SSE handler) already routes `type: status`
events; just add a render branch for `subtype: forge_retry` that produces a
small transcript block. No new SSE event type, no new server-side machinery.

For replay support, see Q4 §"What M1 implies for replay" — defer to spec.

---

## Cross-cutting findings

### CC-1: DeepSeek V4 swap not on `main` yet

`feat/deepseek-v4-swap` (commit `537050c`) is open as a PR but unmerged.
`main`'s `ARTIFACT_MODEL_CASCADE` references `deepseek-chat` /
`deepseek-reasoner` — IDs that DeepSeek has deprecated (still functional via
backward-compat aliases until DeepSeek removes them).

**Implication for M1 sequencing:** spec phase should decide whether M1
depends on V4 merge (cleaner) or M1 carries its own cascade list (independent).
If independent, spec must explicitly use `deepseek-v4-flash` /
`deepseek-v4-pro` IDs and document the dependency on either V4-merge OR
catalog alias resolution (which DOES work via `MODEL_ALIASES` even on
unmerged main).

### CC-2: Accountant gap is a pre-existing bug

Cascade retries (current and future M1) are not debited from the daily token
accountant. M1 amplifies this from "rare hard failure" to "common syntax
failure." Spec should treat the `orchestrator_record_usage` wrap as a
prerequisite, not an M1 feature.

### CC-3: `save_interaction` as the persistence boundary

The single-call invariant at `:2257` simplifies M1: failed retries write
nothing. The only persistence question is whether the *successful* message's
record carries any retry breadcrumbs (Q4 §replay). Defer to spec.

### CC-4: `StreamingActionBuffer` does not participate

Lives in `app/context/action_parser.py:258`. Used during live token streaming
to detect ACTION lines early. Has no involvement in the `done` handler.
**Untouched by M1.**

### CC-5: Replay-convergence spec is "Proposed" not shipped

If M1's transcript block is wired into the StreamRenderer pipeline as a new
block type, the replay-convergence spec's per-renderer `opts.replay` plumbing
needs to extend to it. If M1's transcript block is just a status-emitted
toast that's not part of the message DOM, this is a non-issue. Spec phase
to decide.

### CC-6: No existing in-browser parser stack

esbuild-WASM is not currently in the frontend bundle. The artifact panel uses
CodeMirror or similar for display, but nothing for parse-validation. **Adding
esbuild-WASM is a non-trivial bundle-size addition (~600KB).** Server-side
validation (Option A above) avoids this entirely.

---

## Open questions for spec phase

1. **Validator location**: server-side (Option A, simpler) vs in-browser
   esbuild-WASM (Option B, matches original spec language but needs SSE
   workaround).
2. **Replay support**: does the "🔁 Retry" line survive page reload?
   (Three implementation shapes in Q4.)
3. **Cascade list**: separate `FORGE_VALIDATION_CASCADE` constant, or reuse
   `ARTIFACT_MODEL_CASCADE` (post V4 merge)?
4. **Failure-after-cascade behavior**: when all 4 retries fail, do we surface
   the broken artifact with a warning ribbon, or hold it back entirely and
   tell the user? (Spec says never silently fail — but "surface broken"
   vs "surface error" is still a UX call.)
5. **Cancel button**: M1 spec says one. Where does the cancel signal slot in
   the SSE stream — new event type, or reuse existing abort path? Brief
   investigation needed in spec phase.
6. **AskUser tool**: deferred to M3 per the design discussion. M1 has none.
7. **Server-side accountant fix**: include in M1 PR, or land separately as a
   prerequisite?

---

## Minimum surface estimate

| Type | Count | Notes |
|---|---|---|
| New Python files | 1 | `app/services/artifact_validator.py` |
| New JS files | 0 (Option A) or 1 (Option B) | esbuild-WASM browser worker only on Option B |
| New tests | 1 | `tests/test_artifact_validator.py` + 4-6 fixture artifacts |
| Touched Python files | 2 | `chat_routes.py` (insert validator cascade), `chat_engine.py` (add FORGE_VALIDATION_CASCADE constant + accountant wrap) |
| Touched JS files | 1 | `startNewChatModule.js` (status event subtype handler) |
| Schema changes | 0 (M1 minimum) | Only if Q4 replay-survival decision needs `forge_attempts` field |
| Migration impact | None | Existing artifacts/sessions unchanged |

Estimated implementation effort once spec is locked: **3-5 working days**
for one focused dev. Bulk of the work is the validator module + retry-prompt
construction + a robust test suite. The cascade loop is a copy-modify of the
existing `:1956-2002` regen path.

---

## What this doc does NOT do

- It does not propose architecture decisions (those land in the spec).
- It does not pick A vs B on the in-browser-parser question (deferred).
- It does not scope M2/M3 (visible Plan/Snapshot, server sandbox, etc.).
- It does not include code beyond illustrative excerpts of existing patterns.

Spec authoring proceeds from this investigation as the input.
