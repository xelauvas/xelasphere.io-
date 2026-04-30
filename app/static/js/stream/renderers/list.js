/**
 * List renderer — per-item buffered <ul>/<ol>.
 * Items are parsed out of block.text by splitting on the opener regex.
 * The tail item is held out of the DOM until a newer item opens (closing
 * it) or the block finalizes — so every <li> lands fully formed exactly
 * once and never mutates visibly after insertion.
 */

const UL_ITEM_RE = /^[-+*] (.*)$/;
const OL_ITEM_RE = /^\d+\. (.*)$/;
// Task-list prefixes — render the checkbox manually because parseItems
// strips the leading list bullet before md.render runs, so any markdown-it
// task-list plugin would never see the full `- [ ] ...` syntax it needs
// to fire. Pairing CSS lives at message-styles.css .task-list-item.
const TASK_PREFIX_RE = /^\[([ xX])\]\s+(.*)$/;

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
        _flush(block, { includeTail }) {
            if (!block.el) return;

            const markedReady = typeof window.marked?.parse === 'function';
            const renderMd = (raw) => {
                if (!markedReady) return null;
                const html = window.marked.parse(raw || '');
                return window.DOMPurify ? window.DOMPurify.sanitize(html) : html;
            };

            const items = parseItems(block);
            const existing = block.meta.renderedItemCount;
            const liNoAnim = block.meta.replay ? ' stream-no-anim' : '';

            // An item is "closed" when a later item has started, or when the
            // block is finalizing. Only closed items are flushed to the DOM,
            // so each <li> appears fully formed and never mutates after.
            const insertUpTo = includeTail ? items.length : items.length - 1;

            const frag = document.createDocumentFragment();
            let added = 0;
            for (let i = existing; i < insertUpTo; i++) {
                const text = items[i] || '';
                if (text.trim() === '') continue;
                const li = document.createElement('li');
                li.className = `block-list-item${liNoAnim}`;
                li.style.setProperty('--i', String(i));

                // Task-list item? Detect [ ] or [x] prefix and build the
                // checkbox + label DOM manually — md.render on just "[ ] text"
                // produces literal bracket characters because the task-lists
                // plugin can only activate from the full `- [ ] ...` syntax.
                const taskMatch = text.match(TASK_PREFIX_RE);
                if (taskMatch) {
                    li.classList.add('task-list-item');
                    const checked = /[xX]/.test(taskMatch[1]);
                    if (checked) li.classList.add('checked');
                    const checkbox = document.createElement('input');
                    checkbox.type = 'checkbox';
                    checkbox.disabled = true;
                    checkbox.checked = checked;
                    li.appendChild(checkbox);
                    // Wrap remaining markdown in a label so CSS can strike-through
                    // the text when :has(input:checked) — matches the GFM convention.
                    const label = document.createElement('label');
                    const html = renderMd(taskMatch[2]);
                    if (html == null) label.textContent = taskMatch[2];
                    else label.innerHTML = html;
                    li.appendChild(label);
                } else {
                    const html = renderMd(text);
                    if (html == null) li.textContent = text;
                    else li.innerHTML = html;
                }

                frag.appendChild(li);
                added++;
            }
            if (added) {
                // Preserve the trailing cursor across list growth. Without
                // this, transferCursor() seeded the cursor as the only
                // child of block.el; appending items after would leave
                // the cursor at the TOP of the list instead of after the
                // last <li>. Pop, append items, re-attach at the end.
                const trailingCursor = block.el.querySelector(':scope > .stream-cursor');
                if (trailingCursor) trailingCursor.remove();
                block.el.appendChild(frag);
                if (trailingCursor) block.el.appendChild(trailingCursor);
                block.meta.renderedItemCount = existing + added;
            }
        },
        update(block) {
            // Streaming path: hold the tail out. Only flush items that a
            // newer item has superseded (therefore closed).
            this._flush(block, { includeTail: false });
        },
        finalize(block) {
            // Block is ending — tail is now closed, flush everything.
            this._flush(block, { includeTail: true });
        },
    };
}
