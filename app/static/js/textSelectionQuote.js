/**
 * Text Selection Quote Feature
 * Allows users to select text in AI responses and quote it to the chat input
 * Shows visual quote indicator above textarea, keeps textarea clean for typing
 */

(function() {
    'use strict';

    let floatingButton = null;
    let currentSelection = null;
    let quoteIndicator = null;
    let quotedText = null;

    // Mobile detection
    const isTouchDevice = 'ontouchstart' in window || navigator.maxTouchPoints > 0;
    const isMobile = () => {
        return document.body.classList.contains('device-phone') ||
               document.body.classList.contains('is-mobile') ||
               document.body.classList.contains('is-ios') ||
               window.innerWidth <= 768;
    };

    // Create the floating "Ask about this" button
    function createFloatingButton() {
        if (floatingButton) return floatingButton;

        const btn = document.createElement('button');
        btn.className = 'text-selection-ask-btn';
        btn.innerHTML = `
            <i class="fas fa-reply"></i>
            <span>Ask about this</span>
        `;
        btn.style.display = 'none';
        document.body.appendChild(btn);

        btn.addEventListener('click', handleAskClick);
        btn.addEventListener('mousedown', (e) => e.preventDefault());

        // Mobile touch support
        if (isTouchDevice) {
            btn.addEventListener('touchstart', (e) => {
                e.preventDefault();
                e.stopPropagation();
            }, { passive: false });

            btn.addEventListener('touchend', (e) => {
                e.preventDefault();
                e.stopPropagation();
                handleAskClick(e);
            }, { passive: false });
        }

        floatingButton = btn;
        return btn;
    }

    // Check if element is within an AI response message or email composer
    function isWithinAIResponse(element) {
        if (!element) return false;

        let current = element;
        while (current && current !== document.body) {
            if (current.classList && (
                current.classList.contains('ai') ||
                current.classList.contains('assistant') ||
                current.classList.contains('ai-message') ||
                current.classList.contains('assistant-message') ||
                current.classList.contains('bot-message') ||
                current.classList.contains('email-composer-card') ||
                current.dataset?.role === 'assistant' ||
                current.dataset?.sender === 'ai'
            )) {
                return true;
            }
            if (current.classList && current.classList.contains('message-content')) {
                const parent = current.closest('.chat-message, .message, [data-role]');
                if (parent && (
                    parent.classList.contains('ai') ||
                    parent.classList.contains('assistant') ||
                    parent.classList.contains('ai-message') ||
                    parent.classList.contains('assistant-message') ||
                    parent.dataset?.role === 'assistant'
                )) {
                    return true;
                }
            }
            current = current.parentElement;
        }
        return false;
    }

    // Position the floating button relative to the selection
    // On mobile: BELOW selection (native menu appears above)
    // On desktop: ABOVE selection
    function positionButton(selection) {
        if (!floatingButton || !selection.rangeCount) return;

        const range = selection.getRangeAt(0);
        const rect = range.getBoundingClientRect();

        // Check if rect is valid (not 0,0 which happens in input/textarea)
        if (rect.width === 0 && rect.height === 0) {
            hideButton();
            return;
        }

        // Larger button on mobile
        const btnWidth = isMobile() ? 160 : 140;
        const btnHeight = isMobile() ? 44 : 34;

        // Center horizontally
        let left = rect.left + (rect.width / 2) - (btnWidth / 2);

        // On mobile, position BELOW selection to avoid native context menu
        // On desktop, position ABOVE selection
        let top;
        if (isTouchDevice || isMobile()) {
            top = rect.bottom + 12; // Below selection with gap
        } else {
            top = rect.top - btnHeight - 8; // Above selection
        }

        const viewportWidth = window.innerWidth;
        const viewportHeight = window.innerHeight;

        // Keep within viewport horizontally
        if (left < 10) left = 10;
        if (left + btnWidth > viewportWidth - 10) {
            left = viewportWidth - btnWidth - 10;
        }

        // Keep within viewport vertically
        if (top + btnHeight > viewportHeight - 10) {
            // If below doesn't fit, try above
            top = rect.top - btnHeight - 8;
        }
        if (top < 10) {
            // If above doesn't fit, position below
            top = rect.bottom + 12;
        }

        floatingButton.style.left = `${left}px`;
        floatingButton.style.top = `${top}px`;
    }

    // Handle text selection
    function handleSelection() {
        const activeEl = document.activeElement;

        // Handle selection inside input/textarea (skip email composer - handled by EmailEditToolbar)
        if (activeEl && (activeEl.tagName === 'INPUT' || activeEl.tagName === 'TEXTAREA')) {
            // Skip if in email composer
            if (activeEl.closest('.email-composer-card')) {
                hideButton();
                return;
            }

            const start = activeEl.selectionStart;
            const end = activeEl.selectionEnd;

            if (start !== end) {
                const selectedText = activeEl.value.substring(start, end).trim();

                if (selectedText.length >= 3 && isWithinAIResponse(activeEl)) {
                    currentSelection = selectedText;
                    showButtonForInput(activeEl);
                    return;
                }
            }
            hideButton();
            return;
        }

        // Handle regular DOM selection
        const selection = window.getSelection();
        const selectedText = selection?.toString().trim();

        if (!selectedText || selectedText.length < 3) {
            hideButton();
            return;
        }

        const anchorNode = selection.anchorNode;
        const focusNode = selection.focusNode;

        // Skip if selection is inside email composer (email toolbar handles this)
        const anchorInEmail = anchorNode?.parentElement?.closest('.email-composer-card, .email-body[contenteditable]');
        const focusInEmail = focusNode?.parentElement?.closest('.email-composer-card, .email-body[contenteditable]');
        if (anchorInEmail || focusInEmail) {
            hideButton();
            return;
        }

        if (!isWithinAIResponse(anchorNode) && !isWithinAIResponse(focusNode)) {
            hideButton();
            return;
        }

        currentSelection = selectedText;
        showButton(selection);
    }

    // Show button above input/textarea element
    function showButtonForInput(inputEl) {
        if (!floatingButton) createFloatingButton();

        const rect = inputEl.getBoundingClientRect();
        const btnWidth = 140;
        const btnHeight = 34;

        // Position above the input element, centered
        let left = rect.left + (rect.width / 2) - (btnWidth / 2);
        let top = rect.top - btnHeight - 8;

        const viewportWidth = window.innerWidth;

        // Keep within viewport
        if (left < 10) left = 10;
        if (left + btnWidth > viewportWidth - 10) {
            left = viewportWidth - btnWidth - 10;
        }
        if (top < 10) {
            top = rect.bottom + 8;
        }

        floatingButton.style.left = `${left}px`;
        floatingButton.style.top = `${top}px`;
        floatingButton.style.display = 'flex';
        floatingButton.offsetHeight;
        floatingButton.classList.add('visible');
    }

    function showButton(selection) {
        if (!floatingButton) createFloatingButton();
        positionButton(selection);
        floatingButton.style.display = 'flex';
        floatingButton.offsetHeight;
        floatingButton.classList.add('visible');
    }

    function hideButton() {
        if (floatingButton) {
            floatingButton.classList.remove('visible');
            setTimeout(() => {
                if (!floatingButton.classList.contains('visible')) {
                    floatingButton.style.display = 'none';
                }
            }, 200);
        }
        currentSelection = null;
    }

    // Handle click on "Ask about this"
    function handleAskClick(e) {
        e.preventDefault();
        e.stopPropagation();

        if (!currentSelection) {
            hideButton();
            return;
        }

        showQuoteIndicator(currentSelection);
        window.getSelection()?.removeAllRanges();
        hideButton();
    }

    // Create and show the quote indicator above input area
    function showQuoteIndicator(text) {
        removeQuoteIndicator();

        const textarea = document.querySelector('#prompt, #chatInput, .chat-input');
        if (!textarea) return;

        // Truncate long quotes
        const maxLength = 300;
        const truncated = text.length > maxLength
            ? text.substring(0, maxLength) + '...'
            : text;

        quotedText = truncated;

        // Create indicator
        const indicator = document.createElement('div');
        indicator.className = 'quote-reply-indicator';
        indicator.innerHTML = `
            <div class="quote-reply-bar"></div>
            <div class="quote-reply-content">
                <span class="quote-reply-label">Replying to</span>
                <p class="quote-reply-text">${escapeHtml(truncated)}</p>
            </div>
            <button class="quote-reply-close" title="Cancel reply">
                <i class="fas fa-times"></i>
            </button>
        `;

        // Close button handler
        indicator.querySelector('.quote-reply-close').addEventListener('click', (e) => {
            e.preventDefault();
            e.stopPropagation();
            removeQuoteIndicator();
            textarea.focus();
        });

        // Insert location depends on device
        const inputRow = document.querySelector('.input-row');
        const textareaContainer = document.querySelector('.input-row > .textarea-container');
        const inputContainer = document.querySelector('.input-container');

        // Check if mobile
        const isMobileDevice = document.body.classList.contains('device-phone') ||
                              document.body.classList.contains('is-mobile') ||
                              document.body.classList.contains('is-ios') ||
                              window.innerWidth <= 768;

        if (isMobileDevice && inputRow && textareaContainer) {
            // Mobile: Insert inside input-row, before the pill - ensures exact alignment
            inputRow.insertBefore(indicator, textareaContainer);
        } else if (inputRow && inputRow.parentElement) {
            // Desktop: Insert before input-row
            inputRow.parentElement.insertBefore(indicator, inputRow);
        } else if (inputContainer) {
            // Fallback: append to input container
            inputContainer.insertBefore(indicator, inputContainer.querySelector('.input-row, .textarea-container'));
        } else {
            // Last fallback
            textarea.parentElement.insertBefore(indicator, textarea);
        }

        quoteIndicator = indicator;

        // Focus textarea
        textarea.focus();
    }

    // Remove quote indicator
    function removeQuoteIndicator() {
        if (quoteIndicator) {
            quoteIndicator.remove();
            quoteIndicator = null;
        }
        document.querySelectorAll('.quote-reply-indicator').forEach(el => el.remove());
        quotedText = null;
    }

    // Escape HTML
    function escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }

    // Get the quoted text for submission
    function getQuotedText() {
        return quotedText;
    }

    // Format message with quote for submission
    function formatMessageWithQuote(userMessage) {
        if (!quotedText) return userMessage;
        return `> "${quotedText}"\n\n${userMessage}`;
    }

    // Clear quote after submission
    function clearQuote() {
        removeQuoteIndicator();
    }

    // Check if quote is active
    function hasActiveQuote() {
        return !!quotedText;
    }

    // Initialize
    function init() {
        createFloatingButton();

        // On mobile, use longer delay so native menu (copy/search) appears first
        // Our button appears BELOW the selection, native menu appears ABOVE
        const selectionDelay = isTouchDevice ? 400 : 150;

        let selectionTimeout;
        document.addEventListener('selectionchange', () => {
            clearTimeout(selectionTimeout);
            selectionTimeout = setTimeout(handleSelection, selectionDelay);
        });

        document.addEventListener('click', (e) => {
            if (floatingButton && !floatingButton.contains(e.target)) {
                setTimeout(() => {
                    if (!window.getSelection()?.toString().trim()) {
                        hideButton();
                    }
                }, 100);
            }
        });

        // On mobile, don't hide immediately on scroll - selection often causes minor scroll
        // On desktop, hide immediately for cleaner UX
        if (isTouchDevice) {
            let scrollTimeout;
            document.addEventListener('scroll', () => {
                clearTimeout(scrollTimeout);
                scrollTimeout = setTimeout(() => {
                    if (!window.getSelection()?.toString().trim()) {
                        hideButton();
                    }
                }, 300);
            }, true);
        } else {
            document.addEventListener('scroll', hideButton, true);
        }

        window.addEventListener('resize', () => {
            if (floatingButton && floatingButton.style.display !== 'none') {
                const selection = window.getSelection();
                if (selection && selection.toString().trim()) {
                    positionButton(selection);
                } else {
                    hideButton();
                }
            }
        });

        // Escape to cancel reply
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape' && hasActiveQuote()) {
                removeQuoteIndicator();
            }
        });

        console.log('Text Selection Quote feature initialized');
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }

    // API
    window.TextSelectionQuote = {
        getQuotedText,
        formatMessageWithQuote,
        clearQuote,
        hasActiveQuote
    };

})();
