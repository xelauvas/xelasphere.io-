# Spec: Golden Ratio Frontend Pass — Batch 2

**Status:** Ready for implementation after Batch 1 has shipped and been visually verified
**Scope:** Chat bubble geometry + composer icon sizing + artifact panel resize grip
**Estimated effort:** 45 min implementation + visual/accessibility QA
**Date:** 2026-04-24
**Author:** Xela Uvas

---

## Context

Follows Batch 1 (foundation tokens, dashboard/artifact/upgrade/modal φ). This batch addresses **chat-surface internals** — bubble padding, composer icon scale, artifact resize grip. Each change is felt directly on every interaction, unlike Batch 1 which was mostly structural.

Two items from the original audit (L3/L4 content column recomposition, M1 chat-specific line-height) are **deferred to a Batch 3 decision** — see "Decisions required" section below. They depend on subjective reading preference and should be decided after living with Batches 1 + 2 for ~a week.

φ = 1.618 · 1/φ = 0.618 · √φ = 1.272

---

## Changes

### 1. Assistant bubble padding → φ (with radius companion)

**Files:** chat message CSS

**Current:**
```css
--message-padding-y: 1.25rem;  /* 20px */
--message-padding-x: 1.5rem;   /* 24px */
--message-radius:    1.2rem;   /* 19.2px */
```
Ratio px : py = 24 : 20 = 1.2 (flat)

**New:**
```css
--message-padding-y: 1.25rem;    /* 20px unchanged */
--message-padding-x: 2rem;       /* 32px — φ of py */
--message-radius:    1.2375rem;  /* 19.8px — preserves radius:padding-x proportion */
```
Ratio px : py = 32 : 20 = 1.6 (≈ φ)

**Rationale:** 8px more horizontal padding per side is genuinely felt. Content reads "landscape" — more breathing room around the text column. Radius nudge preserves the corner-to-content proportion so bubbles don't start looking boxy.

**Scope notes:**
- **Assistant bubble only.** User bubble already near-φ (17.6 : 11.2 = 1.571) — leave alone.
- If user and assistant bubbles currently share a single `--message-padding-x` token, split into `--assistant-padding-x` and `--user-padding-x` and migrate consumers. User bubble keeps its current 17.6px.

**Acceptance:**
- [ ] Assistant bubbles: 32px left/right padding, 20px top/bottom
- [ ] Corner radius ~19.8px; visually proportional to new padding (no "boxier" appearance)
- [ ] User bubble padding unchanged (11.2 × 17.6)
- [ ] Max-width behavior unchanged
- [ ] No overflow on narrow viewports (assistant bubbles still wrap at 92% / 72ch)

---

### 2. Composer icon 30 → 24 **with touch-target preservation**

**Files:** composer CSS (send button, attachment icon, any composer chrome icons)

**Current:**
- Icon visual size: 30×30
- Button container size: assumed ~30×30 (verify in implementation)
- Ratio to composer font (15px): 2.0 — icon dominates text

**New:**
- Icon visual size: **24×24** (= 15 × φ, rounded)
- Button container: **≥44×44** via padding (iOS HIG / WCAG 2.5.5 requirement)

**Required CSS pattern:**
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

**Rationale:** 30:15 ratio makes icons compete with text. 24:15 (φ) makes icons feel supportive, not shouty.

**⚠️ Critical accessibility gate:** The visible icon shrinks; the tappable area **must not**. Both iOS HIG and WCAG AAA (2.5.5) require ≥44×44px tap targets. If an implementer shrinks the icon without adding hit-area padding, it's a regression regardless of how the visual looks.

**Acceptance:**
- [ ] All composer icons (send, attach, any others) visually 24×24
- [ ] Each icon button has a ≥44×44 tappable area (verify via dev tools inspection, not eyeball)
- [ ] Mobile 375px: send button reliably hittable with thumb
- [ ] Desktop hover states don't feel oversized or floaty (the 44×44 container shouldn't light up a huge area on hover — if needed, scope hover highlight to the inner 24×24)
- [ ] Focus ring (keyboard nav) wraps the visible icon tightly, not the full 44×44 padding

---

### 3. Artifact panel resize grip → φ progression

**Files:** artifact panel CSS (resize handle styles)

**Current:**
```
idle:   3 × 32 px
hover:  3 × 48 px    (× 1.5 from idle)
active: 3 × 64 px    (× 1.333 from hover)
```
Linear ramp, inconsistent step sizes.

**New:**
```
idle:   3 × 32 px    (unchanged)
hover:  3 × 52 px    (× φ from idle)
active: 3 × 84 px    (× φ from hover, ≈ φ² from idle)
```
Consistent φ progression.

**Rationale:** Active state feels more "live" during drag — grip grows proportionally at each interaction step rather than in arbitrary increments. Subtle but felt mid-drag.

**Acceptance:**
- [ ] Grip heights: 32 idle, 52 hover, 84 active
- [ ] Transitions use existing artifact panel transition timing (don't introduce a new duration token)
- [ ] Hover state clearly distinct from idle
- [ ] Active state clearly distinct from hover
- [ ] Grip stays vertically centered on the split at all sizes

---

## Decisions required before a Batch 3

These are deliberately **not** in this spec. Live with Batches 1 + 2 for ~a week, then pick based on actual feel — not audit math.

### Decision A — Content column philosophy

Two mutually exclusive options, both φ-compliant:

**Option A1 — Narrower, more whitespace:**
- Bubble max-width: 520 → 475
- Composer max-width: 768 → 475 (matches bubble)
- Content column stays at 768

**Option A2 — Wider, denser:**
- Bubble max-width stays at 520
- Content column: 768 → 841 (= 520 × φ)
- Composer inherits content width

Pick **A1** if the composer feels like a full-width slab disconnected from the conversation.
Pick **A2** if the text column feels tight and you want more reading surface.

### Decision B — Chat-specific line-height

`--chat-line-height` currently overrides body default at **1.7**. Batch 1 sets body default to 1.618.

- Keep **1.7** for chat (deliberate: more breathing room for long assistant replies)
- Override to **1.618** for consistency with body rhythm

Test for a week. Cramped on long replies → keep 1.7. Loose / sprawling → move to 1.618.

---

## Out of scope (still deferred from original audit)

Explicit exclusions, same reasoning as Batch 1:

- **Spacing scale Fibonacci refactor (F4).** Audit itself admits it "warrants visual QA at every breakpoint." Cost >> benefit.
- **Animation duration retuning (F3).** Sub-perceptual deltas (150→162ms, etc.).
- **Sub-3% width nudges** (L2 chat stage 1200→1242, L5 sidebar 320→331, A1 close button 28→27, C1 composer sub-pixel fix, M3 user bubble 11.2×17.6→11×18). Mathematically cleaner, perceptually invisible.
- **Display type scale → √φ (F1, F2).** Still needs a visual mock for the 36→41px auth hero jump.
- **Tab-row / dropdown-width √φ ladders (A2, A4).** Chrome rhythm micro-adjustments — defer indefinitely unless you actually notice them.

---

## Testing checklist

- [ ] Assistant bubbles: 32px horizontal padding, correct at 1440 / 1024 / 768 / 375
- [ ] User bubble unchanged (regression check)
- [ ] Composer icons visually 24×24
- [ ] Composer icon tap area ≥44×44 verified in dev tools (mobile device emulation)
- [ ] Mobile 375px: every composer button hittable
- [ ] Keyboard focus rings wrap icons tightly
- [ ] Artifact grip: 32 → 52 → 84 on idle / hover / active, smooth transitions
- [ ] Dark + light theme parity on all changes
- [ ] No layout shifts

---

## Files touched (estimated)

1. Chat message CSS — assistant bubble padding + radius
2. `design-tokens.css` — possibly split `--message-padding-x` into assistant/user tokens
3. Composer CSS — icon sizing + hit-area padding pattern
4. Artifact panel CSS — resize grip sizes

~3–4 files. No JS behavior changes, no new duration tokens, no schema changes.

---

## Handoff notes

- **Run after Batch 1 has landed and been visually verified.** Don't stack on an unmerged Batch 1 — the changes don't conflict, but reviewing two stacked batches at once is worse than reviewing two clean ones sequentially.
- **Touch-target pairing in Change 2 is non-negotiable.** If the implementer shrinks the icon without adding hit-area padding, reject and revise. This is a hard accessibility requirement, not a nice-to-have.
- If implementation reveals user-bubble geometry actually needs adjustment too (unexpected), **stop and revise this spec** — don't improvise extra changes. The Batch 1 postmortem on deferred items explicitly included "don't add while-we're-here changes."
- Same PR discipline as Batch 1: one focused commit, QA at 4 breakpoints + both themes, then ship.

---

## Ambiguity flags surfaced during review (for the plan to resolve)

1. **Change 1 rationale vs math.** The rationale says the new radius (19.8px) "preserves radius:padding-x proportion." Math check: current ratio is `19.2/24 = 0.800`; new ratio is `19.8/32 = 0.619`. These are not equal — the proportion is NOT preserved. However, `32 / φ = 19.78`, so the new radius does introduce a clean `radius = padding-x / φ` relationship (a NEW φ proportion, not a preserved old one). Likely the author's intent; the rationale text should be rewritten to say "introduces radius:padding-x = 1/φ proportion." Flagged for author review — do not silently rewrite during planning.

2. **Change 1 user/assistant token split.** The spec says "if user and assistant bubbles currently share a single `--message-padding-x` token, split." Current state per the Batch 1 audit: user and assistant bubbles use *different* CSS rules (`.user-message .message-content` with `0.7rem 1.1rem`, `.ai-message .message-content` with the `--message-padding-y/x` tokens). The token only feeds the assistant selector. Planning step should verify this and confirm: no split needed — change the assistant token values directly; user rule remains untouched. If the audit read the wrong rules, add the split step.

3. **Change 2 composer icon inventory.** The spec lists "send button, attachment icon, any composer chrome icons." The plan needs an inventory step (similar to Batch 1 Task 0) to find every composer icon button that currently uses 30px sizing: check `styles.css` (composer section), `dictationModule.js` / related CSS if any, and any per-feature button files. Migration is per-button; missing one ships with inconsistent icon sizing.

4. **Change 3 existing transition timing.** Acceptance says "transitions use existing artifact panel transition timing (don't introduce a new duration token)." The current rule at `artifact-panel.css:289` is `transition: background 0.12s ease, height 0.12s ease, opacity 0.12s ease`. The plan step should confirm this 120ms value is reused and not silently bumped.
