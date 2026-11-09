// chat-manager.js
const AUTO_TITLE_STOP_WORDS = new Set([
    'the', 'this', 'that', 'with', 'from', 'have', 'about', 'what', 'when',
    'where', 'your', 'you', 'how', 'can', 'will', 'would', 'could', 'should',
    'and', 'for', 'into', 'using', 'need', 'help', 'please', 'make', 'made',
    'build', 'built', 'issue', 'error', 'fix', 'why', 'want', 'just', 'like',
    'some', 'also', 'here', 'there', 'them', 'they', 'their', 'ours', 'mine'
]);

class ChatManager {
    constructor() {
        // Initialize chat history and session management
        this.chatHistory = {};
        this.dirtySessions = new Set();
        this.autoScrollPaused = false;
        this.activeSessionId = null;
        this.sessionCount = 0;
        this.nextPageToken = null;

        // CSRF Token for secure API requests
        this.csrfToken = document.querySelector('meta[name="csrf-token"]')?.getAttribute('content');

        // ETag cache for GET requests (reduces payload when data unchanged)
        this.etagCache = new Map();

        // Search/filter state
        this.searchQuery = '';
        this.searchActive = false;
        this.autoRenameTimers = {};
        this.imagesPageActive = false;
        this.chatsPageActive = false;
        this.imagesPageMode = 'home'; // 'home' | 'loading' | 'result' | 'error'
        this.imagesLastPrompt = '';
        this.imagesSubmitting = false;
        this.generatedImages = [];
        this.activePreviewImageId = null;
        this.imagesStylesData = [
            { label: '3D glam doll', prefix: '3D glam doll style', gradient: 'linear-gradient(135deg, #ff9ec7 0%, #ffd4a3 50%, #ffb3e6 100%)' },
            { label: 'Ornament', prefix: 'Ornament style', gradient: 'linear-gradient(135deg, #f5d76e 0%, #c0392b 50%, #922b21 100%)' },
            { label: 'Sugar cookie', prefix: 'Sugar cookie style', gradient: 'linear-gradient(135deg, #fff5e1 0%, #ffb88c 50%, #f38181 100%)' },
            { label: 'Fisheye', prefix: 'Fisheye photography', gradient: 'linear-gradient(135deg, #1a1a2e 0%, #16213e 50%, #0f3460 100%)' },
            { label: 'Art school', prefix: 'Art school sketch style', gradient: 'linear-gradient(135deg, #eae2b7 0%, #fcbf49 50%, #f77f00 100%)' },
            { label: 'Watercolor', prefix: 'Watercolor painting', gradient: 'linear-gradient(135deg, #a8d8ea 0%, #aa96da 50%, #fcbad3 100%)' },
            { label: 'Noir', prefix: 'Film noir black and white', gradient: 'linear-gradient(135deg, #232526 0%, #414345 100%)' }
        ];
        this.imagesDiscoverData = [
            { label: 'Me as an emperor', text: 'Portrait of me as a Roman emperor in regal attire' },
            { label: 'Create a cartoon', text: 'A whimsical cartoon illustration' },
            { label: 'Reimagine my pet as a human', text: 'Reimagine my pet as a human character' },
            { label: 'Style me', text: 'Fashion editorial portrait style' },
            { label: 'Give them a bowl cut', text: 'Portrait of a person with a bowl cut hairstyle, 70s vibes' },
            { label: 'What would I look like as a K-Pop star?', text: 'K-Pop idol stage photo portrait' }
        ];
        this.genericSessionNames = [
            'new chat',
            'untitled session',
            'untitled chat',
            'new session'
        ];
        this.openTabMenu = null;
        this.openTabMenuButtonGroup = null;
        this.temporarySessionId = null;
        this.temporaryCentered = false;
        this.isTemporaryUiActive = false;
        this.tempResizeAttached = false;
        this.composerTransitionTimer = null;
        this.isHomeView = true;
        this.pendingSessionPromise = null;
        this.handleTemporaryResize = () => this.positionComposerCentered();
        this.tabListBound = false;
        this.switchEpoch = 0;
        this.isReplayingHistory = false;
        this.replayUserPromptSet = null;
        this.autoSaveEnabled = false;
        
        // DOM Elements
        this.elements = {
            tabList: document.getElementById('tab-list'),
            chatDisplay: document.getElementById('chat-display'),
            pageControls: document.getElementById('page-controls'),
            messageInput: document.getElementById('prompt') || document.getElementById('message-input'),
            sendButton: document.getElementById('send-button'),
            sidebarToggleButton: document.getElementById('sidebar-toggle-button'),
            modelSelect: document.getElementById('model'),
            quickNewChat: document.getElementById('action-new-chat'),
            searchButton: document.getElementById('action-search-chat'),
            searchModal: document.getElementById('history-search-modal'),
            searchModalInput: document.getElementById('history-search-input'),
            searchResults: document.getElementById('history-search-results'),
            searchCloseTargets: document.querySelectorAll('[data-history-search-close]'),
            imagesButton: document.getElementById('action-images'),
            chatsButton: document.getElementById('action-chats'),
            imagesPage: document.getElementById('images-page'),
            chatsPage: document.getElementById('chats-page'),
            imagesPageBack: document.getElementById('images-page-back'),
            imagesPageBody: document.getElementById('images-page-body'),
            imagesPageCreate: document.getElementById('images-page-create'),
            imagesPageComposer: document.getElementById('images-page-composer'),
            imagesPromptBar: document.getElementById('images-prompt-bar'),
            imagesPromptInput: document.getElementById('images-prompt-input'),
            imagesPromptCancel: document.getElementById('images-prompt-cancel'),
            imagesPromptSubmit: document.getElementById('images-prompt-submit'),
            imagesGalleryEmptyCta: document.getElementById('images-gallery-empty-cta'),
            imagesViewer: document.getElementById('images-viewer'),
            imagesViewerImg: document.getElementById('images-viewer-img'),
            imagesViewerCaption: document.getElementById('images-viewer-caption'),
            imagesViewerClose: document.getElementById('images-viewer-close'),
            imagesViewerPrev: document.getElementById('images-viewer-prev'),
            imagesViewerNext: document.getElementById('images-viewer-next'),
            imagesViewerDownload: document.getElementById('images-viewer-download'),
            imagesViewerRegenerate: document.getElementById('images-viewer-regenerate'),
            imagesStylesTrack: document.getElementById('images-styles-track'),
            imagesDiscoverGrid: document.getElementById('images-discover-grid'),
            imagesStylesSection: document.getElementById('images-styles-section'),
            imagesDiscoverSection: document.getElementById('images-discover-section'),
            imagesGallerySection: document.getElementById('images-gallery-section'),
            imagesGrid: document.getElementById('images-gallery-grid'),
            imagesEmpty: document.getElementById('images-gallery-empty'),
            imagesPageLoading: document.getElementById('images-page-loading'),
            imagesPageResult: document.getElementById('images-page-result'),
            imagesResultImg: document.getElementById('images-result-img'),
            imagesResultPrompt: document.getElementById('images-result-prompt'),
            imagesResultError: document.getElementById('images-page-error'),
            imagesErrorRetry: document.getElementById('images-error-retry'),
            imagesResultDownload: document.getElementById('images-result-download'),
            imagesResultRegenerate: document.getElementById('images-result-regenerate'),
            imagesResultEdit: document.getElementById('images-result-edit'),
            imagePreviewModal: document.getElementById('images-preview-modal'),
            imagePreviewPrompt: document.getElementById('images-preview-prompt'),
            imagePreviewMeta: document.getElementById('images-preview-meta'),
            imagePreviewImg: document.getElementById('images-preview-img'),
            imagePreviewLink: document.getElementById('images-preview-link'),
            imagePreviewCloseTargets: document.querySelectorAll('[data-images-preview-close]'),
            tempChatButton: document.getElementById('temp-chat-button'),
            tempChatButtonDesktop: document.getElementById('temp-chat-button-desktop'),
            inputContainer: document.querySelector('.input-container'),
            optionsPanel: document.querySelector('.options')
        };

        // Initialize the ChatManager
        this.init();
    }

    /**
     * Initialization method to bind events and load chat history.
     */
    async init() {
        this.bindEvents();
        this.updateSidebarToggleTooltip();
        await this.loadChatHistory();
        this.applyInitialSurface();
        window.addEventListener('popstate', () => this.syncSurfaceFromLocation());
    }

    applyInitialSurface() {
        const path = typeof window !== 'undefined' ? window.location.pathname : '';
        const surface = document.body?.dataset?.initialSurface
            || (path === '/images' ? 'images' : path === '/chats' ? 'chats' : 'chat');
        if (surface === 'images') {
            this.showImagesPage({ updateHistory: false });
        } else if (surface === 'chats') {
            this.showChatsPage({ updateHistory: false });
        }
    }

    syncSurfaceFromLocation() {
        const path = typeof window !== 'undefined' ? window.location.pathname : '';
        const onImages = path === '/images';
        const onChats = path === '/chats';
        if (onImages && !this.imagesPageActive) {
            this.showImagesPage({ updateHistory: false });
        } else if (!onImages && this.imagesPageActive) {
            this.hideImagesPage({ updateHistory: false });
        }
        if (onChats && !this.chatsPageActive) {
            this.showChatsPage({ updateHistory: false });
        } else if (!onChats && this.chatsPageActive) {
            this.hideChatsPage({ updateHistory: false });
        }
    }

    updateSessionUrl(sessionId = null) {
        if (typeof window === 'undefined' || !window.history?.replaceState) {
            return;
        }
        const currentPath = window.location.pathname;
        // Don't rewrite the URL away from a surface page (/chats, /images)
        // when there's no active session — the user is intentionally on it.
        if (!sessionId && (currentPath === '/chats' || currentPath === '/images')) {
            return;
        }
        // Same for project pages /p/<id>: the user opened the project view
        // explicitly. Never rewrite away from it just because chatManager
        // has no active session loaded yet.
        if (currentPath.startsWith('/p/')) {
            return;
        }
        const suffix = `${window.location.search || ''}${window.location.hash || ''}`;
        const newPath = sessionId ? `/c/${sessionId}` : '/';
        if (currentPath === newPath) {
            return;
        }
        window.history.replaceState(null, '', `${newPath}${suffix}`);
    }

    /**
     * Update the browser tab title based on session name.
     * @param {string|null} sessionId - The session ID, or null for default title.
     */
    updatePageTitle(sessionId = null) {
        if (typeof document === 'undefined') return;

        const defaultTitle = 'XelaSphere';

        if (!sessionId) {
            document.title = defaultTitle;
            return;
        }

        const session = this.chatHistory[sessionId];
        if (session && session.session_name && !this.isGenericName(session.session_name)) {
            document.title = session.session_name;
        } else {
            document.title = defaultTitle;
        }
    }

    /**
     * Bind event listeners for UI interactions.
     */
    bindEvents() {
        // Button click events - handles both mobile drawer and desktop collapse
        this.elements.sidebarToggleButton?.addEventListener('click', () => {
            const isMobile = window.DeviceDetector ? DeviceDetector.isMobile() : (window.innerWidth <= 768);
            const isViewportMobile = document.body.classList.contains('viewport-mobile');

            if (isMobile || isViewportMobile) {
                // Mobile: toggle drawer open/close
                const isOpen = document.body.classList.contains('mobile-sidebar-open');
                if (window.sidebarManager && typeof window.sidebarManager.toggleLeftSidebar === 'function') {
                    window.sidebarManager.toggleLeftSidebar();
                } else {
                    // Fallback for mobile
                    document.body.classList.toggle('mobile-sidebar-open', !isOpen);
                    document.getElementById('mobile-overlay')?.classList.toggle('active', !isOpen);
                }
                // Toggle guest floating login button visibility (show when closed)
                this.updateGuestFloatingLogin(isOpen);
            } else {
                // Desktop: toggle collapse/expand
                const isCollapsed = document.body.classList.contains('sidebar-collapsed');
                if (window.sidebarManager && typeof window.sidebarManager.toggleLeftSidebar === 'function') {
                    window.sidebarManager.toggleLeftSidebar(isCollapsed ? false : true);
                } else if (typeof window.toggleLeftSidebar === 'function') {
                    window.toggleLeftSidebar(isCollapsed ? false : true);
                } else {
                    // Fallback: toggle collapsed classes if SidebarManager isn't initialized yet
                    document.body.classList.toggle('sidebar-collapsed', !isCollapsed);
                    document.getElementById('sidebar')?.classList.toggle('closed', !isCollapsed);
                }
                this.updateSidebarToggleTooltip(!isCollapsed);
                // Toggle guest floating login button visibility
                this.updateGuestFloatingLogin(!isCollapsed);
            }
        });
        this.elements.quickNewChat?.addEventListener('click', () => {
            if (this.imagesPageActive) this.hideImagesPage();
            if (this.chatsPageActive) this.hideChatsPage();
            this.createNewChat();
        });
        this.elements.tempChatButton?.addEventListener('click', () => this.createNewChat(null, {
            temporary: true,
            notifySuccess: false
        }));
        this.elements.tempChatButtonDesktop?.addEventListener('click', () => this.createNewChat(null, {
            temporary: true,
            notifySuccess: false
        }));
        this.elements.searchButton?.addEventListener('click', () => this.openHistorySearchModal());
        this.elements.imagesButton?.addEventListener('click', () => this.showImagesPage());
        this.elements.chatsButton?.addEventListener('click', () => this.showChatsPage());
        this.elements.imagesPageBack?.addEventListener('click', () => this.hideImagesPage());
        this.elements.imagesPromptBar?.addEventListener('submit', (event) => {
            event.preventDefault();
            this.submitImagesPrompt();
        });
        this.elements.imagesStylesTrack?.addEventListener('click', (event) => {
            const card = event.target.closest('.images-style-card');
            if (!card) return;
            const prefix = card.dataset.prefix || '';
            this.applyImagesStylePrefix(prefix);
        });
        this.elements.imagesDiscoverGrid?.addEventListener('click', (event) => {
            const item = event.target.closest('.images-discover-item');
            if (!item) return;
            const text = item.dataset.text || '';
            this.applyImagesDiscoverSuggestion(text);
        });
        document.querySelectorAll('[data-carousel-prev]').forEach(btn => {
            btn.addEventListener('click', () => this.scrollImagesCarousel(btn.dataset.carouselPrev, -1));
        });
        document.querySelectorAll('[data-carousel-next]').forEach(btn => {
            btn.addEventListener('click', () => this.scrollImagesCarousel(btn.dataset.carouselNext, 1));
        });
        this.elements.imagesResultDownload?.addEventListener('click', () => this.downloadImagesResult());
        this.elements.imagesResultRegenerate?.addEventListener('click', () => this.regenerateImagesResult());
        this.elements.imagesResultEdit?.addEventListener('click', () => this.editImagesPrompt());
        // Library: Create button + empty CTA toggle the composer
        this.elements.imagesPageCreate?.addEventListener('click', () => this.openImagesComposer());
        this.elements.imagesGalleryEmptyCta?.addEventListener('click', () => this.openImagesComposer());
        this.elements.imagesPromptCancel?.addEventListener('click', () => this.closeImagesComposer());
        // Library: fullscreen viewer controls
        this.elements.imagesViewerClose?.addEventListener('click', () => this.closeImagesViewer());
        this.elements.imagesViewerPrev?.addEventListener('click', () => this.navigateImagesViewer(-1));
        this.elements.imagesViewerNext?.addEventListener('click', () => this.navigateImagesViewer(1));
        this.elements.imagesViewer?.addEventListener('click', (event) => {
            // backdrop click closes (but not clicks on the image, nav, actions)
            if (event.target === this.elements.imagesViewer) {
                this.closeImagesViewer();
            }
        });
        this.elements.imagesViewerDownload?.addEventListener('click', () => this.downloadImagesViewer());
        this.elements.imagesViewerRegenerate?.addEventListener('click', () => this.regenerateFromViewer());
        this.elements.imagesErrorRetry?.addEventListener('click', () => {
            this.setImagesPageMode('home');
            if (this.imagesLastPrompt && this.elements.imagesPromptInput) {
                this.elements.imagesPromptInput.value = this.imagesLastPrompt;
            }
        });
        this.elements.imagePreviewCloseTargets?.forEach(target => {
            target.addEventListener('click', () => this.hideImagePreview());
        });

        this.elements.searchModalInput?.addEventListener('input', (event) => this.handleSearchInput(event.target.value));

        // Keyboard nav: arrow keys move highlight, Enter opens the active row.
        this.elements.searchModalInput?.addEventListener('keydown', (event) => {
            const results = this.elements.searchResults;
            if (!results) return;
            const rows = results.querySelectorAll('.history-search-result');
            if (!rows.length) return;
            let idx = Array.from(rows).findIndex(r => r.classList.contains('is-active'));
            if (idx < 0) idx = 0;

            if (event.key === 'ArrowDown') {
                event.preventDefault();
                const next = Math.min(idx + 1, rows.length - 1);
                rows[idx]?.classList.remove('is-active');
                rows[next].classList.add('is-active');
                rows[next].scrollIntoView({ block: 'nearest' });
            } else if (event.key === 'ArrowUp') {
                event.preventDefault();
                const prev = Math.max(idx - 1, 0);
                rows[idx]?.classList.remove('is-active');
                rows[prev].classList.add('is-active');
                rows[prev].scrollIntoView({ block: 'nearest' });
            } else if (event.key === 'Enter') {
                event.preventDefault();
                const active = rows[idx] || rows[0];
                const sessionId = active?.dataset.sessionId;
                this.closeHistorySearchModal();
                if (sessionId) this.switchSession(sessionId);
            }
        });
        this.elements.searchCloseTargets?.forEach(target => {
            target.addEventListener('click', () => this.closeHistorySearchModal());
        });
        this.elements.imagesGrid?.addEventListener('click', (event) => {
            const card = event.target.closest('.images-gallery-card');
            if (!card) return;
            event.preventDefault();
            const imageId = card.dataset.imageId;
            if (imageId) {
                this.showImagePreview(imageId);
            }
        });
        this.elements.searchResults?.addEventListener('click', (event) => {
            const result = event.target.closest('[data-session-id]');
            if (!result) return;
            const sessionId = result.dataset.sessionId;
            this.closeHistorySearchModal();
            if (sessionId) {
                this.switchSession(sessionId);
            }
        });
        document.addEventListener('keydown', (event) => {
            if (event.key === 'Escape') {
                const activeSendButton = document.querySelector('.send-button');
                if (activeSendButton?.classList.contains('show-stop')) {
                    event.preventDefault();
                    if (typeof abortMessage === 'function') {
                        abortMessage();
                    }
                    return;
                }
            }
            // Library viewer: arrows + Escape
            if (this.isImagesViewerOpen()) {
                if (event.key === 'ArrowLeft') {
                    event.preventDefault();
                    this.navigateImagesViewer(-1);
                    return;
                }
                if (event.key === 'ArrowRight') {
                    event.preventDefault();
                    this.navigateImagesViewer(1);
                    return;
                }
                if (event.key === 'Escape') {
                    event.preventDefault();
                    this.closeImagesViewer();
                    return;
                }
            }
            if (event.key !== 'Escape') return;
            if (this.searchActive) {
                event.preventDefault();
                this.closeHistorySearchModal();
                return;
            }
            if (this.imagesPageActive && this.imagesPageMode !== 'home') {
                event.preventDefault();
                this.setImagesPageMode('home');
                return;
            }
            if (this.imagesPageActive) {
                event.preventDefault();
                this.hideImagesPage();
                return;
            }
            if (this.isPreviewModalOpen()) {
                event.preventDefault();
                this.hideImagePreview();
                return;
            }
            if (this.openTabMenu) {
                event.preventDefault();
                this.closeOpenTabMenu();
            }
        });
        
        // Global click handler for tab menus and actions
        document.addEventListener('click', (e) => {
            const toggleButton = e.target.closest('[data-actions-toggle]');
            if (toggleButton) {
                e.preventDefault();
                e.stopPropagation();
                const tabElement = toggleButton.closest('.chat-tab');
                if (tabElement) {
                    this.toggleTabMenu(tabElement);
                }
                return;
            }

            const target = e.target.closest('[data-action]');
            if (target) {
                const action = target.dataset.action;
                const sessionContainer = target.closest('[data-session-id]');
                const sessionId = sessionContainer ? sessionContainer.dataset.sessionId : null;
            
                if (sessionId && typeof this[action] === 'function') {
                    e.stopPropagation();
                    this.closeOpenTabMenu();
                    this[action](sessionId);
                }
                return;
            }

            if (this.openTabMenu && 
                !e.target.closest('.chat-tab.tab-menu-open') &&
                !e.target.closest('.button-group')) {
                this.closeOpenTabMenu();
            }
        });

        document.addEventListener('sidebar-state-changed', (event) => {
            const collapsed = !!event.detail?.collapsed;
            this.updateSidebarToggleTooltip(collapsed);
        });

        if (this.elements.tabList && !this.tabListBound) {
            this.elements.tabList.addEventListener('click', (event) => {
                if (event.target.closest('.tab-actions')) {
                    return;
                }
                const tab = event.target.closest('.chat-tab[data-session-id]');
                if (!tab) return;
                const sessionId = tab.dataset.sessionId;
                if (!sessionId) return;
                event.preventDefault();
                this.closeOpenTabMenu();
                this.switchSession(sessionId);
            });
            this.tabListBound = true;
        }
        
    }

    updateSidebarToggleTooltip(collapsed = document.body.classList.contains('sidebar-collapsed')) {
        if (!this.elements.sidebarToggleButton) return;
        const tooltip = collapsed ? 'Open sidebar' : 'Close sidebar';
        this.elements.sidebarToggleButton.setAttribute('data-tooltip', tooltip);
        this.elements.sidebarToggleButton.setAttribute('aria-label', tooltip);
    }

    updateGuestFloatingLogin(show) {
        const floatingLogin = document.getElementById('guest-floating-login');
        if (!floatingLogin) return;
        floatingLogin.classList.toggle('visible', show);
    }

    // =======================
    // API Interaction Methods
    // =======================

    /**
     * Fetch data from the API with error handling.
     * @param {string} url - The API endpoint.
     * @param {string} method - HTTP method ('GET', 'POST', etc.).
     * @param {Object|FormData} body - The request payload.
     * @returns {Promise<Object|string>} - The JSON response or text.
     */
    async fetchApi(url, method = 'GET', body = null) {
        const options = {
            method,
            headers: {
                ...(body instanceof FormData ? {} : { 'Content-Type': 'application/json' }),
                'X-CSRFToken': this.csrfToken
            },
            credentials: 'same-origin'
        };

        // Add ETag header for GET requests (cache validation)
        if (method === 'GET' && this.etagCache.has(url)) {
            const cached = this.etagCache.get(url);
            options.headers['If-None-Match'] = cached.etag;
        }

        if (body) {
            options.body = body instanceof FormData ? body : JSON.stringify(body);
        }

        try {
            const response = await fetch(url, options);

            // Handle 304 Not Modified - return cached data
            if (response.status === 304 && this.etagCache.has(url)) {
                return this.etagCache.get(url).data;
            }

            if (!response.ok) {
                const errorText = await response.text();
                throw new Error(errorText || 'API request failed');
            }

            let result;
            if (response.headers.get('content-type')?.includes('application/json')) {
                result = await response.json();
            } else {
                result = await response.text();
            }

            // Cache ETag for GET requests (with size limit)
            if (method === 'GET') {
                const etag = response.headers.get('ETag');
                if (etag) {
                    // Prune oldest entries if cache exceeds limit
                    const MAX_ETAG_CACHE_SIZE = 50;
                    if (this.etagCache.size >= MAX_ETAG_CACHE_SIZE) {
                        const firstKey = this.etagCache.keys().next().value;
                        this.etagCache.delete(firstKey);
                    }
                    this.etagCache.set(url, { etag, data: result });
                }
            }

            return result;
        } catch (error) {
            console.error(`API request to ${url} failed:`, error);
            throw error;
        }
    }

    // =======================
    // UI Rendering Methods
    // =======================

    /**
     * Render the chat tabs based on categorized sessions.
     */
    renderChatTabs() {
        // Reparent/clean up any open tab menu before we null its state or
        // wipe the tab list. toggleTabMenu moves the .button-group into
        // <body>; without this, a re-render orphans it there with live
        // data-action buttons pointing at the now-stale session.
        this.closeOpenTabMenu();

        const categorizedSessions = this.categorizeSessions();
        const tabList = this.elements.tabList;
        this.openTabMenu = null;
        this.openTabMenuButtonGroup = null;

        // Skip for guest mode (no tab list element)
        if (!tabList) return;

        // Clear existing tabs
        tabList.innerHTML = '';

        const hasSessions = Object.values(categorizedSessions).some(list => list.length > 0);

        if (!hasSessions) {
            const emptyMessage = document.createElement('li');
            emptyMessage.className = 'sidebar-empty-state';
            emptyMessage.textContent = 'No chats yet. Start a new conversation.';
            tabList.appendChild(emptyMessage);
            this.addInfiniteScroll();
            return;
        }

        // Claude-style mobile recents: cap visible chats to 30, then a
        // single "All chats" button at the bottom that opens the full
        // /chats page. No infinite-scroll, no progressive loading —
        // the sidebar shows enough to scan, the dedicated page handles
        // the deep history with search + filters.
        const RECENTS_CAP = 30;
        let appended = 0;
        let totalAvailable = 0;
        Object.keys(categorizedSessions).forEach(category => {
            totalAvailable += categorizedSessions[category].length;
        });

        Object.keys(categorizedSessions).forEach(category => {
            const sessions = categorizedSessions[category];
            sessions.forEach((session, index) => {
                if (appended >= RECENTS_CAP) return;
                const tabElement = this.createTabElement(session, index);
                tabList.appendChild(tabElement);
                appended++;
            });
        });

        // "All chats" footer link — show when either (a) we have more
        // sessions locally than the cap renders, or (b) the server
        // reported a next_page_token (so more chats exist beyond what
        // we've fetched). Without (b), we'd silently hide the link
        // even though the user has hundreds of chats on the server.
        const hasServerMore = Boolean(this.nextPageToken);
        const hasLocalMore = totalAvailable > RECENTS_CAP;
        if (hasServerMore || hasLocalMore) {
            const allChats = document.createElement('li');
            allChats.className = 'sidebar-all-chats-link';
            const remaining = hasLocalMore ? totalAvailable - RECENTS_CAP : null;
            const counterHTML = remaining !== null
                ? `<span class="sidebar-all-chats-count">+${remaining}</span>`
                : '';
            allChats.innerHTML = `
                <a href="/chats" class="sidebar-all-chats-btn" data-sidebar-all-chats data-tooltip="All chats">
                    <span class="sidebar-all-chats-icon" aria-hidden="true">
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
                            <circle cx="5" cy="12" r="1.6"/>
                            <circle cx="12" cy="12" r="1.6"/>
                            <circle cx="19" cy="12" r="1.6"/>
                        </svg>
                    </span>
                    <span class="sidebar-all-chats-label">All chats</span>
                    ${counterHTML}
                </a>
            `;
            tabList.appendChild(allChats);
        }

        // NOTE: deliberately not calling addInfiniteScroll() — the cap
        // above replaces the progressive-loading model. The "All chats"
        // link is the canonical path to the rest of the history.

        // Render "+N more in projects" hint
        const moreHint = document.getElementById('project-more-hint');
        if (moreHint) {
            if (this.groupedCount > 0) {
                moreHint.textContent = `+ ${this.groupedCount} more in projects`;
                moreHint.hidden = false;
            } else {
                moreHint.hidden = true;
            }
        }
    }
    
    addInfiniteScroll() {
        const tabList = this.elements.tabList;
    
        // Add a sentinel element at the bottom of the list for infinite scroll
        let sentinel = tabList.querySelector('.scroll-sentinel');
        if (!sentinel) {
            sentinel = document.createElement('div');
            sentinel.className = 'scroll-sentinel';
            tabList.appendChild(sentinel);
        }
    
        // Disconnect any observer from a prior call before creating a new
        // one, otherwise stale observers accumulate across re-renders.
        if (this._infiniteScrollObserver) {
            this._infiniteScrollObserver.disconnect();
        }

        const observer = new IntersectionObserver(entries => {
            entries.forEach(entry => {
                if (entry.isIntersecting && this.nextPageToken) {
                    this.manageLoadMoreSpinner(true); // Show spinner
                    this.loadChatHistory(this.nextPageToken, 10);
                }
            });
        });
        this._infiniteScrollObserver = observer;

        observer.observe(sentinel);
    }

    toggleTabMenu(tabElement) {
        if (!tabElement) return;
        const isOpen = tabElement.classList.contains('tab-menu-open');
        if (isOpen) {
            this.closeTabMenu(tabElement);
            return;
        }
        this.closeOpenTabMenu();
        const toggleButton = tabElement.querySelector('[data-actions-toggle]');
        const buttonGroup = tabElement.__tabMenuGroup || tabElement.querySelector('.button-group');
        if (!toggleButton || !buttonGroup) return;

        tabElement.__tabMenuGroup = buttonGroup;
        if (!buttonGroup.__originParent) {
            buttonGroup.__originParent = buttonGroup.parentElement;
        }

        if (buttonGroup.parentElement !== document.body) {
            document.body.appendChild(buttonGroup);
        }

        const rect = tabElement.getBoundingClientRect();
        const offset = 16;
        const sidebar = document.getElementById('sidebar');
        const isCollapsed = document.body.classList.contains('sidebar-collapsed');
        const sidebarWidth = sidebar ? sidebar.offsetWidth : 0;
        const top = rect.top;
        const left = isCollapsed ? sidebarWidth + offset : rect.right + offset;
        buttonGroup.style.top = `${top}px`;
        buttonGroup.style.left = `${left}px`;
        buttonGroup.classList.add('button-group-visible');

        tabElement.classList.add('tab-menu-open');
        toggleButton.setAttribute('aria-expanded', 'true');
        this.openTabMenu = tabElement;
        this.openTabMenuButtonGroup = buttonGroup;
    }

    closeTabMenu(tabElement) {
        if (!tabElement) return;
        tabElement.classList.remove('tab-menu-open');
        const toggleButton = tabElement.querySelector('[data-actions-toggle]');
        toggleButton?.setAttribute('aria-expanded', 'false');
        const buttonGroup = tabElement.__tabMenuGroup || this.openTabMenuButtonGroup;
        if (buttonGroup) {
            buttonGroup.style.top = '';
            buttonGroup.style.left = '';
            buttonGroup.classList.remove('button-group-visible');
            if (buttonGroup.__originParent && buttonGroup.parentElement !== buttonGroup.__originParent) {
                buttonGroup.__originParent.appendChild(buttonGroup);
            }
        }
        if (this.openTabMenu === tabElement) {
            this.openTabMenu = null;
            this.openTabMenuButtonGroup = null;
        }
    }

    closeOpenTabMenu() {
        if (!this.openTabMenu) return;
        this.closeTabMenu(this.openTabMenu);
    }

    async cleanupTemporarySession() {
        if (!this.temporarySessionId) return;
        const tempSessionId = this.temporarySessionId;
        this.temporarySessionId = null;
        this.temporaryCentered = false;
        this.isTemporaryUiActive = false;

        // temp_ sessions are never persisted server-side; skip the /chat/delete
        // round-trip (would return 404 "Chat session not found") and clean up
        // local state only.
        const isTempPrefixed = typeof tempSessionId === 'string' && tempSessionId.startsWith('temp_');

        try {
            if (!isTempPrefixed) {
                await this.fetchApi('/chat/delete', 'POST', { session_id: tempSessionId });
            }
        } catch (error) {
            console.error('Failed to remove temporary chat session', error);
        } finally {
            delete this.chatHistory[tempSessionId];
            this.dirtySessions.delete(tempSessionId);
            if (this.activeSessionId === tempSessionId) {
                this.activeSessionId = null;
            }
            this.renderChatTabs();
            this.updateTemporaryUiState();
            this.resetComposerPosition();
        }
    }

    updateTemporaryUiState() {
        if (typeof document === 'undefined') return;
        const root = document.body;
        if (!root) return;
        const isTempActive = this.isTemporaryUiActive;
        const isCentered = isTempActive && this.temporaryCentered;
        root.classList.toggle('temporary-chat-mode', isTempActive);
        root.classList.toggle('temporary-chat-centered', isCentered);

        if (this.elements.optionsPanel) {
            this.elements.optionsPanel.style.display = isTempActive ? 'none' : '';
        }

        if (this.elements.tempChatButton) {
            this.elements.tempChatButton.style.display = isTempActive ? 'none' : '';
        }

        if (isCentered) {
            this.positionComposerCentered();
            if (!this.tempResizeAttached) {
                window.addEventListener('resize', this.handleTemporaryResize, { passive: true });
                this.tempResizeAttached = true;
            }
        } else {
            if (!isTempActive) {
                this.suppressComposerTransitions();
            }
            this.resetComposerPosition();
            if (this.tempResizeAttached) {
                window.removeEventListener('resize', this.handleTemporaryResize);
                this.tempResizeAttached = false;
            }
        }
    }

    suppressComposerTransitions() {
        if (typeof document === 'undefined') return;
        const root = document.body;
        if (!root) return;
        root.classList.add('composer-transition-skip');
        if (this.composerTransitionTimer) {
            clearTimeout(this.composerTransitionTimer);
        }
        this.composerTransitionTimer = setTimeout(() => {
            root.classList.remove('composer-transition-skip');
            this.composerTransitionTimer = null;
        }, 120);
    }

    prepareTemporaryChatUi() {
        // Remove has-messages class for fresh chat layout
        if (window.setHasMessages) window.setHasMessages(false);
        else document.body.classList.remove('has-messages');

        if (typeof window !== 'undefined' && typeof window.clearChat === 'function') {
            window.clearChat({ force: true });
        } else if (this.elements.chatDisplay) {
            this.elements.chatDisplay.innerHTML = '';
        }
        if (this.elements.messageInput) {
            this.elements.messageInput.value = '';
        }
        this.updateFirstMessageFlag(false);

        // Reset scroll position for mobile
        const scrollRegion = document.getElementById('chat-scroll-region');
        if (scrollRegion) {
            scrollRegion.scrollTop = 0;
        }

        // Reset scroll state (hides down arrow)
        if (typeof window.resetScrollState === 'function') {
            window.resetScrollState();
        }
    }

    enterHomeView() {
        this.isHomeView = true;
        this.pendingSessionPromise = null;
        this.activeSessionId = null;
        this.temporarySessionId = null;
        this.isTemporaryUiActive = false;
        this.temporaryCentered = false;
        this.updateSessionUrl(null);
        this.updatePageTitle(null);
        this.updateTemporaryUiState();

        // Close artifact panel when entering home view
        if (window.artifactPanel?.close) window.artifactPanel.close();

        // Remove has-messages class for home page layout
        if (window.setHasMessages) window.setHasMessages(false);
        else document.body.classList.remove('has-messages');

        // Reset scroll state (hides down arrow) when entering home view
        if (typeof window.resetScrollState === 'function') {
            window.resetScrollState();
        }

        if (typeof window !== 'undefined' && typeof window.clearChat === 'function') {
            window.clearChat({ force: true });
        } else if (this.elements.chatDisplay) {
            this.elements.chatDisplay.innerHTML = '';
        }

        if (this.elements.messageInput) {
            this.elements.messageInput.value = '';
        }

        if (typeof window !== 'undefined' && typeof window.clearAttachedFiles === 'function') {
            window.clearAttachedFiles();
        }

        if (typeof window !== 'undefined' && typeof window.resetEphemeralOptionsForNewChat === 'function') {
            window.resetEphemeralOptionsForNewChat();
        }
        if (typeof window !== 'undefined' && typeof window.refreshGreeting === 'function') {
            window.refreshGreeting();
        }

        this.updateFirstMessageFlag(false);
        this.renderChatTabs();
        this.resetComposerPosition();
    }

    async ensureActiveSession(options = {}) {
        if (this.activeSessionId && this.chatHistory[this.activeSessionId]) {
            this.isHomeView = false;
            return this.activeSessionId;
        }

        if (this.pendingSessionPromise) {
            await this.pendingSessionPromise;
            if (this.activeSessionId && this.chatHistory[this.activeSessionId]) {
                this.isHomeView = false;
                return this.activeSessionId;
            }
            throw new Error('Failed to establish chat session');
        }

        const shouldUseTemporary = typeof options.temporary === 'boolean'
            ? options.temporary
            : Boolean(this.isTemporaryUiActive);

        this.pendingSessionPromise = this.createNewChat(null, {
            showOverlay: false,
            notifySuccess: false,
            temporary: shouldUseTemporary
        });

        try {
            await this.pendingSessionPromise;
        } finally {
            this.pendingSessionPromise = null;
        }

        if (!this.activeSessionId || !this.chatHistory[this.activeSessionId]) {
            this.showNotification('Unable to start a new chat. Please try again.', 'error');
            throw new Error('Failed to establish chat session');
        }

        this.isHomeView = false;
        return this.activeSessionId;
    }

    positionComposerCentered() {
        const inputContainer = this.elements.inputContainer;
        if (!inputContainer) return;
        const leftSidebar = document.getElementById('sidebar');
        const rightSidebar = document.getElementById('right-sidebar');
        const rightOpen = rightSidebar && !rightSidebar.classList.contains('closed');
        const leftWidth = leftSidebar ? leftSidebar.offsetWidth : 0;
        const rightWidth = rightOpen ? rightSidebar.offsetWidth : 0;
        const viewportWidth = window.innerWidth;
        const usableWidth = Math.max(0, viewportWidth - leftWidth - rightWidth);
        const maxWidth = Math.min(usableWidth - 32, 720);
        const width = Math.max(360, maxWidth);
        const centerX = leftWidth + usableWidth / 2;

        inputContainer.style.width = `${width}px`;
        inputContainer.style.left = `${centerX}px`;
        inputContainer.style.right = 'auto';
        inputContainer.style.transform = 'translate(-50%, -50%)';
    }

    resetComposerPosition() {
        const inputContainer = this.elements.inputContainer;
        if (!inputContainer) return;
        inputContainer.style.width = '';
        inputContainer.style.left = '';
        inputContainer.style.right = '';
        inputContainer.style.transform = '';
        if (window.sidebarManager?.updateInputContainerPosition) {
            window.sidebarManager.updateInputContainerPosition();
        } else {
            this.alignComposerToChatbox();
        }
    }

    alignComposerToChatbox() {
        const inputContainer = this.elements.inputContainer;
        const chatbox = document.querySelector('.chatbox');
        if (!inputContainer || !chatbox) return;
        const rect = chatbox.getBoundingClientRect();
        const width = rect.width;
        const centerX = rect.left + width / 2;
        requestAnimationFrame(() => {
            inputContainer.style.width = `${width}px`;
            inputContainer.style.left = `${centerX}px`;
            inputContainer.style.right = 'auto';
            inputContainer.style.transform = 'translate(-50%, 0)';
        });
    }

    handleTemporaryPromptSent() {
        if (!this.temporarySessionId || !this.temporaryCentered) return;

        // Animate the input from center to bottom (like ChatGPT)
        // Step 1: Add animation class while still in centered state
        document.body.classList.add('composer-animating-to-bottom');

        // Step 2: After animation completes, switch to normal state
        setTimeout(() => {
            document.body.classList.remove('composer-animating-to-bottom');
            this.temporaryCentered = false;
            this.updateTemporaryUiState();
        }, 420); // Match the 0.4s CSS transition
    }
    
    manageLoadMoreSpinner(show, type = 'default', position = 'bottom') {
        const tabList = this.elements.tabList;
        if (!tabList) return; // Skip for guest mode
        let spinner = tabList.querySelector(`.load-more-spinner-container.${position}`);
    
        if (show) {
            if (!spinner) {
                // Create and append spinner container
                const spinnerContainer = document.createElement('div');
                spinnerContainer.className = `load-more-spinner-container ${position} ${type}`;
    
                // Customize spinner styles based on type
                const spinner = document.createElement('div');
                spinner.className = `load-more-spinner ${type}`;
                
                // Optional: Add additional styling based on type
                switch (type) {
                    case 'dots':
                        spinner.innerHTML = '<span class="dot"></span><span class="dot"></span><span class="dot"></span>';
                        break;
                    case 'circle':
                        spinner.classList.add('spinner-circle');
                        break;
                    default:
                        // Keep the default spinner
                        break;
                }
    
                spinnerContainer.appendChild(spinner);
    
                // Position the spinner at top or bottom
                if (position === 'top') {
                    tabList.insertBefore(spinnerContainer, tabList.firstChild);
                } else {
                    tabList.appendChild(spinnerContainer);
                }
            }
        } else if (spinner) {
            // Remove spinner
            spinner.remove();
        }
    }
    
    
    loadChatHistory(nextPageToken, limit) {
        // Simulate an API call to load additional chat history
        setTimeout(() => {
            const newSessions = this.fetchMoreSessions(nextPageToken, limit);
            if (newSessions) {
                this.appendSessions(newSessions);
            }
    
            // Remove spinner after data is loaded
            this.manageLoadMoreSpinner(false);
        }, 2000); // Simulate network delay
    }
    
    appendSessions(newSessions) {
        const tabList = this.elements.tabList;
        const sentinel = tabList.querySelector('.scroll-sentinel');
    
        newSessions.forEach((session, index) => {
            const tabElement = this.createTabElement(session, index);
            tabList.insertBefore(tabElement, sentinel);
        });
    
        // Update nextPageToken (simulate API response)
        this.nextPageToken = this.getNextPageToken();
    }
    
    // Mock methods for demonstration
    fetchMoreSessions(nextPageToken, limit) {
        // Replace with your data fetching logic
        return [
            { id: 'session1', name: 'New Session 1' },
            { id: 'session2', name: 'New Session 2' },
            // Add more sessions as needed
        ];
    }
    
    getNextPageToken() {
        // Replace with your logic to get the next page token
        return null; // Return null when no more pages are available
    }
    
    
    /**
     * Create a tab element for a specific chat session.
     * @param {Object} session - The session object.
     * @param {number} index - The index of the session.
     * @returns {HTMLElement} - The constructed tab element.
     */
    createTabElement(session, index) {
        const li = document.createElement('li');
        li.className = 'chat-tab';
        li.dataset.sessionId = session.session_id;
        if (session.session_id === this.activeSessionId) {
            li.classList.add('active-tab');
        }
        // ChatGPT-style: only show dot for in-memory background completions (not server polling)
        const hasInMemoryNotification = window.BackgroundRequestManager?.hasPendingNotification?.(session.session_id);
        if (hasInMemoryNotification) {
            li.classList.add('has-notification');
        }

        const safeName = this.escapeHtml(session.session_name || `Chat ${index + 1}`);
        const isPinned = Boolean(session.is_pinned || session.pinned_at);
        if (isPinned) {
            li.classList.add('pinned-tab');
        }
        const toggleButtonClass = `tab-actions-toggle${isPinned ? ' pinned-toggle' : ''}`;
        li.innerHTML = `
            <div class="tab-content">
                <span class="tab-name">${safeName}</span>
                <div class="tab-actions">
                    <span class="session-notification-dot" title="New messages"></span>
                    <button class="${toggleButtonClass}" type="button" data-actions-toggle aria-label="Show chat actions" aria-expanded="false">
                        <i class="fas fa-thumbtack tab-pin-icon" aria-hidden="true"></i>
                        <i class="fas fa-ellipsis-h tab-ellipsis-icon" aria-hidden="true"></i>
                    </button>
                    <div class="button-group" role="menu" data-session-id="${session.session_id}">
                        <button class="chat-btn share-btn" data-action="shareSession" title="Share" role="menuitem">
                            <i class="fas fa-share-alt" aria-hidden="true"></i>
                            <span>Share</span>
                        </button>
                        <button class="chat-btn pin-btn" data-action="pinSession" title="${isPinned ? 'Unpin chat' : 'Pin chat'}" role="menuitem">
                            <i class="fas fa-thumbtack" aria-hidden="true"></i>
                            <span>${isPinned ? 'Unpin chat' : 'Pin chat'}</span>
                        </button>
                        <button class="chat-btn archive-btn" data-action="archiveSession" title="Archive" role="menuitem">
                            <i class="fas fa-box-archive" aria-hidden="true"></i>
                            <span>Archive</span>
                        </button>
                        <button class="chat-btn rename-btn" data-action="renameSession" title="Rename" role="menuitem">
                            <i class="fas fa-edit" aria-hidden="true"></i>
                            <span>Rename</span>
                        </button>
                        <button class="chat-btn add-to-project-btn" data-action="addToProjectSession" title="Add to project" role="menuitem">
                            <i class="fas fa-folder" aria-hidden="true"></i>
                            <span>Add to project</span>
                        </button>
                        <button class="chat-btn delete-btn" data-action="deleteSession" title="Delete" role="menuitem">
                            <i class="fas fa-trash" aria-hidden="true"></i>
                            <span>Delete</span>
                        </button>
                    </div>
                </div>
            </div>
        `;

        return li;
    }

    /**
     * Render chat content for the active session.
     * @param {string} sessionId - The session ID.
     */
    renderChatContent(sessionId) {
        const messages = this.chatHistory[sessionId]?.messages || [];

        this.elements.chatDisplay.innerHTML = messages.map(message => `
            <div class="chat-message">
                ${this.escapeHtml(message.content || '')}
            </div>
        `).join('');

        // Scroll to the bottom after layout settles
        const scrollContainer = document.getElementById('chat-scroll-region');
        if (scrollContainer) {
            requestAnimationFrame(() => {
                scrollContainer.scrollTop = scrollContainer.scrollHeight;
                requestAnimationFrame(() => {
                    scrollContainer.scrollTop = scrollContainer.scrollHeight;
                });
            });
        }
    }

/**
 * Load chat history from the server with pagination support.
 * @param {string|null} pageToken - The token for the next page.
 * @param {number} limit - Number of sessions to load per request.
 */
async loadChatHistory(pageToken = null, limit = 30) {
    // Skip for guest mode (no chat history)
    if (document.body.dataset.guestMode === 'true') return;

    try {
        // Show the spinner
        this.manageLoadMoreSpinner(true, 'circle', 'bottom');

        const params = new URLSearchParams({ page_size: limit });
        if (pageToken) {
            params.set('page_token', pageToken);
        }
        const projectMatch = window.location.pathname.match(/^\/p\/([^/?]+)/);
        if (projectMatch) {
            params.set('project_id', projectMatch[1]);
        } else {
            params.set('ungrouped', '1');
        }
        const url = `/chat/history?${params.toString()}`;

        const response = await this.fetchApi(url, 'GET');

        if (!response || !response.categorized_history) {
            throw new Error("Invalid response structure from server.");
        }

        // On first load, reset chat history
        if (!pageToken) {
            this.chatHistory = {};
            this.dirtySessions.clear();
        }

        // Process each category and its sessions
        Object.entries(response.categorized_history).forEach(([category, sessions]) => {
            sessions.forEach(session => {
                this.chatHistory[session.session_id] = {
                    session_name: session.session_name || 'Untitled Session',
                    created_at: session.created_at,
                    last_updated: session.last_updated || session.created_at,
                    messages: session.messages || [],
                    pages: Array.isArray(session.pages) ? session.pages : [],
                    activePageId: session.active_page_id || session.activePageId || null,
                    autoRenameQueued: false,
                    manuallyRenamed: false,
                    autoTitleEligible: true,
                    model_label: session.model_label || session.model || '',
                    status: session.status || 'active',
                    pinned_at: session.pinned_at || null,
                    is_pinned: Boolean(session.pinned_at),
                    is_temporary: Boolean(session.is_temporary) || session.status === 'temporary',
                    project_id: session.project_id || null,
                };
                this.ensureSessionPages(session.session_id);
                const localSession = this.chatHistory[session.session_id];
                const isGenericName = this.isGenericName(localSession.session_name);
                localSession.autoTitleEligible = isGenericName;
                localSession.manuallyRenamed = !isGenericName;
            });
        });

        // Store next page token
        this.nextPageToken = response.next_page_token;

        // Cache grouped count for the "+N more in projects" hint
        if (!pageToken) {
            this.groupedCount = response.grouped_count || 0;
        }

        // Render the tabs
        this.renderChatTabs();
        if (this.searchActive) {
            this.renderSearchResults();
        }
        this.primeAutoRenameForExistingSessions();
        this.hydrateImagesFromHistory();

        // Apply persisted unread notification states on initial load
        if (!pageToken && window.UnreadNotificationManager?.fetchUnreadCounts) {
            window.UnreadNotificationManager.fetchUnreadCounts();
        }

        if (!pageToken) {
            const sharedId = (window.sharedSessionId || '').trim();
            if (sharedId) {
                let ensureLoaded = Boolean(this.chatHistory[sharedId]);
                if (!ensureLoaded) {
                    ensureLoaded = await this.ensureSessionLoaded(sharedId);
                }
                if (ensureLoaded) {
                    window.sharedSessionId = '';
                    await this.switchSession(sharedId, true);
                    return;
                } else {
                    this.showNotification('Unable to open the shared session.', 'error');
                }
            }
            if (!this.activeSessionId) {
                this.enterHomeView();
            }
        }

    } catch (error) {
        this.showNotification('Failed to load chat history', 'error');
        console.error("Failed to load chat history:", error);
    } finally {
        // Hide the spinner
        this.manageLoadMoreSpinner(false, 'circle', 'bottom');
    }
}

    /**
     * Ensure a specific session is loaded into local history.
     * @param {string} sessionId
     * @returns {Promise<boolean>}
     */
    async ensureSessionLoaded(sessionId) {
        if (!sessionId) return false;
        if (this.chatHistory[sessionId]) return true;
        // Temp ("incognito") sessions are unreachable post-reload by design —
        // they have no Firestore document. Match ChatGPT's behavior: a
        // temp chat can't be reopened. Don't fetch (would 404), don't
        // toast — just signal "not loaded" and let the caller fall
        // through to its no-session path.
        if (sessionId.startsWith('temp_') || sessionId.startsWith('guest_')) {
            return false;
        }
        try {
            const response = await this.fetchApi(`/chat/session/${sessionId}`, 'GET');
            const data = response?.session;
            if (!data) {
                return false;
            }
            const sessionName = data.session_name || 'Untitled Session';
            const isGeneric = this.isGenericName(sessionName);
            this.chatHistory[sessionId] = {
                session_name: sessionName,
                created_at: data.created_at,
                messages: Array.isArray(data.messages) ? data.messages : [],
                pages: Array.isArray(data.pages) ? data.pages : [],
                activePageId: data.active_page_id || data.activePageId || null,
                autoRenameQueued: false,
                manuallyRenamed: !isGeneric,
                autoTitleEligible: isGeneric,
                model_label: data.model_label || data.model || '',
                status: data.status || 'active',
                pinned_at: data.pinned_at || null,
                is_pinned: Boolean(data.pinned_at),
                is_temporary: Boolean(data.is_temporary),
                project_id: data.project_id || null,
            };
            this.ensureSessionPages(sessionId);
            this.renderChatTabs();
            return true;
        } catch (error) {
            console.error('Failed to load shared session:', error);
            return false;
        }
    }

    /**
     * Categorize sessions based on their creation date.
     * @returns {Object} - Categorized sessions.
     */
    categorizeSessions() {
        const today = new Date();
        const yesterday = new Date(today);
        yesterday.setDate(today.getDate() - 1);

        const categorizedSessions = {
            "Today": [],
            "Yesterday": [],
            "Last 7 Days": [],
            "Older": []
        };

        const sessionsArray = this.getSortedSessionsArray();

        sessionsArray.forEach(sessionObj => {
            const { sessionDate } = sessionObj;

            if (sessionDate.toDateString() === today.toDateString()) {
                categorizedSessions["Today"].push(sessionObj);
            } else if (sessionDate.toDateString() === yesterday.toDateString()) {
                categorizedSessions["Yesterday"].push(sessionObj);
            } else if ((today - sessionDate) / (1000 * 60 * 60 * 24) <= 7) {
                categorizedSessions["Last 7 Days"].push(sessionObj);
            } else {
                categorizedSessions["Older"].push(sessionObj);
            }
        });

        return categorizedSessions;
    }

    getSortedSessionsArray() {
        return Object.entries(this.chatHistory)
            .map(([sessionId, session]) => {
                // Use last_updated for sorting (most recent activity), fallback to created_at
                const activityDate = session.last_updated || session.created_at;
                const sessionDate = activityDate ? new Date(activityDate) : new Date();
                const pinnedDate = session.pinned_at ? new Date(session.pinned_at) : null;
                return {
                    ...session,
                    session_id: sessionId,
                    sessionDate,
                    pinnedDate
                };
            })
            .filter(session => session.status !== 'archived' && session.status !== 'temporary')
            .sort((a, b) => {
                const aPinned = Boolean(a.pinnedDate);
                const bPinned = Boolean(b.pinnedDate);
                if (aPinned !== bPinned) {
                    return aPinned ? -1 : 1;
                }
                if (aPinned && bPinned) {
                    return b.pinnedDate - a.pinnedDate;
                }
                return b.sessionDate - a.sessionDate;
            });
    }

    filterSessionsByQuery(sessions) {
        const query = (this.searchQuery || '').trim().toLowerCase();
        if (!query) return sessions;

        return sessions.filter(session => {
            const nameMatch = session.session_name?.toLowerCase().includes(query);
            if (nameMatch) return true;

            if (Array.isArray(session.messages)) {
                return session.messages.some(message => this.messageMatchesQuery(message, query));
            }

            return false;
        });
    }

    messageMatchesQuery(message, query) {
        const candidates = this.collectMessageStrings(message);
        return candidates.some(text => typeof text === 'string' && text.toLowerCase().includes(query));
    }

    /**
     * Switch to a different chat session.
     * @param {string} sessionId - The session ID to switch to.
     */
    async switchSession(sessionId, forceReload = false) {
        // If we're currently in the inline project view (chat-shell hidden),
        // clicking a chat must full-navigate so the user actually sees it.
        if (document.querySelector('.project-page')) {
            window.location.href = `/c/${encodeURIComponent(sessionId)}`;
            return;
        }

        // If we're not forcing and the session is already active, simply return.
        if (!forceReload && this.activeSessionId === sessionId) return;
        if (this.imagesPageActive) this.hideImagesPage();
        if (this.chatsPageActive) this.hideChatsPage();

        // Check if target session has an active background request
        const bgManager = window.BackgroundRequestManager;
        const targetHasActiveRequest = bgManager?.hasActiveRequest?.(sessionId);
        const targetHasPendingNotification = bgManager?.hasPendingNotification?.(sessionId);
        const targetRequest = bgManager?.getRequest?.(sessionId);

        console.log('switchSession debug:', {
            sessionId,
            targetHasActiveRequest,
            targetHasPendingNotification,
            targetRequestStatus: targetRequest?.status,
            hasDisplayContent: !!targetRequest?.displayContent
        });

        // Close artifact panel when switching sessions
        if (window.artifactPanel?.close) window.artifactPanel.close();

        // ALWAYS hide thinking indicator first when switching sessions
        // It will be shown again if target session has active streaming
        if (typeof window.ThinkingIndicator !== 'undefined' && window.ThinkingIndicator.hide) {
            window.ThinkingIndicator.hide();
        }

        // Reset send button state for the new session
        if (typeof window.updateSendButton === 'function') {
            // Show sending state if target session has active request, otherwise normal
            window.updateSendButton(targetHasActiveRequest);
        }

        // ChatGPT-style: clear notification instantly on switch (no scroll waiting)
        this.markSessionReadNow(sessionId);

        // Reset scroll state (hides down arrow) when switching chats
        if (typeof window.resetScrollState === 'function') {
            window.resetScrollState();
        }

        const requestEpoch = ++this.switchEpoch;
        if (this.temporarySessionId && sessionId !== this.temporarySessionId) {
            await this.cleanupTemporarySession();
            if (requestEpoch !== this.switchEpoch) return;
        }

        const previousSessionId = this.activeSessionId;
        const shouldAutoSave = this.autoSaveEnabled &&
            previousSessionId &&
            !forceReload &&
            this.hasUnsavedChanges(previousSessionId);

        if (shouldAutoSave) {
            this.saveSession(previousSessionId, { silent: true }).catch(error => {
                console.error('Failed to auto-save chat before switching', error);
            });
            if (requestEpoch !== this.switchEpoch) return;
        }

        // CRITICAL: Tell server about session switch FIRST, before updating frontend state
        // This prevents race condition where message is sent with wrong session cookie
        try {
            await this.fetchApi('/chat/select', 'POST', { session_id: sessionId });
        } catch (error) {
            console.error("Failed to select session on server:", error);
            // Don't proceed if server selection failed
            this.showNotification('Failed to switch chat session', 'error');
            return;
        }
        if (requestEpoch !== this.switchEpoch) return;

        // Verify the session exists in local history BEFORE mutating
        // activeSessionId / URL / title. Otherwise a miss leaves partial
        // state pointing at a session that was never loaded, which later
        // null-derefs (e.g. in displayMessage's chatHistory push).
        let sessionData = this.chatHistory[sessionId];
        if (!sessionData) {
            this.showNotification('Failed to switch chat session', 'error');
            return;
        }

        this.activeSessionId = sessionId;
        this.updateSessionUrl(sessionId);
        this.updatePageTitle(sessionId);
        this.pauseAutoScroll();

        try {
            // Clear any existing chat UI to avoid mixing sessions.
            const cleared = typeof window !== 'undefined' && typeof window.clearChat === 'function';
            if (cleared) {
                window.clearChat({ force: true });
            } else if (this.elements.chatDisplay) {
                this.elements.chatDisplay.innerHTML = "";
            }
            if (requestEpoch !== this.switchEpoch) return;

            this.ensureSessionPages(sessionId);
            if (requestEpoch !== this.switchEpoch) return;

            if (window._streamRendererReadyPromise && typeof window.StreamRenderer !== 'function') {
                await Promise.race([
                    window._streamRendererReadyPromise,
                    new Promise(r => setTimeout(r, 500))
                ]);
            }
            this.renderActivePage(sessionId);
            if (requestEpoch !== this.switchEpoch) return;
    
            // Update the active session ID and re-render the tabs.
            this.isHomeView = false;
            this.renderChatTabs();
            if (requestEpoch !== this.switchEpoch) return;

            // Ensure the chat display is scrolled to the bottom.
            this.scrollChatToLatest();

            // Recover from a refresh-during-image-generation. If localStorage
            // has pending markers for this session (set at placeholder
            // creation in codeWindow.js's stream loop), the backend may have
            // finished the image while the page was reloading. Schedule one
            // delayed re-fetch to pick up the persisted image from Firestore.
            try {
                if (typeof listRecoverablePendingImages === 'function') {
                    const pending = listRecoverablePendingImages(sessionId);
                    if (pending.length) {
                        setTimeout(() => {
                            if (this.activeSessionId !== sessionId) return;
                            // Re-fetch this session and re-render if the
                            // pending images have landed on the server.
                            if (typeof this.fetchSession === 'function') {
                                this.fetchSession(sessionId, { silent: true })
                                    .then(() => this.renderActivePage(sessionId))
                                    .catch(() => { /* best-effort */ });
                            }
                        }, 15000);
                    }
                }
            } catch (e) { /* recovery is best-effort */ }

            const hasMessages = Array.isArray(sessionData.messages) && sessionData.messages.length > 0;
            this.updateFirstMessageFlag(hasMessages);

            // Update the in-header project context tag.
            if (typeof window.updateChatProjectTag === 'function') {
                window.updateChatProjectTag(sessionData);
            }
            if (typeof window !== 'undefined') {
                if (hasMessages && typeof window.dismissEphemeralOptions === 'function') {
                    window.dismissEphemeralOptions();
                } else if (!hasMessages && typeof window.resetEphemeralOptionsForNewChat === 'function') {
                    window.resetEphemeralOptionsForNewChat();
                }
                if (typeof window.updateEphemeralOptionsVisibility === 'function') {
                    window.updateEphemeralOptionsVisibility();
                }
                // Refresh greeting when switching to empty chat
                if (!hasMessages && typeof window.refreshGreeting === 'function') {
                    window.refreshGreeting();
                }
            }

            // Handle background request state for this session
            if (bgManager) {
                const request = bgManager.getRequest?.(sessionId);
                console.log('Background request check:', {
                    hasRequest: !!request,
                    status: request?.status,
                    hasDisplayContent: !!request?.displayContent,
                    targetHasPendingNotification
                });

                if (request) {
                    console.log('Request found, status:', request.status, 'model:', request.model);

                    if (request.status === 'streaming' || request.status === 'pending') {
                        // Active request - resume live view
                        console.log('RESUMING live streaming for session:', sessionId);
                        // Show thinking indicator with buffered reasoning
                        if (typeof window.ThinkingIndicator !== 'undefined') {
                            const isDeepSeekR1 = request.model === 'deepseek-v4-pro' || request.model === 'deepseek-v4-flash' || request.model === 'deepseek-reasoner';
                            console.log('Showing ThinkingIndicator, streamMode:', isDeepSeekR1);
                            window.ThinkingIndicator.show(isDeepSeekR1);
                            if (request.reasoning && request.reasoning.length > 0) {
                                console.log('Appending buffered reasoning:', request.reasoning.length, 'chars');
                                window.ThinkingIndicator.appendReasoning(request.reasoning);
                            }
                            if (request.content && request.content.length > 0) {
                                console.log('Setting content mode');
                                window.ThinkingIndicator.setContentMode();
                            }
                        } else {
                            console.warn('ThinkingIndicator not available!');
                        }
                    } else if (request.status === 'completed' && request.displayContent) {
                        // Completed request with buffered content - display it
                        console.log('Displaying buffered response for session:', sessionId);
                        this.displayBufferedResponse(sessionId, request);
                    } else {
                        console.log('Request status not handled:', request.status);
                    }
                } else {
                    console.log('No background request found for session');
                }
            }
            // Server was already notified at start of switchSession (before activeSessionId set)
        } catch (error) {
            console.error("Error during session switch:", error);
        } finally {
            this.resumeAutoScroll();
        }
    }

    /**
     * Display a buffered response from a background request.
     * @param {string} sessionId - The session ID.
     * @param {Object} request - The completed request object from BackgroundRequestManager.
     */
    displayBufferedResponse(sessionId, request) {
        console.log('displayBufferedResponse called:', {
            sessionId,
            hasRequest: !!request,
            displayContentLength: request?.displayContent?.length,
            hasArtifact: !!request?.artifact
        });

        if (!request || !request.displayContent) {
            console.warn('No buffered response to display for session:', sessionId);
            return;
        }

        try {
            const {
                displayContent,
                reasoning,
                artifact,
                isDeepSeekR1,
                prompt
            } = request;

            // Generate message ID
            const aiMsgId = typeof window.generateMessageId === 'function'
                ? window.generateMessageId()
                : `msg-${Date.now()}`;

            // Display the message
            const hasCode = displayContent.includes('```');
            const messageContainer = typeof window.displayMessage === 'function'
                ? window.displayMessage(
                    displayContent,
                    'ai',
                    hasCode ? 'code' : 'text',
                    prompt,
                    true,
                    aiMsgId
                )
                : null;

            // Add reasoning section if available (only for R1 model)
            if (isDeepSeekR1 && reasoning && messageContainer && typeof window.ReasoningDisplay !== 'undefined') {
                window.ReasoningDisplay.create(reasoning, messageContainer);
            }

            // Attach artifact preview if present
            if (artifact && messageContainer && window.artifactPanel) {
                const artifactRef = {
                    artifactId: artifact.artifactId,
                    title: artifact.title,
                    type: artifact.type,
                    primaryLanguage: artifact.primaryLanguage,
                    summary: displayContent
                };
                window.artifactPanel.attachPreview(messageContainer, artifactRef, {
                    payload: artifact
                });
            }

            // Scroll to bottom (respect user scroll intent)
            if (typeof window.scrollManager !== 'undefined' && window.scrollManager.scrollToBottom) {
                window.scrollManager.scrollToBottom(true, false); // immediate but not forced
            }

            // Persist the buffered reply into local chat history. It was only
            // rendered to the DOM above; without this it would vanish on the
            // next re-render and never be saved. Match the message shape used
            // elsewhere in chatHistory (sender / content / timestamp).
            const targetSession = this.chatHistory[sessionId];
            if (targetSession) {
                if (!Array.isArray(targetSession.messages)) {
                    targetSession.messages = [];
                }
                const now = new Date().toISOString();
                targetSession.messages.push({
                    sender: 'AI',
                    content: displayContent,
                    timestamp: now
                });
                targetSession.last_updated = now;
                if (typeof this.markSessionDirty === 'function') {
                    this.markSessionDirty(sessionId);
                }
            }

            // Clear the request from BackgroundRequestManager
            if (window.BackgroundRequestManager?.clearRequest) {
                window.BackgroundRequestManager.clearRequest(sessionId);
            }

            console.log('Displayed buffered response for session:', sessionId);
        } catch (error) {
            console.error('Failed to display buffered response:', error);
        }
    }

    async createNewChat(initialPrompt = null, options = {}) {
        const {
            showOverlay = false,
            notifySuccess,
            temporary = false,
            project_id = null
        } = options || {};
        const isTemporary = Boolean(temporary);
        const shouldNotify = typeof notifySuccess === 'boolean' ? notifySuccess : !isTemporary;
        const overlay = showOverlay ? this.showLoadingOverlay() : null;
        const previousTempState = {
            isActive: this.isTemporaryUiActive,
            isCentered: this.temporaryCentered
        };

        try {
            if (this.temporarySessionId) {
                await this.cleanupTemporarySession();
            }

            if (isTemporary) {
                if (!this.isTemporaryUiActive) {
                    this.isTemporaryUiActive = true;
                    this.temporaryCentered = true;
                }
                this.updateTemporaryUiState();
                this.prepareTemporaryChatUi();
            }

            const sessionName = initialPrompt
                ? this.generateSessionName(initialPrompt)
                : "New Chat";
    
            const response = await this.fetchApi('/chat/new', 'POST', {
                session_name: sessionName,
                temporary: isTemporary,
                project_id: project_id || null
            });
    
            if (response.session_id) {
                const sessionId = response.session_id;

                // Create a new chat session with an empty messages array.
                const now = new Date().toISOString();
                this.chatHistory[sessionId] = {
                    session_name: sessionName,
                    created_at: now,
                    last_updated: now,
                    messages: [],
                    pages: [],
                    activePageId: null,
                    autoRenameQueued: false,
                    manuallyRenamed: false,
                    autoTitleEligible: this.isGenericName(sessionName),
                    status: isTemporary ? 'temporary' : 'active',
                    is_temporary: isTemporary,
                    project_id: project_id || null
                };
                // activeSessionId is the source of truth for the form-sent
                // session_id on chat POSTs (codeWindow.js reads it). MUST be
                // updated for both temp and non-temp paths — otherwise the
                // backend session-reconciliation logic sees a stale
                // session_id from the previous active chat and overwrites
                // the just-created cookie session, defeating the entire
                // temp-chat ephemeral promise.
                this.activeSessionId = sessionId;
                if (isTemporary) {
                    this.temporarySessionId = sessionId;
                    this.isTemporaryUiActive = true;
                    this.temporaryCentered = true;
                } else {
                    this.temporarySessionId = null;
                    this.isTemporaryUiActive = false;
                    this.temporaryCentered = false;
                }
                this.updateTemporaryUiState();
                this.isHomeView = false;
    
                // Clear the chat display and the message input.
                if (typeof window !== 'undefined' && typeof window.clearChat === 'function') {
                    window.clearChat({ force: true });
                } else if (this.elements.chatDisplay) {
                    this.elements.chatDisplay.innerHTML = "";
                }
                if (this.elements.messageInput) {
                    this.elements.messageInput.value = "";
                }
                if (typeof window !== 'undefined' && typeof window.resetEphemeralOptionsForNewChat === 'function') {
                    window.resetEphemeralOptionsForNewChat();
                }
                if (typeof window !== 'undefined' && typeof window.refreshGreeting === 'function') {
                    window.refreshGreeting();
                }
                this.updateFirstMessageFlag(false);
    
                if (!isTemporary && sessionName && sessionName !== 'New Chat') {
                    await this.persistSessionName(sessionId, sessionName);
                }

                // Re-render the chat tabs.
                this.renderChatTabs();

                // Force the switch to the new session, even if it happens to be the same as the current one.
                await this.switchSession(sessionId, true);

                // Close mobile sidebar after creating new chat
                const isMobile = window.DeviceDetector ? DeviceDetector.isMobile() : (window.innerWidth <= 768);
                const isViewportMobile = document.body.classList.contains('viewport-mobile');
                if ((isMobile || isViewportMobile) && typeof window.closeMobileSidebar === 'function') {
                    window.closeMobileSidebar();
                }

                // If an initial prompt was provided, set it and send the message.
                if (initialPrompt && typeof window !== 'undefined' && typeof window.sendMessage === 'function') {
                    await window.sendMessage(initialPrompt);
                }

                // Suppress toast notifications for newly created chats per request.
            } else {
                throw new Error('Invalid response from server.');
            }
        } catch (error) {
            if (isTemporary) {
                this.isTemporaryUiActive = false;
                this.temporaryCentered = false;
                this.updateTemporaryUiState();
            } else {
                this.showNotification('Failed to create new chat', 'error');
            }
            console.error(error);
        } finally {
            if (overlay) {
                overlay.remove();
            }
        }
    }

    openHistorySearchModal() {
        if (!this.elements.searchModal) return;
        this.searchActive = true;
        this.searchQuery = '';
        this.elements.searchModal.classList.add('open');
        this.elements.searchModal.setAttribute('aria-hidden', 'false');
        if (typeof document !== 'undefined' && document.body) {
            document.body.classList.add('history-search-open');
        }

        if (this.elements.searchModalInput) {
            this.elements.searchModalInput.value = '';
            setTimeout(() => this.elements.searchModalInput?.focus(), 50);
        }

        this.renderSearchResults();
    }

    closeHistorySearchModal() {
        if (!this.elements.searchModal) return;
        this.searchActive = false;
        this.searchQuery = '';
        this.elements.searchModal.classList.remove('open');
        this.elements.searchModal.setAttribute('aria-hidden', 'true');
        if (typeof document !== 'undefined' && document.body) {
            document.body.classList.remove('history-search-open');
        }
    }

    showImagesPage({ updateHistory = true } = {}) {
        if (!this.elements.imagesPage) return;
        this.imagesPageActive = true;
        this.elements.imagesPage.hidden = false;
        this.elements.imagesPage.classList.add('is-visible');
        this.elements.imagesPage.setAttribute('aria-hidden', 'false');
        this.elements.imagesButton?.setAttribute('aria-pressed', 'true');
        this.elements.imagesButton?.classList.add('is-active');
        if (typeof document !== 'undefined' && document.body) {
            document.body.classList.add('images-page-open');
        }
        if (updateHistory && typeof window !== 'undefined' && window.location.pathname !== '/images') {
            try { window.history.pushState({ surface: 'images' }, '', '/images'); } catch (_) {}
        }
        this.renderImagesStyles();
        this.renderImagesDiscover();
        this.renderImagesGallery();
        this.setImagesPageMode('home');
        setTimeout(() => this.elements.imagesPromptInput?.focus(), 40);
    }

    hideImagesPage({ updateHistory = true } = {}) {
        if (!this.elements.imagesPage) return;
        this.imagesPageActive = false;
        this.elements.imagesPage.hidden = true;
        this.elements.imagesPage.classList.remove('is-visible');
        this.elements.imagesPage.setAttribute('aria-hidden', 'true');
        this.elements.imagesButton?.setAttribute('aria-pressed', 'false');
        this.elements.imagesButton?.classList.remove('is-active');
        if (typeof document !== 'undefined' && document.body) {
            document.body.classList.remove('images-page-open');
        }
        if (updateHistory && typeof window !== 'undefined' && window.location.pathname === '/images') {
            try { window.history.pushState({ surface: 'chat' }, '', '/'); } catch (_) {}
        }
        this.hideImagePreview();
    }

    showChatsPage({ updateHistory = true } = {}) {
        if (!this.elements.chatsPage) return;
        if (this.imagesPageActive) this.hideImagesPage({ updateHistory: false });
        this.chatsPageActive = true;
        this.elements.chatsPage.hidden = false;
        this.elements.chatsPage.classList.add('is-visible');
        this.elements.chatsPage.setAttribute('aria-hidden', 'false');
        if (typeof document !== 'undefined' && document.body) {
            document.body.classList.add('chats-page-open');
            // Sync the data-attr — CSS uses it for the no-flash initial paint.
            document.body.dataset.initialSurface = 'chats';
        }
        if (updateHistory && typeof window !== 'undefined' && window.location.pathname !== '/chats') {
            try { window.history.pushState({ surface: 'chats' }, '', '/chats'); } catch (_) {}
        }
        // Trigger a refresh of the chats list and arm the 30s polling.
        if (typeof window !== 'undefined' && window.__chatsPage) {
            window.__chatsPage.start?.();
            window.__chatsPage.fetchSessions?.();
        }
    }

    hideChatsPage({ updateHistory = true } = {}) {
        if (!this.elements.chatsPage) return;
        this.chatsPageActive = false;
        this.elements.chatsPage.hidden = true;
        this.elements.chatsPage.classList.remove('is-visible');
        this.elements.chatsPage.setAttribute('aria-hidden', 'true');
        if (typeof document !== 'undefined' && document.body) {
            document.body.classList.remove('chats-page-open');
            // Clear the no-flash data-attr; otherwise its CSS rules keep
            // the chat-shell hidden after we leave /chats.
            document.body.dataset.initialSurface = 'chat';
        }
        // Disarm the 30s background poll so it stops burning Firestore reads
        // (and visibilitychange-fetches) while the user is on a different surface.
        if (typeof window !== 'undefined' && window.__chatsPage?.stop) {
            window.__chatsPage.stop();
        }
        if (updateHistory && typeof window !== 'undefined' && window.location.pathname === '/chats') {
            try { window.history.pushState({ surface: 'chat' }, '', '/'); } catch (_) {}
        }
    }

    setImagesPageMode(mode) {
        this.imagesPageMode = mode;
        const isHome = mode === 'home';
        const isLoading = mode === 'loading';
        const isResult = mode === 'result';
        const isError = mode === 'error';
        // Library redesign: Try-a-Style + Discover stay hidden permanently.
        if (this.elements.imagesStylesSection) this.elements.imagesStylesSection.hidden = true;
        if (this.elements.imagesDiscoverSection) this.elements.imagesDiscoverSection.hidden = true;
        if (this.elements.imagesGallerySection) this.elements.imagesGallerySection.hidden = !isHome;
        if (this.elements.imagesPageLoading) this.elements.imagesPageLoading.hidden = !isLoading;
        if (this.elements.imagesPageResult) this.elements.imagesPageResult.hidden = !isResult;
        if (this.elements.imagesResultError) this.elements.imagesResultError.hidden = !isError;
        if (this.elements.imagesPromptSubmit) this.elements.imagesPromptSubmit.disabled = isLoading;
        // Auto-close the composer when we leave home (loading/result/error all take over the canvas).
        if (!isHome && this.elements.imagesPageComposer) {
            this.elements.imagesPageComposer.hidden = true;
        }
    }

    renderImagesStyles() {
        const track = this.elements.imagesStylesTrack;
        if (!track || track.dataset.rendered === '1') return;
        const frag = document.createDocumentFragment();
        this.imagesStylesData.forEach(style => {
            const card = document.createElement('button');
            card.type = 'button';
            card.className = 'images-style-card';
            card.dataset.prefix = style.prefix;
            card.setAttribute('role', 'listitem');
            card.style.background = style.gradient;
            const label = document.createElement('span');
            label.className = 'images-style-card__label';
            label.textContent = style.label;
            card.appendChild(label);
            frag.appendChild(card);
        });
        track.appendChild(frag);
        track.dataset.rendered = '1';
    }

    renderImagesDiscover() {
        const grid = this.elements.imagesDiscoverGrid;
        if (!grid || grid.dataset.rendered === '1') return;
        const frag = document.createDocumentFragment();
        this.imagesDiscoverData.forEach((item, idx) => {
            const btn = document.createElement('button');
            btn.type = 'button';
            btn.className = 'images-discover-item';
            btn.dataset.text = item.text;
            btn.setAttribute('role', 'listitem');
            const thumb = document.createElement('span');
            thumb.className = 'images-discover-item__thumb';
            const hue = (idx * 54) % 360;
            thumb.style.background = `linear-gradient(135deg, hsl(${hue} 70% 55%), hsl(${(hue + 40) % 360} 70% 35%))`;
            const label = document.createElement('span');
            label.className = 'images-discover-item__label';
            label.textContent = item.label;
            btn.appendChild(thumb);
            btn.appendChild(label);
            frag.appendChild(btn);
        });
        grid.appendChild(frag);
        grid.dataset.rendered = '1';
    }

    applyImagesStylePrefix(prefix) {
        if (!this.elements.imagesPromptInput) return;
        const current = this.elements.imagesPromptInput.value.trim();
        const composed = current ? `${prefix}: ${current}` : `${prefix}: `;
        this.elements.imagesPromptInput.value = composed;
        this.elements.imagesPromptInput.focus();
        const len = composed.length;
        try { this.elements.imagesPromptInput.setSelectionRange(len, len); } catch (_) {}
    }

    applyImagesDiscoverSuggestion(text) {
        if (!text || !this.elements.imagesPromptInput) return;
        this.elements.imagesPromptInput.value = text;
        this.submitImagesPrompt();
    }

    scrollImagesCarousel(which, direction) {
        if (which !== 'styles') return;
        const target = this.elements.imagesStylesTrack;
        if (!target) return;
        const delta = (target.clientWidth * 0.6) * direction;
        target.scrollBy({ left: delta, behavior: 'smooth' });
    }

    async submitImagesPrompt() {
        if (this.imagesSubmitting) return;
        const promptValue = (this.elements.imagesPromptInput?.value || '').trim();
        if (!promptValue) {
            if (this.elements.imagesPromptInput) {
                this.elements.imagesPromptInput.classList.add('input-error');
                setTimeout(() => this.elements.imagesPromptInput?.classList.remove('input-error'), 700);
            }
            return;
        }
        this.imagesSubmitting = true;
        this.imagesLastPrompt = promptValue;
        this.setImagesPageMode('loading');

        const csrf = (typeof getCSRFToken === 'function')
            ? getCSRFToken()
            : document.querySelector('meta[name="csrf-token"]')?.getAttribute('content') || '';

        console.log('[Images] POST /api/images/create', promptValue);
        try {
            const res = await fetch('/api/images/create', {
                method: 'POST',
                credentials: 'same-origin',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': csrf
                },
                body: JSON.stringify({ prompt: promptValue })
            });
            console.log('[Images] response status', res.status);
            if (!res.ok) {
                const errText = await res.text().catch(() => '');
                console.error('Images endpoint error', res.status, errText);
                this.setImagesPageMode('error');
                return;
            }
            const data = await res.json();
            console.log('[Images] response data', data);
            const attachment = (data.attachments && data.attachments[0]) || {};
            const imageUrl = attachment.url || data.image_url;
            if (!imageUrl) {
                this.setImagesPageMode('error');
                return;
            }
            const entry = {
                id: `img-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
                url: imageUrl,
                prompt: promptValue,
                sessionId: data.session_id || this.activeSessionId || null,
                createdAt: new Date().toISOString(),
                seed: data.seed || null,
                model: (data.model || '').trim(),
                provider: (data.provider || '').trim(),
                model_label: (data.model_label || data.model || this.getActiveModelLabel()).trim()
            };
            this.generatedImages = [entry, ...(this.generatedImages || [])].slice(0, 60);
            this.showImagesResult(entry);
            this.renderImagesGallery();
            if (this.elements.imagesPromptInput) {
                this.elements.imagesPromptInput.value = '';
            }
        } catch (error) {
            console.error('Images page submit failed', error);
            this.setImagesPageMode('error');
        } finally {
            this.imagesSubmitting = false;
        }
    }

    showImagesResult(entry) {
        if (!entry || !entry.url) return;
        if (this.elements.imagesResultImg) {
            this.elements.imagesResultImg.src = entry.url;
        }
        if (this.elements.imagesResultPrompt) {
            this.elements.imagesResultPrompt.textContent = entry.prompt || this.imagesLastPrompt || '';
        }
        this.setImagesPageMode('result');
    }

    async downloadImagesResult() {
        const url = this.elements.imagesResultImg?.src;
        if (!url) return;
        try {
            const res = await fetch(url);
            const blob = await res.blob();
            const link = document.createElement('a');
            link.href = URL.createObjectURL(blob);
            link.download = 'image.png';
            document.body.appendChild(link);
            link.click();
            link.remove();
            setTimeout(() => URL.revokeObjectURL(link.href), 1000);
        } catch (e) {
            window.open(url, '_blank', 'noopener');
        }
    }

    regenerateImagesResult() {
        if (!this.imagesLastPrompt) return;
        if (this.elements.imagesPromptInput) {
            this.elements.imagesPromptInput.value = this.imagesLastPrompt;
        }
        this.submitImagesPrompt();
    }

    editImagesPrompt() {
        if (this.elements.imagesPromptInput && this.imagesLastPrompt) {
            this.elements.imagesPromptInput.value = this.imagesLastPrompt;
        }
        this.setImagesPageMode('home');
        this.elements.imagesPromptInput?.focus();
    }

    handleSearchInput(value) {
        this.searchQuery = value || '';
        this.renderSearchResults();
    }

    renderSearchResults() {
        const container = this.elements.searchResults;
        if (!container) return;

        const sessions = this.getSortedSessionsArray();
        const trimmedQuery = (this.searchQuery || '').trim();
        const limit = trimmedQuery ? 20 : 8;
        const matches = trimmedQuery ? this.filterSessionsByQuery(sessions) : sessions;
        const limited = matches.slice(0, limit);

        container.innerHTML = '';

        if (!limited.length) {
            const empty = document.createElement('div');
            empty.className = 'history-search-empty';
            empty.textContent = trimmedQuery
                ? `No chats found for "${trimmedQuery}".`
                : 'No conversation history yet.';
            container.appendChild(empty);
            return;
        }

        const fragment = document.createDocumentFragment();
        // Claude-style row: [chat-icon] [name] [time]
        const CHAT_ICON_SVG = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>';
        limited.forEach((session, idx) => {
            const button = document.createElement('button');
            button.type = 'button';
            button.className = 'history-search-result';
            button.dataset.sessionId = session.session_id;
            button.dataset.idx = String(idx);
            if (idx === 0) button.classList.add('is-active');

            const icon = document.createElement('span');
            icon.className = 'history-result-icon';
            icon.setAttribute('aria-hidden', 'true');
            icon.innerHTML = CHAT_ICON_SVG;

            const name = document.createElement('span');
            name.className = 'history-result-name';
            name.textContent = session.session_name || 'Untitled session';

            const timestamp = document.createElement('span');
            timestamp.className = 'history-result-time';
            timestamp.textContent = this.formatSessionTimestamp(session.sessionDate);

            button.appendChild(icon);
            button.appendChild(name);
            button.appendChild(timestamp);

            fragment.appendChild(button);
        });

        container.appendChild(fragment);
    }

    renderImagesGallery() {
        const grid = this.elements.imagesGrid;
        const empty = this.elements.imagesEmpty;
        if (!grid || !empty) return;

        grid.innerHTML = '';
        const items = this.generatedImages || [];
        if (!items.length) {
            empty.style.display = 'flex';
            grid.style.display = 'none';
            this.hideImagePreview();
            return;
        }
        empty.style.display = 'none';
        grid.style.display = 'grid';

        const fragment = document.createDocumentFragment();
        items.forEach(image => {
            const card = document.createElement('article');
            card.className = 'images-gallery-card';
            card.dataset.sessionId = image.sessionId || '';
            card.dataset.imageId = image.id;
            card.title = 'Generated image';

            const previewButton = document.createElement('button');
            previewButton.type = 'button';
            previewButton.className = 'images-card-preview';
            previewButton.setAttribute('aria-label', 'Preview image');

            const img = document.createElement('img');
            img.src = image.url;
            img.alt = 'Generated image';
            previewButton.appendChild(img);

            const meta = document.createElement('div');
            meta.className = 'images-card-meta';

            const footer = document.createElement('div');
            footer.className = 'images-card-footer';

            const model = document.createElement('span');
            model.className = 'images-card-tag';
            model.textContent = this.getImageModelLabel(image);

            const timestamp = document.createElement('span');
            timestamp.className = 'images-card-time';
            timestamp.textContent = this.formatImageTimestamp(image.createdAt);

            footer.appendChild(model);
            footer.appendChild(timestamp);

            meta.appendChild(footer);

            card.appendChild(previewButton);
            card.appendChild(meta);
            fragment.appendChild(card);
        });

        grid.appendChild(fragment);
        if (this.activePreviewImageId) {
            const stillExists = items.some(image => image.id === this.activePreviewImageId);
            if (!stillExists) {
                this.hideImagePreview();
            }
        }
    }

    recordGeneratedImage(imageData) {
        if (!imageData || !imageData.url) return;
        const entry = {
            id: `img-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
            url: imageData.url,
            prompt: imageData.prompt || 'AI image',
            sessionId: imageData.sessionId || this.activeSessionId || null,
            createdAt: imageData.createdAt || new Date().toISOString(),
            seed: imageData.seed || null,
            model: (imageData.model || '').trim(),
            provider: (imageData.provider || '').trim(),
            model_label: (imageData.model_label || imageData.model || this.getActiveModelLabel()).trim()
        };

        this.generatedImages = [entry, ...(this.generatedImages || [])].slice(0, 60);
        this.renderImagesGallery();
        // Only auto-transition when the Images page is actually waiting for a chat-stream image.
        // submitImagesPrompt handles its own transition directly.
        if (this.imagesPageActive && this.imagesPageMode === 'loading' && !this.imagesSubmitting) {
            this.showImagesResult(entry);
        }
    }

    hydrateImagesFromHistory(limit = 60) {
        const collected = [];

        Object.entries(this.chatHistory).forEach(([sessionId, session]) => {
            const sessionMessages = Array.isArray(session.messages) ? session.messages : [];
            sessionMessages.forEach(message => {
                const attachments = this.collectImageAttachments(message);
                attachments.forEach(attachment => {
                    if (!attachment?.url) return;
                    collected.push({
                        id: `img-history-${sessionId}-${Math.random().toString(36).slice(2, 7)}`,
                        url: attachment.url,
                        prompt: attachment.prompt,
                        sessionId,
                        createdAt: attachment.createdAt || message.timestamp || session.created_at,
                        seed: attachment.seed,
                        model: attachment.model,
                        provider: attachment.provider,
                        model_label: session.model_label || attachment.model_label
                    });
                });
            });
        });

        collected.sort((a, b) => new Date(b.createdAt || 0) - new Date(a.createdAt || 0));
        this.generatedImages = collected.slice(0, limit);
        this.renderImagesGallery();
    }

    collectImageAttachments(message) {
        if (!message || !Array.isArray(message.attachments)) return [];
        return message.attachments
            .filter(attachment => this.isAttachmentImage(attachment))
            .map(attachment => {
                const resolvedUrl = this.resolveAttachmentUrl(attachment);
                return {
                    url: resolvedUrl,
                    prompt: '',
                    createdAt: attachment.created_at || message.timestamp,
                    seed: attachment.seed,
                    model: attachment.model,
                    provider: attachment.provider,
                    model_label: attachment.model_label
                };
            })
            .filter(attachment => !!attachment.url);
    }

    isAttachmentImage(attachment) {
        if (!attachment) return false;
        if (attachment.isImage) return true;
        const typeCandidates = [
            attachment.type,
            attachment.mime_type,
            attachment.media_type,
            attachment.content_type
        ].filter(value => typeof value === 'string' && value.trim().length > 0);
        if (typeCandidates.some(value => value.toLowerCase().startsWith('image/'))) {
            return true;
        }
        const nameCandidates = [
            attachment.name,
            attachment.original_filename,
            attachment.originalFilename,
            attachment.filename,
            attachment.file_name,
            attachment.display_name
        ].filter(value => typeof value === 'string' && value.trim().length > 0);
        if (nameCandidates.some(value => /\.(png|jpe?g|gif|webp|bmp|svg)$/i.test(value))) {
            return true;
        }
        const urlCandidates = [
            attachment.url,
            attachment.previewUrl,
            attachment.dataUrl,
            attachment.path,
            attachment.relative_path
        ].filter(value => typeof value === 'string' && value.trim().length > 0);
        return urlCandidates.some(value => {
            if (/^data:image\//i.test(value)) {
                return true;
            }
            const normalized = value.split('?')[0];
            return /\.(png|jpe?g|gif|webp|bmp|svg)$/i.test(normalized);
        });
    }

    resolveAttachmentUrl(attachment, allowPreview = false) {
        if (!attachment) return null;
        let candidate = attachment.dataUrl
            || attachment.url
            || attachment.path
            || attachment.relative_path
            || attachment.local_path
            || '';
        if (!candidate && allowPreview) {
            candidate = attachment.previewUrl || '';
        }
        if (!candidate || typeof candidate !== 'string') {
            return null;
        }
        if (/^(data:|https?:\/\/|\/\/)/i.test(candidate)) {
            return candidate;
        }
        if (candidate.startsWith('/uploads/')) {
            return candidate;
        }
        if (candidate.startsWith('/')) {
            return `/uploads${candidate}`;
        }
        return `/uploads/${candidate.replace(/^\/+/, '')}`;
    }

    formatImageTimestamp(value) {
        if (!value) return '';
        const date = value instanceof Date ? value : new Date(value);
        if (Number.isNaN(date.getTime())) return '';
        const now = new Date();
        const diffHours = (now - date) / (1000 * 60 * 60);
        if (diffHours < 24) {
            return date.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
        }
        if (diffHours < 24 * 7) {
            return date.toLocaleDateString([], { weekday: 'short' });
        }
        return date.toLocaleDateString([], { month: 'short', day: 'numeric' });
    }

    isGeneratedImageAttachment(attachment) {
        if (!attachment) return false;
        if (attachment.provider) return true;
        const pathFields = [attachment.local_path, attachment.relative_path, attachment.path];
        return pathFields.some(value => typeof value === 'string' && value.includes('generated_images'));
    }

    deriveAttachmentFilename(attachment) {
        if (!attachment) return '';
        const candidates = [
            attachment.original_filename,
            attachment.local_path,
            attachment.relative_path,
            attachment.path
        ];
        for (const candidate of candidates) {
            const resolved = this.extractFilenameFromPath(candidate);
            if (resolved) {
                return resolved;
            }
        }
        return '';
    }

    extractFilenameFromPath(value) {
        if (typeof value !== 'string') return '';
        const trimmed = value.trim();
        if (!trimmed) return '';
        const segments = trimmed.split(/[\\/]/);
        return segments[segments.length - 1] || '';
    }

    hasAttachmentPromptData(attachment) {
        if (!attachment) return false;
        return ['prompt_fragment', 'caption', 'prompt', 'description'].some(key => {
            const value = attachment[key];
            return typeof value === 'string' && value.trim().length > 0;
        });
    }

    applyAttachmentPromptFallback(attachments, fallbackPrompt, wordLimit = 3) {
        const list = Array.isArray(attachments) ? attachments : [];
        if (!fallbackPrompt || typeof fallbackPrompt !== 'string') {
            return list;
        }
        const trimmed = fallbackPrompt.trim();
        if (!trimmed) {
            return list;
        }
        const snippet = trimmed.split(/\s+/).slice(0, wordLimit).join(' ');
        if (!snippet) {
            return list;
        }
        return list.map(attachment => {
            if (!attachment || this.hasAttachmentPromptData(attachment)) {
                return attachment;
            }
            return { ...attachment, prompt_fragment: snippet };
        });
    }

    getAttachmentPromptSnippet(attachment, wordLimit = 3) {
        if (!attachment || wordLimit <= 0) return '';
        const sources = [
            attachment.prompt_fragment,
            attachment.caption,
            attachment.prompt,
            attachment.description
        ];
        for (const source of sources) {
            if (typeof source === 'string') {
                const trimmed = source.trim();
                if (trimmed) {
                    const words = trimmed.split(/\s+/).slice(0, wordLimit);
                    return words.join(' ');
                }
            }
        }
        return '';
    }

    getAttachmentDisplayName(attachment, fallback = 'Generated image') {
        if (!attachment) return fallback;
        const derived = this.deriveAttachmentFilename(attachment);
        if (derived) return derived;
        if (!this.isGeneratedImageAttachment(attachment)) {
            const fromName = this.extractFilenameFromPath(attachment.name);
            if (fromName) return fromName;
            if (typeof attachment.name === 'string' && attachment.name.trim()) {
                return attachment.name.trim();
            }
        }
        return fallback;
    }

    buildAttachmentTitle(attachment, fallback = 'Generated image') {
        if (!attachment) return fallback;
        if (attachment.title) return attachment.title;
        const promptSnippet = this.getAttachmentPromptSnippet(attachment, 3);
        if (promptSnippet) return promptSnippet;
        const displayName = this.getAttachmentDisplayName(attachment, fallback);
        if (displayName) return displayName;
        if (attachment.model) return `${attachment.model} result`;
        return fallback;
    }

    buildAttachmentMetaChips(attachment) {
        const chips = [];
        if (!attachment) return chips;
        if (attachment.model) {
            chips.push({ label: 'Model', value: attachment.model });
        }
        if (attachment.provider) {
            chips.push({ label: 'Provider', value: attachment.provider });
        }
        if (attachment.seed !== undefined && attachment.seed !== null && attachment.seed !== '') {
            chips.push({ label: 'Seed', value: attachment.seed });
        }
        if (attachment.width && attachment.height) {
            chips.push({ label: 'Resolution', value: `${attachment.width}×${attachment.height}` });
        } else if (attachment.width || attachment.height) {
            chips.push({ label: 'Size', value: attachment.width || attachment.height });
        }
        const created = attachment.created_at || attachment.createdAt || attachment.timestamp;
        if (created) {
            const formatted = this.formatImageTimestamp(created);
            if (formatted) {
                chips.push({ label: 'Created', value: formatted });
            }
        }
        return chips;
    }

    buildAttachmentDownloadName(attachment, fallback = 'image') {
        const preferred = this.getAttachmentDisplayName(attachment, fallback);
        return sanitizeFilename(preferred || fallback);
    }

    buildAttachmentEyebrow(attachment) {
        if (!attachment) return '';
        const parts = [];
        if (attachment.provider) {
            parts.push(this.capitalizeWords(attachment.provider));
        }
        if (attachment.model) {
            parts.push(attachment.model);
        }
        return parts.length ? parts.join(' • ') : 'Image attachment';
    }

    capitalizeWords(value) {
        if (typeof value !== 'string') return '';
        return value.split(/\s+/).map(word => {
            if (!word) return '';
            return word.charAt(0).toUpperCase() + word.slice(1);
        }).join(' ').trim();
    }

    findImageById(imageId) {
        if (!imageId || !Array.isArray(this.generatedImages)) return null;
        return this.generatedImages.find(image => image.id === imageId) || null;
    }

    showImagePreview(imageId) {
        // Library redesign: prefer fullscreen viewer when present, else fall back to legacy modal.
        if (this.elements.imagesViewer) {
            this.openImagesViewer(imageId);
            return;
        }
        const previewModal = this.elements.imagePreviewModal;
        const previewImg = this.elements.imagePreviewImg;
        const previewPrompt = this.elements.imagePreviewPrompt;
        const previewMeta = this.elements.imagePreviewMeta;
        const previewLink = this.elements.imagePreviewLink;

        const image = this.findImageById(imageId);
        if (!previewModal || !previewImg || !previewPrompt || !previewMeta || !previewLink || !image) {
            return;
        }

        this.activePreviewImageId = imageId;
        previewImg.src = image.url;
        previewImg.alt = 'Generated image preview';
        previewPrompt.textContent = '';
        previewPrompt.style.display = 'none';
        previewMeta.textContent = `${image.model || 'Image model'} • ${this.formatImageTimestamp(image.createdAt)}`;
        previewLink.href = image.url;

        previewModal.classList.add('open');
        previewModal.setAttribute('aria-hidden', 'false');
        if (typeof document !== 'undefined' && document.body) {
            document.body.classList.add('image-preview-open');
        }
    }

    hideImagePreview() {
        // Closes whichever viewer is open
        if (this.elements.imagesViewer && this.isImagesViewerOpen()) {
            this.closeImagesViewer();
            return;
        }
        this.activePreviewImageId = null;
        const previewModal = this.elements.imagePreviewModal;
        if (!previewModal) return;
        previewModal.classList.remove('open');
        previewModal.setAttribute('aria-hidden', 'true');
        if (typeof document !== 'undefined' && document.body) {
            document.body.classList.remove('image-preview-open');
        }
    }

    isPreviewModalOpen() {
        if (this.elements.imagesViewer && this.isImagesViewerOpen()) {
            return true;
        }
        return !!this.elements.imagePreviewModal?.classList.contains('open');
    }

    // ============================================================
    // Library: fullscreen viewer + composer toggle
    // ============================================================

    openImagesComposer() {
        const composer = this.elements.imagesPageComposer;
        const input = this.elements.imagesPromptInput;
        if (!composer) return;
        composer.hidden = false;
        if (input) {
            try { input.focus({ preventScroll: false }); } catch (_) { input.focus(); }
        }
    }

    closeImagesComposer() {
        const composer = this.elements.imagesPageComposer;
        const input = this.elements.imagesPromptInput;
        if (!composer) return;
        composer.hidden = true;
        if (input) input.value = '';
    }

    openImagesViewer(imageId) {
        const viewer = this.elements.imagesViewer;
        const image = this.findImageById(imageId);
        if (!viewer || !image) return;
        this.activePreviewImageId = imageId;
        this.renderImagesViewer(image);
        viewer.hidden = false;
        // Force reflow so the transition runs
        // eslint-disable-next-line no-unused-expressions
        viewer.offsetHeight;
        viewer.classList.add('is-open');
        viewer.setAttribute('aria-hidden', 'false');
        if (typeof document !== 'undefined' && document.body) {
            document.body.classList.add('images-viewer-open');
        }
    }

    renderImagesViewer(image) {
        const img = this.elements.imagesViewerImg;
        const caption = this.elements.imagesViewerCaption;
        if (img) {
            img.src = image.url;
            img.alt = image.prompt || 'Generated image';
        }
        if (caption) {
            caption.textContent = image.prompt || '';
        }
        const items = this.generatedImages || [];
        const idx = items.findIndex(it => it.id === image.id);
        if (this.elements.imagesViewerPrev) {
            this.elements.imagesViewerPrev.disabled = idx <= 0;
        }
        if (this.elements.imagesViewerNext) {
            this.elements.imagesViewerNext.disabled = idx < 0 || idx >= items.length - 1;
        }
    }

    closeImagesViewer() {
        const viewer = this.elements.imagesViewer;
        if (!viewer) return;
        viewer.classList.remove('is-open');
        viewer.setAttribute('aria-hidden', 'true');
        this.activePreviewImageId = null;
        if (typeof document !== 'undefined' && document.body) {
            document.body.classList.remove('images-viewer-open');
        }
        // Defer hide until the transition completes (~220ms)
        window.setTimeout(() => {
            if (!viewer.classList.contains('is-open')) {
                viewer.hidden = true;
            }
        }, 240);
    }

    isImagesViewerOpen() {
        return !!this.elements.imagesViewer?.classList.contains('is-open');
    }

    navigateImagesViewer(direction) {
        const items = this.generatedImages || [];
        if (!items.length) return;
        const idx = items.findIndex(it => it.id === this.activePreviewImageId);
        if (idx < 0) return;
        const nextIdx = idx + direction;
        if (nextIdx < 0 || nextIdx >= items.length) return;
        const nextImage = items[nextIdx];
        this.activePreviewImageId = nextImage.id;
        this.renderImagesViewer(nextImage);
    }

    downloadImagesViewer() {
        const image = this.findImageById(this.activePreviewImageId);
        if (!image?.url) return;
        const a = document.createElement('a');
        a.href = image.url;
        a.download = `xelasphere-${image.id || Date.now()}.png`;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
    }

    regenerateFromViewer() {
        const image = this.findImageById(this.activePreviewImageId);
        if (!image) return;
        this.closeImagesViewer();
        this.openImagesComposer();
        const input = this.elements.imagesPromptInput;
        if (input) {
            input.value = image.prompt || '';
            try { input.focus(); } catch (_) {}
        }
    }

    getSessionPreview(session) {
        if (!Array.isArray(session.messages) || session.messages.length === 0) {
            return 'No messages yet in this chat.';
        }

        for (let i = session.messages.length - 1; i >= 0; i -= 1) {
            const candidates = this.collectMessageStrings(session.messages[i]);
            if (candidates.length && candidates[0].trim()) {
                return this.truncatePreview(candidates[0].trim());
            }
        }

        return 'No textual content available.';
    }

    truncatePreview(text, limit = 96) {
        if (typeof text !== 'string') {
            return '';
        }
        if (text.length <= limit) return text;
        return `${text.slice(0, limit - 1)}…`;
    }

    formatSessionTimestamp(sessionDate) {
        if (!(sessionDate instanceof Date) || Number.isNaN(sessionDate)) return '';
        const now = new Date();
        const diffMs = now - sessionDate;
        const diffHours = diffMs / (1000 * 60 * 60);
        const diffDays = diffHours / 24;

        if (diffHours < 24) {
            return sessionDate.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
        }
        if (diffDays < 7) {
            return sessionDate.toLocaleDateString([], { weekday: 'short' });
        }
        return sessionDate.toLocaleDateString([], { month: 'short', day: 'numeric' });
    }

    getActiveModelLabel() {
        if (this.elements.modelSelect?.textContent) {
            return this.elements.modelSelect.textContent.trim();
        }
        if (this.elements.modelSelect?.dataset?.label) {
            return this.elements.modelSelect.dataset.label;
        }
        return 'Default Model';
    }

    getImageModelLabel(image) {
        if (!image || typeof image !== 'object') {
            return this.getActiveModelLabel();
        }

        const storedLabel = typeof image.model_label === 'string' && image.model_label.trim();
        if (storedLabel) {
            return image.model_label.trim();
        }

        const explicitModel = typeof image.model === 'string' && image.model.trim();
        if (explicitModel) {
            return image.model.trim();
        }

        const providerLabel = this.formatProviderLabel(image.provider);
        if (providerLabel) {
            return providerLabel;
        }

        if (image.sessionId && this.chatHistory[image.sessionId]?.model_label) {
            return this.chatHistory[image.sessionId].model_label;
        }

        return this.getActiveModelLabel();
    }

    formatProviderLabel(provider) {
        if (typeof provider !== 'string' || !provider.trim()) return '';
        return provider
            .replace(/[_-]+/g, ' ')
            .replace(/\s+/g, ' ')
            .trim()
            .replace(/\b\w/g, char => char.toUpperCase());
    }

    collectMessageStrings(message) {
        const values = [];
        if (!message) return values;

        if (typeof message === 'string') {
            values.push(message);
            return values;
        }

        if (typeof message === 'object') {
            ['content', 'text', 'display_content'].forEach(key => {
                if (typeof message[key] === 'string') {
                    values.push(message[key]);
                }
            });

            if (Array.isArray(message.content_parts)) {
                values.push(message.content_parts.join(' '));
            }

            if (Array.isArray(message.parts)) {
                values.push(message.parts.filter(part => typeof part === 'string').join(' '));
            }
        }

        return values.filter(Boolean);
    }

    prepareImagePrompt(closeModal = false) {
        if (!this.elements.messageInput) return;
        const prefix = '/image ';
        if (!this.elements.messageInput.value.startsWith(prefix)) {
            this.elements.messageInput.value = prefix;
        }
        this.elements.messageInput.focus();
        if (closeModal) {
            this.closeImagesModal();
        }
    }

    scheduleAutoRename(sessionId, delay = 900) {
        if (!sessionId || !this.chatHistory[sessionId]) return;
        const session = this.chatHistory[sessionId];
        if (!this.shouldAutoRenameSession(session)) return;

        if (this.autoRenameTimers[sessionId]) {
            clearTimeout(this.autoRenameTimers[sessionId]);
        }

        session.autoRenameQueued = true;
        this.autoRenameTimers[sessionId] = setTimeout(() => {
            delete this.autoRenameTimers[sessionId];
            this.applyAutoTitle(sessionId).catch((error) => {
                console.error('Auto title generation failed', error);
            });
        }, delay);
    }

    async applyAutoTitle(sessionId) {
        const session = this.chatHistory[sessionId];
        if (!session) return;
        session.autoRenameQueued = false;

        if (!this.shouldAutoRenameSession(session) || !this.hasSufficientContext(session)) {
            return;
        }

        const generatedTitle = this.generateContextualSessionTitle(session);
        const normalizedTitle = generatedTitle?.replace(/\s+/g, ' ').trim();

        if (!normalizedTitle || normalizedTitle.length < 4 || normalizedTitle === session.session_name) {
            return;
        }

        session.session_name = normalizedTitle;
        session.autoTitleEligible = false;
        this.renderChatTabs();
        // Update page title if this is the active session
        if (this.activeSessionId === sessionId) {
            this.updatePageTitle(sessionId);
        }
        if (this.searchActive) {
            this.renderSearchResults();
        }

        try {
            await this.fetchApi('/chat/rename', 'POST', {
                session_id: sessionId,
                name: normalizedTitle
            });
        } catch (error) {
            console.error('Failed to persist auto-generated title', error);
        }
    }

    shouldAutoRenameSession(session) {
        if (!session || session.manuallyRenamed) return false;
        if (session.autoTitleEligible === false) return false;
        return this.isGenericName(session.session_name);
    }

    isGenericName(name) {
        const normalized = (name || '').trim().toLowerCase();
        if (!normalized) return true;
        return this.genericSessionNames.some(generic =>
            normalized === generic || normalized.startsWith(`${generic} `)
        );
    }

    hasSufficientContext(session) {
        if (!session || !Array.isArray(session.messages)) return false;
        return session.messages.some(message => {
            const contentBits = this.collectMessageStrings(message);
            return contentBits.some(text => text && text.replace(/\s+/g, ' ').trim().length >= 12);
        });
    }

    generateContextualSessionTitle(session) {
        if (!session || !Array.isArray(session.messages)) return '';

        const userTexts = [];
        const otherTexts = [];

        session.messages.forEach(message => {
            const parts = this.collectMessageStrings(message);
            const joined = parts.join(' ').replace(/\s+/g, ' ').trim();
            if (!joined) return;

            if ((message.sender || '').toLowerCase() === 'user') {
                userTexts.push(joined);
            } else {
                otherTexts.push(joined);
            }
        });

        const prioritized = userTexts[userTexts.length - 1] ||
            userTexts[0] ||
            otherTexts[otherTexts.length - 1] ||
            otherTexts[0] ||
            '';

        const formatted = this.formatPromptTitle(prioritized);
        if (formatted && formatted.length >= 5) {
            return formatted;
        }

        const combined = [...userTexts, ...otherTexts].join(' ').slice(0, 600);
        const keywords = this.extractTopKeywords(combined);
        if (keywords.length >= 2) {
            return keywords.slice(0, 3).join(' ');
        }
        if (keywords.length === 1) {
            return keywords[0];
        }

        if (prioritized) {
            return this.truncatePreview(prioritized, 48);
        }

        return 'New Chat';
    }

    formatPromptTitle(text) {
        if (!text) return '';
        let normalized = text.replace(/\s+/g, ' ').trim();
        if (!normalized) return '';

        const phrasesToTrim = [
            /^((hey|hi|hello)[,!]?\s+)/i,
            /^(please\s+)/i,
            /^(can you|could you|would you|will you|would u)\s+/i,
            /^(i need|we need|need help with)\s+/i,
            /^(help me|show me|tell me|explain|teach me)\s+/i,
            /^(what is|what's|how to|how do i|why is)\s+/i
        ];

        phrasesToTrim.forEach(pattern => {
            normalized = normalized.replace(pattern, '');
        });

        normalized = normalized.replace(/^[\W_]+/, '');

        const sentenceEnd = normalized.search(/[.?!]/);
        if (sentenceEnd > 0) {
            normalized = normalized.slice(0, sentenceEnd);
        }

        normalized = normalized.slice(0, 64).trim();

        if (!normalized) return '';
        return normalized.charAt(0).toUpperCase() + normalized.slice(1);
    }

    extractTopKeywords(text, max = 3) {
        if (!text) return [];
        const tokens = text
            .toLowerCase()
            .match(/[a-z0-9][a-z0-9'-]{2,}/g) || [];

        const frequency = tokens.reduce((acc, token) => {
            if (token.length < 4 || AUTO_TITLE_STOP_WORDS.has(token)) {
                return acc;
            }
            acc[token] = (acc[token] || 0) + 1;
            return acc;
        }, {});

        return Object.entries(frequency)
            .sort((a, b) => {
                if (b[1] === a[1]) {
                    return a[0].localeCompare(b[0]);
                }
                return b[1] - a[1];
            })
            .slice(0, max)
            .map(([word]) => this.capitalizeWord(word));
    }

    capitalizeWord(word) {
        if (!word) return '';
        return word
            .split(/[-\s]/)
            .map(part => part.charAt(0).toUpperCase() + part.slice(1))
            .join(' ');
    }

    primeAutoRenameForExistingSessions(limit = 12) {
        const candidates = Object.entries(this.chatHistory)
            .filter(([sessionId, session]) =>
                this.shouldAutoRenameSession(session) &&
                this.hasSufficientContext(session) &&
                !session.autoRenameQueued
            )
            .slice(0, limit);

        candidates.forEach(([sessionId], index) => {
            this.scheduleAutoRename(sessionId, 400 + index * 140);
        });
    }

    async persistSessionName(sessionId, desiredName) {
        if (!sessionId || !desiredName) return;
        try {
            const response = await this.fetchApi('/chat/rename', 'POST', {
                session_id: sessionId,
                name: desiredName
            });
            const appliedName = response?.updated_name || desiredName;
            if (this.chatHistory[sessionId]) {
                this.chatHistory[sessionId].session_name = appliedName;
            }
        } catch (error) {
            console.error('Failed to persist session name', error);
        }
    }

    markSessionDirty(sessionId) {
        if (!this.autoSaveEnabled || !sessionId) return;
        this.dirtySessions.add(sessionId);
    }

    /**
     * Bump a session to the top of the sidebar by updating last_updated.
     * Called when user sends a message to make session appear first.
     * @param {string} sessionId - The session ID to bump
     */
    bumpSessionToTop(sessionId) {
        if (!sessionId || !this.chatHistory[sessionId]) return;
        this.chatHistory[sessionId].last_updated = new Date().toISOString();
        this.renderChatTabs();
    }

    hasUnsavedChanges(sessionId) {
        if (!this.autoSaveEnabled) return false;
        return !!sessionId && this.dirtySessions.has(sessionId);
    }

    scrollChatToLatest() {
        // Use scrollState.js if available (ChatGPT-style scroll handling)
        if (window.scrollState?.scrollToBottomNow) {
            window.scrollState.scrollToBottomNow();
            return;
        }

        // Fallback: scroll the correct container
        const scrollToEnd = () => {
            // Use the actual scroll container, not document.scrollingElement
            const scroller = document.querySelector('#chat-scroll-region') ||
                             document.querySelector('#chatbox') ||
                             document.scrollingElement ||
                             document.documentElement;

            scroller.scrollTop = scroller.scrollHeight;

            if (window.hideDownArrow) {
                window.hideDownArrow();
            }
        };

        scrollToEnd();
        requestAnimationFrame(scrollToEnd);
    }

    updateFirstMessageFlag(hasMessages) {
        if (typeof window === 'undefined') return;
        const controller = window.conversationStateController;
        if (!controller) return;
        if (hasMessages && typeof controller.markConversationHasHistory === 'function') {
            controller.markConversationHasHistory();
        } else if (!hasMessages && typeof controller.markConversationNew === 'function') {
            controller.markConversationNew();
        }
    }

    pauseAutoScroll() {
        if (this.autoScrollPaused) return;
        const scrollState = typeof window !== 'undefined' ? window.scrollState : null;
        if (scrollState) {
            if (scrollState.animation) {
                window.cancelAnimationFrame(scrollState.animation);
                scrollState.animation = null;
            }
            scrollState.shouldAutoScroll = false;
            scrollState.isScrolling = false;
        }
        this.autoScrollPaused = true;
    }

    resumeAutoScroll() {
        if (!this.autoScrollPaused) return;
        const scrollState = typeof window !== 'undefined' ? window.scrollState : null;
        if (scrollState) {
            scrollState.shouldAutoScroll = true;
        }
        this.autoScrollPaused = false;
    }

    /**
     * Mark session as read — clears notification dot and updates read timestamp.
     * ChatGPT-style: called instantly on session switch, no scroll waiting.
     */
    markSessionReadNow(sessionId) {
        if (window.UnreadNotificationManager?.markSessionRead) {
            window.UnreadNotificationManager.markSessionRead(sessionId);
        }
        if (window.BackgroundRequestManager?.clearNotification) {
            window.BackgroundRequestManager.clearNotification(sessionId);
        }
    }

    recordLiveMessage({ sender, content, messageType = 'text', attachments = [], user_attachments = [], messageId, prompt, search_results = null, style = null } = {}) {
        if (this.isReplayingHistory) return;
        if (!this.activeSessionId) return;
        const normalizedSession = this.ensureSessionPages(this.activeSessionId);
        if (!normalizedSession) return;

        const normalizedType = (messageType || '').toLowerCase();
        const allowedTypes = new Set(['text', 'code', 'image', 'image-placeholder', 'image_placeholder', 'file', 'pdf']);
        let effectiveType = allowedTypes.has(normalizedType) ? normalizedType : 'text';

        const sanitizedContent = typeof content === 'string' ? content : '';
        const hasContent = sanitizedContent.trim().length > 0;
        const hasUserAttachments = Array.isArray(user_attachments) && user_attachments.length > 0;
        const hasAttachments = (Array.isArray(attachments) && attachments.length > 0) || hasUserAttachments;

        if (!hasContent && !hasAttachments && effectiveType === 'text') {
            return;
        }

        const containsCodeFence = sanitizedContent.includes('```');
        if (effectiveType === 'code' || containsCodeFence) {
            effectiveType = 'code';
        }

        if (effectiveType === 'image_placeholder') {
            effectiveType = 'image-placeholder';
        }

        if (!Array.isArray(normalizedSession.messages)) {
            normalizedSession.messages = [];
        }
        const pageMessages = normalizedSession.messages;
        const normalizedSender = sender === 'user' ? 'User' : 'AI';
        const targetId = messageId || this.generateMessageId();
        const searchResultsCopy = search_results && typeof search_results === 'object'
            ? (Array.isArray(search_results) ? search_results.map(r => ({ ...r })) : { ...search_results })
            : null;

        const replacement = {
            id: targetId,
            sender: normalizedSender,
            prompt: normalizedSender === 'User' ? (prompt || sanitizedContent) : prompt,
            content: sanitizedContent,
            attachments: Array.isArray(attachments) ? attachments.map(att => ({ ...att })) : [],
            user_attachments: hasUserAttachments ? user_attachments.map(att => ({ ...att })) : [],
            message_type: effectiveType,
            is_code: effectiveType === 'code',
            suppress_text: !hasContent && effectiveType !== 'text',
            timestamp: new Date().toISOString()
        };
        if (normalizedSender === 'AI' && style) {
            replacement.style = style;
        }

        if (searchResultsCopy) {
            replacement.search_results = searchResultsCopy;
        }

        const existingIndex = pageMessages.findIndex(msg =>
            msg &&
            (msg.id === targetId || msg.response_id === targetId)
        );

        if (existingIndex >= 0) {
            pageMessages[existingIndex] = {
                ...pageMessages[existingIndex],
                ...replacement
            };
        } else {
            pageMessages.push(replacement);
        }

        this.markSessionDirty(this.activeSessionId);
    }

    coalesceMessages(messages) {
        if (!Array.isArray(messages) || messages.length === 0) {
            return Array.isArray(messages) ? messages : [];
        }

        const canonical = new Map();
        messages.forEach(message => {
            if (!message) return;
            const key = this.resolveMessageKey(message);
            canonical.set(key, { ...canonical.get(key), ...message });
        });

        const combinedPromptSet = new Set(
            Array.from(canonical.values())
                .filter(msg => {
                    if (!msg) return false;
                    const sender = (msg.sender || '').toLowerCase();
                    const hasUserSender = sender === 'user';
                    const hasExplicitSender = sender === 'user' || sender === 'ai';
                    const hasPrompt = typeof msg.prompt === 'string' && msg.prompt.trim().length > 0;
                    const hasAttachments = (Array.isArray(msg.attachments) && msg.attachments.length > 0) ||
                                          (Array.isArray(msg.user_attachments) && msg.user_attachments.length > 0);
                    return !hasUserSender && hasPrompt && hasAttachments && !hasExplicitSender;
                })
                .map(msg => this.normalizePromptText(msg.prompt))
                .filter(Boolean)
        );

        const normalized = [];
        canonical.forEach(message => {
            if (!message) return;
            if ((message.sender || '').toLowerCase() === 'user') {
                const normalizedPrompt = this.normalizePromptText(message.content || message.prompt || '');
                if (normalizedPrompt && combinedPromptSet.has(normalizedPrompt)) {
                    return;
                }
            }
            normalized.push(message);
        });
        return normalized;
    }

    resolveMessageKey(message) {
        return message.id ||
            message.response_id ||
            message.message_id ||
            message.timestamp ||
            this.normalizePromptText(message.content || message.prompt || '') ||
            `msg-${Math.random().toString(36).slice(2)}`;
    }

    normalizePromptText(value) {
        if (typeof value !== 'string') return '';
        return value.replace(/\s+/g, ' ').trim().toLowerCase();
    }

    resolveHistoricalStyle(message, promptText = '') {
        const valid = new Set(['editorial', 'modern_blog', 'technical', 'chat', 'comparison', 'creative']);
        if (message && typeof message.style === 'string') {
            const stored = message.style.trim();
            if (valid.has(stored)) return stored;
        }

        const raw = typeof promptText === 'string' && promptText.trim()
            ? promptText
            : (message?.prompt || message?.content || message?.text || '');
        const text = String(raw || '').trim();

        if (text.length < 4) return 'chat';
        if (/^(hi|hello|hey|sup|yo|thanks|thank you|ok|okay|sure|cool|nice|got it|right|y(?:eah|es|up|a)|n(?:o|ah|ope))\b/i.test(text)) {
            return 'chat';
        }
        if (/\b(vs\.?|versus|compared? to|differences? between|which is better|which should i (?:use|pick|choose)|should i (?:use|pick|choose) \w+ or \w+)\b/i.test(text)) {
            return 'comparison';
        }
        if (/^(write (?:me )?(?:a |an )?(?:short )?(?:story|poem|haiku|sonnet|scene|essay|monologue|dialogue|fable)|tell me a story|describe (?:a |the )?(?:scene|setting)|continue the story|roleplay\b)/i.test(text)) {
            return 'creative';
        }
        if (/\b(write|implement|fix|debug|refactor|optimi[sz]e|build|create)\b.*?\b(function|method|class|component|module|script|test|loop|query|api|endpoint|migration|hook|reducer|service|controller|middleware|regex|bug|code)\b/i.test(text) ||
            /```/.test(text) ||
            /\bin (?:python|javascript|typescript|js|ts|rust|go|golang|java|c\+\+|c#|csharp|sql|bash|zsh|ruby|php|swift|kotlin)\b/i.test(text)) {
            return 'technical';
        }
        if (/^(give me |list |what are )?(?:tips|ways|best practices|hacks|ideas|examples|reasons|techniques|strategies) (?:on|for|to)\b/i.test(text) ||
            /^how (?:do i|to|can i|should i|do you)\b/i.test(text) ||
            /^(?:top|best) \d+\b/i.test(text)) {
            return 'modern_blog';
        }
        if (/^(explain|why does|why is|how does|what is the (?:meaning|history|theory|reason|significance|origin)|tell me about)\b/i.test(text) && text.length >= 50) {
            return 'editorial';
        }

        // Mirrors ResponseStyle.default() for records saved before `style`.
        return 'modern_blog';
    }

    generatePageId() {
        return `page-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 6)}`;
    }

    getActivePage(session) {
        if (!session || !Array.isArray(session.pages) || session.pages.length === 0) {
            return null;
        }
        if (!session.activePageId) {
            session.activePageId = session.pages[session.pages.length - 1].pageId;
        }
        return session.pages.find(page => page.pageId === session.activePageId) || session.pages[0];
    }

    setActivePageInternal(session, pageId) {
        if (!session || !Array.isArray(session.pages)) return null;
        const target = session.pages.find(page => page.pageId === pageId);
        if (!target) return null;
        session.activePageId = pageId;
        session.messages = target.messages;
        return target;
    }

    ensureSessionPages(sessionId) {
        if (!sessionId) return null;
        const session = this.chatHistory[sessionId];
        if (!session) return null;

        const normalizeMessages = (messages) => {
            if (!Array.isArray(messages)) return [];
            return this.coalesceMessages(messages);
        };

        if (!Array.isArray(session.pages) || session.pages.length === 0) {
            const initialMessages = normalizeMessages(session.messages || []);
            const pageId = session.activePageId || this.generatePageId();
            session.pages = [{
                pageId,
                parentPageId: null,
                forkedFromMessageId: null,
                createdAt: session.created_at || new Date().toISOString(),
                messages: initialMessages
            }];
            session.activePageId = pageId;
        } else {
            session.pages = session.pages.map(page => ({
                pageId: page.pageId || page.page_id || this.generatePageId(),
                parentPageId: page.parentPageId || page.parent_page_id || null,
                forkedFromMessageId: page.forkedFromMessageId || page.forked_from_message_id || null,
                createdAt: page.createdAt || page.created_at || session.created_at || new Date().toISOString(),
                messages: normalizeMessages(page.messages || [])
            }));
            if (!session.activePageId || !session.pages.some(page => page.pageId === session.activePageId)) {
                session.activePageId = session.pages[session.pages.length - 1].pageId;
            }
        }

        const activePage = this.getActivePage(session);
        session.messages = activePage ? activePage.messages : [];
        return session;
    }

    getActivePageId(sessionId = this.activeSessionId) {
        if (!sessionId) return null;
        const session = this.chatHistory[sessionId];
        if (!session) return null;
        return session.activePageId || null;
    }

    buildPageControls(session, { inline = false, wrapper = true, pagesOverride = null, anchorId = null } = {}) {
        if (!session) return null;
        const pages = Array.isArray(pagesOverride) && pagesOverride.length > 0
            ? pagesOverride
            : session.pages;
        if (!Array.isArray(pages) || pages.length <= 1) {
            return null;
        }
        const activeIndex = pages.findIndex(page => page.pageId === session.activePageId);
        const prevDisabled = activeIndex <= 0;
        const nextDisabled = activeIndex >= pages.length - 1;

        const prevBtn = document.createElement('button');
        prevBtn.type = 'button';
        prevBtn.className = 'page-nav page-prev';
        prevBtn.textContent = '<';
        prevBtn.disabled = prevDisabled;
        prevBtn.setAttribute('aria-label', 'Previous page');
        prevBtn.addEventListener('click', (event) => {
            event.preventDefault();
            event.stopPropagation();
            if (anchorId && pagesOverride) {
                this.changePageWithinGroup(-1, pages, anchorId);
            } else {
                const resolvedAnchor = this.resolveFooterAnchorId(event.currentTarget);
                this.changePage(-1, { anchorId: resolvedAnchor });
            }
        });

        const counter = document.createElement('span');
        counter.className = 'page-counter';
        counter.textContent = `${activeIndex + 1}/${pages.length}`;

        const nextBtn = document.createElement('button');
        nextBtn.type = 'button';
        nextBtn.className = 'page-nav page-next';
        nextBtn.textContent = '>';
        nextBtn.disabled = nextDisabled;
        nextBtn.setAttribute('aria-label', 'Next page');
        nextBtn.addEventListener('click', (event) => {
            event.preventDefault();
            event.stopPropagation();
            if (anchorId && pagesOverride) {
                this.changePageWithinGroup(1, pages, anchorId);
            } else {
                const resolvedAnchor = this.resolveFooterAnchorId(event.currentTarget);
                this.changePage(1, { anchorId: resolvedAnchor });
            }
        });

        const controls = [prevBtn, counter, nextBtn];
        if (!wrapper) {
            return controls;
        }
        const container = document.createElement('div');
        container.className = inline ? 'page-controls inline-page-controls' : 'page-controls';
        controls.forEach(el => container.appendChild(el));
        return container;
    }

    renderPageControls(sessionId = this.activeSessionId) {
        const host = this.elements.pageControls;
        if (host) {
            host.classList.add('hidden');
            host.innerHTML = '';
        }
    }

    changePage(delta, options = {}) {
        const sessionId = this.activeSessionId;
        if (!sessionId) return;
        const session = this.ensureSessionPages(sessionId);
        if (!session) return;
        const pages = session.pages;
        if (!Array.isArray(pages) || pages.length <= 1) return;
        const activeIndex = pages.findIndex(page => page.pageId === session.activePageId);
        const nextIndex = activeIndex + delta;
        if (nextIndex < 0 || nextIndex >= pages.length) return;
        this.setActivePageInternal(session, pages[nextIndex].pageId);
        this.renderActivePage(sessionId, options);
    }

    changePageWithinGroup(delta, pages, anchorId) {
        if (!Array.isArray(pages) || pages.length <= 1) return;
        const sessionId = this.activeSessionId;
        if (!sessionId) return;
        const session = this.ensureSessionPages(sessionId);
        if (!session) return;
        const currentIndex = pages.findIndex(page => page.pageId === session.activePageId);
        const nextIndex = currentIndex + delta;
        if (nextIndex < 0 || nextIndex >= pages.length) return;
        const nextPage = pages[nextIndex];
        if (!nextPage) return;
        this.setActivePageInternal(session, nextPage.pageId);
        this.renderActivePage(sessionId, { anchorId });
    }

    renderActivePage(sessionId = this.activeSessionId, options = {}) {
        if (!sessionId) return;
        const session = this.ensureSessionPages(sessionId);
        if (!session) return;

        // Reset chart stagger counter for new page render. Also clear any
        // chart-render timers still pending from a prior render of this page;
        // otherwise resetting the index mid-stagger bursts the queued
        // requests (each fires at its already-scheduled time but now off a
        // freshly-zeroed counter).
        if (Array.isArray(this._chartHistoryTimers)) {
            this._chartHistoryTimers.forEach(id => clearTimeout(id));
        }
        this._chartHistoryTimers = [];
        this._chartHistoryIndex = 0;

        if (typeof window !== 'undefined' && typeof window.clearChat === 'function') {
            window.clearChat({ force: true });
        } else if (this.elements.chatDisplay) {
            this.elements.chatDisplay.innerHTML = '';
        }

        // Get messages - use session.messages for simple sessions (no branching),
        // or active page's messages when branching is used
        const pages = session.pages || [];
        const hasBranching = pages.length > 1;
        let messages;
        if (hasBranching) {
            const activePage = this.getActivePage(session);
            messages = Array.isArray(activePage?.messages) ? activePage.messages : [];
        } else {
            // Simple session - use session.messages directly (what backend saves to)
            messages = Array.isArray(session.messages) ? session.messages : [];
        }
        const replayPromptSet = new Set(
            messages
                .map(message => {
                    if (!message) return '';
                    const sender = (message.sender || '').toLowerCase();
                    if (sender !== 'user') return '';
                    return this.normalizePromptText(
                        message.content ||
                        message.prompt ||
                        message.text ||
                        ''
                    );
                })
                .filter(Boolean)
        );
        this.replayUserPromptSet = replayPromptSet;
        this.isReplayingHistory = true;
        try {
            messages.forEach(message => {
                if (message) {
                    this.displayHistoricalMessage(message);
                }
            });
        } finally {
            this.isReplayingHistory = false;
            this.replayUserPromptSet = null;
        }

        // Defensive sweep: ensure every AI bubble carries the persisted
        // data-style attribute. Whatever render path produced the bubble,
        // this guarantees replay parity with the live-stream rendering.
        // Only sets the attribute when missing, so live-set values are
        // never overwritten.
        try {
            const styleByRid = new Map();
            messages.forEach(m => {
                if (m && m.response_id) {
                    styleByRid.set(m.response_id, this.resolveHistoricalStyle(m, m.prompt || ''));
                }
            });
            if (styleByRid.size && this.elements.chatDisplay) {
                this.elements.chatDisplay.querySelectorAll('.ai-message').forEach(msgEl => {
                    const id = msgEl.id || msgEl.dataset.messageId || '';
                    const rid = id.replace(/^ai-/, '');
                    const style = styleByRid.get(rid);
                    if (!style) return;
                    const tmc = msgEl.querySelector('.text-message-container');
                    const inner = tmc && tmc.querySelector('.text-message');
                    if (tmc && !tmc.hasAttribute('data-style')) tmc.setAttribute('data-style', style);
                    if (inner && !inner.hasAttribute('data-style')) inner.setAttribute('data-style', style);
                });
            }

            // Invariant: every AI bubble whose persisted message has a `style`
            // must carry that `data-style` after the sweep runs. If any
            // bubble is missing it, a future change has broken the sweep —
            // fail loudly in console so it's caught in real sessions.
            const violations = [];
            if (this.elements.chatDisplay) {
                this.elements.chatDisplay.querySelectorAll('.ai-message').forEach(msgEl => {
                    const id = msgEl.id || msgEl.dataset.messageId || '';
                    const rid = id.replace(/^ai-/, '');
                    const expected = styleByRid.get(rid);
                    if (!expected) return;
                    const actual = msgEl.querySelector('.text-message-container')?.getAttribute('data-style');
                    if (actual !== expected) {
                        violations.push({ rid, expected, actual: actual || '(missing)' });
                    }
                });
            }
            if (violations.length) {
                console.error('[style-replay] invariant violated for', violations.length, 'bubble(s):', violations);
            }
        } catch (_e) { /* sweep is best-effort */ }

        this.renderInlinePageAnchors(sessionId);
        if (options?.anchorId && this.scrollToAnchor(options.anchorId)) {
            this.updateFirstMessageFlag(messages.length > 0);
            return;
        }
        this.scrollChatToLatest();
        this.updateFirstMessageFlag(messages.length > 0);
    }

    renderInlinePageAnchors(sessionId = this.activeSessionId) {
        if (!sessionId) return;
        const session = this.chatHistory[sessionId];
        const container = this.elements.chatDisplay;
        if (!session || !container) return;
        container.querySelectorAll('.inline-page-controls').forEach(el => el.remove());

        if (!Array.isArray(session.pages) || session.pages.length <= 1) return;
        const anchorIds = new Set(
            session.pages
                .map(page => page?.forkedFromMessageId)
                .filter(Boolean)
        );
        if (!anchorIds.size) return;

        anchorIds.forEach(anchorId => {
            const messageEl = container.querySelector(`[data-message-key="${anchorId}"]`);
            if (!messageEl) return;
            const sender = (messageEl.dataset.sender || '').toLowerCase();
            if (sender && sender !== 'user') return;
            const controls = this.buildAnchorPageControls(session, anchorId);
            if (!controls) return;
            controls.dataset.anchorId = anchorId;

            const footer = this.ensureUserFooterElement(messageEl, anchorId);
            if (!footer) return;
            footer.dataset.anchorId = anchorId;

            footer.querySelectorAll('.inline-page-controls').forEach(el => el.remove());
            let copyButton = footer.querySelector('.copy-message-button');
            let editButton = footer.querySelector('.edit-button');
            if (copyButton) {
                copyButton.remove();
            }
            if (editButton) {
                editButton.remove();
            }

            footer.appendChild(controls);
            const editTargetId = messageEl.dataset.messageKey || messageEl.id || anchorId;
            if (copyButton) {
                footer.appendChild(copyButton);
            } else if (typeof appendCopyMessageButton === 'function') {
                appendCopyMessageButton(footer, editTargetId);
            }
            if (editButton) {
                footer.appendChild(editButton);
            } else if (typeof appendEditButton === 'function') {
                appendEditButton(footer, editTargetId);
            }
        });
    }

    buildAnchorPageControls(session, anchorId) {
        const group = this.getAnchorPageGroup(session, anchorId);
        if (!group || group.length <= 1) return null;
        return this.buildPageControls(session, {
            inline: true,
            wrapper: true,
            pagesOverride: group,
            anchorId
        });
    }

    getAnchorPageGroup(session, anchorId) {
        if (!session || !Array.isArray(session.pages) || !anchorId) {
            return null;
        }
        const basePages = session.pages.filter(page => {
            if (!Array.isArray(page?.messages)) return false;
            return page.messages.some(message => {
                if (!message) return false;
                const key = this.resolveMessageKey(message);
                return key === anchorId ||
                    message.id === anchorId ||
                    message.message_id === anchorId ||
                    message.response_id === anchorId;
            });
        });
        const derivedPages = session.pages.filter(page => page?.forkedFromMessageId === anchorId);
        const combined = [...basePages, ...derivedPages];
        if (!combined.length) return null;

        const unique = [];
        const seen = new Set();
        combined.forEach(page => {
            if (page && !seen.has(page.pageId)) {
                unique.push(page);
                seen.add(page.pageId);
            }
        });
        unique.sort((a, b) => {
            const tsA = new Date(a.createdAt || a.created_at || 0).getTime();
            const tsB = new Date(b.createdAt || b.created_at || 0).getTime();
            return tsA - tsB;
        });
        return unique;
    }

    resolveFooterAnchorId(element) {
        if (!element) return null;
        const footer = element.closest('.message-bubble-footer');
        if (footer?.dataset.anchorId) {
            return footer.dataset.anchorId;
        }
        const controls = element.closest('.inline-page-controls');
        if (controls?.dataset.anchorId) {
            return controls.dataset.anchorId;
        }
        return null;
    }

    scrollToAnchor(anchorId) {
        if (!anchorId || !this.elements?.chatDisplay) return false;
        const container = this.elements.chatDisplay;
        const target = container.querySelector(`[data-message-key="${anchorId}"]`);
        if (!target) return false;
        const scroller = document.getElementById('chatbox') || container;
        requestAnimationFrame(() => {
            if (typeof target.scrollIntoView === 'function') {
                target.scrollIntoView({ behavior: 'smooth', block: 'center', inline: 'nearest' });
            } else if (scroller) {
                scroller.scrollTop = target.offsetTop;
            }
        });
        return true;
    }

    cloneMessagesForPage(messages) {
        return messages.map(message => (message ? JSON.parse(JSON.stringify(message)) : message));
    }

    branchSessionAtMessage(messageId, newText) {
        if (!messageId || typeof newText !== 'string' || !this.activeSessionId) return null;
        const session = this.ensureSessionPages(this.activeSessionId);
        if (!session) return null;
        const activePage = this.getActivePage(session);
        if (!activePage) return null;

        const messages = Array.isArray(activePage.messages) ? activePage.messages : [];
        const targetIndex = messages.findIndex(entry => {
            if (!entry) return false;
            return (
                entry.id === messageId ||
                entry.response_id === messageId ||
                entry.message_id === messageId ||
                this.resolveMessageKey(entry) === messageId
            );
        });
        if (targetIndex < 0) return null;

        const clonedMessages = this.cloneMessagesForPage(messages.slice(0, targetIndex + 1));
        const targetMessage = clonedMessages[targetIndex];
        if (targetMessage) {
            targetMessage.id = targetMessage.id || targetMessage.message_id || messageId;
            targetMessage.content = newText;
            targetMessage.prompt = newText;
            targetMessage.text = newText;
            targetMessage.timestamp = new Date().toISOString();
            delete targetMessage.response;
            delete targetMessage.formatted_response;
            delete targetMessage.search_results;
            delete targetMessage.attachments;
            delete targetMessage.suppress_text;
            delete targetMessage.is_code;
        }

        const newPageId = this.generatePageId();
        const newPage = {
            pageId: newPageId,
            parentPageId: activePage.pageId || null,
            forkedFromMessageId: messageId,
            createdAt: new Date().toISOString(),
            messages: clonedMessages
        };

        session.pages = Array.isArray(session.pages) ? [...session.pages, newPage] : [newPage];
        this.setActivePageInternal(session, newPageId);
        const anchorTargetId = targetMessage?.id || messageId;
        this.renderActivePage(this.activeSessionId, { anchorId: anchorTargetId });

        if (this.activeSessionId) {
            this.saveSession(this.activeSessionId, { silent: true }).catch(error => {
                console.error('Failed to persist branched session', error);
            });
        }

        return {
            pageId: newPageId,
            messageId: targetMessage?.id || messageId,
            text: newText
        };
    }

    branchSessionForRetry(aiMessageId, originalPrompt) {
        if (!aiMessageId || !this.activeSessionId) return null;
        const session = this.ensureSessionPages(this.activeSessionId);
        if (!session) return null;
        const activePage = this.getActivePage(session);
        if (!activePage) return null;

        const messages = Array.isArray(activePage.messages) ? activePage.messages : [];

        // Find the USER message entry that corresponds to this AI response
        // First, try to find by matching the original prompt text (most reliable)
        let userEntryIndex = -1;
        if (originalPrompt) {
            const normalizedPrompt = this.normalizePromptText(originalPrompt);
            userEntryIndex = messages.findIndex(entry => {
                if (!entry) return false;
                const sender = (entry.sender || '').toLowerCase();
                if (sender !== 'user') return false;
                const entryPrompt = entry.prompt || entry.content || entry.text || '';
                return this.normalizePromptText(entryPrompt) === normalizedPrompt;
            });
        }

        // Fallback: find AI entry first, then get the user entry before it
        if (userEntryIndex < 0) {
            const cleanAiId = aiMessageId.replace(/^ai-/, '');
            const aiIndex = messages.findIndex(entry => {
                if (!entry) return false;
                const sender = (entry.sender || '').toLowerCase();
                if (sender !== 'ai') return false;
                const responseId = entry.response_id || entry.id;
                return (
                    responseId === aiMessageId ||
                    responseId === cleanAiId ||
                    entry.id === cleanAiId ||
                    entry.id === aiMessageId
                );
            });
            // User entry should be right before the AI entry
            if (aiIndex > 0) {
                const prevEntry = messages[aiIndex - 1];
                if (prevEntry && (prevEntry.sender || '').toLowerCase() === 'user') {
                    userEntryIndex = aiIndex - 1;
                }
            }
        }

        // Last resort: find first user entry, or if only one entry use it
        if (userEntryIndex < 0) {
            userEntryIndex = messages.findIndex(entry => {
                if (!entry) return false;
                const sender = (entry.sender || '').toLowerCase();
                return sender === 'user';
            });
        }
        if (userEntryIndex < 0 && messages.length === 1) {
            userEntryIndex = 0;
        }

        if (userEntryIndex < 0) return null;

        const userEntry = messages[userEntryIndex];
        const userPrompt = originalPrompt || userEntry?.prompt || userEntry?.content || userEntry?.text || '';

        // Get the messageKey from the user message DOM element
        // This is critical for pagination to work - forkedFromMessageId must match user element's data-message-key
        let messageKey = null;
        const aiElement = document.getElementById(aiMessageId);
        if (aiElement) {
            // Find the preceding user message element
            let prev = aiElement.previousElementSibling;
            while (prev) {
                const sender = (prev.dataset?.sender || '').toLowerCase();
                if (sender === 'user') {
                    messageKey = prev.dataset?.messageKey || prev.id?.replace(/^user-/, '');
                    break;
                }
                prev = prev.previousElementSibling;
            }
        }

        // Fallback to resolving from entry if DOM lookup failed
        if (!messageKey) {
            messageKey = userEntry?.id || this.resolveMessageKey(userEntry) || userEntry?.message_id;
        }

        // Clone messages up to and including the user entry (excluding AI response)
        const clonedMessages = this.cloneMessagesForPage(messages.slice(0, userEntryIndex + 1));
        const clonedUserMessage = clonedMessages[userEntryIndex];
        if (clonedUserMessage) {
            // Set the id to match the DOM element's messageKey
            clonedUserMessage.id = messageKey;
            clonedUserMessage.message_id = messageKey;
            clonedUserMessage.content = userPrompt;
            clonedUserMessage.prompt = userPrompt;
            clonedUserMessage.text = userPrompt;
            clonedUserMessage.timestamp = new Date().toISOString();
            // Clear any response data (we're regenerating)
            delete clonedUserMessage.response;
            delete clonedUserMessage.formatted_response;
            delete clonedUserMessage.search_results;
            delete clonedUserMessage.response_id;
        }

        // Create new page with forkedFromMessageId matching the user element's data-message-key
        const newPageId = this.generatePageId();
        const newPage = {
            pageId: newPageId,
            parentPageId: activePage.pageId || null,
            forkedFromMessageId: messageKey,
            createdAt: new Date().toISOString(),
            messages: clonedMessages
        };

        session.pages = Array.isArray(session.pages) ? [...session.pages, newPage] : [newPage];
        this.setActivePageInternal(session, newPageId);
        this.renderActivePage(this.activeSessionId, { anchorId: messageKey });

        if (this.activeSessionId) {
            this.saveSession(this.activeSessionId, { silent: true }).catch(error => {
                console.error('Failed to persist branched session', error);
            });
        }

        return {
            pageId: newPageId,
            messageId: messageKey,
            text: userPrompt
        };
    }

    updateLastAiMessage(update = {}) {
        if (!this.activeSessionId) return;
        const session = this.ensureSessionPages(this.activeSessionId);
        if (!session || !Array.isArray(session.messages) || session.messages.length === 0) {
            return;
        }

        const targetId = update.messageId;

        const findIndex = () => {
            if (targetId) {
                return session.messages.findIndex(msg =>
                    msg && (msg.id === targetId || msg.response_id === targetId)
                );
            }
            for (let i = session.messages.length - 1; i >= 0; i--) {
                const entry = session.messages[i];
                if (!entry) continue;
                if ((entry.sender || '').toLowerCase() !== 'ai') {
                    continue;
                }
                return i;
            }
            return -1;
        };

        const index = findIndex();
        if (index === -1) return;

        const entry = session.messages[index];
        if (!entry) return;

        if (Array.isArray(update.attachments)) {
            entry.attachments = update.attachments.slice();
        }
        if (typeof update.content === 'string') {
            entry.content = update.content;
        }
        if (typeof update.messageType === 'string' && update.messageType.trim()) {
            entry.message_type = update.messageType.trim();
            entry.is_code = entry.message_type === 'code';
        }
        if (typeof update.suppressText === 'boolean') {
            entry.suppress_text = update.suppressText;
        }
        if (update.timestamp) {
            entry.timestamp = update.timestamp;
        }
        if (update.render_mode) {
            entry.render_mode = update.render_mode;
        }
        if (update.artifact_reference) {
            const reference = Object.assign({}, update.artifact_reference);
            entry.artifact_reference = reference;
            entry.artifact_id = reference.artifactId || reference.id || null;
            entry.artifact_title = reference.title || entry.artifact_title || null;
            entry.artifact_summary = reference.summary || entry.artifact_summary || null;
            entry.artifact_type = reference.type || entry.artifact_type || null;
            entry.artifact_language = reference.primaryLanguage || reference.language || entry.artifact_language || null;
            entry.artifact_version_id = reference.versionId || entry.artifact_version_id || null;
            entry.artifact_version_number = reference.versionNumber || entry.artifact_version_number || null;
            entry.artifact_entrypoint = reference.entrypoint || entry.artifact_entrypoint || null;
            if (typeof reference.isActive === 'boolean') {
                entry.artifact_is_active = reference.isActive;
            }
            if (typeof reference.pending === 'boolean') {
                entry.artifact_pending = reference.pending;
            }
        }
        if (update.artifact) {
            entry.artifact_draft = Object.assign({}, update.artifact);
        }
        if (typeof update.reasoning === 'string') {
            entry.reasoning = update.reasoning;
        }
        if (update.responseId) {
            entry.response_id = update.responseId;
        }
        if (typeof update.style === 'string' && update.style.trim()) {
            entry.style = update.style.trim();
        }

        this.markSessionDirty(this.activeSessionId);
    }

    /**
     * Add or update an AI message in local history.
     * Called from codeWindow.js when a response is received.
     * @param {Object} messageData - The message data to add/update
     */
    addOrUpdateAiMessage(messageData = {}) {
        if (!this.activeSessionId || !messageData.response_id) return;
        const session = this.ensureSessionPages(this.activeSessionId);
        if (!session) return;

        // Use session.messages for simple sessions, active page for branching
        const pages = session.pages || [];
        const hasBranching = pages.length > 1;
        let messages;
        if (hasBranching) {
            const activePage = this.getActivePage(session);
            if (!activePage) return;
            if (!Array.isArray(activePage.messages)) {
                activePage.messages = [];
            }
            messages = activePage.messages;
        } else {
            // Simple session - use session.messages (what backend saves to)
            if (!Array.isArray(session.messages)) {
                session.messages = [];
            }
            messages = session.messages;
        }

        // Check if message already exists
        const existingIndex = messages.findIndex(msg =>
            msg && (msg.response_id === messageData.response_id || msg.id === messageData.response_id)
        );

        // Extract model meta (from streaming path or flat fields)
        const mm = messageData.model_meta || {};
        const modelId = mm.model_id || messageData.model_id || null;
        const modelLabel = mm.model_label || messageData.model_label || null;
        const modelProvider = mm.model_provider || messageData.model_provider || null;
        const modelProviderLabel = mm.model_provider_label || messageData.model_provider_label || null;
        const autoSelected = mm.auto_selected || messageData.auto_selected || false;
        const taskType = mm.task_type || messageData.task_type || null;

        const messageEntry = {
            sender: 'AI',
            content: messageData.content || '',
            response: messageData.content || '',
            prompt: messageData.prompt || '',
            timestamp: messageData.timestamp || new Date().toISOString(),
            response_id: messageData.response_id,
            is_code: messageData.is_code || false,
            render_mode: messageData.render_mode || 'inline',
            reasoning: messageData.reasoning || null,
            email: messageData.email || null,
            reminder: messageData.reminder || null,
            crypto_chart: messageData.crypto_chart || null,
            // Model attribution — persists to Firestore
            model_id: modelId,
            model_label: modelLabel,
            model_provider: modelProvider,
            model_provider_label: modelProviderLabel,
            auto_selected: autoSelected,
            task_type: taskType
        };
        if (messageData.style) {
            messageEntry.style = messageData.style;
        }

        // Persist search results (web, images, videos, render_hint, embed_video)
        if (messageData.search_results && typeof messageData.search_results === 'object') {
            messageEntry.search_results = messageData.search_results;
        }

        if (existingIndex >= 0) {
            // Update existing message
            Object.assign(messages[existingIndex], messageEntry);
        } else {
            // Add new message
            messages.push(messageEntry);
        }

        // Update DOM element with model data for quality signal collection
        if (modelId && messageData.response_id) {
            const msgEl = document.querySelector(`[data-message-key="${messageData.response_id}"]`);
            if (msgEl) {
                msgEl.dataset.modelId = modelId;
                if (modelLabel) msgEl.dataset.modelLabel = modelLabel;
                if (modelProvider) msgEl.dataset.modelProvider = modelProvider;
                if (modelProviderLabel) msgEl.dataset.modelProviderLabel = modelProviderLabel;
                if (taskType) msgEl.dataset.taskType = taskType;
                if (autoSelected) msgEl.dataset.autoSelected = 'true';
            }
        }

        this.markSessionDirty(this.activeSessionId);
    }

    /**
     * Add a chat session to a project. Opens a popover listing all projects;
     * clicking one PATCHes the session with project_id and refreshes the sidebar.
     * @param {string} sessionId - The session ID to add to a project.
     */
    addToProjectSession(sessionId) {
        const projects = window.projects?.getList?.() || [];
        if (projects.length === 0) {
            this.showNotification('No projects yet — create one first', 'info');
            return;
        }

        // Close any existing picker.
        document.querySelectorAll('.session-project-picker').forEach((n) => n.remove());

        const picker = document.createElement('div');
        picker.className = 'session-project-picker';
        picker.setAttribute('role', 'dialog');
        picker.setAttribute('aria-label', 'Move chat to project');

        const heading = document.createElement('div');
        heading.className = 'session-project-picker__heading';
        heading.textContent = 'Move to project';
        picker.appendChild(heading);

        const list = document.createElement('div');
        list.className = 'session-project-picker__list';
        list.setAttribute('role', 'listbox');
        projects.forEach((project) => {
            const item = document.createElement('button');
            item.type = 'button';
            item.className = 'session-project-picker__item';
            item.dataset.projectId = project.id;
            item.setAttribute('role', 'option');

            const icon = document.createElement('span');
            icon.className = 'session-project-picker__icon';
            icon.style.setProperty('--picker-project-color', project.color || 'currentColor');
            if (window.PROJECT_ICONS?.has?.(project.icon || 'code')) {
                icon.innerHTML = window.PROJECT_ICONS.svg(project.icon || 'code');
            } else {
                icon.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M20 20a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.93a2 2 0 0 1-1.66-.9l-.82-1.2A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2Z"/><path d="M2 10h20"/></svg>';
            }
            const name = document.createElement('span');
            name.className = 'session-project-picker__name';
            name.textContent = project.name || 'Untitled project';
            item.append(icon, name);
            list.appendChild(item);
        });
        picker.appendChild(list);

        const create = document.createElement('button');
        create.type = 'button';
        create.className = 'session-project-picker__create';
        create.innerHTML = '<span aria-hidden="true">＋</span><span>New project</span>';
        create.addEventListener('click', () => {
            picker.remove();
            document.getElementById('project-create-btn')?.click();
        });
        picker.appendChild(create);

        // Position near the session's menu trigger.
        const sessionRow = document.querySelector(`.chat-tab[data-session-id="${sessionId}"]`);
        const anchor = sessionRow?.querySelector('[data-actions-toggle]') || sessionRow;
        const rect = (anchor || document.body).getBoundingClientRect();
        picker.style.position = 'fixed';
        // Must beat the sidebar (z-index 1100) and the create-project modal (1000).
        picker.style.zIndex = '1500';
        document.body.appendChild(picker);
        const pickerRect = picker.getBoundingClientRect();
        const gap = 8;
        let left = rect.right + gap;
        if (left + pickerRect.width > window.innerWidth - gap) {
            left = rect.left - pickerRect.width - gap;
        }
        const top = Math.min(
            Math.max(gap, rect.top - 8),
            window.innerHeight - pickerRect.height - gap,
        );
        picker.style.left = `${Math.max(gap, left)}px`;
        picker.style.top = `${top}px`;

        picker.addEventListener('click', async (e) => {
            const item = e.target.closest('[data-project-id]');
            if (!item) return;
            const projectId = item.dataset.projectId;
            picker.remove();
            try {
                const csrf = document.querySelector('meta[name="csrf-token"]')?.content || '';
                const resp = await fetch(`/chat/${encodeURIComponent(sessionId)}`, {
                    method: 'PATCH',
                    credentials: 'same-origin',
                    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf },
                    body: JSON.stringify({ project_id: projectId }),
                });
                if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
                const proj = projects.find((p) => p.id === projectId);
                // Update Recents immediately, then reconcile with the server
                // through the existing history refresh event.
                const movedRow = [...document.querySelectorAll('.chat-tab[data-session-id]')]
                    .find((row) => row.dataset.sessionId === sessionId);
                if (movedRow) {
                    movedRow.remove();
                }
                if (this.chatHistory?.[sessionId]) this.chatHistory[sessionId].project_id = projectId;
                this.showNotification(`Added to ${proj?.name || 'project'}`, 'success');
                window.dispatchEvent(new CustomEvent('projects:session-moved', { detail: { sessionId, projectId } }));
            } catch (err) {
                this.showNotification('Failed to add to project', 'error');
                console.error(err);
            }
        });

        const focusable = [...picker.querySelectorAll('button')];
        picker.addEventListener('keydown', (event) => {
            const current = focusable.indexOf(document.activeElement);
            if (event.key === 'Escape') {
                event.preventDefault();
                picker.remove();
                anchor?.focus();
            } else if (event.key === 'ArrowDown') {
                event.preventDefault();
                focusable[(current + 1 + focusable.length) % focusable.length]?.focus();
            } else if (event.key === 'ArrowUp') {
                event.preventDefault();
                focusable[(current - 1 + focusable.length) % focusable.length]?.focus();
            }
        });
        focusable[0]?.focus({ preventScroll: true });

        // Close on outside click (deferred so this click doesn't immediately close it).
        setTimeout(() => {
            const closeHandler = (ev) => {
                if (!picker.contains(ev.target)) {
                    picker.remove();
                    document.removeEventListener('click', closeHandler);
                }
            };
            document.addEventListener('click', closeHandler);
        }, 0);
    }

    /**
     * Share a chat session by copying its URL to the clipboard.
     * @param {string} sessionId - The session ID to share.
     */
    async shareSession(sessionId) {
        try {
            const shareUrl = `${window.location.origin}/c/${sessionId}`;
            await navigator.clipboard.writeText(shareUrl);
            this.showNotification('Share link copied to clipboard', 'success');
        } catch (error) {
            this.showNotification('Failed to share chat', 'error');
            console.error(error);
        }
    }

    /**
     * Save a chat session to the server.
     * @param {string} sessionId - The session ID to save.
     */
    async saveSession(sessionId, { silent = false } = {}) {
        if (!sessionId || !this.chatHistory[sessionId]) return;
        // Skip saving for ephemeral sessions: guest mode AND temporary
        // (incognito) chats. Without the temp_ skip, /chat/save's fallback
        // path silently CREATED a Firestore doc with status='active',
        // defeating the incognito promise on every message.
        if (sessionId.startsWith('guest_') || sessionId.startsWith('temp_')) return;
        const session = this.ensureSessionPages(sessionId) || this.chatHistory[sessionId];
        const payload = {};

        // Helper to strip base64 dataUrls from attachments to prevent Firestore size errors
        const sanitizeAttachments = (attachments) => {
            if (!Array.isArray(attachments)) return attachments;
            return attachments.map(att => {
                if (!att || typeof att !== 'object') return att;
                const { dataUrl, ...rest } = att;
                // Also sanitize url if it's a base64 string
                if (rest.url && typeof rest.url === 'string' && rest.url.startsWith('data:')) {
                    delete rest.url;
                }
                return rest;
            });
        };

        const sanitizeMessages = (messages) => {
            if (!Array.isArray(messages)) return messages;
            return messages.map(msg => {
                if (!msg || typeof msg !== 'object') return msg;
                const sanitized = { ...msg };
                if (sanitized.attachments) {
                    sanitized.attachments = sanitizeAttachments(sanitized.attachments);
                }
                if (sanitized.user_attachments) {
                    sanitized.user_attachments = sanitizeAttachments(sanitized.user_attachments);
                }
                return sanitized;
            });
        };

        if (Array.isArray(session?.messages)) {
            payload.messages = sanitizeMessages(session.messages);
        }
        if (Array.isArray(session?.pages)) {
            // Also sanitize messages within pages
            payload.pages = session.pages.map(page => {
                if (!page || typeof page !== 'object') return page;
                return {
                    ...page,
                    messages: sanitizeMessages(page.messages)
                };
            });
        }
        if (session?.activePageId) {
            payload.active_page_id = session.activePageId;
        }
        if (Object.keys(payload).length === 0) {
            return;
        }

        try {
            await this.fetchApi(`/chat/save/${sessionId}`, 'POST', payload);
            this.dirtySessions.delete(sessionId);
            if (!silent) {
                this.showNotification('Chat saved successfully', 'success');
            }
        } catch (error) {
            if (!silent) {
                this.showNotification('Failed to save chat', 'error');
            }
            console.error(error);
        }
    }

    /**
     * Archive a chat session and remove it from the active list.
     * @param {string} sessionId - The session ID to archive.
     */
    async archiveSession(sessionId) {
        if (!sessionId || !this.chatHistory[sessionId]) return;

        try {
            await this.fetchApi('/chat/archive', 'POST', { session_id: sessionId });
            delete this.chatHistory[sessionId];
            this.dirtySessions.delete(sessionId);
            // Clean up notification state for archived session
            if (window.BackgroundRequestManager?.clearRequest) window.BackgroundRequestManager.clearRequest(sessionId);
            if (window.BackgroundRequestManager?.clearNotification) window.BackgroundRequestManager.clearNotification(sessionId);

            const wasActive = this.activeSessionId === sessionId;
            if (wasActive) {
                this.activeSessionId = null;
                await this.createNewChat(null, {
                    showOverlay: false,
                    notifySuccess: false
                });
            }

            this.renderChatTabs();
        } catch (error) {
            console.error(error);
        }
    }

    /**
     * Pin or unpin a chat session.
     * @param {string} sessionId - The session ID to pin/unpin.
     */
    async pinSession(sessionId) {
        // Re-read the session immediately before computing the toggle so we
        // don't act on a stale is_pinned value captured earlier.
        const session = this.chatHistory[sessionId];
        if (!session) return;

        const shouldPin = !session.is_pinned;

        try {
            const response = await this.fetchApi('/chat/pin', 'POST', {
                session_id: sessionId,
                pinned: shouldPin
            });

            session.is_pinned = Boolean(response.is_pinned);
            session.pinned_at = response.pinned_at || null;
            this.renderChatTabs();
        } catch (error) {
            console.error(error);
        }
    }

    /**
     * Rename a chat session via a modal prompt (Claude-style).
     * @param {string} sessionId - The session ID to rename.
     */
    async renameSession(sessionId) {
        this.closeOpenTabMenu();
        this.showRenameModal(sessionId);
    }

    /**
     * Show a modal dialog to rename a chat session.
     * @param {string} sessionId - The session ID to rename.
     */
    showRenameModal(sessionId) {
        const currentName = this.chatHistory[sessionId]?.session_name || '';

        const modalOverlay = document.createElement('div');
        modalOverlay.className = 'modal-overlay';
        modalOverlay.innerHTML = `
            <div class="modal-content rename-modal">
                <h2>Rename chat</h2>
                <input type="text" class="rename-modal-input" value="" placeholder="Chat name" maxlength="120" />
                <div class="modal-actions">
                    <button class="btn-cancel" type="button">Cancel</button>
                    <button class="btn-save" type="button">Save</button>
                </div>
            </div>
        `;

        document.body.appendChild(modalOverlay);

        const input = modalOverlay.querySelector('.rename-modal-input');
        const btnSave = modalOverlay.querySelector('.btn-save');
        const btnCancel = modalOverlay.querySelector('.btn-cancel');

        input.value = currentName;
        // Autofocus + select after the open animation settles
        requestAnimationFrame(() => {
            input.focus();
            input.select();
        });

        const close = () => {
            if (modalOverlay.parentNode) modalOverlay.remove();
        };

        const doSave = async () => {
            const newName = input.value.trim();
            if (!newName || newName === currentName) { close(); return; }

            btnSave.disabled = true;
            btnCancel.disabled = true;
            input.disabled = true;

            try {
                const response = await this.fetchApi('/chat/rename', 'POST', {
                    session_id: sessionId,
                    name: newName,
                });
                const finalName = response.updated_name || newName;
                this.chatHistory[sessionId].session_name = finalName;
                this.chatHistory[sessionId].manuallyRenamed = true;
                this.chatHistory[sessionId].autoTitleEligible = false;
                this.renderChatTabs();
                if (this.activeSessionId === sessionId) this.updatePageTitle(sessionId);
                this.showNotification('Chat renamed', 'success');
            } catch (error) {
                console.error('rename failed', error);
                this.showNotification('Failed to rename chat', 'error');
            } finally {
                close();
            }
        };

        btnSave.onclick = doSave;
        btnCancel.onclick = close;
        modalOverlay.addEventListener('click', (e) => {
            if (e.target === modalOverlay) close();
        });
        input.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') { e.preventDefault(); doSave(); }
            else if (e.key === 'Escape') { e.preventDefault(); close(); }
        });
    }

    /**
     * Delete a chat session with confirmation modal.
     * @param {string} sessionId - The session ID to delete.
     */
    async deleteSession(sessionId) {
        this.showDeleteModal(sessionId);
    }

    /**
     * Display the delete confirmation modal.
     * @param {string} sessionId - The session ID to delete.
     */
    showDeleteModal(sessionId) {
        const session = this.chatHistory[sessionId];
        const rawName = session?.session_name;
        const chatName = (rawName && !this.isGenericName(rawName)) ? rawName : 'this chat';
        const safeName = String(chatName).replace(/[&<>"']/g, (c) => ({
            '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
        }[c]));

        // Create modal elements
        const modalOverlay = document.createElement('div');
        modalOverlay.className = 'modal-overlay';
        modalOverlay.innerHTML = `
            <div class="modal-content">
                <h2>Delete chat?</h2>
                <p>This will delete <strong>${safeName}.</strong></p>
                <p>This removes the conversation from your chat history. The AI's memory of you stays intact — to clear that, use <strong>Forget Me</strong> in Memory settings.</p>
                <div class="modal-actions">
                    <button class="btn-cancel">Cancel</button>
                    <button class="btn-delete">Delete</button>
                </div>
            </div>
        `;
    
        // Add modal to the document body
        document.body.appendChild(modalOverlay);

        // Tap backdrop to dismiss
        modalOverlay.addEventListener('click', (e) => {
            if (e.target === modalOverlay) modalOverlay.remove();
        });

        // Event listener for Cancel button
        modalOverlay.querySelector('.btn-cancel').onclick = () => {
            modalOverlay.remove();
        };
    
        // Event listener for Delete button
        modalOverlay.querySelector('.btn-delete').onclick = async () => {
            document.body.removeChild(modalOverlay);
            try {
                await this.fetchApi('/chat/delete', 'POST', { session_id: sessionId });

                delete this.chatHistory[sessionId];
                this.dirtySessions.delete(sessionId);
                // Clean up notification state for deleted session
                if (window.BackgroundRequestManager?.clearRequest) window.BackgroundRequestManager.clearRequest(sessionId);
                if (window.BackgroundRequestManager?.clearNotification) window.BackgroundRequestManager.clearNotification(sessionId);
                
                const deletedActiveSession = this.activeSessionId === sessionId;
                this.generatedImages = (this.generatedImages || []).filter(image => image.sessionId !== sessionId);
                this.renderImagesGallery();

                if (deletedActiveSession) {
                    this.enterHomeView();
                } else {
                    this.renderChatTabs();
                }
                
            } catch (error) {
                console.error(error);
            }
        };
    }

    // =======================
    // Message Management
    // =======================

    /**
     * Delegate message sending to the global handler (preserves older integrations).
     */
    async sendMessage(forcedPrompt = null) {
        if (typeof window !== 'undefined' && typeof window.sendMessage === 'function') {
            return window.sendMessage(typeof forcedPrompt === 'string' ? forcedPrompt : undefined);
        }
        console.warn('Global sendMessage handler is unavailable.');
        return null;
    }
    

    /**
     * Display a historical message in the chat interface.
     * @param {Object} message - The message object.
     */
    displayHistoricalMessage(message) {
        // Skip zero-width space placeholders (search banner remnants)
        const _resp = message?.response || message?.content || '';
        if (_resp === '\u200B' || _resp === '\u200b') return;

        const hasAttachments = (message?.attachments && message.attachments.length > 0) ||
                               (message?.user_attachments && message.user_attachments.length > 0);
        if (!message || (!message.prompt && !message.response && !message.content && !hasAttachments)) {
            console.warn('Message content is undefined or missing. Skipping message:', message);
            return;
        }

        const chatDisplay = this.elements.chatDisplay;
        if (!chatDisplay) {
            console.error('Chat display element not found');
            return;
        }
        const messageKey = message.id || message.message_id || message.response_id || this.resolveMessageKey(message);

        // Handle the user's message (prompt)
        const userPromptSource = message.prompt || message.content || message.text || '';
        const normalizedMsgSender = (message.sender || '').toLowerCase();

        // Helper to detect if attachment is user-uploaded (vs AI-generated)
        const isUserUpload = (att) => {
            if (!att) return false;
            // AI-generated images have specific markers - check these FIRST
            if (att.isImage && (att.provider || att.seed !== undefined)) return false;
            if (att.name === 'AI generated image') return false;
            // AI images stored locally go to /generated_images/ subfolder
            const url = att.url || att.path || '';
            if (url.includes('/generated_images/')) return false;
            // User uploads typically have these fields from the upload response
            if (att.original_filename || att.relative_path || att.md5_hash) return true;
            // Check if URL points to /uploads/ (server-stored file) - but not generated_images
            if (url.includes('/uploads/')) return true;
            // If it has a file extension-like name, likely user upload
            if (att.name && /\.\w{2,5}$/.test(att.name)) return true;
            return false;
        };

        // Check user_attachments first (preferred field for user uploads)
        let rawUserAttachments = Array.isArray(message.user_attachments) ? message.user_attachments : [];

        // For backward compatibility: check message.attachments for user uploads
        const allAttachments = Array.isArray(message.attachments) ? message.attachments : [];
        if (rawUserAttachments.length === 0 && allAttachments.length > 0) {
            // If sender is explicitly 'user', all attachments are user uploads
            if (normalizedMsgSender === 'user') {
                rawUserAttachments = allAttachments;
            } else {
                // For combined messages, detect user uploads by their properties
                rawUserAttachments = allAttachments.filter(isUserUpload);
            }
        }
        const userAttachments = this.applyAttachmentPromptFallback(rawUserAttachments, userPromptSource);

        // AI attachments: those that are NOT user uploads
        const aiAttachmentsRaw = allAttachments.filter(att => !isUserUpload(att));
        const aiPromptSource = message.originalPrompt || message.response || '';
        const aiAttachments = this.applyAttachmentPromptFallback(aiAttachmentsRaw, aiPromptSource);
        const aiContent = (message.response || message.content || '').trim();
        const hasAiContent = typeof aiContent === 'string' && aiContent.length > 0;
        const hasAiAttachments = aiAttachments.length > 0;

        const normalizedSender = (message.sender || '').toLowerCase();
        const normalizedPromptText = this.normalizePromptText(userPromptSource);
        const hasRecordedUserPrompt = Boolean(
            normalizedPromptText &&
            this.replayUserPromptSet instanceof Set &&
            this.replayUserPromptSet.has(normalizedPromptText)
        );
        const shouldRenderUserPrompt = normalizedSender === 'user' ||
            (!message.sender && message.prompt);
        const shouldRenderSyntheticUserPrompt = !shouldRenderUserPrompt &&
            !!normalizedPromptText &&
            !hasRecordedUserPrompt &&
            (hasAiAttachments ||
                message.message_type === 'image' ||
                message.message_type === 'image-placeholder' ||
                message.message_type === 'image_placeholder' ||
                message.suppress_text);

        if (shouldRenderUserPrompt) {
            const content = message.content || message.prompt;
            const messageId = messageKey || generateMessageId();
            this.renderMessage({
                id: `user-${messageId}`,
                sender: 'user',
                content: content,
                timestamp: message.timestamp || null,
                originalPrompt: content, // Store original prompt
                isHistory: true,
                timestamp: message.timestamp,
                attachments: userAttachments,
                suppressText: !!message.suppress_text_user,
                style: message.style,
                messageKey
            });
            if (
                normalizedPromptText &&
                this.replayUserPromptSet instanceof Set &&
                !this.replayUserPromptSet.has(normalizedPromptText)
            ) {
                this.replayUserPromptSet.add(normalizedPromptText);
            }
        } else if (shouldRenderSyntheticUserPrompt) {
            const syntheticId = messageKey || generateMessageId();
            this.renderMessage({
                id: `user-synth-${syntheticId}`,
                sender: 'user',
                content: userPromptSource,
                timestamp: message.timestamp || null,
                originalPrompt: userPromptSource,
                isHistory: true,
                timestamp: message.timestamp,
                attachments: userAttachments,
                suppressText: false,
                style: message.style,
                messageKey
            });
            if (
                normalizedPromptText &&
                this.replayUserPromptSet instanceof Set &&
                !this.replayUserPromptSet.has(normalizedPromptText)
            ) {
                this.replayUserPromptSet.add(normalizedPromptText);
            }
        }

        // Handle the AI's response
        if (message.sender === 'AI' || hasAiContent || hasAiAttachments || message.suppress_text) {
            const messageId = message.response_id || message.id || generateMessageId();
            const hasAiImages = aiAttachments.some(att => this.isAttachmentImage(att));
            const replayStyle = this.resolveHistoricalStyle(message, userPromptSource);

            const sources = message.search_results && typeof message.search_results === 'object'
                ? message.search_results : [];

            // Debug: Log crypto_chart data
            console.log('[Chart History] Processing AI message:', messageId);
            console.log('[Chart History] Message has crypto_chart:', !!message.crypto_chart, message.crypto_chart);

            if (hasAiImages) {
                // Match live-stream behavior (codeWindow.js:3499): image
                // responses render the image only, not the accompanying
                // `response` text. Without this, the stored AI narration
                // ("Here's a retro-style Nokia…") reappears as a text
                // block on refresh even though it was never visible during
                // the live stream.
                this.renderMessage({
                    id: `ai-${messageId}`,
                    sender: 'ai',
                    content: '',
                    timestamp: message.timestamp || null,
                    isCode: false,
                    isHistory: true,
                    attachments: aiAttachments,
                    suppressText: true,
                    forceButtons: true,
                    originalPrompt: userPromptSource,
                    sources,
                    messageKey,
                    style: replayStyle,
                    email: message.email || null,
                    reminder: message.reminder || null
                });
                if (typeof window !== 'undefined' && typeof window.updateSourcesButton === 'function') {
                    window.updateSourcesButton(sources);
                }
                // Still render chart even for image messages
                this._renderCryptoChartFromHistory(message, messageId);
                // Reasoning: live stream attaches ReasoningDisplay to image
                // messages when the reasoning model emitted one (codeWindow.js
                // ~line 3210, not gated on hasAiImages). Refresh used to early-
                // return here and drop it on the floor — reasoning pill missing
                // after reload despite being visible during live.
                if (message.reasoning && window.ReasoningDisplay) {
                    const imgTargetEl = document.getElementById(`ai-${messageId}`);
                    if (imgTargetEl) {
                        window.ReasoningDisplay.create(message.reasoning, imgTargetEl);
                    }
                }
                return;
            }

            const normalizedUserText = this.normalizePromptText(userPromptSource);
            const normalizedAiText = this.normalizePromptText(aiContent);
            const isPromptEcho = normalizedAiText && normalizedAiText === normalizedUserText;
            const suppressAiText = !!message.suppress_text || isPromptEcho;

            this.renderMessage({
                id: `ai-${messageId}`,
                sender: 'ai',
                content: suppressAiText ? '' : aiContent,
                timestamp: message.timestamp || null,
                isCode: message.is_code || false,
                isHistory: true,
                timestamp: message.timestamp,
                attachments: aiAttachments,
                suppressText: suppressAiText,
                forceButtons: true,
                originalPrompt: userPromptSource,
                sources,
                messageKey,
                style: replayStyle,
                email: message.email || null,
                reminder: message.reminder || null,
                modelMeta: {
                    auto_selected: !!message.auto_selected,
                    model_id: message.model_id || null,
                    model_label: message.model_label || null,
                    model_provider: message.model_provider || null,
                    model_provider_label: message.model_provider_label || null,
                    task_type: message.task_type || null,
                    user_feedback: message.user_feedback || null,
                }
            });
            if (typeof window !== 'undefined' && typeof window.updateSourcesButton === 'function') {
                window.updateSourcesButton(sources);
            }
            const artifactReference = (() => {
                if (message.artifact_reference) {
                    return Object.assign({}, message.artifact_reference);
                }
                if (message.artifact_id) {
                    return {
                        artifactId: message.artifact_id,
                        title: message.artifact_title,
                        summary: message.artifact_summary,
                        type: message.artifact_type,
                        primaryLanguage: message.artifact_language,
                        isActive: message.artifact_is_active,
                        pending: message.artifact_pending,
                        versionId: message.artifact_version_id,
                        versionNumber: message.artifact_version_number,
                        entrypoint: message.artifact_entrypoint
                    };
                }
                return null;
            })();

            // Only show artifact pill for artifact modes, NOT for code_window or inline
            const renderMode = message.render_mode || 'inline';
            const shouldShowArtifactPill = renderMode !== 'inline' && renderMode !== 'code_window';

            if (typeof window !== 'undefined' && window.artifactPanel && artifactReference && shouldShowArtifactPill) {
                const targetEl = document.getElementById(`ai-${messageId}`);
                if (targetEl) {
                    const reference = Object.assign({}, artifactReference);
                    if (!reference.summary) {
                        reference.summary = aiContent;
                    }
                    if (message.artifact_draft) {
                        window.artifactPanel.registerDraft(reference, message.artifact_draft, this.activeSessionId);
                    }
                    window.artifactPanel.attachPreview(targetEl, reference, {
                        sessionId: this.activeSessionId,
                        payload: message.artifact_draft || null
                    });
                }
            }

            // Restore reasoning section if it was saved
            if (message.reasoning && window.ReasoningDisplay) {
                const targetEl = document.getElementById(`ai-${messageId}`);
                if (targetEl) {
                    window.ReasoningDisplay.create(message.reasoning, targetEl);
                }
            }

            // Restore crypto/stock chart if it was saved
            this._renderCryptoChartFromHistory(message, messageId);
        }
    }

    /**
     * Render crypto/stock chart from historical message data
     */
    _renderCryptoChartFromHistory(message, messageId) {
        if (!message.crypto_chart) {
            return;
        }

        console.log('[Chart History] Attempting to render chart:', message.crypto_chart);

        if (!window.CryptoChartModule) {
            console.warn('[Chart History] CryptoChartModule not available');
            return;
        }

        // Stagger history chart renders to avoid CoinGecko rate limits
        this._chartHistoryIndex = (this._chartHistoryIndex || 0) + 1;
        const staggerDelay = (this._chartHistoryIndex - 1) * 2000; // 2s between charts
        // Guard against switch races: capture the epoch now and bail if the
        // user has switched sessions by the time this fires (otherwise the
        // chart renders into whatever session is active later).
        const epoch = this.switchEpoch;
        const timerId = setTimeout(() => {
            if (this.switchEpoch !== epoch) return;
            const targetEl = document.getElementById(`ai-${messageId}`);
            if (!targetEl) {
                console.warn('[Chart History] Target element not found:', `ai-${messageId}`);
                return;
            }

            const messageContent = targetEl.querySelector('.message-content');
            if (!messageContent) {
                console.warn('[Chart History] Message content not found');
                return;
            }

            // Check if chart already exists
            if (messageContent.querySelector('.crypto-chart-wrapper')) {
                console.log('[Chart History] Chart already exists, skipping');
                return;
            }

            const chartData = message.crypto_chart;
            const assetType = chartData.asset_type || 'crypto';
            const asset = assetType === 'stock' ? chartData.symbol : chartData.coin;

            console.log('[Chart History] Rendering:', { asset, assetType, chartData });

            if (asset) {
                // Create chart wrapper
                const chartWrapper = document.createElement('div');
                chartWrapper.className = 'crypto-chart-wrapper';
                // Prepend chart to match live rendering order (chart appears before text due to async text rendering)
                messageContent.insertBefore(chartWrapper, messageContent.firstChild);

                // Add intro text above chart (insert before chart wrapper)
                if (typeof window.generateChartIntroText === 'function') {
                    const introText = document.createElement('p');
                    introText.className = 'chart-intro-text';
                    introText.textContent = window.generateChartIntroText(asset, assetType);
                    messageContent.insertBefore(introText, chartWrapper);
                }

                // Chat-history charts render as compact sparklines.
                // Default range = 'MAX' (matches the live-chat default
                // set in createCryptoChart). Falls back to 'MAX' instead
                // of '1D' when chartData has no range — the live chart
                // always opens with ALL/MAX so history should match.
                const renderFn = (window.CryptoChartModule.renderSparkline || window.CryptoChartModule.render);
                Promise.resolve(renderFn(chartWrapper, asset, {
                    currency: chartData.currency || 'usd',
                    range: chartData.range || 'MAX',
                    assetType: assetType
                })).catch(err => console.error('[Chart History] Render error:', err));

                console.log('[Chart History] Sparkline render initiated for:', asset);
            } else {
                console.warn('[Chart History] No asset found in chart data');
            }
        }, 100 + staggerDelay);
        // Track the timer so a re-render of this page can cancel pending
        // chart renders before resetting the stagger counter.
        if (!Array.isArray(this._chartHistoryTimers)) {
            this._chartHistoryTimers = [];
        }
        this._chartHistoryTimers.push(timerId);
    }

    /**
     * Render a message in the chat display.
     * @param {Object} options - Options for rendering the message.
     * @param {string} options.id - The unique ID of the message.
     * @param {string} options.sender - The sender of the message ('user' or 'ai').
     * @param {string} options.content - The content of the message.
     * @param {boolean} [options.isCode=false] - Whether the content contains code.
     * @param {boolean} [options.isHistory=false] - Whether the message is historical.
     */
    renderMessage({ id, sender, content, isCode = false, timestamp, isHistory = false, attachments = [], suppressText = false, forceButtons = false, originalPrompt = null, sources = null, messageKey = null, email = null, reminder = null, modelMeta = null, style = null }) {
        const chatDisplay = this.elements.chatDisplay;

        let messageDiv = document.getElementById(id);
        if (!messageDiv) {
            messageDiv = createMessageDiv(id, sender);
            chatDisplay.appendChild(messageDiv);
        } else {
            // If a live StreamRenderer is attached to this message's DOM, tear
            // it down before wiping innerHTML. Otherwise its .messageEl holds
            // a dangling reference and subsequent .append() calls mutate an
            // orphaned node tree — manifests as "edit history fucked up" after
            // fast regenerate/edit cycles.
            const live = window._activeStreamRenderer;
            if (live && !live.destroyed && messageDiv.contains(live.container)) {
                live.destroy();
                window._activeStreamRenderer = null;
            }
            messageDiv.innerHTML = '';
        }

        if (typeof originalPrompt === 'string' && originalPrompt.length) {
            messageDiv.dataset.originalPrompt = originalPrompt;
        }

        if (typeof sender === 'string') {
            messageDiv.dataset.sender = sender.toLowerCase();
        }
        // Set timestamp: use original message time for history, current time for live
        messageDiv.dataset.timestamp = timestamp || new Date().toISOString();
        if (messageKey) {
            messageDiv.dataset.messageKey = messageKey;
        } else if (!messageDiv.dataset.messageKey) {
            messageDiv.dataset.messageKey = id;
        }

        const hasSources = sources && typeof sources === 'object' && (
            Array.isArray(sources) ? sources.length > 0
            : !!(sources.web?.length || sources.images?.length || sources.videos?.length)
        );
        if (hasSources) {
            try {
                messageDiv.dataset.sourceResults = JSON.stringify(sources);
                messageDiv.dataset.hasSources = 'true';
            } catch (error) {
                console.warn('Failed to serialize sources for message', id, error);
                delete messageDiv.dataset.sourceResults;
                delete messageDiv.dataset.hasSources;
            }
        } else {
            delete messageDiv.dataset.sourceResults;
            delete messageDiv.dataset.hasSources;
        }
    
        createHeader(sender, messageDiv);
    
        const messageContent = document.createElement('div');
        messageContent.className = 'message-content';
        messageDiv.appendChild(messageContent);

        const messageType = isCode ? 'code' : 'text';
        // Sanitize content - replace base64 data URLs with placeholder
        let safeContent = content || '';
        if (typeof safeContent === 'string' && safeContent.startsWith('data:')) {
            // This is a base64 data URL that shouldn't be displayed as text
            safeContent = '';  // Don't show anything - attachments will handle the display
        }
        const showButtons = sender === 'ai' && (!isHistory || forceButtons);

        // Handle email composer for historical messages
        if (email && email.body && window.EmailComposer) {
            // Mark container to prevent async text rendering
            messageContent.dataset.emailComposer = 'true';
            // Render email composer instead of normal text
            window.EmailComposer.createCard(email, messageContent, false);
        } else if (!suppressText && safeContent) {
            // Long user messages → document card (Claude-style)
            if (sender === 'user' && typeof isLongMessage === 'function' && isLongMessage(safeContent)) {
                const card = createDocumentCard(safeContent);
                const wrapper = document.createElement('div');
                wrapper.classList.add('text-message-container');
                wrapper.appendChild(card);
                messageContent.appendChild(wrapper);
            } else if (sender === 'ai' && isHistory && typeof window.StreamRenderer === 'function') {
                // Replay convergence: route AI history through StreamRenderer so DOM
                // matches the live-stream render byte-for-byte.
                if (typeof safeContent === 'string' && safeContent.includes('<')) {
                    console.warn('[replay] AI content contains "<" — unexpected, expected raw markdown only');
                }
                const renderer = new window.StreamRenderer(messageContent, { replay: true, streamMeta: style ? { style } : {} });
                renderer.append(safeContent);
                renderer.finalize();
                // KaTeX inline math — live stream runs this post-finalize, replay must too
                if (typeof window.renderMathInMessage === 'function') {
                    window.renderMathInMessage(messageContent);
                }
                if (showButtons && typeof appendActionButtons === 'function') {
                    appendActionButtons(messageContent, id);
                }
                // Model attribution ("via CODE"/"via THINK"/etc.) — live stream
                // applies this inline at finalize; history replay used to skip
                // it entirely because StreamRenderer bypasses appendTextMessage
                // where applyAttribution lives.
                if (modelMeta && typeof window.addModelAttribution === 'function') {
                    window.addModelAttribution(messageDiv, modelMeta);
                }
            } else if (sender === 'ai') {
                // Non-history AI or AI path that missed the streaming branch
                renderAiTextImmediate(messageContent, safeContent, id, showButtons, style);
            } else {
                // User message (history or live re-render)
                renderUserMessage(messageContent, safeContent, id);
                if (showButtons && typeof appendActionButtons === 'function') {
                    appendActionButtons(messageContent, id);
                }
            }
        }
        // Reminder card (history replay): if the saved message carried a
        // reminder object, append the chip below the message body. Same hook
        // as live stream — RemindersModule.renderCardForMessage is idempotent.
        if (reminder && reminder.id && sender === 'ai' && window.RemindersModule) {
            window.RemindersModule.renderCardForMessage(messageContent, reminder);
        }
        // Render attachments - CSS order property ensures they appear after text
        // AI-generated images render full-size (matching initial display), not as file cards
        const aiGeneratedImages = attachments.filter(att =>
            att && att.isImage && (att.provider || att.name === 'AI generated image')
        );
        const otherAttachments = attachments.filter(att =>
            !att || !att.isImage || (!att.provider && att.name !== 'AI generated image')
        );
        if (aiGeneratedImages.length > 0) {
            this._renderFullSizeImages(messageContent, aiGeneratedImages, originalPrompt);
        }
        this.renderAttachments(messageContent, otherAttachments);
        if (typeof window !== 'undefined' && typeof window.updateEphemeralOptionsVisibility === 'function') {
            window.updateEphemeralOptionsVisibility();
            if (!isHistory && typeof window.dismissEphemeralOptions === 'function') {
                window.dismissEphemeralOptions();
            }
        }

        // Render search results (news cards, citations, sources button) for history
        const _hasSearchData = sources && typeof sources === 'object' && (
            (Array.isArray(sources.web) && sources.web.length > 0) ||
            (Array.isArray(sources.images) && sources.images.length > 0) ||
            (Array.isArray(sources.videos) && sources.videos.length > 0) ||
            (Array.isArray(sources) && sources.length > 0)
        );
        if (isHistory && sender === 'ai' && _hasSearchData && window.SearchResults) {
            // sources is the full _search_done_data dict: {web, images, videos, ...}
            const searchData = (sources.web || sources.images || sources.videos) ? sources : { web: sources };
            window.SearchResults.renderFinalResults(
                messageDiv,
                searchData,
                sources.embed_video || null,
                sources.fetched_urls || [],
                sources.render_hint || 'text_only'
            );
        }

        if (sender === 'ai' && modelMeta) {
            // Store for inline attribution after text renders
            messageDiv._modelMeta = modelMeta;
            // Always store model data on DOM for quality signal collection
            if (modelMeta.model_id) messageDiv.dataset.modelId = modelMeta.model_id;
            if (modelMeta.model_label) messageDiv.dataset.modelLabel = modelMeta.model_label;
            if (modelMeta.model_provider) messageDiv.dataset.modelProvider = modelMeta.model_provider;
            if (modelMeta.model_provider_label) messageDiv.dataset.modelProviderLabel = modelMeta.model_provider_label;
            if (modelMeta.task_type) messageDiv.dataset.taskType = modelMeta.task_type;
            if (modelMeta.auto_selected) messageDiv.dataset.autoSelected = 'true';
            if (modelMeta.user_feedback) messageDiv.dataset.feedback = modelMeta.user_feedback;
        }

        if (sender === 'user') {
            const footer = this.ensureUserFooterElement(messageDiv, messageKey || id);
            if (footer) {
                let copyBtn = footer.querySelector('.copy-message-button');
                if (!copyBtn && typeof appendCopyMessageButton === 'function') {
                    appendCopyMessageButton(footer, id);
                } else if (copyBtn) {
                    footer.appendChild(copyBtn);
                }
                let editBtn = footer.querySelector('.edit-button');
                if (!editBtn && typeof appendEditButton === 'function') {
                    appendEditButton(footer, id);
                } else if (editBtn) {
                    footer.appendChild(editBtn);
                }
            }
        }
    }

    ensureAiFooterElement(messageDiv, anchorId = null) {
        if (!messageDiv) return null;
        let footer = messageDiv.querySelector('.ai-message-footer');
        if (!footer) {
            footer = document.createElement('div');
            footer.className = 'ai-message-footer';
            if (anchorId) {
                footer.dataset.anchorId = anchorId;
            }
            const content = messageDiv.querySelector('.message-content');
            if (content) {
                content.insertAdjacentElement('afterend', footer);
            } else {
                messageDiv.appendChild(footer);
            }
        }
        return footer;
    }

    ensureUserFooterElement(messageDiv, anchorId = null) {
        if (!messageDiv) return null;
        let footer = messageDiv.querySelector('.message-bubble-footer');
        if (!footer) {
            footer = document.createElement('div');
            footer.className = 'message-bubble-footer';
            if (anchorId) {
                footer.dataset.anchorId = anchorId;
            }
            const content = messageDiv.querySelector('.message-content');
            if (content) {
                content.insertAdjacentElement('afterend', footer);
            } else {
                messageDiv.appendChild(footer);
            }
        }
        return footer;
    }

    /**
     * Render AI-generated images full-size on history replay, producing the
     * same DOM shape as the live stream path (`appendImageMessage`). All
     * figures share one `.image-message` container so the grid CSS applies
     * when there are 2+ images, matching live behavior.
     */
    _renderFullSizeImages(container, images, promptText) {
        if (!images || !images.length) return;

        // Prefer the locally-hosted copy over any remote URL the generator
        // handed us — provider CDN URLs (Pollinations, OpenRouter) have TTLs
        // and stale ones 404 on old sessions. The backend already rewrites
        // `url` to the /uploads/ path at save time, but belt-and-braces:
        // derive it again from `local_path` if we see one.
        const resolveUrl = (att) => {
            if (!att) return '';
            if (att.local_path && typeof att.local_path === 'string' && att.local_path.length) {
                return '/uploads/' + att.local_path.replace(/^\/+/, '');
            }
            if (typeof FilePreview !== 'undefined' && FilePreview.resolveUrl) {
                return FilePreview.resolveUrl(att);
            }
            return att.url || att.dataUrl || '';
        };

        const rendered = [];
        const imageContainer = document.createElement('div');
        imageContainer.className = 'image-message';

        // script.js loads before this module (see index.html script order),
        // so buildImageLayer is always defined by the time any replay hits
        // this path. If it ever isn't, skip loudly rather than silently
        // render a shape-divergent fallback — we just spent a patch killing
        // that exact divergence and don't want it sneaking back.
        if (typeof buildImageLayer !== 'function') {
            console.error(
                '[history replay] buildImageLayer missing — script.js load order broke. ' +
                'Skipping image render to avoid DOM drift.'
            );
            return;
        }

        images.forEach((att) => {
            const url = resolveUrl(att);
            if (!url) return;
            const altSource = promptText || att.original_filename || att.name || '';
            const alt = typeof formatImageAlt === 'function'
                ? formatImageAlt(altSource, { generated: true })
                : (altSource || 'AI-generated image');
            imageContainer.appendChild(buildImageLayer(url, alt));
            rendered.push(url);
        });

        if (!rendered.length) return;

        // If multiple images landed in this container, apply the same grid
        // treatment the user-attachment path uses so the bubble doesn't
        // stack figures full-width vertically.
        if (rendered.length >= 2) {
            imageContainer.classList.add('image-message--grid');
            imageContainer.dataset.imageCount = String(Math.min(rendered.length, 4));
        }

        container.appendChild(imageContainer);
    }

    renderAttachments(container, attachments = []) {
        if (!attachments || attachments.length === 0) return;

        const attachmentsWrapper = document.createElement('div');
        attachmentsWrapper.className = 'message-attachments';
        // Mirror the live-render path: apply grid for 2+ all-image sets.
        // Shared helper so stream and replay can't drift.
        if (typeof applyAttachmentLayout === 'function') {
            applyAttachmentLayout(attachmentsWrapper, attachments);
        }
        let hasRenderableAttachment = false;

        attachments.forEach(attachment => {
            if (!attachment) return;

            // Use the new FilePreview system for modern thumbnails
            if (typeof FilePreview !== 'undefined') {
                const card = FilePreview.createFileCard(attachment, {
                    showMeta: true,
                    onClick: (att) => {
                        // User-uploaded images should open in the same
                        // fullscreen viewer used elsewhere in chat.
                        const isImage = FilePreview.isImage(
                            att.original_filename || att.name || att.filename,
                            att.mime_type
                        );
                        const url = FilePreview.resolveUrl(att);

                        if (isImage && url && typeof window.__xelasphereOpenLightbox === 'function') {
                            const name = att.original_filename || att.name || att.filename || 'Image';
                            window.__xelasphereOpenLightbox({
                                src: url,
                                name,
                                type: att.mime_type || 'image',
                                isImage: true
                            });
                        } else if (isImage && typeof openAttachmentLightbox === 'function') {
                            const name = att.original_filename || att.name || att.filename || 'Image';
                            openAttachmentLightbox({
                                src: url,
                                title: name,
                                downloadName: name,
                                openUrl: url,
                                isImage: true
                            });
                        } else {
                            FilePreview.openPreviewModal(att);
                        }
                    }
                });
                attachmentsWrapper.appendChild(card);
                hasRenderableAttachment = true;
            } else {
                // Fallback to old behavior if FilePreview not loaded
                const name = this.getAttachmentDisplayName(attachment, 'File');
                const isImage = this.isAttachmentImage(attachment);

                if (isImage) {
                    const src = this.resolveAttachmentUrl(attachment, true);
                    if (!src) return;

                    const card = document.createElement('div');
                    card.className = 'attachment-card image-attachment';

                    const img = document.createElement('img');
                    img.src = src;
                    img.alt = name || 'Generated image';
                    img.className = 'attachment-thumb';

                    // Blur-up loading effect
                    img.onload = () => {
                        img.classList.add('loaded');
                        card.classList.add('loaded');
                    };
                    // If already cached
                    if (img.complete && img.naturalHeight !== 0) {
                        img.classList.add('loaded');
                        card.classList.add('loaded');
                    }

                    img.addEventListener('click', () => {
                        if (typeof window.__xelasphereOpenLightbox === 'function') {
                            window.__xelasphereOpenLightbox({
                                src,
                                name,
                                isImage: true
                            });
                            return;
                        }
                        openAttachmentLightbox({
                            src,
                            title: name,
                            downloadName: name,
                            openUrl: src,
                            isImage: true
                        });
                    });

                    card.appendChild(img);
                    attachmentsWrapper.appendChild(card);
                    hasRenderableAttachment = true;
                } else {
                    const chip = document.createElement('span');
                    chip.className = 'attachment-chip';
                    chip.textContent = name;
                    attachmentsWrapper.appendChild(chip);
                    hasRenderableAttachment = true;
                }
            }
        });

        if (hasRenderableAttachment) {
            container.appendChild(attachmentsWrapper);
        }
    }
    
    /**
     * Display a message in the chat interface.
     * @param {string} content - The message content.
     * @param {string} sender - 'user', 'ai', or 'system'.
     * @param {string} type - 'text' or 'code'.
     * @param {string|null} originalPrompt - The original prompt (optional).
     * @param {boolean} scroll - Whether to scroll to the bottom.
     * @param {string|null} messageId - Unique identifier for the message.
     */
    displayMessage(content, sender, type = 'text', originalPrompt = null, scroll = true, messageId = null) {
        const messageElement = document.createElement('div');
        messageElement.classList.add('chat-message', sender.toLowerCase());
        messageElement.id = messageId || this.generateMessageId();

        const messageContent = document.createElement('div');
        messageContent.className = 'message-content';

        const safeContent = typeof content === 'string' ? content : '';
        if (safeContent.trim().length > 0) {
            const textParagraph = document.createElement('p');
            textParagraph.className = 'text-message';
            textParagraph.innerHTML = this.escapeHtml(safeContent);
            messageContent.appendChild(textParagraph);
        }

        messageElement.appendChild(messageContent);

        this.elements.chatDisplay.appendChild(messageElement);
        if (scroll) {
            // Scroll the actual scrollable container after layout settles
            const scrollContainer = document.getElementById('chat-scroll-region');
            if (scrollContainer) {
                requestAnimationFrame(() => {
                    scrollContainer.scrollTop = scrollContainer.scrollHeight;
                });
            }
        }

        // Update chat history
        const activeSession = this.activeSessionId
            ? this.chatHistory[this.activeSessionId]
            : null;
        if (activeSession && Array.isArray(activeSession.messages)) {
            const now = new Date().toISOString();
            activeSession.messages.push({
                sender: sender === 'user' ? 'User' : 'AI',
                content: content,
                timestamp: now
            });
            // Update last_updated so session moves to top of list
            activeSession.last_updated = now;
            this.markSessionDirty(this.activeSessionId);
            // Re-render tabs immediately so session moves to top
            this.renderChatTabs();
        }
    }

    /**
     * Generate a unique message ID.
     * @returns {string} - A unique message ID.
     */
    generateMessageId() {
        if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
            return 'msg-' + crypto.randomUUID();
        }
        // Fallback for non-secure contexts where crypto.randomUUID is unavailable.
        return 'msg-' + Math.random().toString(36).slice(2, 11);
    }

    /**
     * Generate a session name based on the initial prompt.
     * @param {string} prompt - The initial prompt text.
     * @returns {string} - A formatted session name.
     */
    generateSessionName(prompt) {
        if (!prompt) return 'New Chat';
        const formatted = this.formatPromptTitle(prompt);
        if (formatted && formatted.length >= 4) {
            return formatted.length > 60 ? `${formatted.slice(0, 57)}…` : formatted;
        }
        const fallback = this.truncatePreview(prompt, 36);
        return fallback || 'New Chat';
    }

    isImagePrompt(promptText) {
        if (!promptText) return false;
        const normalized = promptText.trim().toLowerCase();
        return normalized.startsWith('/image') || normalized.startsWith('/img') || normalized.startsWith('img:');
    }

    showImageGenerationPlaceholder(promptText) {
        const chatDisplay = this.elements.chatDisplay;
        if (!chatDisplay || typeof createMessageDiv !== 'function') return null;

        const placeholderId = `image-loading-${this.generateMessageId()}`;
        const messageDiv = createMessageDiv(placeholderId, 'ai');
        messageDiv.classList.add('image-generating');

        const header = document.createElement('div');
        header.className = 'message-header';
        messageDiv.appendChild(header);

        const content = document.createElement('div');
        content.className = 'message-content';

        const spinner = document.createElement('span');
        spinner.className = 'image-loading-spinner';
        spinner.setAttribute('aria-hidden', 'true');

        const copy = document.createElement('div');
        copy.className = 'image-loading-copy';

        const title = document.createElement('p');
        title.className = 'image-loading-title';
        title.textContent = 'Generating image';

        const caption = document.createElement('p');
        caption.className = 'image-loading-prompt';
        caption.textContent = this.truncatePreview(promptText, 80);

        copy.appendChild(title);
        if (caption.textContent) {
            copy.appendChild(caption);
        }

        content.appendChild(spinner);
        content.appendChild(copy);
        messageDiv.appendChild(content);

        chatDisplay.appendChild(messageDiv);
        // Scroll the actual scrollable container after layout settles
        const scrollContainer = document.getElementById('chat-scroll-region');
        if (scrollContainer) {
            requestAnimationFrame(() => {
                scrollContainer.scrollTop = scrollContainer.scrollHeight;
            });
        }
        return placeholderId;
    }

    removeMessageById(messageId) {
        if (!messageId) return;
        const el = document.getElementById(messageId);
        if (el && el.parentNode) {
            el.parentNode.removeChild(el);
        }
    }

    // =======================
    // Utility Functions
    // =======================

    /**
     * Escape HTML to prevent XSS attacks.
     * @param {string} text - The text to escape.
     * @returns {string} - Escaped HTML string.
     */
    escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }
    
    /**
     * Show a loading overlay.
     * @returns {HTMLElement} - The overlay element.
     */
    showLoadingOverlay() {
        const overlay = document.createElement('div');
        overlay.className = 'loading-overlay';
        overlay.innerHTML = '<div class="spinner"></div>';
        document.body.appendChild(overlay);
        return overlay;
    }

    /**
     * Show a notification to the user.
     * @param {string} message - The notification message.
     * @param {string} type - 'success' or 'error'.
     */
    showNotification(message, type = 'success') {
        if (window.Toast) {
            if (type === 'error') window.Toast.error(message);
            else if (type === 'warning') window.Toast.warning(message);
            else if (type === 'info') window.Toast.info(message);
            else window.Toast.success(message);
        } else {
            console.warn('[showNotification] Toast not loaded:', message);
        }
    }

    /**
     * Enable editing of a user message.
     * @param {string} messageId - The ID of the message to edit.
     */
    enableEditing(messageId) {
        const messageDiv = document.getElementById(messageId);
        if (!messageDiv) return;

        const contentDiv = messageDiv.querySelector('.message-content');
        if (!contentDiv) return;

        const originalText = contentDiv.textContent;
        const input = document.createElement('textarea');
        input.value = originalText;
        input.className = 'edit-textarea';

        contentDiv.replaceWith(input);
        input.focus();

        const saveEdit = () => {
            const newText = input.value.trim();
            if (newText && newText !== originalText) {
                contentDiv.textContent = newText;
                input.replaceWith(contentDiv);
                // Update chat history
                const session = this.chatHistory[this.activeSessionId];
                if (session) {
                    const message = session.messages.find(msg => msg.content === originalText);
                    if (message) {
                        message.content = newText;
                    }
                }
                if (this.autoSaveEnabled) {
                    this.markSessionDirty(this.activeSessionId);
                    this.saveSession(this.activeSessionId, { silent: true });
                }
                this.showNotification('Message edited successfully', 'success');
            } else {
                input.replaceWith(contentDiv);
            }
        };

        const cancelEdit = () => {
            input.replaceWith(contentDiv);
        };

        input.addEventListener('blur', saveEdit);
        input.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                saveEdit();
            } else if (e.key === 'Escape') {
                cancelEdit();
            }
        });
    }

    /**
     * Format code content for display.
     * @param {string} content - The raw content containing code blocks.
     * @returns {string} - Formatted HTML with code blocks.
     */
    formatCodeContent(content) {
        const parts = content.split('```');
        return parts.map((part, index) => {
            if (index % 2 === 1) {
                return `<pre><code>${this.escapeHtml(part)}</code></pre>`;
            }
            return `<p class="text-message">${this.escapeHtml(part)}</p>`;
        }).join('');
    }

}

// Initialize ChatManager on page load
document.addEventListener('DOMContentLoaded', () => {
    window.chatManager = new ChatManager();
});

// Attachment lightbox for chat images
let attachmentLightbox = null;

function ensureAttachmentLightbox() {
    if (attachmentLightbox) return attachmentLightbox;

    const overlay = document.createElement('div');
    overlay.className = 'attachment-lightbox';

    const content = document.createElement('div');
    content.className = 'attachment-lightbox__content';

    const img = document.createElement('img');
    img.className = 'attachment-lightbox__img';
    img.alt = '';

    const fallback = document.createElement('div');
    fallback.className = 'attachment-lightbox__fallback';
    const fallbackMessage = document.createElement('p');
    fallbackMessage.textContent = 'Preview unavailable for this attachment.';
    const fallbackLink = document.createElement('a');
    fallbackLink.className = 'attachment-lightbox__fallback-link';
    fallbackLink.target = '_blank';
    fallbackLink.rel = 'noopener';
    fallbackLink.textContent = 'Open file in new tab';
    fallback.appendChild(fallbackMessage);
    fallback.appendChild(fallbackLink);
    fallback.style.display = 'none';

    const body = document.createElement('div');
    body.className = 'attachment-lightbox__body';

    const media = document.createElement('div');
    media.className = 'attachment-lightbox__media';

    const frame = document.createElement('div');
    frame.className = 'attachment-lightbox__frame';

    const title = document.createElement('div');
    title.className = 'attachment-lightbox__title';

    const eyebrow = document.createElement('p');
    eyebrow.className = 'attachment-lightbox__eyebrow';

    const info = document.createElement('div');
    info.className = 'attachment-lightbox__info';

    const details = document.createElement('div');
    details.className = 'attachment-lightbox__details';

    const panel = document.createElement('div');
    panel.className = 'attachment-lightbox__panel';

    const actions = document.createElement('div');
    actions.className = 'attachment-lightbox__actions';

    const downloadBtn = document.createElement('a');
    downloadBtn.className = 'attachment-lightbox__btn';
    downloadBtn.textContent = 'Download';
    downloadBtn.target = '_blank';
    downloadBtn.rel = 'noopener';

    const openBtn = document.createElement('a');
    openBtn.className = 'attachment-lightbox__btn secondary';
    openBtn.textContent = 'Open';
    openBtn.target = '_blank';
    openBtn.rel = 'noopener';

    const closeBtn = document.createElement('button');
    closeBtn.type = 'button';
    closeBtn.className = 'attachment-lightbox__close';
    closeBtn.innerHTML = '&times;';

    actions.appendChild(downloadBtn);
    actions.appendChild(openBtn);

    frame.appendChild(img);
    frame.appendChild(fallback);
    media.appendChild(frame);

    info.appendChild(eyebrow);
    info.appendChild(title);
    info.appendChild(details);
    panel.appendChild(info);
    panel.appendChild(actions);

    body.appendChild(media);
    body.appendChild(panel);

    content.appendChild(body);
    content.appendChild(closeBtn);
    overlay.appendChild(content);
    document.body.appendChild(overlay);

    const closeLightbox = () => {
        overlay.classList.remove('open');
        img.src = '';
        fallback.style.display = 'none';
        img.style.display = 'block';
    };

    overlay.addEventListener('click', (e) => {
        if (e.target === overlay || e.target === closeBtn) {
            closeLightbox();
        }
    });

    document.addEventListener('keydown', (e) => {
        if (overlay.classList.contains('open') && e.key === 'Escape') {
            closeLightbox();
        }
    });

    attachmentLightbox = {
        overlay,
        body,
        media,
        panel,
        img,
        title,
        eyebrow,
        details,
        downloadBtn,
        openBtn,
        fallback,
        fallbackLink,
        close: closeLightbox
    };

    return attachmentLightbox;
}

function openAttachmentLightbox(arg1, arg2) {
    const options = (typeof arg1 === 'object' && arg1 !== null && !Array.isArray(arg1))
        ? arg1
        : { src: arg1, title: arg2 };

    const {
        src,
        title,
        meta = [],
        eyebrow,
        downloadName,
        downloadUrl,
        openUrl,
        isImage = true,
        name
    } = options || {};

    if (!src) return;

    const lb = ensureAttachmentLightbox();
    const resolvedTitle = title || name || 'Attachment';
    const resolvedEyebrow = eyebrow || '';
    const resolvedMeta = Array.isArray(meta) ? meta : [];
    const downloadTarget = downloadUrl || src;
    const openTarget = openUrl || src;
    const resolvedDownloadName = downloadName || deriveDownloadName(resolvedTitle);

    if (resolvedEyebrow) {
        lb.eyebrow.textContent = resolvedEyebrow;
        lb.eyebrow.style.display = '';
    } else {
        lb.eyebrow.style.display = 'none';
        lb.eyebrow.textContent = '';
    }

    lb.title.textContent = resolvedTitle;

    lb.details.innerHTML = '';
    if (resolvedMeta.length) {
        lb.details.style.display = 'flex';
        resolvedMeta.forEach(entry => {
            const text = formatLightboxMetaText(entry);
            if (!text) return;
            const chip = document.createElement('span');
            chip.className = 'attachment-lightbox__detail';
            chip.textContent = text;
            lb.details.appendChild(chip);
        });
    } else {
        lb.details.style.display = 'none';
    }

    if (isImage === false) {
        lb.img.style.display = 'none';
        lb.img.src = '';
        lb.fallback.style.display = 'flex';
        lb.fallbackLink.href = openTarget || '#';
    } else {
        lb.fallback.style.display = 'none';
        lb.img.style.display = 'block';
        lb.img.src = src;
        lb.img.alt = resolvedTitle || 'Image';
    }

    lb.downloadBtn.href = downloadTarget || src;
    lb.downloadBtn.download = resolvedDownloadName;

    if (openTarget) {
        lb.openBtn.href = openTarget;
        lb.openBtn.style.display = '';
    } else {
        lb.openBtn.style.display = 'none';
    }

    lb.overlay.classList.add('open');
}

function formatLightboxMetaText(entry) {
    if (!entry) return '';
    if (typeof entry === 'string') {
        return entry.trim();
    }
    const label = entry.label ? String(entry.label).trim() : '';
    const value = entry.value !== undefined && entry.value !== null
        ? String(entry.value).trim()
        : '';
    if (label && value) {
        return `${label}: ${value}`;
    }
    return label || value;
}

function deriveDownloadName(title) {
    const base = typeof title === 'string' && title.trim() ? title.trim() : 'attachment';
    return sanitizeFilename(base);
}

function sanitizeFilename(name) {
    const fallback = 'attachment';
    if (!name) return fallback;
    const normalized = name
        .toString()
        .replace(/[\\/:*?"<>|]+/g, '-')
        .replace(/\s+/g, '_')
        .replace(/_+/g, '_')
        .replace(/^-+|-+$/g, '')
        .trim();
    return normalized ? normalized.slice(0, 64) : fallback;
}

document.addEventListener('DOMContentLoaded', () => {
    // Setup eye tracking for both mobile and desktop ghost buttons
    const setupGhostEyes = (buttonId) => {
        const tempButton = document.getElementById(buttonId);
        if (!tempButton) return;
        const eyes = tempButton.querySelectorAll('.ghost-eye');
        if (!eyes.length) return;

        const maxShiftX = 1.8;
        const maxShiftY = 0.8;
        let currentX = 0;
        let currentY = 0;
        let targetX = 0;
        let targetY = 0;

        // Add smooth transition to eyes
        eyes.forEach(eye => {
            eye.style.transition = 'transform 0.15s ease-out';
        });

        const moveEyes = (event) => {
            const rect = tempButton.getBoundingClientRect();
            const centerX = rect.left + rect.width / 2;
            const centerY = rect.top + rect.height / 2;

            // Calculate angle to cursor
            const dx = event.clientX - centerX;
            const dy = event.clientY - centerY;
            const distance = Math.sqrt(dx * dx + dy * dy);

            // Normalize and apply max shift (eyes look toward cursor)
            const maxDist = 300;
            const factor = Math.min(distance / maxDist, 1);

            targetX = (dx / (distance || 1)) * maxShiftX * factor;
            targetY = (dy / (distance || 1)) * maxShiftY * factor;

            // Clamp vertical movement (don't look too far down)
            targetY = Math.max(-maxShiftY, Math.min(maxShiftY * 0.5, targetY));

            eyes.forEach(eye => {
                eye.style.transform = `translate(${targetX}px, ${targetY}px)`;
            });
        };

        // Track mouse movement on the whole document
        document.addEventListener('mousemove', moveEyes);

        // Also update on window resize (recalculate position)
        let lastMouseEvent = null;
        document.addEventListener('mousemove', (e) => { lastMouseEvent = e; });

        window.addEventListener('resize', () => {
            if (lastMouseEvent) {
                moveEyes(lastMouseEvent);
            }
        });
    };

    setupGhostEyes('temp-chat-button');
    setupGhostEyes('temp-chat-button-desktop');

    // Re-fetch sidebar history when a chat is moved into/out of a project
    window.addEventListener('projects:session-moved', () => {
        if (window.chatManager?.loadChatHistory) {
            window.chatManager.loadChatHistory();
        } else {
            window.location.reload();
        }
    });

    // Update the in-header project context tag when the active session changes.
    window.updateChatProjectTag = function updateChatProjectTag(session) {
        const tag = document.getElementById('chat-project-tag');
        if (!tag) return;
        const projectId = session?.project_id;
        if (!projectId) {
            tag.hidden = true;
            return;
        }
        const project = window.projects?.getList?.().find((p) => p.id === projectId);
        if (!project) {
            // Projects list not yet loaded — stay hidden (graceful degradation).
            tag.hidden = true;
            return;
        }
        tag.querySelector('.name').textContent = project.name;
        tag.style.setProperty('--project-accent', project.color);
        tag.href = `/p/${encodeURIComponent(project.id)}`;
        tag.hidden = false;
    };

    // Re-run tag update after projects list loads (handles race where chat session
    // was already active before projectsModule finished its fetch).
    window.addEventListener('projects:loaded', () => {
        const manager = window.chatManager;
        if (!manager) return;
        const activeId = manager.activeSessionId;
        if (!activeId) return;
        const sessionData = manager.chatHistory?.[activeId];
        if (sessionData) {
            window.updateChatProjectTag(sessionData);
        }
    });
});

// Honor /?new=1&project_id=X — create a fresh chat scoped to this project.
(function handleNewInProjectFromURL() {
    const url = new URL(window.location.href);
    if (url.searchParams.get('new') !== '1') return;
    const projectId = url.searchParams.get('project_id');
    if (!projectId) return;
    // Strip the params so reloads don't re-trigger.
    url.searchParams.delete('new');
    url.searchParams.delete('project_id');
    window.history.replaceState({}, '', url.pathname + (url.search ? `?${url.searchParams}` : ''));

    // Tag a session-attribute so the next-created chat sets project_id.
    window.__pendingProjectId = projectId;

    // createNewChat now accepts project_id in its options and forwards it to
    // POST /chat/new, which persists it on the Firestore session document.
    if (window.chatManager?.createNewChat) {
        window.chatManager.createNewChat(null, { project_id: projectId, notifySuccess: false });
    }
})();
