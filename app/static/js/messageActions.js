/**
 * Mobile Message Actions - Long Press Popup
 * Shows edit/copy options on long-press like ChatGPT
 */

(function() {
    'use strict';

    const isTouchDevice = 'ontouchstart' in window || navigator.maxTouchPoints > 0;

    const CONFIG = {
        holdDuration: 400,
        moveThreshold: 10,
        vibrationDuration: 30
    };

    let holdTimer = null;
    let startX = 0;
    let startY = 0;
    let activeMessage = null;
    let popup = null;
    let overlay = null;
    let _initialized = false;

    function init() {
        if (!isTouchDevice) return;
        // Idempotent: refresh()/re-init must not stack duplicate popups, overlays,
        // or document-level listeners (each click would otherwise fire closePopup N times).
        if (_initialized) return;
        _initialized = true;

        createPopup();
        createOverlay();

        // Use event delegation on chat display
        const chatDisplay = document.getElementById('chat-display');
        if (chatDisplay) {
            chatDisplay.addEventListener('touchstart', handleTouchStart, { passive: true });
            chatDisplay.addEventListener('touchmove', handleTouchMove, { passive: true });
            chatDisplay.addEventListener('touchend', handleTouchEnd, { passive: true });
            chatDisplay.addEventListener('touchcancel', handleTouchEnd, { passive: true });
        }

        document.addEventListener('click', closePopup);
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape') closePopup();
        });

        console.log('[MessageActions] Long-press popup initialized');
    }

    function createPopup() {
        popup = document.createElement('div');
        popup.className = 'message-actions-popup';
        popup.innerHTML = `
            <button class="message-action-item" data-action="copy">
                <i class="far fa-copy"></i>
                <span>Copy</span>
            </button>
            <button class="message-action-item" data-action="edit">
                <i class="fas fa-pen"></i>
                <span>Edit</span>
            </button>
        `;
        popup.addEventListener('click', handleAction);
        document.body.appendChild(popup);
    }

    function createOverlay() {
        overlay = document.createElement('div');
        overlay.className = 'message-actions-overlay';
        overlay.addEventListener('click', closePopup);
        overlay.addEventListener('touchstart', closePopup, { passive: true });
        document.body.appendChild(overlay);
    }

    function handleTouchStart(e) {
        const userMessage = e.target.closest('.message.user-message');
        if (!userMessage) return;

        // Don't trigger on buttons or interactive elements
        if (e.target.closest('button, a, input, textarea')) return;

        const touch = e.touches[0];
        startX = touch.clientX;
        startY = touch.clientY;
        activeMessage = userMessage;

        // Prevent text selection during long-press
        document.body.classList.add('no-select');

        holdTimer = setTimeout(() => {
            triggerLongPress(userMessage, touch.clientX, touch.clientY);
        }, CONFIG.holdDuration);

        userMessage.classList.add('holding');
    }

    function handleTouchMove(e) {
        if (!holdTimer) return;

        const touch = e.touches[0];
        const deltaX = Math.abs(touch.clientX - startX);
        const deltaY = Math.abs(touch.clientY - startY);

        if (deltaX > CONFIG.moveThreshold || deltaY > CONFIG.moveThreshold) {
            cancelHold();
        }
    }

    function handleTouchEnd() {
        cancelHold();
    }

    function cancelHold() {
        // Only clear selection if a long-press was actually started
        const wasHolding = !!holdTimer;

        if (holdTimer) {
            clearTimeout(holdTimer);
            holdTimer = null;
        }
        if (activeMessage) {
            activeMessage.classList.remove('holding');
        }
        document.body.classList.remove('no-select');

        // Only clear text selection if we were actually holding
        if (wasHolding) {
            window.getSelection()?.removeAllRanges();
        }
    }

    function triggerLongPress(message, x, y) {
        holdTimer = null;

        if (navigator.vibrate) {
            navigator.vibrate(CONFIG.vibrationDuration);
        }

        message.classList.remove('holding');
        message.classList.add('action-active');
        activeMessage = message;

        showPopup(message, x, y);
    }

    function showPopup(message, x, y) {
        if (!popup || !overlay) return;

        const messageId = message.id;
        popup.dataset.messageId = messageId;

        overlay.classList.add('visible');

        // Position popup near the touch point
        const popupWidth = 140;
        const popupHeight = 90;
        const padding = 12;

        let left = x - popupWidth / 2;
        let top = y - popupHeight - 10;

        // Keep within viewport
        if (left < padding) left = padding;
        if (left + popupWidth > window.innerWidth - padding) {
            left = window.innerWidth - popupWidth - padding;
        }
        if (top < padding) {
            top = y + 20; // Show below if no room above
        }

        popup.style.left = left + 'px';
        popup.style.top = top + 'px';
        popup.classList.add('visible');
    }

    function closePopup() {
        // Only clear selection if popup was actually visible
        const wasPopupVisible = popup?.classList.contains('visible');

        if (popup) {
            popup.classList.remove('visible');
        }
        if (overlay) {
            overlay.classList.remove('visible');
        }
        if (activeMessage) {
            activeMessage.classList.remove('action-active', 'holding');
            activeMessage = null;
        }
        document.body.classList.remove('no-select');

        // Only clear selection if we were actually showing the popup
        if (wasPopupVisible) {
            window.getSelection()?.removeAllRanges();
        }
    }

    function handleAction(e) {
        const item = e.target.closest('.message-action-item');
        if (!item) return;

        e.stopPropagation();

        const action = item.dataset.action;
        const messageId = popup.dataset.messageId;
        const message = document.getElementById(messageId);

        if (!message) {
            closePopup();
            return;
        }

        switch (action) {
            case 'copy':
                copyMessage(message);
                break;
            case 'edit':
                editMessage(message, messageId);
                break;
        }

        closePopup();
    }

    function copyMessage(message) {
        const textElement = message.querySelector('.text-message');
        const text = textElement ? textElement.textContent : '';

        navigator.clipboard.writeText(text).then(() => {
            if (window.showToast) {
                window.showToast('Copied to clipboard', 'success');
            }
        }).catch(err => {
            console.error('Copy failed:', err);
        });
    }

    function editMessage(message, messageId) {
        // Use the existing edit functionality
        if (typeof window.enableEditing === 'function') {
            window.enableEditing(messageId);
        } else if (typeof enableEditing === 'function') {
            enableEditing(messageId);
        } else {
            // Fallback: click the existing edit button
            const editBtn = message.querySelector('.edit-button');
            if (editBtn) editBtn.click();
        }
    }

    // Initialize when DOM is ready
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }

    // Re-init when new messages are added (for dynamic content)
    window.MessageActions = {
        refresh: init,
        close: closePopup
    };

})();
