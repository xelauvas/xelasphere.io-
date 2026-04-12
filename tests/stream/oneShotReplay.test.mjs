import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createBlockStateMachine } from '../../app/static/js/stream/blockStateMachine.js';

// Mixed-markdown fixture: prose + heading + code fence + ordered list + $$ math + mermaid
const FIXTURE = [
    '# Document title',
    '',
    'This is an introduction paragraph explaining the document. It has bold **text** and italic *text*.',
    '',
    '## Section one',
    '',
    'Some prose with inline `code` and a [link](https://example.com).',
    '',
    '```python',
    'def add(a, b):',
    '    # Sum two numbers',
    '    return a + b',
    '```',
    '',
    '## Section two — a list',
    '',
    '1. First item with some detail.',
    '2. Second item',
    '   continuing on a new line.',
    '3. Third item',
    '',
    '## A formula',
    '',
    '$$',
    'E = mc^2',
    '$$',
    '',
    '## Diagram',
    '',
    '```mermaid',
    'graph TD; A-->B; B-->C;',
    '```',
    '',
    'Final closing paragraph.',
].join('\n') + '\n';

function runStateMachine(input, chunkSize) {
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

    if (chunkSize <= 0) {
        sm.append(input);
    } else {
        for (let i = 0; i < input.length; i += chunkSize) {
            sm.append(input.slice(i, i + chunkSize));
        }
    }
    sm.finalize();

    return blocks.map(b => ({ type: b.type, text: b.text, ...(b.meta ? { meta: b.meta } : {}) }));
}

test('one-shot append produces same block AST as chunked append', () => {
    const oneShot = runStateMachine(FIXTURE, 0);
    const chunked50 = runStateMachine(FIXTURE, 50);
    const chunked5 = runStateMachine(FIXTURE, 5);
    assert.deepEqual(oneShot, chunked50, '1-shot vs 50-char chunks must match');
    assert.deepEqual(oneShot, chunked5, '1-shot vs 5-char chunks must match');
});

test('one-shot large append produces exactly 12 blocks with expected types', () => {
    const blocks = runStateMachine(FIXTURE, 0);
    const types = blocks.map(b => b.type);
    assert.deepEqual(types, [
        'heading',
        'paragraph',
        'heading',
        'paragraph',
        'code',
        'heading',
        'list-ol',
        'heading',
        'math',
        'heading',
        'mermaid',
        'paragraph',
    ]);
});
