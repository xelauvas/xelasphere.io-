"""WebFetcher — full page content fetching with HTML-to-Markdown and 2-tier LRU cache."""

import logging
from concurrent.futures import ThreadPoolExecutor, wait as futures_wait, FIRST_COMPLETED
from urllib.parse import urlparse
from typing import Dict, Any, List, Optional

import requests
from bs4 import BeautifulSoup
from markdownify import markdownify as md
from cachetools import TTLCache

try:
    from app.config import Config
except Exception:
    Config = None  # type: ignore[assignment,misc]

logger = logging.getLogger(__name__)

# 2-tier LRU caches
url_cache: TTLCache = TTLCache(maxsize=200, ttl=900)   # 15 min, URL-keyed
domain_cache: TTLCache = TTLCache(maxsize=128, ttl=300)  # 5 min, hostname-keyed

# Constants
FETCH_TIMEOUT = 10
MAX_CONTENT_BYTES = 5 * 1024 * 1024  # 5 MB
USER_AGENT = 'XelaSphere/1.0 (Search Bot)'

STRIP_TAGS = [
    'nav', 'footer', 'aside', 'script', 'style', 'noscript',
    'iframe', 'form', 'button', 'svg', 'header',
]

# CSS class fragments commonly found on ad/cookie banners
_AD_COOKIE_CLASSES = [
    'cookie', 'gdpr', 'consent', 'banner', 'popup', 'modal',
    'overlay', 'advertisement', 'ad-', '-ad', 'promo', 'subscribe',
]


class WebFetcher:
    """Fetches full page content, converts HTML to Markdown, extracts og:image."""

    @staticmethod
    def _normalise_domain(d: str) -> str:
        d = d.lower()
        if d.startswith('www.'):
            d = d[4:]
        return d

    def __init__(self):
        self._preapproved: set = {
            self._normalise_domain(d)
            for d in getattr(Config, 'SEARCH_PREAPPROVED_DOMAINS', [])
        } if Config is not None else set()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def fetch(self, url: str, max_chars: int = 20_000) -> Dict[str, Any]:
        """
        Fetch a URL and return its content as Markdown.

        Returns a dict with keys:
            content, og_image, og_title, description, cached, error
        """
        if url in url_cache:
            cached = dict(url_cache[url])
            cached['cached'] = True
            return cached

        result: Dict[str, Any] = {
            'content': '',
            'og_image': None,
            'og_title': None,
            'description': None,
            'cached': False,
            'error': None,
        }

        try:
            response = requests.get(
                url,
                timeout=FETCH_TIMEOUT,
                headers={'User-Agent': USER_AGENT},
                stream=True,
            )
            response.raise_for_status()

            # Guard against huge pages
            raw = b''
            for chunk in response.iter_content(chunk_size=65536):
                raw += chunk
                if len(raw) >= MAX_CONTENT_BYTES:
                    break

            html = raw.decode(response.apparent_encoding or 'utf-8', errors='replace')

            meta = self._extract_meta(html)
            result['og_image'] = meta.get('og_image')
            result['og_title'] = meta.get('og_title')
            result['description'] = meta.get('description')
            result['content'] = self._html_to_markdown(html, max_chars=max_chars)

        except requests.exceptions.Timeout:
            result['error'] = 'Request timed out'
        except requests.exceptions.ConnectionError as exc:
            result['error'] = f'Connection error: {exc}'
        except requests.exceptions.HTTPError as exc:
            result['error'] = f'HTTP error: {exc}'
        except Exception as exc:
            logger.warning('WebFetcher.fetch error for %s: %s', url, exc)
            result['error'] = str(exc)

        if not result['error']:
            url_cache[url] = {k: v for k, v in result.items() if k != 'cached'}

        return result

    def fetch_multiple(
        self,
        urls: List[str],
        max_chars_per_page: int = 20_000,
        deadline_seconds: float = 2.0,
    ) -> List[Dict[str, Any]]:
        """
        Fetch multiple URLs concurrently, honouring a wall-clock deadline.

        Returns a list in the same order as *urls*.  URLs that timed out or
        failed are represented by empty dicts.
        """
        results: Dict[str, Any] = {}
        futures_map = {}

        with ThreadPoolExecutor(max_workers=3) as executor:
            for url in urls:
                future = executor.submit(self.fetch, url, max_chars_per_page)
                futures_map[future] = url

            done, not_done = futures_wait(
                futures_map.keys(), timeout=deadline_seconds
            )

            for future in done:
                url = futures_map[future]
                try:
                    results[url] = future.result()
                except Exception as exc:
                    results[url] = {'error': str(exc)}

            for future in not_done:
                future.cancel()

        return [results.get(url, {}) for url in urls]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _extract_meta(self, html: str) -> Dict[str, Optional[str]]:
        """Extract og:image, og:title, and description from HTML."""
        soup = BeautifulSoup(html, 'html.parser')
        meta: Dict[str, Optional[str]] = {
            'og_image': None,
            'og_title': None,
            'description': None,
        }

        # og:image
        og_image_tag = soup.find('meta', property='og:image')
        if og_image_tag and og_image_tag.get('content'):
            meta['og_image'] = og_image_tag['content'].strip()
        else:
            # Fallback: first <img> with explicit width > 200
            for img in soup.find_all('img', width=True):
                try:
                    width = int(str(img['width']).replace('px', '').strip())
                    if width > 200 and img.get('src'):
                        meta['og_image'] = img['src'].strip()
                        break
                except (ValueError, TypeError):
                    continue

        # og:title
        og_title_tag = soup.find('meta', property='og:title')
        if og_title_tag and og_title_tag.get('content'):
            meta['og_title'] = og_title_tag['content'].strip()

        # description (og:description or plain description)
        og_desc = soup.find('meta', property='og:description')
        if og_desc and og_desc.get('content'):
            meta['description'] = og_desc['content'].strip()
        else:
            plain_desc = soup.find('meta', attrs={'name': 'description'})
            if plain_desc and plain_desc.get('content'):
                meta['description'] = plain_desc['content'].strip()

        return meta

    def _html_to_markdown(self, html: str, max_chars: int = 20_000) -> str:
        """Strip noise from HTML, convert to Markdown, then truncate."""
        soup = BeautifulSoup(html, 'html.parser')

        # Remove structural noise tags
        for tag_name in STRIP_TAGS:
            for tag in soup.find_all(tag_name):
                tag.decompose()

        # Remove elements whose class attribute contains ad/cookie-related tokens
        for element in soup.find_all(class_=True):
            classes = ' '.join(element.get('class', [])).lower()
            if any(token in classes for token in _AD_COOKIE_CLASSES):
                element.decompose()

        markdown = md(str(soup), heading_style='ATX', strip=['a'])
        # Collapse excessive blank lines
        import re
        markdown = re.sub(r'\n{3,}', '\n\n', markdown).strip()

        if len(markdown) > max_chars:
            markdown = markdown[:max_chars] + '\n\n[Content truncated due to length...]'

        return markdown

    def _is_preapproved(self, url: str) -> bool:
        """Return True if the URL's hostname matches a pre-approved domain."""
        try:
            hostname = self._normalise_domain(urlparse(url).hostname or '')
            if not hostname:
                return False
            # Exact match
            if hostname in self._preapproved:
                return True
            # Subdomain match: en.wikipedia.org ends with .wikipedia.org
            for domain in self._preapproved:
                if hostname.endswith('.' + domain):
                    return True
        except Exception:
            pass
        return False
