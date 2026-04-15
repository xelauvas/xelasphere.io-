# Theme System Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Convert ~2,500 hardcoded color values across 30 CSS files to semantic theme tokens so clicking the owl toggle produces a complete, smooth light/dark transition with every pixel covered.

**Architecture:** Single source of truth in `design-tokens.css` (dark in `:root`, light in `[data-theme="light"]`). Every hardcoded color in app CSS becomes `var(--token, #fallback)`. The existing `themeManager.js` toggles `data-theme` on `<html>` — no JS changes needed. Hybrid rule: `.terminal` stays dark in both themes; code blocks flip.

**Tech Stack:** Plain CSS custom properties; `themeManager.js` (already present); Flask/Jinja templates unchanged; grep for verification.

**Spec:** `docs/superpowers/specs/2026-04-13-theme-system-refactor-design.md`

---

## Token Mapping Reference

**Used in every file task.** When refactoring a color, pick the token whose role matches:

| Hardcoded pattern | Token |
| --- | --- |
| Page / chat canvas background (`#0f0f0f`, `#0f0f12`, `#121212`, `#0a0a0a`) | `var(--bg-primary, #0f0f12)` |
| Sidebar / panel background (`#1a1a1a`, `#1a1a22`, `#1e1e1e`) | `var(--bg-secondary, #1a1a22)` |
| Card / nested surface (`#222`, `#222222`, `#22222e`, `#26262e`) | `var(--bg-tertiary, #22222e)` |
| Modal / dropdown / popup (`#2a2a2a`, `#2b2b2b`, `#2d2d2d`) | `var(--bg-elevated, #2a2a38)` |
| Input field background (`#1e1e2a`, `#1f1f1f`, `#2d2d2d`) | `var(--bg-input, #1e1e2a)` |
| Code block background (`#13131a`, `#0d0d0d`) | `var(--bg-code, #13131a)` |
| Hover tint `rgba(255,255,255,0.04-0.08)` | `var(--bg-hover, rgba(255,255,255,0.05))` |
| Body text (`#e8e8f0`, `#f3f3f3`, `#e6e6e6`, `#ffffff` as text) | `var(--text-primary, #e8e8f0)` |
| Secondary text / labels (`#b3b3b3`, `#9898b0`, `#999`) | `var(--text-secondary, #9898b0)` |
| Tertiary text / placeholders (`#5a5a70`, `#666`, `#777`) | `var(--text-tertiary, #5a5a70)` |
| Default border (`rgba(255,255,255,0.08-0.14)`, `#2a2a2a` as border) | `var(--border-primary, rgba(255,255,255,0.08))` |
| Subtle divider (`rgba(255,255,255,0.04-0.06)`) | `var(--border-secondary, rgba(255,255,255,0.04))` |
| Accent purple (`#7c6aef`, `#5b46d6`) | `var(--accent, #7c6aef)` |
| Accent hover (`#9580f5`, lighter purple) | `var(--accent-hover, #9580f5)` |
| Translucent accent (`rgba(124,106,239,0.x)`) | `var(--accent-muted, rgba(124,106,239,0.15))` |

**DO NOT convert:**
- SVG illustrations with hardcoded hex fills inside templates
- `.terminal` and its children — leave as literal `#0f0f12` / `#e8e8f0`
- Prism syntax highlighting palette
- Decorative effects (owl SVG, ghost SVG inline gradients)

**Per-file pattern:**
1. Grep hardcoded colors in the file
2. For each occurrence, decide role (bg, text, border, accent, state) and replace with matching token
3. Keep the original hex as the `var()` fallback
4. Re-run grep; remaining hardcoded values should be only intended exceptions

---

## Task 0: Pre-Phase-1 Audit

**Files:**
- Read-only: `app/static/css/mobile.css`, `app/static/js/artifactPanel.js`, `app/static/js/codeWindow.js`, `app/static/js/chartBuilder.js`

- [ ] **Step 1: Audit mobile.css for hardcoded color overrides that could bleed**

```bash
grep -nE "background|(^|[^-])color\s*:" app/static/css/mobile.css | grep -E "#|rgb" | head -50
```

Expected: list of ~20-50 hardcoded color rules in mobile.css. Note any that set backgrounds explicitly (these could block visual verification in later phases).

- [ ] **Step 2: Audit JS for inline style color mutations**

```bash
grep -rnE "\.style\.(background|color|borderColor)\s*=" app/static/js/ | head -40
```

Expected: handful of matches. Record file:line for each.

For each match that sets a hardcoded color:
- Preferred fix: replace with `el.classList.add('some-state')` + CSS-defined state
- Acceptable fix: `el.style.setProperty('--local-token', 'value')` or replace the value with `var(--token)` string

This audit informs Phase 3 execution. No code changes in this task — just catalog findings.

- [ ] **Step 3: Establish the baseline missed-color count**

```bash
grep -rE "(#[0-9a-fA-F]{3,6}|rgba\(0,\s*0,\s*0|rgba\(255)" \
  app/static/css/ --include="*.css" \
  | grep -v "design-tokens\|theme-toggle" \
  | wc -l
```

Record the number. Every phase's verification checks this count decreased.

- [ ] **Step 4: Commit audit notes (no code changes)**

```bash
git commit --allow-empty -m "chore(theme): baseline audit before Phase 1 refactor"
```

---

## Phase 1 — Foundation

### Task 1: Refine design-tokens.css with the approved palette

**Files:**
- Modify: `app/static/css/design-tokens.css`

- [ ] **Step 1: Update `:root` (dark theme) tokens to approved values**

In the `:root { ... }` block, ensure these variables exist with these exact values (add missing ones, overwrite mismatches):

```css
:root {
  /* --- Surfaces --- */
  --bg-primary: #0f0f12;
  --bg-secondary: #1a1a22;
  --bg-tertiary: #22222e;
  --bg-elevated: #2a2a38;
  --bg-overlay: rgba(10, 10, 18, 0.85);
  --bg-hover: rgba(255, 255, 255, 0.05);
  --bg-input: #1e1e2a;
  --bg-code: #13131a;

  /* --- Text --- */
  --text-primary: #e8e8f0;
  --text-secondary: #9898b0;
  --text-tertiary: #5a5a70;
  --text-inverse: #1a1d2e;
  --text-muted: #6a6a80;

  /* --- Borders --- */
  --border-primary: rgba(255, 255, 255, 0.08);
  --border-secondary: rgba(255, 255, 255, 0.04);
  --border-focus: #7c6aef;

  /* --- Accent --- */
  --accent: #7c6aef;
  --accent-hover: #9580f5;
  --accent-muted: rgba(124, 106, 239, 0.15);
  --accent-translucent: rgba(124, 106, 239, 0.3);

  /* --- Overlays (theme-aware) --- */
  --overlay-hover: rgba(255, 255, 255, 0.05);
  --overlay-pressed: rgba(255, 255, 255, 0.08);

  /* --- Shadows --- */
  --shadow-sm: 0 1px 2px rgba(0, 0, 0, 0.3);
  --shadow-md: 0 4px 12px rgba(0, 0, 0, 0.35);
  --shadow-lg: 0 10px 30px rgba(0, 0, 0, 0.45);
}
```

- [ ] **Step 2: Replace the `[data-theme="light"]` block with the approved light palette**

```css
[data-theme="light"] {
  /* --- Surfaces --- */
  --bg-primary: #f7f9fc;
  --bg-secondary: #eef2f6;
  --bg-tertiary: #e4eaf1;
  --bg-elevated: #ffffff;
  --bg-overlay: rgba(247, 249, 252, 0.85);
  --bg-hover: rgba(0, 0, 0, 0.04);
  --bg-input: #ffffff;
  --bg-code: #eef2f6;

  /* --- Text --- */
  --text-primary: #1a1d2e;
  --text-secondary: #4a5068;
  --text-tertiary: #8890a8;
  --text-inverse: #ffffff;
  --text-muted: #6c7488;

  /* --- Borders --- */
  --border-primary: rgba(0, 0, 0, 0.08);
  --border-secondary: rgba(0, 0, 0, 0.04);
  --border-focus: #5b46d6;

  /* --- Accent --- */
  --accent: #5b46d6;
  --accent-hover: #4a38c2;
  --accent-muted: rgba(91, 70, 214, 0.12);
  --accent-translucent: rgba(91, 70, 214, 0.22);

  /* --- Overlays (theme-aware) --- */
  --overlay-hover: rgba(0, 0, 0, 0.04);
  --overlay-pressed: rgba(0, 0, 0, 0.08);

  /* --- Shadows --- */
  --shadow-sm: 0 1px 2px rgba(20, 30, 60, 0.06);
  --shadow-md: 0 4px 12px rgba(20, 30, 60, 0.08);
  --shadow-lg: 0 10px 30px rgba(20, 30, 60, 0.12);
}
```

- [ ] **Step 3: Add the global transition rule at the end of the file**

```css
/* ============================================================
   Global theme transition — 150ms fade on color properties only.
   Excluded: .terminal (hybrid dark-only rule), elements with
   data-no-transition, and animated transform-heavy elements.
   ============================================================ */
*,
*::before,
*::after {
  transition: background-color 150ms ease,
              color 150ms ease,
              border-color 150ms ease,
              fill 150ms ease,
              stroke 150ms ease,
              box-shadow 150ms ease;
}

.terminal, .terminal *,
[data-no-transition], [data-no-transition] * {
  transition: none;
}
```

- [ ] **Step 4: Verify the CSS parses and reloads cleanly**

Restart service and reload:
```bash
sudo systemctl restart xelasphere.service
```

Open the app; confirm dark theme still renders correctly (no layout break). Open DevTools → Computed tab on `<html>` and confirm the CSS vars are set.

- [ ] **Step 5: Commit**

```bash
git add app/static/css/design-tokens.css
git commit -m "feat(theme): define full dark/light token palette + 150ms transition"
```

---

### Task 2: Refactor `styles.css` (407 colors)

**Files:**
- Modify: `app/static/css/styles.css`

- [ ] **Step 1: List every hardcoded color to map**

```bash
grep -nE "#[0-9a-fA-F]{3,6}|rgba?\(" app/static/css/styles.css > /tmp/styles-colors.txt
wc -l /tmp/styles-colors.txt
```

Expected: ~400+ lines. Review the file visually and decide token per role.

- [ ] **Step 2: Replace `body`, `html`, and main canvas backgrounds**

Find every rule that sets a canvas-level dark background (`#0f0f0f`, `#121212`, `#000`, `#0a0a0a`, `#101010`) on `body`, `html`, `main`, `.chat-area`, `.container`, `.chat-stage`, `.chat-shell`. Replace with:

```css
background: var(--bg-primary, #0f0f12);
/* or, if already background-color */
background-color: var(--bg-primary, #0f0f12);
```

- [ ] **Step 3: Replace secondary surfaces**

Find dark backgrounds on `.sidebar`, `.left-sidebar`, `.right-sidebar`, `.artifact-panel` outer containers, `.chat-shell__header`, `.chat-shell__footer`. Replace with `var(--bg-secondary, #1a1a22)`.

- [ ] **Step 4: Replace cards / nested surfaces**

Find `.card`, `.model-option`, `.chat-tab`, dropdown items, list items with `#222`-range backgrounds. Replace with `var(--bg-tertiary, #22222e)`.

- [ ] **Step 5: Replace inputs**

All `input`, `textarea`, `.input-row`, `.message-input`, `.search-input` backgrounds → `var(--bg-input, #1e1e2a)`.
All input text → `var(--text-primary, #e8e8f0)`.
All input placeholders → `var(--text-tertiary, #5a5a70)`.
All input borders → `var(--border-primary, rgba(255,255,255,0.08))`.

- [ ] **Step 6: Replace modals, dropdowns, menus**

`.modal-overlay` background → `var(--bg-overlay)` or `rgba(0,0,0,0.5)` (unchanged — it's intentional opacity).
`.modal-content`, `.dropdown-menu`, `.popup`, `.tooltip` backgrounds → `var(--bg-elevated, #2a2a38)`.

- [ ] **Step 7: Replace buttons**

- Primary buttons (purple): `background: var(--accent, #7c6aef)`; hover: `var(--accent-hover, #9580f5)`; text: `var(--text-inverse, #1a1d2e)` (light purple buttons need dark text; confirm per-button legibility)
- Secondary buttons: `background: var(--bg-tertiary, #22222e)`; text: `var(--text-primary, #e8e8f0)`
- Ghost buttons: `background: transparent`; hover `background: var(--bg-hover, rgba(255,255,255,0.05))`

- [ ] **Step 8: Replace text colors**

- Body text (`color: #fff`, `#f3f3f3`, `#e6e6e6`) → `var(--text-primary, #e8e8f0)`
- Labels / subtitles (`#b3b3b3`, `#999`, `#9898b0`) → `var(--text-secondary, #9898b0)`
- Placeholders / hints (`#5a5a70`, `#666`, `#777`) → `var(--text-tertiary, #5a5a70)`
- Muted / disabled → `var(--text-muted, #6a6a80)`

- [ ] **Step 9: Replace borders**

Any `border`, `border-top`, etc. with `rgba(255,255,255,0.04-0.14)` → `var(--border-primary, rgba(255,255,255,0.08))` or `var(--border-secondary, rgba(255,255,255,0.04))` based on prominence.
Focus outlines using hardcoded purple → `var(--border-focus, #7c6aef)`.

- [ ] **Step 10: Replace scrollbars**

```css
::-webkit-scrollbar-track { background: var(--bg-secondary, #1a1a22); }
::-webkit-scrollbar-thumb { background: var(--border-primary, rgba(255,255,255,0.08)); }
::-webkit-scrollbar-thumb:hover { background: var(--bg-hover, rgba(255,255,255,0.1)); }
```

- [ ] **Step 11: Replace accent purple occurrences**

Every `#7c6aef` used as an accent → `var(--accent, #7c6aef)`.
Every `rgba(124,106,239,0.x)` → `var(--accent-muted, rgba(124,106,239,0.15))` (for 0.1-0.2 range) or keep explicit if alpha is specifically meaningful.

- [ ] **Step 12: Keep intended exceptions**

Do NOT convert:
- Gradient stops inside decorative gradients (leave as-is unless it's a simple 2-stop bg gradient; those get paired tokens later)
- State colors `#22c55e` (green / success), `#ef4444` (red / error), `#f59e0b` (amber / warning) — these are semantic state colors, keep as literals for now
- Brand colors in logos / SVG fills

- [ ] **Step 13: Re-run grep, confirm count dropped**

```bash
grep -cE "#[0-9a-fA-F]{3,6}|rgba?\(" app/static/css/styles.css
```

Expected: significant drop from ~407 (target: <100, mostly state colors and gradient stops).

- [ ] **Step 14: Visual verification in dark theme**

Restart service, reload browser. Dark theme should look identical to before (fallbacks preserve it).

- [ ] **Step 15: Visual verification in light theme**

Click the owl to toggle. `body`, sidebar, chat area, inputs, buttons should all flip to light colors. Some components will still look dark at this point — that's fine (Phase 2+).

- [ ] **Step 16: Commit**

```bash
git add app/static/css/styles.css
git commit -m "refactor(theme): convert styles.css hardcoded colors to tokens"
```

**Phase 1 checkpoint** — before proceeding, confirm toggling owl swaps body + base layout. If dark stays broken in light mode, re-check `<html data-theme="light">` is being set (DevTools → Elements).

---

## Phase 2 — Core chat UI

### Task 3: Refactor `sidebarContent.css` (139 colors)

**Files:**
- Modify: `app/static/css/sidebarContent.css`

- [ ] **Step 1: Inventory colors**

```bash
grep -cE "#[0-9a-fA-F]{3,6}|rgba?\(" app/static/css/sidebarContent.css
```

- [ ] **Step 2: Replace per the Token Mapping Reference**

Work top to bottom:
- `.sidebar`, `.left-sidebar`, `.sidebar-header`, `.sidebar-footer` backgrounds → `var(--bg-secondary, #1a1a22)`
- `.sidebar-content` inner → `var(--bg-secondary, #1a1a22)` or `var(--bg-primary, #0f0f12)` per existing hierarchy
- Chat tabs: base → `transparent`; hover `var(--bg-hover)`; active `var(--bg-tertiary, #22222e)`
- Tab text → `var(--text-primary)`; inactive → `var(--text-secondary)`
- Borders → `var(--border-primary)` or `var(--border-secondary)`
- Profile section bg → `var(--bg-tertiary, #22222e)`
- Action button (`.sidebar-action-btn`) → `var(--bg-tertiary)`; hover `var(--bg-elevated)`
- Shortcut hint pill → `var(--border-secondary)`
- Sidebar toggle button icons → keep stroke `var(--text-secondary)`

Leave the owl/ghost SVG mascot color tokens alone — they have their own theme-aware rules.

- [ ] **Step 3: Verify grep count dropped**

```bash
grep -cE "#[0-9a-fA-F]{3,6}|rgba?\(" app/static/css/sidebarContent.css
```

- [ ] **Step 4: Restart service; visual check**

```bash
sudo systemctl restart xelasphere.service
```

Toggle themes. Sidebar should flip bg + text cleanly, tabs still readable in both.

- [ ] **Step 5: Commit**

```bash
git add app/static/css/sidebarContent.css
git commit -m "refactor(theme): convert sidebarContent.css to tokens"
```

---

### Task 4: Refactor `message-styles.css` (258 colors)

**Files:**
- Modify: `app/static/css/message-styles.css`

- [ ] **Step 1: Inventory**

```bash
grep -cE "#[0-9a-fA-F]{3,6}|rgba?\(" app/static/css/message-styles.css
```

- [ ] **Step 2: Replace per token mapping**

- User message bubble background → `var(--accent-muted, rgba(124,106,239,0.15))`; text → `var(--text-primary)`
- Assistant message background → `transparent` or `var(--bg-secondary)` per existing design
- Assistant message text → `var(--text-primary, #e8e8f0)`
- Code within messages → `var(--bg-code, #13131a)`; text `var(--text-primary)`
- Inline code → `var(--bg-code)`; slightly different padding likely kept
- Links → `var(--accent, #7c6aef)`; hover `var(--accent-hover)`
- Citations / footnotes → `var(--text-secondary)` and `var(--bg-tertiary)`
- Thinking / reasoning blocks → `var(--bg-tertiary, #22222e)`; text `var(--text-secondary)`
- Blockquote border-left → `var(--accent-muted)`; text `var(--text-secondary)`
- Table borders → `var(--border-primary)`; table stripes → `var(--bg-hover)`

- [ ] **Step 3: Verify grep count dropped**

- [ ] **Step 4: Visual verification**

Send a message with code, a thinking block, a table, and a link. Toggle theme. All readable in both.

- [ ] **Step 5: Commit**

```bash
git add app/static/css/message-styles.css
git commit -m "refactor(theme): convert message-styles.css to tokens"
```

---

### Task 5: Refactor chat auxiliary CSS

**Files:**
- Modify: `app/static/css/onlineSearch.css`
- Modify: `app/static/css/search-results.css`
- Modify: `app/static/css/options-btn.css`

- [ ] **Step 1: Apply token mapping to `onlineSearch.css`**

Chat search panel backgrounds → `var(--bg-secondary)` / `var(--bg-tertiary)`. Input → `var(--bg-input)`. Result items → `var(--bg-tertiary)`; hover `var(--bg-hover)`. Text hierarchy as usual.

- [ ] **Step 2: Apply token mapping to `search-results.css`**

Same pattern: backgrounds, text, borders. Links → `var(--accent)`.

- [ ] **Step 3: Apply token mapping to `options-btn.css`**

Button bg → `var(--bg-tertiary)`; hover `var(--bg-elevated)`; border `var(--border-primary)`; text `var(--text-primary)`.

- [ ] **Step 4: Restart service; visual verification**

Trigger online search, view results. Toggle theme.

- [ ] **Step 5: Commit**

```bash
git add app/static/css/onlineSearch.css app/static/css/search-results.css app/static/css/options-btn.css
git commit -m "refactor(theme): convert chat auxiliary CSS (search, results, options) to tokens"
```

**Phase 2 checkpoint** — send messages, use search, open options. Both themes should render chat cleanly.

---

## Phase 3 — Feature panels

### Task 6: Inline JS style audit fixes (from Task 0)

**Files:**
- Modify (as needed): `app/static/js/artifactPanel.js`, `app/static/js/codeWindow.js`, `app/static/js/chartBuilder.js`, any other file found in Task 0 audit

- [ ] **Step 1: For each inline color mutation from Task 0 audit**

Replace the pattern:
```js
// Before
el.style.backgroundColor = '#1a1a1a';
el.style.color = '#e6e6e6';
```

With one of:
```js
// Option A: use a CSS class with theme-aware styles
el.classList.add('panel-dark-surface');

// Option B: set a CSS custom property referencing a token
el.style.setProperty('background-color', 'var(--bg-secondary)');
el.style.setProperty('color', 'var(--text-primary)');
```

Option B is simpler when the value varies dynamically.

- [ ] **Step 2: If adding new CSS classes, define them**

In the most relevant CSS file (e.g., `artifact-panel.css`), add:
```css
.panel-dark-surface {
    background-color: var(--bg-secondary, #1a1a22);
    color: var(--text-primary, #e8e8f0);
}
```

- [ ] **Step 3: Restart and test**

Trigger every affected feature (open artifact panel, open code viewer, render a chart). Confirm theme-aware colors apply in both themes.

- [ ] **Step 4: Commit**

```bash
git add app/static/js/ app/static/css/
git commit -m "refactor(theme): convert inline JS style mutations to theme-aware CSS"
```

---

### Task 7: Refactor `artifact-panel.css` (208 colors)

**Files:**
- Modify: `app/static/css/artifact-panel.css`

- [ ] **Step 1: Inventory**

```bash
grep -cE "#[0-9a-fA-F]{3,6}|rgba?\(" app/static/css/artifact-panel.css
```

- [ ] **Step 2: Replace per token mapping**

- Panel outer container → `var(--bg-secondary, #1a1a22)`
- Panel header → `var(--bg-tertiary, #22222e)`; title `var(--text-primary)`
- Tabs (preview/code) → inactive `var(--text-secondary)`, active `var(--text-primary)` with `var(--accent)` underline
- Panel inner (preview bg) → `var(--bg-primary, #0f0f12)`
- Panel inner (code bg) → `var(--bg-code, #13131a)`
- Resize handle → `var(--border-primary)`; hover `var(--accent-muted)`
- Close button → `var(--text-secondary)`; hover `var(--text-primary)`
- Publish button → `var(--accent, #7c6aef)`; hover `var(--accent-hover)`; text `var(--text-inverse)`

- [ ] **Step 3: Verify grep count dropped**

- [ ] **Step 4: Open artifact panel, visually verify in both themes**

- [ ] **Step 5: Commit**

```bash
git add app/static/css/artifact-panel.css
git commit -m "refactor(theme): convert artifact-panel.css to tokens"
```

---

### Task 8: Refactor code-rendering CSS (hybrid flip)

**Files:**
- Modify: `app/static/css/code-window.css`
- Modify: `app/static/css/tree-view.css`
- Modify: `app/static/css/stream-blocks.css`

- [ ] **Step 1: `code-window.css` — replace per token mapping**

Code window container → `var(--bg-code, #13131a)` (in light theme this becomes `#eef2f6` — light grey code block, per the hybrid rule).
Line numbers → `var(--text-tertiary)`.
Current line → `var(--bg-hover)`.
Toolbar → `var(--bg-tertiary)`; buttons `var(--text-secondary)`.

- [ ] **Step 2: `tree-view.css` — replace per token mapping**

File tree background → `var(--bg-code)` or `var(--bg-secondary)`. Entries → `var(--text-primary)`; hover `var(--bg-hover)`. Indent guides → `var(--border-secondary)`.

- [ ] **Step 3: `stream-blocks.css` — replace per token mapping**

Streaming block backgrounds → `var(--bg-code)`. Language tags → `var(--accent-muted)`. Copy buttons → `var(--text-secondary)`; hover `var(--text-primary)`.

- [ ] **Step 4: Restart, open a code artifact in both themes**

Code should be light grey with dark text in light theme, dark with light text in dark theme.

- [ ] **Step 5: Commit**

```bash
git add app/static/css/code-window.css app/static/css/tree-view.css app/static/css/stream-blocks.css
git commit -m "refactor(theme): convert code-rendering CSS to tokens (hybrid flip)"
```

---

### Task 9: `terminal-window.css` — hardcoded dark exception

**Files:**
- Modify: `app/static/css/terminal-window.css`

- [ ] **Step 1: Ensure terminal stays dark in both themes**

At the top of the file, add:
```css
/* Terminal always stays dark — hybrid rule. Not themed. */
.terminal,
.terminal *,
[data-terminal] {
  /* Lock colors; global theme transition is excluded via design-tokens.css */
}

.terminal {
  background: #0f0f12 !important;
  color: #e8e8f0 !important;
}

.terminal-output {
  color: #e8e8f0 !important;
}

.terminal-header {
  background: #1a1a22 !important;
  color: #9898b0 !important;
}
```

Adjust rule selectors to match the actual classes in the file. The `!important` guards against accidental theme token bleed.

- [ ] **Step 2: Sweep any theme-dependent vars in this file and hardcode them**

Any existing `var(--bg-*)` references → replace with literal dark values. This file is an exception.

- [ ] **Step 3: Restart, open a terminal in light theme, confirm it stays dark**

- [ ] **Step 4: Commit**

```bash
git add app/static/css/terminal-window.css
git commit -m "feat(theme): lock terminal to dark in both themes (hybrid rule)"
```

---

### Task 10: Refactor embedded content types

**Files:**
- Modify: `app/static/css/crypto-chart.css`
- Modify: `app/static/css/dashboard.css`
- Modify: `app/static/css/recipe-card.css`

- [ ] **Step 1: `crypto-chart.css` — tokens per mapping**

Chart container → `var(--bg-tertiary, #22222e)`. Chart text → `var(--text-primary)`. Axis lines → `var(--border-primary)`. Note: chart SERIES colors (up/down price, line colors) are semantic, leave as literals (`#22c55e`, `#ef4444`, etc.).

- [ ] **Step 2: `dashboard.css` — tokens per mapping**

Dashboard cards → `var(--bg-tertiary)`. Metric numbers → `var(--text-primary)`. Metric labels → `var(--text-secondary)`.

- [ ] **Step 3: `recipe-card.css` — tokens per mapping**

Recipe card bg → `var(--bg-tertiary)`. Title `var(--text-primary)`. Body `var(--text-secondary)`.

- [ ] **Step 4: Trigger each content type in chat, verify both themes**

- [ ] **Step 5: Commit**

```bash
git add app/static/css/crypto-chart.css app/static/css/dashboard.css app/static/css/recipe-card.css
git commit -m "refactor(theme): convert embedded content CSS (crypto, dashboard, recipe) to tokens"
```

---

### Task 11: Refactor small overlays

**Files:**
- Modify: `app/static/css/ghost-model.css`
- Modify: `app/static/css/text-selection-quote.css`

- [ ] **Step 1: `ghost-model.css` — model picker overlay tokens**

Overlay bg → `var(--bg-elevated, #2a2a38)`. Model items → `var(--bg-tertiary)`; hover `var(--bg-hover)`; active `var(--accent-muted)`. Text hierarchy.

- [ ] **Step 2: `text-selection-quote.css` — selection popup tokens**

Popup → `var(--bg-elevated)`; arrow → `var(--bg-elevated)`; text `var(--text-primary)`; buttons `var(--text-secondary)`.

- [ ] **Step 3: Verify both overlays in both themes**

- [ ] **Step 4: Commit**

```bash
git add app/static/css/ghost-model.css app/static/css/text-selection-quote.css
git commit -m "refactor(theme): convert small overlays (ghost-model, selection-quote) to tokens"
```

**Phase 3 checkpoint** — open artifact panel, code window, terminal, chart, recipe card, model picker in both themes. All should look correct.

---

## Phase 4 — Modals, toasts, settings

### Task 12: Refactor modals + toasts

**Files:**
- Modify: `app/static/css/toast.css`
- Modify: `app/static/css/deleteChat.css`
- Modify: `app/static/css/profile-edit.css`
- Modify: `app/static/css/upgrade.css`

- [ ] **Step 1: `toast.css`**

Toast container → `var(--bg-elevated, #2a2a38)`; text `var(--text-primary)`; shadow `var(--shadow-lg)`. State colors (success green, error red, warning amber, info blue) → keep as literals.

- [ ] **Step 2: `deleteChat.css`**

Modal overlay unchanged (`rgba(0,0,0,0.5)` is intentional). Modal card → `var(--bg-elevated)`; buttons per accent/secondary mapping. Danger button → `#ef4444` (literal).

- [ ] **Step 3: `profile-edit.css`**

Form bg → `var(--bg-elevated)`; inputs `var(--bg-input)`; labels `var(--text-secondary)`.

- [ ] **Step 4: `upgrade.css`**

Pricing cards → `var(--bg-tertiary)`. Featured card bg → `var(--accent-muted)`. CTA buttons → `var(--accent)`.

- [ ] **Step 5: Trigger each (a toast, delete confirmation, profile edit, upgrade) in both themes**

- [ ] **Step 6: Commit**

```bash
git add app/static/css/toast.css app/static/css/deleteChat.css app/static/css/profile-edit.css app/static/css/upgrade.css
git commit -m "refactor(theme): convert modals + toasts to tokens"
```

---

### Task 13: Refactor settings pages

**Files:**
- Modify: `app/static/css/api-keys.css`
- Modify: `app/static/css/login.css`

- [ ] **Step 1: `api-keys.css`**

Settings page bg → `var(--bg-primary)`. Settings cards → `var(--bg-tertiary)`. Key rows → `var(--bg-elevated)` with hover `var(--bg-hover)`. Input → `var(--bg-input)`. Danger (delete key) → keep `#ef4444`.

- [ ] **Step 2: `login.css`**

Login card → `var(--bg-elevated)`. Inputs → `var(--bg-input)`. Primary submit → `var(--accent)`. Social login buttons keep brand colors.

- [ ] **Step 3: Visit `/settings/api-keys` and `/login` in both themes**

- [ ] **Step 4: Commit**

```bash
git add app/static/css/api-keys.css app/static/css/login.css
git commit -m "refactor(theme): convert settings + login pages to tokens"
```

---

### Task 14: Refactor email + dev surfaces

**Files:**
- Modify: `app/static/css/email-composer.css`
- Modify: `app/static/css/email-edit-toolbar.css`
- Modify: `app/static/css/debug.css`
- Modify: `app/static/css/prism.css`

- [ ] **Step 1: `email-composer.css` / `email-edit-toolbar.css`**

Composer bg → `var(--bg-elevated)`. Toolbar bg → `var(--bg-tertiary)`. Toolbar buttons → text `var(--text-secondary)`; hover `var(--bg-hover)`. Content editable area → `var(--bg-input)`; text `var(--text-primary)`.

- [ ] **Step 2: `debug.css` — convert surfaces only, keep debug-specific colors literal**

Panel bg → `var(--bg-secondary)`. Debug headings → `var(--text-primary)`. Keep status colors as literals.

- [ ] **Step 3: `prism.css` — hands off except for plain backgrounds**

Prism is a syntax theme. Only convert the BACKGROUND of the pre/code wrapper if it's hardcoded — leave all token colors (keywords, strings, comments) as the Prism palette defines.

- [ ] **Step 4: Verify email composer + debug panel in both themes**

- [ ] **Step 5: Commit**

```bash
git add app/static/css/email-composer.css app/static/css/email-edit-toolbar.css app/static/css/debug.css app/static/css/prism.css
git commit -m "refactor(theme): convert email + debug + prism surfaces to tokens"
```

**Phase 4 checkpoint** — trigger every modal, every toast state, visit settings, visit login, open a terminal, open debug panel, compose an email. Both themes.

---

## Phase 5 — Responsive + polish

### Task 15: Refactor `mobile.css` (172 colors)

**Files:**
- Modify: `app/static/css/mobile.css`

- [ ] **Step 1: Inventory**

```bash
grep -cE "#[0-9a-fA-F]{3,6}|rgba?\(" app/static/css/mobile.css
```

- [ ] **Step 2: Apply token mapping to all mobile-specific color overrides**

Most mobile rules override a base rule with a different padding/size but same color. Where color IS overridden, use the same token as the base rule. Common patterns:
- Mobile sidebar bg (often fully opaque) → `var(--bg-secondary)`
- Mobile header bar → `var(--bg-secondary)`
- Mobile bottom sheet → `var(--bg-elevated)`
- Mobile input row → `var(--bg-secondary)` (iOS-style), input → `var(--bg-input)`
- Swipe action colors → keep `#ef4444` (delete), `#f59e0b` (archive) literal
- Mobile artifact modal → `var(--bg-elevated)`

- [ ] **Step 3: Verify grep count dropped**

- [ ] **Step 4: Open DevTools responsive view (iPhone 12 / Pixel 5), toggle theme**

Scroll through chat, open sidebar, open artifact panel, open model picker. Both themes should work.

- [ ] **Step 5: Commit**

```bash
git add app/static/css/mobile.css
git commit -m "refactor(theme): convert mobile.css to tokens"
```

---

### Task 16: Final sweep — catch what the grep reveals

**Files:**
- Multiple, as discovered

- [ ] **Step 1: Run the missed-color detector**

```bash
grep -rE "(#[0-9a-fA-F]{3,6}|rgba\(0,\s*0,\s*0|rgba\(255)" \
  app/static/css/ --include="*.css" \
  | grep -v "design-tokens\|theme-toggle" \
  | tee /tmp/missed-colors.txt
wc -l /tmp/missed-colors.txt
```

- [ ] **Step 2: Categorize remaining matches**

Expected categories:
- Intentional exceptions (terminal, prism token colors, state colors, gradient stops inside decorative bg gradients)
- Missed conversions (bug — fix)

Triage line-by-line. For each bug, convert to the appropriate token.

- [ ] **Step 3: Re-run detector, confirm only exceptions remain**

- [ ] **Step 4: Commit any fixes**

```bash
git add app/static/css/
git commit -m "refactor(theme): final sweep — convert missed hardcoded colors"
```

---

### Task 17: Final visual QA + verification

- [ ] **Step 1: Prepare a test checklist**

Test in BOTH dark and light:
- Home view (greeting)
- Chat session with at least one user msg + AI response
- Chat with a code block
- Chat with a table
- Chat with thinking/reasoning block
- Artifact panel open (preview + code tabs)
- Terminal window open → should stay dark in light theme
- Crypto chart render
- Dashboard render
- Recipe card render
- Model picker open
- Text selection quote popup
- Toast (trigger a success + error)
- Delete chat modal
- Profile edit modal
- Upgrade modal
- Settings / API keys page
- Login page (logout first)
- Email composer
- Debug panel
- Mobile breakpoint (DevTools iPhone / Pixel)

- [ ] **Step 2: Toggle theme at each screen; verify no stuck dark/light elements**

- [ ] **Step 3: Hover every interactive element in both themes**

Buttons, tabs, tab actions, list items, icon buttons, links. Watch for hover states that don't flip.

- [ ] **Step 4: Reload page in each theme; verify persistence**

LocalStorage should keep the choice across reloads.

- [ ] **Step 5: Smooth-transition check**

Click owl rapidly. No flicker, no color pops, no layout shift. The 150ms fade should feel cohesive.

- [ ] **Step 6: Final commit / tag**

```bash
git commit --allow-empty -m "test(theme): final visual QA complete — theme system covers every pixel"
```

---

## Success Criteria (from spec)

- Clicking the owl flips the entire app between light and dark themes with a smooth 150ms fade
- No visually "stuck" dark elements in light mode (and vice versa)
- Missed-color grep returns only intended exceptions
- Theme choice persists across reloads
- Accent purple reads confidently on both backgrounds
- Hybrid rule holds: code blocks flip, terminal stays dark
