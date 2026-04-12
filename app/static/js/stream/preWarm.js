export function inferLayout(firstChars) {
    if (/```/.test(firstChars)) return 'code-heavy';
    if (/(^|\n)#{1,3} /.test(firstChars)) return 'structured';
    if (/(^|\n)\d+\. /.test(firstChars)) return 'enumerated';
    return 'prose';
}

/**
 * Apply the inferred layout class to the container.
 * Called once per stream, after the first ~100 chars arrive.
 */
export function applyLayout(containerEl, firstChars) {
    const layout = inferLayout(firstChars);
    containerEl.dataset.streamLayout = layout;
    return layout;
}
