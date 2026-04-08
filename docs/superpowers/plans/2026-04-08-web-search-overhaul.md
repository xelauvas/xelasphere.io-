# Web Search Overhaul Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the entire search infrastructure with a multi-backend, ACTION-protocol-driven system that streams rich results (text, images, videos) inline with citations.

**Architecture:** New `SearchOrchestrator` coordinates Brave Web + Brave Images + YouTube Data API in parallel. `WebFetcher` fetches full page content with HTML-to-Markdown conversion and 2-tier LRU cache. `ACTION: web_search` replaces all regex-based detection. Progressive SSE streaming delivers results as they arrive. Frontend renders inline image carousel, video embed, and source cards with citation linking.

**Tech Stack:** Python/Flask, cachetools, markdownify, BeautifulSoup4, requests, asyncio, YouTube Data API v3

**Spec:** `docs/superpowers/specs/2026-04-08-web-search-overhaul-design.md`

---

## File Structure

### New Files
| File | Responsibility |
|------|---------------|
| `app/services/web_fetcher.py` | URL fetching, HTML-to-Markdown, og:image extraction, 2-tier LRU cache |
| `app/services/search_orchestrator.py` | Multi-backend coordination, progressive streaming, relevance scoring |
| `app/static/js/searchResults.js` | Frontend components: search banner, image carousel, video cards/embed, source cards, citation linking |
| `app/static/css/search-results.css` | Styles for all search UI components |
| `tests/test_web_fetcher.py` | Unit tests for WebFetcher |
| `tests/test_search_orchestrator.py` | Unit tests for SearchOrchestrator |
| `tests/test_action_parser_search.py` | Unit tests for web_search ACTION parsing |

### Modified Files
| File | Changes |
|------|---------|
| `app/context/action_parser.py` | Add `web_search` to `_VALID_ACTIONS` and `_MULTILINE_HEADER_ACTIONS`, add `search_payload` to `ParsedAction` |
| `app/blueprints/chat_routes.py` | Remove old search flow, add web_search ACTION handling in streaming generator, progressive SSE events |
| `app/config.py` | Add `YOUTUBE_API_KEY`, `SEARCH_CONTEXT_BUDGET`, `SEARCH_PREAPPROVED_DOMAINS`, `BLOCKED_IMAGE_DOMAINS`, `FETCH_DEADLINE_MS` |
| `app/services/chat_engine.py` | Add `ACTION: web_search` to system prompt |
| `app/search_client.py` | Delete old functions, keep `SearchResult` (extended), `SearchBackend` (extended) |
| `app/templates/index.html` | Remove `#enable-search` checkbox and `.search-btn` |
| `app/static/js/codeWindow.js` | Remove old search UI code, add SSE handlers for search events |
| `app/static/js/speechSynthesisModule.js` | Remove `renderSourcesButton()` and `attachSourcesToMessage()` |
| `requirements-cpu.txt` | Add `markdownify` |

### Deleted Files
| File | Reason |
|------|--------|
| `app/static/js/searchAutoDetect.js` | Regex detection replaced by ACTION protocol |

---

## Task 1: Delete Old Search Detection Code

**Files:**
- Delete: `app/static/js/searchAutoDetect.js`
- Modify: `app/search_client.py:443-532`
- Modify: `app/blueprints/chat_routes.py:1649-1659,1727,2126-2128`
- Modify: `app/templates/index.html:635-642`
- Modify: `app/static/js/codeWindow.js:2854,4085-4086,4306`
- Modify: `app/static/js/speechSynthesisModule.js:158,710,715-750`

- [ ] **Step 1: Delete searchAutoDetect.js**

```bash
rm app/static/js/searchAutoDetect.js
```

- [ ] **Step 2: Remove the script tag loading searchAutoDetect.js from index.html**

Search for `searchAutoDetect` in `app/templates/index.html` and remove the `<script>` tag.

Also remove the `#enable-search` checkbox (line 642) and `.search-btn` label (line 635).

- [ ] **Step 3: Remove should_auto_enable_search() from search_client.py**

Delete the function at lines 443-532. Keep `SearchResult`, `SearchBackend`, `SearchClient` class, and `perform_web_search()` for now (they'll be replaced in later tasks but other code still references them).

- [ ] **Step 4: Remove enable_search form param handling from chat_routes.py**

In `chat_routes.py`, find the `enable_search` param extraction (lines ~1649-1659) and remove it. Find the `perform_web_search()` call (line ~1727) and the search result injection (lines ~2126-2128) — comment them out with `# TODO: replaced by SearchOrchestrator` (they'll be fully replaced in Task 5).

- [ ] **Step 5: Remove searchEnabled from codeWindow.js**

Remove the `searchEnabled` form append (line ~2854):
```javascript
// DELETE this line:
formData.append('enable_search', searchEnabled);
```

Remove the `attachSourcesToMessage` call (lines 4085-4086):
```javascript
// DELETE these lines:
if (typeof window.attachSourcesToMessage === 'function') {
    window.attachSourcesToMessage(aiMsgId, data.search_results);
}
```

- [ ] **Step 6: Remove renderSourcesButton and attachSourcesToMessage from speechSynthesisModule.js**

Delete `renderSourcesButton()` at line 158 and its call at line 710.
Delete `attachSourcesToMessage()` at lines 715-750 and the global export at line 750.

- [ ] **Step 7: Verify and commit**

```bash
python3 -c "import py_compile; py_compile.compile('app/blueprints/chat_routes.py', doraise=True); py_compile.compile('app/search_client.py', doraise=True); print('OK')"
node -e "try{new Function(require('fs').readFileSync('app/static/js/codeWindow.js','utf8'));console.log('OK')}catch(e){console.error(e.message);process.exit(1)}"
sudo systemctl restart xelasphere
```

```bash
git add -A && git commit -m "refactor: delete old search detection — regex, checkbox, sidebar sources

Remove searchAutoDetect.js, should_auto_enable_search(), enable_search
checkbox, sidebar source panel, TF-IDF scoring. Search auto-trigger is
disabled until ACTION: web_search is activated in a later task."
```

---

## Task 2: Add Dependencies and Config

**Files:**
- Modify: `requirements-cpu.txt`
- Modify: `app/config.py:53`

- [ ] **Step 1: Add markdownify to requirements**

Add to `requirements-cpu.txt`:
```
markdownify==0.14.1
```

- [ ] **Step 2: Install it**

```bash
pip install markdownify==0.14.1
```

- [ ] **Step 3: Add search config to Config class**

In `app/config.py`, add after the `BRAVE_SEARCH_API_KEY` line:

```python
YOUTUBE_API_KEY = os.environ.get('YOUTUBE_API_KEY', '')

SEARCH_CONTEXT_BUDGET = {
    'deepseek': 30_000,
    'llama': 20_000,
    'gpt-4': 60_000,
    'kimi': 80_000,
    'qwen': 40_000,
    'default': 40_000,
}

SEARCH_PREAPPROVED_DOMAINS = [
    'wikipedia.org', 'en.wikipedia.org', 'bbc.com', 'bbc.co.uk',
    'reuters.com', 'nytimes.com', 'theguardian.com', 'techcrunch.com',
    'github.com', 'stackoverflow.com', 'developer.mozilla.org',
    'docs.python.org', 'arxiv.org', 'weather.com', 'apnews.com',
]

BLOCKED_IMAGE_DOMAINS = []

FETCH_DEADLINE_MS = 2000
```

- [ ] **Step 4: Commit**

```bash
git add requirements-cpu.txt app/config.py
git commit -m "config: add YouTube API key, search context budgets, preapproved domains"
```

---

## Task 3: Build WebFetcher

**Files:**
- Create: `app/services/web_fetcher.py`
- Create: `tests/test_web_fetcher.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_web_fetcher.py`:

```python
"""Tests for WebFetcher — URL fetching, HTML-to-Markdown, caching."""
import pytest
from unittest.mock import patch, MagicMock
from app.services.web_fetcher import WebFetcher, url_cache, domain_cache


class TestOgImageExtraction:
    def test_extracts_og_image(self):
        html = '<html><head><meta property="og:image" content="https://example.com/img.jpg"></head><body></body></html>'
        fetcher = WebFetcher()
        meta = fetcher._extract_meta(html)
        assert meta['og_image'] == 'https://example.com/img.jpg'

    def test_fallback_to_large_img(self):
        html = '<html><body><img src="https://example.com/small.jpg" width="50"><img src="https://example.com/hero.jpg" width="400"></body></html>'
        fetcher = WebFetcher()
        meta = fetcher._extract_meta(html)
        assert meta['og_image'] == 'https://example.com/hero.jpg'

    def test_no_image_found(self):
        html = '<html><body><p>No images here</p></body></html>'
        fetcher = WebFetcher()
        meta = fetcher._extract_meta(html)
        assert meta['og_image'] == ''


class TestHtmlToMarkdown:
    def test_strips_nav_footer_script(self):
        html = '<html><body><nav>Menu</nav><main><p>Content</p></main><footer>Footer</footer><script>js()</script></body></html>'
        fetcher = WebFetcher()
        md = fetcher._html_to_markdown(html)
        assert 'Content' in md
        assert 'Menu' not in md
        assert 'Footer' not in md
        assert 'js()' not in md

    def test_truncates_to_budget(self):
        html = '<html><body><p>' + 'A' * 50000 + '</p></body></html>'
        fetcher = WebFetcher()
        md = fetcher._html_to_markdown(html, max_chars=1000)
        assert len(md) <= 1050  # small buffer for truncation marker
        assert '[Content truncated' in md


class TestCaching:
    def test_cache_hit_skips_fetch(self):
        fetcher = WebFetcher()
        url_cache['https://example.com/cached'] = {
            'content': '# Cached Content',
            'og_image': 'https://example.com/img.jpg',
            'og_title': 'Cached',
        }
        result = fetcher.fetch('https://example.com/cached')
        assert result['content'] == '# Cached Content'
        assert result['cached'] is True

    def test_cache_miss_fetches(self):
        fetcher = WebFetcher()
        url_cache.clear()
        with patch('app.services.web_fetcher.requests.get') as mock_get:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.headers = {'content-type': 'text/html'}
            mock_resp.text = '<html><body><p>Fresh content</p></body></html>'
            mock_resp.content = mock_resp.text.encode()
            mock_get.return_value = mock_resp
            result = fetcher.fetch('https://example.com/fresh')
            assert 'Fresh content' in result['content']
            assert result['cached'] is False


class TestPreapprovedDomains:
    def test_preapproved_skips_domain_check(self):
        fetcher = WebFetcher()
        assert fetcher._is_preapproved('https://wikipedia.org/wiki/Test')
        assert fetcher._is_preapproved('https://en.wikipedia.org/wiki/Test')
        assert not fetcher._is_preapproved('https://malware.com/bad')


class TestErrorHandling:
    def test_timeout_returns_empty(self):
        fetcher = WebFetcher()
        with patch('app.services.web_fetcher.requests.get', side_effect=Exception('Timeout')):
            result = fetcher.fetch('https://slow.com')
            assert result['content'] == ''
            assert result['error'] is not None
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /root/xelas.io/xelasphere.io-
python3 -m pytest tests/test_web_fetcher.py -v 2>&1 | head -30
```
Expected: ImportError or ModuleNotFoundError for `app.services.web_fetcher`

- [ ] **Step 3: Implement WebFetcher**

Create `app/services/web_fetcher.py`:

```python
"""WebFetcher — full page content fetching with HTML-to-Markdown and 2-tier LRU cache.

Following claude-code's WebFetchTool pattern:
- Fetch URL content with timeout and size limits
- Convert HTML to Markdown via markdownify
- Extract og:image, og:title metadata
- 2-tier LRU cache: URL cache (15min) + domain cache (5min)
- Preapproved domains skip domain checks
"""

import logging
import re
from dataclasses import dataclass
from typing import Dict, Optional
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup
from cachetools import TTLCache
from markdownify import markdownify as md

from app.config import Config

logger = logging.getLogger(__name__)

# 2-tier cache
url_cache: TTLCache = TTLCache(maxsize=200, ttl=900)    # 15 min, ~4MB per worker
domain_cache: TTLCache = TTLCache(maxsize=128, ttl=300)  # 5 min

FETCH_TIMEOUT = 10       # seconds
MAX_CONTENT_BYTES = 5 * 1024 * 1024  # 5MB
USER_AGENT = 'XelaSphere/1.0 (Search Bot)'
MAX_REDIRECTS = 5

# Tags to strip before markdown conversion
STRIP_TAGS = ['nav', 'footer', 'aside', 'script', 'style', 'noscript',
              'iframe', 'form', 'button', 'svg', 'header']


class WebFetcher:
    """Fetches URL content, converts to markdown, caches results."""

    def __init__(self):
        self.preapproved = set()
        for domain in getattr(Config, 'SEARCH_PREAPPROVED_DOMAINS', []):
            self.preapproved.add(domain.lower())
            if not domain.startswith('www.'):
                self.preapproved.add(f'www.{domain.lower()}')

    def fetch(self, url: str, max_chars: int = 20_000) -> Dict:
        """Fetch a URL and return markdown content + metadata.

        Returns:
            {content, og_image, og_title, description, cached, error}
        """
        cached = url_cache.get(url)
        if cached is not None:
            logger.info(f"WebFetcher: cache hit for {url[:80]}")
            return {**cached, 'cached': True}

        result = {
            'content': '', 'og_image': '', 'og_title': '',
            'description': '', 'cached': False, 'error': None,
        }

        try:
            fetch_url = url
            if fetch_url.startswith('http://'):
                fetch_url = 'https://' + fetch_url[7:]

            resp = requests.get(
                fetch_url, timeout=FETCH_TIMEOUT,
                headers={'User-Agent': USER_AGENT, 'Accept': 'text/html, text/plain, */*'},
                allow_redirects=True,
            )
            resp.raise_for_status()

            content_length = int(resp.headers.get('content-length', 0))
            if content_length > MAX_CONTENT_BYTES:
                result['error'] = f'Content too large: {content_length} bytes'
                return result

            content_type = resp.headers.get('content-type', '')
            raw = resp.text

            if 'text/html' in content_type:
                meta = self._extract_meta(raw)
                result['og_image'] = meta['og_image']
                result['og_title'] = meta['og_title']
                result['description'] = meta['description']
                result['content'] = self._html_to_markdown(raw, max_chars=max_chars)
            elif 'text/plain' in content_type:
                result['content'] = raw[:max_chars]
            else:
                result['error'] = f'Unsupported content type: {content_type}'
                return result

            cache_entry = {
                'content': result['content'], 'og_image': result['og_image'],
                'og_title': result['og_title'], 'description': result['description'],
            }
            url_cache[url] = cache_entry

        except Exception as e:
            logger.warning(f"WebFetcher: failed to fetch {url[:80]}: {e}")
            result['error'] = str(e)

        return result

    def _extract_meta(self, html: str) -> Dict[str, str]:
        """Extract og:image, og:title, description from HTML."""
        soup = BeautifulSoup(html, 'html.parser')
        meta = {'og_image': '', 'og_title': '', 'description': ''}

        og_img = soup.find('meta', property='og:image')
        if og_img and og_img.get('content', '').startswith('http'):
            meta['og_image'] = og_img['content']
        else:
            for img in soup.find_all('img', src=True):
                width = img.get('width', '')
                try:
                    if int(width) > 200 and img['src'].startswith('http'):
                        meta['og_image'] = img['src']
                        break
                except (ValueError, TypeError):
                    continue

        og_title = soup.find('meta', property='og:title')
        if og_title:
            meta['og_title'] = og_title.get('content', '')

        desc = soup.find('meta', attrs={'name': 'description'})
        if desc:
            meta['description'] = desc.get('content', '')

        return meta

    def _html_to_markdown(self, html: str, max_chars: int = 20_000) -> str:
        """Convert HTML to clean markdown, stripping navigation/ads."""
        soup = BeautifulSoup(html, 'html.parser')

        for tag_name in STRIP_TAGS:
            for tag in soup.find_all(tag_name):
                tag.decompose()

        for cls in ['cookie', 'banner', 'popup', 'modal', 'sidebar', 'advertisement', 'ad-']:
            for el in soup.find_all(class_=lambda c: c and cls in str(c).lower()):
                el.decompose()

        markdown = md(str(soup), heading_style='ATX', strip=['img'])
        markdown = re.sub(r'\n{3,}', '\n\n', markdown).strip()

        if len(markdown) > max_chars:
            markdown = markdown[:max_chars] + '\n\n[Content truncated due to length...]'

        return markdown

    def _is_preapproved(self, url: str) -> bool:
        """Check if URL's domain is in the preapproved list."""
        hostname = urlparse(url).hostname or ''
        return hostname.lower() in self.preapproved

    def fetch_multiple(self, urls: list, max_chars_per_page: int = 20_000,
                       deadline_seconds: float = 2.0) -> list:
        """Fetch multiple URLs concurrently with a deadline.

        Returns:
            List of fetch results (same order as urls, empty for failures)
        """
        import concurrent.futures
        results = [None] * len(urls)

        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
            future_to_idx = {
                executor.submit(self.fetch, url, max_chars_per_page): i
                for i, url in enumerate(urls)
            }
            done, _ = concurrent.futures.wait(
                future_to_idx.keys(), timeout=deadline_seconds,
            )
            for future in done:
                idx = future_to_idx[future]
                try:
                    results[idx] = future.result()
                except Exception as e:
                    logger.warning(f"WebFetcher: fetch failed for {urls[idx][:60]}: {e}")
                    results[idx] = {'content': '', 'og_image': '', 'error': str(e), 'cached': False}

        for i in range(len(results)):
            if results[i] is None:
                results[i] = {'content': '', 'og_image': '', 'error': 'Fetch deadline exceeded', 'cached': False}

        return results
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /root/xelas.io/xelasphere.io-
python3 -m pytest tests/test_web_fetcher.py -v
```
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add app/services/web_fetcher.py tests/test_web_fetcher.py
git commit -m "feat: add WebFetcher — HTML-to-Markdown, og:image extraction, 2-tier LRU cache"
```

---

## Task 4: Build SearchOrchestrator

**Files:**
- Create: `app/services/search_orchestrator.py`
- Create: `tests/test_search_orchestrator.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_search_orchestrator.py`:

```python
"""Tests for SearchOrchestrator — multi-backend search coordination."""
import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from app.services.search_orchestrator import (
    SearchOrchestrator, SearchResult, search_cache,
    _score_web_result, _score_video_result,
)


class TestRelevanceScoring:
    def test_web_score_position_and_freshness(self):
        score = _score_web_result(position=0, age_hours=12)
        assert 1.1 < score <= 1.2

        score = _score_web_result(position=5, age_hours=200)
        assert 0.4 < score < 0.6

    def test_video_score_with_views(self):
        score = _score_video_result(position=0, view_count=1_000_000)
        assert score > 1.0

        score = _score_video_result(position=0, view_count=10)
        assert score < 1.15


class TestSearchResultDataclass:
    def test_web_result_creation(self):
        r = SearchResult(
            type='web', title='Test', url='https://test.com',
            snippet='A snippet', favicon='https://favicon.com/test.ico',
            thumbnail='', full_content='', video_id='', channel='',
            duration=None, source_domain='test.com', relevance_score=0.9,
        )
        assert r.type == 'web'
        assert r.relevance_score == 0.9

    def test_video_result_creation(self):
        r = SearchResult(
            type='video', title='Video', url='https://youtube.com/watch?v=abc',
            snippet='', favicon='', thumbnail='https://i.ytimg.com/vi/abc/hq.jpg',
            full_content='', video_id='abc', channel='TestChannel',
            duration='5:32', source_domain='youtube.com', relevance_score=0.8,
        )
        assert r.video_id == 'abc'
        assert r.duration == '5:32'


class TestCacheIntegration:
    def test_cache_stores_and_retrieves(self):
        search_cache.clear()
        key = 'general:test query'
        results = [SearchResult(
            type='web', title='Cached', url='https://cached.com',
            snippet='cached', favicon='', thumbnail='', full_content='',
            video_id='', channel='', duration=None,
            source_domain='cached.com', relevance_score=1.0,
        )]
        search_cache[key] = {'web': results, 'images': [], 'videos': []}
        cached = search_cache.get(key)
        assert cached is not None
        assert cached['web'][0].title == 'Cached'
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python3 -m pytest tests/test_search_orchestrator.py -v 2>&1 | head -20
```
Expected: ImportError

- [ ] **Step 3: Implement SearchOrchestrator**

Create `app/services/search_orchestrator.py`:

```python
"""SearchOrchestrator — multi-backend search coordination with progressive streaming.

Coordinates Brave Web, Brave Images, and YouTube Data API in parallel.
Streams results as they arrive via SSE events.
2-tier caching: search-level cache (15min) on top of WebFetcher's URL cache.
"""

import asyncio
import logging
import math
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Generator, List, Optional
from urllib.parse import quote_plus, urlparse

import requests
from cachetools import TTLCache

from app.config import Config
from app.services.web_fetcher import WebFetcher

logger = logging.getLogger(__name__)

search_cache: TTLCache = TTLCache(maxsize=100, ttl=900)

BRAVE_WEB_URL = 'https://api.search.brave.com/res/v1/web/search'
BRAVE_IMAGE_URL = 'https://api.search.brave.com/res/v1/images/search'
YOUTUBE_SEARCH_URL = 'https://www.googleapis.com/youtube/v3/search'
YOUTUBE_VIDEOS_URL = 'https://www.googleapis.com/youtube/v3/videos'

BLOCKED_IMAGE_DOMAINS = set(getattr(Config, 'BLOCKED_IMAGE_DOMAINS', []))


@dataclass
class SearchResult:
    type: str
    title: str
    url: str
    snippet: str = ''
    favicon: str = ''
    thumbnail: str = ''
    full_content: str = ''
    video_id: str = ''
    channel: str = ''
    duration: Optional[str] = None
    source_domain: str = ''
    relevance_score: float = 0.0


@dataclass
class SearchEvent:
    type: str
    data: Dict[str, Any] = field(default_factory=dict)


def _score_web_result(position: int, age_hours: float = 999) -> float:
    base = max(0.0, 1.0 - (position * 0.1))
    if age_hours < 24:
        return base * 1.2
    elif age_hours < 168:
        return base * 1.1
    return base


def _score_video_result(position: int, view_count: int = 0) -> float:
    base = max(0.0, 1.0 - (position * 0.1))
    if view_count > 0:
        view_boost = 1.0 + min(math.log10(max(view_count, 1)), 2) * 0.1
        return base * view_boost
    return base


def _parse_iso8601_duration(duration_str: str) -> Optional[str]:
    if not duration_str:
        return None
    match = re.match(r'PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?', duration_str)
    if not match:
        return None
    h, m, s = int(match.group(1) or 0), int(match.group(2) or 0), int(match.group(3) or 0)
    if h > 0:
        return f'{h}:{m:02d}:{s:02d}'
    return f'{m}:{s:02d}'


class SearchOrchestrator:

    def __init__(self):
        self.web_fetcher = WebFetcher()
        self._last_results = {}
        self._fetched_urls = set()  # URLs that received full content fetch

    def search_progressive(self, query: str, search_type: str = 'general',
                           model: str = 'default') -> Generator[SearchEvent, None, None]:
        cache_key = f'{search_type}:{query.lower().strip()}'
        cached = search_cache.get(cache_key)
        if cached is not None:
            logger.info(f"SearchOrchestrator: cache hit for '{query[:50]}'")
            if cached.get('web'):
                yield SearchEvent('search_web_results', {'results': [self._result_to_dict(r) for r in cached['web']]})
            if cached.get('images'):
                yield SearchEvent('search_images', {'images': [self._result_to_dict(r) for r in cached['images']]})
            if cached.get('videos'):
                yield SearchEvent('search_videos', {'videos': [self._result_to_dict(r) for r in cached['videos']]})
            total = sum(len(v) for v in cached.values())
            yield SearchEvent('search_complete', {'total_results': total, 'cached': True})
            self._last_results = cached
            self._fetched_urls = {r.url for r in cached.get('web', []) if r.full_content}
            return

        web_results = []
        image_results = []
        video_results = []
        self._fetched_urls = set()

        if search_type in ('general', 'news'):
            web_results = self._search_brave_web(query, search_type)
            if web_results:
                yield SearchEvent('search_web_results', {'results': [self._result_to_dict(r) for r in web_results]})

        if search_type in ('general', 'images'):
            image_results = self._search_brave_images(query)
            if image_results:
                yield SearchEvent('search_images', {'images': [self._result_to_dict(r) for r in image_results]})

        if search_type in ('general', 'video'):
            video_results = self._search_youtube(query)
            if video_results:
                yield SearchEvent('search_videos', {'videos': [self._result_to_dict(r) for r in video_results]})

        if web_results:
            budget = Config.SEARCH_CONTEXT_BUDGET.get(
                self._model_family(model),
                Config.SEARCH_CONTEXT_BUDGET.get('default', 40_000)
            )
            per_page = budget // 3
            top_urls = [r.url for r in web_results[:3]]
            deadline = getattr(Config, 'FETCH_DEADLINE_MS', 2000) / 1000.0

            fetch_results = self.web_fetcher.fetch_multiple(top_urls, max_chars_per_page=per_page, deadline_seconds=deadline)

            for i, (url, fetched) in enumerate(zip(top_urls, fetch_results)):
                if fetched and fetched.get('content'):
                    web_results[i].full_content = fetched['content']
                    self._fetched_urls.add(url)
                    if fetched.get('og_image') and not web_results[i].thumbnail:
                        web_results[i].thumbnail = fetched['og_image']
                    yield SearchEvent('search_content', {'url': url})

        merged = {'web': web_results, 'images': image_results, 'videos': video_results}
        search_cache[cache_key] = merged
        self._last_results = merged

        total = len(web_results) + len(image_results) + len(video_results)
        yield SearchEvent('search_complete', {'total_results': total, 'cached': False})

    def get_context_for_ai(self, query: str) -> str:
        results = self._last_results
        parts = []

        for i, r in enumerate(results.get('web', [])[:5]):
            source_label = f'[Source {i+1}: {r.source_domain} - "{r.title}"]'
            if r.full_content:
                parts.append(f'{source_label}\n{r.full_content}')
            else:
                parts.append(f'{source_label}\n{r.snippet}')

        images = results.get('images', [])
        if images:
            img_lines = [f'- {img.title} ({img.source_domain}): {img.thumbnail}' for img in images[:5]]
            parts.append(f'Web images:\n' + '\n'.join(img_lines))

        videos = results.get('videos', [])
        if videos:
            vid_lines = [f'- {v.title} ({v.channel}): https://youtube.com/watch?v={v.video_id}' for v in videos[:3]]
            parts.append(f'Videos:\n' + '\n'.join(vid_lines))

        parts.append(
            'IMPORTANT: Reference sources using [1], [2], [3] notation inline. '
            'Every factual claim from search results must have a citation.'
        )

        return '\n\n'.join(parts)

    def get_fetched_urls(self) -> set:
        """Return URLs that received full content fetch (for source card badges)."""
        return self._fetched_urls

    def _search_brave_web(self, query: str, search_type: str = 'general') -> List[SearchResult]:
        api_key = getattr(Config, 'BRAVE_SEARCH_API_KEY', '')
        if not api_key:
            logger.warning("SearchOrchestrator: BRAVE_SEARCH_API_KEY not set")
            return []

        params = {'q': query, 'count': 7, 'country': 'us', 'search_lang': 'en', 'safesearch': 'moderate'}
        if search_type == 'news':
            params['freshness'] = 'pd'

        try:
            resp = requests.get(
                BRAVE_WEB_URL, params=params,
                headers={'X-Subscription-Token': api_key, 'Accept': 'application/json'},
                timeout=5,
            )
            resp.raise_for_status()
            data = resp.json()

            results = []
            for i, item in enumerate(data.get('web', {}).get('results', [])):
                domain = urlparse(item.get('url', '')).hostname or ''
                age_hours = self._parse_age_to_hours(item.get('age', ''))
                results.append(SearchResult(
                    type='web', title=item.get('title', ''), url=item.get('url', ''),
                    snippet=item.get('description', ''),
                    favicon=f'https://www.google.com/s2/favicons?domain={domain}&sz=32',
                    source_domain=domain,
                    relevance_score=_score_web_result(i, age_hours),
                ))
            return results
        except Exception as e:
            logger.error(f"Brave Web search failed: {e}")
            return []

    def _search_brave_images(self, query: str) -> List[SearchResult]:
        api_key = getattr(Config, 'BRAVE_SEARCH_API_KEY', '')
        if not api_key:
            return []

        try:
            resp = requests.get(
                BRAVE_IMAGE_URL,
                params={'q': query, 'count': 8, 'safesearch': 'moderate'},
                headers={'X-Subscription-Token': api_key, 'Accept': 'application/json'},
                timeout=5,
            )
            resp.raise_for_status()
            data = resp.json()

            results = []
            for i, item in enumerate(data.get('results', [])):
                domain = urlparse(item.get('source', '')).hostname or ''
                thumb = item.get('thumbnail', {}).get('src', '') or item.get('url', '')
                if domain in BLOCKED_IMAGE_DOMAINS or not thumb.startswith('https://'):
                    continue
                results.append(SearchResult(
                    type='image', title=item.get('title', ''), url=item.get('source', ''),
                    thumbnail=thumb, source_domain=domain,
                    relevance_score=max(0.0, 1.0 - (i * 0.1)),
                ))
            return results
        except Exception as e:
            logger.error(f"Brave Image search failed: {e}")
            return []

    def _search_youtube(self, query: str) -> List[SearchResult]:
        api_key = getattr(Config, 'YOUTUBE_API_KEY', '')
        if not api_key:
            logger.warning("SearchOrchestrator: YOUTUBE_API_KEY not set")
            return []

        try:
            resp = requests.get(
                YOUTUBE_SEARCH_URL,
                params={'part': 'snippet', 'q': query, 'type': 'video', 'maxResults': 5, 'key': api_key},
                timeout=5,
            )
            resp.raise_for_status()
            search_data = resp.json()

            results = []
            video_ids = []
            for i, item in enumerate(search_data.get('items', [])):
                vid_id = item.get('id', {}).get('videoId', '')
                snippet = item.get('snippet', {})
                if not vid_id:
                    continue
                video_ids.append(vid_id)
                results.append(SearchResult(
                    type='video', title=snippet.get('title', ''),
                    url=f'https://www.youtube.com/watch?v={vid_id}',
                    snippet=snippet.get('description', ''),
                    thumbnail=snippet.get('thumbnails', {}).get('high', {}).get('url', ''),
                    video_id=vid_id, channel=snippet.get('channelTitle', ''),
                    source_domain='youtube.com',
                    relevance_score=max(0.0, 1.0 - (i * 0.1)),
                ))

            if video_ids:
                try:
                    vid_resp = requests.get(
                        YOUTUBE_VIDEOS_URL,
                        params={'part': 'contentDetails,statistics', 'id': ','.join(video_ids), 'key': api_key},
                        timeout=5,
                    )
                    vid_resp.raise_for_status()
                    vid_map = {}
                    for item in vid_resp.json().get('items', []):
                        vid_map[item['id']] = {
                            'duration': _parse_iso8601_duration(item.get('contentDetails', {}).get('duration', '')),
                            'views': int(item.get('statistics', {}).get('viewCount', 0)),
                        }
                    for r in results:
                        info = vid_map.get(r.video_id, {})
                        r.duration = info.get('duration')
                        idx = results.index(r)
                        r.relevance_score = _score_video_result(idx, info.get('views', 0))
                except Exception as e:
                    logger.warning(f"YouTube video details failed: {e}")

            return results
        except Exception as e:
            logger.error(f"YouTube search failed: {e}")
            return []

    def _result_to_dict(self, result: SearchResult) -> dict:
        return {
            'type': result.type, 'title': result.title, 'url': result.url,
            'snippet': result.snippet, 'favicon': result.favicon,
            'thumbnail': result.thumbnail, 'video_id': result.video_id,
            'channel': result.channel, 'duration': result.duration,
            'source_domain': result.source_domain, 'relevance_score': result.relevance_score,
        }

    @staticmethod
    def _model_family(model_id: str) -> str:
        m = (model_id or '').lower()
        for key in ('deepseek', 'llama', 'gpt', 'kimi', 'qwen'):
            if key in m:
                return 'gpt-4' if key == 'gpt' else key
        return 'default'

    @staticmethod
    def _parse_age_to_hours(age_str: str) -> float:
        if not age_str:
            return 999
        match = re.search(r'(\d+)\s*(hour|day|week|month|minute)', age_str.lower())
        if not match:
            return 999
        num, unit = int(match.group(1)), match.group(2)
        multipliers = {'minute': 1/60, 'hour': 1, 'day': 24, 'week': 168, 'month': 720}
        return num * multipliers.get(unit, 999)


search_orchestrator = SearchOrchestrator()
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python3 -m pytest tests/test_search_orchestrator.py -v
```
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add app/services/search_orchestrator.py tests/test_search_orchestrator.py
git commit -m "feat: add SearchOrchestrator — Brave Web + Images + YouTube, progressive streaming, caching"
```

---

## Task 5: Add ACTION: web_search to Parser + Streaming Handler

**Files:**
- Modify: `app/context/action_parser.py:29,32,38-49`
- Modify: `app/blueprints/chat_routes.py`
- Create: `tests/test_action_parser_search.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_action_parser_search.py`:

```python
"""Tests for web_search ACTION parsing."""
import sys
import importlib.util

spec = importlib.util.spec_from_file_location('action_parser', 'app/context/action_parser.py')
mod = importlib.util.module_from_spec(spec)
sys.modules['action_parser'] = mod
spec.loader.exec_module(mod)
ActionParser = mod.ActionParser
StreamingActionBuffer = mod.StreamingActionBuffer


class TestActionParserWebSearch:
    def test_parses_web_search_action(self):
        resp = 'ACTION: web_search\nquery: weather in London\nsearch_type: general\nreason: real-time weather\n\nHere are the results...'
        r = ActionParser.parse(resp)
        assert r.action == 'web_search'
        assert r.search_payload['query'] == 'weather in London'
        assert r.search_payload['search_type'] == 'general'
        assert r.search_payload['reason'] == 'real-time weather'
        assert 'results' in r.chat_text

    def test_web_search_with_embed_video(self):
        resp = 'ACTION: web_search\nquery: how to make pasta\nsearch_type: general\nreason: recipe lookup\nembed_video: abc123\n\nHere is how...'
        r = ActionParser.parse(resp)
        assert r.search_payload['embed_video'] == 'abc123'

    def test_web_search_defaults(self):
        resp = 'ACTION: web_search\nquery: test\nreason: testing\n\nText'
        r = ActionParser.parse(resp)
        assert r.search_payload['search_type'] == 'general'
        assert r.search_payload.get('embed_video') is None

    def test_web_search_missing_query_falls_back(self):
        resp = 'ACTION: web_search\nreason: no query\n\nText'
        r = ActionParser.parse(resp)
        assert r.action == 'chat'

    def test_web_search_missing_reason_still_works(self):
        resp = 'ACTION: web_search\nquery: test query\n\nText'
        r = ActionParser.parse(resp)
        assert r.action == 'web_search'
        assert r.search_payload['reason'] == ''


class TestStreamingBufferWebSearch:
    def test_chunk_by_chunk(self):
        buf = StreamingActionBuffer()
        assert buf.feed('ACTION: web_search\n') is None
        assert buf.feed('query: test query\n') is None
        assert buf.feed('search_type: news\n') is None
        assert buf.feed('reason: current events\n') is None
        r = buf.feed('\n')
        assert r == 'web_search'
        assert buf.header_fields['query'] == 'test query'
        assert buf.header_fields['search_type'] == 'news'

    def test_all_at_once(self):
        buf = StreamingActionBuffer()
        r = buf.feed('ACTION: web_search\nquery: hello\nreason: test\n\nText here')
        assert r == 'web_search'
        assert buf.header_fields['query'] == 'hello'
        assert 'Text here' in buf.remainder
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python3 -m pytest tests/test_action_parser_search.py -v 2>&1 | head -20
```
Expected: FAIL — `search_payload` not in ParsedAction

- [ ] **Step 3: Add web_search to action_parser.py**

In `app/context/action_parser.py`:

Add `"web_search"` to `_VALID_ACTIONS` (line 29):
```python
_VALID_ACTIONS = {"chat", "create_artifact", "patch_artifact", "show_chart", "generate_image", "web_search"}
```

Add `"web_search"` to `_MULTILINE_HEADER_ACTIONS` (line 32):
```python
_MULTILINE_HEADER_ACTIONS = {"generate_image", "web_search"}
```

Add `search_payload` field to `ParsedAction` (after `image_payload`):
```python
    search_payload: Optional[Dict[str, str]] = None  # {query, search_type, reason, embed_video}
```

Add handling in `ActionParser.parse()`, after the `generate_image` elif:
```python
        elif action == "web_search":
            query = header_fields.get("query", "").strip()
            if query:
                search_payload = {
                    "query": query,
                    "search_type": header_fields.get("search_type", "general").strip(),
                    "reason": header_fields.get("reason", "").strip(),
                    "embed_video": header_fields.get("embed_video", "").strip() or None,
                }
            else:
                logger.warning("web_search ACTION missing query, falling back to chat")
                action = "chat"
```

Add `search_payload=search_payload` to the `ParsedAction` return. Initialize `search_payload = None` alongside `image_payload`.

- [ ] **Step 4: Run tests to verify they pass**

```bash
python3 -m pytest tests/test_action_parser_search.py -v
```
Expected: All tests PASS

- [ ] **Step 5: Add web_search handling in chat_routes.py streaming generator**

In `app/blueprints/chat_routes.py`, add import at top:
```python
from app.services.search_orchestrator import search_orchestrator
```

In the streaming action buffer resolution block (where `generate_image` is handled), add after the generate_image block:
```python
                            # web_search: fire progressive search
                            if result == "web_search" and action_buffer.header_fields.get("query"):
                                ws_query = action_buffer.header_fields["query"]
                                ws_type = action_buffer.header_fields.get("search_type", "general")
                                ws_reason = action_buffer.header_fields.get("reason", "")
                                logger.info(f"[Stream] web_search: query='{ws_query}', type={ws_type}, reason='{ws_reason}'")

                                yield f"data: {json.dumps({'type': 'search_started', 'query': ws_query, 'search_type': ws_type})}\n\n"

                                for event in search_orchestrator.search_progressive(ws_query, ws_type, stream_model):
                                    yield f"data: {json.dumps({'type': event.type, **event.data})}\n\n"
```

In the `done` handler, initialize `_search_done_data = {}` alongside `_image_done_data = {}`.

Add the web_search branch (after `generate_image`, before the `else` fallback):
```python
                        elif parsed_action.action == "web_search" and parsed_action.search_payload:
                            search_context = search_orchestrator.get_context_for_ai(
                                parsed_action.search_payload['query']
                            )
                            chat_summary = parsed_action.chat_text
                            _search_done_data = {
                                'web': [search_orchestrator._result_to_dict(r) for r in search_orchestrator._last_results.get('web', [])],
                                'images': [search_orchestrator._result_to_dict(r) for r in search_orchestrator._last_results.get('images', [])],
                                'videos': [search_orchestrator._result_to_dict(r) for r in search_orchestrator._last_results.get('videos', [])],
                                'fetched_urls': list(search_orchestrator.get_fetched_urls()),
                                'embed_video': parsed_action.search_payload.get('embed_video'),
                            }
                            routing = ArtifactDecision(
                                render_mode="inline",
                                chat_payload=chat_summary,
                                artifact_payload=None,
                                contains_code=False,
                                desired_action=ArtifactPreference.NONE,
                            )
```

Merge into done_payload (alongside image merge):
```python
                    if _search_done_data:
                        done_payload['search_results'] = _search_done_data
                        done_payload['embed_video'] = _search_done_data.get('embed_video')
                        done_payload['fetched_urls'] = _search_done_data.get('fetched_urls', [])
```

- [ ] **Step 6: Remove old search code from chat_routes.py**

Remove the `enable_search` param extraction, the `perform_web_search()` call, and the search result injection that were commented out in Task 1. Also remove the `should_auto_enable_search` import if present.

- [ ] **Step 7: Verify and commit**

```bash
python3 -c "import py_compile; py_compile.compile('app/context/action_parser.py', doraise=True); py_compile.compile('app/blueprints/chat_routes.py', doraise=True); print('OK')"
python3 -m pytest tests/test_action_parser_search.py -v
```

```bash
git add app/context/action_parser.py app/blueprints/chat_routes.py tests/test_action_parser_search.py
git commit -m "feat: add ACTION: web_search to parser + streaming handler with progressive SSE"
```

---

## Task 6: Build Frontend Search Components

**Files:**
- Create: `app/static/js/searchResults.js`
- Create: `app/static/css/search-results.css`
- Modify: `app/static/js/codeWindow.js`
- Modify: `app/templates/index.html`

- [ ] **Step 1: Create search-results.css**

Create `app/static/css/search-results.css` with styles for: search banner with spinner, image carousel (CSS scroll-snap), video embed with meta, source cards with favicon/badge/highlight animation, video cards grid, citation links, image lightbox, and responsive breakpoints. (Full CSS in spec Section 5.)

- [ ] **Step 2: Create searchResults.js**

Create `app/static/js/searchResults.js` with:

- `handleSearchEvent(container, eventType, data)` — processes SSE events during streaming
- `renderFinalResults(messageEl, searchResults, embedVideoId, fetchedUrls)` — renders complete results after done event
- `processCitations(messageEl)` — converts `[1]`, `[2]` text to clickable citation links
- `highlightSource(num)` — highlights source card, handles pending highlights when cards don't exist yet
- `createSearchBanner(query, searchType)` — "Searching: ..." with spinner
- `createImageCarousel(images)` — horizontal scroll with lightbox on click, `onerror` removes broken images
- `createVideoEmbed(videoId, title, channel)` — YouTube iframe
- `createSourceCards(webResults, fetchedUrls)` — favicon + title + domain + checkmark badge for fetched URLs
- `createVideoCards(videos)` — thumbnail grid with duration overlay

Key fix from review: `createSourceCards` receives `fetchedUrls` (a Set of URLs that received full content fetch) and checks it when rendering the checkmark badge — not `r.full_content` which is only available server-side.

```javascript
function createSourceCards(webResults, fetchedUrls) {
    const fetchedSet = new Set(fetchedUrls || []);
    // ...
    webResults.forEach((r, i) => {
        const hasFull = fetchedSet.has(r.url);
        // ... badge: hasFull ? '<span class="search-source-card__badge">&#10003;</span>' : ''
    });
}
```

- [ ] **Step 3: Add SSE handlers in codeWindow.js**

In the `processLine` function, add handlers for all `search_*` event types (before the `done` handler). These delegate to `window.SearchResults.handleSearchEvent()`.

In the `done` handler, store `chunk.search_results`, `chunk.embed_video`, and `chunk.fetched_urls` for post-stream rendering.

In the post-stream section, call `window.SearchResults.renderFinalResults()` with the stored data and the message container element.

- [ ] **Step 4: Add CSS and JS to index.html**

Add `<link>` for `search-results.css` and `<script>` for `searchResults.js`.

- [ ] **Step 5: Remove old search sidebar code from codeWindow.js**

Remove `populateRightSidebar()` function.

- [ ] **Step 6: Verify and commit**

```bash
node -e "try{new Function(require('fs').readFileSync('app/static/js/codeWindow.js','utf8'));console.log('OK')}catch(e){console.error(e.message);process.exit(1)}"
node -e "try{new Function(require('fs').readFileSync('app/static/js/searchResults.js','utf8'));console.log('OK')}catch(e){console.error(e.message);process.exit(1)}"
```

```bash
git add app/static/js/searchResults.js app/static/css/search-results.css app/static/js/codeWindow.js app/templates/index.html
git commit -m "feat: inline search results UI — image carousel, video embed/cards, source cards, citations"
```

---

## Task 7: Add web_search to System Prompt (Feature Flag)

**Files:**
- Modify: `app/services/chat_engine.py`

- [ ] **Step 1: Add web_search to system prompt**

In `app/services/chat_engine.py`, after the `generate_image` examples section, add:

```python
    "ACTION: web_search\n"
    "  query: <search query, resolve all pronouns from context>\n"
    "  search_type: general | images | video | news\n"
    "  reason: <why search is needed, logged server-side only>\n"
    "  embed_video: <YouTube video ID> (optional, model picks most relevant)\n\n"
    "  Use when: user asks about current events, real-time data, facts you're\n"
    "  not confident about, prices, weather, news, 'search for', 'look up',\n"
    "  or any question where up-to-date information improves your answer.\n\n"
    "  NEVER use web_search for:\n"
    "  - Questions you can answer confidently from training data\n"
    "  - Creative tasks (writing, coding, brainstorming)\n"
    "  - Explaining concepts you know well\n"
    "  - Conversational responses ('how are you', 'thank you')\n\n"
    "  After search results are injected, you MUST cite sources inline using\n"
    "  [1], [2], [3] notation. Every factual claim from search results must\n"
    "  have a citation.\n\n"

    "EXAMPLE for web search:\n"
    "ACTION: web_search\n"
    "query: weather in London today\n"
    "search_type: general\n"
    "reason: user needs current weather data\n\n"
    "Based on the search results, the weather in London today is partly cloudy [1]...\n\n"
```

- [ ] **Step 2: Verify and commit**

```bash
python3 -c "import py_compile; py_compile.compile('app/services/chat_engine.py', doraise=True); print('OK')"
```

```bash
git add app/services/chat_engine.py
git commit -m "feat: add ACTION: web_search to system prompt — activates model-driven search"
```

---

## Task 8: Verify End-to-End

- [ ] **Step 1: Restart service**

```bash
sudo systemctl restart xelasphere
```

- [ ] **Step 2: Test current events query**

Type: "What's happening in tech news today?"
Expected: `ACTION: web_search` fires, progressive results stream, AI responds with citations.

- [ ] **Step 3: Test image search**

Type: "Show me pictures of modern kitchen designs"
Expected: Image carousel appears with Brave Image results.

- [ ] **Step 4: Test video search**

Type: "How do I tie a bowline knot?"
Expected: Video cards appear, model may select one for embed.

- [ ] **Step 5a: Test no-search query**

Type: "Explain recursion in programming"
Expected: `ACTION: chat`, no search triggered.

- [ ] **Step 5b: Test false-positive resistance**

Paste: "I was searching for meaning in this poem about looking up at stars and finding today's truth in the price of silence"
Expected: `ACTION: chat` — no search triggered despite "searching", "looking up", "today's", "price" all being present. This is the regression test for the original bug that motivated the rebuild.

- [ ] **Step 6: Test cache hit**

Repeat the weather query from Step 2 within 15 minutes.
Expected: Instant results, `cached: true` in search_complete event.

- [ ] **Step 7: Test citation linking**

Click a `[1]` citation in the AI response.
Expected: Corresponding source card highlights and scrolls into view.

- [ ] **Step 8: Final commit**

```bash
git add -A && git commit -m "verify: web search overhaul — all test scenarios passing"
```
