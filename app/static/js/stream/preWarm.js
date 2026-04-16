/**
 * Prewarm layout inference — runs at ~100 chars of streamed content to
 * set an early `data-ctx` on the message element so CSS can adapt width
 * and rhythm BEFORE the full post-stream classifier runs at finalize.
 * This eliminates the layout jump that happened when the classifier's
 * final decision disagreed with the in-flight default.
 *
 * Vocabulary is intentionally aligned with streamRenderer.classifyContext()
 * and [data-ctx="..."] selectors in stream-blocks.css so prewarm and
 * finalize can emit values CSS understands. Extend in lockstep.
 */
export function inferLayout(firstChars) {
    if (/```/.test(firstChars)) return 'code-heavy';
    if (/(^|\n)#{1,3} /.test(firstChars)) return 'structured';
    if (/(^|\n)\d+\. /.test(firstChars)) return 'list-heavy';
    return 'prose';
}

/**
 * Apply the inferred layout to the message element AND its wrapper/message
 * ancestors. `data-streamLayout` is kept for legacy CSS. `data-ctx` is the
 * current vocabulary — setting it early is what kills the finalize jump.
 *
 * Marks the assignment as `data-ctx-source="prewarm"` so the finalize pass
 * knows its own update is a refinement (may overwrite) rather than a
 * collision with an external authority like the server scaffold event
 * (which marks itself `data-ctx-source="server"` and wins).
 */
export function applyLayout(containerEl, firstChars) {
    const layout = inferLayout(firstChars);
    containerEl.dataset.streamLayout = layout;

    // Bubble data-ctx to the bubble + outer .message so CSS width/rhythm
    // rules fire during streaming, not only at finalize. Skip if a server
    // source already claimed the ctx (scaffold event in v2 rollouts).
    const targets = [
        containerEl,
        containerEl.closest('.text-message-container'),
        containerEl.closest('.message, .ai-message'),
    ];
    for (const el of targets) {
        if (!el) continue;
        if (el.dataset.ctxSource === 'server') continue;
        el.dataset.ctx = layout;
        el.dataset.ctxSource = 'prewarm';
    }

    return layout;
}
