// State Variables
let abortController = new AbortController(); // Used to manage and abort ongoing requests

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


// Append an image message to the chat
function appendImageMessage(messageDiv, imageUrl) {
    const layer = document.createElement('div');
    layer.className = 'image-layer revealed';

    const placeholder = document.createElement('div');
    placeholder.className = 'image-placeholder';
    placeholder.style.opacity = '0';
    const shimmer = document.createElement('div');
    shimmer.className = 'image-placeholder__shimmer';
    const glyph = document.createElement('div');
    glyph.className = 'image-placeholder__glyph';
    placeholder.appendChild(shimmer);
    placeholder.appendChild(glyph);

    const img = document.createElement('img');
    img.src = imageUrl;
    img.alt = "Image attachment";
    img.classList.add('chat-image');
    img.style.setProperty('aspect-ratio', '1 / 1');
    img.loading = 'lazy';
    img.addEventListener('click', () => {
        if (typeof openAttachmentLightbox === 'function') {
            openAttachmentLightbox({ src: imageUrl, name: 'Image attachment', type: 'image', isImage: true });
        }
    });
    img.onerror = function() {
        console.error("Failed to load image:", imageUrl);
        layer.textContent = "Image failed to load.";
    };

    layer.appendChild(placeholder);
    layer.appendChild(img);

    const badge = document.createElement('div');
    badge.className = 'image-preview-card__badge';
    badge.innerHTML = '<i class="fas fa-image"></i><span>Image attachment</span>';

    const caption = document.createElement('div');
    caption.className = 'image-preview-card__caption';
    caption.textContent = 'Tap to enlarge';

    const card = document.createElement('figure');
    card.className = 'image-preview-card';
    card.appendChild(badge);
    card.appendChild(layer);
    card.appendChild(caption);

    const imageContainer = document.createElement('div');
    imageContainer.classList.add('image-message');
    imageContainer.appendChild(card);
    messageDiv.appendChild(imageContainer);
}

// Append an animated placeholder for image generation (Grok-style)
function appendImagePlaceholder(messageDiv) {
    const layer = document.createElement('div');
    layer.className = 'image-layer';

    // Create Grok-style placeholder
    const placeholder = document.createElement('div');
    placeholder.className = 'image-placeholder';

    // Aurora background layers
    const aurora = document.createElement('div');
    aurora.className = 'image-placeholder__aurora';
    const auroraSecondary = document.createElement('div');
    auroraSecondary.className = 'image-placeholder__aurora-secondary';

    // Grid overlay
    const grid = document.createElement('div');
    grid.className = 'image-placeholder__grid';

    // Floating particles
    const particles = document.createElement('div');
    particles.className = 'image-placeholder__particles';
    for (let i = 0; i < 6; i++) {
        const particle = document.createElement('div');
        particle.className = 'image-placeholder__particle';
        particles.appendChild(particle);
    }

    // Central orb
    const orb = document.createElement('div');
    orb.className = 'image-placeholder__orb';
    orb.innerHTML = `
        <div class="image-placeholder__orb-glow"></div>
        <div class="image-placeholder__orb-ring"></div>
        <div class="image-placeholder__orb-inner">
            <i class="fas fa-wand-magic-sparkles image-placeholder__orb-icon"></i>
        </div>
    `;

    // Status text and progress
    const status = document.createElement('div');
    status.className = 'image-placeholder__status';
    status.innerHTML = `
        <span class="image-placeholder__text">Creating image...</span>
        <div class="image-placeholder__progress">
            <div class="image-placeholder__progress-bar"></div>
        </div>
    `;

    // Assemble placeholder
    placeholder.appendChild(aurora);
    placeholder.appendChild(auroraSecondary);
    placeholder.appendChild(grid);
    placeholder.appendChild(particles);
    placeholder.appendChild(orb);
    placeholder.appendChild(status);

    const img = document.createElement('img');
    img.className = 'chat-image';
    img.style.setProperty('aspect-ratio', '1 / 1');

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





function updateMessageContent(messageId, responseText) {
    const messageElement = document.getElementById(messageId);
    if (messageElement) {
        messageElement.textContent = responseText;
        scrollManager.scrollToBottom();
    }
}

// Handle messaging errors
function handleMessageError(error, messageId) {
    console.error('Message error:', error);
    if (messageId) {
        updateMessageContent(messageId, `Error: ${error.message}`);
    } else {
        displayMessage(`Error: ${error.message}`, 'system', 'text');
    }
}





// Adjust the text size within a container to fit
function adjustTextSize(container, maxFontSize = 18, minFontSize = 12, step = 0.5) {
    let fontSize = maxFontSize;
    container.style.fontSize = `${fontSize}px`;

    while (container.scrollHeight > container.clientHeight && fontSize > minFontSize) {
        fontSize -= step;
        container.style.fontSize = `${fontSize}px`;
    }
    
    while (container.scrollHeight < container.clientHeight && fontSize < maxFontSize) {
        fontSize += step;
        container.style.fontSize = `${fontSize}px`;
        if (container.scrollHeight > container.clientHeight) {
            fontSize -= step;
            container.style.fontSize = `${fontSize}px`;
            break;
        }
    }
}


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

    // Clear ProgressiveTextDisplay for the current session to prevent duplicates
    const currentSessionId = window.chatManager?.activeSessionId;
    if (currentSessionId && window.ProgressiveTextDisplay) {
        window.ProgressiveTextDisplay.complete(currentSessionId);
        window.ProgressiveTextDisplay.remove(currentSessionId);
    }

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

function autoResize() {
    this.style.height = 'auto';  // Reset height to recalculate
    this.style.height = this.scrollHeight + 'px';  // Set height based on content
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
    if (options) options.style.cssText = '';
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

// Set up textarea auto resize
document.addEventListener('DOMContentLoaded', function() {
    const textarea = document.querySelector('.edit-textarea');
    if (textarea) {
        textarea.addEventListener('input', autoResize, false);
    }
});

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

    fileInput.addEventListener('change', handleFileChange);
    sendButton.addEventListener('click', () => {
        // Mobile: Dismiss keyboard immediately
        dismissMobileKeyboard();

        if (sendButton.classList.contains('show-stop')) {
            abortMessage();
        } else {
            sendMessage();
        }
    });

    stopIcon.addEventListener('click', abortMessage);
    if (actionsToggle && actionsMenu) {
        const closeActions = () => {
            actionsMenu.classList.remove('open');
            actionsToggle.setAttribute('aria-expanded', 'false');
        };
        closeActionsMenu = closeActions;

        actionsToggle.addEventListener('click', (e) => {
            e.stopPropagation();
            const willOpen = !actionsMenu.classList.contains('open');
            actionsMenu.classList.toggle('open');
            actionsToggle.setAttribute('aria-expanded', willOpen ? 'true' : 'false');
        });

        document.addEventListener('click', (e) => {
            if (!actionsMenu.contains(e.target)) {
                closeActions();
            }
        });
    }

    if (searchToggleLabel && searchCheckbox) {
        searchCheckbox.addEventListener('change', () => {
            searchToggleLabel.setAttribute('aria-checked', searchCheckbox.checked ? 'true' : 'false');
            searchToggleLabel.classList.toggle('active', searchCheckbox.checked);
        });
        searchToggleLabel.classList.toggle('active', searchCheckbox.checked);
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

    function ensureLightbox() {
        if (lightbox) return lightbox;
        const overlay = document.createElement('div');
        overlay.className = 'image-lightbox';

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

        const close = document.createElement('button');
        close.type = 'button';
        close.className = 'image-lightbox__close';
        close.innerHTML = '&times;';

        content.appendChild(img);
        content.appendChild(fallback);
        content.appendChild(caption);
        content.appendChild(close);
        overlay.appendChild(content);
        document.body.appendChild(overlay);

        const closeLightbox = () => {
            overlay.classList.remove('open');
            img.src = '';
        };

        overlay.addEventListener('click', (e) => {
            if (e.target === overlay || e.target.closest('.image-lightbox__close')) {
                closeLightbox();
            }
        });

        document.addEventListener('keydown', (e) => {
            if (overlay.classList.contains('open') && e.key === 'Escape') {
                closeLightbox();
            }
        });

        lightbox = { overlay, img, fallback, fallbackLink, caption, closeLightbox };
        return lightbox;
    }

    function openAttachmentLightbox({ src, name, type, isImage }) {
        if (!src) return;
        const { overlay, img, fallback, fallbackLink, caption } = ensureLightbox();

        // Reset visibility
        img.style.display = 'none';
        fallback.style.display = 'none';

        caption.textContent = name || '';

        if (isImage) {
            img.src = src;
            img.alt = name || 'Attachment preview';
            img.style.display = 'block';
        } else {
            // Avoid CSP issues with blob/data by using an explicit download/open link
            fallback.style.display = 'block';
            fallbackLink.href = src;
        }

        overlay.classList.add('open');
    }

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

    // Auto-expand text area function
    function autoExpandTextArea(element) {
        // Reset the height
        element.style.height = 'auto';
    
        // Set the new height
        element.style.height = element.scrollHeight + 'px';
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
    const profileHeader = document.querySelector('.profile-header'); // Assuming this is the trigger for the dropdown

    // If the click is outside the dropdown and the profile header (or trigger), close the dropdown
    if (!dropdownMenu.contains(event.target) && !profileHeader.contains(event.target)) {
        dropdownMenu.classList.remove('active');
        document.removeEventListener('click', handleClickOutside); // Remove the event listener once the dropdown is closed
    }
}

document.addEventListener("DOMContentLoaded", function () {
    const textareaContainer = document.querySelector(".textarea-container");
    const downArrow = document.getElementById("downArrow");

    // Function to toggle visibility of the arrow button
    function toggleArrowVisibility() {
        if (textareaContainer.scrollHeight > textareaContainer.clientHeight) {
            // Show the arrow if the container is scrollable
            downArrow.classList.add("visible");
        } else {
            // Hide the arrow if there's no overflow
            downArrow.classList.remove("visible");
        }
    }

    // Function to check if the user has scrolled to the bottom
    function handleScroll() {
        const isAtBottom =
            textareaContainer.scrollHeight -
            textareaContainer.scrollTop -
            textareaContainer.clientHeight <=
            1;

        if (isAtBottom) {
            // Hide the arrow when at the bottom
            downArrow.classList.remove("visible");
        } else {
            // Show the arrow otherwise
            downArrow.classList.add("visible");
        }
    }

    // Attach event listeners
    textareaContainer.addEventListener("scroll", handleScroll);

    // Initialize on load
    toggleArrowVisibility();

    // Recheck on window resize
    window.addEventListener("resize", toggleArrowVisibility);
});
