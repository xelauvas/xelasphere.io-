# Golden Ratio Frontend Pass — Batch 2 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Apply the remaining two Batch 2 golden-ratio changes after Change 1 was retracted during state-gathering: composer icon sizing (30 → 24 visible, ≥44×44 hit area) and artifact panel resize grip φ progression (32 / 52 / 84).

**Architecture:** Pure presentation-layer changes. No schema, API, or backend behavior changes. Four commits total: one inventory reference (Task 0), one per-consumer migration for composer icons (Task 1), one resize-grip update (Task 2), and a final QA pass into a single draft PR.

**Tech Stack:** Vanilla CSS with design tokens, no JS changes in this batch, Flask-served templates, no frontend test runner — verification is grep-based plus manual browser QA at 1440 / 1024 / 768 / 375 px, light + dark theme, with explicit touch-target verification for Change 2.

**Spec reference:** `docs/superpowers/specs/2026-04-24-golden-ratio-frontend-pass-batch-2-design.md`

**Plan-time resolutions (bound by spec author 2026-04-24):**
- Flag 3: Task 0 composer icon inventory is required — sweep `styles.css`, `dictationModule.js` + its CSS, per-feature composer button CSS. Every match migrates; none can be skipped.
- Flag 4: Change 3 transition MUST reuse existing `transition: background 0.12s ease, height 0.12s ease, opacity 0.12s ease` at `artifact-panel.css:289`. Do NOT introduce new duration token or change the ease function.
- Change 1 (bubble padding) retracted 2026-04-24 — not in this plan. See spec addendum.

---

## File Structure

| File | Role | Tasks |
|------|------|-------|
| `app/static/css/styles.css` | Composer tokens + consumer rules | 0, 1 |
| `app/static/css/mobile.css` | Mobile composer overrides (30px !important) | 0, 1 |
| `app/static/js/dictationModule.js` + associated CSS | Dictation button (if it has its own sizing) | 0, 1 |
| Per-feature composer button CSS (surfaced by Task 0) | Additional composer icon buttons | 0, 1 |
| `app/static/css/artifact-panel.css` | Resize grip `::after` pseudo-element heights | 2 |
| `docs/superpowers/plans/2026-04-24-composer-icon-inventory.txt` | Task 0 reference artifact | 0 |

---

## Task 0: Pre-flight — Composer Icon Inventory

**Purpose:** Spec Change 2 requires every composer icon to migrate in lockstep (missing one ships 24px send + 30px dictation on the same composer). Sweep first, migrate in Task 1.

**Files:**
- Read-only sweep across `app/static/css/*.css` and `app/static/js/*.js`
- Write: `docs/superpowers/plans/2026-04-24-composer-icon-inventory.txt` (commit)

- [ ] **Step 0.1: Find all 30px rules in composer-related files**

Run:
```bash
grep -nE "^[[:space:]]+(width|height|min-width|min-height): *30px" app/static/css/styles.css app/static/css/mobile.css > /tmp/composer-icon-sweep.txt
cat /tmp/composer-icon-sweep.txt
```

Expected to include `styles.css:2194-2197`, `styles.css:2338-2340`, `mobile.css:3892-3895` as minimum. Inspect each hit's surrounding rule (50 lines context) to confirm it's a composer icon button, not unrelated 30px sizing.

- [ ] **Step 0.2: Find all uses of `--composer-icon-size` and `--send-button-size`**

Run:
```bash
grep -rnE "var\(--composer-icon-size\)|var\(--send-button-size\)" app/static/css/ app/static/js/ >> /tmp/composer-icon-sweep.txt
cat /tmp/composer-icon-sweep.txt
```

These token consumers are the "official" composer icon declarations. Every consumer needs updating.

- [ ] **Step 0.3: Check dictationModule CSS + any per-feature composer buttons**

Run:
```bash
# Dictation button — the dictation module lives in dictationModule.js; check for associated CSS
grep -rnE "dictation|\.dictation-btn|\.mic-btn|voiceInput" app/static/css/*.css | grep -iE "width|height|size" | head -20
# Other composer feature buttons — fetchMoreOptions, emailComposer, etc.
grep -rnE "composer.*button|\.send-btn|\.attach-btn|\.emoji-btn|\.plus-btn" app/static/css/*.css | grep -iE "width|height" | head -20
```

Cross-reference each surfaced rule against the selector in the HTML (check `base.html` or `index.html`) to confirm it's rendered inside the composer. If unsure, include it in the inventory as "uncertain — planner decides" rather than silently skipping.

- [ ] **Step 0.4: Classify each hit**

For every hit from 0.1, 0.2, 0.3, add a classification line in the inventory:

```
# Classification:
# styles.css:106       --composer-icon-size: 30px       → token declaration, target: 24px (visible icon)
# styles.css:107       --send-button-size:   30px       → token declaration, target: 44px (container with 10px padding around 24px inner icon)
# styles.css:1851-1854 width/height/min: var(--send-button-size)  → container rule, consumes --send-button-size at new 44px
# styles.css:2194-2197 width/height/min: 30px           → icon container at [selector: identify from 40 lines context], target: 44×44 container + 24×24 inner
# ... etc for each hit
```

**Token strategy decision (document in inventory):**

Currently `--composer-icon-size: 30px` and `--send-button-size: 30px` are both 30px. After this batch:
- `--composer-icon-size` should represent the VISIBLE icon size (SVG/inner element). New value: **24px**.
- `--send-button-size` should represent the CONTAINER (button outer box) size. New value: **44px**.
- This is the **✅ RECOMMENDED path** because it keeps the two tokens semantically distinct, consumers keep the same token name, and the 44px container wraps the 24px icon via `padding: 10px` on the button rule.

If Task 0 inventory surfaces a case where an existing consumer of `--composer-icon-size` is sized as a container (not an icon), flag it for Task 1 decision. Do not rename tokens; do not introduce new tokens unless the inventory shows a consumer that's neither icon nor container.

- [ ] **Step 0.5: Commit the inventory**

```bash
cp /tmp/composer-icon-sweep.txt docs/superpowers/plans/2026-04-24-composer-icon-inventory.txt
git add docs/superpowers/plans/2026-04-24-composer-icon-inventory.txt
git commit -m "chore(plan): composer icon inventory for φ pass batch 2 task 1

Sweep of all 30px icon/button sizing in composer surfaces (styles.css,
mobile.css, dictationModule and per-feature composer button CSS) with
classification for Task 1 migration. Token strategy documented:
--composer-icon-size retains semantic of visible icon (→ 24px);
--send-button-size becomes container dimension (→ 44px, WCAG 2.5.5).

Refs: docs/superpowers/specs/2026-04-24-golden-ratio-frontend-pass-batch-2-design.md"
```

**Expected output:** A committed `composer-icon-inventory.txt` with every 30px composer-related rule classified and a documented token-strategy decision.

---

## Task 1: Composer Icon Migration

**Files:**
- Modify: `app/static/css/styles.css` (tokens + consumer rules per inventory)
- Modify: `app/static/css/mobile.css` (mobile !important overrides per inventory)
- Modify: `app/static/js/dictationModule.js` + associated CSS if the inventory surfaces composer usage
- Modify: per-feature composer button CSS if the inventory surfaces any

**Purpose:** Shrink visible composer icons from 30 → 24 (= 15 × φ) while preserving ≥44×44 tappable area via padding. Touch-target preservation is a **hard accessibility requirement** (WCAG 2.5.5 Level AAA, iOS HIG), not a nice-to-have.

**Spec reference — required CSS pattern:**

```css
.composer-icon-button {
  width: 44px;
  height: 44px;
  padding: 10px;              /* (44 - 24) / 2 */
  display: inline-flex;
  align-items: center;
  justify-content: center;
  background: transparent;
  border: none;
}
.composer-icon-button svg {
  width: 24px;
  height: 24px;
}
```

- [ ] **Step 1.1: Re-read the inventory**

```bash
cat docs/superpowers/plans/2026-04-24-composer-icon-inventory.txt
```

Confirm the migration targets match what you'll do below. If the inventory is missing a case you expected, STOP and investigate — do not improvise.

- [ ] **Step 1.2: Update composer tokens**

In `app/static/css/styles.css` (around line 106):

```css
/* Before */
--composer-icon-size: 30px;
--send-button-size: 30px;

/* After */
--composer-icon-size: 24px;   /* φ — visible icon (15 × φ rounded) */
--send-button-size: 44px;     /* WCAG 2.5.5 AAA tap target — 44×44 container around 24px icon */
```

- [ ] **Step 1.3: Update existing `--send-button-size` consumers to also have padding**

For each rule that uses `var(--send-button-size)` for `width`/`height`/`min-width`/`min-height` (expect styles.css:1851-1854, 2094-2097 at minimum — inventory confirms exact locations): the container is now 44px, but it needs `padding: 10px` to give the icon 24×24 space inside.

Migration pattern for each such rule block:

```css
/* Before */
.some-composer-btn {
    height: var(--send-button-size);
    width: var(--send-button-size);
    min-width: var(--send-button-size);
    min-height: var(--send-button-size);
    /* other properties */
}

/* After */
.some-composer-btn {
    height: var(--send-button-size);
    width: var(--send-button-size);
    min-width: var(--send-button-size);
    min-height: var(--send-button-size);
    padding: 10px;                      /* (44 - 24) / 2 — hit-area around 24px visible icon */
    box-sizing: border-box;             /* ensure padding is inside the 44px container */
    /* other properties */
}
```

If the rule already specifies `padding` or `box-sizing`, integrate carefully — do not clobber existing padding tokens or layout-affecting properties. If the rule's inner SVG/icon has an explicit sized child, verify it's 24×24 (or use `width: var(--composer-icon-size); height: var(--composer-icon-size);` on the inner element).

- [ ] **Step 1.4: Update hardcoded 30px icon rules per inventory**

For each hardcoded `30px` rule identified in the inventory (expect styles.css:2194-2197, 2338-2340, mobile.css:3892-3895 at minimum):

**Read the rule selector first (50 lines context) to determine whether this is a CONTAINER or a VISIBLE ICON rule:**
- If the rule sets dimensions on a `<button>` or `<div class="*-btn">` — it's a container. Replace `30px` with `var(--send-button-size)` (now 44px) and add `padding: 10px; box-sizing: border-box;`.
- If the rule sets dimensions on an `<svg>` or `<i>` or inner icon element — it's a visible icon. Replace `30px` with `var(--composer-icon-size)` (now 24px). No padding needed.
- If genuinely ambiguous, default to container (44 with padding) — shrinking tap targets is worse than oversized icons.

Mobile `!important` rules follow the same logic; preserve `!important` when present.

- [ ] **Step 1.5: Focus ring scoping**

The spec requires focus rings to wrap the visible icon tightly, not the full 44×44 padding. For each migrated button, verify its focus style:

```bash
grep -nE ":focus|:focus-visible|outline" app/static/css/styles.css | grep -iE "composer|send|dictation|attach" | head -20
```

If any focus rule targets the outer button (e.g. `outline: 2px solid ...; outline-offset: 2px;` on `.send-btn`), consider:

- **Preferred:** move `:focus-visible` styling onto the inner SVG/icon element with `outline-offset: -10px` so the ring appears around the 24px icon, not the 44px container.
- **Acceptable if above is too invasive:** leave the outer-button focus ring; note the discrepancy in the Task 1 report. Do not block migration on this.

- [ ] **Step 1.6: Verify the change via grep**

```bash
# Tokens updated
grep -nE "composer-icon-size:|send-button-size:" app/static/css/styles.css

# Expected:
# 106:    --composer-icon-size: 24px;   /* φ — visible icon ...
# 107:    --send-button-size: 44px;     /* WCAG 2.5.5 ...

# No hardcoded 30px icon dims remaining in composer files (excluding shadows, animations, etc.)
grep -nE "^[[:space:]]+(width|height|min-width|min-height): *30px" app/static/css/styles.css app/static/css/mobile.css
# Expected: no hits (every migrated; if any remain, cross-check against inventory — legitimate non-composer uses stay)
```

If grep shows a 30px rule not in the inventory, STOP and re-classify — you may have missed one in Task 0.

- [ ] **Step 1.7: Manual browser verification** — controller will do this, skip.

- [ ] **Step 1.8: Commit**

```bash
git add app/static/css/styles.css app/static/css/mobile.css [any other files migrated per inventory]
git commit -m "feat(composer): icon 30→24 with ≥44×44 touch-target preservation

--composer-icon-size: 30 → 24 (visible icon at 15 × φ)
--send-button-size: 30 → 44 (WCAG 2.5.5 AAA / iOS HIG tap target)
All consumers updated with padding: 10px + box-sizing: border-box so the
44px container wraps a 24px icon. Hardcoded 30px rules migrated per
inventory with container-vs-icon classification.

Mobile !important overrides preserved.

Refs: docs/superpowers/specs/2026-04-24-golden-ratio-frontend-pass-batch-2-design.md
Inventory: docs/superpowers/plans/2026-04-24-composer-icon-inventory.txt"
```

---

## Task 2: Artifact Panel Resize Grip → φ Progression

**Files:**
- Modify: `app/static/css/artifact-panel.css:286-301` (resize handle `::after` pseudo-element rules)

**Purpose:** Replace linear ramp (32 / 48 / 64 — ratios 1.5 / 1.333) with φ progression (32 / 52 / 84 — ratios φ / φ, ≈ φ² from idle to active). Existing 0.12s ease transition preserved exactly per Flag 4 resolution.

- [ ] **Step 2.1: Verify current state**

```bash
sed -n '279,305p' app/static/css/artifact-panel.css
```

Expected output includes three `height:` declarations at lines 286 (idle = 32px), 295 (hover = 48px), 301 (active = 64px), and the transition at line 289 = `transition: background 0.12s ease, height 0.12s ease, opacity 0.12s ease;`.

- [ ] **Step 2.2: Update grip heights**

In `app/static/css/artifact-panel.css`:

**Idle state at line 286:** unchanged (32px stays).

**Hover state at line 295:**
```css
/* Before */
.artifact-panel__resize-handle:hover::after {
  background: var(--accent-muted, rgba(124, 106, 239, 0.15));
  height: 48px;
  opacity: 1;
}

/* After */
.artifact-panel__resize-handle:hover::after {
  background: var(--accent-muted, rgba(124, 106, 239, 0.15));
  height: 52px;                 /* φ × 32 (idle) */
  opacity: 1;
}
```

**Active state at line 301:**
```css
/* Before */
.artifact-panel__resize-handle:active::after {
  background: var(--accent, #7c6aef);
  height: 64px;
  opacity: 1;
}

/* After */
.artifact-panel__resize-handle:active::after {
  background: var(--accent, #7c6aef);
  height: 84px;                 /* φ × 52 (hover), ≈ φ² × 32 */
  opacity: 1;
}
```

**Do NOT touch the transition at line 289** — must remain `transition: background 0.12s ease, height 0.12s ease, opacity 0.12s ease;` per Flag 4 resolution.

- [ ] **Step 2.3: Verify the change**

```bash
grep -nE "height: *(32|48|52|64|84)px" app/static/css/artifact-panel.css | head -10
```

Expected: idle 32, hover 52, active 84. No 48 or 64 remaining in the resize-handle block. (Note: other `height: 32px` / `48px` etc. may exist elsewhere in the file for unrelated elements — only the three rules in the `:hover::after` / `:active::after` cascade are in scope.)

Also confirm transition unchanged:

```bash
sed -n '289p' app/static/css/artifact-panel.css
```

Expected verbatim:
```
  transition: background 0.12s ease, height 0.12s ease, opacity 0.12s ease;
```

- [ ] **Step 2.4: Manual browser verification** — controller will do this, skip.

- [ ] **Step 2.5: Commit**

```bash
git add app/static/css/artifact-panel.css
git commit -m "style(artifact-panel): resize grip φ progression (32 / 52 / 84)

Replaces linear ramp (32/48/64, ratios 1.5/1.333) with φ progression:
idle 32 → hover 52 (= 32 × φ) → active 84 (= 52 × φ, ≈ φ² × idle).
Transition unchanged per Flag 4 resolution (0.12s ease at line 289).

Refs: docs/superpowers/specs/2026-04-24-golden-ratio-frontend-pass-batch-2-design.md"
```

---

## Task 3: Final QA + Draft PR

**Purpose:** Run grep-level verification across all Batch 2 changes, push the branch, open a draft PR against main.

- [ ] **Step 3.1: Grep-verify all changes landed**

```bash
echo "=== Change 2: composer tokens at target values ==="
grep -nE "composer-icon-size: *24px|send-button-size: *44px" app/static/css/styles.css
echo ""
echo "=== Change 2: no hardcoded 30px icon rules remain in composer files (excluding noise) ==="
grep -nE "^[[:space:]]+(width|height|min-width|min-height): *30px" app/static/css/styles.css app/static/css/mobile.css
echo "(expected: no hits)"
echo ""
echo "=== Change 2: padding + box-sizing added to --send-button-size consumers ==="
# Check at least one consumer has the new padding pattern
grep -nE "var\(--send-button-size\)" app/static/css/styles.css | head -5
echo ""
echo "=== Change 3: grip heights at φ progression ==="
grep -nB1 -A1 "height: *(52|84)px" app/static/css/artifact-panel.css | head -15
echo ""
echo "=== Change 3: transition unchanged at :289 ==="
sed -n '289p' app/static/css/artifact-panel.css
echo ""
echo "=== All commits on branch since main ==="
git log origin/main..HEAD --oneline
```

- [ ] **Step 3.2: Browser QA checklist (controller-driven)**

- [ ] Composer icons render visually at 24×24 on desktop and mobile
- [ ] Tap area inspection: hover/click area on send, dictation, attach (any composer icon) measures ≥44×44 in DevTools
- [ ] Mobile 375px emulation: every composer button reliably hittable with thumb-sized tap
- [ ] Keyboard focus: Tab through composer buttons; focus ring wraps the visible icon tightly (or is acceptable per Step 1.5 note)
- [ ] Dark + light theme: no color regressions around composer
- [ ] Artifact panel: hover the resize divider, grip grows to 52; click-hold-drag, grip at 84; release, returns to 32
- [ ] Transition feels smooth (not jumpy) — confirms 0.12s ease preserved

- [ ] **Step 3.3: Push branch + open draft PR**

```bash
git push -u origin feature/phi-pass-batch-2
gh pr create --draft --title "φ pass batch 2 — composer icon + artifact resize grip" --body "$(cat <<'EOF'
## Summary

Two φ proportions applied after Change 1 was retracted during state-gathering (audit cascade error — see spec addendum).

- **Composer icons:** `--composer-icon-size` 30 → 24 (15 × φ visible icon), `--send-button-size` 30 → 44 (WCAG 2.5.5 AAA / iOS HIG tap target). All consumers updated with padding: 10px + box-sizing: border-box.
- **Artifact resize grip:** linear ramp (32 / 48 / 64) → φ progression (32 / 52 / 84). Existing 0.12s ease transition preserved exactly.

## Refs

- Spec: `docs/superpowers/specs/2026-04-24-golden-ratio-frontend-pass-batch-2-design.md`
- Plan: `docs/superpowers/plans/2026-04-24-golden-ratio-frontend-pass-batch-2.md`
- Composer icon inventory: `docs/superpowers/plans/2026-04-24-composer-icon-inventory.txt`

## Change 1 retracted

Original spec Change 1 (bubble padding) was struck after discovering `.ai-message .text-message` at `message-styles.css:490` zeroes padding/radius — assistant messages have no bubble by design (Claude Desktop parity). Token change would have been a no-op. Full reasoning in spec addendum. Lesson captured in memory (`feedback_audit_trace_to_computed.md`).

## Test plan

- [ ] Composer icons visually 24×24, dark + light
- [ ] Tap areas ≥44×44 verified in DevTools mobile emulation
- [ ] Keyboard focus wraps visible icon, not full 44×44
- [ ] Artifact resize grip progression: 32 / 52 / 84 smooth
- [ ] No layout shifts at mobile 375px

## Depends on

PR #14 (Batch 1) — can land independently but rebase onto main after #14 merges for a clean history.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

---

## Self-review notes

**Spec coverage check:**
- Change 2 (composer icon) → Task 0 + Task 1
- Change 3 (resize grip) → Task 2
- Change 1 (retracted) — not planned, per spec addendum

**Placeholder scan:** Searched for "TBD", "TODO", "implement later", "fill in", "similar to", "add validation", "handle edge cases" — none present. One conditional pattern in Task 1.4 ("If genuinely ambiguous, default to container") is a deliberate planner-judgment clause, not a placeholder — backed by a specific defensible default.

**Type/naming consistency:** Token names (`--composer-icon-size`, `--send-button-size`) used consistently. The 44 / 24 / 10 padding math appears in spec, Task 1.2, and Task 1.3 unchanged.

**Risk notes:**
- Task 1.5 (focus ring scoping) is the softest point — the plan gives a preferred approach (`outline-offset: -10px` on inner SVG) and a documented fallback (leave outer ring, note in report). Either lands a working state.
- Task 1.4 (hardcoded 30px classification) requires subagent judgment on container-vs-icon per selector. Inventory surfaces the selectors; classification is defensible case-by-case but not automatic.
- Touch-target preservation is a hard gate. Any subagent that shrinks `--composer-icon-size` without adding the `44 / padding: 10px` pattern should be rejected and re-dispatched.
