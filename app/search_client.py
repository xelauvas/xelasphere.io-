"""SearchClient for performing web searches.

This module provides a singleton SearchClient that supports the Brave Search API
with caching, rate limiting, and relevance scoring.
"""

import asyncio
import html
import logging
import random
import re
from contextlib import nullcontext
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import requests
import tldextract
from cachetools import TTLCache
from pydantic import BaseModel, HttpUrl, Field
from prometheus_client import Counter, Histogram
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from app.config import Config

logger = logging.getLogger(__name__)


class SearchException(Exception):
    """Custom exception for search-related errors."""
    pass


class SearchResult(BaseModel):
    """
    Represents a single search result.
    Provides convenient validation and serialization via Pydantic.
    """
    title: str
    link: HttpUrl
    snippet: str
    source_domain: str
    website_name: str
    favicon: Optional[str] = None
    relevance_score: float = 0.0
    timestamp: datetime = Field(default_factory=datetime.utcnow)

    def to_dict(self) -> Dict[str, Any]:
        """
        Convert this SearchResult to a dict, ensuring the link is str
        and timestamp is ISO-formatted.
        """
        return {
            'title': self.title,
            'link': str(self.link),
            'snippet': self.snippet,
            'source_domain': self.source_domain,
            'website_name': self.website_name,
            'favicon': self.favicon,
            'relevance_score': self.relevance_score,
            'timestamp': self.timestamp.isoformat(),
        }


class SearchBackend(str, Enum):
    BRAVE = "brave"


def resolve_search_backend(raw_backend: Optional[str]) -> SearchBackend:
    """
    Map user or config provided backend strings to a supported enum.
    Defaults to Brave when the value is missing or invalid.
    """
    if not raw_backend:
        return SearchBackend.BRAVE
    raw_value = raw_backend.lower()
    if raw_value == SearchBackend.BRAVE.value:
        return SearchBackend.BRAVE
    return SearchBackend.BRAVE


class SearchClient:
    """
    A rate-limited, caching search client backed by the Brave Search API.
    """
    CALLS_PER_MINUTE = 30
    BACKOFF_FACTOR = 1.5

    _instance = None
    _metrics = {
        'counter': None,
        'latency': None,
        'errors': None
    }

    def __new__(cls, *args, **kwargs):
        """
        Ensure this is a singleton by keeping a single class-level _instance.
        """
        if cls._instance is None:
            cls._instance = super(SearchClient, cls).__new__(cls)
            cls._initialize_metrics()
        return cls._instance

    @classmethod
    def _initialize_metrics(cls):
        """
        Initialize Prometheus metrics if they are not already set.
        """
        if not cls._metrics['counter']:
            try:
                cls._metrics['counter'] = Counter('search_requests_total', 'Total searches')
                cls._metrics['latency'] = Histogram('search_latency_seconds', 'Search latency')
                cls._metrics['errors'] = Counter('search_errors_total', 'Total search errors')
            except ValueError:
                # If metrics are already registered in a long-running process, skip re-registering.
                pass

    def __init__(
        self,
        timeout: int = 10,
        max_retries: int = 3,
        backend: Optional[SearchBackend] = None
    ):
        """
        Initialize the SearchClient with a chosen backend and other settings.
        """
        # Only initialize once due to singleton pattern
        if not hasattr(self, 'initialized'):
            self.timeout = timeout
            self.max_retries = max_retries
            self.rate_limit_delay = 60 / self.CALLS_PER_MINUTE
            self.last_request_time = 0
            config_backend = resolve_search_backend(getattr(Config, 'SEARCH_DEFAULT_BACKEND', None))
            provided_backend = resolve_search_backend(backend.value if isinstance(backend, SearchBackend) else backend) if backend is not None else None
            self.backend = provided_backend or config_backend
            self.conversation_history: List[Dict[str, Any]] = []

            # Cache up to 1000 queries for 1 hour
            self.cache = TTLCache(maxsize=1000, ttl=3600)
            self.trusted_domains = ['bbc.com', 'nytimes.com', 'wikipedia.org']
            self.blocked_domains = [
                d.strip().lower() for d in getattr(Config, 'SEARCH_BLOCKED_DOMAINS', []) if isinstance(d, str) and d.strip()
            ]

            # Vectorizer for relevance scoring
            self.vectorizer = TfidfVectorizer(stop_words='english')

            self.initialized = True

    # ---------- Metrics Helpers ----------
    def _increment_counter(self):
        if self._metrics['counter']:
            self._metrics['counter'].inc()

    def _record_latency(self):
        if self._metrics['latency']:
            return self._metrics['latency'].time()
        return nullcontext()

    def _increment_error(self):
        if self._metrics['errors']:
            self._metrics['errors'].inc()

    # ---------- Text Helpers ----------
    @staticmethod
    def _clean_text(text: str) -> str:
        if not isinstance(text, str):
            return ""
        text = html.unescape(text)
        text = re.sub(r'\s+', ' ', text)
        text = re.sub(r'[^\w\s.,!?-]', '', text)
        return text.strip()

    def _extract_domain(self, url: str) -> str:
        try:
            parsed = urlparse(url)
            domain = parsed.netloc.lower()
            return domain.replace('www.', '', 1)
        except Exception as e:
            logger.error(f"Domain extraction failed for URL {url}: {e}")
            return ""

    def _is_blocked_domain(self, domain: str) -> bool:
        """Return True if the domain (or its parent) is blocked."""
        if not domain:
            return False
        domain = domain.lower()
        return any(domain == blocked or domain.endswith(f".{blocked}") for blocked in self.blocked_domains)

    @staticmethod
    def _is_latin_heavy(text: str, threshold: float = 0.6) -> bool:
        """Heuristic to skip mostly non-Latin text to reduce irrelevant locale results."""
        if not text:
            return True
        latin = sum(1 for ch in text if ord(ch) < 128 and ch.isalpha())
        total = sum(1 for ch in text if ch.isalpha())
        if total == 0:
            return True
        return (latin / total) >= threshold

    def _extract_website_name(self, url: str) -> str:
        try:
            domain_info = tldextract.extract(url)
            return domain_info.domain.capitalize() or "Unknown"
        except Exception as e:
            logger.error(f"Website name extraction failed for URL {url}: {e}")
            return "Unknown"

    def _generate_summary(self, text: str, max_length: int = 200) -> str:
        try:
            if not text:
                return "No snippet available."
            return text[:max_length]
        except Exception as e:
            logger.error(f"Summary generation failed: {e}")
            return (text or "")[:max_length]

    def _calculate_relevance_score(self, result: Dict[str, str], query: str) -> float:
        try:
            content = f"{result.get('title', '')} {result.get('body', '')}".strip()
            vectors = self.vectorizer.fit_transform([query, content])
            if vectors.shape[1] == 0:
                return 0.0
            tfidf_score = cosine_similarity(vectors[0], vectors[1])[0][0]

            # Bonus for trusted domains
            domain_score = 0.1 if self._extract_domain(result.get('href', '')) in self.trusted_domains else 0
            return min(tfidf_score + domain_score, 1.0)
        except Exception as e:
            logger.error(f"Relevance calculation failed: {e}")
            return 0.0

    def _validate_result(self, result: Dict[str, str]) -> bool:
        try:
            # Require title and href; body/snippet are optional
            if not result.get('title') or not result.get('href'):
                return False

            parsed = urlparse(result['href'])
            if not parsed.scheme or not parsed.netloc:
                return False
            domain = parsed.netloc.lower().replace('www.', '', 1)
            if self._is_blocked_domain(domain):
                return False
            return len(result['title']) >= 3 and self._is_latin_heavy(result.get('title', '') + " " + result.get('body', ''))
        except Exception:
            return False

    def _cache_key(self, query: str, max_results: int, min_relevance: float) -> str:
        backend_value = self.backend.value if isinstance(self.backend, SearchBackend) else str(self.backend)
        return f"{query}:{backend_value}:{self.max_retries}:{self.timeout}:{max_results}:{min_relevance}"

    def _get_backoff_time(self, attempt: int) -> float:
        return min(300, (self.BACKOFF_FACTOR ** attempt) + random.uniform(0, 1))

    # ------------------------------------------------------------------
    # Brave Search
    # ------------------------------------------------------------------
    def _brave_fetch_results(self, query: str, max_results: int) -> List[Dict[str, Any]]:
        """
        Perform Brave Search using the official API.
        """
        api_key = getattr(Config, 'BRAVE_SEARCH_API_KEY', '')
        endpoint = getattr(Config, 'BRAVE_SEARCH_ENDPOINT', 'https://api.search.brave.com/res/v1/web/search')
        if not api_key:
            logger.warning("Brave Search API key not configured; skipping search.")
            return []

        headers = {
            "Accept": "application/json",
            "X-Subscription-Token": api_key
        }
        params = {
            "q": query,
            "count": max_results,
            "country": "US",
            "search_lang": "en",
            "safesearch": "moderate"
        }

        try:
            response = requests.get(endpoint, headers=headers, params=params, timeout=self.timeout)
            response.raise_for_status()
            data = response.json()
            items = data.get("web", {}).get("results", [])
            results = []
            for item in items:
                results.append({
                    "title": item.get("title", ""),
                    "href": item.get("url", ""),
                    "body": html.unescape(item.get("description") or ""),
                })
            return results
        except Exception as e:
            logger.error(f"Brave search failed: {e}")
            return []

    # ------------------------------------------------------------------
    # Master Async Method (delegates to each backend)
    # ------------------------------------------------------------------
    async def search_async(
        self,
        query: str,
        max_results: int = 7,
        min_relevance_score: float = 0.0
    ) -> List[SearchResult]:
        """
        Asynchronous search method that returns up to `max_results` results
        with a relevance score above `min_relevance_score`, using the chosen backend.
        """
        if not query or not isinstance(query, str):
            raise ValueError("Invalid query provided")

        cache_key = self._cache_key(query, max_results, min_relevance_score)
        cached = self.cache.get(cache_key)
        if cached is not None:
            return cached

        # Prepare empty results
        final_results: List[SearchResult] = []
        seen_links = set()

        for attempt in range(self.max_retries):
            try:
                with self._record_latency():
                    self._increment_counter()

                    raw_results: List[Dict[str, Any]] = await asyncio.to_thread(
                        self._brave_fetch_results,
                        query,
                        max_results
                    )

                    for r in raw_results:
                        # Validate minimal fields
                        if not self._validate_result(r):
                            continue

                        # Calculate relevance
                        relevance_score = self._calculate_relevance_score(r, query)
                        if relevance_score < min_relevance_score:
                            continue

                        domain = self._extract_domain(r['href'])
                        body = r.get('body') or r.get('snippet') or r.get('description') or r.get('content') or ''
                        result_obj = SearchResult(
                            title=self._clean_text(r['title']),
                            link=r['href'],
                            snippet=self._generate_summary(body),
                            source_domain=domain,
                            website_name=self._extract_website_name(r['href']),
                            favicon=f"https://{domain}/favicon.ico" if domain else None,
                            relevance_score=relevance_score,
                        )
                        if result_obj.link in seen_links:
                            continue
                        seen_links.add(result_obj.link)
                        final_results.append(result_obj)

                    if final_results:
                        # If we found any valid results, break out of retry loop
                        break

            except Exception as e:
                self._increment_error()
                logger.warning(f"Attempt {attempt + 1} failed: {e}")
                if attempt == self.max_retries - 1:
                    raise SearchException(f"Search failed after {self.max_retries} attempts")
                # Exponential backoff
                await asyncio.sleep(self._get_backoff_time(attempt))

        # Sort by relevance score descending
        final_results.sort(key=lambda x: x.relevance_score, reverse=True)

        trimmed_results = final_results[:max_results]
        # Cache and return the top `max_results`
        self.cache[cache_key] = trimmed_results
        return trimmed_results

    # ------------------------------------------------------------------
    # Synchronous wrapper
    # ------------------------------------------------------------------
    def search(
        self,
        query: str,
        max_results: int = 7,
        min_relevance_score: float = 0.0
    ) -> List[SearchResult]:
        """
        Synchronous wrapper around the async search.
        """
        return asyncio.run(self.search_async(query, max_results, min_relevance_score))

    def explain_results(self, results: List[SearchResult]) -> List[str]:
        """
        Provide a textual explanation of the sources and relevance scores.
        """
        return [
            (
                f"Result from {result.source_domain} (score: {result.relevance_score:.2f})"
                + (" [trusted]" if result.source_domain in self.trusted_domains else "")
            )
            for result in results
        ]

    def get_conversation_history(self) -> List[Dict[str, Any]]:
        """
        Return any stored conversation or query/response history.
        """
        return self.conversation_history


# ----------------------------------------------------
# Higher-level helpers
# ----------------------------------------------------
def perform_web_search(
    user_id: str,
    user_prompt: str,
    backend: SearchBackend = SearchBackend.BRAVE
) -> List[SearchResult]:
    """
    Perform a web search on behalf of a given user, handling any exceptions.
    Returns a list of SearchResult objects.
    """
    resolved_backend = resolve_search_backend(backend.value if isinstance(backend, SearchBackend) else backend)
    search_client = SearchClient(backend=resolved_backend)

    try:
        search_results = search_client.search(user_prompt)
        logger.info(f"Search returned {len(search_results)} results for user {user_id}")
        return search_results
    except SearchException as e:
        logger.error(f"Search failed for user {user_id}: {e}")
        return []
    except Exception as e:
        logger.error(f"Unexpected error during search for user {user_id}: {e}")
        return []


def should_auto_enable_search(user_prompt: str) -> Tuple[bool, str]:
    """
    Smart detector to determine if web search would benefit the response.
    Returns (should_search, confidence) where confidence is 'high', 'medium', or 'low'.
    """
    if not user_prompt:
        return False, 'low'

    normalized = user_prompt.lower()

    # === HIGH CONFIDENCE: Definitely needs search ===
    explicit_search = [
        r"\bsearch\b", r"\blook\s*up\b", r"\bfind\s+online\b",
        r"\bweb\s*search\b", r"\bgoogle\b", r"\bbing\b",
        r"\bsearch\s+(for|the|about)\b",
    ]

    time_sensitive = [
        r"\btoday\b", r"\btonight\b", r"\bright\s+now\b",
        r"\bcurrently\b", r"\bcurrent(ly)?\b", r"\blatest\b",
        r"\brecent(ly)?\b", r"\bjust\s+(happened|announced|released)\b",
        r"\bthis\s+(week|month|year|morning|afternoon|evening)\b",
        r"\byesterday\b", r"\blast\s+(week|month|night|hour)\b",
        r"\bbreaking\b", r"\blive\b", r"\bupdate[sd]?\b",
        r"\bnow\s+playing\b", r"\bhappening\s+now\b",
    ]

    realtime_topics = [
        r"\bweather\b", r"\bforecast\b", r"\btemperature\b",
        r"\brain(ing|y)?\b", r"\bsnow(ing|y)?\b", r"\bstorm\b",
        r"\bstock\s*(price|market|ticker)?\b", r"\bshare\s*price\b",
        r"\bmarket\s*(cap|value|price)\b", r"\btrading\b",
        r"\bexchange\s+rate\b", r"\bcrypto(currency)?\b",
        r"\bbitcoin\b", r"\bethereum\b", r"\b(USD|EUR|GBP)\s*to\b",
        r"\bnews\b", r"\bheadlines?\b", r"\bannouncement\b",
        r"\belection\b", r"\bvot(e|ing|ed)\b", r"\bpoll(s|ing)?\b",
        r"\bscore[sd]?\b", r"\bgame\s+result\b", r"\bwho\s+won\b",
        r"\bmatch\s+result\b", r"\bstandings?\b", r"\bleague\s+table\b",
        r"\bplayoff\b", r"\bchampionship\b", r"\btournament\b",
        r"\btrending\b", r"\bviral\b", r"\bpopular\s+right\s+now\b",
    ]

    high_confidence_patterns = explicit_search + time_sensitive + realtime_topics
    if any(re.search(p, normalized) for p in high_confidence_patterns):
        return True, 'high'

    # === MEDIUM CONFIDENCE: Likely needs search ===
    medium_patterns = [
        r"\bwhat('s|\s+is)\s+happening\b",
        r"\bwhat('s|\s+is)\s+going\s+on\b",
        r"\bwhat('s|\s+is)\s+the\s+(status|state)\b",
        r"\bis\s+.+\s+(open|closed|available|working)\b",
        r"\bwhen\s+(is|does|will|did)\b",
        r"\bwhere\s+can\s+i\s+(buy|find|get|watch)\b",
        r"\bhow\s+much\s+(does|is|are|do)\b",
        r"\bprice\s+(of|for)\b", r"\bcost\s+(of|for)\b",
        r"\bavailab(le|ility)\b",
        r"\bwho\s+is\s+the\s+(current|new)?\s*(ceo|president|cto|leader|head|director)\b",
        r"\bwho\s+(runs|leads|owns)\b",
        r"\blatest\s+(version|release|update|model)\b",
        r"\bnew(est)?\s+(iphone|android|mac|windows|ios|version)\b",
        r"\brelease\s+date\b",
        r"\bnear\s+me\b", r"\bin\s+my\s+area\b",
        r"\brestaurants?\s+in\b", r"\bhotels?\s+in\b",
        r"\b202[4-9]\b", r"\b203[0-5]\b",
    ]

    if any(re.search(p, normalized) for p in medium_patterns):
        return True, 'medium'

    # === NO SEARCH indicators (override) ===
    no_search_patterns = [
        r"\bwrite\s+(a|me|an|the)\b", r"\bgenerate\b", r"\bcreate\b",
        r"\bcompose\b", r"\bdraft\b",
        r"\bcode\b", r"\bprogram\b", r"\bscript\b", r"\bfunction\b",
        r"\bdebug\b", r"\bfix\s+(this|the|my)\b", r"\brefactor\b",
        r"\bimplement\b", r"\bbuild\s+(a|an|the)\b",
        r"\bexplain\b", r"\bwhat\s+does\s+.+\s+mean\b",
        r"\bhow\s+(do|does|to)\s+.+\s+work\b",
        r"\bteach\s+me\b", r"\bhelp\s+me\s+understand\b",
        r"\btranslate\b", r"\bconvert\b",
        r"\bcalculate\b", r"\bsolve\b", r"\bcompute\b",
        r"\bsummarize\b", r"\bsummary\b", r"\btl;?dr\b",
        r"\banalyze\s+(this|the)\b", r"\breview\s+(this|the|my)\b",
    ]

    if any(re.search(p, normalized) for p in no_search_patterns):
        return False, 'high'

    return False, 'low'


def should_auto_enable_search_simple(user_prompt: str) -> bool:
    """Simple wrapper that returns just the boolean for backward compatibility."""
    should_search, _ = should_auto_enable_search(user_prompt)
    return should_search
