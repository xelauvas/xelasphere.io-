import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

const __dirname = dirname(fileURLToPath(import.meta.url));
const sourcePath = resolve(__dirname, '../../app/static/js/codeWindow.js');
const source = readFileSync(sourcePath, 'utf-8');

// Source-level guards for the hideSimplePulsingDot fix.
//
// Background: the thinking-dot (.simple-pulsing-dot containing a
// .stream-cursor) used to fade out over 130ms via a CSS opacity
// transition. During that window, the new StreamRenderer bubble was
// already appended to #chat-display below the dot, each carrying its
// own pulsing cursor. Users saw TWO cursors stacked.
//
// The fix: remove the dot synchronously when hide is called. The new
// message bubble's contentFadeIn (300ms) provides visual continuity.
// Also defensive: sweep any orphaned .simple-pulsing-dot nodes that
// may have been left by a crashed/aborted prior stream.

function extractHideFunction(src) {
    const start = src.indexOf('function hideSimplePulsingDot');
    assert.ok(start !== -1, 'hideSimplePulsingDot function must exist');
    // Find the matching brace — simple counter.
    let depth = 0;
    let i = start;
    while (i < src.length) {
        const ch = src[i];
        if (ch === '{') depth++;
        else if (ch === '}') {
            depth--;
            if (depth === 0) return src.slice(start, i + 1);
        }
        i++;
    }
    throw new Error('unclosed hideSimplePulsingDot function');
}

test('hideSimplePulsingDot removes the tracked dot synchronously (no setTimeout fade)', () => {
    const fn = extractHideFunction(source);
    assert.doesNotMatch(
        fn,
        /setTimeout/,
        'hideSimplePulsingDot must not use setTimeout — the fade leaves the dot in layout while new cursor appears below'
    );
    assert.doesNotMatch(
        fn,
        /transition\s*=\s*['"]opacity/,
        'hideSimplePulsingDot must not set an opacity transition — sync remove only'
    );
});

test('hideSimplePulsingDot defensively sweeps stray .simple-pulsing-dot nodes', () => {
    const fn = extractHideFunction(source);
    assert.match(
        fn,
        /querySelectorAll\(['"]\.simple-pulsing-dot['"]\)/,
        'defensive sweep of all .simple-pulsing-dot elements must run to handle orphans from crashed prior streams'
    );
});

test('hideSimplePulsingDot still nulls the module-level ref after remove', () => {
    const fn = extractHideFunction(source);
    assert.match(
        fn,
        /simplePulsingDotElement\s*=\s*null/,
        'tracked ref must be nulled so the next show() call does not remove a stale node'
    );
});

test('showSimplePulsingDot uses thinking-dots (3 dots), NOT a single .stream-cursor', () => {
    // A single-dot indicator reads as the same visual as the in-stream cursor,
    // causing the "cursor jumped" illusion when the message bubble replaces
    // the indicator. Three staggered bouncing dots are visually distinct.
    const showStart = source.indexOf('function showSimplePulsingDot');
    assert.ok(showStart !== -1, 'showSimplePulsingDot must exist');
    // Find the function body end (matching braces).
    let depth = 0, i = showStart;
    while (i < source.length) {
        const ch = source[i];
        if (ch === '{') depth++;
        else if (ch === '}') { depth--; if (depth === 0) break; }
        i++;
    }
    const fn = source.slice(showStart, i + 1);
    assert.match(
        fn,
        /thinking-dots/,
        'showSimplePulsingDot must render a .thinking-dots block (3 bouncing dots typing indicator)'
    );
    assert.doesNotMatch(
        fn,
        /class=['"]stream-cursor['"]/,
        'showSimplePulsingDot must not render a single .stream-cursor — that reads as the same visual as the in-stream cursor and causes the "cursor jumped" illusion'
    );
});
