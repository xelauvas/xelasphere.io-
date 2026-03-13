/**
 * Mobile Swipe-to-Delete Module
 * Enables swipe gestures on chat sessions for deletion (iOS/Android style)
 */

(function() {
    'use strict';

    // Only run on touch devices
    const isTouchDevice = 'ontouchstart' in window || navigator.maxTouchPoints > 0;

    // Swipe configuration
    const CONFIG = {
        threshold: 80,           // Pixels to swipe to reveal delete button
        deleteThreshold: 160,    // Pixels to swipe for instant delete
        resistance: 0.4,         // Resistance after threshold (0-1)
        animationDuration: 250,  // ms for animations
        velocityThreshold: 0.5   // Velocity threshold for quick swipe delete
    };

    let activeSwipe = null;
    let startX = 0;
    let startY = 0;
    let currentX = 0;
    let startTime = 0;
    let isScrolling = null;

    /**
     * Initialize swipe handlers on the tab list
     */
    function init() {
        if (!isTouchDevice) {
            console.log('[SwipeDelete] Not a touch device, skipping');
            return;
        }

        const tabList = document.getElementById('tab-list');
        if (!tabList) {
            console.log('[SwipeDelete] No tab-list found');
            return;
        }

        // Use event delegation for dynamic content
        tabList.addEventListener('touchstart', handleTouchStart, { passive: true });
        tabList.addEventListener('touchmove', handleTouchMove, { passive: false });
        tabList.addEventListener('touchend', handleTouchEnd, { passive: true });
        tabList.addEventListener('touchcancel', handleTouchCancel, { passive: true });

        // Handle delete button clicks
        tabList.addEventListener('click', handleDeleteClick);

        // Close any open swipe when scrolling sidebar
        const sidebarContent = document.querySelector('.sidebar-content');
        if (sidebarContent) {
            sidebarContent.addEventListener('scroll', closeAllSwipes, { passive: true });
        }

        console.log('[SwipeDelete] Mobile swipe-to-delete initialized');
    }

    /**
     * Handle touch start
     */
    function handleTouchStart(e) {
        const chatTab = e.target.closest('.chat-tab');
        if (!chatTab) return;

        // Don't start swipe if touching action buttons
        if (e.target.closest('.tab-actions') || e.target.closest('.swipe-delete-action')) {
            return;
        }

        // Close any other open swipes
        closeAllSwipes(chatTab);

        const touch = e.touches[0];
        startX = touch.clientX;
        startY = touch.clientY;
        currentX = 0;
        startTime = Date.now();
        isScrolling = null;

        activeSwipe = chatTab;

        // Add swipe container if not exists
        ensureSwipeStructure(chatTab);
    }

    /**
     * Handle touch move
     */
    function handleTouchMove(e) {
        if (!activeSwipe) return;

        const touch = e.touches[0];
        const deltaX = touch.clientX - startX;
        const deltaY = touch.clientY - startY;

        // Determine if scrolling vertically or swiping horizontally
        if (isScrolling === null) {
            isScrolling = Math.abs(deltaY) > Math.abs(deltaX);
        }

        // If scrolling vertically, don't handle swipe
        if (isScrolling) {
            activeSwipe = null;
            return;
        }

        // Prevent vertical scroll while swiping horizontally
        e.preventDefault();

        // Only allow left swipe (negative deltaX)
        if (deltaX > 0) {
            currentX = 0;
        } else {
            // Apply resistance after threshold
            if (Math.abs(deltaX) > CONFIG.threshold) {
                const overflow = Math.abs(deltaX) - CONFIG.threshold;
                currentX = -(CONFIG.threshold + overflow * CONFIG.resistance);
            } else {
                currentX = deltaX;
            }
        }

        // Apply transform
        const content = activeSwipe.querySelector('.tab-content');
        if (content) {
            content.style.transform = `translateX(${currentX}px)`;
            content.style.transition = 'none';
        }

        // Update delete action visibility
        updateDeleteAction(activeSwipe, Math.abs(currentX));
    }

    /**
     * Handle touch end
     */
    function handleTouchEnd(e) {
        if (!activeSwipe) return;

        const deltaTime = Date.now() - startTime;
        const velocity = Math.abs(currentX) / deltaTime;
        const content = activeSwipe.querySelector('.tab-content');

        // Quick swipe or past delete threshold = instant delete
        if (Math.abs(currentX) >= CONFIG.deleteThreshold ||
            (velocity > CONFIG.velocityThreshold && Math.abs(currentX) > CONFIG.threshold)) {
            performDelete(activeSwipe);
        }
        // Past reveal threshold = show delete button
        else if (Math.abs(currentX) >= CONFIG.threshold * 0.6) {
            revealDeleteButton(activeSwipe);
        }
        // Not enough swipe = reset
        else {
            resetSwipe(activeSwipe);
        }

        activeSwipe = null;
        isScrolling = null;
    }

    /**
     * Handle touch cancel
     */
    function handleTouchCancel() {
        if (activeSwipe) {
            resetSwipe(activeSwipe);
            activeSwipe = null;
        }
        isScrolling = null;
    }

    /**
     * Ensure the chat tab has the swipe structure
     */
    function ensureSwipeStructure(chatTab) {
        if (chatTab.querySelector('.swipe-delete-action')) return;

        // Create delete action element
        const deleteAction = document.createElement('div');
        deleteAction.className = 'swipe-delete-action';
        deleteAction.innerHTML = `
            <div class="swipe-delete-btn">
                <i class="fas fa-trash-alt"></i>
                <span>Delete</span>
            </div>
        `;

        // Insert at the beginning of chat tab
        chatTab.insertBefore(deleteAction, chatTab.firstChild);
        chatTab.classList.add('swipe-enabled');
    }

    /**
     * Update delete action appearance based on swipe distance
     */
    function updateDeleteAction(chatTab, distance) {
        const deleteAction = chatTab.querySelector('.swipe-delete-action');
        if (!deleteAction) return;

        const progress = Math.min(distance / CONFIG.deleteThreshold, 1);
        deleteAction.style.opacity = Math.min(distance / CONFIG.threshold, 1);

        // Scale delete icon based on progress
        const btn = deleteAction.querySelector('.swipe-delete-btn');
        if (btn) {
            const scale = 0.8 + (progress * 0.2);
            btn.style.transform = `scale(${scale})`;
        }

        // Add full-swipe class when past delete threshold
        if (distance >= CONFIG.deleteThreshold) {
            chatTab.classList.add('swipe-full');
        } else {
            chatTab.classList.remove('swipe-full');
        }
    }

    /**
     * Reveal delete button (partial swipe)
     */
    function revealDeleteButton(chatTab) {
        const content = chatTab.querySelector('.tab-content');
        if (content) {
            content.style.transition = `transform ${CONFIG.animationDuration}ms ease-out`;
            content.style.transform = `translateX(-${CONFIG.threshold}px)`;
        }
        chatTab.classList.add('swipe-open');
        chatTab.classList.remove('swipe-full');

        const deleteAction = chatTab.querySelector('.swipe-delete-action');
        if (deleteAction) {
            deleteAction.style.opacity = '1';
        }
    }

    /**
     * Reset swipe state
     */
    function resetSwipe(chatTab) {
        const content = chatTab.querySelector('.tab-content');
        if (content) {
            content.style.transition = `transform ${CONFIG.animationDuration}ms ease-out`;
            content.style.transform = 'translateX(0)';
        }
        chatTab.classList.remove('swipe-open', 'swipe-full');

        const deleteAction = chatTab.querySelector('.swipe-delete-action');
        if (deleteAction) {
            deleteAction.style.opacity = '0';
        }
    }

    /**
     * Close all open swipes
     */
    function closeAllSwipes(except = null) {
        const openSwipes = document.querySelectorAll('.chat-tab.swipe-open, .chat-tab.swipe-full');
        openSwipes.forEach(tab => {
            if (tab !== except) {
                resetSwipe(tab);
            }
        });
    }

    /**
     * Perform delete action
     */
    function performDelete(chatTab) {
        const sessionId = chatTab.dataset.sessionId;
        if (!sessionId) {
            resetSwipe(chatTab);
            return;
        }

        // Animate out
        chatTab.style.transition = `all ${CONFIG.animationDuration}ms ease-out`;
        chatTab.style.transform = 'translateX(-100%)';
        chatTab.style.opacity = '0';
        chatTab.style.maxHeight = chatTab.offsetHeight + 'px';

        setTimeout(() => {
            chatTab.style.maxHeight = '0';
            chatTab.style.padding = '0';
            chatTab.style.margin = '0';
        }, CONFIG.animationDuration);

        // Call the actual delete function from ChatManager
        setTimeout(() => {
            if (window.chatManager && typeof window.chatManager.deleteSession === 'function') {
                window.chatManager.deleteSession(sessionId, true); // true = skip confirmation
            } else {
                // Fallback: trigger the delete via DOM
                const deleteBtn = document.querySelector(`.button-group[data-session-id="${sessionId}"] [data-action="deleteSession"]`);
                if (deleteBtn) {
                    deleteBtn.click();
                }
            }
        }, CONFIG.animationDuration * 2);
    }

    /**
     * Handle click on delete button
     */
    function handleDeleteClick(e) {
        const deleteBtn = e.target.closest('.swipe-delete-btn');
        if (!deleteBtn) return;

        e.preventDefault();
        e.stopPropagation();

        const chatTab = deleteBtn.closest('.chat-tab');
        if (chatTab) {
            performDelete(chatTab);
        }
    }

    // Initialize when DOM is ready
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }

    // Re-initialize when new tabs are added (for dynamic content)
    const observer = new MutationObserver((mutations) => {
        mutations.forEach((mutation) => {
            mutation.addedNodes.forEach((node) => {
                if (node.classList && node.classList.contains('chat-tab')) {
                    // New chat tab added, structure will be added on touch
                }
            });
        });
    });

    const tabList = document.getElementById('tab-list');
    if (tabList) {
        observer.observe(tabList, { childList: true, subtree: true });
    }

    // Expose API for external use
    window.SwipeDelete = {
        closeAll: closeAllSwipes,
        reset: resetSwipe
    };

})();
