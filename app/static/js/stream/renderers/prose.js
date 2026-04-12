/**
 * Prose / heading / blockquote renderer.
 * Input: block object with .text (raw markdown) and .el (owned DOM node).
 * Pipeline: marked.parse → DOMPurify.sanitize → morphdom diff (childrenOnly).
 */

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
                // Render as preformatted text rather than throwing; the
                // next update pass will upgrade to real markdown.
                block.el.textContent = block.text;
                return;
            }
            const rawHTML = window.marked.parse(block.text);
            const safeHTML = window.DOMPurify
                ? window.DOMPurify.sanitize(rawHTML)
                : rawHTML;
            const wrapper = document.createElement('div');
            wrapper.innerHTML = safeHTML;
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
            this.update(block);
        },
    };
}
