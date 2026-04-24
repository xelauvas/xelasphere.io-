import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

const __dirname = dirname(fileURLToPath(import.meta.url));
const sourcePath = resolve(__dirname, '../../app/static/js/stream/renderers/prose.js');
const source = readFileSync(sourcePath, 'utf-8');

// Source-level guards for the marked.js fallback recovery path.
//
// Background: if window.marked isn't ready when prose.update() runs
// (CDN failure, async module race), the block falls back to textContent
// with dataset.proseFallback='1'. On replay, update() is called sync
// via append().finalize() — no later update is scheduled, so a late-
// loading marked would leave the block as plaintext forever.
//
// The fix: when fallback is taken, schedule a RAF-polled watcher that
// re-runs update() once window.marked becomes available, bounded by a
// max attempt count so the poller doesn't leak when marked truly never
// loads.

test('prose fallback flags the block with proseFallback dataset', () => {
    assert.match(
        source,
        /block\.el\.dataset\.proseFallback\s*=\s*['"]1['"]/,
        'fallback must tag the block so the next parse knows to blow away stale textContent'
    );
});

test('prose fallback schedules a RAF watcher for marked readiness', () => {
    assert.match(
        source,
        /_markedWatcherId[\s\S]*requestAnimationFrame/,
        'fallback must schedule a watcher via requestAnimationFrame so late-loading marked triggers re-render'
    );
});

test('prose fallback watcher is bounded (no infinite leak)', () => {
    assert.match(
        source,
        /MAX_ATTEMPTS\s*=\s*\d+/,
        'watcher must have a max attempt cap so it stops polling if marked never loads'
    );
});

test('prose fallback watcher clears its handle when done', () => {
    // Both success path (marked loaded) and give-up path (max attempts) must
    // null the watcher id so the same block can retry on later updates.
    const matches = source.match(/block\.meta\._markedWatcherId\s*=\s*null/g) || [];
    assert.ok(
        matches.length >= 2,
        `watcher must null its id on both success and give-up paths (found ${matches.length}, need ≥2)`
    );
});
