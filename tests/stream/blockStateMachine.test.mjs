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

test('heading detected at line-start', () => {
    const events = [];
    const sm = createBlockStateMachine(e => events.push(e));
    sm.append('## Title\n');
    sm.finalize();
    const starts = events.filter(e => e.type === 'BLOCK_START');
    assert.equal(starts.length, 1);
    assert.equal(starts[0].blockType, 'heading');
    assert.equal(starts[0].meta.level, 2);
});

test('blockquote detected at line-start', () => {
    const events = [];
    const sm = createBlockStateMachine(e => events.push(e));
    sm.append('> quoted line\n');
    sm.finalize();
    const starts = events.filter(e => e.type === 'BLOCK_START');
    assert.equal(starts[0].blockType, 'blockquote');
});

test('blank line terminates paragraph', () => {
    const events = [];
    const sm = createBlockStateMachine(e => events.push(e));
    sm.append('First para.\n\nSecond para.\n');
    sm.finalize();
    const starts = events.filter(e => e.type === 'BLOCK_START');
    assert.equal(starts.length, 2);
});

test('# symbol not at line-start stays in paragraph', () => {
    const events = [];
    const sm = createBlockStateMachine(e => events.push(e));
    sm.append('See issue #42 please.\n');
    sm.finalize();
    const starts = events.filter(e => e.type === 'BLOCK_START');
    assert.equal(starts.length, 1);
    assert.equal(starts[0].blockType, 'paragraph');
});
