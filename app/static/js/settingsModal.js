/**
 * SettingsModal — singleton controller for the global settings modal.
 * Mirrors ChatGPT's Settings modal: left-rail navigation, content panes
 * swap on tab click. ESC / backdrop / X all close.
 */
(function() {
    'use strict';

    const VALID_TABS = new Set([
        'general', 'notifications', 'personalization', 'speech',
        'data', 'connected', 'security', 'api-keys'
    ]);
    const DEFAULT_TAB = 'general';

    class SettingsModalController {
        constructor() {
            this.activeTab = DEFAULT_TAB;
            this.triggerElement = null;
            this._modal = null;
            this._railItems = null;
            this._panes = null;
            this._boundEsc = null;
            this._initialized = false;
        }

        _init() {
            if (this._initialized) return;
            this._modal = document.getElementById('settings-modal');
            if (!this._modal) {
                console.warn('[SettingsModal] #settings-modal not found in DOM');
                return;
            }
            this._railItems = this._modal.querySelectorAll('.settings-rail-item');
            this._panes = this._modal.querySelectorAll('.settings-pane');

            // Tab clicks
            this._railItems.forEach(btn => {
                btn.addEventListener('click', () => {
                    this.setTab(btn.dataset.tab);
                });
            });

            // Close button(s)
            this._modal.querySelectorAll('[data-settings-close]').forEach(btn => {
                btn.addEventListener('click', () => this.close());
            });

            // Backdrop click closes (only when target IS the modal element itself,
            // not a descendant — so clicks inside the dialog don't close it)
            this._modal.addEventListener('click', e => {
                if (e.target === this._modal) this.close();
            });

            this._initialized = true;
        }

        open(tabId) {
            this._init();
            if (!this._modal) return;

            this.triggerElement = document.activeElement;
            const targetTab = (tabId && VALID_TABS.has(tabId)) ? tabId : DEFAULT_TAB;
            if (tabId && !VALID_TABS.has(tabId)) {
                console.warn(`[SettingsModal] invalid tab "${tabId}", falling back to "${DEFAULT_TAB}"`);
            }

            this.setTab(targetTab);

            this._modal.setAttribute('aria-hidden', 'false');
            document.body.classList.add('settings-modal-open');

            // ESC handler — added on open, removed on close
            this._boundEsc = e => {
                if (e.key === 'Escape') {
                    e.stopPropagation();
                    this.close();
                }
            };
            document.addEventListener('keydown', this._boundEsc);

            // Focus the active rail item for keyboard navigation
            const activeRailItem = this._modal.querySelector('.settings-rail-item.is-active');
            if (activeRailItem) activeRailItem.focus();
        }

        close() {
            if (!this._modal) return;
            this._modal.setAttribute('aria-hidden', 'true');
            document.body.classList.remove('settings-modal-open');

            if (this._boundEsc) {
                document.removeEventListener('keydown', this._boundEsc);
                this._boundEsc = null;
            }

            if (this.triggerElement && typeof this.triggerElement.focus === 'function') {
                this.triggerElement.focus();
                this.triggerElement = null;
            }
        }

        setTab(tabId) {
            this._init();
            if (!this._modal) return;
            if (!VALID_TABS.has(tabId)) tabId = DEFAULT_TAB;

            this.activeTab = tabId;
            this._railItems.forEach(btn => {
                btn.classList.toggle('is-active', btn.dataset.tab === tabId);
            });
            this._panes.forEach(pane => {
                pane.classList.toggle('is-active', pane.dataset.pane === tabId);
            });
        }

        isOpen() {
            return !!this._modal && this._modal.getAttribute('aria-hidden') === 'false';
        }
    }

    window.SettingsModal = new SettingsModalController();

    // Hijack any element with data-settings-trigger to open the modal.
    // The optional value of the attribute is the tab id to land on.
    document.addEventListener('DOMContentLoaded', () => {
        document.querySelectorAll('[data-settings-trigger]').forEach(el => {
            el.addEventListener('click', e => {
                e.preventDefault();
                const tabId = el.dataset.settingsTrigger || undefined;
                window.SettingsModal.open(tabId);
            });
        });
    });
})();
