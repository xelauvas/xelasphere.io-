import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

const __dirname = dirname(fileURLToPath(import.meta.url));
const sourcePath = resolve(__dirname, '../../app/static/js/streamRenderer.js');
const source = readFileSync(sourcePath, 'utf-8');

// Source-level guards for the live-stream pace governor.
//
// Background: _drainPaced used max(floor, ceil(qlen/PACE_CATCHUP_FRAMES))
// unbounded. A large single-chunk SSE event (cached response, search-
// synthesis flush, ACTION buffer flush, fast providers like Groq)
// balloons qlen and drains 30+ chars/frame, which reads as visible
// "chunks" instead of smooth streaming.
//
// The fix caps charsThisFrame at PACE_CEILING_CHARS_PER_FRAME during
// live streaming. Finalize keeps its accelerated floor (network ended;
// the user wants the remainder on screen).

test('pace governor defines a live-stream ceiling constant', () => {
    assert.match(
        source,
        /const\s+PACE_CEILING_CHARS_PER_FRAME\s*=\s*\d+/,
        'PACE_CEILING_CHARS_PER_FRAME must be defined (removed → stream feels chunky under burst)'
    );
});

test('pace governor caps live drain via Math.min against the ceiling', () => {
    assert.match(
        source,
        /Math\.min\([^)]*PACE_CEILING_CHARS_PER_FRAME/,
        'ceiling must be applied in _drainPaced via Math.min'
    );
});

test('pace ceiling applies only during live streaming, not finalize', () => {
    // Look for the guard that excludes finalize from the cap.
    assert.match(
        source,
        /if\s*\(\s*!this\._finalizing\s*\)\s*\{\s*[\s\S]*?PACE_CEILING_CHARS_PER_FRAME/,
        'ceiling must be skipped when _finalizing (network ended → drain fast)'
    );
});

test('ceiling value is sane (3–30 chars/frame = 180–1800 cps)', () => {
    const m = source.match(/PACE_CEILING_CHARS_PER_FRAME\s*=\s*(\d+)/);
    assert.ok(m, 'PACE_CEILING_CHARS_PER_FRAME must be defined');
    const value = parseInt(m[1], 10);
    assert.ok(
        value >= 3 && value <= 30,
        `PACE_CEILING_CHARS_PER_FRAME=${value} is out of range. ` +
        `<3 starves the stream; >30 re-introduces chunky bursts.`
    );
});
