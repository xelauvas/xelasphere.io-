/**
 * Mobile Long-Press Orbit Ring Menu
 * Actions orbit around the touch point like planets around a sun
 * Innovative mobile UX pattern
 */

(function() {
    'use strict';

    // Only run on touch devices
    const isTouchDevice = 'ontouchstart' in window || navigator.maxTouchPoints > 0;

    // Configuration
    const CONFIG = {
        holdDuration: 400,       // ms to trigger long-press
        moveThreshold: 10,       // px movement allowed during hold
        vibrationDuration: 30,   // ms for haptic feedback
        orbitRadius: 85,         // px distance from center (larger for 5 buttons)
        orbitSpeed: 20,          // seconds for full rotation
        actionSize: 50,          // px size of action buttons
        dismissDelay: 5000       // auto dismiss after 5s
    };

    let holdTimer = null;
    let startX = 0;
    let startY = 0;
    let activeTab = null;
    let orbitContainer = null;
    let orbitAnimation = null;
    let dismissTimer = null;
    let quantumAnimation = null;
    let quantumParticles = null;
    let quantumProgress = 0;

    /**
     * Initialize long-press handlers
     */
    function init() {
        if (!isTouchDevice) {
            console.log('[OrbitMenu] Not a touch device, skipping initialization');
            return;
        }

        const tabList = document.getElementById('tab-list');
        if (!tabList) {
            console.log('[OrbitMenu] No tab-list found');
            return;
        }

        // Add CSS for orbit menu
        injectStyles();

        // Touch events for mobile long-press
        tabList.addEventListener('touchstart', handleTouchStart, { passive: false });
        tabList.addEventListener('touchmove', handleTouchMove, { passive: true });
        tabList.addEventListener('touchend', handleTouchEnd, { passive: true });
        tabList.addEventListener('touchcancel', handleTouchCancel, { passive: true });

        console.log('[OrbitMenu] Orbit Ring menu initialized');
    }

    /**
     * Inject CSS styles for orbit menu
     */
    function injectStyles() {
        if (document.getElementById('orbit-menu-styles')) return;

        const styles = document.createElement('style');
        styles.id = 'orbit-menu-styles';
        styles.textContent = `
            .orbit-menu-overlay {
                position: fixed;
                top: 0;
                left: 0;
                right: 0;
                bottom: 0;
                z-index: 9999;
                background: rgba(0, 0, 0, 0.3);
                backdrop-filter: blur(2px);
                opacity: 0;
                transition: opacity 0.2s ease;
            }
            .orbit-menu-overlay.visible {
                opacity: 1;
            }
            .orbit-ring {
                position: absolute;
                width: 0;
                height: 0;
                pointer-events: none;
            }
            .orbit-center {
                position: absolute;
                width: 16px;
                height: 16px;
                background: #3b82f6;
                border-radius: 50%;
                transform: translate(-50%, -50%);
                box-shadow: 0 0 20px rgba(59, 130, 246, 0.6);
                animation: orbit-pulse 1.5s ease-in-out infinite;
            }
            @keyframes orbit-pulse {
                0%, 100% { transform: translate(-50%, -50%) scale(1); opacity: 1; }
                50% { transform: translate(-50%, -50%) scale(1.3); opacity: 0.7; }
            }
            .orbit-action {
                position: absolute;
                width: ${CONFIG.actionSize}px;
                height: ${CONFIG.actionSize}px;
                background: #1e293b;
                border: 2px solid #3b82f6;
                border-radius: 50%;
                display: flex;
                align-items: center;
                justify-content: center;
                color: #fff;
                font-size: 18px;
                cursor: pointer;
                pointer-events: auto;
                touch-action: manipulation;
                -webkit-tap-highlight-color: transparent;
                transform: translate(-50%, -50%) scale(0);
                transition: transform 0.3s cubic-bezier(0.34, 1.56, 0.64, 1),
                            background 0.2s ease,
                            border-color 0.2s ease;
                box-shadow: 0 4px 15px rgba(0, 0, 0, 0.3);
                z-index: 10000;
            }
            .orbit-action.visible {
                transform: translate(-50%, -50%) scale(1);
            }
            .orbit-action:active,
            .orbit-action.tapping {
                background: #3b82f6;
                transform: translate(-50%, -50%) scale(0.9) !important;
            }
            .orbit-action svg {
                width: 22px;
                height: 22px;
                fill: currentColor;
            }
            .orbit-action-label {
                position: absolute;
                bottom: -24px;
                left: 50%;
                transform: translateX(-50%);
                font-size: 11px;
                color: #fff;
                white-space: nowrap;
                text-shadow: 0 1px 3px rgba(0,0,0,0.5);
                opacity: 0;
                transition: opacity 0.2s ease;
            }
            .orbit-action.visible .orbit-action-label {
                opacity: 1;
            }
            .orbit-ring.rotating .orbit-action {
                animation: orbit-float 3s ease-in-out infinite;
            }
            @keyframes orbit-float {
                0%, 100% { margin-top: 0; }
                50% { margin-top: -4px; }
            }

            /* Quantum Blur - uncertainty effect */
            .quantum-uncertain {
                animation: quantum-flicker 0.15s steps(2) infinite;
            }
            @keyframes quantum-flicker {
                0% { opacity: 1; filter: blur(0px); }
                50% { opacity: 0.7; filter: blur(0.5px); }
                100% { opacity: 1; filter: blur(0px); }
            }
            .quantum-collapse {
                animation: quantum-collapse 0.2s ease-out forwards;
            }
            @keyframes quantum-collapse {
                0% { filter: blur(2px); opacity: 0.5; }
                100% { filter: blur(0); opacity: 1; }
            }
            .quantum-particles {
                position: absolute;
                top: 0;
                left: 0;
                right: 0;
                bottom: 0;
                pointer-events: none;
                overflow: hidden;
            }
            .quantum-particle {
                position: absolute;
                width: 3px;
                height: 3px;
                background: #3b82f6;
                border-radius: 50%;
                opacity: 0;
                box-shadow: 0 0 4px #3b82f6;
            }
            .quantum-particle.active {
                animation: particle-exist 0.3s ease-in-out infinite;
            }
            @keyframes particle-exist {
                0% { opacity: 0; transform: scale(0); }
                50% { opacity: 0.8; transform: scale(1.2); }
                100% { opacity: 0; transform: scale(0); }
            }
            .quantum-ghost {
                position: absolute;
                opacity: 0.15;
                color: #3b82f6;
                pointer-events: none;
                filter: blur(1px);
            }
        `;
        document.head.appendChild(styles);
    }

    /**
     * Start quantum blur effect - uncertainty until observation collapses it
     */
    function startQuantumBlur(tab) {
        if (quantumAnimation) return;

        const tabName = tab.querySelector('.tab-name');
        if (!tabName) return;

        // Add uncertainty flicker to text
        tabName.classList.add('quantum-uncertain');

        // Create particle container
        quantumParticles = document.createElement('div');
        quantumParticles.className = 'quantum-particles';
        tab.style.position = 'relative';
        tab.appendChild(quantumParticles);

        // Create flickering particles around the text
        const particles = [];
        for (let i = 0; i < 12; i++) {
            const particle = document.createElement('div');
            particle.className = 'quantum-particle';
            quantumParticles.appendChild(particle);
            particles.push(particle);
        }

        // Create ghost text duplicates (superposition)
        const ghostCount = 3;
        const ghosts = [];
        for (let i = 0; i < ghostCount; i++) {
            const ghost = document.createElement('span');
            ghost.className = 'quantum-ghost';
            ghost.textContent = tabName.textContent;
            ghost.style.left = `${tabName.offsetLeft}px`;
            ghost.style.top = `${tabName.offsetTop}px`;
            quantumParticles.appendChild(ghost);
            ghosts.push(ghost);
        }

        quantumProgress = 0;
        let frameCount = 0;

        function animateQuantum() {
            frameCount++;
            quantumProgress = Math.min(1, quantumProgress + (1 / (CONFIG.holdDuration / 16)));

            // Particles teleport to random positions (quantum tunneling)
            if (frameCount % 3 === 0) {
                particles.forEach((p, i) => {
                    // Only activate some particles based on progress
                    if (Math.random() < quantumProgress * 0.7) {
                        p.classList.add('active');
                        p.style.left = `${Math.random() * 100}%`;
                        p.style.top = `${Math.random() * 100}%`;
                        // Random delay for async feeling
                        p.style.animationDelay = `${Math.random() * 0.2}s`;
                    } else {
                        p.classList.remove('active');
                    }
                });
            }

            // Ghosts shift position (superposition states)
            if (frameCount % 5 === 0) {
                ghosts.forEach((ghost, i) => {
                    const offsetX = (Math.random() - 0.5) * 6 * quantumProgress;
                    const offsetY = (Math.random() - 0.5) * 4 * quantumProgress;
                    ghost.style.transform = `translate(${offsetX}px, ${offsetY}px)`;
                    ghost.style.opacity = 0.1 + (quantumProgress * 0.15);
                });
            }

            // Increase blur intensity over time
            const blurAmount = quantumProgress * 1.5;
            tabName.style.filter = `blur(${blurAmount}px)`;

            if (quantumProgress < 1) {
                quantumAnimation = requestAnimationFrame(animateQuantum);
            }
        }

        quantumAnimation = requestAnimationFrame(animateQuantum);
    }

    /**
     * Collapse quantum state - reality solidifies
     */
    function collapseQuantum(triggered = false) {
        if (quantumAnimation) {
            cancelAnimationFrame(quantumAnimation);
            quantumAnimation = null;
        }

        if (activeTab) {
            const tabName = activeTab.querySelector('.tab-name');
            if (tabName) {
                tabName.classList.remove('quantum-uncertain');
                tabName.style.filter = '';

                if (triggered) {
                    // Dramatic collapse animation when menu triggers
                    tabName.classList.add('quantum-collapse');
                    setTimeout(() => tabName.classList.remove('quantum-collapse'), 200);
                }
            }
        }

        // Remove particle container
        if (quantumParticles) {
            quantumParticles.remove();
            quantumParticles = null;
        }

        quantumProgress = 0;
    }

    /**
     * Handle touch start
     */
    function handleTouchStart(e) {
        const chatTab = e.target.closest('.chat-tab');
        if (!chatTab) return;

        // Don't trigger on action buttons
        if (e.target.closest('.tab-actions') ||
            e.target.closest('.tab-actions-toggle') ||
            e.target.closest('.button-group')) {
            return;
        }

        const touch = e.touches[0];
        startX = touch.clientX;
        startY = touch.clientY;
        activeTab = chatTab;

        document.body.classList.add('no-select');
        chatTab.classList.add('holding');

        // Start quantum blur effect - uncertainty until collapse
        startQuantumBlur(chatTab);

        holdTimer = setTimeout(() => {
            e.preventDefault();
            collapseQuantum(true);  // Collapse reality, then show menu
            triggerOrbitMenu(chatTab, startX, startY);
        }, CONFIG.holdDuration);
    }

    /**
     * Handle touch move
     */
    function handleTouchMove(e) {
        if (!holdTimer) return;

        const touch = e.touches[0];
        const deltaX = Math.abs(touch.clientX - startX);
        const deltaY = Math.abs(touch.clientY - startY);

        if (deltaX > CONFIG.moveThreshold || deltaY > CONFIG.moveThreshold) {
            cancelHold();
        }
    }

    /**
     * Handle touch end
     */
    function handleTouchEnd() {
        cancelHold();
    }

    /**
     * Handle touch cancel
     */
    function handleTouchCancel() {
        cancelHold();
    }

    /**
     * Cancel hold timer
     */
    function cancelHold() {
        if (holdTimer) {
            clearTimeout(holdTimer);
            holdTimer = null;
        }
        // Collapse quantum state without trigger animation
        collapseQuantum(false);
        if (activeTab) {
            activeTab.classList.remove('holding');
            activeTab = null;
        }
        document.body.classList.remove('no-select');
    }

    /**
     * Trigger orbit menu
     */
    function triggerOrbitMenu(chatTab, x, y) {
        holdTimer = null;
        chatTab.classList.remove('holding');

        // Haptic feedback
        if (navigator.vibrate) {
            navigator.vibrate(CONFIG.vibrationDuration);
        }

        // Get session info
        const sessionId = chatTab.dataset.sessionId;
        const sessionName = chatTab.querySelector('.tab-name')?.textContent || 'Chat';
        const isPinned = chatTab.classList.contains('pinned-tab');

        // Create overlay
        orbitContainer = document.createElement('div');
        orbitContainer.className = 'orbit-menu-overlay';
        orbitContainer.innerHTML = `
            <div class="orbit-ring" style="left: ${x}px; top: ${y}px;">
                <div class="orbit-center"></div>
            </div>
        `;

        const orbitRing = orbitContainer.querySelector('.orbit-ring');

        // Define all 5 actions (matching your existing buttons)
        const actions = [
            { id: 'share', icon: '<i class="fas fa-share-alt"></i>', label: 'Share' },
            { id: 'pin', icon: '<i class="fas fa-thumbtack"></i>', label: isPinned ? 'Unpin' : 'Pin' },
            { id: 'archive', icon: '<i class="fas fa-box-archive"></i>', label: 'Archive' },
            { id: 'rename', icon: '<i class="fas fa-edit"></i>', label: 'Rename' },
            { id: 'delete', icon: '<i class="fas fa-trash"></i>', label: 'Delete' }
        ];

        // Create orbiting action buttons
        const angleStep = 360 / actions.length;
        actions.forEach((action, index) => {
            const angle = (angleStep * index) - 90; // Start from top
            const btn = document.createElement('div');
            btn.className = 'orbit-action';
            btn.dataset.action = action.id;
            btn.innerHTML = `
                ${action.icon}
                <span class="orbit-action-label">${action.label}</span>
            `;

            // Position on orbit
            const rad = (angle * Math.PI) / 180;
            const px = Math.cos(rad) * CONFIG.orbitRadius;
            const py = Math.sin(rad) * CONFIG.orbitRadius;
            btn.style.left = `${px}px`;
            btn.style.top = `${py}px`;

            // Simple tap handler - works on both mobile and desktop
            let tapStarted = false;

            btn.addEventListener('touchstart', (e) => {
                tapStarted = true;
                btn.classList.add('tapping');
            }, { passive: true });

            btn.addEventListener('touchend', (e) => {
                if (tapStarted) {
                    e.preventDefault();
                    console.log('[OrbitMenu] Tapped:', action.id, 'sessionId:', sessionId);
                    btn.classList.remove('tapping');
                    tapStarted = false;

                    // Small delay to show visual feedback
                    setTimeout(() => {
                        handleAction(action.id, sessionId, sessionName, chatTab);
                    }, 50);
                }
            }, { passive: false });

            btn.addEventListener('touchcancel', () => {
                tapStarted = false;
                btn.classList.remove('tapping');
            }, { passive: true });

            // Click for desktop testing
            btn.addEventListener('click', (e) => {
                console.log('[OrbitMenu] Clicked:', action.id, 'sessionId:', sessionId);
                handleAction(action.id, sessionId, sessionName, chatTab);
            });

            orbitRing.appendChild(btn);
        });

        // Add to DOM
        document.body.appendChild(orbitContainer);

        // Animate in
        requestAnimationFrame(() => {
            orbitContainer.classList.add('visible');
            const buttons = orbitContainer.querySelectorAll('.orbit-action');
            buttons.forEach((btn, i) => {
                setTimeout(() => btn.classList.add('visible'), i * 80);
            });

            // Start slow rotation after buttons appear
            setTimeout(() => {
                orbitRing.classList.add('rotating');
                startOrbitAnimation(orbitRing);
            }, actions.length * 80 + 300);
        });

        // Touch/click overlay to dismiss (but not buttons)
        orbitContainer.addEventListener('touchend', (e) => {
            // Only dismiss if tapping the overlay background, not a button
            if (e.target === orbitContainer || e.target.classList.contains('orbit-ring') || e.target.classList.contains('orbit-center')) {
                dismissOrbitMenu();
            }
        }, { passive: true });

        orbitContainer.addEventListener('click', (e) => {
            if (e.target === orbitContainer || e.target.classList.contains('orbit-ring') || e.target.classList.contains('orbit-center')) {
                dismissOrbitMenu();
            }
        });

        // Auto dismiss
        dismissTimer = setTimeout(dismissOrbitMenu, CONFIG.dismissDelay);

        activeTab = null;
        document.body.classList.remove('no-select');
    }

    /**
     * Start orbit animation - very slow rotation
     */
    function startOrbitAnimation(orbitRing) {
        let rotation = 0;
        const speed = 0.1; // degrees per frame (very slow)

        function animate() {
            rotation += speed;
            orbitRing.style.transform = `rotate(${rotation}deg)`;

            // Counter-rotate buttons to keep them upright and tappable
            const actions = orbitRing.querySelectorAll('.orbit-action');
            actions.forEach(action => {
                action.style.transform = `translate(-50%, -50%) rotate(${-rotation}deg) scale(1)`;
            });

            orbitAnimation = requestAnimationFrame(animate);
        }

        orbitAnimation = requestAnimationFrame(animate);
    }

    /**
     * Dismiss orbit menu
     */
    function dismissOrbitMenu() {
        if (dismissTimer) {
            clearTimeout(dismissTimer);
            dismissTimer = null;
        }
        if (orbitAnimation) {
            cancelAnimationFrame(orbitAnimation);
            orbitAnimation = null;
        }
        if (orbitContainer) {
            orbitContainer.classList.remove('visible');
            setTimeout(() => {
                orbitContainer?.remove();
                orbitContainer = null;
            }, 200);
        }
    }

    /**
     * Handle action selection - calls the exact same methods as the original buttons
     */
    function handleAction(actionId, sessionId, sessionName, chatTab) {
        // Quick vibration feedback
        if (navigator.vibrate) {
            navigator.vibrate(20);
        }

        dismissOrbitMenu();

        // Get chatManager instance (the actual global name)
        const manager = window.chatManager;
        if (!manager) {
            console.error('[OrbitMenu] chatManager not found');
            return;
        }

        console.log('[OrbitMenu] Executing action:', actionId, 'on session:', sessionId);

        // Call the exact same methods as the original button-group buttons
        switch (actionId) {
            case 'share':
                // Same as data-action="shareSession"
                manager.shareSession(sessionId);
                break;

            case 'pin':
                // Same as data-action="pinSession" (toggles pin/unpin)
                manager.pinSession(sessionId);
                break;

            case 'archive':
                // Same as data-action="archiveSession"
                manager.archiveSession(sessionId);
                break;

            case 'rename':
                // Same as data-action="renameSession"
                manager.renameSession(sessionId);
                break;

            case 'delete':
                // Same as data-action="deleteSession" (shows delete modal)
                manager.deleteSession(sessionId);
                break;
        }
    }

    /**
     * Fallback rename function
     */
    async function renameSessionFallback(sessionId, newName) {
        try {
            const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content;
            const response = await fetch(`/chat/session/${sessionId}/rename`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': csrfToken
                },
                body: JSON.stringify({ name: newName })
            });
            if (response.ok) {
                const tab = document.querySelector(`.chat-tab[data-session-id="${sessionId}"] .tab-title`);
                if (tab) tab.textContent = newName;
            }
        } catch (e) {
            console.error('[OrbitMenu] Rename failed:', e);
        }
    }

    // Initialize when DOM is ready
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }

})();
