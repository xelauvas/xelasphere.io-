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
        filterModel: document.getElementById('chats-filter-model'),
        filterChips: document.querySelectorAll('.chats-chip[data-filter]'),
        sort: document.getElementById('chats-sort'),
        bulkToolbar: document.getElementById('chats-bulk-toolbar'),
        bulkCount: document.getElementById('chats-bulk-count'),
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

    function populateModelFilter() {
        const models = Array.from(new Set(
            state.sessions.map(s => s.model).filter(Boolean)
        )).sort();
        const current = els.filterModel.value;
        els.filterModel.innerHTML = '<option value="">All models</option>' +
            models.map(m => `<option value="${escapeHtml(m)}">${escapeHtml(m)}</option>`).join('');
        if (models.includes(current)) els.filterModel.value = current;
    }

    /* ─── filter / sort ───────────────────────────────────────── */

    function getVisibleSessions() {
        const q = state.search.trim().toLowerCase();
        let filtered = state.sessions.filter(s => {
            if (state.filters.starred && !s.starred) return false;
            if (state.filters.model && s.model !== state.filters.model) return false;
            if (state.filters.hasArtifact && !s.has_artifact) return false;
            if (state.filters.hasAttachment && !s.has_attachment) return false;
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
                || state.filters.hasArtifact || state.filters.hasAttachment;
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
        els.list.innerHTML = visible.map(renderRow).join('');
        attachRowListeners();
    }

    function renderSkeleton() {
        els.empty.hidden = true;
        const skeletonCount = 6;
        const rows = [];
        for (let i = 0; i < skeletonCount; i++) {
            rows.push(`
                <li class="chats-row is-skeleton">
                    <span class="chats-row__check"></span>
                    <span class="chats-row__star">★</span>
                    <span class="chats-row__title">Loading title placeholder text</span>
                    <span class="chats-row__meta">model · 0 msgs</span>
                    <span class="chats-row__time">—</span>
                </li>
            `);
        }
        els.list.innerHTML = rows.join('');
    }

    function renderRow(s) {
        const isSelected = state.selected.has(s.session_id);
        return `
            <li class="chats-row${isSelected ? ' is-selected' : ''}" data-session-id="${escapeHtml(s.session_id)}">
                <input type="checkbox" class="chats-row__check" aria-label="Select chat"
                    ${isSelected ? 'checked' : ''}>
                <button type="button" class="chats-row__star${s.starred ? ' is-starred' : ''}"
                    aria-label="${s.starred ? 'Unstar' : 'Star'} this chat" aria-pressed="${s.starred}">
                    ${s.starred ? '★' : '☆'}
                </button>
                <span class="chats-row__title">${escapeHtml(s.title)}</span>
                <span class="chats-row__meta">
                    <span class="chats-row__model">${escapeHtml(s.model || '')}</span>
                    <span class="chats-row__msgs">${s.msg_count || 0} msgs</span>
                    <span class="chats-row__indicators">
                        ${s.has_artifact ? '<span class="chats-row__indicator" title="Has artifact">◆</span>' : ''}
                        ${s.has_attachment ? '<span class="chats-row__indicator" title="Has attachment">📎</span>' : ''}
                    </span>
                </span>
                <span class="chats-row__time">${formatTime(s.last_updated)}</span>
                <span class="chats-row__actions">
                    <button type="button" class="chats-row__kebab" aria-label="More actions">⋯</button>
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

            // Default: click row → open chat
            if (e.target.closest('.chats-row__title') || e.target === row) {
                window.location.href = '/c/' + encodeURIComponent(id);
            }
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
        if (count === 0) {
            els.bulkToolbar.hidden = true;
        } else {
            els.bulkToolbar.hidden = false;
            els.bulkCount.textContent = count + (count === 1 ? ' selected' : ' selected');
        }
    }

    function bindBulkActions() {
        els.bulkCancel.addEventListener('click', () => {
            state.selected.clear();
            updateBulkToolbar();
            render();
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
            // Optimistic remove
            const removed = state.sessions.filter(s => ids.includes(s.session_id));
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
                // Revert
                state.sessions = state.sessions.concat(removed);
                saveCache(state.sessions);
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

        const finish = async (commit) => {
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

    function boot() {
        bindControls();
        bindBulkActions();
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

    function bindControls() {
        let searchTimer = null;
        els.search.addEventListener('input', e => {
            clearTimeout(searchTimer);
            searchTimer = setTimeout(() => {
                state.search = e.target.value || '';
                render();
            }, 80);
        });

        els.filterChips.forEach(chip => {
            chip.addEventListener('click', () => {
                const key = chip.dataset.filter;
                state.filters[key] = !state.filters[key];
                chip.setAttribute('aria-pressed', String(state.filters[key]));
                render();
            });
        });

        els.filterModel.addEventListener('change', e => {
            state.filters.model = e.target.value || '';
            render();
        });

        els.sort.addEventListener('change', e => {
            state.sort = e.target.value || 'recent';
            render();
        });

        els.emptyClear.addEventListener('click', () => {
            state.search = '';
            state.filters = { starred: false, model: '', hasArtifact: false, hasAttachment: false };
            els.search.value = '';
            els.filterChips.forEach(c => c.setAttribute('aria-pressed', 'false'));
            els.filterModel.value = '';
            render();
        });

        els.errorRetry.addEventListener('click', () => {
            state.error = null;
            fetchSessions();
        });
    }

    /* ─── background refresh (30s while page is visible) ─────── */
    let refreshTimer = null;
    function startBackgroundRefresh() {
        clearInterval(refreshTimer);
        refreshTimer = setInterval(() => {
            if (document.visibilityState === 'visible') fetchSessions();
        }, 30_000);
    }
    document.addEventListener('visibilitychange', () => {
        if (document.visibilityState === 'visible') fetchSessions();
    });

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', () => { boot(); startBackgroundRefresh(); }, { once: true });
    } else {
        boot();
        startBackgroundRefresh();
    }

    // Expose for later tasks
    window.__chatsPage = { state, render, fetchSessions, invalidateCache };
})();
