/**
 * Prose / heading / blockquote renderer.
 * Input: block object with .text (raw markdown) and .el (owned DOM node).
 * Pipeline: marked.parse → DOMPurify.sanitize → morphdom diff (childrenOnly).
 */

// Trailing AI-assistant preamble pattern. Strips the classic closer that
// Llama-family instruction tuning bakes in ("How can I help you today?"
// and variants) when it appears at the tail of a finalized prose block.
// Applied only on finalize() so mid-stream rendering isn't disturbed.
// The server prompt asks the model not to emit these; this is the belt
// for the models that emit them anyway.
const AI_PREAMBLE_TAIL_RE = /\s+(?:how\s+(?:can|may|might)\s+(?:i|we)\s+(?:help|assist)\s+you(?:\s+today)?\s*\??|what\s+can\s+i\s+(?:help\s+you\s+with|do\s+for\s+you)(?:\s+today)?\s*\??|is\s+there\s+(?:anything|something)(?:\s+else)?\s+i\s+can\s+(?:help|assist)(?:\s+you)?\s+with\s*\??|let\s+me\s+know\s+if\s+(?:you|there\s+is)[^.?!]*[.?!]?)\s*$/i;

function stripTrailingPreamble(text) {
    if (!text) return text;
    const stripped = text.replace(AI_PREAMBLE_TAIL_RE, '').trimEnd();
    // Clean up trailing punctuation that was left dangling (e.g. "Hello,"
    // after stripping "how can I assist you today?"). Replace lone trailing
    // comma with a period for cleaner reading.
    return stripped.replace(/,\s*$/, '.');
}

// Verbatim-quote detect-and-wrap. Llama-family models emit long literary
// quotations inline inside paragraphs with straight double quotes rather
// than using `> ` blockquote syntax, even when the system prompt requires
// it. This heuristic finds a substantial quoted span in a paragraph and
// splits it into prose-before / blockquote / prose-after at markdown
// level, so the existing .text-message blockquote CSS kicks in.
//
// Triggers when a paragraph contains a straight-double-quoted span of
// >= 30 characters. If the paragraph has multiple quoted spans (e.g. a
// book title in quotes + a long quotation), the longest ≥30 span is
// promoted; shorter quotes are left inline. Short embedded quotes
// ("insane", "hello") are never promoted.
const MIN_QUOTE_LEN = 35;

function wrapInlineQuotesAsBlockquotes(text) {
    if (!text) return text;
    return text
        .split(/\n\n+/)
        .map((para) => {
            // Skip paragraphs that already start with '>' (already a blockquote)
            // or are code fences (defensive; blockStateMachine handles these).
            if (/^\s*>/.test(para) || /^\s*```/.test(para)) return para;

            // Collect all straight-double-quoted spans with their positions.
            const quotes = [];
            const re = /"([^"\n]+?)"/g;
            let m;
            while ((m = re.exec(para)) !== null) {
                quotes.push({ full: m[0], inner: m[1], index: m.index, len: m[1].length });
            }
            if (quotes.length === 0) return para;

            // Pick the longest span that meets the length threshold.
            const eligible = quotes.filter((q) => q.len >= MIN_QUOTE_LEN);
            if (eligible.length === 0) return para;
            eligible.sort((a, b) => b.len - a.len);
            const pick = eligible[0];

            // Guard against long prose with a medium-length quoted phrase
            // being hijacked — require surrounding prose to be ≤3× the
            // quote length. Typical "intro + quote + attribution" comes in
            // well under this; a 500-word essay mentioning a 35-char term
            // in passing stays as prose.
            const nonQuoteLen = (para.length - pick.full.length);
            if (nonQuoteLen > pick.len * 3) return para;

            const before = para.slice(0, pick.index).trim();
            const after = para.slice(pick.index + pick.full.length).trim();
            const parts = [];
            if (before) parts.push(before);
            parts.push('> ' + pick.inner);
            if (after) parts.push(after);
            return parts.join('\n\n');
        })
        .join('\n\n');
}

// Wires markdown-rendered <img> tags inside a prose block to the shared
// attachment lightbox. Each image becomes clickable with a zoom-in cursor;
// the whole set in the block is passed together so prev/next navigation
// works across sibling images. Emoji and attachment thumbs are skipped.
function wireMarkdownImagesToLightbox(el) {
    if (!el || typeof el.querySelectorAll !== 'function') return;
    const openLightbox = typeof window !== 'undefined' && window.__xelasphereOpenLightbox;
    if (typeof openLightbox !== 'function') return;

    const imgs = Array.from(el.querySelectorAll(
        'img:not(.emoji):not(.attachment-thumb):not(.chat-image):not(.composer-attachment-thumb)'
    ));
    if (!imgs.length) return;

    const items = imgs.map((img) => ({
        src: img.currentSrc || img.src,
        name: img.getAttribute('alt') || '',
        type: 'image',
        isImage: true,
    }));

    imgs.forEach((img, idx) => {
        if (img.dataset.lightboxWired === '1') return;
        img.dataset.lightboxWired = '1';
        img.classList.add('prose-image');
        if (!img.hasAttribute('alt') || img.getAttribute('alt') === '') {
            img.setAttribute('alt', 'Image');
        }
        img.style.cursor = 'zoom-in';

        // Progressive blur-up. Start in the "loading" state; flip to loaded
        // once the browser has decoded the image so the transition runs.
        if (!img.classList.contains('is-loaded')) {
            img.classList.add('is-loading-blur');
            const markLoaded = () => {
                img.classList.remove('is-loading-blur');
                img.classList.add('is-loaded');
            };
            if (img.complete && img.naturalHeight > 0) {
                markLoaded();
            } else if (typeof img.decode === 'function') {
                img.decode().then(markLoaded).catch(markLoaded);
            } else {
                img.addEventListener('load', markLoaded, { once: true });
                img.addEventListener('error', markLoaded, { once: true });
            }
        }

        img.addEventListener('click', (e) => {
            e.preventDefault();
            e.stopPropagation();
            // Rebuild src list at click time so late-loaded images use their
            // final URL rather than a stale reference from first paint.
            const fresh = imgs.map((i) => ({
                src: i.currentSrc || i.src,
                name: i.getAttribute('alt') || '',
                type: 'image',
                isImage: true,
            }));
            openLightbox(fresh, idx);
        });
    });
}

export function createProseRenderer() {
    return {
        create(block, messageContainer, opts = {}) {
            const el = document.createElement('div');
            const noAnim = opts.replay ? ' stream-no-anim' : '';
            el.className = `stream-block block-${block.type}${noAnim}`;
            el.dataset.blockId = block.id;
            messageContainer.appendChild(el);
            block.el = el;
            return el;
        },
        update(block) {
            if (!block.el) return;
            if (typeof window.marked?.parse !== 'function') {
                // marked hasn't loaded yet (replay can race page init, or
                // the CDN failed). Render as preformatted text; mark the
                // node so the next successful parse knows to upgrade it.
                block.el.textContent = block.text;
                block.el.dataset.proseFallback = '1';
                // Fallback upgrade poller — replay finalizes sync and won't
                // trigger update() again on its own. Without this, CDN-late
                // marked leaves the block as plaintext forever. One watcher
                // per block, bounded retries, cleared once upgraded.
                if (!block.meta._markedWatcherId) {
                    let attempts = 0;
                    const MAX_ATTEMPTS = 240; // ~4s at 60fps — covers slow CDN
                    const watcher = () => {
                        if (!block.el || block.el.dataset.proseFallback !== '1') {
                            block.meta._markedWatcherId = null;
                            return;
                        }
                        if (typeof window.marked?.parse === 'function') {
                            block.meta._markedWatcherId = null;
                            this.update(block);
                            return;
                        }
                        if (++attempts >= MAX_ATTEMPTS) {
                            block.meta._markedWatcherId = null;
                            return;
                        }
                        block.meta._markedWatcherId = requestAnimationFrame(watcher);
                    };
                    block.meta._markedWatcherId = requestAnimationFrame(watcher);
                }
                return;
            }
            const rawHTML = window.marked.parse(block.text);
            const safeHTML = window.DOMPurify
                ? window.DOMPurify.sanitize(rawHTML)
                : rawHTML;
            const wrapper = document.createElement('div');
            wrapper.innerHTML = safeHTML;

            // If the previous render was the textContent fallback, blow away
            // the stale text node first — morphdom would otherwise try to
            // diff text-against-elements and can leave orphaned content.
            if (block.el.dataset.proseFallback === '1') {
                block.el.innerHTML = '';
                delete block.el.dataset.proseFallback;
            }

            // Preserve the stream cursor across morphdom updates. It's
            // seeded by transferCursor() on BLOCK_START and lives as the
            // tail child of block.el — morphdom would otherwise strip it
            // on every text update (cursor only visible for 1 frame per
            // block before disappearing). Pop it off, diff, re-append.
            const detachedCursor = block.el.querySelector(':scope > .stream-cursor');
            if (detachedCursor) detachedCursor.remove();

            if (window.morphdom) {
                window.morphdom(block.el, wrapper, {
                    childrenOnly: true,
                    onBeforeElUpdated(fromEl) {
                        if (fromEl.classList?.contains('citation-pill')) return false;
                        if (fromEl.classList?.contains('sources-btn')) return false;
                        return true;
                    },
                });
            } else {
                block.el.innerHTML = safeHTML;
            }

            if (detachedCursor) block.el.appendChild(detachedCursor);
            wireMarkdownImagesToLightbox(block.el);
        },
        finalize(block) {
            // Strip the AI-preamble closer if present.
            let cleaned = stripTrailingPreamble(block.text);
            // Promote inline long quotes to blockquote syntax.
            cleaned = wrapInlineQuotesAsBlockquotes(cleaned);
            if (cleaned !== block.text) {
                block.text = cleaned;
            }
            this.update(block);
        },
    };
}
