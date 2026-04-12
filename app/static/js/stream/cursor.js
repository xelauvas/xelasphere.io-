const CURSOR_CLASS = 'stream-cursor';

export function createCursorElement() {
    const el = document.createElement('span');
    el.className = CURSOR_CLASS;
    return el;
}

/**
 * Transfer the cursor to `newTailEl`. Removes any existing `.stream-cursor`
 * elements from anywhere in the message container first (defensive — there
 * should only ever be one) and appends a fresh one to newTailEl.
 *
 * Called on every BLOCK_START. Safe to call if no previous cursor exists.
 */
export function transferCursor(messageContainerEl, newTailEl) {
    messageContainerEl.querySelectorAll(`.${CURSOR_CLASS}`).forEach(el => el.remove());
    newTailEl.appendChild(createCursorElement());
}

export function removeCursor(messageContainerEl) {
    messageContainerEl.querySelectorAll(`.${CURSOR_CLASS}`).forEach(el => el.remove());
}
