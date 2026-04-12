import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readdirSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createBlockStateMachine } from '../../app/static/js/stream/blockStateMachine.js';

const corpusDir = fileURLToPath(new URL('./corpus/', import.meta.url));

function runFixture(md) {
    const blocks = [];
    let current = null;
    const sm = createBlockStateMachine(event => {
        if (event.type === 'BLOCK_START') {
            current = { type: event.blockType, text: '', ...(Object.keys(event.meta).length ? { meta: event.meta } : {}) };
            blocks.push(current);
        } else if (event.type === 'BLOCK_APPEND') {
            current.text += event.text;
        }
    });
    sm.append(md);
    sm.finalize();
    return blocks;
}

const fixtures = readdirSync(corpusDir)
    .filter(f => f.endsWith('.md'))
    .sort();

for (const fixture of fixtures) {
    test(`corpus: ${fixture}`, () => {
        const md = readFileSync(join(corpusDir, fixture), 'utf8');
        const expectedPath = join(corpusDir, fixture.replace(/\.md$/, '.expected.json'));
        const expected = JSON.parse(readFileSync(expectedPath, 'utf8'));
        const actual = runFixture(md);
        assert.deepEqual(
            actual.map(b => ({ type: b.type, text: b.text, ...(b.meta ? { meta: b.meta } : {}) })),
            expected,
        );
    });
}
