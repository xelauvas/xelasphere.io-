import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createBlockAST } from '../../app/static/js/stream/blockAST.js';

test('new AST is empty', () => {
    const ast = createBlockAST();
    assert.deepEqual(ast.blocks(), []);
});

test('adding a block appends to the array and marks it live', () => {
    const ast = createBlockAST();
    ast.startBlock({ id: 'b1', type: 'paragraph', meta: {} });
    const blocks = ast.blocks();
    assert.equal(blocks.length, 1);
    assert.equal(blocks[0].frozen, false);
});

test('appendToLive mutates only the live block text', () => {
    const ast = createBlockAST();
    ast.startBlock({ id: 'b1', type: 'paragraph', meta: {} });
    ast.appendToLive('Hello ');
    ast.appendToLive('world.');
    assert.equal(ast.blocks()[0].text, 'Hello world.');
});

test('endBlock freezes the live block; subsequent appendToLive throws; getLive returns null', () => {
    const ast = createBlockAST();
    ast.startBlock({ id: 'b1', type: 'paragraph', meta: {} });
    ast.appendToLive('Done.');
    ast.endBlock();
    assert.equal(ast.blocks()[0].frozen, true);
    assert.equal(ast.getLive(), null);
    assert.throws(() => ast.appendToLive('more'), /no live block/i);
});

test('starting a new block after end leaves prior frozen and creates new live', () => {
    const ast = createBlockAST();
    ast.startBlock({ id: 'b1', type: 'paragraph', meta: {} });
    ast.appendToLive('a');
    ast.endBlock();
    ast.startBlock({ id: 'b2', type: 'heading', meta: { level: 1 } });
    ast.appendToLive('# Title');
    const blocks = ast.blocks();
    assert.equal(blocks.length, 2);
    assert.equal(blocks[0].frozen, true);
    assert.equal(blocks[1].frozen, false);
});
