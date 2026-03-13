/**
 * DeviceDetector v2 - Best practices for 2024+
 *
 * Detection hierarchy (most to least reliable):
 * 1. User-Agent Client Hints API (newest, privacy-friendly)
 * 2. CSS Media Queries Level 4 (pointer, hover)
 * 3. Touch/Input APIs
 * 4. Screen dimensions (least reliable alone)
 *
 * Reactive behavior:
 * - Updates automatically on: resize, orientationchange, prefers-color-scheme, prefers-reduced-motion
 * - Dispatches 'devicedetector:updated' event on body class changes
 * - Subscribe with: window.addEventListener('devicedetector:updated', (e) => console.log(e.detail))
 *
 * SSR-safe:
 * - Check DeviceDetector.isClient before calling detect() in SSR contexts
 * - Returns safe defaults if window is undefined
 */
const DeviceDetector = {
    // Cache
    _cache: {},
    _cacheTime: 0,
    _cacheDuration: 500,
    _clientHints: null,
    _listeners: [],

    /**
     * Async: Get high-entropy client hints (most accurate)
     * Call once on init, results are cached
     */
    async fetchClientHints() {
        if (this._clientHints) return this._clientHints;

        try {
            if (navigator.userAgentData && navigator.userAgentData.getHighEntropyValues) {
                this._clientHints = await navigator.userAgentData.getHighEntropyValues([
                    'platform',
                    'platformVersion',
                    'model',
                    'mobile',
                    'architecture'
                ]);
                return this._clientHints;
            }
        } catch (e) {
            console.log('Client Hints not available, using fallbacks');
        }
        return null;
    },

    /**
     * Get comprehensive device info (sync version)
     * SSR-safe: returns defaults if window undefined
     */
    detect() {
        // SSR safety
        if (typeof window === 'undefined') {
            return this._getSSRDefaults();
        }

        const now = Date.now();
        if (this._cache.detected && (now - this._cacheTime) < this._cacheDuration) {
            return this._cache;
        }

        const width = window.innerWidth;
        const height = window.innerHeight;

        // === 1. User-Agent Client Hints (if available) ===
        let uaHintsMobile = null;
        let uaHintsPlatform = null;
        if (navigator.userAgentData) {
            uaHintsMobile = navigator.userAgentData.mobile; // Boolean, very reliable
            uaHintsPlatform = navigator.userAgentData.platform; // 'Android', 'iOS', 'Windows', etc.
        }

        // === 2. CSS Media Queries Level 4 (most reliable for input type) ===
        const hasCoarsePointer = window.matchMedia('(pointer: coarse)').matches;
        const hasFinePointer = window.matchMedia('(pointer: fine)').matches;
        const hasAnyCoarsePointer = window.matchMedia('(any-pointer: coarse)').matches;
        const hasAnyFinePointer = window.matchMedia('(any-pointer: fine)').matches;
        const hasHover = window.matchMedia('(hover: hover)').matches;
        const hasNoHover = window.matchMedia('(hover: none)').matches;
        const hasAnyHover = window.matchMedia('(any-hover: hover)').matches;

        // Reduced motion preference
        const prefersReducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

        // Color scheme preference
        const prefersDarkMode = window.matchMedia('(prefers-color-scheme: dark)').matches;

        // === 3. Touch/Input APIs ===
        const hasTouchScreen = (
            'ontouchstart' in window ||
            navigator.maxTouchPoints > 0
        );
        const maxTouchPoints = navigator.maxTouchPoints || 0;

        // === 4. Screen characteristics ===
        const pixelRatio = window.devicePixelRatio || 1;
        const isPortrait = height > width;
        const isLandscape = width > height;
        const aspectRatio = width / height;
        const screenWidth = window.screen?.width || width;
        const screenHeight = window.screen?.height || height;

        // Standalone/PWA mode
        const isStandalone = window.matchMedia('(display-mode: standalone)').matches ||
                           window.navigator.standalone === true;

        // === Device Type Classification (smart logic) ===
        let deviceType = 'desktop';

        // Trust Client Hints first if available
        if (uaHintsMobile === true) {
            deviceType = width < 768 ? 'phone' : 'tablet';
        } else if (uaHintsMobile === false) {
            deviceType = width >= 1200 ? 'desktop' : 'laptop';
        } else {
            // Fallback to media queries + dimensions
            const isTouchPrimary = hasCoarsePointer && hasNoHover;
            const isMousePrimary = hasFinePointer && hasHover;
            const isHybrid = hasAnyCoarsePointer && hasAnyFinePointer; // e.g., laptop with touchscreen

            if (isTouchPrimary && width < 768) {
                deviceType = 'phone';
            } else if (isTouchPrimary && width >= 768 && width < 1200) {
                deviceType = 'tablet';
            } else if (isHybrid) {
                // Hybrid device (touchscreen laptop, Surface, etc.)
                deviceType = width >= 1200 ? 'desktop' : 'laptop';
            } else if (isMousePrimary) {
                deviceType = width >= 1200 ? 'desktop' : 'laptop';
            } else if (width >= 1920 && aspectRatio > 1.6 && !hasTouchScreen) {
                deviceType = 'tv';
            } else if (hasCoarsePointer) {
                deviceType = width < 768 ? 'phone' : 'tablet';
            }
        }

        // Boolean flags
        const isPhone = deviceType === 'phone';
        const isTablet = deviceType === 'tablet';
        const isLaptop = deviceType === 'laptop';
        const isDesktop = deviceType === 'desktop';
        const isTV = deviceType === 'tv';
        const isMobile = isPhone || isTablet;
        const isTouchDevice = hasCoarsePointer || hasTouchScreen;
        const isHybridDevice = hasAnyCoarsePointer && hasAnyFinePointer;

        // Viewport-mobile: small viewport on non-mobile device (resized desktop browser)
        const isViewportMobile = width <= 768 && !isMobile;

        // Platform detection (prefer Client Hints, fallback to UA)
        let platform = uaHintsPlatform?.toLowerCase() || '';
        if (!platform) {
            const ua = navigator.userAgent;
            if (/iPad|iPhone|iPod/.test(ua) || (navigator.platform === 'MacIntel' && maxTouchPoints > 1)) {
                platform = 'ios';
            } else if (/Android/i.test(ua)) {
                platform = 'android';
            } else if (/Windows/i.test(ua)) {
                platform = 'windows';
            } else if (/Mac/i.test(ua)) {
                platform = 'macos';
            } else if (/Linux/i.test(ua)) {
                platform = 'linux';
            }
        }

        const isIOS = platform === 'ios';
        const isAndroid = platform === 'android';
        const isWindows = platform === 'windows';
        const isMacOS = platform === 'macos';
        const isLinux = platform === 'linux';

        this._cache = {
            detected: true,
            deviceType,
            isPhone,
            isTablet,
            isLaptop,
            isDesktop,
            isTV,
            isMobile,
            isViewportMobile,
            isTouchDevice,
            isHybridDevice,
            hasHover,
            hasAnyHover,
            hasFinePointer,
            hasCoarsePointer,
            hasAnyFinePointer,
            hasAnyCoarsePointer,
            hasTouchScreen,
            maxTouchPoints,
            isPortrait,
            isLandscape,
            isStandalone,
            platform,
            isIOS,
            isAndroid,
            isWindows,
            isMacOS,
            isLinux,
            width,
            height,
            screenWidth,
            screenHeight,
            pixelRatio,
            aspectRatio,
            prefersReducedMotion,
            prefersDarkMode,
            // Raw Client Hints data if available
            clientHints: this._clientHints
        };
        this._cacheTime = now;

        return this._cache;
    },

    /**
     * SSR defaults - safe values when window is undefined
     */
    _getSSRDefaults() {
        return {
            detected: false,
            deviceType: 'desktop',
            isPhone: false,
            isTablet: false,
            isLaptop: false,
            isDesktop: true,
            isTV: false,
            isMobile: false,
            isViewportMobile: false,
            isTouchDevice: false,
            isHybridDevice: false,
            hasHover: true,
            hasAnyHover: true,
            hasFinePointer: true,
            hasCoarsePointer: false,
            hasAnyFinePointer: true,
            hasAnyCoarsePointer: false,
            hasTouchScreen: false,
            maxTouchPoints: 0,
            isPortrait: false,
            isLandscape: true,
            isStandalone: false,
            platform: '',
            isIOS: false,
            isAndroid: false,
            isWindows: false,
            isMacOS: false,
            isLinux: false,
            width: 1920,
            height: 1080,
            screenWidth: 1920,
            screenHeight: 1080,
            pixelRatio: 1,
            aspectRatio: 1.78,
            prefersReducedMotion: false,
            prefersDarkMode: false,
            clientHints: null
        };
    },

    /**
     * Subscribe to device changes
     * @param {Function} callback - Called with device info on changes
     * @returns {Function} Unsubscribe function
     */
    subscribe(callback) {
        this._listeners.push(callback);

        // Call immediately with current state
        callback(this.detect());

        // Return unsubscribe function
        return () => {
            const idx = this._listeners.indexOf(callback);
            if (idx > -1) this._listeners.splice(idx, 1);
        };
    },

    /**
     * Notify all subscribers
     */
    _notifyListeners(info) {
        this._listeners.forEach(cb => {
            try { cb(info); } catch (e) { console.error('DeviceDetector listener error:', e); }
        });
    },

    // === Quick check methods ===
    isMobile: () => DeviceDetector.detect().isMobile,
    isViewportMobile: () => DeviceDetector.detect().isViewportMobile,
    isPhone: () => DeviceDetector.detect().isPhone,
    isTablet: () => DeviceDetector.detect().isTablet,
    isDesktop: () => DeviceDetector.detect().isDesktop,
    isLaptop: () => DeviceDetector.detect().isLaptop,
    isTV: () => DeviceDetector.detect().isTV,
    isTouchDevice: () => DeviceDetector.detect().isTouchDevice,
    isHybridDevice: () => DeviceDetector.detect().isHybridDevice,
    hasHover: () => DeviceDetector.detect().hasHover,
    prefersReducedMotion: () => DeviceDetector.detect().prefersReducedMotion,
    prefersDarkMode: () => DeviceDetector.detect().prefersDarkMode,

    /**
     * Debug: Expose raw detection signals
     * Useful for troubleshooting edge cases
     */
    debug() {
        const width = window.innerWidth;
        const height = window.innerHeight;

        return {
            // Raw Client Hints
            uaDataAvailable: !!navigator.userAgentData,
            uaMobile: navigator.userAgentData?.mobile ?? null,
            uaPlatform: navigator.userAgentData?.platform ?? null,
            clientHints: this._clientHints,

            // Raw Media Queries
            pointer: window.matchMedia('(pointer: coarse)').matches ? 'coarse' :
                     window.matchMedia('(pointer: fine)').matches ? 'fine' : 'none',
            anyPointer: {
                coarse: window.matchMedia('(any-pointer: coarse)').matches,
                fine: window.matchMedia('(any-pointer: fine)').matches
            },
            hover: window.matchMedia('(hover: hover)').matches ? 'hover' : 'none',
            anyHover: window.matchMedia('(any-hover: hover)').matches,

            // Raw Touch APIs
            ontouchstart: 'ontouchstart' in window,
            maxTouchPoints: navigator.maxTouchPoints || 0,

            // Raw Screen
            innerWidth: width,
            innerHeight: height,
            screenWidth: window.screen?.width,
            screenHeight: window.screen?.height,
            devicePixelRatio: window.devicePixelRatio,
            orientation: width > height ? 'landscape' : 'portrait',

            // Raw Preferences
            prefersReducedMotion: window.matchMedia('(prefers-reduced-motion: reduce)').matches,
            prefersDarkMode: window.matchMedia('(prefers-color-scheme: dark)').matches,
            displayMode: window.matchMedia('(display-mode: standalone)').matches ? 'standalone' : 'browser',

            // Computed result
            result: this.detect()
        };
    },

    /**
     * SSR-safe check: Returns true if running in browser
     */
    get isClient() {
        return typeof window !== 'undefined' && typeof document !== 'undefined';
    },

    /**
     * Apply device classes to body element
     * Call this on load and resize
     */
    applyBodyClasses() {
        const info = this.detect();
        const body = document.body;

        // Remove old classes
        body.classList.remove(
            'device-phone', 'device-tablet', 'device-laptop',
            'device-desktop', 'device-tv',
            'is-mobile', 'viewport-mobile', 'is-touch', 'is-hybrid',
            'is-portrait', 'is-landscape',
            'has-hover', 'no-hover',
            'is-ios', 'is-android', 'is-windows', 'is-macos', 'is-linux',
            'prefers-reduced-motion', 'prefers-dark-mode', 'is-standalone'
        );

        // Device type
        body.classList.add(`device-${info.deviceType}`);

        // Input capabilities
        if (info.isMobile) body.classList.add('is-mobile');
        if (info.isViewportMobile) body.classList.add('viewport-mobile');
        if (info.isTouchDevice) body.classList.add('is-touch');
        if (info.isHybridDevice) body.classList.add('is-hybrid');
        if (info.hasHover) body.classList.add('has-hover');
        if (!info.hasHover) body.classList.add('no-hover');

        // Orientation
        if (info.isPortrait) body.classList.add('is-portrait');
        if (info.isLandscape) body.classList.add('is-landscape');

        // Platform
        if (info.isIOS) body.classList.add('is-ios');
        if (info.isAndroid) body.classList.add('is-android');
        if (info.isWindows) body.classList.add('is-windows');
        if (info.isMacOS) body.classList.add('is-macos');
        if (info.isLinux) body.classList.add('is-linux');

        // User preferences
        if (info.prefersReducedMotion) body.classList.add('prefers-reduced-motion');
        if (info.prefersDarkMode) body.classList.add('prefers-dark-mode');
        if (info.isStandalone) body.classList.add('is-standalone');

        // Dispatch custom event for other scripts
        window.dispatchEvent(new CustomEvent('devicedetector:updated', { detail: info }));

        // Notify subscribers
        this._notifyListeners(info);

        return info;
    },

    /**
     * Initialize - call once on page load
     */
    async init() {
        // Fetch Client Hints (async, won't block)
        this.fetchClientHints().then(() => {
            this._cache = {};
            this.applyBodyClasses();
        });

        // Apply classes immediately (with fallbacks)
        this.applyBodyClasses();

        // Mark page as ready (for FOUC prevention)
        document.body.classList.add('page-ready');

        // FOUC Debug - after page-ready added
        if (window.FOUC_DEBUG) {
            const chatbox = document.querySelector('.chatbox');
            const greeting = document.querySelector('.greeting-container');
            const svg = document.querySelector('.sidebar-toggle-button svg');
            console.log('[FOUC Debug] page-ready added at:', performance.now().toFixed(2) + 'ms', {
                bodyClasses: document.body.className,
                chatboxOpacity: chatbox ? getComputedStyle(chatbox).opacity : 'N/A',
                chatboxVisibility: chatbox ? getComputedStyle(chatbox).visibility : 'N/A',
                greetingOpacity: greeting ? getComputedStyle(greeting).opacity : 'N/A',
                svgWidth: svg ? getComputedStyle(svg).width : 'N/A'
            });
        }

        // Update on resize (debounced)
        let resizeTimer;
        window.addEventListener('resize', () => {
            clearTimeout(resizeTimer);
            resizeTimer = setTimeout(() => {
                this._cache = {};
                this.applyBodyClasses();
            }, 100);
        }, { passive: true });

        // Update on orientation change
        window.addEventListener('orientationchange', () => {
            this._cache = {};
            setTimeout(() => this.applyBodyClasses(), 100);
        }, { passive: true });

        // Listen for media query changes (e.g., user changes dark mode)
        window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => {
            this._cache = {};
            this.applyBodyClasses();
        });

        window.matchMedia('(prefers-reduced-motion: reduce)').addEventListener('change', () => {
            this._cache = {};
            this.applyBodyClasses();
        });

        console.log('DeviceDetector v2 initialized:', this.detect());
    }
};

// Make globally available
window.DeviceDetector = DeviceDetector;

// Auto-init when DOM ready
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => DeviceDetector.init());
} else {
    DeviceDetector.init();
}
