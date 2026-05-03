// Portals tooltips for composer buttons (+ / microphone) outside the
// composer, so they escape the composer's mask-image clipping.
//
// .chat-shell__composer (styles.css) has a mask-image that clips any
// descendant rendered above the composer's solid area — including
// pseudo-element tooltips that extend upward via `bottom: 100% + 8px`.
// To show tooltips above the composer, we move them into <body> via JS.
//
// Self-contained: injects its own CSS + suppresses the original
// pseudo-element tooltips on the targeted buttons to avoid double-render.

(function () {
    'use strict';

    const TARGETS = ['#actions-toggle', '#microphone-button'];

    const style = document.createElement('style');
    style.textContent = `
        .actions-toggle[data-tooltip]::after,
        .actions-toggle[data-tooltip]::before,
        .microphone-button::after,
        .microphone-button::before {
            content: none !important;
        }
        .composer-tooltip-portal {
            position: fixed;
            background: var(--tooltip-bg-color, #0a0908);
            color: var(--tooltip-text-color, #e8e8f0);
            padding: 4px 8px;
            border-radius: 4px;
            font-size: 11px;
            font-weight: 500;
            white-space: nowrap;
            box-shadow: var(--shadow-md, 0 4px 12px rgba(0, 0, 0, 0.35));
            border: 1px solid var(--border-primary, rgba(255, 255, 255, 0.08));
            pointer-events: none;
            opacity: 0;
            transform: translateY(2px) scale(0.92);
            transition: opacity 0.15s ease, transform 0.15s ease;
            z-index: 10000;
        }
        .composer-tooltip-portal.visible {
            opacity: 1;
            transform: translateY(0) scale(1);
        }
        .composer-tooltip-portal::after {
            content: '';
            position: absolute;
            top: 100%;
            left: 50%;
            transform: translateX(-50%);
            border: 5px solid transparent;
            border-top-color: var(--tooltip-bg-color, #0a0908);
        }
    `;
    document.head.appendChild(style);

    let activeTooltip = null;

    function show(button) {
        const text = button.getAttribute('data-tooltip');
        if (!text) return;
        // Suppress actions-toggle tooltip while its popover is open
        // (matches the original CSS behavior at styles.css:2725).
        if (button.id === 'actions-toggle') {
            const menu = document.getElementById('actions-menu');
            if (menu && menu.classList.contains('open')) return;
        }

        hide();

        const tt = document.createElement('div');
        tt.className = 'composer-tooltip-portal';
        tt.textContent = text;
        document.body.appendChild(tt);

        const r = button.getBoundingClientRect();
        const ttR = tt.getBoundingClientRect();
        tt.style.left = (r.left + r.width / 2 - ttR.width / 2) + 'px';
        tt.style.top = (r.top - ttR.height - 8) + 'px';

        requestAnimationFrame(() => tt.classList.add('visible'));
        activeTooltip = tt;
    }

    function hide() {
        if (activeTooltip) {
            activeTooltip.remove();
            activeTooltip = null;
        }
    }

    function init() {
        TARGETS.forEach(sel => {
            const button = document.querySelector(sel);
            if (!button) return;
            button.addEventListener('mouseenter', () => show(button));
            button.addEventListener('mouseleave', hide);
            button.addEventListener('click', hide);
        });
        window.addEventListener('scroll', hide, true);
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
