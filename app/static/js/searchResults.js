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
       createSearchBanner — shimmer "Searching the web"
    ------------------------------------------------------- */
    function createSearchBanner(query, searchType) {
        const banner = document.createElement('div');
        banner.className = 'search-banner';

        const searchTypeLabels = {
            'news': 'Searching news',
            'images': 'Searching images',
            'video': 'Searching videos',
            'general': 'Searching the web',
        };
        const label = document.createElement('div');
        label.className = 'search-banner__label';
        label.textContent = searchTypeLabels[searchType] || 'Searching the web';
        banner.appendChild(label);

        if (query) {
            const queryEl = document.createElement('div');
            queryEl.className = 'search-banner__query';
            queryEl.textContent = '"' + query + '"';
            banner.appendChild(queryEl);
        }

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
                card.style.background = 'rgba(255,255,255,0.03)';
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
                item.style.cssText = 'flex: 0 0 120px; height: 85px; border-radius: 8px; overflow: hidden; cursor: pointer; border: 1px solid rgba(255,255,255,0.08); position: relative; background: rgba(255,255,255,0.03);';
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

            const thumbWrap = document.createElement('div');
            thumbWrap.className = 'search-video-card__thumb';

            if (v.thumbnail) {
                const img = document.createElement('img');
                img.src = v.thumbnail;
                img.alt = v.title || '';
                img.loading = 'lazy';
                img.onerror = function () {
                    thumbWrap.style.background = 'rgba(0,0,0,0.4)';
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
            window.open(_ensureUrl(s.url), '_blank');
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
                link.addEventListener('click', function (e) { e.stopPropagation(); });
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
                st.bannerEl = createSearchBanner(data.query, data.search_type);
                container.appendChild(st.bannerEl);
            }

        } else if (eventType === 'search_web_results') {
            st.webResults = data.results || [];

        } else if (eventType === 'search_images') {
            st.imageResults = data.images || [];

        } else if (eventType === 'search_videos') {
            st.videoResults = data.videos || [];

        } else if (eventType === 'search_content') {
            if (data.url) st.fetchedUrls.add(data.url);

        } else if (eventType === 'search_complete') {
            // Mark complete — dismissBanner() handles the smooth fade
            st.searchComplete = true;
        }
    }

    /* -------------------------------------------------------
       _onTypingComplete — run callback when typing animation
       finishes. Listens for 'typing-complete' custom event
       dispatched by ProgressiveTextDisplay, with a timeout
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
       Public API
    ------------------------------------------------------- */
    window.SearchResults = {
        handleSearchEvent:    handleSearchEvent,
        renderFinalResults:   renderFinalResults,
        processCitations:     processCitations,
        dismissBanner:        dismissBanner,
        openSidebar:          openSidebar,
        openSidebarForCitations: openSidebarForCitations,
        closeSidebar:         closeSidebar,
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
