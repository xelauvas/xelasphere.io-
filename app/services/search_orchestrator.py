"""SearchOrchestrator — coordinates Brave Web, Brave Images, and YouTube searches.

Fires backends in parallel using ThreadPoolExecutor, streams results progressively
as SearchEvent objects, fetches full page content for top web results, and caches
merged results with TTLCache.
"""

import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from math import log10
from typing import Dict, Any, List, Optional, Generator, Set
from urllib.parse import urlparse

import requests
from cachetools import TTLCache

try:
    from app.config import Config
except Exception:
    Config = None  # type: ignore[assignment,misc]

try:
    from app.services.web_fetcher import WebFetcher
except Exception:
    WebFetcher = None  # type: ignore[assignment,misc]

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level cache and API endpoint constants
# ---------------------------------------------------------------------------

search_cache: TTLCache = TTLCache(maxsize=100, ttl=900)  # 15 min

BRAVE_WEB_ENDPOINT = 'https://api.search.brave.com/res/v1/web/search'
BRAVE_NEWS_ENDPOINT = 'https://api.search.brave.com/res/v1/news/search'
BRAVE_IMAGES_ENDPOINT = 'https://api.search.brave.com/res/v1/images/search'
YOUTUBE_SEARCH_ENDPOINT = 'https://www.googleapis.com/youtube/v3/search'
YOUTUBE_VIDEOS_ENDPOINT = 'https://www.googleapis.com/youtube/v3/videos'

REQUEST_TIMEOUT = 8  # seconds per backend call

# Domains that pollute news queries (ticket sites, social media, event pages)
_NEWS_DOMAIN_BLOCKLIST = frozenset({
    'ticketle.co.uk', 'allgigs.co.uk', 'ticketmaster.com', 'eventbrite.com',
    'facebook.com', 'instagram.com', 'tiktok.com', 'pinterest.com',
    'world-border-congress.com', 'newlandchase.com',
})

# Max age in hours for news results (7 days)
_NEWS_MAX_AGE_HOURS = 168


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class SearchResult:
    """Unified result object for web, image, and video results."""
    type: str                          # 'web' | 'image' | 'video'
    title: str
    url: str
    snippet: str = ''
    favicon: Optional[str] = None
    thumbnail: Optional[str] = None
    full_content: Optional[str] = None
    video_id: Optional[str] = None
    channel: Optional[str] = None
    duration: Optional[str] = None
    source_domain: str = ''
    relevance_score: float = 0.0
    _age_hours: float = 0.0  # internal: used for news freshness filtering


@dataclass
class SearchEvent:
    """A single progressive event yielded by search_progressive."""
    type: str          # e.g. 'search_web_results', 'search_images', etc.
    data: Dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Scoring helpers
# ---------------------------------------------------------------------------

def _score_web_result(position: int, age_hours: float) -> float:
    """Score a web result by position and freshness.

    Position penalty: 0.1 per rank (0-based).
    Freshness bonus: 1.2 if <24 h, 1.1 if <7 days, else 1.0.
    """
    base = max(0.0, 1.0 - position * 0.1)
    if age_hours < 24:
        freshness = 1.2
    elif age_hours < 168:   # 7 * 24
        freshness = 1.1
    else:
        freshness = 1.0
    return base * freshness


def _score_video_result(position: int, view_count: int) -> float:
    """Score a video result by position and view count.

    Position penalty: 0.1 per rank (0-based).
    Popularity bonus: 1.0 + min(log10(views), 2) * 0.1
    """
    base = max(0.0, 1.0 - position * 0.1)
    safe_views = max(view_count, 1)
    popularity = 1.0 + min(log10(safe_views), 2) * 0.1
    return base * popularity


# ---------------------------------------------------------------------------
# Duration parsing
# ---------------------------------------------------------------------------

def _parse_iso8601_duration(duration_str: str) -> str:
    """Convert ISO 8601 duration (e.g. PT5M32S) to MM:SS or H:MM:SS string."""
    if not duration_str:
        return ''
    pattern = re.compile(
        r'PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?'
    )
    match = pattern.match(duration_str)
    if not match:
        return duration_str

    hours = int(match.group(1) or 0)
    minutes = int(match.group(2) or 0)
    seconds = int(match.group(3) or 0)

    if hours:
        return f'{hours}:{minutes:02d}:{seconds:02d}'
    return f'{minutes}:{seconds:02d}'


# ---------------------------------------------------------------------------
# Age string parser (Brave returns strings like "3 hours ago", "2 days ago")
# ---------------------------------------------------------------------------

def _parse_age_to_hours(age_str: str) -> float:
    """Convert a Brave-style age string to approximate hours.

    Examples: '3 hours ago' -> 3.0, '2 days ago' -> 48.0,
              '1 week ago' -> 168.0, '1 month ago' -> 720.0
    Returns 8760.0 (1 year) for unknown/unparseable strings.
    """
    if not age_str:
        return 8760.0

    s = age_str.lower().strip()

    # Try to extract a number and unit
    m = re.search(r'(\d+)\s*(second|minute|hour|day|week|month|year)', s)
    if not m:
        return 8760.0

    value = int(m.group(1))
    unit = m.group(2)

    multipliers = {
        'second': 1 / 3600,
        'minute': 1 / 60,
        'hour': 1.0,
        'day': 24.0,
        'week': 168.0,
        'month': 720.0,
        'year': 8760.0,
    }
    return value * multipliers.get(unit, 8760.0)


# ---------------------------------------------------------------------------
# SearchOrchestrator
# ---------------------------------------------------------------------------

class SearchOrchestrator:
    """Coordinates web, image, and video search across multiple backends."""

    def __init__(self):
        self._fetcher = WebFetcher() if WebFetcher is not None else None
        self._last_results: Dict[str, List[SearchResult]] = {}
        self._fetched_urls: Set[str] = set()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def search_progressive(
        self,
        query: str,
        search_type: str = 'general',
        model: str = 'default',
    ) -> Generator[SearchEvent, None, None]:
        """Generator that yields SearchEvent objects as backends return results.

        search_type options: 'general', 'images', 'video', 'news'
        """
        # Normalize unknown search types to 'general'
        if search_type not in ('general', 'images', 'video', 'news'):
            search_type = 'general'

        cache_key = f'{search_type}:{query.lower().strip()}'

        # --- Cache hit ---
        if cache_key in search_cache:
            cached = search_cache[cache_key]
            yield from self._replay_from_cache(cached, search_type)
            return

        web_results: List[SearchResult] = []
        image_results: List[SearchResult] = []
        video_results: List[SearchResult] = []

        # --- Decide which backends to fire ---
        futures_map = {}
        with ThreadPoolExecutor(max_workers=3) as executor:
            if search_type in ('general', 'news'):
                futures_map['web'] = executor.submit(
                    self._search_brave_web, query, search_type
                )
            if search_type in ('general', 'images'):
                futures_map['images'] = executor.submit(
                    self._search_brave_images, query
                )
            if search_type in ('general', 'video'):
                futures_map['video'] = executor.submit(
                    self._search_youtube, query
                )

            # Yield results as each backend completes
            for future in as_completed(futures_map.values()):
                # Find which backend this future belongs to
                backend = next(k for k, v in futures_map.items() if v is future)
                try:
                    results = future.result()
                except Exception as exc:
                    logger.warning('SearchOrchestrator backend %s failed: %s', backend, exc)
                    results = []

                if backend == 'web':
                    web_results = results
                    yield SearchEvent(
                        type='search_web_results',
                        data={'results': [self._result_to_dict(r) for r in web_results]},
                    )
                elif backend == 'images':
                    image_results = results
                    yield SearchEvent(
                        type='search_images',
                        data={'images': [self._result_to_dict(r) for r in image_results]},
                    )
                elif backend == 'video':
                    video_results = results
                    yield SearchEvent(
                        type='search_videos',
                        data={'videos': [self._result_to_dict(r) for r in video_results]},
                    )

        # --- Full content fetch for top 3 web results ---
        if web_results and self._fetcher is not None:
            top_urls = [r.url for r in web_results[:3]]
            deadline_seconds = (
                getattr(Config, 'FETCH_DEADLINE_MS', 2000) / 1000.0
                if Config is not None else 2.0
            )
            fetched = self._fetcher.fetch_multiple(
                top_urls,
                deadline_seconds=deadline_seconds,
            )
            for url, fetch_data in zip(top_urls, fetched):
                if fetch_data and not fetch_data.get('error'):
                    self._fetched_urls.add(url)
                    # Attach content to the matching SearchResult
                    for r in web_results:
                        if r.url == url:
                            r.full_content = fetch_data.get('content', '')
                            break
                    yield SearchEvent(
                        type='search_content',
                        data={
                            'url': url,
                            'content': fetch_data.get('content', ''),
                            'og_image': fetch_data.get('og_image'),
                            'og_title': fetch_data.get('og_title'),
                        },
                    )

        # --- Cache and complete ---
        all_results = {
            'web': [self._result_to_dict(r) for r in web_results],
            'images': [self._result_to_dict(r) for r in image_results],
            'video': [self._result_to_dict(r) for r in video_results],
        }
        search_cache[cache_key] = all_results
        self._last_results[query.lower().strip()] = web_results + image_results + video_results

        yield SearchEvent(type='search_complete', data={'query': query, 'search_type': search_type})

    def get_context_for_ai(self, query: str) -> str:
        """Build a context string from the last fetched results for AI prompt injection."""
        key = query.lower().strip()
        results = self._last_results.get(key, [])
        if not results:
            return ''

        budget = (
            getattr(Config, 'SEARCH_CONTEXT_BUDGET', {}).get('default', 40_000)
            if Config is not None else 40_000
        )

        parts = [f'## Search Results for: {query}\n']
        total_chars = len(parts[0])

        for i, result in enumerate(results, 1):
            if result.type == 'web':
                section = (
                    f'### [{i}] {result.title}\n'
                    f'URL: {result.url}\n'
                    f'Snippet: {result.snippet}\n'
                )
                if result.full_content:
                    section += f'\nContent:\n{result.full_content}\n'
                section += '\n'
            elif result.type == 'image':
                section = (
                    f'### [{i}] {result.title or "Image"}\n'
                    f'URL: {result.url}\n'
                    f'Source: {result.source_domain}\n'
                )
                if result.snippet and result.snippet != result.title:
                    section += f'Description: {result.snippet}\n'
                section += '\n'
            elif result.type == 'video':
                section = (
                    f'### [{i}] {result.title or "Video"}\n'
                    f'URL: {result.url}\n'
                    f'Channel: {result.channel or "Unknown"}\n'
                    f'Duration: {result.duration or "N/A"}\n\n'
                )
            else:
                continue

            if total_chars + len(section) > budget:
                break
            parts.append(section)
            total_chars += len(section)

        return ''.join(parts)

    def get_fetched_urls(self) -> Set[str]:
        """Return the set of URLs that received full content fetching."""
        return set(self._fetched_urls)

    # ------------------------------------------------------------------
    # Backend search methods
    # ------------------------------------------------------------------

    def _search_brave_web(self, query: str, search_type: str = 'general') -> List[SearchResult]:
        """Call Brave Web Search API and return a list of SearchResult objects."""
        api_key = getattr(Config, 'BRAVE_SEARCH_API_KEY', '') if Config is not None else ''
        if not api_key:
            logger.debug('BRAVE_SEARCH_API_KEY not set — skipping web search')
            return []

        headers = {
            'Accept': 'application/json',
            'Accept-Encoding': 'gzip',
            'X-Subscription-Token': api_key,
        }

        # Use dedicated News endpoint for news queries
        if search_type == 'news':
            params: Dict[str, Any] = {
                'q': query,
                'count': 10,
                'safesearch': 'moderate',
                'freshness': 'pd',  # past day
            }
            try:
                resp = requests.get(
                    BRAVE_NEWS_ENDPOINT,
                    params=params,
                    headers=headers,
                    timeout=REQUEST_TIMEOUT,
                )
                resp.raise_for_status()
                data = resp.json()
            except Exception as exc:
                logger.warning('Brave news search failed: %s', exc)
                return []

            results: List[SearchResult] = []
            news_items = data.get('results', [])
            for i, item in enumerate(news_items):
                url = item.get('url', '')
                age_str = item.get('age', '')
                age_hours = _parse_age_to_hours(age_str)
                score = _score_web_result(i, age_hours)

                parsed = urlparse(url)
                domain = parsed.netloc.lstrip('www.')

                # Skip blocklisted domains and stale results
                if domain in _NEWS_DOMAIN_BLOCKLIST:
                    continue
                if age_hours and age_hours > _NEWS_MAX_AGE_HOURS:
                    continue

                results.append(SearchResult(
                    type='web',
                    title=item.get('title', ''),
                    url=url,
                    snippet=item.get('description', ''),
                    favicon=item.get('meta_url', {}).get('favicon') or f'https://www.google.com/s2/favicons?domain={domain}',
                    thumbnail=item.get('thumbnail', {}).get('src') if item.get('thumbnail') else None,
                    source_domain=domain,
                    relevance_score=score,
                    _age_hours=age_hours,
                ))
            logger.info(f"[Search] Brave News API: {len(news_items)} raw -> {len(results)} filtered")
            return results

        # General web search
        params: Dict[str, Any] = {
            'q': query,
            'count': 10,
            'safesearch': 'moderate',
        }

        try:
            resp = requests.get(
                BRAVE_WEB_ENDPOINT,
                params=params,
                headers=headers,
                timeout=REQUEST_TIMEOUT,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            logger.warning('Brave web search failed: %s', exc)
            return []

        results: List[SearchResult] = []
        web_items = data.get('web', {}).get('results', [])
        for i, item in enumerate(web_items):
            url = item.get('url', '')
            age_str = item.get('age', '')
            age_hours = _parse_age_to_hours(age_str)
            score = _score_web_result(i, age_hours)

            parsed = urlparse(url)
            domain = parsed.netloc.lstrip('www.')

            results.append(SearchResult(
                type='web',
                title=item.get('title', ''),
                url=url,
                snippet=item.get('description', ''),
                favicon=item.get('profile', {}).get('img') or f'https://www.google.com/s2/favicons?domain={domain}',
                thumbnail=item.get('thumbnail', {}).get('src') if item.get('thumbnail') else None,
                source_domain=domain,
                relevance_score=score,
                _age_hours=age_hours,
            ))

        return results

    def _search_brave_images(self, query: str) -> List[SearchResult]:
        """Call Brave Images API with content guard filtering."""
        api_key = getattr(Config, 'BRAVE_SEARCH_API_KEY', '') if Config is not None else ''
        if not api_key:
            logger.debug('BRAVE_SEARCH_API_KEY not set — skipping image search')
            return []

        blocked_domains: List[str] = (
            getattr(Config, 'BLOCKED_IMAGE_DOMAINS', []) if Config is not None else []
        )

        params = {
            'q': query,
            'count': 20,
            'safesearch': 'strict',
        }
        headers = {
            'Accept': 'application/json',
            'Accept-Encoding': 'gzip',
            'X-Subscription-Token': api_key,
        }

        try:
            resp = requests.get(
                BRAVE_IMAGES_ENDPOINT,
                params=params,
                headers=headers,
                timeout=REQUEST_TIMEOUT,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            logger.warning('Brave images search failed: %s', exc)
            return []

        results: List[SearchResult] = []
        items = data.get('results', [])
        for i, item in enumerate(items):
            props = item.get('properties', {})
            img_url = props.get('url') or item.get('url', '')
            source_url = item.get('source', item.get('url', ''))

            # Content safety guard: https-only
            if not img_url.startswith('https://'):
                continue

            # Content safety guard: blocked domains
            try:
                img_domain = urlparse(img_url).netloc.lstrip('www.')
            except Exception:
                continue
            if any(blocked in img_domain for blocked in blocked_domains if blocked):
                continue

            # Use source page domain for attribution (not CDN image domain)
            try:
                source_domain = urlparse(source_url).netloc.lstrip('www.') if source_url else img_domain
            except Exception:
                source_domain = img_domain

            # Extract description — prefer page_fetched, fall back to title
            description = (
                item.get('description')
                or item.get('page_fetched', '')
                or ''
            ).strip()
            # Use description as snippet if available, otherwise source domain context
            snippet = description if description and description != item.get('title', '') else (
                f"Image from {source_domain}" if source_domain else ''
            )

            # Favicon from meta_url if available
            meta_url = item.get('meta_url', {})
            favicon_url = (
                meta_url.get('favicon')
                or (f'https://www.google.com/s2/favicons?domain={source_domain}' if source_domain else None)
            )

            # Position-based relevance (same formula as web, no freshness)
            score = max(0.0, 1.0 - i * 0.05)

            results.append(SearchResult(
                type='image',
                title=item.get('title', ''),
                url=source_url,
                snippet=snippet,
                favicon=favicon_url,
                thumbnail=img_url,
                source_domain=source_domain,
                relevance_score=score,
            ))

        return results

    def _search_youtube(self, query: str) -> List[SearchResult]:
        """Call YouTube Data API v3 search + videos endpoints."""
        api_key = getattr(Config, 'YOUTUBE_API_KEY', '') if Config is not None else ''
        if not api_key:
            logger.debug('YOUTUBE_API_KEY not set — skipping YouTube search')
            return []

        # Step 1: Search for videos
        try:
            search_resp = requests.get(
                YOUTUBE_SEARCH_ENDPOINT,
                params={
                    'part': 'snippet',
                    'q': query,
                    'type': 'video',
                    'maxResults': 10,
                    'key': api_key,
                },
                timeout=REQUEST_TIMEOUT,
            )
            search_resp.raise_for_status()
            search_data = search_resp.json()
        except Exception as exc:
            logger.warning('YouTube search API failed: %s', exc)
            return []

        items = search_data.get('items', [])
        if not items:
            return []

        video_ids = [
            item['id']['videoId']
            for item in items
            if item.get('id', {}).get('videoId')
        ]

        # Step 2: Fetch duration and view counts
        stats_map: Dict[str, Dict[str, Any]] = {}
        if video_ids:
            try:
                videos_resp = requests.get(
                    YOUTUBE_VIDEOS_ENDPOINT,
                    params={
                        'part': 'contentDetails,statistics',
                        'id': ','.join(video_ids),
                        'key': api_key,
                    },
                    timeout=REQUEST_TIMEOUT,
                )
                videos_resp.raise_for_status()
                for v in videos_resp.json().get('items', []):
                    vid = v.get('id', '')
                    stats_map[vid] = {
                        'duration': _parse_iso8601_duration(
                            v.get('contentDetails', {}).get('duration', '')
                        ),
                        'views': int(v.get('statistics', {}).get('viewCount', 0)),
                    }
            except Exception as exc:
                logger.warning('YouTube videos API failed: %s', exc)

        results: List[SearchResult] = []
        for i, item in enumerate(items):
            video_id = item.get('id', {}).get('videoId', '')
            if not video_id:
                continue

            snippet = item.get('snippet', {})
            extra = stats_map.get(video_id, {})
            view_count = extra.get('views', 0)
            score = _score_video_result(i, view_count)

            results.append(SearchResult(
                type='video',
                title=snippet.get('title', ''),
                url=f'https://www.youtube.com/watch?v={video_id}',
                snippet=snippet.get('description', ''),
                thumbnail=snippet.get('thumbnails', {}).get('high', {}).get('url')
                    or snippet.get('thumbnails', {}).get('default', {}).get('url'),
                video_id=video_id,
                channel=snippet.get('channelTitle', ''),
                duration=extra.get('duration'),
                source_domain='youtube.com',
                relevance_score=score,
            ))

        return results

    # ------------------------------------------------------------------
    # Utility helpers
    # ------------------------------------------------------------------

    def _result_to_dict(self, result: SearchResult) -> Dict[str, Any]:
        """Convert a SearchResult dataclass to a JSON-serialisable dict."""
        return {
            'type': result.type,
            'title': result.title,
            'url': result.url,
            'snippet': result.snippet,
            'favicon': result.favicon,
            'thumbnail': result.thumbnail,
            'full_content': result.full_content,
            'video_id': result.video_id,
            'channel': result.channel,
            'duration': result.duration,
            'source_domain': result.source_domain,
            'relevance_score': result.relevance_score,
        }

    def _model_family(self, model_id: str) -> str:
        """Map a model ID to a context budget family name."""
        model_id_lower = model_id.lower()
        if 'deepseek' in model_id_lower:
            return 'deepseek'
        if 'llama' in model_id_lower or 'llama' in model_id_lower:
            return 'llama'
        if 'gpt-4' in model_id_lower:
            return 'gpt-4'
        if 'kimi' in model_id_lower or 'moonshot' in model_id_lower:
            return 'kimi'
        if 'qwen' in model_id_lower:
            return 'qwen'
        return 'default'

    # ------------------------------------------------------------------
    # Internal cache replay helper
    # ------------------------------------------------------------------

    def _replay_from_cache(
        self, cached: Dict[str, List[Dict]], search_type: str
    ) -> Generator[SearchEvent, None, None]:
        """Re-yield events from a cached result set."""
        if cached.get('web'):
            yield SearchEvent(type='search_web_results', data={'results': cached['web']})
        if cached.get('images'):
            yield SearchEvent(type='search_images', data={'images': cached['images']})
        if cached.get('video'):
            yield SearchEvent(type='search_videos', data={'videos': cached['video']})
        yield SearchEvent(type='search_complete', data={'query': '', 'search_type': search_type, 'cached': True})


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

search_orchestrator = SearchOrchestrator()
