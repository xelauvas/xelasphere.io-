/**
 * projectsPage.js — Project landing page.
 * Spec: docs/superpowers/specs/2026-05-22-projects-design.md
 */
(function () {
  'use strict';

  const root = document.querySelector('.project-page');
  if (!root) return;

  const projectId = root.dataset.projectId;
  const els = {
    title: document.getElementById('project-title'),
    rename: document.getElementById('project-rename'),
    instructions: document.getElementById('project-instructions'),
    status: document.getElementById('project-instructions-status'),
    newChat: document.getElementById('project-new-chat'),
    chats: document.getElementById('project-chats'),
  };

  /* ---------- Instructions autosave ---------- */
  let saveTimer = null;
  els.instructions?.addEventListener('input', () => {
    setStatus('Saving…');
    clearTimeout(saveTimer);
    saveTimer = setTimeout(saveInstructions, 1500);
  });
  els.instructions?.addEventListener('blur', () => {
    clearTimeout(saveTimer);
    saveInstructions();
  });

  async function saveInstructions() {
    try {
      const resp = await fetch(`/projects/${encodeURIComponent(projectId)}`, {
        method: 'PATCH',
        headers: jsonHeaders(),
        credentials: 'same-origin',
        body: JSON.stringify({ instructions: els.instructions.value }),
      });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      setStatus('Saved');
      setTimeout(() => setStatus(''), 1200);
    } catch (e) {
      setStatus('Save failed');
    }
  }

  function setStatus(text) {
    if (els.status) els.status.textContent = text;
  }

  /* ---------- Rename ---------- */
  els.rename?.addEventListener('click', () => {
    const next = window.prompt('Rename project:', els.title.textContent);
    if (!next || next.trim() === els.title.textContent) return;
    fetch(`/projects/${encodeURIComponent(projectId)}`, {
      method: 'PATCH', headers: jsonHeaders(), credentials: 'same-origin',
      body: JSON.stringify({ name: next.trim() }),
    }).then((r) => {
      if (r.ok) {
        els.title.textContent = next.trim();
        document.title = `${next.trim()} · XelaSphere`;
      }
    });
  });

  /* ---------- New chat in project ---------- */
  els.newChat?.addEventListener('click', () => {
    // Redirect to the main chat page with a flag — the chat module honors it.
    window.location.href = `/?new=1&project_id=${encodeURIComponent(projectId)}`;
  });

  /* ---------- Chats list ---------- */
  loadChats();

  async function loadChats() {
    try {
      const resp = await fetch(
        `/chat/history?project_id=${encodeURIComponent(projectId)}&page_size=200`,
        { credentials: 'same-origin' }
      );
      if (!resp.ok) return;
      const body = await resp.json();
      const all = Object.values(body.categorized_history || {}).flat();
      renderChats(all);
    } catch (_) { /* non-fatal */ }
  }

  function renderChats(chats) {
    if (!els.chats) return;
    els.chats.innerHTML = '';
    if (!chats.length) {
      els.chats.innerHTML = '<li class="project-chats-empty">No chats yet — start one above.</li>';
      return;
    }
    chats.forEach((c) => {
      const li = document.createElement('li');
      li.className = 'project-chat-row';
      li.innerHTML = `<span class="title">${escapeHtml(c.session_name || 'Chat')}</span>
                      <span class="updated">${relTime(c.last_updated)}</span>`;
      li.addEventListener('click', () => {
        window.location.href = `/c/${encodeURIComponent(c.session_id)}`;
      });
      els.chats.appendChild(li);
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

  function relTime(iso) {
    if (!iso) return '';
    const d = new Date(iso);
    const diff = (Date.now() - d.getTime()) / 1000;
    if (diff < 60) return 'just now';
    if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
    if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
    return d.toLocaleDateString();
  }

  /* ---------- Files (upload, list, remove) ---------- */
  const fileEls = {
    dropzone: document.getElementById('project-files-dropzone'),
    input: document.getElementById('project-file-input'),
    addBtn: document.getElementById('project-add-files-btn'),
    list: document.getElementById('project-files'),
  };

  if (fileEls.dropzone) {
    fileEls.addBtn.addEventListener('click', () => fileEls.input.click());
    fileEls.input.addEventListener('change', (e) => uploadFiles(e.target.files));
    fileEls.dropzone.addEventListener('dragover', (e) => {
      e.preventDefault();
      fileEls.dropzone.classList.add('drag-over');
    });
    fileEls.dropzone.addEventListener('dragleave', () => {
      fileEls.dropzone.classList.remove('drag-over');
    });
    fileEls.dropzone.addEventListener('drop', (e) => {
      e.preventDefault();
      fileEls.dropzone.classList.remove('drag-over');
      uploadFiles(e.dataTransfer.files);
    });
    loadFiles();
  }

  async function loadFiles() {
    try {
      const resp = await fetch(`/projects/${encodeURIComponent(projectId)}`, { credentials: 'same-origin' });
      if (!resp.ok) return;
      const { project } = await resp.json();
      renderFiles(project.files || []);
    } catch (_) { /* non-fatal */ }
  }

  function renderFiles(files) {
    if (!fileEls.list) return;
    fileEls.list.innerHTML = '';
    if (!files.length) {
      fileEls.list.innerHTML = '<li class="project-files-empty">No files yet.</li>';
      return;
    }
    files.forEach((f) => {
      const li = document.createElement('li');
      li.className = 'project-file-row';
      li.innerHTML = `
        <span class="filename">${escapeHtml(f.filename)}</span>
        <span class="size">${formatSize(f.size_bytes)}</span>
        <button type="button" class="file-remove" data-file-id="${escapeHtml(f.file_id)}" aria-label="Remove file">×</button>
      `;
      li.querySelector('.file-remove').addEventListener('click', () => removeFile(f.file_id));
      fileEls.list.appendChild(li);
    });
  }

  async function uploadFiles(fileList) {
    for (const file of fileList) {
      const fd = new FormData();
      fd.append('file', file);
      const csrf = document.querySelector('meta[name="csrf-token"]')?.content || '';
      const resp = await fetch(`/projects/${encodeURIComponent(projectId)}/files`, {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'X-CSRFToken': csrf },
        body: fd,
      });
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({}));
        window.toast?.error?.(`Couldn't upload ${file.name}: ${err.error || resp.status}`);
      }
    }
    await loadFiles();
  }

  async function removeFile(fileId) {
    if (!confirm('Remove this file from the project?')) return;
    const csrf = document.querySelector('meta[name="csrf-token"]')?.content || '';
    const resp = await fetch(
      `/projects/${encodeURIComponent(projectId)}/files/${encodeURIComponent(fileId)}`,
      { method: 'DELETE', credentials: 'same-origin', headers: { 'X-CSRFToken': csrf } },
    );
    if (resp.ok) await loadFiles();
  }

  function formatSize(n) {
    if (typeof n !== 'number' || n < 0) return '';
    if (n < 1024) return `${n} B`;
    if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
    return `${(n / 1024 / 1024).toFixed(1)} MB`;
  }

  /* ---------- Project page ⋯ menu (rename / color / delete) ---------- */
  const PALETTE = ['#d97706', '#dc2626', '#db2777', '#7c3aed', '#2563eb',
                   '#0891b2', '#16a34a', '#ca8a04', '#475569', '#737373'];

  const menuBtn = document.getElementById('project-menu');
  menuBtn?.addEventListener('click', (e) => { e.stopPropagation(); openProjectMenu(e); });

  function closeAnyOpenMenus() {
    document.querySelectorAll('.project-page-menu-popover, .project-page-color-picker').forEach((n) => n.remove());
  }

  function positionPopover(node, anchor) {
    const rect = anchor.getBoundingClientRect();
    node.style.position = 'absolute';
    node.style.top = `${rect.bottom + window.scrollY + 4}px`;
    // Place right-edge-aligned with the anchor.
    node.style.left = `${rect.right + window.scrollX - node.offsetWidth}px`;
    node.style.zIndex = '1500';
  }

  function openProjectMenu(e) {
    closeAnyOpenMenus();
    const m = document.createElement('div');
    m.className = 'project-page-menu-popover';
    m.innerHTML = `
      <button type="button" data-action="rename">Rename</button>
      <button type="button" data-action="color">Change color</button>
      <button type="button" data-action="delete" class="danger">Delete project</button>
    `;
    document.body.appendChild(m);
    positionPopover(m, e.currentTarget);
    m.addEventListener('click', (ev) => {
      const action = ev.target.dataset?.action;
      if (action === 'rename') { triggerRename(); m.remove(); }
      else if (action === 'color') { openColorPicker(m, ev.target); }
      else if (action === 'delete') { triggerDelete(); m.remove(); }
    });
    // Close on next outside click.
    setTimeout(() => {
      document.addEventListener('click', closeAnyOpenMenus, { once: true });
    }, 0);
  }

  function openColorPicker(parentMenu, anchor) {
    const picker = document.createElement('div');
    picker.className = 'project-page-color-picker';
    PALETTE.forEach((c) => {
      const swatch = document.createElement('button');
      swatch.type = 'button';
      swatch.style.background = c;
      swatch.dataset.color = c;
      swatch.addEventListener('click', async (e) => {
        e.stopPropagation();
        try {
          const resp = await fetch(`/projects/${encodeURIComponent(projectId)}`, {
            method: 'PATCH', credentials: 'same-origin', headers: jsonHeaders(),
            body: JSON.stringify({ color: c }),
          });
          if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
          root.style.setProperty('--project-accent', c);
        } catch (err) {
          window.toast?.error?.(`Couldn't change color: ${err.message}`);
        }
        picker.remove();
        parentMenu.remove();
      });
      picker.appendChild(swatch);
    });
    document.body.appendChild(picker);
    positionPopover(picker, anchor);
  }

  function triggerRename() {
    const next = window.prompt('Rename project:', els.title.textContent);
    if (!next || next.trim() === els.title.textContent) return;
    fetch(`/projects/${encodeURIComponent(projectId)}`, {
      method: 'PATCH', headers: jsonHeaders(), credentials: 'same-origin',
      body: JSON.stringify({ name: next.trim() }),
    }).then((r) => {
      if (r.ok) {
        els.title.textContent = next.trim();
        document.title = `${next.trim()} · XelaSphere`;
      } else {
        window.toast?.error?.(`Rename failed (${r.status})`);
      }
    });
  }

  async function triggerDelete() {
    // Count chats in the project so we can phrase the dialog right.
    let count = 0;
    try {
      const r = await fetch(
        `/chat/history?project_id=${encodeURIComponent(projectId)}&page_size=500`,
        { credentials: 'same-origin' },
      );
      if (r.ok) {
        const body = await r.json();
        count = Object.values(body.categorized_history || {})
          .reduce((sum, list) => sum + list.length, 0);
      }
    } catch (_) { /* count remains 0 */ }

    let cascade;
    if (count === 0) {
      if (!confirm('Delete this project?')) return;
      cascade = 'keep';
    } else {
      const choice = prompt(
        `This project contains ${count} chat${count === 1 ? '' : 's'}.\n\n` +
        `Type "keep" to keep the chats (they return to your main history), ` +
        `or "delete" to also delete them.`,
        'keep',
      );
      if (choice === null) return;
      if (choice !== 'keep' && choice !== 'delete') {
        alert('Cancelled — type exactly "keep" or "delete".');
        return;
      }
      if (choice === 'delete' && !confirm(
        `Permanently delete ${count} chat${count === 1 ? '' : 's'}?`,
      )) return;
      cascade = choice;
    }

    const resp = await fetch(
      `/projects/${encodeURIComponent(projectId)}?cascade=${cascade}`,
      { method: 'DELETE', credentials: 'same-origin', headers: jsonHeaders() },
    );
    if (resp.ok) {
      window.location.href = '/';
    } else {
      window.toast?.error?.(`Couldn't delete project (${resp.status})`);
    }
  }
})();
