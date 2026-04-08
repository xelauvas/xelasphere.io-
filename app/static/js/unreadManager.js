/**
 * UnreadNotificationManager - Cross-device unread notification system
 *
 * Uses client-side localStorage to track lastReadAt per session.
 * Compares against server's last_message_at to determine unread state.
 * This enables true per-device notifications without shared server state.
 */
const UnreadNotificationManager = (() => {
    // State
    let sessionTimestamps = new Map();  // sessionId -> { last_message_at }
    let pollIntervalId = null;
    let isInitialized = false;
    let cachedETag = null;  // ETag for cache validation
    let broadcastChannel = null;  // Cross-tab sync
    let memoryFallback = null;  // In-memory fallback when localStorage full
    let useMemoryFallback = false;  // Flag when localStorage unavailable
    let originalFavicon = null;  // Original favicon for badge overlay
    let faviconCanvas = null;  // Canvas for drawing badge
    let currentUnreadCount = 0;  // Track current unread count for favicon

    // Configuration
    const POLL_INTERVAL_VISIBLE = 30000;   // 30 seconds when tab is visible
    const POLL_INTERVAL_HIDDEN = 60000;    // 60 seconds when tab is hidden
    const STORAGE_KEY = 'xela_session_read_times';
    const BROADCAST_CHANNEL_NAME = 'xela_unread_sync';
    const MAX_READ_TIMES_ENTRIES = 100;  // Limit entries to prevent bloat
    const FAVICON_SIZE = 32;  // Favicon canvas size
    const BADGE_COLOR = '#3b82f6';  // Blue (matches tab notification dots)

    /**
     * Get CSRF token from meta tag
     */
    function getCSRFToken() {
        const meta = document.querySelector('meta[name="csrf-token"]');
        return meta ? meta.getAttribute('content') : '';
    }

    /**
     * Get the currently active session ID
     */
    function getActiveSessionId() {
        return window.chatManager?.activeSessionId || null;
    }

    /**
     * Initialize favicon badge system
     */
    function initFaviconBadge() {
        // Create canvas for favicon manipulation
        faviconCanvas = document.createElement('canvas');
        faviconCanvas.width = FAVICON_SIZE;
        faviconCanvas.height = FAVICON_SIZE;

        // Find or create favicon link element
        let faviconLink = document.querySelector('link[rel="icon"]') ||
                          document.querySelector('link[rel="shortcut icon"]');

        if (!faviconLink) {
            faviconLink = document.createElement('link');
            faviconLink.rel = 'icon';
            faviconLink.type = 'image/png';
            document.head.appendChild(faviconLink);
        }

        // Load logo image directly (same-origin, no CORS issues)
        const img = new Image();
        img.onload = () => {
            originalFavicon = img;
            updateFaviconBadge(currentUnreadCount);
        };
        img.onerror = () => {
            faviconCanvas = null;
        };
        img.src = '/static/images/logo.png';
    }

    /**
     * Create a default favicon (simple circle)
     */
    function createDefaultFavicon() {
        const canvas = document.createElement('canvas');
        canvas.width = FAVICON_SIZE;
        canvas.height = FAVICON_SIZE;
        const ctx = canvas.getContext('2d');

        // Draw a simple app icon (dark circle with X)
        ctx.fillStyle = '#1e293b';
        ctx.beginPath();
        ctx.arc(FAVICON_SIZE / 2, FAVICON_SIZE / 2, FAVICON_SIZE / 2 - 2, 0, Math.PI * 2);
        ctx.fill();

        // Draw X
        ctx.strokeStyle = '#60a5fa';
        ctx.lineWidth = 3;
        ctx.lineCap = 'round';
        const padding = 10;
        ctx.beginPath();
        ctx.moveTo(padding, padding);
        ctx.lineTo(FAVICON_SIZE - padding, FAVICON_SIZE - padding);
        ctx.moveTo(FAVICON_SIZE - padding, padding);
        ctx.lineTo(padding, FAVICON_SIZE - padding);
        ctx.stroke();

        const img = new Image();
        img.src = canvas.toDataURL();
        return img;
    }

    /**
     * Update favicon with notification badge showing count
     */
    function updateFaviconBadge(count) {
        if (!faviconCanvas || !originalFavicon) return;

        const ctx = faviconCanvas.getContext('2d');
        ctx.clearRect(0, 0, FAVICON_SIZE, FAVICON_SIZE);

        // Draw original favicon
        ctx.drawImage(originalFavicon, 0, 0, FAVICON_SIZE, FAVICON_SIZE);

        // Draw number in bottom-right corner
        if (count > 0) {
            const text = count > 9 ? '9' : String(count);
            const x = FAVICON_SIZE - 8;
            const y = FAVICON_SIZE - 7;

            // Blue number - try Segoe UI (Windows system font)
            ctx.fillStyle = BADGE_COLOR;
            ctx.font = 'bold 20px "Segoe UI", sans-serif';
            ctx.textAlign = 'center';
            ctx.textBaseline = 'middle';
            ctx.fillText(text, x, y);
        }

        // Update favicon
        let faviconLink = document.querySelector('link[rel="icon"]') ||
                          document.querySelector('link[rel="shortcut icon"]');
        if (!faviconLink) {
            faviconLink = document.createElement('link');
            faviconLink.rel = 'icon';
            document.head.appendChild(faviconLink);
        }
        faviconLink.type = 'image/png';
        faviconLink.href = faviconCanvas.toDataURL('image/png');
    }

    /**
     * Set the unread count and update favicon badge
     */
    function setUnreadCount(count) {
        if (count !== currentUnreadCount) {
            currentUnreadCount = count;
            updateFaviconBadge(count);
            updateDocumentTitle(count);
            // Cache for optimistic render on next page load
            try { localStorage.setItem('xela_unread_count', String(count)); } catch (e) { /* ignore */ }
        }
    }

    /**
     * Update document.title with (N) unread badge
     */
    const _originalTitle = document.title;
    function updateDocumentTitle(count) {
        if (count > 0) {
            document.title = `(${count}) ${_originalTitle}`;
        } else {
            document.title = _originalTitle;
        }
    }

    /**
     * Load lastReadAt times from localStorage (with memory fallback)
     */
    function loadReadTimes() {
        // Use memory fallback if localStorage failed previously
        if (useMemoryFallback) {
            return memoryFallback || {};
        }

        try {
            const stored = localStorage.getItem(STORAGE_KEY);
            return stored ? JSON.parse(stored) : {};
        } catch (e) {
            console.warn('[UnreadManager] Failed to load read times from localStorage:', e);
            return memoryFallback || {};
        }
    }

    /**
     * Save lastReadAt times to localStorage (with quota handling)
     */
    function saveReadTimes(readTimes) {
        // Always update memory fallback
        memoryFallback = readTimes;

        if (useMemoryFallback) {
            return; // Already failed, don't retry localStorage
        }

        try {
            localStorage.setItem(STORAGE_KEY, JSON.stringify(readTimes));
        } catch (e) {
            if (e.name === 'QuotaExceededError' || e.code === 22) {
                console.warn('[UnreadManager] localStorage quota exceeded, attempting cleanup...');

                // Try to make space by pruning old entries
                if (tryPruneAndSave(readTimes)) {
                    return; // Success after pruning
                }

                // Try clearing other non-essential localStorage items
                if (tryClearOtherStorageAndSave(readTimes)) {
                    return; // Success after clearing
                }

                // Fall back to memory-only mode
                console.warn('[UnreadManager] Falling back to memory-only mode');
                useMemoryFallback = true;
            } else {
                console.warn('[UnreadManager] Failed to save read times:', e);
            }
        }
    }

    /**
     * Try to prune old entries and save again
     */
    function tryPruneAndSave(readTimes) {
        try {
            const entries = Object.entries(readTimes);
            if (entries.length <= 10) {
                return false; // Not enough to prune
            }

            // Sort by timestamp (oldest first) and keep only recent half
            entries.sort((a, b) => new Date(a[1]) - new Date(b[1]));
            const keepCount = Math.min(Math.floor(entries.length / 2), MAX_READ_TIMES_ENTRIES);
            const pruned = Object.fromEntries(entries.slice(-keepCount));

            localStorage.setItem(STORAGE_KEY, JSON.stringify(pruned));
            memoryFallback = pruned;
            console.log(`[UnreadManager] Pruned read times from ${entries.length} to ${keepCount}`);
            return true;
        } catch (e) {
            return false;
        }
    }

    /**
     * Try clearing non-essential storage and save again
     */
    function tryClearOtherStorageAndSave(readTimes) {
        try {
            // Clear any debug/temp keys we might have
            const keysToTry = ['xela_debug', 'xela_temp', 'xela_cache'];
            for (const key of keysToTry) {
                try { localStorage.removeItem(key); } catch {}
            }

            localStorage.setItem(STORAGE_KEY, JSON.stringify(readTimes));
            return true;
        } catch (e) {
            return false;
        }
    }

    /**
     * Get lastReadAt for a specific session
     */
    function getLastReadAt(sessionId) {
        const readTimes = loadReadTimes();
        return readTimes[sessionId] || null;
    }

    /**
     * Set lastReadAt for a specific session
     */
    function setLastReadAt(sessionId, timestamp) {
        let readTimes = loadReadTimes();
        readTimes[sessionId] = timestamp;

        // Proactively enforce entry limit to prevent quota issues
        const entries = Object.entries(readTimes);
        if (entries.length > MAX_READ_TIMES_ENTRIES) {
            // Sort by timestamp and keep most recent
            entries.sort((a, b) => new Date(a[1]) - new Date(b[1]));
            readTimes = Object.fromEntries(entries.slice(-MAX_READ_TIMES_ENTRIES));
        }

        saveReadTimes(readTimes);
    }

    /**
     * Initialize the manager - call this on page load
     */
    function init() {
        if (isInitialized) return;
        isInitialized = true;

        // Restore cached unread count immediately (prevents flash)
        try {
            const cached = localStorage.getItem('xela_unread_count');
            if (cached) {
                const count = parseInt(cached, 10);
                if (count > 0) {
                    currentUnreadCount = count;
                    updateDocumentTitle(count);
                }
            }
        } catch (e) { /* ignore */ }

        // Setup favicon badge system
        initFaviconBadge();

        // Apply cached count to favicon now that canvas is ready
        if (currentUnreadCount > 0) {
            updateFaviconBadge(currentUnreadCount);
        }

        // Setup cross-tab sync via BroadcastChannel
        setupBroadcastChannel();

        // Start polling
        startPolling();

        // Setup visibility listener for smart polling
        setupVisibilityListener();

        // Initial fetch (will reconcile with server)
        fetchUnreadCounts();

        console.log('[UnreadManager] Initialized');
    }

    /**
     * Setup BroadcastChannel for instant cross-tab sync
     */
    function setupBroadcastChannel() {
        if (typeof BroadcastChannel === 'undefined') {
            console.log('[UnreadManager] BroadcastChannel not supported');
            return;
        }

        try {
            broadcastChannel = new BroadcastChannel(BROADCAST_CHANNEL_NAME);
            broadcastChannel.onmessage = (event) => {
                const { type, sessionId, timestamp } = event.data;

                if (type === 'session_read') {
                    // Another tab marked this session as read — clear dot + favicon
                    console.log(`[UnreadManager] Cross-tab sync: session ${sessionId?.slice(-8)} marked read`);
                    updateNotificationDot(sessionId, false);
                    window.BackgroundRequestManager?.clearNotification?.(sessionId);
                    if (currentUnreadCount > 0) {
                        setUnreadCount(currentUnreadCount - 1);
                    }
                } else if (type === 'session_unread') {
                    // Another tab received new message — show dot if not active
                    const activeSessionId = getActiveSessionId();
                    if (sessionId !== activeSessionId) {
                        updateNotificationDot(sessionId, true);
                        setUnreadCount(currentUnreadCount + 1);
                    }
                } else if (type === 'request_sync') {
                    // Another tab wants fresh data - invalidate our cache
                    cachedETag = null;
                }
            };
            console.log('[UnreadManager] BroadcastChannel initialized');
        } catch (e) {
            console.warn('[UnreadManager] Failed to setup BroadcastChannel:', e);
        }
    }

    /**
     * Broadcast a message to other tabs
     */
    function broadcast(message) {
        if (broadcastChannel) {
            try {
                broadcastChannel.postMessage(message);
            } catch (e) {
                // Channel might be closed
            }
        }
    }

    /**
     * Start polling for session timestamps
     */
    function startPolling() {
        stopPolling();
        const interval = document.visibilityState === 'visible'
            ? POLL_INTERVAL_VISIBLE
            : POLL_INTERVAL_HIDDEN;
        pollIntervalId = setInterval(fetchUnreadCounts, interval);
        console.log(`[UnreadManager] Polling started (${interval}ms interval)`);
    }

    /**
     * Stop polling
     */
    function stopPolling() {
        if (pollIntervalId) {
            clearInterval(pollIntervalId);
            pollIntervalId = null;
        }
    }

    /**
     * Adjust polling interval based on tab visibility
     */
    function setPollingInterval(interval) {
        stopPolling();
        pollIntervalId = setInterval(fetchUnreadCounts, interval);
    }

    /**
     * Setup visibility change listener for smart polling
     */
    function setupVisibilityListener() {
        document.addEventListener('visibilitychange', () => {
            if (document.visibilityState === 'visible') {
                // Tab became visible - immediate sync and faster polling
                console.log('[UnreadManager] Tab visible - syncing immediately');
                fetchUnreadCounts();
                setPollingInterval(POLL_INTERVAL_VISIBLE);
            } else {
                // Tab hidden - slower polling to save resources
                setPollingInterval(POLL_INTERVAL_HIDDEN);
            }
        });
    }

    /**
     * Fetch session timestamps from server
     */
    async function fetchUnreadCounts() {
        try {
            const headers = { 'Accept': 'application/json' };
            if (cachedETag) {
                headers['If-None-Match'] = cachedETag;
            }

            const response = await fetch('/chat/unread-counts', {
                method: 'GET',
                headers,
                credentials: 'same-origin'
            });

            // 304 Not Modified - data unchanged, skip processing
            if (response.status === 304) {
                return;
            }

            if (!response.ok) {
                if (response.status === 403) {
                    // Not logged in - stop polling
                    console.log('[UnreadManager] Not logged in, stopping polling');
                    stopPolling();
                    return;
                }
                console.warn('[UnreadManager] Failed to fetch session timestamps:', response.status);
                return;
            }

            // Store ETag for next request
            const etag = response.headers.get('ETag');
            if (etag) {
                cachedETag = etag;
            }

            const data = await response.json();
            updateUnreadState(data.session_timestamps || {});
        } catch (error) {
            console.warn('[UnreadManager] Error fetching session timestamps:', error);
        }
    }

    /**
     * Update local state and UI based on server timestamps
     */
    function updateUnreadState(serverTimestamps) {
        const activeSessionId = getActiveSessionId();
        const readTimes = loadReadTimes();
        const previousSessions = new Set(sessionTimestamps.keys());

        console.log('[UnreadManager] updateUnreadState called', {
            activeSessionId,
            serverSessionCount: Object.keys(serverTimestamps).length,
            readTimesCount: Object.keys(readTimes).length
        });

        let unreadCount = 0;

        // ChatGPT-style: server polling only updates internal state + favicon badge.
        // Notification dots are managed purely by BackgroundRequestManager (in-memory, event-driven).
        for (const [sessionId, info] of Object.entries(serverTimestamps)) {
            const lastMessageAt = new Date(info.last_message_at).getTime();

            // Use localStorage first, fall back to server last_read_at (cross-device)
            let lastReadTime = 0;
            if (readTimes[sessionId]) {
                lastReadTime = new Date(readTimes[sessionId]).getTime();
            } else if (info.last_read_at) {
                // New device — seed from server
                lastReadTime = new Date(info.last_read_at).getTime();
                readTimes[sessionId] = info.last_read_at;
            }

            sessionTimestamps.set(sessionId, info);
            previousSessions.delete(sessionId);

            const hasUnreadMessages = lastMessageAt > lastReadTime;
            const isActive = sessionId === activeSessionId;

            if (!isActive && hasUnreadMessages) {
                unreadCount++;
            }
        }

        // Persist any server-seeded read times to localStorage
        saveReadTimes(readTimes);

        // Favicon badge only — no DOM dot manipulation from polling
        setUnreadCount(unreadCount);

        // Clean up removed sessions from internal state
        for (const sessionId of previousSessions) {
            sessionTimestamps.delete(sessionId);
        }

        // Periodically prune localStorage (every ~10 polls)
        if (Math.random() < 0.1) {
            cleanupOldReadTimes();
        }
    }

    /**
     * Update notification dot on session tab
     */
    function updateNotificationDot(sessionId, show) {
        // Use BackgroundRequestManager's method if available, otherwise direct DOM manipulation
        if (window.BackgroundRequestManager?.updateSessionNotificationDot) {
            window.BackgroundRequestManager.updateSessionNotificationDot(sessionId, show);
        } else {
            const tabElement = document.querySelector(`.chat-tab[data-session-id="${sessionId}"]`);
            if (tabElement) {
                tabElement.classList.toggle('has-notification', show);
            }
        }
    }

    /**
     * Mark a session as read - call when user opens/focuses a session
     * Stores current timestamp in localStorage for THIS device only
     */
    function markSessionRead(sessionId) {
        if (!sessionId) return;

        // Check if this session was unread (for favicon update)
        const wasUnread = hasUnread(sessionId);

        // Store current time as lastReadAt in localStorage (device-local cache)
        const now = new Date().toISOString();
        setLastReadAt(sessionId, now);

        // Clear notification dot immediately
        updateNotificationDot(sessionId, false);

        // Update favicon badge if session was unread
        if (wasUnread && currentUnreadCount > 0) {
            setUnreadCount(currentUnreadCount - 1);
        }

        // Also clear from BackgroundRequestManager's in-memory state
        if (window.BackgroundRequestManager?.clearNotification) {
            window.BackgroundRequestManager.clearNotification(sessionId);
        }

        // Persist to server — enables cross-device sync
        markReadOnServer(sessionId);

        // Broadcast to other tabs for instant sync
        broadcast({ type: 'session_read', sessionId, timestamp: now });
    }

    /**
     * Persist last_read_at to server for cross-device sync.
     * Fire-and-forget — don't block UI on network.
     */
    function markReadOnServer(sessionId) {
        const csrfToken = getCsrfToken();
        fetch(`/chat/session/${sessionId}/mark-read`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                ...(csrfToken ? { 'X-CSRFToken': csrfToken } : {})
            },
            credentials: 'same-origin'
        }).catch(err => {
            console.warn('[UnreadManager] Failed to mark read on server:', err);
        });
    }

    /**
     * Check if a session has unread messages on THIS device
     */
    function hasUnread(sessionId) {
        const info = sessionTimestamps.get(sessionId);
        if (!info) return false;

        const lastMessageAt = new Date(info.last_message_at).getTime();
        const lastReadAt = getLastReadAt(sessionId);
        const lastReadTime = lastReadAt ? new Date(lastReadAt).getTime() : 0;

        return lastMessageAt > lastReadTime;
    }

    /**
     * Get all sessions with unread messages on THIS device
     */
    function getUnreadSessions() {
        const unread = new Map();
        for (const [sessionId, info] of sessionTimestamps) {
            if (hasUnread(sessionId)) {
                unread.set(sessionId, info);
            }
        }
        return unread;
    }

    /**
     * Clean up old read times for sessions that no longer exist
     */
    function cleanupOldReadTimes() {
        const readTimes = loadReadTimes();
        const validSessionIds = new Set(sessionTimestamps.keys());
        let cleaned = false;

        for (const sessionId in readTimes) {
            if (!validSessionIds.has(sessionId)) {
                delete readTimes[sessionId];
                cleaned = true;
            }
        }

        if (cleaned) {
            saveReadTimes(readTimes);
        }
    }

    /**
     * Notify other tabs that a session has new messages
     * Call this when a background response completes
     */
    function notifyNewMessage(sessionId) {
        if (!sessionId) return;
        broadcast({ type: 'session_unread', sessionId });
    }

    // Public API
    return {
        init,
        fetchUnreadCounts,
        markSessionRead,
        notifyNewMessage,
        hasUnread,
        getUnreadSessions,
        getLastReadAt,
        cleanupOldReadTimes
    };
})();

// Auto-initialize when DOM is ready
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => {
        // Delay initialization slightly to ensure other modules are ready
        setTimeout(() => UnreadNotificationManager.init(), 100);
    });
} else {
    // DOM already loaded
    setTimeout(() => UnreadNotificationManager.init(), 100);
}

// Export for use in other modules
window.UnreadNotificationManager = UnreadNotificationManager;
