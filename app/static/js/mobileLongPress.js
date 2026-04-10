/**
 * Mobile Long-Press Context Menu — iOS Peek & Pop style
 * Long press a chat tab → blurred backdrop → real chat preview card → action sheet
 */

(function() {
    'use strict';

    const isTouchDevice = 'ontouchstart' in window || navigator.maxTouchPoints > 0;

    const CONFIG = {
        holdDuration: 400,
        moveThreshold: 10,
        vibrationDuration: 30,
        animateInMs: 300,
        animateOutMs: 220,
        previewMaxMessages: 8
    };

    let holdTimer = null;
    let startX = 0;
    let startY = 0;
    let activeTab = null;
    let overlay = null;
    let isOpen = false;

    // ── Init ────────────────────────────────────────────────────

    function init() {
        if (!isTouchDevice) return;

        const tabList = document.getElementById('tab-list');
        if (!tabList) return;

        injectStyles();

        tabList.addEventListener('touchstart', handleTouchStart, { passive: false });
        tabList.addEventListener('touchmove', handleTouchMove, { passive: true });
        tabList.addEventListener('touchend', handleTouchEnd, { passive: true });
        tabList.addEventListener('touchcancel', handleTouchCancel, { passive: true });
    }

    // ── Styles ──────────────────────────────────────────────────

    function injectStyles() {
        if (document.getElementById('peek-sheet-styles')) return;

        const s = document.createElement('style');
        s.id = 'peek-sheet-styles';
        s.textContent = `
            /* Backdrop */
            .peek-overlay {
                position: fixed;
                inset: 0;
                z-index: 9990;
                background: rgba(0, 0, 0, 0.55);
                backdrop-filter: blur(12px);
                -webkit-backdrop-filter: blur(12px);
                opacity: 0;
                transition: opacity ${CONFIG.animateInMs}ms ease;
                touch-action: none;
                display: flex;
                flex-direction: column;
                align-items: center;
                justify-content: center;
                padding: 20px 16px;
                padding-bottom: max(20px, env(safe-area-inset-bottom, 20px));
                gap: 10px;
                overflow-y: auto;
                overscroll-behavior: contain;
            }
            .peek-overlay.visible { opacity: 1; }
            .peek-overlay.dismissing { opacity: 0; transition-duration: ${CONFIG.animateOutMs}ms; }

            /* Hold feedback */
            .chat-tab.holding {
                background: rgba(255, 255, 255, 0.06);
                transition: background 0.12s ease;
            }

            body.peek-open #tab-list {
                pointer-events: none !important;
                overflow: hidden !important;
            }

            /* Preview card */
            .peek-card {
                width: 100%;
                max-width: 340px;
                max-height: 50vh;
                min-height: 140px;
                background: #1c1c1e;
                color: #e8e8e8;
                border-radius: 20px;
                overflow: hidden;
                box-shadow: 0 12px 48px rgba(0, 0, 0, 0.55),
                            0 0 0 0.5px rgba(255, 255, 255, 0.1);
                transform: scale(0.88);
                opacity: 0;
                transition: transform ${CONFIG.animateInMs}ms cubic-bezier(0.32, 0.72, 0, 1),
                            opacity ${CONFIG.animateInMs * 0.5}ms ease;
                display: flex;
                flex-direction: column;
            }
            .peek-overlay.visible .peek-card {
                transform: scale(1);
                opacity: 1;
            }
            .peek-overlay.dismissing .peek-card {
                transform: scale(0.92);
                opacity: 0;
            }

            /* Preview header */
            .peek-card-header {
                padding: 14px 18px 12px;
                font-size: 15px;
                font-weight: 600;
                color: #fff;
                letter-spacing: -0.2px;
                font-family: -apple-system, BlinkMacSystemFont, 'SF Pro Display', sans-serif;
                border-bottom: none;
                flex-shrink: 0;
                white-space: nowrap;
                overflow: hidden;
                text-overflow: ellipsis;
            }

            /* Chat preview scroll area */
            .peek-card-body {
                flex: 1;
                overflow-y: auto;
                -webkit-overflow-scrolling: touch;
                overscroll-behavior: contain;
                padding: 12px 12px 14px;
                display: flex;
                flex-direction: column;
                gap: 8px;
            }

            /* ── Chat bubbles ── */
            .peek-bubble {
                max-width: 82%;
                padding: 9px 13px;
                font-size: 13.5px;
                line-height: 1.45;
                font-family: -apple-system, BlinkMacSystemFont, 'SF Pro Text', sans-serif;
                word-break: break-word;
                border-radius: 18px;
                position: relative;
            }

            /* User bubble — right aligned, accent color */
            .peek-bubble.peek-user {
                align-self: flex-end;
                background: #2563eb;
                color: #fff;
                border-bottom-right-radius: 6px;
            }

            /* AI bubble — left aligned, subtle surface */
            .peek-bubble.peek-ai {
                align-self: flex-start;
                background: #2c2c2e;
                color: rgba(255, 255, 255, 0.88);
                border-bottom-left-radius: 6px;
            }

            /* Sender label above bubble */
            .peek-sender {
                font-size: 10.5px;
                font-weight: 600;
                letter-spacing: 0.3px;
                text-transform: uppercase;
                margin-bottom: 2px;
                padding: 0 4px;
            }
            .peek-sender.peek-sender-user {
                align-self: flex-end;
                color: rgba(255, 255, 255, 0.3);
            }
            .peek-sender.peek-sender-ai {
                align-self: flex-start;
                color: rgba(255, 255, 255, 0.3);
            }

            /* Markdown inside AI bubbles */
            .peek-bubble.peek-ai p {
                margin: 0 0 6px;
            }
            .peek-bubble.peek-ai p:last-child {
                margin-bottom: 0;
            }
            .peek-bubble.peek-ai strong {
                color: #fff;
                font-weight: 600;
            }
            .peek-bubble.peek-ai em {
                color: rgba(255, 255, 255, 0.7);
            }
            .peek-bubble.peek-ai code {
                font-size: 12px;
                background: rgba(255, 255, 255, 0.08);
                padding: 1px 5px;
                border-radius: 4px;
                font-family: 'SF Mono', 'Fira Code', Menlo, monospace;
            }
            .peek-bubble.peek-ai pre {
                background: rgba(0, 0, 0, 0.3);
                padding: 8px 10px;
                border-radius: 8px;
                margin: 6px 0;
                overflow-x: auto;
                font-size: 11.5px;
                line-height: 1.5;
            }
            .peek-bubble.peek-ai pre code {
                background: none;
                padding: 0;
                font-size: inherit;
            }
            .peek-bubble.peek-ai ul,
            .peek-bubble.peek-ai ol {
                padding-left: 18px;
                margin: 4px 0;
            }
            .peek-bubble.peek-ai li {
                margin-bottom: 2px;
            }
            .peek-bubble.peek-ai h1,
            .peek-bubble.peek-ai h2,
            .peek-bubble.peek-ai h3,
            .peek-bubble.peek-ai h4 {
                font-size: 13.5px;
                font-weight: 600;
                color: #fff;
                margin: 6px 0 3px;
            }
            .peek-bubble.peek-ai h1:first-child,
            .peek-bubble.peek-ai h2:first-child,
            .peek-bubble.peek-ai h3:first-child {
                margin-top: 0;
            }
            .peek-bubble.peek-ai blockquote {
                border-left: 2px solid rgba(255, 255, 255, 0.15);
                padding-left: 10px;
                margin: 4px 0;
                color: rgba(255, 255, 255, 0.6);
            }

            .peek-card-empty {
                padding: 32px 16px;
                text-align: center;
                color: rgba(255, 255, 255, 0.25);
                font-size: 13px;
                font-family: -apple-system, BlinkMacSystemFont, 'SF Pro Text', sans-serif;
            }

            /* No top fade — clean edge */

            /* Action sheet */
            .peek-actions {
                width: 100%;
                max-width: 340px;
                background: #2c2c2e;
                border-radius: 14px;
                overflow: hidden;
                box-shadow: 0 4px 20px rgba(0, 0, 0, 0.4),
                            0 0 0 0.5px rgba(255, 255, 255, 0.06);
                transform: translateY(10px);
                opacity: 0;
                transition: transform ${CONFIG.animateInMs}ms cubic-bezier(0.32, 0.72, 0, 1) 60ms,
                            opacity ${CONFIG.animateInMs * 0.6}ms ease 60ms;
                flex-shrink: 0;
            }
            .peek-overlay.visible .peek-actions {
                transform: translateY(0);
                opacity: 1;
            }
            .peek-overlay.dismissing .peek-actions {
                transform: translateY(10px);
                opacity: 0;
                transition-delay: 0ms;
            }

            .peek-action-row {
                display: flex;
                align-items: center;
                gap: 14px;
                padding: 0 16px;
                height: 48px;
                color: #e8e8e8;
                font-size: 16px;
                font-weight: 400;
                font-family: -apple-system, BlinkMacSystemFont, 'SF Pro Text', 'Segoe UI', Roboto, sans-serif;
                cursor: pointer;
                -webkit-tap-highlight-color: transparent;
                touch-action: manipulation;
                transition: background 0.1s ease;
                user-select: none;
                border: none;
                background: transparent;
                width: 100%;
                text-align: left;
            }
            .peek-action-row:active { background: rgba(255, 255, 255, 0.06); }
            .peek-action-row + .peek-action-row {
                border-top: 1px solid rgba(255, 255, 255, 0.05);
            }
            .peek-action-icon {
                width: 20px;
                display: flex;
                align-items: center;
                justify-content: center;
                font-size: 15px;
                color: rgba(255, 255, 255, 0.45);
                flex-shrink: 0;
            }
            .peek-action-label { flex: 1; }
            .peek-action-row.peek-danger { color: #ef4444; }
            .peek-action-row.peek-danger .peek-action-icon { color: #ef4444; }
        `;
        document.head.appendChild(s);
    }

    // ── Touch handlers ──────────────────────────────────────────

    function handleTouchStart(e) {
        if (isOpen) return;

        const chatTab = e.target.closest('.chat-tab');
        if (!chatTab) return;

        if (e.target.closest('.tab-actions') ||
            e.target.closest('.tab-actions-toggle') ||
            e.target.closest('.button-group')) {
            return;
        }

        const touch = e.touches[0];
        startX = touch.clientX;
        startY = touch.clientY;
        activeTab = chatTab;

        document.body.classList.add('no-select');
        chatTab.classList.add('holding');

        holdTimer = setTimeout(() => {
            e.preventDefault();
            if (navigator.vibrate) navigator.vibrate(CONFIG.vibrationDuration);
            showPeek(chatTab);
        }, CONFIG.holdDuration);
    }

    function handleTouchMove(e) {
        if (!holdTimer) return;
        const touch = e.touches[0];
        if (Math.abs(touch.clientX - startX) > CONFIG.moveThreshold ||
            Math.abs(touch.clientY - startY) > CONFIG.moveThreshold) {
            cancelHold();
        }
    }

    function handleTouchEnd() { cancelHold(); }
    function handleTouchCancel() { cancelHold(); }

    function cancelHold() {
        if (holdTimer) {
            clearTimeout(holdTimer);
            holdTimer = null;
        }
        if (activeTab && !isOpen) {
            activeTab.classList.remove('holding');
            activeTab = null;
        }
        document.body.classList.remove('no-select');
    }

    // ── Render chat messages as bubbles ────────────────────────

    function renderPreviewMessages(sessionId) {
        const manager = window.chatManager;
        if (!manager || !manager.chatHistory) return null;

        const session = manager.chatHistory[sessionId];
        if (!session) return null;

        const messages = session.messages || [];
        if (messages.length === 0) return null;

        const slice = messages.slice(-CONFIG.previewMaxMessages);
        const frag = document.createDocumentFragment();
        let lastSender = null;

        slice.forEach(msg => {
            if (!msg) return;

            const sender = (msg.sender || '').toLowerCase();
            const isUser = sender === 'user' || (!!msg.prompt && !msg.response);

            let rawText = '';
            if (isUser) {
                rawText = msg.prompt || msg.content || msg.text || '';
            } else {
                rawText = msg.response || msg.content || msg.text || '';
            }
            if (!rawText || rawText === '\u200B') return;

            const senderType = isUser ? 'user' : 'ai';

            // Add sender label when sender changes
            if (senderType !== lastSender) {
                const label = document.createElement('div');
                label.className = `peek-sender peek-sender-${senderType}`;
                label.textContent = isUser ? 'You' : 'AI';
                frag.appendChild(label);
                lastSender = senderType;
            }

            // Chat bubble
            const bubble = document.createElement('div');
            bubble.className = `peek-bubble peek-${senderType}`;

            if (isUser) {
                bubble.textContent = rawText;
            } else {
                // Truncate very long AI responses for preview
                let text = rawText;
                if (text.length > 600) text = text.substring(0, 600) + '\n\n…';

                let html = text;
                try {
                    if (typeof marked !== 'undefined') {
                        html = marked.parse(text);
                    }
                    if (typeof DOMPurify !== 'undefined') {
                        html = DOMPurify.sanitize(html);
                    }
                } catch (e) {
                    const tmp = document.createElement('div');
                    tmp.textContent = text;
                    html = '<p>' + tmp.innerHTML + '</p>';
                }
                bubble.innerHTML = html;
            }

            frag.appendChild(bubble);
        });

        return frag.childNodes.length > 0 ? frag : null;
    }

    // ── Show Peek ───────────────────────────────────────────────

    function showPeek(chatTab) {
        holdTimer = null;
        isOpen = true;
        chatTab.classList.remove('holding');
        document.body.classList.add('peek-open');

        const sessionId = chatTab.dataset.sessionId;
        const sessionName = chatTab.querySelector('.tab-name')?.textContent?.trim() || 'Chat';
        const isPinned = chatTab.classList.contains('pinned-tab');
        const messageFrag = renderPreviewMessages(sessionId);

        const actions = [
            { id: 'share',   icon: 'fa-arrow-up-from-bracket', label: 'Share chat' },
            { id: 'pin',     icon: 'fa-thumbtack',             label: isPinned ? 'Unpin chat' : 'Pin chat' },
            { id: 'rename',  icon: 'fa-pen',                   label: 'Rename' },
            { id: 'archive', icon: 'fa-box-archive',           label: 'Archive' },
            { id: 'delete',  icon: 'fa-trash',                 label: 'Delete', destructive: true }
        ];

        // Build overlay
        overlay = document.createElement('div');
        overlay.className = 'peek-overlay';

        // Preview card
        const card = document.createElement('div');
        card.className = 'peek-card';

        const header = document.createElement('div');
        header.className = 'peek-card-header';
        header.textContent = sessionName;
        card.appendChild(header);

        const body = document.createElement('div');
        body.className = 'peek-card-body';

        if (messageFrag) {
            body.appendChild(messageFrag);
        } else {
            const empty = document.createElement('div');
            empty.className = 'peek-card-empty';
            empty.textContent = 'No messages yet';
            body.appendChild(empty);
        }
        card.appendChild(body);
        overlay.appendChild(card);

        // Scroll preview to bottom (most recent messages)
        requestAnimationFrame(() => {
            body.scrollTop = body.scrollHeight;
        });

        // Action sheet
        const actionsEl = document.createElement('div');
        actionsEl.className = 'peek-actions';

        actions.forEach(action => {
            const row = document.createElement('button');
            row.className = 'peek-action-row' + (action.destructive ? ' peek-danger' : '');
            row.innerHTML = `
                <span class="peek-action-icon"><i class="fas ${action.icon}"></i></span>
                <span class="peek-action-label">${action.label}</span>
            `;
            row.addEventListener('click', () => {
                dismiss(() => handleAction(action.id, sessionId, sessionName, chatTab));
            });
            actionsEl.appendChild(row);
        });
        overlay.appendChild(actionsEl);

        // Tap backdrop to dismiss
        overlay.addEventListener('click', (e) => {
            if (e.target === overlay) dismiss();
        });

        document.body.appendChild(overlay);
        requestAnimationFrame(() => overlay.classList.add('visible'));
    }

    // ── Dismiss ─────────────────────────────────────────────────

    function dismiss(callback) {
        if (!overlay) { if (callback) callback(); return; }

        const el = overlay;
        el.classList.remove('visible');
        el.classList.add('dismissing');

        // Remove immediately to avoid overlap with next modal
        setTimeout(() => {
            el.remove();
            overlay = null;
            isOpen = false;
            activeTab = null;
            document.body.classList.remove('no-select', 'peek-open');
            // Extra frame to let DOM settle before opening next modal
            requestAnimationFrame(() => {
                if (callback) callback();
            });
        }, CONFIG.animateOutMs + 40);
    }

    // ── Action Handler ──────────────────────────────────────────

    function handleAction(actionId, sessionId, sessionName, chatTab) {
        if (navigator.vibrate) navigator.vibrate(20);

        const manager = window.chatManager;
        if (!manager) return;

        switch (actionId) {
            case 'share':   manager.shareSession(sessionId); break;
            case 'pin':     manager.pinSession(sessionId); break;
            case 'archive': manager.archiveSession(sessionId); break;
            case 'rename':  manager.renameSession(sessionId); break;
            case 'delete':  manager.deleteSession(sessionId); break;
        }
    }

    // ── Boot ────────────────────────────────────────────────────

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }

})();
