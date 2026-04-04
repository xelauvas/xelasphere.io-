/**
 * URL Preview Module - ChatGPT Style Rich Cards
 * Detects URLs in chat input and shows rich preview cards with metadata
 */

(function() {
    'use strict';

    // URL detection pattern
    const URL_PATTERN = /https?:\/\/(?:[-\w.]|(?:%[\da-fA-F]{2}))+[^\s<>"']*/gi;

    // Type icons and colors
    const URL_TYPES = {
        youtube: { icon: 'fab fa-youtube', color: '#ff0000', label: 'YouTube' },
        github: { icon: 'fab fa-github', color: '#6e5494', label: 'GitHub' },
        twitter: { icon: 'fab fa-twitter', color: '#1da1f2', label: 'Twitter' },
        pdf: { icon: 'fas fa-file-pdf', color: '#e74c3c', label: 'PDF' },
        arxiv: { icon: 'fas fa-graduation-cap', color: '#b31b1b', label: 'arXiv' },
        article: { icon: 'fas fa-globe', color: '#3498db', label: 'Web' }
    };

    let container = null;
    let debounceTimer = null;
    let currentUrls = [];
    let urlMetadataCache = {};
    let excludedUrls = new Set();

    /**
     * Get CSRF token
     */
    function getCsrfToken() {
        const meta = document.querySelector('meta[name="csrf-token"]');
        return meta ? meta.getAttribute('content') : '';
    }

    /**
     * Detect URLs in text
     */
    function detectUrls(text) {
        const matches = text.match(URL_PATTERN) || [];
        const cleaned = [...new Set(matches.map(url => url.replace(/[.,;:!?)\]}'"]$/, '')))];
        return cleaned.slice(0, 2); // Max 2 URLs
    }

    /**
     * Fetch URL metadata from backend
     */
    async function fetchMetadata(url) {
        // Check cache first
        if (urlMetadataCache[url]) {
            return urlMetadataCache[url];
        }

        try {
            const response = await fetch('/url-metadata', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': getCsrfToken()
                },
                body: JSON.stringify({ url })
            });

            if (response.ok) {
                const data = await response.json();
                urlMetadataCache[url] = data;
                return data;
            }
        } catch (e) {
            console.warn('Failed to fetch URL metadata:', e);
        }

        // Return basic metadata on failure
        const domain = new URL(url).hostname.replace('www.', '');
        return {
            url,
            domain,
            type: 'article',
            title: domain,
            favicon: `https://www.google.com/s2/favicons?domain=${domain}&sz=64`
        };
    }

    /**
     * Create the container element
     */
    function getContainer() {
        if (container) return container;

        container = document.createElement('div');
        container.className = 'url-preview-container';
        container.innerHTML = '<div class="url-preview-cards"></div>';

        // Insert before input-row
        const inputRow = document.querySelector('.input-row');
        const inputContainer = document.querySelector('.input-container');
        if (inputRow && inputContainer) {
            inputContainer.insertBefore(container, inputRow);
        }

        return container;
    }

    /**
     * Create a loading card
     */
    function createLoadingCard(url) {
        const domain = new URL(url).hostname.replace('www.', '');
        return `
            <div class="url-card loading" data-url="${url}">
                <div class="url-card-content">
                    <div class="url-card-icon">
                        <i class="fas fa-spinner fa-spin"></i>
                    </div>
                    <div class="url-card-info">
                        <div class="url-card-title skeleton"></div>
                        <div class="url-card-domain">${domain}</div>
                    </div>
                </div>
                <button class="url-card-remove" title="Don't read this link">
                    <i class="fas fa-times"></i>
                </button>
            </div>
        `;
    }

    /**
     * Create a rich preview card
     */
    function createRichCard(metadata) {
        const typeInfo = URL_TYPES[metadata.type] || URL_TYPES.article;
        const hasImage = metadata.image && metadata.type === 'youtube'; // Only show thumbnail for YouTube

        return `
            <div class="url-card" data-url="${metadata.url}">
                ${hasImage ? `
                    <a href="${metadata.url}" target="_blank" rel="noopener noreferrer" class="url-card-thumbnail" title="Open video">
                        <img src="${metadata.image}" alt="" loading="lazy" onerror="this.parentElement.style.display='none'">
                        <div class="url-card-play"><i class="fas fa-play"></i></div>
                    </a>
                ` : ''}
                <div class="url-card-content">
                    <div class="url-card-icon" style="color: ${typeInfo.color}">
                        <img src="${metadata.favicon}" alt="" onerror="this.outerHTML='<i class=\\'${typeInfo.icon}\\'></i>'">
                    </div>
                    <div class="url-card-info">
                        <div class="url-card-title" title="${metadata.title || metadata.domain}">${metadata.title || metadata.domain}</div>
                        ${metadata.description ? `<div class="url-card-desc">${metadata.description}</div>` : ''}
                        <div class="url-card-domain">
                            <i class="${typeInfo.icon}" style="color: ${typeInfo.color}"></i>
                            ${metadata.domain}
                        </div>
                    </div>
                </div>
                <button class="url-card-remove" title="Don't read this link">
                    <i class="fas fa-times"></i>
                </button>
            </div>
        `;
    }

    /**
     * Handle remove button clicks
     */
    function handleRemove(e) {
        const removeBtn = e.target.closest('.url-card-remove');
        if (!removeBtn) return;

        const card = removeBtn.closest('.url-card');
        if (!card) return;

        const url = card.dataset.url;
        excludedUrls.add(url);

        // Animate out
        card.style.transform = 'scale(0.8)';
        card.style.opacity = '0';
        setTimeout(() => {
            card.remove();
            // Check if any cards left
            const cardsContainer = container.querySelector('.url-preview-cards');
            if (cardsContainer && cardsContainer.children.length === 0) {
                hideContainer();
            }
        }, 200);
    }

    /**
     * Show the container with URL cards
     */
    async function showPreview(urls) {
        const el = getContainer();
        const cardsContainer = el.querySelector('.url-preview-cards');

        // Filter out excluded URLs
        const activeUrls = urls.filter(url => !excludedUrls.has(url));

        if (activeUrls.length === 0) {
            hideContainer();
            return;
        }

        // Show loading cards first
        cardsContainer.innerHTML = activeUrls.map(createLoadingCard).join('');
        el.classList.add('visible');

        // Add remove handlers (only once)
        if (!cardsContainer._removeListenerAdded) {
            cardsContainer.addEventListener('click', handleRemove);
            cardsContainer._removeListenerAdded = true;
        }

        // Fetch metadata for each URL
        for (const url of activeUrls) {
            const metadata = await fetchMetadata(url);
            const card = cardsContainer.querySelector(`[data-url="${url}"]`);
            if (card) {
                const newCard = document.createElement('div');
                newCard.innerHTML = createRichCard(metadata);
                const richCard = newCard.firstElementChild;
                card.replaceWith(richCard);
            }
        }
    }

    /**
     * Hide the container
     */
    function hideContainer() {
        if (container) {
            container.classList.remove('visible');
        }
    }

    /**
     * Update based on input
     */
    function updateUrlPreview(text) {
        // Check if guest mode
        if (document.body.dataset.guestMode === 'true') {
            hideContainer();
            return;
        }

        const urls = detectUrls(text);
        currentUrls = urls;

        if (urls.length > 0) {
            showPreview(urls);
        } else {
            hideContainer();
            excludedUrls.clear(); // Reset exclusions when no URLs
        }
    }

    /**
     * Debounced update
     */
    function debouncedUpdate(text) {
        clearTimeout(debounceTimer);
        debounceTimer = setTimeout(() => updateUrlPreview(text), 300);
    }

    /**
     * Get active URLs (not excluded)
     */
    function getActiveUrls() {
        return currentUrls.filter(url => !excludedUrls.has(url));
    }

    /**
     * Initialize
     */
    function init() {
        const promptInput = document.getElementById('prompt');
        if (!promptInput) return;

        // Listen for input changes
        promptInput.addEventListener('input', (e) => {
            debouncedUpdate(e.target.value);
        });

        // Hide on send
        const sendButton = document.getElementById('send-button');
        if (sendButton) {
            sendButton.addEventListener('click', () => {
                setTimeout(() => {
                    hideContainer();
                    excludedUrls.clear();
                }, 100);
            });
        }

        // Hide on Enter
        promptInput.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
                setTimeout(() => {
                    hideContainer();
                    excludedUrls.clear();
                }, 100);
            }
        });

        // Check initial value
        if (promptInput.value) {
            updateUrlPreview(promptInput.value);
        }

        console.log('[UrlPreview] ChatGPT-style URL preview initialized');
    }

    // Initialize when DOM is ready
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }

    // Expose API
    window.UrlPreview = {
        detect: detectUrls,
        update: updateUrlPreview,
        hide: hideContainer,
        getActiveUrls: getActiveUrls
    };

})();
