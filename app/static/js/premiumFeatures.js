/**
 * Premium Features for XelaSphere Chat
 * - Code block headers with language label + copy button
 * - Auto callout detection (emoji → colored blockquotes)
 * - Smooth content animations
 */

(function() {
    'use strict';

    // ═══════════════════════════════════════════════════════════════
    // CODE BLOCK ENHANCEMENT
    // ═══════════════════════════════════════════════════════════════

    const languageLabels = {
        'js': 'JavaScript',
        'javascript': 'JavaScript',
        'ts': 'TypeScript',
        'typescript': 'TypeScript',
        'py': 'Python',
        'python': 'Python',
        'html': 'HTML',
        'css': 'CSS',
        'scss': 'SCSS',
        'sass': 'SASS',
        'json': 'JSON',
        'xml': 'XML',
        'yaml': 'YAML',
        'yml': 'YAML',
        'md': 'Markdown',
        'markdown': 'Markdown',
        'bash': 'Bash',
        'sh': 'Shell',
        'shell': 'Shell',
        'zsh': 'Zsh',
        'sql': 'SQL',
        'java': 'Java',
        'c': 'C',
        'cpp': 'C++',
        'c++': 'C++',
        'csharp': 'C#',
        'cs': 'C#',
        'go': 'Go',
        'rust': 'Rust',
        'ruby': 'Ruby',
        'rb': 'Ruby',
        'php': 'PHP',
        'swift': 'Swift',
        'kotlin': 'Kotlin',
        'scala': 'Scala',
        'r': 'R',
        'dart': 'Dart',
        'lua': 'Lua',
        'perl': 'Perl',
        'powershell': 'PowerShell',
        'ps1': 'PowerShell',
        'dockerfile': 'Dockerfile',
        'docker': 'Docker',
        'nginx': 'Nginx',
        'apache': 'Apache',
        'graphql': 'GraphQL',
        'jsx': 'JSX',
        'tsx': 'TSX',
        'vue': 'Vue',
        'svelte': 'Svelte',
        'diff': 'Diff',
        'plaintext': 'Text',
        'text': 'Text',
        'txt': 'Text'
    };

    // Terminal/shell languages that should render as terminal window
    const terminalLanguages = new Set([
        'bash', 'sh', 'shell', 'zsh', 'terminal', 'console',
        'cmd', 'powershell', 'ps1', 'command', 'cli'
    ]);

    function isTerminalLanguage(lang) {
        if (!lang) return false;
        const normalized = lang.toLowerCase().replace('language-', '');
        return terminalLanguages.has(normalized);
    }

    function getLanguageLabel(lang) {
        if (!lang) return 'Code';
        const normalized = lang.toLowerCase().replace('language-', '');
        return languageLabels[normalized] || normalized.charAt(0).toUpperCase() + normalized.slice(1);
    }

    function formatTerminalContent(codeText) {
        // Process each line to add proper terminal styling
        const lines = codeText.split('\n');
        return lines.map(line => {
            const trimmed = line.trim();
            // Lines starting with $ or > are commands
            if (trimmed.startsWith('$ ') || trimmed.startsWith('> ')) {
                const prompt = trimmed.startsWith('$ ') ? '$ ' : '> ';
                const command = trimmed.slice(2);
                return `<div class="terminal-line"><span class="terminal-prompt">${prompt}</span><span class="terminal-command">${escapeHtml(command)}</span></div>`;
            }
            // Lines starting with # are comments
            else if (trimmed.startsWith('#')) {
                return `<div class="terminal-line"><span class="terminal-output comment">${escapeHtml(line)}</span></div>`;
            }
            // Empty lines
            else if (trimmed === '') {
                return `<div class="terminal-line"><span class="terminal-output">&nbsp;</span></div>`;
            }
            // Output lines
            else {
                return `<div class="terminal-line"><span class="terminal-output">${escapeHtml(line)}</span></div>`;
            }
        }).join('');
    }

    function escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }

    function createTerminalWindow(pre, code, lang) {
        const codeText = code.textContent;

        const wrapper = document.createElement('div');
        wrapper.className = 'terminal-window';
        wrapper.innerHTML = `
            <div class="terminal-header">
                <div class="terminal-dots">
                    <span class="terminal-dot red"></span>
                    <span class="terminal-dot yellow"></span>
                    <span class="terminal-dot green"></span>
                </div>
                <div class="terminal-title">
                    <i class="fas fa-terminal"></i>
                    <span>Terminal</span>
                </div>
                <div class="terminal-actions">
                    <button class="terminal-copy" title="Copy commands">
                        <i class="fas fa-copy"></i>
                        <span>Copy</span>
                    </button>
                </div>
            </div>
            <div class="terminal-body">
                ${formatTerminalContent(codeText)}
            </div>
        `;

        // Add copy functionality
        const copyBtn = wrapper.querySelector('.terminal-copy');
        copyBtn.addEventListener('click', async () => {
            try {
                // Copy only the commands (lines with $ or >)
                const commands = codeText.split('\n')
                    .filter(line => line.trim().startsWith('$ ') || line.trim().startsWith('> '))
                    .map(line => line.trim().replace(/^[$>]\s*/, ''))
                    .join('\n');

                await navigator.clipboard.writeText(commands || codeText);
                copyBtn.classList.add('copied');
                copyBtn.querySelector('span').textContent = 'Copied!';
                copyBtn.querySelector('i').className = 'fas fa-check';

                setTimeout(() => {
                    copyBtn.classList.remove('copied');
                    copyBtn.querySelector('span').textContent = 'Copy';
                    copyBtn.querySelector('i').className = 'fas fa-copy';
                }, 2000);
            } catch (err) {
                console.error('Failed to copy:', err);
            }
        });

        return wrapper;
    }

    function enhanceCodeBlock(pre) {
        // Skip if already enhanced
        if (pre.dataset.enhanced) return;
        pre.dataset.enhanced = 'true';

        const code = pre.querySelector('code');
        if (!code) return;

        // Get language from class
        const langClass = Array.from(code.classList).find(c => c.startsWith('language-'));
        const lang = langClass ? langClass.replace('language-', '') : '';

        // Check if this is a terminal/shell language
        if (isTerminalLanguage(lang)) {
            const terminalWindow = createTerminalWindow(pre, code, lang);
            pre.parentNode.insertBefore(terminalWindow, pre);
            pre.remove();
            return;
        }

        // Regular code block enhancement
        const label = getLanguageLabel(lang);

        // Create header
        const header = document.createElement('div');
        header.className = 'code-block-header';
        header.innerHTML = `
            <span class="code-block-lang">${label}</span>
            <button class="code-block-copy" title="Copy code">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect>
                    <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path>
                </svg>
                <span class="copy-text">Copy</span>
            </button>
        `;

        // Add copy functionality
        const copyBtn = header.querySelector('.code-block-copy');
        copyBtn.addEventListener('click', async () => {
            try {
                await navigator.clipboard.writeText(code.textContent);
                copyBtn.classList.add('copied');
                copyBtn.querySelector('.copy-text').textContent = 'Copied!';

                setTimeout(() => {
                    copyBtn.classList.remove('copied');
                    copyBtn.querySelector('.copy-text').textContent = 'Copy';
                }, 2000);
            } catch (err) {
                console.error('Failed to copy:', err);
            }
        });

        // Wrap pre in container
        const wrapper = document.createElement('div');
        wrapper.className = 'code-block-wrapper';
        pre.parentNode.insertBefore(wrapper, pre);
        wrapper.appendChild(header);
        wrapper.appendChild(pre);
    }

    // ═══════════════════════════════════════════════════════════════
    // AUTO CALLOUT DETECTION
    // ═══════════════════════════════════════════════════════════════

    const calloutPatterns = [
        { emoji: '⚠️', type: 'warning', keywords: ['warning', 'caution', 'attention'] },
        { emoji: '💡', type: 'tip', keywords: ['tip', 'hint', 'suggestion'] },
        { emoji: '✅', type: 'success', keywords: ['success', 'done', 'complete'] },
        { emoji: 'ℹ️', type: 'info', keywords: ['info', 'note', 'information'] },
        { emoji: '❌', type: 'error', keywords: ['error', 'danger', 'critical'] },
        { emoji: '🔴', type: 'error', keywords: [] },
        { emoji: '🟡', type: 'warning', keywords: [] },
        { emoji: '🟢', type: 'success', keywords: [] },
        { emoji: '📝', type: 'note', keywords: ['note'] },
        { emoji: '🚨', type: 'error', keywords: ['alert'] },
        { emoji: '💬', type: 'info', keywords: [] },
        { emoji: '📌', type: 'note', keywords: [] },
        { emoji: '🎯', type: 'tip', keywords: [] },
        { emoji: '⭐', type: 'tip', keywords: ['best practice'] }
    ];

    function detectCalloutType(text) {
        if (!text) return null;
        const firstLine = text.split('\n')[0].toLowerCase();

        for (const pattern of calloutPatterns) {
            if (text.includes(pattern.emoji)) {
                return pattern.type;
            }
            for (const keyword of pattern.keywords) {
                if (firstLine.includes(keyword)) {
                    return pattern.type;
                }
            }
        }
        return null;
    }

    function enhanceBlockquote(blockquote) {
        // Skip if already enhanced
        if (blockquote.dataset.calloutEnhanced) return;
        blockquote.dataset.calloutEnhanced = 'true';

        const text = blockquote.textContent || '';
        const calloutType = detectCalloutType(text);

        if (calloutType) {
            blockquote.classList.add(`callout-${calloutType}`);
            blockquote.dataset.callout = calloutType;
        }
    }

    // ═══════════════════════════════════════════════════════════════
    // CONTENT ANIMATION
    // ═══════════════════════════════════════════════════════════════

    function animateNewContent(container) {
        const elements = container.querySelectorAll('h1, h2, h3, h4, h5, h6, p, ul, ol, blockquote, pre, table, hr');

        elements.forEach((el, index) => {
            if (!el.dataset.animated) {
                el.dataset.animated = 'true';
                el.style.opacity = '0';
                el.style.transform = 'translateY(8px)';

                setTimeout(() => {
                    el.style.transition = 'opacity 0.3s ease, transform 0.3s ease';
                    el.style.opacity = '1';
                    el.style.transform = 'translateY(0)';
                }, index * 30);
            }
        });
    }

    // ═══════════════════════════════════════════════════════════════
    // MUTATION OBSERVER - Watch for new content
    // ═══════════════════════════════════════════════════════════════

    function processNewContent(container) {
        // Enhance code blocks
        container.querySelectorAll('.text-message pre:not([data-enhanced])').forEach(enhanceCodeBlock);

        // Enhance blockquotes
        container.querySelectorAll('.text-message blockquote:not([data-callout-enhanced])').forEach(enhanceBlockquote);

        // Re-run Prism highlighting if available
        if (window.Prism) {
            container.querySelectorAll('pre code:not(.prism-highlighted)').forEach(code => {
                code.classList.add('prism-highlighted');
                Prism.highlightElement(code);
            });
        }
    }

    // Initialize observer
    const observer = new MutationObserver((mutations) => {
        mutations.forEach(mutation => {
            mutation.addedNodes.forEach(node => {
                if (node.nodeType === Node.ELEMENT_NODE) {
                    if (node.classList && node.classList.contains('message')) {
                        processNewContent(node);
                    } else if (node.querySelector) {
                        const messages = node.querySelectorAll('.message');
                        messages.forEach(processNewContent);
                    }
                }
            });
        });
    });

    // Start observing
    function init() {
        const chatContainer = document.getElementById('chat-display') || document.body;
        observer.observe(chatContainer, {
            childList: true,
            subtree: true
        });

        // Process existing content
        processNewContent(document.body);
    }

    // Initialize when DOM is ready
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }

    // Expose for manual use
    window.XelaPremium = {
        enhanceCodeBlock,
        enhanceBlockquote,
        processNewContent
    };

})();
