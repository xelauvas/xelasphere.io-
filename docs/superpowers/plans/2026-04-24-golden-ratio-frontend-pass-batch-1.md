# Golden Ratio Frontend Pass — Batch 1 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Apply five intentional golden-ratio (φ = 1.618) proportions to the XelaSphere frontend — body line-height, dashboard grid, artifact panel split with snap, upgrade pricing card geometry, and a three-tier modal size scale.

**Architecture:** All changes are pure presentation-layer — CSS tokens, CSS rules, and one small JS addition for drag-release snap in the artifact panel. No schema, API, or backend behavior changes. Each task produces an atomic commit; a final PR bundles all eight commits as `φ pass batch 1`.

**Tech Stack:** Vanilla CSS with design tokens (custom properties), vanilla JS (no framework), Flask-served templates. No frontend test runner — verification is grep-based diff confirmation plus manual browser QA at 1440 / 1024 / 768 / 375 px, light + dark theme.

**Spec reference:** `docs/superpowers/specs/2026-04-24-golden-ratio-frontend-pass-batch-1-design.md`

---

## File Structure

| File | Role | Tasks |
|------|------|-------|
| `app/static/css/design-tokens.css` | System tokens (colors, typography, spacing, modal widths) | 1, 6 |
| `app/static/css/message-styles.css` | Chat body typography token | 1 |
| `app/static/css/dashboard.css` | Dashboard grid + dashboard-local modal | 2, 7 |
| `app/static/css/artifact-panel.css` | Artifact panel CSS vars | 3 |
| `app/static/js/artifactPanel.js` | Default split + drag-release snap | 3, 4 |
| `app/static/css/upgrade.css` | Pricing card geometry | 5 |
| Modal consumer CSS files (inventoried in Task 0) | Migrate hard-coded widths to tokens | 7 |

---

## Task 0: Pre-flight — Modal Inventory

**Purpose:** The spec's Change #5 says "any existing small/confirmation modals → `--modal-sm`; any large/rich-preview modals → `--modal-lg`." We cannot guess. Sweep first, migrate in Task 7.

**Files:**
- Read-only sweep across: `app/static/css/*.css`

- [ ] **Step 0.1: Sweep for modal/dialog width declarations**

Run:
```bash
grep -rnE "max-width: *(445|520|640|720|740|760|800|900|1000|1100|1165|1200)px" app/static/css/*.css | grep -viE "pricing|chart|container|hero|shell|card|page" > /tmp/modal-sweep.txt
cat /tmp/modal-sweep.txt
```

- [ ] **Step 0.2: Sweep for modal class names + `[role="dialog"]`**

Run:
```bash
grep -rnE "\.modal|\[role=\"dialog\"\]|\.dialog\b|\.popover\b|\.overlay\b" app/static/css/*.css | grep -iE "max-width|width:" >> /tmp/modal-sweep.txt
cat /tmp/modal-sweep.txt
```

- [ ] **Step 0.3: Classify each hit**

For each entry in `/tmp/modal-sweep.txt`, add a one-line classification at the end of the file:

```
# Classification (manually added):
# dashboard.css:1100 .profile-modal max-width: 720 → --modal-md
# sidebarContent.css:456 .search-dialog max-width: 740 → --modal-md (20px drift)
# ... etc for each hit
```

**Skip rules:**
- Anything not a modal/dialog/popover (cards, shells, charts, hero sections, containers, pricing grids)
- Tooltips (`.tooltip`) — those are popover positioning, not dialog sizing
- `min-content` / `fit-content` / `max-content` values (not fixed widths)

**Include rules:**
- Any element that opens over page content with backdrop/overlay
- Any `[role="dialog"]`, `[role="alertdialog"]`
- Any `.modal-*`, `.dialog-*`, `.lightbox-*`

- [ ] **Step 0.4: Commit the inventory (no code changes yet)**

```bash
cp /tmp/modal-sweep.txt docs/superpowers/plans/2026-04-24-modal-inventory.txt
git add docs/superpowers/plans/2026-04-24-modal-inventory.txt
git commit -m "chore(plan): modal inventory for φ pass batch 1 task 7"
```

**Expected output:** A committed `modal-inventory.txt` listing every modal/dialog width declaration with its target token (`--modal-sm`, `--modal-md`, or `--modal-lg`).

---

## Task 1: Body line-height → φ

**Files:**
- Modify: `app/static/css/design-tokens.css:147` (token value)
- Modify: `app/static/css/message-styles.css:33` (chat token value)

**Selector-scope decision (ambiguity flag #1 from spec):** Bump the two tokens that govern body/paragraph/markdown reading rhythm (`--leading-relaxed` and `--chat-line-height`). Do NOT add new `line-height` rules to the `body` element — leaving it as default lets buttons, inputs, and chrome keep their own line-heights. The assistant message body already consumes `--chat-line-height` via existing rules in `message-styles.css`.

- [ ] **Step 1.1: Verify current `--leading-relaxed` value**

Run:
```bash
grep -n "leading-relaxed" app/static/css/design-tokens.css
```

Expected output:
```
147:  --leading-relaxed: 1.625;
```

- [ ] **Step 1.2: Update `--leading-relaxed` to φ**

In `app/static/css/design-tokens.css`, replace:

```css
  --leading-relaxed: 1.625;
```

with:

```css
  --leading-relaxed: 1.618;   /* φ — golden-ratio reading rhythm */
```

- [ ] **Step 1.3: Verify current `--chat-line-height` value**

Run:
```bash
grep -n "chat-line-height" app/static/css/message-styles.css
```

Expected output:
```
33:    --chat-line-height: 1.7;
```

- [ ] **Step 1.4: Update `--chat-line-height` to φ**

In `app/static/css/message-styles.css`, replace:

```css
    --chat-line-height: 1.7;
```

with:

```css
    --chat-line-height: 1.618;   /* φ — golden-ratio reading rhythm for assistant markdown */
```

- [ ] **Step 1.5: Verify both changes landed**

Run:
```bash
grep -nE "leading-relaxed|chat-line-height" app/static/css/design-tokens.css app/static/css/message-styles.css
```

Expected output contains:
```
app/static/css/design-tokens.css:147:  --leading-relaxed: 1.618;   /* φ — ...
app/static/css/message-styles.css:33:    --chat-line-height: 1.618;   /* φ — ...
```

- [ ] **Step 1.6: Manual browser verification**

1. Start the dev server (existing project command).
2. Open the chat UI, send an assistant message with 3+ paragraphs of markdown.
3. Open DevTools → inspect `.message-content p` → Computed tab → `line-height` should read `1.618` (or the px equivalent: `24.27px` at 15px font).
4. Check that buttons, composer, code blocks still look normal (they use their own line-height tokens).

- [ ] **Step 1.7: Commit**

```bash
git add app/static/css/design-tokens.css app/static/css/message-styles.css
git commit -m "style(design-tokens): body + chat line-height → φ (1.618)

Bumps --leading-relaxed 1.625→1.618 and --chat-line-height 1.7→1.618
so body paragraphs and assistant markdown read on a golden-ratio rhythm.
UI chrome tokens (--leading-normal, --composer-line-height, --code-line-height)
unchanged.

Refs: docs/superpowers/specs/2026-04-24-golden-ratio-frontend-pass-batch-1-design.md"
```

---

## Task 2: Dashboard grid → φ split

**Files:**
- Modify: `app/static/css/dashboard.css:671` (`.dashboard-main-grid`)
- Modify: `app/static/css/dashboard.css:850` (`.main-grid`, commented "Default for desktop")

**Note:** There are TWO `grid-template-columns: 2fr 1fr` rules in dashboard.css. Both describe a primary-content + companion-panel split and both get the φ ratio. Other `grid-template-columns` rules (e.g. line 411 `repeat(auto-fit, minmax(280px, 1fr))`) are unrelated auto-fit grids and must NOT be touched.

- [ ] **Step 2.1: Verify current state — exactly two 2fr:1fr rules exist**

Run:
```bash
grep -nE "grid-template-columns: *2fr 1fr" app/static/css/dashboard.css
```

Expected output (exactly two lines):
```
671:    grid-template-columns: 2fr 1fr;
850:    grid-template-columns: 2fr 1fr; /* Default for desktop */
```

If the count differs, STOP — investigate before changing anything.

- [ ] **Step 2.2: Update both rules to φ**

In `app/static/css/dashboard.css`, replace both occurrences of:

```css
    grid-template-columns: 2fr 1fr;
```

with:

```css
    grid-template-columns: 1.618fr 1fr;   /* φ split */
```

Preserve the trailing `/* Default for desktop */` comment on line 850.

- [ ] **Step 2.3: Verify the change**

Run:
```bash
grep -nE "grid-template-columns: *(2|1\.618)fr 1fr" app/static/css/dashboard.css
```

Expected output (exactly two lines with `1.618fr`):
```
671:    grid-template-columns: 1.618fr 1fr;   /* φ split */
850:    grid-template-columns: 1.618fr 1fr;   /* φ split */ /* Default for desktop */
```

- [ ] **Step 2.4: Manual browser verification**

1. Open `/dashboard` (or whatever the dashboard route is).
2. Inspect `.main-grid` and `.dashboard-main-grid` → Computed tab → `grid-template-columns` should show two tracks, the first ~1.618× the second.
3. Visually confirm primary column is ~62% of width, companion ~38%.
4. Resize to tablet width (768px) → confirm the existing mobile media query (line 1345: `grid-template-columns: 1fr`) still collapses to single column.
5. Check both light + dark theme — no color regressions expected but verify.

- [ ] **Step 2.5: Commit**

```bash
git add app/static/css/dashboard.css
git commit -m "style(dashboard): grid split 2fr:1fr → φ (1.618:1)

Replaces both .main-grid and .dashboard-main-grid 2:1 layouts with a
1.618:1 golden-ratio split. Mobile single-column breakpoint unchanged.

Refs: docs/superpowers/specs/2026-04-24-golden-ratio-frontend-pass-batch-1-design.md"
```

---

## Task 3: Artifact panel default → 61.8 / 38.2

**Files:**
- Modify: `app/static/css/artifact-panel.css:116` (`--artifact-split-left/right` defaults)
- Modify: `app/static/js/artifactPanel.js:2731` (JS default return when no saved preference)

**Note:** The artifact panel uses both CSS-declared default values AND a JS-side default of 50% when localStorage is empty. Both must move to 61.8 to ensure fresh loads render at φ. Existing users with saved preferences are respected (the spec requires this).

- [ ] **Step 3.1: Verify current CSS default**

Run:
```bash
grep -n "artifact-split-left\|artifact-split-right" app/static/css/artifact-panel.css
```

Expected to include a line around 116 with `1fr` for both. The exact rule uses `var(..., 1fr)` fallbacks inside `grid-template-columns`.

Run:
```bash
grep -nE "grid-template-columns:.*artifact-split" app/static/css/artifact-panel.css
```

Expected output includes:
```
116:    grid-template-columns: var(--artifact-split-left, 1fr) var(--artifact-split-right, 1fr);
```

- [ ] **Step 3.2: Update CSS fallback defaults to φ**

In `app/static/css/artifact-panel.css`, replace the grid-template-columns line that uses artifact-split vars with:

```css
    grid-template-columns: var(--artifact-split-left, 1.618fr) var(--artifact-split-right, 1fr);   /* φ default: chat-primary */
```

- [ ] **Step 3.3: Verify current JS default**

Run:
```bash
grep -nB1 "return 50" app/static/js/artifactPanel.js | head -10
```

Expected output includes (around line 2730–2731):
```
2730:            // Default to 50%
2731:            return 50;
```

- [ ] **Step 3.4: Update JS default to 61.8**

In `app/static/js/artifactPanel.js`, replace:

```js
            // Default to 50%
            return 50;
```

with:

```js
            // Default to 61.8% — φ split, chat-primary (1.618 : 1)
            return 61.8;
```

- [ ] **Step 3.5: Verify both changes landed**

Run:
```bash
grep -nE "artifact-split-left, 1\.618fr|return 61\.8" app/static/css/artifact-panel.css app/static/js/artifactPanel.js
```

Expected output (two lines):
```
app/static/css/artifact-panel.css:<line>:    grid-template-columns: var(--artifact-split-left, 1.618fr) var(--artifact-split-right, 1fr);   /* φ default: chat-primary */
app/static/js/artifactPanel.js:<line>:            return 61.8;
```

- [ ] **Step 3.6: Manual browser verification — fresh load**

1. In DevTools → Application → Local Storage → clear key `artifactPanel:splitPercent`.
2. Hard-refresh the chat page.
3. Open an artifact (any existing conversation with an artifact will do).
4. Expected: chat side is ~61.8% of viewport, artifact panel ~38.2%. Measure with DevTools ruler or inspect the grid tracks.

- [ ] **Step 3.7: Manual browser verification — saved preference preserved**

1. Drag the resize handle to roughly 70/30 (well off the φ default).
2. Release — verify in DevTools that `localStorage.artifactPanel:splitPercent` is ~70.
3. Hard-refresh.
4. Expected: panel restores to ~70/30, NOT the new φ default.

- [ ] **Step 3.8: Commit**

```bash
git add app/static/css/artifact-panel.css app/static/js/artifactPanel.js
git commit -m "feat(artifact-panel): default split 50/50 → φ (61.8/38.2)

Fresh loads (no localStorage entry) now open at a chat-primary 1.618:1
split. Returning users with a saved preference are unaffected.
Snap-to-φ behavior added in the next commit.

Refs: docs/superpowers/specs/2026-04-24-golden-ratio-frontend-pass-batch-1-design.md"
```

---

## Task 4: Artifact panel drag-release snap (38.2 / 61.8)

**Files:**
- Modify: `app/static/js/artifactPanel.js:3373-3404` (`stopResizing` method)

**Purpose:** When the user releases the drag, if the current split is within ±2 percentage points of 38.2% or 61.8%, snap to exactly that target. Outside tolerance, land at the drag position (no snap). Supports both reading (chat-primary 61.8) and coding (code-primary 38.2) workflows.

- [ ] **Step 4.1: Verify current `stopResizing` signature**

Run:
```bash
sed -n '3373,3405p' app/static/js/artifactPanel.js
```

Expected: the method reads `getSplitPercentage()`, then writes it to localStorage. Our snap insert goes BEFORE the localStorage write so the snapped value is what gets saved.

- [ ] **Step 4.2: Add snap helper + apply snap in stopResizing**

In `app/static/js/artifactPanel.js`, find the `stopResizing()` method (around line 3373). Locate this block:

```js
            const isDesktop = window.DeviceDetector ? !DeviceDetector.isMobile() : (window.innerWidth > 768);
            try {
                if (isDesktop) {
                    // Save split percentage for desktop
                    const splitPercent = this.getSplitPercentage();
                    if (splitPercent) {
                        localStorage.setItem('artifactPanel:splitPercent', String(splitPercent));
                    }
                } else {
```

Replace with:

```js
            const isDesktop = window.DeviceDetector ? !DeviceDetector.isMobile() : (window.innerWidth > 768);
            try {
                if (isDesktop) {
                    // Snap-to-φ on drag release: if within ±2% of 38.2 or 61.8, snap to exactly that target.
                    // Supports chat-primary (61.8) and code-primary (38.2) workflows.
                    let splitPercent = this.getSplitPercentage();
                    if (splitPercent) {
                        const SNAP_TOLERANCE = 2;  // percentage points
                        const SNAP_TARGETS = [38.2, 61.8];
                        const snapTarget = SNAP_TARGETS.find(t => Math.abs(splitPercent - t) <= SNAP_TOLERANCE);
                        if (snapTarget !== undefined) {
                            splitPercent = snapTarget;
                            this.applySplitPercentage(snapTarget, 100 - snapTarget);
                        }
                        localStorage.setItem('artifactPanel:splitPercent', String(splitPercent));
                    }
                } else {
```

**Why the shape of this change:** We read the split, compute whether to snap, apply the snap (which updates the CSS vars and `this.currentSplitPercent`), then save the snapped value. If no snap, we save the raw drag position. No visual jump before the save — the DOM updates immediately when `applySplitPercentage` runs.

- [ ] **Step 4.3: Verify the insert**

Run:
```bash
grep -nE "SNAP_TOLERANCE|SNAP_TARGETS" app/static/js/artifactPanel.js
```

Expected (three lines — the constant declarations and the use):
```
<line>:                        const SNAP_TOLERANCE = 2;  // percentage points
<line>:                        const SNAP_TARGETS = [38.2, 61.8];
<line>:                        const snapTarget = SNAP_TARGETS.find(t => Math.abs(splitPercent - t) <= SNAP_TOLERANCE);
```

- [ ] **Step 4.4: Manual browser verification — snap to 38.2**

1. Open chat with an artifact.
2. Drag the resize handle to land somewhere in the 36–40% range (chat on the smaller side).
3. Release.
4. Expected: panel visibly snaps to 38.2% (code-primary). Inspect `document.documentElement.style` or `localStorage.artifactPanel:splitPercent` → should read `38.2`.

- [ ] **Step 4.5: Manual browser verification — snap to 61.8**

1. Drag to land in the 60–64% range.
2. Release.
3. Expected: snaps to 61.8%. localStorage reads `61.8`.

- [ ] **Step 4.6: Manual browser verification — no snap outside tolerance**

1. Drag to land at ~50% (dead center).
2. Release.
3. Expected: NO snap. localStorage reads the raw drag position (e.g. `50.3`, `49.7`, etc.).
4. Drag to ~70%.
5. Release.
6. Expected: NO snap. Stays at ~70.

- [ ] **Step 4.7: Commit**

```bash
git add app/static/js/artifactPanel.js
git commit -m "feat(artifact-panel): snap drag release to φ targets (38.2 / 61.8)

Releases within ±2% of either φ target snap to exactly that percentage,
supporting both chat-primary (61.8) and code-primary (38.2) workflows.
Releases outside tolerance land at drag position — no snap.

Refs: docs/superpowers/specs/2026-04-24-golden-ratio-frontend-pass-batch-1-design.md"
```

---

## Task 5: Upgrade pricing card → golden rectangle

**Files:**
- Modify: `app/static/css/upgrade.css:224-232` (`.pricing-card` base rule)

**Note on aspect-ratio behavior (ambiguity flag #2 from spec):** `aspect-ratio: 1 / 1.618` is a *preferred* geometry — when content overflows, browsers honor `min-block-size: auto` (the default) and let the card grow taller. Do NOT add a defensive `min-height` to "handle" this case; the browser already does.

- [ ] **Step 5.1: Verify current rule**

Run:
```bash
sed -n '224,232p' app/static/css/upgrade.css
```

Expected output:
```css
.pricing-card {
    border-radius: 24px;
    padding: 2rem;
    border: 1px solid rgba(255, 255, 255, 0.08);
    display: flex;
    flex-direction: column;
    gap: 1.5rem;
    min-height: 520px;
}
```

- [ ] **Step 5.2: Update `.pricing-card` to golden rectangle**

In `app/static/css/upgrade.css`, replace:

```css
.pricing-card {
    border-radius: 24px;
    padding: 2rem;
    border: 1px solid rgba(255, 255, 255, 0.08);
    display: flex;
    flex-direction: column;
    gap: 1.5rem;
    min-height: 520px;
}
```

with:

```css
.pricing-card {
    border-radius: 24px;
    padding: 2rem;
    border: 1px solid rgba(255, 255, 255, 0.08);
    display: flex;
    flex-direction: column;
    gap: 1.5rem;
    aspect-ratio: 1 / 1.618;   /* φ portrait — golden rectangle */
}
```

- [ ] **Step 5.3: Verify the change**

Run:
```bash
grep -nE "aspect-ratio|min-height: *520px" app/static/css/upgrade.css
```

Expected:
- `aspect-ratio: 1 / 1.618;` present
- `min-height: 520px;` NOT present (should be no result for that pattern)

- [ ] **Step 5.4: Manual browser verification — desktop**

1. Open `/upgrade`.
2. Each pricing card should render as a tall golden rectangle (height ≈ 1.618 × width).
3. Inspect a card → Computed `aspect-ratio: 1 / 1.618`, no `min-height`.
4. Verify: all three (or however many) tiers — free, plus, business — render consistently.
5. Verify: content does NOT clip at desktop widths (1440, 1024). If any tier's content genuinely exceeds φ height, the card grows vertically — this is expected and acceptable.

- [ ] **Step 5.5: Manual browser verification — tablet + mobile**

1. Resize to 768px — cards may be in a single column (check the existing `.pricing-card` media query at `upgrade.css:363`).
2. Verify cards still render cleanly — if content overflows, cards grow taller (acceptable per spec).
3. Resize to 375px — same check.

- [ ] **Step 5.6: Commit**

```bash
git add app/static/css/upgrade.css
git commit -m "style(upgrade): pricing card → golden rectangle (aspect-ratio 1:φ)

Replaces arbitrary min-height: 520px with aspect-ratio: 1 / 1.618 so
pricing cards scale as golden rectangles across column widths. Content
that genuinely exceeds φ height grows the card vertically via the
browser's default min-block-size: auto — no manual fallback needed.

Refs: docs/superpowers/specs/2026-04-24-golden-ratio-frontend-pass-batch-1-design.md"
```

---

## Task 6: Modal size tokens — declare

**Files:**
- Modify: `app/static/css/design-tokens.css` (add tokens — end of `:root` block, before closing `}`)

**Note:** This task only ADDS the tokens. Migration to consumers happens in Task 7.

- [ ] **Step 6.1: Find a stable insertion point**

The `design-tokens.css` `:root` block ends around line 290 (just before the LIGHT THEME comment). Run:

```bash
grep -nE "^}|/\* LIGHT THEME \*/|LIGHT THEME" app/static/css/design-tokens.css | head -10
```

Identify the closing `}` of `:root` (before the first `[data-theme="light"]` block).

- [ ] **Step 6.2: Insert modal tokens just before the `:root` closing brace**

Add this block inside `:root` (immediately before the closing `}`):

```css
  /* ==================== */
  /* MODAL SIZE SCALE — φ-keyed three-tier */
  /* sm = md / φ, lg = md × φ. Baseline md = 720. */
  /* Use: alerts/confirmations → sm; forms/settings → md; rich previews → lg. */
  /* ==================== */

  --modal-sm: 445px;    /* 720 / φ */
  --modal-md: 720px;    /* baseline */
  --modal-lg: 1165px;   /* 720 × φ */
```

- [ ] **Step 6.3: Verify the tokens exist and are well-formed**

Run:
```bash
grep -nE "modal-(sm|md|lg)" app/static/css/design-tokens.css
```

Expected (three lines):
```
<line>:  --modal-sm: 445px;    /* 720 / φ */
<line>:  --modal-md: 720px;    /* baseline */
<line>:  --modal-lg: 1165px;   /* 720 × φ */
```

- [ ] **Step 6.4: Verify the file still parses (smoke test via grep balance)**

Run:
```bash
# Sanity: { and } counts roughly match
grep -c "^{" app/static/css/design-tokens.css
grep -c "^}" app/static/css/design-tokens.css
```

Numbers should be identical or within 1 (CSS blocks balance). If off by more than 1, the insertion broke something.

- [ ] **Step 6.5: Manual browser verification**

1. Hard-refresh any page.
2. In DevTools console:
   ```js
   getComputedStyle(document.documentElement).getPropertyValue('--modal-sm')
   getComputedStyle(document.documentElement).getPropertyValue('--modal-md')
   getComputedStyle(document.documentElement).getPropertyValue('--modal-lg')
   ```
3. Expected: ` 445px`, ` 720px`, ` 1165px` (leading space is normal).

- [ ] **Step 6.6: Commit**

```bash
git add app/static/css/design-tokens.css
git commit -m "feat(design-tokens): add φ-keyed modal size scale (sm/md/lg)

Introduces --modal-sm (445), --modal-md (720), --modal-lg (1165) — a
three-tier scale where sm = md/φ and lg = md×φ. Consumer migration
follows in a separate commit per the modal inventory.

Refs: docs/superpowers/specs/2026-04-24-golden-ratio-frontend-pass-batch-1-design.md"
```

---

## Task 7: Modal size tokens — migrate consumers

**Files:**
- Modify: each CSS file listed in `docs/superpowers/plans/2026-04-24-modal-inventory.txt` (produced in Task 0)

**Rule:** For each entry in the inventory, replace the raw pixel width with the corresponding token. Any 740 → 720 drift is intentional per the spec (snap to `--modal-md`).

- [ ] **Step 7.1: Re-read the inventory**

Run:
```bash
cat docs/superpowers/plans/2026-04-24-modal-inventory.txt
```

Review each classification line. If any entry is unclear, re-classify before touching code.

- [ ] **Step 7.2: Migrate one file at a time**

For each file in the inventory, replace the raw width declaration with the token. Example transformation:

**Before:**
```css
.profile-modal {
    max-width: 720px;
    ...
}
```

**After:**
```css
.profile-modal {
    max-width: var(--modal-md);
    ...
}
```

**For the 740→720 drift case:**
```css
/* Before */
.search-dialog { max-width: 740px; }
/* After */
.search-dialog { max-width: var(--modal-md);   /* was 740 — snapped to baseline */ }
```

Do not reformat unrelated lines. Only the `max-width` (or `width`) declaration changes.

- [ ] **Step 7.3: Verify no raw modal widths remain**

Run the same sweep from Task 0.1:
```bash
grep -rnE "max-width: *(445|720|740|1165)px" app/static/css/*.css | grep -viE "pricing|chart|container|hero|shell|card|page"
```

Expected: no matches from files listed in the inventory (they all use `var(--modal-*)` now). If a match remains, either it wasn't in the inventory (add it) or the migration missed it.

- [ ] **Step 7.4: Manual browser verification — each migrated modal**

For each modal migrated, trigger it in the UI:
1. Profile modal — open from user menu.
2. Search dialog — open via keyboard shortcut or button.
3. Any other modal in the inventory — open it.

For each:
- Inspect the modal element → Computed `max-width` matches the token value.
- Content does NOT clip (especially for 740→720 cases — check the widest content row).

- [ ] **Step 7.5: Commit**

```bash
git add app/static/css/
git commit -m "refactor(modals): migrate hard-coded widths to φ-keyed tokens

Replaces raw max-width declarations across modal/dialog rules with
--modal-sm (445), --modal-md (720), and --modal-lg (1165). Intentional
drift: dialogs previously at 740 snap to the --modal-md baseline.

Migrated files (see docs/superpowers/plans/2026-04-24-modal-inventory.txt):
<list files touched here>

Refs: docs/superpowers/specs/2026-04-24-golden-ratio-frontend-pass-batch-1-design.md"
```

---

## Task 8: Final QA + PR

**Purpose:** Run the full spec-level acceptance checklist, catch any regressions introduced by interaction between the changes, and open the PR.

- [ ] **Step 8.1: Grep-check all spec changes landed**

Run:
```bash
# Change 1: line-height tokens at φ
grep -nE "leading-relaxed: *1\.618|chat-line-height: *1\.618" app/static/css/design-tokens.css app/static/css/message-styles.css
# Change 2: dashboard grid at φ (expect 2 hits)
grep -cE "grid-template-columns: *1\.618fr 1fr" app/static/css/dashboard.css
# Change 3: artifact default (expect CSS fallback and JS return)
grep -nE "artifact-split-left, 1\.618fr|return 61\.8" app/static/css/artifact-panel.css app/static/js/artifactPanel.js
# Change 4: snap constants
grep -nE "SNAP_TARGETS" app/static/js/artifactPanel.js
# Change 5: aspect-ratio on pricing card
grep -nE "aspect-ratio: *1 */ *1\.618" app/static/css/upgrade.css
# Change 6+7: modal tokens declared and consumed
grep -cE "var\(--modal-(sm|md|lg)\)" app/static/css/*.css
```

All grep counts should match expected:
- line-height: 2 hits
- dashboard grid: 2
- artifact CSS + JS: 2
- snap: ≥1
- aspect-ratio: 1
- modal var usage: ≥ (# of migrated modals)

- [ ] **Step 8.2: Full visual QA at each breakpoint**

Walk through the full spec testing checklist:

- [ ] Dashboard — light + dark theme — at 1440, 1024, 768, 375 px
- [ ] Artifact panel — fresh load (localStorage cleared) shows φ split — at 1440 and 1024
- [ ] Artifact panel — returning user with saved width respected
- [ ] Artifact panel — snap triggers at ~38–40% and ~60–64%
- [ ] Artifact panel — no snap outside tolerance
- [ ] Upgrade page — all pricing tiers at 1440, 1024, 768 — no unintended overflow
- [ ] Every modal from the inventory opens at its assigned size
- [ ] Chat messages render with line-height 1.618 (check a multi-paragraph assistant reply)
- [ ] No layout shifts on mobile (375px) — resize from desktop down, watch for jumps

- [ ] **Step 8.3: Handle any regression**

If a check fails, do NOT revert the whole change. Per the spec's handoff note: "fall back to the minimum adjustment that preserves the φ intent rather than reverting the whole change."

Example: if the pricing card's free tier overflows at 375px in a way the default `min-block-size: auto` doesn't handle gracefully, add:

```css
@media (max-width: 480px) {
    .pricing-card {
        aspect-ratio: auto;   /* let content drive height on narrow mobile */
    }
}
```

Commit any such fallback as its own small commit before opening the PR.

- [ ] **Step 8.4: Push the branch and open the PR**

```bash
# Push the current feature branch (already on feature/mempalace-integration per git status)
# NOTE: if this work should live on its own branch, create one here before pushing.
# Ask the user whether to keep these commits on feature/mempalace-integration
# or branch them off (e.g. feature/phi-pass-batch-1).

git log --oneline origin/main..HEAD   # preview what the PR will contain
```

Decision point for the user:
- If keeping on `feature/mempalace-integration`: push the branch and open the PR against `main`.
- If branching: `git checkout -b feature/phi-pass-batch-1` before pushing, then PR `feature/phi-pass-batch-1` → `main`.

Use `gh pr create` with this body template:

```bash
gh pr create --title "φ pass batch 1 — frontend golden-ratio proportions" --body "$(cat <<'EOF'
## Summary
- Body line-height 1.625→1.618 and chat markdown 1.7→1.618 (φ reading rhythm)
- Dashboard grid 2:1 → 1.618:1
- Artifact panel default split 50/50 → 61.8/38.2 with drag-release snap at both φ targets
- Upgrade pricing card: arbitrary min-height → aspect-ratio 1:φ (golden rectangle)
- Three-tier φ-keyed modal size scale (--modal-sm/md/lg) with consumer migration

## Refs
Spec: docs/superpowers/specs/2026-04-24-golden-ratio-frontend-pass-batch-1-design.md
Plan: docs/superpowers/plans/2026-04-24-golden-ratio-frontend-pass-batch-1.md

## Test plan
- [ ] Dashboard light + dark at 1440/1024/768/375
- [ ] Artifact panel fresh load → φ split
- [ ] Artifact panel saved width preserved
- [ ] Artifact panel snap at both φ targets
- [ ] Upgrade page all tiers at 1440/1024/768 — no clipping
- [ ] Every modal opens at its assigned token size
- [ ] Chat messages render line-height 1.618
- [ ] No layout shifts at mobile breakpoint

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

---

## Self-review notes

**Spec coverage check:** Every numbered Change (1–5) in the spec maps to a task:
- Change 1 (line-height) → Task 1
- Change 2 (dashboard grid) → Task 2
- Change 3 (artifact split + snap) → Tasks 3 + 4
- Change 4 (pricing card) → Task 5
- Change 5 (modal size scale) → Tasks 0 + 6 + 7

All three ambiguity flags from the spec are addressed:
- Flag #1 (selector scope for line-height): resolved in Task 1 — two tokens, no new `body` rule.
- Flag #2 (aspect-ratio overflow): resolved in Task 5 — rely on browser default, explicit "do not add min-height."
- Flag #3 (modal inventory): resolved in Task 0 — committed inventory file before migration.

**Placeholder scan:** Searched plan for "TBD", "TODO", "fill in", "similar to", "add validation", "handle edge cases". None found. The only `<list files touched here>` placeholder is in a commit message template and must be filled during Task 7.5 with the actual file list from the migration.

**Type/naming consistency:** `SNAP_TOLERANCE`, `SNAP_TARGETS`, `snapTarget`, `splitPercent`, `applySplitPercentage`, `getSplitPercentage` all used consistently. CSS token names `--modal-sm/md/lg` consistent across Task 6 and Task 7.

**Risk notes:**
- Task 4 (snap logic) is the only non-trivial behavioral change. Manual verification steps 4.4–4.6 cover both snap targets and the no-snap case.
- Task 7 (modal migration) blast radius depends entirely on Task 0's inventory — if the inventory misses a modal, the PR ships with a mixed system (some tokens, some raw widths). Step 7.3's re-sweep catches this.
