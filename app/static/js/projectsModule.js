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
    state.projects.forEach((project) => {
      const li = document.createElement('li');
      li.className = 'sidebar-project-row';
      li.dataset.projectId = project.id;
      li.style.setProperty('--project-accent', project.color);
      li.innerHTML = `${FOLDER_SVG}<span class="name">${escapeHtml(project.name)}</span>`;
      li.addEventListener('click', () => {
        window.location.href = `/p/${encodeURIComponent(project.id)}`;
      });
      attachDropTarget(li, project.id);
      els.list.appendChild(li);
    });
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
