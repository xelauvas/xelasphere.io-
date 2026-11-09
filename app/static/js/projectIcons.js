/**
 * projectIcons.js — Shared icon library for Projects (sidebar row,
 * project page header, and the icon/color picker grid).
 *
 * Icon keys MUST stay in lockstep with ALLOWED_ICONS in
 * app/blueprints/project_routes.py — backend validates against that set.
 *
 * Paths are Lucide (MIT) outline glyphs. Stroke + 24x24 viewBox.
 */
(function () {
  'use strict';

  const SVG_ATTRS =
    'viewBox="0 0 24 24" fill="none" stroke="currentColor" ' +
    'stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"';

  // Inner SVG content for each icon. `key` is the canonical name stored
  // on the project document.
  const PATHS = {
    folder: '<path d="M20 20a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.93a2 2 0 0 1-1.66-.9l-.82-1.2A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2Z"/>',
    dollar:
      '<circle cx="12" cy="12" r="10"/>' +
      '<path d="M16 8h-6a2 2 0 1 0 0 4h4a2 2 0 1 1 0 4H8"/>' +
      '<path d="M12 18V6"/>',
    book:
      '<path d="M2 3h6a4 4 0 0 1 4 4v14a3 3 0 0 0-3-3H2z"/>' +
      '<path d="M22 3h-6a4 4 0 0 0-4 4v14a3 3 0 0 1 3-3h7z"/>',
    graduation:
      '<path d="M21.42 10.922a1 1 0 0 0-.019-1.838L12.83 5.18a2 2 0 0 0-1.66 0L2.6 9.08a1 1 0 0 0 0 1.832l8.57 3.908a2 2 0 0 0 1.66 0z"/>' +
      '<path d="M22 10v6"/>' +
      '<path d="M6 12.5V16a6 3 0 0 0 12 0v-3.5"/>',
    pencil:
      '<path d="M17 3a2.85 2.85 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5z"/>',
    pin:
      '<line x1="12" x2="12" y1="17" y2="22"/>' +
      '<path d="M5 17h14v-1.76a2 2 0 0 0-1.11-1.79l-1.78-.9A2 2 0 0 1 15 10.76V6h1a2 2 0 0 0 0-4H8a2 2 0 0 0 0 4h1v4.76a2 2 0 0 1-1.11 1.79l-1.78.9A2 2 0 0 0 5 15.24Z"/>',
    code:
      '<path d="M8 3H7a2 2 0 0 0-2 2v5a2 2 0 0 1-2 2 2 2 0 0 1 2 2v5c0 1.1.9 2 2 2h1"/>' +
      '<path d="M16 21h1a2 2 0 0 0 2-2v-5c0-1.1.9-2 2-2a2 2 0 0 1-2-2V5a2 2 0 0 0-2-2h-1"/>',
    film:
      '<rect width="20" height="20" x="2" y="2" rx="2.18"/>' +
      '<line x1="7" x2="7" y1="2" y2="22"/>' +
      '<line x1="17" x2="17" y1="2" y2="22"/>' +
      '<line x1="2" x2="22" y1="12" y2="12"/>' +
      '<line x1="2" x2="7" y1="7" y2="7"/>' +
      '<line x1="2" x2="7" y1="17" y2="17"/>' +
      '<line x1="17" x2="22" y1="17" y2="17"/>' +
      '<line x1="17" x2="22" y1="7" y2="7"/>',
    music:
      '<path d="M9 18V5l12-2v13"/>' +
      '<circle cx="6" cy="18" r="3"/>' +
      '<circle cx="18" cy="16" r="3"/>',
    trash:
      '<path d="M3 6h18"/>' +
      '<path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6"/>' +
      '<path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>',
    scissors:
      '<circle cx="6" cy="6" r="3"/>' +
      '<circle cx="6" cy="18" r="3"/>' +
      '<line x1="20" x2="8.12" y1="4" y2="15.88"/>' +
      '<line x1="14.47" x2="20" y1="14.48" y2="20"/>' +
      '<line x1="8.12" x2="12" y1="8.12" y2="12"/>',
    language:
      '<path d="m5 8 6 6"/>' +
      '<path d="m4 14 6-6 2-3"/>' +
      '<path d="M2 5h12"/>' +
      '<path d="M7 2h1"/>' +
      '<path d="m22 22-5-10-5 10"/>' +
      '<path d="M14 18h6"/>',
    stethoscope:
      '<path d="M11 2v2"/>' +
      '<path d="M5 2v2"/>' +
      '<path d="M5 3H4a2 2 0 0 0-2 2v4a6 6 0 0 0 12 0V5a2 2 0 0 0-2-2h-1"/>' +
      '<path d="M8 15a6 6 0 0 0 12 0v-3"/>' +
      '<circle cx="20" cy="10" r="2"/>',
    wand:
      '<path d="M15 4V2"/>' +
      '<path d="M15 16v-2"/>' +
      '<path d="M8 9h2"/>' +
      '<path d="M20 9h2"/>' +
      '<path d="M17.8 11.8 19 13"/>' +
      '<path d="M15 9h.01"/>' +
      '<path d="M17.8 6.2 19 5"/>' +
      '<path d="m3 21 9-9"/>' +
      '<path d="M12.2 6.2 11 5"/>',
    hand:
      '<path d="M18 11V6a2 2 0 1 0-4 0v5"/>' +
      '<path d="M14 10V4a2 2 0 1 0-4 0v6"/>' +
      '<path d="M10 10.5V6a2 2 0 1 0-4 0v8"/>' +
      '<path d="M18 8a2 2 0 1 1 4 0v6a8 8 0 0 1-8 8h-2c-2.8 0-4.5-.86-5.99-2.34l-3.6-3.6a2 2 0 0 1 2.83-2.82L7 15"/>',
    monitor:
      '<rect width="20" height="14" x="2" y="3" rx="2"/>' +
      '<line x1="8" x2="16" y1="21" y2="21"/>' +
      '<line x1="12" x2="12" y1="17" y2="21"/>',
    chart:
      '<path d="M3 3v18h18"/>' +
      '<path d="M18 17V9"/>' +
      '<path d="M13 17V5"/>' +
      '<path d="M8 17v-3"/>',
    user:
      '<path d="M19 21v-2a4 4 0 0 0-4-4H9a4 4 0 0 0-4 4v2"/>' +
      '<circle cx="12" cy="7" r="4"/>',
    sliders:
      '<line x1="21" x2="14" y1="4" y2="4"/>' +
      '<line x1="10" x2="3" y1="4" y2="4"/>' +
      '<line x1="21" x2="12" y1="12" y2="12"/>' +
      '<line x1="8" x2="3" y1="12" y2="12"/>' +
      '<line x1="21" x2="16" y1="20" y2="20"/>' +
      '<line x1="12" x2="3" y1="20" y2="20"/>' +
      '<line x1="14" x2="14" y1="2" y2="6"/>' +
      '<line x1="8" x2="8" y1="10" y2="14"/>' +
      '<line x1="16" x2="16" y1="18" y2="22"/>',
    document:
      '<path d="M14.5 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7.5z"/>' +
      '<polyline points="14 2 14 8 20 8"/>' +
      '<line x1="16" x2="8" y1="13" y2="13"/>' +
      '<line x1="16" x2="8" y1="17" y2="17"/>',
    scale:
      '<path d="m16 16 3-8 3 8c-.87.65-1.92 1-3 1s-2.13-.35-3-1Z"/>' +
      '<path d="m2 16 3-8 3 8c-.87.65-1.92 1-3 1s-2.13-.35-3-1Z"/>' +
      '<path d="M7 21h10"/>' +
      '<path d="M12 3v18"/>' +
      '<path d="M3 7h2c2 0 5-1 7-2 2 1 5 2 7 2h2"/>',
    mic:
      '<rect width="6" height="11" x="9" y="2" rx="3"/>' +
      '<path d="M19 10v2a7 7 0 0 1-14 0v-2"/>' +
      '<line x1="12" x2="12" y1="19" y2="22"/>',
    plane:
      '<path d="M17.8 19.2 16 11l3.5-3.5C21 6 21.5 4 21 3c-1-.5-3 0-4.5 1.5L13 8 4 6 2 8l6 5-4 4 4 1 1 4 4-4 5 6 2-2"/>',
    globe:
      '<circle cx="12" cy="12" r="10"/>' +
      '<line x1="2" x2="22" y1="12" y2="12"/>' +
      '<path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/>',
    wrench:
      '<path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"/>',
    bot:
      '<path d="M12 8V4H8"/>' +
      '<rect width="16" height="12" x="4" y="8" rx="2"/>' +
      '<path d="M2 14h2"/>' +
      '<path d="M20 14h2"/>' +
      '<path d="M15 13v2"/>' +
      '<path d="M9 13v2"/>',
    flask:
      '<path d="M10 2v7.527a2 2 0 0 1-.211.896L4.72 20.55a1 1 0 0 0 .9 1.45h12.76a1 1 0 0 0 .9-1.45l-5.069-10.127A2 2 0 0 1 14 9.527V2"/>' +
      '<path d="M8.5 2h7"/>' +
      '<path d="M7 16h10"/>',
    contrast:
      '<circle cx="12" cy="12" r="10"/>' +
      '<path d="M12 18a6 6 0 0 0 0-12v12z" fill="currentColor"/>',
    heart:
      '<path d="M19 14c1.49-1.46 3-3.21 3-5.5A5.5 5.5 0 0 0 16.5 3c-1.76 0-3 .5-4.5 2-1.5-1.5-2.74-2-4.5-2A5.5 5.5 0 0 0 2 8.5c0 2.29 1.51 4.04 3 5.5l7 7Z"/>',
    gift:
      '<rect x="3" y="8" width="18" height="4" rx="1"/>' +
      '<path d="M12 8v13"/>' +
      '<path d="M19 12v7a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2v-7"/>' +
      '<path d="M7.5 8a2.5 2.5 0 0 1 0-5A4.8 4.8 0 0 1 12 8a4.8 4.8 0 0 1 4.5-5 2.5 2.5 0 0 1 0 5"/>',
  };

  // Display order for the picker grid (5 columns × 6 rows = 30 icons).
  const ORDER = [
    'folder', 'dollar', 'book', 'graduation', 'pencil',
    'pin', 'code', 'film', 'music', 'trash',
    'scissors', 'language', 'stethoscope', 'wand', 'hand',
    'monitor', 'chart', 'user', 'sliders', 'document',
    'scale', 'mic', 'plane', 'globe', 'wrench',
    'bot', 'flask', 'contrast', 'heart', 'gift',
  ];

  function svg(key) {
    const body = PATHS[key] || PATHS.code;
    return '<svg ' + SVG_ATTRS + '>' + body + '</svg>';
  }

  function has(key) {
    return Object.prototype.hasOwnProperty.call(PATHS, key);
  }

  window.PROJECT_ICONS = { svg, has, ORDER, DEFAULT_KEY: 'code' };
})();
