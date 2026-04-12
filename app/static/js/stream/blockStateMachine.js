export function createBlockStateMachine(emit) {
    let rawBuffer = '';
    let cursor = 0;
    let current = null;
    let blockCounter = 0;

    function openBlock(type, meta = {}) {
        closeBlock('natural');
        blockCounter++;
        current = { id: `b${blockCounter}`, type, meta, text: '' };
        emit({ type: 'BLOCK_START', id: current.id, blockType: type, meta });
    }

    function closeBlock(reason) {
        if (!current) return;
        if (current.type === 'list-ul' || current.type === 'list-ol') {
            emit({ type: 'LIST_ITEM_END', blockId: current.id, itemIndex: current.meta.itemCount - 1 });
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

        if (line === '' && current) {
            closeBlock('natural');
            return;
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
            const ulItem = line.match(/^[-+*] (.*)$/);
            const olItem = line.match(/^\d+\. (.*)$/);
            if ((current.type === 'list-ul' && ulItem) || (current.type === 'list-ol' && olItem)) {
                emit({ type: 'LIST_ITEM_END', blockId: current.id, itemIndex: current.meta.itemCount - 1 });
                current.meta.itemCount++;
                emit({ type: 'LIST_ITEM_START', blockId: current.id, itemIndex: current.meta.itemCount - 1 });
                appendToBlock(full);
                return;
            }
            // Continuation line — append to current item
            appendToBlock(full);
            return;
        }

        // Starting a new list
        const ulStart = line.match(/^[-+*] (.*)$/);
        const olStart = line.match(/^\d+\. (.*)$/);
        if (ulStart) {
            openBlock('list-ul', { itemCount: 1 });
            emit({ type: 'LIST_ITEM_START', blockId: current.id, itemIndex: 0 });
            appendToBlock(full);
            return;
        }
        if (olStart) {
            openBlock('list-ol', { itemCount: 1 });
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
            closeBlock('finalize');
        },
    };
}
