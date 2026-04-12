export function createBlockStateMachine(emit) {
    let inBlock = false;
    let blockId = 0;

    function startBlockIfNeeded(type) {
        if (inBlock) return;
        inBlock = true;
        blockId++;
        emit({ type: 'BLOCK_START', id: `b${blockId}`, blockType: type, meta: {} });
    }

    function endBlockIfNeeded(reason) {
        if (!inBlock) return;
        emit({ type: 'BLOCK_END', id: `b${blockId}`, reason });
        inBlock = false;
    }

    return {
        append(text) {
            if (!text) return;
            startBlockIfNeeded('paragraph');
            emit({ type: 'BLOCK_APPEND', id: `b${blockId}`, text });
        },
        finalize() {
            endBlockIfNeeded('finalize');
        },
    };
}
