import { test } from 'node:test';
import assert from 'node:assert/strict';
import { inferLayout } from '../../app/static/js/stream/preWarm.js';

test('code fence in first chars → code-heavy', () => {
    assert.equal(inferLayout('Here is some code:\n```python\n'), 'code-heavy');
});

test('heading at line-start → structured', () => {
    assert.equal(inferLayout('## Overview\n\nHello'), 'structured');
});

test('ordered list at line-start → enumerated', () => {
    assert.equal(inferLayout('1. First\n2. Second'), 'enumerated');
});

test('plain prose → prose', () => {
    assert.equal(inferLayout('Hello world, this is just prose.'), 'prose');
});

test('prose containing # mid-sentence → prose (not structured)', () => {
    assert.equal(inferLayout('Please file issue #42 now.'), 'prose');
});
