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
        create(block, messageContainer, opts = {}) {
            const el = document.createElement('div');
            const noAnim = opts.replay ? ' stream-no-anim' : '';
            const pending = opts.replay ? '' : ' is-pending';
            el.className = `stream-block block-mermaid${pending}${noAnim}`;
            el.dataset.blockId = block.id;
            el.innerHTML = opts.replay ? '' : '<span class="pulse-dots">…</span>';
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
            // Snapshot the node + text at finalize-time. Between the awaits
            // below the parent message may be destroyed, cleared, or the
            // block detached — in which case we must NOT write to the (now
            // orphaned) node. Guard with isConnected on every resume.
            const el = block.el;
            const src = block.text;
            try {
                await window.mermaid.parse(src);
                if (!el.isConnected) return;
                const { svg } = await window.mermaid.render(
                    `mermaid-${block.id}`,
                    src,
                );
                if (!el.isConnected) return;
                el.innerHTML = svg;
            } catch (err) {
                if (!el.isConnected) return;
                const pre = document.createElement('pre');
                pre.className = 'mermaid-error';
                pre.textContent = src;
                el.innerHTML = '';
                el.appendChild(pre);
            }
        },
    };
}
