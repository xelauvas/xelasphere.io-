const CURSOR_CLASS = 'stream-cursor';

export function createCursorElement() {
    const el = document.createElement('span');
    el.className = CURSOR_CLASS;
    return el;
}

/**
 * Transfer the cursor to `newTailEl` atomically, preserving CSS animation
 * state. Moves the existing cursor node via appendChild (which atomically
 * detaches and reattaches) rather than remove-then-create — the latter
 * resets CSS animations (blink restarts from frame 0 on every block
 * boundary, making the cursor look like it flickers or loses rhythm).
 *
 * Called on every BLOCK_START. Safe to call if no previous cursor exists.
 */
export function transferCursor(messageContainerEl, newTailEl) {
    const existing = messageContainerEl.querySelectorAll(`.${CURSOR_CLASS}`);
    if (existing.length === 0) {
        newTailEl.appendChild(createCursorElement());
        return;
    }
    // Keep the first; drop any extras (defensive — should be ≤1).
    for (let i = 1; i < existing.length; i++) existing[i].remove();
    // appendChild on an already-attached node atomically detaches it from
    // its previous parent and attaches to the new one — CSS animation
    // state is preserved, no blink reset, no paint gap.
    newTailEl.appendChild(existing[0]);
}

export function removeCursor(messageContainerEl) {
    messageContainerEl.querySelectorAll(`.${CURSOR_CLASS}`).forEach(el => el.remove());
}
