/**
 * SettingsModal — singleton controller for the global settings modal.
 * ChatGPT-parity v2: top-left close, 240px grey rail with 9 sections,
 * General pane with MFA promo card + dropdowns + toggles.
 */
(function() {
    'use strict';

    const VALID_TABS = new Set([
        'general', 'notifications', 'personalization', 'apps',
        'schedules', 'data', 'security', 'parental', 'account', 'api-keys'
    ]);
    const DEFAULT_TAB = 'general';

    const PREF_STORAGE_KEY = 'xelasphere-settings-prefs';
    const MFA_DISMISSED_KEY = 'xelasphere-settings-mfa-dismissed';
    const THEME_STORAGE_KEY = 'xelasphere-theme';

    /* ====================================================================
       Modal controller
       ==================================================================== */

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

            this._railItems.forEach(btn => {
                btn.addEventListener('click', () => this.setTab(btn.dataset.tab));
            });

            this._modal.querySelectorAll('[data-settings-close]').forEach(btn => {
                btn.addEventListener('click', () => this.close());
            });

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

            this._boundEsc = e => {
                if (e.key === 'Escape') {
                    e.stopPropagation();
                    this.close();
                }
            };
            document.addEventListener('keydown', this._boundEsc);

            const activeRailItem = this._modal.querySelector('.settings-rail-item.is-active');
            if (activeRailItem) activeRailItem.focus();

            // Refresh local state for all pane controls
            loadMemoryToggleState();
            refreshThemeDropdown();
            refreshPrefDropdowns();
            refreshPrefToggles();
            refreshMfaCard();
        }

        close() {
            if (!this._modal) return;
            this._modal.setAttribute('aria-hidden', 'true');
            document.body.classList.remove('settings-modal-open');
            closeAllDropdowns();

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

    /* ====================================================================
       Sidebar trigger hijack
       ==================================================================== */

    document.addEventListener('DOMContentLoaded', () => {
        document.querySelectorAll('[data-settings-trigger]').forEach(el => {
            el.addEventListener('click', e => {
                e.preventDefault();
                window.SettingsModal.open(el.dataset.settingsTrigger || undefined);
            });
        });
    });

    /* ====================================================================
       Dropdown popover behavior
       ==================================================================== */

    function closeAllDropdowns() {
        document.querySelectorAll('.settings-dropdown.is-open').forEach(d => {
            d.classList.remove('is-open');
        });
    }

    function bindDropdowns() {
        document.querySelectorAll('.settings-dropdown').forEach(dropdown => {
            const trigger = dropdown.querySelector('[data-dropdown-trigger]');
            const menu = dropdown.querySelector('[data-dropdown-menu]');
            if (!trigger || !menu) return;

            trigger.addEventListener('click', e => {
                e.stopPropagation();
                const wasOpen = dropdown.classList.contains('is-open');
                closeAllDropdowns();
                if (!wasOpen) dropdown.classList.add('is-open');
            });

            menu.querySelectorAll('[data-dropdown-option]').forEach(option => {
                option.addEventListener('click', e => {
                    e.stopPropagation();
                    const value = option.dataset.dropdownOption;
                    const label = option.textContent.trim();
                    setDropdownValue(dropdown, value, label);
                    onDropdownChange(dropdown.dataset.dropdown, value, label);
                    dropdown.classList.remove('is-open');
                });
            });
        });

        // Click anywhere outside an open dropdown closes it
        document.addEventListener('click', e => {
            if (!e.target.closest('.settings-dropdown')) closeAllDropdowns();
        });
    }

    function setDropdownValue(dropdown, value, label) {
        const labelEl = dropdown.querySelector('[data-dropdown-label]');
        if (labelEl) labelEl.textContent = label;
        dropdown.querySelectorAll('[data-dropdown-option]').forEach(opt => {
            opt.classList.toggle('is-selected', opt.dataset.dropdownOption === value);
        });
    }

    /* ====================================================================
       Preferences (localStorage-backed, no backend)
       ==================================================================== */

    function loadPrefs() {
        try {
            const raw = localStorage.getItem(PREF_STORAGE_KEY);
            return raw ? JSON.parse(raw) : {};
        } catch (e) {
            return {};
        }
    }

    function savePrefs(prefs) {
        try {
            localStorage.setItem(PREF_STORAGE_KEY, JSON.stringify(prefs));
        } catch (e) {}
    }

    function setPref(key, value) {
        const prefs = loadPrefs();
        prefs[key] = value;
        savePrefs(prefs);
    }

    function refreshPrefDropdowns() {
        const prefs = loadPrefs();
        document.querySelectorAll('.settings-dropdown[data-dropdown]').forEach(dropdown => {
            const key = dropdown.dataset.dropdown;
            if (key === 'appearance') return;  // handled separately by theme
            const stored = prefs[key];
            if (!stored) return;
            const option = dropdown.querySelector(`[data-dropdown-option="${stored}"]`);
            if (option) {
                setDropdownValue(dropdown, stored, option.textContent.trim());
            }
        });
    }

    function refreshPrefToggles() {
        const prefs = loadPrefs();
        document.querySelectorAll('[data-pref-toggle]').forEach(input => {
            const key = input.dataset.prefToggle;
            if (key in prefs) input.checked = !!prefs[key];
        });
    }

    function bindPrefToggles() {
        document.querySelectorAll('[data-pref-toggle]').forEach(input => {
            input.addEventListener('change', () => {
                setPref(input.dataset.prefToggle, input.checked);
            });
        });
    }

    /* ====================================================================
       Theme dropdown — System / Dark / Light
       ==================================================================== */

    function getStoredTheme() {
        try {
            return localStorage.getItem(THEME_STORAGE_KEY);
        } catch (e) {
            return null;
        }
    }

    function getSystemTheme() {
        if (window.matchMedia && window.matchMedia('(prefers-color-scheme: light)').matches) {
            return 'light';
        }
        return 'dark';
    }

    function applyTheme(option) {
        if (option === 'system') {
            const osTheme = getSystemTheme();
            if (window.ThemeManager && typeof window.ThemeManager.setTheme === 'function') {
                window.ThemeManager.setTheme(osTheme, true);
            } else {
                document.documentElement.setAttribute('data-theme', osTheme);
            }
            try { localStorage.removeItem(THEME_STORAGE_KEY); } catch (e) {}
        } else if (option === 'light' || option === 'dark') {
            if (window.ThemeManager && typeof window.ThemeManager.setTheme === 'function') {
                window.ThemeManager.setTheme(option, true);
            } else {
                document.documentElement.setAttribute('data-theme', option);
            }
        }
    }

    function refreshThemeDropdown() {
        const stored = getStoredTheme();
        const active = (stored === 'light' || stored === 'dark') ? stored : 'system';
        const dropdown = document.querySelector('.settings-dropdown[data-dropdown="appearance"]');
        if (!dropdown) return;
        const option = dropdown.querySelector(`[data-dropdown-option="${active}"]`);
        if (option) {
            setDropdownValue(dropdown, active, option.textContent.trim());
        }
    }

    /* ====================================================================
       Dropdown change router
       ==================================================================== */

    function onDropdownChange(key, value, label) {
        if (key === 'appearance') {
            applyTheme(value);
            return;
        }
        // Other dropdowns persist locally; no backend in V1
        setPref(key, value);
    }

    /* ====================================================================
       MFA promo card
       ==================================================================== */

    function refreshMfaCard() {
        const card = document.getElementById('settings-mfa-card');
        if (!card) return;
        let dismissed = false;
        try {
            dismissed = localStorage.getItem(MFA_DISMISSED_KEY) === 'true';
        } catch (e) {}
        card.setAttribute('aria-hidden', dismissed ? 'true' : 'false');
    }

    function dismissMfaCard() {
        const card = document.getElementById('settings-mfa-card');
        if (card) card.setAttribute('aria-hidden', 'true');
        try { localStorage.setItem(MFA_DISMISSED_KEY, 'true'); } catch (e) {}
    }

    /* ====================================================================
       Memory toggles + Manage memory child modal
       ==================================================================== */

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

    /* ====================================================================
       Delete all chats — exposed as window-global
       ==================================================================== */

    window.deleteAllChats = async function deleteAllChats() {
        const ok = window.confirm('Delete all chats? This cannot be undone.');
        if (!ok) return;
        const csrf = document.querySelector('meta[name="csrf-token"]')?.content
                  || document.querySelector('input[name="csrf_token"]')?.value
                  || '';
        try {
            const resp = await fetch('/chat/delete-all', {
                method: 'POST',
                credentials: 'same-origin',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': csrf
                }
            });
            const payload = await resp.json().catch(() => ({}));
            if (!resp.ok) throw new Error(payload.error || `status ${resp.status}`);
            if (window.Toast) {
                window.Toast.success(payload.message || 'All chats deleted');
            }
            window.setTimeout(() => window.location.reload(), 800);
        } catch (e) {
            console.error('[SettingsModal] deleteAllChats failed:', e);
            if (window.Toast) {
                window.Toast.error('Failed to delete chats: ' + e.message);
            }
        }
    };

    /* ====================================================================
       Wire all pane interactions on DOMContentLoaded
       ==================================================================== */

    document.addEventListener('DOMContentLoaded', () => {
        bindDropdowns();
        bindPrefToggles();

        // Memory toggles
        document.querySelectorAll('[data-memory-toggle]').forEach(input => {
            input.addEventListener('change', () => {
                saveMemoryToggle(input.dataset.memoryToggle, input.checked);
            });
        });

        // Manage memory button
        document.querySelectorAll('[data-action="open-memory-modal"]').forEach(btn => {
            btn.addEventListener('click', () => {
                const memoryModal = document.getElementById('memory-modal');
                if (memoryModal) {
                    memoryModal.setAttribute('aria-hidden', 'false');
                }
            });
        });

        // Memory child modal close (backdrop + X)
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

        // MFA card dismiss
        document.querySelectorAll('[data-action="dismiss-mfa"]').forEach(btn => {
            btn.addEventListener('click', dismissMfaCard);
        });

        // MFA setup CTA (no-op for now)
        document.querySelectorAll('[data-action="setup-mfa"]').forEach(btn => {
            btn.addEventListener('click', () => {
                console.log('[SettingsModal] MFA setup not wired yet');
                if (window.Toast) window.Toast.info('MFA setup coming soon');
            });
        });

        // Voice preview play (no-op for now)
        document.querySelectorAll('[data-action="play-voice-preview"]').forEach(btn => {
            btn.addEventListener('click', () => {
                console.log('[SettingsModal] play voice preview');
            });
        });

        // Delete all chats
        document.querySelectorAll('[data-action="delete-all-chats"]').forEach(btn => {
            btn.addEventListener('click', () => window.deleteAllChats());
        });

        // Logout
        document.querySelectorAll('[data-action="logout"]').forEach(btn => {
            btn.addEventListener('click', () => {
                const form = document.getElementById('logout-form');
                if (form) form.submit();
            });
        });
    });
})();
