# Artifact Panel UI Refresh — Design Spec

**Date:** 2026-04-05
**Status:** Draft
**Scope:** CSS/HTML-only visual refresh. No backend changes. No feature changes.

## Problem

The artifact panel works but doesn't feel like Claude.ai. The header, tabs, colors, spacing, and animations all diverge from Claude's clean, minimal aesthetic. Users expect the polished feel they see on claude.ai.

## Goal

Restyle the artifact panel to match Claude.ai's visual language while preserving all existing functionality. The user should feel like they're using a Claude-quality product.

## What Does NOT Change

- All existing features (versioning, editing, diffing, sharing, composer)
- Backend endpoints and data model
- JavaScript logic in `artifactPanel.js`
- Mobile full-screen overlay behavior
- Desktop CSS Grid split-view layout
- Resize handle functionality

## What Changes

All changes are in `app/static/css/artifact-panel.css` with minor HTML structure tweaks in `app/static/js/artifactPanel.js` (class names, element order).

---

## 1. Color Palette

Replace current color values with Claude's warmer, lower-contrast palette.

| Token | Current | New | Usage |
|-------|---------|-----|-------|
| `--artifact-bg` | `#212121` | `#2b2b2b` | Panel background |
| `--artifact-bg-elevated` | (none) | `#333333` | Header, tab bar, cards |
| `--artifact-border` | `rgba(255,255,255,0.08)` | `rgba(255,255,255,0.06)` | All borders |
| `--artifact-border-subtle` | (none) | `rgba(255,255,255,0.04)` | Inner separators |
| `--artifact-text` | `rgba(255,255,255,0.92)` | `rgba(255,255,255,0.88)` | Primary text |
| `--artifact-muted` | `rgba(255,255,255,0.62)` | `rgba(255,255,255,0.5)` | Secondary text, icons |
| `--artifact-hover` | `rgba(255,255,255,0.06)` | `rgba(255,255,255,0.05)` | Hover states |
| `--artifact-active` | (none) | `rgba(255,255,255,0.08)` | Active/pressed states |
| `--artifact-accent` | `#3b82f6` | `#7c6fe0` | Accent (active tabs, focus rings) |
| `--artifact-shadow` | `rgba(0,0,0,0.4)` | `rgba(0,0,0,0.25)` | Panel shadow (softer) |

All colors defined as CSS custom properties at the top of `artifact-panel.css` so they cascade everywhere.

---

## 2. Header

**Current:** 56px height, switch button + title on left, copy/publish buttons on right. Heavy bottom border.

**New:** Claude-style minimal header.

```
┌─────────────────────────────────────────────────────────┐
│  ◀  Counter App                    jsx  │  ↗  ⋯  ✕  │
│      v3 · Apr 5                         │            │
└─────────────────────────────────────────────────────────┘
```

- Height: 48px
- Background: `--artifact-bg-elevated`
- Bottom border: 1px `--artifact-border-subtle` (barely visible)
- Left side: back arrow (when multiple artifacts), title (15px, weight 500), subtitle row (version + date, 12px, muted)
- Right side: language badge (pill, 11px), share icon, overflow menu (three dots), close button
- No heavy borders, no visible button outlines — icons only, hover reveals background
- Title truncates with ellipsis at 200px max-width

---

## 3. Tab Bar

**Current:** Old tabs hidden with `display: none`, replaced by pill-toggle (Preview/Code only). Edit/Diff/Composer accessible elsewhere.

**New:** Claude-style segmented control with all relevant tabs.

```
┌──────────────────────────────────┐
│  [ Preview ]  [ Code ]  [ ⋯ ]   │
└──────────────────────────────────┘
```

- Container: pill-shaped background (`--artifact-bg`), 4px padding, border-radius 10px
- Tabs: 12px font, 6px 14px padding, border-radius 8px
- Active tab: `--artifact-bg-elevated` background, white text, subtle shadow `0 1px 2px rgba(0,0,0,0.15)`
- Inactive tab: transparent background, muted text, no border
- Hover: slightly brighter text, no background change
- Transition: background 0.15s ease, color 0.15s ease
- Overflow button ("...") reveals Edit, Diff, Composer in a dropdown
- Tab bar sits directly below header with 8px vertical padding above and below
- Total tab bar height: ~36px

---

## 4. Content Area

**Current:** Standard padding, code blocks with existing Prism styling.

**New:** 
- Content padding: 20px (up from 14px)
- Preview iframe: border-radius 8px, 1px border `--artifact-border-subtle`
- Code viewer: background `#1e1e1e` (VS Code dark), border-radius 8px
- File tree: left padding 12px, items 32px height (up from default), hover `--artifact-hover`
- Scrollbar: 6px width, thumb `rgba(255,255,255,0.1)`, rounded

---

## 5. Action Buttons

**Current:** Outlined buttons with visible borders and text labels.

**New:** Claude-style ghost buttons — icon-only by default, tooltip on hover.

- Size: 32px square
- Background: transparent
- Border: none
- Icon: 16px, color `--artifact-muted`
- Hover: background `--artifact-hover`, icon color `--artifact-text`
- Active: background `--artifact-active`
- Border-radius: 8px
- Tooltip: 11px, dark bg (#1a1a1a), appears after 400ms delay, 4px below button

Specific buttons:
- **Copy**: clipboard icon, tooltip "Copy"
- **Download**: download icon, tooltip "Download ZIP"  
- **Share**: external-link icon, tooltip "Share"
- **Close**: X icon, always visible, slightly larger (18px icon)

---

## 6. Artifact Pills (Chat)

**Current:** Card with code preview, title, language, version.

**New:** Claude-style minimal pill.

```
┌────────────────────────────────────┐
│  ◇  Counter App          jsx  →  │
└────────────────────────────────────┘
```

- Height: 44px
- Background: `rgba(255,255,255,0.03)`
- Border: 1px `--artifact-border`
- Border-radius: 12px
- Left: diamond icon (◇) in accent color
- Center: title (14px, weight 500), truncated
- Right: language badge (muted pill) + chevron right icon
- Hover: background `rgba(255,255,255,0.05)`, border slightly brighter
- No code preview in the pill (cleaner)
- Click opens/focuses the artifact panel

---

## 7. Animations

### Panel Open/Close
- Duration: 0.3s (up from 0.25s)
- Easing: `cubic-bezier(0.32, 0.72, 0, 1)` (Apple-style spring)
- Desktop: fade in from right (opacity 0→1, subtle translateX(8px)→0)
- Mobile: slide up from bottom (translateY(100%)→0)

### Tab Switch
- Content crossfade: 0.15s ease
- Active indicator: 0.2s ease (smooth slide between tabs)
- No jarring content jump — old content fades while new loads

### Button Hover
- All transitions: 0.12s ease (faster than current 0.15s for snappier feel)

### Loading State
- Skeleton pulse: subtle shimmer on content area while preview loads
- Duration: 1.5s infinite, `rgba(255,255,255,0.02)` → `rgba(255,255,255,0.06)` → `rgba(255,255,255,0.02)`

---

## 8. Version Selector

**Current:** Dropdown with version numbers.

**New:** Compact pill in header subtitle row.

```
v3 · Apr 5  ▾
```

- Inline with header subtitle (after title)
- Click reveals dropdown: list of versions with dates
- Active version has a checkmark
- Dropdown: `--artifact-bg-elevated` background, 8px border-radius, soft shadow
- Each item: 36px height, 14px left padding, hover `--artifact-hover`

---

## 9. Diff View

**Current:** CodeMirror MergeView or fallback side-by-side.

**New:** Keep existing diff logic, restyle containers:
- Version selector dropdowns: match the compact pill style from section 8
- Mode toggle (side-by-side / unified): small segmented control, same style as tab bar
- Changed file list: compact, 28px items, colored badges (green +, red -, yellow ~)
- Diff gutters: narrower (40px), muted line numbers

---

## 10. Composer

**Current:** Text input + submit button.

**New:** Claude-style input:
- Input: full-width, 44px height, border-radius 12px, border `--artifact-border`, background `rgba(255,255,255,0.03)`
- Placeholder: "Describe your changes..." in muted text
- Focus: border transitions to `--artifact-accent` with subtle glow
- Submit: arrow-up icon button, 32px, appears only when input has text, accent background
- Position: fixed at bottom of panel with 12px padding

---

## Files Changed

| File | Change |
|------|--------|
| `app/static/css/artifact-panel.css` | Full restyle — colors, header, tabs, buttons, pills, animations |
| `app/static/js/artifactPanel.js` | Minor HTML structure tweaks (element order, class names for new styles) |

No backend changes. No new files. No new dependencies.

---

## Testing

- Verify all 6 render engines still work (HTML, React, SVG, Markdown, Mermaid, Image)
- Verify version switching, editing, diffing, sharing still function
- Test panel resize on desktop
- Test mobile full-screen overlay
- Check dark mode consistency (this is already dark-only)
- Verify artifact pills in chat still clickable and open panel
