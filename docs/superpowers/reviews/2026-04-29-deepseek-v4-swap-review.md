# DeepSeek V4 Swap — Code Review

**Date:** 2026-04-29
**Reviewer:** automated subagent code review (independent of authoring agent)
**Branch:** `feat/deepseek-v4-swap`
**Base:** `origin/main`

## Verdict

**APPROVED WITH NOTES** — minor non-blocking issues only

## Summary

This branch retires the deprecated `deepseek-chat` and `deepseek-reasoner`
model IDs and introduces `deepseek-v4-flash` (fast, 1M context,
thinking-mode default) and `deepseek-v4-pro` (best for code, 1M context,
thinking-mode default) as first-class catalog entries. The scope is
tightly bounded: model catalog, orchestrator fallback chains, artifact
cascade lists, frontend pill mapping, localStorage versioning,
reasoning-UI detection, template cache-busters, and affected tests. No
behavior logic was changed — all callers that previously used the old
IDs now reach the new ones via aliases.

The implementation is coherent end-to-end. One pre-existing stale default
in `app/utilities.py:send_prompt()` is noted but predates this PR. A
meaningful operational gap around thinking-mode flags warrants a follow-up
ticket before V4 Flash is used in latency-sensitive paths.

## Findings by category

### Model ID resolution

PASS. `app/model_catalog.py`:

- `deepseek-v4-flash` (line 151) and `deepseek-v4-pro` (line 161): both
  catalogued with `provider="deepseek"`, `selectable=True`, `coding=True`,
  `reasoning=True`, `context=1048576`, `long_context=True`. Attributes
  are correct and complete.
- `MODEL_ALIASES` (lines 315–318): all four legacy IDs map in the correct
  direction (legacy → new). `deepseek-chat` → `deepseek-v4-flash`,
  `deepseek-reasoner` → `deepseek-v4-pro`, `deepseek-v3` →
  `deepseek-v4-flash`, `deepseek-r1` → `deepseek-v4-pro`. No inverted
  mappings detected.
- `TASK_ROUTES["coding"]` (line 279) = `"deepseek-v4-pro"`. Correct per
  spec.
- `resolve_model_id("deepseek-v4-flash")` and
  `resolve_model_id("deepseek-v4-pro")` both self-resolve correctly:
  neither ID is in `MODEL_ALIASES` so they fall through the identity
  path, catalog confirms both present, returns the canonical ID.

### Cascade & fallback consistency

PASS. `app/orchestrator/config.py`:

- `FALLBACK_CHAINS["coding"]` (lines 36–46): `deepseek-v4-pro` first,
  `deepseek-v4-flash` second. Correct ordering (best-first).
- `FALLBACK_CHAINS["reasoning"]` (line 47): `deepseek-v4-pro` leads.
  Correct.
- `FALLBACK_CHAINS["general_chat"]` (line 62): `deepseek-v4-flash`.
  Correct (cheaper general-purpose entry).
- `FALLBACK_CHAINS["long_context"]` (line 83): `deepseek-v4-flash`.
  Correct (1M context advantage).
- `FALLBACK_CHAINS["uncensored"]` (line 96): `deepseek-v4-flash`.
  Consistent.

`app/services/chat_engine.py`:

- `ARTIFACT_MODEL_CASCADE` (lines 154–161): `deepseek-v4-pro` then
  `deepseek-v4-flash`. Correct.
- `ARTIFACT_CAPABLE_MODELS` (lines 166–173): both IDs present.

### Frontend pill mapping

PASS. `app/static/js/ghostModel.js`:

- `MODELS` array (lines 14–18): `{ id: 'deepseek-v4-pro', label: 'Code' }`
  and `{ id: 'deepseek-v4-flash', label: 'Think' }`. Code pill →
  v4-pro, Think pill → v4-flash. Matches spec.
- `TASK_MODEL_MAP` (lines 31–33): `coding: 'deepseek-v4-pro'`,
  `reasoning: 'deepseek-v4-pro'`. Correct.
- `ghostLockedModel_v2` localStorage key — confirmed at all 5 sites:
  - line 115: `getItem`
  - line 348: `removeItem` (selectModel/auto path)
  - line 361: `removeItem` (lockModel/auto path)
  - line 365: `setItem`
  - line 376: `removeItem` (clearLock)

### Reasoning-UI detection extension

PASS at all 4 sites. All detection expressions include `deepseek-v4-pro`,
`deepseek-v4-flash`, AND legacy `deepseek-reasoner`:

- `app/static/js/codeWindow.js:2215` (`isDeepSeekEarly`): three-way OR
  confirmed.
- `app/static/js/codeWindow.js:2306` (`isDeepSeekR1`, stream init): same.
- `app/static/js/codeWindow.js:3743` (`isDeepSeekR1`, completion data):
  same.
- `app/static/js/startNewChatModule.js:1494` (`isDeepSeekR1`, resume):
  same.

Legacy `deepseek-reasoner` correctly retained in all checks for backward
compatibility with existing saved sessions.

### Cache-buster bumps

**PASS — empirically verified.** Reviewer's original report flagged
inability to verify the increments without git history. Authoring agent
followed up with `git diff origin/main..HEAD -- app/templates/index.html`
and confirmed:

- `codeWindow.js`: `?v=55` → `?v=57` ✓ (incremented)
- `startNewChatModule.js`: `?v=15` → `?v=17` ✓ (incremented)
- `ghostModel.js`: `?v=4` → `?v=7` ✓ (incremented)

All three V4-modified frontend scripts have their cache-busters properly
bumped upward. Users will fetch fresh JS post-deploy.

### Backward compatibility

PASS. `resolve_model_id('deepseek-chat')` resolves to `deepseek-v4-flash`
via `MODEL_ALIASES`; `resolve_model_id('deepseek-reasoner')` resolves to
`deepseek-v4-pro`. Existing saved sessions referencing legacy IDs will
route correctly at call time.

No unresolved `deepseek-chat` or `deepseek-reasoner` references found in
the diff outside the intentional alias map entries.

### Test correctness

PASS. `tests/test_context_builder.py`:

- All `model=` arguments updated to `"deepseek-v4-flash"` (lines 58, 75,
  101, 117, 130, 139). These are metadata fixtures only — tests assert on
  context-builder behavior, not model routing. The rename is purely
  mechanical and correct.

`tests/test_search_orchestrator.py`:

- `SEARCH_CONTEXT_BUDGET` mock key `'deepseek'` (line 84) is a
  prefix-based substring used by `get_model_family()`, not a literal
  model ID. It correctly covers both V4 IDs since both contain the
  substring `deepseek`. No update required, none made — correct.

### Missing changes / hardcoded references

One pre-existing stale default, **out of scope** but noted for triage:

`app/utilities.py:458`:
```python
def send_prompt(prompt, model="llama-3.1-70b-versatile"):
```

The string `"llama-3.1-70b-versatile"` does not exist in `MODEL_CATALOG`
(the current entry is `"llama-3.3-70b-versatile"`). Any call site that
reaches `send_prompt` without an explicit model argument will have
`resolve_model_id` fall through to `DEFAULT_MODEL` since the default is
not catalog-known. Effective behavior is correct (DEFAULT_MODEL works)
but the argument is misleading. Pre-dates this PR; recommend cleanup in
a separate commit.

No hardcoded `deepseek-chat` or `deepseek-reasoner` strings found
anywhere in the diff outside the alias map. No other files in the
reviewed set contain the old IDs in non-alias contexts.

### Decision 1 prerequisite check (for Forge M1)

PASS. `resolve_model_id('deepseek-v4-flash')` and
`resolve_model_id('deepseek-v4-pro')` both return the input unchanged
(they are not in `MODEL_ALIASES`, so the lookup falls through to the
identity; both are in `MODEL_CATALOG`, so the catalog guard passes). The
Forge M1 artifact-validation loop may safely reference these IDs once
this PR merges.

## Heads-up notes (non-blocking)

**Thinking-mode flag not passed to DeepSeek V4.**
`app/utilities.py:_execute_prompt_request()` (around line 386) constructs
the OpenAI-format payload with `temperature` and `max_tokens` but no
`thinking` control field. `app/services/chat_engine.py:get_ai_response()`
follows the same pattern. DeepSeek V4 has thinking-mode ON by default,
meaning both `deepseek-v4-flash` and `deepseek-v4-pro` will produce
reasoning traces under current call semantics. The existing
`reasoning_content` extraction at `utilities.py:415` handles the output
correctly when traces arrive. However, there is currently no mechanism
to suppress thinking-mode on `deepseek-v4-flash` for use cases where it
was chosen for speed (e.g., `general_chat` and `long_context` fallback
chains). This may produce unexpected latency regressions on those paths
compared to the old `deepseek-chat` behavior. The gap is **out of scope
for this PR** but should be tracked as a follow-up before V4 Flash is
relied upon in latency-sensitive fallback positions.

## Recommendation

**Ready to merge.** The swap is mechanically complete and consistent
across all 11 files. No blocking issues found. Two items to carry
forward:

1. Cache-buster version-increment verification — **completed**, all three
   bumped correctly (see "Cache-buster bumps" above).
2. Open a follow-up ticket for explicit thinking-mode control on
   `deepseek-v4-flash` in latency-sensitive fallback paths.

---

## Notes from authoring agent (post-review)

Two points the review raised that are now resolved or in flight:

- **Cache-buster verification:** confirmed empirically. All three V4-touched
  scripts have their `?v=N` incremented above main's value at rebase time.
- **Pre-existing `llama-3.1-70b-versatile` stale default in
  `utilities.py:458`:** filed as a separate observation. Out of scope for
  this PR; will not be addressed inline (per project convention: don't
  fix incidental code-reading observations inline; file them).
- **Thinking-mode flag follow-up:** valid concern. Not addressed in this
  PR. Should become a tracked issue before V4 Flash sees production load
  on `general_chat` / `long_context` chains.
