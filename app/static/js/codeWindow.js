// Typewriter animation for session titles (ChatGPT-style)
function typewriteTitle(element, text, speed = 28) {
    // Cancel any in-progress typewriter on this element
    if (element._typewriteTimer) {
        clearTimeout(element._typewriteTimer);
        element._typewriteTimer = null;
    }
    element.textContent = '';
    let i = 0;
    function tick() {
        if (i < text.length) {
            element.textContent += text[i];
            i++;
            element._typewriteTimer = setTimeout(tick, speed);
        } else {
            element._typewriteTimer = null;
        }
    }
    tick();
}

// Incremental JSON parser helpers for live patch streaming
function scanClosingQuote(str, from) {
    for (let i = from; i < str.length; i++) {
        if (str[i] === '\\') { i++; continue; }
        if (str[i] === '"') return i;
    }
    return -1;
}

function unescapePatchJsonString(str) {
    return str.replace(/\\(n|t|r|"|\\|\/)/g, (_, ch) => {
        switch (ch) {
            case 'n': return '\n';
            case 't': return '\t';
            case 'r': return '\r';
            case '"': return '"';
            case '\\': return '\\';
            case '/': return '/';
            default: return _;
        }
    });
}

function extractCompletePatchesFromBuffer(buffer, skip) {
    const patches = [];
    let searchFrom = 0;

    while (searchFrom < buffer.length) {
        const fileIdx = buffer.indexOf('"file"', searchFrom);
        if (fileIdx === -1) break;

        const fileMatch = buffer.slice(fileIdx).match(/"file"\s*:\s*"([^"]+)"/);
        if (!fileMatch) break;

        const oldIdx = buffer.indexOf('"old_string"', fileIdx);
        if (oldIdx === -1) break;

        const oldColonQuote = buffer.indexOf('"', oldIdx + '"old_string"'.length + 1);
        if (oldColonQuote === -1) break;
        const oldEnd = scanClosingQuote(buffer, oldColonQuote + 1);
        if (oldEnd === -1) break;

        const newIdx = buffer.indexOf('"new_string"', oldEnd);
        if (newIdx === -1) break;

        const newColonQuote = buffer.indexOf('"', newIdx + '"new_string"'.length + 1);
        if (newColonQuote === -1) break;
        const newEnd = scanClosingQuote(buffer, newColonQuote + 1);
        if (newEnd === -1) break;

        patches.push({
            file: fileMatch[1],
            old_string: unescapePatchJsonString(buffer.slice(oldColonQuote + 1, oldEnd)),
            new_string: unescapePatchJsonString(buffer.slice(newColonQuote + 1, newEnd)),
        });

        searchFrom = newEnd + 1;
    }

    return patches.slice(skip);
}

// Configuration object for constants
const CONFIG = Object.freeze({
    ICONS: {
        COPY: 'fas fa-copy'
    },
    CLASSES: {
        DARK_MODE: 'dark-mode'
    }
});

// ==================== Centralized Asset Config ==================== //
// These are loaded from /api/crypto/config to stay in sync with backend
// Minimal fallbacks for top 10 coins (rest use API response directly)

// Fallback ticker mappings for top 10 coins (used before API loads)
const TICKER_FALLBACKS = {
    'bitcoin': 'BTC', 'btc': 'BTC',
    'ethereum': 'ETH', 'eth': 'ETH',
    'solana': 'SOL', 'sol': 'SOL',
    'ripple': 'XRP', 'xrp': 'XRP',
    'cardano': 'ADA', 'ada': 'ADA',
    'dogecoin': 'DOGE', 'doge': 'DOGE',
    'binancecoin': 'BNB', 'bnb': 'BNB',
    'polkadot': 'DOT', 'dot': 'DOT',
    'chainlink': 'LINK', 'link': 'LINK',
    'litecoin': 'LTC', 'ltc': 'LTC'
};

// Fallback display names for top 10 coins (used before API loads)
const NAME_FALLBACKS = {
    'bitcoin': 'Bitcoin', 'btc': 'Bitcoin',
    'ethereum': 'Ethereum', 'eth': 'Ethereum',
    'solana': 'Solana', 'sol': 'Solana',
    'ripple': 'XRP', 'xrp': 'XRP',
    'cardano': 'Cardano', 'ada': 'Cardano',
    'dogecoin': 'Dogecoin', 'doge': 'Dogecoin',
    'binancecoin': 'BNB', 'bnb': 'BNB',
    'polkadot': 'Polkadot', 'dot': 'Polkadot',
    'chainlink': 'Chainlink', 'link': 'Chainlink',
    'litecoin': 'Litecoin', 'ltc': 'Litecoin'
};

let ASSET_NAMES = {};  // Will be populated from API
let CRYPTO_TICKERS = {};  // Will be populated from API

// Load asset config from API (called by cryptoChartModule or on demand)
let _assetConfigLoaded = false;
function loadAssetConfig(config) {
    if (config && config.asset_names) {
        ASSET_NAMES = config.asset_names;
        _assetConfigLoaded = true;
    }
    if (config && config.crypto_tickers) {
        CRYPTO_TICKERS = config.crypto_tickers;
    }
}

// Make loadAssetConfig available globally for cryptoChartModule
window.loadAssetConfig = loadAssetConfig;

/**
 * Generate intro text for chart display
 * @param {string} asset - The asset ID or symbol
 * @param {string} assetType - 'crypto' or 'stock'
 * @returns {string} The formatted intro text
 */
function generateChartIntroText(asset, assetType) {
    const assetLower = asset.toLowerCase();
    // Use API config if loaded, otherwise fall back to hardcoded defaults
    const displayName = ASSET_NAMES[assetLower] || NAME_FALLBACKS[assetLower] || asset.charAt(0).toUpperCase() + asset.slice(1);
    // Use proper ticker symbol mapping for crypto, uppercase for stocks
    const symbol = assetType === 'stock'
        ? asset.toUpperCase()
        : (CRYPTO_TICKERS[assetLower] || TICKER_FALLBACKS[assetLower] || asset.toUpperCase());

    return `Here's the current ${displayName} price (${symbol}) as of the latest live market data:`;
}

// Make available globally for other modules
window.generateChartIntroText = generateChartIntroText;

// ==================== Background Request Manager ====================
// Manages AI requests that continue running when user switches sessions
const BackgroundRequestManager = (() => {
    // Track ongoing requests by session ID
    const activeRequests = new Map();
    // Track sessions with completed but unviewed responses
    const pendingNotifications = new Set();
    // Event listeners
    const listeners = new Map();

    class SessionRequest {
        constructor(sessionId, prompt, model, pageId) {
            this.sessionId = sessionId;
            this.prompt = prompt;
            this.model = model;
            this.pageId = pageId;
            this.status = 'pending'; // pending, streaming, completed, error
            this.abortController = new AbortController();
            this.startTime = Date.now();

            // Buffered content
            this.reasoning = '';
            this.content = '';
            this.chatSummary = '';
            this.artifact = null;
            this.renderMode = 'inline';

            // For non-streaming models
            this.fullResponse = null;
        }
    }

    function emit(event, data) {
        const eventListeners = listeners.get(event) || [];
        eventListeners.forEach(callback => {
            try {
                callback(data);
            } catch (e) {
                console.error('BackgroundRequestManager event listener error:', e);
            }
        });
    }

    function on(event, callback) {
        if (!listeners.has(event)) {
            listeners.set(event, []);
        }
        listeners.get(event).push(callback);
    }

    function off(event, callback) {
        if (!listeners.has(event)) return;
        const eventListeners = listeners.get(event);
        const index = eventListeners.indexOf(callback);
        if (index > -1) {
            eventListeners.splice(index, 1);
        }
    }

    function startRequest(sessionId, prompt, model, pageId, formData) {
        console.log('BackgroundRequestManager.startRequest:', { sessionId, model });

        // If there's already a request for this session, abort it
        if (activeRequests.has(sessionId)) {
            console.log('Aborting existing request for session:', sessionId);
            const existing = activeRequests.get(sessionId);
            existing.abortController.abort();
        }

        const request = new SessionRequest(sessionId, prompt, model, pageId);
        activeRequests.set(sessionId, request);
        request.status = 'streaming';

        console.log('Request registered:', { sessionId, status: request.status });

        // Show processing indicator on tab
        updateSessionProcessingIndicator(sessionId, true);

        emit('requestStarted', { sessionId, request });

        return request;
    }

    function updateRequest(sessionId, updates) {
        const request = activeRequests.get(sessionId);
        if (!request) return;

        Object.assign(request, updates);
        emit('requestUpdated', { sessionId, request, updates });
    }

    function appendReasoning(sessionId, text) {
        const request = activeRequests.get(sessionId);
        if (!request) return;

        request.reasoning += text;
        emit('reasoningUpdated', { sessionId, text, fullReasoning: request.reasoning });
    }

    function appendContent(sessionId, text) {
        const request = activeRequests.get(sessionId);
        if (!request) return;

        request.content += text;
        emit('contentUpdated', { sessionId, text, fullContent: request.content });
    }

    function completeRequest(sessionId, finalData) {
        console.log('BackgroundRequestManager.completeRequest:', {
            sessionId,
            hasRequest: activeRequests.has(sessionId),
            finalDataKeys: Object.keys(finalData || {})
        });

        const request = activeRequests.get(sessionId);
        if (!request) {
            console.warn('BackgroundRequestManager: No request found for session:', sessionId);
            return;
        }

        request.status = 'completed';
        Object.assign(request, finalData);

        console.log('Request marked complete:', {
            sessionId,
            status: request.status,
            hasDisplayContent: !!request.displayContent
        });

        // Hide processing indicator
        updateSessionProcessingIndicator(sessionId, false);

        // Check if this session is currently active
        const activeSessionId = window.chatManager?.getActiveSessionId?.() ||
                               window.chatManager?.activeSessionId;

        // Only add notification if:
        // 1. We have both session IDs (not undefined/null)
        // 2. They are DIFFERENT (user is in a different session)
        const isInBackground = activeSessionId && sessionId && activeSessionId !== sessionId;

        console.log('completeRequest - checking for notification:', {
            requestSessionId: sessionId,
            activeSessionId,
            isInBackground
        });

        if (isInBackground) {
            // Session is in background - add notification
            console.log('Adding notification dot for background session:', sessionId);
            pendingNotifications.add(sessionId);
            emit('backgroundComplete', { sessionId, request });
            updateSessionNotificationDot(sessionId, true);

            // Notify other tabs via BroadcastChannel
            window.UnreadNotificationManager?.notifyNewMessage?.(sessionId);
        } else {
            console.log('Session is active or IDs missing, no notification needed');
        }

        emit('requestCompleted', { sessionId, request });
    }

    function errorRequest(sessionId, error) {
        const request = activeRequests.get(sessionId);
        if (!request) return;

        request.status = 'error';
        request.error = error;

        // Hide processing indicator
        updateSessionProcessingIndicator(sessionId, false);

        emit('requestError', { sessionId, error, request });
    }

    function getRequest(sessionId) {
        return activeRequests.get(sessionId);
    }

    function hasActiveRequest(sessionId) {
        const request = activeRequests.get(sessionId);
        return request && (request.status === 'pending' || request.status === 'streaming');
    }

    function clearRequest(sessionId) {
        activeRequests.delete(sessionId);
    }

    function hasPendingNotification(sessionId) {
        return pendingNotifications.has(sessionId);
    }

    function clearNotification(sessionId) {
        pendingNotifications.delete(sessionId);
        updateSessionNotificationDot(sessionId, false);
    }

    function updateSessionNotificationDot(sessionId, show) {
        // Find the session tab and toggle has-notification class
        const tabElement = document.querySelector(`.chat-tab[data-session-id="${sessionId}"]`);
        if (tabElement) {
            tabElement.classList.toggle('has-notification', show);
        }
        // Also emit event for sidebar updates
        emit('notificationChanged', { sessionId, hasNotification: show });
    }

    function updateSessionProcessingIndicator(sessionId, show) {
        // Processing indicator removed - function kept as no-op for compatibility
    }

    function abortRequest(sessionId) {
        const request = activeRequests.get(sessionId);
        if (request) {
            request.abortController.abort();
            request.status = 'aborted';
            emit('requestAborted', { sessionId });
        }
    }

    return {
        startRequest,
        updateRequest,
        appendReasoning,
        appendContent,
        completeRequest,
        errorRequest,
        getRequest,
        hasActiveRequest,
        clearRequest,
        hasPendingNotification,
        clearNotification,
        abortRequest,
        updateSessionProcessingIndicator,
        updateSessionNotificationDot,
        on,
        off,
        emit
    };
})();

window.BackgroundRequestManager = BackgroundRequestManager;

// ==================== Progressive Text Display ====================
// Displays text progressively using chunk streaming (like real AI streaming)
const ProgressiveTextDisplay = (() => {
    // Track active displays by session ID
    const activeDisplays = new Map();

    // Streaming configuration
    const MIN_CHUNK_SIZE = 30;  // Minimum characters per chunk
    const MAX_CHUNK_SIZE = 80;  // Maximum characters per chunk
    const MIN_DELAY = 100;      // Minimum ms between chunks
    const MAX_DELAY = 200;      // Maximum ms between chunks

    class TextDisplay {
        constructor(sessionId, text, container, options = {}) {
            this.sessionId = sessionId;
            this.fullText = text;
            // Split into streaming chunks (sentences or size-based)
            this.chunks = this.createChunks(text);
            this.container = container;  // Can be null for background streaming
            this.chunkIndex = 0;
            this.outputText = '';
            this.isComplete = false;
            this.isPaused = false;
            this.onComplete = options.onComplete || null;
            this.isCode = options.isCode || false;
            this.timeoutId = null;
            // Store completion data for background sessions
            this.completionData = options.completionData || null;
            this.isBackgroundStream = !container;
            // IntersectionObserver for smart scrolling
            this.scrollObserver = null;
            this.cursorVisible = true;
            this.setupScrollObserver();
        }

        // Setup IntersectionObserver for smart scrolling
        setupScrollObserver() {
            if (typeof IntersectionObserver === 'undefined') return;

            // Get composer height for root margin calculation
            const composerHeight = getComputedStyle(document.documentElement)
                .getPropertyValue('--composer-reserved-height') || '150px';
            const bottomMargin = parseInt(composerHeight) + 60;

            this.scrollObserver = new IntersectionObserver(
                (entries) => {
                    entries.forEach(entry => {
                        this.cursorVisible = entry.isIntersecting;
                    });
                },
                {
                    root: null, // viewport
                    rootMargin: `-80px 0px -${bottomMargin}px 0px`, // top, right, bottom, left
                    threshold: 0
                }
            );
        }

        // Scroll cursor into view only if it left the viewport AND user is pinned to bottom
        scrollIfNeeded() {
            // Don't scroll if keeping user message at top (ChatGPT-style)
            if (window._keepMessageAtTop) {
                return;
            }
            // Respect user scroll intent - don't scroll if user scrolled up
            if (window.scrollState && !window.scrollState.isPinnedToBottom) {
                return;
            }
            if (!this.cursorVisible) {
                const cursor = this.container?.querySelector('.streaming-cursor');
                if (cursor) {
                    cursor.scrollIntoView({ behavior: 'smooth', block: 'center' });
                }
            }
        }

        // Observe cursor element
        observeCursor(cursor) {
            if (this.scrollObserver && cursor) {
                this.scrollObserver.disconnect();
                this.scrollObserver.observe(cursor);
            }
        }

        // Cleanup observer
        destroyObserver() {
            if (this.scrollObserver) {
                this.scrollObserver.disconnect();
                this.scrollObserver = null;
            }
        }

        // Create chunks that simulate streaming (sentences or character-based)
        createChunks(text) {
            const chunks = [];
            let remaining = text;

            while (remaining.length > 0) {
                // Try to find a sentence boundary within max chunk size
                let chunkSize = Math.min(remaining.length, MAX_CHUNK_SIZE);
                let chunk = remaining.substring(0, chunkSize);

                // Look for sentence endings (. ! ? followed by space or end)
                const sentenceEnd = chunk.search(/[.!?][\s<]|[.!?]$/);
                if (sentenceEnd > MIN_CHUNK_SIZE) {
                    // Found a sentence boundary, cut there
                    chunkSize = sentenceEnd + 1;
                    // Include trailing space if present
                    if (remaining[chunkSize] === ' ') chunkSize++;
                } else if (chunkSize >= MIN_CHUNK_SIZE) {
                    // No sentence boundary, try to break at word boundary
                    const lastSpace = chunk.lastIndexOf(' ');
                    if (lastSpace > MIN_CHUNK_SIZE) {
                        chunkSize = lastSpace + 1;
                    }
                }

                chunks.push(remaining.substring(0, chunkSize));
                remaining = remaining.substring(chunkSize);
            }

            return chunks;
        }

        // Get random delay for natural streaming feel
        getRandomDelay() {
            return MIN_DELAY + Math.random() * (MAX_DELAY - MIN_DELAY);
        }

        // Advance by one chunk
        advance() {
            if (this.isComplete || this.isPaused) return false;

            if (this.chunkIndex < this.chunks.length) {
                this.outputText += this.chunks[this.chunkIndex++];
                return true;
            } else {
                this.isComplete = true;
                return false;
            }
        }

        // Schedule next chunk with random delay
        scheduleNextChunk() {
            if (this.isComplete || this.isPaused) return;

            this.timeoutId = setTimeout(() => {
                const activeSessionId = window.chatManager?.activeSessionId;
                const isActiveSession = this.sessionId === activeSessionId;

                // Always advance chunks (even for background sessions)
                const advanced = this.advance();

                // Only update DOM if this is the active session AND we have a container
                if (advanced && isActiveSession && this.container) {
                    this.updateDOM();

                    // Auto-scroll (only if user is pinned to bottom)
                    if (window.scrollState?.isPinnedToBottom) {
                        if (typeof window.scrollToBottom === 'function') {
                            window.scrollToBottom(false);
                        }
                    }
                }

                // Handle completion
                if (this.isComplete) {
                    // Update DOM if visible
                    if (this.container) {
                        this.updateDOM();
                    }

                    if (this.onComplete) {
                        this.onComplete();
                    }

                    // Reset send button if this was the active session
                    if (isActiveSession) {
                        if (typeof resetSendButtonIfIdle === 'function') {
                            resetSendButtonIfIdle();
                        } else if (typeof updateSendButton === 'function') {
                            updateSendButton(false);
                        }
                    } else {
                        // Show blue dot ONLY after streaming completes for background sessions
                        console.log('Background streaming complete for session:', this.sessionId);
                        BackgroundRequestManager.updateSessionNotificationDot(this.sessionId, true);
                    }
                } else {
                    // Schedule next chunk
                    this.scheduleNextChunk();
                }
            }, this.getRandomDelay());
        }

        // Start the streaming
        start() {
            this.scheduleNextChunk();
        }

        // Get current display text
        getCurrentText() {
            return this.outputText;
        }

        // Get progress percentage
        getProgress() {
            return this.chunks.length > 0
                ? (this.chunkIndex / this.chunks.length) * 100
                : 100;
        }

        // Update DOM if container exists
        updateDOM() {
            if (!this.container || !document.body.contains(this.container)) {
                return false;
            }

            if (this.isComplete) {
                this.container.innerHTML = this.fullText;
                this.cursorElement = null;
                this.destroyObserver();
            } else {
                // Get the latest chunk for fade-in effect
                const latestChunk = this.chunks[this.chunkIndex - 1] || '';
                const previousText = this.outputText.slice(0, -latestChunk.length);
                const fadeInChunk = latestChunk ? `<span class="stream-fade-in">${latestChunk}</span>` : '';

                // Create persistent cursor once (stored on instance)
                if (!this.cursorElement) {
                    this.cursorElement = document.createElement('span');
                    this.cursorElement.className = 'streaming-cursor';
                }

                // Update text content
                this.container.innerHTML = previousText + fadeInChunk;

                // Append cursor to the last element so it stays inline
                const lastChild = this.container.lastElementChild;
                if (lastChild) {
                    lastChild.appendChild(this.cursorElement);
                } else {
                    this.container.appendChild(this.cursorElement);
                }

                // Smart scroll: only scroll if cursor left viewport
                this.observeCursor(this.cursorElement);
                this.scrollIfNeeded();
            }
            return true;
        }

        // Complete immediately (show all text)
        complete() {
            if (this.timeoutId) {
                clearTimeout(this.timeoutId);
                this.timeoutId = null;
            }
            this.outputText = this.fullText;
            this.chunkIndex = this.chunks.length;
            this.isComplete = true;
            this.destroyObserver();
            // Fire onComplete so pending Promises resolve (e.g. handleComplexMessage chain)
            if (this.onComplete) {
                this.onComplete();
                this.onComplete = null;
            }
        }

        // Pause the display
        pause() {
            this.isPaused = true;
            if (this.timeoutId) {
                clearTimeout(this.timeoutId);
                this.timeoutId = null;
            }
        }

        // Resume the display
        resume() {
            this.isPaused = false;
            this.scheduleNextChunk();
        }

        // Attach to a new container (when switching back to session)
        attachToContainer(container) {
            this.container = container;
            this.updateDOM();
        }
    }

    // Start a new progressive display
    function start(sessionId, text, container, options = {}) {
        // Remove any existing display for this session
        if (activeDisplays.has(sessionId)) {
            const existing = activeDisplays.get(sessionId);
            existing.complete();
        }

        const display = new TextDisplay(sessionId, text, container, options);
        activeDisplays.set(sessionId, display);

        // Initial DOM update
        display.updateDOM();

        // Start chunk streaming for this display
        display.start();

        return display;
    }

    // Get display for a session
    function getDisplay(sessionId) {
        return activeDisplays.get(sessionId);
    }

    // Check if session has active display (not complete)
    function hasActiveDisplay(sessionId) {
        const display = activeDisplays.get(sessionId);
        return display && !display.isComplete;
    }

    // Check if session has a completed display (notification pending)
    function hasCompletedDisplay(sessionId) {
        const display = activeDisplays.get(sessionId);
        return display && display.isComplete;
    }

    // Resume display for a session (when switching back)
    function resume(sessionId, container) {
        const display = activeDisplays.get(sessionId);
        if (!display) return null;

        display.attachToContainer(container);

        // Resume streaming if not complete
        if (!display.isComplete) {
            display.resume();
        }

        return display;
    }

    // Complete display immediately
    function complete(sessionId) {
        const display = activeDisplays.get(sessionId);
        if (display) {
            display.complete();
            display.updateDOM();
        }
    }

    // Remove display
    function remove(sessionId) {
        activeDisplays.delete(sessionId);
    }

    // Clear all displays
    function clear() {
        // Stop all active displays
        activeDisplays.forEach(display => {
            display.complete();
        });
        activeDisplays.clear();
    }

    return {
        start,
        getDisplay,
        hasActiveDisplay,
        hasCompletedDisplay,
        resume,
        complete,
        remove,
        clear
    };
})();

window.ProgressiveTextDisplay = ProgressiveTextDisplay;

// ==================== Thinking Indicator System ====================
const ThinkingIndicator = (() => {
    const TIPS = [
        'You can use Shift+Enter for a new line without sending',
        'Try asking follow-up questions for deeper insights',
        'You can attach images and documents to your messages',
        'Use the search toggle for web-enhanced responses',
        'DeepSeek R1 uses chain-of-thought reasoning for complex problems',
        'You can edit and regenerate previous messages',
        'Try being specific - detailed prompts get better results',
        'You can switch models anytime using the dropdown',
        'Code artifacts can be previewed and downloaded',
        'Your chat history is saved automatically',
        'Use Voice Mode for hands-free conversations',
        'Click the microphone to dictate your messages',
        'Click the speaker icon to hear AI responses read aloud',
        'URLs you paste will show rich preview cards',
        'Switch between dark and light themes in settings'
    ];

    let indicatorElement = null;
    let tipInterval = null;
    let startTime = null;
    let elapsedInterval = null;
    let isStreaming = false;
    let reasoningText = '';

    function create(streamMode = false) {
        if (indicatorElement) {
            indicatorElement.remove();
            indicatorElement = null;
        }

        indicatorElement = document.createElement('div');
        indicatorElement.className = 'thinking-indicator';

        if (streamMode) {
            // Streaming mode with expandable reasoning
            indicatorElement.innerHTML = `
                <div class="thinking-header" role="button" tabindex="0">
                    <div class="thinking-content">
                        <div class="thinking-dots">
                            <span></span><span></span><span></span>
                        </div>
                        <div class="thinking-text">
                            <span class="thinking-message">Thinking...</span>
                            <span class="thinking-elapsed"></span>
                        </div>
                    </div>
                    <span class="thinking-chevron">▼</span>
                </div>
                <div class="thinking-reasoning collapsed">
                    <div class="reasoning-stream"></div>
                </div>
                <div class="thinking-tip">
                    <span class="tip-icon">💡</span>
                    <span class="tip-text">${TIPS[Math.floor(Math.random() * TIPS.length)]}</span>
                </div>
            `;

            // Toggle reasoning visibility
            const header = indicatorElement.querySelector('.thinking-header');
            const reasoningDiv = indicatorElement.querySelector('.thinking-reasoning');
            const chevron = indicatorElement.querySelector('.thinking-chevron');

            header.addEventListener('click', () => {
                const isCollapsed = reasoningDiv.classList.contains('collapsed');
                reasoningDiv.classList.toggle('collapsed', !isCollapsed);
                chevron.textContent = isCollapsed ? '▲' : '▼';
                chevron.classList.toggle('expanded', isCollapsed);
            });

            header.addEventListener('keydown', (e) => {
                if (e.key === 'Enter' || e.key === ' ') {
                    e.preventDefault();
                    header.click();
                }
            });
        } else {
            // Simple mode without streaming
            indicatorElement.innerHTML = `
                <div class="thinking-content">
                    <div class="thinking-dots">
                        <span></span><span></span><span></span>
                    </div>
                    <div class="thinking-text">
                        <span class="thinking-message">Thinking...</span>
                        <span class="thinking-elapsed"></span>
                    </div>
                </div>
                <div class="thinking-tip">
                    <span class="tip-icon">💡</span>
                    <span class="tip-text">${TIPS[Math.floor(Math.random() * TIPS.length)]}</span>
                </div>
            `;
        }

        return indicatorElement;
    }

    function show(streamMode = false) {
        // Always use stream mode for consistent indicator
        streamMode = true;
        console.log('ThinkingIndicator.show called, streamMode:', streamMode);
        const chatDisplay = document.getElementById('chat-display');
        if (!chatDisplay) {
            console.warn('ThinkingIndicator: chatDisplay not found!');
            return;
        }

        isStreaming = streamMode;
        reasoningText = '';

        const indicator = create(streamMode);
        chatDisplay.appendChild(indicator);
        console.log('ThinkingIndicator: element created and appended');

        requestAnimationFrame(() => {
            indicator.classList.add('visible');
            // Scroll indicator into view only if not keeping user message at top
            if (!window._keepMessageAtTop && window.scrollState?.isPinnedToBottom !== false) {
                indicator.scrollIntoView({ behavior: 'smooth', block: 'end' });
            } else {
            }
        });

        startTime = Date.now();

        // Update elapsed time every second
        elapsedInterval = setInterval(() => {
            const elapsed = Math.floor((Date.now() - startTime) / 1000);
            const elapsedEl = indicator.querySelector('.thinking-elapsed');
            if (elapsedEl && elapsed >= 3) {
                elapsedEl.textContent = `${elapsed}s`;
            }
        }, 1000);

        // Change tip every 8 seconds
        tipInterval = setInterval(() => {
            const tipEl = indicator.querySelector('.tip-text');
            if (tipEl) {
                tipEl.style.opacity = '0';
                setTimeout(() => {
                    tipEl.textContent = TIPS[Math.floor(Math.random() * TIPS.length)];
                    tipEl.style.opacity = '1';
                }, 150);
            }
        }, 8000);

        // Auto-scroll only if user is pinned to bottom
        if (window.scrollState?.isPinnedToBottom) {
            if (typeof window.scrollToBottom === 'function') {
                window.scrollToBottom(false);
            }
        }
    }

    function appendReasoning(text) {
        if (!indicatorElement || !isStreaming) {
            console.log('appendReasoning skipped:', { hasElement: !!indicatorElement, isStreaming });
            return;
        }

        reasoningText += text;
        console.log('appendReasoning: adding text, total length:', reasoningText.length);
        const streamEl = indicatorElement.querySelector('.reasoning-stream');
        if (streamEl) {
            // Escape HTML and preserve newlines
            const escaped = text.replace(/</g, '&lt;').replace(/>/g, '&gt;');
            streamEl.innerHTML += escaped.replace(/\n/g, '<br>');

            // Auto-scroll reasoning area
            const reasoningDiv = indicatorElement.querySelector('.thinking-reasoning');
            if (reasoningDiv && !reasoningDiv.classList.contains('collapsed')) {
                reasoningDiv.scrollTop = reasoningDiv.scrollHeight;
            }
        }

        // Update message to show we're receiving reasoning
        const msgEl = indicatorElement.querySelector('.thinking-message');
        if (msgEl) {
            msgEl.textContent = 'Reasoning...';
        }
    }

    function setContentMode() {
        if (!indicatorElement) return;

        const msgEl = indicatorElement.querySelector('.thinking-message');
        if (msgEl) {
            msgEl.textContent = 'Generating response...';
        }
    }

    function getReasoning() {
        return reasoningText;
    }

    function hide() {
        if (elapsedInterval) {
            clearInterval(elapsedInterval);
            elapsedInterval = null;
        }
        if (tipInterval) {
            clearInterval(tipInterval);
            tipInterval = null;
        }

        if (indicatorElement && indicatorElement.parentNode) {
            // Save current scroll position before removing element
            const scrollContainer = document.getElementById('chat-scroll-region');
            const savedScrollTop = scrollContainer ? scrollContainer.scrollTop : 0;
            const keepAtTop = window._keepMessageAtTop;

            indicatorElement.classList.remove('visible');
            setTimeout(() => {
                if (indicatorElement) {
                    indicatorElement.remove();
                    indicatorElement = null;

                    // Restore scroll position if we're keeping message at top
                    if (keepAtTop && scrollContainer) {
                        scrollContainer.scrollTop = savedScrollTop;
                    }
                }
            }, 200);
        }

        isStreaming = false;
        reasoningText = '';
    }

    return { show, hide, appendReasoning, setContentMode, getReasoning };
})();

window.ThinkingIndicator = ThinkingIndicator;

// ==================== Simple Pulsing Dot (for non-DeepSeek models) ====================
// Uses the same streaming-cursor style for visual consistency
let simplePulsingDotElement = null;

function showSimplePulsingDot() {
    const chatDisplay = document.getElementById('chat-display');
    if (!chatDisplay) return;

    // Remove existing dot if any
    hideSimplePulsingDot();

    // Use streaming-cursor class so it looks identical to the cursor that leads text
    simplePulsingDotElement = document.createElement('div');
    simplePulsingDotElement.className = 'simple-pulsing-dot';
    simplePulsingDotElement.innerHTML = '<span class="streaming-cursor"></span>';
    chatDisplay.appendChild(simplePulsingDotElement);
}

function hideSimplePulsingDot() {
    if (simplePulsingDotElement) {
        // Save scroll position before removing
        const scrollContainer = document.getElementById('chat-scroll-region');
        const savedScrollTop = scrollContainer ? scrollContainer.scrollTop : 0;
        const keepAtTop = window._keepMessageAtTop;

        simplePulsingDotElement.remove();
        simplePulsingDotElement = null;

        // Restore scroll position if keeping message at top
        if (keepAtTop && scrollContainer) {
            scrollContainer.scrollTop = savedScrollTop;
        }
    }
}

window.showSimplePulsingDot = showSimplePulsingDot;
window.hideSimplePulsingDot = hideSimplePulsingDot;

// ==================== Reasoning Display Component ====================
const ReasoningDisplay = (() => {
    /**
     * Create an expandable reasoning section for DeepSeek R1 responses
     */
    function create(reasoningContent, messageContainer) {
        if (!reasoningContent || !messageContainer) return null;

        // Remove any existing reasoning section first (prevents duplicates on edit/regenerate)
        const existingReasoning = messageContainer.querySelector('.reasoning-section');
        if (existingReasoning) {
            existingReasoning.remove();
        }

        const reasoningEl = document.createElement('div');
        reasoningEl.className = 'reasoning-section collapsed';

        // Format reasoning for display (preserve newlines, clean up)
        const formattedReasoning = reasoningContent
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/\n/g, '<br>');

        const thinkingTime = estimateThinkingTime(reasoningContent);

        // Create preview text (first meaningful line, truncated)
        const previewText = createPreview(reasoningContent);

        reasoningEl.innerHTML = `
            <button class="reasoning-toggle" aria-expanded="false">
                <span class="reasoning-header">
                    <span class="reasoning-label">Thought for ${thinkingTime}</span>
                    <span class="reasoning-chevron">▼</span>
                </span>
                <span class="reasoning-preview">${previewText}</span>
            </button>
            <div class="reasoning-content" aria-hidden="true">
                <div class="reasoning-text">${formattedReasoning}</div>
            </div>
        `;

        // Add toggle functionality
        const toggleBtn = reasoningEl.querySelector('.reasoning-toggle');
        const content = reasoningEl.querySelector('.reasoning-content');
        const chevron = reasoningEl.querySelector('.reasoning-chevron');
        const preview = reasoningEl.querySelector('.reasoning-preview');

        toggleBtn.addEventListener('click', () => {
            const isCollapsed = reasoningEl.classList.contains('collapsed');
            reasoningEl.classList.toggle('collapsed', !isCollapsed);
            toggleBtn.setAttribute('aria-expanded', isCollapsed ? 'true' : 'false');
            content.setAttribute('aria-hidden', !isCollapsed ? 'true' : 'false');
            chevron.textContent = isCollapsed ? '▲' : '▼';
        });

        // Insert at the beginning of the message
        const messageContent = messageContainer.querySelector('.message-content, .ai-message-content');
        if (messageContent) {
            messageContent.insertBefore(reasoningEl, messageContent.firstChild);
        } else {
            messageContainer.insertBefore(reasoningEl, messageContainer.firstChild);
        }

        return reasoningEl;
    }

    /**
     * Create a preview snippet from the reasoning content
     */
    function createPreview(text) {
        if (!text) return '';

        // Get first meaningful line (skip empty lines)
        const lines = text.split('\n').filter(line => line.trim().length > 0);
        if (lines.length === 0) return '';

        let preview = lines[0].trim();

        // Truncate if too long
        const maxLength = 60;
        if (preview.length > maxLength) {
            preview = preview.substring(0, maxLength).trim() + '...';
        }

        // Escape HTML
        return preview
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;');
    }

    /**
     * Estimate thinking time based on reasoning length
     */
    function estimateThinkingTime(text) {
        if (!text) return '0s';
        // Rough estimate: ~50 chars per second of "thinking"
        const seconds = Math.max(1, Math.round(text.length / 50));
        if (seconds < 60) return `${seconds}s`;
        const minutes = Math.floor(seconds / 60);
        const remainingSeconds = seconds % 60;
        return remainingSeconds > 0 ? `${minutes}m ${remainingSeconds}s` : `${minutes}m`;
    }

    return { create };
})();

window.ReasoningDisplay = ReasoningDisplay;

const OFFLINE_REASON_COPY = Object.freeze({
    session_unavailable: 'Realtime storage is unavailable, so new replies only live in this browser until we reconnect.',
    preferences_unavailable: 'Preference data is offline. Messages are held locally until sync resumes.',
    persistence_failed: 'Unable to sync your latest reply yet; keeping it safe here until connectivity returns.',
    summary_update_failed: 'Partial connectivity prevented saving chat history. We will retry automatically once online.',
    title_update_failed: 'Session metadata could not be updated due to connectivity issues. Messages remain locally.',
    artifact_persist_failed: 'Artifact content could not be synced yet. You can still view it locally and it will upload automatically when reconnected.',
    artifact_persist_deferred: 'Artifact content is queued locally until Firestore is reachable again.',
    unknown: 'Working offline. Your latest replies will sync automatically once we reconnect.',
    network_error: 'Network connection lost. Messages stay on this device until your connection stabilizes.'
});

function updateOfflineIndicator(isOffline, reason, pendingCount = 0) {
    const indicator = document.getElementById('offline-indicator');
    if (!indicator) return;

    const headingEl = indicator.querySelector('.offline-heading');
    const detailEl = indicator.querySelector('.offline-detail');
    const message = OFFLINE_REASON_COPY[reason] || OFFLINE_REASON_COPY.unknown;
    const pendingSuffix = pendingCount > 0
        ? ` (${pendingCount} pending ${pendingCount === 1 ? 'message' : 'messages'})`
        : '';

    if (isOffline) {
        if (headingEl) headingEl.textContent = 'Working offline';
        if (detailEl) detailEl.textContent = `${message}${pendingSuffix}`;
        indicator.classList.add('active');
    } else {
        indicator.classList.remove('active');
        if (headingEl) headingEl.textContent = 'Working offline';
        if (detailEl) {
            detailEl.textContent = 'Messages are stored locally until connectivity returns.';
        }
    }
}

window.updateOfflineIndicator = updateOfflineIndicator;

// ==================== Guest Mode Functions ====================
function isGuestMode() {
    return document.body.dataset.guestMode === 'true';
}

function getGuestMessageCount() {
    const count = parseInt(document.body.dataset.guestMessageCount, 10);
    return isNaN(count) ? 0 : count;
}

function getGuestBannerThreshold() {
    const threshold = parseInt(document.body.dataset.guestBannerThreshold, 10);
    return isNaN(threshold) ? 3 : threshold;
}

function updateGuestMessageCount(count) {
    document.body.dataset.guestMessageCount = count;
    // Update placeholder with remaining messages
    const promptInput = document.getElementById('prompt');
    if (promptInput && isGuestMode()) {
        const limit = parseInt(document.body.dataset.guestMessageLimit, 10) || 5;
        const remaining = Math.max(0, limit - count);
        promptInput.placeholder = `Try XelaSphere (${remaining} message${remaining !== 1 ? 's' : ''} left)`;
    }
}

function showGuestSignupBanner() {
    const banner = document.getElementById('guest-signup-banner');
    if (banner && isGuestMode()) {
        banner.style.display = 'flex';
    }
}

function hideGuestSignupBanner() {
    const banner = document.getElementById('guest-signup-banner');
    if (banner) {
        banner.style.display = 'none';
    }
}

function showGuestSignupModal() {
    const modal = document.getElementById('guest-signup-modal');
    if (modal) {
        modal.style.display = 'flex';
        document.body.style.overflow = 'hidden';
    }
}

function hideGuestSignupModal() {
    const modal = document.getElementById('guest-signup-modal');
    if (modal) {
        modal.style.display = 'none';
        document.body.style.overflow = '';
    }
}

// Initialize guest mode event listeners
document.addEventListener('DOMContentLoaded', function() {
    // Guest banner dismiss button
    const dismissBtn = document.getElementById('guest-banner-dismiss');
    if (dismissBtn) {
        dismissBtn.addEventListener('click', hideGuestSignupBanner);
    }

    // Guest modal backdrop click to close
    const modalBackdrop = document.querySelector('.guest-modal-backdrop');
    if (modalBackdrop) {
        modalBackdrop.addEventListener('click', hideGuestSignupModal);
    }

    // Prevent disabled elements from working in guest mode
    if (isGuestMode()) {
        document.querySelectorAll('.guest-disabled').forEach(el => {
            el.addEventListener('click', function(e) {
                e.preventDefault();
                e.stopPropagation();
            });
        });
    }
});

// Export guest functions
window.showGuestSignupModal = showGuestSignupModal;
window.showGuestSignupBanner = showGuestSignupBanner;
window.isGuestMode = isGuestMode;

function isTypingAnimationActive() {
    if (typeof window === 'undefined') return false;

    // Check old typewriter animator
    const animator = window.activeAnimator;
    if (animator && animator.state && !animator.state.isStopped) {
        return true;
    }

    // Check ProgressiveTextDisplay for current session
    const currentSessionId = window.chatManager?.activeSessionId;
    if (currentSessionId && ProgressiveTextDisplay?.hasActiveDisplay?.(currentSessionId)) {
        return true;
    }

    return false;
}

function resetSendButtonIfIdle() {
    if (typeof updateSendButton === 'function' && !isTypingAnimationActive()) {
        updateSendButton(false);
    }
}

function renderMathInMessage(element) {
    if (typeof window === 'undefined' || typeof window.renderMathInElement !== 'function' || !element) {
        return;
    }
    try {
        window.renderMathInElement(element, {
            delimiters: [
                { left: '$$', right: '$$', display: true },
                { left: '\\[', right: '\\]', display: true },
                { left: '\\(', right: '\\)', display: false },
                { left: '$', right: '$', display: false }
            ],
            throwOnError: false
        });
    } catch (error) {
        console.error('Math rendering error:', error);
    }
}

// Terminal/shell languages that should render as terminal window
const TERMINAL_LANGUAGES = new Set([
    'bash', 'sh', 'shell', 'zsh', 'terminal', 'console',
    'cmd', 'powershell', 'ps1', 'command', 'cli'
]);

// Common terminal command patterns for content-based detection
const TERMINAL_COMMAND_PATTERNS = [
    /^\$\s+/,           // Lines starting with $
    /^>\s+/,            // Lines starting with >
    /^#\s+/,            // Shell comments
    /^(npm|npx|yarn|pnpm)\s+(install|run|start|build|test|init|create)/i,
    /^(git)\s+(clone|pull|push|commit|add|checkout|branch|merge|status|init)/i,
    /^(pip|pip3|python|python3)\s+(install|-m|--version)/i,
    /^(cd|mkdir|rm|cp|mv|ls|cat|echo|touch|chmod|chown|curl|wget)\s+/i,
    /^(apt-get|apt|brew|yum|dnf|pacman)\s+(install|update|upgrade)/i,
    /^(docker|docker-compose)\s+(run|build|pull|push|up|down)/i,
    /^(node|deno|bun)\s+/i,
    /^(cargo|rustc|go|ruby|gem)\s+/i,
    /^(sudo)\s+/i,
    /^(export|source|\.\/)/i
];

function isTerminalLanguage(lang) {
    if (!lang) return false;
    return TERMINAL_LANGUAGES.has(lang.toLowerCase());
}

// Detect terminal content based on the actual content, not just language
function isTerminalContent(content, lang) {
    // If explicit terminal language, always true
    if (isTerminalLanguage(lang)) return true;

    // For plaintext or unspecified, analyze content
    if (!lang || lang === 'plaintext' || lang === 'text') {
        const lines = content.split('\n').filter(l => l.trim());
        if (lines.length === 0) return false;

        // Check if most lines look like terminal commands
        let terminalLineCount = 0;
        for (const line of lines) {
            const trimmed = line.trim();
            for (const pattern of TERMINAL_COMMAND_PATTERNS) {
                if (pattern.test(trimmed)) {
                    terminalLineCount++;
                    break;
                }
            }
        }

        // If more than 50% of lines look like terminal commands, treat as terminal
        return terminalLineCount > 0 && (terminalLineCount / lines.length) >= 0.5;
    }

    return false;
}

// ═══════════════════════════════════════════════════════════════
// PROJECT STRUCTURE / TREE VIEW DETECTION & RENDERING
// ═══════════════════════════════════════════════════════════════

// Common file extensions for detection
const FILE_EXTENSIONS = new Set([
    'js', 'ts', 'jsx', 'tsx', 'py', 'rb', 'java', 'go', 'rs', 'c', 'cpp', 'h',
    'css', 'scss', 'sass', 'less', 'html', 'htm', 'vue', 'svelte',
    'json', 'yaml', 'yml', 'xml', 'toml', 'ini', 'env',
    'md', 'txt', 'rst', 'log',
    'sh', 'bash', 'zsh', 'ps1', 'bat', 'cmd',
    'sql', 'graphql', 'prisma',
    'dockerfile', 'gitignore', 'eslintrc', 'prettierrc',
    'png', 'jpg', 'jpeg', 'gif', 'svg', 'ico', 'webp',
    'woff', 'woff2', 'ttf', 'eot',
    'pdf', 'doc', 'docx', 'xls', 'xlsx'
]);

// Tree structure patterns - detect ASCII tree structures
function isTreeStructure(content, lang) {
    // Skip if explicitly marked as code language (not plaintext/text)
    if (lang && lang !== 'plaintext' && lang !== 'text' && lang !== 'tree' && lang !== 'directory' && lang !== '') {
        return false;
    }

    const lines = content.split('\n').filter(l => l.trim());
    if (lines.length < 2) return false;

    let treeIndicators = 0;
    let hasTreeChars = false;

    for (const line of lines) {
        // Check for box-drawing characters (├ └ │ ─)
        if (/[├└│─]/.test(line)) {
            treeIndicators += 2;
            hasTreeChars = true;
            continue;
        }

        // Check for directory paths ending with /
        if (/\/\s*$/.test(line.trim()) && !/\s{2,}/.test(line.trim())) {
            treeIndicators++;
            continue;
        }

        // Check for files with known extensions
        const extMatch = line.trim().match(/[\w\-_]+\.(py|js|ts|jsx|tsx|html|css|json|md|txt|yml|yaml|xml|sql|sh|rb|go|rs|java|php|vue|svelte|env|gitignore|config|cfg)\s*$/i);
        if (extMatch) {
            treeIndicators++;
            continue;
        }

        // Check for common project folder names
        if (/^[\s│├└─]*\s*(src|lib|app|public|assets|components|pages|views|utils|hooks|services|api|tests?|specs?|config|scripts|docs?|templates|static|instance|blueprints)\/?[\s#]*/.test(line)) {
            treeIndicators++;
        }
    }

    // If we found box-drawing chars AND enough indicators, it's a tree
    if (hasTreeChars && treeIndicators >= 4) {
        return true;
    }

    // Otherwise check percentage
    return treeIndicators >= 5 && (treeIndicators / lines.length) >= 0.4;
}

// ═══════════════════════════════════════════════════════════════
// MERMAID DIAGRAM DETECTION & RENDERING
// ═══════════════════════════════════════════════════════════════

function isMermaidDiagram(content, lang) {
    // Check if language is explicitly mermaid
    if (lang && lang.toLowerCase() === 'mermaid') {
        return true;
    }

    // Check for mermaid syntax patterns
    const mermaidPatterns = [
        /^(graph|flowchart)\s+(TB|BT|LR|RL|TD)/im,
        /^sequenceDiagram/im,
        /^classDiagram/im,
        /^stateDiagram/im,
        /^erDiagram/im,
        /^gantt/im,
        /^pie/im,
        /^gitGraph/im,
        /^journey/im,
        /^mindmap/im
    ];

    return mermaidPatterns.some(pattern => pattern.test(content.trim()));
}

function createMermaidDiagram(content) {
    const wrapper = document.createElement('div');
    wrapper.className = 'mermaid-container';
    wrapper.style.cssText = `
        background: transparent !important;
        padding: 16px !important;
        margin: 0.5rem 0 !important;
        overflow-x: auto !important;
        overflow-y: visible !important;
        min-height: 200px !important;
    `;

    // Clean the content - remove any markdown artifacts
    let cleanContent = content.trim();
    // Remove any leading/trailing backticks or language tags
    cleanContent = cleanContent.replace(/^```mermaid\s*/i, '').replace(/```\s*$/, '').trim();
    // Remove "mermaid" if it appears as the first line (language tag leaking into content)
    cleanContent = cleanContent.replace(/^mermaid\s*\n/i, '').trim();

    // Fix common AI-generated Mermaid syntax errors
    // Fix: -->|Label|> should be -->|Label| (extra > is invalid)
    cleanContent = cleanContent.replace(/\|>\s+/g, '| ');
    // Fix: -->|Label|> at end of arrow should remove extra >
    cleanContent = cleanContent.replace(/(\|[^|]+\|)>/g, '$1');
    // Fix: Missing space after arrow label -->|Label|Node should be -->|Label| Node
    cleanContent = cleanContent.replace(/\|([^|]+)\|([A-Z\[])/g, '|$1| $2');
    // Fix: Double arrows --> --> should be single -->
    cleanContent = cleanContent.replace(/-->\s*-->/g, '-->');
    // Fix: Arrow with extra characters like --> > should be -->
    cleanContent = cleanContent.replace(/-->\s*>/g, '-->');
    // Fix: Double quotes inside node labels break parsing - replace with single quotes
    cleanContent = cleanContent.replace(/\[([^\]]*)"([^"]*)"([^\]]*)\]/g, "[$1'$2'$3]");
    // Fix: Also handle in other bracket types like () {} <>
    cleanContent = cleanContent.replace(/\(([^)]*)"([^"]*)"([^)]*)\)/g, "($1'$2'$3)");
    // Fix: Spaces inside arrow labels -->| No| should be -->|No|
    cleanContent = cleanContent.replace(/\|\s+([^|]+)\s*\|/g, '|$1|');
    // Fix: Trim spaces inside labels
    cleanContent = cleanContent.replace(/\|([^|]+)\|/g, (match, label) => `|${label.trim()}|`);

    console.log('[Mermaid] Processing diagram:', cleanContent.substring(0, 100) + '...');

    // Create unique ID for this diagram
    const diagramId = 'mermaid-' + Date.now() + '-' + Math.random().toString(36).substr(2, 9);

    const diagramDiv = document.createElement('div');
    diagramDiv.id = diagramId;
    diagramDiv.className = 'mermaid';
    diagramDiv.style.cssText = `
        display: flex !important;
        justify-content: center !important;
        min-width: max-content !important;
    `;
    diagramDiv.textContent = cleanContent;

    wrapper.appendChild(diagramDiv);

    // Initialize mermaid if available
    if (typeof mermaid !== 'undefined') {
        // Configure mermaid for dark theme
        mermaid.initialize({
            startOnLoad: false,
            theme: 'dark',
            suppressErrors: false,
            themeVariables: {
                primaryColor: '#10A37F',
                primaryTextColor: '#e6edf3',
                primaryBorderColor: '#30363d',
                lineColor: '#8b949e',
                secondaryColor: '#21262d',
                tertiaryColor: '#161b22',
                background: 'transparent',
                mainBkg: '#0d1117',
                nodeBorder: '#30363d',
                clusterBkg: '#161b22',
                clusterBorder: '#30363d',
                titleColor: '#e6edf3',
                edgeLabelBackground: '#0d1117'
            },
            flowchart: {
                htmlLabels: true,
                curve: 'basis',
                nodeSpacing: 50,
                rankSpacing: 60,
                padding: 20,
                useMaxWidth: false
            }
        });

        // Render after a short delay to ensure DOM is ready
        setTimeout(async () => {
            try {
                // Use mermaid.render for better error handling
                const { svg } = await mermaid.render(diagramId + '-svg', cleanContent);
                diagramDiv.innerHTML = svg;
                console.log('[Mermaid] Diagram rendered successfully');
            } catch (err) {
                console.error('[Mermaid] Render error:', err);
                // Show the code in a styled box on error
                diagramDiv.innerHTML = `
                    <div style="background: rgba(248,81,73,0.1); border: 1px solid rgba(248,81,73,0.3); border-radius: 8px; padding: 12px; margin: 8px 0;">
                        <div style="color: #f85149; font-weight: 600; margin-bottom: 8px;">⚠️ Diagram syntax error</div>
                        <pre style="color: #8b949e; font-size: 13px; white-space: pre-wrap; margin: 0;">${escapeHtmlForTerminal(cleanContent)}</pre>
                    </div>
                `;
            }
        }, 100);
    } else {
        console.warn('[Mermaid] Library not loaded');
        diagramDiv.innerHTML = `<pre style="color: #8b949e;">${escapeHtmlForTerminal(cleanContent)}</pre>`;
    }

    return wrapper;
}

// RECIPE DETECTION & RENDERING
// Moved to recipeDetector.js module - use window.RecipeDetector.* functions
// ═══════════════════════════════════════════════════════════════

function getFileIcon(filename) {
    const ext = filename.split('.').pop()?.toLowerCase();
    const name = filename.toLowerCase();

    // Use simple colored circles as reliable icons
    const makeIcon = (color) => `<span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:${color};margin-right:2px;"></span>`;

    // Special files
    if (name === 'package.json') return makeIcon('#cb3837');
    if (name === 'dockerfile') return makeIcon('#2496ed');
    if (name === '.gitignore' || name === '.git') return makeIcon('#f05032');
    if (name === 'readme.md') return makeIcon('#10A37F');
    if (name === '.env' || name.startsWith('.env.')) return makeIcon('#ecd53f');
    if (name === 'requirements.txt') return makeIcon('#3776ab');
    if (name === 'tsconfig.json' || name === 'config.py') return makeIcon('#6e7681');

    // By extension - using colored dots
    const colorMap = {
        'js': '#f7df1e',
        'jsx': '#61dafb',
        'ts': '#3178c6',
        'tsx': '#61dafb',
        'py': '#3776ab',
        'html': '#e34f26',
        'htm': '#e34f26',
        'css': '#1572b6',
        'scss': '#cc6699',
        'sass': '#cc6699',
        'json': '#cbcb41',
        'md': '#ffffff',
        'txt': '#8b949e',
        'vue': '#4fc08d',
        'svg': '#ffb13b',
        'png': '#a074c4',
        'jpg': '#a074c4',
        'jpeg': '#a074c4',
        'gif': '#a074c4',
        'sql': '#336791',
        'yaml': '#cb171e',
        'yml': '#cb171e',
        'sh': '#4EAA25',
        'bash': '#4EAA25',
        'go': '#00add8',
        'rs': '#dea584',
        'java': '#007396',
        'rb': '#cc342d',
        'php': '#777bb4',
        'xml': '#e37933',
        'env': '#ecd53f'
    };

    const color = colorMap[ext] || '#8b949e';
    return makeIcon(color);
}

function parseTreeStructure(content) {
    const lines = content.split('\n');
    const items = [];

    for (const line of lines) {
        if (!line.trim()) continue;

        let depth = 0;
        let name = line;
        let isDirectory = false;

        // Check for ASCII tree format: "├── name" or "│   ├── name"
        const treeMatch = line.match(/^([\s│]*)[├└]──\s*(.+)$/);
        if (treeMatch) {
            // Count depth by │ and spaces
            depth = (treeMatch[1].match(/│/g) || []).length;
            name = treeMatch[2].trim();
        } else {
            // Count leading spaces for depth
            const leadingSpaces = line.match(/^(\s*)/)[1].length;
            depth = Math.floor(leadingSpaces / 2);
            name = line.trim();
        }

        // Check if it's a directory
        if (name.endsWith('/')) {
            isDirectory = true;
            name = name.slice(0, -1);
        } else if (!name.includes('.') && !name.startsWith('.')) {
            // No extension and not a dotfile - likely a directory
            isDirectory = true;
        }

        items.push({ name, depth, isDirectory });
    }

    return items;
}

function createTreeView(content) {
    const items = parseTreeStructure(content);

    const wrapper = document.createElement('div');
    wrapper.className = 'tree-view-inline';
    wrapper.style.cssText = `
        font-family: 'JetBrains Mono', 'SF Mono', 'Consolas', monospace;
        font-size: 13px;
        line-height: 1.6;
        padding: 8px 0;
    `;

    wrapper.innerHTML = renderTreeItemsInline(items);

    // Add expand/collapse functionality for folders
    wrapper.querySelectorAll('.tree-folder-toggle').forEach(toggle => {
        toggle.addEventListener('click', (e) => {
            e.stopPropagation();
            const li = toggle.closest('li');
            const childList = li.querySelector('ul');
            const icon = toggle.querySelector('i');
            if (childList) {
                const isHidden = childList.style.display === 'none';
                childList.style.display = isHidden ? 'block' : 'none';
                icon.className = isHidden ? 'fas fa-chevron-down' : 'fas fa-chevron-right';
            }
        });
    });

    return wrapper;
}

function renderTreeItemsInline(items) {
    let html = '<ul style="list-style: none; padding: 0; margin: 0;">';
    let currentDepth = 0;
    let openLists = 0;

    for (let i = 0; i < items.length; i++) {
        const item = items[i];
        const nextItem = items[i + 1];
        const hasChildren = nextItem && nextItem.depth > item.depth;

        // Close lists if going back up
        while (currentDepth > item.depth) {
            html += '</ul></li>';
            currentDepth--;
            openLists--;
        }

        const indent = item.depth * 20;

        if (item.isDirectory) {
            const folderColor = '#e8ab4c';
            html += `
                <li style="margin: 2px 0; padding-left: ${indent}px;">
                    <span style="display: inline-flex; align-items: center; gap: 6px; padding: 3px 6px; border-radius: 4px; cursor: ${hasChildren ? 'pointer' : 'default'};" ${hasChildren ? 'class="tree-folder-toggle"' : ''}>
                        ${hasChildren ? '<i class="fas fa-chevron-down" style="font-size: 10px; color: #6e7681; width: 12px;"></i>' : '<span style="width: 12px;"></span>'}
                        <i class="fas fa-folder${hasChildren ? '-open' : ''}" style="color: ${folderColor};"></i>
                        <span style="color: #e6edf3; font-weight: 500;">${escapeHtmlForTerminal(item.name)}</span>
                    </span>
            `;
            if (hasChildren) {
                html += `<ul style="list-style: none; padding: 0; margin: 0; border-left: 1px solid rgba(255,255,255,0.1); margin-left: 18px;">`;
                currentDepth = item.depth + 1;
                openLists++;
            } else {
                html += '</li>';
            }
        } else {
            const icon = getFileIcon(item.name);
            html += `
                <li style="margin: 2px 0; padding-left: ${indent}px;">
                    <span style="display: inline-flex; align-items: center; gap: 6px; padding: 3px 6px;">
                        <span style="width: 12px;"></span>
                        ${icon}
                        <span style="color: #c9d1d9;">${escapeHtmlForTerminal(item.name)}</span>
                    </span>
                </li>
            `;
        }
    }

    // Close any remaining open lists
    while (openLists > 0) {
        html += '</ul></li>';
        openLists--;
    }

    html += '</ul>';
    return html;
}

function renderTreeItems(items) {
    let html = '<ul class="tree-list">';
    let currentDepth = 0;
    let openLists = 0;

    for (let i = 0; i < items.length; i++) {
        const item = items[i];
        const nextItem = items[i + 1];
        const hasChildren = nextItem && nextItem.depth > item.depth;

        // Close lists if going back up
        while (currentDepth > item.depth) {
            html += '</ul></li>';
            currentDepth--;
            openLists--;
        }

        if (item.isDirectory) {
            const icon = '<i class="fas fa-folder folder-icon"></i>';
            const openIcon = '<i class="fas fa-folder-open folder-icon-open"></i>';
            html += `
                <li class="tree-item tree-folder ${hasChildren ? 'has-children expanded' : ''}">
                    <div class="tree-item-content">
                        <span class="tree-toggle">${hasChildren ? '<i class="fas fa-chevron-down"></i>' : ''}</span>
                        <span class="tree-icon">${icon}${openIcon}</span>
                        <span class="tree-name">${escapeHtmlForTerminal(item.name)}</span>
                    </div>
            `;
            if (hasChildren) {
                html += '<ul class="tree-list">';
                currentDepth = item.depth + 1;
                openLists++;
            } else {
                html += '</li>';
            }
        } else {
            const icon = getFileIcon(item.name);
            html += `
                <li class="tree-item tree-file">
                    <div class="tree-item-content">
                        <span class="tree-toggle"></span>
                        <span class="tree-icon">${icon}</span>
                        <span class="tree-name">${escapeHtmlForTerminal(item.name)}</span>
                    </div>
                </li>
            `;
        }
    }

    // Close any remaining open lists
    while (openLists > 0) {
        html += '</ul></li>';
        openLists--;
    }

    html += '</ul>';
    return html;
}

function setupTreeInteractions(wrapper) {
    // Toggle folders on click
    wrapper.querySelectorAll('.tree-folder.has-children').forEach(folder => {
        const toggle = folder.querySelector('.tree-item-content');
        toggle.addEventListener('click', (e) => {
            e.stopPropagation();
            folder.classList.toggle('expanded');
            folder.classList.toggle('collapsed');
        });
    });

    // Expand all button
    const expandBtn = wrapper.querySelector('.tree-view-expand-all');
    if (expandBtn) {
        expandBtn.addEventListener('click', () => {
            wrapper.querySelectorAll('.tree-folder.has-children').forEach(folder => {
                folder.classList.add('expanded');
                folder.classList.remove('collapsed');
            });
        });
    }

    // Collapse all button
    const collapseBtn = wrapper.querySelector('.tree-view-collapse-all');
    if (collapseBtn) {
        collapseBtn.addEventListener('click', () => {
            wrapper.querySelectorAll('.tree-folder.has-children').forEach(folder => {
                folder.classList.remove('expanded');
                folder.classList.add('collapsed');
            });
        });
    }
}

function escapeHtmlForTerminal(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

function formatTerminalLines(codeText) {
    const lines = codeText.split('\n');
    return lines.map(line => {
        const trimmed = line.trim();
        // Lines starting with $ or > are commands
        if (trimmed.startsWith('$ ') || trimmed.startsWith('> ')) {
            const prompt = trimmed.startsWith('$ ') ? '$ ' : '> ';
            const command = trimmed.slice(2);
            return `<div class="terminal-line"><span class="terminal-prompt">${prompt}</span><span class="terminal-command">${escapeHtmlForTerminal(command)}</span></div>`;
        }
        // Lines starting with # are comments
        else if (trimmed.startsWith('#')) {
            return `<div class="terminal-line"><span class="terminal-output comment">${escapeHtmlForTerminal(line)}</span></div>`;
        }
        // Empty lines
        else if (trimmed === '') {
            return `<div class="terminal-line"><span class="terminal-output">&nbsp;</span></div>`;
        }
        // Output lines
        else {
            return `<div class="terminal-line"><span class="terminal-output">${escapeHtmlForTerminal(line)}</span></div>`;
        }
    }).join('');
}

function createTerminalWindow(codeContent) {
    const wrapper = document.createElement('div');
    wrapper.className = 'terminal-window';
    wrapper.innerHTML = `
        <div class="terminal-header">
            <div class="terminal-dots">
                <span class="terminal-dot red"></span>
                <span class="terminal-dot yellow"></span>
                <span class="terminal-dot green"></span>
            </div>
            <div class="terminal-title">
                <i class="fas fa-terminal"></i>
                <span>Terminal</span>
            </div>
            <div class="terminal-actions">
                <button class="terminal-copy" title="Copy commands">
                    <i class="fas fa-copy"></i>
                    <span>Copy</span>
                </button>
            </div>
        </div>
        <div class="terminal-body">
            ${formatTerminalLines(codeContent)}
        </div>
    `;

    // Add copy functionality
    const copyBtn = wrapper.querySelector('.terminal-copy');
    copyBtn.addEventListener('click', async () => {
        try {
            // Copy only the commands (lines with $ or >), strip the prompts
            const commands = codeContent.split('\n')
                .filter(line => line.trim().startsWith('$ ') || line.trim().startsWith('> '))
                .map(line => line.trim().replace(/^[$>]\s*/, ''))
                .join('\n');

            await navigator.clipboard.writeText(commands || codeContent);
            copyBtn.classList.add('copied');
            copyBtn.querySelector('span').textContent = 'Copied!';
            copyBtn.querySelector('i').className = 'fas fa-check';

            setTimeout(() => {
                copyBtn.classList.remove('copied');
                copyBtn.querySelector('span').textContent = 'Copy';
                copyBtn.querySelector('i').className = 'fas fa-copy';
            }, 2000);
        } catch (err) {
            console.error('Failed to copy:', err);
        }
    });

    return wrapper;
}

// Function to create a code window with syntax highlighting
async function createCodeWindow(text) {
    if (!text?.trim()) {
        throw new Error('Code content cannot be empty');
    }

    try {
        const { language, codeContent } = await extractLanguageAndCode(text);

        // Check if this is a tree/directory structure
        const isTree = isTreeStructure(codeContent, language);
        if (isTree) {
            console.log('[CodeWindow] Creating tree view for project structure');
            const treeView = createTreeView(codeContent);
            return { codeWindow: treeView, codeElement: null };
        }

        // Check if this is a Mermaid diagram
        const isMermaid = isMermaidDiagram(codeContent, language);
        if (isMermaid) {
            console.log('[CodeWindow] Creating Mermaid diagram');
            const mermaidDiagram = createMermaidDiagram(codeContent);
            return { codeWindow: mermaidDiagram, codeElement: null };
        }

        // Check if this is a recipe (using RecipeDetector module)
        const isRecipeContent = window.RecipeDetector && window.RecipeDetector.isRecipe(codeContent, language);
        if (isRecipeContent) {
            console.log('[CodeWindow] Creating recipe card');
            const recipeCard = window.RecipeDetector.createRecipeCard(codeContent);
            return { codeWindow: recipeCard, codeElement: null };
        }

        // Check if this is terminal content (by language OR content analysis)
        const isTerminal = isTerminalContent(codeContent, language);
        console.log('[CodeWindow] Language detected:', language, 'isTerminal:', isTerminal);

        // Render as terminal window if detected as terminal content
        if (isTerminal) {
            console.log('[CodeWindow] Creating terminal window for:', language || 'content-detected');
            const terminalWindow = createTerminalWindow(codeContent);
            return { codeWindow: terminalWindow, codeElement: null };
        }

        // Regular code window
        const codeWindow = document.createElement('div');
        codeWindow.className = `code-window ${CONFIG.CLASSES.DARK_MODE}`;

        const header = await createCodeHeader(language, codeContent);
        const { pre, code } = createCodeBlock(language, codeContent);

        codeWindow.append(header, pre);
        await applySyntaxHighlighting(code);

        return { codeWindow, codeElement: code };
    } catch (error) {
        console.error('Failed to create code window:', error);
        throw new Error('Failed to create code window');
    }
}

// Helper function to normalize code content - fixes literal escape sequences
function normalizeCodeContent(content) {
    if (!content || typeof content !== 'string') return content || '';

    // Check if content has literal \n instead of actual newlines
    // If there are many more literal \n than actual newlines, convert them
    const actualNewlines = (content.match(/\n/g) || []).length;
    const literalBackslashN = (content.match(/\\n/g) || []).length;

    if (literalBackslashN > 5 && literalBackslashN > actualNewlines * 2) {
        content = content
            .replace(/\\n/g, '\n')
            .replace(/\\t/g, '\t')
            .replace(/\\r/g, '\r');
    }

    return content;
}

// Helper function to create code block elements
function createCodeBlock(language, content) {
    const pre = document.createElement('pre');
    const code = document.createElement('code');
    code.className = language ? `language-${language}` : '';
    // Normalize content to fix literal escape sequences
    code.textContent = normalizeCodeContent(content);
    pre.appendChild(code);
    return { pre, code };
}

// Helper function to extract language and code content from text
async function extractLanguageAndCode(text) {
    let codeContent = text.trim();

    // Normalize content first to handle literal escape sequences
    codeContent = normalizeCodeContent(codeContent);

    const codeFenceRegex = /^```([a-zA-Z]*)\n([\s\S]*?)\n```$/;
    const match = codeContent.match(codeFenceRegex);

    if (match?.[1] || match?.[2]) {
        return {
            language: match[1].trim().toLowerCase() || 'text',
            codeContent: match[2].trim()
        };
    }

    try {
        const language = await Promise.resolve(detector?.detectLanguage(codeContent) ?? 'text');
        return { language, codeContent };
    } catch (error) {
        console.warn('Language detection failed:', error);
        return { language: 'text', codeContent };
    }
}

// Helper function to create the code header with copy button
async function createCodeHeader(language, codeContent) {
    const header = document.createElement('div');
    header.className = 'code-header';

    const langIndicator = createLanguageIndicator(language);
    const copyButton = createCopyButton(codeContent);

    header.append(langIndicator, copyButton);
    return header;
}

// Helper function to create language indicator
function createLanguageIndicator(language) {
    const langIndicator = document.createElement('span');
    langIndicator.className = 'language-indicator';
    langIndicator.textContent = language || 'Text';
    return langIndicator;
}

// Helper function to create the copy button with icon and text
function createCopyButton(codeContent) {
    const button = document.createElement('button');
    button.className = 'copy-button';
    button.innerHTML = `<i class="${CONFIG.ICONS.COPY}"></i><span>Copy code</span>`;

    button.addEventListener('click', async () => {
        try {
            await navigator.clipboard.writeText(codeContent);
            // Show success state
            button.innerHTML = `<i class="fas fa-check"></i><span>Copied!</span>`;
            button.classList.add('copied');

            // Reset after 1 second
            await new Promise(resolve => setTimeout(resolve, 1000));
            button.innerHTML = `<i class="${CONFIG.ICONS.COPY}"></i><span>Copy code</span>`;
            button.classList.remove('copied');
        } catch (error) {
            console.error('Copy failed:', error);
            button.innerHTML = `<i class="fas fa-times"></i><span>Failed</span>`;
            await new Promise(resolve => setTimeout(resolve, 1000));
            button.innerHTML = `<i class="${CONFIG.ICONS.COPY}"></i><span>Copy code</span>`;
        }
    });

    return button;
}

// Helper function to apply syntax highlighting
async function applySyntaxHighlighting(code) {
    if (typeof Prism === 'undefined') return;

    try {
        await Promise.resolve(Prism.highlightElement(code));
    } catch (error) {
        console.error('Prism highlighting failed:', error);
    }
}

// Helper function to create and append the code section
async function createAndAppendCodeSection(container, codeText, isHistory = false) {
    if (!container || !codeText) {
        console.warn('createAndAppendCodeSection: Missing container or codeText');
        return;
    }

    try {
        const { codeWindow, codeElement } = await createCodeWindow(codeText);
        const cleanCode = codeText.replace(/^[a-zA-Z]+\s*\n/, '').trim();

        container.appendChild(codeWindow);

        // If codeElement is null (e.g., terminal window), we're done - no syntax highlighting needed
        if (!codeElement) {
            return;
        }

        if (isHistory) {
            // For history, set text directly
            codeElement.textContent = cleanCode;
            await applySyntaxHighlighting(codeElement);
        } else {
            // For new messages, try typing animation with fallback
            if (typeof window.typeText === 'function') {
                try {
                    await window.typeText(cleanCode, codeElement, true);
                } catch (typeError) {
                    console.warn('typeText failed, falling back to direct render:', typeError);
                    codeElement.textContent = cleanCode;
                    await applySyntaxHighlighting(codeElement);
                }
            } else {
                // Fallback if typeText is not available
                console.log('typeText not available, rendering directly');
                codeElement.textContent = cleanCode;
                await applySyntaxHighlighting(codeElement);
            }
        }
    } catch (error) {
        console.error('Failed to create code section:', error);
        // Create a simple fallback display
        const fallbackPre = document.createElement('pre');
        fallbackPre.className = 'code-window';
        fallbackPre.textContent = codeText;
        container.appendChild(fallbackPre);
    }
}

// Helper function to append user's message with animation
/**
 * Render live attachments using the FilePreview system
 */
function renderLiveAttachments(container, attachmentsMeta) {
    if (!container || !attachmentsMeta || !attachmentsMeta.length) return;

    const attachmentsWrapper = document.createElement('div');
    attachmentsWrapper.className = 'message-attachments';

    attachmentsMeta.forEach(att => {
        if (!att) return;

        // Use FilePreview if available
        if (typeof FilePreview !== 'undefined') {
            const card = FilePreview.createFileCard({
                original_filename: att.name,
                name: att.name,
                mime_type: att.type,
                url: att.url,
                dataUrl: att.url
            }, {
                showMeta: true,
                onClick: (attachment) => {
                    const isImg = att.isImage || FilePreview.isImage(att.name, att.type);
                    if (isImg && typeof openAttachmentLightbox === 'function') {
                        openAttachmentLightbox({
                            src: att.url,
                            title: att.name,
                            downloadName: att.name,
                            openUrl: att.url,
                            isImage: true
                        });
                    } else {
                        FilePreview.openPreviewModal(attachment);
                    }
                }
            });
            attachmentsWrapper.appendChild(card);
        } else {
            // Fallback without FilePreview
            if (att.isImage && att.url) {
                const card = document.createElement('div');
                card.className = 'attachment-card image-attachment';
                const img = document.createElement('img');
                img.src = att.url;
                img.alt = att.name || 'Image';
                img.className = 'attachment-thumb';
                img.addEventListener('click', () => {
                    if (typeof openAttachmentLightbox === 'function') {
                        openAttachmentLightbox({ src: att.url, title: att.name, isImage: true });
                    }
                });
                card.appendChild(img);
                attachmentsWrapper.appendChild(card);
            } else {
                const chip = document.createElement('span');
                chip.className = 'attachment-chip';
                chip.textContent = att.name || 'File';
                attachmentsWrapper.appendChild(chip);
            }
        }
    });

    if (attachmentsWrapper.children.length > 0) {
        container.appendChild(attachmentsWrapper);
    }
}

function appendUserMessage(messageDiv, message, messageId) {
    if (!messageDiv || !message) {
        throw new Error('Message div and content are required');
    }

    const container = messageDiv.querySelector('.message-content') || messageDiv;
    appendTextMessage(container, message, true);

    let footer = messageDiv.querySelector('.message-bubble-footer');
    if (!footer) {
        footer = document.createElement('div');
        footer.className = 'message-bubble-footer';
        if (container && container.parentNode) {
            container.insertAdjacentElement('afterend', footer);
        } else {
            messageDiv.appendChild(footer);
        }
    }

    if (!footer.querySelector('.copy-message-button') && typeof appendCopyMessageButton === 'function') {
        appendCopyMessageButton(footer, messageId);
    }
    if (!footer.querySelector('.edit-button')) {
        appendEditButton(footer, messageId);
    }

    messageDiv.classList.add('user-message-animated');
    requestAnimationFrame(() => {
        messageDiv.classList.add('user-message-visible');
    });
}
// Function to display messages in the chatbox
function displayMessage(message, sender, messageType, originalPrompt, showButtons = true, messageId = null, attachmentsMeta = null, searchResults = null, modelMeta = null) {
    // Prevent rapid duplicate messages
    if (message === lastDisplayedMessage && Date.now() - lastMessageTimestamp < 1000) {
        console.log('Rapid duplicate message detected and prevented.');
        return;
    }
    lastDisplayedMessage = message;
    lastMessageTimestamp = Date.now();

    // Use chat-display for messages to match MutationObserver in sidebarHandler.js
    const chatDisplay = document.getElementById("chat-display");
    if (!chatDisplay) {
        console.error("The chat interface is currently unavailable. Please reload the page.");
        return;
    }

    // Generate a unique message ID if not provided
    if (!messageId) {
        messageId = generateMessageId();
    }

    const normalizedSender = sender && sender.toLowerCase() === 'user' ? 'user' : 'ai';
    const normalizedType = messageType || 'text';
    // Don't show base64 data URLs as text - they should only be used for images
    let safeMessage = message;
    if (normalizedType === 'text' && typeof message === 'string' && message.startsWith('data:')) {
        safeMessage = '';  // Don't display - will be handled by image attachment
    }

    let messageDiv = document.getElementById(messageId);
    if (!messageDiv) {
        // Set has-messages BEFORE append so the layout flips to chat mode
        // in the same frame — prevents a flash of centered-greeting layout.
        if (window.setHasMessages) window.setHasMessages(true);
        else document.body.classList.add('has-messages');
        messageDiv = createMessageDiv(messageId, sender);
        chatDisplay.appendChild(messageDiv);
    } else {
        messageDiv.innerHTML = ''; // Clear existing content to replace rather than append
    }

    createHeader(sender, messageDiv);

    messageDiv.dataset.originalPrompt = originalPrompt;
    messageDiv.dataset.messageId = messageId;
    if (!messageDiv.dataset.timestamp) {
        messageDiv.dataset.timestamp = new Date().toISOString();
    }
    if (!messageDiv.dataset.messageKey) {
        messageDiv.dataset.messageKey = messageId;
    }
    messageDiv.dataset.sender = normalizedSender;
    messageDiv.dataset.timestamp = new Date().toISOString();

    if (Array.isArray(searchResults) && searchResults.length) {
        try {
            messageDiv.dataset.sourceResults = JSON.stringify(searchResults);
            messageDiv.dataset.hasSources = 'true';
        } catch (error) {
            console.warn('Failed to store sources for message', messageId, error);
            delete messageDiv.dataset.sourceResults;
            delete messageDiv.dataset.hasSources;
        }
    } else {
        delete messageDiv.dataset.sourceResults;
        delete messageDiv.dataset.hasSources;
    }

    // Create a container for the message content
    const messageContent = document.createElement('div');
    messageContent.className = 'message-content';
    messageDiv.appendChild(messageContent);

    // Create a separate container for search results
    const searchResultsContainer = document.createElement('div');
    searchResultsContainer.className = 'search-results-wrapper';
    messageDiv.appendChild(searchResultsContainer);

    if (normalizedSender === "user") {
        if (normalizedType === 'image') {
            appendImageMessage(messageContent, message);
        } else if (normalizedType === 'file') {
            appendFileMessage(messageContent, message);
        } else {
            appendUserMessage(messageDiv, message, messageId);
        }
        // Render attachments using FilePreview system for live messages
        if (attachmentsMeta && Array.isArray(attachmentsMeta) && attachmentsMeta.length > 0) {
            renderLiveAttachments(messageContent, attachmentsMeta);
        }
    } else {
        safeMessage = message || '';
        switch (messageType) {
            case 'image-placeholder':
                appendImagePlaceholder(messageContent);
                break;
            case 'image':
                appendImageMessage(messageContent, safeMessage);
                break;
            case 'pdf':
                appendPdfMessage(messageContent, safeMessage);
                break;
            case 'file':
                appendFileMessage(messageContent, safeMessage);
                break;
            default:
                handleComplexMessage(messageContent, safeMessage, messageType, messageId, showButtons);
        }
    }

    // Auto model details (store on DOM node; action-buttons renderer will display it in-theme)
    if (normalizedSender === 'ai' && modelMeta && modelMeta.auto_selected) {
        try {
            messageDiv.dataset.autoSelected = 'true';
            if (modelMeta.model_id) messageDiv.dataset.modelId = modelMeta.model_id;
            if (modelMeta.model_label) messageDiv.dataset.modelLabel = modelMeta.model_label;
            if (modelMeta.model_provider) messageDiv.dataset.modelProvider = modelMeta.model_provider;
            if (modelMeta.model_provider_label) messageDiv.dataset.modelProviderLabel = modelMeta.model_provider_label;
            if (modelMeta.task_type) messageDiv.dataset.taskType = modelMeta.task_type;
        } catch (e) {
            console.warn('Failed to attach model meta to message element:', e);
        }
    }

    // Ghost Model: add "via ModelName" attribution to AI messages
    if (normalizedSender === 'ai' && modelMeta && typeof window.addModelAttribution === 'function') {
        window.addModelAttribution(messageDiv, modelMeta);
    }

    // Scroll is handled by scrollState.js (MutationObserver + scrollUserMessageToTop)
    // Don't call scrollToBottom here - it uses smooth scroll that overrides instant scroll
    resetInputIfNecessary(sender);

    if (typeof window !== 'undefined' && window.chatManager?.recordLiveMessage) {
        const payloadContent = normalizedSender === 'user' ? message : (typeof safeMessage !== 'undefined' ? safeMessage : message);
        const payload = {
            sender: normalizedSender,
            content: payloadContent,
            messageType: normalizedType,
            messageId,
            prompt: originalPrompt
        };
        if (attachmentsMeta && Array.isArray(attachmentsMeta) && attachmentsMeta.length) {
            // Use user_attachments for user messages, attachments for AI messages
            if (normalizedSender === 'user') {
                payload.user_attachments = attachmentsMeta;
            } else {
                payload.attachments = attachmentsMeta;
            }
        }
        if (Array.isArray(searchResults) && searchResults.length) {
            payload.search_results = searchResults.map(result => ({ ...result }));
        }
        window.chatManager.recordLiveMessage(payload);
    }

    if (typeof window !== 'undefined' && typeof window.updateEphemeralOptionsVisibility === 'function') {
        window.updateEphemeralOptionsVisibility();
        if (typeof window.dismissEphemeralOptions === 'function') {
            window.dismissEphemeralOptions();
        }
    }

    // Notify that a message was added (for mobile scroll handling)
    document.dispatchEvent(new CustomEvent('message-added'));

    return messageDiv;
}

// Track placeholder timestamps to enforce minimum display time
const placeholderTimestamps = new Map();

function replacePlaceholderWithImage(placeholderId, imageUrl, promptText) {
    const createdAt = placeholderTimestamps.get(placeholderId) || Date.now();
    const elapsed = Date.now() - createdAt;
    const minDuration = 3000; // 3s minimum animation time
    const delay = Math.max(0, minDuration - elapsed);

    setTimeout(() => {
        const messageDiv = document.getElementById(placeholderId);
        if (!messageDiv) return;
        const messageContent = messageDiv.querySelector('.message-content');
        if (!messageContent) return;
        let layer = messageContent.querySelector('.image-layer');
        if (!layer) {
            layer = document.createElement('div');
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
            img.className = 'chat-image chat-image--reveal';
            img.style.setProperty('aspect-ratio', '1 / 1');
            layer.appendChild(placeholder);
            layer.appendChild(img);
            messageContent.innerHTML = '';
            messageContent.appendChild(layer);
        }

        const img = layer.querySelector('.chat-image');
        const placeholder = layer.querySelector('.image-placeholder');
        if (img) {
            img.src = imageUrl;
            img.alt = promptText || 'Image';
            img.style.cursor = 'pointer';
            img.onload = () => {
                layer.classList.add('revealed');
                // Mobile blur-up effect
                img.classList.add('loaded');
                layer.classList.add('loaded');
            };
            img.onerror = () => {
                messageContent.textContent = 'Image failed to load.';
            };
            // If already cached, trigger loaded immediately
            if (img.complete && img.naturalHeight !== 0) {
                img.classList.add('loaded');
                layer.classList.add('loaded');
            }
            // Add click handler for lightbox
            img.addEventListener('click', () => {
                if (typeof openAttachmentLightbox === 'function') {
                    openAttachmentLightbox({
                        src: imageUrl,
                        title: promptText || 'Generated image',
                        isImage: true
                    });
                }
            });
        }
        if (placeholder) {
            // ensure placeholder stays until revealed class is added
            placeholder.style.opacity = '1';
        }

        placeholderTimestamps.delete(placeholderId);
        // Only scroll if user is pinned to bottom
        if (window.scrollState?.isPinnedToBottom) {
            scrollToBottom(false);
        }
    }, delay);
}

function isImagePrompt(prompt = '') {
    const p = prompt.toLowerCase();
    const keywords = ['image of', 'draw', 'picture of', 'photo of', 'generate', 'illustrate', 'visualize'];
    return keywords.some(k => p.includes(k));
}

// Enhanced message sending function
async function sendMessage(promptText, messageId = null, options = {}) {
    const {
        skipUserMessageRender = false,
        reuseMessageIdForResponse = true,
        forcePageId = null,
        displayPrompt = null  // Custom prompt to display in UI (different from actual prompt sent to backend)
    } = options || {};

    // Get session ID at the START of the request - this is the session we're responding to
    const requestSessionId = window.chatManager?.activeSessionId || null;

    // Helper to check if this session is still the active one
    // Defined here (before try) so it's accessible in finally block
    const isSessionActive = () => {
        // If no session existed when request started (new chat scenario),
        // treat as active - user hasn't explicitly switched to another session
        if (!requestSessionId) return true;

        const currentActive = window.chatManager?.activeSessionId;
        return currentActive === requestSessionId;
    };

    // Check if THIS session already has an active request (per-session, not global)
    // Allow starting requests in other sessions even if one is running
    if (requestSessionId && BackgroundRequestManager.hasActiveRequest(requestSessionId)) {
        console.log('Session already has active request:', requestSessionId);
        return;
    }

    // Also check global typing stopped flag (for abort scenarios)
    if (isTypingStopped) {
        resetState();
        return;
    }

    // Declare bgRequest before try so it's accessible in catch/finally
    let bgRequest = null;

    try {
        // Note: We no longer set global isRequestInProgress - we use per-session tracking
        isTypingStopped = false;

        // Create a new abort controller for this specific request
        const requestAbortController = new AbortController();
        

        // Get DOM elements and values
        const promptInput = document.getElementById('prompt');
        const searchEnabled = document.getElementById('enable-search')?.checked || false;
        let prompt = promptText ? promptText.trim() : promptInput?.value.trim();

        // Check for quoted text from Text Selection Quote feature
        if (!promptText && window.TextSelectionQuote?.hasActiveQuote?.()) {
            if (prompt) {
                // Format: > "quoted text" \n\n user's question
                prompt = window.TextSelectionQuote.formatMessageWithQuote(prompt);
            }
            // Clear quote indicator
            window.TextSelectionQuote.clearQuote();
        }

        const attachments = (typeof window !== 'undefined' && window.getPendingAttachmentsForSend)
            ? await window.getPendingAttachmentsForSend()
            : [];
        const liveUserAttachmentMeta = Array.isArray(attachments)
            ? attachments.map(att => {
                if (!att || typeof att !== 'object') return null;
                const url = att.previewUrl || att.dataUrl;
                if (!url) return null;
                return {
                    name: att.name || 'Attachment',
                    type: att.type || '',
                    isImage: !!att.isImage,
                    url
                };
            }).filter(Boolean)
            : [];
        const needsImagePlaceholder = prompt && isImagePrompt(prompt) && attachments.length === 0;
        const placeholderId = needsImagePlaceholder ? generateMessageId() : null;

        // Validate prompt or attachments
        if (!prompt && attachments.length === 0) {
            throw new Error('Empty prompt');
        }

        // Mobile: Dismiss keyboard immediately after send
        if (typeof window.dismissMobileKeyboard === 'function') {
            window.dismissMobileKeyboard();
        }

        if (typeof window !== 'undefined' && window.chatManager?.ensureActiveSession) {
            await window.chatManager.ensureActiveSession();
        }

        if (typeof window !== 'undefined' && window.chatManager?.handleTemporaryPromptSent) {
            window.chatManager.handleTemporaryPromptSent();
        }

        // Update UI state - show sending state but DON'T disable input
        // User can switch sessions and start other requests
        updateSendButton(true);

        const hasExistingHistory = (() => {
            if (typeof window === 'undefined' || !window.chatManager) return false;
            const { chatManager } = window;
            const activeId = chatManager.activeSessionId;
            if (!activeId) return false;
            const session = chatManager.chatHistory?.[activeId];
            return Array.isArray(session?.messages) && session.messages.length > 0;
        })();

        // Clear chat only if this truly is the first ever message for this session
        if (messageId === null && isFirstMessage && !hasExistingHistory) {
            clearChat();
            isFirstMessage = false;
        } else if (hasExistingHistory) {
            isFirstMessage = false;
        }

        // Display user message and attachments
        let primaryUserMessage = null;
        if (!skipUserMessageRender) {
            if (messageId === null && prompt) {
                // Use custom displayPrompt if provided, otherwise use actual prompt
                const messageToDisplay = displayPrompt !== null ? displayPrompt : prompt;
                primaryUserMessage = displayMessage(
                    messageToDisplay,
                    'user',
                    'text',
                    messageToDisplay,
                    true,
                    null,
                    liveUserAttachmentMeta
                );
            }
            // Attachments are now rendered inline via renderLiveAttachments in displayMessage
            // No need to create separate messages for each attachment
            if (needsImagePlaceholder) {
                displayMessage('', 'ai', 'image-placeholder', prompt, true, placeholderId);
                placeholderTimestamps.set(placeholderId, Date.now());
            }
        }

        // Claude-style: keep view pinned to bottom, new messages appear below.
        // No scroll-to-top — just ensure we're at the bottom showing the user's message.
        if (primaryUserMessage) {
            if (typeof window.scrollToBottom === 'function') {
                window.scrollToBottom(true, true);
            }
        }

        // Show indicator AFTER user message (so it appears below, where response will be)
        const currentModelEarly = (window.ghostModel && window.ghostModel.getSelectedModel()) || 'auto';
        const isDeepSeekEarly = currentModelEarly === 'deepseek-reasoner';

        // Only show full ThinkingIndicator for DeepSeek, simple pulsing dot for others
        if (isDeepSeekEarly) {
            ThinkingIndicator.show(true);
        } else {
            // Show simple pulsing dot for non-DeepSeek models
            showSimplePulsingDot();
        }

        // Bump session to top of sidebar (most recently active)
        if (requestSessionId && window.chatManager?.bumpSessionToTop) {
            window.chatManager.bumpSessionToTop(requestSessionId);
        }

        // Prepare request data
        const formData = new FormData();
        formData.append('prompt', prompt);
        formData.append('enable_search', searchEnabled);
        if (attachments.length) {
            // Strip dataUrl from JSON metadata (too large for payload)
            const serverAttachments = attachments.map(att => {
                const { dataUrl, file, ...rest } = att;
                return rest;
            });
            formData.append('attachments', JSON.stringify(serverAttachments));

            // Send image files as multipart form fields for vision API
            for (const att of attachments) {
                if (att.isImage && att.dataUrl) {
                    try {
                        const resp = await fetch(att.dataUrl);
                        const blob = await resp.blob();
                        formData.append('image_files', blob, att.name || 'image.png');
                    } catch (e) {
                        console.warn('Failed to attach image file:', e);
                    }
                }
            }
        }

        // Get model selection from Ghost Model selector
        const model = (window.ghostModel && window.ghostModel.getSelectedModel()) || 'auto';
        formData.append('model', model);

        // Reset ghost model for next message
        if (window.ghostModel) {
            window.ghostModel.resetAfterSend();
        }
        document.dispatchEvent(new CustomEvent('messageSent'));
        const pageId = forcePageId ||
            (typeof window !== 'undefined' &&
                window.chatManager &&
                typeof window.chatManager.getActivePageId === 'function'
                ? window.chatManager.getActivePageId()
                : null);
        if (pageId) {
            formData.append('page_id', pageId);
        }

        // Check if using a streamable model (DeepSeek, Moonshot/Kimi, or Auto)
        // Auto mode routes to a streaming-capable model server-side.
        // Skip streaming when images are attached — vision needs the regular /chat endpoint.
        const hasImages = attachments.some(a => a.isImage || a.type?.startsWith('image/'));
        const isDeepSeek = model.includes('deepseek');
        const isAuto = !model || model === 'auto';
        const isOpenAI = model.includes('gpt-');
        const isStreamable = (isDeepSeek || model.includes('kimi') || isOpenAI || isAuto) && !hasImages;
        const isDeepSeekR1 = model === 'deepseek-reasoner';

        // ThinkingIndicator is now shown immediately after user message (see above)
        // No need to show it again here

        // Skip scroll-to-bottom - we use scrollUserMessageToTop instead (ChatGPT-style)
        window._pendingScrollMessage = null;
        // Don't scroll to bottom - let scrollUserMessageToTop handle positioning

        // ChatGPT-style: mark streaming started for bottom pin
        window.scrollState?.setStreamingState?.(true);

        let response;
        let streamedReasoning = '';
        let streamedContent = '';
        let streamResponseId = null;

        // Register with BackgroundRequestManager
        console.log('sendMessage: requestSessionId =', requestSessionId);
        bgRequest = requestSessionId ? BackgroundRequestManager.startRequest(
            requestSessionId, prompt, model, pageId, formData
        ) : null;
        console.log('sendMessage: bgRequest created =', !!bgRequest);

        if (isStreamable) {
            // Use streaming endpoint for DeepSeek and Moonshot/Kimi models
            try {
                const streamResponse = await fetch('/chat/stream', {
                    method: 'POST',
                    body: formData,
                    headers: {
                        'X-CSRFToken': getCSRFToken()
                    },
                    signal: bgRequest ? bgRequest.abortController.signal : requestAbortController.signal
                });

                if (!streamResponse.ok) {
                    throw new Error(`Stream request failed: ${streamResponse.status}`);
                }

                const reader = streamResponse.body.getReader();
                const decoder = new TextDecoder();
                let buffer = '';
                let streamArtifact = null;
                let streamRenderMode = 'inline';
                let streamChatSummary = '';
                let streamIsEmail = false;
                let streamEmailData = null;
                let streamCryptoChart = null;
                let streamMemoryUpdated = false;
                let streamDreamNotification = null;
                let streamArtifactPatches = null;
                let patchStreamActive = false;
                let patchStreamBuffer = '';
                let patchParsedPatches = [];
                let artifactStreamActive = false;
                let artifactFencePosition = -1;
                let isFirstContentChunk = true; // Track first chunk for instant scroll
                // Use array to avoid O(n²) string concatenation per chunk.
                // streamedContent is only built (joined) when needed.
                const _contentChunks = [];
                let _contentJoinedAt = 0;  // index up to which streamedContent is current

                const processLine = (line) => {
                    if (!line.startsWith('data: ')) return;
                    const jsonStr = line.slice(6);

                    try {
                        const chunk = JSON.parse(jsonStr);

                        if (chunk.type === 'reasoning') {
                            streamedReasoning += chunk.text || '';
                            // Buffer in BackgroundRequestManager
                            if (bgRequest) {
                                BackgroundRequestManager.appendReasoning(requestSessionId, chunk.text || '');
                            }
                            // ALWAYS try to update ThinkingIndicator
                            // If user is viewing this session, they'll see live updates
                            // If not, ThinkingIndicator is hidden anyway, so this is harmless
                            ThinkingIndicator.appendReasoning(chunk.text || '');
                        } else if (chunk.type === 'content') {
                            const chunkText = chunk.text || '';
                            // Array push is O(1) vs string concat O(n)
                            _contentChunks.push(chunkText);

                            // Fast path: once artifact/patch streaming is active,
                            // only forward to panel. Filter out fence markers, handle file switches.
                            if (artifactStreamActive) {
                                if (window.artifactPanel) {
                                    // Buffer for cross-chunk fence detection
                                    if (!window._artifactStreamBuf) window._artifactStreamBuf = '';
                                    window._artifactStreamBuf += chunkText;
                                    let buf = window._artifactStreamBuf;

                                    // Check for file boundary: closing ``` + new artifact-file:
                                    if (buf.includes('artifact-file:')) {
                                        console.log('[STREAM-BUF] File fence found in buffer! len=' + buf.length, 'snippet=' + buf.substring(buf.indexOf('artifact-file:') - 20, buf.indexOf('artifact-file:') + 40));
                                    }
                                    const fileSwitchRe = /```\s*\n*```artifact-file:([^:]+):(\w+)\s*\n/gi;
                                    let switchMatch;
                                    let lastEnd = 0;
                                    let processed = '';
                                    while ((switchMatch = fileSwitchRe.exec(buf)) !== null) {
                                        // Code before the switch
                                        processed += buf.slice(lastEnd, switchMatch.index);
                                        lastEnd = switchMatch.index + switchMatch[0].length;
                                        // Flush current code to panel, then switch file
                                        if (processed) {
                                            window.artifactPanel.appendStreamingCode(processed);
                                            processed = '';
                                        }
                                        const newPath = switchMatch[1].trim();
                                        const newLang = switchMatch[2].trim();
                                        console.log('[ARTIFACT-STREAM] File switch:', newPath, newLang);
                                        if (window.artifactPanel.switchStreamingFile) {
                                            window.artifactPanel.switchStreamingFile(newPath, newLang);
                                        }
                                    }
                                    processed += buf.slice(lastEnd);

                                    // Strip standalone fence markers (``` on its own line)
                                    processed = processed.replace(/^```artifact-meta\s*\n[\s\S]*?```\s*\n?/i, '');
                                    processed = processed.replace(/^```artifact-file:[^\n]*\n/i, '');

                                    // Keep last 60 chars in buffer for cross-chunk fence detection
                                    // Only send the safe portion before that
                                    const KEEP = 60;
                                    if (processed.length > KEEP) {
                                        const toSend = processed.slice(0, processed.length - KEEP);
                                        // Strip closing fences from what we send
                                        const cleaned = toSend.replace(/\n```\s*$/m, '');
                                        if (cleaned) {
                                            window.artifactPanel.appendStreamingCode(cleaned);
                                        }
                                        window._artifactStreamBuf = processed.slice(processed.length - KEEP);
                                    } else {
                                        // Not enough data yet — keep buffering
                                        window._artifactStreamBuf = processed;
                                    }
                                }
                                if (bgRequest) {
                                    BackgroundRequestManager.appendContent(requestSessionId, chunkText);
                                }
                            } else if (patchStreamActive) {
                                patchStreamBuffer += chunkText;
                                const newPatches = extractCompletePatchesFromBuffer(patchStreamBuffer, patchParsedPatches.length);
                                for (const patch of newPatches) {
                                    patchParsedPatches.push(patch);
                                    window.artifactPanel.applyLivePatch(patch.file, patch.old_string, patch.new_string);
                                }
                                if (bgRequest) {
                                    BackgroundRequestManager.appendContent(requestSessionId, chunkText);
                                }
                            } else {
                                // Detection phase: not yet in artifact/patch mode.
                                // Only join the string when we need to run fence detection.
                                if (isFirstContentChunk) {
                                    isFirstContentChunk = false;
                                    window.scrollState?.contentArrived?.();
                                    ThinkingIndicator.setContentMode();
                                }
                                if (bgRequest) {
                                    BackgroundRequestManager.appendContent(requestSessionId, chunkText);
                                }

                                // Only run fence detection if chunk contains backtick (cheap check)
                                if (chunkText.includes('`')) {
                                    // Rebuild streamedContent only when needed
                                    streamedContent = _contentChunks.join('');
                                    _contentJoinedAt = _contentChunks.length;
                                    // DEBUG: log fence detection attempts
                                    if (streamedContent.includes('artifact')) {
                                        console.log('[ARTIFACT-DEBUG] Fence check:', {
                                            hasMetaFence: /```artifact-meta/i.test(streamedContent),
                                            hasFileFence: /```artifact-file:/i.test(streamedContent),
                                            hasOldFence: /```artifact(?![-_])/i.test(streamedContent),
                                            panelExists: !!window.artifactPanel,
                                            artifactStreamActive,
                                            contentLen: streamedContent.length,
                                            first200: streamedContent.substring(0, 200)
                                        });
                                    }

                                    // --- Artifact fence detection (new fenced format + old fallback) ---
                                    const panelHasArtifact = window.artifactPanel?.currentArtifact &&
                                        window.artifactPanel?.panel?.classList.contains('is-open');

                                    if (window.artifactPanel && !panelHasArtifact && !artifactStreamActive) {
                                        // New format: artifact-meta signals start
                                        const metaMatch = streamedContent.match(/```artifact-meta\s*\n/i);
                                        if (metaMatch) {
                                            // Check if meta block is closed and first file block started
                                            const fileMatch = streamedContent.match(/```artifact-file:([^:]+):(\w+)\s*\n/i);
                                            if (fileMatch) {
                                                artifactStreamActive = true;
                                                artifactFencePosition = metaMatch.index;
                                                window.artifactPanel.openStreaming();
                                                // Switch to first file
                                                const firstPath = fileMatch[1].trim();
                                                const firstLang = fileMatch[2].trim();
                                                if (window.artifactPanel.switchStreamingFile) {
                                                    window.artifactPanel.switchStreamingFile(firstPath, firstLang);
                                                }
                                                // Seed the streaming buffer with all code after the first file fence
                                                // The regular streaming path will handle file switches within it
                                                const fileContentStart = streamedContent.indexOf('\n', fileMatch.index) + 1;
                                                if (fileContentStart > 0) {
                                                    window._artifactStreamBuf = streamedContent.slice(fileContentStart);
                                                }
                                            }
                                        }

                                        // Fallback: old JSON format detection
                                        if (!artifactStreamActive) {
                                            const fenceMatch = streamedContent.match(/```artifact(?![-_])/i);
                                            if (fenceMatch) {
                                                artifactStreamActive = true;
                                                artifactFencePosition = fenceMatch.index;
                                                window.artifactPanel.openStreaming();
                                                const fenceEnd = streamedContent.indexOf('\n', fenceMatch.index);
                                                if (fenceEnd !== -1) {
                                                    const afterFence = streamedContent.slice(fenceEnd + 1);
                                                    if (afterFence) {
                                                        window.artifactPanel.appendStreamingCode(afterFence);
                                                    }
                                                }
                                            }
                                        }
                                    }

                                    // --- Patch fence detection ---
                                    if (!artifactStreamActive) {
                                        const patchFenceMatch = streamedContent.match(/```artifact_patch/i);
                                        if (patchFenceMatch && window.artifactPanel?.currentArtifact) {
                                            patchStreamActive = true;
                                            patchStreamBuffer = streamedContent.slice(patchFenceMatch.index);
                                            patchParsedPatches = [];
                                            window.artifactPanel.openPatchStreaming();
                                        }
                                    }
                                }
                            }
                        } else if (chunk.type === 'done') {
                            console.log('Received done chunk:', chunk);
                            streamedReasoning = chunk.reasoning || streamedReasoning;
                            // Join accumulated chunks if not already joined
                            if (_contentChunks.length > _contentJoinedAt) {
                                streamedContent = _contentChunks.join('');
                            }
                            streamedContent = chunk.content || streamedContent;
                            streamChatSummary = chunk.chat_summary || streamedContent;
                            streamRenderMode = chunk.render_mode || 'inline';
                            streamArtifact = chunk.artifact || null;
                            streamIsEmail = chunk.is_email || false;
                            streamEmailData = chunk.email || null;
                            streamCryptoChart = chunk.crypto_chart || null;
                            streamMemoryUpdated = chunk.memory_updated || false;
                            streamDreamNotification = chunk.dream_notification || null;
                            streamArtifactPatches = chunk.artifact_patches || null;
                            // Use backend's response_id if available for consistency
                            if (chunk.response_id) {
                                streamResponseId = chunk.response_id;
                            }
                            console.log('Parsed artifact from done:', streamArtifact);
                            console.log('Parsed email from done:', streamIsEmail, streamEmailData);
                            if (streamCryptoChart) console.log('Parsed crypto chart:', streamCryptoChart);
                            // Update session title in sidebar with typewriter animation
                            if (chunk.session_name) {
                                const titleName = chunk.session_name.trim();
                                if (titleName && titleName.toLowerCase() !== 'new chat') {
                                    // Defer slightly so tab DOM exists after session creation
                                    setTimeout(() => {
                                        const mgr = window.chatManager;
                                        if (mgr && requestSessionId && mgr.chatHistory?.[requestSessionId]) {
                                            const sess = mgr.chatHistory[requestSessionId];
                                            if (sess.session_name !== titleName) {
                                                sess.session_name = titleName;
                                                sess.autoTitleEligible = false;
                                                const tab = document.querySelector(`.chat-tab[data-session-id="${requestSessionId}"] .tab-name`);
                                                if (tab) {
                                                    typewriteTitle(tab, titleName);
                                                } else if (typeof mgr.renderChatTabs === 'function') {
                                                    mgr.renderChatTabs();
                                                }
                                            }
                                        }
                                    }, 100);
                                }
                            }
                        } else if (chunk.type === 'error') {
                            const errorText = chunk.text || 'Streaming error';
                            // If streaming isn't available for this model, fall back silently
                            if (errorText.includes('Streaming only available')) {
                                return 'fallback';
                            }
                            // API key or other configuration error - show to user
                            if (bgRequest) {
                                BackgroundRequestManager.errorRequest(requestSessionId, errorText);
                            }
                            if (isSessionActive()) {
                                ThinkingIndicator.hide(); hideSimplePulsingDot();
                                displayMessage(errorText, 'ai', 'text', prompt, false, generateMessageId());
                            }
                            return 'error';
                        }
                    } catch (parseError) {
                        // Only log actual JSON parse errors
                        if (parseError instanceof SyntaxError) {
                            console.warn('Failed to parse SSE chunk:', parseError);
                        }
                    }
                };

                while (true) {
                    const { done, value } = await reader.read();

                    if (value) {
                        buffer += decoder.decode(value, { stream: !done });
                    }

                    // Process complete events from buffer
                    const lines = buffer.split('\n\n');
                    buffer = lines.pop() || '';

                    for (const line of lines) {
                        const result = processLine(line);
                        if (result === 'error') return null;
                        if (result === 'fallback') throw new Error('Stream fallback');
                    }

                    if (done) {
                        // Process any remaining data in buffer
                        if (buffer.trim()) {
                            processLine(buffer);
                        }
                        break;
                    }
                }

                // Hide thinking indicator if session is still active
                if (isSessionActive()) {
                    ThinkingIndicator.hide(); hideSimplePulsingDot();
                }

                // Prepare completion data — strip internal protocol lines and artifact fences
                let displayContent = streamChatSummary || streamedContent;
                // Strip ACTION: routing line if it leaked through
                if (displayContent) {
                    displayContent = displayContent.replace(/^\s*[-*]*\s*ACTION:\s*\w+\s*\n*/i, '');
                }
                if (displayContent && (displayContent.includes('```artifact-meta') || displayContent.includes('```artifact-file:') || displayContent.includes('```artifact_patch'))) {
                    displayContent = displayContent
                        .replace(/```artifact-meta\s*\n[\s\S]*?```\s*\n?/gi, '')
                        .replace(/```artifact-file:[^\n]*\n[\s\S]*?```\s*\n?/gi, '')
                        .replace(/```artifact_patch[\s\S]*?```\s*\n?/gi, '')
                        .replace(/```artifact(?![-_])[\s\S]*?```\s*\n?/gi, '')
                        .trim();
                }
                const completionData = {
                    reasoning: streamedReasoning,
                    content: streamedContent,
                    chatSummary: streamChatSummary,
                    renderMode: streamRenderMode,
                    artifact: streamArtifact,
                    displayContent,
                    isDeepSeekR1,
                    prompt
                };

                // If session is no longer active, buffer the response and don't update UI
                if (!isSessionActive()) {
                    console.log('Session switched during streaming, buffering response for:', requestSessionId);
                    if (bgRequest) {
                        BackgroundRequestManager.completeRequest(requestSessionId, completionData);
                    }
                    return null;
                }

                // Session is still active - just clear the request, no need to complete it
                console.log('Session still active, displaying streaming response');

                // Create the AI message container - prefer backend's response_id for consistency
                const aiMsgId = streamResponseId || placeholderId || generateMessageId();

                const hasCode = displayContent.includes('```');

                const messageContainer = displayMessage(
                    displayContent,
                    'ai',
                    hasCode ? 'code' : 'text',
                    prompt,
                    true,
                    aiMsgId
                );

                // Add reasoning section if available (only for R1 model)
                if (isDeepSeekR1 && streamedReasoning && messageContainer) {
                    ReasoningDisplay.create(streamedReasoning, messageContainer);
                }

                // Handle email composer for streamed responses (backend flag)
                if (streamIsEmail && streamEmailData && streamEmailData.body && messageContainer && window.EmailComposer) {
                    const messageContent = messageContainer.querySelector('.message-content');
                    if (messageContent) {
                        messageContent.dataset.emailComposer = 'true';
                        messageContent.innerHTML = '';
                        window.EmailComposer.createCard(streamEmailData, messageContent);
                        console.log('Email composer card created for streamed response');
                    }
                }

                // Handle crypto/stock chart for streamed responses
                if (streamCryptoChart && messageContainer && window.CryptoChartModule) {
                    console.log('[Chart] Stream crypto chart data:', streamCryptoChart);
                    const messageContent = messageContainer.querySelector('.message-content');
                    if (messageContent) {
                        // Determine asset type and identifier
                        const assetType = streamCryptoChart.asset_type || 'crypto';
                        const asset = assetType === 'stock' ? streamCryptoChart.symbol : streamCryptoChart.coin;

                        console.log('[Chart] Rendering:', { assetType, asset });

                        if (asset) {
                            // Add intro text above chart
                            const introText = document.createElement('p');
                            introText.className = 'chart-intro-text';
                            introText.textContent = generateChartIntroText(asset, assetType);
                            messageContent.appendChild(introText);

                            // Create chart container after the intro text
                            const chartWrapper = document.createElement('div');
                            chartWrapper.className = 'crypto-chart-wrapper';
                            messageContent.appendChild(chartWrapper);

                            // Render the chart
                            window.CryptoChartModule.render(chartWrapper, asset, {
                                currency: streamCryptoChart.currency || 'usd',
                                range: streamCryptoChart.range || '1D',
                                assetType: assetType
                            }).catch(err => console.error('[Chart] Render error:', err));
                            console.log(`[Chart] ${assetType} chart rendered for:`, asset);
                        } else {
                            console.warn('[Chart] No asset found in crypto chart data:', streamCryptoChart);
                        }
                    }
                } else if (streamCryptoChart) {
                    console.warn('[Chart] Cannot render - missing:', {
                        hasContainer: !!messageContainer,
                        hasModule: !!window.CryptoChartModule
                    });
                }

                // Sync streamed message to local history (ensures reasoning is preserved on session switch)
                const manager = window.chatManager;
                console.log('[Reasoning Debug] Syncing to history:', {
                    hasManager: !!manager,
                    hasAddOrUpdate: !!manager?.addOrUpdateAiMessage,
                    aiMsgId,
                    hasReasoning: !!streamedReasoning,
                    reasoningLength: streamedReasoning?.length || 0
                });
                // Build artifact reference for local cache if artifact exists
                let streamArtifactRef = null;
                if (streamArtifact && streamArtifact.artifactId) {
                    streamArtifactRef = {
                        artifactId: streamArtifact.artifactId,
                        title: streamArtifact.title,
                        type: streamArtifact.type,
                        primaryLanguage: streamArtifact.primaryLanguage,
                        versionNumber: streamArtifact.activeVersion?.versionNumber || streamArtifact.versionCount || 1,
                        summary: displayContent
                    };
                }

                if (manager?.addOrUpdateAiMessage) {
                    manager.addOrUpdateAiMessage({
                        response_id: aiMsgId,
                        content: displayContent,
                        prompt: prompt,
                        is_code: hasCode,
                        render_mode: streamRenderMode || 'inline',
                        reasoning: streamedReasoning || null,
                        email: streamEmailData || null,
                        artifact_reference: streamArtifactRef,
                        crypto_chart: streamCryptoChart || null
                    });
                    // Also update with artifact fields via updateLastAiMessage for full persistence
                    if (streamArtifactRef && manager.updateLastAiMessage) {
                        manager.updateLastAiMessage({
                            messageId: aiMsgId,
                            artifact_reference: streamArtifactRef,
                            artifact: streamArtifact,
                            render_mode: streamRenderMode || 'inline'
                        });
                    }
                    // Persist to backend immediately so reasoning survives refresh
                    console.log('[Reasoning Debug] Attempting save:', {
                        hasSaveSession: !!manager.saveSession,
                        activeSessionId: manager.activeSessionId,
                        hasArtifact: !!streamArtifactRef
                    });
                    if (manager.saveSession && manager.activeSessionId) {
                        manager.saveSession(manager.activeSessionId, { silent: true })
                            .then(() => console.log('[Reasoning Debug] Save completed successfully'))
                            .catch(err => {
                                console.warn('Failed to auto-save after streaming response:', err);
                            });
                    }
                }

                // Handle artifact panel update
                if (patchStreamActive && streamArtifact && window.artifactPanel) {
                    // Live patch streaming was active — finalize with server result
                    patchStreamActive = false;
                    const artifactRef = streamArtifactRef || {
                        artifactId: streamArtifact.artifactId,
                        title: streamArtifact.title,
                        type: streamArtifact.type,
                        primaryLanguage: streamArtifact.primaryLanguage,
                        summary: displayContent
                    };
                    if (window.artifactPanel.isPatchStreaming) {
                        window.artifactPanel.finalizePatchStreaming(streamArtifact, {
                            sessionId: requestSessionId,
                            reference: artifactRef
                        });
                    } else {
                        // Patch streaming didn't activate — clear streaming state and show in CodeMirror
                        const panel = window.artifactPanel;
                        if (panel.isStreaming) {
                            panel.isStreaming = false;
                            panel._streamBuffer = '';
                            panel.panel.classList.remove('is-streaming');
                        }
                        panel.openArtifact(streamArtifact, {
                            sessionId: requestSessionId,
                            reference: artifactRef,
                            initialTab: 'code'
                        });
                        // Auto-enter edit mode so user sees CodeMirror editor
                        setTimeout(() => {
                            if (panel.currentVersion && !panel.isEditMode) {
                                panel.enterEditMode();
                            }
                        }, 300);
                    }
                } else if (streamArtifact && window.artifactPanel) {
                    const artifactRef = streamArtifactRef || {
                        artifactId: streamArtifact.artifactId,
                        title: streamArtifact.title,
                        type: streamArtifact.type,
                        primaryLanguage: streamArtifact.primaryLanguage,
                        summary: displayContent
                    };

                    if (streamArtifactPatches && streamArtifactPatches.patches && window.artifactPanel.applyPatchesVisually) {
                        // Fallback: patches arrived but live streaming didn't activate
                        window.artifactPanel.applyPatchesVisually(
                            streamArtifactPatches.patches,
                            streamArtifactPatches.patch_results,
                            streamArtifact,
                            { sessionId: requestSessionId, reference: artifactRef }
                        );
                    } else if (artifactStreamActive) {
                        // Live streaming was active — flush remaining buffer and finalize
                        if (window._artifactStreamBuf) {
                            const remaining = window._artifactStreamBuf.replace(/\n```\s*$/, '').replace(/```\s*$/, '');
                            if (remaining) window.artifactPanel.appendStreamingCode(remaining);
                            window._artifactStreamBuf = '';
                        }
                        window.artifactPanel.finalizeStreaming(streamArtifact, {
                            sessionId: requestSessionId,
                            reference: artifactRef
                        });
                    } else {
                        // Live streaming didn't activate — progressive typing into empty panel
                        const panel = window.artifactPanel;
                        const files = streamArtifact.activeVersion?.files || streamArtifact.files || [];
                        if (files.length > 0) {
                            panel.openStreaming();
                            // Set title immediately
                            if (panel.titleEl) panel.titleEl.textContent = streamArtifact.title || 'Artifact';
                            setTimeout(() => {
                                if (!panel.editCodeMirror) panel._ensureCodeMirror();
                                if (!panel.editCodeMirror) {
                                    panel.openArtifact(streamArtifact, { sessionId: requestSessionId, reference: artifactRef });
                                    return;
                                }
                                panel.editCodeMirror.setValue('');
                                panel.editCodeMirror.setOption('readOnly', true);
                                const modeMap = { jsx: 'jsx', javascript: 'javascript', js: 'javascript', css: 'css', html: 'htmlmixed', python: 'python' };
                                let fileIdx = 0, charIdx = 0;
                                const CHARS_PER_TICK = 35;
                                const TICK_MS = 14;

                                // Init first file
                                panel.editCodeMirror.setOption('mode', modeMap[files[0].language] || files[0].language);
                                if (panel.currentFileName) panel.currentFileName.textContent = files[0].path;
                                if (panel.fileSelect) {
                                    panel.fileSelect.innerHTML = '';
                                    files.forEach(f => {
                                        const opt = document.createElement('option');
                                        opt.value = f.path;
                                        opt.textContent = f.path;
                                        panel.fileSelect.appendChild(opt);
                                    });
                                }

                                console.log('[PROGRESSIVE] files:', files.map(f => ({ path: f.path, lang: f.language, len: (f.content||'').length })));
                                const tick = () => {
                                    const file = files[fileIdx];
                                    const content = file.content || '';
                                    const end = Math.min(charIdx + CHARS_PER_TICK, content.length);
                                    const chunk = content.slice(charIdx, end);
                                    if (chunk) {
                                        const doc = panel.editCodeMirror.getDoc();
                                        const last = doc.lastLine();
                                        doc.replaceRange(chunk, { line: last, ch: doc.getLine(last).length });
                                        panel.editCodeMirror.scrollIntoView({ line: doc.lastLine(), ch: 0 });
                                    }
                                    charIdx = end;
                                    if (charIdx >= content.length) {
                                        fileIdx++;
                                        charIdx = 0;
                                        if (fileIdx < files.length) {
                                            panel.editCodeMirror.setValue('');
                                            panel.editCodeMirror.setOption('mode', modeMap[files[fileIdx].language] || files[fileIdx].language);
                                            if (panel.currentFileName) panel.currentFileName.textContent = files[fileIdx].path;
                                            if (panel.fileSelect) panel.fileSelect.value = files[fileIdx].path;
                                        }
                                    }
                                    if (fileIdx < files.length) {
                                        setTimeout(tick, TICK_MS);
                                    } else {
                                        panel.isStreaming = false;
                                        panel.panel.classList.remove('is-streaming');
                                        if (panel.editCodeMirror) panel.editCodeMirror.setOption('readOnly', false);
                                        panel.openArtifact(streamArtifact, { sessionId: requestSessionId, reference: artifactRef });
                                    }
                                };
                                setTimeout(tick, 50);
                            }, 100);
                        } else {
                            panel.openArtifact(streamArtifact, { sessionId: requestSessionId, reference: artifactRef });
                        }
                    }
                }

                // Attach artifact preview if present
                console.log('Stream artifact data:', { streamArtifact, streamRenderMode, hasPanel: !!window.artifactPanel });
                if (streamArtifact && messageContainer && window.artifactPanel) {
                    console.log('Attaching artifact preview:', streamArtifact);
                    const artifactRef = streamArtifactRef || {
                        artifactId: streamArtifact.artifactId,
                        title: streamArtifact.title,
                        type: streamArtifact.type,
                        primaryLanguage: streamArtifact.primaryLanguage,
                        summary: displayContent
                    };
                    window.artifactPanel.attachPreview(messageContainer, artifactRef, {
                        payload: streamArtifact
                    });
                }

                // Show memory updated notice (streaming path)
                if (streamMemoryUpdated && messageContainer) {
                    const memoryNotice = document.createElement('div');
                    memoryNotice.className = 'memory-updated-notice';
                    memoryNotice.innerHTML = '<span class="memory-notice-icon">✓</span> Memory updated';
                    messageContainer.before(memoryNotice);
                    setTimeout(() => {
                        memoryNotice.classList.add('fade-out');
                        setTimeout(() => memoryNotice.remove(), 500);
                    }, 3000);
                }

                // Dream consolidation notification (streaming path)
                if (streamDreamNotification && typeof Toast !== 'undefined') {
                    const dn = streamDreamNotification;
                    const parts = [];
                    if (dn.pruned) parts.push(`${dn.pruned} pruned`);
                    if (dn.merged) parts.push(`${dn.merged} merged`);
                    if (dn.promoted) parts.push(`${dn.promoted} promoted`);
                    if (dn.synthesized) parts.push(`${dn.synthesized} new insight${dn.synthesized > 1 ? 's' : ''}`);
                    if (parts.length > 0) {
                        Toast.show({ message: `Memory improved: ${parts.join(', ')}`, type: 'info', duration: 5000 });
                    }
                }

                if (typeof window.scrollToBottom === 'function') {
                    window.scrollToBottom(true, true);
                }

                // Clear request from BackgroundRequestManager (successfully displayed)
                if (bgRequest) {
                    // Hide processing indicator since response is complete
                    BackgroundRequestManager.updateSessionProcessingIndicator(requestSessionId, false);
                    BackgroundRequestManager.clearRequest(requestSessionId);
                }

                return messageContainer;

            } catch (streamError) {
                ThinkingIndicator.hide(); hideSimplePulsingDot();
                if (streamError.name === 'AbortError') {
                    console.log('Stream request was aborted');
                    return null;
                }
                console.error('Streaming error:', streamError);
                // Fall back to regular endpoint on streaming error
                console.log('Falling back to regular /chat endpoint');
            }
        }

        // Regular (non-streaming) request for non-DeepSeek models
        response = await fetch('/chat', {
            method: 'POST',
            body: formData,
            headers: {
                'X-CSRFToken': getCSRFToken()
            },
            signal: bgRequest ? bgRequest.abortController.signal : requestAbortController.signal
        });

        // Hide thinking indicator once response arrives (only if session still active)
        if (isSessionActive()) {
            ThinkingIndicator.hide(); hideSimplePulsingDot();
        }

        const contentType = response.headers.get('content-type') || '';
        let payload = null;
        if (contentType.includes('application/json')) {
            payload = await response.json();
        } else {
            const textBody = await response.text();
            payload = textBody ? { error: textBody } : null;
        }

        // Handle response
        if (!response.ok) {
            if (response.status === 403) {
                const errorPayload = payload || {};
                // Check for guest limit reached
                if (errorPayload.error === 'guest_limit_reached') {
                    showGuestSignupModal();
                    resetState();
                    return;
                }
                location.reload();
                return;
            }

            const errorPayload = payload || {};
            if (typeof window.updateOfflineIndicator === 'function') {
                window.updateOfflineIndicator(
                    Boolean(errorPayload.offline_mode),
                    errorPayload.offline_reason,
                    errorPayload.offline_cached_messages || 0
                );
            }

            const serverMessage = errorPayload.error || `Server error: ${response.status}`;
            throw new Error(serverMessage);
        }

        const data = payload || {};
        if (typeof window.updateOfflineIndicator === 'function') {
            window.updateOfflineIndicator(
                Boolean(data.offline_mode),
                data.offline_reason,
                data.offline_cached_messages || 0
            );
        }
        const manager = (typeof window !== 'undefined' && window.chatManager) ? window.chatManager : null;
        const sessionId = data.session_id || manager?.activeSessionId || null;

        // Check if session is still active (user may have switched during request)
        // Use raw response - frontend handles markdown conversion via marked.parse()
        const displayContent = data.response || '';
        console.log('Regular response received, checking session active...');
        if (!isSessionActive()) {
            // Session was switched - start background streaming (no DOM, just timing simulation)
            console.log('Session switched during request, starting background streaming for:', requestSessionId);
            console.log('displayContent length:', displayContent.length);

            const completionData = {
                displayContent,
                reasoning: data.reasoning || '',
                content: displayContent,
                artifact: data.artifact || null,
                isDeepSeekR1: model === 'deepseek-reasoner',
                prompt,
                fullData: data  // Store full response for later
            };

            // Store completion data for when user switches back
            if (bgRequest) {
                BackgroundRequestManager.completeRequest(requestSessionId, completionData);
            }

            // Start background streaming with null container
            // Blue dot will appear ONLY when streaming completes
            const htmlContent = marked.parse(displayContent);
            const sanitizedHtmlContent = DOMPurify.sanitize(htmlContent);

            ProgressiveTextDisplay.start(requestSessionId, sanitizedHtmlContent, null, {
                isCode: false,
                completionData: completionData,
                onComplete: () => {
                    console.log('Background streaming finished for session:', requestSessionId);
                }
            });

            return null;
        }
        console.log('Session still active, displaying response normally');

        // Signal that real content has arrived - enable normal scrolling
        window.scrollState?.contentArrived?.();

        // Display AI response
        let messageContainer;
        const reuseIdForResponse = reuseMessageIdForResponse !== false && !!messageId;
        const aiMsgId = reuseIdForResponse ? messageId : (placeholderId || generateMessageId());
        if (data.image_url) {
            if (placeholderId && document.getElementById(placeholderId)) {
                replacePlaceholderWithImage(placeholderId, data.image_url, prompt);
                messageContainer = document.getElementById(placeholderId);
            } else {
                displayMessage('', 'ai', 'image-placeholder', prompt, true, aiMsgId);
                placeholderTimestamps.set(aiMsgId, Date.now());
                replacePlaceholderWithImage(aiMsgId, data.image_url, prompt);
                messageContainer = document.getElementById(aiMsgId);
            }
            if (manager?.updateLastAiMessage) {
                const attachmentList = Array.isArray(data.attachments) && data.attachments.length
                    ? data.attachments
                    : [{
                        name: 'Generated image',
                        isImage: true,
                        url: data.image_url
                    }];
                manager.updateLastAiMessage({
                    attachments: attachmentList,
                    messageType: 'image',
                    suppressText: true,
                    messageId: aiMsgId,
                    responseId: data.message_id
                });
            }
            if (manager?.recordGeneratedImage && data.image_url) {
                manager.recordGeneratedImage({
                    url: data.image_url,
                    prompt,
                    sessionId,
                    seed: data.seed || null,
                    provider: data.provider || '',
                    model: data.model || '',
                    model_label: data.model_label || '',
                    createdAt: new Date().toISOString()
                });
            }
        } else if (data.response) {
            // Use raw response - frontend handles markdown conversion via marked.parse()
            const content = data.response || '';
            messageContainer = displayMessage(
                content,
                'ai',
                data.is_code ? 'code' : 'text',
                prompt,
                true,
                aiMsgId,
                null,
                Array.isArray(data.search_results) ? data.search_results : null,
                {
                    auto_selected: !!data.auto_selected,
                    model_id: data.model_id || null,
                    model_label: data.model_label || null,
                    model_provider: data.model_provider || null,
                    model_provider_label: data.model_provider_label || null,
                    task_type: data.task_type || null,
                }
            );

            // Add reasoning section if DeepSeek R1 returned reasoning content
            if (data.reasoning && messageContainer) {
                ReasoningDisplay.create(data.reasoning, messageContainer);
            }

            // Sync AI message to local history (ensures reasoning is preserved on session switch)
            if (manager?.addOrUpdateAiMessage) {
                manager.addOrUpdateAiMessage({
                    response_id: aiMsgId,
                    content: displayContent,
                    prompt: prompt,
                    is_code: data.is_code,
                    render_mode: data.render_mode || 'inline',
                    reasoning: data.reasoning || null,
                    email: data.email || null,
                    crypto_chart: data.crypto_chart || null,
                    auto_selected: !!data.auto_selected,
                    model_id: data.model_id || null,
                    model_label: data.model_label || null,
                    model_provider: data.model_provider || null,
                    model_provider_label: data.model_provider_label || null,
                    task_type: data.task_type || null,
                });
                // Persist to backend immediately so reasoning survives refresh
                if (manager.saveSession && manager.activeSessionId) {
                    manager.saveSession(manager.activeSessionId, { silent: true }).catch(err => {
                        console.warn('Failed to auto-save after response:', err);
                    });
                }
            }
        } else if (placeholderId) {
            // Replace placeholder with a gentle failure message if no image returned
            messageContainer = displayMessage(
                'Image unavailable. Please try again.',
                'ai',
                'text',
                prompt,
                true,
                aiMsgId
            );
        }

        // Debug logging for artifact panel
        console.log('Artifact debug:', {
            render_mode: data.render_mode,
            has_artifact_reference: !!data.artifact_reference,
            artifact_reference: data.artifact_reference,
            has_artifact: !!data.artifact,
            has_messageContainer: !!messageContainer,
            artifactPanel_exists: !!window.artifactPanel
        });

        // Show toast if artifact parsing failed
        if (data.artifact_error && window.Toast) {
            window.Toast.warning('Artifact update failed: ' + data.artifact_error + '. Try asking again.', 6000);
        }

        // Show artifact pill only for artifact modes, NOT for code_window or inline
        // code_window = small snippets displayed inline without artifact panel
        const showArtifactPill = data.render_mode &&
            data.render_mode !== 'inline' &&
            data.render_mode !== 'code_window';

        if (showArtifactPill && messageContainer) {
            const artifactRef = Object.assign({}, data.artifact_reference || {});
            if (!artifactRef.artifactId && data.artifact?.artifactId) {
                artifactRef.artifactId = data.artifact.artifactId;
            }
            artifactRef.summary = artifactRef.summary || data.chat_summary || data.response || '';
            artifactRef.title = artifactRef.title || data.artifact?.title || 'Artifact';
            artifactRef.type = artifactRef.type || data.artifact?.type || 'project';
            artifactRef.primaryLanguage = artifactRef.primaryLanguage || data.artifact?.primaryLanguage || data.artifact?.language || 'code';
            artifactRef.pending = artifactRef.pending || data.artifact?.pending || data.artifact_status === 'pending_persist';
            if (window.artifactPanel) {
                window.artifactPanel.attachPreview(messageContainer, artifactRef, {
                    sessionId,
                    payload: data.artifact
                });
                // Auto-open the artifact panel for non-streaming responses
                if (data.artifact) {
                    window.artifactPanel.openArtifact(data.artifact, {
                        sessionId,
                        reference: artifactRef
                    });
                }
            }
            if (manager?.updateLastAiMessage) {
                manager.updateLastAiMessage({
                    artifact_reference: artifactRef,
                    artifact: data.artifact,
                    render_mode: data.render_mode,
                    messageId: aiMsgId
                });
            }
        }

        // Handle email composer - if user requested an email (backend flag)
        if (data.is_email && data.email && data.email.body && messageContainer && window.EmailComposer) {
            const messageContent = messageContainer.querySelector('.message-content');
            if (messageContent) {
                // Mark container to prevent async text rendering from adding more content
                messageContent.dataset.emailComposer = 'true';
                // Clear the duplicate text, show only the composer card
                messageContent.innerHTML = '';
                window.EmailComposer.createCard(data.email, messageContent);
                console.log('Email composer card created');
            }
        }

        // Handle crypto/stock chart - if user asked for price/chart
        if (data.crypto_chart && messageContainer && window.CryptoChartModule) {
            console.log('[Chart] Non-stream crypto chart data:', data.crypto_chart);
            const messageContent = messageContainer.querySelector('.message-content');
            if (messageContent) {
                // Determine asset type and identifier
                const assetType = data.crypto_chart.asset_type || 'crypto';
                const asset = assetType === 'stock' ? data.crypto_chart.symbol : data.crypto_chart.coin;

                console.log('[Chart] Rendering (non-stream):', { assetType, asset });

                if (asset) {
                    // Add intro text above chart
                    const introText = document.createElement('p');
                    introText.className = 'chart-intro-text';
                    introText.textContent = generateChartIntroText(asset, assetType);
                    messageContent.appendChild(introText);

                    // Create chart container after the intro text
                    const chartWrapper = document.createElement('div');
                    chartWrapper.className = 'crypto-chart-wrapper';
                    messageContent.appendChild(chartWrapper);

                    // Render the chart
                    window.CryptoChartModule.render(chartWrapper, asset, {
                        currency: data.crypto_chart.currency || 'usd',
                        range: data.crypto_chart.range || '1D',
                        assetType: assetType
                    }).catch(err => console.error('[Chart] Render error:', err));
                    console.log(`[Chart] ${assetType} chart rendered for:`, asset);
                } else {
                    console.warn('[Chart] No asset found in crypto chart data:', data.crypto_chart);
                }
            }
        } else if (data.crypto_chart) {
            console.warn('[Chart] Cannot render (non-stream) - missing:', {
                hasContainer: !!messageContainer,
                hasModule: !!window.CryptoChartModule
            });
        }

        // Handle search results
        if (data.search_results?.length > 0) {
            populateRightSidebar(data.search_results);
            if (typeof window.attachSourcesToMessage === 'function') {
                window.attachSourcesToMessage(aiMsgId, data.search_results);
            }
        }

        // Show subtle memory updated notice (above AI response, auto-vanish)
        if (data.memory_updated && messageContainer) {
            const memoryNotice = document.createElement('div');
            memoryNotice.className = 'memory-updated-notice';
            memoryNotice.innerHTML = '<span class="memory-notice-icon">✓</span> Memory updated';
            messageContainer.before(memoryNotice);

            // Auto-vanish after 3 seconds
            setTimeout(() => {
                memoryNotice.classList.add('fade-out');
                setTimeout(() => memoryNotice.remove(), 500);
            }, 3000);
        }

        // Dream consolidation notification
        if (data.dream_notification) {
            const dn = data.dream_notification;
            const parts = [];
            if (dn.pruned) parts.push(`${dn.pruned} pruned`);
            if (dn.merged) parts.push(`${dn.merged} merged`);
            if (dn.promoted) parts.push(`${dn.promoted} promoted`);
            if (dn.synthesized) parts.push(`${dn.synthesized} new insight${dn.synthesized > 1 ? 's' : ''}`);
            if (dn.superseded) parts.push(`${dn.superseded} resolved`);
            if (parts.length > 0 && window.Toast) {
                window.Toast.info(`Memory improved: ${parts.join(', ')}`, { duration: 5000 });
            }
        }

        // Handle guest mode response data
        if (data.guest_mode) {
            // Update guest message count on frontend
            if (typeof data.guest_message_count === 'number') {
                updateGuestMessageCount(data.guest_message_count);
            }
            // Show banner after threshold
            if (data.guest_show_banner) {
                showGuestSignupBanner();
            }
        }

        if (manager) {
            if (sessionId && manager.chatHistory?.[sessionId]) {
                const session = manager.chatHistory[sessionId];
                if (data.session_name) {
                    const newName = data.session_name.trim();
                    if (newName && session.session_name !== newName) {
                        session.session_name = newName;
                        session.autoTitleEligible = false;
                        const tab = document.querySelector(`.chat-tab[data-session-id="${sessionId}"] .tab-name`);
                        if (tab) {
                            typewriteTitle(tab, newName);
                        } else if (typeof manager.renderChatTabs === 'function') {
                            manager.renderChatTabs();
                        }
                    }
                }
                if (data.active_page_id && session.activePageId !== data.active_page_id) {
                    session.activePageId = data.active_page_id;
                }
            }
            if (typeof manager.saveSession === 'function' && manager.activeSessionId) {
                manager.saveSession(manager.activeSessionId, { silent: true }).catch(error => {
                    console.error('Failed to auto-save session', error);
                });
            }
        }

        if (typeof window.scrollToBottom === 'function') {
            window.scrollToBottom(true, true);
        }

        // Clear request from BackgroundRequestManager (response was displayed successfully)
        // Don't call completeRequest here - that's only for buffered responses
        if (bgRequest) {
            // Hide processing indicator since response is complete
            BackgroundRequestManager.updateSessionProcessingIndicator(requestSessionId, false);
            BackgroundRequestManager.clearRequest(requestSessionId);
        }

        return messageContainer;

    } catch (error) {
        if (error.name === 'AbortError') {
            console.log('Request was aborted');
        } else {
            console.error('Send message error:', error);
            if (typeof window.updateOfflineIndicator === 'function' && (error.name === 'TypeError' || /NetworkError|Failed to fetch/i.test(error.message || ''))) {
                window.updateOfflineIndicator(true, 'network_error');
            }
            // Only show error in UI if session is still active
            if (isSessionActive()) {
                handleMessageError(error, messageId);
            }
            // Mark request as errored in BackgroundRequestManager
            if (bgRequest) {
                BackgroundRequestManager.errorRequest(requestSessionId, error.message);
            }
        }
        return null;

    } finally {
        const stillActive = isSessionActive();

        // ChatGPT-style: mark streaming ended and ensure scroll is at bottom
        window.scrollState?.setStreamingState?.(false);

        // Reset scroll lock flags — response is done, scroll should be at bottom
        if (window._keepMessageAtTop) {
            window._keepMessageAtTop = false;
            const chatDisplayEl = document.getElementById('chat-display');
            if (chatDisplayEl) {
                chatDisplayEl.classList.remove('scroll-padding-active');
            }
        }
        if (window.scrollState) {
            window.scrollState.isPinnedToBottom = true;
        }
        // Ensure we're at the bottom after response completes
        if (typeof window.scrollToBottom === 'function') {
            window.scrollToBottom(true, true);
        }

        // Hide thinking indicator if session is still active
        if (stillActive) {
            ThinkingIndicator.hide(); hideSimplePulsingDot();
        }

        // Reset typing stopped flag
        isTypingStopped = false;

        // Update send button state if still in same session (only if not streaming)
        if (stillActive) {
            // Only hide stop button if no animation is active
            resetSendButtonIfIdle();
            const promptInput = document.getElementById('prompt');
            if (promptInput) {
                if (!messageId) {
                    promptInput.value = '';
                }
                // Don't refocus on mobile - keeps keyboard dismissed after send
                const isMobile = document.body.classList.contains('device-phone') ||
                                document.body.classList.contains('is-mobile') ||
                                document.body.classList.contains('is-ios') ||
                                document.body.classList.contains('device-tablet');
                if (!isMobile) {
                    promptInput.focus();
                }
            }
        }

        if (typeof window !== 'undefined') {
            if (typeof window.clearAttachedFiles === 'function') {
                window.clearAttachedFiles();
            }
            const fileInput = document.getElementById('file-upload');
            if (fileInput) {
                fileInput.value = '';
            }
        }

        // Update button state only if no typing animation is active
        resetSendButtonIfIdle();

        // Mark active session as read so the blue notification dot doesn't appear
        // after switching away (last_message_at updated on server, lastReadAt must match)
        const finalSessionId = requestSessionId || window.chatManager?.activeSessionId;
        if (finalSessionId && window.UnreadNotificationManager?.markSessionRead) {
            window.UnreadNotificationManager.markSessionRead(finalSessionId);
        }
    }
}

function resetState() {
    isRequestInProgress = false;
    isTypingStopped = false;
    const promptInput = document.getElementById('prompt');
    if (promptInput) {
        promptInput.disabled = false;
    }
    resetSendButtonIfIdle();
}

// Abort handler function
function abortMessage() {
    if (abortController) {
        abortController.abort();
        abortController = null;
    }
    isRequestInProgress = false;
    isTypingStopped = true;

    // Stop ProgressiveTextDisplay for current session
    const currentSessionId = window.chatManager?.activeSessionId;
    if (currentSessionId && ProgressiveTextDisplay) {
        ProgressiveTextDisplay.complete(currentSessionId);
        ProgressiveTextDisplay.remove(currentSessionId);
    }

    // Also stop the old typewriter animator if active
    if (window.activeAnimator) {
        window.activeAnimator.stop();
    }

    const promptInput = document.getElementById('prompt');
    if (promptInput) {
        promptInput.disabled = false;
    }

    resetSendButtonIfIdle();
}

// Expose abortMessage globally for other modules (e.g., typewriter stop)
if (typeof window !== 'undefined') {
    window.abortMessage = abortMessage;
}

function populateRightSidebar(searchResults) {
    const resultsContainer = document.getElementById("search-results-container");
    if (!resultsContainer) {
        console.error("Search results container not found.");
        return;
    }

    // Clear existing results
    resultsContainer.innerHTML = "";

    if (searchResults && searchResults.length > 0) {
        const searchSection = document.createElement("div");
        searchSection.className = "search-results-section";

        const searchHeader = document.createElement("div");
        searchHeader.className = "search-header";
        searchHeader.innerHTML = `
            <div class="search-title">
                <i class="fas fa-search"></i>
                <span>Search Results</span>
            </div>
        `;
        searchSection.appendChild(searchHeader);

        const resultsList = document.createElement("div");
        resultsList.className = "search-results-list";

        searchResults.forEach((result) => {
            const resultDiv = document.createElement("div");
            resultDiv.className = "search-result";

            if (result.website_name || result.source_domain) {
                const sourceDiv = document.createElement("div");
                sourceDiv.className = "result-source";

                const favicon = document.createElement("img");
                favicon.src = `https://www.google.com/s2/favicons?domain=${result.source_domain}`;
                favicon.alt = `${result.website_name || result.source_domain} favicon`;
                favicon.className = "result-favicon";
                sourceDiv.appendChild(favicon);

                const sourceText = document.createElement("span");
                sourceText.textContent = result.website_name || result.source_domain;
                sourceDiv.appendChild(sourceText);

                resultDiv.appendChild(sourceDiv);
            }

            if (result.title && result.link) {
                const titleLink = document.createElement("a");
                titleLink.href = result.link;
                titleLink.target = "_blank";
                titleLink.rel = "noopener noreferrer";
                titleLink.className = "result-title";
                titleLink.textContent = result.title;
                resultDiv.appendChild(titleLink);
            }

            if (result.snippet) {
                const snippetDiv = document.createElement("div");
                snippetDiv.className = "result-snippet";
                snippetDiv.textContent = result.snippet;
                resultDiv.appendChild(snippetDiv);
            }

            resultsList.appendChild(resultDiv);
        });

        searchSection.appendChild(resultsList);
        resultsContainer.appendChild(searchSection);

        // Update favicons in the "Sources" button
        updateSourcesButton(searchResults);

        toggleRightSidebar(true);
    } else {
        const noResults = document.createElement("div");
        noResults.className = "no-results";
        noResults.textContent = "No search results found.";
        resultsContainer.appendChild(noResults);
        if (typeof updateSourcesButton === 'function') {
            updateSourcesButton([]);
        }
        toggleRightSidebar(true);
    }
}

// Loader to indicate the debugging process with fading animation
function showLoader(button) {
    button.innerHTML = '<div class="spinner"></div> Debugging...';
    button.disabled = true; // Disable the button during the process
    button.classList.add('fading-text'); // Apply the CSS fading animation
}


// Remove loader and stop the animation when the debug process is done
function removeLoader(button) {
    // Reset the button content back to only the debug icon
    button.innerHTML = '<i class="fas fa-bug"></i>';
    button.disabled = false; // Re-enable the button
    button.classList.remove('fading-text'); // Remove the CSS fading animation
}


// Function to handle the debugging process
async function debugCodeWithLoader(text, debugButton) {
    try {
        // Call the actual debugCode function to send the code for debugging
        await debugCode(text); // Wait until the debugging process is finished
        // Remove loader after the debugging and the debug window is created
        removeLoader(debugButton);
    } catch (error) {
        console.error('Error during debugging:', error);
        removeLoader(debugButton);
        alert('Error during debugging: ' + error.message);
    }
}

// Function to send code to Flask API for debugging
async function debugCode(text) {
    console.log("Sending code to Flask API for debugging...");

    try {
        const csrfToken = getCSRFToken(); // Get the CSRF token

        const response = await fetch('/debug', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': csrfToken // Include the CSRF token in the headers
            },
            body: JSON.stringify({ code: text }),
        });

        if (!response.ok) {
            throw new Error(`Server error: ${response.statusText}`);
        }

        const data = await response.json();

        if (data.error) {
            throw new Error(data.error);
        }

        const debuggedCode = data.debugged_code;
        if (!debuggedCode) {
            throw new Error('No debugged code returned from the server.');
        }

        // Create and display the debug window with the debugged code
        createDebugWindow(text, debuggedCode);
    } catch (error) {
        console.error('Error during the debugging process:', error);
        createDebugWindow(`Error: ${error.message}`);
    }
}

// Helper function to get CSRF token from meta tag
function getCSRFToken() {
    return document.querySelector('meta[name="csrf-token"]').getAttribute('content');
}

function createDebugWindow(originalCode, debuggedCode, logs = [], variables = {}, performanceMetrics = {}) {
    // Utility function to create a button with an icon and title
    function createIconButton(className, iconHtml, title, onClick) {
        const button = document.createElement('button');
        button.className = className;
        button.innerHTML = iconHtml;
        button.title = title;
        button.onclick = onClick;
        return button;
    }

    // Create the main debug window element
    const debugWindow = document.createElement('div');
    debugWindow.className = 'debug-window dark-theme fade-in focused'; // Dark theme, initial animation

    // Create the header (draggable)
    const header = document.createElement('div');
    header.className = 'debug-header draggable'; // Draggable feature

    // Title of the debug window
    const title = document.createElement('span');
    title.className = 'debug-title';
    title.textContent = 'Debug Output';
    header.appendChild(title);

    // Close button
    const closeButton = createIconButton(
        'close-debug-window',
        '<i class="fas fa-times"></i>', // Font Awesome close icon
        'Close Debug Window',
        () => {
            debugWindow.classList.add('fade-out');
            setTimeout(() => debugWindow.remove(), 1400); // Wait for fade-out animation
        }
    );
    header.appendChild(closeButton);

    debugWindow.appendChild(header); // Add header to debug window

    // Create tab headers
    const tabHeader = document.createElement('div');
    tabHeader.className = 'debug-tab-header';

    // Tab configurations: Logs, Variables, Performance
    const tabs = [
        { name: 'Logs', icon: '<i class="fas fa-file-alt"></i>' },        // Document icon
        { name: 'Variables', icon: '<i class="fas fa-cogs"></i>' },       // Gears icon
        { name: 'Performance', icon: '<i class="fas fa-chart-line"></i>' } // Chart icon
    ];

    // Create and append tab buttons
    tabs.forEach((tab, index) => {
        const tabButton = document.createElement('button');
        tabButton.className = `debug-tab ${index === 0 ? 'active-tab' : ''}`; // Set first tab as active
        tabButton.innerHTML = `${tab.icon} ${tab.name}`; // Icon and tab name
        tabButton.onclick = () => switchTab(tab.name, debugWindow, tabContents); // Switch tab logic
        tabHeader.appendChild(tabButton);
    });

    debugWindow.appendChild(tabHeader); // Add tab header to debug window

    // Create content wrapper for tab contents
    const contentWrapper = document.createElement('div');
    contentWrapper.className = 'debug-content-wrapper';

    // Create content sections for each tab (Logs, Variables, Performance)
    const tabContents = {
        Logs: document.createElement('div'),
        Variables: document.createElement('div'),
        Performance: document.createElement('div')
    };

    // Iterate over tabContents to set up each tab's content
    Object.entries(tabContents).forEach(([key, contentDiv]) => {
        contentDiv.className = `debug-content ${key.toLowerCase()} ${key === 'Logs' ? 'active-content' : ''}`; // Logs tab active by default
        contentWrapper.appendChild(contentDiv);
    });

    debugWindow.appendChild(contentWrapper); // Add content wrapper to the debug window

    // Append the debug window to the body and focus on it
    document.body.appendChild(debugWindow);
    debugWindow.scrollIntoView({ behavior: 'smooth', block: 'center' });
    setTimeout(() => debugWindow.classList.remove('focused'), 2000);

    // Make the debug window draggable and resizable
    makeDraggable(debugWindow, header);
    makeResizable(debugWindow);

    // Extract code blocks from debugged code and format with line numbers
    const codeBlockRegex = /```([\s\S]*?)```|<code>([\s\S]*?)<\/code>|~~~([\s\S]*?)~~~/g;
    let extractedCode = [];
    let match;

    while ((match = codeBlockRegex.exec(debuggedCode)) !== null) {
        extractedCode.push((match[1] || match[2] || match[3]).trim());
    }

    extractedCode = extractedCode.length > 0 ? extractedCode.join('\n\n') : 'No code available';
    
    // If code is available, format and display it in Variables tab
    if (extractedCode !== 'No code available') {
        const formattedCode = extractedCode.split('\n').map((line, index) => `${String(index + 1).padStart(4, ' ')} |   ${line}`).join('\n');
        
        const preElement = document.createElement('pre');
        const codeElement = document.createElement('code');
        codeElement.className = 'language-javascript'; // Assuming JavaScript for syntax highlighting
        codeElement.textContent = formattedCode;
        preElement.appendChild(codeElement);
        
        tabContents.Variables.innerHTML = ''; // Clear before inserting
        tabContents.Variables.appendChild(preElement);

        // Apply syntax highlighting after DOM insertion
        requestAnimationFrame(() => {
            if (typeof Prism !== 'undefined') {
                Prism.highlightElement(codeElement); // Use Prism.js if available
            } else if (typeof hljs !== 'undefined') {
                hljs.highlightElement(codeElement); // Use Highlight.js if available
            }
        });
    }

    // Populate tab contents
    populateTabContent(logs, { extractedCode }, performanceMetrics, tabContents);

    // Animate the code correction display in the Logs tab
    animateCodeCorrection(originalCode, debuggedCode, tabContents.Logs);
}



// Function to switch between tabs
function switchTab(tabName, debugWindow, tabContents) {
    // Remove active class from all tab buttons
    const tabButtons = debugWindow.querySelectorAll('.debug-tab');
    tabButtons.forEach(button => {
        button.classList.remove('active-tab');
    });

    // Add active class to the clicked tab
    const activeTabButton = Array.from(tabButtons).find(button => button.textContent === tabName);
    if (activeTabButton) {
        activeTabButton.classList.add('active-tab');
    }

    // Hide all tab contents
    const contentDivs = debugWindow.querySelectorAll('.debug-content');
    contentDivs.forEach(div => {
        div.classList.remove('active-content');
    });

    // Show the selected tab content
    if (tabContents[tabName]) {
        tabContents[tabName].classList.add('active-content');
    }
}


// Function to populate the tab content dynamically
function populateTabContent(logs, variables, performanceMetrics, tabContents) {
    // Populate logs
    const logContent = tabContents.Logs;
    logContent.innerHTML = '';
    if (logs.length > 0) {
        logs.forEach((log, index) => {
            const logEntry = document.createElement('div');
            logEntry.className = 'log-entry';
            logEntry.textContent = `${index + 1}: ${log}`;
            logContent.appendChild(logEntry);
        });
    } else {
        logContent.textContent = 'No logs available';
    }

    // Populate variables
    const variableContent = tabContents.Variables;
    variableContent.innerHTML = '';
    if (Object.keys(variables).length > 0) {
        for (const [varName, varValue] of Object.entries(variables)) {
            const variableEntry = document.createElement('div');
            variableEntry.className = 'variable-entry';
            variableEntry.textContent = `${varName}:
${varValue}`;
            variableContent.appendChild(variableEntry);
        }
    } else {
        variableContent.textContent = 'No variables available';
    }

    // Populate performance metrics
    const performanceContent = tabContents.Performance;
    performanceContent.innerHTML = '';
    if (Object.keys(performanceMetrics).length > 0) {
        for (const [metricName, metricValue] of Object.entries(performanceMetrics)) {
            const performanceEntry = document.createElement('div');
            performanceEntry.className = 'performance-entry';
            performanceEntry.textContent = `${metricName}: ${metricValue}`;
            performanceContent.appendChild(performanceEntry);
        }
    } else {
        performanceContent.textContent = 'No performance metrics available';
    }
}

function makeResizable(el) {
    let isResizing = false;

    // Create the resizer handle
    const resizer = document.createElement('div');
    resizer.style.width = '35px';
    resizer.style.height = '35px';
    resizer.style.position = 'absolute';
    resizer.style.right = '0';
    resizer.style.bottom = '0';
    resizer.style.cursor = 'se-resize';
    resizer.style.zIndex = '100';
    resizer.style.display = 'flex';
    resizer.style.alignItems = 'center';
    resizer.style.justifyContent = 'center';
    resizer.style.background = 'transparent'; // Set background to transparent
    resizer.style.borderRadius = '0 0 6px 0';
    resizer.style.boxShadow = '0px 0px 5px rgba(0, 0, 0, 0.5)';
    resizer.textContent = '↘'; // Add a Unicode arrow symbol for resize

    resizer.style.color = '#ffffff'; // Make sure the symbol stands out

    // Ensure the element has relative or absolute positioning for resizing to work correctly
    const computedStyle = window.getComputedStyle(el);
    if (computedStyle.position === 'static') {
        el.style.position = 'relative';
    }

    // Append resizer handle to the element
    el.appendChild(resizer);

    // Add event listeners for the resizer handle
    resizer.addEventListener('mousedown', resizeMouseDown);
    resizer.addEventListener('touchstart', resizeMouseDown, { passive: false });

    function resizeMouseDown(e) {
        e.preventDefault();
        isResizing = true;

        document.addEventListener('mousemove', resizeElement);
        document.addEventListener('mouseup', stopResizing);
        document.addEventListener('touchmove', resizeElement, { passive: false });
        document.addEventListener('touchend', stopResizing);
    }

    function resizeElement(e) {
        if (!isResizing) return;

        e.preventDefault();
        let currentX, currentY;

        if (e.type === 'touchmove') {
            currentX = e.touches[0].clientX;
            currentY = e.touches[0].clientY;
        } else {
            currentX = e.clientX;
            currentY = e.clientY;
        }

        // Calculate new dimensions
        const newWidth = Math.max(50, currentX - el.getBoundingClientRect().left);
        const newHeight = Math.max(50, currentY - el.getBoundingClientRect().top);

        // Apply new width and height to the element
        el.style.width = `${newWidth}px`;
        el.style.height = `${newHeight}px`;
    }

    function stopResizing() {
        isResizing = false;

        document.removeEventListener('mousemove', resizeElement);
        document.removeEventListener('mouseup', stopResizing);
        document.removeEventListener('touchmove', resizeElement);
        document.removeEventListener('touchend', stopResizing);
    }
}


// Function to make the element draggable
function makeDraggable(el, handle) {
    let offsetX = 0, offsetY = 0, mouseX = 0, mouseY = 0;

    handle.addEventListener('mousedown', dragMouseDown);
    handle.addEventListener('touchstart', dragMouseDown, { passive: false });

    function dragMouseDown(e) {
        e.preventDefault();
        handle.style.cursor = 'grabbing';

        if (e.type === 'touchstart') {
            mouseX = e.touches[0].clientX;
            mouseY = e.touches[0].clientY;
        } else {
            mouseX = e.clientX;
            mouseY = e.clientY;
        }

        document.addEventListener('mousemove', dragElement);
        document.addEventListener('mouseup', stopDragging);
        document.addEventListener('touchmove', dragElement, { passive: false });
        document.addEventListener('touchend', stopDragging);
    }

    function dragElement(e) {
        e.preventDefault();

        let currentX, currentY;

        if (e.type === 'touchmove') {
            currentX = e.touches[0].clientX;
            currentY = e.touches[0].clientY;
        } else {
            currentX = e.clientX;
            currentY = e.clientY;
        }

        offsetX = mouseX - currentX;
        offsetY = mouseY - currentY;
        mouseX = currentX;
        mouseY = currentY;

        el.style.top = (el.offsetTop - offsetY) + "px";
        el.style.left = (el.offsetLeft - offsetX) + "px";
    }

    function stopDragging() {
        handle.style.cursor = 'grab';
        document.removeEventListener('mousemove', dragElement);
        document.removeEventListener('mouseup', stopDragging);
        document.removeEventListener('touchmove', dragElement);
        document.removeEventListener('touchend', stopDragging);
    }
}


function animateCodeCorrection(originalCode, debuggedCode, codeWrapper, options = {}) {
    // Extract lines from original and debugged code
    const originalLines = originalCode.split('\n');
    const debuggedLines = debuggedCode.split('\n');
    const maxLines = Math.max(originalLines.length, debuggedLines.length);

    // Set up customizable options with defaults
    const {
        animationSpeed = 200,
        highlightClass = 'highlight-line',
        addedClass = 'added-line',
        removedClass = 'removed-line',
        modifiedClass = 'modified-line',
        lineNumberClass = 'line-number',
        lineWrapperClass = 'code-line-wrapper',
        codeLineClass = 'code-line',
        pauseOnClick = true,
    } = options;

    // Prepare code lines data and create fragment for DOM manipulation
    const codeLines = [];
    const fragment = document.createDocumentFragment();

    for (let i = 0; i < maxLines; i++) {
        const lineWrapper = document.createElement('div');
        lineWrapper.className = lineWrapperClass;

        const lineNumber = document.createElement('div');
        lineNumber.className = lineNumberClass;
        lineNumber.textContent = i + 1;
        lineWrapper.appendChild(lineNumber);

        const lineElement = document.createElement('div');
        lineElement.className = codeLineClass;
        lineElement.textContent = originalLines[i] || '';
        lineWrapper.appendChild(lineElement);

        fragment.appendChild(lineWrapper);

        codeLines.push({
            lineWrapper,
            lineElement,
            originalLine: originalLines[i] || '',
            debuggedLine: debuggedLines[i] || '',
        });
    }

    // Append all elements at once to avoid multiple reflows
    codeWrapper.innerHTML = '';
    codeWrapper.appendChild(fragment);

    let currentLine = 0;
    let isPaused = false;

    // Function to animate correction of lines one by one
    function updateLineSequentially() {
        if (isPaused || currentLine >= maxLines) return;

        const { lineElement, originalLine, debuggedLine } = codeLines[currentLine];

        // Highlight the current line
        lineElement.classList.add(highlightClass);

        // Animate the correction after a delay
        setTimeout(() => {
            if (originalLine !== debuggedLine) {
                lineElement.textContent = debuggedLine;

                // Apply appropriate class based on type of change
                if (!originalLine) {
                    lineElement.classList.add(addedClass);
                } else if (!debuggedLine) {
                    lineElement.classList.add(removedClass);
                } else {
                    lineElement.classList.add(modifiedClass);
                }
            }

            // Remove highlight and move to the next line
            lineElement.classList.remove(highlightClass);
            currentLine++;
            requestAnimationFrame(updateLineSequentially); // Use requestAnimationFrame for smoother animations
        }, animationSpeed);
    }

    // Start the sequential animation with initial delay
    setTimeout(() => {
        requestAnimationFrame(updateLineSequentially);
    }, animationSpeed);

    // Add event listener for pausing/resuming animation if enabled
    if (pauseOnClick) {
        codeWrapper.addEventListener('click', () => {
            isPaused = !isPaused;
            if (!isPaused) {
                requestAnimationFrame(updateLineSequentially);
            }
        });
    }
}



function appendTextMessage(container, text, isUserMessage, messageId, isHistory = false) {
    // Skip if email composer has taken over this container
    if (container && container.dataset.emailComposer === 'true') {
        return Promise.resolve();
    }

    // Create a container for the message
    const div = document.createElement('div');
    div.classList.add('text-message-container');
    if (messageId) {
        div.id = `message-${messageId}`;
    }
    container.appendChild(div);

    // Create the message element
    const messageElement = document.createElement(isUserMessage ? 'p' : 'div');
    messageElement.classList.add('text-message');
    div.appendChild(messageElement);

    // Handle user messages - parse markdown for proper rendering
    if (isUserMessage) {
        const htmlContent = marked.parse(text);
        const sanitizedHtml = DOMPurify.sanitize(htmlContent);
        messageElement.innerHTML = sanitizedHtml;
        return Promise.resolve();
    }

    // Check if this is a tree structure for AI messages
    if (isTreeStructure(text, null)) {
        console.log('[TreeView] Detected tree structure in text message, rendering as tree view');
        const treeView = createTreeView(text);
        div.innerHTML = '';  // Clear the container
        div.appendChild(treeView);
        return Promise.resolve();
    }

    // Check if this is a recipe for AI messages (using RecipeDetector module)
    if (window.RecipeDetector && window.RecipeDetector.isRecipe(text, null)) {
        console.log('[Recipe] Detected recipe in text message, rendering as card');
        const recipeCard = window.RecipeDetector.createRecipeCard(text);
        div.innerHTML = '';  // Clear the container
        div.appendChild(recipeCard);
        return Promise.resolve();
    }

    // Handle AI or other non-user messages
    const htmlContent = marked.parse(text);
    const sanitizedHtmlContent = DOMPurify.sanitize(htmlContent);

    if (isHistory) {
        // For historical messages, skip typing animation and render immediately
        messageElement.innerHTML = sanitizedHtmlContent;
        renderMathInMessage(messageElement);
        return Promise.resolve();
    } else {
        // For real-time messages, always use ProgressiveTextDisplay for background-aware animation
        const sessionId = window.chatManager?.activeSessionId || 'default';

        return new Promise((resolve) => {
            ProgressiveTextDisplay.start(sessionId, sanitizedHtmlContent, messageElement, {
                isCode: false,
                onComplete: () => {
                    renderMathInMessage(messageElement);
                    resolve();
                }
            });
        });
    }
}


function handleComplexMessage(container, message, messageType, messageId, showButtons, isHistory = false) {
    if (!message || typeof message !== 'string') {
        console.error('Invalid message passed to handleComplexMessage:', message);
        return;
    }

    console.log('handleComplexMessage:', { messageType, hasCodeFence: message.includes('```'), messageLength: message.length });

    // Split the message into sections by code blocks (delimited by ```)
    const sections = message.split("```");
    const hasCodeBlocks = sections.length > 1;

    console.log('handleComplexMessage sections:', sections.length, 'hasCodeBlocks:', hasCodeBlocks);

    const tasks = sections.map((section, index) => {
        const isCodeSection = index % 2 === 1; // Odd indices are inside code fences

        // Handle code blocks - create code window when:
        // 1. This is a code section (inside ```)
        // 2. AND messageType is 'code' (is_code=true from backend)
        if (isCodeSection && messageType === "code") {
            console.log(`Section ${index}: Creating code window for code block`);
            return () => createAndAppendCodeSection(container, section.trim(), isHistory);
        }

        // Handle text sections
        const textContent = section.trim();
        if (!textContent) {
            // Skip empty sections
            return () => Promise.resolve();
        }

        console.log(`Section ${index}: Appending text message`);
        return () => appendTextMessage(container, textContent, false, messageId, isHistory);
    });

    // Process the tasks sequentially
    tasks.reduce((promise, task) => promise.then(task), Promise.resolve())
        .then(() => {
            // Add action buttons if required
            if (showButtons) {
                appendActionButtons(container, messageId);
            }
            // Scroll handled by scrollState.js MutationObserver
            console.log('handleComplexMessage: All sections processed');
        })
        .catch(error => {
            console.error('Error processing complex message:', error);
            // Fallback: Just display the raw message
            const fallbackDiv = document.createElement('div');
            fallbackDiv.className = 'text-message';
            fallbackDiv.textContent = message;
            container.appendChild(fallbackDiv);
        });
}



function resetAndFocusInput() {
    const promptInput = document.getElementById('prompt');
    if (promptInput) {
        promptInput.value = '';
        // Don't refocus on mobile - keeps keyboard dismissed after send
        const isMobile = document.body.classList.contains('device-phone') ||
                        document.body.classList.contains('is-mobile') ||
                        document.body.classList.contains('is-ios') ||
                        document.body.classList.contains('device-tablet');
        if (!isMobile) {
            promptInput.focus();
        }
    }
}

function copyCode(codeText, button) {
    if (!navigator.clipboard) {
        console.error('Clipboard API not available');
        button.innerHTML = 'Clipboard not supported';
        setTimeout(() => button.innerHTML = '<i class="fas fa-copy"></i> ', 2000);
        return;
    }

    navigator.clipboard.writeText(codeText)
        .then(() => {
            button.innerHTML = '🗸 Copied!';
            setTimeout(() => button.innerHTML = '<i class="fas fa-copy"></i> ', 2000);
        })
        .catch(err => {
            console.error('Failed to copy text:', err);
            button.innerHTML = 'Failed to Copy!';
            setTimeout(() => button.innerHTML = '<i class="fas fa-copy"></i> ', 2000);
        });
}

function debounce(func, delay) {
    let timeout;
    return function(...args) {
        clearTimeout(timeout);
        timeout = setTimeout(() => func.apply(this, args), delay);
    };
}

const debouncedInputHandler = debounce((input) => {
    // Process input here
}, 300);


function switchHighlighter(library) {
    if (library === 'prism') {
        import('https://cdn.jsdelivr.net/npm/prismjs/prism.js');
        import('https://cdn.jsdelivr.net/npm/prismjs/components/prism-javascript.min.js');
    } else if (library === 'highlight') {
        import('https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.4.0/highlight.min.js');
    }
}
