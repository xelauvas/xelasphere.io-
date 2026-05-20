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
    const recLabel = _recurrenceLabel(reminder.recurrence);

    // M1.2: if snoozedUntil is set + in the future, show that time + (Snoozed)
    const snoozedUntil = reminder.snoozedUntil;
    if (snoozedUntil) {
      const sdt = new Date(snoozedUntil);
      if (!isNaN(sdt.getTime()) && sdt.getTime() > Date.now()) {
        let snoozeLabel;
        try {
          snoozeLabel = sdt.toLocaleString(undefined, {
            timeZone: tz, month: 'short', day: 'numeric',
            hour: '2-digit', minute: '2-digit',
          });
        } catch {
          snoozeLabel = sdt.toLocaleString();
        }
        return snoozeLabel + ' · ' + tzShort + (recLabel ? ' · ' + recLabel : '') + ' (Snoozed)';
      }
    }

    return label + ' · ' + tzShort + (recLabel ? ' · ' + recLabel : '');
  }

  function _recurrenceLabel(rec) {
    switch (rec) {
      case 'DAILY':    return 'Daily';
      case 'WEEKDAYS': return 'Weekdays';
      case 'WEEKLY':   return 'Weekly';
      case 'MONTHLY':  return 'Monthly';
      case 'YEARLY':   return 'Yearly';
      default:         return '';
    }
  }

  function _updateRecurrenceLabels(selectEl, reminder) {
    if (!reminder || !reminder.fireAt) return;
    const dt = new Date(reminder.fireAt);
    if (isNaN(dt.getTime())) return;
    const tz = reminder.timezone || getBrowserTz();
    const weekday = dt.toLocaleDateString(undefined, { weekday: 'long', timeZone: tz });
    const day = dt.toLocaleDateString(undefined, { day: 'numeric', timeZone: tz });
    const monthDay = dt.toLocaleDateString(undefined, {
      month: 'long', day: 'numeric', timeZone: tz,
    });
    selectEl.querySelectorAll('option[data-dynamic-label]').forEach(opt => {
      if (opt.value === 'WEEKLY') opt.textContent = `Weekly on ${weekday}`;
      if (opt.value === 'MONTHLY') opt.textContent = `Monthly on the ${day}`;
      if (opt.value === 'YEARLY')  opt.textContent = `Yearly on ${monthDay}`;
    });
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

  // ---------- M1.3: push banner ----------

  async function _maybeShowPushBanner(messageEl) {
    if (!messageEl) return;
    if (!window.RemindersModule?.config?.pushEnabled) return;
    if (!window.RemindersModule?.push?.isSupported) return;
    if (!window.RemindersModule.push.isSupported()) return;
    try {
      if (await window.RemindersModule.push.isSubscribed()) return;
    } catch { return; }

    try {
      const resp = await fetch('/push/dismiss-status', { credentials: 'same-origin' });
      if (resp.ok) {
        const { dismissedRecently } = await resp.json();
        if (dismissedRecently) return;
      }
    } catch { /* ignore — show banner */ }

    const tmpl = document.createElement('template');
    tmpl.innerHTML = `
<div class="push-permission-banner" data-push-banner>
  <div class="push-permission-banner__icon" aria-hidden="true">🔔</div>
  <div class="push-permission-banner__body">
    <p class="push-permission-banner__title">Want a browser ping too?</p>
    <p class="push-permission-banner__sub">Get notified even when this tab isn't open.</p>
  </div>
  <div class="push-permission-banner__actions">
    <button type="button" class="btn btn--primary" data-push-banner-enable>Enable push</button>
    <button type="button" class="btn btn--ghost" data-push-banner-dismiss>Not now</button>
    <button type="button" class="push-permission-banner__close" data-push-banner-close aria-label="Dismiss">✕</button>
  </div>
</div>`.trim();
    const banner = tmpl.content.firstElementChild;

    banner.querySelector('[data-push-banner-enable]').addEventListener('click', async () => {
      const r = await window.RemindersModule.push.subscribe();
      banner.remove();
      if (!r.ok) console.warn('push subscribe failed', r);
    });
    banner.querySelectorAll('[data-push-banner-dismiss], [data-push-banner-close]').forEach(el => {
      el.addEventListener('click', async () => {
        try { await window.RemindersModule.push.dismissPrompt(); } catch {}
        banner.remove();
      });
    });

    messageEl.appendChild(banner);
  }

  // ---------- icons (Lucide-style line SVGs, 1.75px stroke) ----------

  const ICON_CLOCK = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></svg>`;
  const ICON_KEBAB = `<svg viewBox="0 0 24 24" fill="currentColor" stroke="none" aria-hidden="true"><circle cx="5" cy="12" r="2"/><circle cx="12" cy="12" r="2"/><circle cx="19" cy="12" r="2"/></svg>`;
  const ICON_PENCIL = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"><path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4Z"/></svg>`;
  const ICON_PAUSE = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"><rect x="6" y="4" width="4" height="16"/><rect x="14" y="4" width="4" height="16"/></svg>`;
  const ICON_PLAY = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"><polygon points="5 3 19 12 5 21 5 3"/></svg>`;
  const ICON_TRASH = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/><path d="M10 11v6M14 11v6"/></svg>`;
  const ICON_RETRY = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"><path d="M3 12a9 9 0 1 0 3-6.7"/><polyline points="3 4 3 9 8 9"/></svg>`;
  const ICON_CHECK_CIRCLE = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="m8.5 12.5 2.5 2.5 4.5-5"/></svg>`;
  const ICON_ALERT = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M10.3 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0Z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>`;

  function iconForStatus(status) {
    switch (status) {
      case 'delivered': return ICON_CHECK_CIRCLE;
      case 'failed':    return ICON_ALERT;
      case 'paused':    return ICON_PAUSE;
      default:          return ICON_CLOCK;
    }
  }

  // ---------- card rendering ----------

  function renderCardInner(reminder) {
    const status = reminder.status || 'scheduled';
    return `<span class="reminder-card__icon">${iconForStatus(status)}</span>
            <span class="reminder-card__body">
              <span class="reminder-card__name">${escapeHtml(reminder.name)}</span>
              <span class="reminder-card__when">${escapeHtml(formatWhen(reminder))}</span>
            </span>
            <button type="button" class="reminder-card__kebab" data-reminder-kebab aria-label="Reminder options">${ICON_KEBAB}</button>`;
  }

  function buildCardElement(reminder) {
    const wrap = document.createElement('div');
    wrap.className = `reminder-card reminder-card--${reminder.status || 'scheduled'}`;
    wrap.dataset.reminderId = reminder.id;
    wrap.dataset.reminderJson = JSON.stringify(reminder);
    wrap.innerHTML = renderCardInner(reminder);

    // Card body click → open edit modal. Kebab click → dropdown (with
    // stopPropagation so the card click handler doesn't also fire).
    wrap.addEventListener('click', (e) => {
      const fresh = JSON.parse(wrap.dataset.reminderJson || '{}');
      openModal(fresh);
    });
    wrap.querySelector('[data-reminder-kebab]').addEventListener('click', (e) => {
      e.stopPropagation();
      const fresh = JSON.parse(wrap.dataset.reminderJson || '{}');
      toggleDropdown(wrap, fresh);
    });
    return wrap;
  }

  function renderCardForMessage(messageEl, reminder) {
    if (!messageEl || !reminder || !reminder.id) return;
    if (messageEl.querySelector(`.reminder-card[data-reminder-id="${reminder.id}"]`)) return;
    const card = buildCardElement(reminder);
    messageEl.appendChild(card);
    // M1.3 — opportunistically prompt for push permission
    _maybeShowPushBanner(messageEl);
    // Saved snapshots in chat history can be stale (worker may have
    // delivered/failed since save). Refresh from API for non-terminal states.
    const status = reminder.status || 'scheduled';
    if (!['delivered', 'failed', 'cancelled'].includes(status)) {
      rehydrateCardsForReminder(reminder.id);
    }
  }

  // ---------- kebab dropdown ----------

  let _openDropdown = null;  // singleton — only one open at a time

  function closeDropdown() {
    if (_openDropdown && _openDropdown.parentNode) {
      _openDropdown.parentNode.removeChild(_openDropdown);
    }
    _openDropdown = null;
  }

  function toggleDropdown(cardEl, reminder) {
    if (_openDropdown && _openDropdown.dataset.forReminder === reminder.id) {
      closeDropdown();
      return;
    }
    closeDropdown();
    openDropdown(cardEl, reminder);
  }

  function openDropdown(cardEl, reminder) {
    const status = reminder.status || 'scheduled';
    const items = [];

    items.push({
      icon: ICON_PENCIL,
      label: 'Edit details',
      onClick: () => { closeDropdown(); openModal(reminder); },
    });

    // M1.2: Snooze (active states only, kill-switch-gated)
    const _snoozeEnabled = (window.RemindersModule && window.RemindersModule.config &&
                            window.RemindersModule.config.snoozeEnabled) === true;
    const _snoozeEligible = (
      status === 'scheduled' ||
      status === 'sending' ||
      (status === 'delivered' && reminder.recurrence)
    );
    if (_snoozeEnabled && _snoozeEligible) {
      items.push({
        icon: ICON_PAUSE,
        label: 'Snooze 10 min',
        onClick: () => { closeDropdown(); _doSnooze(reminder.id, '10m'); },
      });
      items.push({
        icon: ICON_PAUSE,
        label: 'Snooze 1 hour',
        onClick: () => { closeDropdown(); _doSnooze(reminder.id, '1h'); },
      });
      items.push({
        icon: ICON_PAUSE,
        label: 'Snooze tomorrow',
        onClick: () => { closeDropdown(); _doSnooze(reminder.id, 'tomorrow'); },
      });
    }

    if (status === 'scheduled' || status === 'sending') {
      items.push({
        icon: ICON_PAUSE, label: 'Pause',
        onClick: () => { closeDropdown(); patchStatus(reminder.id, 'paused'); },
      });
    } else if (status === 'paused') {
      items.push({
        icon: ICON_PLAY, label: 'Resume',
        onClick: () => { closeDropdown(); patchStatus(reminder.id, 'scheduled'); },
      });
    }

    if (status === 'failed') {
      items.push({
        icon: ICON_RETRY, label: 'Retry',
        onClick: () => { closeDropdown(); retryReminder(reminder.id); },
      });
    }

    items.push({ divider: true });

    items.push({
      icon: ICON_TRASH, label: 'Delete',
      danger: true,
      onClick: () => { closeDropdown(); deleteReminder(reminder.id); },
    });

    const dd = document.createElement('div');
    dd.className = 'reminder-dropdown';
    dd.dataset.forReminder = reminder.id;
    items.forEach(item => {
      if (item.divider) {
        const div = document.createElement('div');
        div.className = 'reminder-dropdown__divider';
        dd.appendChild(div);
        return;
      }
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'reminder-dropdown__item' + (item.danger ? ' reminder-dropdown__item--danger' : '');
      btn.innerHTML = `${item.icon}<span>${escapeHtml(item.label)}</span>`;
      btn.addEventListener('click', (e) => { e.stopPropagation(); item.onClick(); });
      dd.appendChild(btn);
    });

    // Position the dropdown below the kebab, opening down-left (right-aligned)
    document.body.appendChild(dd);
    const kebabRect = cardEl.querySelector('[data-reminder-kebab]').getBoundingClientRect();
    const ddRect = dd.getBoundingClientRect();
    dd.style.top = (window.scrollY + kebabRect.bottom + 6) + 'px';
    dd.style.left = (window.scrollX + kebabRect.right - ddRect.width) + 'px';

    _openDropdown = dd;
  }

  // Close dropdown on outside click or Escape
  document.addEventListener('click', (e) => {
    if (!_openDropdown) return;
    if (_openDropdown.contains(e.target)) return;
    if (e.target.closest('.reminder-card')) return;  // kebab/card handlers already manage this
    closeDropdown();
  });
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && _openDropdown) closeDropdown();
  });

  // ---------- quick-action helpers ----------

  async function patchStatus(reminderId, newStatus) {
    const resp = await fetch(`/reminders/${encodeURIComponent(reminderId)}`, {
      method: 'PATCH',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrf() },
      body: JSON.stringify({ status: newStatus }),
    });
    if (!resp.ok) return;
    const data = await resp.json().catch(() => ({}));
    if (data.reminder) applyReminderToCards(data.reminder);
    else await rehydrateCardsForReminder(reminderId);
    if (window.RemindersModule.refreshSchedulesTab) window.RemindersModule.refreshSchedulesTab();
  }

  async function _doSnooze(reminderId, dur) {
    const resp = await fetch(`/reminders/${encodeURIComponent(reminderId)}/snooze`, {
      method: 'POST',
      credentials: 'same-origin',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRFToken': getCsrf(),
      },
      body: JSON.stringify({ dur }),
    });
    if (!resp.ok) {
      console.warn('snooze failed', resp.status);
      return;
    }
    const data = await resp.json().catch(() => ({}));
    if (data.reminder) {
      applyReminderToCards(data.reminder);
    } else {
      // Fallback: re-fetch the reminder
      if (window.RemindersModule.refreshSchedulesTab) {
        window.RemindersModule.refreshSchedulesTab();
      }
    }
  }

  async function retryReminder(reminderId) {
    const resp = await fetch(`/reminders/${encodeURIComponent(reminderId)}`, {
      method: 'PATCH',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrf() },
      body: JSON.stringify({ _retry: true }),
    });
    if (!resp.ok) return;
    const data = await resp.json().catch(() => ({}));
    if (data.reminder) applyReminderToCards(data.reminder);
    else await rehydrateCardsForReminder(reminderId);
  }

  async function deleteReminder(reminderId) {
    if (!confirm('Delete this reminder?')) return;
    const resp = await fetch(`/reminders/${encodeURIComponent(reminderId)}`, {
      method: 'DELETE',
      credentials: 'same-origin',
      headers: { 'X-CSRFToken': getCsrf() },
    });
    if (resp.ok) {
      document.querySelectorAll(`.reminder-card[data-reminder-id="${reminderId}"]`).forEach(el => el.remove());
      if (window.RemindersModule.refreshSchedulesTab) window.RemindersModule.refreshSchedulesTab();
    }
  }

  // ---------- fetch + re-hydrate ----------

  async function fetchReminder(id) {
    const resp = await fetch(`/reminders/${encodeURIComponent(id)}`, { credentials: 'same-origin' });
    if (!resp.ok) throw new Error(`fetch failed: ${resp.status}`);
    const data = await resp.json();
    return data.reminder;
  }

  function applyReminderToCards(reminder) {
    /** Patch DOM cards in-place from an already-fetched reminder object.
     *  innerHTML replacement blows away the kebab button's click handler,
     *  so we re-attach it after re-rendering. The wrap's click handler
     *  survives because it's bound to the wrap, not its children. */
    if (!reminder || !reminder.id) return;
    document.querySelectorAll(`.reminder-card[data-reminder-id="${reminder.id}"]`).forEach(btn => {
      btn.className = `reminder-card reminder-card--${reminder.status || 'scheduled'}`;
      btn.dataset.reminderJson = JSON.stringify(reminder);
      btn.innerHTML = renderCardInner(reminder);
      const kebab = btn.querySelector('[data-reminder-kebab]');
      if (kebab) {
        kebab.addEventListener('click', (e) => {
          e.stopPropagation();
          const fresh = JSON.parse(btn.dataset.reminderJson || '{}');
          toggleDropdown(btn, fresh);
        });
      }
    });
  }

  async function rehydrateCardsForReminder(reminderId) {
    let fresh;
    try { fresh = await fetchReminder(reminderId); }
    catch { return; }
    applyReminderToCards(fresh);
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

    // M1.1: populate recurrence dropdown
    const recurrenceSel = form.querySelector('[data-reminders-recurrence]');
    if (recurrenceSel) {
      recurrenceSel.value = reminder.recurrence || '';
      // Update dynamic labels based on the fireAt date
      _updateRecurrenceLabels(recurrenceSel, reminder);
    }

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

    // M1.1: include recurrence (empty string → null)
    const recVal = form.elements['recurrence']?.value || null;
    const body = {
      name: form.elements['name'].value.trim(),
      instructions: form.elements['instructions'].value.trim(),
      fireAtLocal,
      timezone: tz,
      recurrence: recVal,
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
      // Server returns the full updated reminder in the response body — skip
      // the redundant GET roundtrip and patch cards in-place.
      if (data.reminder) applyReminderToCards(data.reminder);
      else if (data.id) await rehydrateCardsForReminder(data.id);
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
      const data = await resp.json().catch(() => ({}));
      if (data.reminder) applyReminderToCards(data.reminder);
      else await rehydrateCardsForReminder(id);
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
    const status = reminder.status || 'scheduled';
    li.innerHTML = `
      <span class="schedules-row__icon" aria-hidden="true">${iconForStatus(status)}</span>
      <span class="schedules-row__body">
        <span class="schedules-row__name">${escapeHtml(reminder.name)}</span>
        <span class="schedules-row__when">${escapeHtml(formatWhen(reminder))} · ${STATE_LABEL[reminder.status] || reminder.status}</span>
      </span>
      <button type="button" class="schedules-row__kebab" data-reminder-kebab aria-label="Reminder options">${ICON_KEBAB}</button>`;
    li.addEventListener('click', () => openModal(reminder));
    li.querySelector('[data-reminder-kebab]').addEventListener('click', (e) => {
      e.stopPropagation();
      toggleDropdown(li, reminder);
    });
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
    // Endpoint is @login_required — guests would log a 403 in the console even
    // though we swallow the fetch error. Skip entirely for guests.
    if (document.body.dataset.guestMode === 'true') return;
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

  // ---------- M1.3: Settings → Notifications toggle wiring ----------

  function _initPushSettingsToggle() {
    const pushToggle = document.querySelector('[data-push-settings-toggle]');
    if (!pushToggle || !window.RemindersModule?.push) return;

    window.RemindersModule.push.isSubscribed().then(sub => {
      pushToggle.setAttribute('aria-checked', sub ? 'true' : 'false');
    });

    // Avoid double-binding if called more than once
    if (pushToggle.dataset.pushToggleBound) return;
    pushToggle.dataset.pushToggleBound = '1';

    pushToggle.addEventListener('click', async () => {
      const isOn = pushToggle.getAttribute('aria-checked') === 'true';
      if (!isOn) {
        const r = await window.RemindersModule.push.subscribe();
        if (r.ok) pushToggle.setAttribute('aria-checked', 'true');
      } else {
        if (confirm('Disable push and remove all subscribed devices?')) {
          await window.RemindersModule.push.unsubscribeAll();
          pushToggle.setAttribute('aria-checked', 'false');
        }
      }
    });
  }

  // ---------- DOM ready bootstrap ----------

  document.addEventListener('DOMContentLoaded', () => {
    bindSchedulesTab();
    _initPushSettingsToggle();

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

    // Fire the unseen-failures toast check ~1.5s after load so it doesn't
    // race the initial chat-page paint. Quietly no-ops for guests (the
    // server returns 403 from @login_required, fetch fails, swallowed).
    setTimeout(() => { try { checkUnseenFailures(); } catch {} }, 1500);
  });

  // Settings modal dispatches a custom event when the user switches tabs
  document.addEventListener('settings-tab-shown', (ev) => {
    if (ev.detail && ev.detail.tab === 'schedules') refreshSchedulesTab();
    if (ev.detail && ev.detail.tab === 'notifications') _initPushSettingsToggle();
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
