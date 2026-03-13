document.addEventListener('DOMContentLoaded', () => {
    class TypeTextAnimator {
      constructor(text, container, isCode, onFinish, options = {}) {
        // Defense-in-depth: sanitize HTML content if DOMPurify is available
        // (content should already be sanitized upstream, but this ensures safety)
        const sanitizedText = !isCode && typeof DOMPurify !== 'undefined'
            ? DOMPurify.sanitize(text)
            : text;
        this.text = sanitizedText;
        // Split text into tokens including whitespace so that formatting is preserved.
        this.tokens = sanitizedText.split(/(\s+)/);
        this.container = container;
        this.isCode = isCode;
        this.onFinish = onFinish;
        this.isCodeWindow = container.closest('.code-window') !== null;
        // Cache the chatbox element to avoid repeated lookups.
        this.chatBox = document.getElementById('chatbox');
  
        // Merge default options with any passed in options.
        this.options = {
          speed: options.speed ?? 5,
          cursorChar: options.cursorChar ?? '⚪',
          onProgress: options.onProgress ?? null,
          scrollBehavior: {
            smooth: options.scrollBehavior?.smooth ?? true,
            threshold: options.scrollBehavior?.threshold ?? 100,
            forceScroll: options.scrollBehavior?.forceScroll ?? false,
          },
          language: options.language ?? 'javascript',
        };
  
        this.state = {
          tokenIndex: 0,
          outputText: '',
          cursorInterval: null,
          scroll: {
            userHasScrolled: false,
            lastUserInteraction: Date.now(),
            scrollTimeout: null,
          },
          isHidden: false,
          isStopped: false,
        };
  
        // Initialize UI elements and hide the sources button.
        this.ui = this.initializeUI();
        this.toggleSourcesButton(false);
        
        // Listen for visibility changes so that if the page is hidden the typing jumps to the end.
        this.visibilityHandler = () => {
          this.state.isHidden = document.visibilityState === 'hidden';
        };
        document.addEventListener('visibilitychange', this.visibilityHandler);
  
        // For code windows, create an offscreen element for syntax highlighting.
        if (this.isCodeWindow && this.isCode) {
          this.hiddenElement = this.initializeHiddenElement();
        }
      }
  
      /**
       * Toggles the display of the sources button.
       * @param {boolean} show - If true, show the button; otherwise hide it.
       */
      toggleSourcesButton(show) {
        const sourcesBtn = document.getElementById('sources-btn');
        if (!sourcesBtn) return;
        const hasSources = sourcesBtn.dataset.hasSources === 'true';
        sourcesBtn.dataset.toggleVisible = show && hasSources ? 'true' : 'false';
      }
  
      /**
       * Creates a hidden pre/code element offscreen for Prism to perform syntax highlighting.
       */
      initializeHiddenElement() {
        const pre = document.createElement('pre');
        const code = document.createElement('code');
        code.className = `language-${this.options.language}`;
        pre.appendChild(code);
        Object.assign(pre.style, {
          position: 'absolute',
          left: '-9999px',
          top: '-9999px',
          visibility: 'hidden',
        });
        document.body.appendChild(pre);
        return { pre, code };
      }
  
      /**
       * Finds and initializes the UI controls.
       */
      initializeUI() {
        const sendButton = document.querySelector('.send-button');
        if (!sendButton) {
          throw new Error('Required UI elements not found');
        }

        // Track the active animator globally so other modules can interrogate it.
        window.activeAnimator = this;

        this.stopHandler = () => {
          if (!sendButton.classList.contains('show-stop')) {
            return;
          }
          if (window.activeAnimator === this) {
            window.activeAnimator.stop();
          }
        };
        sendButton.addEventListener('click', this.stopHandler);

        return { sendButton };
      }
  
      /**
       * Checks whether the element is scrolled to the bottom (within a threshold).
       */
      isAtBottom(element) {
        return (
          element.scrollHeight - element.scrollTop - element.clientHeight <=
          this.options.scrollBehavior.threshold
        );
      }
  
      /**
       * Scrolls the chatbox element to the bottom.
       */
      scrollToBottom(smooth = true) {
        // First try to scroll the cursor into view (most reliable)
        const cursor = this.container.querySelector('.cursor-default, .cursor-code, .streaming-cursor');
        if (cursor) {
          cursor.scrollIntoView({
            behavior: smooth ? 'smooth' : 'auto',
            block: 'end',
            inline: 'nearest'
          });
          return;
        }

        // Fallback to chatbox scroll
        if (this.chatBox) {
          this.chatBox.scrollTo({
            top: this.chatBox.scrollHeight,
            behavior: smooth ? 'smooth' : 'auto',
          });
        }
      }
  
      /**
       * Handles auto-scrolling behavior during the typing animation.
       */
      handleScroll() {
        if (!this.chatBox) return;

        // Respect global scrollState if available (takes priority)
        if (window.scrollState && !window.scrollState.isPinnedToBottom) {
          return; // User scrolled up, don't auto-scroll
        }

        const shouldAutoScroll =
          (!this.state.scroll.userHasScrolled && this.isAtBottom(this.chatBox)) ||
          this.options.scrollBehavior.forceScroll ||
          (Date.now() - this.state.scroll.lastUserInteraction > 1000 &&
            this.isAtBottom(this.chatBox));

        if (shouldAutoScroll) {
          this.scrollToBottom(this.options.scrollBehavior.smooth);
        }
      }
  
      /**
       * Sets up listeners for scroll events and returns a cleanup function.
       */
      setupScrollListeners() {
        if (!this.chatBox) return () => {};
  
        const scrollHandler = (e) => {
          if (this.state.scroll.scrollTimeout) {
            clearTimeout(this.state.scroll.scrollTimeout);
          }
          if (e.isTrusted) {
            this.state.scroll.userHasScrolled = true;
            this.state.scroll.lastUserInteraction = Date.now();
          }
          if (this.isAtBottom(this.chatBox)) {
            this.state.scroll.userHasScrolled = false;
          }
          // Debounce (currently a placeholder timeout)
          this.state.scroll.scrollTimeout = setTimeout(() => {}, 150);
        };
  
        this.chatBox.addEventListener('scroll', scrollHandler, { passive: true });
        return () => this.chatBox.removeEventListener('scroll', scrollHandler);
      }
  
      /**
       * Updates the container with the current output text and blinking cursor.
       * Adds Gemini-style fade-in effect on new text chunks.
       */
      updateDisplay() {
        const cursorClass = this.isCodeWindow && this.isCode ? 'cursor-code' : 'cursor-default';

        // Get the latest token for fade-in effect
        const latestToken = this.tokens[this.state.tokenIndex - 1] || '';
        const previousText = this.state.outputText.slice(0, -latestToken.length);

        // Wrap the latest token in a fade-in span
        const fadeInToken = latestToken ? `<span class="stream-fade-in">${latestToken}</span>` : '';

        if (this.isCodeWindow && this.isCode && this.hiddenElement) {
          // Container is already the <code> element, update it directly
          this.hiddenElement.code.textContent = this.state.outputText;
          if (window.Prism) {
            Prism.highlightElement(this.hiddenElement.code);
          }
          // Copy the highlighted HTML along with the cursor.
          this.container.innerHTML = `${this.hiddenElement.code.innerHTML}<span class="${cursorClass}">${this.options.cursorChar}</span>`;
        } else {
          // For normal text, apply fade-in to the latest token
          this.container.innerHTML = `${previousText}${fadeInToken}<span class="${cursorClass}">${this.options.cursorChar}</span>`;
        }
      }
  
      /**
       * A helper method to pause execution.
       * @param {number} ms - Number of milliseconds to sleep.
       */
      sleep(ms) {
        return new Promise((resolve) => setTimeout(resolve, ms));
      }
  
      /**
       * Types the next token. Returns true if more tokens remain.
       */
      async typeToken() {
        if (this.state.isStopped) {
          this.cleanup();
          return false;
        }
  
        if (this.state.tokenIndex < this.tokens.length) {
          // If the page is hidden, output the rest of the text immediately.
          if (this.state.isHidden) {
            this.state.outputText += this.tokens.slice(this.state.tokenIndex).join('');
            this.state.tokenIndex = this.tokens.length;
            this.updateDisplay();
            this.cleanup();
            return false;
          }
  
          const nextToken = this.tokens[this.state.tokenIndex++];
          this.state.outputText += nextToken;
          this.updateDisplay();
          // Always scroll cursor into view during streaming
          this.scrollToBottom(false); // Use instant scroll for smoother following
  
          if (this.options.onProgress) {
            this.options.onProgress({
              index: this.state.tokenIndex,
              total: this.tokens.length,
              percentage: (this.state.tokenIndex / this.tokens.length) * 100,
            });
          }
  
          await this.sleep(this.options.speed);
          return true;
        }
  
        return false;
      }
  
      /**
       * Starts the blinking cursor effect.
       */
      handleCursor() {
        const cursorClass = this.isCodeWindow && this.isCode ? 'cursor-code' : 'cursor-default';
        this.state.cursorInterval = setInterval(() => {
          const cursorElement = this.container.querySelector(`.${cursorClass}`);
          if (cursorElement) {
            cursorElement.classList.toggle('blink');
          }
        }, 500);
  
        if (this.isCodeWindow && this.isCode) {
          // Container is already a <code> element inside a <pre>, don't nest another pre/code
          this.container.innerHTML = `<span class="${cursorClass}">${this.options.cursorChar}</span>`;
        } else {
          this.container.innerHTML = `<span class="${cursorClass}">${this.options.cursorChar}</span>`;
        }
      }
  
      /**
       * Stops the typing animation.
       */
      stop() {
        this.state.isStopped = true;
        // Abort any in-flight chat request if available
        if (typeof window !== 'undefined' && typeof window.abortMessage === 'function') {
          window.abortMessage();
        }
        // Cleanup UI immediately
        this.cleanup();
      }
  
      /**
       * Cleans up resources and resets the UI once typing is complete or stopped.
       */
      cleanup() {
        clearInterval(this.state.cursorInterval);
        const cursor = this.container.querySelector('.cursor-default, .cursor-code');
        if (cursor) {
          cursor.classList.remove('blink');
        }
  
        if (this.isCodeWindow && this.isCode) {
          // Container is already the <code> element, update it directly
          this.container.textContent = this.text;
          if (window.Prism) {
            Prism.highlightElement(this.container);
          }
        } else {
          this.container.innerHTML = this.state.outputText;
        }
  
        // Re-enable UI elements via the shared send button state handler.
        if (typeof updateSendButton === 'function') {
          updateSendButton(false);
        } else if (this.ui?.sendButton) {
          this.ui.sendButton.disabled = false;
        }
  
        // Show the sources button when typing is complete.
        this.toggleSourcesButton(true);
  
        if (this.hiddenElement) {
          document.body.removeChild(this.hiddenElement.pre);
          this.hiddenElement = null;
        }
  
        if (this.ui?.sendButton && this.stopHandler) {
          this.ui.sendButton.removeEventListener('click', this.stopHandler);
        }
        if (window.activeAnimator === this) {
          window.activeAnimator = null;
        }
      }
  
      /**
       * Main method that drives the typing animation.
       */
      async start() {
        const cleanupScroll = this.setupScrollListeners();
        this.handleCursor();
        this.updateDisplay();
        this.toggleSourcesButton(false);
  
        try {
          if (typeof updateSendButton === 'function') {
            updateSendButton(true);
          }
          // Update UI: hide the send icon and show the stop button.
          // Optionally wait before starting for non-code windows.
          if (!this.isCodeWindow) {
            await this.sleep(500);
          }
  
          // Type tokens one after another.
          while (await this.typeToken()) { /* loop until done */ }
  
          if (!this.state.isHidden && !this.state.isStopped) {
            this.cleanup();
          }
  
          if (this.onFinish) {
            this.onFinish();
          }
        } catch (error) {
          console.error('Error during typing animation:', error);
        } finally {
          cleanupScroll();
          document.removeEventListener('visibilitychange', this.visibilityHandler);
        }
      }
    }
  
    /**
     * Updates the sources button with favicons based on search results.
     * @param {Array} searchResults - Array of search result objects containing source_domain.
     */
    function updateSourcesButton(searchResults) {
      const sourcesBtn = document.getElementById("sources-btn");
      if (!sourcesBtn) {
        console.error("Sources button not found.");
        return;
      }

      let faviconContainer = document.getElementById("favicon-container");
      if (!faviconContainer) {
        faviconContainer = document.createElement("div");
        faviconContainer.id = "favicon-container";
        Object.assign(faviconContainer.style, {
          display: "inline-flex",
          gap: "8px"
        });
        sourcesBtn.appendChild(faviconContainer);
      }

      const extractDomain = (entry) => {
        if (!entry) return '';
        if (entry.source_domain) return entry.source_domain;
        if (entry.link) {
          try {
            const url = new URL(entry.link);
            return url.hostname;
          } catch (_) {
            return '';
          }
        }
        return '';
      };

      const domains = [
        ...new Set(
          (searchResults || [])
            .map(extractDomain)
            .filter(Boolean)
        ),
      ];

      faviconContainer.innerHTML = '';

      if (!domains.length) {
        sourcesBtn.dataset.hasSources = 'false';
        sourcesBtn.dataset.toggleVisible = 'false';
        return;
      }

      sourcesBtn.dataset.hasSources = 'true';

      domains.forEach((domain) => {
        const img = document.createElement('img');
        img.src = `https://www.google.com/s2/favicons?domain=${encodeURIComponent(domain)}&sz=32`;
        img.alt = domain;
        img.className = 'source-icon';
        img.onerror = () => img.remove();
        Object.assign(img.style, {
          width: "24px",
          height: "24px",
          borderRadius: "50%"
        });
        faviconContainer.appendChild(img);
      });
    }
  
    // Expose the functions globally.
    window.typeText = function (text, container, isCode, onFinish, options = {}) {
      const animator = new TypeTextAnimator(text, container, isCode, onFinish, options);
      return animator.start();
    };
  
    window.updateSourcesButton = updateSourcesButton;
  });
  
