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
                // marked hasn't loaded yet (replay can race page init).
                // Render as preformatted text; mark the node so the next
                // successful parse knows to upgrade it rather than assume
                // the previous DOM is already a parsed tree.
                block.el.textContent = block.text;
                block.el.dataset.proseFallback = '1';
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
        },
        finalize(block) {
            // Strip the AI-preamble closer if present, then re-render once.
            const cleaned = stripTrailingPreamble(block.text);
            if (cleaned !== block.text) {
                block.text = cleaned;
            }
            this.update(block);
        },
    };
}
