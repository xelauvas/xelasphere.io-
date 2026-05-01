# Style replay invariant — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Append a runtime invariant assertion to the existing data-style sweep in `renderActivePage`, ship it to both clones, restart the service, manually verify the assertion fires when the invariant is violated, and commit on `fix/stream-first-line-pop`.

**Architecture:** Single block of ~12 lines appended inside the existing sweep `try` in `startNewChatModule.js`. After the sweep finishes setting `data-style` on every AI bubble that has a persisted `style`, walk the DOM one more time and `console.error` any bubble whose `data-style` doesn't match the persisted value. Bump the script-tag cache-bust query so browsers fetch the new file.

**Tech Stack:** Vanilla JS (no test framework available in this repo). Flask template (`index.html`) for cache-bust string. Python services unchanged. `systemctl` to reload gunicorn. Two-clone discipline: source repo at `/root/xelas.io/xelasphere.io-` (branch `fix/stream-first-line-pop`) + deployed runtime at `/opt/xelasphere/` — both must end byte-identical.

**Reference spec:** `docs/superpowers/specs/2026-05-01-style-replay-invariant-design.md`

---

## File structure

| File | What changes |
|---|---|
| `app/static/js/startNewChatModule.js` | Append ~12 lines inside the existing `try` block in `renderActivePage` (after the sweep, before the `} catch`). |
| `app/templates/index.html` | Bump `<script src="...startNewChatModule.js?v=19">` → `?v=20` so browsers refetch. |
| `/opt/xelasphere/app/static/js/startNewChatModule.js` | Mirror of source, byte-identical. |
| `/opt/xelasphere/app/templates/index.html` | Mirror of source, byte-identical. |

No new files. No deletions. No DB / schema / API changes.

---

## Test approach (no JS framework)

This codebase has no JS unit-test framework and the spec explicitly rejects adding one. The "test" for this work is twofold:

1. **The assertion itself in production code** — it runs every time `renderActivePage` fires, in every real session. Console.error in a real browser is the regression detector.
2. **One manual fault-injection** post-deploy — corrupt a `data-style` attribute via DevTools, trigger a re-render via session-switch in the sidebar, observe the `[style-replay] invariant violated` error. This proves the assertion actually catches violations.

Each task below uses verify-by-grep / verify-by-syntax-check / verify-by-restart-then-curl in place of unit tests.

---

## Task 1: Append the invariant assertion to the sweep

**Files:**
- Modify: `app/static/js/startNewChatModule.js` — inside `renderActivePage`, after the existing sweep block, before the `} catch (_e)` that closes it.

The existing code in the file (around line 3279-3296) currently looks like:

```js
        try {
            const styleByRid = new Map();
            messages.forEach(m => {
                if (m && m.response_id && m.style) styleByRid.set(m.response_id, m.style);
            });
            if (styleByRid.size && this.elements.chatDisplay) {
                this.elements.chatDisplay.querySelectorAll('.ai-message').forEach(msgEl => {
                    const id = msgEl.id || msgEl.dataset.messageId || '';
                    const rid = id.replace(/^ai-/, '');
                    const style = styleByRid.get(rid);
                    if (!style) return;
                    const tmc = msgEl.querySelector('.text-message-container');
                    const inner = tmc && tmc.querySelector('.text-message');
                    if (tmc && !tmc.hasAttribute('data-style')) tmc.setAttribute('data-style', style);
                    if (inner && !inner.hasAttribute('data-style')) inner.setAttribute('data-style', style);
                });
            }
        } catch (_e) { /* sweep is best-effort */ }
```

- [ ] **Step 1: Apply the Edit**

Use the Edit tool on `/root/xelas.io/xelasphere.io-/app/static/js/startNewChatModule.js`. The `old_string` is the closing brace+catch line of the sweep:

```
            }
        } catch (_e) { /* sweep is best-effort */ }
```

The `new_string` is the same closing block PLUS the assertion inserted before the catch:

```
            }

            // Invariant: every AI bubble whose persisted message has a `style`
            // must carry that `data-style` after the sweep runs. If any
            // bubble is missing it, a future change has broken the sweep —
            // fail loudly in console so it's caught in real sessions.
            const violations = [];
            if (this.elements.chatDisplay) {
                this.elements.chatDisplay.querySelectorAll('.ai-message').forEach(msgEl => {
                    const id = msgEl.id || msgEl.dataset.messageId || '';
                    const rid = id.replace(/^ai-/, '');
                    const expected = styleByRid.get(rid);
                    if (!expected) return;
                    const actual = msgEl.querySelector('.text-message-container')?.getAttribute('data-style');
                    if (actual !== expected) {
                        violations.push({ rid, expected, actual: actual || '(missing)' });
                    }
                });
            }
            if (violations.length) {
                console.error('[style-replay] invariant violated for', violations.length, 'bubble(s):', violations);
            }
        } catch (_e) { /* sweep is best-effort */ }
```

- [ ] **Step 2: JS syntax-check the source file**

Run: `node --check /root/xelas.io/xelasphere.io-/app/static/js/startNewChatModule.js`
Expected: command exits 0 with no output (silent success).

- [ ] **Step 3: Confirm the assertion lines actually landed in the file**

Run: `grep -n "style-replay" /root/xelas.io/xelasphere.io-/app/static/js/startNewChatModule.js`
Expected: one hit, the `console.error('[style-replay] invariant violated for', ...)` line.

Run: `grep -n "violations.push" /root/xelas.io/xelasphere.io-/app/static/js/startNewChatModule.js`
Expected: one hit, the `violations.push({ rid, expected, ...` line.

If either grep returns zero hits → the Edit didn't land or matched the wrong region; re-read the file around line 3296 and re-apply.

---

## Task 2: Bump the cache-bust query string in index.html

**Files:**
- Modify: `app/templates/index.html` — line 1087, the `<script>` tag for `startNewChatModule.js`.

- [ ] **Step 1: Apply the Edit**

Use the Edit tool on `/root/xelas.io/xelasphere.io-/app/templates/index.html`.

`old_string`:

```
    <script src="{{ url_for('static', filename='js/startNewChatModule.js') }}?v=19"></script>
```

`new_string`:

```
    <script src="{{ url_for('static', filename='js/startNewChatModule.js') }}?v=20"></script>
```

- [ ] **Step 2: Confirm the bump**

Run: `grep -n "startNewChatModule.js?v=" /root/xelas.io/xelasphere.io-/app/templates/index.html`
Expected: one hit reading `?v=20` (no `?v=19` left).

---

## Task 3: Mirror source → deployed

**Files:**
- Copy: `/root/xelas.io/xelasphere.io-/app/static/js/startNewChatModule.js` → `/opt/xelasphere/app/static/js/startNewChatModule.js`
- Copy: `/root/xelas.io/xelasphere.io-/app/templates/index.html` → `/opt/xelasphere/app/templates/index.html`

- [ ] **Step 1: Copy + chown both files**

Run:

```bash
cp /root/xelas.io/xelasphere.io-/app/static/js/startNewChatModule.js \
   /opt/xelasphere/app/static/js/startNewChatModule.js
cp /root/xelas.io/xelasphere.io-/app/templates/index.html \
   /opt/xelasphere/app/templates/index.html
chown xelasphere:xelasphere \
   /opt/xelasphere/app/static/js/startNewChatModule.js \
   /opt/xelasphere/app/templates/index.html
```

Expected: silent success, no errors.

- [ ] **Step 2: Verify byte-identity**

Run:

```bash
cmp -s /root/xelas.io/xelasphere.io-/app/static/js/startNewChatModule.js \
       /opt/xelasphere/app/static/js/startNewChatModule.js && echo "JS OK" || echo "JS DIVERGED"
cmp -s /root/xelas.io/xelasphere.io-/app/templates/index.html \
       /opt/xelasphere/app/templates/index.html && echo "HTML OK" || echo "HTML DIVERGED"
```

Expected: both lines print `OK`. If either prints `DIVERGED`, redo the copy from Step 1.

---

## Task 4: Restart service and smoke

- [ ] **Step 1: Restart**

Run: `systemctl restart xelasphere`
Expected: command exits 0.

- [ ] **Step 2: Wait for workers to bind port 5000**

Run (via Bash with `run_in_background: true`, then await the completion notification):

```bash
until curl -sS -o /dev/null --max-time 2 http://127.0.0.1:5000/ 2>/dev/null; do sleep 1; done; echo "READY"
```

Expected: notification fires when curl succeeds (typically 10-20s post-restart).

- [ ] **Step 3: Smoke probe + boot-error scan**

Run:

```bash
systemctl is-active xelasphere
curl -sS -o /dev/null -w "GET / HTTP %{http_code}\n" --max-time 5 http://127.0.0.1:5000/
journalctl -u xelasphere --since "1 minute ago" --no-pager 2>&1 | \
  grep -iE "ImportError|SyntaxError|ModuleNotFoundError|Traceback|FATAL" | head -5
```

Expected:
- `active`
- `GET / HTTP 200`
- empty grep output (no boot errors)

- [ ] **Step 4: Confirm browser would receive the bumped version**

Run: `curl -sS http://127.0.0.1:5000/ 2>&1 | grep -E "startNewChatModule.js"`
Expected: one line containing `?v=20`.

---

## Task 5: Manual fault-injection verification (browser)

This step requires a browser. The agent cannot perform it; the operator must.

- [ ] **Step 1: Confirm baseline — no false alarm**

In the browser:
1. Hard-refresh the chat (`Ctrl+Shift+R` or `Cmd+Shift+R`).
2. Open DevTools Console.
3. Wait for chat to fully render.
4. Confirm: NO `[style-replay] invariant violated` lines appear in the console. (Sweep is correctly setting data-style, so the assertion has nothing to flag.)

Expected: clean console (modulo unrelated app logs).

If `[style-replay]` errors fire on baseline → the sweep itself has a bug; stop here, surface the violation list (rid / expected / actual) and re-design.

- [ ] **Step 2: Inject a fault**

Still in DevTools Console, run:

```js
document.querySelector('.ai-message .text-message-container').setAttribute('data-style', 'WRONG');
```

This corrupts the `data-style` on the first AI bubble.

- [ ] **Step 3: Re-trigger renderActivePage**

In the sidebar, click on the currently-active chat session. This re-fires `renderActivePage` for the same session, which re-runs the sweep + assertion.

(If clicking the active session doesn't re-render, navigate to a different session and back. The sweep runs whenever a session is rendered.)

- [ ] **Step 4: Observe the assertion firing**

Expected console output:

```
[style-replay] invariant violated for 1 bubble(s): [{rid: "msg_...", expected: "editorial", actual: "WRONG"}]
```

(Or similar — the exact rid and style depend on which message you corrupted.)

The sweep itself will RE-FIX the corrupted attribute on that re-render (it sets data-style if missing, but here we set it to `'WRONG'` which is non-empty so the sweep skips it — meaning the assertion catches it). This is the proof that the assertion correctly reports a real-world failure mode.

If no `[style-replay]` line appears → the assertion didn't fire when it should have. Stop and debug — the assertion code in Task 1 didn't take effect, or the sweep is silently swallowing the violation.

- [ ] **Step 5: Repair the corruption**

Hard-refresh the chat once more (`Ctrl+Shift+R`). The fresh page load will re-build the DOM from scratch and the sweep will set `data-style` correctly.

Confirm the chat looks normal and no further `[style-replay]` errors appear.

---

## Task 6: Commit on fix/stream-first-line-pop

- [ ] **Step 1: Verify branch + clean state of unrelated files**

Run:

```bash
git -C /root/xelas.io/xelasphere.io- branch --show-current
git -C /root/xelas.io/xelasphere.io- status --porcelain app/static/js/startNewChatModule.js app/templates/index.html
```

Expected:
- Branch: `fix/stream-first-line-pop`
- Status: two files marked ` M` (modified, unstaged).

If branch isn't `fix/stream-first-line-pop` → switch with `git checkout fix/stream-first-line-pop` and re-verify before committing.

- [ ] **Step 2: Stage only the two files**

Run:

```bash
git -C /root/xelas.io/xelasphere.io- add \
  app/static/js/startNewChatModule.js \
  app/templates/index.html
```

Do NOT use `git add -A` — keep the staged set minimal.

- [ ] **Step 3: Commit (no push)**

Run via HEREDOC for proper formatting:

```bash
git -C /root/xelas.io/xelasphere.io- commit -m "$(cat <<'EOF'
feat(stream): runtime assertion for the data-style replay invariant

Adds a self-check at the end of the existing sweep in renderActivePage.
After the sweep applies data-style to every AI bubble whose persisted
message has a style field, walk the DOM one more time and console.error
any bubble whose data-style does not match the persisted value. Catches
future regressions in real sessions; no test framework needed.

Bumps startNewChatModule.js cache-bust to ?v=20 so browsers fetch the
new file.

Spec: docs/superpowers/specs/2026-05-01-style-replay-invariant-design.md

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 4: Confirm the commit landed**

Run:

```bash
git -C /root/xelas.io/xelasphere.io- log -1 --stat
git -C /root/xelas.io/xelasphere.io- status --porcelain
```

Expected:
- Last commit shows the new commit with two files (`app/static/js/startNewChatModule.js`, `app/templates/index.html`).
- Status is clean (no staged or unstaged changes for these files).

- [ ] **Step 5: Do NOT push**

`git push` is explicitly out of scope. The commit stays local on `fix/stream-first-line-pop`. The user pushes when they're ready, on their schedule.

---

## Acceptance criteria (matches spec)

After all 6 tasks complete:

- ✅ Sweep block at end of `renderActivePage` ends with the assertion (Task 1).
- ✅ Hard-refreshing a chat with mixed-style AI history continues to render every bubble with its persisted style and produces no `[style-replay]` errors on baseline (Task 5 step 1).
- ✅ Manually corrupting `data-style` and triggering re-render produces the `[style-replay] invariant violated` console error within the same render cycle (Task 5 step 4).
- ✅ Cache-bust query string on `startNewChatModule.js` is `?v=20` (Task 2).
- ✅ Source `fix/stream-first-line-pop` and deployed `/opt/xelasphere/` are byte-identical for the two touched files (Task 3 step 2).
- ✅ `xelasphere.service` is `active` and `GET /` returns HTTP 200 with no boot-time errors (Task 4).
- ✅ One commit on `fix/stream-first-line-pop`, no push (Task 6).
