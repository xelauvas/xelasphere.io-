# Chat-shell tokens and scrollbar — design

**Date:** 2026-05-02
**Branch:** `polish/streaming-text-rendering` (continuation, sub-project 1 of the chat-shell rewrite)
**Scope:** Width-token consolidation + chat container architecture cleanup + canonical scrollbar story for the chat messages container. First of six sub-projects in the chat-shell rewrite. Targets audit findings #1, #2, #3, #6, #7 (partial), #10. Deliberately defers composer internals, message bubble architecture, right-side panel logic, and z-index scale to sub-projects 2–6.

## Problem

A deep audit of the chat shell (15 findings, three tiers) surfaced a tangle of conflicting widths, dead scrollbar code, and accidentally-correct macro layout:

- **Four** width tokens (`--content-max-width`, `--composer-max-width`, `--composer-shell-max-width`, `--chat-shell-max-width`) all serve the same conceptual width. Effective rendered width is the smallest container, but four ceilings means changing one breaks another. No single source of truth.
- **Composer pill is 32px wider than the message column.** `.input-container` reads `--composer-shell-max-width: 800px`, message bubbles read `--composer-max-width: 768px`. Visible misalignment between the pill and messages above it. (Audit #3)
- **Two competing scrollbar styles** for `.chat-shell__messages` in `styles.css`: lines 205-232 declare a visible 12px gradient thumb; lines 4761-4780 set `display: none`. Cascade order makes the hidden version win — the visible block is dead code. (Audit #1)
- **`body::-webkit-scrollbar` rules are dead** — `body { overflow: hidden }` ensures the body scrollbar never paints. (Audit #2)
- **`min(100%, var(--composer-max-width, 760px))` fallbacks lie** — token is 768, fallback claims 760. (Audit #10)
- **`--chat-stage-max-width: 1200px` outer cap** is unique to xelasphere — Claude.ai and ChatGPT have no such cap. On wide monitors this creates a visible "frame" the peer products don't have.
- **`--panel-width-default: 480px` is a redundant fallback** in 4 places; the actual artifact panel width is `50vw` driven by JS during resize.

User-visible result: dimensions roughly match Claude/ChatGPT (sidebar 260, content 768, composer 768) but only by accident — four tokens stack and clip to ~768px. The composer-message misalignment is the only Tier-1 visible bug. The other findings are silent debt that compounds when anyone tries to tune dimensions.

The user's stated goal: match Claude / ChatGPT structurally, not just numerically. Brainstorm chose **Approach 2 (Claude-leaning)** — keep 768px content, drop the 1200 outer cap, use one token per concept.

## Decision

**Width-token consolidation + scrollbar canonicalization in a single atomic PR.** No alias / compat layer; consumers update in the same commit as the token deletion. Out-of-scope sub-projects (composer internals, message bubble width, right-side panel positioning, z-index scale) are firewalled — audit findings that surface during implementation get filed as follow-ups, not fixed inline.

Rejected alternatives:

- **Alias-then-clean-up.** Add new tokens, alias old ones to new (`var(--old, var(--new))`), migrate consumers across multiple PRs, delete old tokens later. Higher safety in theory; in practice the alias layer becomes tomorrow's audit finding ("why are there 8 tokens, why does --old still exist") — and consumer count is small enough (~15 rules) for an atomic pass.
- **Single big-bang chat-shell rewrite (Option C as initially scoped).** All six sub-projects in one PR. Unreviewable diff, conflicts compound, can't be staged. User explicitly chose decomposition.
- **Surgical fix only (Option A).** Just align composer to messages. Ships the visible bug fix but leaves the four-token sprawl that caused it. The next dimension change re-creates the misalignment.

## Architecture

### Token system

Two new tokens replace seven existing tokens. Live in `app/static/css/styles.css :root` (where the existing tokens already live — avoids splitting layout tokens across files).

```
--chat-content-width: 768px         /* width cap for header / messages / composer / extras */
--chat-right-panel-width: 50vw      /* canonical right-panel-when-open width */
```

**Replaces:**
- `--content-max-width: 768px`
- `--composer-max-width: var(--content-max-width)` (alias, removed with parent)
- `--composer-shell-max-width: 800px` ← the 32px-misalignment culprit
- `--chat-shell-max-width: var(--chat-stage-max-width)` (alias)
- `--chat-stage-max-width: 1200px` ← the outer-cap kill
- `--panel-width-default: 480px` (artifact-panel.css fallback)
- (Note: `--artifact-panel-width: 50vw` stays — set dynamically by JS during resize. The new `--chat-right-panel-width` is the canonical reference; `--artifact-panel-width` is the live value.)

**Stays untouched (deferred to other sub-projects):**
- `--sidebar-width`, `--left-sidebar-width`, `--right-sidebar-width`, `--sidebar-collapsed-width` → sub-project 5
- `--composer-radius`, `--composer-padding-x`, `--composer-padding-y`, `--composer-locked-height`, etc. → sub-project 2
- `--artifact-panel-width` (dynamic JS-driven value) → sub-project 5

### Layout architecture (DOM hierarchy)

The DOM does not change. Width caps move from `--composer-shell-max-width` (800px) to `--chat-content-width` (768px) on each top-level child of `.chat-shell` and on `.conversation-panel`.

```
<div class="chat-shell">                          width: 100% (no max)
├ <header class="chat-shell__header">             max-width: var(--chat-content-width) /* 768 */
├ <div class="chat-shell__messages" id="chat-scroll-region">   width: 100%, scrolls
│  └ <div class="conversation-panel">             max-width: var(--chat-content-width) /* 768 */
│     └ <div id="chat-display"> ... messages ...
├ <div class="chat-shell__extras">                children: max-width: var(--chat-content-width)
└ <footer class="chat-shell__composer">           position: fixed, calc width
   └ <div class="input-container">                max-width: var(--chat-content-width) /* 768 */
      └ <div class="input-row">                   max-width: 100% (parent caps)
```

**Width-cap pattern:** distributed (current behavior preserved). Each top-level child of `.chat-shell` sets its own max-width to keep the composer's `position: fixed` independent. Centralizing to a single wrapper would force a DOM restructure — out of scope.

**Composer width formula** in `styles.css:4868` is unchanged in shape; only token names change:

```css
.chat-shell__composer {
    position: fixed;
    left: var(--left-sidebar-width, 0px);
    width: calc(100% - var(--left-sidebar-width, 0px) - var(--active-right-sidebar-width, 0px));
}
```

`--active-right-sidebar-width` stays as the abstraction — resolves via body class (`right-sidebar-open`, `artifact-panel-open`) to the live right-panel width. Cleaning up the right-panel logic itself is sub-project 5.

### Outer stage cap removal

`--chat-stage-max-width: 1200px` is deleted, along with its alias `--chat-shell-max-width`. The single rule that consumed it (`.chat-shell__messages > * { max-width: var(--chat-shell-max-width) }` at `styles.css:4782-4787`) is also deleted — it's redundant with `.conversation-panel`'s own 768px cap.

**On a 1920px viewport with sidebar collapsed:** content centers at 768px in the full viewport, exactly like Claude.ai and ChatGPT. No 1200px frame.

**On a 1280px viewport:** behavior unchanged — content already capped at 768px below the old 1200 ceiling.

### Scrollbar story — canonical

**Body never scrolls.** `body { overflow: hidden }` stays. The Romanian comment (`/* DOAR #chat-scroll-region scrollează */`) is translated to English while we're in the file.

**The single canonical scrollable container** is `.chat-shell__messages` (= `#chat-scroll-region`). All other apparent scroll surfaces (artifact panel, modals, code blocks) are scoped to their own components and unaffected by this spec.

**Desktop: hover-only scrollbar (Option B).** Thin 8px scrollbar, transparent at rest, fades to `rgba(255,255,255,0.18)` on container hover, `0.32` on thumb hover. Light-theme variant uses `rgba(40,30,15,...)`. Replaces `styles.css:4761-4780`:

```css
.chat-shell__messages,
#chat-scroll-region {
    flex: 1 1 auto;
    width: 100%;
    display: flex;
    flex-direction: column;
    min-height: 0;
    overflow-y: auto;
    overflow-x: hidden;
    overflow-anchor: none;
    scrollbar-width: thin;
    scrollbar-color: transparent transparent;
    transition: scrollbar-color 200ms ease;
}

.chat-shell__messages:hover,
#chat-scroll-region:hover {
    scrollbar-color: rgba(255, 255, 255, 0.18) transparent;
}

.chat-shell__messages::-webkit-scrollbar,
#chat-scroll-region::-webkit-scrollbar {
    width: 8px;
}
.chat-shell__messages::-webkit-scrollbar-track,
#chat-scroll-region::-webkit-scrollbar-track {
    background: transparent;
}
.chat-shell__messages::-webkit-scrollbar-thumb,
#chat-scroll-region::-webkit-scrollbar-thumb {
    background: transparent;
    border-radius: 999px;
    transition: background 200ms ease;
}
.chat-shell__messages:hover::-webkit-scrollbar-thumb,
#chat-scroll-region:hover::-webkit-scrollbar-thumb {
    background: rgba(255, 255, 255, 0.18);
}
.chat-shell__messages::-webkit-scrollbar-thumb:hover,
#chat-scroll-region::-webkit-scrollbar-thumb:hover {
    background: rgba(255, 255, 255, 0.32);
}

[data-theme="light"] .chat-shell__messages:hover,
[data-theme="light"] #chat-scroll-region:hover {
    scrollbar-color: rgba(40, 30, 15, 0.20) transparent;
}
[data-theme="light"] .chat-shell__messages:hover::-webkit-scrollbar-thumb,
[data-theme="light"] #chat-scroll-region:hover::-webkit-scrollbar-thumb {
    background: rgba(40, 30, 15, 0.20);
}
```

**Mobile: scrollbar stays hidden** (`mobile.css:1635-1664`). The `:hover` pseudo doesn't reliably fire on touch devices; native overlay scrollbars on iOS/Android handle scroll-position cues. mobile.css's existing rules are correct and untouched.

**Dead code deleted:** `styles.css:205-232` — the 12px gradient block that's been overridden since the day it was written. Both the body half (always dead because `overflow: hidden`) and the messages half (overridden by the 4761-4780 block being replaced).

## Migration order

Atomic PR. The order below is for the agent / human implementer to follow within that PR; not separate commits.

1. **Add new tokens** in `styles.css :root`:
   ```css
   --chat-content-width: 768px;
   --chat-right-panel-width: 50vw;
   ```
   Old tokens still present at this stage.
2. **Migrate consumers** across all CSS files via find/replace:
   - `--composer-shell-max-width` → `--chat-content-width`
   - `--composer-max-width` → `--chat-content-width` (and update lying fallbacks `760px` → `768px`)
   - `--content-max-width` → `--chat-content-width`
   - `--chat-shell-max-width` → `--chat-content-width` (only the one consumer; gets deleted next step anyway)
   - `var(--artifact-panel-width, var(--panel-width-default, 480px))` → `var(--artifact-panel-width, var(--chat-right-panel-width))` (4 places in artifact-panel.css). Wires `--chat-right-panel-width` as the canonical fallback when JS hasn't set the live `--artifact-panel-width` value yet.
3. **Delete old tokens** from `styles.css :root`:
   - `--content-max-width`
   - `--composer-max-width`
   - `--composer-shell-max-width`
   - `--chat-shell-max-width`
   - `--chat-stage-max-width`
   - `--panel-width-default`
4. **Delete the rule** `.chat-shell__messages > * { ... max-width: var(--chat-shell-max-width); ... }` at `styles.css:4782-4787`. The `.conversation-panel` cap is sufficient.
5. **Replace `styles.css:4761-4780`** with the hover-only scrollbar block above.
6. **Delete `styles.css:205-232`** (the dead body+messages 12px gradient block). Verify by inspection that no selector outside this block depends on the rules.
7. **Translate Romanian comments to English** in the touched sections only:
   - `/* DOAR #chat-scroll-region scrollează */` → `/* Only #chat-scroll-region scrolls */`
   - `/* FĂRĂ max-width aici - lasă scroll container să fie full-width */` → `/* No max-width here — let scroll container be full-width */`
   - `/* Centrat ca și .conversation-panel */` → `/* Centered like .conversation-panel */`
   - `/* SINGURUL container care scrollează */` → `/* The ONLY container that scrolls */`
   - `/* Spațiu pentru composer */` → `/* Space for the composer */`
   - `/* Inner wrapper - CENTRARE + MAX-WIDTH aici */` → `/* Inner wrapper — centering + max-width here */`
   - `/* Gap mutat pe .conversation-panel */` → `/* Gap moved to .conversation-panel */`
   - `/* Full width pentru copii */` → `/* Full width for children */`
   - `/* SCROLL AICI */` → `/* Scroll here */`
   - `/* Mobile chat-stage styles moved to mobile.css */` (already English, leave)
   - Translate only what's in touched files near touched lines; do not sweep the whole codebase.

## Files touched

| File | Change |
|---|---|
| `app/static/css/styles.css` | Token additions in `:root`; 6 token deletions; ~10 consumer renames; replace 4761-4780; delete 205-232; delete 4782-4787; comment translations. |
| `app/static/css/message-styles.css` | 4 token references (lines 265, 266, 294, 295): `--composer-max-width` → `--chat-content-width`, fallback `760px` → `768px`. |
| `app/static/css/email-composer.css` | 2 token references (lines 45, 46): `--content-max-width` → `--chat-content-width`. Comment at line 43 mentions tokens, update if it references obsolete names. |
| `app/static/css/crypto-chart.css` | 2 token references (lines 255, 256): same as email-composer. |
| `app/static/css/stream-blocks.css` | 1 comment (line 242 mentions `--composer-max-width`); update name reference. |
| `app/static/css/artifact-panel.css` | 4 fallbacks (lines 67, 75, 99, 104): `var(--panel-width-default, 480px)` → `50vw` literal. |
| `app/static/css/mobile.css` | Verify no tokens being renamed are referenced; expected: none. Scrollbar block at 1635-1664 left alone. |
| `app/static/css/text-selection-quote.css` | Untouched. Uses `--composer-radius` which is deferred to sub-project 2. |

Estimated diff: ~30 line edits + 2 block removals + 1 block addition. Single PR sized for one reviewer pass.

## Verification

Visual + DevTools checks at three desktop breakpoints and one mobile:

| Scenario | Expected outcome |
|---|---|
| 1440px viewport, sidebar open, no panel | composer pill width = message column width = 768px (was: composer 800, messages 768) |
| 1920px viewport, sidebar collapsed, no panel | content centered at 768px in full viewport — no 1200 frame visible |
| 1440px viewport, sidebar open, artifact panel open | composer ≤ messages in their grid half; both scale together; no 32px gap |
| Hover over `.chat-shell__messages` | 8px scrollbar fades in over 200ms (Firefox: thin scrollbar matches color) |
| Mouse leaves `.chat-shell__messages` | scrollbar fades out over 200ms |
| Hover thumb directly | thumb darkens (0.18 → 0.32 opacity) |
| Light theme + hover | scrollbar uses `rgba(40,30,15,...)` tone, not white |
| Mobile (≤768px viewport) | no scrollbar visible (mobile.css existing rules) |

DevTools assertions in the implementation plan: computed widths via `getBoundingClientRect()` should report 768 ±0.5 for the composer pill (input-row at 100% of 768 input-container) and the conversation-panel.

## Out of scope

This spec deliberately does **NOT** address the following audit findings. Each is its own sub-project. If audit-trace work during implementation surfaces issues in any of these areas, file a follow-up — do not fix inline.

- **Audit #4** — composer radius 26px hardcoded vs `--composer-radius: 24px` token mismatch → sub-project 2
- **Audit #5** — user message bubble width `520px` hardcoded → sub-project 4
- **Audit #7 (partial)** — `--right-sidebar-width: 320px` legacy path; `--artifact-panel-width: 50vw` still managed by JS → sub-project 5
- **Audit #8** — artifact-panel position-fixed-vs-grid double-positioning + body margin double-counting → sub-project 5
- **Audit #9** — `--composer-padding-x` / `-y` tokens dead vs hardcoded `padding: 12px 16px` → sub-project 2
- **Audit #11** — 30+ ad-hoc z-index values, no scale → sub-project 6
- **Audit #12** — `pointer-events: none` footgun on `.chat-shell__composer` → not addressed in this rewrite
- **Audit #13** — `.input-row::after` U-mask `-50px` overshoot → sub-project 2
- **Audit #14** — redundant `!important` in `chat-shell.css` → sub-project 2 (along with composer rebuild)
- **Audit #15** — mobile composer parallel rules → revisit during sub-project 2 or its own sub-project

The full audit findings list (15 items, three tiers) was generated during the brainstorm session and is captured in this spec's *Problem* and *Out of scope* sections. The brainstorm session HTML in `.superpowers/brainstorm/` carries the visual presentation; this spec carries the canonical text.

## Risks

- **Consumer outside `app/static/css/` references a deleted token.** Mitigation: grep for each old token name in `app/templates/`, `app/static/js/`, and project root before deletion. Expected: tokens are CSS-only, not referenced from JS or HTML.
- **Light-theme scrollbar contrast looks wrong.** The `rgba(40,30,15, ...)` color is a guess based on existing light-theme tokens; verify visually. Acceptable adjustment within this spec without re-brainstorm.
- **Mobile scrollbar hidden via desktop-rule cascade leak.** The new desktop hover-only block is wrapped in `:hover` pseudos that won't fire on mobile, AND mobile.css's `display: none` block is later in the cascade. Both protections should hold; verify.
- **`pointer-events: none` footgun (audit #12) is not addressed.** Any new interactive child added to `.chat-shell__composer` after this PR ships still needs explicit `pointer-events: auto`. Acceptable risk — this spec doesn't add new children.
