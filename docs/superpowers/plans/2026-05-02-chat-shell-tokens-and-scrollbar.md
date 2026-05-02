# Chat-shell tokens and scrollbar — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Consolidate four width tokens into one (`--chat-content-width: 768px`), drop the unique 1200px outer cap, fix the 32px composer-vs-messages misalignment as a side-effect, and replace the dead 12px gradient scrollbar with a hover-only 8px scrollbar matching the Claude/ChatGPT pattern.

**Architecture:** Pure CSS refactor — no DOM, JS, Python, or template changes. Source edits land in `app/static/css/`; a single deployed-clone mirror happens at the end (atomic PR posture from the spec). Each task ends with a commit so we have a clean revert point per logical change.

**Tech Stack:** Plain CSS with CSS custom properties. Flask Jinja templates serve the styles unchanged. Two-clone deployment discipline (source repo at `/root/xelas.io/xelasphere.io-` on branch `polish/streaming-text-rendering` + deployed runtime at `/opt/xelasphere/` — both must end byte-identical).

**Reference spec:** `docs/superpowers/specs/2026-05-02-chat-shell-tokens-and-scrollbar-design.md`

---

## File structure

| File | Disposition |
|---|---|
| `app/static/css/styles.css` | TOUCHED — token additions in `:root`; 6 token deletions; ~10 consumer renames; replace lines 4761-4780 (scrollbar); delete lines 205-232 (dead scrollbar); delete the `.chat-shell__messages > *` rule (~lines 4782-4787); Romanian → English comment translations near touched sections |
| `app/static/css/message-styles.css` | TOUCHED — 4 token references (lines 265, 266, 294, 295): `--composer-max-width` → `--chat-content-width`; fallback `760px` → `768px` |
| `app/static/css/email-composer.css` | TOUCHED — 2 token references (lines 45, 46): `--content-max-width` → `--chat-content-width`. Comment near line 43 mentions tokens; update if it references obsolete names |
| `app/static/css/crypto-chart.css` | TOUCHED — 2 token references (lines 255, 256): same as email-composer |
| `app/static/css/stream-blocks.css` | TOUCHED — 1 comment (line 242 mentions `--composer-max-width`); update name reference |
| `app/static/css/artifact-panel.css` | TOUCHED — 4 fallbacks (lines 67, 75, 99, 104): `var(--artifact-panel-width, var(--panel-width-default, 480px))` → `var(--artifact-panel-width, var(--chat-right-panel-width))` |
| `app/static/css/mobile.css` | VERIFY ONLY — confirm no consumer of any deleted token; expected: none. Scrollbar block at 1635-1664 left alone |
| `app/static/css/text-selection-quote.css` | UNTOUCHED — uses `--composer-radius` which is deferred to sub-project 2 |
| `/opt/xelasphere/app/static/css/*.css` | MIRRORED — one bulk rsync at the end of the plan, byte-identical to source |

No new files. No deletions of files. No template / JS / Python changes.

---

## Test approach

This codebase has no JS/CSS test framework. Verification per task uses three mechanisms:

- **Grep assertions** — token name no longer appears (or appears N times) in expected files
- **DevTools `getBoundingClientRect()`** — open the dev app at `http://localhost:5000`, run JS in the browser console, assert computed widths
- **Manual visual smoke** — at three breakpoints (1440px sidebar-open, 1920px sidebar-collapsed, mobile ≤768px), confirm the spec's verification table outcomes

The dev server is started with `python run.py` from the project root (defaults to FLASK_ENV ≠ production → port 5000, debug auto-reload). CSS changes hot-reload on hard refresh (Ctrl+Shift+R) — no Flask restart needed.

After all source edits land and pass verification, the final task mirrors to `/opt/xelasphere/` and reloads gunicorn so production matches.

**Stage-boundary report rhythm:** after each task's commit, paste a short status (what changed, verification result, what's next). Do not wait for the user to ask.

---

## Task 1 — Add new width tokens to `:root`

Purely additive — no consumer of the new tokens yet, no visible change.

**Files:**
- Modify: `app/static/css/styles.css` (in the `:root` block, after the existing `--chat-shell-max-width` line)

- [ ] **Step 1: Locate the insertion point**

Open `app/static/css/styles.css`. Find the existing token block in `:root` (around lines 56-73). The new tokens go right after `--chat-shell-max-width: var(--chat-stage-max-width);`.

- [ ] **Step 2: Add the two new tokens**

Edit `app/static/css/styles.css`:

```
old_string:
    --chat-shell-max-width: var(--chat-stage-max-width);
    --composer-mobile-height: 190px;

new_string:
    --chat-shell-max-width: var(--chat-stage-max-width);
    /* New canonical width tokens (sub-project 1).
     * --chat-content-width replaces --content-max-width, --composer-max-width,
     * --composer-shell-max-width, --chat-shell-max-width.
     * --chat-right-panel-width is the canonical fallback for the artifact panel
     * when JS hasn't yet set the live --artifact-panel-width.
     * Old tokens stay for now; deleted in Task 6 after consumers migrate. */
    --chat-content-width: 768px;
    --chat-right-panel-width: 50vw;
    --composer-mobile-height: 190px;
```

- [ ] **Step 3: Verify no visible change in the browser**

Start the dev server if not running:

```bash
cd /root/xelas.io/xelasphere.io- && python run.py
```

Open `http://localhost:5000`, hard-refresh, confirm the chat looks identical to before. Open DevTools console and run:

```js
getComputedStyle(document.documentElement).getPropertyValue('--chat-content-width').trim()
// Expected: "768px"
getComputedStyle(document.documentElement).getPropertyValue('--chat-right-panel-width').trim()
// Expected: "50vw"
getComputedStyle(document.documentElement).getPropertyValue('--composer-shell-max-width').trim()
// Expected: "800px" (still there — not deleted yet)
```

- [ ] **Step 4: Commit**

```bash
git add app/static/css/styles.css
git commit -m "$(cat <<'EOF'
refactor(chat-shell): add --chat-content-width and --chat-right-panel-width

Additive only. New tokens declared in :root with no consumers yet.
Step 1 of the chat-shell tokens & scrollbar refactor.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2 — Migrate width consumers in `styles.css`

Switch every consumer of the four old width tokens to `--chat-content-width`. The composer pill width becomes 768 (was 800) — first visible change of the plan.

**Files:**
- Modify: `app/static/css/styles.css` (consumer rules at lines 502, 834, 886, 4750, 4784, 4792, 4832, 4841, 4907)

- [ ] **Step 1: Confirm baseline composer width**

In DevTools console at `http://localhost:5000`:

```js
document.querySelector('.input-container').getBoundingClientRect().width
// Expected: 800 (the broken state — composer 32px wider than messages)
document.querySelector('.conversation-panel').getBoundingClientRect().width
// Expected: 768
```

- [ ] **Step 2: Apply the consumer migrations in `styles.css`**

Edit each occurrence. Show all 9 changes — line numbers may have shifted by ±1 from Task 1 additions.

For each occurrence below, change `var(--composer-shell-max-width)` → `var(--chat-content-width)`:

1. `.input-container` rule — `max-width: var(--composer-shell-max-width);`
2. The line at ~834 (search for `max-width: var(--composer-shell-max-width)` in body class temporary-chat rules)
3. The line at ~886 (inside a `min(...)` expression, also temporary-chat-related)
4. `.chat-shell__header` — `max-width: var(--composer-shell-max-width);`
5. `.chat-shell__messages > *` — `max-width: var(--chat-shell-max-width);` (this becomes `--chat-content-width` for now; the whole rule is deleted in Task 5, but the rename keeps things consistent in the meantime)
6. `.conversation-panel` — `max-width: var(--composer-shell-max-width);`
7. `.chat-shell__extras > *` — `max-width: var(--composer-shell-max-width);`
8. `.formatted-response, #image-container, .message-warning` — `max-width: var(--content-max-width);` → change to `var(--chat-content-width)`
9. `.chat-shell__composer > *` — `max-width: var(--composer-shell-max-width);`

A grep verifies the file is clean of the old names afterwards:

```bash
grep -nE "var\(--(composer-shell-max-width|composer-max-width|content-max-width|chat-shell-max-width)" app/static/css/styles.css
# Expected: no output (all 9 sites migrated)
```

(The `:root` declarations of those tokens still exist — that's fine, they're deleted in Task 6.)

- [ ] **Step 3: Verify the composer pill is now 768**

Hard-refresh `http://localhost:5000`, then in DevTools console:

```js
document.querySelector('.input-container').getBoundingClientRect().width
// Expected: 768 (was 800 — kills audit finding #3)
document.querySelector('.conversation-panel').getBoundingClientRect().width
// Expected: 768 (unchanged)
```

Visually: the composer pill should now sit flush with the message column above it. No 16px overshoot on each side.

- [ ] **Step 4: Visual smoke at 1920px viewport**

Resize browser to 1920px (or use DevTools device emulation). Confirm the composer pill is still 768 (not stretched). The 1200px outer cap still applies at this stage — content is in a 1200 frame. The cap is removed in Task 5.

- [ ] **Step 5: Commit**

```bash
git add app/static/css/styles.css
git commit -m "$(cat <<'EOF'
refactor(chat-shell): migrate styles.css consumers to --chat-content-width

Replaces 9 occurrences of --composer-shell-max-width, --composer-max-width,
--content-max-width, --chat-shell-max-width with --chat-content-width.
Composer pill drops from 800 to 768, killing audit finding #3 (the 32px
composer-vs-messages misalignment). Old tokens remain in :root pending
consumer migrations in other files (Tasks 3-4) and final deletion (Task 6).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3 — Migrate consumers in `message-styles.css`, `email-composer.css`, `crypto-chart.css`, `stream-blocks.css`

Four files, each with 1-4 token references to migrate.

**Files:**
- Modify: `app/static/css/message-styles.css` (lines 265, 266, 294, 295)
- Modify: `app/static/css/email-composer.css` (lines 45, 46, possibly 43 comment)
- Modify: `app/static/css/crypto-chart.css` (lines 255, 256)
- Modify: `app/static/css/stream-blocks.css` (line 242 comment)

- [ ] **Step 1: Edit `message-styles.css` — 4 occurrences**

For each of lines 265, 266, 294, 295, change:

```
old: width: min(100%, var(--composer-max-width, 760px));
new: width: min(100%, var(--chat-content-width, 768px));
```

```
old: max-width: min(100%, var(--composer-max-width, 760px));
new: max-width: min(100%, var(--chat-content-width, 768px));
```

Both the token name AND the fallback value change (760 → 768; spec audit finding #10).

- [ ] **Step 2: Edit `email-composer.css` — 2 occurrences**

Lines 45, 46:

```
old: width: calc(var(--content-max-width, 768px) - 2 * var(--composer-side-pad, 0.95rem)) !important;
new: width: calc(var(--chat-content-width, 768px) - 2 * var(--composer-side-pad, 0.95rem)) !important;
```

```
old: max-width: calc(var(--content-max-width, 768px) - 2 * var(--composer-side-pad, 0.95rem)) !important;
new: max-width: calc(var(--chat-content-width, 768px) - 2 * var(--composer-side-pad, 0.95rem)) !important;
```

Check the comment block around line 41-51. If it mentions `--content-max-width` by name, update the comment to say `--chat-content-width`.

- [ ] **Step 3: Edit `crypto-chart.css` — 2 occurrences**

Lines 255, 256: identical pattern to email-composer.css. Check the comment around lines 250-270 for `--content-max-width` mentions; update.

- [ ] **Step 4: Edit `stream-blocks.css` — 1 comment**

Line 242 mentions `--composer-max-width` in a comment that says "defaults to 880px". The comment is wrong on two counts (token is 768 not 880, and the token name is changing). Update:

```
old: var(--composer-max-width) which defaults to 880px — without
new: var(--chat-content-width) which defaults to 768px — without
```

- [ ] **Step 5: Grep-verify all migrations**

```bash
grep -nE "var\(--(composer-max-width|content-max-width|composer-shell-max-width|chat-shell-max-width)" app/static/css/message-styles.css app/static/css/email-composer.css app/static/css/crypto-chart.css app/static/css/stream-blocks.css
# Expected: no output (all migrated; comments don't use var(...) syntax so they don't trigger)
```

Comments mentioning the old names by bare name (without `var(...)`) should also be checked:

```bash
grep -nE "(--composer-max-width|--content-max-width|--composer-shell-max-width|--chat-shell-max-width)" app/static/css/message-styles.css app/static/css/email-composer.css app/static/css/crypto-chart.css app/static/css/stream-blocks.css
# Expected: no output
```

- [ ] **Step 6: Visual verify**

Hard-refresh `http://localhost:5000`. Trigger a message that contains a code block, an email composer card (if visible), and/or a crypto chart card. Confirm widths look unchanged (these were already at 768 — token rename only).

```js
// If you can find a stream-block (code/email/crypto card), check:
document.querySelectorAll('.stream-block')[0]?.getBoundingClientRect().width
// Expected: same as before (no change to layout)
```

- [ ] **Step 7: Commit**

```bash
git add app/static/css/message-styles.css app/static/css/email-composer.css app/static/css/crypto-chart.css app/static/css/stream-blocks.css
git commit -m "$(cat <<'EOF'
refactor(chat-shell): migrate non-styles.css consumers to --chat-content-width

Updates message-styles.css (4 sites + lying 760 fallback → 768), email-
composer.css (2 sites), crypto-chart.css (2 sites), stream-blocks.css
(1 comment with stale name + wrong number). Audit finding #10 (lying
fallbacks) cleared.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4 — Migrate `artifact-panel.css` fallbacks to `--chat-right-panel-width`

Wires the new `--chat-right-panel-width` token as the canonical fallback so it's not dead-on-arrival. Replaces 4 `var(--artifact-panel-width, var(--panel-width-default, 480px))` chains.

**Files:**
- Modify: `app/static/css/artifact-panel.css` (lines 67, 75, 99, 104)

- [ ] **Step 1: Apply the 4 substitutions**

For each of lines 67, 75, 99, 104:

```
old: margin-right: var(--artifact-panel-width, var(--panel-width-default, 480px));
new: margin-right: var(--artifact-panel-width, var(--chat-right-panel-width));
```

(The exact property is `margin-right` on each of those lines per the spec's File table.)

- [ ] **Step 2: Grep-verify**

```bash
grep -nE "panel-width-default|var\(--artifact-panel-width.*480" app/static/css/artifact-panel.css
# Expected: no output
grep -nE "var\(--artifact-panel-width, var\(--chat-right-panel-width\)" app/static/css/artifact-panel.css
# Expected: 4 lines (67, 75, 99, 104)
```

- [ ] **Step 3: Verify behavior unchanged**

In DevTools console at `http://localhost:5000`, with the artifact panel CLOSED:

```js
getComputedStyle(document.body).marginRight
// Expected: "0px" (panel not open, JS hasn't set --artifact-panel-width)
```

Open an artifact (any code/canvas response) so the panel opens. Then:

```js
getComputedStyle(document.body).marginRight
// Expected: "Xpx" where X equals 50% of viewport width (50vw evaluated to px)
```

Behavior should be identical to before — the fallback value `50vw` = the new token's value, which the artifact-panel JS sets dynamically via `--artifact-panel-width` anyway. The old `480px` literal would have only kicked in if JS failed to initialize the live value, which is rare.

- [ ] **Step 4: Commit**

```bash
git add app/static/css/artifact-panel.css
git commit -m "$(cat <<'EOF'
refactor(artifact-panel): wire --chat-right-panel-width as canonical fallback

Replaces four var(--artifact-panel-width, var(--panel-width-default, 480px))
chains with var(--artifact-panel-width, var(--chat-right-panel-width)).
Removes the magic 480px literal; consolidates to one token. Live behavior
unchanged because JS sets --artifact-panel-width on panel open.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5 — Delete the redundant `.chat-shell__messages > *` rule and remove the 1200 outer cap

Drops the unique-to-xelasphere outer stage cap. On 1920px viewports, content centers at 768 in the full viewport — exactly like Claude.ai.

**Files:**
- Modify: `app/static/css/styles.css` (delete the rule at ~lines 4782-4787)

- [ ] **Step 1: Locate the rule**

In `app/static/css/styles.css`, search for the rule:

```css
.chat-shell__messages > * {
    width: 100%;
    max-width: var(--chat-content-width);  /* was --chat-shell-max-width before Task 2 step 2 #5 */
    margin-left: auto;
    margin-right: auto;
}
```

(After Task 2's rename, the rule references `--chat-content-width`. If you see `--chat-shell-max-width` here you missed item 5 in Task 2.)

- [ ] **Step 2: Delete the entire rule block**

Edit `app/static/css/styles.css`:

```
old_string:
.chat-shell__messages > * {
    width: 100%;
    max-width: var(--chat-content-width);
    margin-left: auto;
    margin-right: auto;
}

/* Inner wrapper - CENTRARE + MAX-WIDTH aici */
.conversation-panel {

new_string:
/* Inner wrapper — centering + max-width here. The .conversation-panel
 * is now the only width-cap inside the scroll region; the previous
 * .chat-shell__messages > * rule (with the 1200 outer cap) was redundant. */
.conversation-panel {
```

(The Romanian comment is also translated to English while we're in this region — Task 9 sweeps the rest.)

- [ ] **Step 3: Verify on a wide viewport**

Hard-refresh `http://localhost:5000` and resize browser to 1920px (or DevTools device emulation at 1920×1080). With sidebar collapsed, the chat content should now center at 768px in the *full* viewport — no 1200px frame.

```js
// At 1920px viewport, sidebar collapsed:
document.querySelector('.conversation-panel').getBoundingClientRect()
// Expected: width 768, centered (left ≈ 576, right ≈ 1344)
```

At 1440px or smaller viewports, behavior is unchanged.

- [ ] **Step 4: Commit**

```bash
git add app/static/css/styles.css
git commit -m "$(cat <<'EOF'
refactor(chat-shell): drop the unique 1200px outer stage cap

Deletes .chat-shell__messages > * which was the sole consumer of
--chat-shell-max-width (1200). The .conversation-panel inside already
caps at 768, so the rule was redundant. Matches Claude/ChatGPT
behavior on wide viewports — content centers at 768 in the full
viewport, no 1200 frame. Translates one Romanian comment to English
while in the region.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6 — Delete six old width tokens from `:root`

Removes the no-longer-referenced declarations. The grep step is the safety net: if any consumer was missed in Tasks 2-4, the deletion would leave a `var(--missing)` reference and visual breakage.

**Files:**
- Modify: `app/static/css/styles.css` (lines 67-73 in the `:root` block)

- [ ] **Step 1: Pre-deletion grep — confirm no consumers remain**

```bash
grep -rnE "var\(--(content-max-width|composer-max-width|composer-shell-max-width|chat-shell-max-width|chat-stage-max-width|panel-width-default)" app/static/css/ app/templates/ app/static/js/
# Expected: no output
```

If any line appears, STOP. Re-do the migration step that missed it before continuing.

- [ ] **Step 2: Delete the six token declarations**

Edit `app/static/css/styles.css` `:root` block:

```
old_string:
    --content-max-width: 768px;
    --chat-stage-max-width: 1200px;
    --composer-max-width: var(--content-max-width);
    --composer-shell-max-width: 800px;
    --composer-gutter: var(--space-5);
    --composer-reserved-height: 150px;
    --chat-shell-max-width: var(--chat-stage-max-width);

new_string:
    --composer-gutter: var(--space-5);
    --composer-reserved-height: 150px;
```

Then delete `--panel-width-default: 480px` from `app/static/css/design-tokens.css`:

```
old_string:
  --panel-width-default: 480px;
  --panel-width-min: 320px;
  --panel-width-max: 720px;
  --panel-border-width: 1px;

new_string:
  --panel-width-min: 320px;
  --panel-width-max: 720px;
  --panel-border-width: 1px;
```

(Min/max/border-width stay — they're consumed by the panel's own min/max-width rules, separate concern.)

- [ ] **Step 3: Verify the comment block above the deletions still makes sense**

The `:root` block had a comment header "Layout Helpers" that grouped these. Read the surrounding text and adjust if a comment now refers to a deleted token.

- [ ] **Step 4: Visual smoke at three breakpoints**

Hard-refresh and verify:

| Viewport | Sidebar | Panel | Expected |
|---|---|---|---|
| 1440 | open | closed | composer = messages = 768 |
| 1920 | collapsed | closed | content centered 768, full viewport (no 1200 frame) |
| 1440 | open | open | composer ≤ messages in their grid half, both scale together |

```js
// At 1440px sidebar open, no panel:
document.querySelector('.input-container').getBoundingClientRect().width
// Expected: 768
```

- [ ] **Step 5: Commit**

```bash
git add app/static/css/styles.css app/static/css/design-tokens.css
git commit -m "$(cat <<'EOF'
refactor(chat-shell): delete six obsolete width tokens

Removes --content-max-width, --composer-max-width, --composer-shell-max-width,
--chat-shell-max-width, --chat-stage-max-width from styles.css :root.
Removes --panel-width-default from design-tokens.css :root. All consumers
migrated in tasks 2-4. Pre-deletion grep confirmed zero references remain.
Audit finding #6 (4 width tokens for one concept) cleared.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 7 — Replace the scrollbar block with hover-only

Replaces lines 4761-4780 in `styles.css` with the canonical hover-only scrollbar (Option B from brainstorm).

**Files:**
- Modify: `app/static/css/styles.css` (lines ~4761-4780, after Task 5's deletion shifts line numbers)

- [ ] **Step 1: Confirm current behavior — scrollbar hidden**

Hard-refresh `http://localhost:5000`. Generate enough chat content that the messages scroll. Hover over the messages area — scrollbar should NOT appear (currently hidden by lines 4761-4780).

- [ ] **Step 2: Replace the block**

Edit `app/static/css/styles.css`. Find the existing block:

```
old_string:
/* #chat-scroll-region - SINGURUL container care scrollează */
.chat-shell__messages,
#chat-scroll-region {
    flex: 1 1 auto;
    width: 100%;
    display: flex;
    flex-direction: column;
    min-height: 0;
    overflow-y: auto; /* SCROLL AICI */
    overflow-x: hidden;
    overflow-anchor: none; /* Prevent browser scroll anchoring - we manage scroll manually */
    /* Hide scrollbar but keep functionality */
    scrollbar-width: none; /* Firefox */
    -ms-overflow-style: none; /* IE/Edge */
}

/* Hide scrollbar for WebKit browsers (Chrome, Safari, Edge) */
.chat-shell__messages::-webkit-scrollbar,
#chat-scroll-region::-webkit-scrollbar {
    display: none;
}

new_string:
/* #chat-scroll-region — the ONLY container that scrolls.
 * Hover-only scrollbar (matches Claude/ChatGPT pattern).
 * Mobile keeps scrollbar fully hidden via mobile.css:1635-1664. */
.chat-shell__messages,
#chat-scroll-region {
    flex: 1 1 auto;
    width: 100%;
    display: flex;
    flex-direction: column;
    min-height: 0;
    overflow-y: auto;
    overflow-x: hidden;
    overflow-anchor: none; /* We manage scroll manually */
    /* Hover-only scrollbar (Firefox) */
    scrollbar-width: thin;
    scrollbar-color: transparent transparent;
    transition: scrollbar-color 200ms ease;
}

.chat-shell__messages:hover,
#chat-scroll-region:hover {
    scrollbar-color: rgba(255, 255, 255, 0.18) transparent;
}

/* WebKit scrollbar — track always present at 8px so layout doesn't jump on hover */
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

/* Light theme: deeper thumb on cream canvas */
[data-theme="light"] .chat-shell__messages:hover,
[data-theme="light"] #chat-scroll-region:hover {
    scrollbar-color: rgba(40, 30, 15, 0.20) transparent;
}
[data-theme="light"] .chat-shell__messages:hover::-webkit-scrollbar-thumb,
[data-theme="light"] #chat-scroll-region:hover::-webkit-scrollbar-thumb {
    background: rgba(40, 30, 15, 0.20);
}
```

- [ ] **Step 3: Visual smoke — desktop hover**

Hard-refresh, hover over messages area. The scrollbar should fade in over 200ms. Move mouse out — fades out. Hover thumb directly — darkens further. Test in both dark and light theme (toggle via your theme switcher).

- [ ] **Step 4: Mobile-emulation smoke**

In DevTools, switch to a mobile viewport (≤768px). The scrollbar should NOT appear regardless of "hover" — mobile.css:1635-1664 still hides it. Verify scroll still works (drag to scroll).

- [ ] **Step 5: Commit**

```bash
git add app/static/css/styles.css
git commit -m "$(cat <<'EOF'
feat(chat-shell): hover-only scrollbar matching Claude/ChatGPT pattern

Replaces the always-hidden scrollbar block (styles.css:4761-4780) with a
thin 8px scrollbar that fades in on container hover, darkens on thumb
hover. Light-theme variant uses warm-dark tone instead of white.
Mobile keeps the existing fully-hidden behavior (mobile.css:1635-1664).
Translates two Romanian comments to English in the region.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 8 — Delete the dead 12px gradient scrollbar block

Removes ~25 lines of dead CSS that have been overridden since the day they shipped (audit findings #1, #2).

**Files:**
- Modify: `app/static/css/styles.css` (lines ~205-232, after Task 7's edits shift positions)

- [ ] **Step 1: Locate the dead block**

In `app/static/css/styles.css`, find the block (its position is between `body { overflow: hidden }` and `.offline-status { ... }`):

```css
body,
.chat-shell__messages {
    scrollbar-width: thin;
    scrollbar-color: var(--border-primary, rgba(255, 255, 255, 0.35)) transparent;
}

body::-webkit-scrollbar,
.chat-shell__messages::-webkit-scrollbar {
    width: 12px;
}

body::-webkit-scrollbar-track,
.chat-shell__messages::-webkit-scrollbar-track {
    background: transparent;
    border-radius: 999px;
}

body::-webkit-scrollbar-thumb,
.chat-shell__messages::-webkit-scrollbar-thumb {
    border-radius: 999px;
    background: linear-gradient(180deg, rgba(255, 255, 255, 0.4), rgba(255, 255, 255, 0.15));
    border: 2px solid rgba(0, 0, 0, 0);
    box-shadow: inset 0 0 0 1px rgba(255, 255, 255, 0.15);
}

body::-webkit-scrollbar-thumb:hover,
.chat-shell__messages::-webkit-scrollbar-thumb:hover {
    background: linear-gradient(180deg, rgba(255, 255, 255, 0.6), rgba(255, 255, 255, 0.25));
}
```

- [ ] **Step 2: Delete the entire block**

Edit `app/static/css/styles.css`. Replace the whole block (5 rule sets) with empty space:

```
old_string:
body,
.chat-shell__messages {
    scrollbar-width: thin;
    scrollbar-color: var(--border-primary, rgba(255, 255, 255, 0.35)) transparent;
}

body::-webkit-scrollbar,
.chat-shell__messages::-webkit-scrollbar {
    width: 12px;
}

body::-webkit-scrollbar-track,
.chat-shell__messages::-webkit-scrollbar-track {
    background: transparent;
    border-radius: 999px;
}

body::-webkit-scrollbar-thumb,
.chat-shell__messages::-webkit-scrollbar-thumb {
    border-radius: 999px;
    background: linear-gradient(180deg, rgba(255, 255, 255, 0.4), rgba(255, 255, 255, 0.15));
    border: 2px solid rgba(0, 0, 0, 0);
    box-shadow: inset 0 0 0 1px rgba(255, 255, 255, 0.15);
}

body::-webkit-scrollbar-thumb:hover,
.chat-shell__messages::-webkit-scrollbar-thumb:hover {
    background: linear-gradient(180deg, rgba(255, 255, 255, 0.6), rgba(255, 255, 255, 0.25));
}

new_string:
```

(Replace with empty string — the block is gone.)

- [ ] **Step 3: Verify hover scrollbar still works**

Hard-refresh. The hover-only scrollbar from Task 7 should still behave identically (the deleted block was dead code; deleting it changes nothing visually).

- [ ] **Step 4: Grep for any remaining references**

```bash
grep -nE "body::-webkit-scrollbar" app/static/css/styles.css
# Expected: no output
```

- [ ] **Step 5: Commit**

```bash
git add app/static/css/styles.css
git commit -m "$(cat <<'EOF'
refactor(chat-shell): delete dead 12px gradient scrollbar block

Removes lines 205-232 from styles.css. The block declared visible
scrollbar styles for body and .chat-shell__messages, but body has
overflow:hidden (never paints) and the messages selector is overridden
by the later block at lines 4761+. Audit findings #1 and #2 cleared.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 9 — Translate Romanian comments to English

Small clarity-of-codebase win. Limited to the touched regions of `styles.css`; do not sweep the entire file.

**Files:**
- Modify: `app/static/css/styles.css` (Romanian comments in or near touched sections)

- [ ] **Step 1: Apply the translations**

For each Romanian comment, edit in place (line numbers approximate; search by Romanian text):

```
old: /* DOAR #chat-scroll-region scrollează */
new: /* Only #chat-scroll-region scrolls */
```

```
old: /* FĂRĂ max-width aici - lasă scroll container să fie full-width */
new: /* No max-width here — scroll container stays full-width */
```

```
old: /* Centrat ca și .conversation-panel */
new: /* Centered like .conversation-panel */
```

```
old: /* Spațiu pentru composer */
new: /* Space for the composer */
```

```
old: /* Gap mutat pe .conversation-panel */
new: /* Gap moved to .conversation-panel */
```

```
old: /* Full width pentru copii */
new: /* Full width for children */
```

(`/* SINGURUL container care scrollează */` and `/* SCROLL AICI */` were already handled in Task 7. `/* Inner wrapper - CENTRARE + MAX-WIDTH aici */` was handled in Task 5.)

- [ ] **Step 2: Verify no Romanian-character clusters remain**

```bash
grep -nE "(SINGURUL container|FĂRĂ max-width aici|Centrat ca și|Spațiu pentru composer|Gap mutat pe|Full width pentru copii|SCROLL AICI|CENTRARE \+ MAX-WIDTH aici|DOAR #chat-scroll-region)" app/static/css/styles.css
# Expected: no output
```

- [ ] **Step 3: Commit**

```bash
git add app/static/css/styles.css
git commit -m "$(cat <<'EOF'
docs(chat-shell): translate Romanian CSS comments to English

Touched regions only — does not sweep the whole file.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 10 — Final verification + mirror to deployed clone

End-to-end visual sweep at three breakpoints, plus the spec's verification table. Then a single rsync to `/opt/xelasphere/` and a gunicorn reload so production matches.

**Files:**
- No source edits — verification only.
- Mirror: `/opt/xelasphere/app/static/css/*.css` byte-identical to source.

- [ ] **Step 1: Spec verification table — desktop**

| Scenario | Expected | DevTools assertion |
|---|---|---|
| 1440px, sidebar open, no panel | composer pill = messages = 768 | `document.querySelector('.input-container').getBoundingClientRect().width` → 768 |
| 1920px, sidebar collapsed, no panel | content centered 768 in full viewport, no 1200 frame | `document.querySelector('.conversation-panel').getBoundingClientRect()` → width 768, ~equal left and right offsets |
| 1440px, sidebar open, artifact panel open | composer ≤ messages in grid half | composer width visibly tracks messages width when resizing the panel |
| Hover over messages | 8px scrollbar fades in over 200ms | visual + thumb opacity 0.18 |
| Mouse leaves messages | scrollbar fades out | visual |
| Hover thumb | thumb darkens to 0.32 | visual |
| Light theme + hover | thumb is warm-dark `rgba(40,30,15,0.20)` not white | visual |

- [ ] **Step 2: Spec verification — mobile**

In DevTools device emulation, switch to a phone viewport (e.g., iPhone 14 — 390×844). Confirm:

- No scrollbar visible regardless of hover
- Scroll still works via touch drag
- Composer pill at full mobile width (mobile.css overrides apply)

- [ ] **Step 3: Final cross-cut grep**

```bash
# All 6 deleted tokens should be gone from references and declarations:
grep -rnE "(--content-max-width|--composer-max-width|--composer-shell-max-width|--chat-shell-max-width|--chat-stage-max-width|--panel-width-default)" app/static/css/ app/templates/ app/static/js/
# Expected: no output

# New tokens should be declared exactly once:
grep -rnE "^\s*--chat-content-width:" app/static/css/
# Expected: 1 line (in styles.css :root)
grep -rnE "^\s*--chat-right-panel-width:" app/static/css/
# Expected: 1 line (in styles.css :root)

# New tokens should have multiple consumers:
grep -rnE "var\(--chat-content-width" app/static/css/ | wc -l
# Expected: ≥ 13 (9 in styles.css + 4 in message-styles.css + 2 in email-composer + 2 in crypto-chart, minus the deleted .chat-shell__messages > * = 17, but precise count varies)
```

- [ ] **Step 4: Mirror source to deployed clone**

```bash
sudo rsync -av --delete \
  /root/xelas.io/xelasphere.io-/app/static/css/ \
  /opt/xelasphere/app/static/css/
```

Verify byte-identical:

```bash
diff -rq /root/xelas.io/xelasphere.io-/app/static/css/ /opt/xelasphere/app/static/css/
# Expected: no output
```

- [ ] **Step 5: Reload gunicorn**

```bash
sudo systemctl reload xelasphere
sudo systemctl status xelasphere --no-pager | head -10
```

Confirm `Active: active (running)` and no recent error lines.

- [ ] **Step 6: Production smoke**

Open `https://xelasphere.com` (production URL) in a fresh browser tab. Hard-refresh. Run the same DevTools assertions as Step 1. The CSS should be byte-identical to dev.

- [ ] **Step 7: Final commit (if any verification cleanup landed)**

If Steps 1-6 surfaced any issue requiring a code change, fix it as its own commit. Otherwise no commit at this step. The plan lands as 9 commits (one per task) — no extra "verification" commit.

```bash
git log --oneline -10
# Expected: 9 commits from this plan, in order
```

---

## Out-of-scope reminder (firewall)

If during implementation you spot any of the following, **file as a follow-up — do not fix inline**:

- Composer pill radius 26 vs 24 mismatch (audit #4) → sub-project 2
- User message bubble width hardcoded 520px (audit #5) → sub-project 4
- `--right-sidebar-width: 320px` legacy logic (audit #7) → sub-project 5
- Artifact panel position-fixed-vs-grid double-positioning (audit #8) → sub-project 5
- `--composer-padding-x` / `-y` dead tokens (audit #9) → sub-project 2
- `.input-row::after` U-mask `-50px` overshoot (audit #13) → sub-project 2
- Z-index sprawl (audit #11) → sub-project 6
- `pointer-events: none` footgun (audit #12) → not addressed in this rewrite

The audit findings list is captured in the spec's *Problem* and *Out of scope* sections.
