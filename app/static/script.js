// State Variables

// Utility: Dismiss mobile keyboard reliably
function dismissMobileKeyboard() {
    const isMobile = document.body.classList.contains('device-phone') ||
                    document.body.classList.contains('is-mobile') ||
                    document.body.classList.contains('is-ios') ||
                    document.body.classList.contains('device-tablet');
    if (!isMobile) return;

    // Blur active element
    if (document.activeElement && document.activeElement !== document.body) {
        document.activeElement.blur();
    }

    // iOS workaround: scroll to force keyboard dismissal
    if (document.body.classList.contains('is-ios')) {
        window.scrollTo(0, window.scrollY);
    }
}
// Expose globally
window.dismissMobileKeyboard = dismissMobileKeyboard;
let lastDisplayedMessage = ""; // Tracks the last message displayed in the chat
let lastMessageTimestamp = 0; // Tracks the timestamp of the last message
let isRequestInProgress = false; // Indicates if a request is currently in progress
let isTypingStopped = false; // Indicates if typing has stopped
let isProgrammaticScroll = false; // Tracks if the scroll was initiated programmatically
let isFirstMessage = true; // Tracks if it is the first message in the chat
let isFirstPrompt = true; // Tracks if it is the first prompt
let isUserScrolling = false; // Tracks if the user is manually scrolling
let shouldAutoScroll = true; // Determines if the chat should auto-scroll to the bottom
let lastScrollTop = 0; // Stores the last scroll position of the chat box
let isFetching = false;
function isImageFile(file) {
    if (!file) return false;
    const mime = (file.type || '').toLowerCase();
    if (mime.startsWith('image/')) return true;
    const name = file.name || '';
    return /\.(png|jpe?g|gif|webp|bmp|svg)$/i.test(name);
}

const attachmentStore = (() => {
    let pending = [];

    const generateId = () => {
        if (typeof crypto !== 'undefined' && crypto.randomUUID) {
            return crypto.randomUUID();
        }
        return `att-${Date.now()}-${Math.random().toString(16).slice(2)}`;
    };

    return {
        add(file) {
            // Create a deferred promise for upload completion
            let resolveUpload;
            const uploadPromise = new Promise(resolve => { resolveUpload = resolve; });

            const attachment = {
                id: generateId(),
                file,
                name: file.name,
                type: file.type || '',
                isImage: isImageFile(file),
                previewUrl: isImageFile(file) ? null : URL.createObjectURL(file),
                dataUrl: null,
                extractedText: null,
                uploadPromise,
                resolveUpload
            };
            if (attachment.isImage) {
                const reader = new FileReader();
                reader.onload = () => {
                    attachment.dataUrl = reader.result;
                    updateComposerPreviewSrc(attachment.id, attachment.dataUrl);
                };
                reader.readAsDataURL(file);
            }
            pending.push(attachment);
            return attachment;
        },
        remove(id) {
            const index = pending.findIndex(item => item.id === id);
            if (index !== -1) {
                const [removed] = pending.splice(index, 1);
                if (removed?.previewUrl) {
                    URL.revokeObjectURL(removed.previewUrl);
                }
            }
        },
        async consumeForSending(toDataUrl) {
            const attachments = [];

            // Wait for all uploads to complete before processing
            const uploadPromises = pending
                .filter(item => item.uploadPromise)
                .map(item => item.uploadPromise);
            if (uploadPromises.length > 0) {
                console.log(`Waiting for ${uploadPromises.length} upload(s) to complete...`);
                await Promise.all(uploadPromises);
                console.log('All uploads complete');
            }

            for (const item of pending) {
                let finalDataUrl = item.dataUrl;
                if (item.isImage && !finalDataUrl && item.file && typeof toDataUrl === 'function') {
                    try {
                        finalDataUrl = await toDataUrl(item.file);
                    } catch (err) {
                        console.error('Failed to convert image to dataUrl', err);
                    }
                }

                attachments.push({
                    id: item.id,
                    name: item.name,
                    type: item.type,
                    isImage: item.isImage,
                    dataUrl: finalDataUrl || null,
                    previewUrl: item.previewUrl,
                    // Keep raw File for multipart upload (vision API)
                    file: item.isImage ? item.file : undefined,
                    extractedText: item.extractedText || null,
                    hasExtractedText: !!item.extractedText,
                    extractedTextLength: item.extractedText ? item.extractedText.length : 0
                });
            }

            // Revoke preview URLs once we've created stable data URLs
            pending.forEach(item => {
                if (item?.previewUrl) {
                    URL.revokeObjectURL(item.previewUrl);
                }
            });
            pending = [];
            return attachments;
        },
        getAll() {
            return pending.slice();
        },
        clearAll() {
            pending.forEach(item => {
                if (item?.previewUrl) {
                    URL.revokeObjectURL(item.previewUrl);
                }
            });
            pending = [];
        }
    };
})();

window.attachmentStore = attachmentStore;

function updateTrayVisibility(tray) {
    if (!tray) return;
    const hasAttachments = !!tray.querySelector('.file-preview-container');
    tray.classList.toggle('has-attachments', hasAttachments);
    tray.style.display = hasAttachments ? 'flex' : 'none';
}

function clearAttachmentPreviews() {
    document.querySelectorAll('.file-preview-container').forEach(container => container.remove());
    updateTrayVisibility(document.getElementById('attachment-preview-tray'));
}

window.clearAttachmentPreviews = clearAttachmentPreviews;

function updateComposerPreviewSrc(attachmentId, src) {
    if (!src) return;
    const container = document.querySelector(`.file-preview-container[data-attachment-id="${attachmentId}"]`);
    if (!container) return;
    const img = container.querySelector('img');
    if (img) {
        img.src = src;
    }
}

function readFileAsDataURL(file) {
    return new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(reader.result);
        reader.onerror = () => reject(reader.error || new Error('Failed to read file'));
        reader.readAsDataURL(file);
    });
}

async function getPendingAttachmentsForSend() {
    const attachments = await attachmentStore.consumeForSending(readFileAsDataURL);
    if (attachments.length) {
        clearAttachmentPreviews();
    }
    return attachments;
}

window.getPendingAttachmentsForSend = getPendingAttachmentsForSend;

function clearAttachedFiles() {
    attachmentStore.clearAll();
    clearAttachmentPreviews();
}

window.clearAttachedFiles = clearAttachedFiles;


// Function to get CSRF token from meta tag
function getCSRFToken() {
    return document.querySelector('meta[name="csrf-token"]').getAttribute('content');
}

// Global state for scroll behavior
const scrollState = {
    shouldAutoScroll: true,
    isScrolling: false,
    scrollTimeout: null,
    thresholdDistance: 100,
    animation: null,
    previousScrollPosition: 0  // Track previous scroll position for direction detection
};

let currentScrollContainer = null;
let scrollListenerTarget = null;

function isScrollableElement(element) {
    if (!element) return false;
    const styles = window.getComputedStyle(element);
    const overflowY = styles ? styles.overflowY : 'visible';
    const canScroll = overflowY === 'auto' || overflowY === 'scroll' || overflowY === 'overlay';
    return canScroll && (element.scrollHeight - element.clientHeight > 2);
}

function resolvePreferredScrollContainer() {
    const scrollRegion = document.getElementById('chat-scroll-region');
    if (isScrollableElement(scrollRegion)) {
        return scrollRegion;
    }

    const chatBox = document.getElementById('chatbox');
    if (isScrollableElement(chatBox)) {
        return chatBox;
    }

    return document.scrollingElement || document.documentElement;
}

function getScrollContainer() {
    if (currentScrollContainer && document.body.contains(currentScrollContainer)) {
        if (isScrollableElement(currentScrollContainer) || currentScrollContainer === document.body || currentScrollContainer === document.documentElement) {
            return currentScrollContainer;
        }
    }
    currentScrollContainer = resolvePreferredScrollContainer();
    return currentScrollContainer;
}

function ensureScrollContainer(force = false) {
    const container = resolvePreferredScrollContainer();
    if (!container) return null;

    const root = document.scrollingElement || document.documentElement;
    const isDocumentScroller = container === root || container === document.documentElement || container === document.body;
    const target = isDocumentScroller ? window : container;

    if (!force && currentScrollContainer === container && scrollListenerTarget === target) {
        return currentScrollContainer;
    }

    if (scrollListenerTarget) {
        scrollListenerTarget.removeEventListener('scroll', handleScroll);
    }

    currentScrollContainer = container;
    scrollListenerTarget = target;
    scrollListenerTarget.addEventListener('scroll', handleScroll, { passive: true });

    return currentScrollContainer;
}

function getScrollMetrics(scroller) {
    const root = document.scrollingElement || document.documentElement;
    const isDocumentScroller = scroller === root || scroller === document.documentElement || scroller === document.body;
    const scrollTop = isDocumentScroller ? (window.pageYOffset || root.scrollTop || 0) : scroller.scrollTop;
    const viewportHeight = isDocumentScroller ? window.innerHeight : scroller.clientHeight;
    return { isDocumentScroller, scrollTop, viewportHeight };
}

const conversationStateController = {
    markConversationNew() {
        isFirstMessage = true;
    },
    markConversationHasHistory() {
        isFirstMessage = false;
    },
    isFreshConversation() {
        return isFirstMessage;
    }
};

if (typeof window !== 'undefined') {
    // Merge with existing scrollState from scrollState.js (preserves isStreaming etc.)
    if (window.scrollState) {
        Object.assign(window.scrollState, scrollState);
    } else {
        window.scrollState = scrollState;
    }
    window.conversationStateController = conversationStateController;
}

// Smoothly scroll to a target position with optional interrupt on click
function smoothScrollTo(element, target, duration) {
    const root = document.scrollingElement || document.documentElement;
    const isDocumentScroller = element === root || element === document.documentElement || element === document.body;

    target = Math.round(target);
    duration = Math.round(duration);

    if (duration < 0) {
        return Promise.reject("Invalid duration");
    }
    if (duration === 0) {
        if (isDocumentScroller) {
            window.scrollTo(0, target);
        } else {
            element.scrollTop = target;
        }
        return Promise.resolve();
    }

    const start = isDocumentScroller ? (window.pageYOffset || root.scrollTop || 0) : element.scrollTop;
    const change = target - start;
    const startTime = performance.now();

    let isInterrupted = false; // Flag to indicate if scrolling should be interrupted
    let stopScrolling = null;

    if (!isDocumentScroller) {
        stopScrolling = () => {
            isInterrupted = true;
            element.removeEventListener('click', stopScrolling);
        };
        element.addEventListener('click', stopScrolling);
    }

    // Ease-in-out function for smooth scroll animation
    function easeInOutQuad(t) {
        return t < 0.5 ? 2 * t * t : -1 + (4 - 2 * t) * t;
    }

    // Animate scroll position
    return new Promise(resolve => {
        function animateScroll(currentTime) {
            if (isInterrupted) {
                if (isDocumentScroller) {
                    window.scrollTo(0, window.pageYOffset || root.scrollTop || 0);
                } else {
                    element.scrollTop = element.scrollTop; // Lock in the current scroll position
                }
                if (stopScrolling) {
                    element.removeEventListener('click', stopScrolling);
                }
                resolve();
                return;
            }

            const elapsed = currentTime - startTime;
            const progress = Math.min(elapsed / duration, 1);
            const nextScrollPosition = start + change * easeInOutQuad(progress);

            if (isDocumentScroller) {
                window.scrollTo(0, nextScrollPosition);
            } else {
                element.scrollTop = nextScrollPosition;
            }

            if (progress < 1) {
                requestAnimationFrame(animateScroll);
            } else {
                if (isDocumentScroller) {
                    window.scrollTo(0, target);
                } else {
                    element.scrollTop = target;
                }
                if (stopScrolling) {
                    element.removeEventListener('click', stopScrolling);
                }
                resolve();
            }
        }

        requestAnimationFrame(animateScroll);
    });
}



// Function to scroll to the bottom of the chat box
function scrollToBottom(force = false, isDownArrowClick = false) {
    const scroller = ensureScrollContainer();
    if (!scroller) {
        console.warn('No scroll container available');
        return;
    }

    const { isDocumentScroller, scrollTop, viewportHeight } = getScrollMetrics(scroller);

    if (isDownArrowClick) {
        scrollState.isScrolling = false;
    }

    const scrollPosition = scrollTop + viewportHeight;
    const distanceFromBottom = scroller.scrollHeight - scrollPosition;
    const isNearBottom = distanceFromBottom < 5;
    const shouldScroll = force || scrollState.shouldAutoScroll || isDownArrowClick;
    
    if (shouldScroll && !scrollState.isScrolling) {
        scrollState.isScrolling = true;

        const performScroll = (behavior) => {
            if (isDocumentScroller) {
                window.scrollTo({ top: scroller.scrollHeight, behavior });
            } else {
                scroller.scrollTo({ top: scroller.scrollHeight, behavior });
            }
        };

        if (isDownArrowClick) {
            // 800ms smooth scroll, hide arrow when done
            smoothScrollTo(scroller, scroller.scrollHeight, 800).finally(() => {
                scrollState.isScrolling = false;
                scrollState.shouldAutoScroll = true;
                const updatedMetrics = getScrollMetrics(scroller);
                scrollState.previousScrollPosition = updatedMetrics.scrollTop;
                hideDownArrow();
            });
        } else {
            performScroll(isNearBottom ? 'auto' : 'smooth');

            setTimeout(() => {
                scrollState.isScrolling = false;
                const metrics = getScrollMetrics(scroller);
                const finalPosition = metrics.scrollTop + metrics.viewportHeight;
                const finalDistance = scroller.scrollHeight - finalPosition;
                scrollState.shouldAutoScroll = finalDistance < 5 || force;
                scrollState.previousScrollPosition = metrics.scrollTop;
                
                if (scrollState.shouldAutoScroll) {
                    hideDownArrow();
                }
            }, 300);
        }
    }
}


// Scroll event handler with direction detection
function handleScroll() {
    const scroller = currentScrollContainer || ensureScrollContainer();
    if (!scroller) return;

    const metrics = getScrollMetrics(scroller);
    const currentPos = metrics.scrollTop;
    const maxScroll = scroller.scrollHeight - metrics.viewportHeight;
    const atBottom = currentPos >= maxScroll - 50;
    const hasScroll = maxScroll > 5;

    // Detect scroll direction
    const scrollingUp = currentPos < scrollState.previousScrollPosition;

    // Update state
    scrollState.shouldAutoScroll = atBottom;

    // Arrow logic: show only when scrolling UP, not at bottom, and has scrollbar
    if (atBottom || !hasScroll) {
        hideDownArrow();
    } else if (scrollingUp) {
        showDownArrow();
    }

    // Store position for next comparison
    scrollState.previousScrollPosition = currentPos;
}

// Initialize chat scrolling functionality
function initializeChatScroll() {
    // If scrollState.js is handling scroll, skip duplicate initialization
    if (window._scrollStateInitialized) {
        console.log('[Scroll Init] scrollState.js handles scroll - skipping');
        return () => {};
    }

    const downArrow = document.getElementById('downArrow');
    const scroller = ensureScrollContainer(true) || document.scrollingElement || document.documentElement;

    console.log('[Scroll Init] scroller:', !!scroller, 'downArrow:', !!downArrow);

    // Initial state: arrow hidden, track position
    if (downArrow) {
        downArrow.style.display = 'none';
    }
    const metrics = getScrollMetrics(scroller);
    scrollState.previousScrollPosition = metrics.scrollTop;

    const observer = observeNewMessages();
    monitorUserScrolling();
    setupDownArrow();

    console.log('[Scroll Init] Complete, initial pos:', metrics.scrollTop);

    return () => {
        if (observer) observer.disconnect();
        if (scrollListenerTarget) {
            scrollListenerTarget.removeEventListener('scroll', handleScroll);
            scrollListenerTarget = null;
        }
        window.removeEventListener('resize', handleViewportChange);
        window.removeEventListener('orientationchange', handleViewportChange);
    };
}

// Function to check if user is at bottom of chat
function isAtBottom(scroller = getScrollContainer()) {
    if (!scroller) return true;
    const metrics = getScrollMetrics(scroller);
    const scrollPosition = Math.ceil(metrics.scrollTop + metrics.viewportHeight);
    return scrollPosition >= scroller.scrollHeight - 10;
}


// Show the down arrow
function showDownArrow() {
    const downArrow = document.getElementById('downArrow');
    if (!downArrow) return;
    downArrow.style.display = 'flex';
}


// Function to hide down arrow
function hideDownArrow() {
    const downArrow = document.getElementById('downArrow');
    if (!downArrow) return;
    downArrow.style.display = 'none';
}


// Reset scroll state (call when switching chats)
function resetScrollState() {
    const scroller = ensureScrollContainer(true) || document.scrollingElement || document.documentElement;

    // Cancel any ongoing animation
    if (scrollState.animation) {
        cancelAnimationFrame(scrollState.animation);
        scrollState.animation = null;
    }
    scrollState.isScrolling = false;

    // Hide arrow
    hideDownArrow();

    // Scroll to bottom after layout settles (2 frames for markdown/images)
    requestAnimationFrame(() => {
        scroller.scrollTop = scroller.scrollHeight;
        requestAnimationFrame(() => {
            scroller.scrollTop = scroller.scrollHeight;
            // Reset position tracking
            const metrics = getScrollMetrics(scroller);
            scrollState.previousScrollPosition = metrics.scrollTop;
            scrollState.shouldAutoScroll = true;
            console.log('[resetScrollState] Scrolled to bottom, pos:', metrics.scrollTop);
        });
    });
}

// Expose scroll functions globally
window.resetScrollState = resetScrollState;
window.hideDownArrow = hideDownArrow;
window.showDownArrow = showDownArrow;

// Initialize when DOM is ready
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initializeChatScroll);
} else {
    initializeChatScroll();
}



// Monitor user's manual scrolling
function handleViewportChange() {
    ensureScrollContainer(true);
}

function monitorUserScrolling() {
    ensureScrollContainer(true);
    window.addEventListener('resize', handleViewportChange);
    window.addEventListener('orientationchange', handleViewportChange);
    console.log('[Scroll] Monitoring viewport changes for scroll container updates');
}



// Observe new messages being added to the chat box
// NOTE: scrollState.js now handles this with ChatGPT-style bottom pinning
// This function is kept for backwards compatibility but defers to scrollState.js
function observeNewMessages() {
    // If scrollState.js is loaded and has setStreamingState, it handles scrolling
    if (window.scrollState?.setStreamingState) {
        console.log('[script.js] scrollState.js handles scroll - skipping duplicate observer');
        return null;
    }

    // Fallback for cases where scrollState.js isn't loaded
    const chatBox = document.getElementById('chatbox');
    if (!chatBox) {
        console.warn('Chat box element not found');
        return null;
    }

    const observer = new MutationObserver((mutations) => {
        const hasContentChanged = mutations.some(mutation =>
            mutation.addedNodes.length > 0 || mutation.removedNodes.length > 0
        );

        if (hasContentChanged && scrollState.shouldAutoScroll) {
            requestAnimationFrame(() => scrollToBottom());
        }
    });

    observer.observe(chatBox, {
        childList: true,
        subtree: true,
        characterData: true
    });

    return observer;
}

// Set up down arrow click handler
function setupDownArrow() {
    const downArrow = document.getElementById('downArrow');
    if (!downArrow) {
        console.warn('Down arrow element not found');
        return;
    }

    const newDownArrow = downArrow.cloneNode(true);
    downArrow.parentNode.replaceChild(newDownArrow, downArrow);
    
    newDownArrow.addEventListener('click', () => {
        // Reset scroll state for down arrow clicks
        scrollState.isScrolling = false;
        scrollToBottom(true, true);
    });
}


// ─── Pending image-generation recovery (refresh-during-generation) ───
// When a user kicks off a /image and refreshes before the stream returns,
// the client-side placeholder ID is lost. These helpers persist a minimal
// "pending" marker to localStorage so the session loader can detect the
// situation on reload and do one delayed re-fetch to pick up the completed
// image the backend wrote to Firestore in the meantime.
const _PENDING_IMG_KEY = 'xs:pendingImage:';
const _PENDING_IMG_MAX_AGE_MS = 5 * 60 * 1000; // 5 minutes

function recordPendingImage(placeholderId, prompt, sessionId) {
    if (!placeholderId || typeof localStorage === 'undefined') return;
    try {
        localStorage.setItem(
            _PENDING_IMG_KEY + placeholderId,
            JSON.stringify({
                placeholderId,
                prompt: (prompt || '').slice(0, 500),
                sessionId: sessionId || null,
                createdAt: Date.now(),
            })
        );
    } catch (e) { /* quota / private mode — skip */ }
}

function clearPendingImage(placeholderId) {
    if (!placeholderId || typeof localStorage === 'undefined') return;
    try { localStorage.removeItem(_PENDING_IMG_KEY + placeholderId); } catch (e) { /* skip */ }
}

// Return all pending entries for `sessionId` that are still within the
// recovery window. Stale entries are garbage-collected in the same scan.
function listRecoverablePendingImages(sessionId) {
    if (typeof localStorage === 'undefined') return [];
    const out = [];
    const now = Date.now();
    const dropKeys = [];
    for (let i = 0; i < localStorage.length; i += 1) {
        const key = localStorage.key(i);
        if (!key || !key.startsWith(_PENDING_IMG_KEY)) continue;
        try {
            const entry = JSON.parse(localStorage.getItem(key) || 'null');
            if (!entry || !entry.createdAt) { dropKeys.push(key); continue; }
            if (now - entry.createdAt > _PENDING_IMG_MAX_AGE_MS) {
                dropKeys.push(key);
                continue;
            }
            if (sessionId && entry.sessionId && entry.sessionId !== sessionId) continue;
            out.push(entry);
        } catch (e) {
            dropKeys.push(key);
        }
    }
    dropKeys.forEach((k) => { try { localStorage.removeItem(k); } catch (e) {} });
    return out;
}

// ─── ChatGPT-style copy toast ────────────────────────────────────────
// A small pill-shaped confirmation that slides up from the bottom of the
// viewport, holds for ~1.8 s, then fades away. One singleton element,
// reused across calls so rapid consecutive copies just refresh the timer.
let _copyToastEl = null;
let _copyToastTimer = null;

function showCopyToast(message) {
    if (!_copyToastEl) {
        _copyToastEl = document.createElement('div');
        _copyToastEl.className = 'copy-toast';
        _copyToastEl.setAttribute('role', 'status');
        _copyToastEl.setAttribute('aria-live', 'polite');
        _copyToastEl.innerHTML = `
            <i class="fas fa-check copy-toast__icon" aria-hidden="true"></i>
            <span class="copy-toast__text"></span>
        `;
        document.body.appendChild(_copyToastEl);
    }
    const textEl = _copyToastEl.querySelector('.copy-toast__text');
    if (textEl) textEl.textContent = message || 'Copied';

    // Restart the show-animation cleanly if already visible.
    _copyToastEl.classList.remove('is-visible');
    // Force a reflow so the next class add retriggers the transition.
    // eslint-disable-next-line no-unused-expressions
    _copyToastEl.offsetWidth;
    _copyToastEl.classList.add('is-visible');

    if (_copyToastTimer) clearTimeout(_copyToastTimer);
    _copyToastTimer = setTimeout(() => {
        _copyToastEl?.classList.remove('is-visible');
    }, 1800);
}

// ─── Share modal ──────────────────────────────────────────────────────
// Vanilla-JS equivalent of the ChatGPT-style share sheet. Opens a centered
// dialog with a preview of the image and a row of channel buttons:
// Copy link / X / LinkedIn / Reddit / Download. Supports ESC to close,
// backdrop click to close, focus trap while open, and navigator.share
// on mobile as a progressive enhancement.
//
// Security notes:
//  - All window.open calls for third-party intents use noopener,noreferrer.
//    Without noopener, the opened tab could navigate us via window.opener.
//  - navigator.clipboard.writeText wrapped in try/catch — fails silently
//    in insecure contexts (non-HTTPS) and in the rare case that the page
//    isn't focused.
let _shareModalEl = null;
let _shareModalFocusRestoreEl = null;
let _shareModalState = { imageUrl: '', title: '', downloadName: 'image' };

function _ensureShareModal() {
    if (_shareModalEl) return _shareModalEl;

    const root = document.createElement('div');
    root.className = 'share-modal';
    root.setAttribute('role', 'dialog');
    root.setAttribute('aria-modal', 'true');
    root.setAttribute('aria-labelledby', 'share-modal-title');
    root.setAttribute('aria-hidden', 'true');
    root.innerHTML = `
        <div class="share-modal__scrim" data-role="scrim"></div>
        <div class="share-modal__dialog" tabindex="-1" role="document">
            <div class="share-modal__header">
                <h2 class="share-modal__title" id="share-modal-title"></h2>
                <button type="button" class="share-modal__close" aria-label="Close share dialog" data-role="close">
                    <i class="fas fa-xmark" aria-hidden="true"></i>
                </button>
            </div>
            <div class="share-modal__divider"></div>
            <div class="share-modal__preview">
                <img class="share-modal__image" alt="" />
            </div>
            <ul class="share-modal__channels">
                <li class="share-modal__channel">
                    <button type="button" class="share-modal__btn" data-action="copy" data-brand="copy" aria-label="Copy link">
                        <i class="fas fa-link" aria-hidden="true"></i>
                    </button>
                    <span class="share-modal__label">Copy link</span>
                </li>
                <li class="share-modal__channel">
                    <button type="button" class="share-modal__btn" data-action="twitter" data-brand="twitter" aria-label="Share on X">
                        <svg viewBox="0 0 24 24" class="share-modal__brand" aria-hidden="true">
                            <path d="M18.244 2.25h3.308l-7.227 8.26 8.502 11.24H16.17l-5.214-6.817L4.99 21.75H1.68l7.73-8.835L1.254 2.25H8.08l4.713 6.231zm-1.161 17.52h1.833L7.084 4.126H5.117z"/>
                        </svg>
                    </button>
                    <span class="share-modal__label">X</span>
                </li>
                <li class="share-modal__channel">
                    <button type="button" class="share-modal__btn" data-action="linkedin" data-brand="linkedin" aria-label="Share on LinkedIn">
                        <svg viewBox="0 0 24 24" class="share-modal__brand" aria-hidden="true">
                            <path d="M20.447 20.452h-3.554v-5.569c0-1.328-.027-3.037-1.852-3.037-1.853 0-2.136 1.445-2.136 2.939v5.667H9.351V9h3.414v1.561h.046c.477-.9 1.637-1.852 3.37-1.852 3.601 0 4.267 2.37 4.267 5.455v6.288zM5.337 7.433a2.062 2.062 0 01-2.063-2.065 2.063 2.063 0 112.063 2.065zm1.782 13.019H3.555V9h3.564v11.452z"/>
                        </svg>
                    </button>
                    <span class="share-modal__label">LinkedIn</span>
                </li>
                <li class="share-modal__channel">
                    <button type="button" class="share-modal__btn" data-action="reddit" data-brand="reddit" aria-label="Share on Reddit">
                        <svg viewBox="0 0 24 24" class="share-modal__brand" aria-hidden="true">
                            <path d="M12 0A12 12 0 000 12a12 12 0 0012 12 12 12 0 0012-12A12 12 0 0012 0zm5.01 4.744c.688 0 1.25.561 1.25 1.249a1.25 1.25 0 01-2.498.056l-2.597-.547-.8 3.747c1.824.07 3.48.632 4.674 1.488.308-.309.73-.491 1.207-.491.968 0 1.754.786 1.754 1.754 0 .716-.435 1.333-1.01 1.614a3.111 3.111 0 01.042.52c0 2.694-3.13 4.87-7.004 4.87-3.874 0-7.004-2.176-7.004-4.87 0-.183.015-.366.043-.534A1.748 1.748 0 014.028 12c0-.968.786-1.754 1.754-1.754.463 0 .898.196 1.207.49 1.207-.883 2.878-1.43 4.744-1.487l.885-4.182a.342.342 0 01.14-.197.35.35 0 01.238-.042l2.906.617a1.214 1.214 0 011.108-.701zM9.25 12C8.561 12 8 12.562 8 13.25c0 .687.561 1.248 1.25 1.248.687 0 1.248-.561 1.248-1.249 0-.688-.561-1.249-1.249-1.249zm5.5 0c-.687 0-1.248.561-1.248 1.25 0 .687.561 1.248 1.249 1.248.688 0 1.249-.561 1.249-1.249 0-.687-.562-1.249-1.25-1.249zm-5.466 3.99a.327.327 0 00-.231.094.33.33 0 000 .463c.842.842 2.484.913 2.961.913.477 0 2.105-.056 2.961-.913a.361.361 0 00.029-.463.33.33 0 00-.464 0c-.547.533-1.684.73-2.512.73-.828 0-1.979-.196-2.512-.73a.326.326 0 00-.232-.095z"/>
                        </svg>
                    </button>
                    <span class="share-modal__label">Reddit</span>
                </li>
                <li class="share-modal__channel">
                    <button type="button" class="share-modal__btn" data-action="download" data-brand="download" aria-label="Download image">
                        <i class="fas fa-download" aria-hidden="true"></i>
                    </button>
                    <span class="share-modal__label">Download</span>
                </li>
            </ul>
        </div>
    `;
    document.body.appendChild(root);

    const dialog = root.querySelector('.share-modal__dialog');
    const scrim = root.querySelector('[data-role="scrim"]');
    const closeBtn = root.querySelector('[data-role="close"]');
    const imgEl = root.querySelector('.share-modal__image');
    const titleEl = root.querySelector('.share-modal__title');
    const buttons = root.querySelectorAll('.share-modal__btn');

    // Close handlers
    scrim.addEventListener('click', closeShareModal);
    closeBtn.addEventListener('click', closeShareModal);
    document.addEventListener('keydown', _handleShareModalKey);

    // Action handlers
    const openIntent = (url) => {
        window.open(url, '_blank', 'noopener,noreferrer,width=600,height=600');
    };

    const toastOrLog = (msg) => {
        if (typeof showCopyToast === 'function') {
            showCopyToast(msg);
        } else if (window.Toast && typeof window.Toast.info === 'function') {
            window.Toast.info(msg, 2000);
        } else {
            console.log('[share]', msg);
        }
    };

    // Resolve a URL that might be relative (e.g. "/uploads/...") into a
    // fully-qualified absolute URL. Required for third-party share targets
    // (Reddit, X, LinkedIn) which need a real http(s):// link — a relative
    // path would fail their URL parser or silently attach to their own
    // domain. Also used for clipboard copy so the copied link actually
    // works when pasted elsewhere.
    const absolutizeUrl = (url) => {
        if (!url) return '';
        // Already absolute — return as-is (covers http://, https://, data:, blob:).
        if (/^(?:[a-z][a-z0-9+.-]*:|\/\/)/i.test(url)) return url;
        try {
            return new URL(url, window.location.origin).href;
        } catch {
            return url;
        }
    };

    const actions = {
        copy: async () => {
            const url = absolutizeUrl(_shareModalState.imageUrl);
            if (!url) return;
            try {
                if (navigator.clipboard?.writeText) {
                    await navigator.clipboard.writeText(url);
                } else {
                    const ta = document.createElement('textarea');
                    ta.value = url;
                    document.body.appendChild(ta);
                    ta.select();
                    document.execCommand('copy');
                    document.body.removeChild(ta);
                }
                toastOrLog('Link copied');
            } catch (err) {
                toastOrLog('Copy failed');
            }
        },
        twitter: () => {
            const u = encodeURIComponent(absolutizeUrl(_shareModalState.imageUrl));
            const t = encodeURIComponent(_shareModalState.title || '');
            openIntent(`https://twitter.com/intent/tweet?url=${u}&text=${t}`);
        },
        linkedin: () => {
            const u = encodeURIComponent(absolutizeUrl(_shareModalState.imageUrl));
            openIntent(`https://www.linkedin.com/sharing/share-offsite/?url=${u}`);
        },
        reddit: () => {
            const u = encodeURIComponent(absolutizeUrl(_shareModalState.imageUrl));
            const t = encodeURIComponent(_shareModalState.title || '');
            openIntent(`https://www.reddit.com/submit?url=${u}&title=${t}`);
        },
        download: () => {
            // Download can keep the relative URL since it's same-origin —
            // but normalizing here too means if the caller ever passes a
            // CDN URL we don't silently misroute it.
            const a = document.createElement('a');
            a.href = absolutizeUrl(_shareModalState.imageUrl);
            a.download = _shareModalState.downloadName || 'image';
            a.rel = 'noopener';
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
        },
    };

    buttons.forEach((btn) => {
        btn.addEventListener('click', (e) => {
            e.stopPropagation();
            const action = btn.getAttribute('data-action');
            if (actions[action]) actions[action]();
        });
    });

    _shareModalEl = { root, dialog, imgEl, titleEl };
    return _shareModalEl;
}

function _handleShareModalKey(e) {
    if (!_shareModalEl) return;
    const { root, dialog } = _shareModalEl;
    if (root.getAttribute('aria-hidden') === 'true') return;

    if (e.key === 'Escape') {
        closeShareModal();
        return;
    }
    // Light focus trap: keep Tab inside the dialog.
    if (e.key === 'Tab') {
        const focusables = dialog.querySelectorAll(
            'button:not([disabled]), a[href], [tabindex]:not([tabindex="-1"])'
        );
        const list = Array.from(focusables);
        if (!list.length) return;
        const first = list[0];
        const last = list[list.length - 1];
        if (e.shiftKey && document.activeElement === first) {
            e.preventDefault();
            last.focus();
        } else if (!e.shiftKey && document.activeElement === last) {
            e.preventDefault();
            first.focus();
        }
    }
}

function openShareModal({ imageUrl, title, downloadName } = {}) {
    if (!imageUrl) return;
    const { root, dialog, imgEl, titleEl } = _ensureShareModal();
    _shareModalState = {
        imageUrl,
        title: title || 'Untitled image',
        downloadName: downloadName || 'image',
    };
    titleEl.textContent = _shareModalState.title;
    imgEl.src = imageUrl;
    imgEl.alt = _shareModalState.title;

    _shareModalFocusRestoreEl = document.activeElement;
    root.setAttribute('aria-hidden', 'false');
    root.classList.add('open');
    // Lock body scroll so the scrim doesn't leak.
    document.body.classList.add('share-modal-open');
    requestAnimationFrame(() => {
        dialog.focus({ preventScroll: true });
    });
}

function closeShareModal() {
    if (!_shareModalEl) return;
    const { root } = _shareModalEl;
    root.classList.remove('open');
    root.setAttribute('aria-hidden', 'true');
    document.body.classList.remove('share-modal-open');
    if (_shareModalFocusRestoreEl && typeof _shareModalFocusRestoreEl.focus === 'function') {
        try { _shareModalFocusRestoreEl.focus(); } catch { /* node gone */ }
    }
    _shareModalFocusRestoreEl = null;
}

// Render a ChatGPT-style failure card when an image generation request
// comes back empty. `errorType` (when the backend eventually sends one)
// maps to a specific title/hint so users get actionable guidance instead
// of "something went wrong". Clicking "Try again" repopulates the composer.
const _IMAGE_ERROR_COPY = {
    rate_limit: {
        title: 'Rate limit hit',
        hint: 'The image provider is throttling requests. Wait a moment and try again.',
    },
    quota_exhausted: {
        title: 'Daily image limit reached',
        hint: "You've hit today's image generation quota. Try again tomorrow, or add your own API key in Settings.",
    },
    missing_key: {
        title: 'No image provider configured',
        hint: 'Add an OpenRouter or OpenAI API key in Settings to generate images.',
    },
    network: {
        title: 'Connection issue',
        hint: 'The image provider is unreachable. Check your connection and retry.',
    },
    content_policy: {
        title: 'Prompt blocked',
        hint: 'The provider refused this prompt for content-policy reasons. Try rephrasing.',
    },
    generation_failed: {
        title: "Couldn't generate image",
        hint: 'The provider returned no image. Try a different prompt or retry.',
    },
};

function renderImageFailureCard(container, prompt, errorType) {
    if (!container) return;
    const copy = _IMAGE_ERROR_COPY[errorType] || _IMAGE_ERROR_COPY.generation_failed;
    const card = document.createElement('div');
    card.className = 'image-failure-card';
    card.dataset.errorType = errorType || 'generation_failed';
    card.setAttribute('role', 'alert');
    card.innerHTML = `
        <div class="image-failure-card__icon" aria-hidden="true">
            <i class="fas fa-image"></i>
        </div>
        <div class="image-failure-card__body">
            <div class="image-failure-card__title"></div>
            <div class="image-failure-card__hint"></div>
        </div>
        <div class="image-failure-card__actions">
            <button type="button" class="image-failure-card__btn image-failure-card__btn--primary" data-action="retry">
                <i class="fas fa-rotate-right" aria-hidden="true"></i> Try again
            </button>
        </div>
    `;
    card.querySelector('.image-failure-card__title').textContent = copy.title;
    card.querySelector('.image-failure-card__hint').textContent = copy.hint;
    const retryBtn = card.querySelector('[data-action="retry"]');
    if (retryBtn) {
        retryBtn.addEventListener('click', () => {
            const promptEl = document.getElementById('prompt');
            if (promptEl && prompt) {
                promptEl.value = prompt;
                promptEl.dispatchEvent(new Event('input'));
                promptEl.focus();
            }
        });
    }
    container.appendChild(card);
}

// Wire a loaded <img> so if it fails mid-session (404, CDN hiccup, etc.) we
// swap to a compact broken-image card instead of a blank rectangle. Also
// reveals any ancestor `.image-layer` so its shimmer placeholder stops
// animating — without this, a 404'd image would leave the generation
// shimmer running forever beneath the error card.
function wireBrokenImageFallback(img, name) {
    if (!img) return;
    img.addEventListener('error', () => {
        const parent = img.parentElement;
        if (!parent) return;

        const card = document.createElement('div');
        card.className = 'broken-image-card';
        card.setAttribute('role', 'img');
        card.setAttribute('aria-label', `Image failed to load${name ? ': ' + name : ''}`);
        card.innerHTML = `
            <i class="fas fa-image-slash" aria-hidden="true"></i>
            <div class="broken-image-card__label">
                <div class="broken-image-card__title">Image failed to load</div>
                ${name ? `<div class="broken-image-card__name">${String(name).replace(/[<>&"']/g, (c) => ({'<':'&lt;','>':'&gt;','&':'&amp;','"':'&quot;',"'":'&#39;'}[c]))}</div>` : ''}
            </div>
        `;
        parent.replaceChild(card, img);

        // Climb to the enclosing .image-layer and kill the shimmer. Without
        // this the `.image-placeholder` keeps its animation even though the
        // image slot is now showing an error card.
        const layer = parent.closest('.image-layer');
        if (layer) {
            layer.classList.add('revealed', 'loaded', 'failed');
            const placeholder = layer.querySelector('.image-placeholder');
            if (placeholder) placeholder.style.opacity = '0';
        }
    }, { once: true });
}

// Format an alt-text string for a generated image. Prefixes with
// "AI-generated image: " so screen readers announce context, and caps the
// prompt at a readable length so multi-paragraph prompts don't get read
// verbatim. Non-generated images (user uploads) use a shorter pattern.
function formatImageAlt(text, { generated = true } = {}) {
    const raw = (text || '').replace(/\s+/g, ' ').trim();
    if (!raw) return generated ? 'AI-generated image' : 'Image attachment';
    const cap = 140;
    const trimmed = raw.length > cap ? raw.slice(0, cap).trimEnd() + '…' : raw;
    return generated ? `AI-generated image: ${trimmed}` : trimmed;
}

// Build one `.image-layer` containing a faded-out placeholder and the
// loaded image. Pure — does not append anywhere. Matches the DOM shape
// that the live stream (`replacePlaceholderWithImage`) produces so replay
// and stream render identically. No badge, no "Tap to enlarge" caption
// (ChatGPT-clean: image speaks for itself).
function buildImageLayer(imageUrl, altText) {
    const alt = altText || 'Image attachment';

    const layer = document.createElement('div');
    layer.className = 'image-layer revealed loaded';

    // Keep the placeholder in the DOM but faded out; the CSS transition
    // between .image-placeholder and .chat-image depends on it being there.
    const placeholder = document.createElement('div');
    placeholder.className = 'image-placeholder';
    placeholder.style.opacity = '0';
    layer.appendChild(placeholder);

    const img = document.createElement('img');
    img.src = imageUrl;
    img.alt = alt;
    img.classList.add('chat-image', 'loaded');
    img.style.setProperty('aspect-ratio', '2 / 3');
    img.style.cursor = 'pointer';
    img.loading = 'lazy';
    img.addEventListener('click', () => {
        if (typeof openAttachmentLightbox === 'function') {
            openAttachmentLightbox({ src: imageUrl, name: alt, type: 'image', isImage: true });
        }
    });
    wireBrokenImageFallback(img, alt);
    layer.appendChild(img);

    // Hover-reveal toolbar (download / copy / regenerate / edit). Matches
    // ChatGPT's image hover controls. Visibility + transition handled in CSS.
    const toolbar = buildImageHoverToolbar(imageUrl, alt);
    if (toolbar) layer.appendChild(toolbar);

    return layer;
}

// Build the bottom action-bar shown over a generated image, ChatGPT-style:
// Edit pill on the left (opens the prompt in the composer), Download icon
// on the right. Both sit on translucent-dark-glass chips.
function buildImageHoverToolbar(imageUrl, altText) {
    if (!imageUrl) return null;

    const stripAltPrefix = (s) => (s || '').replace(/^AI-generated image:\s*/i, '').trim();
    const promptBody = stripAltPrefix(altText);

    const bar = document.createElement('div');
    bar.className = 'image-layer__actions';
    bar.setAttribute('role', 'toolbar');
    bar.setAttribute('aria-label', 'Image actions');

    // Edit pill — left side. Drops the original prompt into the composer
    // so the user can tweak it and resend. Does not auto-send.
    const edit = document.createElement('button');
    edit.type = 'button';
    edit.className = 'image-layer__btn image-layer__btn--pill';
    edit.setAttribute('aria-label', 'Edit prompt');
    edit.innerHTML = '<span>Edit</span>';
    edit.addEventListener('click', (e) => {
        e.stopPropagation();
        const promptEl = document.getElementById('prompt');
        if (!promptEl) return;
        promptEl.value = '/image ' + (promptBody || '');
        promptEl.dispatchEvent(new Event('input'));
        promptEl.focus();
        promptEl.setSelectionRange(promptEl.value.length, promptEl.value.length);
    });
    bar.appendChild(edit);

    // Share icon-circle — right side. Opens the share modal (Copy link /
    // X / LinkedIn / Reddit / Download). The glyph is the iOS-style
    // "arrow out of a tray" share icon (matches ChatGPT).
    const share = document.createElement('button');
    share.type = 'button';
    share.className = 'image-layer__btn image-layer__btn--circle';
    share.setAttribute('aria-label', 'Share image');
    share.innerHTML = '<i class="fas fa-arrow-up-from-bracket" aria-hidden="true"></i>';
    share.addEventListener('click', (e) => {
        e.stopPropagation();
        if (typeof openShareModal !== 'function') return;

        share.classList.add('is-loading');
        const openModalNow = () => {
            openShareModal({
                imageUrl,
                title: promptBody ? promptBody.slice(0, 80) : 'Untitled image',
                downloadName: (promptBody || 'image').slice(0, 80) || 'image',
            });
            // Stop spinning once the slide-up animation ends so the icon
            // reappears with the settled modal.
            setTimeout(() => share.classList.remove('is-loading'), 380);
        };

        // Preload the image (or hit cache) before opening. Prevents the
        // modal from popping up with an empty preview slot — the spinner
        // keeps running until the image is actually ready to paint.
        const preload = new Image();
        preload.src = imageUrl;
        const startIfReady = () => {
            // Minimum dwell so even cache-hits feel intentional.
            const MIN_SPIN_MS = 350;
            const started = Date.now();
            const go = () => {
                const elapsed = Date.now() - started;
                const wait = Math.max(0, MIN_SPIN_MS - elapsed);
                setTimeout(openModalNow, wait);
            };
            if (typeof preload.decode === 'function') {
                preload.decode().then(go).catch(go);
            } else if (preload.complete) {
                go();
            } else {
                preload.addEventListener('load', go, { once: true });
                preload.addEventListener('error', go, { once: true });
            }
        };
        startIfReady();
    });
    bar.appendChild(share);

    return bar;
}

// Back-compat alias. Older call sites use `buildImageFigure`; redirect to
// the new name so they keep working without edits. Safe to delete once no
// external references remain.
const buildImageFigure = buildImageLayer;

// Append a single image message to the chat (live + replay). Wraps one
// `buildImageLayer` in an `.image-message` container.
function appendImageMessage(messageDiv, imageUrl, altText) {
    const layer = buildImageLayer(imageUrl, altText);
    const imageContainer = document.createElement('div');
    imageContainer.classList.add('image-message');
    imageContainer.appendChild(layer);
    messageDiv.appendChild(imageContainer);
}

// Shared layout decision for a chat-message attachments wrapper.
// Applies the `.message-attachments--grid` + `data-image-count` treatment
// only when the attachment set is 2+ and exclusively images. 1-image sets
// stay flex-wrap (a single image doesn't need a grid). Mixed image+file
// sets also stay flex-wrap so file chips read naturally alongside tiles.
// Called from both `renderLiveAttachments` (live) and `renderAttachments`
// (history replay) so the two paths can't drift.
function applyAttachmentLayout(wrapperEl, attachments) {
    if (!wrapperEl || !attachments || !attachments.length) return;
    if (attachments.length < 2) return;

    const isImageAttachment = (a) => {
        if (!a) return false;
        if (a.isImage === true) return true;
        const nameCandidate = a.original_filename || a.name || a.filename;
        if (typeof FilePreview !== 'undefined' &&
            typeof FilePreview.isImage === 'function') {
            return FilePreview.isImage(nameCandidate, a.mime_type || a.type);
        }
        const mime = a.mime_type || a.type || '';
        return typeof mime === 'string' && mime.startsWith('image/');
    };

    const images = attachments.filter(isImageAttachment);
    if (images.length < 2 || images.length !== attachments.length) return;

    wrapperEl.classList.add('message-attachments--grid');
    wrapperEl.dataset.imageCount = String(Math.min(images.length, 4));
}

// Append a clean shimmer placeholder for image generation.
// ChatGPT uses a calm, content-shaped skeleton rather than flashy animation.
// Keep it to: a shimmering gradient + subtle "Creating image…" label.
function appendImagePlaceholder(messageDiv) {
    const layer = document.createElement('div');
    layer.className = 'image-layer';

    const placeholder = document.createElement('div');
    placeholder.className = 'image-placeholder image-placeholder--shimmer';
    placeholder.setAttribute('role', 'status');
    placeholder.setAttribute('aria-label', 'Generating image');

    const shimmer = document.createElement('div');
    shimmer.className = 'image-placeholder__shimmer';

    const label = document.createElement('div');
    label.className = 'image-placeholder__label';
    label.textContent = 'Creating image…';

    placeholder.appendChild(shimmer);
    placeholder.appendChild(label);

    const img = document.createElement('img');
    img.className = 'chat-image';
    img.style.setProperty('aspect-ratio', '2 / 3');

    layer.appendChild(placeholder);
    layer.appendChild(img);

    const imageContainer = document.createElement('div');
    imageContainer.classList.add('image-message');
    imageContainer.appendChild(layer);
    messageDiv.appendChild(imageContainer);

    // Ensure the placeholder stays in view like ChatGPT image generation
    if (typeof scrollState !== 'undefined') {
        scrollState.shouldAutoScroll = true;
        scrollState.isScrolling = false;
    }

    if (typeof scrollToBottom === 'function') {
        scrollToBottom(true, true);
    } else if (typeof scrollManager !== 'undefined' && typeof scrollManager.scrollToBottom === 'function') {
        scrollManager.scrollToBottom(true);
    } else {
        requestAnimationFrame(() => {
            const chatBox = document.getElementById('chatbox');
            if (chatBox) {
                chatBox.scrollTop = chatBox.scrollHeight;
            }
        });
    }

    requestAnimationFrame(() => {
        if (typeof imageContainer.scrollIntoView === 'function') {
            imageContainer.scrollIntoView({ behavior: 'smooth', block: 'end' });
        }
    });
}



// Append a PDF message to the chat
function appendPdfMessage(messageDiv, fileUrl) {
    const pdfContainer = document.createElement('div');
    pdfContainer.classList.add('pdf-message');

    const link = document.createElement('a');
    link.href = fileUrl;
    link.target = '_blank';
    link.classList.add('file-link');

    const pdfIcon = document.createElement('i');
    pdfIcon.className = 'fas fa-file-pdf';
    pdfIcon.style.fontSize = '24px';
    pdfIcon.style.color = 'red';
    pdfIcon.style.marginRight = '8px';

    const fileNameText = document.createElement('span');
    fileNameText.textContent = extractFileName(fileUrl);

    link.appendChild(pdfIcon);
    link.appendChild(fileNameText);
    pdfContainer.appendChild(link);
    messageDiv.appendChild(pdfContainer);
}

// Append a generic file message to the chat
function appendFileMessage(messageDiv, fileUrl) {
    const fileContainer = document.createElement('div');
    fileContainer.classList.add('file-message');

    const link = document.createElement('a');
    link.href = fileUrl;
    link.target = '_blank';
    link.classList.add('file-link');

    const fileIcon = document.createElement('i');
    fileIcon.className = 'fas fa-file-alt';
    fileIcon.style.fontSize = '24px';
    fileIcon.style.color = 'grey';
    fileIcon.style.marginRight = '8px';

    const fileNameText = document.createElement('span');
    fileNameText.textContent = extractFileName(fileUrl);

    link.appendChild(fileIcon);
    link.appendChild(fileNameText);
    fileContainer.appendChild(link);
    messageDiv.appendChild(fileContainer);
}

// Utility function to extract file name from URL
function extractFileName(url) {
    return url.substring(url.lastIndexOf('/') + 1);
}

// Generate a unique message ID
function generateMessageId() {
    return `msg-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

// Create a new message div
function createMessageDiv(messageId, sender) {
    const messageDiv = document.createElement("div");
    messageDiv.id = messageId;
    messageDiv.className = `message ${sender}-message`;
    return messageDiv;
}

// Create a header for the message
function createHeader(sender, messageDiv) {
    const headerDiv = document.createElement("div");
    headerDiv.className = 'message-header';

    messageDiv.appendChild(headerDiv);
}

/// Resets the prompt input: clears its value, resets its height, and refocuses it.
function resetAndFocusInput() {
    if (!promptInput) {
        return;
    }
    promptInput.value = '';
    autoGrowComposer();
    updateSendButtonState();

    // Don't refocus on mobile - keeps keyboard dismissed after send
    const isMobile = document.body.classList.contains('device-phone') ||
                    document.body.classList.contains('is-mobile') ||
                    document.body.classList.contains('is-ios') ||
                    document.body.classList.contains('device-tablet');
    if (!isMobile) {
        promptInput.focus();
    }
}

// Checks if the sender is "user" and resets the input if necessary.
function resetInputIfNecessary(sender) {
    if (sender === "user") {
        resetAndFocusInput();
    }
}


// Function to add copy button to user message
function appendCopyMessageButton(container, messageId) {
    const copyBtn = document.createElement("button");
    copyBtn.className = "copy-message-button";
    copyBtn.setAttribute("aria-label", "Copy message");
    copyBtn.setAttribute("data-tooltip", "Copy");

    const copyIcon = document.createElement("i");
    copyIcon.className = "far fa-copy";
    copyIcon.setAttribute("aria-hidden", "true");

    copyBtn.appendChild(copyIcon);
    copyBtn.addEventListener("click", async () => {
        const messageDiv = document.getElementById(messageId);
        if (!messageDiv) return;

        const textElement = messageDiv.querySelector('.text-message');
        const text = textElement ? textElement.textContent : '';

        try {
            await navigator.clipboard.writeText(text);
            copyIcon.className = "fas fa-check";
            setTimeout(() => {
                copyIcon.className = "far fa-copy";
            }, 1000);
        } catch (err) {
            console.error('Failed to copy:', err);
        }
    });
    container.appendChild(copyBtn);
}

// Function to add edit button to message
function appendEditButton(container, messageId) {
    const editBtn = document.createElement("button");
    editBtn.className = "edit-button";
    editBtn.setAttribute("aria-label", "Edit message");
    editBtn.setAttribute("data-tooltip", "Edit");

    const editIcon = document.createElement("i");
    editIcon.className = "fas fa-pen";
    editIcon.setAttribute("aria-hidden", "true");

    editBtn.appendChild(editIcon);
    editBtn.addEventListener("click", () => enableEditing(messageId));
    container.appendChild(editBtn);
}


// Enable editing a message in the chat box
function enableEditing(messageId) {
    const messageDiv = document.getElementById(messageId);
    if (!messageDiv) {
        console.error(`Element with ID ${messageId} not found.`);
        return;
    }
    const messageKey = messageDiv.dataset.messageKey || messageId;

    // Retrieve the existing text content from the message element
    const originalTextElement = messageDiv.querySelector('.text-message'); // Assuming this class is used for the message text
    const originalText = originalTextElement ? originalTextElement.textContent : '';

    // Create a container for centering
    const messageContainer = document.createElement('div');
    messageContainer.className = 'message-container';

    // Create a textarea for editing and prefill it with the original text
    const textarea = document.createElement('textarea');
    textarea.value = originalText; // Prefill the textarea with the existing content
    textarea.className = 'edit-textarea';
    textarea.style.backgroundColor = '#2B2B2B';
    textarea.style.borderRadius = '22px';
    textarea.style.color = '#FFFFFF';
    textarea.style.marginBottom = '60px'; // Ensure scrollbar ends above buttons
    textarea.addEventListener('input', () => autoExpandTextArea(textarea)); // Auto-expand textarea

    // Add the textarea to the container
    messageContainer.appendChild(textarea);

    // Replace the content of messageDiv with the container
    messageDiv.innerHTML = ''; // Clear existing content
    messageDiv.appendChild(messageContainer); // Add the new container

    // Auto adjust height on initial load and input change
    textarea.addEventListener('input', function() {
        autoExpandTextArea(textarea);
    });

    // Create buttons for saving and canceling
    const buttonsContainer = document.createElement('div');
    buttonsContainer.className = 'button-container';

    const saveButton = document.createElement('button');
    saveButton.textContent = 'Save & Submit';
    saveButton.className = 'save-button';
    buttonsContainer.appendChild(saveButton);
    saveButton.addEventListener('click', () => saveChanges(messageId, textarea.value, messageKey));

    const cancelButton = document.createElement('button');
    cancelButton.textContent = 'Cancel';
    cancelButton.className = 'cancel-button';
    cancelButton.style.backgroundColor = '#1E1E1E';
    cancelButton.style.border = '1px solid #3A3A3A';
    buttonsContainer.appendChild(cancelButton);
    cancelButton.addEventListener('click', () => cancelChanges(messageId, originalText, messageKey));

    
    // Append buttons container to message div
    messageDiv.appendChild(buttonsContainer);

    // Adjust the textarea height initially
    autoExpandTextArea(textarea);

    // Focus and scroll to the textarea to bring it into view
    textarea.focus(); // Set focus to the textarea
    textarea.scrollIntoView({ behavior: 'smooth', block: 'center' }); // Scroll to the textarea smoothly and center it in the view
}

// Function to auto-expand the textarea based on content
function autoExpandTextArea(element) {
    const maxHeight = 200; // Maximum height for the textarea

    // Reset the height to 'auto' to shrink to fit content
    element.style.height = 'auto';

    // Calculate and set the height based on the scroll height
    if (element.scrollHeight > maxHeight) {
        element.style.height = `${maxHeight}px`;
        element.style.overflowY = 'auto';
    } else {
        element.style.height = `${element.scrollHeight}px`;
        element.style.overflowY = 'hidden';
    }

    // Update the container height if necessary
    const textareaContainer = element.parentElement;
    if (textareaContainer && textareaContainer.classList.contains('textarea-container')) {
        textareaContainer.style.height = element.style.height;
    }
}



// Save changes made to an edited message
function saveChanges(messageId, newText, messageKey = null) {
    const trimmed = (newText || '').trim();
    if (!trimmed) {
        cancelChanges(messageId, '', messageKey);
        return;
    }

    let branchResult = null;
    if (messageKey && window.chatManager && typeof window.chatManager.branchSessionAtMessage === 'function') {
        branchResult = window.chatManager.branchSessionAtMessage(messageKey, trimmed);
    } else if (window.chatManager && typeof window.chatManager.renderActivePage === 'function') {
        window.chatManager.renderActivePage(undefined, { anchorId: messageKey || messageId });
    }

    const targetPageId = branchResult?.pageId || (window.chatManager?.getActivePageId?.() ?? null);
    const resolvedMessageId = branchResult?.messageId || messageKey || messageId;

    sendMessage(trimmed, resolvedMessageId, {
        skipUserMessageRender: true,
        reuseMessageIdForResponse: false,
        forcePageId: targetPageId
    });
}

// Cancel changes made to an edited message
function cancelChanges(messageId, originalText, messageKey = null) {
    if (window.chatManager && typeof window.chatManager.renderActivePage === 'function') {
        window.chatManager.renderActivePage(undefined, { anchorId: messageKey || messageId });
    }
}

// Scroll Manager for handling all scroll operations
// Now delegates to scrollState.js for consistency
const scrollManager = {
    isScrolling: false,
    scrollAnimation: null,
    threshold: 100,

    scrollToBottom(immediate = false, force = false) {
        // ChatGPT-style: keep user message at top during response
        if (window._keepMessageAtTop === true) {
            return; // Keep user message at top
        }

        // Respect user scroll intent during streaming - don't scroll if user scrolled up
        if (!force && window.scrollState && !window.scrollState.isPinnedToBottom) {
            return; // User scrolled up, don't auto-scroll
        }

        // Delegate to scrollState.js if available
        if (window.scrollState?.scrollToBottomNow) {
            window.scrollState.scrollToBottomNow();
            return;
        }

        // DOAR #chat-scroll-region scrollează
        const scrollContainer = document.querySelector('#chat-scroll-region');
        if (!scrollContainer) return;

        if (this.scrollAnimation) {
            cancelAnimationFrame(this.scrollAnimation);
        }

        if (immediate) {
            scrollContainer.scrollTop = scrollContainer.scrollHeight;
            return;
        }

        const start = scrollContainer.scrollTop;
        const end = scrollContainer.scrollHeight - scrollContainer.clientHeight;
        const duration = 300;
        const startTime = performance.now();

        const animate = (currentTime) => {
            const elapsed = currentTime - startTime;
            const progress = Math.min(elapsed / duration, 1);

            // Ease out cubic
            const easing = 1 - Math.pow(1 - progress, 3);
            scrollContainer.scrollTop = start + (end - start) * easing;

            if (progress < 1) {
                this.scrollAnimation = requestAnimationFrame(animate);
            } else {
                this.scrollAnimation = null;
                this.isScrolling = false;
            }
        };

        this.isScrolling = true;
        this.scrollAnimation = requestAnimationFrame(animate);
    },

    isAtBottom(element) {
        if (!element) return true;
        const scrollPosition = Math.ceil(element.scrollTop + element.clientHeight);
        return scrollPosition >= element.scrollHeight - this.threshold;
    }
};



// Helper function to handle message errors
function handleMessageError(error, messageId) {
    console.error('Message error:', error);
    
    let errorMessage = 'An error occurred while processing your message.';
    if (error.name === 'AbortError') {
        errorMessage = 'Message was cancelled.';
    } else if (error.message) {
        errorMessage = error.message;
    }

    if (messageId) {
        const messageElement = document.getElementById(messageId);
        if (messageElement) {
            messageElement.textContent = errorMessage;
            messageElement.classList.add('error');
        }
    } else {
        displayMessage(errorMessage, 'error', 'text', '', true);
    }
}



// Update the send button UI state
function updateSendButton(isSending) {
    const sendButton = document.querySelector('.send-button');
    if (!sendButton) return;

    // Keep the button clickable so the stop action always works while typing/streaming.
    sendButton.disabled = false;
    const showStop = !!isSending;
    sendButton.classList.toggle('is-sending', showStop);
    sendButton.classList.toggle('show-stop', showStop);
    sendButton.setAttribute('aria-busy', showStop ? 'true' : 'false');
    sendButton.setAttribute('aria-label', showStop ? 'Stop generating' : 'Send message');
    updateSendButtonState();
}

// Get references to the input and send button
const promptInput = document.getElementById('prompt');
const sendButton = document.querySelector('.send-button');
const sendIcon = sendButton ? sendButton.querySelector('.fas.fa-arrow-up') : null;
const stopIcon = sendButton ? sendButton.querySelector('.fa-stop') : null;
const composerRow = document.querySelector('.input-row');
const composerRootStyles = typeof window !== 'undefined'
    ? window.getComputedStyle(document.documentElement)
    : null;
const composerLockedHeight =
    composerRootStyles
        ? parseFloat(composerRootStyles.getPropertyValue('--composer-locked-height')) || 56
        : 56;

const getComposerMaxHeight = () => {
    if (!promptInput) return 220;
    const computed = parseFloat(window.getComputedStyle(promptInput).maxHeight || '');
    return Number.isNaN(computed) ? 220 : computed;
};

// Reusable off-screen clone for measurement
let measurementClone = null;

function autoGrowComposer() {
    if (!promptInput) return;

    const SINGLE_LINE_HEIGHT = 34;   // Fixed height for single line (matches send button)
    const BUTTON_PADDING = 40;        // Space reserved = button height (34px) + bottom offset (6px)
    const LINE_HEIGHT_MULTI = 1.6;    // Normal line height for multiline text
    const maxHeight = getComposerMaxHeight();

    // Empty input - reset to single line defaults
    if (!promptInput.value) {
        promptInput.style.height = `${SINGLE_LINE_HEIGHT}px`;
        promptInput.style.lineHeight = `${SINGLE_LINE_HEIGHT}px`;
        promptInput.style.paddingBottom = '0';
        promptInput.style.marginBottom = '0';
        promptInput.style.overflowY = 'hidden';
        if (composerRow) composerRow.classList.remove('composer-expanded');
        return;
    }

    // Step 1: Temporarily set to single-line mode to test if content overflows
    promptInput.style.height = `${SINGLE_LINE_HEIGHT}px`;
    promptInput.style.lineHeight = `${SINGLE_LINE_HEIGHT}px`;
    promptInput.style.paddingBottom = '0';
    promptInput.style.marginBottom = '0';
    promptInput.style.overflowY = 'hidden';

    // Step 2: Check if content overflows (needs multiple lines)
    const isMultiline = promptInput.scrollHeight > SINGLE_LINE_HEIGHT;

    if (isMultiline) {
        // MULTILINE MODE: measure actual text height at normal line-height
        const cs = window.getComputedStyle(promptInput);
        const fontSize = parseFloat(cs.fontSize) || 15;
        const lineHeightPx = Math.ceil(fontSize * LINE_HEIGHT_MULTI);

        // Create measurement clone if needed
        if (!measurementClone) {
            measurementClone = document.createElement('textarea');
            measurementClone.setAttribute('aria-hidden', 'true');
            measurementClone.style.cssText = `
                position: absolute;
                top: -9999px;
                left: -9999px;
                visibility: hidden;
                pointer-events: none;
                height: auto;
                min-height: 0;
                max-height: none;
                overflow: hidden;
                white-space: pre-wrap;
                word-wrap: break-word;
                box-sizing: border-box;
            `;
            document.body.appendChild(measurementClone);
        }

        // Measure text height at multiline line-height
        measurementClone.style.width = cs.width;
        measurementClone.style.fontSize = cs.fontSize;
        measurementClone.style.fontFamily = cs.fontFamily;
        measurementClone.style.fontWeight = cs.fontWeight;
        measurementClone.style.letterSpacing = cs.letterSpacing;
        measurementClone.style.lineHeight = `${LINE_HEIGHT_MULTI}`;
        measurementClone.style.padding = '0';
        measurementClone.value = promptInput.value;

        const textHeight = measurementClone.scrollHeight;

        // Max height for textarea = container max minus button space
        const textareaMaxHeight = maxHeight - BUTTON_PADDING;
        const textareaHeight = Math.min(textHeight, textareaMaxHeight);
        const needsScroll = textHeight > textareaMaxHeight;

        // Textarea ends above button using margin-bottom
        // This keeps the scrollbar within the textarea bounds (above button)
        promptInput.style.height = `${textareaHeight}px`;
        promptInput.style.lineHeight = `${LINE_HEIGHT_MULTI}`;
        promptInput.style.paddingBottom = '0';
        promptInput.style.marginBottom = `${BUTTON_PADDING}px`;
        promptInput.style.overflowY = needsScroll ? 'auto' : 'hidden';

        if (composerRow) composerRow.classList.add('composer-expanded');
    } else {
        // SINGLE LINE MODE: already set above, just ensure margin is reset
        promptInput.style.marginBottom = '0';
        if (composerRow) composerRow.classList.remove('composer-expanded');
    }
}

let isComposingMessage = false;

function initPromptInputHandlers() {
    // Re-query DOM elements in case they weren't available initially
    const input = document.getElementById('prompt');
    const btn = document.querySelector('.send-button');

    if (!input || !btn) {
        console.warn('Prompt input or send button not found, retrying...');
        return false;
    }

    input.addEventListener('input', () => {
        if (input.value.trim() !== "") {
            btn.classList.add('typing');
        } else {
            btn.classList.remove('typing');
        }
        autoGrowComposer();
        updateSendButtonState();
    });

    // Don't reset _keepMessageAtTop on focus - it causes scroll issues during streaming
    // The flag is reset when user sends a new message in sendMessage()

    input.addEventListener('compositionstart', () => {
        isComposingMessage = true;
    });

    input.addEventListener('compositionend', () => {
        isComposingMessage = false;
        autoGrowComposer();
        updateSendButtonState();
    });

    input.addEventListener('keydown', (event) => {
        // Don't send while IME is composing (Japanese/Chinese/Korean input)
        if (isComposingMessage || event.isComposing) {
            return;
        }
        // Enter without Shift = send message
        // Shift + Enter = new line (default behavior)
        if (event.key === 'Enter' && !event.shiftKey) {
            const stopVisible = btn && btn.classList.contains('show-stop');
            if (stopVisible) {
                return;
            }
            event.preventDefault();
            if (!btn.disabled) {
                // Mobile: Dismiss keyboard
                dismissMobileKeyboard();
                sendMessage();
            }
        }
    });

    // Handle paste - ensure autosize runs after paste content is inserted
    input.addEventListener('paste', () => {
        requestAnimationFrame(() => {
            autoGrowComposer();
            updateSendButtonState();
        });
    });

    window.addEventListener('resize', autoGrowComposer);
    autoGrowComposer();

    return true;
}

// Initialize immediately if DOM is ready, otherwise wait
if (promptInput && sendButton) {
    initPromptInputHandlers();
} else {
    // Retry on DOMContentLoaded if elements weren't found
    document.addEventListener('DOMContentLoaded', () => {
        if (!initPromptInputHandlers()) {
            // Final retry after a short delay
            setTimeout(initPromptInputHandlers, 100);
        }
    });
}

// --- Composer ResizeObserver ---
// Dynamically updates --composer-reserved-height so the conversation panel's
// bottom padding always matches the actual composer height.  This replaces the
// static 150px fallback and eliminates hardcoded mobile overrides.
(function initComposerResizeObserver() {
    const composer = document.querySelector('.chat-shell__composer');
    if (!composer || typeof ResizeObserver === 'undefined') return;

    const root = document.documentElement;
    let lastHeight = 0;

    const update = () => {
        // Use the visible height (input-container) rather than the full fixed
        // footer which includes transparent click-through space above it.
        const container = composer.querySelector('.input-container');
        const measured = container ? container.getBoundingClientRect().height : composer.offsetHeight;
        // Add breathing room so the last message isn't flush against the composer
        const reserved = Math.ceil(measured) + 24;
        if (reserved !== lastHeight) {
            lastHeight = reserved;
            root.style.setProperty('--composer-reserved-height', reserved + 'px');
        }
    };

    const ro = new ResizeObserver(update);
    ro.observe(composer);
    // Also observe the input-container directly for textarea grow/shrink
    const container = composer.querySelector('.input-container');
    if (container) ro.observe(container);

    // Initial measurement
    update();
})();






// Clear the chat box
function clearChat(options = {}) {
    const forceClear = !!options.force;
    const debugInfo = {
        forceClear,
        hasManager: false,
        activeSessionId: null,
        messageCount: null,
        skipped: false
    };

    if (!forceClear && typeof window !== 'undefined' && window.chatManager) {
        debugInfo.hasManager = true;
        const manager = window.chatManager;
        const activeId = manager.activeSessionId;
        debugInfo.activeSessionId = activeId || null;
        if (activeId) {
            const session = manager.chatHistory?.[activeId];
            const hasHistory = Array.isArray(session?.messages) && session.messages.length > 0;
            debugInfo.messageCount = Array.isArray(session?.messages) ? session.messages.length : null;
            if (hasHistory) {
                debugInfo.skipped = true;
                console.debug('clearChat skipped', debugInfo);
                return;
            }
        }
    }

    console.debug('clearChat executing', debugInfo);

    const chatDisplay = document.getElementById('chat-display');
    const chatBox = document.getElementById('chatbox');
    if (chatDisplay) {
        chatDisplay.innerHTML = '';
    }
    if (chatBox) {
        chatBox.querySelectorAll('.message').forEach(node => node.remove());
    }
    updateEphemeralOptionsVisibility();

    // Dispatch event to notify other components (greeting, home footer, etc.)
    document.dispatchEvent(new CustomEvent('chat-cleared'));
}

// Hide the starter options once real messages exist; show them again when empty.
let optionsDismissed = false;
function updateEphemeralOptionsVisibility() {
    const options = document.querySelector('.options[data-ephemeral="true"]');
    const chatBox = document.getElementById('chatbox');
    if (!options || !chatBox) return;

    if (optionsDismissed) {
        options.style.display = 'none';
        return;
    }

    const hasMessages = !!chatBox.querySelector('.message');
    // Only show options if there are no messages AND server didn't hide them (FOUC prevention)
    if (hasMessages) {
        options.style.display = 'none';
    } else if (!options.style.cssText.includes('!important')) {
        // Only show if server didn't set display:none!important
        options.style.display = '';
    }
}

function dismissEphemeralOptions() {
    optionsDismissed = true;
    updateEphemeralOptionsVisibility();
}

function resetEphemeralOptionsForNewChat() {
    optionsDismissed = false;
    // Clear server-side FOUC prevention style for new chat
    const options = document.querySelector('.options[data-ephemeral="true"]');
    if (options) {
        options.style.cssText = '';
        // Re-trigger button animations (they start opacity:0 and animate in once)
        options.querySelectorAll('button').forEach(btn => {
            btn.style.animation = 'none';
            btn.offsetHeight; // force reflow
            btn.style.animation = '';
        });
    }
    updateEphemeralOptionsVisibility();
}

if (typeof window !== 'undefined') {
    window.updateEphemeralOptionsVisibility = updateEphemeralOptionsVisibility;
    window.dismissEphemeralOptions = dismissEphemeralOptions;
    window.resetEphemeralOptionsForNewChat = resetEphemeralOptionsForNewChat;
    window.clearChat = clearChat;
}

// Initial state on load
document.addEventListener('DOMContentLoaded', () => {
    updateEphemeralOptionsVisibility();
});



// Toggle between voice mode button and send button
const voiceModeButton = document.getElementById('voice-mode-button');

function updateSendButtonState() {
    if (!promptInput || !sendButton) {
        return;
    }
    const isEmpty = promptInput.value.trim() === '';
    const isStopVisible = sendButton.classList.contains('show-stop');

    // Toggle between voice mode button and send button
    if (voiceModeButton) {
        if (isEmpty && !isStopVisible) {
            // Show voice mode button, hide send button
            voiceModeButton.classList.remove('hidden');
            sendButton.style.display = 'none';
        } else {
            // Show send button, hide voice mode button
            voiceModeButton.classList.add('hidden');
            sendButton.style.display = 'flex';
        }
    }

    // Enable/disable send button
    sendButton.disabled = isEmpty && !isStopVisible;

    // Remove old tooltip behavior - no longer needed
    sendButton.removeAttribute('data-tooltip');
}

// Expose for external use
window.updateSendButtonState = updateSendButtonState;
window.updateSendButton = updateSendButton;

if (sendButton && stopIcon) {
    sendButton.addEventListener('mouseover', () => {
        if (sendButton.disabled && !sendButton.classList.contains('show-stop')) {
            showTooltip(sendButton);
        }
    });

    sendButton.addEventListener('mouseout', () => hideTooltip(sendButton));
}

// Voice mode button tooltip and click handler
if (voiceModeButton) {
    voiceModeButton.addEventListener('mouseover', () => {
        showTooltip(voiceModeButton);
    });

    voiceModeButton.addEventListener('mouseout', () => hideTooltip(voiceModeButton));

    voiceModeButton.addEventListener('click', (e) => {
        e.preventDefault();
        // Enter voice mode - will be implemented in voiceModeModule.js
        if (typeof window.VoiceModeModule !== 'undefined' && window.VoiceModeModule.enter) {
            window.VoiceModeModule.enter();
        } else {
            console.log('Voice mode clicked - module not yet loaded');
        }
    });
}

function showTooltip(element) {
    // Clean up any existing tooltip to prevent orphaned DOM nodes
    if (element._tooltip) {
        element._tooltip.remove();
        element._tooltip = null;
    }
    const tooltipText = element.getAttribute('data-tooltip');
    if (tooltipText) {
        const tooltip = document.createElement('div');
        tooltip.className = 'tooltip';
        tooltip.innerText = tooltipText;
        document.body.appendChild(tooltip);

        const rect = element.getBoundingClientRect();
        const viewportCenterX = rect.left + rect.width / 2;
        const viewportTop = rect.top - tooltip.offsetHeight - 8;
        tooltip.style.left = `${viewportCenterX + window.scrollX}px`;
        tooltip.style.top = `${viewportTop + window.scrollY}px`;

        element._tooltip = tooltip;
    }
}

function hideTooltip(element) {
    if (element && element._tooltip) {
        document.body.removeChild(element._tooltip);
        element._tooltip = null;
    }
}

// no mutation observer needed; updateSendButton calls updateSendButtonState

document.addEventListener('DOMContentLoaded', () => {
    const fileInput = document.getElementById('file-upload');
    const form = document.getElementById('upload-form');
    const chatInputWrapper = document.querySelector('.textarea-container'); // Updated element
    const inputContainer = document.querySelector('.input-container');
    const previewTray = ensurePreviewTray(inputContainer, chatInputWrapper);
    const actionsMenu = document.getElementById('actions-menu');
    const actionsToggle = document.getElementById('actions-toggle');
    const actionsPopover = document.getElementById('actions-popover');
    const searchToggleLabel = document.querySelector('.search-btn');
    const searchCheckbox = document.getElementById('enable-search');
    let closeActionsMenu = null;

    if (!sendButton || !stopIcon || !promptInput) {
        return;
    }

    if (fileInput) fileInput.addEventListener('change', handleFileChange);

    // ─── Drag-and-drop + paste-from-clipboard ───
    // Feeds dropped/pasted files through the same pipeline as the file picker,
    // so one `handleFileChange` path drives previews + upload + send.
    (function wireDropAndPaste() {
        if (!fileInput) return;

        const forwardFilesToPipeline = (files) => {
            const incoming = Array.from(files || []).filter(Boolean);
            if (!incoming.length) return;
            const dt = new DataTransfer();
            incoming.forEach((f) => dt.items.add(f));
            fileInput.files = dt.files;
            fileInput.dispatchEvent(new Event('change'));
        };

        // Drop zone overlay — lazily created the first time the user drags in.
        let dropOverlay = null;
        const ensureDropOverlay = () => {
            if (dropOverlay) return dropOverlay;
            dropOverlay = document.createElement('div');
            dropOverlay.className = 'drop-zone-overlay';
            dropOverlay.setAttribute('aria-hidden', 'true');
            dropOverlay.innerHTML = `
                <div class="drop-zone-overlay__panel">
                    <i class="fas fa-cloud-arrow-up drop-zone-overlay__icon" aria-hidden="true"></i>
                    <div class="drop-zone-overlay__title">Drop to attach</div>
                    <div class="drop-zone-overlay__hint">Images, PDFs, and documents</div>
                </div>
            `;
            document.body.appendChild(dropOverlay);
            return dropOverlay;
        };

        // Use a counter to track nested dragenter/dragleave events — otherwise
        // dragging over a child element fires dragleave on the parent.
        let dragDepth = 0;
        const isFileDrag = (e) => {
            const dt = e.dataTransfer;
            if (!dt) return false;
            // Some browsers expose `types` but not items during dragover.
            return Array.from(dt.types || []).includes('Files');
        };

        document.addEventListener('dragenter', (e) => {
            if (!isFileDrag(e)) return;
            dragDepth += 1;
            ensureDropOverlay().classList.add('open');
        });

        document.addEventListener('dragover', (e) => {
            if (!isFileDrag(e)) return;
            e.preventDefault();  // required for `drop` to fire
            if (e.dataTransfer) e.dataTransfer.dropEffect = 'copy';
        });

        document.addEventListener('dragleave', (e) => {
            if (!isFileDrag(e)) return;
            dragDepth = Math.max(0, dragDepth - 1);
            if (dragDepth === 0 && dropOverlay) dropOverlay.classList.remove('open');
        });

        document.addEventListener('drop', (e) => {
            if (!isFileDrag(e)) return;
            e.preventDefault();
            dragDepth = 0;
            if (dropOverlay) dropOverlay.classList.remove('open');
            forwardFilesToPipeline(e.dataTransfer?.files);
        });

        // Paste from clipboard — screenshots, copied-image-from-browser, etc.
        document.addEventListener('paste', (e) => {
            // Don't hijack pastes into text editors / inputs unless the
            // clipboard actually carries files. If the user is pasting text
            // into the prompt, fall through to the native text paste.
            const items = e.clipboardData?.items;
            if (!items) return;
            const files = [];
            for (const item of items) {
                if (item.kind === 'file') {
                    const f = item.getAsFile();
                    if (f) files.push(f);
                }
            }
            if (!files.length) return;
            // Only intercept if the focused target isn't one that needs the
            // file as text (e.g. file inputs — none currently expect paste).
            e.preventDefault();
            forwardFilesToPipeline(files);
        });
    })();

    sendButton.addEventListener('click', () => {
        // If voice mode is active, clicking end button exits voice mode
        if (sendButton.classList.contains('voice-end')) {
            if (window.VoiceModeModule) window.VoiceModeModule.exit();
            return;
        }

        // Mobile: Dismiss keyboard immediately
        dismissMobileKeyboard();

        if (sendButton.classList.contains('show-stop')) {
            abortMessage();
        } else {
            sendMessage();
        }
    });

    stopIcon.addEventListener('click', (e) => { e.stopPropagation(); abortMessage(); });
    // ─── Action Sheet Modal (positioned above + button) ───
    const actionModal = document.getElementById('action-sheet-modal');
    const actionBackdrop = document.getElementById('action-sheet-backdrop');

    if (actionsToggle && actionModal && actionBackdrop) {
        const closeActions = () => {
            actionModal.style.display = 'none';
            actionBackdrop.style.display = 'none';
            actionsToggle.setAttribute('aria-expanded', 'false');
            if (actionsMenu) actionsMenu.classList.remove('open');
            // Close submenus
            document.querySelectorAll('[data-submenu]').forEach(el => {
                const sub = document.getElementById(el.dataset.submenu);
                if (sub) sub.style.display = 'none';
                el.style.background = '';
                el.style.borderRadius = '';
                el.style.margin = '';
            });
        };
        closeActionsMenu = closeActions;

        actionsToggle.addEventListener('click', (e) => {
            e.stopPropagation();
            const isOpen = actionModal.style.display === 'block';
            if (isOpen) {
                closeActions();
            } else {
                // Position above the + button
                const rect = actionsToggle.getBoundingClientRect();
                actionModal.style.display = 'block';
                actionBackdrop.style.display = 'block';
                actionModal.style.left = rect.left + 'px';
                actionModal.style.bottom = (window.innerHeight - rect.top + 8) + 'px';
                actionsToggle.setAttribute('aria-expanded', 'true');
                if (actionsMenu) actionsMenu.classList.add('open');
            }
        });
    }

    if (searchToggleLabel && searchCheckbox) {
        searchCheckbox.addEventListener('change', () => {
            searchToggleLabel.setAttribute('aria-checked', searchCheckbox.checked ? 'true' : 'false');
            searchToggleLabel.classList.toggle('active', searchCheckbox.checked);
            // Sync with action sheet toggle
            const webBtn = document.getElementById('action-web-search');
            if (webBtn) webBtn.classList.toggle('active', searchCheckbox.checked);
        });
        searchToggleLabel.classList.toggle('active', searchCheckbox.checked);
    }

    // ─── Action Sheet Handlers ───
    const cameraBtn = document.getElementById('action-camera');
    const cameraInput = document.getElementById('camera-capture');
    if (cameraBtn && cameraInput) {
        cameraBtn.addEventListener('click', () => {
            if (closeActionsMenu) closeActionsMenu();
            cameraInput.click();
        });
        cameraInput.addEventListener('change', function() {
            // Reuse existing file handler
            const files = Array.from(this.files);
            if (files.length && fileInput) {
                const dt = new DataTransfer();
                files.forEach(f => dt.items.add(f));
                fileInput.files = dt.files;
                fileInput.dispatchEvent(new Event('change'));
            }
            this.value = '';
        });
    }

    // ─── Submenu toggle logic ───
    const _closeAllSubmenus = () => {
        document.querySelectorAll('[data-submenu]').forEach(el => {
            const sub = document.getElementById(el.dataset.submenu);
            if (sub) sub.style.display = 'none';
            el.style.background = '';
            el.style.borderRadius = '';
            el.style.margin = '';
        });
    };

    document.querySelectorAll('[data-submenu]').forEach(btn => {
        btn.addEventListener('click', (e) => {
            e.stopPropagation();
            const subId = btn.dataset.submenu;
            const sub = document.getElementById(subId);
            if (!sub) return;
            const wasOpen = sub.style.display === 'block';
            _closeAllSubmenus();
            if (!wasOpen) {
                sub.style.display = 'block';
                btn.style.background = 'var(--bg-hover, #353331)';
                btn.style.borderRadius = '8px';
                btn.style.margin = '2px 4px';
            }
        });
    });

    // ─── Generate image ───
    const genImageBtn = document.getElementById('action-generate-image');
    if (genImageBtn) {
        genImageBtn.addEventListener('click', () => {
            if (closeActionsMenu) closeActionsMenu();
            _closeAllSubmenus();
            if (promptInput) {
                promptInput.value = '/image ';
                promptInput.focus();
                promptInput.dispatchEvent(new Event('input'));
            }
        });
    }

    // ─── Artifact type submenu ───
    document.querySelectorAll('[data-artifact]').forEach(btn => {
        btn.addEventListener('click', () => {
            if (closeActionsMenu) closeActionsMenu();
            _closeAllSubmenus();
            const type = btn.dataset.artifact;
            const prompts = {
                'app': 'Create a web app that ',
                'dashboard': 'Create a dashboard that shows ',
                'game': 'Create a game: ',
            };
            if (promptInput) {
                promptInput.value = prompts[type] || 'Create ';
                promptInput.focus();
                promptInput.dispatchEvent(new Event('input'));
            }
        });
    });

    // ─── Add to project (placeholder) ───
    const addToProjectBtn = document.getElementById('action-add-to-project');
    if (addToProjectBtn) {
        addToProjectBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            // placeholder — no submenu content yet
        });
    }

    // ─── Skills submenu items (placeholders) ───
    const manageSkillsBtn = document.getElementById('action-manage-skills');
    if (manageSkillsBtn) {
        manageSkillsBtn.addEventListener('click', () => {
            if (closeActionsMenu) closeActionsMenu();
            _closeAllSubmenus();
        });
    }
    const addSkillBtn = document.getElementById('action-add-skill');
    if (addSkillBtn) {
        addSkillBtn.addEventListener('click', () => {
            if (closeActionsMenu) closeActionsMenu();
            _closeAllSubmenus();
        });
    }

    // ─── Connectors (placeholder) ───
    const connectorsBtn = document.getElementById('action-connectors');
    if (connectorsBtn) {
        connectorsBtn.addEventListener('click', () => {
            if (closeActionsMenu) closeActionsMenu();
            _closeAllSubmenus();
        });
    }

    // ─── Deep research ───
    const deepResearchBtn = document.getElementById('action-deep-research');
    if (deepResearchBtn) {
        deepResearchBtn.addEventListener('click', () => {
            if (closeActionsMenu) closeActionsMenu();
            _closeAllSubmenus();
            if (searchCheckbox && !searchCheckbox.checked) {
                searchCheckbox.checked = true;
                searchCheckbox.dispatchEvent(new Event('change'));
            }
            if (promptInput) {
                promptInput.value = '';
                promptInput.focus();
                promptInput.setAttribute('placeholder', 'What would you like to research?');
                promptInput.dispatchEvent(new Event('input'));
            }
        });
    }

    // ─── Web search toggle ───
    const webSearchBtn = document.getElementById('action-web-search');
    if (webSearchBtn) {
        const _setWebState = (on) => {
            const icon = webSearchBtn.querySelector('.asi-web-icon');
            const label = webSearchBtn.querySelector('.asi-web-label');
            const check = webSearchBtn.querySelector('.asi-web-check');
            if (on) {
                if (icon) { icon.style.color = '#378ADD'; icon.style.opacity = '1'; }
                if (label) label.style.color = '#378ADD';
                if (check) check.style.display = 'flex';
            } else {
                if (icon) { icon.style.color = '#fff'; icon.style.opacity = '0.8'; }
                if (label) label.style.color = '#fff';
                if (check) check.style.display = 'none';
            }
            webSearchBtn.dataset.webOn = on ? 'true' : 'false';
        };
        _setWebState(searchCheckbox && searchCheckbox.checked);
        webSearchBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            if (searchCheckbox) {
                searchCheckbox.checked = !searchCheckbox.checked;
                searchCheckbox.dispatchEvent(new Event('change'));
            }
            _setWebState(searchCheckbox ? searchCheckbox.checked : webSearchBtn.dataset.webOn !== 'true');
        });
    }

    // ─── Style submenu ───
    document.querySelectorAll('.action-style-opt').forEach(btn => {
        btn.addEventListener('click', () => {
            document.querySelectorAll('.action-style-opt').forEach(b => {
                b.style.background = '';
                b.style.borderRadius = '';
                b.style.margin = '';
                const chk = b.querySelector('.style-check');
                if (chk) chk.style.display = 'none';
            });
            btn.style.background = '#353331';
            btn.style.borderRadius = '8px';
            btn.style.margin = '2px 4px';
            const chk = btn.querySelector('.style-check');
            if (chk) chk.style.display = 'flex';
            const style = btn.dataset.style;
            if (window.chatManager) window.chatManager._activeStyle = style;
            if (closeActionsMenu) closeActionsMenu();
            _closeAllSubmenus();
        });
    });

    const createStyleBtn = document.getElementById('action-create-style');
    if (createStyleBtn) {
        createStyleBtn.addEventListener('click', () => {
            if (closeActionsMenu) closeActionsMenu();
            _closeAllSubmenus();
            if (promptInput) {
                promptInput.value = 'Create a writing style that ';
                promptInput.focus();
                promptInput.dispatchEvent(new Event('input'));
            }
        });
    }

    updateSendButtonState();

    function handleFileChange() {
        const files = Array.from(this.files); // Get all selected files as an array
        const addedAttachments = [];

        files.forEach(file => {
            const attachment = attachmentStore.add(file);
            addedAttachments.push(attachment);
            if (attachment.isImage) {
                // Wait for dataUrl to be ready, then render preview
                if (attachment.dataUrl) {
                    displayPreview(attachment);
                } else {
                    const reader = new FileReader();
                    reader.onload = () => {
                        attachment.dataUrl = reader.result;
                        displayPreview(attachment);
                    };
                    reader.onerror = () => {
                        console.error('Failed to read image file for preview');
                    };
                    reader.readAsDataURL(file);
                }
            } else {
                displayPreview(attachment); // Display a preview for each non-image file
            }
        });

        performUpload(files, addedAttachments); // Perform upload for all files
        if (typeof closeActionsMenu === 'function') {
            closeActionsMenu();
        }
    }

    function displayPreview(attachment) {
        const filePreviewContainer = createPreviewContainer(attachment.id, attachment.name);
        const closeButton = createCloseButton(filePreviewContainer, attachment.id); // Pass container for removal
        filePreviewContainer.appendChild(closeButton);

        let elementToAppend;
        if (attachment.isImage) {
            elementToAppend = createImagePreview(attachment);
        } else if (attachment.type === 'application/pdf' || (attachment.name && attachment.name.endsWith('.pdf'))) {
            elementToAppend = createPdfIcon();
        } else {
            elementToAppend = createFileIcon(attachment.name);
        }
        filePreviewContainer.appendChild(elementToAppend);

        if (!attachment.isImage) {
            const fileNameLabel = createFileNameLabel(attachment.name);
            filePreviewContainer.appendChild(fileNameLabel);
            enableFileOpen(filePreviewContainer, attachment);
        }

        // Insert previews in the tray above the input
        previewTray.appendChild(filePreviewContainer);
        updateTrayVisibility(previewTray);
    }

    function createPreviewContainer(attachmentId, fileName) {
        const container = document.createElement('div');
        container.classList.add('file-preview-container', 'uploading');
        container.dataset.attachmentId = attachmentId;
        if (fileName) {
            container.setAttribute('title', fileName);
            container.setAttribute('aria-label', `Attached file: ${fileName}`);
        }

        // Add loading overlay with progress bar
        const loadingOverlay = document.createElement('div');
        loadingOverlay.className = 'upload-loading-overlay';
        loadingOverlay.innerHTML = `
            <div class="upload-progress-container">
                <div class="upload-progress-status">Uploading...</div>
                <div class="upload-progress-track">
                    <div class="upload-progress-fill"></div>
                    <div class="upload-progress-shimmer"></div>
                </div>
                <div class="upload-progress-percent">0%</div>
            </div>
        `;
        container.appendChild(loadingOverlay);

        return container;
    }

    function updateUploadProgress(attachmentId, percent, statusText = null) {
        const container = document.querySelector(`.file-preview-container[data-attachment-id="${attachmentId}"]`);
        if (!container) return;

        const fill = container.querySelector('.upload-progress-fill');
        const percentEl = container.querySelector('.upload-progress-percent');
        const statusEl = container.querySelector('.upload-progress-status');
        const progressContainer = container.querySelector('.upload-progress-container');

        if (fill) {
            fill.style.width = `${percent}%`;
        }

        if (percentEl) {
            percentEl.textContent = `${Math.round(percent)}%`;
        }

        if (statusEl && statusText) {
            statusEl.textContent = statusText;
        }

        // Add processing state for visual feedback
        if (progressContainer) {
            if (percent >= 80 && percent < 100) {
                progressContainer.classList.add('processing');
            } else {
                progressContainer.classList.remove('processing');
            }
        }
    }

    function markUploadComplete(attachmentId) {
        const container = document.querySelector(`.file-preview-container[data-attachment-id="${attachmentId}"]`);
        if (container) {
            container.classList.remove('uploading');
            const overlay = container.querySelector('.upload-loading-overlay');
            if (overlay) overlay.remove();
        }
    }

    function createCloseButton(container, attachmentId) {
        const button = document.createElement('button');
        button.innerHTML = '&times;';
        button.classList.add('close-button');
        button.addEventListener('click', () => {
            container.remove(); // Remove this preview container
            attachmentStore.remove(attachmentId);
            updateTrayVisibility(previewTray);
        });
        return button;
    }

    function createImagePreview(attachment) {
        const img = document.createElement('img');
        const src = attachment.dataUrl || '';
        if (src) {
            img.src = src;
            img.addEventListener('click', () => openAttachmentLightbox({ src, name: attachment.name, type: attachment.type, isImage: true }));
        }
        img.alt = attachment.name || 'Image attachment';
        img.classList.add('composer-attachment-thumb');
        img.loading = 'lazy';
        if (!src) {
            img.classList.add('composer-attachment-thumb--empty');
        }
        return img;
    }

    function createPdfIcon() {
        const icon = document.createElement('i');
        icon.className = 'fas fa-file-pdf';
        icon.style.fontSize = '28px';
        icon.style.color = '#ef4444';
        return icon;
    }

    function createFileIcon(filename) {
        const icon = document.createElement('i');
        // Use FilePreview for better icon detection if available
        if (typeof FilePreview !== 'undefined' && filename) {
            icon.className = FilePreview.getFileIcon(filename);
            // Get file category for color
            const category = FilePreview.getFileCategory(filename);
            const colorMap = {
                pdf: '#ef4444',
                doc: '#3b82f6', docx: '#3b82f6',
                xls: '#22c55e', xlsx: '#22c55e', csv: '#22c55e',
                ppt: '#f97316', pptx: '#f97316',
                html: '#f97316',
                css: '#06b6d4',
                js: '#eab308',
                json: '#fbbf24',
                md: '#9ca3af', txt: '#9ca3af',
                code: '#a855f7',
                image: '#60a5fa',
                zip: '#d97706', archive: '#d97706',
                audio: '#14b8a6',
                video: '#ec4899'
            };
            icon.style.color = colorMap[category] || '#9ca3af';
        } else {
            icon.className = 'fas fa-file-alt';
            icon.style.color = '#9ca3af';
        }
        icon.style.fontSize = '28px';
        return icon;
    }

    function createFileNameLabel(fileName) {
        const label = document.createElement('div');
        label.textContent = fileName;
        label.classList.add('file-name');
        return label;
    }

    function ensurePreviewTray(container, textareaWrapper) {
        if (!container) return null;
        let tray = container.querySelector('#attachment-preview-tray');
        if (!tray) {
            tray = document.createElement('div');
            tray.id = 'attachment-preview-tray';
            tray.classList.add('attachment-preview-tray');
            tray.style.display = 'none';
            if (textareaWrapper && textareaWrapper.parentNode === container) {
                container.insertBefore(tray, textareaWrapper);
            } else {
                container.insertBefore(tray, container.firstChild);
            }
        }
        return tray;
    }

    let lightbox = null;
    let lightboxState = {
        items: [],     // [{src, name, type, isImage}]
        index: 0,
        zoom: 1,
        panX: 0,
        panY: 0,
    };

    function ensureLightbox() {
        if (lightbox) return lightbox;
        const overlay = document.createElement('div');
        overlay.className = 'image-lightbox';
        overlay.setAttribute('role', 'dialog');
        overlay.setAttribute('aria-modal', 'true');
        overlay.setAttribute('aria-label', 'Image preview');

        const content = document.createElement('div');
        content.className = 'image-lightbox__content';

        const img = document.createElement('img');
        img.className = 'image-lightbox__img';
        img.alt = '';

        const fallback = document.createElement('div');
        fallback.className = 'image-lightbox__fallback';
        fallback.innerHTML = '<p>Preview unavailable.</p>';

        const fallbackLink = document.createElement('a');
        fallbackLink.className = 'image-lightbox__fallback-link';
        fallbackLink.target = '_blank';
        fallbackLink.rel = 'noopener';
        fallbackLink.textContent = 'Open in new tab';
        fallback.appendChild(fallbackLink);

        const caption = document.createElement('div');
        caption.className = 'image-lightbox__caption';

        const counter = document.createElement('div');
        counter.className = 'image-lightbox__counter';
        counter.hidden = true;

        const close = document.createElement('button');
        close.type = 'button';
        close.className = 'image-lightbox__close';
        close.setAttribute('aria-label', 'Close preview');
        close.innerHTML = '&times;';

        // Toolbar (download + open-in-new-tab)
        const toolbar = document.createElement('div');
        toolbar.className = 'image-lightbox__toolbar';

        const copyBtn = document.createElement('button');
        copyBtn.type = 'button';
        copyBtn.className = 'image-lightbox__btn image-lightbox__copy';
        copyBtn.setAttribute('aria-label', 'Copy image URL');
        copyBtn.innerHTML = '<i class="fas fa-link" aria-hidden="true"></i>';
        copyBtn.addEventListener('click', async (e) => {
            e.stopPropagation();
            const item = lightboxState.items[lightboxState.index];
            const src = item && item.src;
            if (!src) return;
            try {
                if (navigator.clipboard && navigator.clipboard.writeText) {
                    await navigator.clipboard.writeText(src);
                } else {
                    // Fallback for non-secure contexts.
                    const ta = document.createElement('textarea');
                    ta.value = src;
                    ta.setAttribute('readonly', '');
                    ta.style.position = 'absolute';
                    ta.style.left = '-9999px';
                    document.body.appendChild(ta);
                    ta.select();
                    document.execCommand('copy');
                    document.body.removeChild(ta);
                }
                copyBtn.classList.add('is-copied');
                copyBtn.setAttribute('aria-label', 'URL copied');
                setTimeout(() => {
                    copyBtn.classList.remove('is-copied');
                    copyBtn.setAttribute('aria-label', 'Copy image URL');
                }, 1400);
            } catch (err) {
                console.warn('Copy URL failed:', err);
            }
        });

        const downloadBtn = document.createElement('a');
        downloadBtn.className = 'image-lightbox__btn image-lightbox__download';
        downloadBtn.setAttribute('aria-label', 'Download image');
        downloadBtn.setAttribute('download', '');
        downloadBtn.innerHTML = '<i class="fas fa-download" aria-hidden="true"></i>';

        const openTabBtn = document.createElement('a');
        openTabBtn.className = 'image-lightbox__btn image-lightbox__open-tab';
        openTabBtn.setAttribute('aria-label', 'Open in new tab');
        openTabBtn.target = '_blank';
        openTabBtn.rel = 'noopener';
        openTabBtn.innerHTML = '<i class="fas fa-up-right-from-square" aria-hidden="true"></i>';

        toolbar.appendChild(copyBtn);
        toolbar.appendChild(downloadBtn);
        toolbar.appendChild(openTabBtn);

        // Prev / next navigation for multi-image messages
        const prevBtn = document.createElement('button');
        prevBtn.type = 'button';
        prevBtn.className = 'image-lightbox__nav image-lightbox__nav--prev';
        prevBtn.setAttribute('aria-label', 'Previous image');
        prevBtn.innerHTML = '<i class="fas fa-chevron-left" aria-hidden="true"></i>';

        const nextBtn = document.createElement('button');
        nextBtn.type = 'button';
        nextBtn.className = 'image-lightbox__nav image-lightbox__nav--next';
        nextBtn.setAttribute('aria-label', 'Next image');
        nextBtn.innerHTML = '<i class="fas fa-chevron-right" aria-hidden="true"></i>';

        content.appendChild(img);
        content.appendChild(fallback);
        content.appendChild(caption);
        content.appendChild(counter);
        content.appendChild(close);
        content.appendChild(toolbar);
        content.appendChild(prevBtn);
        content.appendChild(nextBtn);
        overlay.appendChild(content);
        document.body.appendChild(overlay);

        const resetZoom = () => {
            lightboxState.zoom = 1;
            lightboxState.panX = 0;
            lightboxState.panY = 0;
            applyZoomTransform();
        };

        const applyZoomTransform = () => {
            const { zoom, panX, panY } = lightboxState;
            img.style.transform = `translate(${panX}px, ${panY}px) scale(${zoom})`;
            img.classList.toggle('is-zoomed', zoom > 1.001);
        };

        const setZoom = (nextZoom, originX, originY) => {
            const clamped = Math.max(1, Math.min(5, nextZoom));
            // Scale pan so zoom-to-cursor feels natural: keep the pointer
            // over the same pixel of the image after the zoom change.
            if (originX != null && originY != null && clamped > 1) {
                const rect = img.getBoundingClientRect();
                const cx = originX - (rect.left + rect.width / 2);
                const cy = originY - (rect.top + rect.height / 2);
                const ratio = clamped / lightboxState.zoom - 1;
                lightboxState.panX -= cx * ratio;
                lightboxState.panY -= cy * ratio;
            }
            if (clamped === 1) {
                lightboxState.panX = 0;
                lightboxState.panY = 0;
            }
            lightboxState.zoom = clamped;
            applyZoomTransform();
        };

        const renderCurrent = () => {
            const items = lightboxState.items || [];
            if (!items.length) return;
            const item = items[lightboxState.index] || items[0];
            if (!item) return;

            img.style.display = 'none';
            fallback.style.display = 'none';

            caption.textContent = item.name || '';

            const multi = items.length > 1;
            counter.hidden = !multi;
            prevBtn.hidden = !multi;
            nextBtn.hidden = !multi;
            counter.textContent = multi
                ? `${lightboxState.index + 1} / ${items.length}`
                : '';

            resetZoom();

            if (item.isImage && item.src) {
                img.src = item.src;
                img.alt = item.name || 'Image preview';
                img.style.display = 'block';
                downloadBtn.href = item.src;
                downloadBtn.setAttribute(
                    'download',
                    item.name || 'image'
                );
                openTabBtn.href = item.src;
                toolbar.style.display = 'flex';
            } else {
                fallback.style.display = 'block';
                fallbackLink.href = item.src || '#';
                toolbar.style.display = 'none';
            }
        };

        const step = (delta) => {
            const items = lightboxState.items || [];
            if (items.length < 2) return;
            const n = items.length;
            lightboxState.index = (lightboxState.index + delta + n) % n;
            renderCurrent();
        };

        let restoreFocusEl = null;
        const focusableSelector =
            'a[href], button:not([disabled]), [tabindex]:not([tabindex="-1"])';

        const trapFocus = (e) => {
            if (!overlay.classList.contains('open')) return;
            if (e.key !== 'Tab') return;
            const focusable = Array.from(
                overlay.querySelectorAll(focusableSelector)
            ).filter((n) => !n.hidden && n.offsetParent !== null);
            if (!focusable.length) return;
            const first = focusable[0];
            const last = focusable[focusable.length - 1];
            if (e.shiftKey && document.activeElement === first) {
                e.preventDefault();
                last.focus();
            } else if (!e.shiftKey && document.activeElement === last) {
                e.preventDefault();
                first.focus();
            }
        };

        const closeLightbox = () => {
            overlay.classList.remove('open');
            img.src = '';
            resetZoom();
            document.removeEventListener('keydown', trapFocus, true);
            if (restoreFocusEl && typeof restoreFocusEl.focus === 'function') {
                try { restoreFocusEl.focus(); } catch { /* node gone */ }
            }
            restoreFocusEl = null;
        };

        const openLightboxA11y = () => {
            restoreFocusEl = document.activeElement;
            document.addEventListener('keydown', trapFocus, true);
            // Defer focus until the overlay is painted so the focus ring
            // lands on a visible element.
            requestAnimationFrame(() => {
                close.focus({ preventScroll: true });
            });
        };

        overlay.addEventListener('click', (e) => {
            if (e.target === overlay) closeLightbox();
        });
        close.addEventListener('click', closeLightbox);
        prevBtn.addEventListener('click', (e) => { e.stopPropagation(); step(-1); });
        nextBtn.addEventListener('click', (e) => { e.stopPropagation(); step(1); });

        // Keyboard controls (ESC / arrows / +/- / 0)
        document.addEventListener('keydown', (e) => {
            if (!overlay.classList.contains('open')) return;
            if (e.key === 'Escape') { closeLightbox(); return; }
            if (e.key === 'ArrowLeft')  { e.preventDefault(); step(-1); return; }
            if (e.key === 'ArrowRight') { e.preventDefault(); step(1); return; }
            if (e.key === '+' || e.key === '=') {
                e.preventDefault();
                setZoom(lightboxState.zoom * 1.25);
                return;
            }
            if (e.key === '-' || e.key === '_') {
                e.preventDefault();
                setZoom(lightboxState.zoom / 1.25);
                return;
            }
            if (e.key === '0') {
                e.preventDefault();
                resetZoom();
                return;
            }
        });

        // Click image to zoom in (toggle between 1x and 2x); wheel to zoom fine.
        img.addEventListener('click', (e) => {
            e.stopPropagation();
            if (lightboxState.zoom > 1.001) {
                resetZoom();
            } else {
                setZoom(2, e.clientX, e.clientY);
            }
        });

        img.addEventListener('wheel', (e) => {
            e.preventDefault();
            const delta = -Math.sign(e.deltaY) * 0.18;
            setZoom(lightboxState.zoom * (1 + delta), e.clientX, e.clientY);
        }, { passive: false });

        // Drag-to-pan when zoomed.
        let dragging = null;
        img.addEventListener('pointerdown', (e) => {
            if (lightboxState.zoom <= 1.001) return;
            dragging = { x: e.clientX, y: e.clientY,
                         startPanX: lightboxState.panX, startPanY: lightboxState.panY };
            img.setPointerCapture(e.pointerId);
            img.classList.add('is-grabbing');
        });
        img.addEventListener('pointermove', (e) => {
            if (!dragging) return;
            lightboxState.panX = dragging.startPanX + (e.clientX - dragging.x);
            lightboxState.panY = dragging.startPanY + (e.clientY - dragging.y);
            applyZoomTransform();
        });
        const endDrag = (e) => {
            if (!dragging) return;
            dragging = null;
            try { img.releasePointerCapture(e.pointerId); } catch {}
            img.classList.remove('is-grabbing');
        };
        img.addEventListener('pointerup', endDrag);
        img.addEventListener('pointercancel', endDrag);

        lightbox = {
            overlay, img, fallback, fallbackLink, caption, counter,
            downloadBtn, openTabBtn, prevBtn, nextBtn,
            closeLightbox, renderCurrent, step, openLightboxA11y,
        };
        return lightbox;
    }

    function openAttachmentLightbox(arg1, arg2) {
        // Accepts either:
        //   openAttachmentLightbox({ src, name|title, type, isImage })
        //   openAttachmentLightbox([{...}, {...}], startIndex)
        // The list form powers multi-image navigation. `title` is accepted as
        // an alias for `name` for backward-compat with older callers.
        const normalize = (it) => {
            if (!it || !it.src) return null;
            return {
                src: it.src,
                name: it.name || it.title || it.downloadName || '',
                type: it.type || 'image',
                isImage: it.isImage !== false,
            };
        };
        let items, index;
        if (Array.isArray(arg1)) {
            items = arg1.map(normalize).filter(Boolean);
            index = Math.max(0, Math.min(items.length - 1, arg2 || 0));
        } else {
            const single = normalize(arg1);
            if (!single) return;
            items = [single];
            index = 0;
        }
        if (!items.length) return;

        const lb = ensureLightbox();
        lightboxState.items = items;
        lightboxState.index = index;
        lb.renderCurrent();
        lb.overlay.classList.add('open');
        lb.openLightboxA11y();
    }

    // Expose to modules (e.g. prose.js) that render images outside this
    // DOMContentLoaded closure but still want the same lightbox.
    window.__xelasphereOpenLightbox = openAttachmentLightbox;

    function enableFileOpen(container, attachment) {
        if (!container || !attachment) return;
        const getUrl = () => attachment.previewUrl || attachment.dataUrl || '';
        container.classList.add('clickable-preview');
        container.addEventListener('click', (e) => {
            if (e.target.closest('.close-button')) return; // allow closing without opening
            const url = getUrl();
            if (url) {
                // Use FilePreview for text/code files, lightbox for others
                if (typeof FilePreview !== 'undefined' && FilePreview.isTextPreviewable(attachment.name)) {
                    FilePreview.openPreviewModal({
                        original_filename: attachment.name,
                        name: attachment.name,
                        mime_type: attachment.type,
                        url: url,
                        dataUrl: attachment.dataUrl
                    });
                } else {
                    openAttachmentLightbox({ src: url, name: attachment.name, type: attachment.type, isImage: false });
                }
            }
        });
    }

    function performUpload(files, addedAttachments = []) {
        const formData = new FormData(form);
        files.forEach((file, index) => {
            formData.append(`file${index}`, file);
        });

        const csrfToken = getCSRFToken();
        const xhr = new XMLHttpRequest();

        // Progress tracking state
        let uploadComplete = false;
        let processingInterval = null;
        let currentProgress = 0;

        // Phase 1: Track actual upload progress (0-80%)
        xhr.upload.addEventListener('progress', (e) => {
            if (e.lengthComputable) {
                // Map upload progress to 0-80%
                currentProgress = (e.loaded / e.total) * 80;
                addedAttachments.forEach(att => {
                    updateUploadProgress(att.id, currentProgress);
                });
            }
        });

        // When upload bytes are sent, start processing phase
        xhr.upload.addEventListener('load', () => {
            uploadComplete = true;
            currentProgress = 80;

            // Phase 2: Animate 80-99% while waiting for server processing
            addedAttachments.forEach(att => {
                updateUploadProgress(att.id, 80, 'Processing...');
            });

            processingInterval = setInterval(() => {
                if (currentProgress < 99) {
                    // Slow down as we approach 99%
                    const remaining = 99 - currentProgress;
                    currentProgress += remaining * 0.08;
                    addedAttachments.forEach(att => {
                        updateUploadProgress(att.id, currentProgress, 'Processing...');
                    });
                }
            }, 200);
        });

        xhr.addEventListener('load', () => {
            // Clear processing animation
            if (processingInterval) clearInterval(processingInterval);

            // Jump to 100%
            addedAttachments.forEach(att => {
                updateUploadProgress(att.id, 100);
            });

            if (xhr.status >= 200 && xhr.status < 300) {
                try {
                    const data = JSON.parse(xhr.responseText);
                    if (data?.files && Array.isArray(data.files)) {
                        data.files.forEach(fileData => {
                            const originalName = fileData?.original_filename;
                            const relativePath = fileData?.relative_path;
                            if (!originalName || !relativePath) return;
                            const attachment = addedAttachments.find(att => att.name === originalName);
                            if (!attachment) return;
                            const cleanedPath = relativePath.replace(/^\/+/, '');
                            const normalizedPath = cleanedPath.startsWith('uploads/')
                                ? `/${cleanedPath}`
                                : `/uploads/${cleanedPath}`;
                            attachment.previewUrl = normalizedPath;
                            // Store extracted text for RAG
                            if (fileData.extracted_text) {
                                attachment.extractedText = fileData.extracted_text;
                                console.log(`Extracted ${fileData.extracted_text.length} chars from ${originalName}`);
                            }
                        });
                    }
                } catch (err) {
                    console.error('Failed to parse upload response:', err);
                }
            } else {
                console.error('Upload failed with status:', xhr.status);
            }

            // Small delay to show 100%, then mark complete
            setTimeout(() => {
                addedAttachments.forEach(att => {
                    markUploadComplete(att.id);
                    if (typeof att.resolveUpload === 'function') {
                        att.resolveUpload();
                    }
                });
            }, 300);
        });

        xhr.addEventListener('error', () => {
            if (processingInterval) clearInterval(processingInterval);
            console.error('Upload failed: network error');
            addedAttachments.forEach(att => {
                markUploadComplete(att.id);
                if (typeof att.resolveUpload === 'function') {
                    att.resolveUpload();
                }
            });
        });

        xhr.open('POST', '/upload');
        xhr.setRequestHeader('X-CSRFToken', csrfToken);
        xhr.send(formData);
    }

});


function togglePause() {
    const pauseElement = document.getElementById('pauseButton'); // Replace with the actual ID
    if (pauseElement) {
        pauseElement.style.display = pauseElement.style.display === 'none' ? 'block' : 'none';
    } else {
        console.error('Element with ID "pauseButton" not found.');
    }
}

function toggleDropdown() {
    const dropdownMenu = document.querySelector('.dropdown-menu');
    if (!dropdownMenu) return;

    // Toggle the active class on the dropdown
    dropdownMenu.classList.toggle('active');
    
    // If the dropdown is open, add a click listener on the document
    if (dropdownMenu.classList.contains('active')) {
        document.addEventListener('click', handleClickOutside);
    } else {
        document.removeEventListener('click', handleClickOutside);
    }
}



function handleClickOutside(event) {
    const dropdownMenu = document.querySelector('.dropdown-menu');
    const profileHeader = document.querySelector('.profile-header');
    if (!profileHeader || !dropdownMenu) return;

    // If the click is outside the dropdown and the profile header (or trigger), close the dropdown
    if (!dropdownMenu.contains(event.target) && !profileHeader.contains(event.target)) {
        dropdownMenu.classList.remove('active');
        document.removeEventListener('click', handleClickOutside); // Remove the event listener once the dropdown is closed
    }
}

// Down arrow visibility is handled by scrollState.js via style.display.
// Removed dead code that toggled a nonexistent .visible CSS class.
