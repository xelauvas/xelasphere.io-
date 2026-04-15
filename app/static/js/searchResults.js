/* searchResults.js — ChatGPT-style search result rendering for XelaSphere
   Exposes window.SearchResults (IIFE)

   render_hint modes:
     news_cards    — news article cards carousel above AI text
     image_grid    — image carousel above AI text
     video_primary — video embed above AI text, remaining as cards
     text_only     — just AI text with citation pills + sources button
*/
(function () {
    'use strict';

    // Per-message state storage keyed by container element
    const _state = new WeakMap();

    function _getState(container) {
        if (!_state.has(container)) {
            _state.set(container, {
                bannerEl: null,
                webResults: [],
                videoResults: [],
                imageResults: [],
                fetchedUrls: new Set()
            });
        }
        return _state.get(container);
    }

    /* -------------------------------------------------------
       XSS-safe escaping via DOM textContent
    ------------------------------------------------------- */
    function _esc(str) {
        if (str == null) return '';
        const d = document.createElement('span');
        d.textContent = String(str);
        return d.innerHTML;
    }

    function _ensureUrl(url) {
        if (!url || url === '#') return '#';
        if (/^https?:\/\//i.test(url)) return url;
        return 'https://' + url;
    }

    /* -------------------------------------------------------
       Premium Reader — Pocket/Instapaper-style overlay
    ------------------------------------------------------- */

    // Validate an outbound href — reject javascript:, data:, vbscript:
    // so markdown links in article content can't inject scripts.
    function _safeHref(href) {
        if (!href) return '#';
        var trimmed = String(href).trim();
        // Allow protocol-relative, http(s), mailto, tel, and relative URLs.
        if (/^\s*(javascript|data|vbscript|file):/i.test(trimmed)) return '#';
        return trimmed;
    }

    function _openLink(url) {
        if (window.innerWidth > 640) {
            window.open(_ensureUrl(url), '_blank');
            return;
        }

        url = _ensureUrl(url);
        var existing = document.getElementById('inapp-browser');
        if (existing) existing.remove();

        var domain = '';
        var faviconUrl = '';
        try {
            var parsed = new URL(url);
            domain = parsed.hostname.replace(/^www\./, '');
            faviconUrl = 'https://www.google.com/s2/favicons?domain=' + domain + '&sz=32';
        } catch (e) { domain = url; }

        // Overlay
        var overlay = document.createElement('div');
        overlay.id = 'inapp-browser';
        overlay.style.cssText = 'position:fixed;inset:0;z-index:10000;background:#0d0d0d;display:flex;flex-direction:column;';

        // Progress bar
        var progress = document.createElement('div');
        progress.style.cssText = 'height:3px;background:#7eb8ff;width:0%;transition:width 0.1s;flex-shrink:0;';

        // Header
        var header = document.createElement('div');
        header.style.cssText = 'display:flex;align-items:center;gap:10px;padding:12px 16px;background:#0d0d0d;flex-shrink:0;padding-top:calc(12px + env(safe-area-inset-top,0px));border-bottom:1px solid rgba(255,255,255,0.08);';

        var closeBtn = document.createElement('button');
        closeBtn.setAttribute('aria-label', 'Close reader');
        closeBtn.innerHTML = '&#10005;';
        closeBtn.style.cssText = 'width:34px;height:34px;border-radius:50%;border:none;background:rgba(255,255,255,0.1);color:rgba(255,255,255,0.8);font-size:14px;cursor:pointer;flex-shrink:0;display:flex;align-items:center;justify-content:center;';
        closeBtn.onclick = function () { _closeReader(); };

        var favEl = document.createElement('img');
        favEl.src = faviconUrl;
        favEl.alt = '';
        favEl.style.cssText = 'width:18px;height:18px;border-radius:4px;flex-shrink:0;';
        favEl.onerror = function () { this.style.display = 'none'; };

        var domainEl = document.createElement('span');
        domainEl.textContent = domain;
        domainEl.style.cssText = 'flex:1;font-size:0.78rem;color:rgba(255,255,255,0.5);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;';

        var aaBtn = document.createElement('button');
        aaBtn.setAttribute('aria-label', 'Change text size');
        aaBtn.textContent = 'Aa';
        aaBtn.style.cssText = 'border:none;background:rgba(255,255,255,0.08);color:rgba(255,255,255,0.7);font-size:0.8rem;padding:6px 10px;border-radius:6px;cursor:pointer;flex-shrink:0;';

        var extBtn = document.createElement('button');
        extBtn.setAttribute('aria-label', 'Open in browser');
        extBtn.innerHTML = '&#8599;';
        extBtn.style.cssText = 'width:34px;height:34px;border-radius:50%;border:none;background:rgba(255,255,255,0.08);color:rgba(255,255,255,0.7);font-size:16px;cursor:pointer;flex-shrink:0;display:flex;align-items:center;justify-content:center;';
        extBtn.onclick = function () { window.open(url, '_blank'); };

        header.appendChild(closeBtn);
        header.appendChild(favEl);
        header.appendChild(domainEl);
        header.appendChild(aaBtn);
        header.appendChild(extBtn);

        // Helpers attached to overlay so they can be removed on close.
        function _closeReader() {
            document.removeEventListener('keydown', _onKey);
            overlay.remove();
        }
        function _onKey(e) {
            if (e.key === 'Escape') { e.preventDefault(); _closeReader(); }
        }
        document.addEventListener('keydown', _onKey);

        // Scrollable content (loading spinner is injected in _fetchArticle)
        var content = document.createElement('div');
        content.style.cssText = 'flex:1;overflow-y:auto;-webkit-overflow-scrolling:touch;';

        overlay.appendChild(progress);
        overlay.appendChild(header);
        overlay.appendChild(content);
        document.body.appendChild(overlay);

        // Scroll progress bar
        content.addEventListener('scroll', function () {
            var max = content.scrollHeight - content.clientHeight;
            if (max > 0) progress.style.width = Math.min(100, content.scrollTop / max * 100) + '%';
        });

        // Font size toggle (persisted in sessionStorage)
        var fontSizes = ['0.9rem', '1.05rem', '1.2rem'];
        var sizeIndex = parseInt(sessionStorage.getItem('reader-font-size') || '1', 10);
        if (sizeIndex < 0 || sizeIndex > 2) sizeIndex = 1;
        var readerBody = null;
        aaBtn.onclick = function () {
            sizeIndex = (sizeIndex + 1) % 3;
            sessionStorage.setItem('reader-font-size', sizeIndex);
            if (readerBody) readerBody.style.fontSize = fontSizes[sizeIndex];
        };

        // Error UI builder (no inline onclick — uses event listeners).
        // Shows both a retry button and an open-in-browser fallback.
        function _showError(msg) {
            content.innerHTML = '';
            var wrap = document.createElement('div');
            wrap.style.cssText = 'display:flex;flex-direction:column;align-items:center;justify-content:center;height:100%;gap:16px;padding:24px;';
            var icon = document.createElement('div');
            icon.style.fontSize = '2.5rem';
            icon.innerHTML = '&#128196;';
            var label = document.createElement('div');
            label.style.cssText = 'color:rgba(255,255,255,0.6);font-size:0.95rem;text-align:center;';
            label.textContent = msg;

            var btnRow = document.createElement('div');
            btnRow.style.cssText = 'display:flex;gap:10px;flex-wrap:wrap;justify-content:center;';

            var retryBtn = document.createElement('button');
            retryBtn.style.cssText = 'background:rgba(255,255,255,0.08);color:rgba(255,255,255,0.9);border:1px solid rgba(255,255,255,0.12);padding:12px 20px;border-radius:10px;font-size:0.9rem;font-weight:500;cursor:pointer;';
            retryBtn.textContent = 'Try again';
            retryBtn.onclick = function () { _fetchArticle(); };

            var btn = document.createElement('button');
            btn.style.cssText = 'background:#7eb8ff;color:#000;border:none;padding:12px 24px;border-radius:10px;font-size:0.9rem;font-weight:600;cursor:pointer;';
            btn.textContent = 'Open in Browser \u2192';
            btn.onclick = function () { window.open(url, '_blank'); };

            btnRow.appendChild(retryBtn);
            btnRow.appendChild(btn);
            wrap.appendChild(icon);
            wrap.appendChild(label);
            wrap.appendChild(btnRow);
            content.appendChild(wrap);
        }

        // Loading state (re-usable so retry can restore it).
        var loadingHtml = '<div style="display:flex;flex-direction:column;align-items:center;justify-content:center;height:100%;gap:16px;">'
            + '<div style="width:36px;height:36px;border:2px solid rgba(255,255,255,0.1);border-top-color:rgba(255,255,255,0.6);border-radius:50%;animation:reader-spin 0.8s linear infinite;"></div>'
            + '<span style="color:rgba(255,255,255,0.4);font-size:0.85rem;">Fetching article...</span>'
            + '</div>';

        function _fetchArticle() {
            content.innerHTML = loadingHtml;
            var csrfMeta = document.querySelector('meta[name="csrf-token"]');
            fetch('/reader', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': csrfMeta ? csrfMeta.getAttribute('content') : ''
                },
                body: JSON.stringify({ url: url })
            })
            .then(function (r) {
                if (!r.ok) throw new Error('HTTP ' + r.status);
                return r.json();
            })
            .then(function (data) {
                if (data.error || !data.content) {
                    _showError(data.error || "Couldn't load this article");
                    return;
                }

                var title = (data.title || '').trim();

                // Strip markdown syntax for accurate word count
                var plainText = data.content.replace(/[#*\[\]()>_`~!|-]/g, '');
                var wordCount = plainText.split(/\s+/).filter(Boolean).length;
                var readTime = Math.max(1, Math.ceil(wordCount / 200));
                var articleHtml = '';

                // Hero image
                if (data.image) {
                    articleHtml += '<div style="position:relative;width:100%;height:240px;overflow:hidden;flex-shrink:0;">'
                        + '<img src="' + _escHtml(data.image) + '" style="width:100%;height:100%;object-fit:cover;" onerror="this.parentNode.style.display=\'none\'">'
                        + '<div style="position:absolute;inset:0;background:linear-gradient(to bottom,transparent 40%,#0d0d0d);"></div>'
                        + '</div>';
                }

                // Title + meta block
                articleHtml += '<div style="padding:' + (data.image ? '4px' : '28px') + ' 20px 16px;max-width:680px;margin:0 auto;">';

                // Article title (new — was missing before)
                if (title) {
                    articleHtml += '<h1 style="font-family:-apple-system,BlinkMacSystemFont,\'Segoe UI\',sans-serif;font-size:1.55rem;font-weight:700;line-height:1.25;margin:0 0 14px;color:rgba(255,255,255,0.95);">' + _escHtml(title) + '</h1>';
                }

                articleHtml += '<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:8px;">'
                    + '<div style="display:flex;align-items:center;gap:6px;min-width:0;">'
                    + '<img src="' + _escHtml(faviconUrl) + '" style="width:14px;height:14px;border-radius:3px;flex-shrink:0;" onerror="this.style.display=\'none\'" alt="">'
                    + '<span style="font-size:0.78rem;color:rgba(255,255,255,0.45);font-weight:500;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">' + _escHtml(domain) + '</span>'
                    + '</div>'
                    + '<span style="font-size:0.75rem;color:rgba(255,255,255,0.35);flex-shrink:0;margin-left:12px;">' + readTime + ' min read</span>'
                    + '</div>'
                    + '<div style="height:1px;background:rgba(255,255,255,0.07);"></div>'
                    + '</div>';

                // Article body. Padding bottom covers iOS home indicator.
                articleHtml += '<div class="reader-body" style="font-size:' + fontSizes[sizeIndex] + ';padding-bottom:calc(80px + env(safe-area-inset-bottom, 0px));">'
                    + _markdownToHtml(data.content) + '</div>';

                content.innerHTML = articleHtml;
                readerBody = content.querySelector('.reader-body');
            })
            .catch(function () {
                _showError('Failed to load article');
            });
        }

        _fetchArticle();
    }

    function _escHtml(s) {
        var d = document.createElement('div');
        d.appendChild(document.createTextNode(s));
        return d.innerHTML;
    }

    function _markdownToHtml(md) {
        var lines = md.split('\n');
        var html = '';
        var inList = false;
        var listTag = 'ul';
        var inCodeBlock = false;
        var codeLines = [];

        for (var i = 0; i < lines.length; i++) {
            var line = lines[i];

            // Code block fences
            if (/^```/.test(line)) {
                if (inCodeBlock) {
                    html += '<pre><code>' + codeLines.join('\n') + '</code></pre>';
                    codeLines = [];
                    inCodeBlock = false;
                } else {
                    if (inList) { html += '</' + listTag + '>'; inList = false; }
                    inCodeBlock = true;
                }
                continue;
            }
            if (inCodeBlock) {
                codeLines.push(line.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;'));
                continue;
            }

            // Horizontal rule
            if (/^(\s*[-*_]\s*){3,}$/.test(line)) {
                if (inList) { html += '</' + listTag + '>'; inList = false; }
                html += '<hr>';
                continue;
            }

            // Headers
            var hMatch = line.match(/^(#{1,3})\s+(.*)/);
            if (hMatch) {
                if (inList) { html += '</' + listTag + '>'; inList = false; }
                var lvl = hMatch[1].length;
                html += '<h' + lvl + '>' + _inlineMd(hMatch[2]) + '</h' + lvl + '>';
                continue;
            }

            // Blockquote
            if (/^>\s*/.test(line)) {
                if (inList) { html += '</' + listTag + '>'; inList = false; }
                html += '<blockquote>' + _inlineMd(line.replace(/^>\s*/, '')) + '</blockquote>';
                continue;
            }

            // Ordered list
            if (/^\s*\d+\.\s+/.test(line)) {
                if (!inList || listTag !== 'ol') {
                    if (inList) html += '</' + listTag + '>';
                    html += '<ol>'; inList = true; listTag = 'ol';
                }
                html += '<li>' + _inlineMd(line.replace(/^\s*\d+\.\s+/, '')) + '</li>';
                continue;
            }

            // Unordered list
            if (/^\s*[-*]\s+/.test(line)) {
                if (!inList || listTag !== 'ul') {
                    if (inList) html += '</' + listTag + '>';
                    html += '<ul>'; inList = true; listTag = 'ul';
                }
                html += '<li>' + _inlineMd(line.replace(/^\s*[-*]\s+/, '')) + '</li>';
                continue;
            }

            if (inList) { html += '</' + listTag + '>'; inList = false; }

            // Blank line
            if (!line.trim()) continue;

            // Paragraph
            html += '<p>' + _inlineMd(line) + '</p>';
        }
        if (inCodeBlock && codeLines.length) {
            html += '<pre><code>' + codeLines.join('\n') + '</code></pre>';
        }
        if (inList) html += '</' + listTag + '>';
        return html;
    }

    function _inlineMd(s) {
        // Extract inline images to placeholders before escaping so the
        // URL isn't mangled by HTML escaping, then re-insert as <img>.
        var imgs = [];
        s = s.replace(/!\[([^\]]*)\]\(([^)\s]+)(?:\s+"[^"]*")?\)/g, function (m, alt, src) {
            var safeSrc = _safeHref(src);
            var i = imgs.length;
            imgs.push({ alt: alt, src: safeSrc });
            return '\0IMG' + i + '\0';
        });

        // Extract inline links to placeholders for the same reason.
        var links = [];
        s = s.replace(/\[([^\]]+)\]\(([^)\s]+)(?:\s+"[^"]*")?\)/g, function (m, text, href) {
            var i = links.length;
            links.push({ text: text, href: _safeHref(href) });
            return '\0LNK' + i + '\0';
        });

        // Escape HTML on the remaining text.
        s = s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');

        // Inline code, bold, italic, strikethrough.
        s = s.replace(/`([^`]+)`/g, '<code>$1</code>');
        s = s.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
        s = s.replace(/\*(.+?)\*/g, '<em>$1</em>');
        s = s.replace(/~~(.+?)~~/g, '<del>$1</del>');

        // Re-insert placeholders as safe <a>/<img> tags. Text content
        // of links is escaped; alt text of images is escaped.
        s = s.replace(/\0LNK(\d+)\0/g, function (_, i) {
            var l = links[+i];
            var text = _escHtml(l.text);
            return '<a href="' + _escHtml(l.href) + '" target="_blank" rel="noopener noreferrer">' + text + '</a>';
        });
        s = s.replace(/\0IMG(\d+)\0/g, function (_, i) {
            var im = imgs[+i];
            return '<img src="' + _escHtml(im.src) + '" alt="' + _escHtml(im.alt) + '" loading="lazy" onerror="this.style.display=\'none\'">';
        });
        return s;
    }

    function _getDomain(r) {
        if (r.source_domain) return r.source_domain.replace(/^www\./, '');
        try { return new URL(r.url).hostname.replace(/^www\./, ''); } catch (e) { return ''; }
    }

    function _getRootDomain(domain) {
        // Strip CDN subdomains: media.istockphoto.com → istockphoto.com
        if (!domain) return domain;
        var parts = domain.split('.');
        // Handle two-part TLDs (co.uk, com.au)
        if (parts.length > 2 && parts[parts.length - 2].length <= 3 && parts[parts.length - 1].length <= 2) {
            return parts.slice(-3).join('.');
        }
        return parts.length > 2 ? parts.slice(-2).join('.') : domain;
    }

    function _getFavicon(r) {
        if (r.favicon) return r.favicon;
        var domain = _getRootDomain(_getDomain(r));
        return domain ? 'https://www.google.com/s2/favicons?domain=' + encodeURIComponent(domain) + '&sz=16' : '';
    }

    /* -------------------------------------------------------
       createSearchBanner — ChatGPT-style shimmer with icon,
       label, query, and live site-count / favicon strip.
    ------------------------------------------------------- */
    function createSearchBanner(query, searchType) {
        const banner = document.createElement('div');
        banner.className = 'search-banner';

        // Top row: shimmer label + dots. No icon (clean / premium look).
        const top = document.createElement('div');
        top.className = 'search-banner__top';

        const searchTypeLabels = {
            'news': 'Searching news',
            'images': 'Searching images',
            'video': 'Searching videos',
            'general': 'Searching the web',
        };
        const label = document.createElement('span');
        label.className = 'search-banner__label';
        label.textContent = searchTypeLabels[searchType] || 'Searching the web';
        top.appendChild(label);

        const dots = document.createElement('span');
        dots.className = 'search-banner__dots';
        dots.innerHTML = '<span></span><span></span><span></span>';
        top.appendChild(dots);

        banner.appendChild(top);

        if (query) {
            const queryEl = document.createElement('div');
            queryEl.className = 'search-banner__query';
            queryEl.textContent = '"' + query + '"';
            banner.appendChild(queryEl);
        }

        // Live progress row — populated by handleSearchEvent as results arrive.
        const progress = document.createElement('div');
        progress.className = 'search-banner__progress';

        const count = document.createElement('span');
        count.className = 'search-banner__count';
        count.textContent = 'Finding sources…';
        progress.appendChild(count);

        const favStrip = document.createElement('span');
        favStrip.className = 'search-banner__favicons';
        progress.appendChild(favStrip);

        banner.appendChild(progress);

        return banner;
    }

    /* -------------------------------------------------------
       createNewsCarousel — news_cards render_hint
    ------------------------------------------------------- */
    function createNewsCarousel(webResults) {
        if (!webResults || !webResults.length) return null;

        const wrapper = document.createElement('div');
        wrapper.className = 'search-news-carousel';

        const track = document.createElement('div');
        track.className = 'search-news-cards';

        webResults.forEach(function (r) {
            const card = document.createElement('a');
            card.className = 'search-news-card';
            card.href = _ensureUrl(r.url);
            card.target = '_blank';
            card.rel = 'noopener noreferrer';
            if (window.innerWidth <= 640) {
                card.addEventListener('click', function (e) { e.preventDefault(); _openLink(r.url); });
            }

            // Image or placeholder
            if (r.thumbnail) {
                var img = document.createElement('img');
                img.className = 'search-news-card__image';
                img.src = r.thumbnail;
                img.alt = r.title || '';
                img.loading = 'lazy';
                img.onerror = function () {
                    var ph = this.nextElementSibling;
                    if (ph) ph.style.display = 'flex';
                    this.remove();
                };
                card.appendChild(img);
            }

            var placeholder = document.createElement('div');
            placeholder.className = 'search-news-card__image search-news-card__image--placeholder';
            placeholder.style.display = r.thumbnail ? 'none' : 'flex';
            placeholder.textContent = '\uD83D\uDCF0'; // newspaper emoji
            card.appendChild(placeholder);

            // Body
            var body = document.createElement('div');
            body.className = 'search-news-card__body';

            var publisher = document.createElement('div');
            publisher.className = 'search-news-card__publisher';

            var favicon = _getFavicon(r);
            if (favicon) {
                var favImg = document.createElement('img');
                favImg.className = 'search-news-card__favicon';
                favImg.src = favicon;
                favImg.alt = '';
                favImg.onerror = function () { this.style.display = 'none'; };
                publisher.appendChild(favImg);
            }

            var pubName = document.createElement('span');
            pubName.className = 'search-news-card__publisher-name';
            pubName.textContent = _getDomain(r);
            publisher.appendChild(pubName);
            body.appendChild(publisher);

            var title = document.createElement('div');
            title.className = 'search-news-card__title';
            title.textContent = r.title || '';
            body.appendChild(title);

            card.appendChild(body);
            track.appendChild(card);
        });

        // Arrow buttons
        var leftArrow = document.createElement('button');
        leftArrow.className = 'search-carousel-arrow search-carousel-arrow--left search-carousel-arrow--hidden';
        leftArrow.innerHTML = '&#8249;';
        leftArrow.setAttribute('aria-label', 'Previous');

        var rightArrow = document.createElement('button');
        rightArrow.className = 'search-carousel-arrow search-carousel-arrow--right';
        rightArrow.innerHTML = '&#8250;';
        rightArrow.setAttribute('aria-label', 'Next');

        var currentIndex = 0;
        var VISIBLE = 3;

        function getCardWidth() {
            var firstCard = track.children[0];
            if (!firstCard) return 200;
            return firstCard.offsetWidth + 12; // 12px gap
        }

        function updateArrows() {
            leftArrow.classList.toggle('search-carousel-arrow--hidden', currentIndex === 0);
            rightArrow.classList.toggle('search-carousel-arrow--hidden',
                currentIndex >= webResults.length - VISIBLE);
        }

        function scrollTo(index) {
            currentIndex = Math.max(0, Math.min(index, webResults.length - VISIBLE));
            var cardWidth = getCardWidth();
            track.style.transform = 'translateX(-' + (currentIndex * cardWidth) + 'px)';
            track.style.transition = 'transform 0.35s cubic-bezier(0.4, 0, 0.2, 1)';
            updateArrows();
        }

        leftArrow.onclick = function () { scrollTo(currentIndex - VISIBLE); };
        rightArrow.onclick = function () { scrollTo(currentIndex + VISIBLE); };

        if (webResults.length <= VISIBLE) {
            rightArrow.classList.add('search-carousel-arrow--hidden');
        }

        wrapper.appendChild(leftArrow);
        wrapper.appendChild(track);
        wrapper.appendChild(rightArrow);

        // Mobile: compact 3-across swipe carousel
        if (window.innerWidth <= 640) {
            wrapper.style.cssText = 'overflow: visible; position: relative; margin: 0 0 16px 0;';
            track.style.cssText = 'display: flex; overflow-x: auto; scroll-snap-type: x mandatory; -webkit-overflow-scrolling: touch; gap: 6px; padding-bottom: 4px;';
            leftArrow.style.display = 'none';
            rightArrow.style.display = 'none';

            Array.from(track.children).forEach(function (card) {
                card.style.flex = '0 0 30%';
                card.style.minWidth = '0';
                card.style.borderRadius = '8px';
                card.style.scrollSnapAlign = 'start';
                card.style.overflow = 'hidden';
                card.style.border = '1px solid rgba(255,255,255,0.08)';
                card.style.background = 'var(--bg-hover, rgba(255,255,255,0.05))';
                card.style.textDecoration = 'none';
                card.style.color = 'inherit';
                card.style.display = 'flex';
                card.style.flexDirection = 'column';

                var img = card.querySelector('img, .search-news-card__image');
                if (img) {
                    img.style.height = '80px';
                    img.style.width = '100%';
                    img.style.objectFit = 'cover';
                    img.style.aspectRatio = 'unset';
                }

                var publisher = card.querySelector('.search-news-card__publisher');
                if (publisher) publisher.style.display = 'none';

                var title = card.querySelector('.search-news-card__title');
                if (title) {
                    title.style.fontSize = '0.65rem';
                    title.style.webkitLineClamp = '3';
                    title.style.display = '-webkit-box';
                    title.style.webkitBoxOrient = 'vertical';
                    title.style.overflow = 'hidden';
                    title.style.padding = '4px 6px';
                }

                var body = card.querySelector('.search-news-card__body');
                if (body) {
                    body.style.padding = '0';
                }
            });
        }

        return wrapper;
    }

    /* -------------------------------------------------------
       createImageCarousel — image_grid render_hint
    ------------------------------------------------------- */
    function createImageCarousel(images) {
        if (!images || !images.length) return null;

        const wrap = document.createElement('div');
        wrap.className = 'search-images';

        images.forEach(function (img) {
            const thumbUrl = img.thumbnail || img.url;
            if (!thumbUrl) return;

            const item = document.createElement('div');
            item.className = 'search-images__item';
            item.title = img.title || '';

            const el = document.createElement('img');
            el.src = thumbUrl;
            el.alt = img.title || '';
            el.loading = 'lazy';
            el.onerror = function () { item.style.display = 'none'; };

            item.appendChild(el);
            item.addEventListener('click', function () {
                openLightbox(thumbUrl, img.title || '', img.url);
            });

            wrap.appendChild(item);
        });

        // Mobile: touch-swipe carousel via translateX (overflow-x blocked by parent)
        if (window.innerWidth <= 640) {
            wrap.style.cssText = 'position: relative; overflow: hidden; padding: 4px 0 8px; margin-bottom: 12px; width: 100%; max-width: 100%;';
            var track = document.createElement('div');
            track.style.cssText = 'display: flex; gap: 8px; transition: transform 0.3s ease; will-change: transform;';
            while (wrap.firstChild) track.appendChild(wrap.firstChild);
            wrap.appendChild(track);

            Array.from(track.children).forEach(function (item) {
                item.style.cssText = 'flex: 0 0 120px; height: 85px; border-radius: 8px; overflow: hidden; cursor: pointer; border: 1px solid rgba(255,255,255,0.08); position: relative;';
                item.style.background = 'var(--bg-hover, rgba(255,255,255,0.05))';
            });

            // Touch swipe — calculate maxOffset lazily (element not in DOM yet)
            var startX = 0, currentX = 0, offsetX = 0;
            var maxOffset = 0;

            function calcMaxOffset() {
                // Count only visible items to calculate real content width
                var visibleCount = 0;
                Array.from(track.children).forEach(function (c) {
                    if (c.offsetWidth > 0) visibleCount++;
                });
                if (visibleCount === 0) return 0;
                var contentWidth = (visibleCount * 120) + ((visibleCount - 1) * 8);
                var visible = wrap.clientWidth;
                return contentWidth > visible ? -(contentWidth - visible) : 0;
            }

            track.addEventListener('touchstart', function (e) {
                maxOffset = calcMaxOffset();
                startX = e.touches[0].clientX;
                currentX = offsetX;
                track.style.transition = 'none';
            }, { passive: true });

            track.addEventListener('touchmove', function (e) {
                var dx = e.touches[0].clientX - startX;
                // Hard clamp — no overscroll past edges
                var newX = Math.max(maxOffset, Math.min(0, currentX + dx));
                track.style.transform = 'translateX(' + newX + 'px)';
                offsetX = newX;
            }, { passive: true });

            track.addEventListener('touchend', function () {
                track.style.transition = 'transform 0.3s ease';
                maxOffset = calcMaxOffset();
                offsetX = Math.max(maxOffset, Math.min(0, offsetX));
                track.style.transform = 'translateX(' + offsetX + 'px)';
            }, { passive: true });
        }

        return wrap.children.length ? wrap : null;
    }

    /* -------------------------------------------------------
       createVideoEmbed — single YouTube embed
    ------------------------------------------------------- */
    function createVideoEmbed(videoId, title, channel) {
        if (!videoId) return null;

        const wrap = document.createElement('div');
        wrap.className = 'search-video-embed';

        const ratio = document.createElement('div');
        ratio.className = 'search-video-embed__ratio';

        const iframe = document.createElement('iframe');
        iframe.src = 'https://www.youtube.com/embed/' + encodeURIComponent(videoId);
        iframe.allow = 'accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture';
        iframe.allowFullscreen = true;
        iframe.title = title || 'Video';

        ratio.appendChild(iframe);
        wrap.appendChild(ratio);

        if (title || channel) {
            const caption = document.createElement('div');
            caption.className = 'search-video-embed__caption';
            caption.textContent = [title, channel].filter(Boolean).join(' \u00B7 ');
            wrap.appendChild(caption);
        }

        return wrap;
    }

    /* -------------------------------------------------------
       createVideoCards — remaining videos as cards
    ------------------------------------------------------- */
    function createVideoCards(videos) {
        if (!videos || !videos.length) return null;

        const wrap = document.createElement('div');
        wrap.className = 'search-video-cards';

        const label = document.createElement('div');
        label.className = 'search-video-cards__label';
        label.textContent = 'Videos';
        wrap.appendChild(label);

        videos.forEach(function (v) {
            const card = document.createElement('a');
            card.className = 'search-video-card';
            card.href = _ensureUrl(v.url);
            card.target = '_blank';
            card.rel = 'noopener noreferrer';
            if (window.innerWidth <= 640) {
                card.addEventListener('click', function (e) { e.preventDefault(); _openLink(v.url); });
            }

            const thumbWrap = document.createElement('div');
            thumbWrap.className = 'search-video-card__thumb';

            if (v.thumbnail) {
                const img = document.createElement('img');
                img.src = v.thumbnail;
                img.alt = v.title || '';
                img.loading = 'lazy';
                img.onerror = function () {
                    thumbWrap.style.background = 'var(--bg-overlay, rgba(10,10,18,0.85))';
                    this.style.display = 'none';
                };
                thumbWrap.appendChild(img);
            }

            if (v.duration) {
                const dur = document.createElement('div');
                dur.className = 'search-video-card__duration';
                dur.textContent = v.duration;
                thumbWrap.appendChild(dur);
            }

            card.appendChild(thumbWrap);

            const body = document.createElement('div');
            body.className = 'search-video-card__body';

            const titleEl = document.createElement('div');
            titleEl.className = 'search-video-card__title';
            titleEl.textContent = v.title || '';
            body.appendChild(titleEl);

            const meta = document.createElement('div');
            meta.className = 'search-video-card__meta';
            meta.textContent = [v.channel, v.source_domain].filter(Boolean).join(' \u00B7 ');
            body.appendChild(meta);

            card.appendChild(body);
            wrap.appendChild(card);
        });

        return wrap.children.length > 1 ? wrap : null;
    }

    /* -------------------------------------------------------
       _domainToName — strip www/TLD, capitalize, truncate
    ------------------------------------------------------- */
    function _domainToName(domain) {
        if (!domain) return '';
        var name = domain.replace(/^www\./, '');
        var parts = name.split('.');
        // Two-part TLDs: theguardian.co.uk → theguardian
        var twoPartTlds = ['co', 'com', 'org', 'net', 'gov', 'edu', 'ac'];
        if (parts.length > 2 && twoPartTlds.indexOf(parts[parts.length - 2]) >= 0) {
            name = parts[parts.length - 3]; // 'theguardian' from 'theguardian.co.uk'
        } else if (parts.length > 2) {
            name = parts[parts.length - 2]; // 'istockphoto' from 'media.istockphoto.com'
        } else {
            name = parts[0]; // 'apnews' from 'apnews.com'
        }
        if (!name) return '';
        name = name.charAt(0).toUpperCase() + name.slice(1);
        if (name.length > 15) name = name.slice(0, 13) + '\u2026';
        return name;
    }

    /* -------------------------------------------------------
       createCitationTooltip — hover preview card with nav
    ------------------------------------------------------- */
    function createCitationTooltip(nums, sourceMap) {
        var sources = nums.map(function (n) { return sourceMap[n]; }).filter(Boolean);
        if (!sources.length) return null;

        var current = 0;
        var tooltip = document.createElement('div');
        tooltip.className = 'citation-tooltip';

        function render() {
            var s = sources[current];
            var favicon = _getFavicon(s);
            var domain = _getDomain(s);
            var rawSnippet = s.snippet || '';
            // Skip snippet if it duplicates the title
            var snippet = (rawSnippet && rawSnippet !== s.title) ? rawSnippet.slice(0, 120) : '';
            if (rawSnippet.length > 120 && snippet) snippet += '\u2026';

            // Nav row (only show if multiple sources)
            var navHtml = '';
            if (sources.length > 1) {
                navHtml = '<div class="citation-tooltip__nav">' +
                    '<button class="ct-prev">\u2190</button>' +
                    '<button class="ct-next">\u2192</button>' +
                    '<span>' + (current + 1) + '/' + sources.length + '</span>' +
                    '</div>';
            }

            tooltip.innerHTML = navHtml +
                '<div class="citation-tooltip__publisher">' +
                    (favicon ? '<img src="' + _esc(favicon) + '" width="14" height="14" onerror="this.style.display=\'none\'">' : '') +
                    '<span>' + _esc(domain) + '</span>' +
                '</div>' +
                '<div class="citation-tooltip__title">' + _esc(s.title || '') + '</div>' +
                (snippet ? '<div class="citation-tooltip__snippet">' + _esc(snippet) + '</div>' : '');

            // Wire nav buttons
            var prevBtn = tooltip.querySelector('.ct-prev');
            var nextBtn = tooltip.querySelector('.ct-next');
            if (prevBtn) prevBtn.addEventListener('click', function (e) {
                e.stopPropagation();
                current = Math.max(0, current - 1);
                render();
            });
            if (nextBtn) nextBtn.addEventListener('click', function (e) {
                e.stopPropagation();
                current = Math.min(sources.length - 1, current + 1);
                render();
            });
        }

        render();

        // Self-remove when mouse leaves tooltip
        tooltip.addEventListener('mouseleave', function () {
            tooltip.remove();
        });

        return tooltip;
    }

    function _positionTooltip(pill, tooltip) {
        var rect = pill.getBoundingClientRect();
        var spaceBelow = window.innerHeight - rect.bottom;

        if (spaceBelow < 200) {
            tooltip.style.top = (rect.top + window.scrollY - tooltip.offsetHeight - 8) + 'px';
        } else {
            tooltip.style.top = (rect.bottom + window.scrollY + 8) + 'px';
        }
        tooltip.style.left = Math.min(
            rect.left + window.scrollX,
            window.innerWidth - 295
        ) + 'px';
    }

    /* -------------------------------------------------------
       processCitations — replace [N] groups with ChatGPT-style pills
       Consecutive [1][2][3] → single pill "Reuters +2"
    ------------------------------------------------------- */
    function processCitations(messageEl, sources) {
        if (!messageEl || !sources || !sources.length) return;
        var content = messageEl.querySelector('.message-content');
        if (!content) return;

        // Build 1-indexed source map
        var sourceMap = {};
        sources.forEach(function (s, i) { sourceMap[i + 1] = s; });

        // Store sources on the message element for sidebar access
        messageEl._citationSources = sources;

        // Walk text nodes, skip code/pre and existing pills
        var walker = document.createTreeWalker(
            content,
            NodeFilter.SHOW_TEXT,
            {
                acceptNode: function (node) {
                    var el = node.parentElement;
                    while (el && el !== content) {
                        if (el.classList && el.classList.contains('citation-pill') ||
                            el.tagName === 'CODE' || el.tagName === 'PRE') {
                            return NodeFilter.FILTER_REJECT;
                        }
                        el = el.parentElement;
                    }
                    return /\[\d+\]/.test(node.nodeValue)
                        ? NodeFilter.FILTER_ACCEPT
                        : NodeFilter.FILTER_SKIP;
                }
            }
        );

        var nodes = [];
        var n;
        while ((n = walker.nextNode())) nodes.push(n);

        nodes.forEach(function (textNode) {
            // Split on consecutive citation groups: ([1][2][3]) or single [1]
            var parts = textNode.nodeValue.split(/((?:\[\d+\])+)/);
            if (parts.length <= 1) return;

            var frag = document.createDocumentFragment();
            parts.forEach(function (part) {
                // Check if this part is a citation group
                var groupMatch = part.match(/^(?:\[\d+\])+$/);
                if (groupMatch) {
                    // Extract all numbers from the group
                    var nums = [];
                    var m;
                    var re = /\[(\d+)\]/g;
                    while ((m = re.exec(part)) !== null) {
                        nums.push(parseInt(m[1], 10));
                    }

                    // Filter to valid sources only
                    var validNums = nums.filter(function (num) { return !!sourceMap[num]; });
                    if (!validNums.length) {
                        frag.appendChild(document.createTextNode(part));
                        return;
                    }

                    var firstSource = sourceMap[validNums[0]];
                    var domain = _getDomain(firstSource);
                    var name = _domainToName(domain) || firstSource.title?.split(/\s+/).slice(0, 2).join(' ') || ('Source ' + validNums[0]);
                    var extra = validNums.length - 1;
                    var label = extra > 0 ? name + ' +' + extra : name;

                    var pill = document.createElement('span');
                    pill.className = 'citation-pill';
                    pill.setAttribute('data-citations', validNums.join(','));

                    pill.textContent = label;

                    // Mobile: compact pill with name, touch-friendly
                    if (window.innerWidth <= 640) {
                        pill.style.cssText = 'display: inline-flex; align-items: center; padding: 3px 8px; min-height: 26px; border-radius: 12px; background: rgba(255,255,255,0.08); font-size: 0.65rem; font-weight: 500; color: rgba(255,255,255,0.6); margin: 0 2px; cursor: pointer; user-select: none; white-space: nowrap;';
                    }

                    // Click → open sources sidebar
                    pill.addEventListener('click', function () {
                        var indices = this.getAttribute('data-citations');
                        openSidebarForCitations(messageEl, indices);
                    });

                    // Hover → tooltip preview card (desktop only)
                    if (window.innerWidth > 640) (function (p, vn, sm) {
                        p.addEventListener('mouseenter', function () {
                            // Remove any existing tooltip
                            var old = document.querySelector('.citation-tooltip');
                            if (old) old.remove();

                            var tt = createCitationTooltip(vn, sm);
                            if (!tt) return;
                            document.body.appendChild(tt);
                            _positionTooltip(p, tt);
                            p._tooltip = tt;
                        });
                        p.addEventListener('mouseleave', function () {
                            setTimeout(function () {
                                if (p._tooltip && !p._tooltip.matches(':hover')) {
                                    p._tooltip.remove();
                                    p._tooltip = null;
                                }
                            }, 150);
                        });
                    })(pill, validNums, sourceMap);

                    frag.appendChild(pill);
                } else {
                    frag.appendChild(document.createTextNode(part));
                }
            });

            textNode.parentNode.replaceChild(frag, textNode);
        });
    }

    /* -------------------------------------------------------
       createSourcesButton — favicon stack + "Sources" label
    ------------------------------------------------------- */
    function createSourcesButton(sources) {
        var btn = document.createElement('button');
        btn.className = 'sources-btn';

        var top3 = sources.slice(0, 3);
        var faviconsWrap = document.createElement('div');
        faviconsWrap.className = 'sources-btn__favicons';

        top3.forEach(function (s) {
            var img = document.createElement('img');
            img.className = 'sources-btn__favicon';
            img.src = _getFavicon(s);
            img.alt = _getDomain(s);
            img.onerror = function () { this.style.display = 'none'; };
            faviconsWrap.appendChild(img);
        });

        var label = document.createElement('span');
        label.className = 'sources-btn__label';
        label.textContent = 'Sources';

        btn.appendChild(faviconsWrap);
        btn.appendChild(label);

        btn.addEventListener('click', function () {
            openSidebar(sources);
        });

        // Mobile: larger touch-friendly pill
        if (window.innerWidth <= 640) {
            btn.style.cssText = 'display: inline-flex; align-items: center; gap: 6px; height: 36px; padding: 0 12px 0 6px; border-radius: 18px; background: rgba(255,255,255,0.06); border: 1px solid rgba(255,255,255,0.1); cursor: pointer; font-size: 0.8rem; color: rgba(255,255,255,0.7); font-weight: 500;';
        }

        return btn;
    }

    /* -------------------------------------------------------
       Sources Sidebar
    ------------------------------------------------------- */
    var _outsideClickBound = null;

    function openSidebarForCitations(messageEl, indicesStr) {
        var indices = indicesStr.split(',').map(Number);
        var sources = messageEl._citationSources || [];
        if (!sources.length) return;
        openSidebar(sources, indices);
    }

    function openSidebar(sources, highlightIndices) {
        var sidebar = document.getElementById('sources-sidebar');
        var body = document.getElementById('sources-sidebar-body');
        if (!sidebar || !body) return;

        body.innerHTML = '';

        // Build all source items with data-source-num
        sources.forEach(function (s, i) {
            var item = createSourceItem(s);
            item.dataset.sourceNum = String(i + 1); // 1-indexed
            body.appendChild(item);

            // Insert "More" separator after 4th item
            if (i === 3 && sources.length > 4) {
                var more = document.createElement('div');
                more.className = 'sources-sidebar__more';
                more.textContent = 'More';
                body.appendChild(more);
            }
        });

        // Highlight cited sources and scroll to first
        if (highlightIndices && highlightIndices.length) {
            highlightIndices.forEach(function (num, idx) {
                var item = body.querySelector('[data-source-num="' + num + '"]');
                if (item) {
                    item.classList.add('source-item--highlight');
                    if (idx === 0) {
                        setTimeout(function () {
                            item.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
                        }, 50);
                    }
                }
            });
        }

        sidebar.classList.add('sources-sidebar--open');
        sidebar.setAttribute('aria-hidden', 'false');

        // Mobile: convert sidebar to bottom sheet
        if (window.innerWidth <= 640) {
            sidebar.style.cssText = 'position: fixed; bottom: 0; left: 0; right: 0; top: auto; width: 100%; max-height: 70vh; height: auto; border-radius: 16px 16px 0 0; border-left: none; border-top: 1px solid rgba(255,255,255,0.1); transform: translateY(0); z-index: 1000; background: #1a1a1a; display: flex; flex-direction: column; overflow: hidden; overscroll-behavior: contain;';
            // Prevent pull-to-refresh on the scrollable body
            var sidebarBody = document.getElementById('sources-sidebar-body');
            if (sidebarBody) {
                sidebarBody.style.overscrollBehavior = 'contain';
                sidebarBody.style.webkitOverflowScrolling = 'touch';
                sidebarBody.style.overflowY = 'auto';
            }
            // Add drag handle
            var handle = sidebar.querySelector('.sources-sidebar__drag-handle');
            if (!handle) {
                handle = document.createElement('div');
                handle.className = 'sources-sidebar__drag-handle';
                handle.style.cssText = 'width: 36px; height: 4px; border-radius: 2px; background: rgba(255,255,255,0.2); margin: 8px auto 4px; flex-shrink: 0;';
                sidebar.insertBefore(handle, sidebar.firstChild);
            }
            // Add overlay backdrop
            var overlay = document.getElementById('sources-overlay');
            if (!overlay) {
                overlay = document.createElement('div');
                overlay.id = 'sources-overlay';
                overlay.style.cssText = 'position: fixed; inset: 0; background: rgba(0,0,0,0.5); z-index: 999; touch-action: none;';
                overlay.addEventListener('click', function () { closeSidebar(); });
                overlay.addEventListener('touchmove', function (e) { e.preventDefault(); }, { passive: false });
                document.body.appendChild(overlay);
            }
            overlay.style.display = 'block';
        }

        // Close on outside click (delayed to avoid catching the opening click)
        setTimeout(function () {
            _outsideClickBound = function (e) {
                if (sidebar && !sidebar.contains(e.target) &&
                    !e.target.closest('.sources-btn') &&
                    !e.target.closest('.citation-pill')) {
                    closeSidebar();
                }
            };
            document.addEventListener('click', _outsideClickBound);
        }, 0);
    }

    function closeSidebar() {
        var sidebar = document.getElementById('sources-sidebar');
        if (sidebar) {
            sidebar.classList.remove('sources-sidebar--open');
            sidebar.setAttribute('aria-hidden', 'true');
            sidebar.style.cssText = ''; // Reset inline styles
        }
        var overlay = document.getElementById('sources-overlay');
        if (overlay) overlay.style.display = 'none';
        var handle = sidebar && sidebar.querySelector('.sources-sidebar__drag-handle');
        if (handle) handle.remove();
        if (_outsideClickBound) {
            document.removeEventListener('click', _outsideClickBound);
            _outsideClickBound = null;
        }
    }

    function createSourceItem(s) {
        var item = document.createElement('div');
        item.className = 'source-item';
        item.addEventListener('click', function () {
            _openLink(s.url);
        });

        var publisher = document.createElement('div');
        publisher.className = 'source-item__publisher';

        var favicon = _getFavicon(s);
        if (favicon) {
            var favImg = document.createElement('img');
            favImg.className = 'source-item__favicon';
            favImg.src = favicon;
            favImg.alt = '';
            favImg.onerror = function () { this.style.display = 'none'; };
            publisher.appendChild(favImg);
        }

        var name = document.createElement('span');
        name.className = 'source-item__name';
        name.textContent = _getDomain(s);
        publisher.appendChild(name);
        item.appendChild(publisher);

        var title = document.createElement('div');
        title.className = 'source-item__title';
        title.textContent = s.title || '';
        item.appendChild(title);

        if (s.snippet && s.snippet !== s.title) {
            var snippet = document.createElement('div');
            snippet.className = 'source-item__snippet';
            snippet.textContent = s.snippet;
            item.appendChild(snippet);
        }

        return item;
    }

    /* -------------------------------------------------------
       openLightbox
    ------------------------------------------------------- */
    function openLightbox(imageUrl, title, sourceUrl) {
        var existing = document.querySelector('.search-lightbox');
        if (existing) existing.remove();

        var overlay = document.createElement('div');
        overlay.className = 'search-lightbox';

        var img = document.createElement('img');
        img.className = 'search-lightbox__img';
        img.src = imageUrl;
        img.alt = title || '';

        var closeBtn = document.createElement('button');
        closeBtn.className = 'search-lightbox__close';
        closeBtn.innerHTML = '&times;';
        closeBtn.setAttribute('aria-label', 'Close');

        overlay.appendChild(img);
        overlay.appendChild(closeBtn);

        if (title || sourceUrl) {
            var cap = document.createElement('div');
            cap.className = 'search-lightbox__title';
            if (title) cap.textContent = title;
            if (sourceUrl) {
                var link = document.createElement('a');
                link.href = sourceUrl;
                link.target = '_blank';
                link.rel = 'noopener noreferrer';
                link.textContent = ' View source';
                link.style.cssText = 'color: #60a5fa; margin-left: 8px; font-size: 0.85em;';
                link.addEventListener('click', function (e) {
                    e.stopPropagation();
                    if (window.innerWidth <= 640) { e.preventDefault(); _openLink(sourceUrl); }
                });
                cap.appendChild(link);
            }
            overlay.appendChild(cap);
        }

        function close() { overlay.remove(); }
        overlay.addEventListener('click', close);
        closeBtn.addEventListener('click', function (e) { e.stopPropagation(); close(); });
        document.addEventListener('keydown', function esc(e) {
            if (e.key === 'Escape') { close(); document.removeEventListener('keydown', esc); }
        });

        document.body.appendChild(overlay);
    }

    /* -------------------------------------------------------
       handleSearchEvent — called during streaming
    ------------------------------------------------------- */
    function handleSearchEvent(container, eventType, data) {
        var st = _getState(container);

        if (eventType === 'search_started') {
            if (!st.bannerEl) {
                st.searchType = data.search_type || 'general';
                st.bannerEl = createSearchBanner(data.query, st.searchType);
                // Insert FIRST so the banner sits above the (initially empty)
                // text-message-container placeholder. Keeps its position stable
                // when the StreamRenderer replaces that placeholder with its
                // own wrapper later — otherwise the banner appears below the
                // placeholder first, then jumps above the answer on phase-2.
                container.insertBefore(st.bannerEl, container.firstChild);
            }

        } else if (eventType === 'search_web_results') {
            st.webResults = data.results || [];
            _updateBannerProgress(st);

        } else if (eventType === 'search_images') {
            st.imageResults = data.images || [];
            _updateBannerProgress(st);

        } else if (eventType === 'search_videos') {
            st.videoResults = data.videos || [];
            _updateBannerProgress(st);

        } else if (eventType === 'search_content') {
            if (data.url) st.fetchedUrls.add(data.url);
            _updateBannerProgress(st);

        } else if (eventType === 'search_complete') {
            // Mark complete — banner stays in "Reading" state until dismiss.
            st.searchComplete = true;
            _updateBannerProgress(st, true);
        }
    }

    // Live update the count + favicon strip inside the shimmer banner.
    // Adapts the noun and the data source to the search type so "Searching
    // videos" counts video results, "Searching images" counts images, and
    // the general/news path counts unique web domains.
    function _updateBannerProgress(st, complete) {
        if (!st.bannerEl) return;
        var countEl = st.bannerEl.querySelector('.search-banner__count');
        var stripEl = st.bannerEl.querySelector('.search-banner__favicons');

        var type = st.searchType || 'general';
        var noun, domains = new Set(), nounPlural;

        if (type === 'video') {
            // Domains from video result URLs.
            (st.videoResults || []).forEach(function (v) {
                var u = v.url || v.link || v.watchUrl || v.thumbnail;
                if (!u) return;
                try { domains.add(new URL(u).hostname); } catch (_) { /* ignore */ }
            });
            noun = 'video'; nounPlural = 'videos';
        } else if (type === 'images') {
            (st.imageResults || []).forEach(function (img) {
                var u = img.source || img.link || img.url || img.thumbnail;
                if (!u) return;
                try { domains.add(new URL(u).hostname); } catch (_) { /* ignore */ }
            });
            noun = 'image'; nounPlural = 'images';
        } else {
            (st.webResults || []).forEach(function (r) {
                var d = _getDomain(r);
                if (d) domains.add(d);
            });
            (Array.from(st.fetchedUrls || [])).forEach(function (u) {
                try { domains.add(new URL(u).hostname); } catch (_) { /* ignore */ }
            });
            noun = 'source'; nounPlural = 'sources';
        }

        var n = domains.size;
        if (countEl) {
            if (complete) {
                countEl.textContent = 'Reading ' + n + ' ' +
                    (n === 1 ? noun : nounPlural) + '…';
            } else if (n > 0) {
                countEl.textContent = 'Checking ' + n + ' ' +
                    (n === 1 ? noun : nounPlural) + '…';
            } else {
                countEl.textContent = 'Finding ' + nounPlural + '…';
            }
        }

        if (stripEl) {
            var arr = Array.from(domains).slice(0, 6);
            var existing = stripEl.children.length;
            for (var i = existing; i < arr.length; i++) {
                var f = document.createElement('img');
                f.className = 'search-banner__favicon';
                f.src = _getFavicon({ url: 'https://' + arr[i] });
                f.loading = 'lazy';
                f.alt = '';
                f.onerror = function () { this.style.display = 'none'; };
                stripEl.appendChild(f);
            }
        }
    }

    /* -------------------------------------------------------
       _onTypingComplete — run callback when typing animation
       finishes. Listens for 'typing-complete' custom event
       dispatched by the rendering pipeline, with a timeout
       fallback in case the event never fires.
    ------------------------------------------------------- */
    function _processCitationsWhenReady(messageEl, sources, attempt, lastLen) {
        attempt = attempt || 0;
        lastLen = lastLen || 0;
        var MAX_ATTEMPTS = 40;  // 40 × 200ms = 8s max
        var content = messageEl.querySelector('.message-content');
        if (!content) return;

        var textContent = content.textContent || '';
        var curLen = textContent.length;
        var hasCitations = /\[\d+\]/.test(textContent);
        var isStable = curLen > 0 && curLen === lastLen;

        if (hasCitations && isStable) {
            processCitations(messageEl, sources);
            _appendSourcesButton(messageEl, sources);
            return;
        }

        // If text is stable but no citations, still add sources button
        if (isStable && curLen > 0 && attempt > 4) {
            _appendSourcesButton(messageEl, sources);
            return;
        }

        if (attempt < MAX_ATTEMPTS) {
            setTimeout(function () {
                _processCitationsWhenReady(messageEl, sources, attempt + 1, curLen);
            }, 200);
        }
    }

    /* -------------------------------------------------------
       _appendSourcesButton — add sources button to action bar
    ------------------------------------------------------- */
    function _appendSourcesButton(messageEl, sources) {
        var actionBar = messageEl.querySelector('.action-buttons');
        if (actionBar) {
            if (!actionBar.querySelector('.sources-btn')) {
                actionBar.appendChild(createSourcesButton(sources));
            }
            return;
        }
        // Action bar may not exist yet — brief retry
        var attempts = 0;
        var interval = setInterval(function () {
            var bar = messageEl.querySelector('.action-buttons');
            if (bar) {
                clearInterval(interval);
                if (!bar.querySelector('.sources-btn')) {
                    bar.appendChild(createSourcesButton(sources));
                }
            } else if (++attempts >= 10) {
                clearInterval(interval);
            }
        }, 200);
    }

    /* -------------------------------------------------------
       renderFinalResults — called after done event
       render_hint determines what renders above the AI text
    ------------------------------------------------------- */
    function renderFinalResults(messageEl, searchResults, embedVideoId, fetchedUrls, renderHint) {
        if (!messageEl || !searchResults) return;

        var content = messageEl.querySelector('.message-content');
        if (!content) return;

        var st = _getState(content);

        // Remove shimmer banner if still in container (safety net for history/edge cases)
        if (st.bannerEl && st.bannerEl.parentNode === content) {
            st.bannerEl.remove();
        }
        st.bannerEl = null;

        // Merge data
        if (Array.isArray(fetchedUrls)) {
            fetchedUrls.forEach(function (u) { st.fetchedUrls.add(u); });
        }

        var webRes = searchResults.web || [];
        var images = searchResults.images || st.imageResults || [];
        var videos = searchResults.videos || st.videoResults || [];

        // Normalize render_hint
        var hint = renderHint || 'text_only';

        // 1. Primary content above AI text — based on render_hint
        //    Insert before the first text container, not before action-buttons
        var _insertTarget = content.querySelector('.text-message-container') || content.firstChild;

        if (hint === 'news_cards' && webRes.length > 0) {
            var carousel = createNewsCarousel(webRes);
            if (carousel) content.insertBefore(carousel, _insertTarget);
        } else if (hint === 'image_grid' && images.length > 0) {
            var imgCarousel = createImageCarousel(images);
            if (imgCarousel) content.insertBefore(imgCarousel, _insertTarget);
        } else if (hint === 'video_primary' && videos.length > 0) {
            var topVideoId = embedVideoId || (videos[0] && videos[0].video_id);
            if (topVideoId) {
                var matchedVideo = videos.find(function (v) { return v.video_id === topVideoId; });
                var embedEl = createVideoEmbed(
                    topVideoId,
                    matchedVideo ? matchedVideo.title : null,
                    matchedVideo ? matchedVideo.channel : null
                );
                if (embedEl) content.insertBefore(embedEl, _insertTarget);

                // Remaining videos as cards below
                var remaining = videos.filter(function (v) { return v.video_id !== topVideoId; });
                if (remaining.length > 0) {
                    var videoCards = createVideoCards(remaining);
                    if (videoCards) content.appendChild(videoCards);
                }
            }
        }

        // 2. Citation pills + sources button — use all available results as sources
        //    Web results are preferred, but image/video results also carry URLs
        var allSources = webRes.length > 0 ? webRes : [].concat(images, videos).filter(function (r) { return r && r.url; });
        if (allSources.length > 0) {
            _processCitationsWhenReady(messageEl, allSources);
        }
    }

    /* -------------------------------------------------------
       dismissBanner — fade out banner when answer starts streaming
    ------------------------------------------------------- */
    function dismissBanner(container) {
        if (!container) return;
        var banner = container.querySelector('.search-banner');
        if (!banner) return;

        // Capture position and detach from flow so displayMessage's
        // innerHTML='' won't hard-cut the fade animation
        var rect = banner.getBoundingClientRect();
        var scrollTop = window.pageYOffset || document.documentElement.scrollTop;
        banner.style.position = 'absolute';
        banner.style.top = (rect.top + scrollTop) + 'px';
        banner.style.left = rect.left + 'px';
        banner.style.width = rect.width + 'px';
        banner.style.zIndex = '10';
        document.body.appendChild(banner);

        // Fade out
        banner.style.transition = 'opacity 0.4s ease, transform 0.4s ease';
        banner.style.opacity = '0';
        banner.style.transform = 'translateY(-8px)';
        setTimeout(function () {
            if (banner.parentNode) banner.remove();
        }, 400);
    }

    /* -------------------------------------------------------
       collapseBanner — ChatGPT-style: shrink the big "Searching…"
       banner into a compact "Searched the web" pill that sits above
       the streaming answer. Stays clickable to open the sources
       sidebar. No fade out.
    ------------------------------------------------------- */
    function collapseBanner(container) {
        if (!container) return;
        var banner = container.querySelector('.search-banner');
        if (!banner) return;
        if (banner.classList.contains('search-banner--collapsed')) return;

        banner.classList.add('search-banner--collapsed');

        // Replace any "Searching for X…" label with a compact "Searched the web" pill.
        // Preserve existing click handlers (they route to the sources sidebar).
        var label = banner.querySelector('.search-banner__label, .search-banner__query');
        if (label) {
            label.textContent = 'Searched the web';
        }
        // Remove spinner / animated dots if present.
        var spinner = banner.querySelector('.search-banner__spinner, .search-banner__dots');
        if (spinner) spinner.remove();
    }

    /* -------------------------------------------------------
       Public API
    ------------------------------------------------------- */
    window.SearchResults = {
        handleSearchEvent:    handleSearchEvent,
        renderFinalResults:   renderFinalResults,
        processCitations:     processCitations,
        dismissBanner:        dismissBanner,
        collapseBanner:       collapseBanner,
        openSidebar:          openSidebar,
        openSidebarForCitations: openSidebarForCitations,
        closeSidebar:         closeSidebar,
        openLink:             _openLink,
        // Helpers exposed for testing
        createSearchBanner:   createSearchBanner,
        createNewsCarousel:   createNewsCarousel,
        createImageCarousel:  createImageCarousel,
        createVideoEmbed:     createVideoEmbed,
        createVideoCards:     createVideoCards,
        createSourcesButton:  createSourcesButton,
        openLightbox:         openLightbox
    };

}());
