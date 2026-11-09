// /chats page — full-feature chat browser.
// State: in-memory + sessionStorage cache.
// Filter/sort/search: all client-side against cached array.
// Mutations: optimistic UI + server roundtrip.

(function () {
    'use strict';

    const CACHE_KEY = 'chats-page-cache';
    const CACHE_TTL_MS = 60_000;

    const state = {
        sessions: [],
        search: '',
        filters: {
            starred: false,
            model: '',
            hasArtifact: false,
            hasAttachment: false,
            dateFrom: '',
            dateTo: '',
        },
        sort: 'recent',
        selected: new Set(),
        loading: false,
        error: null,
    };

    const els = {
        list: document.getElementById('chats-list'),
        empty: document.getElementById('chats-empty'),
        search: document.getElementById('chats-search'),
        toolbar: document.getElementById('chats-toolbar'),
        toggles: document.querySelectorAll('.chats-toggle[data-filter]'),
        dropdowns: document.querySelectorAll('.chats-dropdown[data-dropdown]'),
        bulkToolbar: document.getElementById('chats-bulk-toolbar'),
        bulkCount: document.getElementById('chats-bulk-count'),
        bulkSelectAll: document.getElementById('chats-bulk-select-all'),
        bulkStar: document.getElementById('chats-bulk-star'),
        bulkDelete: document.getElementById('chats-bulk-delete'),
        bulkCancel: document.getElementById('chats-bulk-cancel'),
        emptyClear: document.getElementById('chats-empty-clear'),
        error: document.getElementById('chats-error'),
        errorMsg: document.getElementById('chats-error-msg'),
        errorRetry: document.getElementById('chats-error-retry'),
    };

    /* ─── cache ───────────────────────────────────────────────── */

    function loadCache() {
        try {
            const raw = sessionStorage.getItem(CACHE_KEY);
            if (!raw) return null;
            const obj = JSON.parse(raw);
            if (!obj || typeof obj !== 'object') return null;
            if (Date.now() - obj.cachedAt > CACHE_TTL_MS) return null;
            return obj.sessions || null;
        } catch (_) { return null; }
    }

    function saveCache(sessions) {
        try {
            sessionStorage.setItem(CACHE_KEY, JSON.stringify({
                sessions, cachedAt: Date.now(),
            }));
        } catch (_) { /* quota exceeded — ignore */ }
    }

    function invalidateCache() {
        try { sessionStorage.removeItem(CACHE_KEY); } catch (_) {}
    }

    /* ─── fetch ───────────────────────────────────────────────── */

    async function fetchSessions() {
        // Endpoint is auth-only — guests would just rack up 30s 403 console
        // noise. Bail before issuing the request.
        if (document.body.dataset.guestMode === 'true') return;
        state.loading = true;
        renderSkeleton();
        try {
            const resp = await fetch('/chat/list-metadata', {
                credentials: 'same-origin',
            });
            if (!resp.ok) throw new Error('HTTP ' + resp.status);
            const body = await resp.json();
            state.sessions = body.sessions || [];
            state.error = null;
            saveCache(state.sessions);
        } catch (err) {
            state.error = err.message || 'Failed to load chats';
            state.sessions = [];
        } finally {
            state.loading = false;
            populateModelFilter();
            render();
        }
    }

    /* ─── Model list (populated from sessions) ─────────────────── */
    let availableModels = [];
    function populateModelFilter() {
        // Use the per-session 'models' array (every model the chat has
        // ever used) so the dropdown surfaces every option, not just the
        // latest model of each chat.
        const all = new Set();
        state.sessions.forEach(s => {
            const list = Array.isArray(s.models) && s.models.length
                ? s.models
                : (s.model ? [s.model] : []);
            list.forEach(m => { if (m) all.add(m); });
        });
        availableModels = Array.from(all).sort();
        // If currently-selected model isn't in the list anymore, clear it
        if (state.filters.model && !availableModels.includes(state.filters.model)) {
            state.filters.model = '';
        }
        updateFilterUI();
    }

    /* ─── Sort options ─────────────────────────────────────────── */
    const SORT_OPTIONS = [
        { value: 'recent',         label: 'Most recent' },
        { value: 'oldest',         label: 'Oldest first' },
        { value: 'alpha',          label: 'A → Z' },
        { value: 'msg_count',      label: 'Most messages' },
        { value: 'starred_first',  label: 'Starred first' },
    ];

    /* ─── Date presets ─────────────────────────────────────────── */
    const DATE_PRESETS = [
        { value: 'all',     label: 'Anytime',         range: () => ({ from: '', to: '' }) },
        { value: 'today',   label: 'Today',           range: () => dateRangeOffset(0) },
        { value: '7d',      label: 'Last 7 days',     range: () => dateRangeOffset(7) },
        { value: '30d',     label: 'Last 30 days',    range: () => dateRangeOffset(30) },
        { value: '90d',     label: 'Last 90 days',    range: () => dateRangeOffset(90) },
        { value: 'year',    label: 'This year',       range: () => yearRange() },
        { value: 'custom',  label: 'Custom range…',   range: null },  // sentinel; opens custom inputs
    ];

    function pad(n) { return String(n).padStart(2, '0'); }
    function isoDay(d) { return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`; }
    function dateRangeOffset(days) {
        const now = new Date();
        const from = new Date(now.getFullYear(), now.getMonth(), now.getDate() - days + (days === 0 ? 0 : 0));
        return { from: isoDay(from), to: '' };
    }
    function yearRange() {
        const now = new Date();
        return { from: `${now.getFullYear()}-01-01`, to: '' };
    }

    function activeDatePreset() {
        if (!state.filters.dateFrom && !state.filters.dateTo) return 'all';
        // If both set or 'to' is set without matching a preset shape, treat as custom
        if (state.filters.dateTo) return 'custom';
        for (const p of DATE_PRESETS) {
            if (!p.range) continue;
            const { from, to } = p.range();
            if (from === state.filters.dateFrom && to === state.filters.dateTo) return p.value;
        }
        return 'custom';
    }

    /* ─── filter / sort ───────────────────────────────────────── */

    function getVisibleSessions() {
        const q = state.search.trim().toLowerCase();
        let filtered = state.sessions.filter(s => {
            if (state.filters.starred && !s.starred) return false;
            if (state.filters.model) {
                // Match against the full models list when present (chat may
                // have been used with several models); fall back to the
                // single 'model' field for older payloads.
                const list = Array.isArray(s.models) && s.models.length
                    ? s.models
                    : (s.model ? [s.model] : []);
                if (!list.includes(state.filters.model)) return false;
            }
            // Content-type filters (hasArtifact, hasAttachment) combine as OR.
            // They're alternative kinds of rich content — when both are toggled
            // the user is asking for chats that have ANY rich content, not
            // chats that happen to have BOTH (a much rarer case).
            if (state.filters.hasArtifact || state.filters.hasAttachment) {
                const passesArtifact = state.filters.hasArtifact && s.has_artifact;
                const passesAttachment = state.filters.hasAttachment && s.has_attachment;
                if (!passesArtifact && !passesAttachment) return false;
            }
            if (state.filters.dateFrom && (s.last_updated || '') < state.filters.dateFrom) return false;
            if (state.filters.dateTo && (s.last_updated || '') > state.filters.dateTo + 'T23:59:59') return false;
            if (q && !(s.title || '').toLowerCase().includes(q)) return false;
            return true;
        });
        switch (state.sort) {
            case 'oldest':
                filtered.sort((a, b) => (a.last_updated || '').localeCompare(b.last_updated || ''));
                break;
            case 'alpha':
                filtered.sort((a, b) => (a.title || '').localeCompare(b.title || ''));
                break;
            case 'msg_count':
                filtered.sort((a, b) => (b.msg_count || 0) - (a.msg_count || 0));
                break;
            case 'starred_first':
                filtered.sort((a, b) => {
                    if (a.starred !== b.starred) return a.starred ? -1 : 1;
                    return (b.last_updated || '').localeCompare(a.last_updated || '');
                });
                break;
            case 'recent':
            default:
                filtered.sort((a, b) => (b.last_updated || '').localeCompare(a.last_updated || ''));
        }
        return filtered;
    }

    /* ─── render ──────────────────────────────────────────────── */

    function render() {
        // BAIL OUT if the user is mid-rename — re-rendering would replace
        // the live contenteditable node and silently commit whatever they've
        // typed so far via the blur listener firing on a detached element.
        if (els.list && els.list.querySelector('.chats-row__title.is-editing')) {
            return;
        }
        if (state.error) {
            els.list.innerHTML = '';
            els.empty.hidden = true;
            els.error.hidden = false;
            els.errorMsg.textContent = state.error;
            return;
        }
        els.error.hidden = true;
        const visible = getVisibleSessions();
        if (visible.length === 0) {
            els.list.innerHTML = '';
            els.empty.hidden = false;
            const hasFilters = state.search || state.filters.starred || state.filters.model
                || state.filters.hasArtifact || state.filters.hasAttachment
                || state.filters.dateFrom || state.filters.dateTo;
            const msg = els.empty.querySelector('p');
            const btn = els.empty.querySelector('.chats-empty__clear');
            if (state.sessions.length === 0) {
                msg.textContent = 'No chats yet. Start a new chat to get going.';
                btn.textContent = 'New chat';
                btn.onclick = () => { window.location.href = '/'; };
            } else if (hasFilters) {
                msg.textContent = 'No chats match your filters.';
                btn.textContent = 'Clear filters';
                btn.onclick = null;  // existing emptyClear listener handles this
            } else {
                msg.textContent = 'No chats found.';
            }
            return;
        }
        els.empty.hidden = true;
        els.list.innerHTML = renderGroupedList(visible);
        attachRowListeners();
    }

    /* ─── Premium icons (inline SVG, currentColor, Lucide-style) ─ */
    const ICONS = {
        starFilled: '<svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor"><path d="M12 2.5l3 6.1 6.7 1-4.85 4.7 1.15 6.65L12 17.85 5.95 20.95l1.15-6.65L2.25 9.6 9 8.6z"/></svg>',
        starEmpty: '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><polygon points="12 2.5 15 8.6 21.7 9.6 16.85 14.3 18 20.95 12 17.85 6 20.95 7.15 14.3 2.3 9.6 9 8.6"/></svg>',
        kebab: '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="5" r="1.4"/><circle cx="12" cy="12" r="1.4"/><circle cx="12" cy="19" r="1.4"/></svg>',
        artifact: '<svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><polyline points="16 18 22 12 16 6"/><polyline points="8 6 2 12 8 18"/></svg>',
        attachment: '<svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M21.44 11.05l-9.19 9.19a6 6 0 01-8.49-8.49l9.19-9.19a4 4 0 015.66 5.66l-9.2 9.19a2 2 0 01-2.83-2.83l8.49-8.48"/></svg>',
        message: '<svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a2 2 0 01-2 2H7l-4 4V5a2 2 0 012-2h14a2 2 0 012 2z"/></svg>',
        check: '<svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>',
    };

    function renderSkeleton() {
        els.empty.hidden = true;
        const rows = [];
        for (let i = 0; i < 7; i++) {
            const w = [40, 65, 50, 75, 55, 70, 45][i];
            rows.push(`
                <li class="chats-row is-skeleton">
                    <span class="chats-row__check"></span>
                    <span class="chats-row__star"></span>
                    <span class="chats-row__title" style="width:${w}%"></span>
                    <span class="chats-row__time"></span>
                </li>
            `);
        }
        els.list.innerHTML = rows.join('');
    }

    /* ─── Date bucketing (Today / Yesterday / etc.) ───────────── */
    const BUCKETS = [
        { id: 'today',     label: 'Today' },
        { id: 'yesterday', label: 'Yesterday' },
        { id: 'last7',     label: 'Previous 7 days' },
        { id: 'last30',    label: 'Previous 30 days' },
        { id: 'older',     label: 'Older' },
    ];

    function bucketFor(iso) {
        if (!iso) return 'older';
        const t = new Date(iso).getTime();
        if (isNaN(t)) return 'older';
        const now = new Date();
        const startOfDay = (d) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
        const today = startOfDay(now);
        const yesterday = today - 86400000;
        const week = today - 6 * 86400000;
        const month = today - 29 * 86400000;
        if (t >= today) return 'today';
        if (t >= yesterday) return 'yesterday';
        if (t >= week) return 'last7';
        if (t >= month) return 'last30';
        return 'older';
    }

    function shouldGroupByDate() {
        return state.sort === 'recent' || state.sort === 'oldest';
    }

    function renderGroupedList(sessions) {
        // Pinned/starred section first (only when grouping)
        if (!shouldGroupByDate()) {
            return sessions.map((s, i) => renderRow(s, i)).join('');
        }
        const grouped = { today: [], yesterday: [], last7: [], last30: [], older: [] };
        const pinned = [];
        sessions.forEach(s => {
            if (s.starred) pinned.push(s);
            grouped[bucketFor(s.last_updated)].push(s);
        });
        const html = [];
        let idx = 0;
        if (pinned.length) {
            html.push(renderGroupHeader('Starred', pinned.length, 'starred'));
            html.push(...pinned.map(s => renderRow(s, idx++)));
        }
        BUCKETS.forEach(b => {
            const items = grouped[b.id];
            if (!items.length) return;
            html.push(renderGroupHeader(b.label, items.length, b.id));
            html.push(...items.map(s => renderRow(s, idx++)));
        });
        return html.join('');
    }

    function renderGroupHeader(label, count, id) {
        return `
            <li class="chats-group" data-group="${id}" aria-hidden="true">
                <span class="chats-group__label">${escapeHtml(label)}</span>
                <span class="chats-group__count">${count}</span>
            </li>
        `;
    }

    function renderRow(s, index = 0) {
        const isSelected = state.selected.has(s.session_id);
        const stagger = Math.min(index * 18, 200);
        const indicators = [
            s.has_artifact   ? `<span class="chats-row__indicator" title="Has artifact">${ICONS.artifact}</span>` : '',
            s.has_attachment ? `<span class="chats-row__indicator" title="Has attachment">${ICONS.attachment}</span>` : '',
        ].join('');
        return `
            <li class="chats-row${isSelected ? ' is-selected' : ''}${s.starred ? ' is-pinned' : ''}"
                data-session-id="${escapeHtml(s.session_id)}"
                style="--stagger:${stagger}ms">
                <label class="chats-row__check-wrap" aria-label="Select chat">
                    <input type="checkbox" class="chats-row__check" ${isSelected ? 'checked' : ''}>
                    <span class="chats-row__check-box">${ICONS.check}</span>
                </label>
                <button type="button" class="chats-row__star${s.starred ? ' is-starred' : ''}"
                    aria-label="${s.starred ? 'Unstar' : 'Star'} this chat" aria-pressed="${s.starred}">
                    ${s.starred ? ICONS.starFilled : ICONS.starEmpty}
                </button>
                <span class="chats-row__title" title="${escapeHtml(s.title)}">${escapeHtml(s.title)}</span>
                <span class="chats-row__meta">
                    ${s.model ? `<span class="chats-row__model">${escapeHtml(s.model)}</span>` : ''}
                    <span class="chats-row__msgs">${ICONS.message} ${s.msg_count || 0}</span>
                    ${indicators ? `<span class="chats-row__indicators">${indicators}</span>` : ''}
                </span>
                <span class="chats-row__time">${formatTime(s.last_updated)}</span>
                <span class="chats-row__actions">
                    <button type="button" class="chats-row__kebab" aria-label="More actions">${ICONS.kebab}</button>
                </span>
            </li>
        `;
    }

    function attachRowListeners() {
        // Single delegation listener on the list — survives re-renders
        if (els.list.dataset.bound === '1') return;
        els.list.dataset.bound = '1';

        els.list.addEventListener('click', e => {
            const row = e.target.closest('.chats-row');
            if (!row) return;
            const id = row.dataset.sessionId;
            if (!id) return;

            // Star button
            if (e.target.closest('.chats-row__star')) {
                e.preventDefault();
                e.stopPropagation();
                toggleStar(id);
                return;
            }

            // Checkbox
            if (e.target.matches('.chats-row__check')) {
                e.stopPropagation();
                toggleSelect(id, e.target.checked);
                return;
            }

            // Kebab
            if (e.target.closest('.chats-row__kebab')) {
                e.preventDefault();
                e.stopPropagation();
                openKebabMenu(row, id);
                return;
            }

            // Default: click anywhere in row (except interactive children) → open chat
            if (e.target.closest('.chats-row__check-wrap, .chats-row__star, .chats-row__kebab, .chats-row__menu, .chats-row__title.is-editing')) {
                return;
            }
            window.location.href = '/c/' + encodeURIComponent(id);
        });
    }

    async function toggleStar(sessionId) {
        const s = state.sessions.find(x => x.session_id === sessionId);
        if (!s) return;
        const next = !s.starred;
        s.starred = next;  // optimistic
        saveCache(state.sessions);
        render();
        try {
            const resp = await fetch('/chat/' + encodeURIComponent(sessionId), {
                method: 'PATCH',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': getCsrfToken(),
                },
                credentials: 'same-origin',
                body: JSON.stringify({ starred: next }),
            });
            if (!resp.ok) throw new Error('HTTP ' + resp.status);
        } catch (err) {
            s.starred = !next;  // revert
            saveCache(state.sessions);
            render();
            showToast('Couldn\'t update star. Try again.');
        }
    }

    function toggleSelect(sessionId, isChecked) {
        if (isChecked) state.selected.add(sessionId);
        else state.selected.delete(sessionId);
        updateBulkToolbar();
    }

    function updateBulkToolbar() {
        const count = state.selected.size;
        const chatsPageEl = document.querySelector('.chats-page');
        if (count === 0) {
            els.bulkToolbar.hidden = true;
            chatsPageEl?.classList.remove('is-bulk-active');
            return;
        }
        els.bulkToolbar.hidden = false;
        chatsPageEl?.classList.add('is-bulk-active');
        const visibleIds = getVisibleSessions().map(s => s.session_id);
        const visibleCount = visibleIds.length;
        const allVisibleSelected = visibleCount > 0 && visibleIds.every(id => state.selected.has(id));
        // DOM construction (no innerHTML) — values are integers today, but
        // building this with text nodes makes it impossible to stop being safe
        // if a future caller injects a label string into the count.
        const countText = els.bulkCount.querySelector('.chats-bulk__count-text') || els.bulkCount;
        countText.replaceChildren();
        const full = document.createElement('span');
        full.className = 'chats-bulk__count-full';
        full.textContent = `${count} of ${visibleCount} selected`;
        const short = document.createElement('span');
        short.className = 'chats-bulk__count-short';
        short.setAttribute('aria-hidden', 'true');
        short.textContent = String(count);
        const sep = document.createElement('span');
        sep.className = 'chats-bulk__count-sep';
        sep.textContent = '/';
        short.appendChild(sep);
        short.appendChild(document.createTextNode(String(visibleCount)));
        countText.appendChild(full);
        countText.appendChild(short);
        els.bulkSelectAll.textContent = allVisibleSelected ? 'Deselect all' : 'Select all';
    }

    function bindBulkActions() {
        els.bulkSelectAll.addEventListener('click', () => {
            const visibleIds = getVisibleSessions().map(s => s.session_id);
            const allVisibleSelected = visibleIds.length > 0
                && visibleIds.every(id => state.selected.has(id));
            if (allVisibleSelected) {
                visibleIds.forEach(id => state.selected.delete(id));
            } else {
                visibleIds.forEach(id => state.selected.add(id));
            }
            updateBulkToolbar();
            render();
        });

        // "Clear" in bulk bar — full reset: drops all filters AND deselects everything
        els.bulkCancel.addEventListener('click', () => {
            state.selected.clear();
            document.querySelector('.chats-page').classList.remove('in-select-mode');
            resetAllFilters();  // clears filters, search, then re-renders
            updateBulkToolbar();
        });

        els.bulkStar.addEventListener('click', async () => {
            const ids = Array.from(state.selected);
            if (ids.length === 0) return;
            // Optimistic: star them all
            ids.forEach(id => {
                const s = state.sessions.find(x => x.session_id === id);
                if (s) s.starred = true;
            });
            saveCache(state.sessions);
            render();
            try {
                await Promise.all(ids.map(id => fetch('/chat/' + encodeURIComponent(id), {
                    method: 'PATCH',
                    headers: { 'Content-Type': 'application/json',
                               'X-CSRFToken': getCsrfToken() },
                    credentials: 'same-origin',
                    body: JSON.stringify({ starred: true }),
                }).then(r => { if (!r.ok) throw new Error('HTTP ' + r.status); })));
            } catch (_) {
                showToast('Some sessions could not be starred. Refreshing…');
                fetchSessions();
            }
        });

        els.bulkDelete.addEventListener('click', async () => {
            const ids = Array.from(state.selected);
            if (ids.length === 0) return;
            const confirmed = window.confirm(
                'Delete ' + ids.length + ' chat' + (ids.length === 1 ? '' : 's') + '? This cannot be undone.'
            );
            if (!confirmed) return;
            // Optimistic remove. Snapshot BOTH the rows and the selection
            // so a failure leaves the user looking at the same UI they
            // started from — rows back, checkboxes still ticked, bulk bar
            // still showing the count.
            const removed = state.sessions.filter(s => ids.includes(s.session_id));
            const snapshotSelected = new Set(state.selected);
            state.sessions = state.sessions.filter(s => !ids.includes(s.session_id));
            state.selected.clear();
            updateBulkToolbar();
            saveCache(state.sessions);
            render();
            try {
                const resp = await fetch('/chat/bulk-delete', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json',
                               'X-CSRFToken': getCsrfToken() },
                    credentials: 'same-origin',
                    body: JSON.stringify({ session_ids: ids }),
                });
                if (!resp.ok) throw new Error('HTTP ' + resp.status);
            } catch (_) {
                // Revert: rows AND selection.
                state.sessions = state.sessions.concat(removed);
                state.selected = snapshotSelected;
                saveCache(state.sessions);
                updateBulkToolbar();
                showToast('Bulk delete failed. Restored.');
                render();
            }
        });
    }

    function getCsrfToken() {
        const m = document.querySelector('meta[name="csrf-token"]');
        return m ? m.content : '';
    }

    function showToast(msg) {
        // Minimal toast — uses existing window.showToast if available, else console
        if (typeof window.showToast === 'function') return window.showToast(msg);
        console.warn('[chats]', msg);
    }

    /* ─── kebab menu (rename + single delete) ─────────────────── */

    let openMenu = null;

    function openKebabMenu(row, sessionId) {
        closeKebabMenu();
        const menu = document.createElement('div');
        menu.className = 'chats-row__menu';
        menu.innerHTML = `
            <button type="button" class="chats-row__menu-item" data-action="rename">Rename</button>
            <button type="button" class="chats-row__menu-item chats-row__menu-item--danger" data-action="delete">Delete</button>
        `;
        document.body.appendChild(menu);
        const r = row.querySelector('.chats-row__kebab').getBoundingClientRect();
        menu.style.top = (r.bottom + window.scrollY + 4) + 'px';
        menu.style.left = (r.right + window.scrollX - menu.offsetWidth) + 'px';
        menu.addEventListener('click', e => {
            const btn = e.target.closest('.chats-row__menu-item');
            if (!btn) return;
            const action = btn.dataset.action;
            closeKebabMenu();
            if (action === 'rename') startRename(row, sessionId);
            if (action === 'delete') deleteSingle(sessionId);
        });
        setTimeout(() => document.addEventListener('click', closeKebabMenu, { once: true }), 0);
        openMenu = menu;
    }

    function closeKebabMenu() {
        if (openMenu) { openMenu.remove(); openMenu = null; }
    }

    function startRename(row, sessionId) {
        const titleEl = row.querySelector('.chats-row__title');
        if (!titleEl) return;
        const original = titleEl.textContent;
        titleEl.classList.add('is-editing');
        titleEl.contentEditable = 'true';
        titleEl.focus();
        // Select all text
        const range = document.createRange();
        range.selectNodeContents(titleEl);
        const sel = window.getSelection();
        sel.removeAllRanges();
        sel.addRange(range);

        // Idempotent — Escape calls finish(false) AND triggers blur which
        // would otherwise fire finish(true) and commit anyway.
        let done = false;
        const finish = async (commit) => {
            if (done) return;
            done = true;
            titleEl.contentEditable = 'false';
            titleEl.classList.remove('is-editing');
            const next = (titleEl.textContent || '').trim();
            if (!commit || !next || next === original) {
                titleEl.textContent = original;
                return;
            }
            const s = state.sessions.find(x => x.session_id === sessionId);
            if (s) { s.title = next; saveCache(state.sessions); }
            try {
                const resp = await fetch('/chat/' + encodeURIComponent(sessionId), {
                    method: 'PATCH',
                    headers: { 'Content-Type': 'application/json',
                               'X-CSRFToken': getCsrfToken() },
                    credentials: 'same-origin',
                    body: JSON.stringify({ title: next }),
                });
                if (!resp.ok) throw new Error('HTTP ' + resp.status);
            } catch (_) {
                if (s) { s.title = original; saveCache(state.sessions); render(); }
                showToast('Rename failed.');
            }
        };

        titleEl.addEventListener('blur', () => finish(true), { once: true });
        titleEl.addEventListener('keydown', e => {
            if (e.key === 'Enter') { e.preventDefault(); titleEl.blur(); }
            if (e.key === 'Escape') { e.preventDefault(); finish(false); titleEl.blur(); }
        });
    }

    async function deleteSingle(sessionId) {
        const s = state.sessions.find(x => x.session_id === sessionId);
        if (!s) return;
        const confirmed = window.confirm('Delete "' + s.title + '"? This cannot be undone.');
        if (!confirmed) return;
        state.sessions = state.sessions.filter(x => x.session_id !== sessionId);
        saveCache(state.sessions);
        render();
        try {
            const resp = await fetch('/chat/delete', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json',
                           'X-CSRFToken': getCsrfToken() },
                credentials: 'same-origin',
                body: JSON.stringify({ session_id: sessionId }),
            });
            if (!resp.ok) throw new Error('HTTP ' + resp.status);
        } catch (_) {
            state.sessions = state.sessions.concat([s]);
            saveCache(state.sessions);
            render();
            showToast('Delete failed. Restored.');
        }
    }

    /* ─── helpers ─────────────────────────────────────────────── */

    function escapeHtml(s) {
        return String(s == null ? '' : s)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    }

    function formatTime(iso) {
        if (!iso) return '';
        const then = new Date(iso);
        if (isNaN(then.getTime())) return '';
        const now = new Date();
        const diffMs = now - then;
        const diffH = diffMs / 3600000;
        if (diffH < 1) return Math.max(1, Math.floor(diffMs / 60000)) + 'm';
        if (diffH < 24) return Math.floor(diffH) + 'h';
        const diffD = diffH / 24;
        if (diffD < 7) return Math.floor(diffD) + 'd';
        return then.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
    }

    /* ─── boot ────────────────────────────────────────────────── */

    function bindLongPress() {
        let pressTimer = null;
        let pressTarget = null;
        const LONG_PRESS_MS = 500;

        els.list.addEventListener('touchstart', e => {
            const row = e.target.closest('.chats-row');
            if (!row) return;
            pressTarget = row;
            pressTimer = setTimeout(() => {
                if (pressTarget === row) {
                    document.querySelector('.chats-page').classList.add('in-select-mode');
                    const id = row.dataset.sessionId;
                    if (id) toggleSelect(id, true);
                    row.querySelector('.chats-row__check').checked = true;
                    if (navigator.vibrate) navigator.vibrate(20);
                }
            }, LONG_PRESS_MS);
        }, { passive: true });

        const cancel = () => {
            clearTimeout(pressTimer);
            pressTimer = null;
            pressTarget = null;
        };
        els.list.addEventListener('touchend', cancel, { passive: true });
        els.list.addEventListener('touchmove', cancel, { passive: true });
        els.list.addEventListener('touchcancel', cancel, { passive: true });
    }

    function boot() {
        if (!els.list) return;
        bindControls();
        bindBulkActions();
        bindLongPress();
        bindPullToRefreshGuard();
        const cached = loadCache();
        if (cached) {
            state.sessions = cached;
            populateModelFilter();
            render();
            fetchSessions(); // background refresh
        } else {
            fetchSessions();
        }
    }

    /**
     * Kill the browser's pull-to-refresh on /chats. CSS
     * `overscroll-behavior` is inconsistent on iOS Safari and Chrome
     * Android, so we add an explicit touch guard: if the user starts
     * a vertical drag at the top of the chat-list scroll container
     * (or anywhere outside any scrollable area), we preventDefault on
     * the downward-direction touchmove. The browser never sees a
     * "pull-down-at-top" gesture so it can't trigger a reload.
     */
    function bindPullToRefreshGuard() {
        const scroller = document.getElementById('chats-scroll');
        if (!scroller) return;

        let startY = 0;
        let startScrollTop = 0;
        let armed = false;

        scroller.addEventListener('touchstart', (e) => {
            if (e.touches.length !== 1) { armed = false; return; }
            startY = e.touches[0].clientY;
            startScrollTop = scroller.scrollTop;
            armed = startScrollTop <= 0;
        }, { passive: true });

        scroller.addEventListener('touchmove', (e) => {
            if (!armed || e.touches.length !== 1) return;
            const deltaY = e.touches[0].clientY - startY;
            // Pulling down (positive deltaY) while at scrollTop 0:
            // block the gesture so the browser can't initiate refresh.
            if (deltaY > 0 && scroller.scrollTop <= 0) {
                e.preventDefault();
            }
        }, { passive: false });

        // Document-level safety net: if any touch happens OUTSIDE a
        // scrollable container and would cause a pull-down at the
        // page edge, block it too. Cheap to install and removes the
        // last edge case (e.g., dragging a non-scrollable bit of the
        // chrome at the top of the page).
        if (window.__chatsPullToRefreshDocGuard) return;
        window.__chatsPullToRefreshDocGuard = true;
        let docStartY = 0;
        document.addEventListener('touchstart', (e) => {
            if (e.touches.length === 1) docStartY = e.touches[0].clientY;
        }, { passive: true });
        document.addEventListener('touchmove', (e) => {
            // Only block when on the chats page AND the target isn't
            // inside a scrollable element that's still scrolled away
            // from the top.
            if (!document.body.classList.contains('chats-page-open')) return;
            if (e.touches.length !== 1) return;
            const deltaY = e.touches[0].clientY - docStartY;
            if (deltaY <= 0) return;
            // Walk up from target finding a scrollable ancestor.
            let el = e.target;
            while (el && el !== document.body) {
                const cs = el.scrollHeight > el.clientHeight &&
                    /(auto|scroll)/.test(getComputedStyle(el).overflowY);
                if (cs) {
                    if (el.scrollTop > 0) return; // scrolled inside — let it work
                    break;
                }
                el = el.parentElement;
            }
            e.preventDefault();
        }, { passive: false });
    }

    function bindControls() {
        // Search input (debounced)
        let searchTimer = null;
        els.search.addEventListener('input', e => {
            clearTimeout(searchTimer);
            searchTimer = setTimeout(() => {
                state.search = e.target.value || '';
                updateFilterUI();
                render();
            }, 80);
        });

        // Quick toggle pills (starred, hasArtifact, hasAttachment)
        els.toggles.forEach(btn => {
            btn.addEventListener('click', () => {
                const key = btn.dataset.filter;
                state.filters[key] = !state.filters[key];
                updateFilterUI();
                render();
            });
        });

        // Custom dropdowns (model, date, sort)
        els.dropdowns.forEach(dd => {
            const btn = dd.querySelector('.chats-dropdown__btn');
            btn.addEventListener('click', e => {
                e.stopPropagation();
                toggleDropdown(dd);
            });
        });

        // Outside click closes any open dropdown
        document.addEventListener('click', e => {
            if (!e.target.closest('.chats-dropdown')) closeAllDropdowns();
        });

        // Escape closes
        document.addEventListener('keydown', e => {
            if (e.key === 'Escape') closeAllDropdowns();
        });

        // Empty state clear button
        els.emptyClear.addEventListener('click', () => {
            resetAllFilters();
        });

        els.errorRetry.addEventListener('click', () => {
            state.error = null;
            fetchSessions();
        });

        // Initial UI sync
        updateFilterUI();
    }

    /* ─── Dropdown engine ──────────────────────────────────────── */

    let openDropdownEl = null;

    function toggleDropdown(dd) {
        if (openDropdownEl === dd) { closeAllDropdowns(); return; }
        closeAllDropdowns();
        openDropdown(dd);
    }

    function openDropdown(dd) {
        const name = dd.dataset.dropdown;
        const menu = document.createElement('div');
        menu.className = 'chats-dropdown__menu';
        menu.setAttribute('role', 'listbox');
        menu.innerHTML = renderDropdownMenu(name);
        dd.appendChild(menu);
        dd.classList.add('is-open');
        dd.querySelector('.chats-dropdown__btn').setAttribute('aria-expanded', 'true');

        // Bind item clicks via delegation
        menu.addEventListener('click', e => {
            const item = e.target.closest('.chats-dropdown__item');
            if (item) {
                e.stopPropagation();
                handleDropdownPick(name, item.dataset.value);
                return;
            }
            // Custom date inputs — keep dropdown open
            if (e.target.closest('.chats-date-custom')) {
                e.stopPropagation();
            }
        });

        // Wire custom-date inputs
        if (name === 'date') {
            menu.querySelector('[data-custom-from]')?.addEventListener('change', e => {
                state.filters.dateFrom = e.target.value || '';
                updateFilterUI();
                render();
            });
            menu.querySelector('[data-custom-to]')?.addEventListener('change', e => {
                state.filters.dateTo = e.target.value || '';
                updateFilterUI();
                render();
            });
        }

        openDropdownEl = dd;
    }

    function closeAllDropdowns() {
        document.querySelectorAll('.chats-dropdown.is-open').forEach(dd => {
            dd.classList.remove('is-open');
            dd.querySelector('.chats-dropdown__btn')?.setAttribute('aria-expanded', 'false');
            dd.querySelector('.chats-dropdown__menu')?.remove();
        });
        openDropdownEl = null;
    }

    function renderDropdownMenu(name) {
        if (name === 'sort') {
            return SORT_OPTIONS.map(o => menuItem(o.value, o.label, o.value === state.sort)).join('');
        }
        if (name === 'model') {
            const items = [menuItem('', 'All models', !state.filters.model)];
            availableModels.forEach(m => {
                items.push(menuItem(m, m, state.filters.model === m));
            });
            if (availableModels.length === 0) {
                return `<div class="chats-dropdown__empty">No models yet</div>`;
            }
            return items.join('');
        }
        if (name === 'date') {
            const active = activeDatePreset();
            const presets = DATE_PRESETS.map(p => menuItem(p.value, p.label, p.value === active)).join('');
            const showCustom = active === 'custom';
            const customBlock = `
                <div class="chats-date-custom${showCustom ? ' is-open' : ''}" data-custom-block>
                    <label class="chats-date-custom__field">
                        <span>From</span>
                        <input type="date" data-custom-from value="${escapeHtml(state.filters.dateFrom || '')}">
                    </label>
                    <label class="chats-date-custom__field">
                        <span>To</span>
                        <input type="date" data-custom-to value="${escapeHtml(state.filters.dateTo || '')}">
                    </label>
                </div>
            `;
            return presets + customBlock;
        }
        return '';
    }

    function menuItem(value, label, isSelected) {
        const checkSvg = '<svg class="chats-dropdown__check" viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>';
        return `
            <button type="button" class="chats-dropdown__item${isSelected ? ' is-selected' : ''}"
                role="option" aria-selected="${isSelected}" data-value="${escapeHtml(value)}">
                <span class="chats-dropdown__item-label">${escapeHtml(label)}</span>
                ${isSelected ? checkSvg : ''}
            </button>
        `;
    }

    function handleDropdownPick(name, value) {
        if (name === 'sort') {
            state.sort = value || 'recent';
            closeAllDropdowns();
        } else if (name === 'model') {
            state.filters.model = value || '';
            closeAllDropdowns();
        } else if (name === 'date') {
            const preset = DATE_PRESETS.find(p => p.value === value);
            if (!preset) return;
            if (preset.range) {
                const r = preset.range();
                state.filters.dateFrom = r.from;
                state.filters.dateTo = r.to;
                closeAllDropdowns();
            } else {
                // Custom — re-render the menu to expand custom block, keep open
                state.filters.dateFrom = state.filters.dateFrom || '';
                state.filters.dateTo = state.filters.dateTo || '';
                const menu = openDropdownEl?.querySelector('.chats-dropdown__menu');
                if (menu) menu.innerHTML = renderDropdownMenu('date');
                // Re-wire inputs after re-render
                menu.querySelector('[data-custom-from]')?.addEventListener('change', e => {
                    state.filters.dateFrom = e.target.value || '';
                    updateFilterUI();
                    render();
                });
                menu.querySelector('[data-custom-to]')?.addEventListener('change', e => {
                    state.filters.dateTo = e.target.value || '';
                    updateFilterUI();
                    render();
                });
            }
        }
        updateFilterUI();
        render();
    }

    /* ─── Filter UI sync ───────────────────────────────────────── */

    function updateFilterUI() {
        // Toggles: pressed + active-class for filled state
        els.toggles.forEach(btn => {
            const key = btn.dataset.filter;
            const on = !!state.filters[key];
            btn.setAttribute('aria-pressed', String(on));
            btn.classList.toggle('is-active', on);
        });

        // Dropdown labels
        els.dropdowns.forEach(dd => {
            const name = dd.dataset.dropdown;
            const labelEl = dd.querySelector('.chats-dropdown__label');
            let label = '';
            let active = false;
            if (name === 'model') {
                label = state.filters.model || 'All models';
                active = !!state.filters.model;
            } else if (name === 'date') {
                const p = DATE_PRESETS.find(x => x.value === activeDatePreset());
                if (activeDatePreset() === 'custom') {
                    const f = state.filters.dateFrom;
                    const t = state.filters.dateTo;
                    if (f && t) label = `${formatDateShort(f)} – ${formatDateShort(t)}`;
                    else if (f) label = `From ${formatDateShort(f)}`;
                    else if (t) label = `Until ${formatDateShort(t)}`;
                    else label = 'Custom';
                } else {
                    label = p?.label || 'Anytime';
                }
                active = !!(state.filters.dateFrom || state.filters.dateTo);
            } else if (name === 'sort') {
                const o = SORT_OPTIONS.find(x => x.value === state.sort);
                label = o?.label || 'Most recent';
                active = state.sort !== 'recent';
            }
            if (labelEl) labelEl.textContent = label;
            dd.classList.toggle('is-active', active);
        });

    }

    function activeFilterCount() {
        let n = 0;
        if (state.filters.starred) n++;
        if (state.filters.hasArtifact) n++;
        if (state.filters.hasAttachment) n++;
        if (state.filters.model) n++;
        if (state.filters.dateFrom || state.filters.dateTo) n++;
        if (state.search) n++;
        return n;
    }

    function resetAllFilters() {
        state.search = '';
        state.filters = { starred: false, model: '', hasArtifact: false, hasAttachment: false,
                          dateFrom: '', dateTo: '' };
        els.search.value = '';
        closeAllDropdowns();
        updateFilterUI();
        render();
    }

    function formatDateShort(iso) {
        if (!iso) return '';
        const d = new Date(iso + 'T00:00:00');
        if (isNaN(d.getTime())) return iso;
        return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
    }

    /* ─── background refresh (30s while page is visible) ─────── */
    let refreshTimer = null;
    let isActive = false;  // gates the visibility listener too

    function startBackgroundRefresh() {
        if (isActive) return;
        isActive = true;
        clearInterval(refreshTimer);
        refreshTimer = setInterval(() => {
            if (document.visibilityState === 'visible') fetchSessions();
        }, 30_000);
    }

    function stopBackgroundRefresh() {
        isActive = false;
        clearInterval(refreshTimer);
        refreshTimer = null;
    }

    // The visibility listener stays bound for the page lifetime, but it
    // only acts when the chats surface is currently active — otherwise
    // we'd be hitting /chat/list-metadata every tab focus from any other
    // surface in the app.
    document.addEventListener('visibilitychange', () => {
        if (isActive && document.visibilityState === 'visible') fetchSessions();
    });

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', () => { boot(); startBackgroundRefresh(); }, { once: true });
    } else {
        boot();
        startBackgroundRefresh();
    }

    // Expose for SPA show/hide hooks in startNewChatModule.
    window.__chatsPage = {
        state, render, fetchSessions, invalidateCache,
        start: startBackgroundRefresh,
        stop: stopBackgroundRefresh,
    };
})();
