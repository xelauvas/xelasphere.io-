# Theme System Refactor — Complete Light/Dark Coverage

**Date:** 2026-04-13
**Status:** Design approved, ready for implementation plan

## Problem

XelaSphere's theme system has working infrastructure (`themeManager.js` toggles `[data-theme]` on `<html>`; `design-tokens.css` defines both themes), but the actual app CSS is ~2,500 hardcoded color values scattered across 30 CSS files that do not reference theme variables. Clicking the theme toggle flashes a transition overlay but produces no visible theme change because the UI does not honor the cascade.

The owl mascot (top-right) has been wired to `window.ThemeManager.toggle()`. The sidebar ghost has been wired to start incognito chats. Both are in place; what's missing is a CSS codebase that actually responds to the theme switch.

## Goal

A complete light/dark theme that covers every pixel of the app: body, sidebars, chat area, messages, inputs, modals, artifact panel, code blocks, settings, mobile, etc. Single source of truth for colors. Clicking the owl must produce an immediate, smooth, full-app theme transition in both directions.

## Non-goals

- Redesigning the dark theme (refine palette values, but keep overall look)
- Changing syntax highlighting (Prism theme) — remains its own palette
- Re-theming the `.terminal` surface — it stays dark in both modes (hybrid rule)
- Decorative SVG illustrations in templates — hardcoded values remain

## Approach

**Refactor (not override).** Replace every hardcoded color in the CSS files with a semantic theme token via `var(--token, #fallback)`. The light theme then works automatically because `[data-theme="light"]` redefines the same tokens.

Rejected alternative: a high-specificity override stylesheet. Faster to ship but creates a parallel maintenance burden; new components added later would not inherit light-theme support.

## Architecture

Three layers:

1. **`app/static/css/design-tokens.css`** (already loaded) — defines all color tokens. Two blocks:
   - `:root` — dark theme (default)
   - `[data-theme="light"]` — light theme overrides
2. **App CSS files** — every hardcoded color becomes `var(--token-name, #fallback)`. Fallback ensures graceful degradation if the variable is missing.
3. **`themeManager.js`** (already in place) — toggles `[data-theme]` attribute on `<html>`; CSS variables cascade automatically.

## Token naming convention

Semantic, not literal. ~25 tokens describe both themes.

- **Surfaces**: `--bg-primary`, `--bg-secondary`, `--bg-tertiary`, `--bg-elevated`, `--bg-overlay`, `--bg-hover`, `--bg-input`, `--bg-code`
- **Text**: `--text-primary`, `--text-secondary`, `--text-tertiary`, `--text-inverse`, `--text-muted`
- **Borders**: `--border-primary`, `--border-secondary`, `--border-focus`
- **Accents**: `--accent`, `--accent-hover`, `--accent-muted`, `--accent-translucent`
- **States**: `--success`, `--warning`, `--error`, `--info`
- **Shadows**: `--shadow-sm`, `--shadow-md`, `--shadow-lg`

## Color palette

### Dark theme (`:root`)

| Token | Value | Role |
| --- | --- | --- |
| `--bg-primary` | `#0f0f12` | Main canvas |
| `--bg-secondary` | `#1a1a22` | Sidebars, panels |
| `--bg-tertiary` | `#22222e` | Cards, nested surfaces |
| `--bg-elevated` | `#2a2a38` | Modals, dropdowns |
| `--bg-hover` | `#ffffff0d` | Hover states |
| `--bg-input` | `#1e1e2a` | Input fields |
| `--bg-code` | `#13131a` | Code blocks |
| `--text-primary` | `#e8e8f0` | Body text |
| `--text-secondary` | `#9898b0` | Labels, subtitles |
| `--text-tertiary` | `#5a5a70` | Placeholders, hints |
| `--border-primary` | `#ffffff14` | Default borders |
| `--border-secondary` | `#ffffff08` | Subtle dividers |
| `--accent` | `#7c6aef` | Buttons, links, focus |
| `--accent-hover` | `#9580f5` | Hover on accent |
| `--accent-muted` | `#7c6aef22` | Chip backgrounds, tags |

### Light theme (`[data-theme="light"]`) — cool off-white (Linear/Vercel feel)

| Token | Value | Role |
| --- | --- | --- |
| `--bg-primary` | `#f7f9fc` | Main canvas |
| `--bg-secondary` | `#eef2f6` | Sidebars, panels |
| `--bg-tertiary` | `#e4eaf1` | Cards, nested surfaces |
| `--bg-elevated` | `#ffffff` | Modals, dropdowns |
| `--bg-hover` | `#00000008` | Hover states |
| `--bg-input` | `#ffffff` | Input fields |
| `--bg-code` | `#eef2f6` | Code blocks (light grey) |
| `--text-primary` | `#1a1d2e` | Body text |
| `--text-secondary` | `#4a5068` | Labels, subtitles |
| `--text-tertiary` | `#8890a8` | Placeholders, hints |
| `--border-primary` | `#00000012` | Default borders |
| `--border-secondary` | `#00000008` | Subtle dividers |
| `--accent` | `#5b46d6` | Buttons, links, focus (deeper purple) |
| `--accent-hover` | `#4a38c2` | Hover on accent |
| `--accent-muted` | `#5b46d618` | Chip backgrounds, tags |

### Terminal exception (hybrid)

`.terminal`, `.terminal-output`, `[data-terminal]` hardcode `background: #0f0f12; color: #e8e8f0;` in both themes — they never flip.

### Transition (150 ms fade)

```css
*, *::before, *::after {
  transition: background-color 150ms ease,
              color 150ms ease,
              border-color 150ms ease,
              box-shadow 150ms ease;
}
/* Exclude terminals and animated elements */
.terminal *, [data-no-transition] * { transition: none; }
```

## File-by-file refactor plan

**Scope:** ~2,500 color occurrences across 30 CSS files.

### Phase 1 — Foundation

1. `design-tokens.css` — add/refine tokens per the palette above
2. `styles.css` (407 colors) — global body, html, base elements, common layouts, inputs, buttons, scrollbars

### Phase 2 — Core chat UI

3. `sidebarContent.css` (139)
4. `message-styles.css` (258)
5. `onlineSearch.css`, `search-results.css`, `options-btn.css`

### Phase 3 — Feature panels

6. `artifact-panel.css` (208)
7. `code-window.css`, `tree-view.css`, `stream-blocks.css` — code rendering (hybrid: flip to light in light mode)
8. `terminal-window.css` — hardcoded dark always (terminal exception)
9. `crypto-chart.css`, `dashboard.css`, `recipe-card.css`
10. `ghost-model.css`, `text-selection-quote.css`

### Phase 4 — Modals, toasts, settings

11. `toast.css`, `deleteChat.css`, `profile-edit.css`, `upgrade.css`
12. `api-keys.css`, `login.css`
13. `email-composer.css`, `email-edit-toolbar.css`
14. `debug.css`, `prism.css`

### Phase 5 — Responsive + polish

15. `mobile.css` (172)
16. Add global 150ms fade transition CSS
17. Visual QA pass in both themes

### Per-file pattern

- Identify each hardcoded color → map to semantic token → replace with `var(--token, #fallback)`
- Token choice by role (is this a main bg, a nested card, an input?) — not by exact color match
- Greys → text tier (primary / secondary / tertiary) by hierarchy

## Edge cases

### 1. mobile.css audit (pre-Phase-1)

Run `grep -nE "background|color" app/static/css/mobile.css` before Phase 1 starts. Catalog any media-query overrides setting explicit colors that'd bleed into Phase 2-4 testing. Fix conflicting rules immediately; defer the rest to Phase 5.

### 2. Inline JS styles (pre-Phase-3)

`grep -rE "\.style\.(background|color|borderColor)" app/static/js/` before Phase 3.

Likely offenders: `artifactPanel.js`, `codeWindow.js`, `chartBuilder.js`.

For each match:
- Replace with `element.classList.add/remove` + CSS-defined states, OR
- `element.style.setProperty('--custom', value)` referencing theme-aware variables
- Any that must stay inline → use `var(--token)` strings

### 3. Gradients

Two-variable treatment: `linear-gradient(135deg, var(--bg-gradient-start), var(--bg-gradient-end))` with separate light/dark values in each theme block.

### 4. RGBA overlays with dark-assumed alphas

`rgba(255,255,255,0.05)` reads bright on dark but invisible on light. Add semantic tokens `--overlay-hover`, `--overlay-pressed` that resolve to `rgba(255,255,255,X)` in dark and `rgba(0,0,0,X)` in light.

### 5. Shadows

Current shadows using `rgba(0,0,0,0.4)` look muddy on light. Light theme shadow tokens use lower opacity + tighter radius (defined in palette).

### 6. Inset box-shadows for card depth

Require inversion: white insets on dark become dark subtle insets on light.

### 7. Focus rings

`outline` colors must use `var(--accent)`, not hardcoded purple.

### 8. Scrollbars

`::-webkit-scrollbar-*` theming — light theme scrollbars = light grey thumb on white track.

### 9. Transition safety

Global 150ms fade via `* { transition: ... }` could override existing animations using transition shorthand. Mitigation: use `transition-property` for color props only, and add `will-change: background-color, color` to opt-out patterns for animated elements.

## Verification

### Per-phase checkpoints

- **After Phase 1** — toggle owl, confirm body/base switches correctly before touching any component
- **After Phase 2** — full chat session in both themes
- **After Phase 3** — open every content type (code block, terminal, chart, crypto card, recipe) and verify hybrid rules
- **After Phase 4** — trigger every modal/toast in both themes
- **After Phase 5** — mobile device or devtools responsive view; scan every screen at sm/md/lg breakpoints

### Automated missed-color detector

After each phase, run:

```bash
grep -rE "(#[0-9a-fA-F]{3,6}|rgba\(0,\s*0,\s*0|rgba\(255)" \
  app/static/css/ --include="*.css" \
  | grep -v "design-tokens\|theme-toggle"
```

Output count should shrink monotonically with each phase. Final count = intended exceptions only (terminal, prism, decorative SVG in CSS).

### Final QA

- Hover every interactive element in both themes (catches hover-state colors)
- Screenshot each phase's target screens in both themes, diff side by side
- Confirm transition feels smooth (no flicker, no color pops)

## Risks

| Risk | Mitigation |
| --- | --- |
| Missed hardcoded values in obscure files | Automated grep count per phase |
| JS setting inline styles that ignore CSS vars | Pre-Phase-3 audit; convert to CSS classes or CSS custom properties via `setProperty` |
| Mobile.css overrides bleeding into Phase 2-4 testing | Pre-Phase-1 audit; fix conflicting rules early |
| 150ms global transition conflicts with existing animations | Scope transition to color props only; opt-out pattern for animated elements |
| Gradient/overlay alphas looking wrong under new theme | Dedicated overlay tokens; gradient tokens as pairs |

## Success criteria

- Clicking the owl flips the entire app between light and dark themes with a smooth 150ms fade
- No visually "stuck" dark elements in light mode (and vice versa) across: home view, chat with messages, artifact panel open, every modal, settings pages, mobile breakpoint
- The automated missed-color grep returns only intended exceptions (terminal/prism/decorative)
- Theme choice persists across reloads (existing themeManager handles this)
- Accent purple reads confidently on both backgrounds
- Hybrid rule holds: code blocks flip, terminal stays dark
