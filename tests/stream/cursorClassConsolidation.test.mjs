import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve, join, extname } from 'node:path';

const __dirname = dirname(fileURLToPath(import.meta.url));
const repoRoot = resolve(__dirname, '../..');

// The legacy .streaming-cursor class was aliased to .stream-cursor
// (CSS rules lived in lockstep, JS used whichever, lint noise all over).
// They've been collapsed to a single canonical .stream-cursor. This
// test is the fence — no source file should reintroduce the old name.
//
// Scanned dirs: app/static/{css,js} and tests. Skipped: morphdom vendor
// bundle (not ours), __pycache__, node_modules (if any).

const SCAN_DIRS = [
    join(repoRoot, 'app', 'static', 'css'),
    join(repoRoot, 'app', 'static', 'js'),
    join(repoRoot, 'app', 'templates'),
    join(repoRoot, 'tests'),
];
const SKIP_FILES = ['morphdom.min.js'];
const SKIP_DIRS = ['__pycache__', 'node_modules', '.pytest_cache'];
const SCAN_EXT = new Set(['.css', '.js', '.mjs', '.html', '.py']);

// This test file itself contains the banned string inside the assertion
// literal — skip it from the scan or the test self-fails.
const SELF_BASENAME = 'cursorClassConsolidation.test.mjs';

function walk(dir) {
    const out = [];
    let entries;
    try {
        entries = readdirSync(dir);
    } catch {
        return out;
    }
    for (const name of entries) {
        if (SKIP_DIRS.includes(name)) continue;
        const full = join(dir, name);
        const st = statSync(full);
        if (st.isDirectory()) out.push(...walk(full));
        else if (st.isFile()
                 && SCAN_EXT.has(extname(name))
                 && !SKIP_FILES.includes(name)
                 && name !== SELF_BASENAME) {
            out.push(full);
        }
    }
    return out;
}

test('no source references .streaming-cursor (legacy class name retired)', () => {
    const offenders = [];
    for (const dir of SCAN_DIRS) {
        for (const file of walk(dir)) {
            const content = readFileSync(file, 'utf-8');
            if (content.includes('streaming-cursor')) {
                offenders.push(file.replace(repoRoot + '/', ''));
            }
        }
    }
    assert.equal(
        offenders.length,
        0,
        `Legacy .streaming-cursor references found in:\n  ${offenders.join('\n  ')}\n` +
        `Use .stream-cursor instead. Only one canonical class exists.`
    );
});

test('stream-blocks.css defines a single .stream-cursor rule (no alias)', () => {
    const css = readFileSync(
        join(repoRoot, 'app', 'static', 'css', 'stream-blocks.css'),
        'utf-8'
    );
    // The cursor visual rule must be keyed on .stream-cursor alone, not
    // a ", " alias chain. Match the actual animation-bearing declaration.
    const ruleMatch = css.match(/(\.[\w-]+(?:\s*,\s*\.[\w-]+)*)\s*\{[^}]*animation:\s*chatgpt-pulse/);
    assert.ok(ruleMatch, 'cursor keyframe rule must exist in stream-blocks.css');
    const selectors = ruleMatch[1].split(',').map(s => s.trim());
    assert.deepEqual(
        selectors,
        ['.stream-cursor'],
        `cursor rule selector must be exactly [.stream-cursor], got ${JSON.stringify(selectors)}`
    );
});
