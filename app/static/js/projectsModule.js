/**
 * projectsModule.js — Projects sidebar state, list render, create modal.
 * Spec: docs/superpowers/specs/2026-05-22-projects-design.md
 */
(function () {
  'use strict';

  const FOLDER_SVG = `
    <svg class="folder-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor"
         stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
      <path d="M20 20a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.93a2 2 0 0 1-1.66-.9l-.82-1.2A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2Z"/>
      <path d="M2 10h20"/>
    </svg>
  `;

  const state = { projects: [], loaded: false, creating: false, lastFocused: null };
  const els = {};

  function cache() {
    els.section = document.getElementById('projects-section');
    els.list = document.getElementById('project-list');
    els.createBtn = document.getElementById('project-create-btn');
    els.moreHint = document.getElementById('project-more-hint');
    els.modal = document.getElementById('project-create-modal');
    els.modalInput = document.getElementById('project-name-input');
    els.modalCreate = document.getElementById('project-modal-create');
    els.modalError = document.getElementById('project-modal-error');
    els.modalContent = els.modal?.querySelector('.project-modal-content');
  }

  async function fetchProjects() {
    const resp = await fetch('/projects', { credentials: 'same-origin' });
    if (!resp.ok) throw new Error(`GET /projects ${resp.status}`);
    const body = await resp.json();
    return Array.isArray(body.projects) ? body.projects : [];
  }

  function renderList() {
    if (!els.list) return;
    // Wipe only the project rows; the always-on "New project" newrow stays.
    els.list.querySelectorAll('.sidebar-project-row').forEach((n) => n.remove());
    state.projects.forEach((project) => {
      const li = document.createElement('li');
      li.className = 'sidebar-project-row';
      li.dataset.projectId = project.id;
      li.tabIndex = 0;
      li.setAttribute('role', 'link');
      li.setAttribute('aria-label', `Open project ${project.name}`);
      li.style.setProperty('--project-accent', project.color);
      li.innerHTML = `
        ${FOLDER_SVG}
        <span class="name">${escapeHtml(project.name)}</span>
        <button type="button" class="sidebar-project-menu-btn" aria-label="Options for ${escapeHtml(project.name)}" data-project-menu>
          <i class="fas fa-ellipsis-h" aria-hidden="true"></i>
        </button>
      `;
      li.addEventListener('click', (e) => {
        if (e.target.closest('[data-project-menu]')) return;
        window.location.href = `/p/${encodeURIComponent(project.id)}`;
      });
      li.addEventListener('keydown', (e) => {
        if ((e.key === 'Enter' || e.key === ' ') && e.target === li) {
          e.preventDefault();
          window.location.href = `/p/${encodeURIComponent(project.id)}`;
        }
      });
      li.querySelector('[data-project-menu]').addEventListener('click', (e) => {
        e.stopPropagation();
        openProjectRowMenu(project, e.currentTarget);
      });
      attachDropTarget(li, project.id);
      els.list.appendChild(li);
    });
  }

  /* ---------- Project-row ⋯ menu (Share / Rename / Delete) ---------- */
  function openProjectRowMenu(project, anchor) {
    document.querySelectorAll('.sidebar-project-menu-popover').forEach((n) => n.remove());

    const m = document.createElement('div');
    m.className = 'sidebar-project-menu-popover';
    m.innerHTML = `
      <button type="button" data-pact="share"><i class="fas fa-share-alt"></i><span>Share project</span></button>
      <button type="button" data-pact="rename"><i class="fas fa-edit"></i><span>Rename project</span></button>
      <button type="button" data-pact="delete" class="danger"><i class="fas fa-trash"></i><span>Delete project</span></button>
    `;
    document.body.appendChild(m);
    const rect = anchor.getBoundingClientRect();
    m.style.position = 'fixed';
    const menuTop = Math.min(rect.bottom + 4, window.innerHeight - m.offsetHeight - 8);
    const menuLeft = Math.min(
      Math.max(8, rect.left - m.offsetWidth + rect.width),
      window.innerWidth - m.offsetWidth - 8,
    );
    m.style.top = `${Math.max(8, menuTop)}px`;
    m.style.left = `${Math.max(8, menuLeft)}px`;
    m.style.zIndex = '1500';

    m.addEventListener('click', (ev) => {
      const btn = ev.target.closest('[data-pact]');
      if (!btn) return;
      const act = btn.dataset.pact;
      m.remove();
      if (act === 'share') shareProject(project);
      else if (act === 'rename') renameProject(project);
      else if (act === 'delete') deleteProject(project);
    });

    setTimeout(() => {
      const close = (ev) => { if (!m.contains(ev.target)) { m.remove(); document.removeEventListener('click', close); } };
      document.addEventListener('click', close);
      m.addEventListener('keydown', (ev) => {
        if (ev.key === 'Escape') {
          m.remove();
          document.removeEventListener('click', close);
          anchor.focus();
        }
      });
    }, 0);
  }

  async function shareProject(project) {
    const url = `${window.location.origin}/p/${encodeURIComponent(project.id)}`;
    try {
      await navigator.clipboard.writeText(url);
      window.toast?.success?.('Project link copied');
    } catch (_) {
      window.prompt('Copy this project link:', url);
    }
  }

  async function renameProject(project) {
    const next = window.prompt('Rename project:', project.name);
    if (!next || next.trim() === project.name) return;
    try {
      const resp = await fetch(`/projects/${encodeURIComponent(project.id)}`, {
        method: 'PATCH',
        credentials: 'same-origin',
        headers: jsonHeaders(),
        body: JSON.stringify({ name: next.trim() }),
      });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      await reload();
    } catch (err) {
      window.toast?.error?.(`Couldn't rename: ${err.message}`);
    }
  }

  async function deleteProject(project) {
    // Count chats so the confirm copy reads naturally.
    let count = 0;
    try {
      const r = await fetch(`/chat/history?project_id=${encodeURIComponent(project.id)}&page_size=500`, { credentials: 'same-origin' });
      if (r.ok) {
        const b = await r.json();
        count = Object.values(b.categorized_history || {}).reduce((s, l) => s + l.length, 0);
      }
    } catch (_) {}

    let cascade;
    if (count === 0) {
      if (!confirm(`Delete project "${project.name}"?`)) return;
      cascade = 'keep';
    } else {
      const choice = prompt(
        `Project "${project.name}" contains ${count} chat${count === 1 ? '' : 's'}.\n\n` +
        `Type "keep" to keep the chats (they return to your main history), ` +
        `or "delete" to also delete them.`,
        'keep',
      );
      if (choice === null) return;
      if (choice !== 'keep' && choice !== 'delete') {
        alert('Cancelled — type exactly "keep" or "delete".');
        return;
      }
      if (choice === 'delete' && !confirm(`Permanently delete ${count} chat${count === 1 ? '' : 's'}?`)) return;
      cascade = choice;
    }

    try {
      const resp = await fetch(`/projects/${encodeURIComponent(project.id)}?cascade=${cascade}`, {
        method: 'DELETE', credentials: 'same-origin', headers: jsonHeaders(),
      });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      // If we're currently viewing this project, return to /. Else just refresh sidebar.
      const onThisProject = document.querySelector(`.project-page[data-project-id="${project.id}"]`);
      if (onThisProject) {
        window.location.href = '/';
      } else {
        await reload();
        window.dispatchEvent(new CustomEvent('projects:session-moved'));
      }
    } catch (err) {
      window.toast?.error?.(`Couldn't delete: ${err.message}`);
    }
  }

  function renderSubmenu() {
    const target = document.getElementById('submenu-projects-list');
    if (!target) return;
    target.innerHTML = '';
    if (state.projects.length === 0) {
      target.innerHTML = '<div style="padding:9px 14px;font-size:13px;opacity:0.5">No projects yet</div>';
      return;
    }
    state.projects.forEach((p) => {
      const item = document.createElement('div');
      item.className = 'asi submenu-project-item';
      item.style.cssText = 'display:flex;align-items:center;gap:8px;padding:8px 12px;cursor:pointer;font-size:13.5px;';
      item.dataset.projectId = p.id;
      item.innerHTML = `
        <svg style="width:14px;height:14px;color:${p.color};flex-shrink:0" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 20a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.93a2 2 0 0 1-1.66-.9l-.82-1.2A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2Z"/><path d="M2 10h20"/></svg>
        <span>${escapeHtml(p.name)}</span>
      `;
      item.addEventListener('click', () => addCurrentChatToProject(p.id));
      target.appendChild(item);
    });
  }

  async function addCurrentChatToProject(projectId) {
    const sid = window.chatManager?.currentSessionId || window.chatManager?.sessionId;
    const currentSessionId = typeof sid === 'function' ? sid() : sid;
    if (!currentSessionId) {
      window.toast?.error?.('No active chat to add');
      return;
    }
    const resp = await fetch(`/chat/${encodeURIComponent(currentSessionId)}`, {
      method: 'PATCH',
      credentials: 'same-origin',
      headers: jsonHeaders(),
      body: JSON.stringify({ project_id: projectId }),
    });
    if (!resp.ok) {
      window.toast?.error?.('Could not add this chat to the project');
      return;
    }
    window.dispatchEvent(new CustomEvent('projects:session-moved', {
      detail: { sessionId: currentSessionId, projectId },
    }));
    const submenu = document.getElementById('submenu-projects');
    if (submenu) submenu.style.display = 'none';
  }

  /* ---------- Create modal ---------- */
  function openModal() {
    if (!els.modal || !els.modalInput || state.creating) return;
    state.lastFocused = document.activeElement;
    els.modal.hidden = false;
    els.modalInput.value = '';
    els.modalCreate.disabled = true;
    setModalError('');
    document.body.classList.add('project-modal-open');
    requestAnimationFrame(() => els.modalInput.focus());
  }
  function closeModal() {
    if (!els.modal || state.creating) return;
    els.modal.hidden = true;
    document.body.classList.remove('project-modal-open');
    const focusTarget = state.lastFocused?.isConnected ? state.lastFocused : els.createBtn;
    focusTarget?.focus?.();
    state.lastFocused = null;
  }

  function setModalError(message) {
    if (!els.modalError) return;
    els.modalError.textContent = message;
    els.modalError.hidden = !message;
    els.modalInput?.setAttribute('aria-invalid', message ? 'true' : 'false');
  }

  function setCreating(creating) {
    state.creating = creating;
    els.modalInput.disabled = creating;
    els.modalCreate.disabled = creating || !els.modalInput.value.trim();
    els.modalCreate.textContent = creating ? 'Creating…' : (els.modalCreate.dataset.defaultLabel || 'Create project');
    els.modalCreate.setAttribute('aria-busy', String(creating));
  }

  function trapModalFocus(e) {
    if (e.key !== 'Tab' || els.modal.hidden) return;
    const focusable = [...els.modalContent.querySelectorAll('input:not(:disabled), button:not(:disabled), [tabindex]:not([tabindex="-1"])')];
    if (!focusable.length) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (e.shiftKey && document.activeElement === first) {
      e.preventDefault();
      last.focus();
    } else if (!e.shiftKey && document.activeElement === last) {
      e.preventDefault();
      first.focus();
    }
  }

  function bindModal() {
    if (!els.modal) return;
    els.createBtn?.addEventListener('click', openModal);
    els.modal.querySelectorAll('[data-dismiss="modal"]').forEach((el) => {
      el.addEventListener('click', closeModal);
    });
    els.modalInput.addEventListener('input', () => {
      els.modalCreate.disabled = !els.modalInput.value.trim();
      setModalError('');
    });
    els.modalInput.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && !els.modalCreate.disabled) {
        e.preventDefault();
        commit();
      } else if (e.key === 'Escape') {
        closeModal();
      }
    });
    els.modalCreate.addEventListener('click', commit);
    els.modal.addEventListener('keydown', trapModalFocus);
  }

  async function commit() {
    if (state.creating) return;
    const name = els.modalInput.value.trim();
    if (!name) return;
    setModalError('');
    setCreating(true);
    try {
      const resp = await fetch('/projects', {
        method: 'POST',
        headers: jsonHeaders(),
        credentials: 'same-origin',
        body: JSON.stringify({ name }),
      });
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({}));
        throw new Error(err.error || `HTTP ${resp.status}`);
      }
      const { project } = await resp.json();
      if (!project?.id) throw new Error('Server returned an invalid project');
      state.creating = false;
      closeModal();
      window.location.href = `/p/${encodeURIComponent(project.id)}`;
    } catch (err) {
      setCreating(false);
      const message = err?.message || 'Please try again.';
      setModalError(message);
      window.toast?.error?.(`Couldn't create project: ${message}`);
      els.modalInput.focus();
    }
  }

  /* ---------- Drag-drop (move chat into project) ---------- */
  function attachDropTarget(rowEl, projectId) {
    rowEl.addEventListener('dragover', (e) => {
      if (!e.dataTransfer.types.includes('application/x-session-id')) return;
      e.preventDefault();
      rowEl.dataset.dragOver = 'true';
    });
    rowEl.addEventListener('dragleave', () => { delete rowEl.dataset.dragOver; });
    rowEl.addEventListener('drop', async (e) => {
      delete rowEl.dataset.dragOver;
      const sessionId = e.dataTransfer.getData('application/x-session-id');
      if (!sessionId) return;
      e.preventDefault();
      const resp = await fetch(`/chat/${encodeURIComponent(sessionId)}`, {
        method: 'PATCH',
        headers: jsonHeaders(),
        credentials: 'same-origin',
        body: JSON.stringify({ project_id: projectId }),
      });
      if (!resp.ok) {
        window.toast?.error?.('Could not move this chat to the project');
        return;
      }
      window.dispatchEvent(new CustomEvent('projects:session-moved', { detail: { sessionId, projectId } }));
    });
  }

  /* ---------- Helpers ---------- */
  function jsonHeaders() {
    const h = { 'Content-Type': 'application/json' };
    const csrf = document.querySelector('meta[name="csrf-token"]')?.content;
    if (csrf) h['X-CSRFToken'] = csrf;
    return h;
  }

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, (c) => ({
      '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'
    }[c]));
  }

  async function reload() {
    state.projects = await fetchProjects();
    state.loaded = true;
    renderList();
    renderSubmenu();
    window.dispatchEvent(new CustomEvent('projects:loaded'));
  }

  function bindToggle() {
    const toggle = document.getElementById('projects-section-toggle');
    if (!toggle || !els.section) return;
    // Restore prior collapsed state from localStorage.
    try {
      if (localStorage.getItem('projects:collapsed') === '1') {
        toggle.setAttribute('aria-expanded', 'false');
        els.section.classList.add('sidebar-projects--collapsed');
      }
    } catch (_) {}
    toggle.addEventListener('click', () => {
      const isExpanded = toggle.getAttribute('aria-expanded') === 'true';
      toggle.setAttribute('aria-expanded', String(!isExpanded));
      els.section.classList.toggle('sidebar-projects--collapsed', isExpanded);
      try { localStorage.setItem('projects:collapsed', isExpanded ? '1' : '0'); } catch (_) {}
    });
  }

  function bindCollapsedExpand() {
    const btn = document.getElementById('projects-collapsed-expand');
    if (!btn) return;
    btn.addEventListener('click', () => {
      // Expand the sidebar so the user can see their projects.
      if (typeof window.toggleLeftSidebar === 'function') {
        window.toggleLeftSidebar();
      } else {
        document.body.classList.remove('sidebar-collapsed');
      }
    });
  }

  async function init() {
    cache();
    if (!els.section) return;
    bindModal();
    bindToggle();
    bindCollapsedExpand();
    try { await reload(); } catch (e) { console.warn('projects: load failed', e); }
  }

  window.projects = { init, reload, getList: () => state.projects.slice() };
})();
