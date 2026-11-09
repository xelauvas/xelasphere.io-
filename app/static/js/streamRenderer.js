/**
 * StreamRenderer — streaming markdown for assistant messages.
 *
 * The whole message is parsed with one CommonMark + GFM parser on every paint
 * (see stream/markdownEngine.js) and split into top-level blocks. A block whose
 * source has not changed keeps its DOM untouched; only the block that is still
 * growing is diffed in place with morphdom. Live streaming and history replay go
 * through the same code, so a message looks the same while it is written and
 * after a reload.
 *
 * Public contract (unchanged):
 *   new StreamRenderer(container, { replay, streamMeta })
 *     .append(text)  .finalize(onComplete)  .destroy()
 */

import {
    parseBlocks, prepareStreamingText, prepareFinalText, joinSplitTableRows,
    onAssetsChanged, engineReady,
} from './stream/markdownEngine.js?v=6';

// Fill-rate governor: decouples paint rate from network arrival so bursty
// deliveries read as steady writing instead of a wall of text.
//   charsThisFrame = max(floor, ceil(queueLen / catchupFrames))
const PACE_FLOOR_CHARS_PER_FRAME = 3;     // ~180 cps
const PACE_CATCHUP_FRAMES = 30;           // clear a backlog in ~500 ms
const PACE_FINALIZE_CHARS_PER_FRAME = 40; // after the network is done

const CURSOR_CLASS = 'stream-cursor';
const CURSOR_TAG = 'xela-cursor';
// Elements the cursor may sit inside, so it ends up right after the last word.
const CURSOR_HOSTS = new Set(['P', 'UL', 'OL', 'LI', 'BLOCKQUOTE', 'H1', 'H2', 'H3', 'H4', 'H5', 'H6']);

const CHECK_SVG = '<svg class="copy-icon" viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="20 6 9 17 4 12"/></svg>';

function sanitize(html) {
    return window.DOMPurify ? window.DOMPurify.sanitize(html) : html;
}

// Images in an answer open in the shared lightbox.
function wireImages(el) {
    const open = window.__xelasphereOpenLightbox;
    if (typeof open !== 'function') return;
    const imgs = Array.from(el.querySelectorAll('img:not(.emoji):not(.attachment-thumb):not(.chat-image)'));
    imgs.forEach((img, idx) => {
        if (img.dataset.lightboxWired === '1') return;
        img.dataset.lightboxWired = '1';
        img.classList.add('prose-image');
        if (!img.getAttribute('alt')) img.setAttribute('alt', 'Image');
        img.addEventListener('click', (e) => {
            e.preventDefault();
            e.stopPropagation();
            open(imgs.map((i) => ({ src: i.currentSrc || i.src, name: i.getAttribute('alt') || '', type: 'image', isImage: true })), idx);
        });
    });
}

let mermaidReady = false;
let mermaidSeq = 0;
async function renderMermaid(el) {
    if (!window.mermaid || el.dataset.mermaidDone === '1') return;
    el.dataset.mermaidDone = '1';
    const src = el.dataset.mermaidSrc || '';
    try {
        if (!mermaidReady) {
            const dark = document.documentElement.getAttribute('data-theme') !== 'light';
            window.mermaid.initialize({ startOnLoad: false, theme: dark ? 'dark' : 'neutral' });
            mermaidReady = true;
        }
        await window.mermaid.parse(src);
        if (!el.isConnected) return;
        const { svg } = await window.mermaid.render(`xmd-mermaid-${++mermaidSeq}`, src);
        if (el.isConnected) el.innerHTML = svg;
    } catch (_) { /* keep the source visible */ }
}

// One click handler per message for every copy button in it.
function wireCopy(messageEl) {
    if (messageEl.dataset.copyWired === '1') return;
    messageEl.dataset.copyWired = '1';
    messageEl.addEventListener('click', (e) => {
        const btn = e.target.closest && e.target.closest('.copy-button');
        if (!btn || !messageEl.contains(btn)) return;
        const code = btn.closest('.code-window')?.querySelector('pre code');
        if (!code) return;
        const done = () => {
            const label = btn.querySelector('.copy-label');
            const icon = btn.querySelector('.copy-icon');
            const original = icon ? icon.outerHTML : '';
            if (icon) icon.outerHTML = CHECK_SVG;
            if (label) label.textContent = 'Copied';
            btn.classList.add('copied');
            setTimeout(() => {
                if (!btn.isConnected) return;
                const cur = btn.querySelector('.copy-icon');
                if (cur && original) cur.outerHTML = original;
                if (label) label.textContent = 'Copy code';
                btn.classList.remove('copied');
            }, 1800);
        };
        const text = code.textContent || '';
        if (navigator.clipboard?.writeText) navigator.clipboard.writeText(text).then(done, done);
        else done();
    });
}

class StreamRenderer {
    constructor(container, { replay = false, streamMeta = {} } = {}) {
        this.container = container;
        this.replay = replay;
        this.rawBuffer = '';      // everything received
        this._text = '';          // everything released to the painter
        this._paintQueue = '';
        this._blocks = [];        // [{ key, type, el, usedFallback }]
        this.destroyed = false;
        this.finalized = false;
        this._finalizing = false;
        this._done = false;
        this._rafScheduled = false;
        this._lastRenderMs = 0;
        this._lastRenderAt = 0;
        this._onCompleteCallback = null;
        void streamMeta; // per-message style variants are gone: one typography for every answer

        // Adopt the bubble the caller pre-created at send time (so the cursor is
        // visible while waiting for the first token), or build one.
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
        inner.classList.add('xmd');
        if (replay) inner.classList.add('xmd--static');
        wireCopy(inner);

        // The cursor is a custom element on purpose: morphdom pairs nodes by tag
        // name, and a <span> cursor gets paired with (and rewritten into) the next
        // <span> of real content. No markdown output ever contains this tag.
        this._cursor = null;
        const seeded = inner.querySelector(`.${CURSOR_CLASS}`);
        if (!replay) {
            this._cursor = document.createElement(CURSOR_TAG);
            this._cursor.className = CURSOR_CLASS;
            this._cursor.setAttribute('aria-hidden', 'true');
            if (seeded) seeded.replaceWith(this._cursor); else inner.appendChild(this._cursor);
        } else if (seeded) {
            seeded.remove();
        }

        // KaTeX or a syntax grammar arrived after this message was painted.
        this._offAssets = onAssetsChanged(() => {
            if (this.destroyed || !this.messageEl.isConnected) { this._offAssets?.(); return; }
            if (this._blocks.some((b) => b.usedFallback)) this._render(this._done, { refreshFallbacks: true });
        });
    }

    append(text) {
        if (this.destroyed || this.finalized || !text) return;
        this.rawBuffer += text;
        if (this.replay) {
            this._text += text;
            this._schedule();
            return;
        }
        this._paintQueue += text;
        this._schedule();
    }

    finalize(onComplete) {
        if (this.destroyed || this.finalized) {
            if (typeof onComplete === 'function') onComplete();
            return;
        }
        this.finalized = true;
        this._finalizing = true;
        this._onCompleteCallback = typeof onComplete === 'function' ? onComplete : null;
        if (this._paintQueue) { this._schedule(); return; } // drain at the faster rate, then finish
        this._complete();
    }

    destroy() {
        this.destroyed = true;
        this._offAssets?.();
        this._removeCursor();
    }

    // ------------------------------------------------------------- internals
    _schedule() {
        if (this._rafScheduled) return;
        this._rafScheduled = true;
        requestAnimationFrame(() => {
            this._rafScheduled = false;
            if (this.destroyed || this._done) return;
            this._tick();
        });
    }

    _tick() {
        if (this._paintQueue) {
            const floor = this._finalizing ? PACE_FINALIZE_CHARS_PER_FRAME : PACE_FLOOR_CHARS_PER_FRAME;
            const n = Math.max(floor, Math.ceil(this._paintQueue.length / PACE_CATCHUP_FRAMES));
            this._text += this._paintQueue.slice(0, n);
            this._paintQueue = this._paintQueue.slice(n);
        }
        if (this._finalizing && !this._paintQueue) { this._complete(); return; }

        // Long answers cost more per paint; never spend more than about a third
        // of the time painting.
        const now = performance.now();
        const paint = now - this._lastRenderAt >= this._lastRenderMs * 3;
        if (paint) this._render(false);
        // a skipped paint still owes the reader the latest text
        if (this._paintQueue || this._finalizing || !paint) this._schedule();
    }

    _complete() {
        if (this.destroyed || this._done) return;
        this._done = true;
        this._text = this.rawBuffer;
        this._paintQueue = '';
        this._render(true);
        this._removeCursor();
        this.messageEl.classList.remove('xmd--streaming');
        if (this._onCompleteCallback) {
            const cb = this._onCompleteCallback;
            this._onCompleteCallback = null;
            try { cb(); } catch (_) { /* caller errors must not break finalize */ }
        }
    }

    _removeCursor() {
        this.messageEl.querySelectorAll(`.${CURSOR_CLASS}`).forEach((el) => el.remove());
        this._cursor = null;
    }

    _render(final, { refreshFallbacks = false } = {}) {
        const started = performance.now();
        if (!engineReady()) {
            // markdown-it has not loaded yet (replay can race page init): show plain
            // text now and paint properly as soon as it is there.
            if (!this._plain) {
                this._plain = document.createElement('div');
                this._plain.className = 'stream-block block-paragraph xmd-plain';
                this.messageEl.insertBefore(this._plain, this._cursor && this._cursor.parentNode === this.messageEl ? this._cursor : null);
            }
            this._plain.textContent = this._text;
            setTimeout(() => { if (!this.destroyed) this._render(this._done); }, 120);
            return;
        }
        if (this._plain) { this._plain.remove(); this._plain = null; }

        const streaming = !final && !this.replay;
        const source = final ? prepareFinalText(this._text) : (streaming ? prepareStreamingText(this._text) : joinSplitTableRows(this._text));
        const blocks = parseBlocks(source, { streaming }) || [];
        if (streaming) this.messageEl.classList.add('xmd--streaming');

        const cursor = this._cursor;
        for (let i = 0; i < blocks.length; i++) {
            const next = blocks[i];
            const cur = this._blocks[i];
            if (cur && cur.key === next.key && !(refreshFallbacks && cur.usedFallback)) continue;

            const { html, usedFallback } = next.render();
            const safe = sanitize(html);
            if (cur && cur.type === next.type) {
                this._morph(cur.el, safe);
                cur.key = next.key;
                cur.usedFallback = usedFallback;
                this._afterPaint(cur.el, final || i < blocks.length - 1);
            } else {
                const el = document.createElement('div');
                el.className = `stream-block block-${next.type}`;
                el.innerHTML = safe;
                if (cur) {
                    if (cursor && cur.el.contains(cursor)) this.messageEl.appendChild(cursor);
                    cur.el.replaceWith(el);
                } else {
                    const anchor = cursor && cursor.parentNode === this.messageEl ? cursor : null;
                    this.messageEl.insertBefore(el, anchor);
                }
                this._blocks[i] = { key: next.key, type: next.type, el, usedFallback };
                this._afterPaint(el, final || i < blocks.length - 1);
            }
            // A block that is no longer the last one is complete.
            if (i > 0 && this._blocks[i - 1]) this._afterPaint(this._blocks[i - 1].el, true);
        }
        while (this._blocks.length > blocks.length) {
            const gone = this._blocks.pop();
            if (cursor && gone.el.contains(cursor)) this.messageEl.appendChild(cursor);
            gone.el.remove();
        }
        if (!final) this._placeCursor();

        this._lastRenderMs = performance.now() - started;
        this._lastRenderAt = performance.now();
    }

    _morph(el, html) {
        if (!window.morphdom) { el.innerHTML = html; return; }
        const next = document.createElement('div');
        next.innerHTML = html;
        const cursor = this._cursor;
        window.morphdom(el, next, {
            childrenOnly: true,
            onBeforeElUpdated(fromEl, toEl) {
                // citation pills and the sources button are added after the fact
                if (fromEl.classList?.contains('citation-pill') || fromEl.classList?.contains('sources-btn')) return false;
                if (fromEl === cursor || fromEl.dataset?.mermaidDone === '1') return false;
                return !fromEl.isEqualNode(toEl);
            },
            onBeforeNodeDiscarded(node) {
                if (node === cursor) return false;
                if (node.classList?.contains('citation-pill') || node.classList?.contains('sources-btn')) return false;
                return true;
            },
        });
    }

    _afterPaint(el, complete) {
        wireImages(el);
        if (complete) el.querySelectorAll('.xmd-mermaid').forEach((m) => { renderMermaid(m); });
    }

    // Keep the cursor right after the last character that was written.
    _placeCursor() {
        const cursor = this._cursor;
        if (!cursor) return;
        const last = this._blocks[this._blocks.length - 1];
        let host = this.messageEl;
        if (last) {
            host = last.el;
            for (;;) {
                let child = host.lastElementChild;
                if (child === cursor) child = child.previousElementSibling;
                if (!child) break;
                if (child.classList.contains('code-window')) {
                    const code = child.querySelector('pre code');
                    if (code) host = code;
                    break;
                }
                if (!CURSOR_HOSTS.has(child.tagName)) break;
                host = child;
            }
        }
        if (cursor.parentNode !== host || host.lastChild !== cursor) host.appendChild(cursor);
    }
}

window.StreamRenderer = StreamRenderer;
if (typeof window._streamRendererReadyResolve === 'function') {
    window._streamRendererReadyResolve();
    delete window._streamRendererReadyResolve;
}
export { StreamRenderer };
