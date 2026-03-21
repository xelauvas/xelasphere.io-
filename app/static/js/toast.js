/**
 * Toast Notification System
 * XelaSphere — liquid glass design
 */

(function() {
  'use strict';

  const DEFAULTS = {
    duration: 5000,
    type: 'default',
    closable: true
  };

  const TYPE_ICONS = {
    error:   'fa-solid fa-circle-xmark',
    success: 'fa-solid fa-circle-check',
    warning: 'fa-solid fa-triangle-exclamation',
    info:    'fa-solid fa-circle-info',
    default: 'fa-solid fa-circle-info'
  };

  class ToastManager {
    constructor() {
      this.container = null;
      this.toasts = new Map();
      this.init();
    }

    init() {
      if (!this.container) {
        this.container = document.createElement('div');
        this.container.className = 'toast-container';
        this.container.setAttribute('aria-live', 'polite');

        const inputContainer = document.querySelector('.input-container');
        if (inputContainer) {
          inputContainer.insertBefore(this.container, inputContainer.firstChild);
        } else {
          document.body.appendChild(this.container);
        }
      }
    }

    show(options) {
      if (typeof options === 'string') {
        options = { message: options };
      }

      const config = { ...DEFAULTS, ...options };
      const id = `toast-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`;
      const toast = this._build(id, config);

      this.container.appendChild(toast);
      this.toasts.set(id, { element: toast, config, timer: null });

      // Auto-dismiss
      if (config.duration > 0) {
        this._startTimer(id, config.duration);

        toast.addEventListener('mouseenter', () => this._clearTimer(id));
        toast.addEventListener('mouseleave', () => this._startTimer(id, 1500));
      }

      return id;
    }

    _build(id, config) {
      const type = config.type || 'default';

      const toast = document.createElement('div');
      toast.className = `toast toast--${type}`;
      toast.id = id;
      toast.setAttribute('role', type === 'error' ? 'alert' : 'status');

      // --- Icon ---
      const icon = document.createElement('span');
      icon.className = 'toast__icon';
      icon.innerHTML = `<i class="${TYPE_ICONS[type] || TYPE_ICONS.default}"></i>`;
      toast.appendChild(icon);

      // --- Message ---
      const msg = document.createElement('span');
      msg.className = 'toast__msg';
      msg.textContent = config.message || '';
      toast.appendChild(msg);

      // --- Actions (top-right) ---
      const actions = document.createElement('span');
      actions.className = 'toast__actions';

      // Copy
      const copyBtn = document.createElement('button');
      copyBtn.className = 'toast__btn';
      copyBtn.setAttribute('aria-label', 'Copy');
      copyBtn.innerHTML = '<i class="fa-regular fa-copy"></i>';
      copyBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        navigator.clipboard.writeText(msg.textContent.trim()).then(() => {
          copyBtn.innerHTML = '<i class="fa-solid fa-check"></i>';
          copyBtn.classList.add('toast__btn--done');
          setTimeout(() => {
            copyBtn.innerHTML = '<i class="fa-regular fa-copy"></i>';
            copyBtn.classList.remove('toast__btn--done');
          }, 1400);
        }).catch(() => {});
      });
      actions.appendChild(copyBtn);

      // Close
      if (config.closable) {
        const closeBtn = document.createElement('button');
        closeBtn.className = 'toast__btn';
        closeBtn.setAttribute('aria-label', 'Close');
        closeBtn.innerHTML = '<i class="fa-solid fa-xmark"></i>';
        closeBtn.addEventListener('click', (e) => {
          e.stopPropagation();
          this.dismiss(id);
        });
        actions.appendChild(closeBtn);
      }

      toast.appendChild(actions);

      // Click body to dismiss
      toast.addEventListener('click', (e) => {
        if (e.target.closest('.toast__btn')) return;
        this.dismiss(id);
      });

      return toast;
    }

    _startTimer(id, ms) {
      this._clearTimer(id);
      const data = this.toasts.get(id);
      if (!data) return;
      data.timer = setTimeout(() => this.dismiss(id), ms);
    }

    _clearTimer(id) {
      const data = this.toasts.get(id);
      if (data && data.timer) {
        clearTimeout(data.timer);
        data.timer = null;
      }
    }

    dismiss(id) {
      const data = this.toasts.get(id);
      if (!data) return;
      this._clearTimer(id);

      data.element.classList.add('toast--exit');
      setTimeout(() => {
        if (data.element.parentNode) data.element.parentNode.removeChild(data.element);
        this.toasts.delete(id);
      }, 250);
    }

    dismissAll() {
      this.toasts.forEach((_, id) => this.dismiss(id));
    }

    success(msg, opts = {}) { return this.show({ ...opts, message: msg, type: 'success' }); }
    error(msg, opts = {})   { return this.show({ ...opts, message: msg, type: 'error' }); }
    warning(msg, opts = {}) { return this.show({ ...opts, message: msg, type: 'warning' }); }
    info(msg, opts = {})    { return this.show({ ...opts, message: msg, type: 'info' }); }
  }

  window.Toast = new ToastManager();
})();
