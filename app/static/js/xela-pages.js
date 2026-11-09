/**
 * xela-pages.js: the pages around the chat.
 *
 * - Projects, Artifacts and Scheduled pages inside the shell (sidebar stays).
 * - Search palette (Ctrl/Cmd+K): chats, projects, artifacts, scheduled, actions.
 * - Sidebar: Pinned / Recents sections, sentence-case titles.
 * - Home: recent artifacts under the composer; model button under the box.
 *
 * Uses existing endpoints only: /projects, /reminders, /chat/list-metadata,
 * /chat/<id>/artifacts. Nothing here writes except deleting a scheduled task.
 */
(function () {
  'use strict';

  const body = document.body;
  if (!body || body.dataset.guestMode === 'true') return;

  const $ = (sel, root) => (root || document).querySelector(sel);
  const esc = (v) => String(v == null ? '' : v).replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[c]));
  const icon = (paths, size) => `<svg viewBox="0 0 24 24" width="${size || 18}" height="${size || 18}" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${paths}</svg>`;
  const I = {
    chat: '<path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>',
    folder: '<path d="M20 20a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.93a2 2 0 0 1-1.66-.9l-.82-1.2A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2Z"/>',
    artifact: '<rect x="3" y="3" width="13" height="13" rx="2"/><path d="M8 21h11a2 2 0 0 0 2-2V8"/>',
    clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    plus: '<path d="M12 5v14M5 12h14"/>',
    search: '<circle cx="11" cy="11" r="7"/><path d="m21 21-4.3-4.3"/>',
    code: '<path d="m16 18 6-6-6-6M8 6l-6 6 6 6"/>',
    doc: '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5M9 13h6M9 17h4"/>',
    trash: '<path d="M3 6h18M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6M10 11v6M14 11v6M9 6V4a2 2 0 0 1 2-2h2a2 2 0 0 1 2 2v2"/>',
    edit: '<path d="M12 20h9"/><path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4Z"/>',
    repeat: '<path d="m17 2 4 4-4 4"/><path d="M3 11v-1a4 4 0 0 1 4-4h14M7 22l-4-4 4-4"/><path d="M21 13v1a4 4 0 0 1-4 4H3"/>',
    sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M6.3 17.7l-1.4 1.4M19.1 4.9l-1.4 1.4"/>',
    inbox: '<path d="M22 12h-6l-2 3h-4l-2-3H2"/><path d="M5.5 5.1 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.5-6.9A2 2 0 0 0 16.8 4H7.2a2 2 0 0 0-1.7 1.1z"/>',
    list: '<path d="M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01"/>',
    bulb: '<path d="M9 18h6M10 22h4"/><path d="M15.1 14c.2-1 .7-1.7 1.4-2.5A4.7 4.7 0 0 0 18 8 6 6 0 0 0 6 8c0 1 .2 2.2 1.5 3.5.7.7 1.2 1.5 1.4 2.5"/>',
    eye: '<path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12Z"/><circle cx="12" cy="12" r="3"/>',
    enter: '<path d="M9 10 4 15l5 5"/><path d="M20 4v7a4 4 0 0 1-4 4H4"/>',
  };

  // ------------------------------------------------------------------ helpers
  function sentenceCase(text) {
    const t = String(text || '').trim();
    if (!t) return t;
    let words = t.split(/\s+/);
    const isUpper = (w) => w.length > 0 && w[0] !== w[0].toLowerCase() && w[0] === w[0].toUpperCase();
    const capped = words.filter(isUpper).length;
    if (words.length > 1 && capped >= Math.max(2, words.length - 1)) {
      words = [words[0]].concat(words.slice(1).map((w) => {
        const rest = w.slice(1);
        return (isUpper(w) && rest === rest.toLowerCase()) ? w.toLowerCase() : w;
      }));
    }
    const out = words.join(' ');
    return out.charAt(0).toUpperCase() + out.slice(1);
  }

  function relDate(value) {
    const d = value ? new Date(value) : null;
    if (!d || isNaN(d)) return '';
    const diff = (Date.now() - d.getTime()) / 1000;
    if (diff < 60) return 'just now';
    if (diff < 3600) return Math.floor(diff / 60) + ' min ago';
    if (diff < 86400) return Math.floor(diff / 3600) + ' h ago';
    if (diff < 86400 * 7) return Math.floor(diff / 86400) + ' d ago';
    return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: d.getFullYear() === new Date().getFullYear() ? undefined : 'numeric' });
  }

  function whenLabel(value) {
    const d = value ? new Date(value) : null;
    if (!d || isNaN(d)) return '';
    return d.toLocaleString(undefined, { weekday: 'short', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });
  }

  async function getJSON(url) {
    const res = await fetch(url, { credentials: 'same-origin', headers: { Accept: 'application/json' } });
    if (!res.ok) throw new Error(url + ' -> HTTP ' + res.status);
    return res.json();
  }

  function csrf() {
    const m = document.querySelector('meta[name="csrf-token"]');
    return m ? m.getAttribute('content') : '';
  }

  const cm = () => window.chatManager || null;

  // ------------------------------------------------------------------- data
  const cache = {};
  function cached(key, ttlMs, loader) {
    const hit = cache[key];
    if (hit && Date.now() - hit.at < ttlMs) return hit.promise;
    const promise = loader().catch((err) => { delete cache[key]; throw err; });
    cache[key] = { at: Date.now(), promise };
    return promise;
  }

  const loadProjects = () => cached('projects', 15000, async () => (await getJSON('/projects')).projects || []);
  const loadReminders = () => cached('reminders', 10000, async () => (await getJSON('/reminders')).reminders || []);
  const loadSessions = () => cached('sessions', 20000, async () => (await getJSON('/chat/list-metadata')).sessions || []);

  // Artifacts live under their chat: list the chats that have one, then ask each (capped).
  const loadArtifacts = () => cached('artifacts', 30000, async () => {
    const sessions = (await loadSessions()).filter((s) => s.has_artifact).slice(0, 14);
    const lists = await Promise.all(sessions.map(async (s) => {
      try {
        const data = await getJSON(`/chat/${encodeURIComponent(s.session_id)}/artifacts`);
        return (data.artifacts || []).map((a) => Object.assign({}, a, {
          sessionId: s.session_id, sessionName: sentenceCase(s.session_name || ''),
        }));
      } catch (_) { return []; }
    }));
    return lists.flat().sort((a, b) => String(b.updatedAt || '').localeCompare(String(a.updatedAt || '')));
  });

  // ------------------------------------------------------------- navigation
  function goChat(sessionId) {
    window.location.href = '/c/' + encodeURIComponent(sessionId);
  }
  function goArtifact(a) {
    window.location.href = '/c/' + encodeURIComponent(a.sessionId) + '?artifact=' + encodeURIComponent(a.artifactId);
  }
  function goProject(p) {
    window.location.href = '/p/' + encodeURIComponent(p.id);
  }
  function newChat() {
    closeSurface();
    const btn = $('#action-new-chat');
    if (btn) btn.click();
  }
  function newProject() {
    const btn = $('#project-create-btn');
    if (btn) btn.click();
  }
  function newTask(reminder) {
    if (window.RemindersModule && typeof window.RemindersModule.openModal === 'function') {
      window.RemindersModule.openModal(reminder || {});
      const modal = $('#reminders-modal');
      if (modal) {
        const title = $('#reminders-modal-title', modal);
        if (title) title.textContent = reminder && reminder.id ? 'Edit task' : 'New task';
        if (!modal.dataset.xcWired) {
          modal.dataset.xcWired = '1';
          document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape' && !modal.hidden) {
              e.stopPropagation();
              window.RemindersModule.closeModal();
            }
          }, true);
          // the list on the Scheduled page follows whatever the dialog saved or deleted
          new MutationObserver(() => {
            if (modal.hidden && current === 'scheduled') setTimeout(() => {
              delete cache.reminders;
              if (current === 'scheduled') renderScheduled(host());
            }, 350);
          }).observe(modal, { attributes: true, attributeFilter: ['hidden'] });
        }
      }
      return true;
    }
    return false;
  }
  function askInChat(text) {
    closeSurface();
    const btn = $('#action-new-chat');
    if (btn) btn.click();
    setTimeout(() => {
      const prompt = $('#prompt');
      if (!prompt) return;
      prompt.value = text;
      prompt.dispatchEvent(new Event('input', { bubbles: true }));
      prompt.focus();
    }, 250);
  }

  // ---------------------------------------------------------------- surfaces
  const SURFACES = {
    projects: { path: '/all-projects', title: 'Projects', render: renderProjects },
    artifacts: { path: '/artifacts', title: 'Artifacts', render: renderArtifacts },
    scheduled: { path: '/scheduled', title: 'Scheduled', render: renderScheduled },
  };
  let current = null;
  const host = () => $('#xc-surface');

  function setNavPressed(name) {
    document.querySelectorAll('[data-xc-surface]').forEach((b) => {
      b.setAttribute('aria-pressed', String(b.dataset.xcSurface === name));
    });
  }

  function shell(title, actionsHtml, bodyHtml) {
    return `<div class="xc-page">
      <header class="xc-page__head">
        <h1 class="xc-page__title">${esc(title)}</h1>
        <div class="xc-page__actions">${actionsHtml || ''}</div>
      </header>
      <div class="xc-page__body">${bodyHtml}</div>
    </div>`;
  }
  const loading = '<div class="xc-skeleton-grid"><span></span><span></span><span></span><span></span></div>';
  const failed = (what) => `<div class="xc-empty"><p>Couldn't load ${esc(what)}.</p><button type="button" class="xc-btn" data-xc-retry>Try again</button></div>`;

  function openSurface(name, opts) {
    const def = SURFACES[name];
    const el = host();
    if (!def || !el) return;
    const manager = cm();
    if (manager) {
      if (manager.chatsPageActive && manager.hideChatsPage) manager.hideChatsPage({ updateHistory: false });
      if (manager.imagesPageActive && manager.hideImagesPage) manager.hideImagesPage({ updateHistory: false });
    }
    current = name;
    el.hidden = false;
    el.dataset.surface = name;
    body.classList.add('xc-surface-open');
    setNavPressed(name);
    document.title = def.title + ' · Xela';
    if (!(opts && opts.fromHistory) && window.location.pathname !== def.path) {
      try { window.history.pushState({ xcSurface: name }, '', def.path); } catch (_) { /* file:// */ }
    }
    def.render(el);
    el.scrollTop = 0;
  }

  function closeSurface() {
    const el = host();
    if (!current || !el) return;
    const def = SURFACES[current];
    current = null;
    el.hidden = true;
    el.innerHTML = '';
    body.classList.remove('xc-surface-open');
    setNavPressed(null);
    if (def && window.location.pathname === def.path) {
      try { window.history.replaceState({}, '', '/'); } catch (_) { /* ignore */ }
    }
    if (document.title.endsWith('· Xela') && !body.classList.contains('has-messages')) document.title = 'Xela';
  }

  function surfaceFromPath() {
    return Object.keys(SURFACES).find((k) => SURFACES[k].path === window.location.pathname) || null;
  }

  // -- Projects
  function renderProjects(el) {
    const actions = `<button type="button" class="xc-btn xc-btn--primary" data-xc-act="new-project">${icon(I.plus, 15)}<span>New project</span></button>`;
    el.innerHTML = shell('Projects', actions, loading);
    loadProjects().then((projects) => {
      if (current !== 'projects') return;
      const bodyEl = $('.xc-page__body', el);
      if (!projects.length) {
        bodyEl.innerHTML = `<div class="xc-empty">
          <span class="xc-empty__icon">${icon(I.folder, 30)}</span>
          <h2>No projects yet</h2>
          <p>Projects keep related chats, files and instructions together.</p>
          <button type="button" class="xc-btn xc-btn--primary" data-xc-act="new-project">Create a project</button>
        </div>`;
        return;
      }
      bodyEl.innerHTML = `<div class="xc-grid xc-grid--2">${projects.map((p, i) => `
        <a class="xc-card xc-card--row" href="/p/${encodeURIComponent(p.id)}" data-i="${i}">
          <span class="xc-card__icon" style="--tint:${esc(p.color || '')}">${icon(I.folder, 18)}</span>
          <span class="xc-card__text">
            <span class="xc-card__title">${esc(p.name)}</span>
            <span class="xc-card__meta">${esc(relDate(p.updated_at || p.created_at) ? 'Updated ' + relDate(p.updated_at || p.created_at) : 'Project')}</span>
          </span>
        </a>`).join('')}</div>`;
    }).catch(() => { if (current === 'projects') $('.xc-page__body', el).innerHTML = failed('your projects'); });
  }

  // -- Artifacts
  function artifactKind(a) {
    const t = String((a.meta && a.meta.primaryLanguage) || a.type || '').toLowerCase();
    const entry = String(a.entrypointPath || '').toLowerCase();
    if (/\.(html?|jsx|tsx|svg)$/.test(entry) || /html|react|web|svg/.test(t)) return { label: 'Web', paths: I.eye };
    if (/\.(md|txt|docx?)$/.test(entry) || /doc|markdown|text/.test(t)) return { label: 'Document', paths: I.doc };
    return { label: 'Code', paths: I.code };
  }

  function artifactCard(a) {
    const kind = artifactKind(a);
    const versions = a.versionCount > 1 ? ` · ${a.versionCount} versions` : '';
    return `<a class="xc-card xc-card--art" href="/c/${encodeURIComponent(a.sessionId)}?artifact=${encodeURIComponent(a.artifactId)}">
      <span class="xc-art__thumb" data-kind="${esc(kind.label)}">
        <span class="xc-art__sheet"><i></i><i></i><i></i><i></i></span>
        <span class="xc-art__badge">${icon(kind.paths, 14)}${esc(kind.label)}</span>
      </span>
      <span class="xc-card__text">
        <span class="xc-card__title">${esc(sentenceCase(a.title || 'Artifact'))}</span>
        <span class="xc-card__meta">${esc(relDate(a.updatedAt || a.createdAt) || 'Artifact')}${esc(versions)}</span>
      </span>
    </a>`;
  }

  function renderArtifacts(el) {
    el.innerHTML = shell('Artifacts', '', loading);
    loadArtifacts().then((artifacts) => {
      if (current !== 'artifacts') return;
      const bodyEl = $('.xc-page__body', el);
      const starters = `<p class="xc-section-label">Make something new</p>
        <div class="xc-starters">
          <button type="button" class="xc-starter" data-xc-ask="Create an interactive web page artifact: " style="--tint:#d97757">${icon(I.eye, 22)}<span>Web page</span></button>
          <button type="button" class="xc-starter" data-xc-ask="Write a document as an artifact about: " style="--tint:#5b8def">${icon(I.doc, 22)}<span>Document</span></button>
          <button type="button" class="xc-starter" data-xc-ask="Build a small app as an artifact that " style="--tint:#8b6ff0">${icon(I.code, 22)}<span>App</span></button>
          <button type="button" class="xc-starter" data-xc-ask="Create a chart artifact from this data: " style="--tint:#4caf7d">${icon('<path d="M4 20V10M10 20V4M16 20v-7M22 20H2"/>', 22)}<span>Chart</span></button>
        </div>`;
      if (!artifacts.length) {
        bodyEl.innerHTML = starters + `<div class="xc-empty xc-empty--inline">
          <span class="xc-empty__icon">${icon(I.artifact, 30)}</span>
          <h2>No artifacts yet</h2>
          <p>Ask Xela to build a page, a document or a small app. It will show up here.</p>
        </div>`;
        return;
      }
      bodyEl.innerHTML = starters + `<p class="xc-section-label">Your artifacts</p><div class="xc-grid xc-grid--3">${artifacts.map(artifactCard).join('')}</div>`;
    }).catch(() => { if (current === 'artifacts') $('.xc-page__body', el).innerHTML = failed('your artifacts'); });
  }

  // -- Scheduled
  const TEMPLATES = [
    { paths: I.sun, name: 'Daily briefing', desc: 'What needs your attention today.', when: 'Weekdays at 8:00', ask: 'Every weekday at 8:00, remind me to review what needs my attention today.' },
    { paths: I.inbox, name: 'Inbox triage', desc: 'Go through your inbox and reply to anything urgent.', when: 'Weekdays at 9:00', ask: 'Every weekday at 9:00, remind me to triage my inbox and reply to anything urgent.' },
    { paths: I.list, name: 'Weekly review', desc: 'A Friday summary of what happened this week.', when: 'Every Friday at 16:00', ask: 'Every Friday at 16:00, remind me to write my weekly review.' },
    { paths: I.bulb, name: 'Content ideas', desc: 'Draft a few post ideas for the week.', when: 'Every Monday at 9:00', ask: 'Every Monday at 9:00, remind me to draft content ideas for this week.' },
  ];
  const RECUR = { DAILY: 'Every day', WEEKDAYS: 'Weekdays', WEEKLY: 'Every week', MONTHLY: 'Every month', YEARLY: 'Every year' };

  function reminderRow(r, i) {
    const status = String(r.status || '').toLowerCase();
    const done = status && status !== 'scheduled';
    const when = r.snoozedUntil || r.fireAt;
    return `<li class="xc-task${done ? ' is-done' : ''}" data-i="${i}">
      <span class="xc-card__icon">${icon(r.recurrence ? I.repeat : I.clock, 18)}</span>
      <span class="xc-card__text">
        <span class="xc-card__title">${esc(r.name || 'Reminder')}</span>
        <span class="xc-card__meta">${esc(whenLabel(when))}${r.recurrence ? ' · ' + esc(RECUR[r.recurrence] || r.recurrence) : ''}${done ? ' · ' + esc(status.charAt(0).toUpperCase() + status.slice(1)) : ''}</span>
      </span>
      <span class="xc-task__actions">
        <button type="button" class="xc-icon-btn" data-xc-task="edit" aria-label="Edit task" title="Edit">${icon(I.edit, 16)}</button>
        <button type="button" class="xc-icon-btn" data-xc-task="delete" aria-label="Delete task" title="Delete">${icon(I.trash, 16)}</button>
      </span>
    </li>`;
  }

  let scheduledItems = [];
  function renderScheduled(el) {
    const actions = `<button type="button" class="xc-btn xc-btn--primary" data-xc-act="new-task">${icon(I.plus, 15)}<span>New task</span></button>`;
    el.innerHTML = shell('Scheduled', actions, loading);
    loadReminders().then((items) => {
      if (current !== 'scheduled') return;
      scheduledItems = items.slice().sort((a, b) => {
        const ad = String(a.status || '') === 'scheduled' ? 0 : 1;
        const bd = String(b.status || '') === 'scheduled' ? 0 : 1;
        if (ad !== bd) return ad - bd;
        return ad === 0 ? String(a.fireAt || '').localeCompare(String(b.fireAt || '')) : String(b.fireAt || '').localeCompare(String(a.fireAt || ''));
      });
      const templates = `<p class="xc-section-label">Templates</p><div class="xc-grid xc-grid--2">${TEMPLATES.map((t, i) => `
        <button type="button" class="xc-card xc-card--row xc-card--tpl" data-xc-tpl="${i}">
          <span class="xc-card__icon">${icon(t.paths, 18)}</span>
          <span class="xc-card__text">
            <span class="xc-card__title">${esc(t.name)}</span>
            <span class="xc-card__meta">${esc(t.desc)}</span>
            <span class="xc-card__when">${icon(I.clock, 12)}${esc(t.when)}</span>
          </span>
        </button>`).join('')}</div>`;
      const bodyEl = $('.xc-page__body', el);
      if (!scheduledItems.length) {
        bodyEl.innerHTML = `<div class="xc-empty xc-empty--inline">
          <span class="xc-empty__icon">${icon(I.clock, 34)}</span>
          <h2>No scheduled tasks yet</h2>
          <p>Xela can remind you by email, once or on a schedule. Ask in chat or set one up here.</p>
          <button type="button" class="xc-btn" data-xc-act="new-task">Set up manually</button>
        </div>` + templates;
        return;
      }
      bodyEl.innerHTML = `<ul class="xc-tasks">${scheduledItems.map(reminderRow).join('')}</ul>` + templates;
    }).catch(() => { if (current === 'scheduled') $('.xc-page__body', el).innerHTML = failed('your scheduled tasks'); });
  }

  async function deleteTask(r, rowEl) {
    if (!window.confirm(`Delete "${r.name || 'this task'}"?`)) return;
    rowEl.classList.add('is-busy');
    try {
      const res = await fetch('/reminders/' + encodeURIComponent(r.id), {
        method: 'DELETE', credentials: 'same-origin', headers: { 'X-CSRFToken': csrf() },
      });
      if (!res.ok) throw new Error('HTTP ' + res.status);
      delete cache.reminders;
      if (current === 'scheduled') renderScheduled(host());
    } catch (_) {
      rowEl.classList.remove('is-busy');
      if (window.showToast) window.showToast('Could not delete the task', 'error');
    }
  }

  function initSurfaces() {
    const el = host();
    if (!el) return;

    document.addEventListener('click', (e) => {
      const nav = e.target.closest('[data-xc-surface]');
      if (nav) {
        e.preventDefault();
        if (current === nav.dataset.xcSurface) closeSurface();
        else openSurface(nav.dataset.xcSurface);
        return;
      }
      // Anything in the sidebar that leads back to chat closes the page.
      if (current && e.target.closest('#tab-list .chat-tab, #action-new-chat, #action-chats, #action-images, .sidebar-logo-link, .sidebar-header-ghost')) {
        closeSurface();
      }
    }, true);

    el.addEventListener('click', (e) => {
      const act = e.target.closest('[data-xc-act]');
      if (act) {
        if (act.dataset.xcAct === 'new-project') newProject();
        if (act.dataset.xcAct === 'new-task' && !newTask()) askInChat('Remind me to ');
        return;
      }
      if (e.target.closest('[data-xc-retry]')) {
        Object.keys(cache).forEach((k) => delete cache[k]);
        if (current) SURFACES[current].render(el);
        return;
      }
      const ask = e.target.closest('[data-xc-ask]');
      if (ask) { askInChat(ask.dataset.xcAsk); return; }
      const tpl = e.target.closest('[data-xc-tpl]');
      if (tpl) { askInChat(TEMPLATES[Number(tpl.dataset.xcTpl)].ask); return; }
      const taskBtn = e.target.closest('[data-xc-task]');
      if (taskBtn) {
        const row = taskBtn.closest('.xc-task');
        const r = scheduledItems[Number(row.dataset.i)];
        if (!r) return;
        if (taskBtn.dataset.xcTask === 'delete') deleteTask(r, row);
        else if (!newTask(r)) askInChat('Change my reminder "' + (r.name || '') + '" to ');
      }
    });

    // Lists change when a project or a reminder is created elsewhere on the page.
    const refresh = (key, name) => () => { delete cache[key]; if (current === name) SURFACES[name].render(el); };
    document.addEventListener('xela:projects-changed', refresh('projects', 'projects'));
    window.addEventListener('focus', () => { if (current === 'scheduled') refresh('reminders', 'scheduled')(); });

    window.addEventListener('popstate', () => {
      const name = surfaceFromPath();
      if (name) openSurface(name, { fromHistory: true });
      else if (current) { const c = current; current = null; body.classList.remove('xc-surface-open'); el.hidden = true; el.innerHTML = ''; setNavPressed(null); void c; }
    });

    const initial = body.dataset.initialSurface;
    if (SURFACES[initial]) openSurface(initial, { fromHistory: true });
  }

  // ----------------------------------------------------------------- palette
  const TABS = [['all', 'All'], ['chats', 'Chats'], ['projects', 'Projects'], ['artifacts', 'Artifacts'], ['scheduled', 'Scheduled']];
  const pal = { el: null, input: null, list: null, tab: 'all', items: [], index: 0, token: 0 };

  function buildPalette() {
    const wrap = document.createElement('div');
    wrap.className = 'xc-palette';
    wrap.hidden = true;
    wrap.innerHTML = `<div class="xc-palette__scrim" data-pal-close></div>
      <div class="xc-palette__dialog" role="dialog" aria-modal="true" aria-label="Search">
        <div class="xc-palette__top">
          <span class="xc-palette__glass">${icon(I.search, 17)}</span>
          <input class="xc-palette__input" type="text" placeholder="Search chats, projects, artifacts…" autocomplete="off" spellcheck="false" aria-label="Search">
          <button type="button" class="xc-icon-btn" data-pal-close aria-label="Close">${icon('<path d="M18 6 6 18M6 6l12 12"/>', 16)}</button>
        </div>
        <div class="xc-palette__tabs" role="tablist">${TABS.map(([k, label]) => `<button type="button" role="tab" data-pal-tab="${k}" aria-selected="${k === 'all'}">${label}</button>`).join('')}</div>
        <div class="xc-palette__list" role="listbox"></div>
        <div class="xc-palette__foot">
          <span><kbd>Esc</kbd> Close</span><span><kbd>←</kbd><kbd>→</kbd> Change type</span><span><kbd>↑</kbd><kbd>↓</kbd> Navigate</span><span class="xc-palette__foot-end"><kbd>↵</kbd> Open</span>
        </div>
      </div>`;
    document.body.appendChild(wrap);
    pal.el = wrap;
    pal.input = $('.xc-palette__input', wrap);
    pal.list = $('.xc-palette__list', wrap);

    wrap.addEventListener('click', (e) => {
      if (e.target.closest('[data-pal-close]')) { closePalette(); return; }
      const tab = e.target.closest('[data-pal-tab]');
      if (tab) { setTab(tab.dataset.palTab); return; }
      const row = e.target.closest('[data-pal-i]');
      if (row) runItem(pal.items[Number(row.dataset.palI)]);
    });
    pal.list.addEventListener('mousemove', (e) => {
      const row = e.target.closest('[data-pal-i]');
      if (row && Number(row.dataset.palI) !== pal.index) { pal.index = Number(row.dataset.palI); paintActive(); }
    });
    pal.input.addEventListener('input', renderPalette);
    wrap.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); closePalette(); return; }
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
        e.preventDefault();
        if (!pal.items.length) return;
        pal.index = (pal.index + (e.key === 'ArrowDown' ? 1 : -1) + pal.items.length) % pal.items.length;
        paintActive(true);
        return;
      }
      if ((e.key === 'ArrowLeft' || e.key === 'ArrowRight') && !pal.input.value) {
        e.preventDefault();
        const i = TABS.findIndex(([k]) => k === pal.tab);
        setTab(TABS[(i + (e.key === 'ArrowRight' ? 1 : -1) + TABS.length) % TABS.length][0]);
        return;
      }
      if (e.key === 'Enter') { e.preventDefault(); runItem(pal.items[pal.index]); }
    });
  }

  function setTab(tab) {
    pal.tab = tab;
    pal.el.querySelectorAll('[data-pal-tab]').forEach((b) => b.setAttribute('aria-selected', String(b.dataset.palTab === tab)));
    renderPalette();
    pal.input.focus();
  }

  function paintActive(scroll) {
    pal.list.querySelectorAll('[data-pal-i]').forEach((row) => {
      const on = Number(row.dataset.palI) === pal.index;
      row.classList.toggle('is-active', on);
      row.setAttribute('aria-selected', String(on));
      if (on && scroll) row.scrollIntoView({ block: 'nearest' });
    });
  }

  function runItem(item) {
    if (!item) return;
    closePalette();
    item.run();
  }

  function chatItems(query) {
    const manager = cm();
    let sessions = [];
    if (manager && typeof manager.getSortedSessionsArray === 'function') {
      sessions = manager.getSortedSessionsArray();
      if (query && typeof manager.filterSessionsByQuery === 'function') {
        const prev = manager.searchQuery;
        manager.searchQuery = query;
        try { sessions = manager.filterSessionsByQuery(sessions); } finally { manager.searchQuery = prev; }
      }
    }
    return sessions.slice(0, query ? 20 : 8).map((s) => ({
      group: query ? 'Chats' : 'Recents', paths: I.chat,
      title: sentenceCase(s.session_name || 'New chat'), meta: relDate(s.last_updated || s.created_at),
      run: () => goChat(s.session_id),
    }));
  }

  async function renderPalette() {
    const token = ++pal.token;
    const query = pal.input.value.trim();
    const q = query.toLowerCase();
    const match = (text) => !q || String(text || '').toLowerCase().includes(q);
    const want = (k) => pal.tab === 'all' || pal.tab === k;
    let items = [];

    if (want('chats')) items = items.concat(chatItems(query));
    const pending = [];
    if (want('projects')) pending.push(loadProjects().then((list) => list.filter((p) => match(p.name)).slice(0, pal.tab === 'all' && !q ? 3 : 12).map((p) => ({
      group: 'Projects', paths: I.folder, title: p.name, meta: relDate(p.updated_at || p.created_at), run: () => goProject(p),
    }))).catch(() => []));
    if (want('artifacts') && (q || pal.tab === 'artifacts')) pending.push(loadArtifacts().then((list) => list.filter((a) => match(a.title)).slice(0, 12).map((a) => ({
      group: 'Artifacts', paths: I.artifact, title: sentenceCase(a.title || 'Artifact'), meta: a.sessionName, run: () => goArtifact(a),
    }))).catch(() => []));
    if (want('scheduled') && (q || pal.tab === 'scheduled')) pending.push(loadReminders().then((list) => list.filter((r) => match(r.name)).slice(0, 12).map((r) => ({
      group: 'Scheduled', paths: I.clock, title: r.name || 'Reminder', meta: whenLabel(r.fireAt), run: () => openSurface('scheduled'),
    }))).catch(() => []));

    const actions = [
      { group: 'Actions', paths: I.plus, title: 'New chat', run: newChat },
      { group: 'Actions', paths: I.folder, title: 'New project', run: newProject },
      { group: 'Actions', paths: I.clock, title: 'New scheduled task', run: () => { if (!newTask()) askInChat('Remind me to '); } },
      { group: 'Actions', paths: I.artifact, title: 'Browse artifacts', run: () => openSurface('artifacts') },
    ].filter((a) => pal.tab === 'all' && match(a.title));

    const paint = (list) => {
      if (token !== pal.token) return;
      pal.items = list;
      pal.index = 0;
      if (!list.length) {
        pal.list.innerHTML = `<div class="xc-palette__empty">${q ? 'No results for “' + esc(query) + '”' : 'Nothing here yet'}</div>`;
        return;
      }
      let html = '';
      let group = null;
      list.forEach((item, i) => {
        if (item.group !== group) { group = item.group; html += `<div class="xc-palette__group">${esc(group)}</div>`; }
        html += `<div class="xc-palette__row" role="option" data-pal-i="${i}">
          <span class="xc-palette__icon">${icon(item.paths, 16)}</span>
          <span class="xc-palette__title">${esc(item.title)}</span>
          ${item.meta ? `<span class="xc-palette__meta">${esc(item.meta)}</span>` : ''}
          <span class="xc-palette__enter">${icon(I.enter, 14)}</span>
        </div>`;
      });
      pal.list.innerHTML = html;
      paintActive();
    };

    paint(items.concat(actions));
    if (pending.length) {
      const extra = (await Promise.all(pending)).flat();
      paint(items.concat(extra, actions));
    }
  }

  function openPalette() {
    if (!pal.el) buildPalette();
    const manager = cm();
    if (manager && typeof manager.closeHistorySearchModal === 'function') {
      try { manager.closeHistorySearchModal(); } catch (_) { /* not open */ }
    }
    pal.el.hidden = false;
    body.classList.add('xc-palette-open');
    pal.input.value = '';
    setTab('all');
    requestAnimationFrame(() => pal.input.focus());
  }

  function closePalette() {
    if (!pal.el || pal.el.hidden) return;
    pal.el.hidden = true;
    body.classList.remove('xc-palette-open');
  }

  function initPalette() {
    // Take over the sidebar Search row and Ctrl/Cmd+K from the older search dialog.
    document.addEventListener('click', (e) => {
      if (e.target.closest('#action-search-chat')) {
        e.preventDefault();
        e.stopImmediatePropagation();
        openPalette();
      }
    }, true);
    window.addEventListener('keydown', (e) => {
      if ((e.ctrlKey || e.metaKey) && !e.shiftKey && !e.altKey && e.key.toLowerCase() === 'k') {
        e.preventDefault();
        e.stopImmediatePropagation();
        if (pal.el && !pal.el.hidden) closePalette(); else openPalette();
      }
    }, true);
  }

  // ----------------------------------------------------------------- sidebar
  function initSidebar() {
    const first = $('#tab-list');
    if (!first) return;
    // Watch the section, not the list: the app re-renders rows in several passes and
    // may swap the list element itself.
    const root = first.closest('.sidebar-section') || first.parentElement;
    const options = { childList: true, subtree: true };
    let queued = false;
    let observer = null;

    const tidy = () => {
      queued = false;
      const list = $('#tab-list');
      if (!list) return;
      const header = $('.sidebar-section-header', root);
      observer.disconnect();
      try {
        list.querySelectorAll('.xc-list-label').forEach((n) => n.remove());
        const tabs = [...list.querySelectorAll('li.chat-tab')];
        tabs.forEach((tab) => {
          const name = $('.tab-name', tab);
          if (!name || name.isContentEditable) return;
          const raw = name.textContent;
          // A title that is still being typed out ends in a space now and then;
          // rewriting it at that moment would swallow the space.
          if (raw !== raw.trim()) return;
          const next = sentenceCase(raw);
          if (next && next !== raw) name.textContent = next;
        });
        const pinned = tabs.filter((t) => t.querySelector('.pinned-toggle'));
        const firstRecent = tabs.find((t) => !t.querySelector('.pinned-toggle'));
        // pinned chats always sit above the rest, whatever order they arrived in
        if (firstRecent) {
          pinned.forEach((t) => {
            if (firstRecent.compareDocumentPosition(t) & Node.DOCUMENT_POSITION_FOLLOWING) {
              list.insertBefore(t, firstRecent);
            }
          });
        }
        if (header) header.textContent = pinned.length ? 'Pinned' : 'Recents';
        if (pinned.length && firstRecent) {
          const label = document.createElement('li');
          label.className = 'xc-list-label';
          label.setAttribute('aria-hidden', 'true');
          label.textContent = 'Recents';
          list.insertBefore(label, firstRecent);
        }
      } finally {
        observer.observe(root, options);
      }
    };

    observer = new MutationObserver(() => {
      if (queued) return;
      queued = true;
      requestAnimationFrame(tidy);
    });
    observer.observe(root, options);
    tidy();
  }

  // -------------------------------------------------------------------- home
  function initComposerMeta() {
    const btn = $('#model-menu-button');
    const row = $('.chat-shell__composer .input-row');
    if (!btn || !row || window.innerWidth <= 768) return;
    const meta = document.createElement('div');
    meta.className = 'xc-composer-meta';
    row.insertAdjacentElement('afterend', meta);
    meta.appendChild(btn);
  }

  function initHomeArtifacts() {
    const options = $('.options');
    if (!options) return;
    const box = document.createElement('div');
    box.className = 'xc-home-artifacts';
    box.hidden = true;
    options.insertAdjacentElement('afterend', box);
    const run = () => loadArtifacts().then((list) => {
      if (!list.length) return;
      box.innerHTML = `<div class="xc-home-artifacts__head"><span>Recent artifacts</span><button type="button" class="xc-link" data-xc-surface="artifacts">View all</button></div>
        <div class="xc-grid xc-grid--3">${list.slice(0, 3).map(artifactCard).join('')}</div>`;
      box.hidden = false;
    }).catch(() => { /* optional block */ });
    if ('requestIdleCallback' in window) window.requestIdleCallback(run, { timeout: 2500 }); else setTimeout(run, 800);
  }

  // Opened from an artifact card: /c/<session>?artifact=<id>
  function openArtifactFromUrl() {
    const params = new URLSearchParams(window.location.search);
    const artifactId = params.get('artifact');
    const m = /^\/c\/([^/]+)/.exec(window.location.pathname);
    if (!artifactId || !m) return;
    let tries = 0;
    const attempt = () => {
      const panel = window.artifactPanel;
      if (panel && typeof panel.openFromReference === 'function') {
        try { panel.openFromReference({ artifactId }, decodeURIComponent(m[1])); } catch (_) { /* ignore */ }
        params.delete('artifact');
        const qs = params.toString();
        try { window.history.replaceState(window.history.state, '', window.location.pathname + (qs ? '?' + qs : '')); } catch (_) { /* ignore */ }
        return;
      }
      if (++tries < 20) setTimeout(attempt, 300);
    };
    setTimeout(attempt, 600);
  }

  function boot() {
    initSurfaces();
    initPalette();
    initSidebar();
    initComposerMeta();
    initHomeArtifacts();
    openArtifactFromUrl();
    window.XelaPages = { openSurface, closeSurface, openPalette, closePalette, sentenceCase };
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
  else boot();
})();
