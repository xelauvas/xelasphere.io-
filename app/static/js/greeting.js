/**
 * Personalized Dynamic Greeting System
 * Displays time-aware, personalized greetings to users
 */

(function() {
    'use strict';

    // Greeting pools organized by time of day
    const greetings = {
        morning: [
            "Good morning, {name}. What's on the agenda?",
            "Morning, {name}! Ready to dive in?",
            "Rise and shine, {name}. Where should we begin?",
            "Hey {name}, coffee's ready. What are we building?",
            "Good morning, {name}. Let's make today count.",
            "Morning, {name}. What's first on your list?",
            "Hey {name}, fresh start today. What's the plan?",
            "Good morning, {name}. Ready when you are.",
            "Morning, {name}! What can I help you with?",
            "Hey {name}, let's get things rolling."
        ],
        afternoon: [
            "Good afternoon, {name}. What are you working on?",
            "Hey {name}, how can I help this afternoon?",
            "Afternoon, {name}. Let's get things done.",
            "Good afternoon, {name}. What's next?",
            "Hey {name}, making progress?",
            "Afternoon, {name}. What's on your mind?",
            "Good afternoon, {name}. Ready to continue?",
            "Hey {name}, let's keep the momentum going.",
            "Afternoon, {name}. What can I do for you?",
            "Good afternoon, {name}. Where were we?"
        ],
        evening: [
            "Good evening, {name}. What's on your mind?",
            "Evening, {name}. Wrapping up or just getting started?",
            "{name}, still going strong. What's next?",
            "Good evening, {name}. How can I help?",
            "Evening, {name}. What are we working on?",
            "Hey {name}, winding down or pushing through?",
            "Good evening, {name}. Let's finish strong.",
            "Evening, {name}. What's left on the list?",
            "{name}, hope your day went well. What's up?",
            "Good evening, {name}. Ready when you are."
        ],
        night: [
            "Burning the midnight oil, {name}?",
            "Late night session, {name}? Let's do this.",
            "Night owl mode, {name}. What are we tackling?",
            "Hey {name}, quiet hours. What's on your mind?",
            "Still at it, {name}? How can I help?",
            "Late night, {name}. Let's make it productive.",
            "{name}, the night is young. What's the plan?",
            "Midnight thoughts, {name}? Let's explore.",
            "Hey {name}, burning bright tonight.",
            "Night shift, {name}. What are we building?"
        ],
        monday: [
            "Monday momentum, {name}! Let's crush it.",
            "New week, new possibilities, {name}.",
            "Happy Monday, {name}. Fresh start ahead.",
            "Monday's here, {name}. What's the game plan?",
            "Hey {name}, let's kick off the week right."
        ],
        friday: [
            "Happy Friday, {name}! Almost there.",
            "TGIF, {name}. What's the final push?",
            "Friday vibes, {name}. Let's finish strong.",
            "Hey {name}, weekend's calling. Last stretch!",
            "Friday, {name}! What's left to tackle?"
        ],
        weekend: [
            "Weekend vibes, {name}. What's the project?",
            "Relaxed weekend mode, {name}. How can I help?",
            "Hey {name}, weekend warrior. What's up?",
            "Weekend coding, {name}? Let's build something.",
            "{name}, taking it easy or going hard?"
        ],
        guest: [
            "Incognito mode. No history saved.",
            "You're incognito. Chat freely.",
            "Private session. Nothing stored.",
            "Incognito. Your chat stays private.",
            "Ghost mode. No traces left."
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

        // Mobile: typewriter (first sentence only)
        if (isMobile()) {
            const sentenceEnd = greeting.search(/[.!?]/);
            const mobileGreeting = sentenceEnd > -1 ? greeting.substring(0, sentenceEnd + 1) : greeting;
            typewriterEffect(textEl, mobileGreeting);
        } else {
            // Desktop: smooth fade in
            textEl.textContent = greeting;
            textEl.classList.add('greeting-fade-in');
        }

        // Set incognito label (always use guest/incognito greetings)
        const tempLabel = document.querySelector('.temp-composer-label');
        if (tempLabel) {
            tempLabel.textContent = pickRandom(greetings.guest);
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
