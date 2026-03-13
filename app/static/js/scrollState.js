// ===========================================
// ChatGPT-style Bottom Pin Scroll Logic
// ===========================================

function initScrollState() {
    // Find the scroll container - #chat-scroll-region is THE scroll container
    function findScrollableContainer() {
        // DOAR #chat-scroll-region scrollează (pattern ChatGPT/Slack/Discord)
        const scrollContainer = document.querySelector("#chat-scroll-region");

        if (scrollContainer) {
            return scrollContainer;
        }

        // Fallback (nu ar trebui să ajungem aici)
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
    let isPinnedToBottom = true; // assume pinned until user scrolls up
    let waitingForContent = false; // true = don't auto-scroll until first AI content
    let isProgrammaticScroll = false; // flag pentru a ignora scroll events din cod
    let raf1 = null;
    let raf2 = null;

    const BOTTOM_THRESHOLD = 80; // px "near bottom" tolerance

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
        // Don't scroll if we want to keep user message at top (ChatGPT-style)
        if (waitingForContent || window._keepMessageAtTop) {
            return;
        }
        if (!isStreaming || !isPinnedToBottom) {
            return;
        }

        // Cancel any pending frames to avoid jitter
        if (raf1) cancelAnimationFrame(raf1);
        if (raf2) cancelAnimationFrame(raf2);

        // Marchează că e scroll programatic - ignoră scroll events
        isProgrammaticScroll = true;

        // 2-frame settle: DOM update -> layout -> markdown reflow -> final
        raf1 = requestAnimationFrame(() => {
            scrollToBottom("auto");
            raf2 = requestAnimationFrame(() => {
                scrollToBottom("auto");
                // După scroll, așteaptă puțin apoi permite iar user scroll detection
                setTimeout(() => {
                    isProgrammaticScroll = false;
                }, 50);
            });
        });
    }

    // Track user intent: if they scroll up, stop forcing bottom
    scrollEl.addEventListener("scroll", () => {
        // Ignoră scroll events cauzate de codul nostru
        if (isProgrammaticScroll) return;

        // User a scrollat manual - verifică dacă e la bottom
        const wasAtBottom = isPinnedToBottom;
        isPinnedToBottom = nearBottom();

        // When user manually scrolls to bottom, remove padding and reset flags
        if (!wasAtBottom && isPinnedToBottom && window._keepMessageAtTop) {
            window._keepMessageAtTop = false;
            waitingForContent = false;
            // Remove the extra padding since user is now at bottom
            const chatDisplayEl = document.getElementById('chat-display');
            if (chatDisplayEl) {
                chatDisplayEl.classList.remove('scroll-padding-active');
            }
        }

    }, { passive: true });

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
            schedulePinnedScroll();
        }
    }, true); // capture phase to catch all loads

    // -----------------------------
    // Down Arrow UI
    // -----------------------------

    const isMobileDevice = () => {
        return document.body.classList.contains('device-phone') ||
               document.body.classList.contains('is-mobile') ||
               document.body.classList.contains('is-ios');
    };

    // Check if content is scrollable (has overflow)
    function hasScrollableContent() {
        return scrollEl.scrollHeight > scrollEl.clientHeight + 10; // 10px buffer
    }

    function showDownArrow() {
        // Don't show if no scrollable content
        if (!hasScrollableContent()) {
            hideDownArrow();
            return;
        }
        document.body.classList.remove('at-scroll-bottom');
        // Desktop: also set inline style
        if (!isMobileDevice()) {
            const downArrow = document.getElementById('downArrow');
            if (downArrow) downArrow.style.display = 'flex';
        }
    }

    function hideDownArrow() {
        document.body.classList.add('at-scroll-bottom');
        // Desktop: also set inline style
        if (!isMobileDevice()) {
            const downArrow = document.getElementById('downArrow');
            if (downArrow) downArrow.style.display = 'none';
        }
    }

    // Update arrow visibility based on scroll position
    function updateArrowVisibility() {
        // Hide if no scrollable content, at bottom, or streaming
        if (!hasScrollableContent() || nearBottom() || isStreaming) {
            hideDownArrow();
        } else {
            showDownArrow();
        }
    }

    // Create a sentinel element at the bottom for IntersectionObserver
    const sentinel = document.createElement('div');
    sentinel.id = 'scroll-sentinel';
    sentinel.style.cssText = 'height: 1px; width: 100%; pointer-events: none;';
    chatDisplay.appendChild(sentinel);

    // Use IntersectionObserver for reliable mobile scroll detection
    const observerOptions = {
        root: scrollEl,
        threshold: 0,
        rootMargin: '100px'
    };

    const sentinelObserver = new IntersectionObserver((entries) => {
        entries.forEach(entry => {
            if (entry.isIntersecting) {
                // Sentinel visible = at bottom
                hideDownArrow();
            } else {
                // Sentinel not visible = scrolled up
                if (!isStreaming) {
                    showDownArrow();
                }
            }
        });
    }, observerOptions);

    sentinelObserver.observe(sentinel);

    // Also use scroll event as backup
    scrollEl.addEventListener("scroll", () => {
        updateArrowVisibility();
    }, { passive: true });

    // Down arrow click handler
    function setupDownArrow() {
        const downArrow = document.getElementById('downArrow');
        if (!downArrow) return;

        // Clone to remove old listeners
        const newArrow = downArrow.cloneNode(true);
        downArrow.parentNode.replaceChild(newArrow, downArrow);

        const handleArrowClick = (e) => {
            e.preventDefault();
            e.stopPropagation();
            isPinnedToBottom = true;

            // Check mobile at click time (body classes may not be set at setup)
            const isMobile = isMobileDevice();

            if (isMobile) {
                // Mobile: use scrollIntoView (more reliable)
                const sentinel = document.getElementById('scroll-sentinel');
                if (sentinel) {
                    sentinel.scrollIntoView({ behavior: 'smooth', block: 'end' });
                } else {
                    const messages = document.querySelectorAll('#chat-display .message');
                    if (messages.length > 0) {
                        messages[messages.length - 1].scrollIntoView({ behavior: 'smooth', block: 'end' });
                    }
                }
                // Extra push after first scroll settles
                setTimeout(() => {
                    scrollEl.scrollTo({ top: scrollEl.scrollHeight, behavior: 'smooth' });
                }, 300);
                setTimeout(hideDownArrow, 400);
            } else {
                // Desktop: original behavior
                scrollToBottom("smooth");
                hideDownArrow();
            }
        };

        newArrow.addEventListener('click', handleArrowClick);
        newArrow.addEventListener('touchstart', handleArrowClick, { passive: false });
    }

    // Initialize
    setupDownArrow();
    // Hide arrow on init (desktop uses inline style, mobile uses CSS)
    hideDownArrow();

    // -----------------------------
    // Public API
    // -----------------------------

    window.scrollState = window.scrollState || {};

    // Set streaming state (called by sendMessage)
    window.scrollState.setStreamingState = function(state) {
        isStreaming = !!state;

        if (isStreaming) {
            // When streaming begins, DON'T scroll - let MutationObserver handle it
            // when actual content arrives. This preserves scrollUserMessageToTop position.
            hideDownArrow();
        } else {
            // Streaming ended - DON'T remove padding here!
            // Padding keeps user message at top after response completes.
            // Padding is removed by:
            // - scrollUserMessageToTop() when sending next message
            // - resetScrollState() when switching chats
            // - User manually scrolling to bottom (handled in scroll event)
            updateArrowVisibility();
        }
    };

    // Instant scroll to bottom (for first token)
    window.scrollState.scrollToBottomNow = function() {
        // Don't scroll if keeping user message at top
        if (waitingForContent || window._keepMessageAtTop) {
            return;
        }

        isPinnedToBottom = true;
        scrollEl.scrollTop = scrollEl.scrollHeight;
        hideDownArrow();
    };

    // Scroll user message to top of viewport (ChatGPT-style: prompt at top, response fills below)
    window.scrollUserMessageToTop = function(messageElement) {
        if (!messageElement) return;


        // Set ALL flags FIRST - before any DOM changes or scrolling
        isPinnedToBottom = false;
        waitingForContent = true;
        window._keepMessageAtTop = true;
        isProgrammaticScroll = true;

        // Add temporary padding to allow scrolling to top
        const chatDisplay = document.getElementById('chat-display');
        if (chatDisplay) {
            chatDisplay.classList.add('scroll-padding-active');
        }

        // Calculate message position and scroll manually
        // This is more reliable than scrollIntoView which can affect other scroll containers
        const messageRect = messageElement.getBoundingClientRect();
        const scrollRect = scrollEl.getBoundingClientRect();
        const offsetFromTop = messageRect.top - scrollRect.top;
        const targetScrollTop = scrollEl.scrollTop + offsetFromTop - 16; // 16px padding from top

        scrollEl.scrollTop = Math.max(0, targetScrollTop);

        // Reset programmatic flag after scroll settles
        setTimeout(() => {
            isProgrammaticScroll = false;
        }, 200);

        hideDownArrow();
    };

    // Called when first AI content arrives
    window.scrollState.contentArrived = function() {
        // Keep waitingForContent true to preserve user message at top
        // User can scroll manually or focus input to reset
        // Don't enable auto-scroll - ChatGPT-style keeps message at top
    };

    // Set waiting state externally
    window.scrollState.setWaitingForContent = function(state) {
        waitingForContent = !!state;
        if (state) {
            isPinnedToBottom = false; // Don't pin when waiting
        }
    };

    // Force scroll to bottom (for programmatic use)
    window.scrollToBottom = function(force = false, instant = false) {
        if (force) isPinnedToBottom = true;
        if (isPinnedToBottom || force) {
            if (instant) {
                scrollEl.scrollTop = scrollEl.scrollHeight;
            } else {
                scrollToBottom("smooth");
            }
        }
    };

    // Legacy compatibility
    window.setStreamingState = function(state) {
        window.scrollState.setStreamingState(state);
    };

    // Reset scroll state (for chat switches)
    window.resetScrollState = function() {
        isPinnedToBottom = true;
        isStreaming = false;
        waitingForContent = false;
        window._keepMessageAtTop = false;
        if (raf1) cancelAnimationFrame(raf1);
        if (raf2) cancelAnimationFrame(raf2);
        raf1 = null;
        raf2 = null;
        // Remove scroll padding from previous chat
        const chatDisplayEl = document.getElementById('chat-display');
        if (chatDisplayEl) {
            chatDisplayEl.classList.remove('scroll-padding-active');
        }
        hideDownArrow();
        scrollEl.scrollTop = scrollEl.scrollHeight;
    };

    window.hideDownArrow = hideDownArrow;
    window.showDownArrow = showDownArrow;

    // Also expose isPinnedToBottom for debugging
    Object.defineProperty(window.scrollState, 'isPinnedToBottom', {
        get: () => isPinnedToBottom,
        set: (val) => { isPinnedToBottom = val; }
    });

    Object.defineProperty(window.scrollState, 'isStreaming', {
        get: () => isStreaming
    });
}

// Initialize when DOM is ready
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initScrollState);
} else {
    initScrollState();
}
