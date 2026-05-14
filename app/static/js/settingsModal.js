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

            // Notify listeners (e.g. RemindersModule refreshes the Schedules
            // tab data when this fires for tabId='schedules').
            document.dispatchEvent(new CustomEvent('settings-tab-shown', {
                detail: { tab: tabId },
            }));
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
                    // Use only the option's primary text node as the trigger
                    // label, dropping any inline hint spans (e.g. "· af_heart").
                    const labelNode = option.firstChild;
                    const label = labelNode && labelNode.nodeType === Node.TEXT_NODE
                        ? labelNode.textContent.trim()
                        : option.textContent.trim();
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
            if (PERSONALIZATION_DROPDOWN_KEYS.has(key)) return;  // in-memory only
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
        document.querySelectorAll('[data-pref-toggle]').forEach(btn => {
            const key = btn.dataset.prefToggle;
            if (key in prefs) setToggleState(btn, !!prefs[key]);
        });
    }

    function bindToggleClicks() {
        // Three flavors of toggle, all sharing the same click → aria-checked flip.
        // Routing on which dataset attr is present determines persistence:
        //   data-memory-toggle        → POST /user/memory-toggles (server)
        //   data-pref-toggle          → localStorage (PREF_STORAGE_KEY)
        //   data-personalization-toggle → in-memory only (personalizationState)
        document.querySelectorAll('[data-memory-toggle], [data-pref-toggle], [data-personalization-toggle]').forEach(btn => {
            btn.addEventListener('click', () => {
                const newValue = !getToggleState(btn);
                setToggleState(btn, newValue);
                if (btn.dataset.memoryToggle) {
                    saveMemoryToggle(btn.dataset.memoryToggle, newValue);
                } else if (btn.dataset.prefToggle) {
                    setPref(btn.dataset.prefToggle, newValue);
                } else if (btn.dataset.personalizationToggle) {
                    personalizationState[btn.dataset.personalizationToggle] = newValue;
                }
            });
        });
    }

    /* ====================================================================
       Personalization pane — in-memory state (no persistence per spec v1)
       ==================================================================== */

    // Dropdown keys that belong to the Personalization pane. Routing in
    // onDropdownChange treats them as in-memory rather than localStorage.
    const PERSONALIZATION_DROPDOWN_KEYS = new Set([
        'p-base-style', 'p-warm', 'p-enthusiastic', 'p-headers-lists', 'p-emoji'
    ]);

    const personalizationState = {
        'p-base-style': 'default',
        'p-warm': 'default',
        'p-enthusiastic': 'default',
        'p-headers-lists': 'default',
        'p-emoji': 'default',
        'p-fast-answers': true,
        'p-record-history': false,
        'p-web-search': true,
        'p-canvas': true,
        'p-voice': true,
        'p-advanced-voice': true,
        'p-connector-search': true,
        'p-custom-instructions': '',
        'p-nickname': '',
        'p-occupation': '',
        'p-more-about-you': '',
        'p-advanced-open': false
    };

    function bindPersonalizationInputs() {
        document.querySelectorAll('[data-personalization-input]').forEach(input => {
            input.addEventListener('input', () => {
                personalizationState[input.dataset.personalizationInput] = input.value;
            });
        });
    }

    function bindCollapsibles() {
        document.querySelectorAll('[data-collapsible-trigger]').forEach(trigger => {
            trigger.addEventListener('click', () => {
                const expanded = trigger.getAttribute('aria-expanded') === 'true';
                trigger.setAttribute('aria-expanded', expanded ? 'false' : 'true');
                const section = trigger.closest('[data-collapsible]');
                if (section && section.id === 'personalization-advanced') {
                    personalizationState['p-advanced-open'] = !expanded;
                }
            });
        });
    }

    // Expose for tests / debug — read-only snapshot.
    window.__getPersonalizationState = function () {
        return Object.assign({}, personalizationState);
    };

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
        if (PERSONALIZATION_DROPDOWN_KEYS.has(key)) {
            // Personalization pane v1 — in-memory only, no persistence.
            personalizationState[key] = value;
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

    function setToggleState(btn, value) {
        btn.setAttribute('aria-checked', value ? 'true' : 'false');
    }

    function getToggleState(btn) {
        return btn.getAttribute('aria-checked') === 'true';
    }

    async function loadMemoryToggleState() {
        try {
            const resp = await fetch('/user/memory-toggles', { credentials: 'same-origin' });
            if (!resp.ok) return;
            const state = await resp.json();
            document.querySelectorAll('[data-memory-toggle]').forEach(btn => {
                const key = btn.dataset.memoryToggle;
                if (key in state) setToggleState(btn, !!state[key]);
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
       Voice preview — Kokoro TTS via existing /tts/groq endpoint
       (Kokoro local sidecar → Gemini → Groq fallback chain on the server)
       ==================================================================== */

    const PLAY_BTN_DEFAULT_HTML = '<i class="fas fa-play"></i> Play';
    const PLAY_BTN_LOADING_HTML = '<i class="fas fa-spinner fa-spin"></i> Loading';
    const PLAY_BTN_STOP_HTML = '<i class="fas fa-stop"></i> Stop';
    let _activePreviewAudio = null;

    // Per-voice introduction scripts for the curated "Recommended" voices.
    // Key = backend voice id (data-dropdown-option value, raw Kokoro id).
    const VOICE_SAMPLES = {
        'af_heart':  "Hi, I'm Ember. My voice is warm and welcoming, perfect for cozy conversations.",
        'af_sarah':  "Hi, I'm Maple. I'll keep things gentle and patient as we talk.",
        'af_sky':    "Hey, I'm Sol. I bring brightness and a touch of energy to every chat.",
        'am_adam':   "Hi, I'm Vale. I speak with measured precision and steady clarity.",
        'bm_george': "Hello, I'm Arbor. Mature and considered, with a British accent."
    };

    // Lookup tables for generating intro lines for the rest of the 54 voices.
    const KOKORO_LANG_NAMES = {
        a: 'American English', b: 'British English',
        e: 'Spanish', f: 'French',
        h: 'Hindi', i: 'Italian',
        j: 'Japanese', p: 'Portuguese', z: 'Mandarin Chinese'
    };
    const KOKORO_GENDER_NAMES = { f: 'female', m: 'male' };

    const FALLBACK_PREVIEW_TEXT = "Hi, I'm your XelaSphere voice assistant.";

    /**
     * Generate a friendly intro line for any Kokoro voice id (e.g. "bm_lewis"
     * or "zf_xiaoxiao") when no curated sample exists.
     *   "Hi, I'm Lewis. I'm a British English male voice."
     */
    function buildVoiceIntro(voiceId, displayName) {
        const m = /^([abefhijpz])([fm])_(.+)$/.exec(voiceId || '');
        if (!m) return FALLBACK_PREVIEW_TEXT;
        const lang = KOKORO_LANG_NAMES[m[1]] || 'this language';
        const gender = KOKORO_GENDER_NAMES[m[2]] || '';
        const name = (displayName || m[3]).replace(/^./, c => c.toUpperCase());
        return `Hi, I'm ${name}. I'm a ${lang} ${gender} voice.`;
    }

    /**
     * Public: read the user's preferred voice. Used by speechSynthesisModule,
     * voiceModeModule, and any other future TTS caller so that the choice in
     * Settings → General → Voice is the default everywhere.
     */
    window.getPreferredVoice = function getPreferredVoice() {
        try {
            const raw = localStorage.getItem('xelasphere-settings-prefs');
            if (!raw) return 'af_heart';
            const prefs = JSON.parse(raw);
            return prefs.voice || 'af_heart';
        } catch (e) {
            return 'af_heart';
        }
    };

    function getSelectedVoice() {
        return window.getPreferredVoice();
    }

    function resetPlayButton(btn) {
        if (!btn) return;
        btn.disabled = false;
        btn.innerHTML = PLAY_BTN_DEFAULT_HTML;
    }

    async function playVoicePreview(btn) {
        // Click-while-playing toggles to stop (no refetch).
        if (_activePreviewAudio) {
            _activePreviewAudio.pause();
            _activePreviewAudio.src = '';
            _activePreviewAudio = null;
            resetPlayButton(btn);
            return;
        }

        const csrf = document.querySelector('meta[name="csrf-token"]')?.content
                  || document.querySelector('input[name="csrf_token"]')?.value
                  || '';

        if (btn) {
            btn.disabled = true;
            btn.innerHTML = PLAY_BTN_LOADING_HTML;
        }

        try {
            const voice = getSelectedVoice();
            // Find the option to extract its display name for the intro fallback
            const opt = document.querySelector(
                `.settings-dropdown[data-dropdown="voice"] [data-dropdown-option="${voice}"]`
            );
            const displayName = opt
                ? (opt.firstChild ? opt.firstChild.textContent.trim() : opt.textContent.trim())
                : null;
            const sample = VOICE_SAMPLES[voice] || buildVoiceIntro(voice, displayName);
            const resp = await fetch('/tts/groq', {
                method: 'POST',
                credentials: 'same-origin',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': csrf
                },
                body: JSON.stringify({
                    text: sample,
                    voice: voice,
                    lang: 'en'
                })
            });
            if (!resp.ok) {
                if (resp.status === 503) throw new Error('Voice service is busy, try again');
                throw new Error(`Voice preview failed (${resp.status})`);
            }
            const blob = await resp.blob();
            const audioUrl = URL.createObjectURL(blob);
            const audio = new Audio(audioUrl);
            _activePreviewAudio = audio;

            const cleanup = () => {
                URL.revokeObjectURL(audioUrl);
                if (_activePreviewAudio === audio) _activePreviewAudio = null;
                resetPlayButton(btn);
            };
            audio.addEventListener('ended', cleanup);
            audio.addEventListener('error', cleanup);

            if (btn) {
                btn.disabled = false;
                btn.innerHTML = PLAY_BTN_STOP_HTML;
            }
            await audio.play();
        } catch (e) {
            console.error('[SettingsModal] voice preview failed:', e);
            if (window.Toast) window.Toast.error(e.message || 'Voice preview failed');
            resetPlayButton(btn);
        }
    }

    /* ====================================================================
       Delete all chats — exposed as window-global
       ==================================================================== */

    function openDeleteAllChatsModal() {
        const m = document.getElementById('delete-all-chats-confirm-modal');
        if (!m) return;
        m.classList.add('open');
        m.setAttribute('aria-hidden', 'false');
        document.getElementById('cancel-delete-all-chats')?.focus();
    }

    function closeDeleteAllChatsModal() {
        const m = document.getElementById('delete-all-chats-confirm-modal');
        if (!m) return;
        m.classList.remove('open');
        m.setAttribute('aria-hidden', 'true');
    }

    async function performDeleteAllChats() {
        const csrf = document.querySelector('meta[name="csrf-token"]')?.content
                  || document.querySelector('input[name="csrf_token"]')?.value
                  || '';
        const confirmBtn = document.getElementById('confirm-delete-all-chats');
        const tileBtn = document.querySelector('[data-action="delete-all-chats"]');
        const originalConfirmLabel = confirmBtn ? confirmBtn.textContent : 'Delete all';
        const originalTileLabel = tileBtn ? tileBtn.textContent : 'Delete all';
        if (confirmBtn) { confirmBtn.disabled = true; confirmBtn.textContent = 'Deleting...'; }
        if (tileBtn) { tileBtn.disabled = true; tileBtn.textContent = 'Deleting...'; }
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
            closeDeleteAllChatsModal();
            window.setTimeout(() => window.location.reload(), 800);
        } catch (e) {
            console.error('[SettingsModal] deleteAllChats failed:', e);
            if (window.Toast) {
                window.Toast.error('Failed to delete chats: ' + e.message);
            }
            if (confirmBtn) { confirmBtn.disabled = false; confirmBtn.textContent = originalConfirmLabel; }
            if (tileBtn) { tileBtn.disabled = false; tileBtn.textContent = originalTileLabel; }
        }
    }

    // Preserved global entry point — opens the themed confirm modal
    // (replaces the old window.confirm() native dialog).
    window.deleteAllChats = openDeleteAllChatsModal;

    /* ====================================================================
       Wire all pane interactions on DOMContentLoaded
       ==================================================================== */

    document.addEventListener('DOMContentLoaded', () => {
        bindDropdowns();
        bindToggleClicks();
        bindPersonalizationInputs();
        bindCollapsibles();

        // Memory child modal — owned by memoryModalController.js, which wires
        // open/close, list load, Forget Me, tabs, and dream consolidation.
        // Both surfaces (#manage-memory-btn on /account/manage AND
        // [data-action="open-memory-modal"] in this modal's Personalization
        // pane) are bound there. We just need it loaded before this script.

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

        // Voice preview play — call /tts/groq with the selected voice and a
        // short sample sentence, then play the returned audio.
        document.querySelectorAll('[data-action="play-voice-preview"]').forEach(btn => {
            btn.addEventListener('click', () => playVoicePreview(btn));
        });

        // Delete all chats — opens the themed confirm modal
        document.querySelectorAll('[data-action="delete-all-chats"]').forEach(btn => {
            btn.addEventListener('click', () => window.deleteAllChats());
        });

        // Themed confirm modal wiring (Cancel / Close / backdrop / Confirm / Escape)
        const deleteAllChatsModal = document.getElementById('delete-all-chats-confirm-modal');
        document.getElementById('cancel-delete-all-chats')?.addEventListener('click', closeDeleteAllChatsModal);
        document.getElementById('close-delete-all-chats-modal')?.addEventListener('click', closeDeleteAllChatsModal);
        document.getElementById('confirm-delete-all-chats')?.addEventListener('click', performDeleteAllChats);
        if (deleteAllChatsModal) {
            deleteAllChatsModal.addEventListener('click', (e) => {
                if (e.target === deleteAllChatsModal) closeDeleteAllChatsModal();
            });
        }
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape' && deleteAllChatsModal?.classList.contains('open')) {
                closeDeleteAllChatsModal();
            }
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
