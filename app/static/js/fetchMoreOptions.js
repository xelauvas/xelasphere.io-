(() => {
    // Constants for configuration
    const CONFIG = {
        ENDPOINTS: {
            MORE_OPTIONS: '/more-options'
        },
        SELECTORS: {
            CSRF_META: 'meta[name="csrf-token"]',
            LOADING_INDICATOR: '#loading-indicator',
            ERROR_MESSAGE: '#error-message',
            OPTIONS_CONTAINER: '.options',
            MORE_BUTTON: '.more-button',
            OPTIONS_BUTTON: '.options-button',
            BUTTON_TEXT: '.button-text',
            LOADING_SPINNER: '.loading-spinner'
        },
        CLASSES: {
            LOADING: 'loading',
            FADE_IN_NEW: 'fade-in-new',
            MORE_BUTTON: 'more-button',
            OPTIONS_BUTTON: 'options-button',
            MODERN_OPTION: 'modern-option'
        },
        ANIMATION: {
            STAGGER_DELAY: 50,
            DEFAULT_DURATION: 300
        },
        TEXT: {
            LOADING: 'Loading',
            MORE: 'More',
            ERROR_PREFIX: 'Error: '
        }
    };

    // Guest mode demo responses - shown instead of calling API
    const GUEST_DEMO_RESPONSES = {
        'Quick Recipe Ideas': `Here are some delicious 30-minute meals!

**1. Garlic Butter Shrimp Pasta** (25 mins)
- Cook pasta, sauté shrimp in garlic butter, toss together with parsley

**2. Sheet Pan Chicken Fajitas** (30 mins)
- Slice peppers & onions, season chicken, roast at 400°F for 20 mins

**3. 15-Minute Fried Rice**
- Use day-old rice, scrambled eggs, frozen veggies, soy sauce

**Quick Tips:**
- Prep ingredients before cooking
- Keep a stocked pantry with basics
- Frozen veggies are your friend!

*Sign up for personalized recipes based on what's in your fridge!*`,

        'Plan My Week': `Let's organize your week for success!

**Weekly Planning Template:**

**Monday:** Start strong - tackle your biggest task first
**Tuesday:** Follow-up day - check in on ongoing projects
**Wednesday:** Mid-week review - adjust priorities if needed
**Thursday:** Creative work - schedule deep focus time
**Friday:** Wrap up - finish tasks, plan next week

**Pro Tips:**
- Block 2-3 hours for focused work daily
- Schedule breaks - they boost productivity
- Review your week every Sunday evening
- Use the "2-minute rule" - if it takes less, do it now

*Create a free account to get AI-powered weekly planning and reminders!*`,

        'Workout Routine': `Here's a balanced workout plan for you!

**Quick 20-Minute Full Body Workout:**

**Warm-up (3 mins):** Jumping jacks, arm circles, leg swings

**Circuit (repeat 2x):**
- 10 Squats
- 10 Push-ups (or knee push-ups)
- 10 Lunges (each leg)
- 30-sec Plank
- 10 Burpees

**Cool-down (2 mins):** Stretch major muscle groups

**Weekly Split Suggestion:**
- Mon/Thu: Upper body
- Tue/Fri: Lower body
- Wed/Sat: Cardio or active recovery
- Sun: Rest

*Sign up for customized workout plans based on your fitness goals!*`,

        'Budget Helper': `Let's get your finances on track!

**The 50/30/20 Rule:**
- **50%** Needs (rent, groceries, utilities)
- **30%** Wants (dining out, entertainment)
- **20%** Savings & debt payoff

**Quick Money-Saving Tips:**
1. Track every expense for one week
2. Cancel unused subscriptions
3. Use the 24-hour rule for impulse buys
4. Automate your savings
5. Meal prep to reduce food costs

**Monthly Budget Checklist:**
- [ ] Review last month's spending
- [ ] Set category limits
- [ ] Plan for upcoming expenses
- [ ] Check subscription renewals

*Create an account for personalized budgeting advice and expense tracking!*`,

        'Travel Planner': `Let's plan your perfect trip!

**Trip Planning Checklist:**

**8 Weeks Before:**
- Choose destination and dates
- Set budget
- Book flights and accommodation

**4 Weeks Before:**
- Research activities and restaurants
- Check visa/passport requirements
- Book popular attractions

**1 Week Before:**
- Confirm all reservations
- Download offline maps
- Notify bank of travel plans
- Pack using a checklist

**Packing Essentials:**
- Travel documents
- Chargers and adapters
- Medications
- Comfortable walking shoes

*Sign up for personalized itineraries and travel recommendations!*`,

        'Gift Ideas': `Finding the perfect gift made easy!

**By Personality:**

**For the Foodie:** Cooking class, artisan food basket, restaurant gift card

**For the Bookworm:** E-reader, book subscription, cozy reading accessories

**For the Adventurer:** Experience gift, travel accessories, outdoor gear

**For the Homebody:** Candles, cozy blankets, streaming subscription

**Budget-Friendly Ideas:**
- Handwritten letter with photos
- Homemade treats or crafts
- Curated playlist
- "Coupon book" of favors

**Gift-Giving Tips:**
- Listen for hints year-round
- Experiences often beat things
- Presentation matters!

*Create an account for personalized gift suggestions based on the recipient!*`,

        'Movie Night': `Here are tonight's top picks!

**For a Cozy Night:**
- *The Grand Budapest Hotel* - Quirky & charming
- *Amélie* - Whimsical French delight

**For Thrills:**
- *Knives Out* - Clever mystery
- *Parasite* - Mind-bending thriller

**For Laughs:**
- *The Hangover* - Classic comedy
- *Bridesmaids* - Hilarious & heartfelt

**For the Whole Family:**
- *Coco* - Beautiful animation
- *The Incredibles* - Action-packed fun

**Movie Night Essentials:**
- Popcorn (extra butter!)
- Cozy blankets
- Phones on silent
- Snacks variety

*Sign up for personalized recommendations based on your taste!*`,

        'Home Organization': `Let's declutter and organize!

**The 4-Box Method:**
Label boxes: Keep, Donate, Trash, Relocate

**Room-by-Room Quick Wins:**

**Kitchen:** Clear counters, organize one drawer
**Bedroom:** Make bed daily, declutter nightstand
**Bathroom:** Toss expired products, organize under sink
**Living Room:** Designate spots for remotes, clear surfaces

**10-Minute Daily Habits:**
1. Make your bed
2. Do dishes immediately
3. Put things back after use
4. Sort mail daily
5. Quick evening tidy-up

**The One-In-One-Out Rule:**
For every new item, remove one old item.

*Create an account for room-specific organization plans!*`,

        'Learn Something New': `Discover your next skill or hobby!

**Quick-Start Ideas:**

**Creative:** Drawing, photography, playing ukulele, journaling

**Practical:** Basic cooking, home repairs, personal finance, first aid

**Digital:** Video editing, graphic design, coding basics, social media

**Physical:** Yoga, dancing, rock climbing, martial arts

**How to Start:**
1. Pick ONE thing that excites you
2. Find a free beginner tutorial online
3. Commit to 15 minutes daily for 2 weeks
4. Join a community of learners
5. Celebrate small wins!

**The 20-Hour Rule:**
You can learn the basics of almost anything in just 20 focused hours.

*Sign up for personalized learning paths and skill recommendations!*`,

        'Stress Relief': `Quick techniques to feel calmer now!

**Instant Calm (2 minutes):**
- **Box Breathing:** Inhale 4 sec, hold 4 sec, exhale 4 sec, hold 4 sec
- Repeat 4 times

**5-Minute Reset:**
1. Step away from screens
2. Stretch your neck and shoulders
3. Take 10 deep breaths
4. Think of 3 things you're grateful for
5. Drink a glass of water

**Daily Stress-Busters:**
- Morning: 5-min meditation or stretching
- Afternoon: Short walk outside
- Evening: No screens 1 hour before bed

**Long-term Habits:**
- Regular exercise
- Quality sleep (7-9 hours)
- Connect with loved ones
- Limit caffeine and alcohol

*Create an account for guided relaxation sessions and stress tracking!*`,

        'Party Planning': `Let's plan an amazing celebration!

**6 Weeks Before:**
- Set date, budget, and guest count
- Choose theme (optional but fun!)
- Book venue if needed

**3 Weeks Before:**
- Send invitations
- Plan menu and drinks
- Create playlist

**1 Week Before:**
- Confirm RSVPs
- Shop for decorations
- Prep any make-ahead food

**Day Before:**
- Clean and set up space
- Prep food that can be made ahead
- Charge camera/phone

**Party Essentials Checklist:**
- [ ] Ice (more than you think!)
- [ ] Napkins and plates
- [ ] Trash bags
- [ ] Background music
- [ ] Phone charger for guests

*Sign up for themed party ideas and shopping lists!*`,

        'Daily Motivation': `Here's your dose of inspiration!

**Today's Mindset:**

> "The only way to do great work is to love what you do." — Steve Jobs

**Your Daily Reminders:**
- You're capable of more than you know
- Small progress is still progress
- It's okay to ask for help
- Today is a fresh start

**Quick Motivation Boost:**
1. Write down 3 things you're grateful for
2. Visualize your success for 60 seconds
3. Tell yourself: "I've got this"
4. Take one small action toward your goal

**Power Questions:**
- What would make today great?
- What's one thing I can do right now?
- How will future me thank present me?

*Create an account for daily personalized motivation and goal tracking!*`
    };

    // Guest mode fallback options (no API call needed) - 6 options per set
    // Pool of all suggestion options — "More" shuffles from these
    const GUEST_FALLBACK_OPTIONS = [
        [
            { title: 'Build me a landing page', subtitle: 'Responsive, modern design', action: "sendMessage('Build me a modern responsive landing page for a SaaS product with hero, features, pricing, and footer')", icon: 'code' },
            { title: 'Draft a resignation letter', subtitle: 'Professional, ready to send', action: "sendMessage('Write me a professional resignation letter giving two weeks notice. Keep it graceful and positive.')", icon: 'edit' },
            { title: 'Plan my week', subtitle: 'Time-blocked schedule', action: "sendMessage('Create a time-blocked weekly schedule. I work 9-5, want to exercise 3x/week, learn coding evenings, weekends free.')", icon: 'calendar' },
        ],
        [
            { title: 'Write a cold email', subtitle: 'Gets replies, no fluff', action: "sendMessage('Write me a cold outreach email for a B2B SaaS product that gets replies. Short, specific, no fluff.')", icon: 'edit' },
            { title: 'Compare React vs Vue', subtitle: 'Pros, cons, verdict', action: "sendMessage('Compare React vs Vue vs Svelte for building a modern web app. Table with pros, cons, and your recommendation.')", icon: 'lightbulb' },
            { title: 'Meal prep for 5 days', subtitle: 'Shopping list + recipes', action: "sendMessage('Plan 5 days of meal prep. Shopping list and simple recipes I can cook on Sunday. Budget-friendly.')", icon: 'coffee' },
        ],
        [
            { title: 'Name my startup', subtitle: '10 creative options', action: "sendMessage('Give me 10 creative startup name ideas for an AI-powered app. Include .com domain suggestions.')", icon: 'sparkles' },
            { title: 'Build a workout plan', subtitle: '4-week program', action: "sendMessage('Create a 4-week workout plan. 4 days/week, strength and cardio. Dumbbells and pull-up bar at home.')", icon: 'activity' },
            { title: 'Plan a trip to Japan', subtitle: '7-day itinerary', action: "sendMessage('Plan a 7-day trip to Japan. Day-by-day itinerary: Tokyo, Kyoto, Osaka. Food spots and transport tips.')", icon: 'globe' },
        ],
        [
            { title: '30-day Python plan', subtitle: 'Zero to projects', action: "sendMessage('Create a 30-day study plan to learn Python from scratch. Daily goals, resources, and mini-projects.')", icon: 'book' },
            { title: 'Write a short story', subtitle: 'Sci-fi, 500 words', action: "sendMessage('Write me a 500-word sci-fi short story about the last human who discovers they are not alone after all')", icon: 'sparkles' },
            { title: 'Top 10 movies + shows', subtitle: 'Highly rated picks', action: "sendMessage('Recommend 5 movies and 5 TV shows. Mix: thriller, sci-fi, comedy, drama. Only highly rated ones.')", icon: 'film' },
        ],
    ];
    // Build a flat pool of ALL guest options (excluding "More" buttons)
    const GUEST_OPTIONS_POOL = GUEST_FALLBACK_OPTIONS
        .flat()
        .filter(opt => opt.title !== 'More');
    let guestShownTitles = new Set();

    // State management
    class FetchState {
        constructor() {
            this.isFetching = false;
        }

        startFetch() {
            if (this.isFetching) return false;
            this.isFetching = true;
            return true;
        }

        endFetch() {
            this.isFetching = false;
        }
    }

    const fetchState = new FetchState();

    // Check if in guest mode (defined early for fetchMoreOptions)
    function isGuestMode() {
        return document.body.dataset.guestMode === 'true';
    }

    // Pick fresh random options from the pool — never repeat until all are shown
    function getNextGuestOptions() {
        // Filter out already-shown options
        let available = GUEST_OPTIONS_POOL.filter(opt => !guestShownTitles.has(opt.title));

        // If we've shown everything, reset
        if (available.length < 6) {
            guestShownTitles.clear();
            available = [...GUEST_OPTIONS_POOL];
        }

        // Shuffle and pick 3 (so grid shows 3 + More = 4 total)
        for (let i = available.length - 1; i > 0; i--) {
            const j = Math.floor(Math.random() * (i + 1));
            [available[i], available[j]] = [available[j], available[i]];
        }
        const picked = available.slice(0, 3);
        picked.forEach(opt => guestShownTitles.add(opt.title));

        // "More" is always the 4th button
        picked.push({ title: 'More', subtitle: 'Load new ideas', action: 'fetchMoreOptions()', icon: 'more' });
        return picked;
    }

    // Main function to fetch more options
    async function fetchMoreOptions() {
        if (!fetchState.startFetch()) return;

        const elements = getDOMElements();
        if (!elements) {
            fetchState.endFetch();
            return;
        }

        const { loadingIndicator, errorMessage, optionsContainer } = elements;
        const startTime = performance.now();

        let greetingLock = null;

        try {
            await handlePreFetchUI(elements);

            // Lock greeting position before transition (mobile)
            greetingLock = lockGreetingPosition();

            // Guest mode: use local fallback options (no API call)
            if (isGuestMode()) {
                const guestOptions = getNextGuestOptions();
                await delay(200); // Small delay for visual feedback
                await animateAndRemoveOldOptions(optionsContainer, 200);
                handleNewOptions(guestOptions, optionsContainer);
                finalizeFetch(loadingIndicator);
                fetchState.endFetch();
                // Unlock after a brief delay for DOM to settle
                setTimeout(() => unlockGreetingPosition(greetingLock), 100);
                return;
            }

            const csrfToken = getCSRFToken();

            if (!csrfToken) {
                throw new Error('CSRF token is missing.');
            }

            const data = await fetchOptionsFromServer(csrfToken);
            const fetchDuration = performance.now() - startTime;

            await Promise.all([
                animateAndRemoveOldOptions(optionsContainer, fetchDuration),
                delay(fetchDuration)
            ]);

            handleNewOptions(data.options, optionsContainer);

            // Unlock after new options are rendered
            setTimeout(() => unlockGreetingPosition(greetingLock), 100);
        } catch (error) {
            displayError(errorMessage, error);
            unlockGreetingPosition(greetingLock);
        } finally {
            finalizeFetch(loadingIndicator);
            fetchState.endFetch();
        }
    }

    // DOM element handling
    function getDOMElements() {
        const elements = {
            loadingIndicator: document.querySelector(CONFIG.SELECTORS.LOADING_INDICATOR),
            errorMessage: document.querySelector(CONFIG.SELECTORS.ERROR_MESSAGE),
            optionsContainer: document.querySelector(CONFIG.SELECTORS.OPTIONS_CONTAINER)
        };

        if (!validateDOMElements(elements)) {
            console.error('Missing required DOM elements');
            return null;
        }

        return elements;
    }

    function validateDOMElements(elements) {
        return Object.values(elements).every(element => element !== null);
    }

    function getCSRFToken() {
        const metaTag = document.querySelector(CONFIG.SELECTORS.CSRF_META);
        return metaTag?.getAttribute('content') || null;
    }

    // UI Handlers
    async function handlePreFetchUI({ loadingIndicator, errorMessage, optionsContainer }) {
        setElementVisibility(loadingIndicator, true);
        setElementVisibility(errorMessage, false);
        updateMoreButtonState(optionsContainer, true);
    }

    function updateMoreButtonState(container, isLoading) {
        const moreButton = container.querySelector(CONFIG.SELECTORS.MORE_BUTTON);
        if (!moreButton) return;

        moreButton.classList.toggle(CONFIG.CLASSES.LOADING, isLoading);
        const buttonText = moreButton.querySelector(CONFIG.SELECTORS.BUTTON_TEXT);
        if (buttonText) {
            buttonText.textContent = isLoading ? CONFIG.TEXT.LOADING : CONFIG.TEXT.MORE;
        }
    }

    // Greeting is now fixed via CSS, no JS needed
    function lockGreetingPosition() {
        return null;
    }

    function unlockGreetingPosition(lockData) {
        // No-op
    }

    // Animation and DOM manipulation — lock height to prevent greeting from jumping
    async function animateAndRemoveOldOptions(container, duration) {
        // Lock the container height before removing anything
        const currentHeight = container.offsetHeight;
        container.style.setProperty('--options-lock-height', currentHeight + 'px');

        const selector = CONFIG.SELECTORS.OPTIONS_BUTTON;
        await animateElementsOut(container, selector);
        clearOldOptions(container);
    }

    function clearOldOptions(container) {
        container.querySelectorAll(CONFIG.SELECTORS.OPTIONS_BUTTON)
            .forEach(option => option.remove());
    }

    async function animateElementsOut(container, selector) {
        const elements = container.querySelectorAll(selector);
        if (elements.length === 0) return;

        // Fade out all at once — no stagger on exit, feels snappier
        const animations = Array.from(elements).map((element) => {
            return new Promise(resolve => {
                element.classList.add('fade-out');
                element.addEventListener('animationend', () => resolve(), { once: true });
                setTimeout(resolve, 260);
            });
        });

        await Promise.all(animations);
    }

    // API interaction
    async function fetchOptionsFromServer(csrfToken) {
        const response = await fetch(CONFIG.ENDPOINTS.MORE_OPTIONS, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': csrfToken
            },
            body: JSON.stringify({})
        });

        if (!response.ok) {
            throw new Error(`${response.status}. Unable to fetch more options.`);
        }

        const data = await response.json();
        validateFetchedData(data);
        return data;
    }

    function validateFetchedData(data) {
        if (!data?.options?.length || !Array.isArray(data.options)) {
            throw new Error('Invalid response format: Expected non-empty options array.');
        }
    }

    // Options handling
    function handleNewOptions(options, container) {
        // All old options (including More) are already cleared in animateAndRemoveOldOptions
        appendNewOptions(options, container);
    }

    function appendNewOptions(options, container) {
        const fragment = document.createDocumentFragment();
        options.forEach(option => {
            const button = createOptionButton(option);
            button.classList.add(CONFIG.CLASSES.FADE_IN_NEW);
            fragment.appendChild(button);
        });
        container.appendChild(fragment);

        // Release the height lock after new options are laid out
        requestAnimationFrame(() => {
            container.style.removeProperty('--options-lock-height');
        });
    }

    // Icon SVG map - matches the icons from TASK_ICONS in grog_api.py
    const ICON_SVG_MAP = {
        'edit': '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 20h9"/><path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z"/></svg>',
        'code': '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="16 18 22 12 16 6"/><polyline points="8 6 2 12 8 18"/></svg>',
        'image': '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="2" ry="2"/><circle cx="8.5" cy="8.5" r="1.5"/><polyline points="21 15 16 10 5 21"/></svg>',
        'search': '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>',
        'info': '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="12" y1="16" x2="12" y2="12"/><line x1="12" y1="8" x2="12.01" y2="8"/></svg>',
        'calendar': '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="18" rx="2" ry="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/></svg>',
        'activity': '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/></svg>',
        'dollar': '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="1" x2="12" y2="23"/><path d="M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6"/></svg>',
        'globe': '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="2" y1="12" x2="22" y2="12"/><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/></svg>',
        'gift': '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 12 20 22 4 22 4 12"/><rect x="2" y="7" width="20" height="5"/><line x1="12" y1="22" x2="12" y2="7"/><path d="M12 7H7.5a2.5 2.5 0 0 1 0-5C11 2 12 7 12 7z"/><path d="M12 7h4.5a2.5 2.5 0 0 0 0-5C13 2 12 7 12 7z"/></svg>',
        'film': '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="2" width="20" height="20" rx="2.18" ry="2.18"/><line x1="7" y1="2" x2="7" y2="22"/><line x1="17" y1="2" x2="17" y2="22"/><line x1="2" y1="12" x2="22" y2="12"/><line x1="2" y1="7" x2="7" y2="7"/><line x1="2" y1="17" x2="7" y2="17"/><line x1="17" y1="17" x2="22" y2="17"/><line x1="17" y1="7" x2="22" y2="7"/></svg>',
        'home': '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><polyline points="9 22 9 12 15 12 15 22"/></svg>',
        'book': '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"/></svg>',
        'heart': '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20.84 4.61a5.5 5.5 0 0 0-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 0 0-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 0 0 0-7.78z"/></svg>',
        'zap': '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/></svg>',
        'coffee': '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 8h1a4 4 0 0 1 0 8h-1"/><path d="M2 8h16v9a4 4 0 0 1-4 4H6a4 4 0 0 1-4-4V8z"/><line x1="6" y1="1" x2="6" y2="4"/><line x1="10" y1="1" x2="10" y2="4"/><line x1="14" y1="1" x2="14" y2="4"/></svg>',
        'music': '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/></svg>',
        'sun': '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="5"/><line x1="12" y1="1" x2="12" y2="3"/><line x1="12" y1="21" x2="12" y2="23"/><line x1="4.22" y1="4.22" x2="5.64" y2="5.64"/><line x1="18.36" y1="18.36" x2="19.78" y2="19.78"/><line x1="1" y1="12" x2="3" y2="12"/><line x1="21" y1="12" x2="23" y2="12"/><line x1="4.22" y1="19.78" x2="5.64" y2="18.36"/><line x1="18.36" y1="5.64" x2="19.78" y2="4.22"/></svg>',
        'star': '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/></svg>',
        'sparkles': '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3l1.5 4.5L18 9l-4.5 1.5L12 15l-1.5-4.5L6 9l4.5-1.5L12 3z"/><path d="M5 19l1 3 1-3 3-1-3-1-1-3-1 3-3 1 3 1z"/><path d="M19 5l.5 1.5L21 7l-1.5.5L19 9l-.5-1.5L17 7l1.5-.5L19 5z"/></svg>',
        'lightbulb': '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="9" y1="18" x2="15" y2="18"/><line x1="10" y1="22" x2="14" y2="22"/><path d="M15.09 14c.18-.98.65-1.74 1.41-2.5A4.65 4.65 0 0 0 18 8 6 6 0 0 0 6 8c0 1 .23 2.23 1.5 3.5A4.61 4.61 0 0 1 8.91 14"/></svg>',
        'message': '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>',
        'more': '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/><circle cx="5" cy="12" r="1"/></svg>'
    };

    // Get icon SVG from icon name (falls back to star)
    function getIconSvg(iconName) {
        return ICON_SVG_MAP[iconName] || ICON_SVG_MAP['star'];
    }

    function createOptionButton(option) {
        const button = document.createElement('button');
        const isMoreButton = option.title === CONFIG.TEXT.MORE;

        button.className = `${CONFIG.CLASSES.OPTIONS_BUTTON} ${CONFIG.CLASSES.MODERN_OPTION} ${
            isMoreButton ? CONFIG.CLASSES.MORE_BUTTON : ''
        }`;
        if (option.action) {
            button.dataset.action = option.action;
        }

        // Store metadata for click tracking
        if (option.category) button.dataset.category = option.category;
        if (option.slot_type) button.dataset.slotType = option.slot_type;

        // Use icon from server response, fallback to 'star' if not provided
        const iconName = option.icon || (isMoreButton ? 'more' : 'star');
        button.dataset.icon = iconName;
        const iconSvg = getIconSvg(iconName);

        button.innerHTML = `
            <span class="option-icon">${iconSvg}</span>
            <span class="option-content">
                <span class="${CONFIG.SELECTORS.BUTTON_TEXT.slice(1)}">${option.title}</span>
                ${option.subtitle ? `<span class="subtitle">${option.subtitle}</span>` : ''}
            </span>
        `;

        return button;
    }

    // Utility functions
    function setElementVisibility(element, isVisible) {
        element.style.display = isVisible ? 'flex' : 'none';
    }

    function displayError(errorElement, error) {
        console.error('Error:', error);
        errorElement.textContent = CONFIG.TEXT.ERROR_PREFIX + error.message;
        setElementVisibility(errorElement, true);
    }

    function finalizeFetch(loadingIndicator) {
        setElementVisibility(loadingIndicator, false);
        const moreButton = document.querySelector(CONFIG.SELECTORS.MORE_BUTTON);
        updateMoreButtonState(moreButton?.parentElement, false);
    }

    function delay(ms) {
        return new Promise(resolve => setTimeout(resolve, ms));
    }

    // Make only the necessary functions globally available
    window.fetchMoreOptions = fetchMoreOptions;

    function normalizeAction(action) {
        if (!action) return '';
        return action.trim().replace(/\(\s*\)$/, '');
    }

    function extractSendMessagePayload(action) {
        const match = action.match(/^sendMessage\s*\((.*)\)\s*$/);
        if (!match) return null;
        let payload = match[1].trim();
        if ((payload.startsWith("'") && payload.endsWith("'")) || (payload.startsWith('"') && payload.endsWith('"'))) {
            payload = payload.slice(1, -1);
        }
        return payload;
    }

    // Find demo response for a given prompt
    function findDemoResponse(prompt) {
        if (!prompt) return null;
        const normalizedPrompt = prompt.toLowerCase().trim();

        for (const [key, response] of Object.entries(GUEST_DEMO_RESPONSES)) {
            if (normalizedPrompt.includes(key.toLowerCase())) {
                return { prompt: key, response };
            }
        }
        return null;
    }

    // Show demo response for guests (no API call)
    async function showGuestDemoResponse(prompt, demoResponse) {
        // Hide options
        const optionsContainer = document.querySelector(CONFIG.SELECTORS.OPTIONS_CONTAINER);
        if (optionsContainer) {
            optionsContainer.style.display = 'none';
        }

        // Add has-messages class to body
        if (window.setHasMessages) window.setHasMessages(true);
        else document.body.classList.add('has-messages');

        // Display user message
        if (typeof window.displayMessage === 'function') {
            window.displayMessage(prompt, 'user', 'text', prompt, true, null, null, null);
        }

        // Small delay to simulate thinking
        await delay(300);

        // Display AI response with typing animation
        if (typeof window.displayMessage === 'function') {
            window.displayMessage(demoResponse, 'ai', 'text', prompt, true, null, null, null);
        }

        // Show guest signup banner after demo
        if (typeof window.showGuestSignupBanner === 'function') {
            setTimeout(() => window.showGuestSignupBanner(), 1000);
        }
    }

    // Report suggestion click to backend for learning loop
    function reportSuggestionClick(clickData) {
        const csrfToken = getCSRFToken();
        if (!csrfToken) return;

        fetch('/suggestion-click', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': csrfToken,
            },
            body: JSON.stringify(clickData),
        }).catch(() => {}); // fire-and-forget
    }

    function handleOptionClick(button) {
        const rawAction = button?.dataset?.action || '';
        const action = normalizeAction(rawAction);

        // Allow "More" button to work in guest mode
        if (action === 'fetchMoreOptions') {
            fetchMoreOptions();
            return;
        }

        // Report click for smart suggestions learning loop (fire-and-forget)
        if (!isGuestMode()) {
            const buttonText = button?.querySelector(CONFIG.SELECTORS.BUTTON_TEXT)?.textContent?.trim();
            const subtitle = button?.querySelector('.subtitle')?.textContent?.trim();
            const category = button?.dataset?.category || '';
            const slotType = button?.dataset?.slotType || '';
            reportSuggestionClick({
                title: buttonText || '',
                subtitle: subtitle || '',
                category: category,
                slot_type: slotType,
            });
        }

        // Check for guest mode demo responses
        if (isGuestMode()) {
            const buttonText = button?.querySelector(CONFIG.SELECTORS.BUTTON_TEXT)?.textContent?.trim();
            const sendPayload = extractSendMessagePayload(rawAction);
            const promptToCheck = sendPayload || buttonText;

            const demo = findDemoResponse(promptToCheck);
            if (demo) {
                showGuestDemoResponse(demo.prompt, demo.response);
                return;
            }
        }

        const sendPayload = extractSendMessagePayload(rawAction);
        if (sendPayload && typeof window.sendMessage === 'function') {
            window.sendMessage(sendPayload);
            return;
        }

        if (action && typeof window[action] === 'function') {
            window[action]();
            return;
        }

        const buttonText = button?.querySelector(CONFIG.SELECTORS.BUTTON_TEXT)?.textContent?.trim();
        if (buttonText && typeof window.sendMessage === 'function') {
            window.sendMessage(buttonText);
        }
    }

    // Initialize after DOM is loaded
    document.addEventListener('DOMContentLoaded', () => {
        // Attach event listeners to all more buttons
        document.querySelectorAll(CONFIG.SELECTORS.MORE_BUTTON).forEach(button => {
            button.addEventListener('click', fetchMoreOptions);
        });

        // Initialize any existing more buttons
        const optionsContainer = document.querySelector(CONFIG.SELECTORS.OPTIONS_CONTAINER);
        if (optionsContainer) {
            updateMoreButtonState(optionsContainer, false);
            optionsContainer.addEventListener('click', (event) => {
                const button = event.target.closest(CONFIG.SELECTORS.OPTIONS_BUTTON);
                if (!button) return;
                handleOptionClick(button);
            });
        }
    });
})();
