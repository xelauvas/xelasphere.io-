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
