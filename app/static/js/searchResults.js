/* searchResults.js — Inline search result rendering for XelaSphere
   Exposes window.SearchResults (IIFE)
*/
(function () {
    'use strict';

    // Per-message state storage keyed by container element
    const _state = new WeakMap();

    function _getState(container) {
        if (!_state.has(container)) {
            _state.set(container, {
                bannerEl: null,
                carouselRendered: false,
                webResults: [],
                videoResults: [],
                fetchedUrls: new Set(),
                pendingHighlights: new Set()
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

    /* -------------------------------------------------------
       createSearchBanner
    ------------------------------------------------------- */
    function createSearchBanner(query, searchType) {
        const banner = document.createElement('div');
        banner.className = 'search-banner';

        const spinner = document.createElement('div');
        spinner.className = 'search-banner__spinner';

        const text = document.createElement('div');
        text.className = 'search-banner__text';

        const label = searchType === 'images' ? 'Searching images'
            : searchType === 'videos' ? 'Searching videos'
            : 'Searching the web';

        text.textContent = query ? `${label} for "${query}"` : label + '…';

        banner.appendChild(spinner);
        banner.appendChild(text);
        return banner;
    }

    /* -------------------------------------------------------
       createImageCarousel
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
                openLightbox(img.url || thumbUrl, img.title || '');
            });

            wrap.appendChild(item);
        });

        return wrap.children.length ? wrap : null;
    }

    /* -------------------------------------------------------
       createVideoEmbed
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
            caption.textContent = [title, channel].filter(Boolean).join(' · ');
            wrap.appendChild(caption);
        }

        return wrap;
    }

    /* -------------------------------------------------------
       createSourceCards
    ------------------------------------------------------- */
    function createSourceCards(webResults, fetchedUrls) {
        if (!webResults || !webResults.length) return null;

        const fetchedSet = fetchedUrls instanceof Set
            ? fetchedUrls
            : new Set(Array.isArray(fetchedUrls) ? fetchedUrls : []);

        const wrap = document.createElement('div');
        wrap.className = 'search-sources';

        const label = document.createElement('div');
        label.className = 'search-sources__label';
        label.textContent = 'Sources';
        wrap.appendChild(label);

        webResults.forEach(function (r, i) {
            const num = i + 1;
            const card = document.createElement('a');
            card.className = 'search-source-card';
            card.href = r.url || '#';
            card.target = '_blank';
            card.rel = 'noopener noreferrer';
            card.dataset.citationNum = num;

            // Citation number bubble
            const numEl = document.createElement('div');
            numEl.className = 'search-source-card__num';
            numEl.textContent = num;
            card.appendChild(numEl);

            // Favicon
            if (r.source_domain || r.url) {
                const domain = r.source_domain || (() => {
                    try { return new URL(r.url).hostname; } catch (e) { return ''; }
                })();
                if (domain) {
                    const fav = document.createElement('img');
                    fav.className = 'search-source-card__favicon';
                    fav.src = r.favicon || ('https://www.google.com/s2/favicons?domain=' + encodeURIComponent(domain) + '&sz=16');
                    fav.alt = '';
                    fav.onerror = function () { this.style.display = 'none'; };
                    card.appendChild(fav);
                }
            }

            // Body
            const body = document.createElement('div');
            body.className = 'search-source-card__body';

            const titleEl = document.createElement('div');
            titleEl.className = 'search-source-card__title';
            titleEl.textContent = r.title || r.source_domain || r.url || '';
            body.appendChild(titleEl);

            const domainEl = document.createElement('div');
            domainEl.className = 'search-source-card__domain';
            domainEl.textContent = r.source_domain || (() => {
                try { return new URL(r.url).hostname.replace(/^www\./, ''); } catch (e) { return ''; }
            })();
            body.appendChild(domainEl);

            card.appendChild(body);

            // Checkmark badge for fetched URLs
            const urlToCheck = r.url || '';
            if (urlToCheck && fetchedSet.has(urlToCheck)) {
                const check = document.createElement('div');
                check.className = 'search-source-card__check';
                check.textContent = '✓';
                check.title = 'Full content read';
                card.appendChild(check);
            }

            wrap.appendChild(card);
        });

        return wrap.children.length > 1 ? wrap : null; // >1 because label is a child
    }

    /* -------------------------------------------------------
       createVideoCards
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
            card.href = v.url || ('#' + (v.video_id || ''));
            card.target = '_blank';
            card.rel = 'noopener noreferrer';

            // Thumbnail
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

            // Body
            const body = document.createElement('div');
            body.className = 'search-video-card__body';

            const titleEl = document.createElement('div');
            titleEl.className = 'search-video-card__title';
            titleEl.textContent = v.title || '';
            body.appendChild(titleEl);

            const meta = document.createElement('div');
            meta.className = 'search-video-card__meta';
            meta.textContent = [v.channel, v.source_domain].filter(Boolean).join(' · ');
            body.appendChild(meta);

            card.appendChild(body);
            wrap.appendChild(card);
        });

        return wrap.children.length > 1 ? wrap : null;
    }

    /* -------------------------------------------------------
       openLightbox
    ------------------------------------------------------- */
    function openLightbox(url, title) {
        // Remove any existing lightbox
        var existing = document.querySelector('.search-lightbox');
        if (existing) existing.remove();

        const overlay = document.createElement('div');
        overlay.className = 'search-lightbox';

        const img = document.createElement('img');
        img.className = 'search-lightbox__img';
        img.src = url;
        img.alt = title || '';

        const closeBtn = document.createElement('button');
        closeBtn.className = 'search-lightbox__close';
        closeBtn.innerHTML = '&times;';
        closeBtn.setAttribute('aria-label', 'Close');

        overlay.appendChild(img);
        overlay.appendChild(closeBtn);

        if (title) {
            const cap = document.createElement('div');
            cap.className = 'search-lightbox__title';
            cap.textContent = title;
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
       processCitations — replace [N] text with citation links
    ------------------------------------------------------- */
    function processCitations(messageEl) {
        const content = messageEl.querySelector('.message-content');
        if (!content) return;

        // Gather all text nodes (skip already-processed citation links)
        const walker = document.createTreeWalker(
            content,
            NodeFilter.SHOW_TEXT,
            {
                acceptNode: function (node) {
                    // Skip if inside a citation-link or code block
                    let el = node.parentElement;
                    while (el && el !== content) {
                        if (el.classList && (el.classList.contains('citation-link') ||
                            el.tagName === 'CODE' || el.tagName === 'PRE')) {
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

        const nodes = [];
        let n;
        while ((n = walker.nextNode())) nodes.push(n);

        nodes.forEach(function (textNode) {
            const parts = textNode.nodeValue.split(/(\[\d+\])/);
            if (parts.length <= 1) return;

            const frag = document.createDocumentFragment();
            parts.forEach(function (part) {
                const match = part.match(/^\[(\d+)\]$/);
                if (match) {
                    const num = parseInt(match[1], 10);
                    const a = document.createElement('a');
                    a.className = 'citation-link';
                    a.textContent = num;
                    a.href = '#';
                    a.dataset.citationNum = num;
                    a.addEventListener('click', function (e) {
                        e.preventDefault();
                        highlightSource(num);
                    });
                    frag.appendChild(a);
                } else {
                    frag.appendChild(document.createTextNode(part));
                }
            });

            textNode.parentNode.replaceChild(frag, textNode);
        });
    }

    /* -------------------------------------------------------
       highlightSource
    ------------------------------------------------------- */
    function highlightSource(num) {
        const card = document.querySelector('.search-source-card[data-citation-num="' + num + '"]');
        if (card) {
            card.classList.remove('search-source-card--highlight');
            // Force reflow so animation restarts
            void card.offsetWidth;
            card.classList.add('search-source-card--highlight');
            card.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
            card.addEventListener('animationend', function () {
                card.classList.remove('search-source-card--highlight');
            }, { once: true });
        } else {
            // Card not rendered yet — scroll to bottom of chat to show it when it appears
            const chatbox = document.querySelector('.chatbox') || document.querySelector('.messages-container');
            if (chatbox) {
                chatbox.scrollTop = chatbox.scrollHeight;
            }
        }
    }

    /* -------------------------------------------------------
       handleSearchEvent — called during streaming
    ------------------------------------------------------- */
    function handleSearchEvent(container, eventType, data) {
        const st = _getState(container);

        if (eventType === 'search_started') {
            if (!st.bannerEl) {
                st.bannerEl = createSearchBanner(data.query, data.search_type);
                container.appendChild(st.bannerEl);
            }

        } else if (eventType === 'search_web_results') {
            st.webResults = data.results || [];

        } else if (eventType === 'search_images') {
            // Remove banner and show carousel immediately during streaming
            if (st.bannerEl) { st.bannerEl.remove(); st.bannerEl = null; }
            if (!st.carouselRendered) {
                const carousel = createImageCarousel(data.images || []);
                if (carousel) {
                    container.insertBefore(carousel, container.firstChild);
                    st.carouselRendered = true;
                }
            }

        } else if (eventType === 'search_videos') {
            st.videoResults = data.videos || [];

        } else if (eventType === 'search_content') {
            if (data.url) st.fetchedUrls.add(data.url);

        } else if (eventType === 'search_complete') {
            if (st.bannerEl) { st.bannerEl.remove(); st.bannerEl = null; }
        }
    }

    /* -------------------------------------------------------
       renderFinalResults — called after done event
    ------------------------------------------------------- */
    function renderFinalResults(messageEl, searchResults, embedVideoId, fetchedUrls) {
        if (!messageEl || !searchResults) return;

        const content = messageEl.querySelector('.message-content');
        if (!content) return;

        const st = _getState(content);

        // Merge server-provided fetchedUrls into the set
        if (Array.isArray(fetchedUrls)) {
            fetchedUrls.forEach(function (u) { st.fetchedUrls.add(u); });
        }

        const images   = searchResults.images  || [];
        const webRes   = searchResults.web      || [];
        const videos   = searchResults.videos   || [];
        const fetched  = st.fetchedUrls;

        // 1. Image carousel — if not already shown during streaming
        if (images.length && !st.carouselRendered) {
            const carousel = createImageCarousel(images);
            if (carousel) {
                content.insertBefore(carousel, content.firstChild);
                st.carouselRendered = true;
            }
        }

        // 2. YouTube embed
        var embeddedVideoId = null;
        if (embedVideoId) {
            // Find matching video metadata
            var allVideos = videos.length ? videos : (st.videoResults || []);
            var matchedVideo = allVideos.find(function (v) { return v.video_id === embedVideoId; });
            var embedTitle   = matchedVideo ? matchedVideo.title   : null;
            var embedChannel = matchedVideo ? matchedVideo.channel : null;

            var embedEl = createVideoEmbed(embedVideoId, embedTitle, embedChannel);
            if (embedEl) {
                content.insertBefore(embedEl, content.firstChild);
                embeddedVideoId = embedVideoId;
            }
        }

        // 3. Source cards (appended after AI text)
        if (webRes.length) {
            const sourceCards = createSourceCards(webRes, fetched);
            if (sourceCards) content.appendChild(sourceCards);
        }

        // 4. Remaining video cards (exclude embedded one)
        var allVideos2 = videos.length ? videos : (st.videoResults || []);
        var remainingVideos = embeddedVideoId
            ? allVideos2.filter(function (v) { return v.video_id !== embeddedVideoId; })
            : allVideos2;

        if (remainingVideos.length) {
            var videoCards = createVideoCards(remainingVideos);
            if (videoCards) content.appendChild(videoCards);
        }

        // 5. Process citations in AI text
        processCitations(messageEl);
    }

    /* -------------------------------------------------------
       Public API
    ------------------------------------------------------- */
    window.SearchResults = {
        handleSearchEvent:  handleSearchEvent,
        renderFinalResults: renderFinalResults,
        processCitations:   processCitations,
        highlightSource:    highlightSource,
        // Helpers exposed for testing
        createSearchBanner:  createSearchBanner,
        createImageCarousel: createImageCarousel,
        createVideoEmbed:    createVideoEmbed,
        createSourceCards:   createSourceCards,
        createVideoCards:    createVideoCards,
        openLightbox:        openLightbox
    };

}());
