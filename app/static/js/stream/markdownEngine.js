/**
 * Markdown engine for assistant messages.
 *
 * One real CommonMark + GFM parser (markdown-it) for everything, instead of a
 * hand-written line classifier. That is what makes these work:
 *   - ordered lists keep their numbers when code or paragraphs sit between steps
 *   - nested lists, loose lists, paragraphs and code blocks inside list items
 *   - block quotes with several paragraphs, tables, rules, task lists
 *   - LaTeX: \( \), \[ \], $ $ and $$ $$
 *   - every code block (also inside lists and quotes) gets the same header,
 *     highlighting and copy button
 *
 * parseBlocks(text, { streaming }) returns the top-level blocks of the document,
 * each with a `key` (type + source) so the renderer can leave finished blocks
 * untouched and only repaint the one that is still growing.
 */

const escapeHtml = (s) => String(s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');

// ----------------------------------------------------------------- assets
// KaTeX and extra Prism grammars load after first paint. Blocks rendered with a
// fallback are marked; when the asset arrives every live renderer repaints them.
const listeners = new Set();
let assetEpoch = 0;
export function onAssetsChanged(fn) { listeners.add(fn); return () => listeners.delete(fn); }
export function getAssetEpoch() { return assetEpoch; }
function assetsChanged() {
    assetEpoch++;
    listeners.forEach((fn) => { try { fn(); } catch (_) { /* ignore */ } });
}

let katexWatch = null;
function waitForKatex() {
    if (katexWatch || window.katex) return;
    let tries = 0;
    katexWatch = setInterval(() => {
        tries++;
        if (window.katex) { clearInterval(katexWatch); katexWatch = null; assetsChanged(); }
        else if (tries > 100) { clearInterval(katexWatch); katexWatch = null; }
    }, 150);
}

const LANG_ALIASES = {
    js: 'javascript', jsx: 'jsx', ts: 'typescript', tsx: 'tsx', py: 'python', python3: 'python',
    sh: 'bash', shell: 'bash', zsh: 'bash', console: 'bash', terminal: 'bash', shellscript: 'bash',
    yml: 'yaml', html: 'markup', xml: 'markup', svg: 'markup', vue: 'markup',
    'c++': 'cpp', 'c#': 'csharp', cs: 'csharp', golang: 'go', rs: 'rust', rb: 'ruby',
    md: 'markdown', kt: 'kotlin', ps1: 'powershell', dockerfile: 'docker', tf: 'hcl',
    objc: 'objectivec', text: '', txt: '', plaintext: '', plain: '', '': '',
};
const requestedLangs = new Set();
const failedLangs = new Set();

function prismLang(lang) {
    const l = String(lang || '').trim().toLowerCase();
    return Object.prototype.hasOwnProperty.call(LANG_ALIASES, l) ? LANG_ALIASES[l] : l;
}

function requestLanguage(lang) {
    const P = window.Prism;
    if (!P || !P.plugins || !P.plugins.autoloader || requestedLangs.has(lang) || failedLangs.has(lang)) return;
    requestedLangs.add(lang);
    try {
        P.plugins.autoloader.loadLanguages([lang], () => assetsChanged(), () => failedLangs.add(lang));
    } catch (_) { failedLangs.add(lang); }
}

const MAX_LIVE_HIGHLIGHT = 14000; // chars; larger blocks are highlighted once, at the end

function highlightCode(code, lang, env) {
    const l = prismLang(lang);
    const P = window.Prism;
    if (l === 'diff' || l === 'patch') {
        return code.split('\n').map((line) => {
            let cls = 'diff-ctx';
            if (line.startsWith('+++') || line.startsWith('---')) cls = 'diff-meta';
            else if (line.startsWith('@@')) cls = 'diff-hunk';
            else if (line.startsWith('+')) cls = 'diff-added';
            else if (line.startsWith('-')) cls = 'diff-removed';
            return `<span class="diff-line ${cls}">${escapeHtml(line) || ' '}</span>`;
        }).join('\n');
    }
    if (!P || !l) return escapeHtml(code);
    if (env && env.streaming && code.length > MAX_LIVE_HIGHLIGHT) { env.usedFallback = true; return escapeHtml(code); }
    const grammar = P.languages[l];
    if (!grammar) {
        if (!failedLangs.has(l)) { requestLanguage(l); if (env) env.usedFallback = true; }
        return escapeHtml(code);
    }
    try { return P.highlight(code, grammar, l); } catch (_) { return escapeHtml(code); }
}

function renderMath(tex, display, env) {
    const src = String(tex || '').trim();
    if (!window.katex) {
        waitForKatex();
        if (env) env.usedFallback = true;
        return `<span class="xmd-math-raw">${escapeHtml(src)}</span>`;
    }
    try {
        return window.katex.renderToString(src, { throwOnError: false, displayMode: !!display, strict: 'ignore' });
    } catch (_) {
        return `<code class="xmd-math-raw">${escapeHtml(src)}</code>`;
    }
}

const COPY_SVG = '<svg class="copy-icon" viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>';

function codeWindow(code, info, env) {
    const lang = String(info || '').trim().split(/\s+/)[0] || '';
    const body = code.replace(/\n$/, '');
    if (lang.toLowerCase() === 'mermaid') {
        return `<div class="xmd-mermaid" data-mermaid-src="${escapeHtml(body)}"><pre><code>${escapeHtml(body)}</code></pre></div>\n`;
    }
    const label = escapeHtml(lang || 'text');
    const cls = escapeHtml(prismLang(lang) || 'plaintext');
    return `<div class="code-window" data-lang="${label}">`
        + `<div class="code-header"><span class="language-indicator">${label}</span>`
        + `<button class="copy-button" type="button" aria-label="Copy code">${COPY_SVG}<span class="copy-label">Copy code</span></button></div>`
        + `<pre><code class="language-${cls}">${highlightCode(body, lang, env)}</code></pre></div>\n`;
}

// ----------------------------------------------------------------- plugins
function mathPlugin(md) {
    // \( … \)   \[ … \]   $ … $   $$ … $$ inside a line
    function mathInline(state, silent) {
        const src = state.src;
        const pos = state.pos;
        const max = state.posMax;
        const ch = src.charCodeAt(pos);
        let open; let close; let display = false;
        if (ch === 0x5C /* \ */) {
            const next = src[pos + 1];
            if (next === '(') { open = '\\('; close = '\\)'; }
            else if (next === '[') { open = '\\['; close = '\\]'; display = true; }
            else return false;
        } else if (ch === 0x24 /* $ */) {
            if (src[pos + 1] === '$') { open = '$$'; close = '$$'; display = true; }
            else { open = '$'; close = '$'; }
        } else return false;

        const start = pos + open.length;
        let end;
        if (open === '$') {
            // Currency guard: "$5 and $10" is not math. The opener must be followed
            // by a non-space, the closer preceded by a non-space and not followed by a digit.
            if (start >= max || /\s/.test(src[start])) return false;
            end = start;
            for (;;) {
                end = src.indexOf('$', end);
                if (end === -1 || end >= max) return false;
                if (src[end - 1] === '\\') { end++; continue; }
                break;
            }
            if (end === start || /\s/.test(src[end - 1]) || /\d/.test(src[end + 1] || '')) return false;
        } else {
            end = src.indexOf(close, start);
            if (end === -1 || end > max) {
                // Still being written: hide the half-typed formula instead of showing raw TeX.
                if (state.env && state.env.streaming && open !== '$$') {
                    if (!silent) {
                        const t = state.push('math_inline', 'math', 0);
                        t.content = ''; t.meta = { pending: true, display };
                    }
                    state.pos = max;
                    return true;
                }
                return false;
            }
        }
        if (!silent) {
            const token = state.push('math_inline', 'math', 0);
            token.content = src.slice(start, end);
            token.meta = { display };
        }
        state.pos = end + close.length;
        return true;
    }

    // $$ … $$ and \[ … \] as their own block (one line or many)
    function mathBlock(state, startLine, endLine, silent) {
        const pos = state.bMarks[startLine] + state.tShift[startLine];
        const max = state.eMarks[startLine];
        if (state.sCount[startLine] - state.blkIndent >= 4) return false;
        const src = state.src;
        let close;
        if (src.startsWith('$$', pos)) close = '$$';
        else if (src.startsWith('\\[', pos)) close = '\\]';
        else return false;

        const first = src.slice(pos + 2, max);
        let content; let nextLine; let found = false;
        const idx = first.indexOf(close);
        if (idx !== -1 && first.slice(idx + 2).trim() === '') {
            content = first.slice(0, idx); nextLine = startLine + 1; found = true;
        } else {
            if (idx !== -1) return false; // "\[a\] and more text": leave it to the inline rule
            const lines = [first];
            for (nextLine = startLine + 1; nextLine < endLine; nextLine++) {
                const p = state.bMarks[nextLine] + state.tShift[nextLine];
                const line = src.slice(p, state.eMarks[nextLine]);
                const trimmed = line.trimEnd();
                if (trimmed.endsWith(close)) { lines.push(trimmed.slice(0, -2)); found = true; nextLine++; break; }
                lines.push(line);
            }
            content = lines.join('\n');
        }
        if (!found) {
            if (!(state.env && state.env.streaming)) return false;
            nextLine = endLine;
        }
        if (silent) return true;
        const token = state.push('math_block', 'math', 0);
        token.block = true;
        token.content = content;
        token.map = [startLine, nextLine];
        token.meta = { pending: !found };
        state.line = nextLine;
        return true;
    }

    md.inline.ruler.before('escape', 'math_inline', mathInline);
    md.block.ruler.before('fence', 'math_block', mathBlock, { alt: ['paragraph', 'reference', 'blockquote', 'list'] });

    md.renderer.rules.math_inline = (tokens, idx, _o, env) => {
        const t = tokens[idx];
        if (t.meta && t.meta.pending) return '<span class="xmd-math-pending" aria-hidden="true"></span>';
        return t.meta && t.meta.display
            ? `<span class="xmd-math xmd-math--display">${renderMath(t.content, true, env)}</span>`
            : `<span class="xmd-math">${renderMath(t.content, false, env)}</span>`;
    };
    md.renderer.rules.math_block = (tokens, idx, _o, env) => {
        const t = tokens[idx];
        if (t.meta && t.meta.pending) return '<div class="xmd-math xmd-math--block is-pending" aria-hidden="true"><span></span><span></span><span></span></div>\n';
        return `<div class="xmd-math xmd-math--block">${renderMath(t.content, true, env)}</div>\n`;
    };
}

function taskListPlugin(md) {
    md.core.ruler.after('inline', 'xmd_task_lists', (state) => {
        const tokens = state.tokens;
        for (let i = 2; i < tokens.length; i++) {
            const inline = tokens[i];
            if (inline.type !== 'inline' || tokens[i - 1].type !== 'paragraph_open' || tokens[i - 2].type !== 'list_item_open') continue;
            const m = /^\[([ xX])\][  ]+/.exec(inline.content);
            if (!m || !inline.children || !inline.children.length || inline.children[0].type !== 'text') continue;
            const checked = m[1] !== ' ';
            inline.content = inline.content.slice(m[0].length);
            inline.children[0].content = inline.children[0].content.slice(m[0].length);
            const box = new state.Token('html_inline', '', 0);
            box.content = `<input class="xmd-task" type="checkbox" disabled${checked ? ' checked' : ''}>`;
            inline.children.unshift(box);
            tokens[i - 2].attrJoin('class', checked ? 'task-list-item is-checked' : 'task-list-item');
            // mark the enclosing list
            const level = tokens[i - 2].level - 1;
            for (let j = i - 3; j >= 0; j--) {
                if (tokens[j].level === level && (tokens[j].type === 'bullet_list_open' || tokens[j].type === 'ordered_list_open')) {
                    if (!/\bcontains-task-list\b/.test(tokens[j].attrGet('class') || '')) tokens[j].attrJoin('class', 'contains-task-list');
                    break;
                }
            }
        }
    });
}

// Models write a few harmless tags by habit, most of all <br> inside table cells.
// Raw HTML stays off; these are re-emitted as fixed strings, never passed through.
const SAFE_TAG_RE = /^<(\/?)(br|sub|sup|kbd|mark|u)\s*\/?>/i;
function safeTagPlugin(md) {
    md.inline.ruler.before('escape', 'xmd_safe_tag', (state, silent) => {
        if (state.src.charCodeAt(state.pos) !== 0x3C /* < */) return false;
        const m = SAFE_TAG_RE.exec(state.src.slice(state.pos, state.pos + 8));
        if (!m) return false;
        if (!silent) {
            const tag = m[2].toLowerCase();
            const token = state.push('html_inline', '', 0);
            token.content = tag === 'br' ? '<br>' : `<${m[1]}${tag}>`;
        }
        state.pos += m[0].length;
        return true;
    });
}

function chromePlugin(md) {
    md.renderer.rules.fence = (tokens, idx, _o, env) => codeWindow(tokens[idx].content, tokens[idx].info, env);
    md.renderer.rules.code_block = (tokens, idx, _o, env) => codeWindow(tokens[idx].content, '', env);
    md.renderer.rules.table_open = () => '<div class="xmd-table"><table>\n';
    md.renderer.rules.table_close = () => '</table></div>\n';
    const defaultLink = md.renderer.rules.link_open
        || ((tokens, idx, options, _env, self) => self.renderToken(tokens, idx, options));
    md.renderer.rules.link_open = (tokens, idx, options, env, self) => {
        const href = tokens[idx].attrGet('href') || '';
        if (/^(https?:)?\/\//i.test(href)) {
            tokens[idx].attrSet('target', '_blank');
            tokens[idx].attrSet('rel', 'noopener noreferrer');
        }
        return defaultLink(tokens, idx, options, env, self);
    };
}

let engine = null;
function getEngine() {
    if (engine) return engine;
    if (typeof window.markdownit !== 'function') return null;
    engine = window.markdownit({
        html: false,        // model output never becomes live HTML
        linkify: true,
        typographer: false, // keep --flags and "quotes" exactly as written
        breaks: true,       // a single newline is a line break, as in every chat UI
    });
    engine.linkify.set({ fuzzyLink: false, fuzzyEmail: false }); // only explicit http(s):// links
    engine.use(mathPlugin).use(taskListPlugin).use(safeTagPlugin).use(chromePlugin);
    return engine;
}

export function engineReady() { return !!getEngine(); }

const TYPE_BY_TOKEN = {
    paragraph_open: 'paragraph', heading_open: 'heading', bullet_list_open: 'list-ul',
    ordered_list_open: 'list-ol', blockquote_open: 'blockquote', table_open: 'table',
    fence: 'fence', code_block: 'fence', hr: 'hr', math_block: 'math', html_block: 'paragraph',
};

/**
 * Split the document into top-level blocks.
 * Each block: { type, key, pending, render(env) → html }
 */
export function parseBlocks(text, { streaming = false } = {}) {
    const md = getEngine();
    if (!md) return null;
    const env = { streaming };
    const tokens = md.parse(text || '', env);
    const lines = (text || '').split('\n');
    const blocks = [];
    let i = 0;
    while (i < tokens.length) {
        const open = tokens[i];
        let end = i;
        if (open.nesting === 1) {
            let depth = 0;
            for (let j = i; j < tokens.length; j++) {
                depth += tokens[j].nesting;
                if (depth === 0) { end = j; break; }
            }
        }
        const slice = tokens.slice(i, end + 1);
        const type = TYPE_BY_TOKEN[open.type] || 'paragraph';
        const map = open.map || [0, 0];
        const source = lines.slice(map[0], map[1]).join('\n');
        const pending = !!(open.meta && open.meta.pending);
        blocks.push({
            type,
            pending,
            // streaming is part of the key only where it changes the output
            key: `${type}\u0000${pending ? 'p' : ''}\u0000${source}`,
            render() {
                const renderEnv = { ...env, usedFallback: false };
                const html = md.renderer.render(slice, md.options, renderEnv);
                return { html, usedFallback: renderEnv.usedFallback };
            },
        });
        i = end + 1;
    }
    return blocks;
}

// ----------------------------------------------------------------- streaming tail
// A fence can open right after a list marker or a quote mark ("3. ```sql", "> ```").
const FENCE_OPEN_RE = /^\s*(?:(?:[-*+]|\d{1,9}[.)])\s+|>\s?)*(`{3,}|~{3,})/;
const FENCE_LINE_RE = /^\s*(?:>\s?)*(`{3,}|~{3,})\s*$/;

// Walks the lines once and reports whether the text ends inside a code fence.
function scanFences(lines, onLine) {
    let fence = null;
    for (let i = 0; i < lines.length; i++) {
        const line = lines[i];
        let inside = !!fence;
        if (fence) {
            const m = FENCE_LINE_RE.exec(line);
            if (m && m[1][0] === fence[0] && m[1].length >= fence.length) fence = null;
        } else {
            const m = FENCE_OPEN_RE.exec(line);
            if (m) { fence = m[1]; inside = true; }
        }
        if (onLine) onLine(line, inside, i);
    }
    return !!fence;
}
// A line that is only markdown punctuation so far ("-", "##", "1.", "|", "``", ">").
// Rendering it early makes the block flash as the wrong thing (a lone "-" under a
// paragraph is a heading underline), so it waits for its first real character.
const MARKER_ONLY_RE = /^\s*(?:[-=*_+>#|`~]+|\d{1,3}[.)]?|\\)\s*$/;
// "3. ```" or "```sq": a fence opener still being typed (its language is not complete)
const PARTIAL_FENCE_RE = /^\s*(?:(?:[-*+]|\d{1,9}[.)])\s+|>\s?)*(?:`{3,}|~{3,})[\w.+#-]*$/;
const TABLE_DELIM_RE = /^\s*\|?\s*:?-{1,}:?\s*(?:\|\s*:?-{1,}:?\s*)*\|?\s*$/;

function insideOpenFence(lines) {
    return scanFences(lines);
}

// Some models put an empty line between every table row, which turns the table
// into a stack of paragraphs full of pipes. Rows separated by exactly one blank
// line are pulled back together (never inside a code fence).
export function joinSplitTableRows(text) {
    if (!text || text.indexOf('|') === -1 || text.indexOf('\n\n') === -1) return text;
    const lines = text.split('\n');
    const out = [];
    scanFences(lines, (line, inside, i) => {
        if (!inside && line === '' && out.length && /^\s*\|/.test(out[out.length - 1])
            && i + 1 < lines.length && /^\s*\|/.test(lines[i + 1])) return;
        out.push(line);
    });
    return out.join('\n');
}

function countOutsideCode(line, needle) {
    // occurrences of `needle` that are not inside `inline code`
    let count = 0; let inCode = false;
    for (let i = 0; i < line.length; i++) {
        if (line[i] === '`') { inCode = !inCode; continue; }
        if (!inCode && line.startsWith(needle, i)) { count++; i += needle.length - 1; }
    }
    return count;
}

function closeInline(line) {
    let out = line;
    // image or link whose target is still being typed
    out = out.replace(/!\[[^\]\n]*\]\([^)\n]*$/, '');
    out = out.replace(/\[([^\]\n]*)\]\([^)\n]*$/, '$1');
    out = out.replace(/\\$/, ''); // first half of "\(" or "\["
    // an opener with nothing after it yet: "text **", "see `"
    out = out.replace(/(^|[\s(])(?:\*{1,3}|_{1,2}|~~|`+)$/, '$1');
    if ((out.match(/`/g) || []).length % 2 === 1) return out + '`';

    // A closer needs a non-space before it, so trailing spaces go inside it:
    // "**2 " + "**" would print the stars.
    const close = (mark) => {
        const trimmed = out.replace(/\s+$/, '');
        out = trimmed + mark + out.slice(trimmed.length);
    };
    if (countOutsideCode(out, '**') % 2 === 1) {
        // "**bold*" — the second star of the closer has not arrived yet
        if (/[^*\s]\*$/.test(out)) out = out.slice(0, -1);
        close('**');
    }
    if (countOutsideCode(out, '~~') % 2 === 1) {
        if (/[^~\s]~$/.test(out)) out = out.slice(0, -1);
        close('~~');
    } else if (/[^~\s]~$/.test(out)) {
        out = out.slice(0, -1); // first "~" of a closing "~~"
    }
    // single * emphasis: stars that are not part of ** and not a list bullet
    const body = out.replace(/^\s*\*\s/, '').replace(/\*\*/g, '');
    let singles = 0; let inCode = false;
    for (let i = 0; i < body.length; i++) {
        if (body[i] === '`') inCode = !inCode;
        else if (!inCode && body[i] === '*' && !(body[i - 1] === ' ' && (body[i + 1] === ' ' || body[i + 1] === undefined))) singles++;
    }
    if (singles % 2 === 1) close('*');
    return out;
}

/**
 * Text → what should be painted while the answer is still arriving.
 * Closes half-typed inline markup and holds back constructs that cannot be
 * classified yet, so nothing flashes as raw symbols and then changes shape.
 */
export function prepareStreamingText(text) {
    if (!text) return '';
    text = joinSplitTableRows(text);
    const lines = text.split('\n');
    const endsWithNewline = text.endsWith('\n');
    // "```py" while the language is still being typed: wait for the newline, so the
    // block opens once with its final label
    if (!endsWithNewline && PARTIAL_FENCE_RE.test(lines[lines.length - 1])
        && !insideOpenFence(lines.slice(0, -1))) {
        lines.pop();
        return lines.join('\n');
    }
    if (insideOpenFence(lines)) return text;
    if (endsWithNewline) lines.pop();

    if (!endsWithNewline && lines.length) {
        const last = lines[lines.length - 1];
        if (MARKER_ONLY_RE.test(last)) lines.pop();
        else lines[lines.length - 1] = closeInline(last);
    }

    // A table exists only once its delimiter row is complete; until then the
    // header row would show as a line of pipes.
    let start = lines.length;
    while (start > 0 && /^\s*\|/.test(lines[start - 1])) start--;
    const rows = lines.length - start;
    if (rows > 0) {
        const delimComplete = rows >= 2 && TABLE_DELIM_RE.test(lines[start + 1])
            && (rows > 2 || endsWithNewline);
        if (!delimComplete) lines.length = start;
    }
    return lines.join('\n');
}

// Assistant closers that some models add no matter what the prompt says.
const CLOSER_RE = /\s+(?:how\s+(?:can|may|might)\s+(?:i|we)\s+(?:help|assist)\s+you(?:\s+today)?\s*\??|what\s+can\s+i\s+(?:help\s+you\s+with|do\s+for\s+you)(?:\s+today)?\s*\??|is\s+there\s+(?:anything|something)(?:\s+else)?\s+i\s+can\s+(?:help|assist)(?:\s+you)?\s+with\s*\??)\s*$/i;
export function prepareFinalText(text) {
    if (!text) return '';
    text = joinSplitTableRows(text);
    const lines = text.split('\n');
    if (insideOpenFence(lines)) return text;
    return text.replace(CLOSER_RE, '').replace(/[ \t]+$/, '');
}
