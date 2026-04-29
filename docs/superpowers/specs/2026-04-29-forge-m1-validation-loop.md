# Forge M1 — Validation Loop (formal spec)

**Date:** 2026-04-29
**Status:** Spec — locked. Decisions in §"Locked decisions" are not re-opened
during implementation.
**Branch:** `feat/forge-m1-spec` (off `feat/forge-m1-investigation`)
**Inputs:**
- `docs/superpowers/specs/2026-04-29-forge-m1-investigation.md` — integration
  surface map, file:line citations, gotchas. Read before this spec.
- `docs/superpowers/notes/forge-design-summary.md` — multi-milestone design
  context. Captures the M1/M2/M3 phasing decision.

**Audience:** the implementing engineer + reviewers. The spec assumes the
investigation doc has been read; it does not re-cite line numbers already
documented there.

---

## 1. Goal

Catch syntax-level artifact bugs (the Netflix-clone-class
`return (<></>) =>` pattern) before the broken artifact reaches the panel.
After the model produces an artifact, the server runs a per-file syntax
parser on every JSX/TSX/JS/TS file in the payload. If any file fails, the
server feeds the validator error back to the model and retries, escalating
through a fixed model cascade (V4-Flash → V4-Pro → Kimi-K2.5 → GPT-4.1) up
to four attempts. The user sees a small "🔁 Retry — escalating to V4-Pro"
status block in chat for each retry attempt; on success they see the clean
artifact with no further intervention. **No new tools, no Plan, no Snapshot,
no panel changes.** This is the smallest infrastructure that solves the
syntax-bug class today, shipping in days rather than weeks.

## 2. Out of scope

Explicit non-goals for M1 — these belong to M2 / M3 and **must not** creep
into the M1 PR:

- **No `Plan` tool.** No visible plan checklist. The retry loop is
  invisible-by-default except for the `🔁 Retry` status line.
- **No `Snapshot` / `Rollback`.** Successful artifacts don't pin a
  version-history rail. (Existing version history continues to work as
  today.)
- **No `PatchFile` / `EditFile` / `MultiEdit` tools.** M1's retry asks the
  model to regenerate the COMPLETE artifact each time, mirroring the
  existing patch-regen pattern.
- **No artifact panel changes.** Code/Preview tabs untouched. No file tree.
  No version rail additions. No diff overlays.
- **No server-side container sandbox.** No Docker, no `npm install`, no
  test runner. Pure syntax check via subprocess against installed `node`
  and `tsc` binaries.
- **No client-side esbuild-WASM.** No browser-side parser. No second-HTTP-
  request SSE round-trip.
- **No cancel button.** Hard 4-iteration cap and per-turn retry token budget
  (Decision 8) are the only bounds. Cancel UI ships with M2 when agent
  loops have multiple visible tool calls and cancellation is more useful.
- **No `AskUser` tool.** Deferred to M3.
- **No Python / HTML / CSS validation.** JSX/TSX/JS/TS only. Rationale in
  Decision 11.
- **No new fenced-format tools.** Existing `artifact-meta` / `artifact-file`
  fences are the only protocol. No new ACTION verbs.
- **No transcript replay polish.** Reload shows a single "Healed after N
  attempts" breadcrumb reconstructed from persisted counts (Decision 2);
  individual per-iteration cards are live-only.

## 3. Locked decisions

These are settled. The implementing engineer should treat them as constraints,
not as starting points for re-discussion. If a constraint blocks
implementation, escalate to spec-author rather than re-opening the decision.

1. **Validator location: server-side.** `node --check` for `.js`/`.jsx`,
   `tsc --noEmit --jsx preserve` for `.ts`/`.tsx`. Subprocess via `asyncio`
   with 3s timeout per file. No esbuild-WASM, no client-side validation.
2. **Replay persistence:** add two nullable fields to the message record —
   `forge_attempts: int` and `forge_healed_at: int | null` (the iteration
   number that succeeded; `null` = exhausted).
3. **Cascade:** `FORGE_VALIDATION_CASCADE` is its own constant in
   `chat_engine.py`, hardcoded to `["deepseek-v4-flash", "deepseek-v4-pro",
   "kimi-k2.5", "gpt-4.1"]`. **Not** a reuse of `ARTIFACT_MODEL_CASCADE`.
   Different failure class, different cascade.
4. **Failure-after-cascade:** render the broken artifact with a prominent
   red warning ribbon at the top of the panel: *"Validation failed after 4
   attempts. Review for syntax errors before using."* Validator errors
   expanded inline below the ribbon. **Never hide the artifact entirely.
   Never silently fail.**
5. **Cancel button:** deferred to M2.
6. **AskUser tool:** deferred to M3.
7. **Accountant fix:** prerequisite, ships with M1 PR. Wraps every cascade
   `get_ai_response` call (existing cascades #1/#2/#3 AND the new Forge
   cascade) in `orchestrator_record_usage`. Not split out as a separate PR.
8. **Per-turn retry token budget:** hard cap of 80,000 tokens spent on
   retries within a single user turn. On exceeding, stop the cascade early
   and surface broken-with-warning per Decision 4. Log
   `forge_budget_exhausted=true`.
9. **Loop detection / idempotency:** if iter N produces an artifact whose
   validator errors are byte-identical to iter N-1's errors, treat that
   tier as failed-fast and skip ahead two tiers (e.g. flash fails → pro
   produces same error → skip kimi, jump to gpt-4.1). Compare on
   `(file_path, line, message)` tuples sorted ascending.
10. **Telemetry contract (mandatory):** every artifact-bearing turn emits
    the seven `forge_*` metrics named in §12.
11. **Validator scope rationale:** JSX/TSX/JS/TS only. Documented because
    JSX/TSX is uniquely failure-prone — it requires context-sensitive
    parsing of a hybrid syntax that the model frequently corrupts. Vanilla
    JS, HTML, CSS rarely fail the same class of bug at this layer and
    would add subprocess overhead with low yield. Future-us should not
    think this was a forgotten case.

### Implementation prerequisite (not a decision, a fact)

Decision 3 references `deepseek-v4-flash` and `deepseek-v4-pro`. These IDs
are **not** in `MODEL_CATALOG` on `main` — they live on
`feat/deepseek-v4-swap` (PR open). On `main`, `resolve_model_id` would
fall back to `DEFAULT_MODEL` for these IDs.

**M1 PR therefore depends on `feat/deepseek-v4-swap` merging first.** The
V4 swap branch is already deployed to `/opt/xelasphere/` via file-copy and
verified working. Merging the V4 PR is a precondition listed in §13.

If the V4 swap merge slips, the fallback is to add minimal `MODEL_CATALOG`
entries for v4-flash/v4-pro as part of M1 PR (cherry-pick from the swap
branch). This is documented but not the recommended path.

---

## 4. Decision amendments since spec authoring

This section is the standing audit trail of decisions amended after the
spec was first authored. Future-you (or future-agent) reading this spec in
six months needs to see amendments at a glance, not buried in section
bodies. Add new amendments at the top of this section, dated.

### Amendment 1 — 2026-04-29 — Decision 1 (validator binary for JS/JSX)

**Original (Decision 1):** *"Use `node --check` for `.js`/`.jsx`,
`tsc --noEmit --jsx preserve` for `.ts`/`.tsx`. Subprocess via `asyncio`
with 3s timeout per file. No esbuild-WASM, no client-side validation."*

**Amended to:** *"Use `@babel/parser` via a thin Node CLI wrapper for
`.js`/`.jsx`. `tsc --noEmit --jsx preserve` for `.ts`/`.tsx` is
unchanged. Subprocess + 3s timeout + no-client-side-validation
constraints all preserved."*

**Rationale:** vanilla `node --check` does not parse JSX (Node has no
native JSX parser). Decision 1's letter would cause `.jsx` validation to
silently no-op, defeating the M1 goal of catching the Netflix-clone-class
JSX bug. The substitution preserves Decision 1's spirit — server-side,
Node-family subprocess, single binary, one timeout pattern — while
handling the JSX gap. Full alternative analysis (`tsc`-for-everything;
revert-to-Decision-1) in §6.3 "Deviation note (from Decision 1)".

**Impact on other decisions:** none. Decisions 2–11 unchanged. Telemetry
field names unchanged. Cascade unchanged. Persistence schema unchanged.

---

## 5. Architecture

### 5.1 Where Forge fits in the existing pipeline

```
[user message]
   │
   ▼
/chat/stream  (chat_routes.py:1675 begins the chunk_type=='done' branch)
   │
   ├─ :1696  full_content = ''.join(content_buffer)
   │
   ├─ :1710  parsed_action = ActionParser.parse(full_content)
   │
   ├─ :1729-1751  EXISTING cascade #1
   │             (malformed artifact JSON → cycle ARTIFACT_MODEL_CASCADE)
   │             [WRAPPED with orchestrator_record_usage in M1 PR — Dec 7]
   │
   ├─ :1775-1831  EXISTING cascade #2 (patch-apply fail, path A)
   │              [WRAPPED with orchestrator_record_usage in M1 PR — Dec 7]
   │
   ├─ :1956-2002  EXISTING cascade #3 (patch-apply fail, path B)
   │              [WRAPPED with orchestrator_record_usage in M1 PR — Dec 7]
   │
   ├─ ★★★ NEW: Forge validator cascade ★★★
   │           ── INSERTED between :1751 and :2042 ──
   │           if routing.artifact_payload:
   │               result = validate_artifact_payload(routing.artifact_payload)
   │               if not result.ok:
   │                   loop FORGE_VALIDATION_CASCADE (Dec 3)
   │                     emit SSE forge_retry status
   │                     build retry prompt (§7)
   │                     get_ai_response(retry_model)  + record_usage
   │                     parse + re-validate
   │                     check loop-detect (Dec 9)
   │                     check token budget (Dec 8)
   │                   on success: routing.artifact_payload = healed_payload
   │                   on exhaust: surface broken-with-warning (Dec 4)
   │                   emit SSE forge_done | forge_exhausted status
   │
   ├─ :2049-2065  artifact_data dict assembled
   │
   ├─ :2177       artifact_store.persist_decision (Firestore write)
   │              [Now also writes forge_attempts, forge_healed_at — Dec 2]
   │
   ├─ :2257       save_interaction (single-call invariant preserved)
   │
   ├─ :2346       orchestrator_record_usage(stream_model, ...)
   │              [Existing — debits primary stream model only]
   │
   └─ :2382       yield SSE 'done'
```

The Forge validator cascade is an **inline server-side block** inside the
existing `chunk_type == 'done'` handler. It does not introduce new
endpoints, new background workers, or new async machinery. The streaming
SSE channel stays open throughout — retry status events are yielded
between `data:` frames the client already handles.

### 5.2 Component boundaries

| Component | Location | Responsibility |
|---|---|---|
| **Validator** | `app/services/artifact_validator.py` (NEW) | Pure function: `(payload) → (ok, errors[])`. No I/O beyond subprocess. No state. |
| **Retry-prompt builder** | same module, helper function | Build the synthetic retry prompt (§7). Pure string construction. |
| **Cascade orchestrator** | `app/blueprints/chat_routes.py` (extend `chunk_type=='done'` branch) | Loop, escalate, emit SSE, enforce budget, detect loop. |
| **Cascade constant** | `app/services/chat_engine.py` (NEW: `FORGE_VALIDATION_CASCADE`) | Source of truth for retry order. |
| **Telemetry collector** | inline in cascade orchestrator | Emit metrics per Decision 10. |
| **Persistence extension** | `app/blueprints/chat_routes.py` (extend `save_interaction` call) | Write `forge_attempts`, `forge_healed_at` fields. |
| **Client status renderer** | `app/static/js/startNewChatModule.js` (extend SSE handler) | Render `forge_retry`/`forge_done`/`forge_exhausted` subtype events. |

---

## 6. The validator

### 6.1 Module API

`app/services/artifact_validator.py`:

```python
from dataclasses import dataclass
from typing import Optional

@dataclass(frozen=True)
class ValidationError:
    file_path: str        # e.g. "App.jsx"
    line: int             # 1-indexed
    column: int           # 1-indexed; 0 if unparseable
    message: str          # human-readable, normalized across parsers
    excerpt: str          # 3-line code window centered on error
    parser: str           # "node" | "tsc" — for diagnostics, not display

@dataclass(frozen=True)
class ValidationResult:
    ok: bool
    errors: tuple[ValidationError, ...]   # always sorted by (file_path, line)
    skipped: tuple[str, ...]              # paths skipped (non-validatable language)
    elapsed_ms: int                       # for telemetry

def validate_artifact_payload(payload: dict) -> ValidationResult:
    """Validate every JSX/TSX/JS/TS file in the payload.
    
    Returns ok=True if all validatable files pass OR no validatable files exist.
    Returns ok=True (with logged warning) if validator infrastructure itself
    fails (subprocess error, binary missing, timeout) — graceful degradation
    per Decision 11 and structure-intelligence-v1's fallback discipline.
    """
```

### 6.2 Per-language dispatch

```python
_LANGUAGE_VALIDATORS = {
    'jsx':  '_validate_via_node',
    'js':   '_validate_via_node',
    'tsx':  '_validate_via_tsc',
    'ts':   '_validate_via_tsc',
}
```

Files whose `language` is not in this map are added to `result.skipped` and
do not block the artifact. Examples that are skipped: `css`, `html`,
`python`, `markdown`, `json`, `yaml`. Skip is silent (no warning) — these
languages are correctly out of scope per Decision 11.

### 6.3 Subprocess invocation

**Deviation note (from Decision 1).** Decision 1 prescribed `node --check`
for `.js`/`.jsx`. Vanilla `node --check` does not parse JSX (Node has no
native JSX parser; `--check` only validates the JS subset). Considered
alternatives:

(a) **`@babel/parser` via a thin Node CLI wrapper** *(picked)* —
    preserves Decision 1's spirit (server-side, Node-family subprocess,
    single binary) while handling the JSX gap. Adds `@babel/parser`
    (~250 KB single npm package) as a Node dependency.
(b) **`tsc --noEmit --jsx preserve --allowJs` for all four languages** —
    one binary, one error format, one stderr parser. Architecturally
    cleaner but adds TypeScript as an ops dependency on the deploy host
    (not currently a dep). Slower startup (~500 ms vs ~200 ms for
    Node+Babel).
(c) **Revert to Decision 1 verbatim, accept JSX silently no-ops** —
    rejected; defeats the M1 goal of catching the Netflix-clone-class
    JSX-syntax bug.

**Picked (a).** Tightness of fit (parse-only library scoped to the
validation job) wins over (b)'s consolidation, and (c) is a non-starter.
The deviation is logged in §4 "Decision amendments since spec authoring."
TS/TSX path below is unchanged from Decision 1.

**JS/JSX path: `@babel/parser` via Node CLI wrapper.** The wrapper script
lives in `app/services/forge_validators/check_jsx.cjs`:

```javascript
// check_jsx.cjs — minimal JSX/JS syntax check via @babel/parser
const fs = require('fs');
const parser = require('@babel/parser');
const path = process.argv[2];
const code = fs.readFileSync(path, 'utf8');
try {
    parser.parse(code, {
        sourceType: 'module',
        plugins: ['jsx'],
        errorRecovery: false,
    });
    process.exit(0);
} catch (err) {
    // Babel error: { loc: { line, column }, message, ... }
    process.stderr.write(JSON.stringify({
        kind: 'parse_error',
        line: err.loc?.line ?? 0,
        column: err.loc?.column ?? 0,
        message: err.message ?? 'Unknown parse error',
    }) + '\n');
    process.exit(1);
}
```

Subprocess call:

```python
proc = await asyncio.create_subprocess_exec(
    'node', str(CHECK_JSX_SCRIPT), tmpfile_path,
    stdout=asyncio.subprocess.PIPE,
    stderr=asyncio.subprocess.PIPE,
)
try:
    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=3.0)
except asyncio.TimeoutError:
    proc.kill()
    await proc.wait()
    logger.warning(f"Forge validator timeout on {path}")
    return None  # caller treats as ok=True (Decision 11)
```

**TS/TSX path: `tsc --noEmit --jsx preserve` (unchanged from Decision 1).**

```
tsc --noEmit --jsx preserve --target ESNext --module ESNext \
    --allowJs --skipLibCheck /tmp/forge_<uuid>.<tsx|ts>
```

stderr format (TS6133 etc.):

```
/tmp/forge_a1b2c3.tsx(45,12): error TS1109: Expression expected.
```

Parsed via regex: `^.+?\((\d+),(\d+)\): error TS\d+: (.+)$`.

### 6.4 Timeout, error, and missing-binary handling

| Condition | Behavior |
|---|---|
| Subprocess returns 0 | File passes; no error appended |
| Subprocess returns non-zero with parseable stderr | Append `ValidationError` |
| Subprocess returns non-zero with unparseable stderr | Append `ValidationError(line=0, column=0, message="<raw stderr tail>")` |
| Subprocess timeout (3s) | Log warning, treat file as passed, do NOT fail validation |
| `FileNotFoundError` (node/tsc missing) | Log warning ONCE per process, skip all files of that language going forward, return overall `ok=True` |
| `validate_artifact_payload` itself raises | Caller catches, logs warning, continues as if `ok=True` (graceful degradation) |

The "treat as passed on infrastructure failure" choice is intentional —
the user must never get a stuck stream because the validator is broken.
The cost: an artifact with broken JSX could slip through if the validator
binary is missing. That's an operational concern (alert on the warning
log) and acceptable for M1.

### 6.5 Error normalization

Both Babel and tsc report `(line, column, message)` per parse error. Babel
columns are 0-indexed, tsc columns are 1-indexed. Normalize all columns to
1-indexed in `ValidationError`. Normalize all `line` to 1-indexed (both
parsers already are).

`message` is taken verbatim from the parser. `excerpt` is computed from the
file content: 3 lines centered on `line`, with the error column flagged
inline (no syntax highlighting in the excerpt itself; that's a UI concern).

---

## 7. The retry loop

### 7.1 Cascade pseudo-code

Inserted in `chat_routes.py` between line 1751 (existing cascade #1 end)
and line 2042 (artifact_data assembly start). Wrapped in a function so the
existing cascade #1 can call it on its successful exit too:

```python
# === Forge M1 — validation loop ===
forge_metrics = {
    'forge_validation_attempted': False,
    'forge_validation_passed_first_try': True,
    'forge_healed_at_iter': None,        # 1-4, or None if exhausted/skipped
    'forge_total_retry_tokens': 0,
    'forge_time_to_heal_ms': None,
    'forge_budget_exhausted': False,
    'forge_loop_detected_skip': False,
}

if routing and routing.artifact_payload:
    # Skip validator entirely if payload has no validatable files
    if _has_validatable_files(routing.artifact_payload):
        forge_metrics['forge_validation_attempted'] = True
        t_start = time.monotonic()
        result = validate_artifact_payload(routing.artifact_payload)
        
        if not result.ok:
            forge_metrics['forge_validation_passed_first_try'] = False
            previous_error_signature = _signature(result.errors)
            previous_skip_count = 0
            cascade_iter = 0
            
            while cascade_iter < len(FORGE_VALIDATION_CASCADE):
                # Loop-detect: skip 2 tiers if errors are byte-identical to prior iter
                effective_iter = cascade_iter + previous_skip_count
                if effective_iter >= len(FORGE_VALIDATION_CASCADE):
                    break
                retry_model = FORGE_VALIDATION_CASCADE[effective_iter]
                attempt_num = cascade_iter + 1  # 1-indexed for user-visible
                
                # Token budget check
                if forge_metrics['forge_total_retry_tokens'] >= FORGE_RETRY_TOKEN_BUDGET:
                    forge_metrics['forge_budget_exhausted'] = True
                    logger.warning(f"🔥 Forge budget exhausted "
                                   f"({forge_metrics['forge_total_retry_tokens']} tokens), stopping cascade")
                    break
                
                # Emit retry status to client
                yield f"data: {json.dumps({
                    'type': 'status',
                    'subtype': 'forge_retry',
                    'attempt': attempt_num,
                    'model': retry_model,
                    'reason': _short_reason(result.errors),
                })}\n\n"
                
                # Build prompt (§7.2) and call model
                retry_prompt = build_validation_retry_prompt(
                    payload=routing.artifact_payload,
                    errors=result.errors,
                    original_user_prompt=prompt,
                )
                try:
                    retry_response, _, _ = get_ai_response(retry_prompt, retry_model)
                    in_tokens, out_tokens = _estimate_tokens(retry_prompt, retry_response)
                    orchestrator_record_usage(retry_model, in_tokens, out_tokens)  # Dec 7
                    forge_metrics['forge_total_retry_tokens'] += (in_tokens + out_tokens)
                except Exception as e:
                    logger.warning(f"🔥 Forge retry {retry_model} error: {e}")
                    cascade_iter += 1
                    continue
                
                # Parse + re-validate
                retry_parsed = ActionParser.parse(retry_response)
                if not retry_parsed.artifact_payload:
                    logger.info(f"🔥 Forge retry {retry_model} produced no artifact payload")
                    cascade_iter += 1
                    continue
                
                new_result = validate_artifact_payload(retry_parsed.artifact_payload)
                if new_result.ok:
                    routing.artifact_payload = retry_parsed.artifact_payload
                    forge_metrics['forge_healed_at_iter'] = attempt_num
                    forge_metrics['forge_time_to_heal_ms'] = int((time.monotonic() - t_start) * 1000)
                    yield f"data: {json.dumps({
                        'type': 'status',
                        'subtype': 'forge_done',
                        'healed_at': attempt_num,
                    })}\n\n"
                    break
                
                # Loop-detect: same errors as last iter? Skip 2 tiers (Dec 9)
                new_signature = _signature(new_result.errors)
                if new_signature == previous_error_signature:
                    forge_metrics['forge_loop_detected_skip'] = True
                    previous_skip_count += 2
                    logger.info(f"🔥 Forge loop-detect: same errors as prior iter, skipping 2 tiers")
                
                previous_error_signature = new_signature
                result = new_result
                cascade_iter += 1
            
            # Cascade exhausted without healing
            if forge_metrics['forge_healed_at_iter'] is None:
                yield f"data: {json.dumps({
                    'type': 'status',
                    'subtype': 'forge_exhausted',
                    'attempts': cascade_iter,
                    'errors': [_error_to_json(e) for e in result.errors],
                })}\n\n"
                # Surface broken-with-warning per Dec 4 — artifact still flows
                # to artifact_data assembly, but with a warning flag.
                routing.artifact_payload['_forge_validation_failed'] = True
                routing.artifact_payload['_forge_errors'] = [
                    _error_to_json(e) for e in result.errors
                ]

# === END Forge M1 ===

# Existing artifact_data assembly continues at :2049...
```

### 7.2 Notes on the cascade pseudo-code

- **`FORGE_RETRY_TOKEN_BUDGET = 80_000`** — module-level constant in
  `chat_engine.py` next to `FORGE_VALIDATION_CASCADE`.
- **`_has_validatable_files(payload)`** returns `True` iff at least one
  file's `language` is in `_LANGUAGE_VALIDATORS`. Cheap O(n) check; avoids
  spawning subprocess for SVG/HTML/CSS-only artifacts.
- **`_signature(errors)`** returns a hashable tuple:
  `tuple(sorted((e.file_path, e.line, e.message) for e in errors))`.
- **`_short_reason(errors)`** picks the first error and formats:
  `"validator: <kind> @ <file>:<line>"`. For the user-visible status block.
- **`_estimate_tokens(prompt, response)`** delegates to existing
  `orchestrator/accountant.py` token estimation utilities.
- The `previous_skip_count` mechanism implements Decision 9: when iter N
  produces identical errors to iter N-1, the next iteration takes the
  cascade entry at `cascade_iter + 2` (skipping two tiers).
- Even on exhaustion, the artifact still flows downstream — `routing.artifact_payload`
  remains populated and `persist_decision` runs as normal. The two
  `_forge_validation_failed` / `_forge_errors` keys are picked up by the
  panel renderer to draw the warning ribbon (Decision 4).

---

## 8. Retry-prompt construction

### 8.1 Function signature

In `app/services/artifact_validator.py`:

```python
def build_validation_retry_prompt(
    payload: dict,
    errors: tuple[ValidationError, ...],
    original_user_prompt: str,
) -> str:
    """Build the synthetic retry prompt for the cascade.
    
    Modeled on the patch-regen pattern at chat_routes.py:1956-1976.
    """
```

### 8.2 Exact prompt format

```
Your previous response produced an artifact that failed syntax validation.

VALIDATOR ERRORS:
1. App.jsx:45:12 — Binding invalid left-hand side in function parameter list
   Excerpt:
       43 |     };
       44 |     window.addEventListener('scroll', handleScroll);
   >>> 45 |     return (<></>) => window.removeEventListener('scroll', handleScroll);
       46 |   }, []);
       47 | 

(N more errors omitted if applicable)

YOUR PREVIOUS ARTIFACT (unchanged):

```artifact-meta
{"title":"...","type":"project","primaryLanguage":"jsx"}
```

```artifact-file:App.jsx:jsx
import React, { useState } from 'react';
[...full file content...]
```

```artifact-file:components/Hero.jsx:jsx
[...full file content...]
```

USER REQUEST: {original_user_prompt}

Regenerate the COMPLETE artifact with the syntax error fixed. Use
ACTION: create_artifact and the fenced format. Include ALL files.
```

### 8.3 Construction rules

- All errors are listed, not just the first. Models often introduce
  fix-the-symptom-create-a-new-bug patterns; showing all errors helps
  avoid regression.
- The full artifact is included — every file, fenced format, no
  JSON-escaped content (matches the existing fenced-artifact contract,
  which the model is already trained on).
- `original_user_prompt` is the *raw user request*, not the augmented
  ContextBuilder prompt. The model has already seen the augmented context
  in the original turn; re-feeding it would balloon retry tokens.
- Excerpts are 3 lines centered on the error, with the error line
  prefixed `>>>` and others prefixed by line number. Plain text, no
  syntax highlighting.
- **Token budget pressure mitigation:** for very large artifacts (>25k
  tokens of file content), include only the *failed* files in the
  artifact section, not all files. The retry prompt header notes:
  `"Files not shown: <list>. Their content is preserved from your
  previous response — do not change them."`
- The "ACTION: create_artifact" instruction is explicit even when the
  original turn used `patch_artifact` — at retry time we always want a
  full regen because the patch-apply path has its own existing cascade
  (#2/#3) and Forge does not interleave with it.

### 8.4 Why this beats simpler alternatives

- **vs "just send same prompt to next model":** existing cascade #1 does
  this and demonstrably fails for syntax bugs (the Netflix bug arose at
  18:23:03 from cascade #1 retry — cascade just got the same broken JSX
  with a new model that wasn't told what was wrong).
- **vs "send only the broken file":** loses cross-file context. A bug in
  `App.jsx` may be caused by a wrong import from `Hero.jsx`. Multi-file
  errors are common in React projects.
- **vs "send full chat history including user turn + assistant turn":**
  ballooning. Conversation history is already in the model's KV cache from
  the original turn; re-paying for it on every retry is wasteful.

---

## 9. Transcript block (SSE events)

### 9.1 Event shapes

The cascade emits three event subtypes, all under the existing `type:
status` umbrella the client already routes:

**`forge_retry`** — emitted at the start of each retry attempt:
```json
{
  "type": "status",
  "subtype": "forge_retry",
  "attempt": 1,                                    // 1-indexed
  "model": "deepseek-v4-pro",                      // resolved cascade entry
  "reason": "validator: jsx-parse @ App.jsx:45"    // short user-readable
}
```

**`forge_done`** — emitted on successful heal:
```json
{
  "type": "status",
  "subtype": "forge_done",
  "healed_at": 2                                   // iter number (1-4)
}
```

**`forge_exhausted`** — emitted when all 4 (or fewer if budget/skip) iterations failed:
```json
{
  "type": "status",
  "subtype": "forge_exhausted",
  "attempts": 4,                                   // total iters performed
  "errors": [                                      // for warning ribbon
    {"file_path": "App.jsx", "line": 45, "column": 12, "message": "..."}
  ]
}
```

### 9.2 Client rendering

In `app/static/js/startNewChatModule.js`, the SSE handler routes
`type: 'status'` events. Add a switch on `subtype`:

```javascript
case 'forge_retry':
    appendForgeRetryBlock(data.attempt, data.model, data.reason);
    break;
case 'forge_done':
    appendForgeHealedBlock(data.healed_at);
    break;
case 'forge_exhausted':
    appendForgeExhaustedRibbon(data.attempts, data.errors);
    break;
```

Visual treatment (high-level — exact CSS in implementation):

- `forge_retry` block: small inline card under the user's message, with a
  spinning-icon, the model name, and the reason. Successive retries append
  new cards.
- `forge_done` block: compact "✓ Healed after N attempts" line, replaces
  the spinner on the last retry card.
- `forge_exhausted`: a red ribbon attached to the artifact panel header
  (NOT in the chat stream), with the validator errors expanded inline
  below it. Per Decision 4 the broken artifact still renders.

---

## 10. Persistence & replay

### 10.1 Schema (Firestore)

Firestore is schema-less; "migration" here means: writes start emitting two
new fields, reads gracefully handle their absence on legacy documents.

The chat message record (saved at `:2257` via `save_interaction`) gains:

```json
{
  "...existing fields...": "...",
  "forge_attempts":   integer | null,    // null if validator was skipped
                                          // (no JSX/TSX/JS/TS files)
                                          // 0 if first-try-pass
                                          // 1-4 if retries occurred
  "forge_healed_at":  integer | null     // null if exhausted OR skipped
                                          // 1-4 if healed at that iter
}
```

Semantics:

| State | `forge_attempts` | `forge_healed_at` |
|---|---|---|
| No validatable files (e.g. SVG) | `null` | `null` |
| Validated, passed first try | `0` | `null` |
| Validated, healed at iter 1 | `1` | `1` |
| Validated, healed at iter 3 | `3` | `3` |
| Validated, exhausted (4 iters) | `4` | `null` |
| Validated, budget-stopped at 2 | `2` | `null` |

### 10.2 Write path

In the existing `save_interaction(...)` call site (`:2257`), pass the two
new fields. The function itself needs a small extension to accept and
forward them to the Firestore document update.

### 10.3 Replay path

On history replay (`startNewChatModule.js`'s `renderMessage` or its
StreamRenderer-replay successor per `replay-convergence-design.md`),
read `forge_attempts` and `forge_healed_at` from the message record. If
`forge_attempts > 0`, render a small static breadcrumb next to the
message timestamp:

- `forge_healed_at != null`: `"✓ Healed after {forge_healed_at} attempt(s)"`
- `forge_healed_at == null && forge_attempts > 0`: `"⚠ Validation failed (4 attempts)"`

No per-iteration cards reconstructed. The live `forge_retry` cards are
DOM-only and not persisted.

### 10.4 Backward compatibility

Legacy messages have neither field. Reads check `if forge_attempts is None`
first → render no breadcrumb (current behavior). No data migration needed.

---

## 11. Edge cases

| # | Case | Behavior |
|---|---|---|
| E1 | Stream cancelled mid-flight (existing abort path) | Existing abort handling propagates; the cascade loop's `yield` raises `GeneratorExit`, the loop unwinds, no persistence happens. **No new code needed** — verify in implementation that the `try`/`except` around `get_ai_response` does not swallow `GeneratorExit`. |
| E2 | Validator subprocess errors (binary missing, OS-level fail) | Logged warning, treated as ok=True, artifact flows as if validation passed. Graceful degradation. |
| E3 | Validator times out (3s/file) | Same as E2. Per-file timeout; doesn't cascade-fail the whole artifact. |
| E4 | All 4 iterations fail | Per Decision 4: emit `forge_exhausted`, set `_forge_validation_failed=True` on the payload, render artifact with red warning ribbon + inline errors. |
| E5 | Multi-file artifact, only one file fails | Validator reports the one failure. Retry prompt includes ALL files (model needs cross-file context per §8.4). On heal, all files are re-saved. |
| E6 | Model returns identical errors twice | Loop-detection fires (Decision 9), `previous_skip_count += 2`, next iteration jumps two cascade tiers ahead. Logged as `forge_loop_detected_skip=true`. |
| E7 | Retry token budget (80k) exceeded mid-cascade | Loop breaks immediately. `forge_budget_exhausted=true`. Treated like exhaustion: artifact renders with red ribbon, current errors surfaced. |
| E8 | Non-validatable artifact (no JSX/TSX/JS/TS files; pure HTML/CSS/SVG/Python) | `_has_validatable_files` returns False, validator never runs. `forge_attempts=null`, `forge_healed_at=null`. Zero overhead. |
| E9 | Artifact has zero files | Same as E8 — validator skipped. |
| E10 | User regenerates the same broken artifact in next turn | Each turn is a fresh cascade. No cross-turn state. The previous turn's `forge_attempts` does not influence the new turn. |
| E11 | Cascade #1 (existing JSON-malform retry) succeeds; Forge runs after | Sequential pipeline: cascade #1 produces a valid `artifact_payload`, Forge then validates it. Both can fire in one turn. |
| E12 | Patch-apply cascade (#2/#3) succeeds; should Forge also run? | **Yes** — patches can introduce syntax bugs just like full regens. Forge runs on the post-patch `artifact_payload` regardless of how it got built. |
| E13 | Stream model is one of the cascade models AND fails | Cascade still iterates from index 0. We do not skip the same model — it may have failed for a transient reason. Loop detection (E6) handles the case where it consistently produces the same error. |
| E14 | Retry prompt exceeds the model's context window | Caught by `get_ai_response` exception path, treated as a failed cascade iteration, loop continues. The §8.3 large-artifact mitigation should rarely allow this in practice. |
| E15 | Validator finds errors only in `_forge_validation_failed`-flagged retry artifacts | Defensive: never re-validate an artifact already flagged failed. Prevents double-Forge on legacy paths if they ever feed into the hook. |

---

## 12. Telemetry contract

Per Decision 10, every artifact-bearing turn emits these metrics. Sink:
existing `logger.info` with structured fields (parseable by the metrics
extraction pipeline). Field names are exact:

| Field | Type | Semantics |
|---|---|---|
| `forge_validation_attempted` | bool | True iff `_has_validatable_files` returned True. |
| `forge_validation_passed_first_try` | bool | True iff first `validate_artifact_payload` call returned ok. False if any retry was needed. Only meaningful when `forge_validation_attempted=true`. |
| `forge_healed_at_iter` | int \| null | 1-4 if healed; null if exhausted, budget-stopped, or skipped. |
| `forge_total_retry_tokens` | int | Sum of input+output tokens for all cascade `get_ai_response` calls within this turn. 0 if no retries. |
| `forge_time_to_heal_ms` | int \| null | ms from first-validate-fail to successful heal. null if not healed. |
| `forge_budget_exhausted` | bool | True iff cascade stopped due to 80k retry budget. |
| `forge_loop_detected_skip` | bool | True iff Decision 9's tier-skip mechanism fired at least once during the cascade. |

Log line shape (single-line key=value for grep / metrics-extractor):

```
INFO 🔥 forge: forge_validation_attempted=true forge_validation_passed_first_try=false forge_healed_at_iter=2 forge_total_retry_tokens=32487 forge_time_to_heal_ms=8412 forge_budget_exhausted=false forge_loop_detected_skip=false
```

Emit once per turn, after the cascade resolves (success, exhaustion, or
skipped), at the existing accountant log site (`:2346` neighborhood).

---

## 13. Prerequisite work in M1 PR

### 13.1 V4 swap merge (hard prerequisite)

`feat/deepseek-v4-swap` (commit `537050c`, PR open) **must merge first**.
Decision 3's hardcoded `["deepseek-v4-flash", ...]` cascade depends on
those IDs being in `MODEL_CATALOG`.

Verification: after merge, `from app.model_catalog import MODEL_CATALOG;
assert 'deepseek-v4-flash' in MODEL_CATALOG`. The M1 PR should include a
guard at module load time:

```python
# app/services/chat_engine.py — immediately after FORGE_VALIDATION_CASCADE
for _m in FORGE_VALIDATION_CASCADE:
    if _m not in MODEL_CATALOG and _m not in MODEL_ALIASES:
        raise RuntimeError(
            f"FORGE_VALIDATION_CASCADE references unknown model {_m!r}. "
            f"Did feat/deepseek-v4-swap merge? See "
            f"docs/superpowers/specs/2026-04-29-forge-m1-validation-loop.md "
            f"§3 / §13."
        )
```

This fails at app startup (gunicorn worker spawn) rather than at first
artifact request — fast feedback during deploy.

### 13.2 Accountant fix (Decision 7)

Wrap every existing `get_ai_response` call in cascades #1, #2, #3 with
`orchestrator_record_usage(<retry_model>, in_tokens, out_tokens)`. Token
estimation reuses the same estimator the §7 cascade pseudo-code uses.

Files:
- `app/blueprints/chat_routes.py` — three call sites at `:1734`, `:1806`,
  `:1982` (line numbers approximate; verify against current main).

This is a non-feature change shipped under M1 PR. Without it, M1's amplified
retry traffic silently drains the daily token budget.

### 13.3 Schema fields (Decision 2)

Extend `save_interaction` (in `app/services/chat_engine.py` per the
investigation doc, around `:1292-1450`) to accept and forward
`forge_attempts` and `forge_healed_at` fields. No schema migration script
needed (Firestore-schema-less).

### 13.4 No prerequisite outside the M1 PR

Everything M1 needs ships in this PR. The V4 swap is a separate PR, but
its merge is upstream of M1 PR opening. M1 PR base = post-V4-merge `main`.

---

## 14. File-by-file change manifest

### NEW

| Path | Purpose | Approx LOC |
|---|---|---|
| `app/services/artifact_validator.py` | Validator + `build_validation_retry_prompt` + helpers (`_has_validatable_files`, `_signature`, `_short_reason`, `_error_to_json`) | ~250 |
| `app/services/forge_validators/check_jsx.cjs` | Babel-based Node CLI wrapper for JSX/JS syntax check | ~25 |
| `app/services/forge_validators/__init__.py` | Empty package marker | 0 |
| `tests/test_artifact_validator.py` | Unit tests for the validator (clean React, Netflix bug, multi-file partial-fail, missing import, validator-timeout simulation, missing-binary simulation) | ~200 |
| `tests/fixtures/artifacts/netflix_broken_jsx.json` | The Netflix bug fixture (line 45 `(<></>) =>` pattern) | ~30 |
| `tests/fixtures/artifacts/clean_react.json` | Validates clean | ~20 |
| `tests/fixtures/artifacts/multi_file_partial_fail.json` | 3 files, 1 has a syntax error | ~50 |
| `tests/fixtures/artifacts/svg_only.json` | SVG, no validatable files (skip path) | ~10 |

### TOUCHED

| Path | Change | Approx LOC |
|---|---|---|
| `app/blueprints/chat_routes.py` | Insert validator cascade between `:1751` and `:2042` (§6/§7/§8); wrap existing cascades #1/#2/#3 with `orchestrator_record_usage` (§13.2); pass `forge_attempts`/`forge_healed_at` to `save_interaction` (§13.3) | ~120 added |
| `app/services/chat_engine.py` | Add `FORGE_VALIDATION_CASCADE` constant; add `FORGE_RETRY_TOKEN_BUDGET = 80_000`; extend `save_interaction` signature for new fields; add startup guard (§13.1) | ~25 added |
| `app/static/js/startNewChatModule.js` | SSE handler `type:'status'` switch additions for `forge_retry` / `forge_done` / `forge_exhausted`; small renderers `appendForgeRetryBlock` / `appendForgeHealedBlock` / `appendForgeExhaustedRibbon`; replay-side breadcrumb reconstruction from `forge_attempts`/`forge_healed_at` | ~100 added |
| `app/static/js/artifactPanel.js` | Read `_forge_validation_failed` / `_forge_errors` flags from incoming artifact; render red warning ribbon (Decision 4) | ~40 added |
| `app/static/css/styles.css` (or appropriate stylesheet) | `.forge-retry-card`, `.forge-healed-line`, `.forge-warning-ribbon` styles | ~50 added |

### DOCS

| Path | Change |
|---|---|
| `README.md` | Add a one-line bullet under features: "Self-healing artifact validation (Forge M1) — JSX/TSX syntax errors auto-retry with model cascade." |
| `docs/superpowers/specs/2026-04-29-forge-m1-investigation.md` | (already merged via separate PR) |
| `docs/superpowers/notes/forge-design-summary.md` | (committed alongside this spec) |

### NOT TOUCHED (deliberately)

- `app/context/artifact_state_tracker.py` — Q3 of investigation confirmed
  no changes needed; tracker handles failed intermediates correctly.
- `app/context/action_parser.py` — used as-is.
- `app/artifacts.py` — used as-is.
- `app/orchestrator/*.py` — only the existing `record_usage` is called
  more often, no signature changes.
- `app/static/js/codeWindow.js` — live-patch streaming path untouched.

### Deps added

- `@babel/parser` (Node) — required by `check_jsx.cjs`. Add to
  `package.json` under `dependencies`. Version: ^7.x latest stable.

No Python packages added. All Python deps already in `requirements.txt`
(asyncio is stdlib).

---

## Acceptance criteria

The M1 PR is ready to merge when **all** of the following pass:

1. **Netflix bug heals.** Synthetic input artifact with the
   `return (<></>) =>` pattern on line 45 of `App.jsx` heals on iter 1 or 2
   without user action. Verified by an integration test that mocks the
   model cascade to return the Netflix bug on iter 1 and a fixed version on
   iter 2.
2. **Clean artifact passes first-try.** Synthetic clean React artifact
   triggers no retry. `forge_attempts=0`, `forge_healed_at=null`,
   `forge_total_retry_tokens=0`.
3. **SVG / pure-CSS artifact bypasses validator.** Synthetic SVG-only
   artifact triggers no subprocess spawn. `forge_attempts=null`,
   `forge_healed_at=null`. Verified by spy on subprocess invocation.
4. **Impossible artifact exhausts cascade.** Mock-cascade returns
   syntactically broken artifact for all 4 iters. `forge_exhausted` event
   emitted, red ribbon renders in panel, validator errors expanded inline.
   `forge_attempts=4`, `forge_healed_at=null`.
5. **Identical-error-twice triggers tier skip.** Mock-cascade returns
   identical errors on iter 1 and 2. `forge_loop_detected_skip=true`,
   iter 3 entry calls cascade index 3 (gpt-4.1), not index 2 (kimi).
6. **80k retry budget triggers early stop.** Mock-cascade with very large
   artifact + 4 retries each consuming ~25k tokens. Cascade stops after
   iter 3 (cumulative ~75k), `forge_budget_exhausted=true`.
7. **Validator timeout doesn't block.** Mock validator subprocess sleeps
   5s (>3s timeout). Logged warning, artifact renders, no cascade kicked
   off. `forge_validation_attempted=true`,
   `forge_validation_passed_first_try=true` (treated as pass on timeout).
8. **All cascade retries debited from accountant.** Spy on
   `orchestrator_record_usage`: assert it's called once per cascade
   iteration (not just once for the stream model). Verified for cascades
   #1/#2/#3 AND the new Forge cascade.
9. **Reload after healed turn shows breadcrumb.** Save a healed message,
   reload page, assert "✓ Healed after 2 attempts" string present.
10. **All `forge_*` metrics emitted.** For every turn that produced an
    artifact (regardless of validator outcome), the seven fields appear in
    one structured log line. Verified by parsing test logs.
11. **No regression in existing artifact flow.** The existing live-patch
    streaming, patch-apply cascade #2/#3, and JSON-malform cascade #1 all
    continue to work — the M1 changes are additive, not replacing.

The implementation plan (separate doc, `superpowers:writing-plans`) decomposes
these 11 acceptance criteria into ordered TDD tasks.

---

## Out-of-scope reminders for reviewers

If the PR diff includes any of the below, that's scope creep and should
be pushed back:

- New ACTION verbs in `action_parser.py`
- Changes to `artifactPanel.js` beyond the warning ribbon (Decision 4)
- Any new SSE event `type` (only `subtype` extensions allowed)
- Plan / Snapshot / Tools UI scaffolding for M2 — even commented out
- Cancel button machinery (Decision 5 deferred)
- Python / HTML / CSS validators beyond the four Decision 11 languages

---

## Stop point

Spec written. **No implementation in the M1 PR until this spec is reviewed
and approved by the human reviewer.** The next deliverable is the
implementation plan via `superpowers:writing-plans`, which decomposes
§14 manifest + §11 acceptance into ordered TDD tasks. **Only after both
spec AND plan are approved does any code get written.**
