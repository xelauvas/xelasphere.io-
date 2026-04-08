"""
Tests for app/services/web_fetcher.py

These tests are self-contained: they mock requests.get and Config so no
real network traffic or Firebase initialisation is needed.
"""

import sys
import os
import unittest
from unittest.mock import patch, MagicMock

# ---------------------------------------------------------------------------
# Ensure the root conftest Firebase mocking is already in effect (it is when
# running via pytest from the repo root).  For standalone runs we patch the
# absolute minimum here as well.
# ---------------------------------------------------------------------------

# Minimal stubs so that `from app.config import Config` doesn't blow up even
# if the root conftest.py hasn't been loaded yet.
for _mod in ('firebase_admin', 'firebase_admin.credentials',
             'firebase_admin.firestore', 'firebase_admin.auth'):
    if _mod not in sys.modules:
        sys.modules[_mod] = MagicMock()

import pytest
from io import BytesIO

# ---------------------------------------------------------------------------
# Helper to build a fake requests.Response
# ---------------------------------------------------------------------------

def _make_response(html: str, status_code: int = 200, raise_for: Exception = None):
    """Build a minimal fake requests.Response."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.apparent_encoding = 'utf-8'
    raw_bytes = html.encode('utf-8')

    # iter_content yields the full body in one chunk
    resp.iter_content.return_value = iter([raw_bytes])

    if raise_for:
        resp.raise_for_status.side_effect = raise_for
    else:
        resp.raise_for_status.return_value = None

    return resp


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SAMPLE_HTML_WITH_OG = """
<!DOCTYPE html>
<html>
<head>
  <meta property="og:image" content="https://example.com/img.jpg" />
  <meta property="og:title" content="OG Title" />
  <meta property="og:description" content="OG Desc" />
</head>
<body><p>Hello world</p></body>
</html>
"""

SAMPLE_HTML_NO_OG_WITH_LARGE_IMG = """
<!DOCTYPE html>
<html>
<head><title>Page Title</title></head>
<body>
  <img src="https://example.com/big.jpg" width="400" height="300" />
  <p>Content here</p>
</body>
</html>
"""

SAMPLE_HTML_NO_IMAGE = """
<!DOCTYPE html>
<html>
<head><title>No Image</title></head>
<body><p>Just text, no images.</p></body>
</html>
"""

SAMPLE_HTML_WITH_NOISE = """
<!DOCTYPE html>
<html>
<head></head>
<body>
  <nav>Navigation links</nav>
  <header>Site header</header>
  <script>alert('xss')</script>
  <footer>Footer content</footer>
  <main><p>Main article content that matters.</p></main>
</body>
</html>
"""

PREAPPROVED_DOMAINS = [
    'wikipedia.org',
    'bbc.com',
    'github.com',
]

# ---------------------------------------------------------------------------
# Patch Config at import time for all tests
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def patch_config():
    """Patch Config so WebFetcher picks up SEARCH_PREAPPROVED_DOMAINS."""
    with patch('app.services.web_fetcher.Config') as mock_cfg:
        mock_cfg.SEARCH_PREAPPROVED_DOMAINS = PREAPPROVED_DOMAINS
        yield mock_cfg


@pytest.fixture(autouse=True)
def clear_caches():
    """Clear both caches before every test to prevent cross-test pollution."""
    from app.services import web_fetcher as wf
    wf.url_cache.clear()
    wf.domain_cache.clear()
    yield
    wf.url_cache.clear()
    wf.domain_cache.clear()


# ---------------------------------------------------------------------------
# Test classes
# ---------------------------------------------------------------------------

class TestOgImageExtraction:
    """og:image extraction from HTML meta tags and fallback logic."""

    def setup_method(self):
        from app.services.web_fetcher import WebFetcher
        self.fetcher = WebFetcher()

    def test_og_image_from_meta_tag(self):
        meta = self.fetcher._extract_meta(SAMPLE_HTML_WITH_OG)
        assert meta['og_image'] == 'https://example.com/img.jpg'

    def test_og_title_from_meta_tag(self):
        meta = self.fetcher._extract_meta(SAMPLE_HTML_WITH_OG)
        assert meta['og_title'] == 'OG Title'

    def test_description_from_og_meta(self):
        meta = self.fetcher._extract_meta(SAMPLE_HTML_WITH_OG)
        assert meta['description'] == 'OG Desc'

    def test_fallback_to_large_img_when_no_og(self):
        meta = self.fetcher._extract_meta(SAMPLE_HTML_NO_OG_WITH_LARGE_IMG)
        assert meta['og_image'] == 'https://example.com/big.jpg'

    def test_no_image_returns_none(self):
        meta = self.fetcher._extract_meta(SAMPLE_HTML_NO_IMAGE)
        assert meta['og_image'] is None

    def test_small_img_ignored_in_fallback(self):
        html = '<html><body><img src="tiny.png" width="50"/></body></html>'
        meta = self.fetcher._extract_meta(html)
        assert meta['og_image'] is None

    def test_description_fallback_to_name_meta(self):
        html = '<html><head><meta name="description" content="Plain desc"/></head><body></body></html>'
        meta = self.fetcher._extract_meta(html)
        assert meta['description'] == 'Plain desc'


class TestHtmlToMarkdown:
    """HTML-to-Markdown conversion: noise stripping and truncation."""

    def setup_method(self):
        from app.services.web_fetcher import WebFetcher
        self.fetcher = WebFetcher()

    def test_strips_nav(self):
        md = self.fetcher._html_to_markdown(SAMPLE_HTML_WITH_NOISE)
        assert 'Navigation links' not in md

    def test_strips_footer(self):
        md = self.fetcher._html_to_markdown(SAMPLE_HTML_WITH_NOISE)
        assert 'Footer content' not in md

    def test_strips_script(self):
        md = self.fetcher._html_to_markdown(SAMPLE_HTML_WITH_NOISE)
        assert "alert('xss')" not in md

    def test_strips_header_tag(self):
        md = self.fetcher._html_to_markdown(SAMPLE_HTML_WITH_NOISE)
        assert 'Site header' not in md

    def test_keeps_main_content(self):
        md = self.fetcher._html_to_markdown(SAMPLE_HTML_WITH_NOISE)
        assert 'Main article content that matters.' in md

    def test_truncation_adds_marker(self):
        long_html = '<html><body><p>' + ('A' * 25000) + '</p></body></html>'
        md = self.fetcher._html_to_markdown(long_html, max_chars=20_000)
        assert '[Content truncated due to length...]' in md

    def test_no_truncation_when_short(self):
        short_html = '<html><body><p>Short content.</p></body></html>'
        md = self.fetcher._html_to_markdown(short_html, max_chars=20_000)
        assert '[Content truncated' not in md

    def test_truncated_content_respects_max_chars(self):
        long_html = '<html><body><p>' + ('B' * 30000) + '</p></body></html>'
        result = self.fetcher._html_to_markdown(long_html, max_chars=500)
        # Marker is appended after max_chars, so total is slightly over but
        # the actual content portion is exactly max_chars.
        assert len(result) > 500
        assert result[500:].startswith('\n\n[Content truncated due to length...]')

    def test_strips_ad_class_elements(self):
        html = '<html><body><div class="cookie-banner">Accept cookies</div><p>Real content</p></body></html>'
        result = self.fetcher._html_to_markdown(html)
        assert 'Accept cookies' not in result
        assert 'Real content' in result


class TestCaching:
    """Cache hit / miss behaviour."""

    def setup_method(self):
        from app.services.web_fetcher import WebFetcher
        self.fetcher = WebFetcher()

    @patch('app.services.web_fetcher.requests.get')
    def test_cache_miss_fetches_url(self, mock_get):
        mock_get.return_value = _make_response('<html><body><p>Fresh</p></body></html>')
        result = self.fetcher.fetch('https://example.com/page')
        mock_get.assert_called_once()
        assert result['cached'] is False

    @patch('app.services.web_fetcher.requests.get')
    def test_cache_hit_returns_cached(self, mock_get):
        mock_get.return_value = _make_response('<html><body><p>Cached page</p></body></html>')

        url = 'https://example.com/cached'
        # First call — miss
        first = self.fetcher.fetch(url)
        assert first['cached'] is False

        # Second call — hit (requests.get should NOT be called again)
        second = self.fetcher.fetch(url)
        assert second['cached'] is True
        assert mock_get.call_count == 1

    @patch('app.services.web_fetcher.requests.get')
    def test_error_result_not_cached(self, mock_get):
        import requests as req_lib
        mock_get.return_value = _make_response(
            '', raise_for=req_lib.exceptions.Timeout()
        )

        url = 'https://example.com/timeout'
        result = self.fetcher.fetch(url)
        assert result['error'] is not None

        # Should fetch again next time (error not cached)
        mock_get.return_value = _make_response('<html><body><p>OK</p></body></html>')
        second = self.fetcher.fetch(url)
        assert second['cached'] is False
        assert mock_get.call_count == 2


class TestPreapprovedDomains:
    """_is_preapproved domain checking."""

    def setup_method(self):
        from app.services.web_fetcher import WebFetcher
        self.fetcher = WebFetcher()

    def test_preapproved_domain_returns_true(self):
        assert self.fetcher._is_preapproved('https://en.wikipedia.org/wiki/Python') is True

    def test_preapproved_domain_without_subdomain(self):
        assert self.fetcher._is_preapproved('https://github.com/user/repo') is True

    def test_preapproved_www_variant(self):
        assert self.fetcher._is_preapproved('https://www.bbc.com/news') is True

    def test_unknown_domain_returns_false(self):
        assert self.fetcher._is_preapproved('https://randomblog.xyz/post') is False

    def test_similar_but_not_matching_domain_returns_false(self):
        # 'fakewikipedia.org' should NOT match 'wikipedia.org'
        assert self.fetcher._is_preapproved('https://fakewikipedia.org/page') is False

    def test_empty_url_returns_false(self):
        assert self.fetcher._is_preapproved('') is False


class TestErrorHandling:
    """Timeout and connection error handling."""

    def setup_method(self):
        from app.services.web_fetcher import WebFetcher
        self.fetcher = WebFetcher()

    @patch('app.services.web_fetcher.requests.get')
    def test_timeout_returns_empty_content_with_error(self, mock_get):
        import requests as req_lib
        mock_get.side_effect = req_lib.exceptions.Timeout()

        result = self.fetcher.fetch('https://slow.example.com/page')
        assert result['content'] == ''
        assert result['error'] is not None
        assert 'timed out' in result['error'].lower()

    @patch('app.services.web_fetcher.requests.get')
    def test_connection_error_returns_error(self, mock_get):
        import requests as req_lib
        mock_get.side_effect = req_lib.exceptions.ConnectionError('refused')

        result = self.fetcher.fetch('https://unreachable.example.com/')
        assert result['content'] == ''
        assert result['error'] is not None

    @patch('app.services.web_fetcher.requests.get')
    def test_http_error_returns_error(self, mock_get):
        import requests as req_lib
        mock_resp = _make_response(
            '', status_code=404,
            raise_for=req_lib.exceptions.HTTPError('404 Not Found')
        )
        mock_get.return_value = mock_resp

        result = self.fetcher.fetch('https://example.com/missing')
        assert result['content'] == ''
        assert result['error'] is not None

    @patch('app.services.web_fetcher.requests.get')
    def test_fetch_multiple_deadline_honoured(self, mock_get):
        """fetch_multiple returns empty dicts for URLs that don't finish in time."""
        import time
        import requests as req_lib

        def _slow_fetch(*args, **kwargs):
            time.sleep(5)  # much longer than deadline
            return _make_response('<html><body></body></html>')

        mock_get.side_effect = _slow_fetch

        fetcher_local = self._make_fresh_fetcher()
        urls = ['https://slow1.example.com/', 'https://slow2.example.com/']
        results = fetcher_local.fetch_multiple(urls, deadline_seconds=0.1)

        assert len(results) == 2
        # Both should be empty dicts (timed out)
        for r in results:
            assert r == {}

    def _make_fresh_fetcher(self):
        from app.services.web_fetcher import WebFetcher
        return WebFetcher()

    @patch('app.services.web_fetcher.requests.get')
    def test_fetch_multiple_returns_results_in_order(self, mock_get):
        mock_get.side_effect = [
            _make_response('<html><body><p>Page A</p></body></html>'),
            _make_response('<html><body><p>Page B</p></body></html>'),
        ]
        fetcher = self._make_fresh_fetcher()
        urls = [
            'https://a.example.com/',
            'https://b.example.com/',
        ]
        results = fetcher.fetch_multiple(urls, deadline_seconds=5.0)
        assert len(results) == 2
        assert 'Page A' in results[0]['content']
        assert 'Page B' in results[1]['content']
