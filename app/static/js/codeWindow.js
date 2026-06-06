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

// Apply an auto-generated session title: updates chatManager state,
// locates the sidebar tab, animates the rename via typewriteTitle.
// Safe across all three response-complete paths (streaming, non-streaming
// text, image-only non-streaming). Works regardless of whether the tab
// is the active one.
function applySessionTitle(sessionId, newTitle) {
    const mgr = window.chatManager;
    if (!mgr || !sessionId || !newTitle) return;
    const name = String(newTitle).trim();
    if (!name || name.toLowerCase() === 'new chat') return;
    const sess = mgr.chatHistory?.[sessionId];
    if (!sess || sess.session_name === name) return;
    sess.session_name = name;
    sess.autoTitleEligible = false;
    const tab = document.querySelector(
        `.chat-tab[data-session-id="${sessionId}"] .tab-name`
    );
    if (tab) {
        typewriteTitle(tab, name);
    } else if (typeof mgr.renderChatTabs === 'function') {
        mgr.renderChatTabs();
    }
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

// Per-message search stream data (replaces window._stream* globals)
const _searchStreamData = new Map();

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
        pendingNotifications.delete(sessionId);
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

        // Start thinking sounds (subtle typing clicks)
        if (window.ThinkingSounds) window.ThinkingSounds.start();

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
            return;
        }

        reasoningText += text;
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
        // Stop thinking sounds
        if (window.ThinkingSounds) window.ThinkingSounds.stop();

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

function formatGuestComposerPlaceholder(remaining) {
    if (window.matchMedia?.('(max-width: 480px)').matches) {
        return 'Try XelaSphere';
    }
    return `Try XelaSphere (${remaining} message${remaining !== 1 ? 's' : ''} left)`;
}

function updateGuestMessageCount(count) {
    document.body.dataset.guestMessageCount = count;
    // Update placeholder with remaining messages
    const promptInput = document.getElementById('prompt');
    if (promptInput && isGuestMode()) {
        const limit = parseInt(document.body.dataset.guestMessageLimit, 10) || 5;
        const remaining = Math.max(0, limit - count);
        promptInput.placeholder = formatGuestComposerPlaceholder(remaining);
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
        updateGuestMessageCount(getGuestMessageCount());
        window.addEventListener('resize', () => updateGuestMessageCount(getGuestMessageCount()));

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
    return !!window._activeStreamRenderer;
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
                { left: '\\(', right: '\\)', display: false }
                // Single $ removed — too aggressive, catches currency like $7
            ],
            throwOnError: false
        });
    } catch (error) {
        console.error('Math rendering error:', error);
    }
}
window.renderMathInMessage = renderMathInMessage;

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

/**
 * Render live attachments using the FilePreview system
 */
function renderLiveAttachments(container, attachmentsMeta) {
    if (!container || !attachmentsMeta || !attachmentsMeta.length) return;

    const attachmentsWrapper = document.createElement('div');
    attachmentsWrapper.className = 'message-attachments';

    // ChatGPT-style grid for all-image attachment sets. Delegates to the
    // shared helper in script.js so the replay path (`renderAttachments` in
    // startNewChatModule.js) stays in lockstep.
    if (typeof applyAttachmentLayout === 'function') {
        applyAttachmentLayout(attachmentsWrapper, attachmentsMeta);
    }

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
                    const url = FilePreview.resolveUrl(attachment) || att.url;
                    if (isImg && url && typeof window.__xelasphereOpenLightbox === 'function') {
                        window.__xelasphereOpenLightbox({
                            src: url,
                            name: att.name,
                            type: att.type || 'image',
                            isImage: true
                        });
                    } else if (isImg && typeof openAttachmentLightbox === 'function') {
                        openAttachmentLightbox({
                            src: url,
                            title: att.name,
                            downloadName: att.name,
                            openUrl: url,
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

// Threshold for collapsing long user messages into a document card
const LONG_MESSAGE_CHAR_THRESHOLD = 500;
const LONG_MESSAGE_LINE_THRESHOLD = 8;

function isLongMessage(text) {
    if (!text) return false;
    if (text.length > LONG_MESSAGE_CHAR_THRESHOLD) return true;
    const lines = text.split('\n');
    return lines.length > LONG_MESSAGE_LINE_THRESHOLD;
}

function createDocumentCard(text) {
    const lines = text.split('\n').filter(l => l.trim());
    const lineCount = lines.length;
    const charCount = text.length;

    // Get a preview label — first non-empty line, truncated
    const firstLine = lines[0] || 'Pasted text';
    const label = firstLine.length > 50 ? firstLine.slice(0, 50) + '…' : firstLine;

    // Build the card
    const card = document.createElement('div');
    card.className = 'user-doc-card';

    // Miniature text preview (visual texture, not readable)
    const preview = document.createElement('div');
    preview.className = 'user-doc-card__preview';
    // Show up to 20 lines as miniature text
    const previewLines = lines.slice(0, 20);
    previewLines.forEach(line => {
        const lineEl = document.createElement('div');
        lineEl.className = 'user-doc-card__line';
        lineEl.textContent = line;
        preview.appendChild(lineEl);
    });
    if (lines.length > 20) {
        const fade = document.createElement('div');
        fade.className = 'user-doc-card__fade';
        preview.appendChild(fade);
    }
    card.appendChild(preview);

    // Info bar
    const info = document.createElement('div');
    info.className = 'user-doc-card__info';
    info.innerHTML = `
        <span class="user-doc-card__label">${escapeHtml(label)}</span>
        <span class="user-doc-card__meta">${lineCount} lines · ${charCount.toLocaleString()} chars</span>
    `;
    card.appendChild(info);

    // Expanded state (full text, hidden by default)
    const expanded = document.createElement('div');
    expanded.className = 'user-doc-card__expanded';
    const htmlContent = marked.parse(text);
    expanded.innerHTML = DOMPurify.sanitize(htmlContent);
    card.appendChild(expanded);

    // Click to toggle
    card.addEventListener('click', (e) => {
        // Don't toggle if clicking a link inside expanded
        if (e.target.closest('a')) return;
        card.classList.toggle('is-expanded');
    });

    return card;
}

function escapeHtml(str) {
    const d = document.createElement('div');
    d.textContent = str;
    return d.innerHTML;
}

function appendUserMessage(messageDiv, message, messageId) {
    if (!messageDiv || !message) {
        throw new Error('Message div and content are required');
    }

    const container = messageDiv.querySelector('.message-content') || messageDiv;

    // Long messages → document card (Claude-style)
    if (isLongMessage(message)) {
        const card = createDocumentCard(message);
        const wrapper = document.createElement('div');
        wrapper.classList.add('text-message-container');
        wrapper.appendChild(card);
        container.appendChild(wrapper);
    } else {
        renderUserMessage(container, message, messageId);
    }

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
function displayMessage(message, sender, messageType, originalPrompt, showButtons = true, messageId = null, attachmentsMeta = null, searchResults = null, modelMeta = null, style = null) {
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
        // Same guard as startNewChatModule.renderMessage — if a live
        // StreamRenderer is attached to this node's subtree, destroy it
        // before wiping innerHTML so its messageEl doesn't keep mutating
        // an orphaned tree after fast regenerate / edit.
        const liveSr = window._activeStreamRenderer;
        if (liveSr && !liveSr.destroyed && messageDiv.contains(liveSr.container)) {
            liveSr.destroy();
            window._activeStreamRenderer = null;
        }
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
                renderAiTextImmediate(messageContent, safeMessage, messageId, showButtons, style);
        }
    }

    // Store model data on DOM for quality signal collection (always, not just auto)
    if (normalizedSender === 'ai' && modelMeta) {
        try {
            if (modelMeta.model_id) messageDiv.dataset.modelId = modelMeta.model_id;
            if (modelMeta.model_label) messageDiv.dataset.modelLabel = modelMeta.model_label;
            if (modelMeta.model_provider) messageDiv.dataset.modelProvider = modelMeta.model_provider;
            if (modelMeta.model_provider_label) messageDiv.dataset.modelProviderLabel = modelMeta.model_provider_label;
            if (modelMeta.task_type) messageDiv.dataset.taskType = modelMeta.task_type;
            if (modelMeta.auto_selected) messageDiv.dataset.autoSelected = 'true';
        } catch (e) {
            console.warn('Failed to attach model meta to message element:', e);
        }
    }

    // Ghost Model: store modelMeta on DOM for attribution after typing completes
    if (normalizedSender === 'ai' && modelMeta) {
        messageDiv._modelMeta = modelMeta;
    }

    // Scroll is handled by scrollState.js (MutationObserver + scrollUserMessageToTop)
    // Don't call scrollToBottom here - it uses smooth scroll that overrides instant scroll
    resetInputIfNecessary(sender);

    // Skip recording zero-width space placeholders (search banner) to history
    const _isPlaceholder = typeof message === 'string' && (message === '\u200B' || message.trim() === '');
    if (!_isPlaceholder && typeof window !== 'undefined' && window.chatManager?.recordLiveMessage) {
        const payloadContent = normalizedSender === 'user' ? message : (typeof safeMessage !== 'undefined' ? safeMessage : message);
        const payload = {
            sender: normalizedSender,
            content: payloadContent,
            messageType: normalizedType,
            messageId,
            prompt: originalPrompt
        };
        if (normalizedSender === 'ai' && style) {
            payload.style = style;
        }
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

function replacePlaceholderWithImage(placeholderId, imageUrlOrList, promptText) {
    // Accept either a single URL (legacy single-image generation) or an
    // array of URLs (when the backend returns multiple images in one reply).
    const imageUrls = Array.isArray(imageUrlOrList)
        ? imageUrlOrList.filter(Boolean)
        : (imageUrlOrList ? [imageUrlOrList] : []);
    if (!imageUrls.length) return;

    const createdAt = placeholderTimestamps.get(placeholderId) || Date.now();
    const elapsed = Date.now() - createdAt;
    // Long enough for the fade to register, short enough to not feel like
    // an artificial freeze. Fast providers (Pollinations sub-second) no
    // longer look broken-but-waiting; slow ones still get their animation.
    const minDuration = 400;
    const delay = Math.max(0, minDuration - elapsed);

    setTimeout(() => {
        const messageDiv = document.getElementById(placeholderId);
        if (!messageDiv) return;
        const messageContent = messageDiv.querySelector('.message-content');
        if (!messageContent) return;

        // Image responses render image-only. Strip any caption text the
        // backend streamed into this bubble — before, during, or after the
        // image arrived. We use an allowlist of known-safe children rather
        // than a blocklist of text classes, because the streamed prose can
        // land under several selectors (StreamRenderer's .stream-block,
        // renderAiTextImmediate's .text-message-container, direct .text-
        // message paragraphs, etc.). Anything not on the allowlist gets
        // removed. Also re-runs twice to catch async renders that arrive
        // after the initial swap (e.g. late chunks post-done).
        const CAPTION_ALLOWED = new Set([
            'image-message', 'image-layer', 'image-preview-card',
            'message-attachments', 'thinking-indicator', 'reasoning-display',
            'search-banner', 'search-results-wrapper', 'sources-btn',
            'message-actions', 'message-footer',
        ]);
        const stripCaptionText = () => {
            Array.from(messageContent.children).forEach((child) => {
                const keep = Array.from(child.classList || []).some(
                    (c) => CAPTION_ALLOWED.has(c)
                );
                if (!keep) child.remove();
            });
        };
        stripCaptionText();
        setTimeout(stripCaptionText, 150);
        setTimeout(stripCaptionText, 600);

        const alt = typeof formatImageAlt === 'function'
            ? formatImageAlt(promptText, { generated: true })
            : (promptText || 'AI-generated image');

        // Single-image path: keep reusing the existing shimmer layer the
        // placeholder stage already built. Multi-image path: replace the
        // container with a grid of fresh layers (one per URL).
        if (imageUrls.length === 1) {
            let layer = messageContent.querySelector('.image-layer');
            if (!layer) {
                layer = document.createElement('div');
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
                img.className = 'chat-image chat-image--reveal';
                img.style.setProperty('aspect-ratio', '2 / 3');
                layer.appendChild(placeholder);
                layer.appendChild(img);
                messageContent.innerHTML = '';
                messageContent.appendChild(layer);
            }
            finalizeSingleImage(layer, imageUrls[0], alt, promptText);
        } else {
            // Multi-image: drop the placeholder, render all images into a
            // single .image-message grid container. buildImageLayer produces
            // the same DOM shape the single-image path lands on after load.
            messageContent.innerHTML = '';
            const container = document.createElement('div');
            container.className = 'image-message image-message--grid';
            container.dataset.imageCount = String(Math.min(imageUrls.length, 4));
            imageUrls.forEach((u) => {
                if (typeof buildImageLayer === 'function') {
                    container.appendChild(buildImageLayer(u, alt));
                }
            });
            messageContent.appendChild(container);
        }

        placeholderTimestamps.delete(placeholderId);
        // Only scroll if user is pinned to bottom
        if (window.scrollState?.isPinnedToBottom) {
            scrollToBottom(false);
        }
    }, delay);
}

// Finalize the single-image placeholder → loaded image transition.
// Extracted so replacePlaceholderWithImage can branch cleanly between the
// single-image (reuse existing layer) and multi-image (rebuild grid) paths.
function finalizeSingleImage(layer, imageUrl, alt, promptText) {
    const img = layer.querySelector('.chat-image');
    const placeholder = layer.querySelector('.image-placeholder');
    if (!img) return;

    img.src = imageUrl;
    img.alt = alt;
    img.style.cursor = 'pointer';
    img.onload = () => {
        layer.classList.add('revealed', 'loaded');
        img.classList.add('loaded');
    };
    if (typeof wireBrokenImageFallback === 'function') {
        wireBrokenImageFallback(img, promptText || 'Generated image');
    } else {
        img.onerror = () => { layer.textContent = 'Image failed to load.'; };
    }
    if (img.complete && img.naturalHeight !== 0) {
        layer.classList.add('revealed', 'loaded');
        img.classList.add('loaded');
    }
    img.addEventListener('click', () => {
        if (typeof openAttachmentLightbox === 'function') {
            openAttachmentLightbox({ src: imageUrl, title: alt, isImage: true });
        }
    });
    if (placeholder) placeholder.style.opacity = '1';

    // Hover-reveal Edit/Share toolbar — refresh path gets this via buildImageLayer()
    // in script.js, live single-image was reusing the placeholder's pre-built layer
    // and never attaching it. Skip if already present (defensive against re-render).
    if (!layer.querySelector('.image-layer__actions') && typeof buildImageHoverToolbar === 'function') {
        const toolbar = buildImageHoverToolbar(imageUrl, alt);
        if (toolbar) layer.appendChild(toolbar);
    }
}

function isImagePrompt(prompt = '') {
    const p = prompt.trim().toLowerCase();
    return p.startsWith('/image ') || p.startsWith('/image\n') || p === '/image'
        || p.startsWith('/img ') || p.startsWith('/img\n') || p === '/img';
}

// Allocate the AI message bubble at prompt-send so the cursor has somewhere
// to live during TTFB. Returns the bubble's id; sendMessage stashes it in
// _preStreamBubbleId, and the response handlers (chat / image / search /
// error) prefer that id over a freshly-generated one so the same bubble is
// reused. StreamRenderer detects an existing text-message-container in the
// constructor and adopts it (streamRenderer.js:153) — the cursor stays in
// the same DOM node from t=0 to stream-end.
function createPreStreamBubble(prompt) {
    const chatDisplay = document.getElementById('chat-display');
    if (!chatDisplay) return null;

    if (window.setHasMessages) window.setHasMessages(true);
    else document.body.classList.add('has-messages');

    const id = generateMessageId();
    const bubbleEl = createMessageDiv(id, 'ai');
    bubbleEl.dataset.originalPrompt = prompt || '';
    bubbleEl.dataset.sender = 'ai';
    bubbleEl.dataset.timestamp = new Date().toISOString();
    bubbleEl.dataset.preStream = '1';
    chatDisplay.appendChild(bubbleEl);
    createHeader('ai', bubbleEl);

    const mc = document.createElement('div');
    mc.className = 'message-content';
    bubbleEl.appendChild(mc);

    // Pre-place the structure StreamRenderer adopts. .stream-cursor uses the
    // same class and same animation as the in-stream cursor — once the first
    // BLOCK_START fires, transferCursor() moves THIS node into the new block,
    // animation state preserved (no fade, no swap).
    const wrapper = document.createElement('div');
    wrapper.className = 'text-message-container';
    const inner = document.createElement('div');
    inner.className = 'text-message';
    const cursor = document.createElement('span');
    cursor.className = 'stream-cursor';
    inner.appendChild(cursor);
    wrapper.appendChild(inner);
    mc.appendChild(wrapper);

    return id;
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
    let requestSessionId = window.chatManager?.activeSessionId || null;

    // Helper to check if this session is still the active one
    // Defined here (before try) so it's accessible in finally block
    const isSessionActive = () => {
        // If no session existed when request started (new chat scenario),
        // treat as active - user hasn't explicitly switched to another session
        if (!requestSessionId) return true;

        const currentActive = window.chatManager?.activeSessionId;
        return currentActive === requestSessionId;
    };

    // Also check global typing stopped flag (for abort scenarios)
    if (isTypingStopped) {
        resetState();
        return;
    }

    // Declare bgRequest before try so it's accessible in catch/finally
    let bgRequest = null;
    // Hoisted so the outer catch (line ~4435) can clean up on mid-stream errors.
    let _streamRenderer = null;
    let _streamMsgId = null;
    // Bubble allocated at prompt-send so the cursor has somewhere to live during
    // TTFB. Response handlers below (chat / image / search / error) prefer this
    // ID over a freshly-generated one, so the bubble is reused — cursor stays in
    // the same DOM node from prompt-send through stream-end. No element swap.
    let _preStreamBubbleId = null;

    try {
        // Note: We no longer set global isRequestInProgress - we use per-session tracking
        isTypingStopped = false;

        // Create a new abort controller for this specific request
        const requestAbortController = new AbortController();
        abortController = requestAbortController;

        // Get DOM elements and values
        const promptInput = document.getElementById('prompt');
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

        // ─── Writing Style (from + menu → Use style) ───
        // Delegates to styleManager.js, which also handles custom styles +
        // localStorage persistence. Display bubble shows the clean prompt.
        let _styleDisplayPrompt = null;
        const activeStyle = window.chatManager?._activeStyle;
        const styleInstr = (typeof window.__getStyleInstruction === 'function')
            ? window.__getStyleInstruction(activeStyle)
            : null;
        if (prompt && styleInstr) {
            _styleDisplayPrompt = prompt;
            prompt = `${styleInstr}\n\n${prompt}`;
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
        // Reuse the pre-stream bubble for the image placeholder when present,
        // so the bubble allocated at prompt-send becomes the placeholder slot
        // (no orphan empty bubble left behind).
        const placeholderId = needsImagePlaceholder ? (_preStreamBubbleId || generateMessageId()) : null;

        // Reject bare /image (or /img) with no description — the backend
        // would otherwise generate something meaningless. Show a toast and
        // leave focus in the composer.
        if (needsImagePlaceholder) {
            const body = prompt.trim().replace(/^\/(image|img)\s*/i, '').trim();
            if (!body) {
                if (window.Toast && typeof window.Toast.warning === 'function') {
                    window.Toast.warning('What should I draw? Add a description after /image.', 4000);
                }
                const promptEl = document.getElementById('prompt');
                if (promptEl) promptEl.focus();
                throw new Error('Empty image prompt');
            }
        }

        // Validate prompt or attachments
        if (!prompt && attachments.length === 0) {
            throw new Error('Empty prompt');
        }

        // Mobile: Dismiss keyboard immediately after send
        if (typeof window.dismissMobileKeyboard === 'function') {
            window.dismissMobileKeyboard();
        }

        if (typeof window !== 'undefined' && window.chatManager?.ensureActiveSession) {
            const ensuredSessionId = await window.chatManager.ensureActiveSession();
            if (ensuredSessionId) {
                requestSessionId = ensuredSessionId;
            } else if (window.chatManager?.activeSessionId) {
                requestSessionId = window.chatManager.activeSessionId;
            }
        }

        // Re-check after ensureActiveSession(). The first message of a new chat
        // used to snapshot a null session id before creation, which disabled
        // per-session dedupe and allowed double-send races.
        if (requestSessionId && BackgroundRequestManager.hasActiveRequest(requestSessionId)) {
            console.log('Session already has active request:', requestSessionId);
            return;
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
                // Use custom displayPrompt if provided, otherwise the style-clean
                // prompt (if a style prefix was applied), otherwise the raw prompt.
                const messageToDisplay = displayPrompt !== null
                    ? displayPrompt
                    : (_styleDisplayPrompt !== null ? _styleDisplayPrompt : prompt);
                primaryUserMessage = displayMessage(
                    messageToDisplay,
                    'user',
                    'text',
                    messageToDisplay,
                    true,
                    null,
                    liveUserAttachmentMeta
                );
                // Attach the active style as a tiny badge INSIDE the user message
                // bubble — the chip "rides" with the prompt instead of floating below.
                if (primaryUserMessage && activeStyle && activeStyle !== 'Normal' && _styleDisplayPrompt !== null) {
                    try {
                        const bubble = primaryUserMessage.querySelector('.message-content') || primaryUserMessage;
                        const badge = document.createElement('span');
                        badge.className = 'message-style-badge';
                        badge.setAttribute('data-style', activeStyle);
                        badge.innerHTML = `
                            <span class="message-style-badge__dot" aria-hidden="true"></span>
                            <span class="message-style-badge__label">${activeStyle.replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}</span>
                        `;
                        // Insert at the top of the bubble so it reads as a header tag
                        bubble.insertBefore(badge, bubble.firstChild);
                        primaryUserMessage.classList.add('has-style-badge');
                    } catch (e) { /* non-fatal */ }
                }
            }
            // Attachments are now rendered inline via renderLiveAttachments in displayMessage
            // No need to create separate messages for each attachment
            if (needsImagePlaceholder) {
                displayMessage('', 'ai', 'image-placeholder', prompt, true, placeholderId);
                placeholderTimestamps.set(placeholderId, Date.now());
            }
        }

        // ChatGPT/Claude-style: anchor the just-sent prompt near the top and
        // reserve space below (via scrollUserMessageToTop's 40vh padding) so the
        // answer streams in beneath it. Plain pin-to-bottom used to let the
        // prompt scroll up under the fixed mobile header and clip out of view —
        // especially on the first message of a fresh chat (nothing above it).
        // contentArrived() hands off to follow-the-response once tokens arrive.
        if (primaryUserMessage) {
            if (typeof window.scrollUserMessageToTop === 'function') {
                window.scrollUserMessageToTop(primaryUserMessage);
            } else if (typeof window.scrollToBottom === 'function') {
                window.scrollToBottom(true, true);
            }
        }

        // Show indicator AFTER user message (so it appears below, where response will be)
        const currentModelEarly = (window.ghostModel && window.ghostModel.getSelectedModel()) || 'auto';
        const isDeepSeekEarly = currentModelEarly === 'deepseek-v4-pro' || currentModelEarly === 'deepseek-v4-flash' || currentModelEarly === 'deepseek-reasoner';

        if (isDeepSeekEarly) {
            // DeepSeek R1: chain-of-thought reasoning. Expandable indicator at
            // chat bottom; gets hidden when first content chunk arrives.
            ThinkingIndicator.show(true);
        } else {
            // Non-DeepSeek: pre-allocate the AI bubble with a cursor inside so
            // the cursor is visible during TTFB and stays in the same DOM node
            // when content streams in. Response handlers below fall back to
            // _preStreamBubbleId so the bubble is reused, not orphaned.
            _preStreamBubbleId = createPreStreamBubble(prompt);
        }

        // Bump session to top of sidebar (most recently active)
        if (requestSessionId && window.chatManager?.bumpSessionToTop) {
            window.chatManager.bumpSessionToTop(requestSessionId);
        }

        // Prepare request data
        const formData = new FormData();
        formData.append('prompt', prompt);
        if (_styleDisplayPrompt !== null) {
            formData.append('display_prompt', _styleDisplayPrompt);
        }
        // Browser timezone — reminders M0 hook reads this to interpret
        // relative times in chat ("remind me at 9am" → 9am LOCAL, not UTC).
        try {
            const _tz = Intl.DateTimeFormat().resolvedOptions().timeZone;
            if (_tz) formData.append('timezone', _tz);
        } catch (_e) { /* ignore — server falls back to UTC */ }

        // Request mode: single source of truth for UI intent overrides.
        //   'auto'     → model picks ACTION: itself (default)
        //   'search'   → force web_search
        //   'research' → force deep research (future)
        //   'chat'     → force chat (skip search even if model wants it)
        //   'artifact' → force artifact creation (future)
        // The "+" menu "Web search" item stores its on/off state on
        // webSearchBtn.dataset.webOn. A separate #enable-search checkbox
        // was historical; today this dataset attr is the real source.
        var _webBtn = document.getElementById('action-web-search');
        var _webOn = _webBtn && _webBtn.dataset.webOn === 'true';
        var _mode = _webOn ? 'search' : 'auto';
        formData.append('mode', _mode);
        console.log('[MODE] sending mode=', _mode, 'webOn=', _webOn);
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

        const activeSid = window.chatManager?.activeSessionId;
        if (activeSid) {
            formData.append('session_id', activeSid);
        }

        // Check if using a streamable model (DeepSeek, Moonshot/Kimi, or Auto)
        // Auto mode routes to a streaming-capable model server-side.
        // Skip streaming when images are attached — vision needs the regular /chat endpoint.
        const hasImages = attachments.some(a => a.isImage || a.type?.startsWith('image/'));
        const isDeepSeek = model.includes('deepseek');
        const isAuto = !model || model === 'auto';
        const isOpenAI = model.includes('gpt-');
        const isGroq = model.includes('llama') || model.includes('qwen') || model.includes('gpt-oss') || model.includes('gemma');
        const isStreamable = (isDeepSeek || model.includes('kimi') || isOpenAI || isGroq || isAuto) && !hasImages;
        const isDeepSeekR1 = model === 'deepseek-v4-pro' || model === 'deepseek-v4-flash' || model === 'deepseek-reasoner';

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
            // Declared OUTSIDE the try block so the catch handler at the end of
            // this block can read it — `let` is block-scoped, so a declaration
            // inside the try{} is invisible inside the corresponding catch{}.
            // When a stream errored, the catch crashed on "streamHasVisibleOutput
            // is not defined" instead of doing its proper partial-response recovery.
            let streamHasVisibleOutput = false;
            try {
                // SL-1: ensure streamRenderer.js ES module is loaded before attempting to
                // use window.StreamRenderer. Short timeout so slow loads fall back gracefully.
                if (window._streamRendererReadyPromise && typeof window.StreamRenderer !== 'function') {
                    await Promise.race([
                        window._streamRendererReadyPromise,
                        new Promise(r => setTimeout(r, 500))
                    ]);
                }
                const streamResponse = await fetch('/chat/stream', {
                    method: 'POST',
                    body: formData,
                    headers: {
                        'X-CSRFToken': getCSRFToken()
                    },
                    signal: bgRequest ? bgRequest.abortController.signal : requestAbortController.signal
                });

                if (!streamResponse.ok) {
                    if (streamResponse.status === 403) {
                        const contentType = streamResponse.headers.get('content-type') || '';
                        let errorPayload = {};
                        if (contentType.includes('application/json')) {
                            try {
                                errorPayload = await streamResponse.json();
                            } catch (e) {
                                errorPayload = {};
                            }
                        }
                        if (errorPayload.error === 'guest_limit_reached') {
                            showGuestSignupModal();
                            resetState();
                            return;
                        }
                    }
                    throw new Error(`Stream request failed: ${streamResponse.status}`);
                }

                // Check if the response is a JSON image result (not SSE stream)
                const contentType = streamResponse.headers.get('content-type') || '';
                if (contentType.includes('application/json')) {
                    const data = await streamResponse.json();
                    if (data.image_url) {
                        console.log('[Stream] Image response intercepted:', data.image_url);
                        // Replace placeholder with image or display image
                        const aiMsgId = placeholderId || generateMessageId();
                        const altPrompt = _styleDisplayPrompt !== null ? _styleDisplayPrompt : prompt;
                        if (placeholderId && document.getElementById(placeholderId)) {
                            replacePlaceholderWithImage(placeholderId, data.image_url, altPrompt);
                        } else {
                            displayMessage('', 'ai', 'image-placeholder', altPrompt, true, aiMsgId);
                            placeholderTimestamps.set(aiMsgId, Date.now());
                            replacePlaceholderWithImage(aiMsgId, data.image_url, altPrompt);
                        }
                        // Save to chat history
                        if (manager?.addOrUpdateAiMessage) {
                            manager.addOrUpdateAiMessage({
                                response_id: data.message_id || aiMsgId,
                                content: '',
                                prompt: prompt,
                                is_code: false,
                                render_mode: 'inline',
                                attachments: data.attachments || [{
                                    isImage: true,
                                    url: data.image_url,
                                    name: 'AI generated image'
                                }]
                            });
                        }
                        // Update session title with typewriter animation.
                        // This path was previously calling an undefined
                        // updateSessionName() — silently losing the rename.
                        if (data.session_name && manager?.activeSessionId) {
                            applySessionTitle(manager.activeSessionId, data.session_name);
                        }
                        if (bgRequest) {
                            BackgroundRequestManager.completeRequest(requestSessionId);
                        }
                        return;
                    }
                }

                const reader = streamResponse.body.getReader();
                const decoder = new TextDecoder();
                let buffer = '';
                let streamArtifact = null;
                let streamRenderMode = 'inline';
                let streamChatSummary = '';
                let streamIsEmail = false;
                let streamEmailData = null;
                let streamReminderData = null;
                let streamCryptoChart = null;
                let streamMemoryUpdated = false;
                let streamDreamNotification = null;
                let streamArtifactPatches = null;
                let streamModelMeta = null;
                let streamMeta = {};   // captured from the first 'stream-meta' SSE chunk
                let patchStreamActive = false;
                let patchStreamBuffer = '';
                let patchParsedPatches = [];
                let artifactStreamActive = false;
                let artifactMetaPending = false;
                let artifactMetaIndex = -1;
                let searchMsgId = null; // Track message element for search banner
                let searchBannerDismissed = false; // Track if search banner was dismissed

                // Per-stream state for image generation. Previously stored on
                // `window.*` which meant two concurrent `/image` calls would
                // stomp each other's placeholder IDs. Localized here so each
                // sendMessage invocation gets an isolated state bag.
                let pendingImagePlaceholderId = null;
                let streamDoneImageUrls = null;   // array of URLs (supports multi-image)
                let streamDoneAttachments = null;
                _streamRenderer = null; window._activeStreamRenderer = null; // Phase 2: live SSE-to-DOM renderer (hoisted above try)
                _streamMsgId = null;    // Message ID for StreamRenderer container (hoisted above try)
                let artifactFencePosition = -1;
                let isFirstContentChunk = true; // Track first chunk for instant scroll
                // Use array to avoid O(n²) string concatenation per chunk.
                // streamedContent is only built (joined) when needed.
                const _contentChunks = [];
                let _contentJoinedAt = 0;  // index up to which streamedContent is current

                // Client-side safety net: buffer until we can decide whether
                // the stream opens with an `ACTION: xxx` line (which must be
                // hidden from the UI). If so, strip it. Otherwise release the
                // buffer verbatim. Disables itself once real content starts.
                let _actionStripDone = false;
                let _actionStripBuffer = '';
                const _actionStripRe = /^\s*[-*]*\s*ACTION:\s*\w+[^\n]*\n(?:[ \t]*[a-zA-Z_][\w-]*\s*:[^\n]*\n)*\n*/i;
                function _stripActionFromChunk(chunkText) {
                    if (_actionStripDone) return chunkText;
                    _actionStripBuffer += chunkText;
                    // Decide as early as possible. If the buffered prefix
                    // clearly cannot match (>= 7 chars and doesn't start with
                    // "action:"), release it verbatim.
                    const lead = _actionStripBuffer.replace(/^[\s\-*]+/, '');
                    if (lead.length >= 7 && lead.slice(0, 7).toLowerCase() !== 'action:') {
                        const out = _actionStripBuffer;
                        _actionStripBuffer = '';
                        _actionStripDone = true;
                        return out;
                    }
                    // If it's potentially an ACTION header, wait for the blank
                    // line that ends the header (or \n after single-line form).
                    const m = _actionStripBuffer.match(_actionStripRe);
                    if (m) {
                        const stripped = _actionStripBuffer.slice(m[0].length);
                        _actionStripBuffer = '';
                        _actionStripDone = true;
                        return stripped;
                    }
                    // Not enough data yet — swallow this chunk and wait.
                    return '';
                }

                const processLine = (line) => {
                    if (!line.startsWith('data: ')) return;
                    const jsonStr = line.slice(6);

                    try {
                        const chunk = JSON.parse(jsonStr);

                        if (chunk.type === 'reasoning') {
                            streamHasVisibleOutput = true;
                            streamedReasoning += chunk.text || '';
                            // Buffer in BackgroundRequestManager
                            if (bgRequest) {
                                BackgroundRequestManager.appendReasoning(requestSessionId, chunk.text || '');
                            }
                            // ALWAYS try to update ThinkingIndicator
                            // If user is viewing this session, they'll see live updates
                            // If not, ThinkingIndicator is hidden anyway, so this is harmless
                            ThinkingIndicator.appendReasoning(chunk.text || '');
                        } else if (chunk.type === 'stream-meta') {
                            // First chunk from server when RESPONSE_STYLE_ORGANISM_ENABLED is on.
                            // Captures the chosen visual style. Passed into StreamRenderer when
                            // the first content chunk creates one.
                            streamMeta = chunk;  // {type, style, via}
                            console.log('[StreamMeta] received:', chunk.style, '(via', chunk.via + ')');
                            return;
                        } else if (chunk.type === 'content') {
                            if (chunk.text) {
                                streamHasVisibleOutput = true;
                            }
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
                                if (isFirstContentChunk) {
                                    isFirstContentChunk = false;
                                    window.scrollState?.contentArrived?.();
                                    ThinkingIndicator.hide();

                                    // Phase 2: Create StreamRenderer on first content chunk
                                    // Skip when an image placeholder is already pending:
                                    // the narration that follows an ACTION: generate_image
                                    // header would otherwise render into its own bubble
                                    // alongside the image, diverging from the refresh path.
                                    if (isSessionActive() && typeof StreamRenderer !== 'undefined' && !pendingImagePlaceholderId) {
                                        // Prefer reusing the pre-stream bubble allocated at prompt-send
                                        // (so the cursor stays in the same DOM node); fall back to search /
                                        // image placeholder ids; last resort generate fresh.
                                        _streamMsgId = searchMsgId || placeholderId || _preStreamBubbleId || generateMessageId();
                                        var targetEl = document.getElementById(_streamMsgId);
                                        if (!targetEl) {
                                            // Create message element directly — don't use displayMessage
                                            if (window.setHasMessages) window.setHasMessages(true);
                                            else document.body.classList.add('has-messages');
                                            var chatDisplay = document.getElementById('chat-display');
                                            if (chatDisplay) {
                                                targetEl = createMessageDiv(_streamMsgId, 'ai');
                                                chatDisplay.appendChild(targetEl);
                                                createHeader('ai', targetEl);
                                                var mc = document.createElement('div');
                                                mc.className = 'message-content';
                                                targetEl.appendChild(mc);
                                                targetEl.dataset.originalPrompt = prompt;
                                                targetEl.dataset.sender = 'ai';
                                                targetEl.dataset.timestamp = new Date().toISOString();
                                            }
                                        } else if (targetEl.dataset.preStream === '1') {
                                            // Pre-stream bubble: clear the marker now that real
                                            // streaming is taking over.
                                            delete targetEl.dataset.preStream;
                                        }
                                        var targetContent = targetEl?.querySelector('.message-content');
                                        if (targetContent) {
                                            // Kill any existing legacy streaming cursor (the .streaming-cursor
                                            // class from the now-deleted simple-pulsing-dot system; defensive).
                                            var oldCursor = targetContent.querySelector('.streaming-cursor');
                                            if (oldCursor) oldCursor.remove();
                                            // Remove everything EXCEPT (a) the search banner — re-inserting
                                            // would replay its 0.3s bannerFadeIn and look glitchy, and (b) the
                                            // pre-stream .text-message-container so its cursor stays in place;
                                            // StreamRenderer's constructor adopts that container instead of
                                            // creating a fresh one, so the cursor never swaps elements.
                                            var _preservedBanner = targetContent.querySelector('.search-banner');
                                            var _preservedTextMsg = targetContent.querySelector(':scope > .text-message-container');
                                            console.log('[SEARCH-BANNER] first content chunk. bannerFound=', !!_preservedBanner, 'msgId=', _streamMsgId);
                                            Array.from(targetContent.children).forEach(function (child) {
                                                if (child !== _preservedBanner && child !== _preservedTextMsg) child.remove();
                                            });
                                            _streamRenderer = new StreamRenderer(targetContent, { streamMeta });
                                            window._activeStreamRenderer = _streamRenderer;
                                        } else {
                                            console.warn('[StreamRenderer] No target element — falling back');
                                            _streamRenderer = null; window._activeStreamRenderer = null;
                                        }
                                    }
                                }
                                // --- Artifact fence detection BEFORE rendering ---
                                // Must run before StreamRenderer.append() so artifact
                                // content never enters the message bubble. Previous
                                // order (append first, detect after) let batched RAF
                                // renders paint artifact code in the bubble before the
                                // fence was caught.
                                let _fenceJustTriggered = false;
                                if (artifactMetaPending) _fenceJustTriggered = true;
                                if (chunkText.includes('`') || artifactMetaPending) {
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
                                        const _reseedChatWithProse = (fenceIndex) => {
                                            let proseOnly = streamedContent.slice(0, fenceIndex).trim();
                                            // Strip ACTION header to match what the backend
                                            // sends as chat_summary (parity with refresh path).
                                            proseOnly = proseOnly
                                                .replace(/^\s*[-*]*\s*ACTION:\s*\w+[^\n]*\n(?:[ \t]*[a-zA-Z_][\w-]*\s*:[^\n]*\n)*\n*/i, '')
                                                // Strip standalone artifact metadata JSON
                                                // (model sometimes emits it before the fence)
                                                .replace(/\n*\s*\{[^{}]*"title"\s*:[^{}]*"primaryLanguage"\s*:[^{}]*\}\s*$/i, '')
                                                .replace(/\n*\s*\{[^{}]*"primaryLanguage"\s*:[^{}]*"title"\s*:[^{}]*\}\s*$/i, '')
                                                .trim();
                                            if (_streamRenderer) {
                                                _streamRenderer.destroy();
                                                _streamRenderer = null; window._activeStreamRenderer = null;
                                            }
                                            if (typeof targetContent !== 'undefined' && targetContent) {
                                                targetContent.innerHTML = '';
                                                if (proseOnly && typeof StreamRenderer !== 'undefined') {
                                                    const proseRenderer = new StreamRenderer(targetContent, { replay: true, streamMeta });
                                                    proseRenderer.append(proseOnly);
                                                    proseRenderer.finalize();
                                                }
                                            }
                                        };

                                        const metaMatch = streamedContent.match(/```artifact-meta\s*\n/i);
                                        if (metaMatch) {
                                            // Phase 1: as soon as artifact-meta is seen,
                                            // reseed the chat with prose-only and stop
                                            // feeding StreamRenderer. This prevents code
                                            // from flashing in the chat between read()
                                            // calls while we wait for artifact-file:.
                                            if (!artifactMetaPending) {
                                                artifactMetaPending = true;
                                                artifactMetaIndex = metaMatch.index;
                                                _fenceJustTriggered = true;
                                                _reseedChatWithProse(metaMatch.index);
                                            }

                                            // Phase 2: once artifact-file: arrives, fully
                                            // activate artifact streaming.
                                            const fileMatch = streamedContent.match(/```artifact-file:([^:]+):(\w+)\s*\n/i);
                                            if (fileMatch) {
                                                artifactStreamActive = true;
                                                artifactMetaPending = false;
                                                _fenceJustTriggered = true;
                                                artifactFencePosition = metaMatch.index;
                                                window.artifactPanel.openStreaming();
                                                const firstPath = fileMatch[1].trim();
                                                const firstLang = fileMatch[2].trim();
                                                if (window.artifactPanel.switchStreamingFile) {
                                                    window.artifactPanel.switchStreamingFile(firstPath, firstLang);
                                                }
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
                                                _fenceJustTriggered = true;
                                                artifactFencePosition = fenceMatch.index;
                                                _reseedChatWithProse(fenceMatch.index);
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

                                // Feed StreamRenderer AFTER fence detection, only when
                                // no artifact fence was just triggered. This prevents
                                // artifact code from leaking into the message bubble.
                                // Also skip when an image placeholder is active — the
                                // narration streaming after an ACTION: generate_image
                                // header must not render into a chat bubble.
                                if (!_fenceJustTriggered && !artifactStreamActive && !artifactMetaPending && !pendingImagePlaceholderId && _streamRenderer) {
                                    const _cleanChunk = _stripActionFromChunk(chunkText);
                                    if (_cleanChunk) {
                                        _streamRenderer.append(_cleanChunk);
                                    }
                                }
                                // Search banner dismiss logic
                                if (searchMsgId && !searchBannerDismissed && window.SearchResults) {
                                    searchBannerDismissed = true;
                                    var _searchMsgIdSnapshot = searchMsgId;
                                    var _waitStart = performance.now();
                                    var _MIN_TEXT = 80;
                                    var _MAX_WAIT_MS = 30000;
                                    var _waitForVisibleContent = function () {
                                        var searchEl = document.getElementById(_searchMsgIdSnapshot);
                                        var searchContent = searchEl?.querySelector('.message-content');
                                        if (!searchContent) return;
                                        var block = searchContent.querySelector(
                                            '.stream-block:not(.block-mermaid):not(.block-math)'
                                        );
                                        var elapsed = performance.now() - _waitStart;
                                        var textLen = block ? (block.textContent || '').trim().length : 0;
                                        if ((block && textLen >= _MIN_TEXT) || elapsed > _MAX_WAIT_MS) {
                                            console.log('[SEARCH-BANNER] dismissing after',
                                                Math.round(elapsed), 'ms. textLen=', textLen);
                                            window.SearchResults.dismissBanner(searchContent);
                                        } else {
                                            requestAnimationFrame(_waitForVisibleContent);
                                        }
                                    };
                                    requestAnimationFrame(_waitForVisibleContent);
                                }
                                if (bgRequest) {
                                    BackgroundRequestManager.appendContent(requestSessionId, chunkText);
                                }
                            }
                        } else if (chunk.type === 'image_generating') {
                            streamHasVisibleOutput = true;
                            // Explicit image request: show placeholder immediately
                            console.log('[Stream] image_generating:', chunk.description);
                            if (isSessionActive()) {
                                ThinkingIndicator.hide();
                                // Tear down any narration StreamRenderer that already
                                // raced ahead of us. Image responses are image-only on
                                // refresh (suppressText:true), so live must match —
                                // otherwise the narration bubble stays visible beside
                                // the image.
                                if (_streamRenderer) {
                                    _streamRenderer.destroy();
                                    _streamRenderer = null;
                                    window._activeStreamRenderer = null;
                                }
                                if (_streamMsgId) {
                                    const staleEl = document.getElementById(_streamMsgId);
                                    if (staleEl) staleEl.remove();
                                    _streamMsgId = null;
                                }
                                const imgPlaceholderId = generateMessageId();
                                displayMessage('', 'ai', 'image-placeholder', chunk.description || prompt, true, imgPlaceholderId);
                                placeholderTimestamps.set(imgPlaceholderId, Date.now());
                                // Store per-stream (local) so concurrent
                                // /image requests don't clobber each other.
                                pendingImagePlaceholderId = imgPlaceholderId;
                                // Also stash in localStorage so a refresh
                                // during generation can detect and recover.
                                try {
                                    if (typeof recordPendingImage === 'function') {
                                        recordPendingImage(imgPlaceholderId, chunk.description || prompt, requestSessionId);
                                    }
                                } catch (e) { /* storage unavailable — skip */ }
                            }
                        } else if (chunk.type === 'image_confirm') {
                            streamHasVisibleOutput = true;
                            // Inferred image request: show confirmation UI
                            console.log('[Stream] image_confirm:', chunk.description);
                            if (isSessionActive()) {
                                ThinkingIndicator.hide();
                                const confirmId = generateMessageId();
                                const desc = chunk.description || '';
                                const confirmHtml = `<div class="image-confirm" data-description="${desc.replace(/"/g, '&quot;')}">
                                    <p>Generate image: <em>"${desc}"</em> — is that right?</p>
                                    <div class="image-confirm__actions">
                                        <button class="image-confirm__btn image-confirm__btn--yes" onclick="window._handleImageConfirm(true)">Yes</button>
                                        <button class="image-confirm__btn image-confirm__btn--no" onclick="window._handleImageConfirm(false)">No</button>
                                    </div>
                                </div>`;
                                displayMessage(confirmHtml, 'ai', 'text', prompt, false, confirmId);
                                window._handleImageConfirm = (confirmed) => {
                                    const btns = document.querySelectorAll('.image-confirm__actions');
                                    btns.forEach(b => b.remove());
                                    if (confirmed) {
                                        sendMessage('yes');
                                    } else {
                                        sendMessage('no');
                                    }
                                    delete window._handleImageConfirm;
                                };
                            }
                        } else if (chunk.type === 'search_started' || chunk.type === 'search_web_results' ||
                                   chunk.type === 'search_images' || chunk.type === 'search_videos' ||
                                   chunk.type === 'search_content' || chunk.type === 'search_complete') {
                            streamHasVisibleOutput = true;
                            if (isSessionActive()) {
                                ThinkingIndicator.hide();
                                // Destroy StreamRenderer that has ACTION text from 1st LLM call
                                if (chunk.type === 'search_started') {
                                    if (_streamRenderer) {
                                        _streamRenderer.destroy();
                                        _streamRenderer = null; window._activeStreamRenderer = null;
                                    }
                                    // Remove the element that had ACTION text
                                    if (_streamMsgId) {
                                        var actionEl = document.getElementById(_streamMsgId);
                                        if (actionEl) actionEl.remove();
                                        _streamMsgId = null;
                                    }
                                    // Reset so synthesis chunks create a fresh StreamRenderer
                                    isFirstContentChunk = true;
                                    // Reset the client-side ACTION stripper so the synthesis
                                    // pass's first tokens get the same safety net applied.
                                    _actionStripDone = false;
                                    _actionStripBuffer = '';
                                }
                                if (!searchMsgId) {
                                    // Reuse the pre-stream bubble so the search banner lands inside
                                    // the same bubble allocated at prompt-send.
                                    searchMsgId = placeholderId || _preStreamBubbleId || generateMessageId();
                                }
                                let searchMsgEl = document.getElementById(searchMsgId);
                                if (!searchMsgEl) {
                                    displayMessage('\u200B', 'ai', 'text', prompt, false, searchMsgId);
                                    searchMsgEl = document.getElementById(searchMsgId);
                                }
                                const searchContent = searchMsgEl?.querySelector('.message-content');
                                if (searchContent && window.SearchResults) {
                                    window.SearchResults.handleSearchEvent(searchContent, chunk.type, chunk);
                                }
                            }
                        } else if (chunk.type === 'done') {
                            console.log('Received done chunk:', chunk);
                            if (chunk.guest_mode) {
                                if (typeof chunk.guest_message_count === 'number') {
                                    updateGuestMessageCount(chunk.guest_message_count);
                                }
                                if (chunk.guest_show_banner) {
                                    showGuestSignupBanner();
                                }
                            }
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
                            streamReminderData = chunk.reminder || null;
                            streamCryptoChart = chunk.crypto_chart || null;
                            streamMemoryUpdated = chunk.memory_updated || false;
                            streamDreamNotification = chunk.dream_notification || null;
                            streamArtifactPatches = chunk.artifact_patches || null;
                            // Model attribution (hoisted via var for access after loop)
                            streamModelMeta = chunk.model_meta || null;
                            // Use backend's response_id if available for consistency
                            if (chunk.response_id) {
                                streamResponseId = chunk.response_id;
                            }
                            console.log('Parsed artifact from done:', streamArtifact);
                            console.log('Parsed email from done:', streamIsEmail, streamEmailData);
                            if (streamCryptoChart) console.log('Parsed crypto chart:', streamCryptoChart);
                            // Store search results for post-stream rendering (message-scoped)
                            if (chunk.search_results || chunk.embed_video) {
                                const _sMsgId = searchMsgId || placeholderId || 'pending';
                                _searchStreamData.set(_sMsgId, {
                                    searchResults: chunk.search_results,
                                    embedVideo: chunk.embed_video,
                                    fetchedUrls: chunk.fetched_urls || [],
                                    renderHint: chunk.render_hint || 'text_only',
                                });
                            }
                            // Handle image from generate_image ACTION path.
                            // Support multi-image: if chunk.attachments has
                            // N image entries, pass the full URL list so
                            // replacePlaceholderWithImage renders a grid.
                            if ((chunk.image_url || (Array.isArray(chunk.attachments) && chunk.attachments.length))
                                && pendingImagePlaceholderId) {
                                const atts = Array.isArray(chunk.attachments) ? chunk.attachments : [];
                                const urls = atts
                                    .filter((a) => a && (a.isImage || (a.mime_type || '').startsWith('image/')))
                                    .map((a) => a.url)
                                    .filter(Boolean);
                                const finalUrls = urls.length ? urls : (chunk.image_url ? [chunk.image_url] : []);
                                if (finalUrls.length) {
                                    replacePlaceholderWithImage(
                                        pendingImagePlaceholderId,
                                        finalUrls.length === 1 ? finalUrls[0] : finalUrls,
                                        _styleDisplayPrompt !== null ? _styleDisplayPrompt : prompt
                                    );
                                    streamDoneImageUrls = finalUrls;
                                    streamDoneAttachments = atts.length ? atts : [{
                                        isImage: true,
                                        url: finalUrls[0],
                                        name: 'AI generated image'
                                    }];
                                    // Placeholder resolved — clear the
                                    // pending-refresh-recovery marker.
                                    try {
                                        if (typeof clearPendingImage === 'function') {
                                            clearPendingImage(pendingImagePlaceholderId);
                                        }
                                    } catch (e) { /* skip */ }
                                }
                            }
                            // Update session title in sidebar. Defer slightly
                            // so the tab DOM exists after session creation,
                            // then delegate to the shared helper.
                            if (chunk.session_name) {
                                setTimeout(() => {
                                    const mgr = window.chatManager;
                                    const titleSessionId = requestSessionId || mgr?.activeSessionId;
                                    applySessionTitle(titleSessionId, chunk.session_name);
                                }, 100);
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
                                ThinkingIndicator.hide();
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
                    ThinkingIndicator.hide();
                }

                // Prepare completion data — strip internal protocol lines and artifact fences
                let displayContent = streamChatSummary || streamedContent;
                // Strip ACTION: routing line and multi-line headers if leaked through
                if (displayContent) {
                    displayContent = displayContent
                        .replace(/^\s*[-*]*\s*ACTION:\s*\w+\s*\n*/i, '')
                        .replace(/^\s*description:\s*.+\n?/im, '')
                        .replace(/^\s*source:\s*.+\n?/im, '')
                        .trim();
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
                    // Clean up live StreamRenderer before abandoning this response
                    if (_streamRenderer) {
                        _streamRenderer.destroy();
                        _streamRenderer = null;
                        window._activeStreamRenderer = null;
                    }
                    if (bgRequest) {
                        BackgroundRequestManager.completeRequest(requestSessionId, completionData);
                    }
                    return null;
                }

                // Session is still active - just clear the request, no need to complete it
                console.log('Session still active, displaying streaming response');

                // Create the AI message container - prefer backend's response_id for consistency
                // Reuse searchMsgId if search created a placeholder element
                // If StreamRenderer created the element, use its ID (or rename to response_id)
                let aiMsgId = streamResponseId || searchMsgId || placeholderId || generateMessageId();
                if (_streamRenderer && _streamMsgId && _streamMsgId !== aiMsgId) {
                    // Rename the element to match the canonical response ID
                    var streamEl = document.getElementById(_streamMsgId);
                    if (streamEl) {
                        streamEl.id = aiMsgId;
                        streamEl.dataset.messageId = aiMsgId;
                    }
                }

                // generate_image done: placeholder already replaced, show chat text below image and save
                if (streamDoneImageUrls && streamDoneImageUrls.length) {
                    // Image branch takes over the bubble — tear down the StreamRenderer
                    // the same way the artifact/text branches below do, otherwise
                    // window._activeStreamRenderer lingers and resetSendButtonIfIdle()
                    // refuses to hide the stop button ("Generating response…" stuck).
                    if (_streamRenderer) {
                        _streamRenderer.destroy();
                        _streamRenderer = null;
                        window._activeStreamRenderer = null;
                    }
                    const imgUrl = streamDoneImageUrls[0]; // legacy single-URL hook
                    const imgAttachments = streamDoneAttachments || [{ isImage: true, url: imgUrl, name: 'AI generated image' }];
                    streamDoneImageUrls = null;
                    streamDoneAttachments = null;
                    const imgMsgId = pendingImagePlaceholderId || aiMsgId;
                    pendingImagePlaceholderId = null;
                    // Image responses render image-only — the refresh path strips the
                    // narration via suppressText:true, so live must match. Keeping the
                    // chat_summary in memory (displayContent) for persistence but not
                    // rendering it below the image.

                    const imgManager = window.chatManager;
                    if (imgManager?.addOrUpdateAiMessage) {
                        imgManager.addOrUpdateAiMessage({
                            response_id: imgMsgId,
                            content: displayContent || '',
                            prompt: prompt,
                            is_code: false,
                            render_mode: 'inline',
                            attachments: imgAttachments,
                            message_type: 'image',
                            model_meta: streamModelMeta || null
                        });
                    }
                    if (imgManager?.updateLastAiMessage) {
                        imgManager.updateLastAiMessage({
                            attachments: imgAttachments,
                            messageType: 'image'
                        });
                    }

                    // Canonical AI-message post-processing that the non-image branches
                    // at line ~3115 do. Image branch early-returns so everything below
                    // — dataset fields, model attribution, action buttons
                    // (Edit/Share/Copy) — must be applied here explicitly or refresh
                    // renders differently than live.
                    const imgContainer = document.getElementById(imgMsgId);
                    if (imgContainer) {
                        imgContainer.dataset.originalPrompt = prompt;
                        imgContainer.dataset.sender = 'ai';
                        imgContainer.dataset.timestamp = new Date().toISOString();
                        if (streamModelMeta) {
                            imgContainer._modelMeta = streamModelMeta;
                            if (typeof window.addModelAttribution === 'function') {
                                window.addModelAttribution(imgContainer, streamModelMeta);
                            }
                        }
                        const imgContent = imgContainer.querySelector('.message-content');
                        if (imgContent && !imgContainer.querySelector('.action-buttons')
                            && typeof appendActionButtons === 'function') {
                            appendActionButtons(imgContent, imgMsgId);
                        }
                    }

                    // Background request cleanup — non-image branches reach the
                    // shared cleanup at line ~3485 via the fall-through, this branch
                    // must do it manually before the early return or the manager
                    // keeps the session status as "streaming" and re-shows the
                    // ThinkingIndicator on the next switchSession() round-trip.
                    if (bgRequest) {
                        BackgroundRequestManager.updateSessionProcessingIndicator(requestSessionId, false);
                        BackgroundRequestManager.clearRequest(requestSessionId);
                    }
                    return aiMsgId;
                }

                const hasCode = displayContent.includes('```');

                // Phase 3: If StreamRenderer was active, finalize instead of re-rendering
                let messageContainer;
                if (_streamRenderer) {
                    // Destroy StreamRenderer for artifact responses — artifact panel takes over
                    if (streamRenderMode === 'artifact' || (streamArtifact && streamArtifact.artifactId)) {
                        _streamRenderer.destroy();
                        _streamRenderer = null; window._activeStreamRenderer = null;
                    }
                }

                if (_streamRenderer) {
                    // Safety net: server parsed an artifact but live fence
                    // detection never fired (e.g. model emitted artifact-file
                    // without artifact-meta, or fence straddled chunk
                    // boundaries awkwardly). In that case the bubble has
                    // unfenced metadata JSON ({"title":"...","primaryLanguage":
                    // "..."}) that server chat_summary has already stripped.
                    // Rebuild the bubble from chat_summary so live matches
                    // refresh byte-for-byte.
                    const needsArtifactReseed = streamArtifact && streamArtifact.artifactId
                        && !artifactStreamActive && !artifactMetaPending;
                    if (needsArtifactReseed) {
                        _streamRenderer.destroy();
                        _streamRenderer = null; window._activeStreamRenderer = null;
                        messageContainer = document.getElementById(aiMsgId);
                        if (messageContainer) {
                            const mc = messageContainer.querySelector('.message-content');
                            if (mc) {
                                mc.innerHTML = '';
                                if (streamChatSummary && typeof StreamRenderer !== 'undefined') {
                                    const reseedRenderer = new StreamRenderer(mc, { replay: true, streamMeta });
                                    reseedRenderer.append(streamChatSummary);
                                    reseedRenderer.finalize();
                                }
                            }
                        }
                    } else {
                        // Content was rendered live — flush remaining buffer.
                        // Keep _activeStreamRenderer set until the paced
                        // drain actually finishes (governor may still have
                        // chars queued) so the stop button doesn't flip
                        // back to send while text is still filling in.
                        const pendingRenderer = _streamRenderer;
                        pendingRenderer.finalize(() => {
                            if (window._activeStreamRenderer === pendingRenderer) {
                                window._activeStreamRenderer = null;
                            }
                            if (typeof resetSendButtonIfIdle === 'function') {
                                resetSendButtonIfIdle();
                            }
                        });
                        _streamRenderer = null;
                        messageContainer = document.getElementById(aiMsgId);
                    }
                    if (messageContainer) {
                        messageContainer.dataset.originalPrompt = prompt;
                        messageContainer.dataset.sender = 'ai';
                        messageContainer.dataset.timestamp = new Date().toISOString();
                        if (streamModelMeta) {
                            messageContainer._modelMeta = streamModelMeta;
                            if (typeof window.addModelAttribution === 'function') {
                                window.addModelAttribution(messageContainer, streamModelMeta);
                            }
                        }
                        if (!messageContainer.querySelector('.action-buttons') && typeof appendActionButtons === 'function') {
                            appendActionButtons(messageContainer.querySelector('.message-content'), aiMsgId);
                        }
                    }
                    if (messageContainer) {
                        var mc = messageContainer.querySelector('.message-content');
                        if (mc && typeof renderMathInMessage === 'function') {
                            renderMathInMessage(mc);
                        }
                        if (mc && typeof Prism !== 'undefined') {
                            Prism.highlightAllUnder(mc);
                        }
                    }
                } else if (artifactStreamActive || artifactMetaPending) {
                    // Artifact fence detection already reseeded the bubble with
                    // prose-only content via _reseedChatWithProse and nulled
                    // _streamRenderer. The bubble DOM is correct RIGHT NOW.
                    // Do NOT call displayMessage — it would re-render the full
                    // displayContent (which includes artifact code from the
                    // server's done chunk) back into the bubble, undoing the
                    // reseed. Just grab the existing element.
                    messageContainer = document.getElementById(_streamMsgId || aiMsgId);
                    if (messageContainer) {
                        messageContainer.dataset.originalPrompt = prompt;
                        messageContainer.dataset.sender = 'ai';
                        messageContainer.dataset.timestamp = new Date().toISOString();
                        if (streamModelMeta) {
                            messageContainer._modelMeta = streamModelMeta;
                            if (typeof window.addModelAttribution === 'function') {
                                window.addModelAttribution(messageContainer, streamModelMeta);
                            }
                        }
                        if (!messageContainer.querySelector('.action-buttons') && typeof appendActionButtons === 'function') {
                            appendActionButtons(messageContainer.querySelector('.message-content'), aiMsgId);
                        }
                    }
                } else {
                    // Non-streaming or artifact fallback — existing path
                    messageContainer = displayMessage(
                        displayContent,
                        'ai',
                        hasCode ? 'code' : 'text',
                        prompt,
                        true,
                        aiMsgId,
                        null,  // attachmentsMeta
                        null,  // searchResults
                        streamModelMeta
                    );
                }

                // Add reasoning section if available (only for R1 model)
                if (isDeepSeekR1 && streamedReasoning && messageContainer) {
                    ReasoningDisplay.create(streamedReasoning, messageContainer);
                }

                // Render inline search results (sources, video cards, citations)
                const _sdKey = [aiMsgId, searchMsgId, placeholderId, 'pending'].find(k => k && _searchStreamData.has(k));
                const _sd = _sdKey ? _searchStreamData.get(_sdKey) : null;
                if (_sd && window.SearchResults) {
                    window.SearchResults.renderFinalResults(
                        messageContainer || document.getElementById(aiMsgId),
                        _sd.searchResults,
                        _sd.embedVideo,
                        _sd.fetchedUrls,
                        _sd.renderHint
                    );
                    _searchStreamData.delete(_sdKey);
                }

                // Handle email composer for streamed responses (backend flag)
                if (streamIsEmail && streamEmailData && streamEmailData.body && messageContainer && window.EmailComposer) {
                    const messageContent = messageContainer.querySelector('.message-content');
                    if (messageContent) {
                        messageContent.dataset.emailComposer = 'true';
                        messageContent.innerHTML = '';
                        // Pass streaming=false — the email body arrived in the done
                        // payload already complete, so the character-by-character
                        // animation just adds a pointless 1-2s delay. Refresh path
                        // (startNewChatModule.js:4495) already passes false.
                        window.EmailComposer.createCard(streamEmailData, messageContent, false);
                        if (typeof appendActionButtons === 'function'
                            && !messageContainer.querySelector('.action-buttons')) {
                            appendActionButtons(messageContent, aiMsgId);
                        }
                        console.log('Email composer card created for streamed response');
                    }
                }

                // Handle reminder card for streamed responses
                if (streamReminderData && streamReminderData.id && messageContainer && window.RemindersModule) {
                    const messageContent = messageContainer.querySelector('.message-content') || messageContainer;
                    window.RemindersModule.renderCardForMessage(messageContent, streamReminderData);
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
                            // DOM order matches the refresh path
                            // (startNewChatModule.js:4400-4408):
                            //   [introText] [chartWrapper] [...AI text...]
                            // Stream used to append both AFTER the text — so the
                            // chart sat below the response while live, then jumped
                            // above the text on refresh. Prepend matches refresh.
                            const chartWrapper = document.createElement('div');
                            chartWrapper.className = 'crypto-chart-wrapper';
                            messageContent.insertBefore(chartWrapper, messageContent.firstChild);

                            const introText = document.createElement('p');
                            introText.className = 'chart-intro-text';
                            introText.textContent = generateChartIntroText(asset, assetType);
                            messageContent.insertBefore(introText, chartWrapper);

                            // Render as compact sparkline (refresh path uses the
                            // same default). Range MAX matches the refresh default.
                            const renderFn = (window.CryptoChartModule.renderSparkline || window.CryptoChartModule.render);
                            Promise.resolve(renderFn(chartWrapper, asset, {
                                currency: streamCryptoChart.currency || 'usd',
                                range: streamCryptoChart.range || 'MAX',
                                assetType: assetType
                            })).catch(err => console.error('[Chart] Render error:', err));
                            console.log(`[Chart] ${assetType} sparkline rendered for:`, asset);
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
                // Build artifact reference for local cache if artifact exists.
                // Must mirror the shape used by the refresh path
                // (startNewChatModule.js:4290+) so the pill renders identically —
                // pending/isActive drive CSS state, versionId/entrypoint drive
                // the file-open behavior.
                let streamArtifactRef = null;
                if (streamArtifact && streamArtifact.artifactId) {
                    streamArtifactRef = {
                        artifactId: streamArtifact.artifactId,
                        title: streamArtifact.title,
                        type: streamArtifact.type,
                        primaryLanguage: streamArtifact.primaryLanguage,
                        versionNumber: streamArtifact.activeVersion?.versionNumber || streamArtifact.versionCount || 1,
                        versionId: streamArtifact.activeVersion?.versionId,
                        entrypoint: streamArtifact.activeVersion?.entrypointPath,
                        isActive: streamArtifact.isActive,
                        pending: streamArtifact.pending,
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
                        reminder: streamReminderData || null,
                        artifact_reference: streamArtifactRef,
                        crypto_chart: streamCryptoChart || null,
                        model_meta: streamModelMeta || null,
                        search_results: (_sd && _sd.searchResults) || null,
                        style: streamMeta?.style || null
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
                    // Register the streamed payload as a session-scoped draft so
                    // clicking the pill opens instantly from local cache instead of
                    // triggering a server fetch. Refresh path does this at
                    // startNewChatModule.js:4319 — live was skipping it.
                    if (typeof window.artifactPanel.registerDraft === 'function') {
                        window.artifactPanel.registerDraft(artifactRef, streamArtifact, requestSessionId);
                    }
                    window.artifactPanel.attachPreview(messageContainer, artifactRef, {
                        sessionId: requestSessionId,
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
                ThinkingIndicator.hide();
                if (streamError.name === 'AbortError') {
                    console.log('Stream request was aborted');
                    return null;
                }
                console.error('Streaming error:', streamError);
                if (streamHasVisibleOutput) {
                    // Do not issue a second /chat request after the live stream
                    // has already started rendering. Keep the partial answer
                    // instead of duplicating the turn with a fallback request.
                    if (_streamRenderer) {
                        _streamRenderer.finalize();
                        _streamRenderer = null;
                        window._activeStreamRenderer = null;
                    }
                    if (bgRequest) {
                        BackgroundRequestManager.errorRequest(requestSessionId, streamError.message || 'Streaming error');
                        BackgroundRequestManager.clearRequest(requestSessionId);
                    }
                    if (isSessionActive() && window.Toast) {
                        window.Toast.warning('Connection interrupted. Partial response shown.', 3500);
                    }
                    return _streamMsgId ? document.getElementById(_streamMsgId) : null;
                }
                // Tear down any partial StreamRenderer before the fallback path
                // writes into the same container — otherwise the legacy
                // displayMessage path double-renders on top of stale block DOM.
                if (_streamRenderer) {
                    _streamRenderer.destroy();
                    _streamRenderer = null; window._activeStreamRenderer = null;
                }
                if (_streamMsgId) {
                    const partialEl = document.getElementById(_streamMsgId);
                    if (partialEl) partialEl.remove();
                }
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
            ThinkingIndicator.hide();
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
                isDeepSeekR1: model === 'deepseek-v4-pro' || model === 'deepseek-v4-flash' || model === 'deepseek-reasoner',
                prompt,
                fullData: data  // Store full response for later
            };

            // Store completion data for when user switches back
            if (bgRequest) {
                BackgroundRequestManager.completeRequest(requestSessionId, completionData);
            }

            // Session switched away — no fake-stream delay needed.
            // BackgroundRequestManager.completeRequest already stored completion data;
            // flip the blue-dot notification immediately.
            console.log('Background response captured for session:', requestSessionId);
            if (typeof BackgroundRequestManager !== 'undefined') {
                BackgroundRequestManager.updateSessionNotificationDot(requestSessionId, true);
            }

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
            const altPrompt = _styleDisplayPrompt !== null ? _styleDisplayPrompt : prompt;
            if (placeholderId && document.getElementById(placeholderId)) {
                replacePlaceholderWithImage(placeholderId, data.image_url, altPrompt);
                messageContainer = document.getElementById(placeholderId);
            } else {
                displayMessage('', 'ai', 'image-placeholder', altPrompt, true, aiMsgId);
                placeholderTimestamps.set(aiMsgId, Date.now());
                replacePlaceholderWithImage(aiMsgId, data.image_url, altPrompt);
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
                },
                data.style || null
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
                    reminder: data.reminder || null,
                    crypto_chart: data.crypto_chart || null,
                    auto_selected: !!data.auto_selected,
                    model_id: data.model_id || null,
                    model_label: data.model_label || null,
                    model_provider: data.model_provider || null,
                    model_provider_label: data.model_provider_label || null,
                    task_type: data.task_type || null,
                    style: data.style || null,
                });
                // Persist to backend immediately so reasoning survives refresh
                if (manager.saveSession && manager.activeSessionId) {
                    manager.saveSession(manager.activeSessionId, { silent: true }).catch(err => {
                        console.warn('Failed to auto-save after response:', err);
                    });
                }
            }
        } else if (placeholderId) {
            // Render a proper failure card with a retry affordance instead of
            // a bare "Image unavailable" text bubble. Retry restores the
            // prompt to the composer so the user can refine and resend.
            messageContainer = displayMessage(
                '',
                'ai',
                'text',
                prompt,
                true,
                aiMsgId
            );
            if (messageContainer) {
                const content = messageContainer.querySelector('.message-content') || messageContainer;
                renderImageFailureCard(content, prompt);
            }
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
                // streaming=false — full body is already in data.email. Without
                // this the composer runs its char-by-char replay animation on
                // complete data, which is pure UX dead weight.
                window.EmailComposer.createCard(data.email, messageContent, false);
                if (typeof appendActionButtons === 'function'
                    && !messageContainer.querySelector('.action-buttons')) {
                    appendActionButtons(messageContent, aiMsgId);
                }
                console.log('Email composer card created');
            }
        }

        // Handle reminder card — AI created a reminder this turn
        if (data.reminder && data.reminder.id && messageContainer && window.RemindersModule) {
            const messageContent = messageContainer.querySelector('.message-content') || messageContainer;
            window.RemindersModule.renderCardForMessage(messageContent, data.reminder);
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
                    // DOM order matches refresh path: [introText][chartWrapper][text]
                    const chartWrapper = document.createElement('div');
                    chartWrapper.className = 'crypto-chart-wrapper';
                    messageContent.insertBefore(chartWrapper, messageContent.firstChild);

                    const introText = document.createElement('p');
                    introText.className = 'chart-intro-text';
                    introText.textContent = generateChartIntroText(asset, assetType);
                    messageContent.insertBefore(introText, chartWrapper);

                    const renderFn = (window.CryptoChartModule.renderSparkline || window.CryptoChartModule.render);
                    Promise.resolve(renderFn(chartWrapper, asset, {
                        currency: data.crypto_chart.currency || 'usd',
                        range: data.crypto_chart.range || 'MAX',
                        assetType: assetType
                    })).catch(err => console.error('[Chart] Render error:', err));
                    console.log(`[Chart] ${assetType} sparkline rendered (non-stream) for:`, asset);
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
                    applySessionTitle(sessionId, data.session_name);
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
        // Clean up StreamRenderer if stream failed mid-render
        if (_streamRenderer) {
            _streamRenderer.destroy();
            _streamRenderer = null; window._activeStreamRenderer = null;
        }
        // Clear any partially-rendered StreamRenderer DOM so fallback path starts clean
        if (_streamMsgId) {
            const partialEl = document.getElementById(_streamMsgId);
            if (partialEl) partialEl.remove();
        }
        // Drop the pre-stream bubble if no response path adopted it (e.g. abort
        // before first content chunk, network error, etc.). The data-pre-stream
        // marker is still set when no handler took ownership; if any handler
        // (image placeholder, search banner, content stream) claimed it, the
        // marker has been cleared and we leave the bubble alone — handleMessageError
        // / displayMessage will decide what to render in it.
        if (_preStreamBubbleId && _preStreamBubbleId !== _streamMsgId) {
            const orphan = document.getElementById(_preStreamBubbleId);
            if (orphan && orphan.dataset.preStream === '1') orphan.remove();
        }
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
            ThinkingIndicator.hide();
        }

        // Defensive: if the pre-stream bubble somehow survived without any
        // response handler claiming it (e.g. server returned 0 events), drop it.
        if (_preStreamBubbleId && _preStreamBubbleId !== _streamMsgId) {
            const orphan = document.getElementById(_preStreamBubbleId);
            if (orphan && orphan.dataset.preStream === '1') orphan.remove();
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
    // Abort the active session's background request (this is the controller wired to fetch)
    const currentSessionId = window.chatManager?.activeSessionId;
    if (currentSessionId) {
        BackgroundRequestManager.abortRequest(currentSessionId);
    }

    // Also abort the global fallback controller
    if (abortController) {
        abortController.abort();
        abortController = null;
    }

    isRequestInProgress = false;
    isTypingStopped = true;

    // Finalize active StreamRenderer if any (cancels RAF loop + removes cursor)
    if (window._activeStreamRenderer) {
        window._activeStreamRenderer.finalize();
        window._activeStreamRenderer = null;
    }

    // Remove all cursors from the DOM
    document.querySelectorAll('.streaming-cursor, .stream-cursor').forEach(el => el.remove());

    // Hide thinking indicator
    if (typeof ThinkingIndicator !== 'undefined') ThinkingIndicator.hide();

    const promptInput = document.getElementById('prompt');
    if (promptInput) {
        promptInput.disabled = false;
    }

    resetSendButtonIfIdle();
}

if (typeof window !== 'undefined') {
    window.abortMessage = abortMessage;
}

/**
 * Render a user message: markdown-it inline → DOMPurify → innerHTML.
 * No streaming, no code-window chrome, no animation.
 * User code blocks render as <pre><code> via markdown-it's default rules —
 * intentionally plain, matching the text bubble styling.
 */
function renderUserMessage(container, text, messageId) {
    if (container && container.dataset.emailComposer === 'true') {
        return;
    }
    const div = document.createElement('div');
    div.classList.add('text-message-container');
    if (messageId) {
        div.id = `message-${messageId}`;
    }
    container.appendChild(div);

    const messageEl = document.createElement('div');
    messageEl.classList.add('text-message');
    div.appendChild(messageEl);

    if (window.md) {
        messageEl.innerHTML = DOMPurify.sanitize(window.md.render(text || ''));
    } else {
        // markdown-it CDN failed to load (e.g. offline). Render as plain text
        // rather than throwing — preserves send flow over flaky networks.
        messageEl.textContent = text || '';
    }
}

/**
 * Render an AI text message that did NOT come from the live SSE stream.
 * Routes through StreamRenderer (replay mode) so history, fallbacks,
 * and non-streamed responses produce identical DOM to the live stream.
 * Preserves tree-view and recipe special cases.
 *
 * `container` is the .message-content element; this function appends to it.
 */
function renderAiTextImmediate(container, text, messageId, showButtons, style) {
    if (container && container.dataset.emailComposer === 'true') {
        return;
    }
    if (!text || typeof text !== 'string') {
        return;
    }

    const applyStyle = (wrapper) => {
        const valid = new Set(['editorial', 'modern_blog', 'technical', 'chat', 'comparison', 'creative']);
        if (style && valid.has(style)) wrapper.dataset.style = style;
    };

    // Special widget: tree view
    if (typeof isTreeStructure === 'function' && isTreeStructure(text, null)) {
        const wrapper = document.createElement('div');
        wrapper.classList.add('text-message-container');
        applyStyle(wrapper);
        wrapper.appendChild(createTreeView(text));
        container.appendChild(wrapper);
        if (showButtons && typeof appendActionButtons === 'function') {
            appendActionButtons(container, messageId);
        }
        return;
    }

    // Special widget: recipe card
    if (window.RecipeDetector && window.RecipeDetector.isRecipe(text, null)) {
        const wrapper = document.createElement('div');
        wrapper.classList.add('text-message-container');
        applyStyle(wrapper);
        wrapper.appendChild(window.RecipeDetector.createRecipeCard(text));
        container.appendChild(wrapper);
        if (showButtons && typeof appendActionButtons === 'function') {
            appendActionButtons(container, messageId);
        }
        return;
    }

    // Default: StreamRenderer in replay mode (no typing animation, DOM matches live)
    const renderer = new StreamRenderer(container, { replay: true, streamMeta: style ? { style } : {} });
    renderer.append(text);
    renderer.finalize();

    // KaTeX inline math (\(...\), $$...$$) — live stream path does this at line ~3227
    // after finalize; replay path needs it explicit here.
    if (typeof renderMathInMessage === 'function') {
        renderMathInMessage(container);
    }

    // Model attribution (live stream applies this at finalize; replay needs it explicit)
    const msgDiv = container.closest('.message');
    if (msgDiv && msgDiv._modelMeta && typeof window.addModelAttribution === 'function') {
        window.addModelAttribution(msgDiv, msgDiv._modelMeta);
        delete msgDiv._modelMeta;
    }

    if (showButtons && typeof appendActionButtons === 'function') {
        appendActionButtons(container, messageId);
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
