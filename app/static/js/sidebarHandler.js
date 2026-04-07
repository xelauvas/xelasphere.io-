class SidebarManager {
    constructor() {
      this.initialized = false;
      this.resizeTimeout = null;
      this.isUserCollapsed = false;
      this.isAutoCollapsed = false;

      // Mobile state
      this.isMobile = false;
      this.isViewportMobile = false;
      this.mobileBreakpoint = 768;
      this.touchStartX = 0;
      this.touchStartY = 0;
      this.touchCurrentX = 0;
      this.swipeThreshold = 50;
      this.edgeSwipeZone = 30;

      // Elementele DOM cache
      this.elements = {
        leftSidebar: null,
        rightSidebar: null,
        container: null,
        toggleButton: null,
        contentWrapper: null,
        inputContainer: null,
        downArrow: null,
        modelSelectWrapper: null,
        sourcesButton: null,
        closeSidebarButton: null,
        mobileOverlay: null,
        sidebarToggleButton: null
      };

      // Constante
      this.SIDEBAR_WIDTH = 250;
      this.TRANSITION_DURATION = 300;

      // Legăm metodele
      this.toggleLeftSidebar = this.toggleLeftSidebar.bind(this);
      this.toggleRightSidebar = this.toggleRightSidebar.bind(this);
      this.updateInputContainerPosition = this.updateInputContainerPosition.bind(this);
      this.updateModelSelectPosition = this.updateModelSelectPosition.bind(this);
      this.handleResize = this.handleResize.bind(this);
      this.handleTouchStart = this.handleTouchStart.bind(this);
      this.handleTouchMove = this.handleTouchMove.bind(this);
      this.handleTouchEnd = this.handleTouchEnd.bind(this);
      this.closeMobileSidebar = this.closeMobileSidebar.bind(this);
      this.openMobileSidebar = this.openMobileSidebar.bind(this);
    }

    init() {
      if (this.initialized) return;

      document.body.classList.add('no-transitions');

      // Cache DOM elements
      this.elements = {
        leftSidebar: document.getElementById('sidebar'),
        rightSidebar: document.getElementById('right-sidebar'),
        container: document.querySelector('.container'),
        toggleButton: document.querySelector('.toggle-button'),
        mobileHeaderBurger: document.getElementById('mobile-header-burger'),
        contentWrapper: document.querySelector('.content-wrapper'),
        inputContainer: document.querySelector('.input-container'),
        downArrow: document.querySelector('.arrow-down'),
        modelSelectWrapper: document.querySelector('.model-select-wrapper'),
        sourcesButton: document.getElementById('sources-btn'),
        closeSidebarButton: document.querySelector('.close-sidebar-btn'),
        mobileOverlay: document.getElementById('mobile-overlay'),
        sidebarToggleButton: document.getElementById('sidebar-toggle-button')
      };

      // Check if mobile on init
      this.checkMobileViewport();

      this.addTransitionStyles();
      this.initializeSidebarStates();
      this.bindUiEvents();
      this.bindMobileEvents();

      // Event listener pentru redimensionare
      window.addEventListener('resize', this.handleResize, { passive: true });

      // Metodele devin accesibile global
      window.toggleLeftSidebar = this.toggleLeftSidebar;
      window.toggleRightSidebar = this.toggleRightSidebar;
      window.openMobileSidebar = this.openMobileSidebar;
      window.closeMobileSidebar = this.closeMobileSidebar;

      requestAnimationFrame(() => {
        requestAnimationFrame(() => {
          document.body.classList.remove('no-transitions');
        });
      });

      this.initialized = true;
    }

    bindUiEvents() {
      const { toggleButton, mobileHeaderBurger, sourcesButton, closeSidebarButton } = this.elements;

      if (toggleButton) {
        toggleButton.addEventListener('click', () => this.toggleLeftSidebar());
      }

      // Mobile header burger button
      if (mobileHeaderBurger) {
        mobileHeaderBurger.addEventListener('click', () => {
          if (this.shouldUseMobileLayout()) {
            this.openMobileSidebar();
          } else {
            this.toggleLeftSidebar();
          }
        });
      }

      if (sourcesButton) {
        sourcesButton.addEventListener('click', () => this.toggleRightSidebar());
      }

      if (closeSidebarButton) {
        closeSidebarButton.addEventListener('click', () => this.toggleRightSidebar());
      }
    }

    addTransitionStyles() {
      const styleSheet = document.createElement('style');
      styleSheet.textContent = `
        .sidebar, .container, .toggle-button, .input-container, 
        .content-wrapper, .model-select-wrapper {
          transition: all ${this.TRANSITION_DURATION}ms ease;
        }
        
        .no-transitions,
        .no-transitions * {
          transition: none !important;
        }
        
        .model-select-wrapper {
          position: fixed;
          z-index: 150;
        }
        
      `;
      document.head.appendChild(styleSheet);
    }

    initializeSidebarStates() {
      const { leftSidebar, rightSidebar, container, toggleButton } = this.elements;

      if (leftSidebar) {
        leftSidebar.classList.remove('closed');
      }

      document.body.classList.remove('sidebar-collapsed');
      this.isUserCollapsed = false;

      if (container) {
        container.style.marginLeft = '';
        container.style.width = '';
      }

      if (rightSidebar && !rightSidebar.classList.contains('open')) {
        rightSidebar.classList.add('closed');
      }

      this.syncSidebarWithViewport();
    }

    syncSidebarWithViewport() {
      this.isAutoCollapsed = false;
      this.applySidebarState();
    }

    applySidebarState() {
      const { leftSidebar, toggleButton, container, downArrow } = this.elements;
      const shouldCollapse = this.isAutoCollapsed || this.isUserCollapsed;

      document.body.classList.toggle('sidebar-collapsed', shouldCollapse);

      if (leftSidebar) {
        leftSidebar.classList.toggle('closed', shouldCollapse);
      }

      if (toggleButton) {
        toggleButton.classList.toggle('open', !shouldCollapse);
        toggleButton.classList.toggle('closed', shouldCollapse);
        toggleButton.classList.toggle('closed-hover', shouldCollapse);
      }

      if (container) {
        container.style.marginLeft = '';
        container.style.width = '';
      }

      if (downArrow) {
        downArrow.style.left = '';
      }

      this.updateInputContainerPosition();
      this.updateModelSelectPosition();
      this.updateGuestFloatingLogin(shouldCollapse);

      document.dispatchEvent(new CustomEvent('sidebar-state-changed', {
        detail: { collapsed: shouldCollapse }
      }));
    }

    updateGuestFloatingLogin(show) {
      const floatingLogin = document.getElementById('guest-floating-login');
      if (!floatingLogin) return;
      floatingLogin.classList.toggle('visible', show);
    }

    updateModelSelectPosition() {
      const { leftSidebar, modelSelectWrapper } = this.elements;
      if (!modelSelectWrapper) return;

      requestAnimationFrame(() => {
        modelSelectWrapper.style.position = 'fixed';
        modelSelectWrapper.style.top = '1rem';
        modelSelectWrapper.style.left = '';
        modelSelectWrapper.style.right = 'auto';
        modelSelectWrapper.style.transform = 'none';
      });
    }
    
    // Modificare: Dacă ambele sidebar-uri sunt open, containerul de input se centerază
    updateInputContainerPosition() {
      const { inputContainer } = this.elements;
      if (!inputContainer) return;

      if (document.body.classList.contains('temporary-chat-centered')) {
        return;
      }

      requestAnimationFrame(() => {
        inputContainer.style.width = '';
        inputContainer.style.left = '';
        inputContainer.style.right = '';
        inputContainer.style.transform = '';
      });
    }

    toggleRightSidebar(force = null) {
      const { rightSidebar, contentWrapper } = this.elements;
      if (!rightSidebar) return;

      const currentlyClosed = rightSidebar.classList.contains('closed');
      const targetClosed = force === null
        ? !currentlyClosed
        : force === true
          ? false
          : true;

      if (force !== null && targetClosed === currentlyClosed) {
        return;
      }

      rightSidebar.classList.toggle('closed', targetClosed);
      const isClosed = targetClosed;
      const willOpen = !isClosed;

      if (contentWrapper) {
        contentWrapper.style.marginRight = isClosed ? '0' : `${this.SIDEBAR_WIDTH}px`;
      }

      document.body.classList.toggle('right-sidebar-open', !isClosed);

      if (isClosed) {
        const sourcesBtn = document.getElementById('sources-btn');
        if (sourcesBtn && sourcesBtn.dataset) {
          delete sourcesBtn.dataset.activeSourceMessage;
        }
      }

      this.updateInputContainerPosition();
      this.updateModelSelectPosition();
    }

    handleResize() {
      if (this.resizeTimeout) {
        window.cancelAnimationFrame(this.resizeTimeout);
      }
      this.resizeTimeout = window.requestAnimationFrame(() => {
        const wasMobile = this.isMobile;
        this.checkMobileViewport();

        // If switching from mobile to desktop, close mobile sidebar
        if (wasMobile && !this.isMobile) {
          this.closeMobileSidebar();
        }

        this.syncSidebarWithViewport();
        this.resizeTimeout = null;
      });
    }

    // === MOBILE METHODS ===

    checkMobileViewport() {
      // Use DeviceDetector if available, fallback to width check
      if (window.DeviceDetector) {
        const device = DeviceDetector.detect();
        this.isMobile = device.isMobile;
        this.isPhone = device.isPhone;
        this.isTablet = device.isTablet;
        this.isTouchDevice = device.isTouchDevice;
        this.isViewportMobile = device.isViewportMobile || false;
        // DeviceDetector already applies body classes
      } else {
        // Fallback to simple width check
        this.isMobile = window.innerWidth <= this.mobileBreakpoint;
        this.isViewportMobile = window.innerWidth <= this.mobileBreakpoint;
        document.body.classList.toggle('is-mobile', this.isMobile);
      }
    }

    // Check if mobile layout should be used (real mobile OR resized desktop)
    shouldUseMobileLayout() {
      return this.isMobile || this.isViewportMobile || document.body.classList.contains('viewport-mobile');
    }

    bindMobileEvents() {
      const { mobileOverlay, sidebarToggleButton, toggleButton } = this.elements;

      // Overlay click closes sidebar
      if (mobileOverlay) {
        mobileOverlay.addEventListener('click', this.closeMobileSidebar);
      }

      // Touch events for swipe gestures
      document.addEventListener('touchstart', this.handleTouchStart, { passive: true });
      document.addEventListener('touchmove', this.handleTouchMove, { passive: true });
      document.addEventListener('touchend', this.handleTouchEnd, { passive: true });

      // Escape key closes mobile sidebar
      document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape' && this.shouldUseMobileLayout() && document.body.classList.contains('mobile-sidebar-open')) {
          this.closeMobileSidebar();
        }
      });

      // Close sidebar when a chat tab is clicked on mobile (but not when clicking tab actions)
      document.addEventListener('click', (e) => {
        if (!this.shouldUseMobileLayout()) return;

        // Don't close if clicking on tab actions toggle or its dropdown
        const isTabActionsClick = e.target.closest('.tab-actions-toggle') ||
                                   e.target.closest('.button-group') ||
                                   e.target.closest('.tab-actions');
        if (isTabActionsClick) return;

        const chatTab = e.target.closest('.chat-tab');
        if (chatTab && document.body.classList.contains('mobile-sidebar-open')) {
          // Small delay to allow the click action to complete
          setTimeout(() => {
            this.closeMobileSidebar();
          }, 150);
        }
      });
    }

    openMobileSidebar() {
      if (!this.shouldUseMobileLayout()) return;

      const { leftSidebar, mobileOverlay } = this.elements;

      document.body.classList.add('mobile-sidebar-open');

      if (mobileOverlay) {
        mobileOverlay.classList.add('active');
      }

      if (leftSidebar) {
        leftSidebar.classList.remove('closed');
      }

      // Dispatch event for other components
      document.dispatchEvent(new CustomEvent('mobile-sidebar-opened'));
    }

    closeMobileSidebar() {
      const { leftSidebar, mobileOverlay } = this.elements;

      document.body.classList.remove('mobile-sidebar-open');

      if (mobileOverlay) {
        mobileOverlay.classList.remove('active');
      }

      // Dispatch event for other components
      document.dispatchEvent(new CustomEvent('mobile-sidebar-closed'));
    }

    handleTouchStart(e) {
      if (!this.isMobile) return;

      const touch = e.touches[0];
      this.touchStartX = touch.clientX;
      this.touchStartY = touch.clientY;
      this.touchCurrentX = touch.clientX;
    }

    handleTouchMove(e) {
      if (!this.isMobile) return;

      const touch = e.touches[0];
      this.touchCurrentX = touch.clientX;
    }

    handleTouchEnd(e) {
      if (!this.isMobile) return;

      const deltaX = this.touchCurrentX - this.touchStartX;
      const isSidebarOpen = document.body.classList.contains('mobile-sidebar-open');

      // Swipe right from edge to open
      if (!isSidebarOpen && this.touchStartX <= this.edgeSwipeZone && deltaX > this.swipeThreshold) {
        this.openMobileSidebar();
      }

      // Swipe left to close
      if (isSidebarOpen && deltaX < -this.swipeThreshold) {
        this.closeMobileSidebar();
      }

      // Reset touch tracking
      this.touchStartX = 0;
      this.touchStartY = 0;
      this.touchCurrentX = 0;
    }

    // Override toggleLeftSidebar for mobile
    toggleLeftSidebar(force = null) {
      // On mobile/viewport-mobile, use the drawer pattern
      if (this.shouldUseMobileLayout()) {
        const isSidebarOpen = document.body.classList.contains('mobile-sidebar-open');
        const nextState = force !== null ? force : !isSidebarOpen;

        if (nextState) {
          this.openMobileSidebar();
        } else {
          this.closeMobileSidebar();
        }
        return;
      }

      // Desktop behavior
      const { leftSidebar } = this.elements;
      if (!leftSidebar) return;

      const nextState = force !== null ? force : !this.isUserCollapsed;
      if (this.isUserCollapsed === nextState) {
        this.applySidebarState();
        return;
      }

      this.isUserCollapsed = nextState;
      this.applySidebarState();
    }
  }

  // Inițializare când documentul este gata
  document.addEventListener('DOMContentLoaded', () => {
    const sidebarManager = new SidebarManager();
    sidebarManager.init();
    window.sidebarManager = sidebarManager;

    // Enable mobile sidebar transitions after initial render (prevents FOUC)
    requestAnimationFrame(() => {
      document.body.classList.add('mobile-ready');
    });

    // === Pull to Refresh (Mobile Only) ===
    const isMobileDevice = window.DeviceDetector ? DeviceDetector.isMobile() : (window.innerWidth <= 768);
    if (isMobileDevice && ('ontouchstart' in window || navigator.maxTouchPoints > 0)) {
      initPullToRefresh();

      // Fix iOS Safari input focus issue
      // iOS has a bug where quick taps on inputs don't properly focus
      initIOSInputFix();
    }

    // Fix Chrome caret rendering bug on hybrid touch/mouse devices
    initHybridCaretFix();

    // === Has Messages Class Management ===
    // Toggle body.has-messages based on chat content for mobile home page styling
    const checkHasMessages = () => {
      const chatDisplay = document.getElementById('chat-display');
      // Count actual messages, excluding scroll-sentinel and other non-message elements
      let messageCount = 0;
      if (chatDisplay) {
        for (const child of chatDisplay.children) {
          if (child.id === 'scroll-sentinel') continue;
          if (child.classList.contains('message') || child.dataset.role) {
            messageCount++;
          }
        }
      }
      document.body.classList.toggle('has-messages', messageCount > 0);
    };

    // Initial check
    checkHasMessages();

    // Watch for changes in chat-display
    const chatDisplay = document.getElementById('chat-display');
    if (chatDisplay) {
      const observer = new MutationObserver(checkHasMessages);
      observer.observe(chatDisplay, { childList: true, subtree: true });
    }

    // Also check on custom events (new chat, load chat, etc.)
    document.addEventListener('chat-cleared', checkHasMessages);
    document.addEventListener('messages-loaded', checkHasMessages);
    document.addEventListener('message-added', checkHasMessages);

    // Make function globally available
    window.checkHasMessages = checkHasMessages;

    // === Keyboard Shortcuts Handler ===
    initKeyboardShortcuts();
  });

  /**
   * Initialize keyboard shortcuts for sidebar actions
   * Detects Mac vs Windows/Linux and shows appropriate shortcuts on hover
   */
  function initKeyboardShortcuts() {
    const isMac = navigator.platform.toUpperCase().indexOf('MAC') >= 0 ||
                  navigator.userAgent.toUpperCase().indexOf('MAC') >= 0;

    // Find all buttons with shortcuts
    const shortcutButtons = document.querySelectorAll('.sidebar-action-btn.has-shortcut');

    shortcutButtons.forEach(btn => {
      const shortcutHint = btn.querySelector('.shortcut-hint');
      if (!shortcutHint) return;

      // Get the appropriate shortcut based on platform
      const shortcut = isMac ? btn.dataset.shortcutMac : btn.dataset.shortcutWin;
      if (shortcut) {
        shortcutHint.textContent = shortcut;
      }
    });

    // Add global keyboard event listener
    document.addEventListener('keydown', handleKeyboardShortcut);
  }

  /**
   * Handle keyboard shortcuts
   * @param {KeyboardEvent} e - The keyboard event
   */
  function handleKeyboardShortcut(e) {
    // Don't trigger shortcuts if user is typing in an input/textarea
    const activeEl = document.activeElement;
    const isTyping = activeEl && (
      activeEl.tagName === 'INPUT' ||
      activeEl.tagName === 'TEXTAREA' ||
      activeEl.isContentEditable
    );

    if (isTyping) return;

    // Check for guest mode
    const isGuest = document.body.dataset.guestMode === 'true';

    const isMac = navigator.platform.toUpperCase().indexOf('MAC') >= 0 ||
                  navigator.userAgent.toUpperCase().indexOf('MAC') >= 0;
    const modKey = isMac ? e.metaKey : e.ctrlKey;

    // Ctrl/Cmd + Shift + O => New Chat
    if (modKey && e.shiftKey && e.key.toLowerCase() === 'o') {
      e.preventDefault();
      if (!isGuest) {
        const newChatBtn = document.getElementById('action-new-chat');
        if (newChatBtn) newChatBtn.click();
      }
      return;
    }

    // Ctrl/Cmd + K => Search Chats
    if (modKey && !e.shiftKey && e.key.toLowerCase() === 'k') {
      e.preventDefault();
      if (!isGuest) {
        const searchBtn = document.getElementById('action-search-chat');
        if (searchBtn) searchBtn.click();
      }
      return;
    }

    // Ctrl/Cmd + Shift + I => Images
    if (modKey && e.shiftKey && e.key.toLowerCase() === 'i') {
      e.preventDefault();
      if (!isGuest) {
        const imagesBtn = document.getElementById('action-images');
        if (imagesBtn) imagesBtn.click();
      }
      return;
    }
  }

  /**
   * Initialize Pull to Refresh for mobile (no animation)
   */
  function initPullToRefresh() {
    let startY = 0;
    let pulling = false;
    const threshold = 80;

    const isAtTop = (el) => {
      // Check if touch is inside chat scroll region
      const chatScrollRegion = document.getElementById('chat-scroll-region');
      if (chatScrollRegion && chatScrollRegion.contains(el)) {
        // Inside chat - check if chat container is at top
        return chatScrollRegion.scrollTop <= 0;
      }
      // Outside chat - check window scroll
      return (window.scrollY || document.documentElement.scrollTop || 0) <= 0;
    };

    const isInputElement = (el) => {
      const tag = el.tagName.toLowerCase();
      return tag === 'input' || tag === 'textarea' || el.isContentEditable;
    };

    const isInsideScrollableChat = (el) => {
      const chatScrollRegion = document.getElementById('chat-scroll-region');
      return chatScrollRegion && chatScrollRegion.contains(el);
    };

    document.addEventListener('touchstart', (e) => {
      // Don't activate pull-to-refresh when touching inputs
      if (isInputElement(e.target)) return;

      // Don't activate when inside chat area (chat has its own scroll)
      if (isInsideScrollableChat(e.target) && document.body.classList.contains('has-messages')) {
        return;
      }

      if (isAtTop(e.target) && !document.body.classList.contains('mobile-sidebar-open')) {
        startY = e.touches[0].pageY;
        pulling = true;
      }
    }, { passive: true });

    document.addEventListener('touchend', (e) => {
      if (!pulling) return;

      const endY = e.changedTouches[0].pageY;
      const pullDistance = endY - startY;

      if (pullDistance >= threshold && isAtTop(e.target)) {
        window.location.reload();
      }

      pulling = false;
      startY = 0;
    }, { passive: true });
  }

  /**
   * Fix iOS Safari input focus issue
   * iOS Safari has a bug where quick taps don't properly focus textareas
   * This uses a combination of techniques to ensure proper focus
   */
  function initIOSInputFix() {
    const chatInput = document.querySelector('#prompt, .chat-input');
    const textareaContainer = document.querySelector('.textarea-container');

    if (!chatInput) return;

    // Detect iOS Safari
    const isIOS = /iPad|iPhone|iPod/.test(navigator.userAgent) ||
                  (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
    const isSafari = /^((?!chrome|android).)*safari/i.test(navigator.userAgent);

    // Remove autofocus on iOS as it can interfere with manual focus
    if (isIOS) {
      chatInput.removeAttribute('autofocus');
    }

    // Track touch state
    let isTouching = false;
    let touchTimer = null;

    // Helper to force focus on iOS
    const forceIOSFocus = (input) => {
      // Clear any no-select
      document.body.classList.remove('no-select');

      // Blur first then focus - this can help reset iOS state
      if (document.activeElement !== input) {
        input.blur();
      }

      // Small delay then focus
      requestAnimationFrame(() => {
        input.focus();

        // Set selection to trigger keyboard
        if (input.setSelectionRange) {
          const len = input.value ? input.value.length : 0;
          try {
            input.setSelectionRange(len, len);
          } catch (e) {
            // Ignore selection errors
          }
        }
      });
    };

    // For iOS, we need to intercept touches early and ensure focus
    if (isIOS) {
      // Use a capturing listener on the container to catch touches first
      const inputContainer = chatInput.closest('.input-row') || textareaContainer;

      if (inputContainer) {
        inputContainer.addEventListener('touchstart', (e) => {
          // Check if touch is on or near the input
          const target = e.target;
          if (target === chatInput || target.closest('.textarea-container')) {
            isTouching = true;
            document.body.classList.remove('no-select');

            // Start a timer - if touchend doesn't fire soon, it's a hold
            touchTimer = setTimeout(() => {
              touchTimer = null;
            }, 400);
          }
        }, { passive: true, capture: true });

        inputContainer.addEventListener('touchend', (e) => {
          if (!isTouching) return;
          isTouching = false;

          const target = e.target;
          const isQuickTap = touchTimer !== null;

          if (touchTimer) {
            clearTimeout(touchTimer);
            touchTimer = null;
          }

          // If quick tap on input area, force focus
          if (isQuickTap && (target === chatInput || target.closest('.textarea-container'))) {
            // Prevent default to stop iOS from doing its own thing
            // Note: This might not work with passive but we try anyway

            // Force focus multiple times to ensure it sticks
            forceIOSFocus(chatInput);
            setTimeout(() => forceIOSFocus(chatInput), 50);
            setTimeout(() => forceIOSFocus(chatInput), 150);
          }
        }, { passive: true, capture: true });
      }

      // Also add a direct handler on the input itself
      chatInput.addEventListener('touchend', (e) => {
        // Always try to focus on touchend for the input itself
        setTimeout(() => {
          document.body.classList.remove('no-select');
          chatInput.focus();
        }, 0);
      }, { passive: true });
    }

    // Click handler for all platforms
    chatInput.addEventListener('click', () => {
      document.body.classList.remove('no-select');
      chatInput.focus();
    });

    // Focus handler
    chatInput.addEventListener('focus', () => {
      document.body.classList.remove('no-select');
    }, { passive: true });

    console.log('[iOS Fix] Initialized', isIOS ? '(iOS)' : '(non-iOS)', isSafari ? '(Safari)' : '');
  }

  /**
   * Fix Chrome caret rendering bug on hybrid touch/mouse devices
   * Chrome sometimes fails to render the caret even when the textarea has focus.
   * This fix forces a repaint using multiple techniques.
   */
  function initHybridCaretFix() {
    const chatInput = document.querySelector('#prompt, .chat-input');
    if (!chatInput) return;

    // Only apply on hybrid devices (touch + mouse support)
    const isHybrid = document.body.classList.contains('is-hybrid') ||
                     (('ontouchstart' in window || navigator.maxTouchPoints > 0) &&
                      window.matchMedia('(pointer: fine)').matches);

    if (!isHybrid) return;

    // Force repaint helper
    const forceCaretRepaint = () => {
      // Save current selection before repaint
      const selStart = chatInput.selectionStart;
      const selEnd = chatInput.selectionEnd;

      // Technique 1: Toggle opacity to force repaint
      chatInput.style.opacity = '0.999';

      // Technique 2: Force layout recalculation
      void chatInput.offsetHeight;

      // Technique 3: Reset opacity after brief delay
      requestAnimationFrame(() => {
        chatInput.style.opacity = '';

        // Technique 4: Re-apply the same selection to trigger caret render
        // Only if no text is selected (preserves user selection)
        if (chatInput.setSelectionRange && document.activeElement === chatInput) {
          if (selStart === selEnd) {
            try {
              // Re-set same position to trigger caret render
              chatInput.setSelectionRange(selStart, selEnd);
            } catch (e) {
              // Ignore selection errors
            }
          }
        }
      });
    };

    // Apply fix on focus
    chatInput.addEventListener('focus', forceCaretRepaint);

    // Also apply on click (handles the focusin/focusout Chrome bug)
    chatInput.addEventListener('click', () => {
      // Small delay to let focus settle
      setTimeout(forceCaretRepaint, 10);
    });

    console.log('[Hybrid Caret Fix] Initialized');
  }
