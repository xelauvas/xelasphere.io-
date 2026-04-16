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

// Backpressure safety valve (fix H). If the SSE source bursts faster
// than RAF can drain, rawBuffer grows unbounded. At this many pending
// chars we force a synchronous drain inline — accepting a paint hitch
// rather than letting pending text balloon into a multi-frame stall
// when the RAF finally runs.
const MAX_PENDING_CHARS = 4096;

/**
 * Content-profile classification — walks the finalized block list and
 * decides a context label ('code-heavy', 'list-heavy', 'conversational',
 * etc.) so CSS can adapt the message container's layout to the content
 * that actually landed. Deterministic; no model calls. Costs ~1ms.
 */
function buildMessageProfile(blocks) {
    const profile = {
        totalBlocks: blocks.length,
        codeBlocks: 0,
        listBlocks: 0,
        headingBlocks: 0,
        proseBlocks: 0,
        mathBlocks: 0,
        mermaidBlocks: 0,
        totalListItems: 0,
        totalProseChars: 0,
    };
    for (const block of blocks) {
        switch (block.type) {
            case 'code':
                profile.codeBlocks++;
                break;
            case 'list-ul':
            case 'list-ol':
                profile.listBlocks++;
                // Prefer the rendered DOM child count (authoritative);
                // fall back to counting markdown item lines if DOM missing.
                if (block.el && block.el.children) {
                    profile.totalListItems += block.el.children.length;
                } else if (block.text) {
                    const lines = block.text.split('\n');
                    for (const line of lines) {
                        if (/^\s*(?:[-*+]|\d+\.)\s+/.test(line)) profile.totalListItems++;
                    }
                }
                break;
            case 'heading':
                profile.headingBlocks++;
                break;
            case 'math':
                profile.mathBlocks++;
                break;
            case 'mermaid':
                profile.mermaidBlocks++;
                break;
            case 'blockquote':
            case 'paragraph':
            default:
                profile.proseBlocks++;
                profile.totalProseChars += (block.text && block.text.length) || 0;
                break;
        }
    }
    return profile;
}

function classifyContext(profile) {
    const {
        totalBlocks, codeBlocks, listBlocks, headingBlocks,
        proseBlocks, totalListItems, totalProseChars,
    } = profile;

    if (totalBlocks === 0) return 'conversational';
    // Code-dominant: at least one code block and it's ≥ prose blocks.
    if (codeBlocks > 0 && codeBlocks >= proseBlocks) return 'code-heavy';
    // Any code mixed with prose = technical.
    if (codeBlocks > 0) return 'technical';
    // Substantial list content.
    if (listBlocks > 0 && totalListItems >= 6) return 'list-heavy';
    // Multiple headings → document-like.
    if (headingBlocks >= 2) return 'structured';
    // Multi-paragraph explanation.
    if (proseBlocks >= 2 && totalProseChars > 800) return 'long-prose';
    // Single short block → conversational.
    if (totalBlocks === 1 && totalProseChars < 200) return 'conversational';
    return 'prose';
}

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
        this.wrapperEl = wrapper;

        this.ast = createBlockAST();
        this.sm = createBlockStateMachine(event => this._handleEvent(event));
        this.rafScheduled = false;
        // Fix A+B: chunks buffer here and drain in one RAF pass, so
        // multiple SSE tokens arriving between paint frames coalesce
        // into a single SM-drain + renderer.update. Synchronous SM
        // processing per chunk was burning frame budget even when the
        // user sees no visual change per chunk.
        this._pending = '';
    }

    append(text) {
        if (this.destroyed || this.finalized) return;
        if (!text) return;
        this.rawBuffer += text;
        this._pending += text;
        if (!this.prewarmDone && this.rawBuffer.length >= PREWARM_THRESHOLD) {
            applyLayout(this.messageEl, this.rawBuffer.slice(0, PREWARM_THRESHOLD));
            this.prewarmDone = true;
        }
        // Fix H — backpressure safety valve. If pending grows past the
        // threshold (pathological burst from upstream), drain now so the
        // main thread doesn't stall on a huge SM+render pass when RAF
        // eventually fires.
        if (this._pending.length >= MAX_PENDING_CHARS) {
            this._drainPending();
            return;
        }
        this._scheduleRender();
    }

    finalize() {
        if (this.destroyed || this.finalized) return;
        this.finalized = true;
        if (!this.prewarmDone) {
            applyLayout(this.messageEl, this.rawBuffer);
            this.prewarmDone = true;
        }
        // Drain anything that didn't hit RAF before finalize arrived, so
        // the last few tokens make it to the AST before sm.finalize()
        // emits BLOCK_END on them.
        if (this._pending) {
            this.sm.append(this._pending);
            this._pending = '';
        }
        // sm.finalize() emits BLOCK_END for any still-live block, which runs
        // r.update() + r.finalize() through _handleEvent. Every block is finalized
        // exactly once through that path — do NOT re-iterate the block array here.
        this.sm.finalize();
        removeCursor(this.messageEl);

        // Content profile → data-ctx. CSS contexts in stream-blocks.css
        // pick up the attribute and adapt max-width / line-height / block
        // rhythm. We bubble the attribute up to the outermost .message
        // element so it can override width caps on BOTH .message-content
        // and .text-message-container.
        //
        // Authority order (highest wins, all three can set data-ctx):
        //   1. server  — scaffold SSE event (v2). Pre-stream, authoritative.
        //   2. prewarm — inferLayout() from first ~100 chars. Kills jumps.
        //   3. finalize — this classifier. Only replaces prewarm, never server.
        //
        // Refinement at finalize only re-paints when the answer is genuinely
        // different from what's on the element — a matching classification
        // leaves data-ctx untouched and avoids a pointless CSS recompute.
        try {
            const profile = buildMessageProfile(this.ast.blocks());
            const ctx = classifyContext(profile);
            const targets = [
                this.wrapperEl,
                this.wrapperEl && this.wrapperEl.closest('.message, .ai-message'),
            ];
            for (const el of targets) {
                if (!el) continue;
                if (el.dataset.ctxSource === 'server') continue;  // #2
                if (el.dataset.ctx === ctx) continue;              // #1 — no jump if stable
                el.dataset.ctx = ctx;
                el.dataset.ctxSource = 'finalize';
            }
        } catch (_e) { /* non-critical */ }
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
                // Cursor handoff is atomic — transferCursor() removes the
                // old cursor and appends a fresh one to the new block in
                // one DOM mutation. Doing an explicit removeCursor() first
                // (the old code path) left a 1-frame gap where the cursor
                // blinked out before reappearing. If the renderer claims
                // ownsCursor the renderer will manage its own cursor.
                if (!this.replay && !block.meta?.ownsCursor) {
                    transferCursor(this.messageEl, block.el);
                } else if (!this.replay && block.meta?.ownsCursor) {
                    // Renderer owns it: still retire the previous block's
                    // cursor so we don't leave two on screen.
                    removeCursor(this.messageEl);
                }
                break;
            }
            case 'BLOCK_APPEND': {
                // AST text updates inline (cheap, ~1 pointer op). The
                // renderer.update call used to run here AND again on RAF —
                // double work per chunk. Now RAF is the single source of
                // truth for mid-block rendering (fix A+B).
                this.ast.appendToLive(event.text);
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
            this._drainPending();
        });
    }

    /**
     * Single drain-and-render path. Used by both the RAF callback and
     * the backpressure safety valve in append(). Processes all buffered
     * text through the SM (emitting BLOCK_START/APPEND/END events which
     * mutate the AST), then runs one renderer.update on the live block.
     */
    _drainPending() {
        if (this.destroyed || this.finalized) return;
        if (this._pending) {
            const text = this._pending;
            this._pending = '';
            this.sm.append(text);  // fires events, mutates AST
        }
        const live = this.ast.getLive();
        if (!live) return;
        const r = rendererFor(live.type);
        performance.mark('stream-render-start');
        r.update(live);
        performance.mark('stream-render-end');
        performance.measure('stream-render', 'stream-render-start', 'stream-render-end');
    }
}

window.StreamRenderer = StreamRenderer;
if (typeof window._streamRendererReadyResolve === 'function') {
    window._streamRendererReadyResolve();
    delete window._streamRendererReadyResolve;
}
export { StreamRenderer };
