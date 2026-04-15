const UL_ITEM_RE = /^[-+*] (.*)$/;
const OL_ITEM_RE = /^\d+\. (.*)$/;

export function createBlockStateMachine(emit) {
    let rawBuffer = '';
    let cursor = 0;
    let current = null;
    let blockCounter = 0;
    // Loose-list support: a single blank line inside a list is held, not
    // closed. If the next non-blank line is another list-item of the same
    // type, we keep the block open (markdown's "loose list"); anything else
    // flushes the close before processing normally.
    let pendingBlankInList = false;

    function openBlock(type, meta = {}) {
        closeBlock('natural');
        blockCounter++;
        current = { id: `b${blockCounter}`, type, meta, text: '' };
        emit({ type: 'BLOCK_START', id: current.id, blockType: type, meta });
    }

    function closeBlock(reason) {
        if (!current) return;
        if (current.type === 'list-ul' || current.type === 'list-ol') {
            emit({ type: 'LIST_ITEM_END', blockId: current.id, itemIndex: current._itemCount - 1 });
        }
        emit({ type: 'BLOCK_END', id: current.id, reason });
        current = null;
    }

    function appendToBlock(text) {
        if (!current) openBlock('paragraph');
        current.text += text;
        emit({ type: 'BLOCK_APPEND', id: current.id, text });
    }

    function processLine(line, hasTrailingNewline) {
        const full = line + (hasTrailingNewline ? '\n' : '');

        // Inside a math block: only $$ at line-start closes
        if (current && current.type === 'math') {
            if (line === '$$') {
                closeBlock('natural');
                return;
            }
            appendToBlock(full);
            return;
        }

        // Opening math block — must be the entire line
        if (line === '$$') {
            openBlock('math');
            return;
        }

        // Inside a code or mermaid block: only a closing fence can exit
        if (current && (current.type === 'code' || current.type === 'mermaid')) {
            if (line === '```') {
                closeBlock('natural');
                return;
            }
            appendToBlock(full);
            return;
        }

        // Opening fence
        const fenceMatch = line.match(/^```([\w.+-]*)$/);
        if (fenceMatch) {
            const lang = fenceMatch[1] || '';
            if (lang === 'mermaid') {
                openBlock('mermaid');
            } else {
                openBlock('code', { lang });
            }
            return;
        }

        if (line === '') {
            if (current && (current.type === 'list-ul' || current.type === 'list-ol')) {
                if (pendingBlankInList) {
                    // Second consecutive blank inside a list → end the list.
                    closeBlock('natural');
                    pendingBlankInList = false;
                } else {
                    pendingBlankInList = true;
                }
                return;
            }
            if (current) closeBlock('natural');
            return;
        }

        // Non-blank line after a held blank inside a list: decide whether to
        // resume the list (same-type item opener) or flush and process anew.
        if (pendingBlankInList) {
            const ulItem = line.match(UL_ITEM_RE);
            const olItem = line.match(OL_ITEM_RE);
            const sameList =
                (current?.type === 'list-ul' && ulItem) ||
                (current?.type === 'list-ol' && olItem);
            if (sameList) {
                // Preserve the blank as a newline in the block text so
                // list.js' parseItems still sees a clean line boundary.
                appendToBlock('\n');
                pendingBlankInList = false;
                // fall through to the "Inside a list" handler below
            } else {
                closeBlock('natural');
                pendingBlankInList = false;
                // fall through to normal processing
            }
        }

        const headingMatch = line.match(/^(#{1,6}) (.*)$/);
        if (headingMatch) {
            openBlock('heading', { level: headingMatch[1].length });
            appendToBlock(full);
            closeBlock('natural');
            return;
        }

        // Inside a list: handle item continuation
        if (current && (current.type === 'list-ul' || current.type === 'list-ol')) {
            const ulItem = line.match(UL_ITEM_RE);
            const olItem = line.match(OL_ITEM_RE);
            if ((current.type === 'list-ul' && ulItem) || (current.type === 'list-ol' && olItem)) {
                emit({ type: 'LIST_ITEM_END', blockId: current.id, itemIndex: current._itemCount - 1 });
                current._itemCount++;
                emit({ type: 'LIST_ITEM_START', blockId: current.id, itemIndex: current._itemCount - 1 });
                appendToBlock(full);
                return;
            }
            // Continuation line — append to current item
            appendToBlock(full);
            return;
        }

        // Starting a new list
        const ulStart = line.match(UL_ITEM_RE);
        const olStart = line.match(OL_ITEM_RE);
        if (ulStart) {
            // _itemCount is closure-internal; never leak into emitted event meta (audit any direct reads of `current` from L3 or renderers).
            openBlock('list-ul', {});
            current._itemCount = 1;
            emit({ type: 'LIST_ITEM_START', blockId: current.id, itemIndex: 0 });
            appendToBlock(full);
            return;
        }
        if (olStart) {
            openBlock('list-ol', {});
            current._itemCount = 1;
            emit({ type: 'LIST_ITEM_START', blockId: current.id, itemIndex: 0 });
            appendToBlock(full);
            return;
        }

        if (line.startsWith('> ')) {
            if (!current || current.type !== 'blockquote') openBlock('blockquote');
            appendToBlock(full);
            return;
        }

        if (!current || current.type !== 'paragraph') openBlock('paragraph');
        appendToBlock(full);
    }

    function drain(final) {
        while (cursor < rawBuffer.length) {
            const nl = rawBuffer.indexOf('\n', cursor);
            if (nl === -1) {
                if (!final) break;
                processLine(rawBuffer.slice(cursor), false);
                cursor = rawBuffer.length;
                break;
            }
            processLine(rawBuffer.slice(cursor, nl), true);
            cursor = nl + 1;
        }
    }

    return {
        append(text) {
            if (!text) return;
            rawBuffer += text;
            drain(false);
        },
        finalize() {
            drain(true);
            pendingBlankInList = false;
            closeBlock('finalize');
        },
    };
}
