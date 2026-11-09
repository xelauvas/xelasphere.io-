/**
 * xela-claude.js: behavior for the claude.ai-style shell.
 *
 * - Model menu in the composer (replaces the Chat / Code / Think pills). The choice is
 *   stored through window.ghostModel, which every send path already reads.
 * - Top bar with the conversation title (Pin / Rename / Delete) and Share.
 * - Light / Dark / System switch in the account menu.
 * - Empty-state layout: greeting and composer centered, suggestion chips below.
 */
(function () {
  'use strict';

  const $ = (sel, root) => (root || document).querySelector(sel);
  const esc = (v) => String(v == null ? '' : v).replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[c]));
  const ICON_CHECK = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M20 6 9 17l-5-5"/></svg>';
  const ICON_CHEV = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m9 18 6-6-6-6"/></svg>';
  const LOCAL_MODEL = 'qwen3.6-27b';
  const THEME_KEY = 'xelasphere-theme';

  // ------------------------------------------------------------------ menus
  let open = null; // { menu, button }

  function closeMenu(restoreFocus) {
    if (!open) return;
    const { menu, button } = open;
    menu.hidden = true;
    button.setAttribute('aria-expanded', 'false');
    open = null;
    if (restoreFocus) button.focus({ preventScroll: true });
  }

  function placeMenu(menu, button) {
    menu.hidden = false;
    menu.style.left = '0px';
    menu.style.top = '0px';
    const r = button.getBoundingClientRect();
    const mw = menu.offsetWidth;
    const mh = menu.offsetHeight;
    const alignRight = menu.dataset.align === 'right';
    let left = alignRight ? r.right - mw : r.left;
    left = Math.max(8, Math.min(left, window.innerWidth - mw - 8));
    const preferAbove = r.top > window.innerHeight * 0.55;
    let top = preferAbove ? r.top - mh - 6 : r.bottom + 6;
    if (preferAbove && top < 8) top = r.bottom + 6;
    if (!preferAbove && top + mh > window.innerHeight - 8) top = Math.max(8, r.top - mh - 6);
    menu.style.left = Math.round(left) + 'px';
    menu.style.top = Math.round(top) + 'px';
    menu.style.transformOrigin = (preferAbove ? 'bottom ' : 'top ') + (alignRight ? 'right' : 'left');
  }

  function toggleMenu(menu, button, beforeOpen) {
    if (open && open.menu === menu) {
      closeMenu(false);
      return;
    }
    closeMenu(false);
    if (beforeOpen) beforeOpen(menu);
    placeMenu(menu, button);
    button.setAttribute('aria-expanded', 'true');
    open = { menu, button };
    const first = menu.querySelector('[aria-checked="true"]') || menu.querySelector('[role^="menuitem"]');
    if (first) first.focus({ preventScroll: true });
  }

  document.addEventListener('mousedown', (e) => {
    if (open && !open.menu.contains(e.target) && !open.button.contains(e.target)) closeMenu(false);
  }, true);

  document.addEventListener('keydown', (e) => {
    if (!open) return;
    if (e.key === 'Escape') {
      e.preventDefault();
      closeMenu(true);
      return;
    }
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      const items = [...open.menu.querySelectorAll('[role^="menuitem"], .xc-more-toggle')]
        .filter((el) => el.offsetParent !== null);
      if (!items.length) return;
      e.preventDefault();
      const i = items.indexOf(document.activeElement);
      const next = e.key === 'ArrowDown' ? (i + 1) % items.length : (i - 1 + items.length) % items.length;
      items[next].focus();
    }
  });

  window.addEventListener('resize', () => closeMenu(false));

  // ------------------------------------------------------------- model menu
  const PRIMARY = ['auto', LOCAL_MODEL, 'deepseek-v4-pro', 'deepseek-v4-flash'];
  const DESCRIPTIONS = {
    auto: 'Picks the best model for each message',
    [LOCAL_MODEL]: 'Your own model, running on your GPU',
    'deepseek-v4-pro': 'Best for coding and hard problems',
    'deepseek-v4-flash': 'Fast reasoning for everyday work',
  };
  const SPEED = { fast: 'Fast', medium: 'Balanced', slow: 'Thorough' };

  function catalog() {
    const map = new Map([['auto', { id: 'auto', label: 'Auto' }]]);
    (Array.isArray(window.XELA_MODELS) ? window.XELA_MODELS : []).forEach((m) => {
      if (m && m.id) map.set(m.id, m);
    });
    return map;
  }

  function shortLabel(m) {
    return String(m.label || m.id).replace(/\s*\([^)]*\)\s*$/, '');
  }

  function describe(m) {
    if (DESCRIPTIONS[m.id]) return DESCRIPTIONS[m.id];
    const bits = [];
    const paren = /\(([^)]*)\)\s*$/.exec(m.label || '');
    if (paren) bits.push(paren[1]);
    else if (m.reasoning) bits.push('Reasoning');
    if (m.speed && SPEED[m.speed]) bits.push(SPEED[m.speed]);
    return bits.join(' · ');
  }

  function selectedModel() {
    return (window.ghostModel && window.ghostModel.getSelectedModel()) || 'auto';
  }

  function updateModelButton() {
    const label = $('#model-menu-label');
    if (!label) return;
    const id = selectedModel();
    const m = catalog().get(id);
    label.textContent = m ? shortLabel(m) : id;
    const btn = $('#model-menu-button');
    if (btn) btn.classList.toggle('is-local', id === LOCAL_MODEL);
  }

  function setModel(id) {
    const g = window.ghostModel;
    if (!g) return;
    if (id === 'auto') g.clearLock();
    else g.lockModel(id);
    updateModelButton();
  }

  function modelItem(m, current) {
    const badge = m.id === LOCAL_MODEL ? '<span class="xc-model__badge">LOCAL</span>' : '';
    return `<button type="button" class="xc-menu__item xc-model" role="menuitemradio" aria-checked="${m.id === current}" data-model="${esc(m.id)}">
      <span class="xc-model__text"><span class="xc-model__name">${esc(shortLabel(m))}${badge}</span><span class="xc-model__desc">${esc(describe(m))}</span></span>
      ${ICON_CHECK.replace('<svg', '<svg class="xc-model__check"')}
    </button>`;
  }

  function buildModelMenu(menu) {
    const cat = catalog();
    const current = selectedModel();
    const primary = PRIMARY.map((id) => cat.get(id)).filter(Boolean);
    const rest = [...cat.values()].filter((m) => !PRIMARY.includes(m.id));
    const restOpen = rest.some((m) => m.id === current);
    let html = primary.map((m) => modelItem(m, current)).join('');
    if (rest.length) {
      html += `<div class="xc-menu__sep" role="separator"></div>
        <button type="button" class="xc-menu__item xc-more-toggle" aria-expanded="${restOpen}">
          <span>More models</span>${ICON_CHEV.replace('<svg', '<svg class="xc-chev"')}
        </button>
        <div class="xc-more"${restOpen ? '' : ' hidden'}>${rest.map((m) => modelItem(m, current)).join('')}</div>`;
    }
    menu.innerHTML = html;
  }

  function initModelMenu() {
    const btn = $('#model-menu-button');
    const menu = $('#xc-model-menu');
    if (!btn || !menu) return;
    menu.dataset.align = 'right';
    btn.addEventListener('click', (e) => {
      e.preventDefault();
      if (btn.disabled) return;
      toggleMenu(menu, btn, buildModelMenu);
    });
    menu.addEventListener('click', (e) => {
      const more = e.target.closest('.xc-more-toggle');
      if (more) {
        const list = menu.querySelector('.xc-more');
        const expanded = more.getAttribute('aria-expanded') === 'true';
        more.setAttribute('aria-expanded', String(!expanded));
        if (list) list.hidden = expanded;
        placeMenu(menu, btn);
        return;
      }
      const item = e.target.closest('[data-model]');
      if (!item) return;
      setModel(item.dataset.model);
      closeMenu(false);
      const prompt = $('#prompt');
      if (prompt) prompt.focus({ preventScroll: true });
    });
    updateModelButton();
  }

  // ----------------------------------------------------------------- title bar
  function chatManager() {
    return window.chatManager || null;
  }

  function activeSession() {
    const cm = chatManager();
    const id = cm && cm.activeSessionId;
    if (!id) return null;
    return { id, data: (cm.chatHistory && cm.chatHistory[id]) || null };
  }

  function activeName(sess) {
    const tab = document.querySelector('#tab-list li.chat-tab.active-tab .tab-name');
    const fromTab = tab && tab.textContent.trim();
    if (fromTab) return fromTab;
    return (sess && sess.data && sess.data.session_name) || '';
  }

  function hasConversation() {
    if (document.body.classList.contains('has-messages')) return true;
    const display = $('#chat-display');
    return !!(display && display.querySelector('.message'));
  }

  let titleFrame = 0;
  function scheduleTitle() {
    if (titleFrame) return;
    titleFrame = requestAnimationFrame(() => {
      titleFrame = 0;
      updateTitle();
    });
  }

  function updateTitle() {
    const btn = $('#xc-title');
    const share = $('#xc-share');
    if (!btn || !share) return;
    const body = document.body;
    const guest = body.dataset.guestMode === 'true';
    const sess = activeSession();
    const name = activeName(sess);
    const otherSurface = body.classList.contains('chats-page-open') || body.classList.contains('images-page-open');
    const temporary = sess && (String(sess.id).startsWith('temp_') || (sess.data && sess.data.is_temporary));
    const show = !guest && !otherSurface && !!sess && !temporary && !!name && hasConversation();
    btn.hidden = !show;
    share.hidden = !show;
    if (show) {
      const text = $('#xc-title-text');
      if (text && text.textContent !== name) text.textContent = name;
      btn.title = name;
    }
    const mobile = $('.xc-mobile-title');
    if (mobile) mobile.textContent = show ? name : '';
  }

  function initTitleBar() {
    const btn = $('#xc-title');
    const menu = $('#xc-title-menu');
    const share = $('#xc-share');
    if (!btn || !menu) return;

    btn.addEventListener('click', () => {
      toggleMenu(menu, btn, () => {
        const sess = activeSession();
        const pinned = !!(sess && sess.data && (sess.data.is_pinned || sess.data.pinned_at));
        const label = menu.querySelector('[data-act="pin"] [data-label]');
        if (label) label.textContent = pinned ? 'Unpin' : 'Pin';
      });
    });

    menu.addEventListener('click', (e) => {
      const item = e.target.closest('[data-act]');
      if (!item) return;
      const cm = chatManager();
      const sess = activeSession();
      closeMenu(false);
      if (!cm || !sess) return;
      if (item.dataset.act === 'pin') cm.pinSession(sess.id);
      if (item.dataset.act === 'rename') cm.renameSession(sess.id);
      if (item.dataset.act === 'delete') cm.deleteSession(sess.id);
    });

    if (share) {
      share.addEventListener('click', () => {
        const cm = chatManager();
        const sess = activeSession();
        if (cm && sess && typeof cm.shareSession === 'function') cm.shareSession(sess.id);
      });
    }

    // Mobile header: the title sits in the middle slot.
    const center = $('#mobile-header-center');
    if (center && !$('.xc-mobile-title', center)) {
      const span = document.createElement('span');
      span.className = 'xc-mobile-title';
      center.appendChild(span);
    }

    const tabs = $('#tab-list');
    if (tabs) {
      new MutationObserver(scheduleTitle).observe(tabs, {
        subtree: true, childList: true, characterData: true, attributes: true, attributeFilter: ['class'],
      });
    }
    new MutationObserver(scheduleTitle).observe(document.body, { attributes: true, attributeFilter: ['class'] });
    const display = $('#chat-display');
    if (display) new MutationObserver(scheduleTitle).observe(display, { childList: true });
    window.addEventListener('popstate', scheduleTitle);
    updateTitle();
  }

  // --------------------------------------------------------------- top bar edge
  function syncTopbarLeft() {
    const sidebar = $('#sidebar');
    let left = 0;
    if (sidebar && window.innerWidth > 768) {
      const r = sidebar.getBoundingClientRect();
      const visible = r.width > 0 && r.right > 0 && getComputedStyle(sidebar).visibility !== 'hidden';
      if (visible) left = Math.max(0, Math.round(r.right));
    }
    document.documentElement.style.setProperty('--xc-topbar-left', left + 'px');
    // An open artifact panel takes the right side; the top bar ends where it starts.
    const panel = $('#artifact-panel');
    let right = 0;
    if (panel && document.body.classList.contains('artifact-panel-open') && window.innerWidth > 768) {
      const pr = panel.getBoundingClientRect();
      if (pr.width > 0) right = Math.max(0, Math.round(window.innerWidth - pr.left));
    }
    document.documentElement.style.setProperty('--xc-topbar-right', right + 'px');
  }

  function initTopbarEdge() {
    const sidebar = $('#sidebar');
    syncTopbarLeft();
    window.addEventListener('resize', syncTopbarLeft);
    if (!sidebar) return;
    if (typeof ResizeObserver !== 'undefined') new ResizeObserver(syncTopbarLeft).observe(sidebar);
    sidebar.addEventListener('transitionend', syncTopbarLeft);
    new MutationObserver(syncTopbarLeft).observe(sidebar, { attributes: true, attributeFilter: ['class', 'style'] });
    new MutationObserver(syncTopbarLeft).observe(document.body, { attributes: true, attributeFilter: ['class'] });
    const panel = $('#artifact-panel');
    if (panel) {
      if (typeof ResizeObserver !== 'undefined') new ResizeObserver(syncTopbarLeft).observe(panel);
      panel.addEventListener('transitionend', syncTopbarLeft);
    }
  }

  // ------------------------------------------------------------------- theme
  function storedTheme() {
    try { return localStorage.getItem(THEME_KEY); } catch (_) { return null; }
  }

  function systemTheme() {
    return window.matchMedia && window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark';
  }

  function syncThemeSwitch() {
    const seg = $('#xc-theme-seg');
    if (!seg) return;
    const choice = storedTheme() || 'system';
    seg.querySelectorAll('[data-theme-choice]').forEach((b) => {
      b.setAttribute('aria-pressed', String(b.dataset.themeChoice === choice));
    });
  }

  function applyThemeChoice(choice) {
    const tm = window.ThemeManager;
    if (choice === 'system') {
      try { localStorage.removeItem(THEME_KEY); } catch (_) { /* private mode */ }
      const theme = systemTheme();
      if (tm && typeof tm.applyTheme === 'function') {
        tm.applyTheme(theme, true);
        tm.currentTheme = theme;
      } else {
        document.documentElement.setAttribute('data-theme', theme);
      }
    } else if (tm && typeof tm.setTheme === 'function') {
      tm.setTheme(choice, true);
    } else {
      document.documentElement.setAttribute('data-theme', choice);
      try { localStorage.setItem(THEME_KEY, choice); } catch (_) { /* private mode */ }
    }
    syncThemeSwitch();
  }

  function initThemeSwitch() {
    const seg = $('#xc-theme-seg');
    if (!seg) return;
    seg.addEventListener('click', (e) => {
      e.stopPropagation(); // keep the account menu open
      const b = e.target.closest('[data-theme-choice]');
      if (b) applyThemeChoice(b.dataset.themeChoice);
    });
    const row = seg.closest('.xc-theme-row');
    if (row) row.addEventListener('click', (e) => e.stopPropagation());
    if (window.matchMedia) {
      const mq = window.matchMedia('(prefers-color-scheme: light)');
      const onChange = () => { if (!storedTheme()) applyThemeChoice('system'); };
      if (mq.addEventListener) mq.addEventListener('change', onChange);
      else if (mq.addListener) mq.addListener(onChange);
    }
    syncThemeSwitch();
  }

  // --------------------------------------------------------------- empty state
  let heroFrame = 0;
  function scheduleHero() {
    if (heroFrame) return;
    heroFrame = requestAnimationFrame(() => {
      heroFrame = 0;
      layoutHero();
    });
  }

  function layoutHero() {
    const root = document.documentElement;
    const body = document.body;
    const greeting = $('#greeting-container');
    const input = $('.chat-shell__composer .input-container');
    if (!greeting || !input) return;
    if (body.classList.contains('has-messages') || window.innerWidth <= 768) return;
    const gh = greeting.getBoundingClientRect().height || 56;
    const ch = input.getBoundingClientRect().height || 110;
    root.style.setProperty('--xc-greet-h', Math.ceil(gh) + 'px');
    root.style.setProperty('--xc-composer-h', Math.ceil(ch) + 'px');
    // Center the greeting + composer block a little above the middle of the window.
    const block = gh + 24 + ch;
    const target = Math.max(72, Math.round(window.innerHeight * 0.44 - block / 2));
    const chatbox = $('#chatbox');
    const currentTop = greeting.getBoundingClientRect().top;
    const currentPad = parseFloat(getComputedStyle(chatbox || greeting).paddingTop) || 0;
    const nextPad = Math.max(0, Math.round(currentPad + (target - currentTop)));
    root.style.setProperty('--xc-hero-top', nextPad + 'px');
    const bottom = greeting.getBoundingClientRect().bottom;
    root.style.setProperty('--xc-composer-top', Math.round(bottom + 22) + 'px');
  }

  function initHero() {
    layoutHero();
    window.addEventListener('resize', scheduleHero);
    if (document.fonts && document.fonts.ready) document.fonts.ready.then(scheduleHero);
    const greeting = $('#greeting-container');
    const input = $('.chat-shell__composer .input-container');
    if (typeof ResizeObserver !== 'undefined') {
      const ro = new ResizeObserver(scheduleHero);
      if (greeting) ro.observe(greeting);
      if (input) ro.observe(input);
    }
    new MutationObserver(scheduleHero).observe(document.body, { attributes: true, attributeFilter: ['class'] });
    const text = $('#greeting-text');
    if (text) new MutationObserver(scheduleHero).observe(text, { childList: true, characterData: true, subtree: true });
  }

  // ------------------------------------------------------------ phone placeholder
  // "How can I help you today?" gets cut on phones; use the short form there.
  function initPlaceholder() {
    const prompt = $('#prompt');
    if (!prompt || document.body.dataset.guestMode === 'true') return;
    const LONG = 'How can I help you today?';
    const SHORT = 'Chat with Xela';
    const apply = () => {
      const current = prompt.getAttribute('placeholder') || '';
      const narrow = window.innerWidth <= 480;
      if (narrow && current === LONG) prompt.setAttribute('placeholder', SHORT);
      else if (!narrow && current === SHORT) prompt.setAttribute('placeholder', LONG);
    };
    apply();
    window.addEventListener('resize', apply);
  }

  // ---------------------------------------------------------------------- boot
  function boot() {
    initPlaceholder();
    initModelMenu();
    initTitleBar();
    initTopbarEdge();
    initThemeSwitch();
    initHero();
    // ghostModel restores a saved model after DOMContentLoaded; refresh the label then.
    setTimeout(updateModelButton, 0);
    setTimeout(updateModelButton, 400);
    window.XelaShell = { updateModelButton, updateTitle, layoutHero, setModel };
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
  else boot();
})();
