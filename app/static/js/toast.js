/**
 * Toast Notification System
 * Based on XelaSphere Design Specifications
 *
 * Usage:
 * Toast.show('Message');
 * Toast.success('Success message');
 * Toast.error('Error message');
 * Toast.warning('Warning message');
 * Toast.info('Info message');
 *
 * Advanced:
 * Toast.show({
 *   title: 'Title',
 *   message: 'Message',
 *   type: 'success',
 *   duration: 5000,
 *   action: {
 *     label: 'Undo',
 *     onClick: () => console.log('Action clicked')
 *   }
 * });
 */

(function() {
  'use strict';

  // Toast configuration
  const DEFAULTS = {
    duration: 5000,
    type: 'default', // default, success, error, warning, info
    position: 'top-right', // top-right, top-left, bottom-right, bottom-left
    closable: true,
    icon: true
  };

  // Icon mapping
  const ICONS = {
    default: 'fa-circle-info',
    success: 'fa-circle-check',
    error: 'fa-circle-exclamation',
    warning: 'fa-triangle-exclamation',
    info: 'fa-circle-info'
  };

  class ToastManager {
    constructor() {
      this.container = null;
      this.toasts = new Map();
      this.init();
    }

    init() {
      // Create container if it doesn't exist
      if (!this.container) {
        this.container = document.createElement('div');
        this.container.className = 'toast-container';
        this.container.setAttribute('aria-live', 'polite');
        this.container.setAttribute('aria-atomic', 'false');
        document.body.appendChild(this.container);
      }
    }

    /**
     * Show a toast notification
     * @param {string|object} options - Message string or options object
     * @returns {string} Toast ID
     */
    show(options) {
      // Handle simple string message
      if (typeof options === 'string') {
        options = { message: options };
      }

      // Merge with defaults
      const config = { ...DEFAULTS, ...options };

      // Generate unique ID
      const id = `toast-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`;

      // Create toast element
      const toast = this.createToast(id, config);

      // Add to container
      this.container.appendChild(toast);
      this.toasts.set(id, { element: toast, config });

      // Trigger entrance animation
      requestAnimationFrame(() => {
        toast.classList.add('toast-visible');
      });

      // Set auto-dismiss timer
      if (config.duration > 0) {
        const progressBar = toast.querySelector('::before');
        if (progressBar) {
          toast.style.setProperty('--toast-duration', `${config.duration}ms`);
        }

        const timer = setTimeout(() => {
          this.dismiss(id);
        }, config.duration);

        // Store timer for pause/resume
        this.toasts.get(id).timer = timer;

        // Pause on hover
        toast.addEventListener('mouseenter', () => {
          clearTimeout(timer);
        });

        toast.addEventListener('mouseleave', () => {
          const newTimer = setTimeout(() => {
            this.dismiss(id);
          }, 1000); // Give 1 second after mouse leave
          this.toasts.get(id).timer = newTimer;
        });
      }

      return id;
    }

    /**
     * Create toast DOM element
     */
    createToast(id, config) {
      const toast = document.createElement('div');
      toast.className = `toast toast-${config.type}`;
      toast.id = id;
      toast.setAttribute('role', config.type === 'error' ? 'alert' : 'status');
      toast.style.setProperty('--toast-duration', `${config.duration}ms`);

      // Icon
      if (config.icon) {
        const iconEl = document.createElement('div');
        iconEl.className = 'toast__icon';
        iconEl.setAttribute('aria-hidden', 'true');
        iconEl.innerHTML = `<i class="fas ${ICONS[config.type] || ICONS.default}"></i>`;
        toast.appendChild(iconEl);
      }

      // Content
      const content = document.createElement('div');
      content.className = 'toast__content';

      if (config.title) {
        const title = document.createElement('h4');
        title.className = 'toast__title';
        title.textContent = config.title;
        content.appendChild(title);
      }

      if (config.message) {
        const message = document.createElement('p');
        message.className = 'toast__message';
        message.textContent = config.message;
        content.appendChild(message);
      }

      // Action button
      if (config.action) {
        const actionDiv = document.createElement('div');
        actionDiv.className = 'toast__action';

        const actionBtn = document.createElement('button');
        actionBtn.className = 'toast__action-btn';
        actionBtn.textContent = config.action.label || 'Action';
        actionBtn.addEventListener('click', () => {
          if (typeof config.action.onClick === 'function') {
            config.action.onClick();
          }
          this.dismiss(id);
        });

        actionDiv.appendChild(actionBtn);
        content.appendChild(actionDiv);
      }

      toast.appendChild(content);

      // Close button
      if (config.closable) {
        const closeBtn = document.createElement('button');
        closeBtn.className = 'toast__close';
        closeBtn.setAttribute('aria-label', 'Close notification');
        closeBtn.innerHTML = '<i class="fas fa-times"></i>';
        closeBtn.addEventListener('click', () => {
          this.dismiss(id);
        });
        toast.appendChild(closeBtn);
      }

      return toast;
    }

    /**
     * Dismiss a toast
     * @param {string} id - Toast ID
     */
    dismiss(id) {
      const toastData = this.toasts.get(id);
      if (!toastData) return;

      const { element, timer } = toastData;

      // Clear timer
      if (timer) {
        clearTimeout(timer);
      }

      // Add exit animation
      element.classList.add('toast-exiting');

      // Remove after animation
      setTimeout(() => {
        if (element.parentNode) {
          element.parentNode.removeChild(element);
        }
        this.toasts.delete(id);
      }, 300); // Match animation duration
    }

    /**
     * Dismiss all toasts
     */
    dismissAll() {
      this.toasts.forEach((_, id) => {
        this.dismiss(id);
      });
    }

    /**
     * Shorthand methods
     */
    success(message, options = {}) {
      return this.show({ ...options, message, type: 'success' });
    }

    error(message, options = {}) {
      return this.show({ ...options, message, type: 'error' });
    }

    warning(message, options = {}) {
      return this.show({ ...options, message, type: 'warning' });
    }

    info(message, options = {}) {
      return this.show({ ...options, message, type: 'info' });
    }
  }

  // Create global instance
  window.Toast = new ToastManager();

  // Export for modules
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = window.Toast;
  }
})();
