/**
 * Mobile Long-Press Bottom Action Sheet
 * 3-layer depth: blurred backdrop → cloned lifted tab → action sheet
 *
 * The sidebar is position:fixed + z-index:1000, creating a stacking context.
 * A child tab can never escape above a body-level overlay via z-index.
 * Fix: clone the selected tab as a fixed element on <body> above the overlay.
 */

(function() {
    'use strict';

    const isTouchDevice = 'ontouchstart' in window || navigator.maxTouchPoints > 0;

    const CONFIG = {
        holdDuration: 400,
        moveThreshold: 10,
        vibrationDuration: 30,
        dismissDelay: 8000,
        animateInMs: 280,
        animateOutMs: 200
    };

    let holdTimer = null;
    let startX = 0;
    let startY = 0;
    let activeTab = null;
    let sheetOverlay = null;
    let liftedClone = null;
    let dismissTimer = null;
    let isOpen = false;

    // ── Initialization ──────────────────────────────────────────

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
        if (document.getElementById('action-sheet-styles')) return;

        const s = document.createElement('style');
        s.id = 'action-sheet-styles';
        s.textContent = `
            /* ═══ Layer 1: Backdrop ═══ */
            .ctx-overlay {
                position: fixed;
                inset: 0;
                z-index: 9990;
                background: rgba(0, 0, 0, 0.5);
                backdrop-filter: blur(6px);
                -webkit-backdrop-filter: blur(6px);
                opacity: 0;
                transition: opacity 200ms ease;
                touch-action: none;
            }
            .ctx-overlay.visible { opacity: 1; }

            /* Lock sidebar */
            body.action-sheet-open #tab-list {
                pointer-events: none !important;
                overflow: hidden !important;
            }

            /* Hide original tab while clone floats above */
            .chat-tab.ctx-hidden {
                opacity: 0 !important;
                transition: none !important;
            }

            /* ═══ Layer 2: Cloned tab — above backdrop ═══ */
            .ctx-clone {
                position: fixed;
                z-index: 9995;
                pointer-events: none;
                border-radius: 10px;
                box-sizing: border-box;
                overflow: hidden;
                transform: scale(1);
                opacity: 1;
                transition: transform 180ms cubic-bezier(0.34, 1.3, 0.64, 1),
                            box-shadow 180ms ease;
            }
            .ctx-clone.lifted {
                transform: scale(1.03);
                box-shadow: 0 6px 28px rgba(0, 0, 0, 0.5);
            }
            .ctx-clone.lowering {
                transform: scale(1);
                box-shadow: none;
                opacity: 0;
                transition: transform 200ms ease,
                            box-shadow 200ms ease,
                            opacity 200ms ease;
            }

            /* Hold feedback on actual tab */
            .chat-tab.holding {
                background: rgba(255, 255, 255, 0.06);
                transition: background 0.12s ease;
            }

            /* ═══ Layer 3: Sheet — compact, centered ═══ */
            .ctx-sheet {
                position: fixed;
                bottom: 0;
                left: 50%;
                z-index: 9999;
                width: min(280px, calc(100% - 32px));
                background: #2a2a2a;
                border-radius: 14px;
                box-shadow: 0 -10px 40px rgba(0, 0, 0, 0.5),
                            0 -2px 10px rgba(0, 0, 0, 0.3);
                transform: translate(-50%, 100%);
                transition: transform ${CONFIG.animateInMs}ms cubic-bezier(0.32, 0.72, 0, 1);
                margin-bottom: max(12px, env(safe-area-inset-bottom, 12px));
                padding: 6px 0;
                max-height: 70vh;
                overflow-y: auto;
                overscroll-behavior: contain;
            }
            .ctx-sheet.visible { transform: translate(-50%, 0); }
            .ctx-sheet.dismissing {
                transform: translate(-50%, 100%);
                transition-duration: ${CONFIG.animateOutMs}ms;
                transition-timing-function: ease-in;
            }

            /* Rows */
            .ctx-row {
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
            .ctx-row:active { background: rgba(255, 255, 255, 0.06); }
            .ctx-row + .ctx-row {
                border-top: 1px solid rgba(255, 255, 255, 0.05);
            }
            .ctx-row-icon {
                width: 20px;
                display: flex;
                align-items: center;
                justify-content: center;
                font-size: 15px;
                color: rgba(255, 255, 255, 0.45);
                flex-shrink: 0;
            }
            .ctx-row-label { flex: 1; }
            .ctx-row.ctx-danger { color: #ef4444; }
            .ctx-row.ctx-danger .ctx-row-icon { color: #ef4444; }
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
            showActionSheet(chatTab);
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

    // ── Action Sheet ────────────────────────────────────────────

    function showActionSheet(chatTab) {
        holdTimer = null;
        isOpen = true;
        chatTab.classList.remove('holding');
        document.body.classList.add('action-sheet-open');

        const sessionId = chatTab.dataset.sessionId;
        const sessionName = chatTab.querySelector('.tab-name')?.textContent?.trim() || 'Chat';
        const isPinned = chatTab.classList.contains('pinned-tab');

        const actions = [
            { id: 'share',   icon: 'fa-arrow-up-from-bracket', label: 'Share chat' },
            { id: 'pin',     icon: 'fa-thumbtack',             label: isPinned ? 'Unpin' : 'Pin' },
            { id: 'rename',  icon: 'fa-pen',                   label: 'Rename' },
            { id: 'archive', icon: 'fa-box-archive',           label: 'Archive' },
            { id: 'delete',  icon: 'fa-trash',                 label: 'Delete', destructive: true }
        ];

        // ── Layer 2: Clone the selected tab above the backdrop ──
        const rect = chatTab.getBoundingClientRect();
        const styles = window.getComputedStyle(chatTab);

        liftedClone = chatTab.cloneNode(true);
        liftedClone.className = 'ctx-clone';
        liftedClone.style.top = rect.top + 'px';
        liftedClone.style.left = rect.left + 'px';
        liftedClone.style.width = rect.width + 'px';
        liftedClone.style.height = rect.height + 'px';
        liftedClone.style.background = styles.background || '#1a1a22';
        liftedClone.style.padding = styles.padding;
        liftedClone.style.display = styles.display;
        liftedClone.style.alignItems = styles.alignItems;
        liftedClone.style.color = '#fff';
        liftedClone.style.fontSize = styles.fontSize;

        // Hide original
        chatTab.classList.add('ctx-hidden');

        // Mount clone, then lift
        document.body.appendChild(liftedClone);
        requestAnimationFrame(() => liftedClone.classList.add('lifted'));

        // ── Layer 1: Backdrop ──
        sheetOverlay = document.createElement('div');
        sheetOverlay.className = 'ctx-overlay';
        document.body.appendChild(sheetOverlay);
        requestAnimationFrame(() => sheetOverlay.classList.add('visible'));
        sheetOverlay.addEventListener('click', () => dismiss());

        // ── Layer 3: Sheet ──
        const sheet = document.createElement('div');
        sheet.className = 'ctx-sheet';

        actions.forEach(action => {
            const row = document.createElement('button');
            row.className = 'ctx-row' + (action.destructive ? ' ctx-danger' : '');
            row.innerHTML = `
                <span class="ctx-row-icon"><i class="fas ${action.icon}"></i></span>
                <span class="ctx-row-label">${action.label}</span>
            `;
            row.addEventListener('click', () => {
                dismiss(() => handleAction(action.id, sessionId, sessionName, chatTab));
            });
            sheet.appendChild(row);
        });

        document.body.appendChild(sheet);
        sheetOverlay._sheet = sheet;

        // Delay sheet slightly for sequenced feel
        setTimeout(() => {
            requestAnimationFrame(() => sheet.classList.add('visible'));
        }, 80);

        dismissTimer = setTimeout(() => dismiss(), CONFIG.dismissDelay);
    }

    function dismiss(callback) {
        if (!sheetOverlay) { if (callback) callback(); return; }

        if (dismissTimer) { clearTimeout(dismissTimer); dismissTimer = null; }

        const sheet = sheetOverlay._sheet;
        const overlay = sheetOverlay;

        // Lower clone
        if (liftedClone) {
            liftedClone.classList.remove('lifted');
            liftedClone.classList.add('lowering');
        }

        // Dismiss overlay + sheet
        overlay.classList.remove('visible');
        if (sheet) sheet.classList.add('dismissing');

        setTimeout(() => {
            // Restore original tab
            document.querySelectorAll('.ctx-hidden').forEach(el =>
                el.classList.remove('ctx-hidden')
            );

            if (liftedClone) { liftedClone.remove(); liftedClone = null; }
            overlay.remove();
            if (sheet) sheet.remove();
            sheetOverlay = null;
            isOpen = false;
            activeTab = null;
            document.body.classList.remove('no-select', 'action-sheet-open');

            if (callback) callback();
        }, CONFIG.animateOutMs + 30);
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

    // ── Utilities ───────────────────────────────────────────────

    function escapeHtml(str) {
        const d = document.createElement('div');
        d.textContent = str;
        return d.innerHTML;
    }

    // ── Boot ────────────────────────────────────────────────────

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }

})();
