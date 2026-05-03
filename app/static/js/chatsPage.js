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
        const visible = getVisibleSessions();
        if (visible.length === 0) {
            els.list.innerHTML = '';
            els.empty.hidden = false;
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
        // Wired in later tasks — for now, click the title navigates.
        document.querySelectorAll('.chats-row').forEach(row => {
            const titleEl = row.querySelector('.chats-row__title');
            if (titleEl) {
                titleEl.addEventListener('click', () => {
                    const id = row.dataset.sessionId;
                    if (id) window.location.href = '/c/' + encodeURIComponent(id);
                });
            }
        });
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
        const cached = loadCache();
        if (cached) {
            state.sessions = cached;
            populateModelFilter();
            render();
            // Background refresh
            fetchSessions();
        } else {
            fetchSessions();
        }
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
