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

    /**
     * Memory toggle wiring. Mirrors the legacy settings_api_keys.html flow
     * but lives in this controller so it survives PR 5's deletion of that
     * template.
     */
    async function loadMemoryToggleState() {
        try {
            const resp = await fetch('/user/memory-toggles', { credentials: 'same-origin' });
            if (!resp.ok) return;
            const state = await resp.json();
            document.querySelectorAll('[data-memory-toggle]').forEach(input => {
                const key = input.dataset.memoryToggle;
                if (key in state) input.checked = !!state[key];
            });
        } catch (e) {
            console.warn('[SettingsModal] failed to load memory toggles', e);
        }
    }

    async function saveMemoryToggle(key, value) {
        const csrf = document.querySelector('meta[name="csrf-token"]')?.content
                  || document.querySelector('input[name="csrf_token"]')?.value
                  || '';
        try {
            const resp = await fetch('/user/memory-toggles', {
                method: 'POST',
                credentials: 'same-origin',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': csrf
                },
                body: JSON.stringify({ [key]: value })
            });
            if (!resp.ok) throw new Error(`status ${resp.status}`);
            if (window.Toast) window.Toast.success('Saved', { duration: 1800 });
        } catch (e) {
            console.error('[SettingsModal] failed to save memory toggle', e);
            if (window.Toast) window.Toast.error('Could not save preference');
        }
    }

    document.addEventListener('DOMContentLoaded', () => {
        // Toggle change handlers
        document.querySelectorAll('[data-memory-toggle]').forEach(input => {
            input.addEventListener('change', () => {
                saveMemoryToggle(input.dataset.memoryToggle, input.checked);
            });
        });

        // Manage memory button → open the memory child modal. Markup is
        // rendered globally from _settings_memory_modal.html.
        document.querySelectorAll('[data-action="open-memory-modal"]').forEach(btn => {
            btn.addEventListener('click', () => {
                const memoryModal = document.getElementById('memory-modal');
                if (memoryModal) {
                    memoryModal.setAttribute('aria-hidden', 'false');
                } else {
                    console.warn('[SettingsModal] #memory-modal not in DOM');
                }
            });
        });

        // Backdrop + close button close the memory child modal. The legacy
        // settings_api_keys.html inline JS wires the same handlers, but it
        // doesn't run on /chat pages, so we wire them here for the global
        // path. Setting aria-hidden twice is a no-op.
        const memoryModal = document.getElementById('memory-modal');
        if (memoryModal) {
            memoryModal.addEventListener('click', e => {
                if (e.target === memoryModal) {
                    memoryModal.setAttribute('aria-hidden', 'true');
                }
            });
            const closeBtn = document.getElementById('close-memory-modal');
            if (closeBtn) {
                closeBtn.addEventListener('click', () => {
                    memoryModal.setAttribute('aria-hidden', 'true');
                });
            }
        }
    });

    // Wrap open() so memory toggle state loads each time the modal opens.
    const _origOpen = window.SettingsModal.open.bind(window.SettingsModal);
    window.SettingsModal.open = function(tabId) {
        _origOpen(tabId);
        loadMemoryToggleState();
    };
})();
