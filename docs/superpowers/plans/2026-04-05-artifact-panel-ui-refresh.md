# Artifact Panel UI Refresh Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restyle the artifact panel to match Claude.ai's visual language (colors, header, tabs, buttons, pills, animations) without changing any functionality.

**Architecture:** Pure CSS refactor of `artifact-panel.css` with minor HTML class adjustments in `artifactPanel.js`. No backend changes. Each task targets one visual section so progress is incrementally visible.

**Tech Stack:** CSS custom properties, existing Prism.js/CodeMirror, existing HTML structure

---

### Task 1: Replace Color Palette with CSS Custom Properties

**Files:**
- Modify: `app/static/css/artifact-panel.css:1-9`

This task adds the new Claude-aligned color tokens at the top of the file. All subsequent tasks reference these tokens.

- [ ] **Step 1: Add color token block at the top of artifact-panel.css**

Insert immediately after the opening comment block (after line 3), before the first rule:

```css
/* ==================== */
/* CLAUDE-STYLE COLOR TOKENS */
/* ==================== */

.artifact-panel,
.artifact-chat-pill {
  --artifact-bg: #2b2b2b;
  --artifact-bg-elevated: #333333;
  --artifact-border: rgba(255, 255, 255, 0.06);
  --artifact-border-subtle: rgba(255, 255, 255, 0.04);
  --artifact-text: rgba(255, 255, 255, 0.88);
  --artifact-muted: rgba(255, 255, 255, 0.5);
  --artifact-hover: rgba(255, 255, 255, 0.05);
  --artifact-active: rgba(255, 255, 255, 0.08);
  --artifact-accent: #7c6fe0;
  --artifact-shadow: rgba(0, 0, 0, 0.25);
}
```

- [ ] **Step 2: Update the main panel container to use new tokens**

Replace the `.artifact-panel` rule (currently lines 10-33) with:

```css
.artifact-panel {
  position: fixed;
  top: 0;
  right: 0;
  bottom: 0;
  width: var(--artifact-panel-width, 50vw);
  min-width: 320px;
  max-width: 55vw;
  background: var(--artifact-bg);
  border-left: 1px solid var(--artifact-border);
  border-radius: 12px 0 0 12px;
  box-shadow: -8px 0 32px var(--artifact-shadow);
  transform: translateX(100%);
  opacity: 0;
  transition:
    transform 0.3s cubic-bezier(0.32, 0.72, 0, 1),
    opacity 0.3s cubic-bezier(0.32, 0.72, 0, 1),
    box-shadow 0.3s ease;
  z-index: var(--z-fixed);
  display: flex;
  flex-direction: column;
  overflow: hidden;
  will-change: transform, opacity;
}

.artifact-panel.is-open {
  transform: translateX(0);
  opacity: 1;
  box-shadow: -12px 0 48px var(--artifact-shadow);
}

.artifact-panel[aria-hidden="false"] {
  transform: translateX(0);
  opacity: 1;
  box-shadow: -12px 0 48px var(--artifact-shadow);
}
```

- [ ] **Step 3: Verify visually**

Open the app, trigger an artifact. Confirm the panel background is slightly warmer (#2b2b2b vs old #212121) and the shadow is softer. The open/close animation should feel smoother (0.3s spring curve).

- [ ] **Step 4: Commit**

```bash
git add app/static/css/artifact-panel.css
git commit -m "style(artifacts): add Claude-style color tokens and update panel container"
```

---

### Task 2: Restyle Header

**Files:**
- Modify: `app/static/css/artifact-panel.css` (header section, ~lines 284-403)

- [ ] **Step 1: Replace header styles**

Replace the entire header section (`.artifact-panel__header` through `.artifact-panel__header-btn--publish:hover`) with:

```css
/* ==================== */
/* HEADER - Claude Style */
/* ==================== */

.artifact-panel__header {
  position: sticky;
  top: 0;
  z-index: 100;
  flex-shrink: 0;
  height: 48px;
  padding: 0 16px;
  background: var(--artifact-bg-elevated);
  border-bottom: 1px solid var(--artifact-border-subtle);
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

/* Left header group */
.artifact-panel__header-left {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
  flex: 1;
}

/* Artifact switch button - ghost icon button */
.artifact-panel__switch-btn {
  width: 32px;
  height: 32px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: transparent;
  border: none;
  border-radius: 8px;
  color: var(--artifact-muted);
  cursor: pointer;
  transition: all 0.12s ease;
  flex-shrink: 0;
}

.artifact-panel__switch-btn:hover {
  background: var(--artifact-hover);
  color: var(--artifact-text);
}

.artifact-panel__switch-btn:focus-visible {
  outline: 2px solid var(--artifact-accent);
  outline-offset: 2px;
}

.artifact-panel__switch-btn i {
  font-size: 14px;
}

/* Right header group */
.artifact-panel__header-right {
  display: flex;
  align-items: center;
  gap: 4px;
  flex-shrink: 0;
}

/* Header action buttons - ghost icon style (no borders, no text) */
.artifact-panel__header-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  padding: 0;
  font-size: 0;
  color: var(--artifact-muted);
  background: transparent;
  border: none;
  border-radius: 8px;
  cursor: pointer;
  transition: all 0.12s ease;
}

.artifact-panel__header-btn:hover {
  background: var(--artifact-hover);
  color: var(--artifact-text);
}

.artifact-panel__header-btn:focus-visible {
  outline: 2px solid var(--artifact-accent);
  outline-offset: 2px;
}

.artifact-panel__header-btn i {
  font-size: 16px;
  color: inherit;
}

/* Remove old dropdown caret on copy button */
.artifact-panel__header-btn--copy::after {
  display: none;
}

/* Publish button - same ghost style, no special treatment */
.artifact-panel__header-btn--publish {
  background: transparent;
  border: none;
}

.artifact-panel__header-btn--publish:hover {
  background: var(--artifact-hover);
}
```

- [ ] **Step 2: Verify visually**

Open the app with an artifact. Header should be 48px tall, warmer background, barely visible bottom border. Buttons are icon-only ghosts with no visible borders. Hover reveals a subtle background.

- [ ] **Step 3: Commit**

```bash
git add app/static/css/artifact-panel.css
git commit -m "style(artifacts): restyle header to Claude ghost-button design"
```

---

### Task 3: Restyle Tab Bar (Pill Toggle)

**Files:**
- Modify: `app/static/css/artifact-panel.css` (tab toggle section, ~lines 2362-2421)

- [ ] **Step 1: Replace the pill tab toggle styles**

Replace the section from `.artifact-panel__tab-toggle` through the `/* --- Hide old tabs nav --- */` block with:

```css
/* --- Claude-Style Pill Tab Toggle --- */
.artifact-panel__tab-toggle {
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  background: var(--artifact-bg);
  border-radius: 10px;
  padding: 3px;
  gap: 2px;
}

.artifact-panel__tab-toggle > .artifact-panel__tab-btn {
  display: inline-flex;
  align-items: center;
  padding: 6px 14px;
  font-size: 12px;
  font-weight: 500;
  font-family: system-ui, -apple-system, sans-serif;
  color: var(--artifact-muted);
  background: transparent;
  border: none;
  border-radius: 8px;
  cursor: pointer;
  transition: background 0.15s ease, color 0.15s ease, box-shadow 0.15s ease;
  white-space: nowrap;
}

.artifact-panel__tab-toggle > .artifact-panel__tab-btn:first-child {
  border-radius: 8px;
}

.artifact-panel__tab-toggle > .artifact-panel__tab-btn:last-child {
  border-radius: 8px;
}

.artifact-panel__tab-toggle > .artifact-panel__tab-btn:hover {
  color: var(--artifact-text);
}

.artifact-panel__tab-toggle > .artifact-panel__tab-btn.is-active {
  background: var(--artifact-bg-elevated);
  color: #fff;
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.15);
}

/* --- Hide old tabs nav --- */
.artifact-panel__tabs {
  display: none !important;
}
```

- [ ] **Step 2: Verify visually**

Switch between Preview and Code tabs. Active tab should have a #333 elevated background with subtle shadow. Inactive tabs should be transparent with muted text. Transitions should be smooth 0.15s.

- [ ] **Step 3: Commit**

```bash
git add app/static/css/artifact-panel.css
git commit -m "style(artifacts): restyle tab toggle to Claude pill design"
```

---

### Task 4: Restyle Artifact Pills in Chat

**Files:**
- Modify: `app/static/css/artifact-panel.css` (pill section, ~lines 1965-2076)

- [ ] **Step 1: Replace the active pill styles**

Replace from `/* === PILL LAYOUT (ACTIVE STYLES) === */` through `.artifact-chat-pill__language::after` with:

```css
/* ===========================
   PILL LAYOUT — Claude Style
   =========================== */

button.artifact-chat-pill {
  border: 0 !important;
  background: transparent;
  padding: 0;
  text-align: left !important;
  cursor: pointer !important;
  width: 100% !important;
}

button.artifact-chat-pill {
  display: flex !important;
  align-items: center !important;
  justify-content: space-between !important;
  padding: 12px 16px !important;
  border-radius: 12px !important;
  background: rgba(255, 255, 255, 0.03) !important;
  border: 1px solid var(--artifact-border, rgba(255, 255, 255, 0.06)) !important;
  box-shadow: none !important;
  backdrop-filter: none !important;
  transition: background 0.12s ease, border-color 0.12s ease !important;
}

button.artifact-chat-pill:hover {
  background: rgba(255, 255, 255, 0.05) !important;
  border-color: rgba(255, 255, 255, 0.12) !important;
  box-shadow: none !important;
}

.artifact-chat-pill__content {
  width: 100% !important;
  display: flex !important;
  align-items: center !important;
  justify-content: space-between !important;
  gap: 12px !important;
  min-width: 0 !important;
}

/* Left side */
.artifact-chat-pill__header {
  display: flex !important;
  align-items: center !important;
  gap: 12px !important;
  min-width: 0 !important;
  flex: 1 1 auto !important;
}

/* Icon — accent diamond */
.artifact-chat-pill__icon {
  width: 32px;
  height: 32px;
  border-radius: 8px;
  display: grid;
  place-items: center;
  background: rgba(124, 111, 224, 0.12);
  box-shadow: none;
  flex: 0 0 auto;
}

.artifact-chat-pill__icon i {
  font-size: 14px;
  color: var(--artifact-accent, #7c6fe0);
  opacity: 1;
}

/* Filename + meta */
.artifact-chat-pill__info {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}

.artifact-chat-pill__title {
  font-weight: 500;
  font-size: 14px;
  line-height: 1.2;
  color: var(--artifact-text, rgba(255, 255, 255, 0.88));
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.artifact-chat-pill__meta {
  display: flex !important;
  align-items: center !important;
  flex-direction: row !important;
  gap: 6px !important;
  font-size: 12px !important;
  color: var(--artifact-muted, rgba(255, 255, 255, 0.5)) !important;
  white-space: nowrap !important;
}

.artifact-chat-pill__language::after {
  content: "·";
  margin-left: 6px;
  opacity: 0.6;
}
```

- [ ] **Step 2: Verify visually**

Send a message that creates an artifact. The pill in chat should be a clean, minimal card: no blue border glow, no heavy shadow, no backdrop-filter. Just a subtle border with the title and a small purple icon.

- [ ] **Step 3: Commit**

```bash
git add app/static/css/artifact-panel.css
git commit -m "style(artifacts): restyle chat pills to Claude minimal design"
```

---

### Task 5: Restyle Resize Handle and Content Area Spacing

**Files:**
- Modify: `app/static/css/artifact-panel.css` (resize handle ~lines 222-282, content areas)

- [ ] **Step 1: Update resize handle to be more subtle**

Replace the resize handle section (`.artifact-panel__resize-handle` through `.artifact-panel__resize-handle:active::after`) with:

```css
/* ==================== */
/* RESIZE HANDLE — Subtle */
/* ==================== */

.artifact-panel__resize-handle {
  position: absolute;
  top: 0;
  left: 0;
  bottom: 0;
  width: 6px;
  cursor: ew-resize;
  background: transparent;
  z-index: 9999;
  user-select: none;
  -webkit-user-select: none;
  transition: background 0.12s ease;
}

.artifact-panel__resize-handle:hover {
  background: rgba(255, 255, 255, 0.04);
}

.artifact-panel__resize-handle:active {
  background: rgba(255, 255, 255, 0.08);
}

.artifact-panel.is-open .artifact-panel__resize-handle {
  pointer-events: auto;
}

/* Wider invisible hit area */
.artifact-panel__resize-handle::before {
  content: '';
  position: absolute;
  top: 0;
  left: -8px;
  width: 22px;
  height: 100%;
  cursor: ew-resize;
}

/* Visible grip indicator */
.artifact-panel__resize-handle::after {
  content: '';
  position: absolute;
  top: 50%;
  left: 50%;
  transform: translate(-50%, -50%);
  width: 3px;
  height: 32px;
  background: rgba(255, 255, 255, 0.1);
  border-radius: 2px;
  transition: background 0.12s ease, height 0.12s ease, opacity 0.12s ease;
  opacity: 0;
}

.artifact-panel__resize-handle:hover::after {
  background: rgba(255, 255, 255, 0.15);
  height: 48px;
  opacity: 1;
}

.artifact-panel__resize-handle:active::after {
  background: rgba(255, 255, 255, 0.3);
  height: 64px;
  opacity: 1;
}
```

- [ ] **Step 2: Add content area spacing overrides**

Add these rules at the end of the content area section (after the existing preview/code tab styles):

```css
/* ==================== */
/* CONTENT AREA — Claude Spacing */
/* ==================== */

.artifact-panel__content {
  padding: 20px;
}

.artifact-panel__preview-frame {
  border-radius: 8px;
  border: 1px solid var(--artifact-border-subtle);
}

/* Code viewer background */
.artifact-panel__code-viewer pre,
.artifact-panel__code-viewer code {
  background: #1e1e1e;
  border-radius: 8px;
}

/* File tree items */
.artifact-file-node {
  height: 32px;
  padding-left: 12px;
  transition: background 0.12s ease;
}

.artifact-file-node:hover {
  background: var(--artifact-hover);
}

/* Scrollbar styling */
.artifact-panel ::-webkit-scrollbar {
  width: 6px;
}

.artifact-panel ::-webkit-scrollbar-track {
  background: transparent;
}

.artifact-panel ::-webkit-scrollbar-thumb {
  background: rgba(255, 255, 255, 0.1);
  border-radius: 3px;
}

.artifact-panel ::-webkit-scrollbar-thumb:hover {
  background: rgba(255, 255, 255, 0.18);
}
```

- [ ] **Step 3: Verify visually**

Resize the panel. The grip handle should be invisible until hovered (then fades in). Content padding should feel more spacious. Scrollbars should be thin and subtle.

- [ ] **Step 4: Commit**

```bash
git add app/static/css/artifact-panel.css
git commit -m "style(artifacts): refine resize handle, content spacing, and scrollbars"
```

---

### Task 6: Add Loading Skeleton and Smooth Tab Transitions

**Files:**
- Modify: `app/static/css/artifact-panel.css` (animations section)

- [ ] **Step 1: Add skeleton and transition animations**

Add at the end of the file (before any media queries for `prefers-reduced-motion`):

```css
/* ==================== */
/* LOADING & TRANSITION ANIMATIONS */
/* ==================== */

/* Skeleton shimmer for loading state */
@keyframes artifactShimmer {
  0% { background-position: -200% 0; }
  100% { background-position: 200% 0; }
}

.artifact-panel__loading {
  background: linear-gradient(
    90deg,
    rgba(255, 255, 255, 0.02) 25%,
    rgba(255, 255, 255, 0.06) 50%,
    rgba(255, 255, 255, 0.02) 75%
  );
  background-size: 200% 100%;
  animation: artifactShimmer 1.5s ease-in-out infinite;
  border-radius: 8px;
  min-height: 200px;
}

/* Tab content crossfade */
.artifact-panel__tab-content {
  transition: opacity 0.15s ease;
}

.artifact-panel__tab-content.is-hidden {
  opacity: 0;
  pointer-events: none;
  position: absolute;
}

.artifact-panel__tab-content.is-visible {
  opacity: 1;
  pointer-events: auto;
  position: relative;
}

/* Mobile panel: slide up from bottom */
@media (max-width: 768px) {
  .artifact-panel {
    transform: translateY(100%) !important;
    transition:
      transform 0.3s cubic-bezier(0.32, 0.72, 0, 1),
      opacity 0.3s cubic-bezier(0.32, 0.72, 0, 1) !important;
  }

  .artifact-panel.is-open,
  body.artifact-panel-open .artifact-panel {
    transform: translateY(0) !important;
  }
}
```

- [ ] **Step 2: Verify visually**

1. Desktop: open/close the panel — should feel like a smooth spring (0.3s, no bounce).
2. Mobile: panel should slide up from bottom instead of appearing.
3. Switch tabs — content should crossfade smoothly.

- [ ] **Step 3: Commit**

```bash
git add app/static/css/artifact-panel.css
git commit -m "style(artifacts): add loading skeleton and smooth tab transitions"
```

---

### Task 7: Final Visual Pass — Type Badge, Version Selector, Composer Input

**Files:**
- Modify: `app/static/css/artifact-panel.css`

- [ ] **Step 1: Restyle the type badge**

Find the `.artifact-panel__type-badge` rule and replace it with:

```css
.artifact-panel__type-badge {
  display: inline-flex;
  align-items: center;
  padding: 2px 8px;
  font-size: 11px;
  font-weight: 500;
  font-family: system-ui, -apple-system, sans-serif;
  color: var(--artifact-muted);
  background: rgba(255, 255, 255, 0.04);
  border: 1px solid var(--artifact-border);
  border-radius: 6px;
  text-transform: lowercase;
  letter-spacing: 0.01em;
}
```

- [ ] **Step 2: Restyle the version selector**

Find the `.artifact-panel__version-label select` rules and replace with:

```css
.artifact-panel__version-label {
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 12px;
  color: var(--artifact-muted);
}

.artifact-panel__version-label span {
  font-weight: 500;
}

.artifact-panel__version-label select {
  padding: 2px 8px;
  font-size: 12px;
  font-family: system-ui, -apple-system, sans-serif;
  color: var(--artifact-text);
  background: var(--artifact-bg);
  border: 1px solid var(--artifact-border);
  border-radius: 6px;
  cursor: pointer;
  transition: border-color 0.12s ease;
}

.artifact-panel__version-label select:hover {
  border-color: rgba(255, 255, 255, 0.12);
}

.artifact-panel__version-label select:focus {
  outline: 2px solid var(--artifact-accent);
  outline-offset: 2px;
  border-color: var(--artifact-accent);
}
```

- [ ] **Step 3: Restyle composer input (if it exists in CSS)**

Add or replace composer styles:

```css
/* ==================== */
/* COMPOSER — Claude Style Input */
/* ==================== */

.artifact-panel__composer-input {
  width: 100%;
  height: 44px;
  padding: 0 44px 0 16px;
  font-size: 14px;
  font-family: system-ui, -apple-system, sans-serif;
  color: var(--artifact-text);
  background: rgba(255, 255, 255, 0.03);
  border: 1px solid var(--artifact-border);
  border-radius: 12px;
  outline: none;
  transition: border-color 0.15s ease, box-shadow 0.15s ease;
}

.artifact-panel__composer-input::placeholder {
  color: var(--artifact-muted);
}

.artifact-panel__composer-input:focus {
  border-color: var(--artifact-accent);
  box-shadow: 0 0 0 3px rgba(124, 111, 224, 0.1);
}

.artifact-panel__composer-submit {
  position: absolute;
  right: 8px;
  top: 50%;
  transform: translateY(-50%);
  width: 32px;
  height: 32px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--artifact-accent);
  border: none;
  border-radius: 8px;
  color: #fff;
  cursor: pointer;
  opacity: 0;
  transition: opacity 0.12s ease, background 0.12s ease;
  pointer-events: none;
}

.artifact-panel__composer-input:not(:placeholder-shown) ~ .artifact-panel__composer-submit,
.artifact-panel__composer-submit.is-visible {
  opacity: 1;
  pointer-events: auto;
}

.artifact-panel__composer-submit:hover {
  background: #6b5ec7;
}
```

- [ ] **Step 4: Verify visually**

1. Type badge should be lowercase, subtle, no bright colors.
2. Version selector should be a compact dropdown matching the panel theme.
3. Composer input should have a clean rounded style with accent focus ring.

- [ ] **Step 5: Commit**

```bash
git add app/static/css/artifact-panel.css
git commit -m "style(artifacts): restyle type badge, version selector, and composer input"
```
