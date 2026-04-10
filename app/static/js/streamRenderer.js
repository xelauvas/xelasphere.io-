/**
 * StreamRenderer — Progressive markdown streaming renderer.
 *
 * Renders SSE tokens directly to the DOM as they arrive, using:
 * - Safe boundary scanner to avoid splitting markdown mid-construct
 * - morphdom for minimal DOM patching (preserves event listeners)
 * - CSS-only inline cursor (no JS positioning)
 * - requestAnimationFrame batching for 60fps rendering
 *
 * Usage:
 *   const renderer = new StreamRenderer(containerEl);
 *   renderer.append(token);  // called per SSE chunk
 *   renderer.finalize();     // called on done
 */
(function () {
    'use strict';

    // ---------------------------------------------------------------------------
    // Safe Boundary Scanner
    // ---------------------------------------------------------------------------

    /**
     * Find the last position in `text` where all markdown constructs
     * are closed/complete. Returns the safe length to render.
     *
     * Rules:
     *  1. Unclosed ** or * → back up to before opener
     *  2. Unclosed ` → back up to before backtick
     *  3. Unclosed ``` → render nothing past fence (500-char fallback)
     *  4. Unclosed [ or ]( → back up to before [
     *  5. Incomplete line (no trailing \n) → back up to last \n
     *  6. Everything else → safe at current position
     *
     * Malformed fallback: if no safe point found within 200 chars
     * of the end, fall back to last \n.
     */
    function findSafePoint(text) {
        if (!text) return 0;

        var len = text.length;
        var openStrong = 0;   // **
        var openEm = 0;       // *
        var openCode = false;  // `
        var openFence = false; // ```
        var openLink = 0;      // [...]
        var lastSafe = 0;
        var lastNewline = 0;
        var fenceStart = -1;

        for (var i = 0; i < len; i++) {
            var ch = text[i];
            var next = i + 1 < len ? text[i + 1] : '';
            var next2 = i + 2 < len ? text[i + 2] : '';

            if (ch === '\n') {
                lastNewline = i + 1;
            }

            // Code fence: ``` (typically at start of line)
            if (ch === '`' && next === '`' && next2 === '`') {
                if (!openFence) {
                    openFence = true;
                    fenceStart = i;
                    // Skip to end of line (fence may have language tag)
                    while (i < len && text[i] !== '\n') i++;
                    continue;
                } else {
                    openFence = false;
                    fenceStart = -1;
                    i += 2; // skip past ```
                    // Track safe point after closing fence
                    if (!openStrong && !openEm && !openCode && !openLink) {
                        lastSafe = i + 1;
                    }
                    continue;
                }
            }

            // Inside code fence — don't track other constructs
            if (openFence) continue;

            // Inline code: `
            if (ch === '`') {
                openCode = !openCode;
                if (!openCode && !openStrong && !openEm && !openFence && !openLink) {
                    lastSafe = i + 1;
                }
                continue;
            }

            // Inside inline code — don't track emphasis/links
            if (openCode) continue;

            // Strong: **
            if (ch === '*' && next === '*') {
                openStrong = openStrong ? 0 : 1;
                i++; // skip second *
                if (!openStrong && !openEm && !openCode && !openFence && !openLink) {
                    lastSafe = i + 1;
                }
                continue;
            }

            // Emphasis: single *
            if (ch === '*' && next !== '*') {
                openEm = openEm ? 0 : 1;
                if (!openEm && !openStrong && !openCode && !openFence && !openLink) {
                    lastSafe = i + 1;
                }
                continue;
            }

            // Link: [
            if (ch === '[') {
                openLink++;
                continue;
            }
            if (ch === ']') {
                if (openLink > 0) {
                    if (next === '(') continue;
                    openLink--;
                }
                continue;
            }
            if (ch === ')' && openLink > 0) {
                openLink--;
                if (!openLink && !openStrong && !openEm && !openCode && !openFence) {
                    lastSafe = i + 1;
                }
                continue;
            }

            // Track safe points — all constructs closed (regular characters)
            if (!openStrong && !openEm && !openCode && !openFence && !openLink) {
                lastSafe = i + 1;
            }
        }

        // If inside open fence — apply 500-char fallback
        if (openFence) {
            var fenceContentLen = len - fenceStart;
            if (fenceContentLen > 500) {
                return len;
            }
            // Don't render past fence start
            return fenceStart >= 0 ? fenceStart : 0;
        }

        // If all constructs closed, safe at end
        if (!openStrong && !openEm && !openCode && !openLink) {
            return len;
        }

        // Malformed fallback: if lastSafe is more than 200 chars behind,
        // use lastNewline instead
        if (len - lastSafe > 200 && lastNewline > lastSafe) {
            return lastNewline;
        }

        return lastSafe || lastNewline || 0;
    }

    // ---------------------------------------------------------------------------
    // StreamRenderer
    // ---------------------------------------------------------------------------

    var CURSOR_HTML = '<span class="stream-cursor"></span>';

    function StreamRenderer(container) {
        this.container = container;
        this.rawBuffer = '';
        this.renderedSafe = '';    // last safeBuffer that was rendered
        this.previousHTML = '';     // last HTML applied to DOM
        this.rafId = null;
        this.destroyed = false;
        this.finalized = false;

        // Create the standard message wrapper structure so CSS applies correctly
        // .message-content > .text-message-container > .text-message (render target)
        var wrapper = document.createElement('div');
        wrapper.className = 'text-message-container';
        var inner = document.createElement('div');
        inner.className = 'text-message';
        wrapper.appendChild(inner);
        this.container.appendChild(wrapper);
        this.renderTarget = inner;

        // Bind the RAF callback
        this._tick = this._tick.bind(this);
    }

    StreamRenderer.prototype.append = function (text) {
        if (this.destroyed || this.finalized) return;
        this.rawBuffer += text;
        // Schedule a render if not already scheduled
        if (!this.rafId) {
            this.rafId = requestAnimationFrame(this._tick);
        }
    };

    StreamRenderer.prototype._tick = function () {
        this.rafId = null;
        if (this.destroyed || this.finalized) return;

        var safeLen = findSafePoint(this.rawBuffer);
        var safeBuffer = this.rawBuffer.substring(0, safeLen);

        // Nothing new to render
        if (safeBuffer === this.renderedSafe) return;
        this.renderedSafe = safeBuffer;

        this._render(safeBuffer, false);
    };

    StreamRenderer.prototype._render = function (text, isFinal) {
        var target = this.renderTarget || this.container;
        if (!target) return;

        // Strip ACTION protocol lines (e.g., "ACTION: chat", "ACTION: web_search")
        text = text.replace(/^ACTION:\s*\w+.*$/gm, '').replace(/^\s*\n/gm, '');

        // Parse markdown → HTML
        var rawHTML = '';
        if (typeof window.marked !== 'undefined') {
            rawHTML = window.marked.parse(text);
        } else if (typeof window.markdownit !== 'undefined') {
            rawHTML = window.md ? window.md.render(text) : text;
        } else {
            rawHTML = text;
        }

        // Sanitize
        if (typeof DOMPurify !== 'undefined') {
            rawHTML = DOMPurify.sanitize(rawHTML);
        }

        // Add cursor if not final
        var html = isFinal ? rawHTML : rawHTML + CURSOR_HTML;

        // Skip if nothing changed
        if (html === this.previousHTML) return;
        this.previousHTML = html;

        // Use morphdom for minimal DOM patching (preserves event listeners)
        if (typeof morphdom !== 'undefined') {
            var wrapper = document.createElement('div');
            wrapper.innerHTML = html;
            try {
                morphdom(target, wrapper, {
                    childrenOnly: true,
                    onBeforeElUpdated: function (fromEl, toEl) {
                        if (fromEl.classList) {
                            if (fromEl.classList.contains('citation-pill')) return false;
                            if (fromEl.classList.contains('sources-btn')) return false;
                        }
                        return true;
                    }
                });
            } catch (e) {
                target.innerHTML = html;
            }
        } else {
            target.innerHTML = html;
        }
    };

    StreamRenderer.prototype.finalize = function () {
        if (this.destroyed || this.finalized) return;
        this.finalized = true;

        // Cancel pending RAF
        if (this.rafId) {
            cancelAnimationFrame(this.rafId);
            this.rafId = null;
        }

        // Strip ACTION protocol lines
        var cleanText = this.rawBuffer.replace(/^ACTION:\s*\w+.*$/gm, '').replace(/^\s*\n/gm, '');

        // Final render — use innerHTML for a clean slate (no morphdom artifacts)
        var target = this.renderTarget || this.container;
        if (!target) return;

        var rawHTML = '';
        if (typeof window.marked !== 'undefined') {
            rawHTML = window.marked.parse(cleanText);
        } else if (window.md) {
            rawHTML = window.md.render(cleanText);
        } else {
            rawHTML = cleanText;
        }
        if (typeof DOMPurify !== 'undefined') {
            rawHTML = DOMPurify.sanitize(rawHTML);
        }
        target.innerHTML = rawHTML;

        // Post-render transforms
        if (typeof renderMathInMessage === 'function') {
            renderMathInMessage(target);
        }
        if (typeof Prism !== 'undefined') {
            Prism.highlightAllUnder(target);
        }
    };

    StreamRenderer.prototype.destroy = function () {
        this.destroyed = true;
        if (this.rafId) {
            cancelAnimationFrame(this.rafId);
            this.rafId = null;
        }
        // Remove cursor if present
        var cursor = this.container && this.container.querySelector('.stream-cursor');
        if (cursor) cursor.remove();
    };

    // ---------------------------------------------------------------------------
    // CSS injection (stream-cursor styles)
    // ---------------------------------------------------------------------------

    (function injectCursorCSS() {
        if (document.getElementById('stream-cursor-css')) return;
        var style = document.createElement('style');
        style.id = 'stream-cursor-css';
        style.textContent =
            '.stream-cursor {\n' +
            '    display: inline-block;\n' +
            '    width: 12px;\n' +
            '    height: 12px;\n' +
            '    border-radius: 50%;\n' +
            '    background: rgba(255, 255, 255, 0.85);\n' +
            '    margin-left: 4px;\n' +
            '    vertical-align: middle;\n' +
            '    animation: stream-pulse 1.5s ease-in-out infinite;\n' +
            '}\n' +
            '@keyframes stream-pulse {\n' +
            '    0%, 100% { transform: scale(1); opacity: 0.4; }\n' +
            '    50% { transform: scale(1.3); opacity: 1; }\n' +
            '}\n';
        document.head.appendChild(style);
    })();

    // ---------------------------------------------------------------------------
    // Export
    // ---------------------------------------------------------------------------

    window.StreamRenderer = StreamRenderer;

    // Expose scanner for testing
    window.StreamRenderer._findSafePoint = findSafePoint;

})();
