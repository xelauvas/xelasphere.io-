// Reminders M0 — inline card render, edit modal, Schedules tab list.
//
// API entry points (exposed on window.RemindersModule):
//   renderCardForMessage(messageEl, reminder)
//        Attach a reminder card to a chat message DOM element.
//        Called from the chat renderer's done-payload handler.
//   openModal(reminder | null)
//        Open the edit modal. Pass null/{} for "+ New reminder".
//   refreshSchedulesTab()
//        Re-fetch + re-render the Upcoming/Past lists in settings → Schedules.
//   checkUnseenFailures()
//        Page-load toast for failed reminders since last visit. Fired by M5.

(function () {
  'use strict';

  const STATE_LABEL = {
    scheduled: 'scheduled',
    sending: 'sending',
    paused: 'paused',
    delivered: 'delivered',
    failed: 'failed',
    cancelled: 'cancelled',
  };

  // ---------- utilities ----------

  function getCsrf() {
    // CSRFProtect stores the token on the page as a meta tag or hidden input.
    // Try common locations; fall back to cookie scan.
    const metaTok = document.querySelector('meta[name="csrf-token"]');
    if (metaTok) return metaTok.getAttribute('content');
    const hiddenInput = document.querySelector('input[name="csrf_token"]');
    if (hiddenInput) return hiddenInput.value;
    const m = document.cookie.match(/(?:^|;\s*)csrf_token=([^;]+)/);
    return m ? decodeURIComponent(m[1]) : '';
  }

  function getBrowserTz() {
    try { return Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC'; }
    catch { return 'UTC'; }
  }

  function escapeHtml(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;')
      .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }

  function formatWhen(reminder) {
    if (!reminder || !reminder.fireAt) return '';
    const dt = new Date(reminder.fireAt);
    if (isNaN(dt.getTime())) return '';
    const tz = reminder.timezone || getBrowserTz();
    let label;
    try {
      label = dt.toLocaleString(undefined, {
        timeZone: tz, month: 'short', day: 'numeric',
        hour: '2-digit', minute: '2-digit',
      });
    } catch {
      label = dt.toLocaleString();
    }
    const tzShort = tz.includes('/') ? tz.split('/').pop().replace('_', ' ') : tz;
    return label + ' · ' + tzShort;
  }

  function splitFireAtLocal(reminder) {
    // Convert UTC fireAt + tz → date input (YYYY-MM-DD) + time input (HH:MM)
    const dt = new Date(reminder.fireAt);
    const tz = reminder.timezone || getBrowserTz();
    try {
      const parts = new Intl.DateTimeFormat('en-CA', {
        timeZone: tz,
        year: 'numeric', month: '2-digit', day: '2-digit',
        hour: '2-digit', minute: '2-digit', hour12: false,
      }).formatToParts(dt);
      const get = type => (parts.find(p => p.type === type) || {}).value || '';
      return {
        date: `${get('year')}-${get('month')}-${get('day')}`,
        time: `${get('hour')}:${get('minute')}`,
      };
    } catch {
      return { date: '', time: '' };
    }
  }

  // ---------- card rendering ----------

  function renderCardInner(reminder) {
    const state = reminder.status || 'scheduled';
    const label = STATE_LABEL[state] || state;
    return `<span class="reminder-card__icon" aria-hidden="true">🔔</span>
            <span class="reminder-card__body">
              <span class="reminder-card__name">${escapeHtml(reminder.name)}</span>
              <span class="reminder-card__when">${escapeHtml(formatWhen(reminder))} · ${label}</span>
            </span>`;
  }

  function buildCardElement(reminder) {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = `reminder-card reminder-card--${reminder.status || 'scheduled'}`;
    btn.dataset.reminderId = reminder.id;
    btn.dataset.reminderJson = JSON.stringify(reminder);
    btn.innerHTML = renderCardInner(reminder);
    btn.addEventListener('click', () => openModal(JSON.parse(btn.dataset.reminderJson)));
    return btn;
  }

  function renderCardForMessage(messageEl, reminder) {
    if (!messageEl || !reminder || !reminder.id) return;
    // Don't double-render if message already has a card for this reminder
    if (messageEl.querySelector(`.reminder-card[data-reminder-id="${reminder.id}"]`)) return;
    const card = buildCardElement(reminder);
    messageEl.appendChild(card);
  }

  // ---------- fetch + re-hydrate ----------

  async function fetchReminder(id) {
    const resp = await fetch(`/reminders/${encodeURIComponent(id)}`, { credentials: 'same-origin' });
    if (!resp.ok) throw new Error(`fetch failed: ${resp.status}`);
    const data = await resp.json();
    return data.reminder;
  }

  async function rehydrateCardsForReminder(reminderId) {
    let fresh;
    try { fresh = await fetchReminder(reminderId); }
    catch { return; }
    document.querySelectorAll(`.reminder-card[data-reminder-id="${reminderId}"]`).forEach(btn => {
      btn.className = `reminder-card reminder-card--${fresh.status || 'scheduled'}`;
      btn.dataset.reminderJson = JSON.stringify(fresh);
      btn.innerHTML = renderCardInner(fresh);
    });
  }

  // ---------- modal ----------

  function openModal(reminder) {
    reminder = reminder || {};
    const modal = document.getElementById('reminders-modal');
    if (!modal) return;
    const form = modal.querySelector('[data-reminders-form]');
    form.elements['id'].value = reminder.id || '';
    form.elements['name'].value = reminder.name || '';
    form.elements['instructions'].value = reminder.instructions || '';

    let dateStr = '', timeStr = '';
    if (reminder.fireAt) {
      const split = splitFireAtLocal(reminder);
      dateStr = split.date; timeStr = split.time;
    } else {
      // Default: 1 hour from now in browser tz
      const now = new Date(Date.now() + 60 * 60 * 1000);
      const pad = n => String(n).padStart(2, '0');
      dateStr = `${now.getFullYear()}-${pad(now.getMonth()+1)}-${pad(now.getDate())}`;
      timeStr = `${pad(now.getHours())}:${pad(now.getMinutes())}`;
    }
    form.elements['date'].value = dateStr;
    form.elements['time'].value = timeStr;

    const tz = reminder.timezone || getBrowserTz();
    modal.querySelector('[data-tz]').textContent = tz;
    form.dataset.timezone = tz;

    // Pause/Resume button label + target
    const pauseBtn = modal.querySelector('[data-action="pause"]');
    if (reminder.id) {
      pauseBtn.hidden = false;
      pauseBtn.textContent = reminder.status === 'paused' ? 'Resume' : 'Pause';
      pauseBtn.dataset.targetStatus = reminder.status === 'paused' ? 'scheduled' : 'paused';
    } else {
      pauseBtn.hidden = true;
    }

    // Delete button — hide for delivered/sending (terminal/in-flight)
    const deleteBtn = modal.querySelector('[data-action="delete"]');
    deleteBtn.hidden = !reminder.id || ['delivered', 'sending'].includes(reminder.status);

    modal.hidden = false;
    // Focus the name field for quick edit
    setTimeout(() => { try { form.elements['name'].focus(); } catch {} }, 0);
  }

  function closeModal() {
    const modal = document.getElementById('reminders-modal');
    if (modal) modal.hidden = true;
  }

  async function saveFromForm(ev) {
    ev.preventDefault();
    const form = ev.currentTarget;
    const id = form.elements['id'].value;
    const tz = form.dataset.timezone || getBrowserTz();
    const fireAtLocal = `${form.elements['date'].value}T${form.elements['time'].value}:00`;

    const body = {
      name: form.elements['name'].value.trim(),
      instructions: form.elements['instructions'].value.trim(),
      fireAtLocal,
      timezone: tz,
    };
    try {
      const url = id ? `/reminders/${encodeURIComponent(id)}` : `/reminders`;
      const method = id ? 'PATCH' : 'POST';
      const resp = await fetch(url, {
        method,
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrf() },
        body: JSON.stringify(body),
      });
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({ error: resp.statusText }));
        alert(`Could not save reminder: ${err.error || resp.statusText}`);
        return;
      }
      closeModal();
      const data = await resp.json();
      const newId = data.id || (data.reminder && data.reminder.id) || id;
      if (newId) await rehydrateCardsForReminder(newId);
      if (window.RemindersModule.refreshSchedulesTab) window.RemindersModule.refreshSchedulesTab();
    } catch (e) {
      alert(`Network error: ${e.message}`);
    }
  }

  async function pauseFromForm(form) {
    const id = form.elements['id'].value;
    if (!id) return;
    const targetStatus = form.querySelector('[data-action="pause"]').dataset.targetStatus;
    const resp = await fetch(`/reminders/${encodeURIComponent(id)}`, {
      method: 'PATCH',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrf() },
      body: JSON.stringify({ status: targetStatus }),
    });
    if (resp.ok) {
      closeModal();
      await rehydrateCardsForReminder(id);
      if (window.RemindersModule.refreshSchedulesTab) window.RemindersModule.refreshSchedulesTab();
    }
  }

  async function deleteFromForm(form) {
    const id = form.elements['id'].value;
    if (!id) return;
    if (!confirm('Delete this reminder?')) return;
    const resp = await fetch(`/reminders/${encodeURIComponent(id)}`, {
      method: 'DELETE',
      credentials: 'same-origin',
      headers: { 'X-CSRFToken': getCsrf() },
    });
    if (resp.ok) {
      closeModal();
      document.querySelectorAll(`.reminder-card[data-reminder-id="${id}"]`).forEach(btn => btn.remove());
      if (window.RemindersModule.refreshSchedulesTab) window.RemindersModule.refreshSchedulesTab();
    }
  }

  // ---------- Schedules tab ----------

  function renderRow(reminder) {
    const li = document.createElement('li');
    li.className = `schedules-row schedules-row--${reminder.status || 'scheduled'}`;
    li.dataset.reminderId = reminder.id;
    const icon = (reminder.status === 'delivered') ? '✓' : '🔔';
    li.innerHTML = `
      <span class="schedules-row__icon" aria-hidden="true">${icon}</span>
      <span class="schedules-row__body">
        <span class="schedules-row__name">${escapeHtml(reminder.name)}</span>
        <span class="schedules-row__when">${escapeHtml(formatWhen(reminder))} · ${STATE_LABEL[reminder.status] || reminder.status}</span>
      </span>`;
    li.addEventListener('click', () => openModal(reminder));
    return li;
  }

  async function refreshSchedulesTab() {
    const pane = document.querySelector('[data-pane="schedules"]');
    if (!pane) return;
    try {
      const resp = await fetch('/reminders', { credentials: 'same-origin' });
      if (!resp.ok) return;
      const data = await resp.json();
      const reminders = data.reminders || [];
      const upcoming = reminders.filter(r => ['scheduled', 'paused', 'failed', 'sending'].includes(r.status));
      const past = reminders.filter(r => ['delivered', 'cancelled'].includes(r.status));

      const upUl = pane.querySelector('[data-schedules-upcoming]');
      const pastUl = pane.querySelector('[data-schedules-past]');
      const upCount = pane.querySelector('[data-schedules-upcoming-count]');
      const pastCount = pane.querySelector('[data-schedules-past-count]');

      upUl.innerHTML = upcoming.length === 0
        ? '<li class="schedules-pane__empty">No upcoming reminders.</li>'
        : '';
      upcoming.forEach(r => upUl.appendChild(renderRow(r)));

      pastUl.innerHTML = past.length === 0
        ? '<li class="schedules-pane__empty">No past reminders.</li>'
        : '';
      past.forEach(r => pastUl.appendChild(renderRow(r)));

      upCount.textContent = `(${upcoming.length})`;
      pastCount.textContent = `(${past.length})`;
    } catch (e) {
      console.warn('refreshSchedulesTab failed', e);
    }
  }

  function bindSchedulesTab() {
    const pane = document.querySelector('[data-pane="schedules"]');
    if (!pane) return;
    const newBtn = pane.querySelector('[data-schedules-new]');
    if (newBtn) newBtn.addEventListener('click', () => openModal({}));

    const pastToggle = pane.querySelector('[data-schedules-toggle-past]');
    const pastList = pane.querySelector('[data-schedules-past]');
    if (pastToggle && pastList) {
      pastToggle.addEventListener('click', () => {
        const isHidden = pastList.hasAttribute('hidden');
        if (isHidden) { pastList.removeAttribute('hidden'); pastToggle.textContent = 'hide'; }
        else { pastList.setAttribute('hidden', ''); pastToggle.textContent = 'show'; }
      });
    }
  }

  // ---------- unseen-failures toast (M5) ----------

  async function checkUnseenFailures() {
    try {
      const resp = await fetch('/reminders/unseen-failures', { credentials: 'same-origin' });
      if (!resp.ok) return;
      const data = await resp.json();
      if (data.count > 0) {
        showToast(`${data.count} reminder${data.count === 1 ? '' : 's'} failed to deliver. Open Schedules to see why.`);
        fetch('/reminders/mark-seen', {
          method: 'POST', credentials: 'same-origin',
          headers: { 'X-CSRFToken': getCsrf() },
        });
      }
    } catch {}
  }

  function showToast(message) {
    if (window.Toast && typeof window.Toast.show === 'function') {
      window.Toast.show(message, { type: 'warning', duration: 6000 });
      return;
    }
    const el = document.createElement('div');
    el.className = 'reminders-toast';
    el.textContent = message;
    document.body.appendChild(el);
    setTimeout(() => el.remove(), 6000);
  }

  // ---------- DOM ready bootstrap ----------

  document.addEventListener('DOMContentLoaded', () => {
    bindSchedulesTab();

    const modal = document.getElementById('reminders-modal');
    if (modal) {
      modal.querySelectorAll('[data-close]').forEach(el =>
        el.addEventListener('click', closeModal));
      const form = modal.querySelector('[data-reminders-form]');
      if (form) {
        form.addEventListener('submit', saveFromForm);
        const pauseBtn = form.querySelector('[data-action="pause"]');
        if (pauseBtn) pauseBtn.addEventListener('click', () => pauseFromForm(form));
        const deleteBtn = form.querySelector('[data-action="delete"]');
        if (deleteBtn) deleteBtn.addEventListener('click', () => deleteFromForm(form));
      }
    }
  });

  // Settings modal dispatches a custom event when the user switches tabs
  document.addEventListener('settings-tab-shown', (ev) => {
    if (ev.detail && ev.detail.tab === 'schedules') refreshSchedulesTab();
  });

  // Expose API
  window.RemindersModule = {
    renderCardForMessage,
    rehydrateCardsForReminder,
    openModal,
    closeModal,
    refreshSchedulesTab,
    checkUnseenFailures,
    fetchReminder,
  };
})();
