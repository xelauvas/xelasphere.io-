# Spec: Golden Ratio Frontend Pass — Batch 1

**Status:** Ready for implementation
**Scope:** XelaSphere frontend — 5 discrete changes, ~6 files, no behavior changes
**Estimated effort:** 30–60 min implementation + visual QA
**Date:** 2026-04-24
**Author:** Xela Uvas

---

## Context

Following a full frontend audit for golden-ratio opportunities, we selected five high-leverage, low-risk changes that introduce intentional φ proportions where the current values are arbitrary or blocky.

Deferred items (Fibonacci spacing refactor, animation duration retuning, sub-3% width nudges, display type scale) were consciously excluded — they deliver subtle benefit at meaningful QA cost and belong in a separate pass.

**φ = 1.618 · 1/φ = 0.618**

The goal isn't "more φ everywhere." It's replacing arbitrary numbers with a systematic ratio at the handful of places where the proportion is actually felt.

---

## Changes

### 1. Body line-height → φ

**File:** `app/static/css/design-tokens.css`
**Current:** `--leading-relaxed: 1.625`; body copy defaults to `--leading-normal` (1.5)
**New:**

```css
--leading-relaxed: 1.618;
```

Body / paragraph / markdown-rendered content defaults to `var(--leading-relaxed)`.

**Rationale:** The token is already nearly there — this is a 0.4% nudge that makes the value intentional and sets body copy rhythm to φ.

**Implementation note (flagged ambiguity):** Selector scope for "body text" must be decided in the plan — candidates are `body {}`, `body + p`, or `body + p + .message-content` (where assistant markdown renders). UI chrome (labels, buttons) must stay on `--leading-normal` / `--leading-tight`.

**Acceptance:**

- [ ] Body text, `<p>`, and markdown-rendered content render with line-height 1.618
- [ ] Labels, buttons, UI chrome stay on `--leading-normal` or `--leading-tight` (no regressions)

---

### 2. Dashboard grid → φ split

**File:** `app/static/css/dashboard.css`
**Current:**

```css
grid-template-columns: 2fr 1fr;
```

**New:**

```css
grid-template-columns: 1.618fr 1fr;
```

**Rationale:** 2:1 reads as blocky. φ is the canonical "primary content + companion panel" proportion.

**Acceptance:**

- [ ] Primary column ≈ 61.8% of available width; companion ≈ 38.2%
- [ ] No content overflow / wrapping regressions at existing breakpoints
- [ ] Responsive collapse to single-column below tablet unchanged

---

### 3. Artifact panel default → φ (62/38) with snap points

**Files:**
- `app/static/css/artifact-panel.css`
- `app/static/js/artifactPanel.js` (resize handler)

**Current:** 50/50 default via `--artifact-split-left: 1fr; --artifact-split-right: 1fr;`, user-resizable, width persisted to localStorage.

**New:**

- Default: chat 61.8%, artifact 38.2%

```css
--artifact-split-left: 1.618fr;
--artifact-split-right: 1fr;
```

- Add snap points at 38.2% and 61.8% with ±2% tolerance (supports chat-primary and code-primary workflows via the inverse ratio)
- Keep drag behavior otherwise unchanged
- **Do not override saved preferences** — the new default only applies to fresh loads with no stored width

**Rationale:** 50/50 reads as undecided. φ default makes chat primary; bidirectional snap respects both reading and coding flows.

**Acceptance:**

- [ ] Fresh load with no saved preference → 61.8 / 38.2
- [ ] Returning user with saved width → saved width respected
- [ ] Dragging release within ~36–40% snaps to exactly 38.2%
- [ ] Dragging release within ~60–64% snaps to exactly 61.8%
- [ ] Dragging release anywhere else lands at drag position (no snap)

---

### 4. Upgrade pricing card → golden rectangle

**Files:** `app/static/css/upgrade.css` (and `app/templates/upgrade.html` if any inline styles)
**Current:**

```css
.pricing-card {
  min-height: 520px;
  padding: 32px;
}
```

**New:**

```css
.pricing-card {
  aspect-ratio: 1 / 1.618;
  padding: 32px;
  /* min-height removed */
}
```

**Rationale:** Pricing cards are the most "designed" element on the upgrade page. A golden rectangle reads as intentional craftsmanship and scales gracefully with column width.

**Implementation note (flagged):** `aspect-ratio` is a *preferred* geometry — browser default `min-block-size: auto` already lets content push the card taller if it genuinely overflows. Do not add defensive `min-height` to "handle" this case; the browser already does.

**Acceptance:**

- [ ] Cards render as golden rectangles (height ≈ width × 1.618) at desktop + tablet
- [ ] Content fits without overflow at the narrowest supported column width
- [ ] If content genuinely exceeds φ height at small widths, card grows vertically (don't clip — aspect-ratio yields to content)
- [ ] Check all pricing tiers (free / pro / enterprise or whichever exist)

---

### 5. Modal size scale → φ-keyed three-tier

**Files:** `app/static/css/design-tokens.css` (add tokens), modal component CSS files (consume tokens)
**Current:** Widths chosen ad-hoc — profile 720, dialog 740, no small-modal standard.

**New tokens:**

```css
--modal-sm: 445px;   /* 720 / φ — alerts, confirmations */
--modal-md: 720px;   /* baseline — forms, settings */
--modal-lg: 1165px;  /* 720 × φ — artifact share, rich previews */
```

**Migration:**

- Profile modal (720) → `--modal-md`
- Dialog (740) → `--modal-md` (snap the 20px drift)
- Any existing small / confirmation modals → `--modal-sm`
- Any existing large / rich-preview modals → `--modal-lg`

**Implementation note (flagged ambiguity):** Modal inventory must be done in the plan step — identify every modal/dialog in the CSS tree, classify each by purpose, and assign a token. No guessing.

**Rationale:** A φ-keyed scale gives small/medium/large a proportional relationship instead of being picked per-dialog.

**Acceptance:**

- [ ] All modals reference one of the three tokens — no raw pixel widths in modal files
- [ ] Dialog's 740→720 doesn't clip content (verify each dialog that used 740)
- [ ] Tokens are documented with a comment block in `design-tokens.css` so future modals inherit the system

---

## Out of scope (explicitly deferred, do not add)

Each of the below was excluded for a specific reason. Resist "while we're here" expansion.

- **Spacing scale Fibonacci refactor** (4/8/12/16/20/24/32/40/48 → 4/8/12/20/32/52/84/136). Cascades through every surface; cost >> benefit for this batch.
- **Animation duration retuning** (150→162ms etc.). Below perceptual threshold.
- **Sub-3% width nudges** (chat stage 1200→1242, right sidebar 320→331, message bubble / composer widths). Mathematically cleaner, perceptually invisible.
- **Display type scale → √φ.** Needs a visual mock for the 36→41px auth hero jump before committing. Separate pass.
- **Border-radius scale φ-keying.** Radii are perceptually notched (4/8/16); φ here is over-engineering.

---

## Testing checklist

- [ ] Visual smoke test on dashboard — light + dark theme
- [ ] Artifact panel: fresh load shows φ split; saved preference respected; snap points trigger correctly
- [ ] Upgrade page: all pricing tiers at 1440px, 1024px, 768px — no overflow
- [ ] Every modal opens at its assigned token size
- [ ] Chat messages and markdown content render at line-height 1.618
- [ ] No layout shifts on mobile breakpoints (375px)

---

## Files touched (estimated)

- `app/static/css/design-tokens.css` — `--leading-relaxed` value + `--modal-sm/md/lg` tokens
- `app/static/css/dashboard.css` — `grid-template-columns`
- `app/static/css/artifact-panel.css` — default split custom properties
- `app/static/js/artifactPanel.js` — snap logic in resize handler
- `app/static/css/upgrade.css` — pricing card geometry
- Modal consumer CSS files (inventoried during planning) — migrate hard-coded widths to tokens

No schema changes. No API changes. No user-facing behavior changes beyond visual proportion.

---

## Handoff notes

- This spec was produced after a full audit. If you see an "obvious" φ opportunity not listed above, it's almost certainly in the deferred list — check there before adding it. The deferred items were excluded deliberately, not overlooked.
- If a change reveals an unexpected regression (e.g. pricing card content genuinely doesn't fit at φ ratio on one tier), fall back to the minimum adjustment that preserves the φ intent rather than reverting the whole change.
- After implementation: visual QA at 1440 / 1024 / 768 / 375 px, light + dark theme, then commit as a single focused PR titled "φ pass batch 1" or similar.

---

## Ambiguity flags surfaced during review (for the plan to resolve)

1. **Change #1 selector scope** — pick from `body {}`, `body + p`, or `body + p + .message-content`. Plan step should make an explicit choice and list every selector touched.
2. **Change #4 aspect-ratio overflow behavior** — relying on browser default `min-block-size: auto` is intentional; do not add defensive `min-height` to "handle" overflow.
3. **Change #5 modal inventory** — sweep required. Plan step should `grep` the CSS tree for `.modal`, `[role="dialog"]`, `.dialog`, and classify each result before migrating.
