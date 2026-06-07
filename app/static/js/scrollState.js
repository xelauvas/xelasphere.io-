// ===========================================
// ChatGPT-style Bottom Pin Scroll Logic
// ===========================================

function initScrollState() {
    // Find the scroll container - #chat-scroll-region is THE scroll container
    function findScrollableContainer() {
        const scrollContainer = document.querySelector("#chat-scroll-region");

        if (scrollContainer) {
            return scrollContainer;
        }

        console.warn('[ScrollState] #chat-scroll-region not found!');
        return document.scrollingElement || document.documentElement;
    }

    const scrollEl = findScrollableContainer();
    const chatDisplay = document.querySelector("#chat-display");

    if (!scrollEl || !chatDisplay) {
        console.warn('[ScrollState] Could not find scroll container or chat display');
        return;
    }

    // Prevent double initialization
    if (window._scrollStateInitialized) {
        return;
    }
    window._scrollStateInitialized = true;

    let isStreaming = false;
    let isPinnedToBottom = true;
    let waitingForContent = false;
    let isProgrammaticScroll = false;
    let userBrokePin = false;
    let topAnchorEl = null;
    let raf1 = null;
    let raf2 = null;

    const BOTTOM_THRESHOLD = 80;
    const TRUE_BOTTOM_THRESHOLD = 4;

    // Distance-from-bottom check at the true bottom (not the 80px arrow band)
    function atTrueBottom() {
        return scrollEl.scrollHeight - (scrollEl.scrollTop + scrollEl.clientHeight) <= TRUE_BOTTOM_THRESHOLD;
    }

    // Check if user is near the bottom
    function nearBottom() {
        return scrollEl.scrollHeight - (scrollEl.scrollTop + scrollEl.clientHeight) < BOTTOM_THRESHOLD;
    }

    // Scroll to bottom with optional behavior
    function scrollToBottom(behavior = "auto") {
        scrollEl.scrollTo({ top: scrollEl.scrollHeight, behavior });
    }

    // Schedule scroll after layout settles (2-frame for markdown reflow)
    function schedulePinnedScroll() {
        if (waitingForContent || window._keepMessageAtTop) {
            return;
        }
        if (userBrokePin) {
            return;
        }
        if (!isStreaming || !isPinnedToBottom) {
            return;
        }

        if (raf1) cancelAnimationFrame(raf1);
        if (raf2) cancelAnimationFrame(raf2);

        isProgrammaticScroll = true;

        raf1 = requestAnimationFrame(() => {
            scrollToBottom("auto");
            raf2 = requestAnimationFrame(() => {
                scrollToBottom("auto");
                setTimeout(() => {
                    isProgrammaticScroll = false;
                }, 150);  // Allow smooth scroll animations to complete
            });
        });
    }

    // Track user intent: if they scroll up, stop forcing bottom
    scrollEl.addEventListener("scroll", () => {
        if (isProgrammaticScroll) return;

        const wasAtBottom = isPinnedToBottom;

        // Only (re)pin when the user genuinely reaches the true bottom, not the
        // 80px arrow band. Reaching true bottom also clears a user-broken pin.
        if (atTrueBottom()) {
            isPinnedToBottom = true;
            userBrokePin = false;
        } else {
            isPinnedToBottom = false;
        }

        // When user manually scrolls to bottom, remove padding and reset flags
        if (!wasAtBottom && isPinnedToBottom && window._keepMessageAtTop) {
            window._keepMessageAtTop = false;
            waitingForContent = false;
            topAnchorEl = null;
            const chatDisplayEl = document.getElementById('chat-display');
            if (chatDisplayEl) {
                chatDisplayEl.classList.remove('scroll-padding-active');
            }
        }

        // Update arrow on user scroll
        updateArrowVisibility();

    }, { passive: true });

    // Sticky user-intent break: any manual wheel/touch scroll stops auto-pinning
    // until the user returns to the true bottom (handled in the scroll listener).
    function breakPinOnUserScroll() {
        userBrokePin = true;
        isPinnedToBottom = false;
    }
    scrollEl.addEventListener("wheel", breakPinOnUserScroll, { passive: true });
    scrollEl.addEventListener("touchmove", breakPinOnUserScroll, { passive: true });

    // MutationObserver catches appended nodes + text updates
    const mo = new MutationObserver(() => {
        schedulePinnedScroll();
    });

    mo.observe(chatDisplay, {
        childList: true,
        subtree: true,
        characterData: true
    });

    // Catch late layout changes (images, video posters, iframes, etc.)
    chatDisplay.addEventListener("load", (e) => {
        if (e.target && (e.target.tagName === "IMG" || e.target.tagName === "VIDEO" || e.target.tagName === "IFRAME")) {
            // While the prompt is anchored at the top, a late media load shifts
            // layout; re-run the top-anchor routine instead of letting it drift.
            if (window._keepMessageAtTop && topAnchorEl) {
                isProgrammaticScroll = true;
                anchorMessageToTop(topAnchorEl);
                setTimeout(() => {
                    isProgrammaticScroll = false;
                }, 200);
                return;
            }
            schedulePinnedScroll();
        }
    }, true);

    // -----------------------------
    // Down Arrow UI (ChatGPT logic)
    // -----------------------------
    // Simple rule: show arrow ONLY when:
    //   1. There are messages in the chat
    //   2. Content overflows (scrollable)
    //   3. User is NOT near the bottom
    //   4. Not currently streaming
    // Otherwise: always hidden.

    const isMobileDevice = () => {
        return document.body.classList.contains('device-phone') ||
               document.body.classList.contains('device-tablet') ||
               document.body.classList.contains('viewport-mobile') ||
               document.body.classList.contains('is-mobile') ||
               document.body.classList.contains('is-ios');
    };

    function hasScrollableContent() {
        return scrollEl.scrollHeight > scrollEl.clientHeight + 10;
    }

    function hasMessages() {
        return document.body.classList.contains('has-messages');
    }

    function isTypingActive() {
        return (window._activeTypingAnimations || 0) > 0;
    }

    function shouldShowArrow() {
        return hasMessages() &&
               hasScrollableContent() &&
               !nearBottom() &&
               !isStreaming &&
               !isTypingActive();
    }

    function showDownArrow() {
        document.body.classList.remove('at-scroll-bottom');
        const downArrow = document.getElementById('downArrow');
        if (downArrow) downArrow.style.display = 'flex';
    }

    function hideDownArrow() {
        document.body.classList.add('at-scroll-bottom');
        const downArrow = document.getElementById('downArrow');
        if (downArrow) downArrow.style.display = 'none';
    }

    function updateArrowVisibility() {
        if (shouldShowArrow()) {
            showDownArrow();
        } else {
            hideDownArrow();
        }
    }

    // Down arrow click handler
    function setupDownArrow() {
        const downArrow = document.getElementById('downArrow');
        if (!downArrow) return;

        // Clone to remove old listeners
        const newArrow = downArrow.cloneNode(true);
        downArrow.parentNode.replaceChild(newArrow, downArrow);

        let lastArrowHandledAt = 0;
        const handleArrowClick = (e) => {
            e.preventDefault();
            e.stopPropagation();

            // touchstart + click both fire on touch devices; ignore the echo.
            const now = Date.now();
            if (now - lastArrowHandledAt < 400) return;
            lastArrowHandledAt = now;

            isPinnedToBottom = true;
            userBrokePin = false;

            // Single scroll to the true bottom (no competing second animation).
            scrollToBottom("smooth");
            hideDownArrow();
        };

        newArrow.addEventListener('click', handleArrowClick);
        newArrow.addEventListener('touchstart', handleArrowClick, { passive: false });
    }

    // Create sentinel for scroll detection
    const sentinel = document.createElement('div');
    sentinel.id = 'scroll-sentinel';
    sentinel.style.cssText = 'height: 1px; width: 100%; pointer-events: none;';
    chatDisplay.appendChild(sentinel);

    // Initialize
    setupDownArrow();
    hideDownArrow();

    // -----------------------------
    // Mobile keyboard / visualViewport re-anchor
    // -----------------------------
    // When the on-screen keyboard opens/closes the visual viewport resizes;
    // if we were pinned to the bottom, re-pin so the latest content stays in view.
    if (window.visualViewport && !window._scrollStateViewportBound) {
        window._scrollStateViewportBound = true;
        let viewportTimer = null;
        const onViewportChange = () => {
            if (viewportTimer) clearTimeout(viewportTimer);
            viewportTimer = setTimeout(() => {
                if (isPinnedToBottom && !userBrokePin) {
                    scrollEl.scrollTop = scrollEl.scrollHeight;
                }
            }, 100);
        };
        window.visualViewport.addEventListener('resize', onViewportChange);
        window.visualViewport.addEventListener('scroll', onViewportChange);
    }

    // -----------------------------
    // Public API
    // -----------------------------

    window.scrollState = window.scrollState || {};

    window.scrollState.setStreamingState = function(state) {
        isStreaming = !!state;

        if (isStreaming) {
            hideDownArrow();
        } else {
            // Streaming ended — ensure we're in a clean state
            // If we were in "keep at top" mode, release it now
            if (waitingForContent) {
                waitingForContent = false;
            }
            if (window._keepMessageAtTop) {
                window._keepMessageAtTop = false;
                topAnchorEl = null;
                const chatDisplayEl = document.getElementById('chat-display');
                if (chatDisplayEl) {
                    chatDisplayEl.classList.remove('scroll-padding-active');
                }
            }
            // Re-pin to bottom after streaming completes
            isPinnedToBottom = true;
            userBrokePin = false;
            updateArrowVisibility();
        }
    };

    window.scrollState.scrollToBottomNow = function() {
        if (waitingForContent || window._keepMessageAtTop) {
            return;
        }

        isPinnedToBottom = true;
        userBrokePin = false;
        scrollEl.scrollTop = scrollEl.scrollHeight;
        hideDownArrow();
    };

    // Position a message at the top of the scroll region (16px inset).
    function anchorMessageToTop(messageElement) {
        const messageRect = messageElement.getBoundingClientRect();
        const scrollRect = scrollEl.getBoundingClientRect();
        const offsetFromTop = messageRect.top - scrollRect.top;
        const targetScrollTop = scrollEl.scrollTop + offsetFromTop - 16;
        scrollEl.scrollTop = Math.max(0, targetScrollTop);
    }

    window.scrollUserMessageToTop = function(messageElement) {
        if (!messageElement) return;

        isPinnedToBottom = false;
        waitingForContent = true;
        window._keepMessageAtTop = true;
        isProgrammaticScroll = true;
        topAnchorEl = messageElement;

        const chatDisplay = document.getElementById('chat-display');
        if (chatDisplay) {
            chatDisplay.classList.add('scroll-padding-active');
        }

        // Wait one frame for padding to apply and layout to settle
        requestAnimationFrame(() => {
            anchorMessageToTop(messageElement);

            setTimeout(() => {
                isProgrammaticScroll = false;
            }, 200);
        });

        hideDownArrow();
    };

    window.scrollState.contentArrived = function() {
        // When first content arrives during streaming, transition from
        // "user message at top" to "following the response" mode.
        // This is how ChatGPT/Claude work: the user message scrolls up,
        // then once the response starts, the view follows the new content.
        if (waitingForContent) {
            waitingForContent = false;
        }
        // Re-enable pinned scroll so MutationObserver can keep up with streaming
        if (isStreaming && !isPinnedToBottom) {
            // Remove the scroll-to-top padding since we're now following content
            window._keepMessageAtTop = false;
            topAnchorEl = null;
            const chatDisplayEl = document.getElementById('chat-display');
            if (chatDisplayEl) {
                chatDisplayEl.classList.remove('scroll-padding-active');
            }
            isPinnedToBottom = true;
            userBrokePin = false;
            isProgrammaticScroll = true;
            scrollToBottom("auto");
            setTimeout(() => {
                isProgrammaticScroll = false;
            }, 100);
        }
    };

    window.scrollState.setWaitingForContent = function(state) {
        waitingForContent = !!state;
        if (state) {
            isPinnedToBottom = false;
        }
    };

    window.scrollToBottom = function(force = false, instant = false) {
        if (force) { isPinnedToBottom = true; userBrokePin = false; }
        if (isPinnedToBottom || force) {
            if (instant) {
                scrollEl.scrollTop = scrollEl.scrollHeight;
            } else {
                scrollToBottom("smooth");
            }
        }
    };

    window.setStreamingState = function(state) {
        window.scrollState.setStreamingState(state);
    };

    window.resetScrollState = function() {
        isPinnedToBottom = true;
        isStreaming = false;
        waitingForContent = false;
        userBrokePin = false;
        topAnchorEl = null;
        window._keepMessageAtTop = false;
        if (raf1) cancelAnimationFrame(raf1);
        if (raf2) cancelAnimationFrame(raf2);
        raf1 = null;
        raf2 = null;
        const chatDisplayEl = document.getElementById('chat-display');
        if (chatDisplayEl) {
            chatDisplayEl.classList.remove('scroll-padding-active');
        }
        hideDownArrow();
        scrollEl.scrollTop = scrollEl.scrollHeight;
    };

    window.hideDownArrow = hideDownArrow;
    window.showDownArrow = showDownArrow;

    Object.defineProperty(window.scrollState, 'isPinnedToBottom', {
        configurable: true,
        get: () => isPinnedToBottom,
        set: (val) => { isPinnedToBottom = val; }
    });

    Object.defineProperty(window.scrollState, 'isStreaming', {
        configurable: true,
        get: () => isStreaming
    });
}

// Initialize when DOM is ready
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initScrollState);
} else {
    initScrollState();
}
