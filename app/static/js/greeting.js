/**
 * Personalized Dynamic Greeting System
 * Displays time-aware, personalized greetings to users
 */

(function() {
    'use strict';

    // Greeting pools organized by time of day
    const greetings = {
        morning: [
            "Good morning, {name}",
            "Morning, {name}",
            "Coffee and Xela time, {name}?",
            "Hello, {name}"
        ],
        afternoon: [
            "Good afternoon, {name}",
            "Afternoon, {name}",
            "Back at it, {name}",
            "Hello, {name}"
        ],
        evening: [
            "Good evening, {name}",
            "Evening, {name}",
            "How was your day, {name}?",
            "Hello, {name}"
        ],
        night: [
            "Hello, night owl",
            "Still up, {name}?",
            "Late night thinking, {name}?",
            "Good evening, {name}"
        ],
        monday: [
            "Happy Monday, {name}",
            "New week, {name}"
        ],
        friday: [
            "Happy Friday, {name}",
            "Friday at last, {name}"
        ],
        weekend: [
            "Happy weekend, {name}",
            "Weekend mode, {name}"
        ],
        guest: [
            "How can I help you today?",
            "What's on your mind?",
            "Let's think it through together"
        ],
        incognito: [
            "Incognito chat. Nothing is saved.",
            "You're incognito. This chat won't be saved.",
            "Private chat. No history is kept."
        ]
    };

    /**
     * Get the time period based on current hour
     */
    function getTimePeriod(hour) {
        if (hour >= 5 && hour < 12) return 'morning';
        if (hour >= 12 && hour < 17) return 'afternoon';
        if (hour >= 17 && hour < 21) return 'evening';
        return 'night';
    }

    /**
     * Get special day type (Monday, Friday, Weekend)
     */
    function getDayType(dayOfWeek) {
        if (dayOfWeek === 1) return 'monday';
        if (dayOfWeek === 5) return 'friday';
        if (dayOfWeek === 0 || dayOfWeek === 6) return 'weekend';
        return null;
    }

    /**
     * Pick a random greeting from an array
     */
    function pickRandom(arr) {
        return arr[Math.floor(Math.random() * arr.length)];
    }

    /**
     * Get personalized greeting
     */
    function getGreeting(username, isGuest) {
        // Guest mode
        if (isGuest || !username) {
            return pickRandom(greetings.guest);
        }

        // Use only the first name
        const firstName = username.split(' ')[0];

        const now = new Date();
        const hour = now.getHours();
        const dayOfWeek = now.getDay();

        const timePeriod = getTimePeriod(hour);
        const dayType = getDayType(dayOfWeek);

        // 30% chance to use day-specific greeting if applicable
        if (dayType && Math.random() < 0.3) {
            return pickRandom(greetings[dayType]).replace('{name}', firstName);
        }

        // Otherwise use time-based greeting
        return pickRandom(greetings[timePeriod]).replace('{name}', firstName);
    }

    /**
     * Check if device is mobile
     */
    function isMobile() {
        return document.body.classList.contains('device-phone') ||
               document.body.classList.contains('viewport-mobile') ||
               document.body.classList.contains('is-mobile') ||
               document.body.classList.contains('is-ios') ||
               window.innerWidth <= 768;
    }

    /**
     * Typewriter animation - types text character by character with cursor
     */
    function typewriterEffect(element, text, speed = 25) {
        // Preserve any existing icons
        const existingIcon = element.querySelector('.time-icon');

        // Create a text span to hold the typed text
        let textSpan = element.querySelector('.greeting-typed-text');
        if (!textSpan) {
            textSpan = document.createElement('span');
            textSpan.className = 'greeting-typed-text';
            element.appendChild(textSpan);
        }
        textSpan.textContent = '';

        element.style.opacity = '1';
        element.classList.add('typewriter-active');
        let index = 0;

        function typeNext() {
            if (index < text.length) {
                textSpan.textContent += text.charAt(index);
                index++;
                // Slight variation for natural feel
                const nextDelay = speed + Math.random() * 15;
                setTimeout(typeNext, nextDelay);
            } else {
                // Typing complete - remove cursor immediately
                element.classList.remove('typewriter-active');
            }
        }

        // Start immediately
        typeNext();
    }

    /**
     * Create time-based icon (sun/moon)
     */
    function createTimeIcon() {
        const hour = new Date().getHours();
        const isDay = hour >= 6 && hour < 18;

        const icon = document.createElement('span');
        icon.className = `time-icon ${isDay ? 'sun' : 'moon'}`;

        if (isDay) {
            // Sun SVG
            icon.innerHTML = `<svg viewBox="0 0 24 24" fill="currentColor" width="28" height="28">
                <circle cx="12" cy="12" r="5"/>
                <line x1="12" y1="1" x2="12" y2="3" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
                <line x1="12" y1="21" x2="12" y2="23" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
                <line x1="4.22" y1="4.22" x2="5.64" y2="5.64" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
                <line x1="18.36" y1="18.36" x2="19.78" y2="19.78" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
                <line x1="1" y1="12" x2="3" y2="12" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
                <line x1="21" y1="12" x2="23" y2="12" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
                <line x1="4.22" y1="19.78" x2="5.64" y2="18.36" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
                <line x1="18.36" y1="5.64" x2="19.78" y2="4.22" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
            </svg>`;
        } else {
            // Moon SVG
            icon.innerHTML = `<svg viewBox="0 0 24 24" fill="currentColor" width="28" height="28">
                <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/>
            </svg>`;
        }

        return icon;
    }

    /**
     * Initialize greeting display
     */
    function initGreeting() {
        const container = document.getElementById('greeting-container');
        const textEl = document.getElementById('greeting-text');

        if (!container || !textEl) return;

        const username = container.dataset.username || '';
        const isGuest = container.dataset.isGuest === 'true';

        const greeting = getGreeting(username, isGuest);

        // Add time-based body class for styling
        const hour = new Date().getHours();
        const isDay = hour >= 6 && hour < 18;
        document.body.classList.remove('is-daytime', 'is-nighttime');
        document.body.classList.add(isDay ? 'is-daytime' : 'is-nighttime');

        // Same calm fade on every device (no typewriter)
        textEl.textContent = greeting;
        textEl.classList.add('greeting-fade-in');

        // Incognito label above the composer
        const tempLabel = document.querySelector('.temp-composer-label');
        if (tempLabel) {
            tempLabel.textContent = pickRandom(greetings.incognito);
        }

        // Add visible class for animation (only if server didn't hide it)
        const serverHidden = container.style.cssText.includes('!important');
        if (!serverHidden) {
            requestAnimationFrame(() => {
                container.classList.add('visible');
            });
        }

        // Set up visibility management
        setupVisibilityManagement(container);
    }

    /**
     * Manage greeting visibility based on chat messages
     */
    function setupVisibilityManagement(container) {
        const chatDisplay = document.getElementById('chat-display');
        if (!chatDisplay) return;

        // Check initial state
        updateVisibility(container, chatDisplay);

        // Watch for changes
        const observer = new MutationObserver(() => {
            updateVisibility(container, chatDisplay);
        });

        observer.observe(chatDisplay, { childList: true, subtree: true });

        // Listen for chat events
        document.addEventListener('chat-cleared', () => {
            container.classList.remove('hidden');
            container.classList.add('visible');
        });

        document.addEventListener('message-added', () => {
            container.classList.add('hidden');
            container.classList.remove('visible');
        });
    }

    /**
     * Update greeting visibility based on messages
     */
    function updateVisibility(container, chatDisplay) {
        // Count actual messages, excluding scroll-sentinel and other non-message elements
        let messageCount = 0;
        if (chatDisplay) {
            for (const child of chatDisplay.children) {
                // Skip scroll-sentinel and hidden elements
                if (child.id === 'scroll-sentinel') continue;
                if (child.classList.contains('message') || child.dataset.role) {
                    messageCount++;
                }
            }
        }
        const hasMessages = messageCount > 0;

        // Check if server hid the greeting (FOUC prevention for sessions with messages)
        const serverHidden = container.style.cssText.includes('!important');

        if (hasMessages || serverHidden) {
            container.classList.add('hidden');
            container.classList.remove('visible');
        } else {
            container.classList.remove('hidden');
            container.classList.add('visible');
        }
    }

    /**
     * Refresh greeting visibility (does NOT change the greeting text)
     * Greeting only changes on browser refresh
     */
    function refreshGreeting() {
        const container = document.getElementById('greeting-container');
        if (!container) return;

        // Clear server-side FOUC prevention style for new chat
        container.style.cssText = '';
        // Only manage visibility - never change the greeting text
        container.classList.remove('hidden');
        container.classList.add('visible');
    }

    // Initialize on DOM ready
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initGreeting);
    } else {
        initGreeting();
    }

    // Expose refresh function globally
    window.refreshGreeting = refreshGreeting;

})();
