import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createBlockStateMachine } from '../../app/static/js/stream/blockStateMachine.js';

test('empty input emits no events', () => {
    const events = [];
    const sm = createBlockStateMachine(e => events.push(e));
    sm.append('');
    sm.finalize();
    assert.deepEqual(events, []);
});

test('simple prose emits one paragraph block', () => {
    const events = [];
    const sm = createBlockStateMachine(e => events.push(e));
    sm.append('Hello world.');
    sm.finalize();
    const types = events.map(e => e.type);
    assert.deepEqual(types, ['BLOCK_START', 'BLOCK_APPEND', 'BLOCK_END']);
    assert.equal(events[0].blockType, 'paragraph');
});
