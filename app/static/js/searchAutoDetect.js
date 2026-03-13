/**
 * Search Auto-Detection Module
 * Automatically detects when web search would benefit the user's query
 * Mirrors the backend logic in views.py should_auto_enable_search()
 */

(function() {
    'use strict';

    // === Pattern definitions (mirrors backend) ===

    // HIGH CONFIDENCE: Definitely needs search
    const explicitSearch = [
        /\bsearch\b/i, /\blook\s*up\b/i, /\bfind\s+online\b/i,
        /\bweb\s*search\b/i, /\bgoogle\b/i, /\bbing\b/i,
        /\bsearch\s+(for|the|about)\b/i,
    ];

    const timeSensitive = [
        /\btoday\b/i, /\btonight\b/i, /\bright\s+now\b/i,
        /\bcurrently\b/i, /\bcurrent(ly)?\b/i, /\blatest\b/i,
        /\brecent(ly)?\b/i, /\bjust\s+(happened|announced|released)\b/i,
        /\bthis\s+(week|month|year|morning|afternoon|evening)\b/i,
        /\byesterday\b/i, /\blast\s+(week|month|night|hour)\b/i,
        /\bbreaking\b/i, /\blive\b/i, /\bupdate[sd]?\b/i,
        /\bnow\s+playing\b/i, /\bhappening\s+now\b/i,
    ];

    const realtimeTopics = [
        // Weather
        /\bweather\b/i, /\bforecast\b/i, /\btemperature\b/i,
        /\brain(ing|y)?\b/i, /\bsnow(ing|y)?\b/i, /\bstorm\b/i,
        // Finance
        /\bstock\s*(price|market|ticker)?\b/i, /\bshare\s*price\b/i,
        /\bmarket\s*(cap|value|price)\b/i, /\btrading\b/i,
        /\bexchange\s+rate\b/i, /\bcrypto(currency)?\b/i,
        /\bbitcoin\b/i, /\bethereum\b/i, /\b(USD|EUR|GBP)\s*to\b/i,
        // News & Events
        /\bnews\b/i, /\bheadlines?\b/i, /\bannouncement\b/i,
        /\belection\b/i, /\bvot(e|ing|ed)\b/i, /\bpoll(s|ing)?\b/i,
        // Sports
        /\bscore[sd]?\b/i, /\bgame\s+result\b/i, /\bwho\s+won\b/i,
        /\bmatch\s+result\b/i, /\bstandings?\b/i, /\bleague\s+table\b/i,
        /\bplayoff\b/i, /\bchampionship\b/i, /\btournament\b/i,
        // Trending/Viral
        /\btrending\b/i, /\bviral\b/i, /\bpopular\s+right\s+now\b/i,
    ];

    // MEDIUM CONFIDENCE: Likely needs search
    const mediumPatterns = [
        /\bwhat('s|\s+is)\s+happening\b/i,
        /\bwhat('s|\s+is)\s+going\s+on\b/i,
        /\bwhat('s|\s+is)\s+the\s+(status|state)\b/i,
        /\bis\s+.+\s+(open|closed|available|working)\b/i,
        /\bwhen\s+(is|does|will|did)\b/i,
        /\bwhere\s+can\s+i\s+(buy|find|get|watch)\b/i,
        /\bhow\s+much\s+(does|is|are|do)\b/i,
        /\bprice\s+(of|for)\b/i, /\bcost\s+(of|for)\b/i,
        /\bavailab(le|ility)\b/i,
        /\bwho\s+is\s+the\s+(current|new)?\s*(ceo|president|cto|leader|head|director)\b/i,
        /\bwho\s+(runs|leads|owns)\b/i,
        /\blatest\s+(version|release|update|model)\b/i,
        /\bnew(est)?\s+(iphone|android|mac|windows|ios|version)\b/i,
        /\brelease\s+date\b/i,
        /\bnear\s+me\b/i, /\bin\s+my\s+area\b/i,
        /\brestaurants?\s+in\b/i, /\bhotels?\s+in\b/i,
        /\b202[4-9]\b/, /\b203[0-5]\b/,
    ];

    // NO SEARCH indicators
    const noSearchPatterns = [
        // Creative/generative
        /\bwrite\s+(a|me|an|the)\b/i, /\bgenerate\b/i, /\bcreate\b/i,
        /\bcompose\b/i, /\bdraft\b/i,
        // Coding
        /\bcode\b/i, /\bprogram\b/i, /\bscript\b/i, /\bfunction\b/i,
        /\bdebug\b/i, /\bfix\s+(this|the|my)\b/i, /\brefactor\b/i,
        /\bimplement\b/i, /\bbuild\s+(a|an|the)\b/i,
        // Explanation
        /\bexplain\b/i, /\bwhat\s+does\s+.+\s+mean\b/i,
        /\bhow\s+(do|does|to)\s+.+\s+work\b/i,
        /\bteach\s+me\b/i, /\bhelp\s+me\s+understand\b/i,
        // Translation/conversion
        /\btranslate\b/i, /\bconvert\b/i,
        // Math/logic
        /\bcalculate\b/i, /\bsolve\b/i, /\bcompute\b/i,
        // Summarization
        /\bsummarize\b/i, /\bsummary\b/i, /\btl;?dr\b/i,
        // Analysis
        /\banalyze\s+(this|the)\b/i, /\breview\s+(this|the|my)\b/i,
    ];

    /**
     * Detect if web search would benefit the query
     * @param {string} prompt - User's input prompt
     * @returns {{shouldSearch: boolean, confidence: string, reason: string}}
     */
    function detectSearchNeeded(prompt) {
        if (!prompt || typeof prompt !== 'string') {
            return { shouldSearch: false, confidence: 'low', reason: null };
        }

        const text = prompt.trim();
        if (text.length < 3) {
            return { shouldSearch: false, confidence: 'low', reason: null };
        }

        // Check high confidence patterns
        const highPatterns = [...explicitSearch, ...timeSensitive, ...realtimeTopics];
        for (const pattern of highPatterns) {
            if (pattern.test(text)) {
                const reason = getReasonFromPattern(pattern, text);
                return { shouldSearch: true, confidence: 'high', reason };
            }
        }

        // Check medium confidence patterns
        for (const pattern of mediumPatterns) {
            if (pattern.test(text)) {
                const reason = getReasonFromPattern(pattern, text);
                return { shouldSearch: true, confidence: 'medium', reason };
            }
        }

        // Check no-search patterns
        for (const pattern of noSearchPatterns) {
            if (pattern.test(text)) {
                return { shouldSearch: false, confidence: 'high', reason: null };
            }
        }

        return { shouldSearch: false, confidence: 'low', reason: null };
    }

    /**
     * Get a human-readable reason for why search was triggered
     */
    function getReasonFromPattern(pattern, text) {
        const match = text.match(pattern);
        if (!match) return 'Real-time info';

        const keyword = match[0].toLowerCase();

        // Map keywords to friendly reasons
        const reasons = {
            'weather': 'Weather query',
            'forecast': 'Weather query',
            'temperature': 'Weather query',
            'news': 'News query',
            'headlines': 'News query',
            'stock': 'Finance query',
            'price': 'Price lookup',
            'bitcoin': 'Crypto query',
            'ethereum': 'Crypto query',
            'crypto': 'Crypto query',
            'score': 'Sports query',
            'today': 'Current info',
            'current': 'Current info',
            'latest': 'Latest info',
            'recent': 'Recent info',
            'trending': 'Trending topics',
            'search': 'Search request',
            'google': 'Search request',
            'election': 'Current events',
            'vote': 'Current events',
        };

        for (const [key, reason] of Object.entries(reasons)) {
            if (keyword.includes(key)) {
                return reason;
            }
        }

        return 'Real-time info';
    }

    // === UI Elements ===

    let searchIndicator = null;
    let isIndicatorVisible = false;
    let userOverride = null; // null = auto, true = force on, false = force off

    /**
     * Create the search indicator element
     */
    function createSearchIndicator() {
        if (searchIndicator) return searchIndicator;

        const indicator = document.createElement('div');
        indicator.className = 'search-auto-indicator';
        indicator.innerHTML = `
            <div class="search-auto-indicator-content">
                <i class="fas fa-globe"></i>
                <span class="search-auto-indicator-text">Web search</span>
                <button class="search-auto-indicator-toggle" title="Click to toggle">
                    <i class="fas fa-times"></i>
                </button>
            </div>
        `;

        // Toggle button click handler
        indicator.querySelector('.search-auto-indicator-toggle').addEventListener('click', (e) => {
            e.preventDefault();
            e.stopPropagation();
            toggleSearchOverride();
        });

        // Insert before the input-row
        const inputRow = document.querySelector('.input-row');
        const inputContainer = document.querySelector('.input-container');
        if (inputRow && inputContainer) {
            inputContainer.insertBefore(indicator, inputRow);
        }

        searchIndicator = indicator;
        return indicator;
    }

    /**
     * Show the search indicator
     */
    function showIndicator(reason = 'Web search') {
        const indicator = createSearchIndicator();
        const textEl = indicator.querySelector('.search-auto-indicator-text');
        const toggleIcon = indicator.querySelector('.search-auto-indicator-toggle i');

        textEl.textContent = reason;
        toggleIcon.className = 'fas fa-times';
        indicator.classList.add('visible');
        indicator.classList.remove('disabled');
        isIndicatorVisible = true;

        // Update the hidden checkbox to enable search
        const searchCheckbox = document.getElementById('enable-search');
        if (searchCheckbox && userOverride !== false) {
            searchCheckbox.checked = true;
        }
    }

    /**
     * Hide the search indicator
     */
    function hideIndicator() {
        if (searchIndicator) {
            searchIndicator.classList.remove('visible');
            isIndicatorVisible = false;
        }

        // Reset the checkbox if we're in auto mode
        if (userOverride === null) {
            const searchCheckbox = document.getElementById('enable-search');
            if (searchCheckbox) {
                searchCheckbox.checked = false;
            }
        }
    }

    /**
     * Show indicator in disabled/override state
     */
    function showDisabledIndicator() {
        const indicator = createSearchIndicator();
        const textEl = indicator.querySelector('.search-auto-indicator-text');
        const toggleIcon = indicator.querySelector('.search-auto-indicator-toggle i');

        textEl.textContent = 'Search off';
        toggleIcon.className = 'fas fa-undo';
        indicator.classList.add('visible', 'disabled');
        isIndicatorVisible = true;

        const searchCheckbox = document.getElementById('enable-search');
        if (searchCheckbox) {
            searchCheckbox.checked = false;
        }
    }

    /**
     * Toggle between auto/force-off states
     */
    function toggleSearchOverride() {
        if (userOverride === false) {
            // Was forced off, go back to auto
            userOverride = null;
            // Re-evaluate current prompt
            const promptInput = document.getElementById('prompt');
            if (promptInput) {
                updateSearchIndicator(promptInput.value);
            }
        } else {
            // Force off
            userOverride = false;
            showDisabledIndicator();
        }
    }

    /**
     * Update the search indicator based on prompt content
     */
    function updateSearchIndicator(prompt) {
        // If user has forced search off, show disabled state
        if (userOverride === false) {
            showDisabledIndicator();
            return;
        }

        // Check if guest mode (no search for guests)
        if (document.body.dataset.guestMode === 'true') {
            hideIndicator();
            return;
        }

        const result = detectSearchNeeded(prompt);

        if (result.shouldSearch) {
            showIndicator(result.reason || 'Web search');
        } else {
            hideIndicator();
        }
    }

    /**
     * Reset override when message is sent
     */
    function resetOverride() {
        userOverride = null;
        hideIndicator();
    }

    // === Debounce helper ===
    function debounce(fn, delay) {
        let timeout;
        return function(...args) {
            clearTimeout(timeout);
            timeout = setTimeout(() => fn.apply(this, args), delay);
        };
    }

    // === Initialize ===
    function init() {
        const promptInput = document.getElementById('prompt');
        if (!promptInput) return;

        // Create indicator element
        createSearchIndicator();

        // Listen for input changes with debounce
        const debouncedUpdate = debounce((e) => {
            updateSearchIndicator(e.target.value);
        }, 200);

        promptInput.addEventListener('input', debouncedUpdate);

        // Reset on form submit / message send
        const sendButton = document.getElementById('send-button');
        if (sendButton) {
            sendButton.addEventListener('click', () => {
                // Small delay to let the message send first
                setTimeout(resetOverride, 100);
            });
        }

        // Reset on Enter key
        promptInput.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
                setTimeout(resetOverride, 100);
            }
        });

        // Check initial value
        if (promptInput.value) {
            updateSearchIndicator(promptInput.value);
        }
    }

    // Initialize when DOM is ready
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }

    // Expose API
    window.SearchAutoDetect = {
        detect: detectSearchNeeded,
        update: updateSearchIndicator,
        reset: resetOverride,
        isEnabled: () => {
            const checkbox = document.getElementById('enable-search');
            return checkbox ? checkbox.checked : false;
        }
    };

})();
