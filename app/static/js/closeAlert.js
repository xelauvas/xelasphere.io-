// Constants for configuration
const ANIMATION_DURATION = 300; // milliseconds
const ALERT_STATES = {
    VISIBLE: 'alert-visible',
    HIDING: 'alert-hiding',
    REMOVED: 'alert-removed'
};

/**
 * Enhanced function to close alerts with animations and additional features
 * @param {HTMLElement} button - The button that triggered the close
 * @param {Object} options - Configuration options
 * @returns {Promise} Resolves when alert is fully closed
 */
function closeAlert(button, options = {}) {
    const {
        animationDuration = ANIMATION_DURATION,
        onBeforeClose = null,
        onAfterClose = null,
        enableRestore = true
    } = options;

    return new Promise((resolve, reject) => {
        // Find and validate alert element
        const alert = button?.closest('.alert');
        if (!alert) {
            reject(new Error('No alert element found'));
            return;
        }

        // Store alert data for potential restoration
        const alertData = {
            html: alert.innerHTML,
            classes: [...alert.classList],
            position: alert.getBoundingClientRect()
        };

        // Prevent multiple close attempts
        if (alert.dataset.closing === 'true') {
            reject(new Error('Alert is already closing'));
            return;
        }
        alert.dataset.closing = 'true';

        // Calculate alert height for smooth collapse animation
        const alertHeight = alert.offsetHeight;
        alert.style.height = `${alertHeight}px`;

        // Execute before close callback
        if (onBeforeClose) {
            onBeforeClose(alert);
        }

        // Start closing animation
        requestAnimationFrame(() => {
            alert.classList.add(ALERT_STATES.HIDING);
            alert.style.height = '0';
            alert.style.opacity = '0';
            alert.style.margin = '0';
            alert.style.padding = '0';

            // Handle animation completion
            const handleTransitionEnd = () => {
                alert.classList.add(ALERT_STATES.REMOVED);
                alert.remove();

                // Execute after close callback
                if (onAfterClose) {
                    onAfterClose(alertData);
                }

                // Add restore functionality if enabled
                if (enableRestore) {
                    const restoreFunction = () => {
                        const newAlert = document.createElement('div');
                        newAlert.innerHTML = alertData.html;
                        newAlert.className = alertData.classes.join(' ');
                        document.body.appendChild(newAlert);
                        
                        // Trigger reflow and animate in
                        requestAnimationFrame(() => {
                            newAlert.classList.add(ALERT_STATES.VISIBLE);
                        });
                    };
                    // Store restore function for later use
                    window._lastRemovedAlert = restoreFunction;
                }

                resolve(alertData);
            };

            // Ensure transition completes
            setTimeout(handleTransitionEnd, animationDuration);
        });
    });
}

document.addEventListener('DOMContentLoaded', () => {
    document.addEventListener('click', (event) => {
        const button = event.target.closest('.btn-close');
        if (!button) return;
        closeAlert(button).catch(() => {});
    });
});
