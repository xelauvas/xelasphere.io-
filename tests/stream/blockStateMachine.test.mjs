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

test('code fence opens with language tag', () => {
    const events = [];
    const sm = createBlockStateMachine(e => events.push(e));
    sm.append('```python\nprint("hi")\n```\n');
    sm.finalize();
    const starts = events.filter(e => e.type === 'BLOCK_START');
    assert.equal(starts[0].blockType, 'code');
    assert.equal(starts[0].meta.lang, 'python');
});

test('code fence content is emitted as append', () => {
    const events = [];
    const sm = createBlockStateMachine(e => events.push(e));
    sm.append('```\nline1\nline2\n```\n');
    sm.finalize();
    const appends = events.filter(e => e.type === 'BLOCK_APPEND').map(e => e.text);
    assert.equal(appends.join(''), 'line1\nline2\n');
});

test('closing fence emits BLOCK_END natural', () => {
    const events = [];
    const sm = createBlockStateMachine(e => events.push(e));
    sm.append('```\nbody\n```\nafter\n');
    sm.finalize();
    const ends = events.filter(e => e.type === 'BLOCK_END');
    assert.equal(ends[0].reason, 'natural');
    const starts = events.filter(e => e.type === 'BLOCK_START');
    assert.equal(starts.length, 2);
    assert.equal(starts[1].blockType, 'paragraph');
});

test('mermaid language routes to mermaid block type', () => {
    const events = [];
    const sm = createBlockStateMachine(e => events.push(e));
    sm.append('```mermaid\ngraph TD; A-->B;\n```\n');
    sm.finalize();
    const starts = events.filter(e => e.type === 'BLOCK_START');
    assert.equal(starts[0].blockType, 'mermaid');
});

test('unclosed code fence at finalize emits BLOCK_END finalize', () => {
    const events = [];
    const sm = createBlockStateMachine(e => events.push(e));
    sm.append('```python\ndef foo():\n    pass\n');
    sm.finalize();
    const ends = events.filter(e => e.type === 'BLOCK_END');
    assert.equal(ends.at(-1).reason, 'finalize');
});

test('objective-c language tag with dash is preserved', () => {
    const events = [];
    const sm = createBlockStateMachine(e => events.push(e));
    sm.append('```objective-c\nNSLog(@"hi");\n```\n');
    sm.finalize();
    const starts = events.filter(e => e.type === 'BLOCK_START');
    assert.equal(starts[0].meta.lang, 'objective-c');
});

test('unordered list emits a single list-ul block with item boundaries', () => {
    const events = [];
    const sm = createBlockStateMachine(e => events.push(e));
    sm.append('- first\n- second\n- third\n');
    sm.finalize();
    const starts = events.filter(e => e.type === 'BLOCK_START');
    assert.equal(starts.length, 1);
    assert.equal(starts[0].blockType, 'list-ul');
    const itemStarts = events.filter(e => e.type === 'LIST_ITEM_START');
    assert.equal(itemStarts.length, 3);
});

test('ordered list detects numeric prefix', () => {
    const events = [];
    const sm = createBlockStateMachine(e => events.push(e));
    sm.append('1. first\n2. second\n');
    sm.finalize();
    const starts = events.filter(e => e.type === 'BLOCK_START');
    assert.equal(starts[0].blockType, 'list-ol');
});

test('list item continuation line appends to current item', () => {
    const events = [];
    const sm = createBlockStateMachine(e => events.push(e));
    sm.append('- first item\n  continuation line\n- second\n');
    sm.finalize();
    const itemStarts = events.filter(e => e.type === 'LIST_ITEM_START');
    assert.equal(itemStarts.length, 2);
});

test('blank line terminates list', () => {
    const events = [];
    const sm = createBlockStateMachine(e => events.push(e));
    sm.append('- a\n- b\n\nAfter.\n');
    sm.finalize();
    const starts = events.filter(e => e.type === 'BLOCK_START');
    assert.equal(starts.length, 2);
    assert.equal(starts[0].blockType, 'list-ul');
    assert.equal(starts[1].blockType, 'paragraph');
});
