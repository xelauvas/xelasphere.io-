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

  const state = { projects: [], loaded: false };
  const els = {};

  function cache() {
    els.section = document.getElementById('projects-section');
    els.list = document.getElementById('project-list');
    els.createBtn = document.getElementById('project-create-btn');
    els.moreHint = document.getElementById('project-more-hint');
    els.modal = document.getElementById('project-create-modal');
    els.modalInput = document.getElementById('project-name-input');
    els.modalCreate = document.getElementById('project-modal-create');
  }

  async function fetchProjects() {
    const resp = await fetch('/projects', { credentials: 'same-origin' });
    if (!resp.ok) throw new Error(`GET /projects ${resp.status}`);
    return (await resp.json()).projects || [];
  }

  function renderList() {
    if (!els.list) return;
    els.list.innerHTML = '';
    if (state.projects.length === 0) {
      const empty = document.createElement('li');
      empty.className = 'sidebar-projects-empty';
      empty.innerHTML = `
        <button type="button" class="sidebar-projects-empty-cta" id="project-create-cta">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>
          <span>New project</span>
        </button>
      `;
      empty.querySelector('button').addEventListener('click', openModal);
      els.list.appendChild(empty);
      return;
    }
    state.projects.forEach((project) => {
      const li = document.createElement('li');
      li.className = 'sidebar-project-row';
      li.dataset.projectId = project.id;
      li.style.setProperty('--project-accent', project.color);
      li.innerHTML = `
        ${FOLDER_SVG}
        <span class="name">${escapeHtml(project.name)}</span>
        <button type="button" class="sidebar-project-menu-btn" aria-label="Project options" data-project-menu>
          <i class="fas fa-ellipsis-h" aria-hidden="true"></i>
        </button>
      `;
      li.addEventListener('click', (e) => {
        if (e.target.closest('[data-project-menu]')) return;
        window.location.href = `/p/${encodeURIComponent(project.id)}`;
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
    m.style.top = `${rect.bottom + 4}px`;
    m.style.left = `${Math.max(8, rect.left - m.offsetWidth + rect.width)}px`;
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
    await fetch(`/chat/${encodeURIComponent(currentSessionId)}`, {
      method: 'PATCH',
      credentials: 'same-origin',
      headers: jsonHeaders(),
      body: JSON.stringify({ project_id: projectId }),
    });
    window.dispatchEvent(new CustomEvent('projects:session-moved', {
      detail: { sessionId: currentSessionId, projectId },
    }));
    const submenu = document.getElementById('submenu-projects');
    if (submenu) submenu.style.display = 'none';
  }

  /* ---------- Create modal ---------- */
  function openModal() {
    els.modal.hidden = false;
    els.modalInput.value = '';
    els.modalCreate.disabled = true;
    els.modalInput.focus();
  }
  function closeModal() {
    els.modal.hidden = true;
  }

  function bindModal() {
    if (!els.modal) return;
    els.createBtn?.addEventListener('click', openModal);
    els.modal.querySelectorAll('[data-dismiss="modal"]').forEach((el) => {
      el.addEventListener('click', closeModal);
    });
    els.modalInput.addEventListener('input', () => {
      els.modalCreate.disabled = !els.modalInput.value.trim();
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
  }

  async function commit() {
    const name = els.modalInput.value.trim();
    if (!name) return;
    els.modalCreate.disabled = true;
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
      closeModal();
      window.location.href = `/p/${encodeURIComponent(project.id)}`;
    } catch (err) {
      window.toast?.error?.(`Couldn't create project: ${err.message}`);
      els.modalCreate.disabled = false;
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
      await fetch(`/chat/${encodeURIComponent(sessionId)}`, {
        method: 'PATCH',
        headers: jsonHeaders(),
        credentials: 'same-origin',
        body: JSON.stringify({ project_id: projectId }),
      });
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

  async function init() {
    cache();
    if (!els.section) return;
    bindModal();
    try { await reload(); } catch (e) { console.warn('projects: load failed', e); }
  }

  window.projects = { init, reload, getList: () => state.projects.slice() };
})();
