"""
Tests for app/services/search_orchestrator.py

Self-contained: mocks Config, requests, and WebFetcher so no real network
traffic or Firebase initialisation is needed.
"""

import sys
import os
import unittest
from unittest.mock import patch, MagicMock

# ---------------------------------------------------------------------------
# Ensure Firebase stubs are in place before any app import
# ---------------------------------------------------------------------------
for _mod in ('firebase_admin', 'firebase_admin.credentials',
             'firebase_admin.firestore', 'firebase_admin.auth'):
    if _mod not in sys.modules:
        sys.modules[_mod] = MagicMock()

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_brave_web_response(items):
    """Build a minimal Brave web search API response dict."""
    return {'web': {'results': items}}


def _make_brave_images_response(items):
    """Build a minimal Brave images API response dict."""
    return {'results': items}


def _make_youtube_search_response(video_ids):
    """Build a YouTube search response with given video IDs."""
    return {
        'items': [
            {
                'id': {'videoId': vid},
                'snippet': {
                    'title': f'Video {vid}',
                    'description': f'Description for {vid}',
                    'channelTitle': 'Test Channel',
                    'thumbnails': {
                        'high': {'url': f'https://img.youtube.com/vi/{vid}/hqdefault.jpg'},
                        'default': {'url': f'https://img.youtube.com/vi/{vid}/default.jpg'},
                    },
                },
            }
            for vid in video_ids
        ]
    }


def _make_youtube_videos_response(video_ids, duration='PT3M45S', views=100000):
    """Build a YouTube videos (contentDetails + statistics) response."""
    return {
        'items': [
            {
                'id': vid,
                'contentDetails': {'duration': duration},
                'statistics': {'viewCount': str(views)},
            }
            for vid in video_ids
        ]
    }


# ---------------------------------------------------------------------------
# Patch Config for all tests in this module
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def patch_config():
    """Inject a lightweight Config mock so the orchestrator has valid keys."""
    with patch('app.services.search_orchestrator.Config') as mock_cfg:
        mock_cfg.BRAVE_SEARCH_API_KEY = 'test-brave-key'
        mock_cfg.YOUTUBE_API_KEY = 'test-yt-key'
        mock_cfg.BLOCKED_IMAGE_DOMAINS = ['blocked.example.com']
        mock_cfg.FETCH_DEADLINE_MS = 2000
        mock_cfg.SEARCH_CONTEXT_BUDGET = {
            'deepseek': 30_000,
            'llama': 20_000,
            'gpt-4': 60_000,
            'kimi': 80_000,
            'qwen': 40_000,
            'default': 40_000,
        }
        mock_cfg.SEARCH_PREAPPROVED_DOMAINS = ['wikipedia.org', 'bbc.com']
        yield mock_cfg


@pytest.fixture(autouse=True)
def clear_search_cache():
    """Clear the module-level TTLCache before and after each test."""
    from app.services import search_orchestrator as so_mod
    so_mod.search_cache.clear()
    yield
    so_mod.search_cache.clear()


# ---------------------------------------------------------------------------
# TestRelevanceScoring
# ---------------------------------------------------------------------------

class TestRelevanceScoring:
    """Tests for _score_web_result and _score_video_result."""

    def test_web_score_position_zero_no_age_penalty(self):
        from app.services.search_orchestrator import _score_web_result
        score = _score_web_result(0, 8760.0)
        assert score == pytest.approx(1.0)

    def test_web_score_position_penalty(self):
        from app.services.search_orchestrator import _score_web_result
        score_0 = _score_web_result(0, 8760.0)
        score_5 = _score_web_result(5, 8760.0)
        assert score_5 < score_0
        assert score_5 == pytest.approx(0.5)

    def test_web_score_position_10_clamps_to_zero(self):
        from app.services.search_orchestrator import _score_web_result
        score = _score_web_result(10, 8760.0)
        assert score == pytest.approx(0.0)

    def test_web_score_position_beyond_10_stays_zero(self):
        from app.services.search_orchestrator import _score_web_result
        score = _score_web_result(15, 8760.0)
        assert score == 0.0

    def test_web_score_fresh_within_24h_bonus(self):
        from app.services.search_orchestrator import _score_web_result
        fresh = _score_web_result(0, 12.0)    # < 24 h
        stale = _score_web_result(0, 8760.0)  # old
        assert fresh == pytest.approx(1.2)
        assert fresh > stale

    def test_web_score_within_7_days_gets_1_1_bonus(self):
        from app.services.search_orchestrator import _score_web_result
        score = _score_web_result(0, 72.0)   # 3 days
        assert score == pytest.approx(1.1)

    def test_web_score_older_than_7_days_no_bonus(self):
        from app.services.search_orchestrator import _score_web_result
        score = _score_web_result(0, 200.0)  # ~8 days
        assert score == pytest.approx(1.0)

    # --- Video scoring ---

    def test_video_score_position_zero_low_views(self):
        from app.services.search_orchestrator import _score_video_result
        score = _score_video_result(0, 1)
        # base=1.0, log10(1)=0.0, popularity=1.0 + 0*0.1 = 1.0
        assert score == pytest.approx(1.0)

    def test_video_score_popularity_bonus_increases_score(self):
        from app.services.search_orchestrator import _score_video_result
        low_views = _score_video_result(0, 10)
        high_views = _score_video_result(0, 1_000_000)
        assert high_views > low_views

    def test_video_score_popularity_bonus_caps_at_log10_2(self):
        from app.services.search_orchestrator import _score_video_result
        from math import log10
        # views = 10^3 -> log10=3, capped to 2 -> popularity = 1.2
        score = _score_video_result(0, 1000)
        expected = 1.0 * (1.0 + min(log10(1000), 2) * 0.1)
        assert score == pytest.approx(expected)

    def test_video_score_position_penalty_applies(self):
        from app.services.search_orchestrator import _score_video_result
        score_0 = _score_video_result(0, 100_000)
        score_5 = _score_video_result(5, 100_000)
        assert score_5 < score_0

    def test_video_score_position_10_clamps_to_zero(self):
        from app.services.search_orchestrator import _score_video_result
        score = _score_video_result(10, 1_000_000)
        assert score == pytest.approx(0.0)

    def test_video_score_zero_views_handled_gracefully(self):
        from app.services.search_orchestrator import _score_video_result
        # Should not raise; 0 views -> uses max(0, 1) internally
        score = _score_video_result(0, 0)
        assert score >= 0.0


# ---------------------------------------------------------------------------
# TestSearchResultDataclass
# ---------------------------------------------------------------------------

class TestSearchResultDataclass:
    """Tests for SearchResult and SearchEvent dataclass creation."""

    def test_web_result_creation(self):
        from app.services.search_orchestrator import SearchResult
        result = SearchResult(
            type='web',
            title='Python Tutorial',
            url='https://docs.python.org/3/',
            snippet='Official Python docs',
            source_domain='docs.python.org',
            relevance_score=0.9,
        )
        assert result.type == 'web'
        assert result.title == 'Python Tutorial'
        assert result.url == 'https://docs.python.org/3/'
        assert result.snippet == 'Official Python docs'
        assert result.source_domain == 'docs.python.org'
        assert result.relevance_score == pytest.approx(0.9)
        # Optional fields default to None
        assert result.video_id is None
        assert result.channel is None
        assert result.duration is None
        assert result.full_content is None

    def test_video_result_creation(self):
        from app.services.search_orchestrator import SearchResult
        result = SearchResult(
            type='video',
            title='Learn Python in 10 minutes',
            url='https://www.youtube.com/watch?v=abc123',
            snippet='Quick Python intro',
            thumbnail='https://img.youtube.com/vi/abc123/hqdefault.jpg',
            video_id='abc123',
            channel='TechChannel',
            duration='10:00',
            source_domain='youtube.com',
            relevance_score=0.8,
        )
        assert result.type == 'video'
        assert result.video_id == 'abc123'
        assert result.channel == 'TechChannel'
        assert result.duration == '10:00'

    def test_image_result_creation(self):
        from app.services.search_orchestrator import SearchResult
        result = SearchResult(
            type='image',
            title='Cat photo',
            url='https://example.com/cat',
            thumbnail='https://example.com/cat.jpg',
            source_domain='example.com',
        )
        assert result.type == 'image'
        assert result.thumbnail == 'https://example.com/cat.jpg'

    def test_search_event_creation(self):
        from app.services.search_orchestrator import SearchEvent
        event = SearchEvent(type='search_web_results', data={'results': []})
        assert event.type == 'search_web_results'
        assert event.data == {'results': []}

    def test_search_event_default_data_is_empty_dict(self):
        from app.services.search_orchestrator import SearchEvent
        event = SearchEvent(type='search_complete')
        assert event.data == {}

    def test_result_to_dict_roundtrip(self):
        from app.services.search_orchestrator import SearchResult, SearchOrchestrator
        orch = SearchOrchestrator()
        result = SearchResult(
            type='web',
            title='Test',
            url='https://test.com',
            snippet='A snippet',
            source_domain='test.com',
            relevance_score=0.7,
        )
        d = orch._result_to_dict(result)
        assert d['type'] == 'web'
        assert d['title'] == 'Test'
        assert d['url'] == 'https://test.com'
        assert d['snippet'] == 'A snippet'
        assert d['relevance_score'] == pytest.approx(0.7)
        assert d['video_id'] is None
        assert d['duration'] is None


# ---------------------------------------------------------------------------
# TestCacheIntegration
# ---------------------------------------------------------------------------

class TestCacheIntegration:
    """Tests that search_cache stores and retrieves correctly."""

    def test_cache_miss_calls_backends(self):
        """On first call, backends are invoked (no cache)."""
        from app.services.search_orchestrator import SearchOrchestrator

        orch = SearchOrchestrator()

        brave_web_item = {
            'title': 'Test Page',
            'url': 'https://test.com/page',
            'description': 'A test page',
            'age': '2 days ago',
        }

        mock_web_resp = MagicMock()
        mock_web_resp.json.return_value = _make_brave_web_response([brave_web_item])
        mock_web_resp.raise_for_status.return_value = None

        mock_images_resp = MagicMock()
        mock_images_resp.json.return_value = _make_brave_images_response([])
        mock_images_resp.raise_for_status.return_value = None

        mock_yt_resp = MagicMock()
        mock_yt_resp.json.return_value = {'items': []}
        mock_yt_resp.raise_for_status.return_value = None

        # Mock WebFetcher to avoid real HTTP during content fetch
        with patch('app.services.search_orchestrator.requests.get') as mock_get:
            mock_get.side_effect = [mock_web_resp, mock_images_resp, mock_yt_resp]
            with patch.object(orch, '_fetcher') as mock_fetcher:
                mock_fetcher.fetch_multiple.return_value = [{}]
                events = list(orch.search_progressive('python tutorial', 'general'))

        event_types = [e.type for e in events]
        assert 'search_complete' in event_types

    def test_cache_hit_skips_backends(self):
        """Second identical query should be served from cache."""
        from app.services.search_orchestrator import SearchOrchestrator, search_cache

        orch = SearchOrchestrator()
        cache_key = 'general:python tutorial'
        search_cache[cache_key] = {
            'web': [{'type': 'web', 'title': 'Cached Result', 'url': 'https://cached.com',
                     'snippet': '', 'favicon': None, 'thumbnail': None, 'full_content': None,
                     'video_id': None, 'channel': None, 'duration': None,
                     'source_domain': 'cached.com', 'relevance_score': 0.9}],
            'images': [],
            'video': [],
        }

        with patch('app.services.search_orchestrator.requests.get') as mock_get:
            events = list(orch.search_progressive('python tutorial', 'general'))
            # No HTTP calls should be made
            mock_get.assert_not_called()

        event_types = [e.type for e in events]
        assert 'search_web_results' in event_types
        assert 'search_complete' in event_types

    def test_cache_key_is_lowercase_and_stripped(self):
        """Cache key should be normalised regardless of input case/whitespace."""
        from app.services.search_orchestrator import SearchOrchestrator, search_cache

        orch = SearchOrchestrator()
        # Pre-populate with a specific normalised key
        cache_key = 'general:python tutorial'
        search_cache[cache_key] = {'web': [], 'images': [], 'video': []}

        with patch('app.services.search_orchestrator.requests.get') as mock_get:
            # Both variants should hit the cache
            list(orch.search_progressive('  Python Tutorial  ', 'general'))
            list(orch.search_progressive('PYTHON TUTORIAL', 'general'))
            mock_get.assert_not_called()

    def test_cache_stores_after_successful_search(self):
        """After a fresh search the result should be in search_cache."""
        from app.services.search_orchestrator import SearchOrchestrator, search_cache

        orch = SearchOrchestrator()

        mock_web_resp = MagicMock()
        mock_web_resp.json.return_value = _make_brave_web_response([])
        mock_web_resp.raise_for_status.return_value = None

        mock_images_resp = MagicMock()
        mock_images_resp.json.return_value = _make_brave_images_response([])
        mock_images_resp.raise_for_status.return_value = None

        mock_yt_resp = MagicMock()
        mock_yt_resp.json.return_value = {'items': []}
        mock_yt_resp.raise_for_status.return_value = None

        with patch('app.services.search_orchestrator.requests.get') as mock_get:
            mock_get.side_effect = [mock_web_resp, mock_images_resp, mock_yt_resp]
            with patch.object(orch, '_fetcher'):
                list(orch.search_progressive('flask testing', 'general'))

        assert 'general:flask testing' in search_cache

    def test_cache_separate_by_search_type(self):
        """Different search_type values produce independent cache entries."""
        from app.services.search_orchestrator import search_cache

        search_cache['general:ai news'] = {'web': [], 'images': [], 'video': []}
        assert 'news:ai news' not in search_cache


# ---------------------------------------------------------------------------
# TestDurationParsing
# ---------------------------------------------------------------------------

class TestDurationParsing:
    """Tests for _parse_iso8601_duration."""

    def test_minutes_and_seconds(self):
        from app.services.search_orchestrator import _parse_iso8601_duration
        assert _parse_iso8601_duration('PT5M32S') == '5:32'

    def test_hours_minutes_seconds(self):
        from app.services.search_orchestrator import _parse_iso8601_duration
        assert _parse_iso8601_duration('PT1H2M3S') == '1:02:03'

    def test_only_seconds(self):
        from app.services.search_orchestrator import _parse_iso8601_duration
        assert _parse_iso8601_duration('PT45S') == '0:45'

    def test_only_minutes(self):
        from app.services.search_orchestrator import _parse_iso8601_duration
        assert _parse_iso8601_duration('PT10M') == '10:00'

    def test_empty_string(self):
        from app.services.search_orchestrator import _parse_iso8601_duration
        assert _parse_iso8601_duration('') == ''

    def test_invalid_returns_original(self):
        from app.services.search_orchestrator import _parse_iso8601_duration
        assert _parse_iso8601_duration('not-a-duration') == 'not-a-duration'


# ---------------------------------------------------------------------------
# TestAgeParser
# ---------------------------------------------------------------------------

class TestAgeParser:
    """Tests for _parse_age_to_hours."""

    def test_hours(self):
        from app.services.search_orchestrator import _parse_age_to_hours
        assert _parse_age_to_hours('3 hours ago') == pytest.approx(3.0)

    def test_days(self):
        from app.services.search_orchestrator import _parse_age_to_hours
        assert _parse_age_to_hours('2 days ago') == pytest.approx(48.0)

    def test_weeks(self):
        from app.services.search_orchestrator import _parse_age_to_hours
        assert _parse_age_to_hours('1 week ago') == pytest.approx(168.0)

    def test_months(self):
        from app.services.search_orchestrator import _parse_age_to_hours
        assert _parse_age_to_hours('1 month ago') == pytest.approx(720.0)

    def test_empty_string_returns_large_value(self):
        from app.services.search_orchestrator import _parse_age_to_hours
        assert _parse_age_to_hours('') == pytest.approx(8760.0)

    def test_unknown_string_returns_large_value(self):
        from app.services.search_orchestrator import _parse_age_to_hours
        assert _parse_age_to_hours('just now') == pytest.approx(8760.0)


# ---------------------------------------------------------------------------
# TestModelFamily
# ---------------------------------------------------------------------------

class TestModelFamily:
    """Tests for SearchOrchestrator._model_family."""

    def setup_method(self):
        from app.services.search_orchestrator import SearchOrchestrator
        self.orch = SearchOrchestrator()

    def test_deepseek_family(self):
        assert self.orch._model_family('deepseek-v4-pro') == 'deepseek'

    def test_llama_family(self):
        assert self.orch._model_family('llama-3.1-70b') == 'llama'

    def test_gpt4_family(self):
        assert self.orch._model_family('gpt-4-turbo') == 'gpt-4'

    def test_kimi_family(self):
        assert self.orch._model_family('kimi-moonshot-v1') == 'kimi'

    def test_qwen_family(self):
        assert self.orch._model_family('qwen-2.5-72b') == 'qwen'

    def test_unknown_returns_default(self):
        assert self.orch._model_family('some-random-model') == 'default'


# ---------------------------------------------------------------------------
# TestImageContentGuard
# ---------------------------------------------------------------------------

class TestImageContentGuard:
    """Tests for _search_brave_images content safety filtering."""

    def setup_method(self):
        from app.services.search_orchestrator import SearchOrchestrator
        self.orch = SearchOrchestrator()

    def _make_image_item(self, img_url, title='Test Image'):
        return {
            'title': title,
            'url': img_url,
            'source': img_url,
            'properties': {'url': img_url},
        }

    def test_https_only_filter(self):
        """HTTP images should be filtered out."""
        items = [
            self._make_image_item('http://example.com/img.jpg'),  # http — should be blocked
            self._make_image_item('https://good.com/img.jpg'),    # https — OK
        ]
        mock_resp = MagicMock()
        mock_resp.json.return_value = _make_brave_images_response(items)
        mock_resp.raise_for_status.return_value = None

        with patch('app.services.search_orchestrator.requests.get', return_value=mock_resp):
            results = self.orch._search_brave_images('cats')

        urls = [r.thumbnail for r in results]
        assert all(u.startswith('https://') for u in urls)
        assert 'http://example.com/img.jpg' not in urls

    def test_blocked_domain_filtered(self):
        """Images from blocked domains should be filtered out."""
        items = [
            self._make_image_item('https://blocked.example.com/img.jpg'),
            self._make_image_item('https://safe.com/img.jpg'),
        ]
        mock_resp = MagicMock()
        mock_resp.json.return_value = _make_brave_images_response(items)
        mock_resp.raise_for_status.return_value = None

        with patch('app.services.search_orchestrator.requests.get', return_value=mock_resp):
            results = self.orch._search_brave_images('cats')

        thumbnails = [r.thumbnail for r in results]
        assert not any('blocked.example.com' in t for t in thumbnails)
        assert any('safe.com' in t for t in thumbnails)

    def test_api_failure_returns_empty(self):
        """If the Brave images API fails, return an empty list gracefully."""
        with patch('app.services.search_orchestrator.requests.get',
                   side_effect=Exception('network error')):
            results = self.orch._search_brave_images('cats')
        assert results == []


# ---------------------------------------------------------------------------
# TestYouTubeGracefulDegradation
# ---------------------------------------------------------------------------

class TestYouTubeGracefulDegradation:
    """Tests that YouTube search degrades gracefully when API key is missing."""

    def test_no_api_key_returns_empty(self):
        from app.services.search_orchestrator import SearchOrchestrator
        orch = SearchOrchestrator()

        with patch('app.services.search_orchestrator.Config') as mock_cfg:
            mock_cfg.YOUTUBE_API_KEY = ''
            mock_cfg.BRAVE_SEARCH_API_KEY = 'key'
            mock_cfg.BLOCKED_IMAGE_DOMAINS = []
            mock_cfg.FETCH_DEADLINE_MS = 2000
            with patch('app.services.search_orchestrator.requests.get') as mock_get:
                results = orch._search_youtube('python')
                mock_get.assert_not_called()

        assert results == []

    def test_api_error_returns_empty(self):
        from app.services.search_orchestrator import SearchOrchestrator
        orch = SearchOrchestrator()

        with patch('app.services.search_orchestrator.requests.get',
                   side_effect=Exception('timeout')):
            results = orch._search_youtube('python')

        assert results == []
