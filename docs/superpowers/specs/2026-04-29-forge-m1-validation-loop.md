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
    the eight `forge_*` metrics named in §12 (was seven; expanded by
    Amendment 8 to include `forge_output_estimate_actual_ratio` for
    pre-flight calibration).
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

### Amendment 8 — 2026-04-29 — pre-flight calibration telemetry (§7.1, §7.2, §12, §10.1, AC#15)

**Original (review-of-spec, pre-second-pass):** §7.1 used a magic 1.2x
output multiplier in pre-flight estimation
(`output_estimate = _artifact_content_tokens(payload) * 1.2`) with no
empirical validation, no calibration plan, and no logging of the
projected-vs-actual delta.

**Amended to:** add an 8th telemetry field
`forge_output_estimate_actual_ratio: float | null` (mean of per-iter
`actual_out / projected_out` ratios across the turn's cascade calls,
null if no cascade fired). Also emit a separate per-iter
`🔥 forge_calibration:` log line on every successful cascade call with
`model / projected_out / actual_out / ratio`. After ~100 production
retries land, aggregate per-iter logs and recalibrate the 1.2x.
Tracked by a follow-up issue (filed post-commit).

**Rationale:** "magic constant with no calibration" is technical debt
that compounds. Without empirical data, we won't know whether 1.2x is
right, too tight (premature pre-flight blocks), or too loose (overshoot
budget). Cheap-to-add observability now saves days of "why is the
budget firing too aggressively?" later. New AC #15 verifies both per-iter
and per-turn telemetry are emitted.

**Impact on other decisions:** Decision 10 expanded — was 7 telemetry
fields, now 8. Locked-decision text in §3 updated to reflect.

---

### Amendment 7 — 2026-04-29 — `forge_attempts` added to forge_metrics + schema corrected (§7.1, §10.1, §14)

**Original (review-of-spec, pre-second-pass):** §10.1 schema listed
`forge_attempts` as a persisted envelope field, but the §7.1
`forge_metrics` dict never initialized or incremented it. The schema
also said max value was `1-4` ("validated, exhausted (4 iters)"),
which was wrong post-Amendment 4 (`start_idx` advances past
`stream_model`, so max iters depends on stream-model position).

**Amended to:**
- Add `'forge_attempts': 0` to `forge_metrics` initialization in §7.1.
- Increment `forge_metrics['forge_attempts'] += 1` exactly once per
  *successful* `get_ai_response` cascade call (not on the
  catch-and-continue exception path). Excludes the original stream
  attempt by definition.
- Update §10.1 with stream-model-aware max-`forge_attempts` table:
  Flash-stream → 3, Pro-stream → 2, Kimi-stream → 1, GPT-4.1-stream →
  0 (E16). Loop-detect (Decision 9) further reduces actual count per
  turn.

**Rationale:** schema/code drift is a guaranteed implementation-time
bug source. Either the schema or the code had to be wrong; chose to
align both around the "count of cascade calls" semantics
(implementation-friendly, telemetry-useful, separable from
`forge_healed_at_iter`).

**Impact on other decisions:** Decision 2 (persistence schema)
expanded — was 4 envelope fields, now 5 (with
`forge_output_estimate_actual_ratio` from Amendment 8 it's 5; the
fifth field was always `forge_attempts` per Decision 2's original
intent — this amendment just makes the implementation match).

---

### Amendment 6 — 2026-04-29 — worked-example correction in §7.1 (Decision 9 implementation detail; no behavior change)

**Original (review-of-spec, pre-second-pass):** the §7.1 worked example
demonstrated loop-detect by showing stream-Flash + Pro + Kimi (where
Pro and Kimi produced identical errors → skip → exit-out-of-bounds).
This contradicted Acceptance Criterion #6, which described stream-Flash
+ Pro = identical errors → skip Kimi → call GPT-4.1.

**Code reality:** `previous_error_signature = _signature(result.errors)`
is set BEFORE the loop entry, where `result` is the stream attempt's
errors. So the loop-detect baseline IS the stream errors; iter 1's
errors are compared against the stream's, not against iter 2's. AC #6
matched the code; the worked example didn't.

**Amended to:** rewrite the worked example to match the actual baseline
behavior. The example now shows stream-Flash fails with X →
`previous_error_signature = X` set pre-loop → cascade iter 1 (Pro)
returns X → loop-detect fires immediately → `cascade_iter += 2 = 3` →
GPT-4.1 called. Both heal and fail branches shown. The example also
includes a "why baseline = stream errors" rationale paragraph.

**Rationale:** internal inconsistency between code, worked example,
and acceptance test = guaranteed implementation-time confusion. The
code is the source of truth; example and AC must match it. AC #6 was
already correct; this amendment fixes only the example.

**Impact on other decisions:** none. No behavior change. Pure
documentation/clarity fix to make the implementation contract
unambiguous.

---

### Amendment 5 — 2026-04-29 — broken-with-warning signaling refactor (§7.1, §10.1, §14)

**Original (review-of-spec, pre-feedback):** on cascade exhaustion, the
spec mutated `routing.artifact_payload` with two underscore-prefixed
keys (`_forge_validation_failed`, `_forge_errors`) and let the panel
renderer / persist path read them off the payload directly.

**Amended to:** validation status travels in the **message envelope**
alongside the artifact, not inside it. Four new envelope fields persist
in the message record (`forge_attempts`, `forge_healed_at`,
`forge_validation_failed`, `forge_errors`), emitted as part of the SSE
done payload. The panel renderer reads them from the envelope; the
artifact_payload is never mutated.

**Rationale:** other consumers of `artifact_payload` (Firestore persist,
ContextBuilder on next turn, replay convergence) shouldn't have to know
about Forge meta-keys. Underscore-prefix is a Python convention for
"private" — it doesn't carry that meaning over the wire to JSON
consumers. Mutating the payload would have round-tripped the meta-keys
to Firestore on the artifact document forever, even after a healing
regen on the next turn. Envelope-not-payload separation keeps the
artifact data shape clean and the validation status owned by the
message, where it logically belongs.

**Impact on other decisions:** Decision 2 (persistence schema)
unchanged in intent but expanded — was 2 fields, now 4. Schema table in
§10.1 updated. Decision 4 (broken-with-warning) unchanged in intent,
implementation moved to envelope.

---

### Amendment 4 — 2026-04-29 — stream_model cascade start position (§7.1, §11.E13, §11.E16)

**Original (review-of-spec, pre-feedback):** cascade always iterated from
index 0 — meaning if `stream_model` was `deepseek-v4-flash` and the
stream attempt failed, iter 1 of the cascade would call `deepseek-v4-flash`
*again*. Spec relied on loop-detect (Decision 9) to handle the
"identical errors twice" case, but loop-detect requires byte-identical
errors — slightly-different errors on the second Flash attempt would
slip through and burn iter 1 for no value.

**Amended to:** `start_idx = FORGE_VALIDATION_CASCADE.index(stream_model) + 1`
when `stream_model` is in the cascade. The stream attempt counts as
iter 0; cascade starts at the next tier up. New edge case **E16**: if
`stream_model` is the LAST cascade entry (e.g. user explicitly picked
`gpt-4.1`), `start_idx = len(cascade)` → no retries fire → artifact
surfaces broken-with-warning per Decision 4.

**Rationale:** user's review caught that Decision 9's loop-detect was
being asked to do work that belongs at cascade-startup. Skipping past
`stream_model` is a structural fix; loop-detect remains in place for
mid-cascade identical-error-twice patterns.

**Impact on other decisions:** Decision 9 unchanged. Decision 4
unchanged. New acceptance criterion: when `stream_model` is V4-Flash
and validation fails, iter 1 uses V4-Pro, not V4-Flash again.

---

### Amendment 3 — 2026-04-29 — token budget pre-flight check (§7.1, §7.2)

**Original (review-of-spec, pre-feedback):** budget check was post-call
accumulate then check-before-next-call. Effective semantics: "soft cap
with up to one retry of overshoot acceptable" (a single retry could
push the total well above 80k as long as the prior cumulative was below
80k).

**Amended to:** pre-flight estimate of the retry prompt + projected
output tokens, summed against the running total, gates each retry
**before** calling the model. If projected total exceeds budget, break
the loop without making the call. Hard cap, no overshoot.

**Rationale:** Decision 8 framed the budget as a "hard cap." The
implementation didn't match the framing. Future-debugging discipline
prefers "the spec says 80k and we mean 80k." Pre-flight estimation
re-uses existing `orchestrator/accountant.py` token utilities; the
output estimate is conservative (~1.2x the broken artifact's content
size, since the model regenerates roughly the same shape).

**Impact on other decisions:** Decision 8 unchanged in intent,
implementation tightened. Decision 7 (accountant wrap) unchanged —
post-call recording still happens, plus the new pre-flight estimate.

---

### Amendment 2 — 2026-04-29 — cascade increment discipline (§7.1, §7.2)

**Original (review-of-spec, pre-feedback):** `previous_skip_count`
parallel counter advanced by 2 on loop-detect match, while
`cascade_iter` advanced by 1 unconditionally at the end of the loop.
Two increment sites that combined incorrectly: on Flash + Pro = identical
errors, the math went `cascade_iter=2, skip=2 → effective=4 → break`,
missing GPT-4.1 entirely (the strongest model in the cascade and the
one most likely to break a same-family failure pattern).

**Amended to:** drop `previous_skip_count` entirely. Single increment
site discipline — on loop-detect match: `cascade_iter += 2; continue`.
Otherwise: `cascade_iter += 1` at the bottom of the loop. The `continue`
is load-bearing — it bypasses the unconditional `+= 1` that would
otherwise double-step. A worked-example walkthrough is now embedded as
a comment in the §7.1 pseudo-code so the iteration sequence on a
loop-detect case is explicit and reviewable.

**Rationale:** original logic was an off-by-one with invisible exposure
(no worked example to validate against). The bug would have caused the
cascade to terminate one tier short on every loop-detect-firing case,
producing user-facing exhaustion when GPT-4.1 might have healed the
artifact.

**Impact on other decisions:** Decision 9 unchanged in intent,
implementation made correct. New acceptance criterion: on Flash + Pro
producing identical errors, cascade reaches GPT-4.1 (skipping Kimi).

---

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
lives in `app/services/forge_validators/check_jsx.cjs` and reads source
**from stdin** (avoids tmpfile creation, race conditions, and cleanup):

```javascript
// check_jsx.cjs — minimal JSX/JS syntax check via @babel/parser.
// Reads source from stdin (avoids tmpfile race + cleanup).
const parser = require('@babel/parser');
const chunks = [];
process.stdin.on('data', (chunk) => chunks.push(chunk));
process.stdin.on('end', () => {
    const code = Buffer.concat(chunks).toString('utf8');
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
});
```

Subprocess call (content fed via stdin, no tmpfile):

```python
proc = await asyncio.create_subprocess_exec(
    'node', str(CHECK_JSX_SCRIPT),
    stdin=asyncio.subprocess.PIPE,
    stdout=asyncio.subprocess.PIPE,
    stderr=asyncio.subprocess.PIPE,
)
try:
    stdout, stderr = await asyncio.wait_for(
        proc.communicate(input=file_content.encode('utf-8')),
        timeout=3.0,
    )
except asyncio.TimeoutError:
    proc.kill()
    await proc.wait()
    logger.warning(f"Forge validator timeout on {file_path}")
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
    'forge_attempts': 0,                  # count of cascade get_ai_response
                                           # calls that returned a response
                                           # (does NOT count stream attempt;
                                           #  does NOT count failed/exception
                                           #  cascade calls). 0..3 depending
                                           #  on stream_model position.
    'forge_healed_at_iter': None,        # 1-based cascade-position index at
                                          # which we healed; None if
                                          # exhausted/skipped
    'forge_total_retry_tokens': 0,
    'forge_time_to_heal_ms': None,
    'forge_budget_exhausted': False,
    'forge_loop_detected_skip': False,
    'forge_validation_failed': False,    # True iff exhausted without heal
    'forge_errors': [],                  # validator errors at terminal state
    'forge_output_estimate_actual_ratio': None,  # mean(actual_out/projected_out)
                                                  # across all cascade iters in
                                                  # this turn. None if no
                                                  # cascade fired. Used for
                                                  # post-launch calibration
                                                  # of the 1.2x output
                                                  # multiplier in pre-flight
                                                  # (see §7.2).
    '_ratio_log': [],                    # internal: per-iter ratios, mean'd
                                          # at end of cascade. Not persisted.
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
            
            # Stream model already had its turn — advance past it in cascade.
            # If stream_model is the LAST cascade entry, no retries possible.
            try:
                start_idx = FORGE_VALIDATION_CASCADE.index(stream_model) + 1
            except ValueError:
                start_idx = 0  # stream_model not in cascade — start from top
            
            cascade_iter = start_idx
            
            # ─────────────────────────────────────────────────────────────
            # Worked example — loop-detect with cascade [Flash, Pro, Kimi, GPT-4.1]
            # stream_model = Flash, fails with errors X.
            # previous_error_signature = X  (set BEFORE loop entry, from
            #                                stream's validate result — this
            #                                is the load-bearing detail).
            # start_idx = 1 (advance past Flash).
            #
            #   cascade_iter=1 → Pro called. Returns errors X (same sig).
            #                    Loop-detect fires immediately (Pro's
            #                    errors == stream's errors).
            #                    cascade_iter += 2 → 3. continue.
            #
            #   cascade_iter=3 → GPT-4.1 called.
            #                    Branch A (heals): forge_attempts=2,
            #                      forge_healed_at_iter=4 (1-based cascade
            #                      position of GPT-4.1, since GPT-4.1 sits
            #                      at cascade index 3). break.
            #                    Branch B (still fails X):
            #                      Loop-detect fires again, cascade_iter += 2
            #                      → 5. continue. Out of bounds → exit.
            #                      forge_attempts=2,
            #                      forge_validation_failed=true.
            #
            # Why baseline = stream errors and not iter-1's errors: the
            # stream model was already given a turn. If iter 1 (Pro)
            # produces the SAME errors, the family has demonstrably failed
            # to fix the bug class — escalate aggressively to a different
            # family (skip Kimi, jump to GPT-4.1). This captures the most
            # useful signal: "stream + first retry produced identical
            # errors, so don't waste a tier on more of the same."
            #
            # Compare: WITHOUT loop-detect, cascade would run Pro + Kimi
            # + GPT-4.1 (3 retries) in sequence even when the first two
            # produced identical broken output, wasting tokens.
            # ─────────────────────────────────────────────────────────────
            
            while cascade_iter < len(FORGE_VALIDATION_CASCADE):
                retry_model = FORGE_VALIDATION_CASCADE[cascade_iter]
                
                # Build prompt (§8) — needed for token pre-flight estimate
                retry_prompt = build_validation_retry_prompt(
                    payload=routing.artifact_payload,
                    errors=result.errors,
                    original_user_prompt=prompt,
                )
                
                # Pre-flight token-budget check (Decision 8 — hard cap, no overshoot)
                prompt_estimate = _estimate_input_tokens(retry_prompt)
                # Conservative output estimate: ~1.2x the broken artifact's
                # total content size (model regenerates roughly the same shape)
                output_estimate = int(_artifact_content_tokens(routing.artifact_payload) * 1.2)
                projected_total = (forge_metrics['forge_total_retry_tokens']
                                   + prompt_estimate + output_estimate)
                if projected_total > FORGE_RETRY_TOKEN_BUDGET:
                    forge_metrics['forge_budget_exhausted'] = True
                    logger.warning(
                        f"🔥 Forge budget pre-flight: projected {projected_total} > "
                        f"budget {FORGE_RETRY_TOKEN_BUDGET}, stopping cascade "
                        f"(accumulated={forge_metrics['forge_total_retry_tokens']}, "
                        f"projected_call={prompt_estimate + output_estimate})"
                    )
                    break
                
                # Emit retry status to client. `attempt` = 1-indexed cascade
                # position so the user-visible model trail is self-documenting
                # (skipped tiers leave gaps which the UI can render as "skipped
                # X (same errors as previous)").
                yield f"data: {json.dumps({
                    'type': 'status',
                    'subtype': 'forge_retry',
                    'attempt': cascade_iter + 1,
                    'model': retry_model,
                    'reason': _short_reason(result.errors),
                })}\n\n"
                
                # Call model
                try:
                    retry_response, _, _ = get_ai_response(retry_prompt, retry_model)
                    in_tokens, out_tokens = _estimate_tokens(retry_prompt, retry_response)
                    orchestrator_record_usage(retry_model, in_tokens, out_tokens)  # Dec 7
                    forge_metrics['forge_total_retry_tokens'] += (in_tokens + out_tokens)
                    forge_metrics['forge_attempts'] += 1  # successful cascade call
                    # Capture pre-flight calibration ratio for this iter
                    # (output_estimate was computed pre-call above)
                    if output_estimate > 0:
                        forge_metrics['_ratio_log'].append(out_tokens / output_estimate)
                    # Per-iter calibration log line (separate from per-turn
                    # aggregate — gives raw data points for tuning the 1.2x):
                    logger.info(
                        f"🔥 forge_calibration: model={retry_model} "
                        f"projected_out={output_estimate} actual_out={out_tokens} "
                        f"ratio={out_tokens / output_estimate:.3f}"
                    )
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
                    forge_metrics['forge_healed_at_iter'] = cascade_iter + 1
                    forge_metrics['forge_time_to_heal_ms'] = int((time.monotonic() - t_start) * 1000)
                    yield f"data: {json.dumps({
                        'type': 'status',
                        'subtype': 'forge_done',
                        'healed_at': cascade_iter + 1,
                    })}\n\n"
                    break
                
                # Loop-detect (Decision 9): if errors are byte-identical to the
                # immediately-prior iteration, the current model family is also
                # stuck on the same pattern. Skip the next tier (advance by 2)
                # and continue. Single increment site to avoid double-step bugs.
                new_signature = _signature(new_result.errors)
                if new_signature == previous_error_signature:
                    forge_metrics['forge_loop_detected_skip'] = True
                    logger.info(
                        f"🔥 Forge loop-detect: {retry_model} produced same errors as prior iter, "
                        f"skipping cascade[{cascade_iter + 1}]"
                    )
                    previous_error_signature = new_signature
                    result = new_result
                    cascade_iter += 2
                    continue
                
                previous_error_signature = new_signature
                result = new_result
                cascade_iter += 1
            
            # Cascade exhausted without healing
            if forge_metrics['forge_healed_at_iter'] is None:
                forge_metrics['forge_validation_failed'] = True
                forge_metrics['forge_errors'] = [_error_to_json(e) for e in result.errors]
                yield f"data: {json.dumps({
                    'type': 'status',
                    'subtype': 'forge_exhausted',
                    'attempts': forge_metrics['forge_attempts'],
                    'errors': forge_metrics['forge_errors'],
                })}\n\n"
        
        # Aggregate per-turn calibration ratio (mean across iters in this turn).
        # None if no cascade fired. Used post-launch to validate / re-tune the
        # 1.2x output multiplier in pre-flight estimation.
        if forge_metrics['_ratio_log']:
            forge_metrics['forge_output_estimate_actual_ratio'] = (
                sum(forge_metrics['_ratio_log']) / len(forge_metrics['_ratio_log'])
            )
        # Drop the internal _ratio_log key before persistence (it's a working
        # buffer, not an envelope field).
        forge_metrics.pop('_ratio_log', None)
                # Surface broken-with-warning per Decision 4. Note: forge_*
                # fields ride in the message envelope (see §10), NOT mutated
                # onto routing.artifact_payload. The artifact_payload's data
                # shape stays clean for downstream consumers (panel, persist,
                # ContextBuilder); validation status travels alongside as
                # message metadata. The panel renderer reads forge_validation_failed
                # / forge_errors from the message envelope to draw the ribbon.

# forge_metrics is then passed alongside artifact_data into the SSE done
# payload (§9.2) and to save_interaction (§13.3) for persistence.

# === END Forge M1 ===

# Existing artifact_data assembly continues at :2049...
```

### 7.2 Notes on the cascade pseudo-code

- **`FORGE_RETRY_TOKEN_BUDGET = 80_000`** — module-level constant in
  `chat_engine.py` next to `FORGE_VALIDATION_CASCADE`. Hard cap (no
  overshoot) — pre-flight estimate gates each retry call before invoking
  the model.
- **`_has_validatable_files(payload)`** returns `True` iff at least one
  file's `language` is in `_LANGUAGE_VALIDATORS`. Cheap O(n) check; avoids
  spawning subprocess for SVG/HTML/CSS-only artifacts.
- **`_signature(errors)`** returns a hashable tuple:
  `tuple(sorted((e.file_path, e.line, e.message) for e in errors))`.
  Column-exclusion rationale documented inline in §11 E6 (loop-detect
  edge case) where the comparison logic lives.
- **`_short_reason(errors)`** handles the multi-file case explicitly:
  - Single file: `"validator: <kind>-parse @ <file>:<line>"`.
  - Multi-file (>1 distinct paths): `"validator: N files with syntax
    errors (<file1>:<line1>, <file2>:<line2>, ...)"` — first error per
    file, sorted by file path.
  This ensures the SSE `forge_retry` event surfaces all impacted files,
  not just the alphabetically-first one.
- **`_estimate_tokens(prompt, response)`** and `_estimate_input_tokens(prompt)`
  delegate to existing `orchestrator/accountant.py` token estimation
  utilities. `_artifact_content_tokens(payload)` sums tokenized file
  contents (already in payload) for output-size estimation.
- **Increment discipline (Decision 9 implementation):** there is exactly
  ONE place `cascade_iter` advances on a non-skip path (`cascade_iter +=
  1`) and exactly ONE on a skip-detect path (`cascade_iter += 2` followed
  by `continue`). The `continue` is load-bearing — it bypasses the
  unconditional `cascade_iter += 1` at the bottom of the loop, preventing
  double-increment. Worked-example walkthrough in the §7.1 code comment
  validates that on Flash+Pro=identical-errors, the cascade reaches
  Kimi(skipped→GPT-4.1 region), as Decision 9 intends.
- **Stream model handling (Decision 9-adjacent):** if the original stream
  attempt's model is in `FORGE_VALIDATION_CASCADE`, `start_idx` advances
  past it. The stream attempt counts as iter 0; cascade starts at the
  next tier. Avoids burning iter 1 on the same model that just failed.
  Edge case: stream_model is the LAST cascade entry (e.g. user picked
  GPT-4.1 directly) → `start_idx = len(cascade)` → no retries fire,
  artifact surfaces broken-with-warning (E16).
- **Even on exhaustion, the artifact flows downstream** —
  `routing.artifact_payload` remains populated AND UNMUTATED;
  `persist_decision` runs as normal. Validation status (`forge_validation_failed`,
  `forge_errors`) travels in the message envelope, NOT injected into the
  artifact payload. The panel renderer reads from the envelope to draw
  the warning ribbon (Decision 4). This separation matters because other
  consumers of `artifact_payload` (Firestore persist, ContextBuilder on
  next turn, replay convergence) shouldn't have to know about Forge meta-keys.
- **`forge_attempts` semantics:** initialized to 0; incremented exactly
  once per *successful* `get_ai_response` call inside the cascade loop
  (the `successful` qualifier excludes the catch-and-continue path
  where the call raised). Does NOT count the original stream attempt.
  Maximum value is bounded by `len(FORGE_VALIDATION_CASCADE) - start_idx`,
  i.e. depends on `stream_model` per Amendment 4. See §10.1's
  stream-model-aware semantics table for max-on-exhaustion values per
  cascade-position.
- **Pre-flight 1.2x output multiplier — calibration plan:** the 1.2x
  factor in `output_estimate = _artifact_content_tokens(payload) * 1.2`
  is an *initial heuristic*, not an empirically-validated constant.
  Each cascade iteration logs a `forge_calibration` line with
  `projected_out`, `actual_out`, and `ratio`. The per-turn aggregate
  `forge_output_estimate_actual_ratio` is the mean of those ratios (or
  `null` if no cascade fired). After ~100 production retries land,
  aggregate the per-iter logs and recalibrate the multiplier. A
  follow-up issue ("calibrate Forge pre-flight output estimate against
  measured retry sizes") tracks this. Until calibrated, the 1.2x is
  conservative (slight over-estimate) so the budget gate is more likely
  to pre-flight-block than to overshoot.

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

**Acknowledged trade-off.** The retry prompt deliberately omits the
prior conversation history, including any nuanced user preferences from
earlier turns ("no class components", "use Tailwind", etc.). The retry
model sees only the validator error, the broken artifact, and the *raw
user request from this turn*. Fine-grained preferences from turns N-1,
N-2, etc. are not visible to retry attempts. This is acceptable for
M1's syntax-fix scope — JSX parser errors are syntactically local and
rarely require multi-turn context to fix correctly. If M2 expands Forge
beyond syntax fixes (e.g. semantic / behavioral validation via Run-tool
output), this trade-off should be revisited.

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

Firestore is schema-less; "migration" here means: writes start emitting
five new fields, reads gracefully handle their absence on legacy documents.

The chat message record (saved at `:2257` via `save_interaction`) gains:

```json
{
  "...existing fields...": "...",
  "forge_attempts":          integer | null,    // count of cascade
                                                 // get_ai_response calls that
                                                 // returned a response.
                                                 // Excludes the stream
                                                 // attempt and excludes
                                                 // catch-and-continue
                                                 // failures. Max value
                                                 // depends on stream_model
                                                 // position (see table below).
  "forge_healed_at":         integer | null,    // null if exhausted, skipped,
                                                 // OR no retries needed
                                                 // (passed first try);
                                                 // 1-based cascade-position
                                                 // index of the model that
                                                 // healed (1=Flash, 2=Pro,
                                                 // 3=Kimi, 4=GPT-4.1).
  "forge_validation_failed": boolean | null,    // null if validator skipped;
                                                 // false if passed (with or
                                                 // without retries); true iff
                                                 // cascade exhausted without
                                                 // healing
  "forge_errors":            array | null,      // null unless
                                                 // forge_validation_failed=true;
                                                 // then: list of error dicts
                                                 // {file_path, line, column,
                                                 //  message, excerpt}
  "forge_output_estimate_actual_ratio": float | null  // mean of
                                                 // (actual_out / projected_out)
                                                 // across all cascade iters
                                                 // in this turn. null if no
                                                 // cascade fired. Used post-
                                                 // launch for calibrating the
                                                 // 1.2x output multiplier in
                                                 // pre-flight (see §7.2 +
                                                 // M2 calibration follow-up).
}
```

Rationale for `forge_validation_failed` + `forge_errors` in the message
envelope (NOT mutated onto `artifact_payload`): the payload's data shape
stays clean for downstream consumers (panel renderer, persist_decision,
ContextBuilder on next turn, replay convergence). Validation status is
message metadata — it travels alongside the artifact, not inside it.
The panel renderer reads from the message envelope to draw the warning
ribbon (Decision 4).

#### Per-state semantics

| State | `forge_attempts` | `forge_healed_at` | `forge_validation_failed` | `forge_errors` | `forge_output_estimate_actual_ratio` |
|---|---|---|---|---|---|
| No validatable files (e.g. SVG) | `null` | `null` | `null` | `null` | `null` |
| Validated, passed first try | `0` | `null` | `false` | `null` | `null` |
| Validated, healed at cascade-pos 2 (Pro) | `1` | `2` | `false` | `null` | `<float>` |
| Validated, healed at cascade-pos 4 (GPT-4.1) | varies (see below) | `4` | `false` | `null` | `<float>` |
| Validated, exhausted | varies (see below) | `null` | `true` | `[...errors]` | `<float>` |
| Validated, budget pre-flight stopped | varies (see below) | `null` | `true` | `[...errors]` | `<float>` |
| Validated, no retries possible (E16: stream = GPT-4.1) | `0` | `null` | `true` | `[...errors]` | `null` |

#### Stream-model-aware max `forge_attempts` on exhaustion

Post Amendment 4, `forge_attempts` max is bounded by where `stream_model`
sits in the cascade (since cascade `start_idx` advances past it):

| Stream model (cascade position) | `start_idx` | Reachable cascade iters | Max `forge_attempts` on exhaustion |
|---|---|---|---|
| Flash (pos 1) | 1 | Pro, Kimi, GPT-4.1 | **3** |
| Pro (pos 2) | 2 | Kimi, GPT-4.1 | **2** |
| Kimi (pos 3) | 3 | GPT-4.1 | **1** |
| GPT-4.1 (pos 4) | 4 | — (none) | **0** (E16) |
| Stream model NOT in cascade (e.g. user picks `auto` and router lands on a non-cascade model) | 0 | Flash, Pro, Kimi, GPT-4.1 | **4** |

Note: loop-detect (Decision 9) further reduces the actual attempt count
per turn — e.g. stream-Flash + iter-1-Pro = identical errors → skip Kimi
→ run GPT-4.1 = 2 attempts on exhaustion (not 3). The "Max" column is
the upper bound assuming no skip-detect fires.

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
| E4 | Cascade exhausted (all reachable iters fail) | Per Decision 4: emit `forge_exhausted` SSE event, set `forge_validation_failed=true` and `forge_errors=[...]` in the **message envelope** (NOT mutated onto the artifact payload — see Amendment 5). Panel renderer reads from envelope and draws red warning ribbon + inline errors. Number of "all" reachable iters varies with `stream_model` per E13/E16. |
| E5 | Multi-file artifact, only one file fails | Validator reports the one failure. Retry prompt includes ALL files (model needs cross-file context per §8.4). On heal, all files are re-saved. |
| E6 | Model returns identical errors to the prior baseline (initially the stream attempt's errors; thereafter the immediately-prior cascade iter's errors) | Loop-detection fires (Decision 9): on byte-identical signature `tuple(sorted((file_path, line, message)))`, `cascade_iter += 2` and `continue` (skipping the next tier). Logged as `forge_loop_detected_skip=true`. Worked example in §7.1 pseudo-code comment shows stream-Flash + iter-1-Pro = identical-errors → skip Kimi → call GPT-4.1. **Why the signature excludes column:** minor reformatting (whitespace differences, prettier vs raw output) can shift the column without changing the underlying parse failure. Including column would cause spurious skip-misses on cosmetically-different but semantically-identical errors. Excluding it makes loop-detect conservative (likely to fire when it should) at the cost of rare false positives on genuinely-different-bugs-on-same-line — an acceptable trade because the worst-case false positive (skip a tier that might have helped) is bounded by the cascade reaching GPT-4.1 anyway. |
| E7 | Retry token budget (80k) exceeded mid-cascade | Loop breaks immediately. `forge_budget_exhausted=true`. Treated like exhaustion: artifact renders with red ribbon, current errors surfaced. |
| E8 | Non-validatable artifact (no JSX/TSX/JS/TS files; pure HTML/CSS/SVG/Python) | `_has_validatable_files` returns False, validator never runs. `forge_attempts=null`, `forge_healed_at=null`. Zero overhead. |
| E9 | Artifact has zero files | Same as E8 — validator skipped. |
| E10 | User regenerates the same broken artifact in next turn | Each turn is a fresh cascade. No cross-turn state. The previous turn's `forge_attempts` does not influence the new turn. |
| E11 | Cascade #1 (existing JSON-malform retry) succeeds; Forge runs after | Sequential pipeline: cascade #1 produces a valid `artifact_payload`, Forge then validates it. Both can fire in one turn. |
| E12 | Patch-apply cascade (#2/#3) succeeds; should Forge also run? | **Yes** — patches can introduce syntax bugs just like full regens. Forge runs on the post-patch `artifact_payload` regardless of how it got built. |
| E13 | Stream model is one of the cascade models AND fails | Cascade advances past `stream_model` — `start_idx = FORGE_VALIDATION_CASCADE.index(stream_model) + 1`. The stream attempt counts as iter 0; cascade starts at the next tier up. Avoids burning a retry slot on the same model that just failed in the original stream attempt (a slightly-different error wouldn't trip loop-detect, so without this, iter 1 on Flash-stream-failure would call Flash again). |
| E14 | Retry prompt exceeds the model's context window | Caught by `get_ai_response` exception path, treated as a failed cascade iteration, loop continues. The §8.3 large-artifact mitigation should rarely allow this in practice. The pre-flight token-budget check (Decision 8) also bounds projected size. |
| E15 | Validator runs on an artifact already flagged failed in the same turn | Defensive: never re-validate. The `forge_validation_failed` envelope flag (set on cascade exhaustion) is a one-shot terminal state per turn; the validator is not re-entered after it's set. |
| E16 | Stream model is the LAST cascade entry (e.g. user explicitly picked `gpt-4.1` and validation fails) | `start_idx = len(cascade)` → cascade loop never enters → no retries possible. Artifact surfaces broken-with-warning per Decision 4. Persists as `forge_attempts=0`, `forge_validation_failed=true`, `forge_errors=[...]`. The user gets the broken artifact + the warning ribbon and can manually retry / reword. |

---

## 12. Telemetry contract

Per Decision 10 (as expanded by Amendment 8), every artifact-bearing
turn emits these eight metrics. Sink: existing `logger.info` with
structured fields (parseable by the metrics extraction pipeline). Field
names are exact:

| Field | Type | Semantics |
|---|---|---|
| `forge_validation_attempted` | bool | True iff `_has_validatable_files` returned True. |
| `forge_validation_passed_first_try` | bool | True iff first `validate_artifact_payload` call returned ok. False if any retry was needed. Only meaningful when `forge_validation_attempted=true`. |
| `forge_healed_at_iter` | int \| null | 1-based cascade-position index where heal occurred (1=Flash, 2=Pro, 3=Kimi, 4=GPT-4.1); null if exhausted, budget-stopped, or skipped. |
| `forge_total_retry_tokens` | int | Sum of input+output tokens for all cascade `get_ai_response` calls within this turn. 0 if no retries. |
| `forge_time_to_heal_ms` | int \| null | ms from first-validate-fail to successful heal. null if not healed. |
| `forge_budget_exhausted` | bool | True iff cascade stopped due to 80k pre-flight retry budget. |
| `forge_loop_detected_skip` | bool | True iff Decision 9's tier-skip mechanism fired at least once during the cascade. |
| `forge_output_estimate_actual_ratio` | float \| null | Per-turn aggregate (mean) of per-iter `actual_output_tokens / projected_output_tokens`. null if no cascade fired. **Calibration metric** — used post-launch to validate / re-tune the 1.2x multiplier in pre-flight estimation (§7.2). Per Amendment 8 + the M2 follow-up issue. |

Per-iter calibration log lines also fire (one per cascade call), at
`logger.info` level, prefixed `🔥 forge_calibration:`. These are
*separate* from the per-turn aggregate — they give raw
projected-vs-actual data points for tuning the multiplier. Aggregate
the per-iter logs after ~100 production retries to validate the 1.2x.

Per-turn log line shape (single-line key=value for grep / metrics-extractor):

```
INFO 🔥 forge: forge_validation_attempted=true forge_validation_passed_first_try=false forge_healed_at_iter=2 forge_total_retry_tokens=32487 forge_time_to_heal_ms=8412 forge_budget_exhausted=false forge_loop_detected_skip=false forge_output_estimate_actual_ratio=1.087
```

Per-iter calibration log line shape:

```
INFO 🔥 forge_calibration: model=deepseek-v4-pro projected_out=18000 actual_out=19560 ratio=1.087
```

Emit the per-turn aggregate once per turn, after the cascade resolves
(success, exhaustion, or skipped), at the existing accountant log site
(`:2346` neighborhood).

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
| `app/services/artifact_validator.py` | Validator + `build_validation_retry_prompt` + helpers (`_has_validatable_files`, `_signature`, `_short_reason`, `_error_to_json`, `_estimate_input_tokens`, `_artifact_content_tokens`) | ~280 |
| `app/services/forge_validators/check_jsx.cjs` | Babel-based Node CLI wrapper for JSX/JS syntax check (reads from stdin, no tmpfile) | ~25 |
| `app/services/forge_validators/__init__.py` | Empty package marker | 0 |
| `tests/test_artifact_validator.py` | Unit tests for the validator (clean React, Netflix bug, multi-file partial-fail, missing import, validator-timeout simulation, missing-binary simulation, loop-detect tier-skip, budget exhaustion, context-window overflow) | ~280 |
| `tests/fixtures/artifacts/netflix_broken_jsx.json` | The Netflix bug fixture (line 45 `(<></>) =>` pattern) | ~30 |
| `tests/fixtures/artifacts/clean_react.json` | Validates clean | ~20 |
| `tests/fixtures/artifacts/multi_file_partial_fail.json` | 3 files, 1 has a syntax error | ~50 |
| `tests/fixtures/artifacts/svg_only.json` | SVG, no validatable files (skip path) | ~10 |
| `tests/fixtures/artifacts/loop_detect_identical_errors.json` | Pre-canned cascade response that returns byte-identical errors twice (exercises Decision 9 tier-skip math) | ~60 |
| `tests/fixtures/artifacts/budget_exhaustion_large.json` | Artifact whose retry-prompt projected size triggers the 80k pre-flight budget check (E7) | ~80 |
| `tests/fixtures/artifacts/context_window_overflow.json` | Artifact fixture for E14 test. **Strategy: mock-the-call**, not actually overflow — V4 context windows are huge (Flash 128k, Pro 256k, Kimi 256k, GPT-4.1 1M) and generating a fixture that genuinely overflows even Flash would need ~40k tokens of artifact content (≈400 KB on disk). Instead, the test mocks `get_ai_response` to raise the exception type the SDK raises on context overflow, and asserts the cascade catch-and-continue path handles it cleanly. The fixture itself is a normal-sized artifact (~100 LOC) — only the model's response is mocked to simulate overflow. | ~100 |

### TOUCHED

| Path | Change | Approx LOC |
|---|---|---|
| `app/blueprints/chat_routes.py` | Insert validator cascade between `:1751` and `:2042` (§6/§7/§8); wrap existing cascades #1/#2/#3 with `orchestrator_record_usage` (§13.2); pass all four `forge_*` envelope fields (`forge_attempts`, `forge_healed_at`, `forge_validation_failed`, `forge_errors`) to `save_interaction` (§13.3) and into the SSE done payload | ~130 added |
| `app/services/chat_engine.py` | Add `FORGE_VALIDATION_CASCADE` constant; add `FORGE_RETRY_TOKEN_BUDGET = 80_000`; extend `save_interaction` signature for the four new envelope fields (§13.3); add startup guard (§13.1) | ~30 added |
| `app/static/js/startNewChatModule.js` | SSE handler `type:'status'` switch additions for `forge_retry` / `forge_done` / `forge_exhausted`; small renderers `appendForgeRetryBlock` / `appendForgeHealedBlock` / `appendForgeExhaustedRibbon`; replay-side breadcrumb reconstruction from message envelope (`forge_attempts`, `forge_healed_at`); pass envelope `forge_validation_failed`/`forge_errors` to artifactPanel | ~110 added |
| `app/static/js/artifactPanel.js` | Read `forge_validation_failed` / `forge_errors` from the **message envelope** (NOT the artifact payload) when opening an artifact; render red warning ribbon (Decision 4) when failed=true | ~40 added |
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
  `package.json` under `dependencies`. Pin a specific minor for
  reproducibility: `^7.24.0` (verify against latest stable at
  implementation time and bump the floor if a newer minor has fixes
  we want; never use `^7.x` floating).

No Python packages added. All Python deps already in `requirements.txt`
(asyncio is stdlib).

---

## Acceptance criteria

The M1 PR is ready to merge when **all** of the following pass:

1. **Netflix bug heals.** Synthetic input artifact with the
   `return (<></>) =>` pattern on line 45 of `App.jsx`, with stream_model
   = `deepseek-v4-flash`, heals on cascade iter 1 (Pro) without user
   action. Verified by an integration test that mocks the cascade to
   return the Netflix bug on stream + a fixed version on iter 1
   (Pro). Asserts `forge_attempts=1`, `forge_healed_at=1`,
   `forge_validation_failed=false`.
2. **Clean artifact passes first-try.** Synthetic clean React artifact
   triggers no retry. `forge_attempts=0`, `forge_healed_at=null`,
   `forge_validation_failed=false`, `forge_errors=null`,
   `forge_total_retry_tokens=0`.
3. **SVG / pure-CSS artifact bypasses validator.** Synthetic SVG-only
   artifact triggers no subprocess spawn. `forge_attempts=null`,
   `forge_healed_at=null`, `forge_validation_failed=null`,
   `forge_errors=null`. Verified by spy on subprocess invocation.
4. **Impossible artifact exhausts cascade.** Mock-cascade returns
   syntactically broken artifact for all reachable iters (varies with
   stream_model: V4-Flash stream → 3 cascade attempts; V4-Pro stream →
   2; Kimi stream → 1; GPT-4.1 stream → 0). `forge_exhausted` event
   emitted, red ribbon renders in panel from message envelope (NOT
   from artifact payload), validator errors expanded inline.
   `forge_validation_failed=true`, `forge_errors=[non-empty list of
   ValidationError dicts]`. **Two assertions** required to defend
   against regression:
   - **Positive:** the persisted Firestore message document contains
     all five `forge_*` envelope fields (`forge_attempts`,
     `forge_healed_at`, `forge_validation_failed`, `forge_errors`,
     `forge_output_estimate_actual_ratio`) with values matching the
     failure state (`forge_validation_failed=true`,
     `forge_errors=[non-empty]`, etc.).
   - **Negative (regression guard):** `routing.artifact_payload`
     does NOT contain underscore-prefixed keys — verified by checking
     `_forge_*` keys are absent from the persisted Firestore artifact
     document. This guards against a future change accidentally
     reintroducing payload-mutation (Amendment 5's specific anti-goal).
5. **Stream model skip — iter 1 uses next tier.** When `stream_model =
   deepseek-v4-flash` and the stream attempt fails validation, iter 1
   of the cascade calls `deepseek-v4-pro`, NOT `deepseek-v4-flash`
   again. Verified by capturing the model argument on the first
   `get_ai_response` cascade call.
6. **Identical-error-twice triggers tier skip — Decision 9 worked
   example.** Mock setup: `stream_model = deepseek-v4-flash` produces
   error signature X on the stream attempt. Cascade iter 1 (Pro) also
   produces X. Assertions:
   - `forge_loop_detected_skip = true`
   - The next `get_ai_response` call uses `gpt-4.1`, NOT `kimi-k2.5`
     (Kimi is the skipped tier).
   - If GPT-4.1 then fails (4th model attempt counting stream), cascade
     exits with `forge_validation_failed=true`.
7. **Stream model is GPT-4.1 — no retries (E16).** When user picks
   `gpt-4.1` directly and the stream attempt fails validation,
   `start_idx = len(cascade)` → cascade loop never enters. Asserts
   zero `get_ai_response` cascade calls, `forge_attempts=0`,
   `forge_validation_failed=true`, `forge_errors=[non-empty]`.
   Artifact still surfaces broken-with-warning per Decision 4.
8. **80k retry budget triggers pre-flight early stop (Decision 8 hard
   cap).** Mock-cascade with a large artifact whose retry-prompt
   pre-flight projection exceeds budget on iter 2 (e.g. iter 1
   accumulates 35k actual; iter 2's pre-flight projects another 50k →
   85k projected total). Assertions:
   - `forge_budget_exhausted = true`.
   - Iter 2's `get_ai_response` is NOT called (pre-flight gates the
     call before it fires).
   - `forge_total_retry_tokens` reflects only iter 1's actual usage
     (no iter 2 overshoot).
9. **Validator timeout doesn't block.** Mock validator subprocess
   sleeps 5s (>3s timeout). Logged warning, artifact renders, no
   cascade kicked off. `forge_validation_attempted=true`,
   `forge_validation_passed_first_try=true` (treated as pass on
   timeout per graceful degradation).
10. **All cascade retries debited from accountant (Decision 7).** Spy
    on `orchestrator_record_usage`: assert it's called once per
    cascade iteration (not just once for the stream model). Verified
    for the existing cascades #1/#2/#3 AND the new Forge cascade.
11. **Multi-file `_short_reason` formats correctly.** Mock validator
    returns errors in 2 distinct files (`App.jsx:45`, `Hero.jsx:12`).
    Captured `forge_retry` SSE event has
    `reason: "validator: 2 files with syntax errors (App.jsx:45,
    Hero.jsx:12)"`. Single-file case still produces
    `"validator: parse_error @ App.jsx:45"`.
12. **Reload after healed turn shows breadcrumb (Decision 2 envelope
    persistence).** Save a healed message
    (`forge_attempts=2, forge_healed_at=2`), reload page, assert
    "✓ Healed after 2 attempts" string present in DOM. Save an
    exhausted message (`forge_validation_failed=true`), reload, assert
    red warning ribbon renders in artifact panel. Both reads come from
    the message envelope, NOT from the artifact payload.
13. **All `forge_*` telemetry metrics emitted (Decision 10).** For
    every turn that produced an artifact (regardless of validator
    outcome), the eight fields appear in one structured log line.
    Verified by parsing test logs.
14. **No regression in existing artifact flow.** The existing
    live-patch streaming, patch-apply cascade #2/#3, and JSON-malform
    cascade #1 all continue to work — the M1 changes are additive, not
    replacing.
15. **Pre-flight calibration ratio is logged for every cascade
    iteration (Amendment 8).** For each successful cascade
    `get_ai_response` call, a `🔥 forge_calibration:` log line is
    emitted with `model`, `projected_out`, `actual_out`, and `ratio`
    fields. The per-turn aggregate `forge_output_estimate_actual_ratio`
    is the mean of those iter ratios (or `null` if no cascade fired).
    Verified by parsing test logs for both per-iter and per-turn lines.
    Note for M2: aggregate the per-iter logs across the first ~100
    production retries and recalibrate the 1.2x output multiplier in
    `output_estimate = _artifact_content_tokens(payload) * 1.2`.
    Tracked by the follow-up issue *"calibrate Forge pre-flight output
    estimate against measured retry sizes"*.

The implementation plan (separate doc, `superpowers:writing-plans`) decomposes
these 15 acceptance criteria into ordered TDD tasks.

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
