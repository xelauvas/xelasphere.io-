// Tests for app/static/js/stream/markdownEngine.js
//   node --test tests/stream/
// The parser tests need markdown-it (the page loads it from a CDN). Point
// MARKDOWN_IT_JS at a copy of markdown-it.min.js to run them; without it only the
// pure text helpers are tested.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';

globalThis.window = globalThis.window || {};
const mdPath = process.env.MARKDOWN_IT_JS;
if (mdPath && fs.existsSync(mdPath)) {
    const mod = { exports: {} };
    new Function('module', 'exports', 'self', fs.readFileSync(mdPath, 'utf8'))(mod, mod.exports, globalThis.window);
    globalThis.window.markdownit = globalThis.window.markdownit || mod.exports;
}
const hasParser = typeof globalThis.window.markdownit === 'function';

const engine = await import('../../app/static/js/stream/markdownEngine.js');
const { prepareStreamingText: live, prepareFinalText, joinSplitTableRows, parseBlocks } = engine;

const html = (md, opts) => parseBlocks(md, opts).map((b) => b.render().html).join('');

// ---------------------------------------------------------------- streaming tail
test('half-typed bold is closed, not shown as stars', () => {
    assert.equal(live('run **one command'), 'run **one command**');
    assert.equal(live('run **one command*'), 'run **one command**');
    assert.equal(live('at least **2 '), 'at least **2** ');
});

test('an opener with nothing after it is held back', () => {
    assert.equal(live('text **'), 'text ');
    assert.equal(live('see `'), 'see ');
});

test('inline code, strike and emphasis are closed', () => {
    assert.equal(live('see `comp'), 'see `comp`');
    assert.equal(live('~~str'), '~~str~~');
    assert.equal(live('some *ital'), 'some *ital*');
});

test('a link shows its label while the URL is still arriving', () => {
    assert.equal(live('a [link](https://ex'), 'a link');
});

test('a line that is only markdown punctuation waits for real text', () => {
    assert.equal(live('Text:\n-'), 'Text:');      // would flash as a heading underline
    assert.equal(live('a\n##'), 'a');
    assert.equal(live('a\n1.'), 'a');
});

test('a fence opener waits for its newline, also inside a list item', () => {
    assert.equal(live('a\n```py'), 'a');
    assert.equal(live('a\n3.  ```sql'), 'a');
});

test('text inside an open code fence is never touched', () => {
    const code = 'a\n```py\nx = "**" + `';
    assert.equal(live(code), code);
    const inList = 'a\n3.  ```sql\n    SELECT **';
    assert.equal(live(inList), inList);
});

test('a fence closed inside a list item does not swallow what follows', () => {
    const text = 'a\n3.  ```sql\n    SELECT 1;\n    ```\n4. x\n\n| a | b |\n| :---';
    assert.equal(live(text), 'a\n3.  ```sql\n    SELECT 1;\n    ```\n4. x\n');
});

test('a table appears only once its delimiter row is complete', () => {
    assert.equal(live('x\n\n| a | b |\n'), 'x\n');
    assert.equal(live('x\n\n| a | b |\n| :--- | :-'), 'x\n');
    assert.equal(live('x\n\n| a | b |\n| --- | --- |\n| 1'), 'x\n\n| a | b |\n| --- | --- |\n| 1');
});

test('table rows split by blank lines are joined, but not inside code', () => {
    assert.equal(joinSplitTableRows('| a |\n\n| - |\n\n| 1 |\n\nend'), '| a |\n| - |\n| 1 |\n\nend');
    const code = '```\n| x |\n\n| y |\n```';
    assert.equal(joinSplitTableRows(code), code);
});

test('final text drops the stock assistant closer only', () => {
    assert.equal(prepareFinalText('Done. How can I help you today?'), 'Done.');
    assert.equal(prepareFinalText('Let me know if you need more.'), 'Let me know if you need more.');
});

// ---------------------------------------------------------------- parser
test('numbered steps keep their numbers around code blocks', { skip: !hasParser }, () => {
    const md = '1. one\n\n   ```bash\n   ls\n   ```\n\n2. two\n\n```yaml\na: 1\n```\n\n3. three\n';
    const out = html(md);
    assert.match(out, /<ol start="3">/);
    assert.match(out, /<li>[\s\S]*class="code-window"[\s\S]*<\/li>/); // code inside the first item
});

test('every code block gets the same chrome, also inside a list', { skip: !hasParser }, () => {
    const out = html('- item\n\n  ```js\n  let a = 1;\n  ```\n');
    assert.match(out, /language-indicator">js</);
    assert.match(out, /class="copy-button"/);
    assert.match(out, /<code class="language-javascript">/);
});

test('LaTeX delimiters become math tokens, currency does not', { skip: !hasParser }, () => {
    const out = html('Inline \\(a+b\\) and $x^2$.\n\n\\[\nE = mc^2\n\\]\n\nCosts $5 and $10.');
    assert.equal((out.match(/class="xmd-math"/g) || []).length, 2);
    assert.match(out, /xmd-math--block/);
    assert.match(out, /Costs \$5 and \$10\./);
});

test('unfinished math is hidden while streaming and literal when final', { skip: !hasParser }, () => {
    assert.match(html('so \\(a + ', { streaming: true }), /xmd-math-pending/);
    assert.doesNotMatch(html('so \\(a + ', { streaming: false }), /xmd-math-pending/);
});

test('task lists render checkboxes', { skip: !hasParser }, () => {
    const out = html('- [x] done\n- [ ] todo\n');
    assert.match(out, /class="contains-task-list"/);
    assert.match(out, /<input class="xmd-task" type="checkbox" disabled checked>/);
});

test('file names are not turned into links; explicit URLs are', { skip: !hasParser }, () => {
    const out = html('Edit main.py and README.md, see https://example.com/x');
    assert.equal((out.match(/<a /g) || []).length, 1);
    assert.match(out, /target="_blank"/);
});

test('raw HTML stays text, except a few harmless inline tags', { skip: !hasParser }, () => {
    const out = html('a<br>b <kbd>Ctrl</kbd> <script>alert(1)</script> <img src=x onerror=alert(1)>');
    assert.match(out, /a<br>b/);
    assert.match(out, /<kbd>Ctrl<\/kbd>/);
    assert.doesNotMatch(out, /<script>/);
    assert.doesNotMatch(out, /<img/);
});

test('a single newline is a line break; a block keeps a stable key', { skip: !hasParser }, () => {
    assert.match(html('Roses are red,\nViolets are blue.'), /red,<br>\s*Violets/);
    const a = parseBlocks('# T\n\npara one\n\npara tw');
    const b = parseBlocks('# T\n\npara one\n\npara two and more');
    assert.equal(a[0].key, b[0].key);
    assert.equal(a[1].key, b[1].key);
    assert.notEqual(a[2].key, b[2].key);
});

test('a line that starts with inline code is not mistaken for a fence', () => {
    assert.equal(live('- `llama'), '- `llama`');
    assert.equal(live('`config.py` is'), '`config.py` is');
});
