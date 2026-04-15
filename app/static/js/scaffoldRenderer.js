/**
 * Structure Intelligence v2 — client-side renderer for structured responses.
 *
 * Spec: docs/superpowers/specs/2026-04-15-structure-intelligence-v2-design.md §4.6
 * Plan: W-23
 *
 * Consumes the JSON payload shipped by the server on the `structured` SSE
 * event and renders to DOM. Pure function — no module state, no network,
 * no side effects other than mutating the target element.
 *
 * Input shape (STRUCTURED_RESPONSE_SCHEMA):
 *   {
 *     opening: string,
 *     sections: [{title, emoji, body, style: "prose"|"list"|"steps"}],
 *     closing: string,
 *   }
 *
 * Note: full Jest/jsdom unit tests were deferred — no JS test harness in
 * this repo and setting one up was out of proportion for the scope. Behavior
 * is covered by integration tests (W-25) and manual browser QA (W-27).
 */
(function (global) {
  'use strict';

  /**
   * Render a structured response JSON into the given element.
   * Clears the element first. Safe against null/missing fields.
   *
   * @param {Object} data  - Parsed structured JSON from the server
   * @param {HTMLElement} targetEl - Container element to render into
   * @returns {void}
   */
  function renderStructuredResponse(data, targetEl) {
    if (!targetEl) return;
    if (!data || typeof data !== 'object') {
      targetEl.textContent = '';
      return;
    }

    // Clear any existing content.
    while (targetEl.firstChild) targetEl.removeChild(targetEl.firstChild);

    // Opening paragraph (optional).
    const opening = (data.opening || '').trim();
    if (opening) {
      targetEl.appendChild(_paragraph(opening));
    }

    const sections = Array.isArray(data.sections) ? data.sections : [];

    // If opening existed AND sections exist, insert an <hr> separator —
    // matches the scaffold.separator contract (the SSE-emitted scaffold
    // tells the client whether a separator was planned).
    if (opening && sections.length > 0) {
      targetEl.appendChild(document.createElement('hr'));
    }

    // Sections.
    for (let i = 0; i < sections.length; i++) {
      const s = sections[i];
      if (!s || typeof s !== 'object') continue;
      const header = _sectionHeader(s);
      if (header) targetEl.appendChild(header);

      const body = (s.body || '').trim();
      if (!body) continue;
      const style = (s.style || 'prose').toLowerCase();
      switch (style) {
        case 'list':
          targetEl.appendChild(_renderList(body, 'ul'));
          break;
        case 'steps':
          targetEl.appendChild(_renderList(body, 'ol'));
          break;
        case 'prose':
        default:
          _renderProse(body, targetEl);
          break;
      }
    }

    const closing = (data.closing || '').trim();
    if (closing) {
      targetEl.appendChild(_paragraph(closing));
    }
  }

  // ---------- helpers ----------

  function _sectionHeader(section) {
    const title = (section.title || '').trim();
    if (!title) return null;
    const emoji = (section.emoji || '').trim();
    const h = document.createElement('h2');
    h.textContent = emoji ? `${emoji} ${title}` : title;
    return h;
  }

  function _paragraph(text) {
    const p = document.createElement('p');
    p.textContent = text;
    return p;
  }

  /**
   * Split prose on blank lines and render each paragraph as its own <p>.
   * textContent usage means HTML in the body is NOT rendered — prevents
   * injection of scripts / arbitrary tags from LLM output.
   */
  function _renderProse(body, container) {
    const paragraphs = body
      .split(/\n\s*\n/)
      .map(function (p) { return p.trim(); })
      .filter(Boolean);
    for (let i = 0; i < paragraphs.length; i++) {
      container.appendChild(_paragraph(paragraphs[i]));
    }
  }

  /**
   * Parse a markdown list body into the given list element.
   * Accepts '-' / '*' bullets and '1. 2.' numbered items.
   */
  function _renderList(body, tagName) {
    const list = document.createElement(tagName);
    const lines = body.split('\n')
      .map(function (l) { return l.trim(); })
      .filter(Boolean);
    for (let i = 0; i < lines.length; i++) {
      const line = lines[i];
      // Strip leading bullet / number marker:
      const text = line.replace(/^\s*(?:[-*]|\d+\.)\s*/, '').trim();
      if (!text) continue;
      const li = document.createElement('li');
      li.textContent = text;
      list.appendChild(li);
    }
    return list;
  }

  // Expose on window (browser) and module.exports (if loaded in Node).
  const api = { renderStructuredResponse: renderStructuredResponse };
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = api;
  } else {
    global.scaffoldRenderer = api;
  }
})(typeof window !== 'undefined' ? window : globalThis);
