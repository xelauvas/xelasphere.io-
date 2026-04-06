(function () {
    const SMALL_SNIPPET_LINE_LIMIT = 12;
    const SMALL_SNIPPET_BYTE_LIMIT = 800;
    const SAFE_TEXT_LANGUAGES = new Set(['text', 'plain', 'markdown']);

    const RENDERERS = [
        {
            name: 'image',
            matches: version => {
                const entry = (version.entrypointPath || '').toLowerCase();
                if (/\.(png|jpg|jpeg|gif|webp|bmp)$/.test(entry)) return true;
                
                const entryFile = version.files?.find(f => f.path === version.entrypointPath);
                if (entryFile?.mime?.startsWith('image/')) return true;

                return false;
            },
            render: (frame, version) => {
                if (!frame) return;
                const fileMap = buildFileMap(version);
                const entry = fileMap[version.entrypointPath] || version.files?.[0];

                if (!entry || !entry.isBinary) {
                    frame.srcdoc = '<p style="padding:1rem;color:#fff;font-family:sans-serif;">Image not found or not a binary file.</p>';
                    return;
                }

                const mime = entry.mime || 'image/png';
                const doc = `
                    <!doctype html>
                    <html>
                    <head>
                        <meta charset="utf-8"/>
                        <style>
                            body { 
                                margin: 0; 
                                display: flex; 
                                align-items: center; 
                                justify-content: center; 
                                min-height: 100vh; 
                                background: #060a14; 
                            }
                            img { 
                                max-width: 100%; 
                                max-height: 100vh; 
                                object-fit: contain; 
                            }
                        </style>
                    </head>
                    <body>
                        <img src="data:${mime};base64,${entry.content}" alt="${escapeHtml(entry.path)}">
                    </body>
                    </html>
                `;
                frame.srcdoc = doc;
            }
        },
        {
            name: 'html',
            matches: version => {
                // Check if this looks like HTML
                const entry = (version.entrypointPath || '').toLowerCase();
                const hasHtml = entry.endsWith('.html') || entry.endsWith('.htm') ||
                    version.files?.some(file => file.path?.toLowerCase() === 'index.html');

                if (!hasHtml) return false;

                // BUT if there's React/JSX content, let React renderer handle it
                const hasReact = version.files?.some(f => {
                    const path = (f.path || '').toLowerCase();
                    if (path.endsWith('.jsx') || path.endsWith('.tsx')) return true;
                    const content = f.content || '';
                    // Check for actual React patterns (not just any self-closing tags)
                    return content.includes('import React') ||
                           content.includes('from "react"') ||
                           content.includes("from 'react'") ||
                           content.includes('useState(') ||
                           content.includes('useEffect(') ||
                           content.includes('useRef(') ||
                           content.includes('React.createElement') ||
                           content.includes('ReactDOM.render') ||
                           content.includes('createRoot');
                });

                // Skip HTML renderer if React content is detected
                if (hasReact) return false;

                return true;
            },
            render: (frame, version) => {
                if (!frame) return;
                const fileMap = buildFileMap(version);
                const entry = version.entrypointPath && fileMap[version.entrypointPath]
                    ? fileMap[version.entrypointPath]
                    : version.files?.find(file => file.path?.toLowerCase() === 'index.html') || version.files?.[0];
                if (!entry) {
                    frame.srcdoc = '<p style="padding:1rem;color:#fff;font-family:sans-serif;">Nothing to preview.</p>';
                    return;
                }
                const cssBlocks = version.files
                    .filter(file => (file.language || '').toLowerCase() === 'css' && file.path !== entry.path)
                    .map(file => `<style>${file.content}</style>`)
                    .join('\n');

                // Helper to strip import statements from JS code
                const stripImports = (code) => {
                    if (!code) return '';
                    let result = code;
                    // Remove all import statement patterns
                    result = result.replace(/import\s+type\s+\{[\s\S]*?\}\s*from\s*['"][^'"]*['"];?/g, '');
                    result = result.replace(/import\s+type\s+\w+\s+from\s*['"][^'"]*['"];?/g, '');
                    result = result.replace(/import\s*\{[\s\S]*?\}\s*from\s*['"][^'"]*['"];?/g, '');
                    result = result.replace(/import\s*\*\s*as\s+\w+\s+from\s*['"][^'"]*['"];?/g, '');
                    result = result.replace(/import\s+\w+\s*,\s*\{[\s\S]*?\}\s*from\s*['"][^'"]*['"];?/g, '');
                    result = result.replace(/import\s+\w+\s+from\s*['"][^'"]*['"];?/g, '');
                    result = result.replace(/import\s*['"][^'"]*['"];?/g, '');
                    result = result.replace(/import\s*\(\s*['"][^'"]*['"]\s*\)/g, 'null');
                    // Line-by-line filter
                    result = result.split('\n').filter(line => {
                        const t = line.trim();
                        if (/^import\s/.test(t) || t.startsWith('import{') || t.startsWith('import(')) return false;
                        if (/\bimport\s*[\s\S]*?from\s*['"]/.test(t)) return false;
                        if (/\bimport\s*['"]/.test(t)) return false;
                        return true;
                    }).join('\n');
                    return result;
                };

                const scriptBlocks = version.files
                    .filter(file => (file.language || '').toLowerCase() === 'javascript' && file.path !== entry.path)
                    .map(file => `<script>${escapeScript(stripImports(file.content))}</script>`)
                    .join('\n');

                // Process HTML content to strip imports from inline <script> tags
                let htmlContent = entry.content || '';
                htmlContent = htmlContent.replace(/<script([^>]*)>([\s\S]*?)<\/script>/gi, (match, attrs, code) => {
                    // Skip if it's an external script (has src attribute)
                    if (/\bsrc\s*=/.test(attrs)) return match;
                    // Skip if it's a module type (modules can have imports)
                    if (/\btype\s*=\s*["']module["']/.test(attrs)) return match;
                    // Strip imports from inline script
                    const cleanCode = stripImports(code);
                    return `<script${attrs}>${cleanCode}</script>`;
                });

                // Check if this is a complete HTML document (single file with everything)
                const isCompleteDocument = /<!doctype\s+html|<html[\s>]/i.test(htmlContent);

                // Build file lookup map for resolving external references
                const cssFileMap = {};
                const jsFileMap = {};
                version.files.forEach(file => {
                    const path = (file.path || '').toLowerCase();
                    if (path.endsWith('.css')) {
                        cssFileMap[file.path] = file.content || '';
                        cssFileMap[path] = file.content || '';
                    } else if (path.endsWith('.js') || path.endsWith('.jsx') || path.endsWith('.tsx') || path.endsWith('.ts') || (file.language || '').toLowerCase() === 'javascript') {
                        jsFileMap[file.path] = file.content || '';
                        jsFileMap[path] = file.content || '';
                    }
                });

                let doc;
                if (isCompleteDocument) {
                    // Complete HTML document - replace external CSS/JS links with inline content
                    doc = htmlContent;

                    // Replace <link rel="stylesheet" href="..."> with inline <style>
                    doc = doc.replace(/<link\s+[^>]*rel\s*=\s*["']stylesheet["'][^>]*>/gi, (match) => {
                        const hrefMatch = match.match(/href\s*=\s*["']([^"']+)["']/i);
                        if (hrefMatch) {
                            const href = hrefMatch[1];
                            const cssContent = cssFileMap[href] || cssFileMap[href.toLowerCase()];
                            if (cssContent) {
                                return `<style>${cssContent}</style>`;
                            }
                        }
                        return match; // Keep original if not found
                    });

                    // Replace <script src="..."></script> with inline <script>
                    doc = doc.replace(/<script\s+[^>]*src\s*=\s*["']([^"']+)["'][^>]*>\s*<\/script>/gi, (match, src) => {
                        const jsContent = jsFileMap[src] || jsFileMap[src.toLowerCase()];
                        if (jsContent) {
                            return `<script>${escapeScript(stripImports(jsContent))}</script>`;
                        }
                        return match; // Keep original if not found (external CDN scripts)
                    });
                } else {
                    // Fragment - wrap in HTML structure
                    doc = [
                        '<!doctype html>',
                        '<html>',
                        '<head>',
                        '<meta charset="utf-8"/>',
                        cssBlocks,
                        '</head>',
                        '<body>',
                        htmlContent,
                        scriptBlocks,
                        '</body>',
                        '</html>'
                    ].join('\n');
                }
                frame.srcdoc = doc;
            }
        },
        {
            name: 'markdown',
            matches: version => {
                const entry = (version.entrypointPath || '').toLowerCase();
                return entry.endsWith('.md') || entry.endsWith('.markdown');
            },
            render: (frame, version) => {
                if (!frame) return;
                const fileMap = buildFileMap(version);
                const entry = version.entrypointPath && fileMap[version.entrypointPath]
                    ? fileMap[version.entrypointPath]
                    : version.files?.[0];
                if (!entry) {
                    frame.srcdoc = '<p style="padding:1rem;color:#fff;font-family:sans-serif;">Nothing to preview.</p>';
                    return;
                }
                const htmlContent = typeof marked !== 'undefined' ? marked.parse(entry.content || '') : entry.content || '';
                const sanitized = typeof DOMPurify !== 'undefined' ? DOMPurify.sanitize(htmlContent) : htmlContent;
                frame.srcdoc = `
                    <!doctype html>
                    <html>
                    <head>
                        <meta charset="utf-8"/>
                        <style>
                            body{font-family: 'Inter', system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;background:#060a14;color:#f4f4f4;padding:24px;line-height:1.6;}
                            pre{background:rgba(255,255,255,0.06);padding:12px;border-radius:8px;overflow:auto;}
                            code{font-family:'JetBrains Mono','Fira Code',monospace;}
                            a{color:#7fb9ff;}
                        </style>
                    </head>
                    <body>${sanitized}</body>
                    </html>
                `;
            }
        },
        {
            name: 'react',
            matches: version => {
                // Check if entrypoint is JSX/TSX
                const entry = (version.entrypointPath || '').toLowerCase();
                if (entry.endsWith('.jsx') || entry.endsWith('.tsx')) return true;

                // Check if any file is JSX/TSX
                if (version.files?.some(f => {
                    const path = (f.path || '').toLowerCase();
                    return path.endsWith('.jsx') || path.endsWith('.tsx');
                })) return true;

                // Check file content for React patterns
                return version.files?.some(file => {
                    const content = file.content || '';
                    const lang = (file.language || '').toLowerCase();
                    return lang === 'jsx' || lang === 'tsx' ||
                           content.includes('import React') ||
                           content.includes('from "react"') ||
                           content.includes("from 'react'") ||
                           (content.includes('React.') && content.includes('render'));
                });
            },
            render: (frame, version) => {
                if (!frame) return;

                // Normalize content: convert literal \n to actual newlines if needed
                const normalizeContent = (code) => {
                    if (!code) return '';
                    const actualNL = (code.match(/\n/g) || []).length;
                    const literalNL = (code.match(/\\n/g) || []).length;
                    if (literalNL > 5 && literalNL > actualNL * 2) {
                        code = code.replace(/\\n/g, '\n').replace(/\\t/g, '\t').replace(/\\"/g, '"');
                    }
                    return code;
                };

                // Collect CSS files (check BOTH path extension AND language field)
                const cssFiles = version.files
                    ?.filter(file => {
                        const path = (file.path || '').toLowerCase();
                        const lang = (file.language || '').toLowerCase();
                        return path.endsWith('.css') || lang === 'css';
                    }) || [];

                console.log('[CSS Debug] version.files:', JSON.stringify(version.files?.map(f => ({
                    path: f.path, lang: f.language, contentLen: (f.content||'').length
                }))));
                console.log('[CSS Debug] Found', cssFiles.length, 'CSS files');

                // Collect all JS/JSX/TS/TSX files
                const jsFiles = version.files?.filter(f => {
                    const path = (f.path || '').toLowerCase();
                    return path.endsWith('.js') || path.endsWith('.jsx') || path.endsWith('.tsx') || path.endsWith('.ts');
                }) || [];

                // Markdown cleanup for DeepSeek (embeds markdown in JSX)
                // Only runs if code actually contains markdown patterns to avoid
                // corrupting valid code (e.g. stripping * multiply operators)
                const cleanMd = (code) => {
                    if (!code) return '';
                    code = normalizeContent(code);

                    // Detect if this code actually has markdown contamination
                    // Look for ** at word boundaries (bold), or # at line starts (headings)
                    const hasBoldMarkdown = /(?:^|[^\\*\w])\*\*\w/.test(code);
                    const hasHeadingMarkdown = /^\s*#{1,6}\s+\w/m.test(code);
                    const hasBulletMarkers = /^\s*[•·‣]\s+/m.test(code);

                    if (!hasBoldMarkdown && !hasHeadingMarkdown && !hasBulletMarkers) {
                        return code; // No markdown detected, return as-is
                    }

                    // 1. Loop paired **bold** removal until stable (handles ****nested****)
                    if (hasBoldMarkdown) {
                        let prev, iter = 0;
                        do {
                            prev = code;
                            code = code.replace(/\*\*(.+?)\*\*/g, '$1');
                            iter++;
                        } while (code !== prev && iter < 10);

                        // 2. Strip remaining unpaired ** (broken markdown)
                        code = code.replace(/\*{2,}/g, (match, offset, str) => {
                            const before = offset > 0 ? str[offset - 1] : '';
                            const after = offset + match.length < str.length ? str[offset + match.length] : '';
                            if (/\w/.test(before) && /\w/.test(after)) return match;
                            return '';
                        });
                    }

                    // 3. Convert markdown headings to JSX (skip comment lines)
                    if (hasHeadingMarkdown) {
                        code = code.replace(/^(\s*)#{1,6}\s+(.+)$/gm, (match, indent, text) => {
                            if (match.trim().startsWith('//') || match.trim().startsWith('/*')) return match;
                            const level = match.trim().match(/^#+/)[0].length;
                            return `${indent}<h${level}>${text}</h${level}>`;
                        });
                    }

                    // 4. Strip bullet markers (•, ·, ‣)
                    if (hasBulletMarkers) {
                        code = code.replace(/^\s*[•·‣]\s+/gm, '');
                    }

                    return code;
                };

                // Sort files: other components first, then App file last
                const appFile = jsFiles.find(f => (f.path || '').toLowerCase().includes('app.'));
                const otherFiles = jsFiles.filter(f => f !== appFile);
                const orderedFiles = [...otherFiles, ...(appFile ? [appFile] : [])];

                // Log raw code BEFORE markdown cleanup
                const rawCode = orderedFiles.map(f => normalizeContent(f.content || '')).join('\n\n');
                console.log('[Artifact] RAW code (before cleanMd):', rawCode);

                // Concatenate all code (keep imports/exports — Babel will convert them)
                let combinedCode = orderedFiles.map(f => cleanMd(f.content || '')).join('\n\n');

                console.log('[Artifact] CLEANED code (after cleanMd):', combinedCode);

                // Embed code AND CSS safely via JSON in data elements
                // Replace </script> to prevent breaking the HTML parser
                const safeJson = (val) => JSON.stringify(val).replace(/<\/script>/gi, '<\\/script>');
                const codePayload = safeJson(combinedCode);
                const cssPayload = safeJson(cssFiles.map(f => normalizeContent(f.content || '')));

                const doc = `<!doctype html>
<html>
<head>
    <meta charset="utf-8"/>
    <meta name="viewport" content="width=device-width, initial-scale=1"/>
    <style>
        body { margin: 0; padding: 0; background: #1a1a2e; color: #f4f4f4; font-family: system-ui, -apple-system, sans-serif; }
        #root { min-height: 100vh; display: flex; justify-content: center; align-items: center; }
    </style>
    <script crossorigin src="https://unpkg.com/react@18/umd/react.development.js"><\/script>
    <script crossorigin src="https://unpkg.com/react-dom@18/umd/react-dom.development.js"><\/script>
    <script src="https://unpkg.com/@babel/standalone/babel.min.js"><\/script>
</head>
<body>
    <div id="root"><p style="padding:2rem;color:#888;font-family:monospace;">Loading React...</p></div>
    <script id="code-data" type="application/json">${codePayload}<\/script>
    <script id="css-data" type="application/json">${cssPayload}<\/script>
    <script>
    (function() {
        // --- 1. INJECT CSS ---
        var cssArray = JSON.parse(document.getElementById('css-data').textContent);
        console.log('[Artifact] CSS:', cssArray.length, 'files,', cssArray.reduce(function(a,b){return a+b.length},0), 'chars');
        cssArray.forEach(function(css) {
            var s = document.createElement('style');
            s.textContent = css;
            document.head.appendChild(s);
        });

        // --- 2. GET CODE ---
        var code = JSON.parse(document.getElementById('code-data').textContent);

        // --- 3. STRIP IMPORTS (resolved via globals) ---
        // Single-line imports: import X from '...', import { X } from '...'
        code = code.replace(/^\\s*import\\s+.*from\\s+['\"][^'\"]+['\"];?\\s*$/gm, '');
        // Multiline destructured: import {\\n  X,\\n  Y\\n} from '...'
        code = code.replace(/^\\s*import\\s*(?:[\\w$]+\\s*,\\s*)?\\{[\\s\\S]*?\\}\\s*from\\s+['\"][^'\"]+['\"];?\\s*$/gm, '');
        // Side-effect imports: import './style.css'
        code = code.replace(/^\\s*import\\s+['\"][^'\"]+['\"];?\\s*$/gm, '');

        // --- 4. STRIP EXPORTS (keep declarations) ---
        code = code.replace(/export\\s+default\\s+function\\s+/g, 'function ');
        code = code.replace(/export\\s+default\\s+class\\s+/g, 'class ');
        code = code.replace(/export\\s+default\\s+/g, 'var __defaultExport = ');
        code = code.replace(/export\\s+(const|let|var|function|class)\\s+/g, '$1 ');

        // --- 4b. FIX MALFORMED JSX ---
        // Self-closing + closing tag: <tag .../></tag> -> <tag ...></tag>
        // Only match when /> is immediately followed by </tag> (no JSX content between)
        code = code.replace(/\\/>\\s*<\\/([a-zA-Z][a-zA-Z0-9]*?)>/g, '></$1>');

        // --- 5. DETECT MAIN COMPONENT ---
        // Collect ALL PascalCase identifiers from functions, classes, and const/let/var assignments
        var componentName = null;
        var allNames = [];
        var funcMatches = code.match(/function\\s+([A-Z][A-Za-z0-9]*)/g);
        if (funcMatches) { funcMatches.forEach(function(m) { allNames.push(m.replace(/function\\s+/, '')); }); }
        var classMatches = code.match(/class\\s+([A-Z][A-Za-z0-9]*)/g);
        if (classMatches) { classMatches.forEach(function(m) { allNames.push(m.replace(/class\\s+/, '')); }); }
        // Arrow function components: const App = () => { ... }
        var constMatches = code.match(/(?:const|let|var)\\s+([A-Z][A-Za-z0-9]*)\\s*=/g);
        if (constMatches) { constMatches.forEach(function(m) { allNames.push(m.replace(/(?:const|let|var)\\s+/, '').replace(/\\s*=$/,'')); }); }
        // Prefer 'App', else use last PascalCase name found
        if (allNames.indexOf('App') !== -1) { componentName = 'App'; }
        else if (allNames.length > 0) { componentName = allNames[allNames.length - 1]; }
        console.log('[Artifact] Detected component:', componentName, 'candidates:', allNames);
        console.log('[Artifact] Code after strip (full):', code);

        // --- 6. TRANSFORM & RENDER ---
        function escErr(s) { var d = document.createElement('div'); d.textContent = String(s); return d.innerHTML; }
        function showError(msg) { document.getElementById('root').innerHTML = '<pre style="padding:1rem;color:#ff6b6b;font-family:monospace;font-size:12px;white-space:pre-wrap;">' + escErr(msg) + '</pre>'; }
        var lastRenderError = '';

        function tryRender(src, label) {
            try {
                var transformed = Babel.transform(src, { presets: ['react'], filename: 'app.jsx' }).code;

                // Build execution wrapper: provide React hooks as locals, return the component
                var hookSetup = 'var useState=React.useState,useEffect=React.useEffect,useRef=React.useRef,useCallback=React.useCallback,useMemo=React.useMemo,useContext=React.useContext,useReducer=React.useReducer,createContext=React.createContext,Fragment=React.Fragment,memo=React.memo,forwardRef=React.forwardRef,useLayoutEffect=React.useLayoutEffect,useDeferredValue=React.useDeferredValue,useTransition=React.useTransition,useId=React.useId;';

                var returnComponent = componentName
                    ? '\\nreturn typeof ' + componentName + '==="function"?' + componentName + ':typeof __defaultExport==="function"?__defaultExport:null;'
                    : '\\nreturn typeof __defaultExport==="function"?__defaultExport:null;';

                var fn = new Function('React', 'ReactDOM', hookSetup + transformed + returnComponent);
                var Component = fn(React, ReactDOM);

                if (typeof Component === 'function') {
                    ReactDOM.createRoot(document.getElementById('root')).render(React.createElement(Component));
                    console.log('[Artifact] Rendered OK (' + label + ')');

                    // Log DOM structure for debugging (3 levels deep)
                    setTimeout(function() {
                        function describeEl(el, depth) {
                            if (!el || depth > 3) return '';
                            var tag = el.tagName.toLowerCase();
                            if (el.className) tag += '.' + el.className.replace(/\\s+/g, '.');
                            if (el.id) tag += '#' + el.id;
                            var kids = [];
                            for (var i = 0; i < Math.min(el.children.length, 8); i++) {
                                kids.push(describeEl(el.children[i], depth + 1));
                            }
                            if (el.children.length > 8) kids.push('...+' + (el.children.length - 8));
                            return tag + (kids.length ? ' > [' + kids.join(', ') + ']' : '');
                        }
                        var root = document.getElementById('root');
                        if (root && root.firstElementChild) {
                            console.log('[Artifact] DOM tree:', describeEl(root.firstElementChild, 0));
                        }
                    }, 500);
                    return true;
                }
                lastRenderError = label + ': Component is ' + (typeof Component);
                console.warn('[Artifact] ' + lastRenderError);
                return false;
            } catch(e) {
                lastRenderError = e.message;
                console.warn('[Artifact] ' + label + ':', e.message);
                return false;
            }
        }

        if (!tryRender(code, 'direct')) {
            // Adjacent JSX error — wrap return(...) bodies in <> ... </>
            var fragCode = code;
            var returnRe = /\\breturn\\s*\\(/g;
            var match;
            var parts = [];
            var lastEnd = 0;
            returnRe.lastIndex = 0;
            while ((match = returnRe.exec(code)) !== null) {
                var openPos = match.index + match[0].length - 1;
                var depth = 1;
                var pos = openPos + 1;
                while (pos < code.length && depth > 0) {
                    if (code[pos] === '(') depth++;
                    else if (code[pos] === ')') depth--;
                    pos++;
                }
                var closePos = pos - 1;
                parts.push(code.substring(lastEnd, openPos + 1));
                parts.push('<>');
                parts.push(code.substring(openPos + 1, closePos));
                parts.push('<\\/>');
                lastEnd = closePos;
            }
            parts.push(code.substring(lastEnd));
            fragCode = parts.join('');
            console.log('[Artifact] Fragment-wrapped code (first 400):', fragCode.substring(0, 400));
            if (!tryRender(fragCode, 'fragment-wrapped')) {
                showError(lastRenderError || 'Could not render component');
            }
        }
    })();
    <\/script>
</body>
</html>`;
                frame.srcdoc = doc;
            }
        },
        {
            name: 'svg',
            matches: version => {
                // Check if entrypoint is SVG
                const entry = (version.entrypointPath || '').toLowerCase();
                if (entry.endsWith('.svg')) return true;

                // Check if any file is SVG
                return version.files?.some(file => {
                    const path = (file.path || '').toLowerCase();
                    return path.endsWith('.svg');
                });
            },
            render: (frame, version) => {
                if (!frame) return;
                const fileMap = buildFileMap(version);

                // Find SVG file
                const entry = version.entrypointPath && fileMap[version.entrypointPath]
                    ? fileMap[version.entrypointPath]
                    : version.files?.find(f => (f.path || '').toLowerCase().endsWith('.svg')) || version.files?.[0];

                if (!entry) {
                    frame.srcdoc = '<p style="padding:1rem;color:#fff;font-family:sans-serif;">No SVG file found.</p>';
                    return;
                }

                const doc = `
                    <!doctype html>
                    <html>
                    <head>
                        <meta charset="utf-8"/>
                        <meta name="viewport" content="width=device-width, initial-scale=1"/>
                        <style>
                            body {
                                margin: 0;
                                padding: 0;
                                display: flex;
                                align-items: center;
                                justify-content: center;
                                min-height: 100vh;
                                background: #060a14;
                            }
                            svg {
                                max-width: 95%;
                                max-height: 95vh;
                                height: auto;
                            }
                        </style>
                    </head>
                    <body>
                        ${entry.content || ''}
                    </body>
                    </html>
                `;
                frame.srcdoc = doc;
            }
        },
        {
            name: 'mermaid',
            matches: version => {
                // Check if entrypoint is Mermaid file
                const entry = (version.entrypointPath || '').toLowerCase();
                if (entry.endsWith('.mmd') || entry.endsWith('.mermaid')) return true;

                // Check file content for Mermaid syntax
                return version.files?.some(file => {
                    const content = file.content || '';
                    const lang = (file.language || '').toLowerCase();

                    // Check language or content patterns
                    return lang === 'mermaid' ||
                           content.trim().startsWith('graph ') ||
                           content.trim().startsWith('sequenceDiagram') ||
                           content.trim().startsWith('classDiagram') ||
                           content.trim().startsWith('stateDiagram') ||
                           content.trim().startsWith('erDiagram') ||
                           content.trim().startsWith('gantt') ||
                           content.trim().startsWith('pie') ||
                           content.trim().startsWith('journey') ||
                           content.trim().startsWith('gitGraph') ||
                           content.trim().startsWith('flowchart');
                });
            },
            render: (frame, version) => {
                if (!frame) return;
                const fileMap = buildFileMap(version);

                // Find Mermaid file
                let entry = null;
                if (version.entrypointPath && fileMap[version.entrypointPath]) {
                    entry = fileMap[version.entrypointPath];
                } else {
                    entry = version.files?.find(f => {
                        const path = (f.path || '').toLowerCase();
                        return path.endsWith('.mmd') || path.endsWith('.mermaid');
                    }) || version.files?.[0];
                }

                if (!entry) {
                    frame.srcdoc = '<p style="padding:1rem;color:#fff;font-family:sans-serif;">No diagram found.</p>';
                    return;
                }

                const doc = `
                    <!doctype html>
                    <html>
                    <head>
                        <meta charset="utf-8"/>
                        <meta name="viewport" content="width=device-width, initial-scale=1"/>
                        <script src="https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.min.js"></script>
                        <style>
                            body {
                                margin: 0;
                                padding: 24px;
                                background: #060a14;
                                color: #f4f4f4;
                                font-family: system-ui, -apple-system, sans-serif;
                            }
                            .mermaid {
                                display: flex;
                                justify-content: center;
                                align-items: center;
                            }
                        </style>
                    </head>
                    <body>
                        <div class="mermaid">
${escapeHtml(entry.content || '')}
                        </div>
                        <script>
                            try {
                                mermaid.initialize({
                                    startOnLoad: true,
                                    theme: 'dark',
                                    themeVariables: {
                                        darkMode: true,
                                        background: '#060a14',
                                        primaryColor: '#5681ff',
                                        primaryTextColor: '#f4f4f4',
                                        primaryBorderColor: '#7fb9ff',
                                        lineColor: '#7fb9ff',
                                        secondaryColor: '#4a5568',
                                        tertiaryColor: '#2d3748'
                                    }
                                });
                            } catch (error) {
                                document.body.innerHTML = '<div style="padding:2rem;color:#ff8686;font-family:monospace;"><strong>Mermaid Error:</strong> ' + error.message + '</div>';
                                console.error('Mermaid render error:', error);
                            }
                        </script>
                    </body>
                    </html>
                `;
                frame.srcdoc = doc;
            }
        },
        {
            name: 'python',
            matches: version => {
                const entry = (version.entrypointPath || '').toLowerCase();
                if (entry.endsWith('.py')) return true;
                if (version.files?.some(f => (f.path || '').toLowerCase().endsWith('.py'))) return true;
                return version.files?.some(f => (f.language || '').toLowerCase() === 'python');
            },
            render: (frame, version) => {
                if (!frame) return;
                const pyFile = version.files?.find(f =>
                    (f.path || '').toLowerCase().endsWith('.py') || (f.language || '').toLowerCase() === 'python'
                ) || version.files?.[0];
                const code = pyFile?.content || '';

                // Collect all .py files for multi-file support
                const allFiles = {};
                (version.files || []).forEach(f => {
                    if ((f.path || '').endsWith('.py')) {
                        allFiles[f.path] = f.content || '';
                    }
                });

                const codePayload = JSON.stringify(code).replace(/<\/script/g, '<\\/script');
                const filesPayload = JSON.stringify(allFiles).replace(/<\/script/g, '<\\/script');
                const entryName = pyFile?.path || 'main.py';

                frame.srcdoc = `<!doctype html>
<html>
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<style>
*{box-sizing:border-box;margin:0;padding:0;}
body{background:#1e1e1e;color:#d4d4d4;font-family:'Söhne Mono',ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:13px;line-height:1.6;height:100vh;display:flex;flex-direction:column;overflow:hidden;}
#toolbar{padding:6px 12px;background:#181818;border-bottom:1px solid rgba(255,255,255,0.06);display:flex;align-items:center;gap:8px;flex-shrink:0;}
#toolbar .file-name{color:rgba(255,255,255,0.4);font-size:11px;}
.tool-btn{padding:3px 10px;font-size:11px;font-weight:500;color:#1e1e1e;background:#4ec9b0;border:none;border-radius:4px;cursor:pointer;transition:background .15s;}
.tool-btn:hover{background:#5edfc4;}
.tool-btn:disabled{opacity:0.4;cursor:wait;}
.tool-btn.ghost{color:rgba(255,255,255,0.5);background:transparent;}
.tool-btn.ghost:hover{color:rgba(255,255,255,0.8);background:rgba(255,255,255,0.06);}
#status{font-size:11px;color:rgba(255,255,255,0.3);margin-left:auto;}
#output-wrap{flex:1;overflow:auto;padding:8px 12px;}
#output{white-space:pre-wrap;word-break:break-word;}
.stdout{color:#d4d4d4;}
.stderr{color:#f44747;}
.repl-in{color:#4ec9b0;}
.repl-prompt{color:#6a9955;}
.plot-container{margin:8px 0;max-width:100%;border-radius:6px;overflow:hidden;}
.plot-container img{max-width:100%;display:block;}
#repl{flex-shrink:0;display:flex;align-items:center;padding:6px 12px;background:#181818;border-top:1px solid rgba(255,255,255,0.06);gap:6px;}
#repl-prompt{color:#4ec9b0;font-size:13px;flex-shrink:0;}
#repl-input{flex:1;background:transparent;border:none;color:#d4d4d4;font-family:inherit;font-size:13px;outline:none;caret-color:#4ec9b0;}
#repl-input::placeholder{color:rgba(255,255,255,0.2);}
</style>
</head>
<body>
<div id="toolbar">
    <button class="tool-btn" onclick="runScript()">&#9654; Run</button>
    <button class="tool-btn ghost" onclick="clearOutput()">Clear</button>
    <span class="file-name">${escapeHtml(entryName)}</span>
    <span id="status"></span>
</div>
<div id="output-wrap">
    <div id="output"></div>
</div>
<div id="repl">
    <span id="repl-prompt">&gt;&gt;&gt;</span>
    <input id="repl-input" type="text" placeholder="Type Python here..." autocomplete="off" spellcheck="false"/>
</div>

<script id="code-data" type="application/json">${codePayload}</script>
<script id="files-data" type="application/json">${filesPayload}</script>

<script>
var pyodideReady = null;
var pyodide = null;
var history = [];
var histIdx = -1;

async function initPyodide() {
    if (pyodideReady) return pyodideReady;
    pyodideReady = (async () => {
        setStatus('Loading Python...');
        if (!window.loadPyodide) {
            var s = document.createElement('script');
            s.src = 'https://cdn.jsdelivr.net/pyodide/v0.25.1/full/pyodide.js';
            document.head.appendChild(s);
            await new Promise((ok, fail) => { s.onload = ok; s.onerror = fail; });
        }
        pyodide = await loadPyodide({ indexURL: 'https://cdn.jsdelivr.net/pyodide/v0.25.1/full/' });

        // Redirect stdout/stderr
        pyodide.runPython(
            'import sys\\n' +
            'class _Out:\\n' +
            '    def __init__(self, cls):\\n' +
            '        self.cls = cls\\n' +
            '    def write(self, text):\\n' +
            '        if text:\\n' +
            '            from js import appendOutput\\n' +
            '            appendOutput(text, self.cls)\\n' +
            '    def flush(self):\\n' +
            '        pass\\n' +
            'sys.stdout = _Out("stdout")\\n' +
            'sys.stderr = _Out("stderr")'
        );

        setStatus('Python 3.11 ready');
        setTimeout(() => setStatus(''), 3000);
        return pyodide;
    })();
    return pyodideReady;
}

function setStatus(t) { document.getElementById('status').textContent = t; }

function appendOutput(text, cls) {
    var el = document.getElementById('output');
    var span = document.createElement('span');
    span.className = cls || 'stdout';
    span.textContent = text;
    el.appendChild(span);
    scrollBottom();
}

function appendHtml(html) {
    var el = document.getElementById('output');
    var div = document.createElement('div');
    div.innerHTML = html;
    el.appendChild(div);
    scrollBottom();
}

function scrollBottom() {
    var w = document.getElementById('output-wrap');
    w.scrollTop = w.scrollHeight;
}

function clearOutput() {
    document.getElementById('output').innerHTML = '';
}

// Packages that need native Pyodide builds (not micropip)
var BUILTIN_PKGS = ['numpy','pandas','matplotlib','scipy','scikit-learn','pillow','sympy','networkx','sqlalchemy'];
// Packages that cannot work in browser
var UNSUPPORTED = ['pygame','tkinter','cv2','opencv','flask','django','fastapi','socket','multiprocessing','subprocess'];

async function loadPackagesFor(code) {
    // Detect all "import X" and "from X import" statements
    var importRe = /(?:^|\\n)\\s*(?:import|from)\\s+([a-zA-Z_][a-zA-Z0-9_]*)/g;
    var match, detected = new Set();
    while ((match = importRe.exec(code)) !== null) detected.add(match[1]);

    // Check for unsupported packages — block execution if found
    var blocked = [];
    for (var u of UNSUPPORTED) {
        if (detected.has(u)) {
            blocked.push(u);
            detected.delete(u);
        }
    }
    if (blocked.length > 0) {
        appendOutput('Cannot run: ' + blocked.join(', ') + ' not available in browser Python.\\n', 'stderr');
        appendOutput('These packages require native OS access and cannot run in WebAssembly.\\n', 'stderr');
        appendOutput('Tip: Use the REPL below for packages that work in the browser.\\n\\n', 'repl-prompt');
        return false;
    }

    // Load built-in Pyodide packages
    var builtins = [];
    for (var d of detected) {
        var pkg = BUILTIN_PKGS.find(function(p) { return p === d || p.replace('-','') === d; });
        if (pkg) { builtins.push(pkg); detected.delete(d); }
    }
    // Remove standard library modules
    var stdlib = ['sys','os','io','json','math','random','re','collections','itertools','functools','datetime','time','string','pathlib','typing','abc','copy','hashlib','base64','struct','csv','textwrap','enum','dataclasses'];
    for (var s of stdlib) detected.delete(s);

    if (builtins.length > 0) {
        setStatus('Loading ' + builtins.join(', ') + '...');
        await pyodide.loadPackage(builtins);
        setStatus('');
    }

    // Try micropip for remaining packages
    var remaining = Array.from(detected);
    if (remaining.length > 0) {
        try {
            await pyodide.loadPackage('micropip');
            var micropip = pyodide.pyimport('micropip');
            for (var pkg of remaining) {
                try {
                    setStatus('Installing ' + pkg + '...');
                    await micropip.install(pkg);
                    setStatus('');
                } catch(e) {
                    appendOutput('Could not install ' + pkg + ': ' + (e.message || e) + '\\n', 'stderr');
                }
            }
            setStatus('');
        } catch(e) {
            setStatus('');
        }
    }
    if (builtins.includes('matplotlib')) {
        pyodide.runPython(
            'import matplotlib; matplotlib.use("Agg")\\n' +
            'import matplotlib.pyplot as _plt\\n' +
            'def _inline_show(*a, **kw):\\n' +
            '    import io, base64\\n' +
            '    from js import appendHtml\\n' +
            '    buf = io.BytesIO()\\n' +
            '    _plt.savefig(buf, format="png", dpi=100, bbox_inches="tight", facecolor="#1e1e1e", edgecolor="none")\\n' +
            '    buf.seek(0)\\n' +
            '    b64 = base64.b64encode(buf.read()).decode()\\n' +
            '    buf.close()\\n' +
            '    appendHtml(\\'<div class="plot-container"><img src="data:image/png;base64,\\' + b64 + \\'"/></div>\\')\\n' +
            '    _plt.close("all")\\n' +
            '_plt.show = _inline_show'
        );
    }
    return true;
}

async function runScript() {
    var py = await initPyodide();
    var code = JSON.parse(document.getElementById('code-data').textContent);
    var files = JSON.parse(document.getElementById('files-data').textContent);

    // Write aux files
    for (var path in files) {
        if (path !== '${entryName.replace(/'/g, "\\'")}') {
            py.FS.writeFile('/home/pyodide/' + path, files[path]);
        }
    }

    appendOutput('--- Running ${entryName.replace(/'/g, "\\'")} ---\\n', 'repl-prompt');

    var canRun = await loadPackagesFor(code);
    if (canRun === false) {
        document.getElementById('repl-input').focus();
        return;
    }

    try {
        await py.runPythonAsync(code);
    } catch (err) {
        appendOutput(err.message || String(err), 'stderr');
    }
    appendOutput('\\n', 'stdout');
    scrollBottom();
    document.getElementById('repl-input').focus();
}

// REPL input handler
var replInput = document.getElementById('repl-input');
replInput.addEventListener('keydown', async function(e) {
    if (e.key === 'Enter') {
        var cmd = replInput.value;
        replInput.value = '';
        if (!cmd.trim()) return;
        history.push(cmd);
        histIdx = history.length;

        appendOutput('>>> ' + cmd + '\\n', 'repl-in');

        // Handle !pip install commands
        if (cmd.trim().startsWith('!pip install') || cmd.trim().startsWith('pip install')) {
            var pkgName = cmd.replace(/!?pip\\s+install\\s+/,'').trim();
            if (pkgName) {
                try {
                    var py = await initPyodide();
                    await py.loadPackage('micropip');
                    var micropip = py.pyimport('micropip');
                    setStatus('Installing ' + pkgName + '...');
                    await micropip.install(pkgName);
                    appendOutput('Successfully installed ' + pkgName + '\\n', 'stdout');
                    setStatus('');
                } catch(e) {
                    appendOutput('Failed to install ' + pkgName + ': ' + (e.message || e) + '\\n', 'stderr');
                    setStatus('');
                }
            }
            scrollBottom();
            return;
        }

        try {
            var py = await initPyodide();
            await loadPackagesFor(cmd);

            // Pass command via globals to avoid quoting issues
            py.globals.set('_repl_cmd', cmd);
            var result = await py.runPythonAsync(
                '_repl_result = None\\n' +
                'try:\\n' +
                '    _repl_code = compile(_repl_cmd, "<repl>", "eval")\\n' +
                '    _repl_result = eval(_repl_code)\\n' +
                'except SyntaxError:\\n' +
                '    exec(compile(_repl_cmd, "<repl>", "exec"))\\n' +
                '_repl_result'
            );
            if (result !== undefined && result !== null && String(result) !== 'None') {
                appendOutput(String(result) + '\\n', 'stdout');
            }
        } catch (err) {
            appendOutput(err.message || String(err), 'stderr');
            appendOutput('\\n', 'stderr');
        }
        scrollBottom();
    } else if (e.key === 'ArrowUp') {
        e.preventDefault();
        if (histIdx > 0) { histIdx--; replInput.value = history[histIdx]; }
    } else if (e.key === 'ArrowDown') {
        e.preventDefault();
        if (histIdx < history.length - 1) { histIdx++; replInput.value = history[histIdx]; }
        else { histIdx = history.length; replInput.value = ''; }
    }
});

// Auto-run script on load, then focus REPL
window.addEventListener('load', function() {
    setTimeout(async function() {
        await runScript();
    }, 100);
});
</script>
</body>
</html>`;
            }
        },
        {
            name: 'plain',
            matches: () => true,
            render: (frame, version) => {
                if (!frame) return;
                const entry = version.files?.find(file => file.path === version.entrypointPath) || version.files?.[0];
                const text = entry?.content || 'Nothing to preview.';
                frame.srcdoc = `
                    <!doctype html>
                    <html>
                    <head>
                        <meta charset="utf-8"/>
                        <style>
                            body{font-family:'JetBrains Mono','Fira Code',monospace;background:#060a14;color:#f5f5f5;padding:24px;white-space:pre-wrap;}
                        </style>
                    </head>
                    <body>${escapeHtml(text)}</body>
                    </html>
                `;
            }
        }
    ];

    class ArtifactPanel {
        constructor() {
            this.panel = document.getElementById('artifact-panel');
            this.statusEl = document.getElementById('artifact-panel-status');
            this.titleEl = document.getElementById('artifact-panel-title');
            this.languageBadge = document.getElementById('artifact-panel-language');
            this.closeBtn = document.getElementById('artifact-panel-close');
            this.downloadBtn = document.getElementById('artifact-download-btn');
            this.versionSelect = document.getElementById('artifact-version-select');
            this.versionBadge = document.getElementById('artifact-version-badge');
            this.tabButtons = Array.from(document.querySelectorAll('[data-artifact-tab]'));
            this.tabPanels = Array.from(document.querySelectorAll('[data-artifact-panel]'));
            this.previewFrame = document.getElementById('artifact-preview-frame');
            this.fileTreeEl = document.getElementById('artifact-file-tree');
            this.fileSelect = document.getElementById('artifact-file-select');
            this.codeBlock = document.getElementById('artifact-code-block');
            this.currentFileName = document.getElementById('artifact-current-file');
            this.copyFileBtn = document.getElementById('artifact-copy-file-btn');
            this.copyAllBtn = document.getElementById('artifact-copy-all-btn');
            this.copyVersionBtn = document.getElementById('artifact-copy-version-btn');
            this.refreshBtn = document.getElementById('artifact-refresh-btn');
            this.publishBtn = document.getElementById('artifact-publish-btn');
            this.infoList = document.getElementById('artifact-info-list');
            this.resizeHandle = document.getElementById('artifact-panel-resize');

            // Artifact switcher dropdown elements
            this.switchBtn = document.getElementById('artifact-switch-btn');
            this.switcherDropdown = document.getElementById('artifact-switcher-dropdown');
            this.switcherList = document.getElementById('artifact-switcher-list');
            this.copyBtn = document.getElementById('artifact-copy-btn');
            this.switcherOpen = false;
            this.artifacts = []; // List of artifacts in current session

            // Code tab with edit mode elements
            this.editModeToggleBtn = document.getElementById('artifact-edit-mode-toggle');
            this.editCodeMirrorContainer = document.getElementById('artifact-edit-codemirror');
            this.editCodeMirror = null;  // Will be initialized when edit mode activates
            this.codeDisplay = document.getElementById('artifact-code-display');
            this.saveEditBtn = document.getElementById('artifact-save-edit-btn');
            this.revertEditBtn = document.getElementById('artifact-revert-edit-btn');
            this.isEditMode = false;

            // Composer elements
            this.composerPrompt = document.getElementById('composer-prompt');
            this.composerSubmitBtn = document.getElementById('composer-submit-btn');
            this.composerClearBtn = document.getElementById('composer-clear-btn');

            // Diff tab elements
            this.diffFromSelect = document.getElementById('artifact-diff-from');
            this.diffToSelect = document.getElementById('artifact-diff-to');
            this.diffFileList = document.getElementById('artifact-diff-file-list');
            this.diffViewer = document.getElementById('artifact-diff-viewer');
            this.diffModeButtons = Array.from(document.querySelectorAll('[data-diff-mode]'));

            // Share button
            this.shareBtn = document.getElementById('artifact-share-btn');

            this.defaultWidth = 860;
            this.minWidth = 600;
            this.currentWidth = this.loadStoredWidth();
            this.versionCache = new Map();
            this.drafts = new Map();
            this.cache = new Map();
            this.token = document.querySelector('meta[name="csrf-token"]')?.getAttribute('content') || '';
            this.isResizing = false;
            this.currentArtifact = null;
            this.currentVersion = null;
            this.currentReference = null;
            this.currentSessionId = null;
            this.activeTab = 'preview';

            // Live streaming state
            this.isStreaming = false;
            this._streamBuffer = '';
            this._contentFound = false;

            // Live patch streaming state
            this.isPatchStreaming = false;
            this._patchQueue = Promise.resolve();

            // Edit state
            this.editingFile = null;
            this.editedFiles = new Map();  // Track edited file contents
            this.originalFileContent = null;

            // Diff state
            this.currentDiffMode = 'side-by-side';

            // Resize performance state
            this.rafId = null;

            this.applyPanelWidth(this.currentWidth, { applyOffset: false });
            // Set default split percentage for CSS
            document.documentElement.style.setProperty('--artifact-chat-width-percent', '0.5');
            this.handleResizeMove = this.onResizeMove.bind(this);
            this.handleResizeStop = this.stopResizing.bind(this);
            this.bindEvents();
        }

        bindEvents() {
            this.closeBtn?.addEventListener('click', () => this.close());
            this.versionSelect?.addEventListener('change', () => {
                this.handleVersionChange();
                if (this.versionBadge) this.versionBadge.value = this.versionSelect.value;
            });
            this.versionBadge?.addEventListener('change', () => {
                if (this.versionSelect) {
                    this.versionSelect.value = this.versionBadge.value;
                    this.handleVersionChange();
                }
            });
            this.fileSelect?.addEventListener('change', () => this.handleFileSelectChange());
            this.downloadBtn?.addEventListener('click', () => this.downloadZip());
            this.copyFileBtn?.addEventListener('click', () => this.copyCurrentFile());
            this.copyAllBtn?.addEventListener('click', () => this.copyAllFiles());
            this.copyVersionBtn?.addEventListener('click', () => this.copyAllFiles());
            this.refreshBtn?.addEventListener('click', () => this.reloadCurrentVersion());
            this.publishBtn?.addEventListener('click', () => this.publishCurrentVersion());
            this.tabButtons.forEach(button => {
                button.addEventListener('click', () => this.switchTab(button.dataset.artifactTab));
            });
            this.resizeHandle?.addEventListener('mousedown', event => this.startResizing(event));

            // Handle responsive layout changes
            let resizeTimeout;
            window.addEventListener('resize', () => {
                this.syncWidthWithViewport();
                clearTimeout(resizeTimeout);
                resizeTimeout = setTimeout(() => {
                    this.handleResponsiveLayout();
                }, 150);
            });

            // Edit mode events
            this.editModeToggleBtn?.addEventListener('click', () => this.toggleEditMode());
            this.saveEditBtn?.addEventListener('click', () => this.saveEdits());
            this.revertEditBtn?.addEventListener('click', () => this.revertEdits());
            // CodeMirror change event is bound in initializeEditMode()

            // Composer events
            this.composerPrompt?.addEventListener('input', () => this.handleComposerInput());
            this.composerSubmitBtn?.addEventListener('click', () => this.submitComposerPrompt());
            this.composerClearBtn?.addEventListener('click', () => this.clearComposerPrompt());

            // Diff tab events
            this.diffFromSelect?.addEventListener('change', () => this.handleDiffSelectionChange());
            this.diffToSelect?.addEventListener('change', () => this.handleDiffSelectionChange());
            this.diffModeButtons.forEach(button => {
                button.addEventListener('click', () => this.switchDiffMode(button.dataset.diffMode));
            });

            // Share button event
            this.shareBtn?.addEventListener('click', () => this.shareArtifact());

            // Artifact switcher dropdown events
            this.switchBtn?.addEventListener('click', (e) => {
                e.stopPropagation();
                this.toggleSwitcherDropdown();
            });
            this.copyBtn?.addEventListener('click', () => this.copyAllFiles());

            // Close dropdown when clicking outside
            document.addEventListener('click', (e) => {
                if (this.switcherOpen && !this.switcherDropdown?.contains(e.target) && e.target !== this.switchBtn) {
                    this.closeSwitcherDropdown();
                }
            });
        }

        // Artifact switcher dropdown methods
        toggleSwitcherDropdown() {
            if (this.switcherOpen) {
                this.closeSwitcherDropdown();
            } else {
                this.openSwitcherDropdown();
            }
        }

        openSwitcherDropdown() {
            if (!this.switcherDropdown) return;
            this.switcherOpen = true;
            this.switcherDropdown.classList.add('is-open');
            this.switcherDropdown.setAttribute('aria-hidden', 'false');
            this.switchBtn?.setAttribute('aria-expanded', 'true');
            this.populateSwitcherList();
        }

        closeSwitcherDropdown() {
            if (!this.switcherDropdown) return;
            this.switcherOpen = false;
            this.switcherDropdown.classList.remove('is-open');
            this.switcherDropdown.setAttribute('aria-hidden', 'true');
            this.switchBtn?.setAttribute('aria-expanded', 'false');
        }

        populateSwitcherList() {
            if (!this.switcherList) return;

            // Clear existing items
            this.switcherList.innerHTML = '';

            // If no artifacts, show empty message
            if (!this.artifacts || this.artifacts.length === 0) {
                const emptyItem = document.createElement('li');
                emptyItem.className = 'artifact-switcher-dropdown__item';
                emptyItem.innerHTML = '<span class="artifact-switcher-dropdown__item-title">No artifacts yet</span>';
                this.switcherList.appendChild(emptyItem);
                return;
            }

            // Create list items for each artifact
            this.artifacts.forEach((artifact) => {
                const item = document.createElement('li');
                item.className = 'artifact-switcher-dropdown__item';
                if (artifact.artifactId === this.currentArtifact?.artifactId) {
                    item.classList.add('is-selected');
                }
                item.setAttribute('role', 'option');
                item.setAttribute('aria-selected', artifact.artifactId === this.currentArtifact?.artifactId ? 'true' : 'false');

                const title = artifact.title || 'Untitled';
                const type = artifact.type === 'project' ? 'Interactive artifact' : 'Code';

                item.innerHTML = `
                    <span class="artifact-switcher-dropdown__item-title">${escapeHtml(title)}</span>
                    <span class="artifact-switcher-dropdown__item-label">${escapeHtml(type)}</span>
                `;

                item.addEventListener('click', () => {
                    this.selectArtifact(artifact);
                    this.closeSwitcherDropdown();
                });

                this.switcherList.appendChild(item);
            });
        }

        selectArtifact(artifact) {
            if (!artifact) return;
            // Switch to this artifact
            this.loadArtifact(artifact.artifactId, this.currentSessionId);
        }

        addArtifactToList(artifact) {
            if (!artifact || !artifact.artifactId) return;
            // Check if already in list
            const exists = this.artifacts.find(a => a.artifactId === artifact.artifactId);
            if (!exists) {
                this.artifacts.push(artifact);
            }
        }

        attachPreview(messageEl, reference, options = {}) {
            console.log('attachPreview called:', { messageEl, reference, options });
            if (!messageEl || !reference) {
                console.log('attachPreview: early return - missing messageEl or reference');
                return;
            }
            // Skip if no artifact payload (code_window mode doesn't have artifact payload)
            if (!options.payload && !reference.artifactId) {
                console.log('attachPreview: early return - no payload and no artifactId (likely code_window mode)');
                return;
            }
            if (!this.supportsArtifact(reference, options.payload)) {
                console.log('attachPreview: early return - supportsArtifact returned false', {
                    language: reference?.primaryLanguage || options.payload?.primaryLanguage,
                    hasFiles: !!options.payload?.files?.length
                });
                return;
            }
            if (this.isSmallSnippet(options.payload)) {
                console.log('attachPreview: rendering inline snippet instead of pill');
                this.renderInlineSnippet(messageEl, options.payload, reference);
                return;
            }

            const footer = this.ensureFooter(messageEl);
            console.log('attachPreview: footer element:', footer);
            if (!footer) return;
            footer.querySelectorAll('.artifact-preview-pill').forEach(node => node.remove());

            const pill = document.createElement('button');
            pill.type = 'button';
            pill.className = 'artifact-chat-pill';
            pill.dataset.artifactId = reference.artifactId || '';
            if (reference.pending) {
                pill.classList.add('is-pending');
            }

            // Get code preview for thumbnail
            const codePreview = this.getCodePreview(options.payload, reference.primaryLanguage);

            // Get the artifact title (just the filename/title, no prefix)
            const artifactTitle = this.getArtifactTitle(reference, options.payload);

            pill.innerHTML = `
                <div class="artifact-chat-pill__content">
                    <div class="artifact-chat-pill__header">
                        <span class="artifact-chat-pill__icon"><i class="fas fa-code"></i></span>
                        <div class="artifact-chat-pill__info">
                            <span class="artifact-chat-pill__title">${artifactTitle}</span>
                            <div class="artifact-chat-pill__meta">
                                ${reference.primaryLanguage ? `<span class="artifact-chat-pill__language">${reference.primaryLanguage}</span>` : ''}
                                ${reference.versionNumber ? `<span class="artifact-chat-pill__version">v${reference.versionNumber}</span>` : ''}
                            </div>
                        </div>
                    </div>
                    ${codePreview ? `<div class="artifact-chat-pill__preview"><pre><code>${codePreview}</code></pre></div>` : ''}
                </div>
            `;

            pill.addEventListener('click', () => {
                // Mark this pill as active, remove active from others
                this.setActivePill(pill, reference.artifactId);

                if (options.payload) {
                    this.registerDraft(reference, options.payload, options.sessionId);
                    this.openArtifact(options.payload, { sessionId: options.sessionId, reference });
                } else {
                    this.openFromReference(reference, options.sessionId);
                }
            });

            footer.appendChild(pill);
        }

        ensureFooter(messageEl) {
            let footer = messageEl.querySelector('.message-bubble-footer');
            if (!footer) {
                footer = document.createElement('div');
                footer.className = 'message-bubble-footer';
                const content = messageEl.querySelector('.message-content');
                if (content) {
                    content.insertAdjacentElement('afterend', footer);
                } else {
                    messageEl.appendChild(footer);
                }
            }
            return footer;
        }

        getArtifactTitle(reference, payload) {
            const cleanTitle = (title) => {
                if (!title) return null;
                let cleaned = title
                    .replace(/^\+\s*Artifact\s*[-:]\s*/i, '')
                    .replace(/^Artifact\s*[-:]\s*/i, '')
                    .trim();
                // If result is just "Artifact" or empty, reject it
                if (!cleaned || cleaned.toLowerCase() === 'artifact') return null;
                return cleaned;
            };

            // Try reference title first
            if (reference && reference.title) {
                const title = cleanTitle(reference.title);
                if (title) return title;
            }

            // Try payload title
            if (payload) {
                if (payload.title) {
                    const title = cleanTitle(payload.title);
                    if (title) return title;
                }

                // Try entrypoint path
                if (payload.entrypointPath) {
                    return payload.entrypointPath.split('/').pop();
                }

                // Try filename
                if (payload.filename) {
                    return payload.filename;
                }

                // Try first file path
                if (payload.files && payload.files.length > 0) {
                    if (payload.files[0].path) {
                        return payload.files[0].path.split('/').pop();
                    }
                    if (payload.files[0].filename) {
                        return payload.files[0].filename;
                    }
                }

                // Try version-based artifacts
                if (payload.versions && payload.versions.length > 0) {
                    const version = payload.versions[0];

                    // Try version title
                    if (version.title) {
                        const title = cleanTitle(version.title);
                        if (title) return title;
                    }

                    // Try version entrypoint
                    if (version.entrypointPath) {
                        return version.entrypointPath.split('/').pop();
                    }

                    // Try version files
                    if (version.files && version.files.length > 0) {
                        if (version.files[0].path) {
                            return version.files[0].path.split('/').pop();
                        }
                        if (version.files[0].filename) {
                            return version.files[0].filename;
                        }
                    }
                }
            }

            // Generate a name based on language if available
            if (reference && reference.primaryLanguage) {
                const ext = {
                    'javascript': 'js',
                    'typescript': 'ts',
                    'python': 'py',
                    'html': 'html',
                    'css': 'css',
                    'java': 'java',
                    'cpp': 'cpp',
                    'c': 'c'
                }[reference.primaryLanguage.toLowerCase()] || 'txt';
                return `code.${ext}`;
            }

            // Final fallback
            return 'Untitled';
        }

        getCodePreview(payload, language) {
            if (!payload) {
                // No payload - will be fetched from server on click
                return null;
            }

            const formatPreview = (code) => {
                if (!code || typeof code !== 'string') return null;

                // Split into lines and skip leading empty lines only
                const allLines = code.split('\n');
                let startIndex = 0;
                while (startIndex < allLines.length && allLines[startIndex].trim().length === 0) {
                    startIndex++;
                }

                // Take up to 8 lines from the beginning (fits 64px height with 7px font)
                const lines = allLines.slice(startIndex, startIndex + 8);

                if (lines.length === 0) return null;

                // Find minimum indentation from non-empty lines
                const nonEmptyLines = lines.filter(line => line.trim().length > 0);
                if (nonEmptyLines.length === 0) return null;

                const minIndent = Math.min(
                    ...nonEmptyLines.map(line => {
                        const match = line.match(/^(\s*)/);
                        return match ? match[1].length : 0;
                    })
                );

                // Remove common indentation and trim to max width
                const formatted = lines.map(line => {
                    if (line.trim().length === 0) return ''; // Preserve empty lines
                    const trimmed = line.substring(minIndent);
                    // Truncate long lines to fit preview (90px width ≈ 24 chars at 7px font)
                    return trimmed.length > 24 ? trimmed.substring(0, 21) + '...' : trimmed;
                }).join('\n');

                return this.escapeHtml(formatted);
            };

            try {
                // Try activeVersion first (most common case)
                if (payload.activeVersion) {
                    const version = payload.activeVersion;

                    // Try version.code
                    if (version.code) {
                        const preview = formatPreview(version.code);
                        if (preview) return preview;
                    }

                    // Try version.files
                    if (version.files && version.files.length > 0) {
                        const firstFile = version.files[0];
                        if (firstFile.content) {
                            const preview = formatPreview(firstFile.content);
                            if (preview) return preview;
                        }
                        if (firstFile.code) {
                            const preview = formatPreview(firstFile.code);
                            if (preview) return preview;
                        }
                    }
                }

                // For single-file artifacts
                if (payload.code) {
                    const preview = formatPreview(payload.code);
                    if (preview) return preview;
                }

                // For multi-file artifacts
                if (payload.files && payload.files.length > 0) {
                    const firstFile = payload.files[0];
                    if (firstFile.content) {
                        const preview = formatPreview(firstFile.content);
                        if (preview) return preview;
                    }
                    if (firstFile.code) {
                        const preview = formatPreview(firstFile.code);
                        if (preview) return preview;
                    }
                }

                // For version-based artifacts (versions array)
                if (payload.versions && payload.versions.length > 0) {
                    const version = payload.versions[0];
                    if (version.code) {
                        const preview = formatPreview(version.code);
                        if (preview) return preview;
                    }
                    if (version.files && version.files.length > 0) {
                        const firstFile = version.files[0];
                        if (firstFile.content) {
                            const preview = formatPreview(firstFile.content);
                            if (preview) return preview;
                        }
                        if (firstFile.code) {
                            const preview = formatPreview(firstFile.code);
                            if (preview) return preview;
                        }
                    }
                }

                console.warn('No code found in payload:', payload);
            } catch (error) {
                console.error('Error generating code preview:', error, payload);
            }

            return '// No code';
        }

        escapeHtml(text) {
            const div = document.createElement('div');
            div.textContent = text;
            return div.innerHTML;
        }

        setActivePill(activePill, artifactId) {
            // Remove is-active from all pills
            document.querySelectorAll('.artifact-chat-pill').forEach(pill => {
                pill.classList.remove('is-active');
            });

            // Add is-active to the clicked pill
            if (activePill) {
                activePill.classList.add('is-active');
            }

            // Store current active artifact ID
            this.currentActiveArtifactId = artifactId;
        }

        clearActivePills() {
            // Remove is-active from all pills
            document.querySelectorAll('.artifact-chat-pill').forEach(pill => {
                pill.classList.remove('is-active');
            });
            this.currentActiveArtifactId = null;
        }

        registerDraft(reference, payload, sessionId) {
            if (!reference?.artifactId || !sessionId) return;
            this.drafts.set(this.buildCacheKey(sessionId, reference.artifactId), payload);
        }

        buildCacheKey(sessionId, artifactId) {
            return `${sessionId}:${artifactId}`;
        }

        async fetchArtifact(sessionId, artifactId, { force = false } = {}) {
            if (!sessionId || !artifactId) return null;
            const cacheKey = this.buildCacheKey(sessionId, artifactId);
            if (!force) {
                if (this.drafts.has(cacheKey)) return this.drafts.get(cacheKey);
                if (this.cache.has(cacheKey)) return this.cache.get(cacheKey);
            }
            try {
                const response = await fetch(`/chat/${sessionId}/artifacts/${artifactId}`, {
                    headers: { 'X-CSRFToken': this.token }
                });
                if (!response.ok) {
                    throw new Error(`Failed to load artifact (${response.status})`);
                }
                const data = await response.json();
                if (data?.artifact) {
                    this.cache.set(cacheKey, data.artifact);
                    return data.artifact;
                }
            } catch (error) {
                console.error('Artifact fetch failed', error);
                this.setStatus('Unable to load artifact. Please try again.', 'error');
            }
            return null;
        }

        async openFromReference(reference, sessionId) {
            if (!reference?.artifactId) return;
            const artifact = await this.fetchArtifact(sessionId || this.currentSessionId, reference.artifactId);
            if (artifact) {
                this.openArtifact(artifact, { sessionId, reference });
            }
        }

        openArtifact(artifact, { sessionId = null, reference = null, initialTab = 'preview' } = {}) {
            if (!artifact) return;
            this.currentArtifact = artifact;
            this.currentSessionId = sessionId || this.currentSessionId;
            this.currentReference = reference || this.currentReference || {
                artifactId: artifact.artifactId,
                title: artifact.title
            };
            if (this.currentSessionId && artifact.artifactId) {
                this.cache.set(this.buildCacheKey(this.currentSessionId, artifact.artifactId), artifact);
            }
            const activeVersion = artifact.activeVersion || artifact.versions?.[0];
            if (activeVersion) {
                this.versionCache.set(activeVersion.versionId, activeVersion);
                this.currentVersion = activeVersion;
            }
            this.populateVersionSelect(artifact);
            this.updateHeader(artifact);
            if (this.currentVersion) {
                this.renderVersion(this.currentVersion);
            }
            this.activeTab = initialTab;
            this.switchTab(initialTab);
            this.panel.classList.add('is-open');
            this.panel.setAttribute('aria-hidden', 'false');
            document.body.classList.add('artifact-panel-open');

            // Load saved split ratio for desktop
            this.loadSavedSplit();

            // No offset manipulation in split-view
            this.updateOffset(false);

            // Trigger composer width adjustment
            this.adjustComposerWidth(true);

            // Fetch full artifact from server to get all versions for the dropdown
            if (this.currentSessionId && artifact.artifactId) {
                this.fetchArtifact(this.currentSessionId, artifact.artifactId, { force: true })
                    .then(fullArtifact => {
                        if (fullArtifact && fullArtifact.versions && fullArtifact.versions.length > 1) {
                            this.populateVersionSelect(fullArtifact);
                            // Cache all versions
                            for (const v of fullArtifact.versions || []) {
                                if (v.versionId && v.files) {
                                    this.versionCache.set(v.versionId, v);
                                }
                            }
                        }
                    })
                    .catch(() => {}); // silent — dropdown just won't have old versions
            }

            // Mark corresponding pill as active if it exists
            if (reference && reference.artifactId) {
                const pill = document.querySelector(`.artifact-chat-pill[data-artifact-id="${reference.artifactId}"]`);
                if (pill) {
                    this.setActivePill(pill, reference.artifactId);
                }
            }
        }

        close() {
            if (!this.panel) return;

            // Clean up streaming state if active
            if (this.isStreaming) {
                this.isStreaming = false;
                this._streamBuffer = '';
                this._contentFound = false;
                this.panel.classList.remove('is-streaming');
            }

            this.panel.classList.remove('is-open');
            this.panel.setAttribute('aria-hidden', 'true');
            document.body.classList.remove('artifact-panel-open', 'chat-area-wide');
            this.updateOffset(false);
            this.adjustComposerWidth(false);
            this.clearActivePills();
            this.activeTab = 'preview';
        }

        openStreaming() {
            this.isStreaming = true;
            this.streamingStartTime = Date.now();
            this._streamBuffer = '';
            this._contentFound = false;
            this._contentStart = 0;
            this._detectedLang = null;
            this._detectedPath = null;
            this._streamFiles = [];
            this._lastParsePos = 0;

            if (this.titleEl) {
                this.titleEl.textContent = 'Generating...';
            }
            if (this.languageBadge) {
                this.languageBadge.textContent = '';
            }
            if (this.versionSelect) {
                this.versionSelect.innerHTML = '';
                this.versionSelect.disabled = true;
            }
            if (this.currentFileName) {
                this.currentFileName.textContent = 'Streaming...';
            }
            if (this.fileSelect) {
                this.fileSelect.innerHTML = '<option>Streaming...</option>';
            }

            // Init CodeMirror for streaming (read-only during stream)
            this._ensureCodeMirror();
            if (this.editCodeMirror) {
                this.editCodeMirror.setValue('');
                this.editCodeMirror.setOption('readOnly', true);
                this.editCodeMirror.setOption('mode', 'jsx');
            }

            // Show CodeMirror, hide old code display
            if (this.codeDisplay) this.codeDisplay.style.display = 'none';
            if (this.editCodeMirrorContainer) this.editCodeMirrorContainer.style.display = 'block';

            this.panel.classList.add('is-open', 'is-streaming');
            this.panel.setAttribute('aria-hidden', 'false');
            document.body.classList.add('artifact-panel-open');
            this.activeTab = 'code';
            this.switchTab('code');
            this.loadSavedSplit();
            this.updateOffset(false);
            this.adjustComposerWidth(true);

            // Refresh after layout settles
            setTimeout(() => this.editCodeMirror?.refresh(), 50);
        }

        _ensureCodeMirror() {
            if (!this.editCodeMirror && typeof CodeMirror !== 'undefined') {
                this.editCodeMirrorContainer.innerHTML = '';
                this.editCodeMirror = CodeMirror(this.editCodeMirrorContainer, {
                    lineNumbers: true,
                    theme: 'material-darker',
                    mode: 'javascript',
                    matchBrackets: true,
                    autoCloseBrackets: true,
                    styleActiveLine: true,
                    indentUnit: 4,
                    tabSize: 4,
                    indentWithTabs: false,
                    lineWrapping: false
                });
                this.editCodeMirror.on('change', () => this.handleEditInput());
            }
        }

        appendStreamingCode(text) {
            if (!this.isStreaming) return;

            this._streamBuffer += text;

            // Parse all files from the buffer and display in CodeMirror
            this._parseAndDisplayStreamingFiles();
        }

        _parseAndDisplayStreamingFiles() {
            // Extract all complete file entries from the streaming JSON buffer.
            // Each file looks like: {"path":"App.jsx","language":"jsx","content":"...code..."}
            // We find each "content":" and scan for the closing unescaped "

            if (!this._streamFiles) this._streamFiles = [];

            const buf = this._streamBuffer;
            let searchFrom = this._lastParsePos || 0;

            // Find all "path":"..." + "content":"..." pairs
            while (searchFrom < buf.length) {
                // Find next "path" key
                const pathIdx = buf.indexOf('"path"', searchFrom);
                if (pathIdx === -1) break;

                // Extract path value
                const pathValMatch = buf.slice(pathIdx).match(/"path"\s*:\s*"([^"]+)"/);
                if (!pathValMatch) break;

                // Find "language" near this path
                const langSearch = buf.slice(pathIdx, pathIdx + 200);
                const langMatch = langSearch.match(/"language"\s*:\s*"(\w+)"/);
                const lang = langMatch ? langMatch[1] : 'text';

                // Find "content":" after this path
                const contentIdx = buf.indexOf('"content"', pathIdx);
                if (contentIdx === -1) break;

                const contentValMatch = buf.slice(contentIdx).match(/"content"\s*:\s*"/);
                if (!contentValMatch) break;

                const contentStart = contentIdx + contentValMatch[0].length;

                // Scan for the closing unescaped " of the content value
                let endIdx = -1;
                for (let i = contentStart; i < buf.length; i++) {
                    if (buf[i] === '\\') { i++; continue; }
                    if (buf[i] === '"') { endIdx = i; break; }
                }

                const filePath = pathValMatch[1];
                const isComplete = endIdx !== -1;
                const rawContent = isComplete
                    ? buf.slice(contentStart, endIdx)
                    : buf.slice(contentStart);

                // Update or add this file in our tracking array
                const existing = this._streamFiles.find(f => f.path === filePath);
                if (existing) {
                    existing.content = rawContent;
                    existing.complete = isComplete;
                    existing.lang = lang;
                } else {
                    this._streamFiles.push({ path: filePath, content: rawContent, complete: isComplete, lang });
                }

                if (isComplete) {
                    // Move past this file to look for the next one
                    searchFrom = endIdx + 1;
                    this._lastParsePos = searchFrom;
                } else {
                    // Content still streaming — stay here
                    break;
                }
            }

            // Display the LAST file that has content (the one currently streaming)
            if (this._streamFiles.length === 0) return;

            // Find the file currently being written (last incomplete, or last complete if all done)
            let displayFile = this._streamFiles[this._streamFiles.length - 1];
            for (let i = this._streamFiles.length - 1; i >= 0; i--) {
                if (!this._streamFiles[i].complete) {
                    displayFile = this._streamFiles[i];
                    break;
                }
            }

            // Update file dropdown
            if (this.fileSelect) {
                const options = this._streamFiles.map((f, i) =>
                    `<option value="${i}" ${f.path === displayFile.path ? 'selected' : ''}>${f.path}${f.complete ? ' ✓' : ' ...'}</option>`
                ).join('');
                this.fileSelect.innerHTML = options;
            }
            if (this.currentFileName) {
                this.currentFileName.textContent = displayFile.path;
            }

            // Display in CodeMirror if available, fallback to codeBlock
            const content = this._unescapeJsonString(displayFile.content);
            if (this.editCodeMirror && this.isStreaming) {
                const mode = this.getCodeMirrorMode({ language: displayFile.lang, path: displayFile.path });
                this.editCodeMirror.setOption('mode', mode);
                // Only update if content changed (avoid cursor jump)
                if (this.editCodeMirror.getValue() !== content) {
                    this.editCodeMirror.setValue(content);
                    // Auto-scroll to bottom during streaming
                    this.editCodeMirror.scrollIntoView({ line: this.editCodeMirror.lineCount() - 1, ch: 0 });
                }
            } else if (this.codeBlock) {
                const prismLang = displayFile.lang === 'jsx' ? 'javascript' : displayFile.lang;
                this.codeBlock.className = `language-${prismLang}`;
                this.codeBlock.textContent = content;
            }
        }

        _unescapeJsonString(str) {
            // Process all JSON string escapes in one pass
            return str.replace(/\\(n|t|r|"|\\|\/)/g, (match, ch) => {
                switch (ch) {
                    case 'n': return '\n';
                    case 't': return '\t';
                    case 'r': return '\r';
                    case '"': return '"';
                    case '\\': return '\\';
                    case '/': return '/';
                    default: return match;
                }
            });
        }

        finalizeStreaming(artifactData, { sessionId = null, reference = null } = {}) {
            const doFinalize = () => {
                this.isStreaming = false;
                this.panel.classList.remove('is-streaming');
                this._streamBuffer = '';
                this._contentFound = false;

                // Make CodeMirror editable now that streaming is done
                if (this.editCodeMirror) {
                    this.editCodeMirror.setOption('readOnly', false);
                }

                if (artifactData) {
                    // openArtifact will render preview + populate code tab properly
                    this.openArtifact(artifactData, { sessionId, reference });
                } else {
                    // Parsing failed — the streamed code stays visible in the code tab
                    if (this.titleEl) {
                        this.titleEl.textContent = 'Artifact (raw)';
                    }
                }
            };

            // Ensure user sees code streaming for at least 1.5s before switching to preview
            const elapsed = Date.now() - (this.streamingStartTime || 0);
            const minDisplayTime = 1500;
            if (elapsed < minDisplayTime) {
                setTimeout(doFinalize, minDisplayTime - elapsed);
            } else {
                doFinalize();
            }
        }

        applyPatchesVisually(patches, patchResults, finalArtifact, { sessionId = null, reference = null } = {}) {
            // Open patched artifact on code tab, highlight changed regions,
            // scroll to first change, then transition to preview.
            // "Make change visible, not theatrical."
            if (!finalArtifact) return;

            const applied = patches.filter((p, i) => !patchResults?.[i] || patchResults[i].status === 'applied');
            const failed = patches.length - applied.length;

            // Open directly on code tab — no flash from preview→code switch
            this.openArtifact(finalArtifact, { sessionId, reference, initialTab: 'code' });

            // Update title with patch summary
            const savedTitle = finalArtifact.title;
            if (this.titleEl) {
                const summary = failed > 0
                    ? `Updated (${applied.length} changes, ${failed} failed)`
                    : `Updated (${applied.length} change${applied.length !== 1 ? 's' : ''})`;
                this.titleEl.textContent = summary;
            }

            // Highlight changed regions — switch to the patched file first
            if (this.codeBlock && applied.length > 0 && this.currentVersion?.files) {
                // Find which file was patched (use first applied patch's target)
                const patchedFileName = applied[0].file;
                const patchedFile = this.currentVersion.files.find(f => f.path === patchedFileName);
                if (patchedFile && this.currentFile?.path !== patchedFileName) {
                    this.renderCodeViewer(this.currentVersion, patchedFile);
                    // Update file dropdown to match
                    if (this.fileSelect) {
                        const idx = this.currentVersion.files.indexOf(patchedFile);
                        if (idx >= 0) this.fileSelect.value = String(idx);
                    }
                }

                // Wait two frames for Prism to finish highlighting
                requestAnimationFrame(() => { requestAnimationFrame(() => {
                    for (const patch of applied) {
                        if (!patch.new_string) continue;
                        // If patch targets a different file than currently shown, skip
                        if (patch.file !== this.currentFile?.path) continue;
                        this._highlightTextInElement(this.codeBlock, patch.new_string);
                    }
                    const firstMark = this.codeBlock.querySelector('.patch-highlight');
                    if (firstMark) this._scrollToElement(firstMark);
                }); });
            }

            // After 3s, fade highlights and switch to preview
            setTimeout(() => {
                if (this.codeBlock) {
                    this.codeBlock.querySelectorAll('.patch-highlight').forEach(el => {
                        el.classList.add('patch-highlight-fade');
                    });
                }
                // Remove marks and switch tab after fade completes
                setTimeout(() => {
                    if (this.codeBlock) {
                        this.codeBlock.querySelectorAll('.patch-highlight').forEach(el => {
                            const parent = el.parentNode;
                            while (el.firstChild) parent.insertBefore(el.firstChild, el);
                            parent.removeChild(el);
                        });
                    }
                    this.activeTab = 'preview';
                    this.switchTab('preview');
                    if (this.titleEl && savedTitle) {
                        this.titleEl.textContent = savedTitle;
                    }
                }, 400);
            }, 3000);
        }

        openPatchStreaming() {
            if (!this.currentVersion || !this.codeBlock) return;

            this.isPatchStreaming = true;
            this._patchQueue = Promise.resolve();

            // Exit edit mode so patch animations show on the code display
            if (this.isEditMode) {
                this.exitEditMode();
            }

            // Switch to code tab showing current (pre-patch) code
            this.activeTab = 'code';
            this.switchTab('code');

            if (this.titleEl) {
                this.titleEl.textContent = 'Updating...';
            }

            // Ensure panel is open
            if (!this.panel.classList.contains('is-open')) {
                this.panel.classList.add('is-open');
                this.panel.setAttribute('aria-hidden', 'false');
                document.body.classList.add('artifact-panel-open');
                this.loadSavedSplit();
                this.updateOffset(false);
                this.adjustComposerWidth(true);
            }
        }

        applyLivePatch(file, oldStr, newStr) {
            if (!this.isPatchStreaming) return;
            this._patchQueue = this._patchQueue.then(() => this._animatePatch(file, oldStr, newStr));
        }

        async _animatePatch(file, oldStr, newStr) {
            if (!this.codeBlock) return;

            // Switch to target file if needed
            const needsSwitch = this.currentVersion?.files && this.currentFile?.path !== file;
            if (needsSwitch) {
                const targetFile = this.currentVersion.files.find(f => f.path === file);
                if (targetFile) {
                    // Clear previous file's marks before switching
                    this.codeBlock.querySelectorAll('.patch-removing, .patch-added').forEach(el => {
                        const parent = el.parentNode;
                        while (el.firstChild) parent.insertBefore(el.firstChild, el);
                        parent.removeChild(el);
                    });
                    this.renderCodeViewer(this.currentVersion, targetFile);
                    if (this.fileSelect) {
                        const idx = this.currentVersion.files.indexOf(targetFile);
                        if (idx >= 0) this.fileSelect.value = String(idx);
                    }
                    // Extra frame wait for Prism highlight after file switch
                    await new Promise(r => requestAnimationFrame(r));
                }
            }

            // Wait a frame for render to settle
            await new Promise(r => requestAnimationFrame(r));

            const codeText = this.codeBlock.textContent || '';

            // Normalize for matching (JSON may have literal \n)
            let searchOld = oldStr;
            if (!codeText.includes(searchOld)) {
                searchOld = searchOld.replace(/\\n/g, '\n').replace(/\\t/g, '\t');
            }
            if (!codeText.includes(searchOld)) return; // can't find it, skip

            // Highlight old code in red
            this._highlightTextInElement(this.codeBlock, searchOld, 'patch-removing');
            const removeMarks = this.codeBlock.querySelectorAll('.patch-removing');
            if (removeMarks.length > 0) this._scrollToElement(removeMarks[0]);

            // Pause so user sees the red highlight
            await new Promise(r => setTimeout(r, 400));

            // Replace all old marks with a single green mark containing new code
            if (removeMarks.length > 0) {
                const addMark = document.createElement('mark');
                addMark.className = 'patch-added';
                addMark.textContent = newStr;
                removeMarks[0].parentNode.insertBefore(addMark, removeMarks[0]);
                // Remove all old red marks
                removeMarks.forEach(m => m.parentNode.removeChild(m));
                // Scroll to follow the green replacement
                this._scrollToElement(addMark);
            }

            // Update in-memory file content so subsequent patches match correctly
            if (this.currentFile) {
                this.currentFile.content = (this.currentFile.content || '').replace(oldStr, newStr);
            }

            // Brief pause to show green before next patch
            await new Promise(r => setTimeout(r, 300));
        }

        finalizePatchStreaming(artifact, { sessionId = null, reference = null } = {}) {
            if (!this.isPatchStreaming) return;

            // Wait for animation queue to drain, then switch to final artifact
            this._patchQueue.then(() => {
                this.isPatchStreaming = false;

                // Brief pause to show last green highlight
                setTimeout(() => {
                    // Remove all patch marks
                    if (this.codeBlock) {
                        this.codeBlock.querySelectorAll('.patch-removing, .patch-added').forEach(el => {
                            const parent = el.parentNode;
                            while (el.firstChild) parent.insertBefore(el.firstChild, el);
                            parent.removeChild(el);
                        });
                    }

                    // Open the server-patched artifact (authoritative) on Preview
                    if (artifact) {
                        this.openArtifact(artifact, { sessionId, reference });
                    }
                }, 500);
            });
        }

        /**
         * Highlight a substring within an element by wrapping matching text nodes
         * in <mark> tags — preserves existing DOM structure (Prism spans, etc.)
         */
        _highlightTextInElement(root, searchStr, className = 'patch-highlight') {
            if (!searchStr || !root) return;
            const normalized = searchStr.replace(/\r\n/g, '\n');

            // Collect all text nodes in order
            const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
            const textNodes = [];
            let node;
            while ((node = walker.nextNode())) textNodes.push(node);

            // Build concatenated text with node index mapping
            let fullText = '';
            const map = []; // map[charIndex] = { node, offset }
            for (const tn of textNodes) {
                const text = tn.nodeValue;
                for (let i = 0; i < text.length; i++) {
                    map.push({ node: tn, offset: i });
                }
                fullText += text;
            }

            // Find the substring in the concatenated text
            const idx = fullText.indexOf(normalized);
            if (idx === -1) return;

            // Determine which text nodes are spanned by the match
            const startEntry = map[idx];
            const endEntry = map[idx + normalized.length - 1];
            if (!startEntry || !endEntry) return;

            // Collect unique nodes involved in the match
            const startNode = startEntry.node;
            const startOffset = startEntry.offset;
            const endNode = endEntry.node;
            const endOffset = endEntry.offset;

            // Use Range to wrap the match
            try {
                const range = document.createRange();
                range.setStart(startNode, startOffset);
                range.setEnd(endNode, endOffset + 1);

                const mark = document.createElement('mark');
                mark.className = className;
                range.surroundContents(mark);
            } catch (e) {
                // surroundContents fails if range crosses element boundaries.
                // Fallback: wrap each line's text nodes individually.
                const lines = normalized.split('\n');
                let searchPos = idx;
                for (const line of lines) {
                    if (!line) { searchPos += 1; continue; } // skip newline char
                    const lineStart = map[searchPos];
                    const lineEnd = map[searchPos + line.length - 1];
                    if (lineStart && lineEnd) {
                        try {
                            const r = document.createRange();
                            r.setStart(lineStart.node, lineStart.offset);
                            r.setEnd(lineEnd.node, lineEnd.offset + 1);
                            const m = document.createElement('mark');
                            m.className = className;
                            r.surroundContents(m);
                        } catch (_) { /* skip this line */ }
                    }
                    searchPos += line.length + 1; // +1 for newline
                }
            }
        }

        _scrollToElement(el) {
            // Scroll within the code section only — don't use scrollIntoView
            // which scrolls ALL ancestor containers including the panel
            const codeSection = document.getElementById('artifact-code-panel');
            if (!codeSection || !el) return;
            const sectionRect = codeSection.getBoundingClientRect();
            const elRect = el.getBoundingClientRect();
            const offset = elRect.top - sectionRect.top - (sectionRect.height / 2) + (elRect.height / 2);
            codeSection.scrollBy({ top: offset, behavior: 'smooth' });
        }

        _escapeHtml(str) {
            const div = document.createElement('div');
            div.textContent = str;
            return div.innerHTML;
        }

        adjustComposerWidth(isOpen) {
            const composer = document.querySelector('.chat-shell__composer');
            if (!composer) return;

            // CSS handles width via media queries, just ensure transition is active
            composer.style.transition = 'width 0.3s cubic-bezier(0.4, 0, 0.2, 1), left 0.3s cubic-bezier(0.4, 0, 0.2, 1)';
        }

        handleResponsiveLayout() {
            const isOpen = this.panel?.classList.contains('is-open');
            if (!isOpen) return;

            // Load split ratio for desktop, or reset for mobile
            this.loadSavedSplit();

            // Adjust composer width when window is resized
            this.adjustComposerWidth(true);
        }

        applySplitPercentage(chatPercent, artifactPercent) {
            // Update CSS variables for grid columns
            const chatFr = chatPercent / 100;
            const artifactFr = artifactPercent / 100;

            document.documentElement.style.setProperty('--artifact-split-left', `${chatFr}fr`);
            document.documentElement.style.setProperty('--artifact-split-right', `${artifactFr}fr`);
            document.documentElement.style.setProperty('--artifact-chat-width-percent', chatFr);

            // Store for later retrieval
            this.currentSplitPercent = chatPercent;
        }

        getSplitPercentage() {
            // Try to get from current CSS variable
            if (this.currentSplitPercent) {
                return this.currentSplitPercent;
            }

            // Try to get from localStorage
            try {
                const stored = localStorage.getItem('artifactPanel:splitPercent');
                if (stored) {
                    return parseFloat(stored);
                }
            } catch (error) {
                console.warn('Unable to read split percentage', error);
            }

            // Default to 50%
            return 50;
        }

        loadSavedSplit() {
            const isDesktop = window.DeviceDetector ? !DeviceDetector.isMobile() : (window.innerWidth > 768);
            if (!isDesktop) return;

            const chatPercent = this.getSplitPercentage();
            const artifactPercent = 100 - chatPercent;
            this.applySplitPercentage(chatPercent, artifactPercent);
        }

        populateVersionSelect(artifact) {
            if (!this.versionSelect) return;
            this.versionSelect.innerHTML = '';

            const versions = artifact.versions || [];
            const versionCount = artifact.versionCount || versions.length || 1;

            // If we only have the active version but know there are more,
            // build placeholder entries so the dropdown isn't disabled
            if (versions.length <= 1 && versionCount > 1) {
                for (let i = 1; i <= versionCount; i++) {
                    const option = document.createElement('option');
                    if (i === versionCount && versions.length === 1) {
                        option.value = versions[0].versionId;
                        option.textContent = `v${i}`;
                        option.selected = true;
                    } else {
                        option.value = `placeholder-v${i}`;
                        option.textContent = `v${i}`;
                    }
                    this.versionSelect.appendChild(option);
                }
            } else {
                versions.forEach(version => {
                    const option = document.createElement('option');
                    option.value = version.versionId;
                    option.textContent = `v${version.versionNumber}`;
                    if (artifact.activeVersionId === version.versionId) {
                        option.selected = true;
                    }
                    this.versionSelect.appendChild(option);
                });
            }

            this.versionSelect.disabled = versionCount <= 1;

            // Sync header version badge
            if (this.versionBadge) {
                this.versionBadge.innerHTML = this.versionSelect.innerHTML;
                this.versionBadge.value = this.versionSelect.value;
                this.versionBadge.disabled = this.versionSelect.disabled;
            }
        }

        updateHeader(artifact) {
            if (this.titleEl) {
                this.titleEl.textContent = artifact.title || 'Artifact';
            }
            const language = this.currentVersion?.primaryLanguage || artifact.primaryLanguage || 'code';
            if (this.languageBadge) {
                this.languageBadge.textContent = language ? `Artifact · ${capitalize(language)}` : 'Artifact';
            }
        }

        async handleVersionChange() {
            const versionId = this.versionSelect?.value;
            if (!versionId || versionId === this.currentVersion?.versionId) {
                return;
            }
            const cached = this.versionCache.get(versionId);
            if (cached) {
                this.currentVersion = cached;
                this.renderVersion(cached);
                return;
            }
            if (!this.currentArtifact?.artifactId) {
                return;
            }
            this.setStatus('Loading version…', 'pending');
            try {
                const version = await this.fetchVersionFromServer(versionId);
                if (version) {
                    this.versionCache.set(versionId, version);
                    this.currentVersion = version;
                    this.renderVersion(version);
                    this.setStatus('', 'idle');
                } else {
                    this.setStatus('Version unavailable.', 'error');
                }
            } catch (error) {
                console.error('Version fetch failed', error);
                this.setStatus('Unable to load version.', 'error');
            }
        }

        async fetchVersionFromServer(versionId) {
            if (!this.currentArtifact?.artifactId || !this.currentSessionId || !versionId) {
                return null;
            }
            const response = await fetch(`/chat/${this.currentSessionId}/artifacts/${this.currentArtifact.artifactId}/versions/${versionId}`, {
                headers: { 'X-CSRFToken': this.token }
            });
            if (!response.ok) {
                throw new Error(`Failed to fetch version (${response.status})`);
            }
            const data = await response.json();
            return data?.version || null;
        }

        async reloadCurrentVersion() {
            if (!this.currentVersion?.versionId) {
                this.setStatus('Open an artifact first.', 'error');
                return;
            }
            try {
                this.setStatus('Refreshing preview…', 'pending');
                const version = await this.fetchVersionFromServer(this.currentVersion.versionId);
                if (version) {
                    this.versionCache.set(version.versionId, version);
                    this.currentVersion = version;
                    this.renderVersion(version);
                    this.setStatus('Preview updated.', 'success');
                } else {
                    this.setStatus('Unable to refresh version.', 'error');
                }
            } catch (error) {
                console.error('Refresh failed', error);
                this.setStatus('Unable to refresh version.', 'error');
            }
        }

        async publishCurrentVersion() {
            if (!this.currentArtifact?.artifactId || !this.currentVersion?.versionId || !this.currentSessionId) {
                this.setStatus('Nothing to publish yet.', 'error');
                return;
            }
            try {
                this.setStatus('Publishing version…', 'pending');
                const response = await fetch(`/chat/${this.currentSessionId}/artifacts/active`, {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-CSRFToken': this.token
                    },
                    body: JSON.stringify({
                        artifact_id: this.currentArtifact.artifactId,
                        version_id: this.currentVersion.versionId
                    })
                });
                if (!response.ok) {
                    throw new Error(`Publish failed (${response.status})`);
                }
                const data = await response.json();
                if (data?.artifact) {
                    this.currentArtifact = data.artifact;
                    if (this.currentSessionId && data.artifact.artifactId) {
                        this.cache.set(this.buildCacheKey(this.currentSessionId, data.artifact.artifactId), data.artifact);
                    }
                    const version = data.artifact.activeVersion;
                    if (version) {
                        this.versionCache.set(version.versionId, version);
                        this.currentVersion = version;
                        this.renderVersion(version);
                    }
                    this.populateVersionSelect(this.currentArtifact);
                    this.setStatus('Version published.', 'success');
                } else {
                    throw new Error('Malformed publish response');
                }
            } catch (error) {
                console.error('Publish failed', error);
                this.setStatus('Unable to publish artifact.', 'error');
            }
        }

        renderVersion(version) {
            if (!version) return;
            this.currentVersion = version;
            version.files = version.files || [];
            this.renderPreview(version);
            this.renderFileTree(version);
            this.renderCodeViewer(version, version.files[0]);
            this.renderInfo(version);
            if (!version.files.length) {
                this.setStatus('This artifact has no files yet.', 'error');
            } else if (this.statusEl?.textContent) {
                this.setStatus('', 'idle');
            }
            if (!this.activeTab) {
                this.activeTab = 'preview';
            }
            this.switchTab(this.activeTab);
        }

        renderPreview(version) {
            if (!this.previewFrame) return;
            if (!version.files?.length) {
                this.previewFrame.srcdoc = '<p style="padding:1rem;font-family:system-ui;color:#b5b5b5;">Nothing to preview yet.</p>';
                return;
            }
            const renderer = RENDERERS.find(entry => entry.matches(version)) || RENDERERS[RENDERERS.length - 1];
            console.log('Artifact Preview - Using renderer:', renderer.name, 'Files:', version.files?.map(f => f.path));
            renderer.render(this.previewFrame, version);
        }

        renderFileTree(version) {
            // Populate the file dropdown instead of tree
            if (this.fileSelect) {
                this.fileSelect.innerHTML = '';
                if (!version.files?.length) {
                    const defaultOpt = document.createElement('option');
                    defaultOpt.value = '';
                    defaultOpt.textContent = 'No files available';
                    this.fileSelect.appendChild(defaultOpt);
                    return;
                }
                version.files.forEach((file, index) => {
                    const opt = document.createElement('option');
                    opt.value = index;
                    opt.textContent = file.path || `File ${index + 1}`;
                    this.fileSelect.appendChild(opt);
                });
                // Select first file by default
                if (version.files.length > 0) {
                    this.fileSelect.value = '0';
                }
            }

            // Keep legacy tree support (hidden by CSS)
            if (this.fileTreeEl) {
                this.fileTreeEl.innerHTML = '';
                if (!version.files?.length) {
                    const empty = document.createElement('p');
                    empty.className = 'artifact-file-tree__empty';
                    empty.textContent = 'Files will appear here once generated.';
                    this.fileTreeEl.appendChild(empty);
                    return;
                }
                const tree = buildFileTree(version.files || []);
                const fragment = document.createDocumentFragment();
                tree.forEach(node => fragment.appendChild(this.renderTreeNode(node)));
                this.fileTreeEl.appendChild(fragment);
                const firstFileNode = this.fileTreeEl.querySelector('.artifact-file-node.file');
                if (firstFileNode) {
                    firstFileNode.classList.add('is-active');
                }
            }
        }

        handleFileSelectChange() {
            if (!this.fileSelect || !this.currentVersion?.files) return;
            const selectedIndex = parseInt(this.fileSelect.value, 10);
            if (isNaN(selectedIndex) || selectedIndex < 0) return;
            const file = this.currentVersion.files[selectedIndex];
            if (file) {
                if (this.isEditMode && this.editCodeMirror) {
                    this.loadFileForEditing(file);
                } else {
                    this.renderCodeViewer(this.currentVersion, file);
                }
            }
        }

        renderTreeNode(node) {
            const el = document.createElement('div');
            el.className = `artifact-file-node ${node.type}`;
            el.innerHTML = `<span><i class="${node.type === 'folder' ? 'fas fa-folder' : 'fas fa-file-code'}"></i>${escapeHtml(node.name)}</span>`;
            if (node.type === 'folder') {
                const childrenContainer = document.createElement('div');
                childrenContainer.style.paddingLeft = '12px';
                node.children.forEach(child => {
                    childrenContainer.appendChild(this.renderTreeNode(child));
                });
                el.appendChild(childrenContainer);
            } else if (node.type === 'file') {
                el.addEventListener('click', () => {
                    this.renderCodeViewer(this.currentVersion, node.file);
                    this.fileTreeEl?.querySelectorAll('.is-active').forEach(active => active.classList.remove('is-active'));
                    el.classList.add('is-active');
                });
            }
            return el;
        }

        renderCodeViewer(version, file) {
            if (!this.codeBlock || !this.currentFileName) return;
            if (!version.files?.length) {
                this.codeBlock.textContent = '';
                this.currentFileName.textContent = 'No file selected';
                this.currentFile = null;
                return;
            }
            const targetFile = file || version.files?.[0];
            if (!targetFile) {
                this.codeBlock.textContent = '';
                this.currentFileName.textContent = 'No file selected';
                return;
            }
            this.currentFile = targetFile;
            this.currentFileName.textContent = targetFile.path || 'File';

            // Normalize content: convert literal escape sequences to actual characters
            // This fixes issues where AI generates escaped newlines (\n) instead of actual newlines
            let content = targetFile.content || '';
            content = this.normalizeCodeContent(content);

            this.codeBlock.textContent = content;

            // Apply syntax highlighting with error handling
            if (typeof Prism !== 'undefined') {
                try {
                    // Set language class based on file extension or language
                    const lang = targetFile.language || this.guessLanguageFromPath(targetFile.path);
                    if (lang) {
                        this.codeBlock.className = `language-${lang}`;
                    }
                    Prism.highlightElement(this.codeBlock);
                } catch (err) {
                    console.warn('Prism highlighting failed:', err);
                    // Code will display without highlighting
                }
            }
        }

        // Normalize code content by converting literal escape sequences to actual characters
        normalizeCodeContent(content) {
            if (!content || typeof content !== 'string') return content || '';

            // If content looks like it has literal \n instead of newlines,
            // convert them (but be careful not to break already-correct content)
            // Check if content has very few actual newlines but many literal \n
            const actualNewlines = (content.match(/\n/g) || []).length;
            const literalBackslashN = (content.match(/\\n/g) || []).length;

            // If there are many more literal \n than actual newlines, convert them
            if (literalBackslashN > 5 && literalBackslashN > actualNewlines * 2) {
                content = content
                    .replace(/\\n/g, '\n')
                    .replace(/\\t/g, '\t')
                    .replace(/\\r/g, '\r');
            }

            return content;
        }

        // Guess language from file path extension
        guessLanguageFromPath(path) {
            if (!path) return null;
            const ext = path.split('.').pop()?.toLowerCase();
            const langMap = {
                'js': 'javascript',
                'jsx': 'jsx',
                'ts': 'typescript',
                'tsx': 'tsx',
                'py': 'python',
                'rb': 'ruby',
                'java': 'java',
                'go': 'go',
                'rs': 'rust',
                'c': 'c',
                'cpp': 'cpp',
                'h': 'c',
                'hpp': 'cpp',
                'css': 'css',
                'scss': 'scss',
                'html': 'html',
                'xml': 'xml',
                'json': 'json',
                'yaml': 'yaml',
                'yml': 'yaml',
                'md': 'markdown',
                'sql': 'sql',
                'sh': 'bash',
                'bash': 'bash',
                'zsh': 'bash',
                'php': 'php',
                'swift': 'swift',
                'kt': 'kotlin'
            };
            return langMap[ext] || ext || null;
        }

        renderInfo(version) {
            if (!this.infoList) return;
            this.infoList.innerHTML = '';
            const items = [
                ['Version', `v${version.versionNumber || 1}`],
                ['Entrypoint', version.entrypointPath || 'Not set'],
                ['Files', version.fileCount || (version.files?.length || 0)],
                ['Primary language', capitalize(version.primaryLanguage || 'code')]
            ];
            items.forEach(([label, value]) => {
                const dt = document.createElement('dt');
                dt.textContent = label;
                const dd = document.createElement('dd');
                dd.textContent = value;
                this.infoList.appendChild(dt);
                this.infoList.appendChild(dd);
            });
        }

        async downloadZip() {
            if (!this.currentArtifact?.artifactId || !this.currentVersion?.versionId || !this.currentSessionId) {
                this.setStatus('Save the artifact first before downloading.', 'error');
                return;
            }
            const url = `/chat/${this.currentSessionId}/artifacts/${this.currentArtifact.artifactId}/versions/${this.currentVersion.versionId}/zip`;
            window.open(url, '_blank');
        }

        copyCurrentFile() {
            if (!this.currentFile) {
                this.setStatus('Select a file to copy.', 'error');
                return;
            }
            navigator.clipboard.writeText(this.currentFile.content || '')
                .then(() => this.setStatus('File copied to clipboard.', 'success'))
                .catch(() => this.setStatus('Unable to copy file.', 'error'));
        }

        copyAllFiles() {
            if (!this.currentVersion?.files?.length) {
                this.setStatus('No files available to copy.', 'error');
                return;
            }
            const text = this.currentVersion.files.map(file => `// ${file.path}\n${file.content || ''}`).join('\n\n');
            navigator.clipboard.writeText(text)
                .then(() => this.setStatus('All files copied.', 'success'))
                .catch(() => this.setStatus('Unable to copy files.', 'error'));
        }

        switchTab(target) {
            if (!target) return;
            this.activeTab = target;
            this.tabButtons.forEach(button => {
                const isActive = button.dataset.artifactTab === target;
                button.classList.toggle('is-active', isActive);
                button.setAttribute('aria-selected', isActive ? 'true' : 'false');
            });
            this.tabPanels.forEach(panel => {
                panel.classList.toggle('is-active', panel.dataset.artifactPanel === target);
            });

            // Initialize tab content when switching
            clearTimeout(this._autoEditTimer);
            if (target === 'code' && this.currentVersion) {
                // Auto-enter edit mode when switching to code tab (not during streaming)
                if (!this.isEditMode && !this.isPatchStreaming && !this.isStreaming) {
                    this._autoEditTimer = setTimeout(() => {
                        if (!this.isStreaming && !this.isPatchStreaming) {
                            this.enterEditMode();
                        }
                    }, 50);
                }
            } else if (target === 'diff' && this.currentArtifact) {
                this.initializeDiffTab(this.currentArtifact);
            } else if (target === 'composer') {
                this.initializeComposer();
            }
        }

        setStatus(message, variant = 'idle') {
            if (!this.statusEl) return;
            this.statusEl.textContent = message || '';
            this.statusEl.classList.remove('artifact-panel__status--pending', 'artifact-panel__status--error', 'artifact-panel__status--success');
            if (variant === 'pending') {
                this.statusEl.classList.add('artifact-panel__status--pending');
            } else if (variant === 'error') {
                this.statusEl.classList.add('artifact-panel__status--error');
            } else if (variant === 'success') {
                this.statusEl.classList.add('artifact-panel__status--success');
            }
        }

        supportsArtifact(reference, payload = null) {
            // Always support if we have an artifactId (can fetch from server)
            if (reference?.artifactId) {
                return true;
            }
            const language = (reference?.primaryLanguage || payload?.primaryLanguage || '').toLowerCase();
            if (!language || SAFE_TEXT_LANGUAGES.has(language)) {
                return !!payload?.files?.length;
            }
            return true;
        }

        isSmallSnippet(payload = null) {
            if (!payload?.files?.length) return false;
            const file = payload.files[0];
            const content = file.content || '';
            const byteSize = content.length;
            const lines = content.split(/\r?\n/).filter(line => line.trim()).length;
            return payload.files.length === 1 && byteSize <= SMALL_SNIPPET_BYTE_LIMIT && lines <= SMALL_SNIPPET_LINE_LIMIT;
        }

        renderInlineSnippet(messageEl, payload, reference) {
            if (!messageEl || !payload?.files?.length) return;
            if (messageEl.querySelector('.artifact-inline-snippet')) return;
            const file = payload.files[0];

            // Normalize content to fix literal escape sequences
            const normalizedContent = this.normalizeCodeContent(file.content || '');

            const container = document.createElement('div');
            container.className = 'artifact-inline-snippet';
            const header = document.createElement('div');
            header.className = 'artifact-inline-snippet__header';
            header.textContent = `${reference.title || 'Snippet'}`;
            const copyBtn = document.createElement('button');
            copyBtn.type = 'button';
            copyBtn.className = 'artifact-inline-snippet__copy';
            copyBtn.textContent = 'Copy';
            copyBtn.addEventListener('click', () => {
                navigator.clipboard.writeText(normalizedContent).then(() => {
                    copyBtn.textContent = 'Copied';
                    setTimeout(() => (copyBtn.textContent = 'Copy'), 1500);
                }).catch(() => {
                    copyBtn.textContent = 'Retry';
                });
            });
            header.appendChild(copyBtn);
            const pre = document.createElement('pre');
            pre.className = 'artifact-inline-snippet__code';
            const codeEl = document.createElement('code');
            const lang = file.language || this.guessLanguageFromPath(file.path);
            if (lang) {
                codeEl.className = `language-${lang}`;
            }
            codeEl.textContent = normalizedContent;
            pre.appendChild(codeEl);
            container.append(header, pre);
            const target = messageEl.querySelector('.message-content') || messageEl;
            target.appendChild(container);
            if (typeof Prism !== 'undefined') {
                try {
                    Prism.highlightElement(codeEl);
                } catch (err) {
                    console.warn('Prism highlighting failed for inline snippet:', err);
                }
            }
        }

        loadStoredWidth() {
            try {
                const stored = parseFloat(localStorage.getItem('artifactPanel:width') || '');
                if (!isNaN(stored)) {
                    return this.clampWidth(stored);
                }
            } catch (error) {
                console.warn('Unable to read stored width', error);
            }
            return this.defaultWidth;
        }

        getMaxWidth() {
            return Math.min(window.innerWidth * 0.85, 1120);
        }

        clampWidth(value) {
            return Math.min(Math.max(value, this.minWidth), this.getMaxWidth());
        }

        applyPanelWidth(width, { applyOffset = false } = {}) {
            const clamped = this.clampWidth(width);
            this.currentWidth = clamped;
            document.documentElement.style.setProperty('--artifact-panel-width', `${clamped}px`);
            if (applyOffset) {
                this.updateOffset(true);
            }
        }

        updateOffset(active) {
            document.documentElement.style.setProperty('--artifact-panel-offset', active ? `${this.currentWidth}px` : '0px');
        }

        startResizing(event) {
            if (!this.panel) return;

            event.preventDefault();
            event.stopPropagation();

            this.isResizing = true;
            this.startX = event.clientX;

            // For desktop split-view, track the viewport width
            const isDesktop = window.DeviceDetector ? !DeviceDetector.isMobile() : (window.innerWidth > 768);
            if (isDesktop) {
                this.viewportWidth = window.innerWidth;
                // Get current split percentage or default to 50%
                this.currentSplitPercent = this.getSplitPercentage() || 50;
            } else {
                this.startWidth = this.panel.offsetWidth;
            }

            document.body.classList.add('artifact-panel-resizing');
            document.body.style.cursor = 'ew-resize';
            document.body.style.userSelect = 'none';

            document.addEventListener('mousemove', this.handleResizeMove, { passive: false });
            document.addEventListener('mouseup', this.handleResizeStop);
        }

        onResizeMove(event) {
            if (!this.isResizing) return;
            event.preventDefault();

            if (this.rafId) {
                cancelAnimationFrame(this.rafId);
            }

            this.rafId = requestAnimationFrame(() => {
                const isDesktop = window.DeviceDetector ? !DeviceDetector.isMobile() : (window.innerWidth > 768);

                if (isDesktop) {
                    // Desktop: Update grid split based on mouse position
                    const mouseX = event.clientX;
                    const contentWrapper = document.querySelector('.content-wrapper');
                    if (!contentWrapper) return;

                    const rect = contentWrapper.getBoundingClientRect();
                    const mouseRelativeX = mouseX - rect.left;
                    const totalWidth = rect.width;

                    // Calculate percentage (chat area gets the left portion)
                    let chatPercent = (mouseRelativeX / totalWidth) * 100;

                    // Constrain between 20% and 80% for usability
                    chatPercent = Math.max(20, Math.min(80, chatPercent));
                    const artifactPercent = 100 - chatPercent;

                    this.applySplitPercentage(chatPercent, artifactPercent);
                } else {
                    // Mobile: Use old width-based logic
                    const delta = this.startX - event.clientX;
                    const newWidth = this.startWidth + delta;
                    this.applyPanelWidth(newWidth, { applyOffset: true });
                }
            });
        }

        stopResizing() {
            if (!this.isResizing) return;

            if (this.rafId) {
                cancelAnimationFrame(this.rafId);
                this.rafId = null;
            }

            this.isResizing = false;
            document.body.classList.remove('artifact-panel-resizing');
            document.body.style.cursor = '';
            document.body.style.userSelect = '';

            document.removeEventListener('mousemove', this.handleResizeMove);
            document.removeEventListener('mouseup', this.handleResizeStop);

            const isDesktop = window.DeviceDetector ? !DeviceDetector.isMobile() : (window.innerWidth > 768);
            try {
                if (isDesktop) {
                    // Save split percentage for desktop
                    const splitPercent = this.getSplitPercentage();
                    if (splitPercent) {
                        localStorage.setItem('artifactPanel:splitPercent', String(splitPercent));
                    }
                } else {
                    // Save width for mobile
                    localStorage.setItem('artifactPanel:width', String(this.currentWidth));
                }
            } catch (error) {
                console.warn('Unable to store panel settings', error);
            }
        }

        syncWidthWithViewport() {
            const adjusted = this.clampWidth(this.currentWidth);
            if (adjusted !== this.currentWidth) {
                this.applyPanelWidth(adjusted, { applyOffset: this.panel?.classList.contains('is-open') });
            }
        }

        /* ============================================================
           Edit Tab Functionality
           ============================================================ */

        initializeEditTab(version) {
            if (!version || !this.editFileTreeEl || !this.editCodeMirrorContainer) return;

            // Reset edit state
            this.editingFile = null;
            this.editedFiles.clear();

            // Initialize CodeMirror if not already created
            if (!this.editCodeMirror && typeof CodeMirror !== 'undefined') {
                this.editCodeMirror = CodeMirror(this.editCodeMirrorContainer, {
                    lineNumbers: true,
                    theme: 'material-darker',
                    mode: 'javascript',
                    matchBrackets: true,
                    autoCloseBrackets: true,
                    styleActiveLine: true,
                    indentUnit: 4,
                    tabSize: 4,
                    indentWithTabs: false,
                    lineWrapping: false
                });

                // Listen for changes
                this.editCodeMirror.on('change', () => this.handleEditInput());
            }

            // Render file tree for editing
            this.renderEditFileTree(version);

            // Clear editor
            if (this.editCodeMirror) {
                this.editCodeMirror.setValue('');
            }
            if (this.editCurrentFileName) {
                this.editCurrentFileName.textContent = '';
            }

            // Disable save and revert buttons initially
            this.updateEditButtons(false);
        }

        renderEditFileTree(version) {
            if (!this.editFileTreeEl) return;
            this.editFileTreeEl.innerHTML = '';

            if (!version.files?.length) {
                const empty = document.createElement('p');
                empty.className = 'artifact-file-tree__empty';
                empty.textContent = 'No files available to edit.';
                this.editFileTreeEl.appendChild(empty);
                return;
            }

            const tree = buildFileTree(version.files || []);
            const fragment = document.createDocumentFragment();
            tree.forEach(node => fragment.appendChild(this.renderEditTreeNode(node)));
            this.editFileTreeEl.appendChild(fragment);

            // Auto-select first file
            const firstFileNode = this.editFileTreeEl.querySelector('.artifact-file-node.file');
            if (firstFileNode) {
                firstFileNode.classList.add('is-active');
                const firstFile = version.files[0];
                if (firstFile) {
                    this.loadFileForEditing(firstFile);
                }
            }
        }

        renderEditTreeNode(node) {
            const el = document.createElement('div');
            el.className = `artifact-file-node ${node.type}`;
            el.innerHTML = `<span><i class="${node.type === 'folder' ? 'fas fa-folder' : 'fas fa-file-code'}"></i>${escapeHtml(node.name)}</span>`;

            if (node.type === 'folder') {
                const childrenContainer = document.createElement('div');
                childrenContainer.style.paddingLeft = '12px';
                node.children.forEach(child => {
                    childrenContainer.appendChild(this.renderEditTreeNode(child));
                });
                el.appendChild(childrenContainer);
            } else if (node.type === 'file') {
                el.addEventListener('click', () => {
                    this.loadFileForEditing(node.file);
                    this.editFileTreeEl.querySelectorAll('.is-active').forEach(active => active.classList.remove('is-active'));
                    el.classList.add('is-active');
                });
            }

            return el;
        }

        loadFileForEditing(file) {
            if (!file || !this.editCodeMirror) return;

            this.editingFile = file;
            this.originalFileContent = file.content || '';

            // Load edited version if exists, otherwise original
            const content = this.editedFiles.get(file.path) || file.content || '';

            // Set content in CodeMirror
            this.editCodeMirror.setValue(content);

            // Set mode based on file extension/language
            const mode = this.getCodeMirrorMode(file);
            this.editCodeMirror.setOption('mode', mode);

            if (this.currentFileName) this.currentFileName.textContent = file.path || 'File';

            // Update button states
            const hasChanges = this.editedFiles.has(file.path);
            this.updateEditButtons(hasChanges);

            // Refresh CodeMirror to ensure proper rendering
            setTimeout(() => this.editCodeMirror.refresh(), 1);
        }

        getCodeMirrorMode(file) {
            const language = (file.language || '').toLowerCase();
            const path = (file.path || '').toLowerCase();

            // Map language to CodeMirror mode
            const modeMap = {
                'javascript': 'javascript',
                'js': 'javascript',
                'jsx': 'jsx',
                'typescript': 'javascript',
                'ts': 'javascript',
                'tsx': 'jsx',
                'python': 'python',
                'py': 'python',
                'html': 'htmlmixed',
                'htm': 'htmlmixed',
                'css': 'css',
                'scss': 'css',
                'less': 'css',
                'json': 'javascript',
                'xml': 'xml',
                'svg': 'xml',
                'markdown': 'markdown',
                'md': 'markdown',
                'sql': 'sql',
                'shell': 'shell',
                'bash': 'shell',
                'sh': 'shell'
            };

            // Try language first
            if (modeMap[language]) {
                return modeMap[language];
            }

            // Try file extension
            const ext = path.split('.').pop();
            if (modeMap[ext]) {
                return modeMap[ext];
            }

            // Default to javascript
            return 'javascript';
        }

        handleEditInput() {
            if (!this.editingFile || !this.editCodeMirror) return;

            const currentContent = this.editCodeMirror.getValue();
            const originalContent = this.originalFileContent || '';

            if (currentContent !== originalContent) {
                this.editedFiles.set(this.editingFile.path, currentContent);
                this.updateEditButtons(true);
                // Auto-save after 2s of inactivity
                clearTimeout(this._autoSaveTimer);
                this._autoSaveTimer = setTimeout(() => this.saveEdits(), 2000);
            } else {
                this.editedFiles.delete(this.editingFile.path);
                this.updateEditButtons(this.editedFiles.size > 0);
                clearTimeout(this._autoSaveTimer);
            }
        }

        updateEditButtons(hasChanges) {
            if (this.saveEditBtn) {
                this.saveEditBtn.disabled = !hasChanges;
            }
            if (this.revertEditBtn) {
                this.revertEditBtn.disabled = !hasChanges;
            }
        }

        revertEdits() {
            if (!this.editingFile || !this.editCodeMirror) return;

            // Revert current file to original
            this.editCodeMirror.setValue(this.originalFileContent || '');
            this.editedFiles.delete(this.editingFile.path);

            this.updateEditButtons(this.editedFiles.size > 0);
            this.setStatus('Changes reverted.', 'success');
        }

        async saveEdits() {
            if (!this.currentArtifact?.artifactId || !this.currentVersion?.versionId || !this.currentSessionId) {
                this.setStatus('Unable to save edits: artifact not loaded.', 'error');
                return;
            }

            if (this.editedFiles.size === 0) {
                this.setStatus('No changes to save.', 'error');
                return;
            }

            try {
                // Build updated files array
                const updatedFiles = this.currentVersion.files.map(file => {
                    if (this.editedFiles.has(file.path)) {
                        return { ...file, content: this.editedFiles.get(file.path) };
                    }
                    return file;
                });

                // Create new version with edited files
                const payload = {
                    files: updatedFiles,
                    entrypointPath: this.currentVersion.entrypointPath,
                    primaryLanguage: this.currentVersion.primaryLanguage,
                    title: this.currentArtifact.title,
                    description: `Edited version (based on v${this.currentVersion.versionNumber || 1})`
                };

                this.setStatus('Saving changes...', 'loading');

                const response = await fetch(`/chat/${this.currentSessionId}/artifacts/${this.currentArtifact.artifactId}/edit`, {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-CSRFToken': this.token
                    },
                    body: JSON.stringify(payload)
                });

                if (!response.ok) {
                    throw new Error(`Failed to save edits: ${response.statusText}`);
                }

                const result = await response.json();

                if (result.success) {
                    this.setStatus('Changes saved successfully!', 'success');
                    this.editedFiles.clear();
                    this.updateEditButtons(false);

                    // Reload artifact to show new version
                    await this.openFromReference(this.currentReference, this.currentSessionId);
                    this.switchTab('preview');
                } else {
                    throw new Error(result.error || 'Failed to save edits');
                }
            } catch (error) {
                console.error('Save edit error:', error);
                this.setStatus(`Error saving edits: ${error.message}`, 'error');
            }
        }

        /* ============================================================
           Edit Mode Toggle (Code Tab)
           ============================================================ */

        toggleEditMode() {
            if (this.isEditMode) {
                this.exitEditMode();
            } else {
                this.enterEditMode();
            }
        }

        enterEditMode() {
            if (!this.currentVersion || this.isEditMode) return;

            this.isEditMode = true;

            this._ensureCodeMirror();
            if (this.editCodeMirror) {
                this.editCodeMirror.setOption('readOnly', false);
            }

            // Get current file from code viewer
            const currentFileName = this.currentFileName?.textContent;
            const currentFile = this.currentVersion.files?.find(f => f.path === currentFileName);

            if (currentFile) {
                this.editingFile = currentFile;
                this.originalFileContent = currentFile.content || '';

                // Load content into CodeMirror
                const content = this.editedFiles.get(currentFile.path) || currentFile.content || '';
                this.editCodeMirror.setValue(content);

                // Set mode
                const mode = this.getCodeMirrorMode(currentFile);
                this.editCodeMirror.setOption('mode', mode);

                // Refresh CodeMirror
                setTimeout(() => this.editCodeMirror.refresh(), 10);
            }

            // Update UI
            this.codeDisplay.style.display = 'none';
            this.editCodeMirrorContainer.style.display = 'block';
            this.editModeToggleBtn.innerHTML = '<i class="fas fa-eye" aria-hidden="true"></i> View';
            this.saveEditBtn.style.display = '';
            this.revertEditBtn.style.display = '';

            // Update button states
            this.updateEditButtons(this.editedFiles.size > 0);
        }

        exitEditMode() {
            if (!this.isEditMode) return;

            this.isEditMode = false;

            // Update UI
            this.codeDisplay.style.display = 'block';
            this.editCodeMirrorContainer.style.display = 'none';
            this.editModeToggleBtn.innerHTML = '<i class="fas fa-pencil" aria-hidden="true"></i> Edit';
            this.saveEditBtn.style.display = 'none';
            this.revertEditBtn.style.display = 'none';

            // If there are unsaved changes, show in code view
            if (this.editingFile && this.editedFiles.has(this.editingFile.path)) {
                const editedContent = this.editedFiles.get(this.editingFile.path);
                this.codeBlock.textContent = editedContent;
                if (typeof Prism !== 'undefined') {
                    Prism.highlightElement(this.codeBlock);
                }
            }
        }

        /* ============================================================
           Composer Functionality
           ============================================================ */

        initializeComposer() {
            // Reset composer state
            if (this.composerPrompt) {
                this.composerPrompt.value = '';
                this.composerSubmitBtn.disabled = true;
            }
        }

        handleComposerInput() {
            if (!this.composerPrompt || !this.composerSubmitBtn) return;

            const hasContent = this.composerPrompt.value.trim().length > 0;
            this.composerSubmitBtn.disabled = !hasContent;
        }

        async submitComposerPrompt() {
            if (!this.composerPrompt || !this.currentArtifact || !this.currentSessionId) {
                console.error('Composer: Missing required data');
                return;
            }

            const prompt = this.composerPrompt.value.trim();
            if (!prompt) return;

            try {
                // Show progress UI
                const composerForm = document.getElementById('composer-form');
                const composerProgress = document.getElementById('composer-progress');
                const progressText = document.getElementById('composer-progress-text');

                if (composerForm) composerForm.style.display = 'none';
                if (composerProgress) composerProgress.style.display = 'flex';

                this.setStatus('Preparing to update artifact...', 'loading');

                // Note: We no longer need to manually set artifacts as active.
                // The backend's persist_decision() will automatically detect the active artifact
                // based on the UPDATE keywords in the message and the session context.

                this.setStatus('Generating updated version...', 'loading');

                // Store current version count for monitoring
                const initialVersionCount = this.currentArtifact.versions?.length || 0;

                // Build context message with current artifact code
                let filesContext = '';
                if (this.currentVersion && this.currentVersion.files && this.currentVersion.files.length > 0) {
                    filesContext = this.buildFilesContext(this.currentVersion.files);
                } else {
                    console.warn('Composer: No files in current version');
                    filesContext = `Artifact: ${this.currentArtifact.title}`;
                }

                // Use strong UPDATE keywords and explicit artifact block request
                const messageText = `Update the artifact "${this.currentArtifact.title}" with these changes:

${prompt}

Current artifact code:
${filesContext}

Please provide the COMPLETE updated artifact code in a \`\`\`artifact block with all files, incorporating the requested changes. Use this exact format:

\`\`\`artifact
{
  "title": "${this.currentArtifact.title}",
  "files": [
    {"path": "...", "language": "...", "content": "..."}
  ],
  "entrypoint": "..."
}
\`\`\`

Make sure to include ALL files with the complete updated code.`;

                console.log('Composer: Sending update request', {
                    artifactId: this.currentArtifact.artifactId,
                    versionCount: initialVersionCount,
                    hasFiles: !!(this.currentVersion && this.currentVersion.files),
                    filesCount: this.currentVersion?.files?.length || 0
                });

                // Create a clean, user-friendly message to display in chat
                const displayMessage = `✨ Update artifact: ${prompt}`;

                // Send message using the global sendMessage function with custom display text
                if (typeof window.sendMessage === 'function') {
                    // Send the full messageText to backend, but display clean message in UI
                    await window.sendMessage(messageText, null, {
                        displayPrompt: displayMessage
                    });

                    // Clear composer input
                    this.composerPrompt.value = '';

                    // Show toast notification
                    if (window.Toast) {
                        Toast.success('Updating artifact...');
                    }

                    // Switch to preview tab
                    this.switchTab('preview');

                    // Start monitoring for new version
                    this.monitorForNewVersion(initialVersionCount, progressText);
                } else {
                    throw new Error('sendMessage function not available');
                }

            } catch (error) {
                console.error('Composer error:', error);
                this.setStatus(`Error: ${error.message}`, 'error');

                if (window.Toast) {
                    Toast.error('Failed to send composer request');
                }

                // Reset UI on error
                const composerForm = document.getElementById('composer-form');
                const composerProgress = document.getElementById('composer-progress');
                if (composerForm) composerForm.style.display = 'flex';
                if (composerProgress) composerProgress.style.display = 'none';
            }
        }

        buildFilesContext(files) {
            if (!files || files.length === 0) return '';

            // Format files as JSON for clearer context that AI can easily reproduce
            const filesJson = files.map(file => {
                return {
                    path: file.path || 'unknown',
                    language: file.language || '',
                    content: file.content || ''
                };
            });

            // Return formatted JSON structure showing current files
            return `\`\`\`json
${JSON.stringify(filesJson, null, 2)}
\`\`\``;
        }

        monitorForNewVersion(initialVersionCount, progressText) {
            let checkCount = 0;
            const maxChecks = 120; // 2 minutes max (120 * 1000ms = 2 min)

            const checkInterval = setInterval(async () => {
                checkCount++;

                // Update progress text
                if (progressText) {
                    const dots = '.'.repeat((checkCount % 3) + 1);
                    progressText.textContent = `AI is working on your request${dots}`;
                }

                // Stop after max checks
                if (checkCount > maxChecks) {
                    clearInterval(checkInterval);
                    this.resetComposerUI();
                    if (window.Toast) {
                        Toast.warning('Request is taking longer than expected. Please check back later.');
                    }
                    return;
                }

                // Check for new version
                try {
                    const response = await fetch(`/chat/${this.currentSessionId}/artifacts/${this.currentArtifact.artifactId}`);
                    if (response.ok) {
                        const data = await response.json();
                        if (data.artifact && data.artifact.versions) {
                            const newVersionCount = data.artifact.versions.length;

                            // Debug logging
                            if (checkCount % 10 === 0) {
                                console.log(`Composer check #${checkCount}: versions=${newVersionCount}, expecting>${initialVersionCount}`);
                            }

                            if (newVersionCount > initialVersionCount) {
                                // New version detected!
                                console.log('✅ Composer SUCCESS: New version detected!', {
                                    artifactId: this.currentArtifact.artifactId,
                                    oldVersionCount: initialVersionCount,
                                    newVersionCount: newVersionCount,
                                    newVersion: `v${newVersionCount}`
                                });
                                clearInterval(checkInterval);

                                if (progressText) {
                                    progressText.textContent = 'New version created! Refreshing...';
                                }

                                // Wait a moment then refresh
                                setTimeout(async () => {
                                    await this.openFromReference(this.currentReference, this.currentSessionId);
                                    this.resetComposerUI();

                                    // Select the newest version
                                    const latestVersionId = data.artifact.versions[newVersionCount - 1].versionId;
                                    if (this.versionSelect) {
                                        this.versionSelect.value = latestVersionId;
                                        await this.handleVersionChange();
                                    }

                                    if (window.Toast) {
                                        Toast.success(`Version ${newVersionCount} created successfully!`);
                                    }
                                    this.setStatus(`Version ${newVersionCount} ready`, 'success');
                                }, 500);
                            }
                        }
                    }
                } catch (error) {
                    console.error('Error checking for new version:', error);
                }
            }, 1000); // Check every second
        }

        resetComposerUI() {
            const composerForm = document.getElementById('composer-form');
            const composerProgress = document.getElementById('composer-progress');

            if (composerForm) composerForm.style.display = 'flex';
            if (composerProgress) composerProgress.style.display = 'none';

            if (this.composerSubmitBtn) {
                this.composerSubmitBtn.disabled = true;
            }
        }

        clearComposerPrompt() {
            if (this.composerPrompt) {
                this.composerPrompt.value = '';
                this.composerSubmitBtn.disabled = true;
            }
        }

        /* ============================================================
           Diff Tab Functionality
           ============================================================ */

        initializeDiffTab(artifact) {
            if (!artifact || !this.diffFromSelect || !this.diffToSelect) return;

            // Populate version selects
            this.populateDiffVersionSelects(artifact);

            // Clear diff viewer
            this.clearDiffViewer();
        }

        populateDiffVersionSelects(artifact) {
            if (!this.diffFromSelect || !this.diffToSelect) return;

            const versions = artifact.versions || [];

            // Clear existing options (except placeholder)
            this.diffFromSelect.innerHTML = '<option value="">Select version...</option>';
            this.diffToSelect.innerHTML = '<option value="">Select version...</option>';

            // Add version options
            versions.forEach((version, index) => {
                const optionFrom = document.createElement('option');
                optionFrom.value = version.versionId || index;
                optionFrom.textContent = `v${version.versionNumber || index + 1} - ${new Date(version.createdAt).toLocaleString()}`;

                const optionTo = optionFrom.cloneNode(true);

                this.diffFromSelect.appendChild(optionFrom);
                this.diffToSelect.appendChild(optionTo);
            });

            // Auto-select: from = previous version, to = current version
            if (versions.length >= 2) {
                this.diffFromSelect.selectedIndex = versions.length - 1;
                this.diffToSelect.selectedIndex = versions.length;
            }
        }

        async handleDiffSelectionChange() {
            const fromVersionId = this.diffFromSelect?.value;
            const toVersionId = this.diffToSelect?.value;

            if (!fromVersionId || !toVersionId) {
                this.clearDiffViewer();
                return;
            }

            if (fromVersionId === toVersionId) {
                this.showDiffPlaceholder('Please select two different versions to compare.');
                return;
            }

            try {
                this.setStatus('Loading versions for comparison...', 'loading');

                // Fetch both versions
                const fromVersion = await this.loadVersionById(fromVersionId);
                const toVersion = await this.loadVersionById(toVersionId);

                if (!fromVersion || !toVersion) {
                    throw new Error('Unable to load versions for comparison');
                }

                // Generate and render diff
                this.renderDiff(fromVersion, toVersion);
                this.setStatus('Comparison loaded.', 'success');
            } catch (error) {
                console.error('Diff loading error:', error);
                this.setStatus(`Error loading diff: ${error.message}`, 'error');
                this.clearDiffViewer();
            }
        }

        async loadVersionById(versionId) {
            // Check cache first
            if (this.versionCache.has(versionId)) {
                return this.versionCache.get(versionId);
            }

            // Find in current artifact versions
            const version = this.currentArtifact?.versions?.find(v => v.versionId === versionId);
            if (version && version.files) {
                this.versionCache.set(versionId, version);
                return version;
            }

            // Fetch from server if needed
            if (!this.currentSessionId || !this.currentArtifact?.artifactId) {
                return null;
            }

            try {
                const response = await fetch(`/chat/${this.currentSessionId}/artifacts/${this.currentArtifact.artifactId}/versions/${versionId}`);
                if (!response.ok) {
                    throw new Error(`Failed to fetch version: ${response.statusText}`);
                }

                const data = await response.json();
                this.versionCache.set(versionId, data.version);
                return data.version;
            } catch (error) {
                console.error('Version fetch error:', error);
                return null;
            }
        }

        renderDiff(fromVersion, toVersion) {
            if (!this.diffViewer || !this.diffFileList) return;

            // Clear previous diff
            this.diffViewer.innerHTML = '';
            this.diffFileList.innerHTML = '';

            // Build file maps
            const fromFiles = new Map(fromVersion.files.map(f => [f.path, f]));
            const toFiles = new Map(toVersion.files.map(f => [f.path, f]));

            // Find all unique file paths
            const allPaths = new Set([...fromFiles.keys(), ...toFiles.keys()]);
            const changedFiles = [];

            allPaths.forEach(path => {
                const fromFile = fromFiles.get(path);
                const toFile = toFiles.get(path);

                if (!fromFile && toFile) {
                    // File added
                    changedFiles.push({ path, status: 'added', fromFile: null, toFile });
                } else if (fromFile && !toFile) {
                    // File deleted
                    changedFiles.push({ path, status: 'deleted', fromFile, toFile: null });
                } else if (fromFile && toFile && fromFile.content !== toFile.content) {
                    // File modified
                    changedFiles.push({ path, status: 'modified', fromFile, toFile });
                }
            });

            if (changedFiles.length === 0) {
                this.showDiffPlaceholder('No changes between these versions.');
                return;
            }

            // Render file list
            changedFiles.forEach((file, index) => {
                const item = document.createElement('div');
                item.className = 'artifact-diff__file-item';
                if (index === 0) item.classList.add('is-active');

                const icon = file.status === 'added' ? 'fa-plus' :
                            file.status === 'deleted' ? 'fa-minus' : 'fa-pen';

                item.innerHTML = `<i class="fas ${icon}"></i>${escapeHtml(file.path)}`;
                item.addEventListener('click', () => {
                    this.diffFileList.querySelectorAll('.is-active').forEach(el => el.classList.remove('is-active'));
                    item.classList.add('is-active');
                    this.renderFileDiff(file);
                });

                this.diffFileList.appendChild(item);
            });

            // Render first file diff by default
            this.renderFileDiff(changedFiles[0]);
        }

        renderFileDiff(file) {
            if (!this.diffViewer) return;

            this.diffViewer.innerHTML = '';

            // Use CodeMirror merge view for advanced diff if available
            if (typeof CodeMirror !== 'undefined' && CodeMirror.MergeView) {
                this.renderCodeMirrorDiff(file);
            } else {
                // Fallback to basic diff
                const contentDiv = document.createElement('div');
                contentDiv.className = `artifact-diff__content ${this.currentDiffMode}`;

                if (this.currentDiffMode === 'side-by-side') {
                    this.renderSideBySideDiff(contentDiv, file);
                } else {
                    this.renderUnifiedDiff(contentDiv, file);
                }

                this.diffViewer.appendChild(contentDiv);
            }
        }

        renderCodeMirrorDiff(file) {
            const fromContent = file.fromFile?.content || '';
            const toContent = file.toFile?.content || '';

            // Determine file mode
            const mode = file.toFile ? this.getCodeMirrorMode(file.toFile) :
                        file.fromFile ? this.getCodeMirrorMode(file.fromFile) : 'javascript';

            // Create merge view container
            const mergeContainer = document.createElement('div');
            mergeContainer.style.height = '100%';
            this.diffViewer.appendChild(mergeContainer);

            // Initialize CodeMirror merge view
            const mergeView = CodeMirror.MergeView(mergeContainer, {
                value: toContent,
                orig: fromContent,
                lineNumbers: true,
                mode: mode,
                theme: 'material-darker',
                highlightDifferences: true,
                connect: 'align',
                collapseIdentical: false,
                readOnly: true,
                styleActiveLine: false,
                matchBrackets: true
            });

            // Store reference for cleanup
            this.currentMergeView = mergeView;
        }

        renderSideBySideDiff(container, file) {
            const fromContent = file.fromFile?.content || '';
            const toContent = file.toFile?.content || '';

            const fromColumn = document.createElement('div');
            fromColumn.className = 'diff-column';
            fromColumn.innerHTML = `<div class="diff-column-header">Previous Version</div>`;

            const toColumn = document.createElement('div');
            toColumn.className = 'diff-column';
            toColumn.innerHTML = `<div class="diff-column-header">Current Version</div>`;

            const fromLines = fromContent.split('\n');
            const toLines = toContent.split('\n');

            // Simple line-by-line comparison (basic implementation)
            const maxLines = Math.max(fromLines.length, toLines.length);

            for (let i = 0; i < maxLines; i++) {
                const fromLine = fromLines[i] || '';
                const toLine = toLines[i] || '';

                const fromDiv = document.createElement('div');
                fromDiv.className = 'diff-line';
                if (i >= fromLines.length) {
                    fromDiv.classList.add('removed');
                } else if (fromLine !== toLine) {
                    fromDiv.classList.add('removed');
                }
                fromDiv.innerHTML = `<span class="diff-line-number">${i + 1}</span><span class="diff-line-content">${escapeHtml(fromLine)}</span>`;

                const toDiv = document.createElement('div');
                toDiv.className = 'diff-line';
                if (i >= toLines.length) {
                    toDiv.classList.add('added');
                } else if (fromLine !== toLine) {
                    toDiv.classList.add('added');
                }
                toDiv.innerHTML = `<span class="diff-line-number">${i + 1}</span><span class="diff-line-content">${escapeHtml(toLine)}</span>`;

                fromColumn.appendChild(fromDiv);
                toColumn.appendChild(toDiv);
            }

            container.appendChild(fromColumn);
            container.appendChild(toColumn);
        }

        renderUnifiedDiff(container, file) {
            const fromContent = file.fromFile?.content || '';
            const toContent = file.toFile?.content || '';

            const fromLines = fromContent.split('\n');
            const toLines = toContent.split('\n');

            // Simple unified diff (basic implementation)
            const maxLines = Math.max(fromLines.length, toLines.length);

            for (let i = 0; i < maxLines; i++) {
                const fromLine = fromLines[i];
                const toLine = toLines[i];

                if (fromLine === toLine) {
                    // Unchanged line
                    const lineDiv = document.createElement('div');
                    lineDiv.className = 'diff-line';
                    lineDiv.innerHTML = `<span class="diff-line-number">${i + 1}</span><span class="diff-line-content">${escapeHtml(fromLine || '')}</span>`;
                    container.appendChild(lineDiv);
                } else {
                    // Show removed line
                    if (fromLine !== undefined) {
                        const removedDiv = document.createElement('div');
                        removedDiv.className = 'diff-line removed';
                        removedDiv.innerHTML = `<span class="diff-line-number">-${i + 1}</span><span class="diff-line-content">${escapeHtml(fromLine)}</span>`;
                        container.appendChild(removedDiv);
                    }

                    // Show added line
                    if (toLine !== undefined) {
                        const addedDiv = document.createElement('div');
                        addedDiv.className = 'diff-line added';
                        addedDiv.innerHTML = `<span class="diff-line-number">+${i + 1}</span><span class="diff-line-content">${escapeHtml(toLine)}</span>`;
                        container.appendChild(addedDiv);
                    }
                }
            }
        }

        switchDiffMode(mode) {
            if (!mode) return;

            this.currentDiffMode = mode;

            this.diffModeButtons.forEach(button => {
                const isActive = button.dataset.diffMode === mode;
                button.classList.toggle('is-active', isActive);
            });

            // Re-render current diff with new mode
            const activeFileItem = this.diffFileList?.querySelector('.is-active');
            if (activeFileItem) {
                activeFileItem.click();
            }
        }

        clearDiffViewer() {
            if (this.diffViewer) {
                this.showDiffPlaceholder('Select two versions to compare');
            }
            if (this.diffFileList) {
                this.diffFileList.innerHTML = '';
            }
        }

        showDiffPlaceholder(message) {
            if (!this.diffViewer) return;

            this.diffViewer.innerHTML = `
                <div class="artifact-diff__placeholder">
                    <i class="fas fa-code-compare" aria-hidden="true"></i>
                    <p>${message}</p>
                </div>
            `;
        }

        /* ============================================================
           Share Functionality
           ============================================================ */

        async shareArtifact() {
            if (!this.currentArtifact?.artifactId || !this.currentSessionId) {
                this.setStatus('No artifact loaded to share.', 'error');
                return;
            }

            try {
                this.setStatus('Generating share link...', 'loading');

                const response = await fetch(`/chat/${this.currentSessionId}/artifacts/${this.currentArtifact.artifactId}/share`, {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-CSRFToken': this.token
                    },
                    body: JSON.stringify({})
                });

                if (!response.ok) {
                    throw new Error(`Failed to generate share link: ${response.statusText}`);
                }

                const result = await response.json();

                if (result.success && result.shareUrl) {
                    // Copy share URL to clipboard
                    await navigator.clipboard.writeText(result.shareUrl);
                    this.setStatus('Share link copied to clipboard!', 'success');

                    // Optionally show a modal with the link
                    this.showShareDialog(result.shareUrl);
                } else {
                    throw new Error(result.error || 'Failed to generate share link');
                }
            } catch (error) {
                console.error('Share error:', error);
                this.setStatus(`Error sharing artifact: ${error.message}`, 'error');
            }
        }

        showShareDialog(shareUrl) {
            // Simple alert for now - could be replaced with a proper modal
            const message = `Share link copied to clipboard!\n\n${shareUrl}\n\nAnyone with this link can view this artifact.`;
            alert(message);
        }

    }

    function buildFileMap(version) {
        const map = {};
        (version.files || []).forEach(file => {
            if (file.path) {
                map[file.path] = file;
            }
        });
        return map;
    }

    function buildFileTree(files) {
        const root = new Map();
        (files || []).forEach(file => {
            const parts = (file.path || '').split('/');
            let level = root;
            parts.forEach((part, index) => {
                if (!level.has(part)) {
                    level.set(part, { type: index === parts.length - 1 ? 'file' : 'folder', name: part, children: new Map(), file: null });
                }
                const node = level.get(part);
                if (index === parts.length - 1) {
                    node.file = file;
                } else {
                    node.children = node.children || new Map();
                    level = node.children;
                }
            });
        });
        return Array.from(root.values()).map(node => normalizeTree(node));
    }

    function normalizeTree(node) {
        if (node.type === 'file') {
            return { type: 'file', name: node.name, file: node.file };
        }
        return {
            type: 'folder',
            name: node.name,
            children: Array.from((node.children || new Map()).values()).map(child => normalizeTree(child))
        };
    }

    function escapeHtml(text) {
        return (text || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    }

    function escapeScript(text) {
        return (text || '').replace(/<\/script>/gi, '<\\/script>');
    }

    function capitalize(text) {
        if (!text) return '';
        return text.charAt(0).toUpperCase() + text.slice(1);
    }

    window.artifactPanel = new ArtifactPanel();
})();
