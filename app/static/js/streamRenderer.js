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
import { transferCursor, removeCursor, createCursorElement } from './stream/cursor.js';

const PREWARM_THRESHOLD = 100; // chars before we fire layout inference

// Backpressure safety valve (fix H). If the SSE source bursts faster
// than RAF can drain, rawBuffer grows unbounded. At this many pending
// chars we force a synchronous drain inline — accepting a paint hitch
// rather than letting pending text balloon into a multi-frame stall
// when the RAF finally runs. Applies to replay mode only; live mode
// uses the paced drain below.
const MAX_PENDING_CHARS = 4096;

// Fill-rate governor — decouples paint rate from network arrival so
// bursty SSE deliveries (ACTION-header flush, cached responses, fast
// providers like Groq) render as smooth streaming, not as a wall of
// text appearing at once. Formula:
//   charsThisFrame = max(floor, ceil(queueLen / catchupFrames))
// At 60fps this gives a smooth natural read pace when the queue is
// short (PACE_FLOOR) and smooth acceleration up to "drain everything
// within CATCHUP_FRAMES frames" when the queue builds up.
const PACE_FLOOR_CHARS_PER_FRAME = 3;     // ~180 cps — natural read speed
const PACE_CATCHUP_FRAMES = 30;           // clear backlog in ~500ms
const PACE_FINALIZE_CHARS_PER_FRAME = 40; // accelerate after network ends

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

// Block types whose renderers paint block.liveText (= block.text + SM's pending
// partial line) instead of just block.text. Renderers that buffer-then-finalize
// (math, mermaid) ignore liveText anyway. List items stay line-batched —
// overlaying mid-item would leak the next item's "- " prefix into the previous
// item's markdown.
const _OVERLAY_TYPES = new Set(['paragraph', 'blockquote', 'code']);
function _supportsLiveOverlay(blockType) {
    return _OVERLAY_TYPES.has(blockType);
}

class StreamRenderer {
    constructor(container, { replay = false } = {}) {
        this.container = container;
        this.replay = replay;
        this.rawBuffer = '';
        this.prewarmDone = false;
        this.destroyed = false;
        this.finalized = false;

        // Message wrapper that callers expect. Adopt an existing
        // text-message-container > text-message if the caller pre-created
        // one (e.g. an empty bubble allocated at prompt-send so the cursor
        // is visible during TTFB) — that keeps the cursor in the same DOM
        // node from t=0 to stream-end, no element swap, no flicker.
        let wrapper = container.querySelector(':scope > .text-message-container');
        let inner = wrapper && wrapper.querySelector(':scope > .text-message');
        if (!wrapper || !inner) {
            wrapper = document.createElement('div');
            wrapper.className = 'text-message-container';
            inner = document.createElement('div');
            inner.className = 'text-message';
            wrapper.appendChild(inner);
            this.container.appendChild(wrapper);
        }
        this.messageEl = inner;
        this.wrapperEl = wrapper;

        // Seed a cursor immediately so there's never a visible gap between
        // "thinking indicator hid" and "first block gets its cursor". The
        // block state machine only emits BLOCK_START on the first \n, which
        // can be several governor frames after content chunks start arriving
        // — leaving the bubble blank until then. The cursor telegraphs
        // "yes, I'm typing" from t=0. transferCursor() finds this node on
        // the first BLOCK_START and moves it to the block atomically.
        // Skip seeding if the caller pre-placed one (adoption case above).
        if (!replay && !inner.querySelector(':scope > .stream-cursor')) {
            inner.appendChild(createCursorElement());
        }

        this.ast = createBlockAST();
        this.sm = createBlockStateMachine(event => this._handleEvent(event));
        this.rafScheduled = false;
        // Fix A+B: chunks buffer here and drain in one RAF pass, so
        // multiple SSE tokens arriving between paint frames coalesce
        // into a single SM-drain + renderer.update. Synchronous SM
        // processing per chunk was burning frame budget even when the
        // user sees no visual change per chunk.
        this._pending = '';
        // Paced drain (live mode only) — chars arrive here from append()
        // and release to _pending at a governed rate so bursty network
        // deliveries look like smooth streaming.
        this._paintQueue = '';
        this._pacedRafScheduled = false;
        this._finalizing = false;
        // Phantom <div> rendered into messageEl while the SM has partial chars
        // but no block is open yet (i.e. before the first \n of a paragraph).
        // Without this, the first line of every paragraph is invisible until
        // its trailing \n arrives — then it pops in all at once. Removed when
        // BLOCK_START finally opens the real block.
        this._partialPreviewEl = null;
    }

    append(text) {
        if (this.destroyed || this.finalized) return;
        if (!text) return;
        this.rawBuffer += text;
        if (!this.prewarmDone && this.rawBuffer.length >= PREWARM_THRESHOLD) {
            applyLayout(this.messageEl, this.rawBuffer.slice(0, PREWARM_THRESHOLD));
            this.prewarmDone = true;
        }

        if (this.replay) {
            // Replay mode: render the whole message in one pass, no pacing.
            // History/refresh wants instant DOM, not a re-animated stream.
            this._pending += text;
            if (this._pending.length >= MAX_PENDING_CHARS) {
                this._drainPending();
                return;
            }
            this._scheduleRender();
            return;
        }

        // Live mode: push into paint queue, governor decides release rate.
        this._paintQueue += text;
        this._schedulePacedRender();
    }

    _schedulePacedRender() {
        if (this._pacedRafScheduled) return;
        this._pacedRafScheduled = true;
        requestAnimationFrame(() => {
            this._pacedRafScheduled = false;
            if (this.destroyed) return;
            this._drainPaced();
            // Keep draining until queue empty (or we're torn down).
            if (this._paintQueue.length > 0 && !this.destroyed) {
                this._schedulePacedRender();
            }
        });
    }

    _drainPaced() {
        if (!this._paintQueue) return;
        const qlen = this._paintQueue.length;
        const floor = this._finalizing
            ? PACE_FINALIZE_CHARS_PER_FRAME
            : PACE_FLOOR_CHARS_PER_FRAME;
        const catchup = Math.ceil(qlen / PACE_CATCHUP_FRAMES);
        const charsThisFrame = Math.max(floor, catchup);
        const slice = this._paintQueue.slice(0, charsThisFrame);
        this._paintQueue = this._paintQueue.slice(charsThisFrame);
        this.sm.append(slice);
        const live = this.ast.getLive();
        if (live) {
            this._removePartialPreview();
            this._paintLive(live);
        } else {
            this._paintPartialPreview();
        }
    }

    // Phantom <div> render path: paints the SM's partial line BEFORE any block
    // is open. Without this, the first line of every paragraph is invisible
    // until its trailing \n arrives, because the L2 SM only opens a block on
    // \n. Existing _paintLive overlay handles the case where a block IS open.
    _paintPartialPreview() {
        const partial = (this.sm.getPartialLine && this.sm.getPartialLine()) || '';
        if (!partial) return;
        if (!this._partialPreviewEl) {
            const el = document.createElement('div');
            el.className = 'stream-block block-paragraph';
            el.dataset.partialPreview = '1';
            const cursor = this.messageEl.querySelector(':scope > .stream-cursor');
            if (cursor) {
                this.messageEl.insertBefore(el, cursor);
            } else {
                this.messageEl.appendChild(el);
            }
            this._partialPreviewEl = el;
        }
        const html = (typeof window.marked?.parse === 'function')
            ? window.marked.parse(partial)
            : partial;
        this._partialPreviewEl.innerHTML = window.DOMPurify
            ? window.DOMPurify.sanitize(html)
            : html;
    }

    _removePartialPreview() {
        if (this._partialPreviewEl) {
            this._partialPreviewEl.remove();
            this._partialPreviewEl = null;
        }
    }

    /**
     * Paint the live block including the SM's pending partial line. Without
     * this overlay, prose feels chunky: BLOCK_APPEND only fires on \n, so a
     * 200-char paragraph without internal newlines sits invisible until the
     * trailing \n arrives, then pops in all at once. We extend block.text
     * transiently with the SM's partial — the renderer reads the canonical
     * field unmodified, but for the duration of this synchronous update()
     * call it sees text-plus-partial. block.text is restored before this
     * function returns so finalize() / history replay / the profile
     * classifier all see canonical line-aligned text.
     */
    _paintLive(block) {
        const r = rendererFor(block.type);
        const partial = (this.sm.getPartialLine && this.sm.getPartialLine()) || '';
        if (partial && _supportsLiveOverlay(block.type)) {
            const canonical = block.text;
            try {
                block.text = canonical + partial;
                r.update(block);
            } finally {
                block.text = canonical;
            }
        } else {
            r.update(block);
        }
    }

    finalize(onComplete) {
        if (this.destroyed || this.finalized) {
            if (typeof onComplete === 'function') onComplete();
            return;
        }
        this.finalized = true;
        this._finalizing = true;
        this._onCompleteCallback = typeof onComplete === 'function' ? onComplete : null;
        if (!this.prewarmDone) {
            applyLayout(this.messageEl, this.rawBuffer);
            this.prewarmDone = true;
        }
        // If the paced governor still holds chars, drain them at the
        // finalize-accelerated rate asynchronously. _completeFinalize()
        // runs the real close-out (sm.finalize, cursor removal, context
        // classification) once the queue is empty. Caller can pass a
        // callback to run post-drain (e.g. release the stop button).
        if (this._paintQueue) {
            this._schedulePacedFinalize();
            return;
        }
        this._completeFinalize();
    }

    _schedulePacedFinalize() {
        requestAnimationFrame(() => {
            if (this.destroyed) return;
            this._drainPaced();
            if (this._paintQueue.length > 0) {
                this._schedulePacedFinalize();
            } else {
                this._completeFinalize();
            }
        });
    }

    _completeFinalize() {
        if (this.destroyed) return;
        // Drain anything sync-pending (replay path or if sm was sync-fed).
        if (this._pending) {
            this.sm.append(this._pending);
            this._pending = '';
        }
        // Remove the phantom preview before sm.finalize fires its own
        // BLOCK_START for any trailing partial line — the real block element
        // takes over from here.
        this._removePartialPreview();
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
        // Two sources can set data-ctx: prewarm (inferLayout from first ~100
        // chars) and finalize (this classifier, refinement). Finalize only
        // re-paints when the answer is genuinely different — a matching
        // classification avoids a pointless CSS recompute.
        try {
            const profile = buildMessageProfile(this.ast.blocks());
            const ctx = classifyContext(profile);
            const targets = [
                this.wrapperEl,
                this.wrapperEl && this.wrapperEl.closest('.message, .ai-message'),
            ];
            for (const el of targets) {
                if (!el) continue;
                if (el.dataset.ctx === ctx) continue;
                el.dataset.ctx = ctx;
                el.dataset.ctxSource = 'finalize';
            }
        } catch (_e) { /* non-critical */ }

        // Fire any callback the caller passed to finalize(). This is how
        // codeWindow.js knows "the paced drain has finished, it's safe to
        // release the stop button and null the _activeStreamRenderer
        // reference" — without waiting, the button would flip to send
        // while chars are still visibly filling in.
        if (this._onCompleteCallback) {
            const cb = this._onCompleteCallback;
            this._onCompleteCallback = null;
            try { cb(); } catch (_e) { /* caller errors must not break finalize */ }
        }
    }

    destroy() {
        this.destroyed = true;
        this._removePartialPreview();
        removeCursor(this.messageEl);
    }

    _handleEvent(event) {
        switch (event.type) {
            case 'BLOCK_START': {
                // Real block opening — clear the phantom preview NOW. Without this,
                // single-line blocks (headings, blank-line transitions) that open and
                // close inside one sm.append cycle leave the preview stuck onscreen,
                // because _drainPaced sees live=null again at the end and skips the
                // remove path entirely.
                this._removePartialPreview();
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
                    // Renderer owns its own cursor inside block.el (code
                    // block creates one inside its <pre>). Retire any
                    // cursor from the PREVIOUS block — but don't touch
                    // the one we just created. removeCursor() used to
                    // nuke everything including the fresh one.
                    const allCursors = this.messageEl.querySelectorAll('.stream-cursor');
                    allCursors.forEach((c) => {
                        if (!block.el.contains(c)) c.remove();
                    });
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
        performance.mark('stream-render-start');
        this._paintLive(live);
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
