/**
 * Mermaid diagram renderer.
 * Buffered: shows pending placeholder while streaming, validates + renders on BLOCK_END.
 * On invalid source, shows the raw text inside a <pre class="mermaid-error">.
 */

let mermaidInitialized = false;

function initializeMermaidOnce() {
    if (mermaidInitialized || !window.mermaid) return;
    window.mermaid.initialize({ startOnLoad: false, theme: 'dark' });
    mermaidInitialized = true;
}

export function createMermaidRenderer() {
    return {
        create(block, messageContainer) {
            const el = document.createElement('div');
            el.className = 'stream-block block-mermaid is-pending';
            el.dataset.blockId = block.id;
            el.innerHTML = '<span class="pulse-dots">…</span>';
            messageContainer.appendChild(el);
            block.el = el;
            return el;
        },
        update(_block) {
            // no-op while streaming
        },
        async finalize(block) {
            if (!block.el) return;
            block.el.classList.remove('is-pending');
            initializeMermaidOnce();
            if (!window.mermaid) {
                block.el.innerHTML = '';
                block.el.textContent = block.text;
                return;
            }
            try {
                await window.mermaid.parse(block.text);
                const { svg } = await window.mermaid.render(
                    `mermaid-${block.id}`,
                    block.text,
                );
                block.el.innerHTML = svg;
            } catch (err) {
                const pre = document.createElement('pre');
                pre.className = 'mermaid-error';
                pre.textContent = block.text;
                block.el.innerHTML = '';
                block.el.appendChild(pre);
            }
        },
    };
}
