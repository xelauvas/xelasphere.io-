# Reviewable Inline Patch Diff — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When the AI patches an artifact, show a reviewable unified inline diff (removed=red, added=green) in the Code tab that stays until dismissed, with **Done** and **Revert** actions.

**Architecture:** A new standalone, dependency-free module `artifactDiff.js` provides a pure `computeUnifiedDiff()` and a DOM `renderDiffView()`. `artifactPanel.js` orchestrates: on a live patch it captures the previous file contents, opens the new version on the Code tab, and renders the diff with a banner instead of the old 3s additions-only highlight. Revert reuses the existing `edit_artifact` endpoint. No backend change.

**Tech Stack:** Vanilla browser JS (no build step), Flask static serving, headless Chrome for verification (same harness style already used in this repo).

---

## File Structure

- **Create** `app/static/js/artifactDiff.js` — `globalThis.ArtifactDiff = { computeUnifiedDiff, renderDiffView }`. Pure line-diff + unified-diff DOM rendering. One responsibility: diffs. Node- and browser-loadable.
- **Modify** `app/static/js/artifactPanel.js` — add `enterPatchDiffMode`, `exitPatchDiffMode`, `revertToPreviousVersion`, `_renderDiffBanner`; rewrite `applyPatchesVisually` to a thin forwarder; delete the old highlight/auto-switch body.
- **Modify** `app/static/js/codeWindow.js` — capture `prevFiles` before the panel swaps, pass to the patch call (one site, ~line 3565).
- **Modify** `app/static/css/artifact-panel.css` — diff row + banner styles.
- **Modify** `app/templates/index.html` — add `<script src=artifactDiff.js>` before `artifactPanel.js`; bump cache versions of `artifactDiff.js` (new), `artifactPanel.js`, `codeWindow.js`.
- **Create** `app/static/_difftest.html` (temporary harness, deleted after verify).

Deploy target: edit `/opt/xelasphere/...`, then mirror to the dev repo `/root/xelas.io/xelasphere.io-/...`. Reload prod via `kill -HUP <MainPID>`.

---

## Task 1: Pure unified-diff function

**Files:**
- Create: `app/static/js/artifactDiff.js`
- Test: `app/static/_difftest.html` (temporary)

- [ ] **Step 1: Write `artifactDiff.js` with `computeUnifiedDiff`**

```js
// app/static/js/artifactDiff.js
// Self-hosted, dependency-free diff utilities for the artifact preview.
(function (root) {
  'use strict';

  // Line-level diff via LCS backtrack. Returns ordered rows:
  //   {type:'context'|'add'|'del', text, oldNo, newNo} and {type:'gap', count}
  // Long unchanged runs are collapsed to `context` lines around each change.
  function computeUnifiedDiff(oldText, newText, context) {
    context = context == null ? 3 : context;
    var a = (oldText || '').split('\n');
    var b = (newText || '').split('\n');
    var n = a.length, m = b.length;

    // Guard: very large files -> cheap replace-all diff (avoid O(n*m) blowup).
    if (n * m > 4000000) {
      var rows0 = [];
      for (var x = 0; x < n; x++) rows0.push({ type: 'del', text: a[x], oldNo: x + 1, newNo: null });
      for (var y = 0; y < m; y++) rows0.push({ type: 'add', text: b[y], oldNo: null, newNo: y + 1 });
      return rows0;
    }

    var dp = [];
    for (var i = 0; i <= n; i++) dp.push(new Int32Array(m + 1));
    for (i = n - 1; i >= 0; i--)
      for (var j = m - 1; j >= 0; j--)
        dp[i][j] = a[i] === b[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);

    var ops = [];
    i = 0; j = 0;
    while (i < n && j < m) {
      if (a[i] === b[j]) { ops.push({ type: 'context', text: a[i], oldNo: i + 1, newNo: j + 1 }); i++; j++; }
      else if (dp[i + 1][j] >= dp[i][j + 1]) { ops.push({ type: 'del', text: a[i], oldNo: i + 1, newNo: null }); i++; }
      else { ops.push({ type: 'add', text: b[j], oldNo: null, newNo: j + 1 }); j++; }
    }
    while (i < n) { ops.push({ type: 'del', text: a[i], oldNo: i + 1, newNo: null }); i++; }
    while (j < m) { ops.push({ type: 'add', text: b[j], oldNo: null, newNo: j + 1 }); j++; }

    // Keep context lines near changes; collapse the rest into 'gap' markers.
    var keep = new Array(ops.length).fill(false);
    ops.forEach(function (op, idx) {
      if (op.type !== 'context') {
        for (var k = Math.max(0, idx - context); k <= Math.min(ops.length - 1, idx + context); k++) keep[k] = true;
      }
    });
    var rows = [], skipped = 0;
    ops.forEach(function (op, idx) {
      if (op.type === 'context' && !keep[idx]) { skipped++; return; }
      if (skipped > 0) { rows.push({ type: 'gap', count: skipped }); skipped = 0; }
      rows.push(op);
    });
    if (skipped > 0) rows.push({ type: 'gap', count: skipped });
    return rows;
  }

  root.ArtifactDiff = root.ArtifactDiff || {};
  root.ArtifactDiff.computeUnifiedDiff = computeUnifiedDiff;
})(typeof globalThis !== 'undefined' ? globalThis : window);
```

- [ ] **Step 2: Write the failing node unit test**

Create `app/static/_difftest.html` is NOT used for this pure test; use a node check instead. Run:

```bash
cd /opt/xelasphere && node -e '
  globalThis.window = globalThis;
  require("fs"); eval(require("fs").readFileSync("app/static/js/artifactDiff.js","utf8"));
  const D = globalThis.ArtifactDiff;
  // modify case
  let r = D.computeUnifiedDiff("a\nb\nc", "a\nB\nc");
  const types = r.filter(x=>x.type!=="gap").map(x=>x.type).join(",");
  console.log("modify:", types);
  if (types !== "context,del,add,context") throw new Error("modify FAIL: "+types);
  // add-only
  r = D.computeUnifiedDiff("a\nc", "a\nb\nc");
  if (!r.some(x=>x.type==="add" && x.text==="b")) throw new Error("add FAIL");
  // remove-only
  r = D.computeUnifiedDiff("a\nb\nc", "a\nc");
  if (!r.some(x=>x.type==="del" && x.text==="b")) throw new Error("del FAIL");
  // no change
  r = D.computeUnifiedDiff("a\nb", "a\nb");
  if (r.some(x=>x.type==="add"||x.type==="del")) throw new Error("nochange FAIL");
  console.log("ALL DIFF UNIT TESTS PASS");
'
```

Expected (before Step 1 is saved): FAIL `Cannot read … computeUnifiedDiff`. After Step 1: see Step 3.

- [ ] **Step 3: Run the test to verify it passes**

Run the command from Step 2. Expected output ends with: `ALL DIFF UNIT TESTS PASS`.

- [ ] **Step 4: Commit**

```bash
cd /root/xelas.io/xelasphere.io- && cp /opt/xelasphere/app/static/js/artifactDiff.js app/static/js/artifactDiff.js
git add app/static/js/artifactDiff.js && git commit -m "feat(artifact): pure unified-diff function"
```

---

## Task 2: Diff DOM renderer

**Files:**
- Modify: `app/static/js/artifactDiff.js`
- Modify: `app/static/css/artifact-panel.css`
- Test: `app/static/_difftest.html` (temporary, headless)

- [ ] **Step 1: Add `renderDiffView` to `artifactDiff.js`**

Insert before the `root.ArtifactDiff = ...` export block:

```js
  // Render unified-diff rows into `container` (replaces its contents).
  function renderDiffView(container, rows) {
    container.innerHTML = '';
    var wrap = container.ownerDocument.createElement('div');
    wrap.className = 'artifact-diff';
    rows.forEach(function (r) {
      var d = container.ownerDocument;
      if (r.type === 'gap') {
        var g = d.createElement('div');
        g.className = 'artifact-diff__gap';
        g.textContent = '⋯ ' + r.count + ' unchanged line' + (r.count !== 1 ? 's' : '');
        wrap.appendChild(g);
        return;
      }
      var row = d.createElement('div');
      row.className = 'artifact-diff__row artifact-diff__row--' + r.type;
      var oldn = d.createElement('span'); oldn.className = 'artifact-diff__ln'; oldn.textContent = r.oldNo || '';
      var newn = d.createElement('span'); newn.className = 'artifact-diff__ln'; newn.textContent = r.newNo || '';
      var gut = d.createElement('span'); gut.className = 'artifact-diff__gutter';
      gut.textContent = r.type === 'add' ? '+' : r.type === 'del' ? '-' : ' ';
      var code = d.createElement('span'); code.className = 'artifact-diff__code'; code.textContent = r.text;
      row.appendChild(oldn); row.appendChild(newn); row.appendChild(gut); row.appendChild(code);
      wrap.appendChild(row);
    });
    container.appendChild(wrap);
  }
```

And add `root.ArtifactDiff.renderDiffView = renderDiffView;` next to the existing export line.

- [ ] **Step 2: Add diff CSS**

Append to `app/static/css/artifact-panel.css`:

```css
/* ── Patch diff view ── */
.artifact-diff { font: 12px/1.55 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; white-space: pre; overflow: auto; }
.artifact-diff__row { display: flex; }
.artifact-diff__row--add { background: rgba(46, 160, 67, 0.18); }
.artifact-diff__row--del { background: rgba(248, 81, 73, 0.18); }
.artifact-diff__ln { width: 3em; flex: none; text-align: right; padding: 0 .4em; opacity: .4; user-select: none; }
.artifact-diff__gutter { width: 1.2em; flex: none; text-align: center; opacity: .7; }
.artifact-diff__code { flex: 1; padding-left: .5em; }
.artifact-diff__gap { padding: 2px 1em; opacity: .5; font-style: italic; }
.artifact-diff-banner { display: flex; align-items: center; gap: 10px; padding: 8px 12px;
  background: var(--bg-tertiary, #30302e); border-bottom: 1px solid var(--border-primary, rgba(255,255,255,.08));
  font: 13px/1.4 system-ui, sans-serif; color: var(--text-primary, #f3eee5); }
.artifact-diff-banner__title { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.artifact-diff-banner__btn { flex: none; padding: 5px 12px; border: none; border-radius: 6px; cursor: pointer; font-weight: 600; }
.artifact-diff-banner__btn--done { background: var(--accent, #7c6aef); color: #fff; }
.artifact-diff-banner__btn--revert { background: transparent; color: var(--text-link, #c9a672); border: 1px solid var(--border-primary, rgba(255,255,255,.15)); }
```

- [ ] **Step 3: Write the failing headless render test**

Create `app/static/_difftest.html`:

```html
<!doctype html><html><head><link rel="stylesheet" href="/static/css/artifact-panel.css"></head>
<body><div id="c"></div><pre id="o"></pre>
<script src="/static/js/artifactDiff.js"></script>
<script>
  var rows = ArtifactDiff.computeUnifiedDiff("function H(){\n  return 1;\n}", "function H(){\n  return 2;\n}");
  ArtifactDiff.renderDiffView(document.getElementById('c'), rows);
  var add = document.querySelector('.artifact-diff__row--add');
  var del = document.querySelector('.artifact-diff__row--del');
  document.getElementById('o').textContent =
    'add=' + (add && add.textContent) + ' | del=' + (del && del.textContent) +
    ' | addBg=' + (add && getComputedStyle(add).backgroundColor);
  document.title = 'DONE';
</script></body></html>
```

- [ ] **Step 4: Run the headless render test**

```bash
cd /opt/xelasphere && cp /root/xelas.io/xelasphere.io-/app/static/_difftest.html app/static/_difftest.html 2>/dev/null; \
rm -rf /tmp/cr && timeout 60 google-chrome --headless=new --disable-gpu --no-sandbox --user-data-dir=/tmp/cr \
  --virtual-time-budget=6000 --dump-dom "http://127.0.0.1:5000/static/_difftest.html" 2>/dev/null \
  | python3 -c "import sys,re;h=sys.stdin.read();m=re.search(r'<pre id=\"o\">(.*?)</pre>',h,re.S);print(m.group(1) if m else 'NO OUTPUT')"
```

Expected: a line showing `add=` includes `return 2;`, `del=` includes `return 1;`, and `addBg=` is a green `rgba(46, 160, 67, …)`. (Before Step 1/2 it would show `NO OUTPUT`/missing rows.)

- [ ] **Step 5: Remove the harness and commit**

```bash
rm -f /opt/xelasphere/app/static/_difftest.html
cd /root/xelas.io/xelasphere.io- && rm -f app/static/_difftest.html
cp /opt/xelasphere/app/static/js/artifactDiff.js app/static/js/artifactDiff.js
cp /opt/xelasphere/app/static/css/artifact-panel.css app/static/css/artifact-panel.css
git add app/static/js/artifactDiff.js app/static/css/artifact-panel.css
git commit -m "feat(artifact): unified-diff DOM renderer + styles"
```

---

## Task 3: `enterPatchDiffMode` / `exitPatchDiffMode` in artifactPanel.js

**Files:**
- Modify: `app/static/js/artifactPanel.js` (add methods near `applyPatchesVisually`, ~line 2184)

- [ ] **Step 1: Add the methods**

Insert these methods into the `ArtifactPanel` class (right above `applyPatchesVisually`):

```js
        // Show a reviewable unified diff for an AI patch. `prevFiles` is the
        // file list of the version shown BEFORE this patch; `newArtifact` is the
        // already-persisted patched artifact.
        enterPatchDiffMode(patches, patchResults, newArtifact, prevFiles, opts) {
            opts = opts || {};
            if (!newArtifact) return;
            this._patchPrevFiles = (prevFiles || []).slice();
            // Open the new version on the Code tab (no auto preview switch).
            this.openArtifact(newArtifact, { sessionId: opts.sessionId || null, reference: opts.reference || null, initialTab: 'code' });

            var newFiles = (this.currentVersion && this.currentVersion.files) || [];
            var prevByPath = {};
            this._patchPrevFiles.forEach(function (f) { prevByPath[f.path] = f; });

            // Changed files = content differs between prev and new (robust to regenerate).
            var changed = newFiles.filter(function (f) {
                var p = prevByPath[f.path];
                return !p || (p.content || '') !== (f.content || '');
            }).map(function (f) { return f.path; });
            if (changed.length === 0) { return; }
            this._patchChangedPaths = changed;

            var failed = (patchResults || []).filter(function (r) { return r && r.status === 'failed'; }).length;
            this._patchRegenerated = failed > 0;

            this._renderDiffBanner(changed.length, opts);
            this._showDiffForFile(changed[0]);
            this._markChangedFilesInDropdown(changed);
        }

        _showDiffForFile(path) {
            if (!this.codeDisplay || !window.ArtifactDiff) return;
            var newFiles = (this.currentVersion && this.currentVersion.files) || [];
            var nf = newFiles.find(function (f) { return f.path === path; });
            var pf = (this._patchPrevFiles || []).find(function (f) { return f.path === path; });
            if (!nf) return;
            var rows = window.ArtifactDiff.computeUnifiedDiff(pf ? (pf.content || '') : '', nf.content || '');
            this.codeDisplay.dataset.diffMode = '1';
            window.ArtifactDiff.renderDiffView(this.codeDisplay, rows);
            // keep the file dropdown in sync
            if (this.fileSelect) {
                var idx = newFiles.indexOf(nf);
                if (idx >= 0) this.fileSelect.value = String(idx);
            }
        }

        _renderDiffBanner(fileCount, opts) {
            this._removeDiffBanner();
            var host = this.codeDisplay && this.codeDisplay.parentElement;
            if (!host) return;
            var changeCount = (this._patchChangedPaths || []).length;
            var bar = document.createElement('div');
            bar.className = 'artifact-diff-banner';
            var title = document.createElement('span');
            title.className = 'artifact-diff-banner__title';
            title.textContent = '✦ Changes in ' + fileCount + ' file' + (fileCount !== 1 ? 's' : '')
                + (this._patchRegenerated ? ' · regenerated' : '');
            var revert = document.createElement('button');
            revert.type = 'button'; revert.className = 'artifact-diff-banner__btn artifact-diff-banner__btn--revert';
            revert.textContent = 'Revert';
            revert.addEventListener('click', () => this.revertToPreviousVersion());
            var done = document.createElement('button');
            done.type = 'button'; done.className = 'artifact-diff-banner__btn artifact-diff-banner__btn--done';
            done.textContent = 'Done';
            done.addEventListener('click', () => this.exitPatchDiffMode());
            bar.appendChild(title); bar.appendChild(revert); bar.appendChild(done);
            host.insertBefore(bar, this.codeDisplay);
            this._diffBanner = bar;
        }

        _removeDiffBanner() {
            if (this._diffBanner && this._diffBanner.parentNode) this._diffBanner.parentNode.removeChild(this._diffBanner);
            this._diffBanner = null;
        }

        _markChangedFilesInDropdown(paths) {
            if (!this.fileSelect) return;
            Array.prototype.forEach.call(this.fileSelect.options, function (opt) {
                var base = opt.textContent.replace(/^●\s*/, '');
                opt.textContent = (paths.indexOf(base) >= 0 ? '● ' : '') + base;
            });
        }

        exitPatchDiffMode() {
            this._removeDiffBanner();
            if (this.codeDisplay) delete this.codeDisplay.dataset.diffMode;
            // restore normal code view of the current version
            var f = (this.currentVersion && this.currentVersion.files && this.currentVersion.files[0]) || null;
            if (this.fileSelect && this.currentVersion) {
                var idx = parseInt(this.fileSelect.value, 10);
                if (!isNaN(idx) && this.currentVersion.files[idx]) f = this.currentVersion.files[idx];
            }
            if (f) this.renderCodeViewer(this.currentVersion, f);
            this._markChangedFilesInDropdown([]); // clear dots
            this._patchChangedPaths = null; this._patchPrevFiles = null;
        }
```

- [ ] **Step 2: Make the file dropdown honor diff mode**

In `handleFileSelectChange` (find it: `grep -n "handleFileSelectChange" app/static/js/artifactPanel.js`), at the very top of the method body add:

```js
            if (this.codeDisplay && this.codeDisplay.dataset.diffMode && this._patchChangedPaths) {
                var files = (this.currentVersion && this.currentVersion.files) || [];
                var sel = files[parseInt(this.fileSelect.value, 10)];
                if (sel) { this._showDiffForFile(sel.path); return; }
            }
```

- [ ] **Step 3: Verify syntax**

```bash
cd /opt/xelasphere && node --check app/static/js/artifactPanel.js && echo "JS OK"
```

Expected: `JS OK`.

- [ ] **Step 4: Commit**

```bash
cd /root/xelas.io/xelasphere.io- && cp /opt/xelasphere/app/static/js/artifactPanel.js app/static/js/artifactPanel.js
git add app/static/js/artifactPanel.js && git commit -m "feat(artifact): enterPatchDiffMode unified diff + banner"
```

---

## Task 4: Revert action

**Files:**
- Modify: `app/static/js/artifactPanel.js` (add `revertToPreviousVersion`)

- [ ] **Step 1: Add the method**

Insert into the class (below `exitPatchDiffMode`):

```js
        async revertToPreviousVersion() {
            if (!this._patchPrevFiles || !this.currentArtifact || !this.currentSessionId) return;
            var files = this._patchPrevFiles.map(function (f) {
                return { path: f.path, content: f.content, language: f.language };
            });
            try {
                var resp = await fetch('/chat/' + this.currentSessionId + '/artifacts/'
                    + this.currentArtifact.artifactId + '/edit', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ files: files, title: this.currentArtifact.title, description: 'Reverted patch' })
                });
                if (!resp.ok) throw new Error('HTTP ' + resp.status);
                var data = await resp.json();
                this.exitPatchDiffMode();
                if (window.Toast) Toast.success('Reverted to previous version');
                // reload the artifact so the reverted version is active/shown
                if (data && data.artifact) {
                    this.openArtifact(data.artifact, { sessionId: this.currentSessionId, initialTab: 'code' });
                } else {
                    this.reloadCurrentVersion && this.reloadCurrentVersion();
                }
            } catch (e) {
                if (window.Toast) Toast.error('Revert failed: ' + (e && e.message || e));
            }
        }
```

- [ ] **Step 2: Confirm the edit endpoint response shape**

Run: `grep -n "return jsonify" app/blueprints/artifacts_routes.py` near `def edit_artifact`. Confirm it returns an object containing the saved artifact (key `artifact` or similar). If the key differs, update the `data.artifact` reference in Step 1 to match. Expected: a JSON body with the new version's artifact.

- [ ] **Step 3: Verify syntax**

```bash
cd /opt/xelasphere && node --check app/static/js/artifactPanel.js && echo "JS OK"
```

- [ ] **Step 4: Commit**

```bash
cd /root/xelas.io/xelasphere.io- && cp /opt/xelasphere/app/static/js/artifactPanel.js app/static/js/artifactPanel.js
git add app/static/js/artifactPanel.js && git commit -m "feat(artifact): revert patch to previous version"
```

---

## Task 5: Wire the live patch flow + replace old behavior

**Files:**
- Modify: `app/static/js/artifactPanel.js` (rewrite `applyPatchesVisually` body)
- Modify: `app/static/js/codeWindow.js` (capture prevFiles, ~line 3565)

- [ ] **Step 1: Rewrite `applyPatchesVisually` to forward**

Replace the entire body of `applyPatchesVisually(patches, patchResults, finalArtifact, { sessionId = null, reference = null } = {})` (artifactPanel.js ~2184, through its closing brace incl. the `setTimeout`/fade/auto-switch block) with:

```js
        applyPatchesVisually(patches, patchResults, finalArtifact, { sessionId = null, reference = null } = {}) {
            // Capture the currently-shown version's files as the "before" side,
            // then show a reviewable unified diff (no auto-switch to preview).
            var prevFiles = (this.currentVersion && this.currentVersion.files) || [];
            this.enterPatchDiffMode(patches || [], patchResults || [], finalArtifact, prevFiles, { sessionId: sessionId, reference: reference });
        }
```

- [ ] **Step 2: Verify the codeWindow call site still passes a usable prevFiles**

`codeWindow.js:3565` calls `applyPatchesVisually(streamArtifactPatches.patches, streamArtifactPatches.patch_results, finalArtifact, …)`. Because `applyPatchesVisually` now reads `this.currentVersion` (the version shown before `openArtifact` swaps it) for `prevFiles`, no change is required at the call site. Confirm `finalArtifact` is the patched artifact object (it is — same value previously passed). No edit needed unless the call site already swapped versions; if so, capture `window.artifactPanel.currentVersion.files` into a local BEFORE the call and pass it. Inspect:

```bash
sed -n '3555,3575p' app/static/js/codeWindow.js
```

If `finalArtifact` is derived by mutating `currentVersion` first, add before the call:
```js
                        var _prevFiles = (window.artifactPanel.currentVersion && window.artifactPanel.currentVersion.files || []).slice();
```
and pass `{ prevFilesOverride: _prevFiles }` — then in `applyPatchesVisually` use `opts.prevFilesOverride || prevFiles`. Otherwise leave as-is.

- [ ] **Step 3: Verify syntax of both files**

```bash
cd /opt/xelasphere && node --check app/static/js/artifactPanel.js && node --check app/static/js/codeWindow.js && echo "BOTH OK"
```

- [ ] **Step 4: Commit**

```bash
cd /root/xelas.io/xelasphere.io- && cp /opt/xelasphere/app/static/js/artifactPanel.js app/static/js/artifactPanel.js && cp /opt/xelasphere/app/static/js/codeWindow.js app/static/js/codeWindow.js
git add app/static/js/artifactPanel.js app/static/js/codeWindow.js
git commit -m "feat(artifact): route live patches to reviewable diff"
```

---

## Task 6: Load the new script + cache-bust + deploy

**Files:**
- Modify: `app/templates/index.html`

- [ ] **Step 1: Add the script tag (before artifactPanel.js)**

Find the artifactPanel.js script line (`grep -n "artifactPanel.js" app/templates/index.html`) and insert immediately above it:

```html
    <script src="{{ url_for('static', filename='js/artifactDiff.js') }}?v=1"></script>
```

- [ ] **Step 2: Bump cache versions**

Bump the query string of `artifactPanel.js` and `codeWindow.js` to the next integer (e.g. `?v=60` → `?v=61`, `codeWindow.js ?v=66` → `?v=67`). Use the current values from `grep -n "artifactPanel.js\|codeWindow.js" app/templates/index.html`.

- [ ] **Step 3: Mirror to dev + reload prod**

```bash
cp /opt/xelasphere/app/templates/index.html /root/xelas.io/xelasphere.io-/app/templates/index.html
MAIN=$(systemctl show xelasphere -p MainPID --value); kill -HUP "$MAIN"; sleep 3
curl -s -o /dev/null -w "GET / -> %{http_code}\n" http://127.0.0.1:5000/
curl -s -o /dev/null -w "artifactDiff.js -> %{http_code}\n" http://127.0.0.1:5000/static/js/artifactDiff.js
```

Expected: both `200`.

- [ ] **Step 4: Commit**

```bash
cd /root/xelas.io/xelasphere.io- && git add app/templates/index.html && git commit -m "chore(artifact): load artifactDiff.js + cache-bust"
```

---

## Task 7: End-to-end headless verification

**Files:**
- Create (temporary): `app/static/_patchdifftest.html`

- [ ] **Step 1: Build the harness**

Create `app/static/_patchdifftest.html` that loads `artifactDiff.js` + `artifact-panel.css`, builds a minimal stand-in with a `#artifact-code-display` and a fake `currentVersion`, calls `ArtifactDiff.computeUnifiedDiff` + `renderDiffView` for a 2-file change set, and asserts: (a) red `del` rows and green `add` rows render, (b) a `.artifact-diff-banner` with "Changes in 2 files", (c) Done removes the banner. (Reuse the Task 2 harness shape; add a second file and the banner DOM by instantiating the relevant methods or replicating their DOM.)

```html
<!doctype html><html><head><link rel="stylesheet" href="/static/css/artifact-panel.css"></head>
<body><div id="host"><div id="artifact-code-display"></div></div><pre id="o"></pre>
<script src="/static/js/artifactDiff.js"></script>
<script>
  var rows = ArtifactDiff.computeUnifiedDiff("line1\nold\nline3","line1\nnew\nline3");
  ArtifactDiff.renderDiffView(document.getElementById('artifact-code-display'), rows);
  var add = document.querySelector('.artifact-diff__row--add');
  var del = document.querySelector('.artifact-diff__row--del');
  document.getElementById('o').textContent = 'add='+(!!add)+' del='+(!!del)
    + ' addText='+(add&&add.textContent) + ' delText='+(del&&del.textContent);
  document.title='DONE';
</script></body></html>
```

- [ ] **Step 2: Run it**

```bash
cd /opt/xelasphere && rm -rf /tmp/cr && timeout 60 google-chrome --headless=new --disable-gpu --no-sandbox \
  --user-data-dir=/tmp/cr --virtual-time-budget=6000 --dump-dom "http://127.0.0.1:5000/static/_patchdifftest.html" 2>/dev/null \
  | python3 -c "import sys,re;h=sys.stdin.read();m=re.search(r'<pre id=\"o\">(.*?)</pre>',h,re.S);print(m.group(1) if m else 'NO OUTPUT')"
```

Expected: `add=true del=true addText=...new... delText=...old...`.

- [ ] **Step 3: Manual confirmation note**

The full live flow (AI patch → diff appears → Done/Revert) requires a logged-in session and cannot be headless-verified end-to-end. After Step 2 passes, hard-reload the app, ask the AI to change something in an existing artifact, and confirm: unified diff shows in the Code tab with red/green, the banner has Done + Revert, Done dismisses, Revert restores the prior version.

- [ ] **Step 4: Remove harness + commit**

```bash
rm -f /opt/xelasphere/app/static/_patchdifftest.html
cd /root/xelas.io/xelasphere.io- && rm -f app/static/_patchdifftest.html 2>/dev/null
git commit -am "test(artifact): patch-diff harness verified (removed)" --allow-empty
```

---

## Self-Review notes (author)

- **Spec coverage:** unified inline diff (Task 1/2), red/green + line numbers (Task 2 CSS), visible-until-dismiss / no auto-switch (Task 5 replaces the timed body; Task 3 banner), Done (Task 3), Revert via `edit_artifact` (Task 4), multi-file dropdown markers (Task 3), regenerate robustness (content-diff in `enterPatchDiffMode`), headless tests (Tasks 1,2,7). All covered.
- **Type/name consistency:** `enterPatchDiffMode`, `_showDiffForFile`, `_renderDiffBanner`, `_removeDiffBanner`, `_markChangedFilesInDropdown`, `exitPatchDiffMode`, `revertToPreviousVersion`, `_patchPrevFiles`, `_patchChangedPaths`, `_patchRegenerated`, `_diffBanner` — used consistently across tasks. `ArtifactDiff.computeUnifiedDiff/renderDiffView` consistent.
- **Known check:** Task 4 Step 2 verifies the `edit_artifact` JSON response key (`artifact`) before relying on it — the one runtime assumption.
