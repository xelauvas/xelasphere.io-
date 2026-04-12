/**
 * Code block renderer using Prism.js (already loaded on every page).
 * - Sets language-{lang} class so Prism autoloader picks up the grammar.
 * - Falls back to language-plaintext for empty or unrecognized languages.
 * - Calls Prism.highlightElement on every update; re-runs on finalize for
 *   the autoloader race (grammar may have arrived after last highlight).
 */

export function createCodeRenderer() {
    return {
        create(block, messageContainer, opts = {}) {
            const lang = block.meta.lang || 'plaintext';
            const pre = document.createElement('pre');
            const noAnim = opts.replay ? ' stream-no-anim' : '';
            // is-streaming only while live; replay has full text already
            const streamingClass = opts.replay ? '' : ' is-streaming';
            pre.className = `stream-block block-code${streamingClass}${noAnim}`;
            pre.dataset.blockId = block.id;
            pre.dataset.lang = lang;

            const code = document.createElement('code');
            code.className = `language-${lang}`;
            pre.appendChild(code);
            messageContainer.appendChild(pre);

            block.el = pre;
            block.meta.codeEl = code;
            return pre;
        },
        update(block) {
            if (!block.meta.codeEl) return;
            block.meta.codeEl.textContent = block.text;
            if (window.Prism) {
                window.Prism.highlightElement(block.meta.codeEl);
            }
        },
        finalize(block) {
            this.update(block);
            if (block.el) block.el.classList.remove('is-streaming');
            if (window.Prism && block.meta.codeEl) {
                queueMicrotask(() => window.Prism.highlightElement(block.meta.codeEl));
            }
            this.attachCopyButton(block);
        },
        attachCopyButton(block) {
            if (!block.el || block.el.querySelector('.code-copy-btn')) return;
            const btn = document.createElement('button');
            btn.className = 'code-copy-btn';
            btn.type = 'button';
            btn.textContent = 'Copy';
            btn.addEventListener('click', () => {
                navigator.clipboard?.writeText(block.text);
                btn.textContent = 'Copied';
                setTimeout(() => { btn.textContent = 'Copy'; }, 1500);
            });
            block.el.appendChild(btn);
        },
    };
}
