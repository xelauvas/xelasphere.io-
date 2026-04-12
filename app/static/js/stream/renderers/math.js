/**
 * Math block renderer (display math, $$…$$).
 * Buffered: shows a pending placeholder while streaming, renders with KaTeX on BLOCK_END.
 */

export function createMathRenderer() {
    return {
        create(block, messageContainer, opts = {}) {
            const el = document.createElement('div');
            const noAnim = opts.replay ? ' stream-no-anim' : '';
            // Replay has full text; skip pending placeholder so KaTeX renders immediately on finalize
            const pending = opts.replay ? '' : ' is-pending';
            el.className = `stream-block block-math${pending}${noAnim}`;
            el.dataset.blockId = block.id;
            el.innerHTML = opts.replay ? '' : '<span class="pulse-dots">…</span>';
            messageContainer.appendChild(el);
            block.el = el;
            return el;
        },
        update(_block) {
            // Intentional no-op during streaming — math is buffered.
        },
        finalize(block) {
            if (!block.el) return;
            block.el.classList.remove('is-pending');
            block.el.innerHTML = '';
            if (!window.katex) {
                block.el.textContent = block.text;
                return;
            }
            try {
                window.katex.render(block.text, block.el, {
                    throwOnError: false,
                    displayMode: true,
                });
            } catch (err) {
                const pre = document.createElement('pre');
                pre.className = 'math-error';
                pre.textContent = block.text;
                block.el.appendChild(pre);
            }
        },
    };
}
