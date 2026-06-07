# Context-Appropriate Formatting & Emoji Policy (Layer 1) — Design Spec

Date: 2026-06-07
Status: Approved (brainstorming) — pending implementation plan
Area: Prompt layer — `app/services/response_style.py` (style charters),
`app/services/chat_engine.py` (developer instructions), `app/blueprints/chat_routes.py`
(prompt assembly + setting resolution), user preferences.

## Why this replaces the renderer approach

ChatGPT's "look" is two layers:

- **Layer 1 — the model emits context-appropriate markdown.** Short answer → prose,
  no headings; long structured answer → `##` sections and lists; emoji used with
  restraint. This is steered by the *system prompt*, not the renderer.
- **Layer 2 — the renderer paints that markdown beautifully** (CSS vertical rhythm,
  edge-margin resets, capped headings, tolerant streaming).

An audit of this codebase found **Layer 2 is already at ChatGPT quality**:
`message-styles.css` has an explicit "ChatGPT polish" section (edge resets, capped
heading tokens, 600-weight `strong`, tight `li > p`); the streaming **block state
machine** keeps a code block open until a real closing fence (no raw-markdown
flicker); prose renders via `marked → DOMPurify → morphdom` and highlights only on
finalize (no per-token jank). The earlier renderer spec (density tokens, auto-structure,
collapsible code) was therefore overbuilding a layer that is not broken.

**The real symptom is Layer 1**, specifically: the `MODERN_BLOG` style charter — which
`classify_response_style` is told to *"prefer when uncertain"* (the effective default)
— instructs the model to put a **decorative topic emoji on every `##`/`###` heading and
every bullet** (`## ⏰ …`, `### 🎯 …`, `- 📊 …`). Over-emoji is the single biggest tell
of a cheap chatbot. The other five charters are already restrained
(technical: only ⚠️✅❌; chat: ≤1; comparison/editorial/creative: none). There is also
**no global emoji policy** and no "mirror the user" rule.

This spec fixes Layer 1 only. **No renderer, CSS, or streaming changes.**

## Goals

- Output formatting matches context (the existing length/structure rules already do
  much of this; we add emoji restraint as the missing piece).
- Emoji default to **none**, governed by one authoritative policy that overrides any
  per-style emoji guidance.
- A single per-tenant/per-user setting `emoji_policy ∈ {off, minimal, mirror}` selects
  the policy, so a luxury-hotel deployment can run zero emoji and a casual brand can
  allow a light touch.
- De-decorate `MODERN_BLOG` so the default style stops sprinkling emoji.

## Non-Goals (YAGNI)

- No changes to the renderer, CSS, or streaming pipeline (already ChatGPT-grade).
- No auto-structure / density-token / collapsible-code work (the previous spec — dropped).
- No new response-style categories; no model swap.
- No three full duplicated prompts — one base prompt with a swappable emoji paragraph.

## Current State (as built)

- `response_style.py` holds `STYLE_CHARTERS: dict[ResponseStyle, str]` (the per-style
  prompt blocks) and `classify_response_style()`.
- `chat_routes.py:1244-1247`: classifies the style, then
  `base_system = base_system + "\n\n" + charter`. `base_system` is the base developer
  instructions from `chat_engine.get_developer_instructions()`.
- `MODERN_BLOG` charter mandates decorative emoji on headings/bullets (the bug).
- Base developer instructions have good restraint rules ("lead with the answer",
  "match length to complexity", "skip filler") but **no emoji rule**.
- User preferences live in Firestore `users/{uid}` under a `preferences` map; helper
  `fetch_user_preferences(user_id)` already exists (`pages_routes.py:69`).

## Design

### 1. Emoji policy resolution (one setting, three variants)

A new module-level helper, `emoji_policy_block(policy: str) -> str`, in
`response_style.py` (next to the charters), returns one of three paragraphs. The
setting is resolved per request:

1. Per-user override: `preferences.emoji_policy` (if set to a valid value).
2. Else the deployment default: `EMOJI_POLICY` env var / config (default `"minimal"`).
3. Invalid/missing → `"minimal"`.

`"off"` → emoji never appear (luxury/formal tenants). `"minimal"` → functional markers
only. `"mirror"` → mirror the user.

The three variants (exact prompt text):

```
# off
EMOJI POLICY (authoritative — overrides any emoji guidance in the style section)
- Do not use emoji anywhere: not in headings, lists, callouts, or prose. Plain text only.

# minimal  (default)
EMOJI POLICY (authoritative — overrides any emoji guidance in the style section)
- Default to no emoji. Emoji are a rare exception, never a default flourish.
- The ONLY allowed use is a single functional status/structure marker in a list or
  callout where it genuinely aids scanning: ✅ done, ⚠️ caution, ❌ avoid, 📌 note.
- Never use emoji in: code, error or failure messages, formal/business writing, or any
  sensitive topic (complaints, money, bad news).
- Never put decorative emoji in headings or sprinkle them through prose.
- Cap: at most one emoji in the whole message. Prefer the plainest common marker.

# mirror
EMOJI POLICY (authoritative — overrides any emoji guidance in the style section)
- Default to no emoji. Mirror the user: use emoji only if the user used them first,
  match their level, and stop when they stop.
- When mirroring, allowed: functional markers (✅ ⚠️ ❌ 📌) in lists/callouts, and light
  affective emoji (🙂 👍 🎉) only when the tone is already casual and positive.
- Never use emoji in: code, error/failure messages, formal/business writing, or any
  sensitive topic (complaints, money, bad news).
- Cap: at most one emoji per message; never multiple per sentence.
```

### 2. Injection point — appended LAST, after the charter

In `chat_routes.py` (both the stream path ~1247 and the parallel path ~3154), after the
charter is appended, append the resolved emoji policy block **last** so it governs:

```python
base_system = base_system + "\n\n" + charter
base_system = base_system + "\n\n" + emoji_policy_block(resolved_policy)
```

Appending last + the "authoritative — overrides the style section" header guarantees
`off` wins even over a charter that allows markers. Wrapped in the existing try/except
so a failure degrades to no emoji block (and the base prompt's existing restraint).

### 3. De-decorate the MODERN_BLOG charter

Rewrite `STYLE_CHARTERS[MODERN_BLOG]` to keep the scannable structure (one-line hook,
`##`/`###` headings, bullets, **bold** key terms, a `> **Bottom line:**` blockquote
callout) but **remove every mandated topic emoji** from headings and bullets, and
replace the worked example with an emoji-free one of the same density. The other five
charters are already restrained; leave them, except delete any now-redundant per-style
emoji lines that the global policy covers (technical keeps its functional ⚠️✅❌ note
since `minimal` permits exactly those).

### 4. Setting storage & surface

- **Read:** resolve `emoji_policy` during prompt assembly (per §1). Reuse
  `fetch_user_preferences(user_id)`; read `EMOJI_POLICY` default from config/env.
- **Write (per-user):** add `emoji_policy` to the personalization settings the user can
  set (the existing `preferences` map + the personalization settings UI). A simple
  three-option control: Off / Minimal / Mirror.
- **Tenant default:** the `EMOJI_POLICY` env var is the deployment ("tenant") default —
  one value per brand. Per-user override wins when present.

## Data flow

```
request → classify_response_style(message) → charter
        → resolve emoji_policy (user pref → EMOJI_POLICY env → "minimal")
        → base_system + charter + emoji_policy_block(policy)   [policy appended LAST]
        → LLM → restrained, context-appropriate markdown → existing renderer (unchanged)
```

## Error handling

- Invalid/missing `emoji_policy` → `"minimal"`.
- `emoji_policy_block` / preference fetch wrapped in try/except; on failure the charter
  still applies and the base restraint rules hold (no emoji block appended).
- No renderer paths touched, so no rendering regressions are possible from this change.

## Testing

- **Unit (prompt assembly):** for each policy value, assert the correct paragraph is
  appended and appears AFTER the charter; assert an invalid value falls back to minimal;
  assert per-user pref overrides the env default.
- **Charter audit test:** assert `STYLE_CHARTERS[MODERN_BLOG]` contains no emoji
  characters in heading/bullet template lines (regex over the charter string).
- **Behavioural spot-check (manual, non-deterministic):** run a handful of prompts
  (a greeting, a how-to, a code question, a "complaint" message) under each policy and
  confirm: greeting→prose, how-to→headings/lists without decorative emoji, sensitive
  message→zero emoji, `off`→zero emoji everywhere.

## Rollout

Ship with `EMOJI_POLICY` defaulting to `minimal` (matches XelaSphere's
business-facing posture). Deploy to `/opt/xelasphere` (Python change → `systemctl
restart`, since prompt assembly runs in-process), sync to the dev repo. The per-user
settings control can land in the same change or a fast follow. Reverting is a one-line
default change; no data migration.
