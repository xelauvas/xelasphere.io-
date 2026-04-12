/**
 * StreamRenderer — block-oriented progressive markdown.
 *
 * SSE tokens → L2 state machine → L3 block AST → per-type renderers → DOM.
 * Frozen blocks never re-render. Cursor transfers on BLOCK_START.
 *
 * Public contract (unchanged from 2026-04-10 spec):
 *   new StreamRenderer(container).append(text).finalize() / .destroy()
 */

import { createBlockStateMachine } from './stream/blockStateMachine.js';
import { createBlockAST } from './stream/blockAST.js';
import { createProseRenderer } from './stream/renderers/prose.js';
import { createCodeRenderer } from './stream/renderers/code.js';
import { createListRenderer } from './stream/renderers/list.js';
import { createMathRenderer } from './stream/renderers/math.js';
import { createMermaidRenderer } from './stream/renderers/mermaid.js';
import { applyLayout } from './stream/preWarm.js';
import { transferCursor, removeCursor } from './stream/cursor.js';

const PREWARM_THRESHOLD = 100; // chars before we fire layout inference

const _renderers = {
    prose: createProseRenderer(),
    code: createCodeRenderer(),
    list: createListRenderer(),
    math: createMathRenderer(),
    mermaid: createMermaidRenderer(),
};

function rendererFor(blockType) {
    switch (blockType) {
        case 'code':       return _renderers.code;
        case 'mermaid':    return _renderers.mermaid;
        case 'math':       return _renderers.math;
        case 'list-ul':
        case 'list-ol':    return _renderers.list;
        case 'paragraph':
        case 'heading':
        case 'blockquote':
        default:           return _renderers.prose;
    }
}

class StreamRenderer {
    constructor(container, { replay = false } = {}) {
        this.container = container;
        this.replay = replay;
        this.rawBuffer = '';
        this.prewarmDone = false;
        this.destroyed = false;
        this.finalized = false;

        // Message wrapper that callers expect
        const wrapper = document.createElement('div');
        wrapper.className = 'text-message-container';
        const inner = document.createElement('div');
        inner.className = 'text-message';
        wrapper.appendChild(inner);
        this.container.appendChild(wrapper);
        this.messageEl = inner;

        this.ast = createBlockAST();
        this.sm = createBlockStateMachine(event => this._handleEvent(event));
        this.rafScheduled = false;
    }

    append(text) {
        if (this.destroyed || this.finalized) return;
        this.rawBuffer += text;
        if (!this.prewarmDone && this.rawBuffer.length >= PREWARM_THRESHOLD) {
            applyLayout(this.messageEl, this.rawBuffer.slice(0, PREWARM_THRESHOLD));
            this.prewarmDone = true;
        }
        this.sm.append(text);
        this._scheduleRender();
    }

    finalize() {
        if (this.destroyed || this.finalized) return;
        this.finalized = true;
        if (!this.prewarmDone) {
            applyLayout(this.messageEl, this.rawBuffer);
            this.prewarmDone = true;
        }
        // sm.finalize() emits BLOCK_END for any still-live block, which runs
        // r.update() + r.finalize() through _handleEvent. Every block is finalized
        // exactly once through that path — do NOT re-iterate the block array here.
        this.sm.finalize();
        removeCursor(this.messageEl);
    }

    destroy() {
        this.destroyed = true;
        removeCursor(this.messageEl);
    }

    _handleEvent(event) {
        switch (event.type) {
            case 'BLOCK_START': {
                const block = this.ast.startBlock({
                    id: event.id,
                    type: event.blockType,
                    meta: event.meta,
                });
                const r = rendererFor(block.type);
                r.create(block, this.messageEl, { replay: this.replay });
                if (!this.replay) {
                    transferCursor(this.messageEl, block.el);
                }
                break;
            }
            case 'BLOCK_APPEND': {
                this.ast.appendToLive(event.text);
                // Render inline so the live block grows token-by-token.
                // RAF batching (via _scheduleRender) was leaving updates stuck
                // at BLOCK_END boundaries; direct update keeps the stream visible.
                if (!this.replay) {
                    const live = this.ast.getLive();
                    if (live) {
                        const r = rendererFor(live.type);
                        r.update(live);
                    }
                }
                break;
            }
            case 'BLOCK_END': {
                const live = this.ast.getLive();
                if (live) {
                    const r = rendererFor(live.type);
                    r.update(live);
                    r.finalize?.(live);
                }
                this.ast.endBlock();
                break;
            }
            // LIST_ITEM_START / LIST_ITEM_END are advisory — the list renderer
            // parses items out of block.text directly.
        }
    }

    _scheduleRender() {
        if (this.rafScheduled) return;
        this.rafScheduled = true;
        requestAnimationFrame(() => {
            this.rafScheduled = false;
            if (this.destroyed) return;
            const live = this.ast.getLive();
            if (!live) return;
            const r = rendererFor(live.type);
            performance.mark('stream-render-start');
            r.update(live);
            performance.mark('stream-render-end');
            performance.measure('stream-render', 'stream-render-start', 'stream-render-end');
        });
    }
}

window.StreamRenderer = StreamRenderer;
export { StreamRenderer };
