# Smart Context Window — Design Specification

**Status:** Approved, ready for implementation
**Date:** 2026-04-06
**Branch:** feat/dream-system

---

## Summary

| Step | Component | What it fixes |
|------|-----------|---------------|
| 1 | Artifact State Tracker | Per-file SHA-256 hashing, dirty-file detection, patch validation, flush-before-eviction, version-transition diffing |
| 2 | Context Builder | 4-zone prompt assembly, 3-tier history (verbatim/metadata/dropped), microcompact on turns [len-7..len-3], dynamic Zone 2 budget, metadata extraction at save time with `had_error` flag |
| 3 | Decision-via-Prompt + ActionParser | `ACTION:` first-line protocol, streaming buffer for chunk boundaries, conditional artifact injection, bounded 1-retry patch correction, fallback logging with model name |
| 4 | AutoCompact (future) | Demand-triggered LLM summary, async with `compact_in_progress` guard, `compact_boundary_turn = len - tier1_size`, opaque labeled block injection, cheapest-model hint with fallback |

**What gets removed:** `<META>` parsing, `ArtifactDecisionEngine`, `detect_artifact_update_intent()`, fixed 20-message cap.

**What stays:** `ArtifactStore`, `ArtifactRouter` (simplified), existing Firestore schema (extended, not replaced), Memory V2/V3, all existing artifact routes/UI.

---

## Not In Scope

This design intentionally does not solve:

- **Memory V2/V3 integration with the Context Builder** — Memory injection remains a separate concern. The Context Builder accepts `memory_context` as an opaque string; how Memory assembles that string is unchanged.
- **Multi-artifact sessions** — The State Tracker tracks one active artifact per session. Supporting multiple concurrent artifacts would require a selection/routing layer not designed here.
- **Cross-session compaction** — AutoCompact summaries are per-session. There is no mechanism for summarizing across sessions or carrying compact summaries into new sessions (that's Memory's job).
- **Provider-specific cache optimization** — The design is provider-agnostic by constraint. Anthropic cache edits, OpenAI cached prompts, or similar provider features are not leveraged. They could be added as optional accelerators without changing the architecture.
- **Artifact content deduplication** — If multiple versions share identical files, each version stores its own copy. Storage optimization is orthogonal to context management.

---

## Step 1: Artifact State Tracker

### Purpose

Know exactly what the model has seen vs what actually exists, so patches always target real content.

### Data Model

A new `ArtifactStateTracker` class, one instance per chat session, stored server-side (in-memory dict keyed by session ID, with Firestore persistence for recovery).

```
ArtifactState:
  artifact_id: str
  version_id: str
  files: dict[file_path → FileState]

FileState:
  content_hash: str        # SHA-256 of current content
  last_sent_hash: str|None # SHA-256 of what model last saw (None = never sent)
  byte_size: int
  language: str
```

### Core Operations

1. **`sync_from_firestore(artifact_id, version_id)`** — Load current artifact files, compute content hashes. Called when a session loads or after any artifact mutation.

2. **`get_dirty_files()`** → list of `(path, content)` where `content_hash != last_sent_hash`. These are the files the model needs to see.

3. **`mark_sent(file_paths)`** — After injecting files into the prompt, set `last_sent_hash = content_hash` for those paths. Now the model's view matches reality.

4. **`mark_mutated(file_path, new_content)`** — After a successful patch or new version, update `content_hash`. If `content_hash != last_sent_hash`, that file is dirty again (the model hasn't seen its own edit yet — this handles the case where a patch was applied but the model's next turn needs to know the result).

5. **`get_context_injection(max_bytes)`** — Returns the minimal set of file contents needed for the next prompt. Priority: dirty files first (scored by keyword overlap between user message tokens and file paths/languages, with recency as tiebreaker — no LLM call), then recently-patched files. Respects a byte budget so large artifacts don't blow the context window.

### Version Transition Handling

When `sync_from_firestore` runs after a new version, diff old vs new file hashes — only mark files as dirty if `content_hash` actually changed. Files that carried over unchanged keep their `last_sent_hash`. This avoids re-injecting unchanged files across version boundaries.

### Lifecycle

- Created when a session with an active artifact is loaded
- Lives in a `SESSION_STATES: dict[session_id, ArtifactState]` module-level cache
- Evicted after 30 minutes of inactivity (LRU)
- **Flush to Firestore before eviction** — ensures crash recovery and LRU eviction give the same guarantees
- Persisted to Firestore on mutation (just the hashes, not content) for crash recovery

### Integration Points

- **Before prompt build** (`chat_routes.py`): call `get_dirty_files()` to decide what to inject. Replace the current "load entire artifact" approach.
- **After patch apply** (`artifacts.py:apply_patches()`): call `mark_mutated()` for each patched file.
- **After new version create** (`ArtifactStore._append_version()`): call `sync_from_firestore()` to reset state with version-transition diffing.
- **Patch validation**: before calling `apply_patches()`, verify each `old_string` exists in the file at `content_hash`. If it doesn't match, trigger the patch retry mechanism (see Step 3).

---

## Step 2: Context Builder

### Purpose

Replace the current "last 20 messages + full artifact dump" with a smart context assembler that fits the most useful information into the token budget each turn.

### Architecture

```
ContextBuilder:
  token_budget: int          # Model's context window minus reserved output tokens
  reserved_system: int       # Tokens reserved for system prompt + instructions

  build(
    system_prompt: str,
    full_history: list[Message],
    artifact_state: ArtifactState | None,
    user_message: str,
    memory_context: str | None
  ) → BuiltContext
```

### Output

```
BuiltContext:
  system_prompt: str
  messages: list[dict]       # The assembled conversation for the API call
  artifact_files: list[dict] # Files injected (from State Tracker dirty check)
  token_estimate: int        # Estimated total tokens
  compression_level: str     # 'none' | 'microcompact' | 'summarized'
```

### The Assembly Algorithm (4 zones)

The prompt is assembled into fixed zones with priority-based budget allocation:

```
Zone 1: System prompt + instructions          [fixed cost, always included]
Zone 2: Artifact context (dirty files only)   [dynamic budget — see below]
Zone 3: Conversation history                  [fills remaining budget]
Zone 4: User's current message                [fixed cost, always included]
```

**Zone 2 budget derivation** (not a fixed constant):

```
zone3_minimum = estimate_tokens(last 5 turns verbatim)  # Tier 1 floor
zone2_budget = token_budget - zone1_cost - zone4_cost - zone3_minimum
```

This ensures a large artifact can never starve conversation history. The State Tracker's `get_context_injection(max_bytes)` receives this derived budget.

### Zone 3: Conversation History (3 tiers)

**Tier 1: Recent messages (last 5 turns)** — included verbatim, full content. These are what the model needs for conversational coherence.

**Tier 2: Metadata summary of older messages** — everything before Tier 1, compressed into a structured block:

```
## Conversation Summary (turns 1-15)
- Turn 1: User asked to build a Snake game in PyGame
- Turn 3: Created artifact "Snake Game" (v1, 3 files, python)
- Turn 5: User requested food spawning fix at score 30
- Turn 7: Patched snake_game.py (food logic) → v2
- Turn 8: Patch failed: old_string mismatch on line 42 [ERROR]
- Turn 10: User requested sound effects
- Turn 12: Created v3 with pygame.mixer sound system
- Turn 14: User reported truncation in code generation
```

Each summary line is extracted from message metadata at save time — not generated by an LLM. See "Metadata Extraction" below.

**Tier 3: Dropped** — if even the metadata summary exceeds budget (extremely long sessions), truncate from the oldest summary lines first.

### MicroCompact (applied to Tier 1)

Before including Tier 1 messages verbatim, strip heavyweight content from older ones in the window. MicroCompact applies to turns `[len-7 .. len-3]` (Tier 1 minus the last 2 turns). Turns `[len-2 .. len]` are **never** microcompacted — the model needs them intact.

Replacements:
- Code blocks in assistant responses longer than 50 lines → `[Code block: {language}, {line_count} lines — see artifact v{N}]`
- Search results → `[Search: {query}, {N} results]`
- File attachment content → `[Attached: {filename}, {size}]`

### Token Estimation

Use a simple heuristic: `len(text) / 3.5` for English-heavy content, `len(text) / 3` for code-heavy content. No tokenizer dependency — keeps it provider-agnostic. This is an estimate for budget allocation, not an exact count. Leave 10% headroom.

> **Note:** Dense minified JS or generated JSON can hit `len/2.5`. The 10% headroom should absorb this, but if context overruns appear in production, check code-density ratio before blaming the model.

### Metadata Extraction (at save time, not build time)

When `save_interaction()` is called in `chat_routes.py`, extract and store a `turn_metadata` dict alongside each message:

```
turn_metadata:
  turn_number: int
  user_intent_summary: str     # Strip code blocks first, then take first 80 chars
  action_type: str             # 'create_artifact' | 'patch_artifact' | 'chat'
  had_error: bool              # True if patch failure, parse error, API error
  error_detail: str | None     # Brief description when had_error is True
  artifact_mutation: {         # None if no artifact change
    artifact_id: str
    from_version: str | None
    to_version: str
    files_changed: list[str]
  }
  model: str                   # selected_model from response pipeline
```

`action_type` values align exactly with the `ACTION:` protocol from Step 3. The metadata extractor records the parsed ACTION value directly.

`user_intent_summary` extraction order: strip code blocks from user message first, then take first 80 chars of what remains. This handles the paste-then-ask pattern correctly.

Stored in the Firestore message entry so it's available on session reload without reprocessing.

### Integration

Replaces the current logic in `chat_routes.py`:

```python
# Before (current approach):
previous_messages = session.messages[-20:]

# After:
context = context_builder.build(
    system_prompt=system_prompt,
    full_history=session.messages,
    artifact_state=get_artifact_state(session_id),
    user_message=user_prompt,
    memory_context=memory_block
)
# context.messages → sent to the API
# context.artifact_files → what the State Tracker marked dirty
```

Both `/chat` and `/chat_stream` use the same `ContextBuilder.build()` call.

---

## Step 3: Decision-via-Prompt + ActionParser

### Purpose

Replace the fragile `<META>output_mode</META>` tag system with clear system prompt rules that make the model's output mode decision reliable and parseable across all providers.

### New Protocol: Single Action Header

Every model response must begin with a single mandatory first line:

```
ACTION: chat
ACTION: create_artifact
ACTION: patch_artifact
```

One line, one keyword. No JSON, no XML tags, no structured block to parse. Every provider can produce this reliably.

### System Prompt Decision Rules

Injected into the system prompt (replacing current META instructions):

```
## Response Format

Your FIRST LINE must be an ACTION declaration. Choose exactly one:

ACTION: chat
  Use when: answering questions, explaining concepts, discussing approaches,
  giving feedback, or when the user's request does not produce a runnable
  deliverable. Short code snippets (under 12 lines) that illustrate a point
  belong in chat, not an artifact.

ACTION: create_artifact
  Use when: the user asks you to BUILD, CREATE, or MAKE something that is a
  complete, runnable deliverable (app, game, tool, page, script). The result
  must be self-contained code that a user would want to run, preview, or
  download. Do NOT create an artifact for partial code, pseudocode, or
  explanations with code examples.

ACTION: patch_artifact
  Use when: the user asks you to FIX, UPDATE, CHANGE, MODIFY, or IMPROVE
  an existing artifact. You will receive the current file contents below.
  Respond with only the patches needed — do not regenerate unchanged files.

After the ACTION line, write your response following the format rules for
that action type.
```

### Per-Action Response Formats

**ACTION: chat**
```
ACTION: chat

Your normal markdown response here. Code blocks are fine for
illustration. No artifact fence.
```

**ACTION: create_artifact**
```
ACTION: create_artifact

Brief explanation of what you're building.

\`\`\`artifact
{"title": "...", "entrypoint": "...", "files": [...], "meta": {...}}
\`\`\`
```

**ACTION: patch_artifact**
```
ACTION: patch_artifact

Brief explanation of what you're changing.

\`\`\`artifact_patch
{"patches": [{"file": "main.py", "old_string": "...", "new_string": "..."}]}
\`\`\`
```

### ActionParser

A new lightweight parser replacing `ArtifactDecisionEngine` and the META extraction:

```
ActionParser.parse(response_text: str) → ParsedAction

ParsedAction:
  action: 'chat' | 'create_artifact' | 'patch_artifact'
  chat_text: str              # Everything except the artifact/patch fence
  artifact_payload: dict|None # Parsed artifact JSON (if create)
  patch_payload: dict|None    # Parsed patch JSON (if patch)
  raw_response: str           # Full response for fallback
```

**Parsing logic:**

1. Check if first non-empty line starts with `ACTION:`. Extract the keyword.
2. If `create_artifact`: extract `artifact` fence, parse JSON, validate via existing `normalize_artifact_payload()`.
3. If `patch_artifact`: extract `artifact_patch` fence, parse JSON.
4. Strip the ACTION line from `chat_text` — the user never sees it.

**Fallback when ACTION line is missing:**

1. If response contains `` ```artifact `` fence → treat as `create_artifact`
2. If response contains `` ```artifact_patch `` fence → treat as `patch_artifact`
3. Otherwise → treat as `chat`

This fallback is the same heuristic as today, but now the fallback path, not primary. Log when fallback triggers — include `selected_model` and the first 100 chars of the response for debugging which providers skip the ACTION line.

### Streaming ACTION Parse

Streaming chunks don't respect line boundaries. The first chunk might be `"ACT"`, the second `"ION: patch_"`, the third `"artifact\n"`. Implementation requires a small buffer:

1. Accumulate chunks into a buffer until the first `\n` is received.
2. Parse the ACTION line from the buffer.
3. After ACTION is parsed, stream remaining chunks normally to the client.

This is a one-time cost at turn start. Without explicit handling, intermittent parse failures will occur on fast streams.

### Conditional Artifact Injection

With `detect_artifact_update_intent()` removed, artifact context injection follows this rule:

- **Inject** when dirty files exist (State Tracker) OR when the last action was `patch_artifact`
- **Skip** on pure chat turns where nothing is dirty and no recent patch activity

The model can still choose `ACTION: patch_artifact` on a turn where context wasn't injected — if patch validation fails because the model doesn't have current content, the retry mechanism (below) handles it by injecting the actual files.

### Patch Retry Mechanism

When `ACTION: patch_artifact` is returned but `old_string` doesn't match the actual file content:

1. **Validation**: Before calling `apply_patches()`, check each `old_string` against the file content from the State Tracker. If any don't match, the patch is rejected.

2. **Retry**: Make one additional API call in the same request cycle. Inject the actual file content with an explicit correction prompt:

   ```
   Your patch for {file_path} failed: the old_string you provided does not
   exist in the current file. Here is the actual current content of the file:

   ```{language}
   {actual_content}
   ```

   Please provide a corrected artifact_patch using the exact content above.
   ```

3. **Bounded**: Maximum 1 retry. If the second attempt also fails, surface the error to the user: *"I tried to update {file_path} but couldn't match the code. Here's what I was trying to change: {explanation}"*. The `chat_text` from the model's response is still shown — the user sees the intent even if the patch failed.

4. **Streaming UX**: During streaming, the retry is transparent — the user sees a brief "Verifying patch..." indicator between the first response ending and the retry starting. The retry response streams normally.

5. **Cost**: One extra API call, but only on patch failure. The injected file content is bounded by `get_context_injection(max_bytes)`, so it won't blow the budget.

### What Gets Removed

- `<META>` block parsing and generation instructions
- `ArtifactDecisionEngine` class
- `detect_artifact_update_intent()` function
- Heuristic code-detection fallback as primary path (kept only as fallback)

### Integration

- `chat_routes.py`: Remove `detect_artifact_update_intent()` call. After receiving model response, run `ActionParser.parse()` instead of `extract_artifact_block()` + `ArtifactDecisionEngine`.
- `chat_routes.py` (streaming): Buffer first line, parse ACTION, route artifact/patch extraction to same `ActionParser`.
- `artifacts.py`: `ArtifactRouter.route()` is replaced by `ActionParser.parse()`. `ArtifactDecisionEngine` is deprecated.
- `chat_engine.py`: `build_prompt()` no longer needs META format instructions. Replace with ACTION format rules.

---

## Step 4: AutoCompact (Future Upgrade Path)

**Status:** Design specification only — ships when real 50+ turn sessions become common.

### Purpose

Replace metadata-based Tier 2 summaries with LLM-generated summaries for higher semantic quality in very long sessions.

### Where It Slots In

```
Current (Steps 1-3):
  Tier 2 = metadata_summary(messages[0..len-6])

Future (with AutoCompact):
  Tier 2 = autocompact_summary ?? metadata_summary(messages[0..len-6])
```

If an AutoCompact summary exists for this session, use it. If not, fall back to metadata summary. The Context Builder doesn't care which produced the text — it just receives a string for the Tier 2 slot.

### Trigger

AutoCompact fires when the Context Builder detects that the metadata summary alone can't fit within the Tier 2 budget:

```
tier2_budget = token_budget - zone1 - zone2 - zone4 - tier1_cost
metadata_summary_cost = estimate_tokens(metadata_summary)

if metadata_summary_cost > tier2_budget:
    trigger autocompact
```

Demand-driven, not turn-count-driven. A 50-turn conversation with small messages might never trigger it. A 20-turn conversation with huge code blocks might.

### The Compact Call

A single LLM call with a structured prompt. Uses the same model the user selected for the session (provider-agnostic). Cheapest available model variant as a hint if the provider exposes one — fall back to the session's selected model if not. The hint is best-effort, never a hard requirement.

**Prompt:**

```
Summarize this conversation for continuity. Focus on:
1. What the user is building (project goal, current state)
2. Artifact history (versions created, files changed, what each version changed)
3. Errors encountered and their resolutions
4. Decisions made (why approach X was chosen over Y)
5. Current state (what works, what's broken, what's next)

Do NOT summarize chat pleasantries or repeated attempts.
Keep the summary under {tier2_budget_chars} characters.

Conversation:
{full_history_as_text}
```

**Output:** An opaque string injected as a labeled block:

```
## Session Summary (compacted)
{compact_summary_text}
```

The Context Builder treats this identically to the metadata summary — it's just a string in the Tier 2 slot.

### Storage

```
Firestore: users/{uid}/chat_sessions/{sessionId}/
  compact_summary: str|None         # The LLM-generated summary
  compact_boundary_turn: int|None   # = len(messages) - tier1_size at compaction time
  compact_in_progress: bool         # Prevents double-triggering
  compact_generated_at: timestamp
```

After compaction, messages before `compact_boundary_turn` are still in Firestore (available for metadata extraction) but won't be loaded into the prompt. The Context Builder reads:

- `compact_summary` for Tier 2 (turns 1 through boundary)
- Metadata summary for turns between boundary and Tier 1 start (if any gap)
- Tier 1 verbatim for last 5 turns

### Async Execution

The compact call runs **after** the response is sent to the user, not blocking the current turn. The next turn benefits from the summary.

**`compact_in_progress` guard:** If the next turn arrives before the compact call finishes, it sees `compact_in_progress = True` and falls back to metadata summary gracefully rather than double-triggering a second compact.

### Cost Control

- Max 1 compact per session per 20 turns — don't re-compact every turn once triggered
- `compact_boundary_turn = len(messages) - tier1_size` at the moment compact fires — ensures no turns fall between boundary and Tier 1 start
- Async execution avoids adding latency to user-facing responses

### What Doesn't Change

- Context Builder API: `build()` signature stays the same
- Tier 1 and Tier 3 logic: untouched
- MicroCompact: still runs on Tier 1 messages
- Artifact State Tracker: still handles Zone 2
- ActionParser: still handles response parsing

The only code changes when enabling AutoCompact:
1. Add the trigger check inside `ContextBuilder.build()`
2. Add the compact call + Firestore persistence
3. Add the Tier 2 source selection (`compact_summary ?? metadata_summary`)
