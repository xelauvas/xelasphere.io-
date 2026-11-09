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
      els.chats.innerHTML = '<li class="project-chats-empty">No chats yet.</li>';
      return;
    }
    chats.forEach((c) => {
      const li = document.createElement('li');
      li.className = 'project-chat-row';
      // Preview text isn't returned by /chat/history yet — leave empty,
      // CSS hides the row gracefully. We can fill this in once the
      // backend includes a last-message snippet.
      const preview = c.preview ? escapeHtml(c.preview) : '';
      li.innerHTML = `
        <div class="pcr-text">
          <span class="pcr-title">${escapeHtml(c.session_name || 'Chat')}</span>
          ${preview ? `<span class="pcr-preview">${preview}</span>` : ''}
        </div>
        <span class="pcr-date">${shortDate(c.last_updated)}</span>
      `;
      li.addEventListener('click', () => {
        window.location.href = `/c/${encodeURIComponent(c.session_id)}`;
      });
      els.chats.appendChild(li);
    });
  }

  /* ---------- Tabs ---------- */
  document.querySelectorAll('.project-page-tab').forEach((tab) => {
    tab.addEventListener('click', () => {
      const name = tab.dataset.tab;
      document.querySelectorAll('.project-page-tab').forEach((t) => {
        const active = t === tab;
        t.classList.toggle('is-active', active);
        t.setAttribute('aria-selected', String(active));
      });
      document.querySelectorAll('.project-tab-panel').forEach((p) => {
        p.classList.toggle('pp-hidden', p.dataset.panel !== name);
      });
    });
  });

  /* ---------- Header icon (renders from data-project-icon) ---------- */
  function renderHeaderIcon() {
    const slot = document.querySelector('.project-page-icon');
    if (!slot) return;
    const key = root.dataset.projectIcon || 'code';
    const svg = (window.PROJECT_ICONS && window.PROJECT_ICONS.svg(key)) || '';
    slot.innerHTML = svg;
  }
  renderHeaderIcon();

  /* ---------- Icon + color picker popover ----------
     8 color swatches + 5×6 icon grid. Anchored under the header
     icon button. Clicking a swatch/icon PATCHes the project and
     updates header + sidebar live. */
  const ICP_PALETTE = [
    '#ffffff', '#e74c3c', '#e67e22', '#f1c40f',
    '#2ecc71', '#3b82f6', '#9b59b6', '#ec4899',
  ];

  function currentColor() {
    // The accent var is set inline on .project-page; cheaper than re-parsing.
    return root.style.getPropertyValue('--project-accent').trim() || ICP_PALETTE[5];
  }

  function openPicker(anchor) {
    closePicker();
    const popover = document.createElement('div');
    popover.className = 'icon-color-picker';
    popover.setAttribute('role', 'dialog');
    popover.setAttribute('aria-label', 'Choose icon and color');

    const sel = currentColor().toLowerCase();
    const selIcon = root.dataset.projectIcon || 'code';

    const swatchesHtml = ICP_PALETTE.map((c) => {
      const active = c.toLowerCase() === sel ? ' is-selected' : '';
      return `<button type="button" class="icp-swatch${active}" data-color="${c}" style="background:${c}" aria-label="${c}"></button>`;
    }).join('');

    const iconKeys = (window.PROJECT_ICONS && window.PROJECT_ICONS.ORDER) || [];
    const iconsHtml = iconKeys.map((k) => {
      const active = k === selIcon ? ' is-selected' : '';
      return `<button type="button" class="icp-icon-cell${active}" data-icon="${k}" aria-label="${k}">${window.PROJECT_ICONS.svg(k)}</button>`;
    }).join('');

    popover.innerHTML = `
      <div class="icp-colors" role="group" aria-label="Color">${swatchesHtml}</div>
      <div class="icp-divider"></div>
      <div class="icp-icons" role="group" aria-label="Icon">${iconsHtml}</div>
      <div class="icp-footer"><button type="button" class="icp-close">Close menu</button></div>
    `;
    document.body.appendChild(popover);

    // Anchor below the icon button.
    const rect = anchor.getBoundingClientRect();
    popover.style.top = `${rect.bottom + 6 + window.scrollY}px`;
    popover.style.left = `${rect.left + window.scrollX}px`;

    popover.addEventListener('click', (ev) => {
      const swatch = ev.target.closest('[data-color]');
      const icon = ev.target.closest('[data-icon]');
      const close = ev.target.closest('.icp-close');
      if (swatch) {
        const color = swatch.dataset.color;
        applyColor(color);
        popover.querySelectorAll('.icp-swatch').forEach((b) =>
          b.classList.toggle('is-selected', b === swatch));
        savePatch({ color });
      } else if (icon) {
        const key = icon.dataset.icon;
        applyIcon(key);
        popover.querySelectorAll('.icp-icon-cell').forEach((b) =>
          b.classList.toggle('is-selected', b === icon));
        savePatch({ icon: key });
      } else if (close) {
        closePicker();
      }
    });

    // Click-outside-to-close. Defer so the opening click doesn't immediately close.
    setTimeout(() => {
      const handler = (ev) => {
        if (!popover.contains(ev.target) && !anchor.contains(ev.target)) {
          closePicker();
          document.removeEventListener('click', handler);
        }
      };
      document.addEventListener('click', handler);
      popover._closeHandler = handler;
    }, 0);
  }

  function closePicker() {
    document.querySelectorAll('.icon-color-picker').forEach((p) => {
      if (p._closeHandler) document.removeEventListener('click', p._closeHandler);
      p.remove();
    });
  }

  function applyColor(color) {
    root.style.setProperty('--project-accent', color);
    // Sidebar row uses the same --project-accent var via inline style; we
    // also need to update that row so the folder/icon picks up the new color.
    const row = document.querySelector(`.sidebar-project-row[data-project-id="${root.dataset.projectId}"]`);
    if (row) row.style.setProperty('--project-accent', color);
  }

  function applyIcon(key) {
    root.dataset.projectIcon = key;
    renderHeaderIcon();
    // Tell projectsModule to re-render so the sidebar row icon updates.
    window.dispatchEvent(new CustomEvent('projects:icon-changed', {
      detail: { projectId: root.dataset.projectId, icon: key },
    }));
  }

  async function savePatch(body) {
    try {
      const resp = await fetch(`/projects/${encodeURIComponent(projectId)}`, {
        method: 'PATCH',
        headers: jsonHeaders(),
        credentials: 'same-origin',
        body: JSON.stringify(body),
      });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    } catch (err) {
      window.toast?.error?.(`Couldn't save: ${err.message}`);
    }
  }

  const iconBtn = document.getElementById('project-page-icon-btn');
  iconBtn?.addEventListener('click', (e) => {
    e.stopPropagation();
    if (document.querySelector('.icon-color-picker')) closePicker();
    else openPicker(iconBtn);
  });

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

  /* ChatGPT-style short date: "May 23" for current year, "May 23, 2024"
     for older. Falls back to relTime for things in the last 24h to keep
     them feeling fresh. */
  function shortDate(iso) {
    if (!iso) return '';
    const d = new Date(iso);
    const diff = (Date.now() - d.getTime()) / 1000;
    if (diff < 86400) return relTime(iso);
    const now = new Date();
    const fmt = { month: 'short', day: 'numeric' };
    if (d.getFullYear() !== now.getFullYear()) fmt.year = 'numeric';
    return d.toLocaleDateString(undefined, fmt);
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
