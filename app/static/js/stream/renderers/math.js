/**
 * Math block renderer (display math, $$…$$).
 * Buffered: shows a pending placeholder while streaming, renders with KaTeX on BLOCK_END.
 */

export function createMathRenderer() {
    return {
        create(block, messageContainer) {
            const el = document.createElement('div');
            el.className = 'stream-block block-math is-pending';
            el.dataset.blockId = block.id;
            el.innerHTML = '<span class="pulse-dots">…</span>';
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
