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

            // Lib-guarded render: mirror prose.js' guards. If marked isn't
            // loaded yet (CDN slow / replay-before-init race), fall back to
            // plain text so we don't throw TypeError mid-stream. Next update
            // will upgrade once marked arrives.
            const markedReady = typeof window.marked?.parse === 'function';
            const renderMd = (raw) => {
                if (!markedReady) return null;
                const html = window.marked.parse(raw || '');
                return window.DOMPurify ? window.DOMPurify.sanitize(html) : html;
            };

            const items = parseItems(block);
            const existing = block.meta.renderedItemCount;
            const liNoAnim = block.meta.replay ? ' stream-no-anim' : '';

            // Update the tail item's text in place (still streaming). Use
            // morphdom so partial-markdown diffs are reconciled against the
            // existing node tree instead of blowing away any post-finalize
            // injections (citation pills, sources buttons).
            if (existing > 0 && items.length >= existing) {
                const tailLi = block.el.children[existing - 1];
                if (tailLi) {
                    const tailHTML = renderMd(items[existing - 1]);
                    if (tailHTML == null) {
                        tailLi.textContent = items[existing - 1] || '';
                    } else if (window.morphdom) {
                        const staging = document.createElement('li');
                        staging.innerHTML = tailHTML;
                        window.morphdom(tailLi, staging, {
                            childrenOnly: true,
                            onBeforeElUpdated(fromEl) {
                                if (fromEl.classList?.contains('citation-pill')) return false;
                                if (fromEl.classList?.contains('sources-btn')) return false;
                                return true;
                            },
                        });
                    } else {
                        tailLi.innerHTML = tailHTML;
                    }
                }
            }

            // Append any new items after the previous tail. Two rules to avoid
            // the "empty row appears, then fills" cascade during streaming:
            //   1) Hold the currently-streaming tail item OUT of the DOM until
            //      it has real content. A brand-new list-item opener like "5. "
            //      arrives before any words; materialising an empty <li> now
            //      just to morphdom content into it reads visually as a blank
            //      row popping in then filling character-by-character.
            //   2) Batch every materialization into a DocumentFragment so one
            //      stream-chunk = one DOM mutation (mirrors prose's morphdom
            //      atomicity).
            const frag = document.createDocumentFragment();
            let added = 0;
            for (let i = existing; i < items.length; i++) {
                const text = items[i] || '';
                const isTail = i === items.length - 1;
                if (isTail && text.trim() === '') break; // hold empty tail
                const li = document.createElement('li');
                li.className = `block-list-item${liNoAnim}`;
                li.style.setProperty('--i', String(i));
                const html = renderMd(text);
                if (html == null) li.textContent = text;
                else li.innerHTML = html;
                frag.appendChild(li);
                added++;
            }
            if (added) block.el.appendChild(frag);
            block.meta.renderedItemCount = existing + added;
        },
        finalize(block) {
            this.update(block);
        },
    };
}
