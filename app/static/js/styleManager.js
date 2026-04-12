/**
 * Writing-Style Manager
 * ─────────────────────
 * - Persists the active style across reloads (localStorage).
 * - Renders a small pill above the composer when a style is active;
 *   click × to clear back to "Normal".
 * - "Create & edit styles" opens a tiny modal to add a custom style,
 *   which is then injected into the submenu.
 *
 * Depends on markup inside index.html:
 *   #submenu-styles (contains .action-style-opt items + #action-create-style)
 *   window.chatManager._activeStyle (read by codeWindow.sendMessage)
 *   .input-container (we inject the chip into this)
 */
(() => {
    const LS_ACTIVE = 'xs_active_style';
    const LS_CUSTOM = 'xs_custom_styles';
    const BUILTIN_STYLES = ['Normal', 'Learning', 'Concise', 'Explanatory', 'Formal'];

    // Built-in instruction prompts — MUST stay in sync with codeWindow.js
    const BUILTIN_INSTRUCTIONS = {
        'Learning':    'Respond in a teaching style: explain concepts clearly with examples, check for understanding, and build up from fundamentals.',
        'Concise':     'Respond concisely. Be brief and direct. No filler, no restating the question, no wrap-up sentences.',
        'Explanatory': 'Respond with thorough explanation: context, reasoning, step-by-step detail, and illustrative examples.',
        'Formal':      'Respond in a formal, professional tone. Avoid contractions and casual phrasing.'
    };

    // ── Storage helpers ──
    function loadActive() {
        try { return localStorage.getItem(LS_ACTIVE) || 'Normal'; }
        catch (e) { return 'Normal'; }
    }
    function saveActive(style) {
        try { localStorage.setItem(LS_ACTIVE, style); } catch (e) {}
    }
    function loadCustom() {
        try {
            const raw = localStorage.getItem(LS_CUSTOM);
            const arr = raw ? JSON.parse(raw) : [];
            return Array.isArray(arr) ? arr : [];
        } catch (e) { return []; }
    }
    function saveCustom(list) {
        try { localStorage.setItem(LS_CUSTOM, JSON.stringify(list)); } catch (e) {}
    }

    // ── Active instruction lookup (used by send path) ──
    // Expose a getter so codeWindow can read both builtin + custom instructions.
    window.__getStyleInstruction = function (styleName) {
        if (!styleName || styleName === 'Normal') return null;
        if (BUILTIN_INSTRUCTIONS[styleName]) return BUILTIN_INSTRUCTIONS[styleName];
        const custom = loadCustom().find(s => s.name === styleName);
        return custom ? custom.instruction : null;
    };

    // ── Placeholder continuation ──
    // Instead of a floating chip above the composer, we append the active
    // style to the input's placeholder (e.g. "Ask anything · Concise").
    // Normal = no modification.
    let _originalPlaceholder = null;

    function getInput() {
        return document.getElementById('prompt');
    }

    function updatePlaceholder(style) {
        const input = getInput();
        if (!input) return;
        if (_originalPlaceholder === null) {
            _originalPlaceholder = input.getAttribute('placeholder') || '';
        }
        if (!style || style === 'Normal') {
            input.setAttribute('placeholder', _originalPlaceholder);
            return;
        }
        input.setAttribute('placeholder', `${_originalPlaceholder} · ${style}`);
    }

    // Remove any stale chip element from older builds (safe no-op otherwise).
    function removeLegacyChip() {
        const chip = document.getElementById('active-style-chip');
        if (chip) chip.remove();
    }

    // Fully close the + action sheet so the toggle icon reverts from × to +.
    // The + / × swap is driven by `.actions-menu.open` class set in script.js —
    // we need to mirror its cleanup steps here.
    function closeActionsFully() {
        const modal = document.getElementById('action-sheet-modal');
        const back = document.getElementById('action-sheet-backdrop');
        const actionsMenu = document.getElementById('actions-menu');
        const actionsToggle = document.getElementById('actions-toggle');
        if (modal) modal.style.display = 'none';
        if (back) back.style.display = 'none';
        if (actionsMenu) actionsMenu.classList.remove('open');
        if (actionsToggle) actionsToggle.setAttribute('aria-expanded', 'false');
        document.querySelectorAll('[data-submenu]').forEach(el => {
            const sub = document.getElementById(el.dataset.submenu);
            if (sub) sub.style.display = 'none';
            el.style.background = '';
            el.style.borderRadius = '';
            el.style.margin = '';
        });
    }

    // ── Sync submenu visual state ──
    function syncSubmenuHighlight(style) {
        document.querySelectorAll('.action-style-opt').forEach(opt => {
            const isActive = opt.dataset.style === style;
            opt.style.background = isActive ? '#353331' : '';
            opt.style.borderRadius = isActive ? '8px' : '';
            opt.style.margin = isActive ? '2px 4px' : '';
            const chk = opt.querySelector('.style-check');
            if (chk) chk.style.display = isActive ? 'flex' : 'none';
        });
    }

    // ── The single source of truth for changing the active style ──
    function setActiveStyle(style) {
        if (!style) style = 'Normal';
        if (window.chatManager) window.chatManager._activeStyle = style;
        saveActive(style);
        syncSubmenuHighlight(style);
        updatePlaceholder(style);
    }

    // ── Render custom styles into the submenu ──
    function renderCustomStyles() {
        const submenu = document.getElementById('submenu-styles');
        if (!submenu) return;
        // Remove previously-rendered custom rows
        submenu.querySelectorAll('[data-custom="true"]').forEach(el => el.remove());

        const customs = loadCustom();
        const createBtn = document.getElementById('action-create-style');
        if (!createBtn) return;

        // Insert a divider before "Create & edit styles" if we have customs
        if (customs.length) {
            const div = document.createElement('div');
            div.setAttribute('data-custom', 'true');
            div.style.cssText = 'height:0.5px;background:rgba(255,255,255,0.07);margin:3px 0';
            createBtn.parentNode.insertBefore(div, createBtn);
        }

        customs.forEach(c => {
            const row = document.createElement('div');
            row.className = 'asi action-style-opt';
            row.dataset.style = c.name;
            row.setAttribute('data-custom', 'true');
            row.style.cssText = 'display:flex;align-items:center;gap:11px;padding:9px 12px;cursor:pointer;color:rgba(255,255,255,0.88);font-size:13.5px;user-select:none';
            row.innerHTML = `
                <span style="opacity:0.75;display:flex">
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M12 20h9"/><path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z"/></svg>
                </span>
                <span style="flex:1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${escapeHtml(c.name)}</span>
                <button class="style-edit-btn" data-edit="${escapeHtml(c.name)}" type="button" aria-label="Edit ${escapeHtml(c.name)}" title="Edit"
                    style="background:transparent;border:none;padding:2px 4px;color:rgba(255,255,255,0.45);cursor:pointer;display:flex;align-items:center">
                    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M12 20h9"/><path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z"/></svg>
                </button>
                <span class="style-check" style="color:#378ADD;display:none;align-items:center;flex-shrink:0">
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6L9 17l-5-5"/></svg>
                </span>
            `;
            createBtn.parentNode.insertBefore(row, createBtn);

            row.addEventListener('click', (e) => {
                if (e.target.closest('.style-edit-btn')) return;
                setActiveStyle(c.name);
                closeActionsFully();
            });

            row.querySelector('.style-edit-btn').addEventListener('click', (e) => {
                e.stopPropagation();
                openStyleModal(c.name);
            });
        });
    }

    function escapeHtml(s) {
        return String(s).replace(/[&<>"']/g, ch => ({
            '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
        })[ch]);
    }

    // ── Create / edit modal ──
    function openStyleModal(editingName) {
        // Close any open submenu/actions
        document.querySelectorAll('[id^="submenu-"]').forEach(s => s.style.display = 'none');
        const actionSheet = document.getElementById('action-sheet-modal');
        const actionBackdrop = document.getElementById('action-sheet-backdrop');
        if (actionSheet) actionSheet.style.display = 'none';
        if (actionBackdrop) actionBackdrop.style.display = 'none';

        const existing = editingName
            ? loadCustom().find(c => c.name === editingName)
            : null;

        const overlay = document.createElement('div');
        overlay.className = 'modal-overlay';
        overlay.innerHTML = `
            <div class="modal-content" style="width:380px;max-width:calc(100% - 40px)">
                <h2>${existing ? 'Edit style' : 'Create style'}</h2>
                <p style="padding: 0 20px 14px">Give your style a name and tell the model how to write.</p>
                <input type="text" class="rename-modal-input style-name-input" placeholder="Name (e.g. Friendly, Academic)" maxlength="40" value="${existing ? escapeHtml(existing.name) : ''}">
                <textarea class="rename-modal-input style-instruction-input" placeholder="Describe the style. Example: Respond with warmth and light humor. Keep answers under 3 short paragraphs." maxlength="600" rows="4" style="resize:vertical;min-height:96px;font-family:inherit;line-height:1.45">${existing ? escapeHtml(existing.instruction) : ''}</textarea>
                <div class="modal-actions" style="justify-content:space-between">
                    ${existing ? `<button type="button" class="btn-cancel style-delete-btn" style="color:rgba(237,75,61,0.85)">Delete</button>` : '<span></span>'}
                    <div style="display:flex;gap:8px">
                        <button type="button" class="btn-cancel style-cancel-btn">Cancel</button>
                        <button type="button" class="btn-save style-save-btn">Save</button>
                    </div>
                </div>
            </div>
        `;
        document.body.appendChild(overlay);
        const nameInput = overlay.querySelector('.style-name-input');
        const instrInput = overlay.querySelector('.style-instruction-input');
        nameInput.focus();
        nameInput.select();

        function close() { overlay.remove(); }

        overlay.addEventListener('click', (e) => { if (e.target === overlay) close(); });
        overlay.querySelector('.style-cancel-btn').addEventListener('click', close);

        overlay.querySelector('.style-save-btn').addEventListener('click', () => {
            const name = nameInput.value.trim();
            const instruction = instrInput.value.trim();
            if (!name) { nameInput.focus(); return; }
            if (!instruction) { instrInput.focus(); return; }
            if (BUILTIN_STYLES.includes(name)) {
                alert(`"${name}" is a built-in style name. Pick a different name.`);
                nameInput.focus(); return;
            }
            const list = loadCustom();
            if (existing) {
                const idx = list.findIndex(c => c.name === existing.name);
                if (idx >= 0) list[idx] = { name, instruction };
                // if rename changed the active-style reference, migrate it
                if (loadActive() === existing.name) saveActive(name);
            } else {
                if (list.some(c => c.name === name)) {
                    alert(`You already have a style named "${name}".`);
                    nameInput.focus(); return;
                }
                list.push({ name, instruction });
            }
            saveCustom(list);
            renderCustomStyles();
            if (existing && loadActive() === existing.name) setActiveStyle(name);
            close();
        });

        const delBtn = overlay.querySelector('.style-delete-btn');
        if (delBtn && existing) {
            delBtn.addEventListener('click', () => {
                const list = loadCustom().filter(c => c.name !== existing.name);
                saveCustom(list);
                if (loadActive() === existing.name) setActiveStyle('Normal');
                renderCustomStyles();
                close();
            });
        }

        document.addEventListener('keydown', function esc(e) {
            if (e.key === 'Escape') {
                close();
                document.removeEventListener('keydown', esc);
            }
        });
    }

    // ── Wire up built-in style clicks (replace cosmetic-only handler) ──
    function wireBuiltinStyleClicks() {
        document.querySelectorAll('.action-style-opt:not([data-custom="true"])').forEach(btn => {
            // Clone to detach any previous listener set by script.js
            const clone = btn.cloneNode(true);
            btn.parentNode.replaceChild(clone, btn);
            clone.addEventListener('click', () => {
                setActiveStyle(clone.dataset.style);
                closeActionsFully();
            });
        });
    }

    // ── Wire up "Create & edit styles" ──
    function wireCreateEdit() {
        const createBtn = document.getElementById('action-create-style');
        if (!createBtn) return;
        const clone = createBtn.cloneNode(true);
        createBtn.parentNode.replaceChild(clone, createBtn);
        clone.addEventListener('click', () => openStyleModal(null));
    }

    // ── Boot (slight delay so script.js finishes its DOMContentLoaded) ──
    function boot() {
        removeLegacyChip();
        renderCustomStyles();
        wireBuiltinStyleClicks();
        wireCreateEdit();
        const active = loadActive();
        setActiveStyle(active);
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', () => setTimeout(boot, 0));
    } else {
        setTimeout(boot, 0);
    }
})();
