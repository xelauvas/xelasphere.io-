/**
 * List renderer — renders a <ul> or <ol> with per-item stagger animation.
 * Items are parsed out of block.text by splitting on the opener regex.
 * Completed items (all but the last) are sub-frozen — we skip re-render for them.
 */

const UL_ITEM_RE = /^[-+*] (.*)$/;
const OL_ITEM_RE = /^\d+\. (.*)$/;

function parseItems(block) {
    const lines = block.text.split('\n');
    const items = [];
    let current = null;
    const re = block.type === 'list-ul' ? UL_ITEM_RE : OL_ITEM_RE;
    for (const line of lines) {
        const m = line.match(re);
        if (m) {
            current = m[1];
            items.push(current);
        } else if (line.trim() !== '' && items.length) {
            items[items.length - 1] += '\n' + line;
        }
    }
    return items;
}

export function createListRenderer() {
    return {
        create(block, messageContainer, opts = {}) {
            const tag = block.type === 'list-ol' ? 'ol' : 'ul';
            const el = document.createElement(tag);
            const noAnim = opts.replay ? ' stream-no-anim' : '';
            el.className = `stream-block block-${block.type}${noAnim}`;
            el.dataset.blockId = block.id;
            messageContainer.appendChild(el);
            block.el = el;
            block.meta.renderedItemCount = 0;
            block.meta.replay = !!opts.replay;
            return el;
        },
        update(block) {
            if (!block.el) return;
            const items = parseItems(block);
            const existing = block.meta.renderedItemCount;
            const liNoAnim = block.meta.replay ? ' stream-no-anim' : '';

            // Update the tail item's text in place (it may still be streaming)
            if (existing > 0 && items.length >= existing) {
                const tailLi = block.el.children[existing - 1];
                if (tailLi) {
                    const tailHTML = window.DOMPurify.sanitize(
                        window.marked.parse(items[existing - 1] || '')
                    );
                    tailLi.innerHTML = tailHTML;
                }
            }

            // Append any new items after the previous tail
            for (let i = existing; i < items.length; i++) {
                const li = document.createElement('li');
                li.className = `block-list-item${liNoAnim}`;
                li.style.setProperty('--i', String(i));
                li.innerHTML = window.DOMPurify.sanitize(window.marked.parse(items[i] || ''));
                block.el.appendChild(li);
            }
            block.meta.renderedItemCount = items.length;
        },
        finalize(block) {
            this.update(block);
        },
    };
}
