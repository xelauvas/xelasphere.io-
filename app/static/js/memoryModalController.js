/**
 * MemoryModalController — shared controller for the memory child modal.
 *
 * Used by both:
 *   - /account/manage standalone page (settings_api_keys.html)
 *   - Global settings modal Personalization → Manage (index.html)
 *
 * Markup lives in templates/_settings_memory_modal.html.
 * Endpoints used:
 *   GET    /user/memories
 *   POST   /user/memories/pin
 *   DELETE /user/memories/item
 *   DELETE /user/forget-me           (Forget Me cascade)
 *   GET    /user/memory-toggles
 *   POST   /user/memory-toggles
 *   GET    /user/conversation-memory
 *   DELETE /user/conversation-memory/<drawer_id>
 *   GET    /user/dream-settings
 *   POST   /user/dream-settings
 *   GET    /user/memory-activity
 *   POST   /user/memory-activity/<id>/undo
 *   POST   /user/dream-test
 */
(function () {
    'use strict';

    let initialized = false;
    let bucketMetadata = {};
    let dreamActivityLoaded = false;
    let conversationMemoryLoaded = false;

    function getCsrfToken() {
        const meta = document.querySelector('meta[name="csrf-token"]');
        return meta ? meta.getAttribute('content') : '';
    }

    function $(id) { return document.getElementById(id); }

    function setSwitchState(btn, value) {
        if (btn) btn.setAttribute('aria-checked', value ? 'true' : 'false');
    }
    function getSwitchState(btn) {
        return btn ? btn.getAttribute('aria-checked') === 'true' : false;
    }

    function escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text || '';
        return div.innerHTML;
    }

    /* ====================================================================
       Modal open/close
       ==================================================================== */

    function openMemoryModal() {
        const memoryModal = $('memory-modal');
        if (!memoryModal) return;
        memoryModal.classList.add('open');
        memoryModal.setAttribute('aria-hidden', 'false');
        loadMemories();
        loadDreamToggle();
        loadMemoryToggles();
    }

    function closeMemoryModal() {
        const memoryModal = $('memory-modal');
        if (!memoryModal) return;
        memoryModal.classList.remove('open');
        memoryModal.setAttribute('aria-hidden', 'true');
    }

    function openClearMemoryModal() {
        const m = $('clear-memory-confirm-modal');
        if (!m) return;
        m.classList.add('open');
        m.setAttribute('aria-hidden', 'false');
    }

    function closeClearMemoryModal() {
        const m = $('clear-memory-confirm-modal');
        if (!m) return;
        m.classList.remove('open');
        m.setAttribute('aria-hidden', 'true');
    }

    /* ====================================================================
       Memories tab — load, render, pin, delete
       ==================================================================== */

    async function loadMemories() {
        const memoryBuckets = $('memory-buckets');
        const memoryLoading = $('memory-loading');
        const memoryEmpty = $('memory-empty');
        const memoryCount = $('memory-count');
        if (!memoryBuckets || !memoryLoading || !memoryEmpty) return;

        memoryLoading.style.display = 'flex';
        memoryEmpty.style.display = 'none';
        memoryBuckets.innerHTML = '';

        try {
            const response = await fetch('/user/memories', {
                headers: { 'X-CSRFToken': getCsrfToken() },
                credentials: 'same-origin',
            });
            const data = await response.json();

            memoryLoading.style.display = 'none';
            bucketMetadata = data.metadata || {};

            if (!data.buckets || data.count === 0) {
                memoryEmpty.style.display = 'flex';
                if (memoryCount) memoryCount.textContent = '0 memories';
                return;
            }

            if (memoryCount) {
                memoryCount.textContent = `${data.count} ${data.count === 1 ? 'memory' : 'memories'}`;
            }

            const bucketOrder = ['profile', 'preference', 'project', 'constraint', 'temporary'];
            bucketOrder.forEach(bucketName => {
                const items = data.buckets[bucketName] || [];
                const meta = bucketMetadata[bucketName] || {};
                if (items.length === 0) return;

                const bucketEl = document.createElement('div');
                bucketEl.className = 'memory-bucket';
                bucketEl.setAttribute('data-bucket', bucketName);

                const headerEl = document.createElement('div');
                headerEl.className = 'memory-bucket-header';
                headerEl.innerHTML = `
                    <span class="bucket-icon" style="color: ${escapeHtml(meta.color || '#888')}">
                        <i class="fas ${escapeHtml(meta.icon || 'fa-circle')}"></i>
                    </span>
                    <span class="bucket-label">${escapeHtml(meta.label || bucketName)}</span>
                    <span class="bucket-count">${items.length}</span>
                `;

                const listEl = document.createElement('ul');
                listEl.className = 'memory-bucket-list';

                items.forEach(memory => {
                    const li = document.createElement('li');
                    li.className = 'memory-item' + (memory.pinned ? ' pinned' : '');

                    const textSpan = document.createElement('span');
                    textSpan.className = 'memory-text';
                    textSpan.textContent = memory.text;

                    const actionsDiv = document.createElement('div');
                    actionsDiv.className = 'memory-item-actions';

                    const pinBtn = document.createElement('button');
                    pinBtn.type = 'button';
                    pinBtn.className = 'memory-pin-btn' + (memory.pinned ? ' active' : '');
                    pinBtn.title = memory.pinned ? 'Unpin' : 'Pin';
                    pinBtn.dataset.id = memory.id;
                    pinBtn.dataset.text = memory.text;
                    pinBtn.innerHTML = '<i class="fas fa-thumbtack"></i>';

                    const deleteBtn = document.createElement('button');
                    deleteBtn.type = 'button';
                    deleteBtn.className = 'memory-delete-btn';
                    deleteBtn.title = 'Remove';
                    deleteBtn.dataset.id = memory.id;
                    deleteBtn.dataset.text = memory.text;
                    deleteBtn.innerHTML = '<i class="fas fa-xmark"></i>';

                    actionsDiv.appendChild(pinBtn);
                    actionsDiv.appendChild(deleteBtn);
                    li.appendChild(textSpan);
                    li.appendChild(actionsDiv);
                    listEl.appendChild(li);
                });

                bucketEl.appendChild(headerEl);
                bucketEl.appendChild(listEl);
                memoryBuckets.appendChild(bucketEl);
            });

            memoryBuckets.querySelectorAll('.memory-pin-btn').forEach(btn => {
                btn.addEventListener('click', () => togglePin(btn.dataset.id, btn.dataset.text, btn));
            });

            memoryBuckets.querySelectorAll('.memory-delete-btn').forEach(btn => {
                btn.addEventListener('click', () =>
                    deleteMemory(btn.dataset.id, btn.dataset.text, btn.closest('.memory-item'))
                );
            });
        } catch (error) {
            console.error('[memory-modal] failed to load memories:', error);
            memoryLoading.style.display = 'none';
            memoryEmpty.style.display = 'flex';
            const p = memoryEmpty.querySelector('p');
            if (p) p.textContent = 'Failed to load memories. Please try again.';
        }
    }

    async function togglePin(id, text, btn) {
        if (!btn) return;
        btn.classList.add('loading');
        try {
            const response = await fetch('/user/memories/pin', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': getCsrfToken(),
                },
                credentials: 'same-origin',
                body: JSON.stringify({ id, text }),
            });
            if (response.ok) {
                const data = await response.json();
                const item = btn.closest('.memory-item');
                if (data.pinned) {
                    btn.classList.add('active');
                    btn.title = 'Unpin';
                    if (item) item.classList.add('pinned');
                } else {
                    btn.classList.remove('active');
                    btn.title = 'Pin';
                    if (item) item.classList.remove('pinned');
                }
            } else {
                console.error('[memory-modal] pin toggle failed');
            }
        } catch (error) {
            console.error('[memory-modal] pin toggle error:', error);
        } finally {
            btn.classList.remove('loading');
        }
    }

    async function deleteMemory(id, text, element) {
        if (!element) return;
        element.classList.add('deleting');
        try {
            const response = await fetch('/user/memories/item', {
                method: 'DELETE',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': getCsrfToken(),
                },
                credentials: 'same-origin',
                body: JSON.stringify({ id, text }),
            });
            if (response.ok) {
                element.classList.add('deleted');
                setTimeout(() => {
                    element.remove();
                    updateMemoryCount();
                }, 300);
            } else {
                element.classList.remove('deleting');
                console.error('[memory-modal] delete failed');
            }
        } catch (error) {
            element.classList.remove('deleting');
            console.error('[memory-modal] delete error:', error);
        }
    }

    function updateMemoryCount() {
        const memoryBuckets = $('memory-buckets');
        const memoryCount = $('memory-count');
        const memoryEmpty = $('memory-empty');
        if (!memoryBuckets) return;

        const items = memoryBuckets.querySelectorAll('.memory-item');
        const count = items.length;
        if (memoryCount) {
            memoryCount.textContent = `${count} ${count === 1 ? 'memory' : 'memories'}`;
        }

        memoryBuckets.querySelectorAll('.memory-bucket').forEach(bucket => {
            const bucketItems = bucket.querySelectorAll('.memory-item');
            if (bucketItems.length === 0) {
                bucket.remove();
            } else {
                const counter = bucket.querySelector('.bucket-count');
                if (counter) counter.textContent = bucketItems.length;
            }
        });

        if (count === 0 && memoryEmpty) {
            memoryEmpty.style.display = 'flex';
        }
    }

    /* ====================================================================
       Forget Me — DELETE /user/forget-me cascade
       ==================================================================== */

    async function clearAllMemories() {
        const confirmBtn = $('confirm-clear-memory');
        const memoryBuckets = $('memory-buckets');
        const memoryEmpty = $('memory-empty');
        const memoryCount = $('memory-count');
        if (!confirmBtn) return;

        const originalText = confirmBtn.textContent;
        confirmBtn.disabled = true;
        confirmBtn.textContent = 'Forgetting...';

        try {
            const response = await fetch('/user/forget-me', {
                method: 'DELETE',
                headers: { 'X-CSRFToken': getCsrfToken() },
                credentials: 'same-origin',
            });

            if (response.ok) {
                const result = await response.json();
                const partial = result.partial_failures || [];
                closeClearMemoryModal();
                if (memoryBuckets) memoryBuckets.innerHTML = '';
                if (memoryEmpty) memoryEmpty.style.display = 'flex';
                if (memoryCount) memoryCount.textContent = '0 memories';
                conversationMemoryLoaded = false;
                fetchConversationMemory('all');
                if (partial.length > 0) {
                    alert(`Mostly done — these stores couldn't be wiped right now: ${partial.join(', ')}. Try again in a moment.`);
                }
            } else {
                console.error('[memory-modal] forget-me failed');
                alert("Couldn't complete Forget Me — try again.");
            }
        } catch (error) {
            console.error('[memory-modal] forget-me error:', error);
            alert("Couldn't complete Forget Me — network error.");
        } finally {
            confirmBtn.disabled = false;
            confirmBtn.textContent = originalText;
        }
    }

    /* ====================================================================
       Conversation memory tab
       ==================================================================== */

    async function fetchConversationMemory(filter) {
        filter = filter || 'all';
        const loading = $('conversation-loading');
        const empty = $('conversation-empty');
        const error = $('conversation-error');
        const list = $('conversation-list');
        if (!list) return;

        if (loading) loading.style.display = 'flex';
        if (empty) empty.style.display = 'none';
        if (error) error.style.display = 'none';
        list.innerHTML = '';

        try {
            const resp = await fetch('/user/conversation-memory', { credentials: 'same-origin' });
            if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
            const data = await resp.json();
            const turns = data.turns || [];
            const filtered = filter === 'user' ? turns.filter(t => t.role === 'user') : turns;

            if (loading) loading.style.display = 'none';
            if (filtered.length === 0) {
                if (empty) empty.style.display = 'flex';
                return;
            }
            list.innerHTML = filtered.map(t => `
                <div class="conv-row" data-drawer-id="${escapeHtml(t.drawer_id)}">
                    <span class="conv-role-badge conv-role-${t.role === 'assistant' ? 'assistant' : 'user'}">[${escapeHtml(t.role)}]</span>
                    <span class="conv-text">${escapeHtml(t.text)}</span>
                    <button type="button" class="conv-delete-btn" data-drawer-id="${escapeHtml(t.drawer_id)}" title="Delete this turn">
                        <i class="fas fa-trash"></i>
                    </button>
                </div>
            `).join('');
        } catch (e) {
            console.warn('[memory-modal] conversation-memory fetch failed:', e);
            if (loading) loading.style.display = 'none';
            if (error) error.style.display = 'flex';
        }
    }

    async function handleConvDelete(btn) {
        const drawerId = btn.getAttribute('data-drawer-id');
        if (!drawerId) return;
        if (!confirm('Delete this conversation turn? The AI will no longer remember it.')) return;

        btn.disabled = true;
        try {
            const resp = await fetch(`/user/conversation-memory/${encodeURIComponent(drawerId)}`, {
                method: 'DELETE',
                credentials: 'same-origin',
                headers: { 'X-CSRFToken': getCsrfToken() },
            });
            if (resp.ok) {
                const row = btn.closest('.conv-row');
                if (row) row.remove();
            } else {
                alert("Couldn't delete that turn — try again in a moment.");
                btn.disabled = false;
            }
        } catch (err) {
            console.warn('[memory-modal] conv-delete failed:', err);
            alert("Couldn't delete that turn — network error.");
            btn.disabled = false;
        }
    }

    /* ====================================================================
       Memory toggles inside the child modal
       (master / ref_v2 / ref_mempalace — by ID, separate from the
        Personalization-pane copies which use data-memory-toggle attrs)
       ==================================================================== */

    async function loadMemoryToggles() {
        try {
            const resp = await fetch('/user/memory-toggles', { credentials: 'same-origin' });
            if (!resp.ok) return;
            const t = await resp.json();
            setSwitchState($('memory-toggle-master'), t.master !== false);
            setSwitchState($('memory-toggle-ref-v2'), t.ref_v2 !== false);
            setSwitchState($('memory-toggle-ref-mempalace'), t.ref_mempalace !== false);
        } catch (e) {
            console.warn('[memory-modal] memory-toggles load failed:', e);
        }
    }

    async function persistMemoryToggle(field, value, fieldId) {
        try {
            const resp = await fetch('/user/memory-toggles', {
                method: 'POST',
                credentials: 'same-origin',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': getCsrfToken(),
                },
                body: JSON.stringify({ [field]: value }),
            });
            if (!resp.ok) {
                setSwitchState($(fieldId), !value);
                alert('Could not save preference; please retry.');
            }
        } catch (e) {
            console.warn('[memory-modal] memory-toggles persist failed:', e);
        }
    }

    function bindMemorySwitch(id, field) {
        const btn = $(id);
        if (!btn || btn.dataset.bound === '1') return;
        btn.dataset.bound = '1';
        btn.addEventListener('click', () => {
            const newValue = !getSwitchState(btn);
            setSwitchState(btn, newValue);
            persistMemoryToggle(field, newValue, id);
        });
    }

    /* ====================================================================
       Dream Consolidation — toggle + activity log
       ==================================================================== */

    async function loadDreamToggle() {
        const dreamToggle = $('dream-enabled-toggle');
        if (!dreamToggle) return;
        try {
            const res = await fetch('/user/dream-settings', {
                headers: { 'X-CSRFToken': getCsrfToken() },
                credentials: 'same-origin',
            });
            const data = await res.json();
            setSwitchState(dreamToggle, data.dream_enabled !== false);
        } catch (e) {
            console.warn('[memory-modal] dream-settings load failed:', e);
        }
    }

    async function loadDreamActivity() {
        const loading = $('dream-activity-loading');
        const empty = $('dream-activity-empty');
        const error = $('dream-activity-error');
        const list = $('dream-activity-list');
        if (!list) return;

        if (loading) loading.style.display = 'flex';
        if (empty) empty.style.display = 'none';
        if (error) error.style.display = 'none';
        list.innerHTML = '';

        try {
            const res = await fetch('/user/memory-activity', {
                headers: { 'X-CSRFToken': getCsrfToken() },
                credentials: 'same-origin',
            });
            if (!res.ok) throw new Error('Failed');
            const data = await res.json();
            if (loading) loading.style.display = 'none';

            if (!data.logs || data.logs.length === 0) {
                if (empty) empty.style.display = 'flex';
                dreamActivityLoaded = true;
                return;
            }

            data.logs.forEach(log => list.appendChild(renderDreamEntry(log)));
            dreamActivityLoaded = true;
        } catch (e) {
            console.error('[memory-modal] dream-activity load failed:', e);
            if (loading) loading.style.display = 'none';
            if (error) error.style.display = 'block';
        }
    }

    function renderDreamEntry(log) {
        const entry = document.createElement('div');
        entry.className = 'dream-activity-entry' + (log.undone ? ' undone' : '');

        const header = document.createElement('div');
        header.className = 'dream-entry-header';

        const time = document.createElement('span');
        time.className = 'dream-entry-time';
        time.textContent = formatDreamDate(log.started_at);
        header.appendChild(time);

        if (log.undone) {
            const badge = document.createElement('span');
            badge.className = 'dream-entry-badge';
            badge.textContent = 'Undone';
            header.appendChild(badge);
        }

        const actionsList = document.createElement('ul');
        actionsList.className = 'dream-entry-actions-list';

        const summary = log.summary || buildDreamSummary(log);
        const lines = [];
        if (summary.merged > 0) lines.push(`Merged ${summary.merged} duplicate memories`);
        if (summary.pruned > 0) lines.push(`Pruned ${summary.pruned} memories`);
        if (summary.superseded > 0) lines.push(`Resolved ${summary.superseded} conflicts`);
        if (summary.promoted > 0) lines.push(`Promoted ${summary.promoted} memories`);

        const allActions = [...(log.phase1_actions || []), ...(log.phase2_actions || [])];
        allActions.filter(a => a.action_type === 'synthesize').forEach(a => {
            lines.push(`New insight: "${a.reason}"`);
        });

        if (lines.length === 0) lines.push('Consolidation cycle completed');

        lines.forEach(text => {
            const li = document.createElement('li');
            li.textContent = text;
            actionsList.appendChild(li);
        });

        entry.appendChild(header);
        entry.appendChild(actionsList);

        if (!log.undone && isWithin7Days(log.started_at)) {
            const undoBtn = document.createElement('button');
            undoBtn.type = 'button';
            undoBtn.className = 'dream-undo-btn';
            undoBtn.textContent = 'Undo';
            undoBtn.addEventListener('click', () => undoDream(log.id, undoBtn, entry));
            entry.appendChild(undoBtn);
        }

        return entry;
    }

    function buildDreamSummary(log) {
        const counts = { pruned: 0, merged: 0, superseded: 0, promoted: 0, synthesized: 0 };
        const all = [...(log.phase1_actions || []), ...(log.phase2_actions || [])];
        all.forEach(a => {
            if (a.action_type === 'prune') counts.pruned++;
            else if (a.action_type === 'merge') counts.merged++;
            else if (a.action_type === 'supersede') counts.superseded++;
            else if (a.action_type === 'promote') counts.promoted++;
            else if (a.action_type === 'synthesize') counts.synthesized++;
        });
        return counts;
    }

    function formatDreamDate(isoStr) {
        if (!isoStr) return 'Unknown date';
        try {
            const d = new Date(isoStr);
            return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })
                + ' — '
                + d.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' });
        } catch { return isoStr; }
    }

    function isWithin7Days(isoStr) {
        if (!isoStr) return false;
        try {
            const d = new Date(isoStr);
            return (Date.now() - d.getTime()) < 7 * 24 * 60 * 60 * 1000;
        } catch { return false; }
    }

    async function undoDream(logId, btn, entry) {
        btn.disabled = true;
        btn.textContent = 'Undoing...';
        try {
            const res = await fetch(`/user/memory-activity/${logId}/undo`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': getCsrfToken(),
                },
                credentials: 'same-origin',
            });
            const data = await res.json();
            if (res.ok && data.success) {
                entry.classList.add('undone');
                btn.remove();
                const badge = document.createElement('span');
                badge.className = 'dream-entry-badge';
                badge.textContent = 'Undone';
                const header = entry.querySelector('.dream-entry-header');
                if (header) header.appendChild(badge);
            } else {
                btn.textContent = data.error || 'Failed';
                setTimeout(() => { btn.textContent = 'Undo'; btn.disabled = false; }, 2000);
            }
        } catch (e) {
            console.error('[memory-modal] undo failed:', e);
            btn.textContent = 'Failed';
            setTimeout(() => { btn.textContent = 'Undo'; btn.disabled = false; }, 2000);
        }
    }

    /* ====================================================================
       Wiring — idempotent
       ==================================================================== */

    function bindOnce(el, event, handler) {
        if (!el) return;
        const key = `bound_${event}`;
        if (el.dataset[key] === '1') return;
        el.dataset[key] = '1';
        el.addEventListener(event, handler);
    }

    function init() {
        if (initialized) return;
        const memoryModal = $('memory-modal');
        if (!memoryModal) return;
        initialized = true;

        // Open triggers (both surfaces)
        document.querySelectorAll('#manage-memory-btn, [data-action="open-memory-modal"]').forEach(btn => {
            bindOnce(btn, 'click', openMemoryModal);
        });

        // Close memory modal — X + backdrop
        bindOnce($('close-memory-modal'), 'click', closeMemoryModal);
        bindOnce(memoryModal, 'click', e => {
            if (e.target === memoryModal) closeMemoryModal();
        });

        // Forget Me flow
        bindOnce($('clear-all-memories'), 'click', openClearMemoryModal);
        bindOnce($('close-clear-memory-modal'), 'click', closeClearMemoryModal);
        bindOnce($('cancel-clear-memory'), 'click', closeClearMemoryModal);
        bindOnce($('confirm-clear-memory'), 'click', clearAllMemories);
        const confirmModal = $('clear-memory-confirm-modal');
        bindOnce(confirmModal, 'click', e => {
            if (e.target === confirmModal) closeClearMemoryModal();
        });

        // Refresh
        bindOnce($('refresh-memories'), 'click', loadMemories);

        // Tab switching (Memories / Conversation memory / Activity)
        const tabBtns = document.querySelectorAll('.memory-tab-btn');
        const tabPanels = document.querySelectorAll('.memory-tab-panel');
        tabBtns.forEach(btn => {
            bindOnce(btn, 'click', () => {
                const target = btn.dataset.memoryTab;
                tabBtns.forEach(b => b.classList.toggle('active', b === btn));
                tabPanels.forEach(p => p.classList.toggle('active', p.dataset.memoryPanel === target));
                if (target === 'activity' && !dreamActivityLoaded) {
                    loadDreamActivity();
                }
                if (target === 'conversation' && !conversationMemoryLoaded) {
                    fetchConversationMemory('all');
                    conversationMemoryLoaded = true;
                }
            });
        });

        // Conversation memory filter chips
        document.querySelectorAll('.conv-filter-chip').forEach(chip => {
            bindOnce(chip, 'click', () => {
                document.querySelectorAll('.conv-filter-chip').forEach(c => c.classList.remove('active'));
                chip.classList.add('active');
                fetchConversationMemory(chip.getAttribute('data-conv-filter') || 'all');
            });
        });

        bindOnce($('conversation-retry'), 'click', e => {
            e.preventDefault();
            const active = document.querySelector('.conv-filter-chip.active');
            const filter = active ? active.getAttribute('data-conv-filter') : 'all';
            fetchConversationMemory(filter || 'all');
        });

        // Conversation memory: per-row delete (delegated, scoped to modal)
        bindOnce(memoryModal, 'click', e => {
            const btn = e.target.closest('.conv-delete-btn');
            if (btn) handleConvDelete(btn);
        });

        // Memory toggles inside the child modal
        bindMemorySwitch('memory-toggle-master', 'master');
        bindMemorySwitch('memory-toggle-ref-v2', 'ref_v2');
        bindMemorySwitch('memory-toggle-ref-mempalace', 'ref_mempalace');

        // Dream toggle
        const dreamToggle = $('dream-enabled-toggle');
        bindOnce(dreamToggle, 'click', async () => {
            const newValue = !getSwitchState(dreamToggle);
            setSwitchState(dreamToggle, newValue);
            try {
                await fetch('/user/dream-settings', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-CSRFToken': getCsrfToken(),
                    },
                    credentials: 'same-origin',
                    body: JSON.stringify({ dream_enabled: newValue }),
                });
            } catch (e) {
                console.error('[memory-modal] dream-settings save failed:', e);
                setSwitchState(dreamToggle, !newValue);
            }
        });

        // Dream test button
        const dreamTestBtn = $('dream-test-btn');
        bindOnce(dreamTestBtn, 'click', async () => {
            dreamTestBtn.disabled = true;
            dreamTestBtn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Running...';
            try {
                const res = await fetch('/user/dream-test', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-CSRFToken': getCsrfToken(),
                    },
                    credentials: 'same-origin',
                });
                const data = await res.json();
                dreamTestBtn.innerHTML = '<i class="fas fa-check"></i> ' + (data.message || data.error || 'Done');
                if (data.success) {
                    dreamActivityLoaded = false;
                    loadDreamActivity();
                }
                setTimeout(() => {
                    dreamTestBtn.innerHTML = '<i class="fas fa-flask"></i> Run consolidation now (test)';
                    dreamTestBtn.disabled = false;
                }, 3000);
            } catch (e) {
                dreamTestBtn.innerHTML = '<i class="fas fa-times"></i> Failed';
                setTimeout(() => {
                    dreamTestBtn.innerHTML = '<i class="fas fa-flask"></i> Run consolidation now (test)';
                    dreamTestBtn.disabled = false;
                }, 2000);
            }
        });

        // Activity retry
        bindOnce($('dream-activity-retry'), 'click', e => {
            e.preventDefault();
            loadDreamActivity();
        });
    }

    /* ====================================================================
       Public API
       ==================================================================== */

    window.MemoryModalController = {
        init,
        open: openMemoryModal,
        close: closeMemoryModal,
        reloadMemories: loadMemories,
    };

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
