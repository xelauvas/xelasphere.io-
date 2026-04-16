/**
 * Code block renderer — macOS-chrome code window (traffic-light dots,
 * language badge, copy button). Uses Prism.js for highlighting.
 *
 *   <div class="code-window">
 *     <div class="code-header">
 *       <!-- ::before paints traffic-light dots -->
 *       <span class="language-indicator">python</span>
 *       <button class="copy-button"><svg/><span>Copy code</span></button>
 *     </div>
 *     <pre><code class="language-python">…</code></pre>
 *   </div>
 *
 * - Falls back to language-plaintext for empty/unrecognized languages.
 * - Skips Prism for diff/patch and emits per-line spans instead.
 * - Blinking pulse cursor inside <pre> while streaming.
 */

function escapeHtml(s) {
    return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

function renderDiff(text) {
    return text.split('\n').map(line => {
        let cls;
        if (line.startsWith('+++') || line.startsWith('---')) cls = 'diff-meta';
        else if (line.startsWith('@@'))                       cls = 'diff-hunk';
        else if (line.startsWith('+'))                        cls = 'diff-added';
        else if (line.startsWith('-'))                        cls = 'diff-removed';
        else                                                  cls = 'diff-ctx';
        const body = escapeHtml(line) || ' ';
        return `<span class="diff-line ${cls}">${body}</span>`;
    }).join('\n');
}

const COPY_SVG = `
<svg class="copy-icon" viewBox="0 0 24 24" width="13" height="13" fill="none"
     stroke="currentColor" stroke-width="2"
     stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
    <rect x="9" y="9" width="13" height="13" rx="2"/>
    <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>
</svg>`;

const CHECK_SVG = `
<svg class="copy-icon" viewBox="0 0 24 24" width="13" height="13" fill="none"
     stroke="currentColor" stroke-width="2.4"
     stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
    <polyline points="20 6 9 17 4 12"/>
</svg>`;

export function createCodeRenderer() {
    return {
        create(block, messageContainer, opts = {}) {
            const lang = block.meta.lang || 'plaintext';
            const noAnim = opts.replay ? ' stream-no-anim' : '';
            const streamingClass = opts.replay ? '' : ' is-streaming';

            const wrapper = document.createElement('div');
            wrapper.className = `stream-block block-code code-window${streamingClass}${noAnim}`;
            wrapper.dataset.blockId = block.id;
            wrapper.dataset.lang = lang;

            const header = document.createElement('div');
            header.className = 'code-header';

            const badge = document.createElement('span');
            badge.className = 'language-indicator';
            badge.textContent = lang;
            header.appendChild(badge);

            const copyBtn = document.createElement('button');
            copyBtn.className = 'copy-button';
            copyBtn.type = 'button';
            copyBtn.innerHTML = `${COPY_SVG}<span class="copy-label">Copy code</span>`;
            const copyLabel = copyBtn.querySelector('.copy-label');
            header.appendChild(copyBtn);

            const pre = document.createElement('pre');
            const code = document.createElement('code');
            code.className = `language-${lang}`;
            pre.appendChild(code);

            // Blinking pulse cursor while live-streaming. Removed on finalize.
            let cursor = null;
            if (!opts.replay) {
                cursor = document.createElement('span');
                cursor.className = 'stream-cursor';
                pre.appendChild(cursor);
                block.meta.ownsCursor = true;
            }

            wrapper.appendChild(header);
            wrapper.appendChild(pre);
            messageContainer.appendChild(wrapper);

            block.el = wrapper;
            block.meta.preEl = pre;
            block.meta.codeEl = code;
            block.meta.cursorEl = cursor;
            block.meta.copyBtn = copyBtn;
            block.meta.copyLabel = copyLabel;
            return wrapper;
        },
        update(block) {
            if (!block.meta.codeEl) return;
            const lang = (block.meta.lang || 'plaintext').trim().toLowerCase();
            if (lang === 'diff' || lang === 'patch') {
                block.meta.codeEl.innerHTML = renderDiff(block.text);
                return;
            }
            // Always set the plain text — this wipes any prior highlighted
            // HTML and lets the user see new tokens immediately.
            block.meta.codeEl.textContent = block.text;

            if (!window.Prism) return;

            // Throttle Prism to bound O(n²) cost on long blocks.
            //  - Only re-highlight when the last char is a newline (natural
            //    break — Prism's tokenizer needs whole lines for multi-line
            //    constructs like template strings / triple-quoted blocks).
            //  - AND at most once per 150ms — caps worst-case highlight
            //    frequency when code streams in complete-line bursts.
            // Finalize ignores this throttle and always re-highlights.
            const now = performance.now();
            const lastAt = block.meta.lastHighlightAt || 0;
            const endsOnNewline = block.text.length > 0 && block.text.charCodeAt(block.text.length - 1) === 10;
            if (endsOnNewline && (now - lastAt) >= 150) {
                window.Prism.highlightElement(block.meta.codeEl);
                block.meta.lastHighlightAt = now;
            }
        },
        finalize(block) {
            this.update(block);
            if (block.el) block.el.classList.remove('is-streaming');
            if (block.meta.cursorEl) {
                block.meta.cursorEl.remove();
                block.meta.cursorEl = null;
            }
            const finLang = (block.meta.lang || 'plaintext').toLowerCase();
            if (window.Prism && block.meta.codeEl && finLang !== 'diff' && finLang !== 'patch') {
                queueMicrotask(() => window.Prism.highlightElement(block.meta.codeEl));
            }
            this.wireCopyButton(block);
            this.maybeCollapse(block);
        },
        maybeCollapse(block) {
            const COLLAPSE_AT = 30;
            const totalLines = block.text ? block.text.split('\n').length : 0;
            if (totalLines <= COLLAPSE_AT) return;
            if (!block.el || block.el.querySelector('.code-expand-footer')) return;

            block.el.classList.add('is-collapsed');
            const hidden = totalLines - COLLAPSE_AT;

            const footer = document.createElement('button');
            footer.className = 'code-expand-footer';
            footer.type = 'button';
            footer.textContent = `Show ${hidden} more line${hidden === 1 ? '' : 's'}`;
            footer.addEventListener('click', () => {
                block.el.classList.remove('is-collapsed');
                footer.remove();
            });
            block.el.appendChild(footer);
        },
        wireCopyButton(block) {
            const btn = block.meta.copyBtn;
            const label = block.meta.copyLabel;
            if (!btn || btn.dataset.wired) return;
            btn.dataset.wired = '1';
            btn.addEventListener('click', () => {
                navigator.clipboard?.writeText(block.text);
                const icon = btn.querySelector('.copy-icon');
                icon?.outerHTML && (icon.outerHTML = CHECK_SVG);
                if (label) label.textContent = 'Copied';
                btn.classList.add('copied');
                setTimeout(() => {
                    const curIcon = btn.querySelector('.copy-icon');
                    if (curIcon) curIcon.outerHTML = COPY_SVG;
                    if (label) label.textContent = 'Copy code';
                    btn.classList.remove('copied');
                }, 1800);
            });
        },
    };
}
