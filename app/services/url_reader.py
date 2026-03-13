"""
URL Reader Service
Extracts and processes content from URLs for AI summarization.
Supports: Articles, PDFs, YouTube, GitHub, and more.
"""

import re
import hashlib
import logging
from typing import Optional, Dict, Any, Tuple
from urllib.parse import urlparse, parse_qs
from datetime import datetime, timedelta, timezone

import requests
from bs4 import BeautifulSoup

# Optional imports with fallbacks
try:
    import trafilatura
    HAS_TRAFILATURA = True
except ImportError:
    HAS_TRAFILATURA = False

try:
    from youtube_transcript_api import YouTubeTranscriptApi
    HAS_YOUTUBE_TRANSCRIPT = True
except ImportError:
    HAS_YOUTUBE_TRANSCRIPT = False

try:
    import fitz  # PyMuPDF
    HAS_PYMUPDF = True
except ImportError:
    HAS_PYMUPDF = False

logger = logging.getLogger(__name__)

# Configuration
CONFIG = {
    'jina_reader_url': 'https://r.jina.ai/',
    'request_timeout': 15,
    'max_content_length': 4000,  # Max chars to include in prompt (reduced to avoid 413 errors)
    'max_urls_per_message': 2,   # Limit URLs to prevent oversized requests
    'cache_ttl_hours': 24,
    'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
}

# URL pattern for detection
URL_PATTERN = re.compile(
    r'https?://(?:[-\w.]|(?:%[\da-fA-F]{2}))+[^\s<>"\']*',
    re.IGNORECASE
)

# In-memory cache (replace with Redis/Firestore in production)
_url_cache: Dict[str, Dict[str, Any]] = {}


class URLReader:
    """Main URL reader class with multiple extraction strategies."""

    def __init__(self, firestore_db=None):
        self.db = firestore_db
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': CONFIG['user_agent'],
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
        })

    def detect_urls(self, text: str) -> list:
        """Detect URLs in text."""
        urls = URL_PATTERN.findall(text)
        # Clean up URLs (remove trailing punctuation)
        cleaned = []
        for url in urls:
            url = url.rstrip('.,;:!?)\'\"')
            if url and len(url) > 10:
                cleaned.append(url)
        return cleaned[:CONFIG['max_urls_per_message']]

    def classify_url(self, url: str) -> str:
        """Classify URL type for appropriate handling."""
        parsed = urlparse(url.lower())
        domain = parsed.netloc
        path = parsed.path

        # YouTube
        if 'youtube.com' in domain or 'youtu.be' in domain:
            return 'youtube'

        # PDF
        if path.endswith('.pdf'):
            return 'pdf'

        # GitHub
        if 'github.com' in domain:
            return 'github'

        # Twitter/X
        if 'twitter.com' in domain or 'x.com' in domain:
            return 'twitter'

        # ArXiv
        if 'arxiv.org' in domain:
            return 'arxiv'

        # Default: article/webpage
        return 'article'

    def get_cache_key(self, url: str) -> str:
        """Generate cache key for URL."""
        return hashlib.md5(url.encode()).hexdigest()

    def get_cached(self, url: str) -> Optional[Dict[str, Any]]:
        """Get cached content if available and not expired."""
        cache_key = self.get_cache_key(url)
        now = datetime.now(timezone.utc)

        # Check in-memory cache first
        if cache_key in _url_cache:
            cached = _url_cache[cache_key]
            expires_at = cached.get('expires_at')
            if expires_at:
                # Make timezone-aware if needed
                if expires_at.tzinfo is None:
                    expires_at = expires_at.replace(tzinfo=timezone.utc)
                if now < expires_at:
                    return cached.get('content')

        # Check Firestore cache if available
        if self.db:
            try:
                doc_ref = self.db.collection('url_cache').document(cache_key)
                doc = doc_ref.get()
                if doc.exists:
                    data = doc.to_dict()
                    expires_at = data.get('expires_at')
                    if expires_at:
                        # Firestore returns timezone-aware datetimes
                        if expires_at.tzinfo is None:
                            expires_at = expires_at.replace(tzinfo=timezone.utc)
                        if now < expires_at:
                            return data.get('content')
            except Exception as e:
                logger.warning(f"Firestore cache read error: {e}")

        return None

    def set_cached(self, url: str, content: Dict[str, Any], ttl_hours: int = None):
        """Cache URL content."""
        cache_key = self.get_cache_key(url)
        ttl = ttl_hours or CONFIG['cache_ttl_hours']
        expires_at = datetime.now(timezone.utc) + timedelta(hours=ttl)

        cache_data = {
            'content': content,
            'expires_at': expires_at,
            'url': url,
            'cached_at': datetime.now(timezone.utc)
        }

        # In-memory cache
        _url_cache[cache_key] = cache_data

        # Firestore cache if available
        if self.db:
            try:
                doc_ref = self.db.collection('url_cache').document(cache_key)
                doc_ref.set(cache_data)
            except Exception as e:
                logger.warning(f"Firestore cache write error: {e}")

    def fetch_url(self, url: str, use_cache: bool = True) -> Dict[str, Any]:
        """
        Fetch and extract content from URL.
        Returns dict with: title, content, url, type, success, error
        """
        # Check cache
        if use_cache:
            cached = self.get_cached(url)
            if cached:
                cached['from_cache'] = True
                return cached

        # Classify and route to appropriate handler
        url_type = self.classify_url(url)

        try:
            if url_type == 'youtube':
                result = self._fetch_youtube(url)
            elif url_type == 'pdf':
                result = self._fetch_pdf(url)
            elif url_type == 'github':
                result = self._fetch_github(url)
            elif url_type == 'arxiv':
                result = self._fetch_arxiv(url)
            else:
                result = self._fetch_article(url)

            # Cache successful results
            if result.get('success') and use_cache:
                self.set_cached(url, result)

            return result

        except Exception as e:
            logger.error(f"Error fetching URL {url}: {e}")
            return {
                'success': False,
                'error': str(e),
                'url': url,
                'type': url_type
            }

    def _fetch_article(self, url: str) -> Dict[str, Any]:
        """Fetch article content using Jina Reader (primary) or trafilatura (fallback)."""

        # Try Jina Reader first (best quality)
        try:
            jina_url = f"{CONFIG['jina_reader_url']}{url}"
            response = self.session.get(
                jina_url,
                timeout=CONFIG['request_timeout'],
                headers={'Accept': 'text/plain'}
            )

            if response.status_code == 200 and len(response.text) > 100:
                content = response.text

                # Extract title from markdown (first # heading)
                title_match = re.search(r'^#\s+(.+)$', content, re.MULTILINE)
                title = title_match.group(1) if title_match else self._extract_domain(url)

                # Truncate if needed
                if len(content) > CONFIG['max_content_length']:
                    content = self._smart_truncate(content, CONFIG['max_content_length'])

                return {
                    'success': True,
                    'title': title,
                    'content': content,
                    'url': url,
                    'type': 'article',
                    'source': 'jina'
                }
        except Exception as e:
            logger.warning(f"Jina Reader failed for {url}: {e}")

        # Fallback to trafilatura
        if HAS_TRAFILATURA:
            try:
                response = self.session.get(url, timeout=CONFIG['request_timeout'])
                if response.status_code == 200:
                    content = trafilatura.extract(
                        response.text,
                        include_title=True,
                        include_comments=False,
                        include_tables=True
                    )

                    if content:
                        # Extract title
                        soup = BeautifulSoup(response.text, 'html.parser')
                        title = soup.title.string if soup.title else self._extract_domain(url)

                        if len(content) > CONFIG['max_content_length']:
                            content = self._smart_truncate(content, CONFIG['max_content_length'])

                        return {
                            'success': True,
                            'title': title,
                            'content': content,
                            'url': url,
                            'type': 'article',
                            'source': 'trafilatura'
                        }
            except Exception as e:
                logger.warning(f"Trafilatura failed for {url}: {e}")

        # Final fallback: basic scraping
        return self._fetch_basic(url)

    def _fetch_basic(self, url: str) -> Dict[str, Any]:
        """Basic HTML scraping fallback."""
        try:
            response = self.session.get(url, timeout=CONFIG['request_timeout'])
            soup = BeautifulSoup(response.text, 'html.parser')

            # Remove script, style, nav elements
            for tag in soup(['script', 'style', 'nav', 'footer', 'header', 'aside']):
                tag.decompose()

            # Get title
            title = soup.title.string if soup.title else self._extract_domain(url)

            # Try to find main content
            main = soup.find('main') or soup.find('article') or soup.find('body')
            if main:
                content = main.get_text(separator='\n', strip=True)
            else:
                content = soup.get_text(separator='\n', strip=True)

            # Clean up whitespace
            content = re.sub(r'\n{3,}', '\n\n', content)

            if len(content) > CONFIG['max_content_length']:
                content = self._smart_truncate(content, CONFIG['max_content_length'])

            return {
                'success': True,
                'title': title,
                'content': content,
                'url': url,
                'type': 'article',
                'source': 'basic'
            }
        except Exception as e:
            return {
                'success': False,
                'error': f"Failed to fetch: {str(e)}",
                'url': url,
                'type': 'article'
            }

    def _fetch_youtube(self, url: str) -> Dict[str, Any]:
        """Fetch YouTube video transcript."""
        video_id = self._extract_youtube_id(url)
        if not video_id:
            return {
                'success': False,
                'error': 'Could not extract YouTube video ID',
                'url': url,
                'type': 'youtube'
            }

        # Get video metadata via oEmbed
        title = f"YouTube Video ({video_id})"
        author = None
        try:
            oembed_url = f"https://www.youtube.com/oembed?url=https://www.youtube.com/watch?v={video_id}&format=json"
            oembed_resp = self.session.get(oembed_url, timeout=5)
            if oembed_resp.status_code == 200:
                oembed_data = oembed_resp.json()
                title = oembed_data.get('title', title)
                author = oembed_data.get('author_name')
        except:
            pass

        # Get transcript
        if HAS_YOUTUBE_TRANSCRIPT:
            try:
                transcript_list = YouTubeTranscriptApi.get_transcript(video_id)
                transcript = ' '.join([item['text'] for item in transcript_list])

                if len(transcript) > CONFIG['max_content_length']:
                    transcript = self._smart_truncate(transcript, CONFIG['max_content_length'])

                return {
                    'success': True,
                    'title': title,
                    'content': f"[YouTube Video Transcript]\n\n{transcript}",
                    'url': url,
                    'type': 'youtube',
                    'video_id': video_id
                }
            except Exception as e:
                # Try with Jina Reader as fallback
                pass

        # Fallback: Use Jina Reader for YouTube
        try:
            jina_url = f"{CONFIG['jina_reader_url']}{url}"
            response = self.session.get(jina_url, timeout=CONFIG['request_timeout'])
            if response.status_code == 200:
                content = response.text
                if len(content) > CONFIG['max_content_length']:
                    content = self._smart_truncate(content, CONFIG['max_content_length'])
                return {
                    'success': True,
                    'title': title,
                    'content': content,
                    'url': url,
                    'type': 'youtube',
                    'video_id': video_id,
                    'source': 'jina'
                }
        except:
            pass

        # Return metadata even without transcript so AI knows what video is being discussed
        metadata_content = f"[YouTube Video - No transcript available]\n\nTitle: {title}"
        if author:
            metadata_content += f"\nChannel: {author}"
        metadata_content += f"\nVideo ID: {video_id}\nURL: {url}\n\nNote: This video does not have captions/transcript available. The user is asking about this video but full content cannot be extracted."

        return {
            'success': True,  # Return as success with limited info
            'title': title,
            'content': metadata_content,
            'url': url,
            'type': 'youtube',
            'video_id': video_id,
            'limited': True  # Flag that content is limited
        }

    def _fetch_pdf(self, url: str) -> Dict[str, Any]:
        """Fetch and extract text from PDF."""
        try:
            response = self.session.get(url, timeout=CONFIG['request_timeout'] * 2)

            if response.status_code != 200:
                return {
                    'success': False,
                    'error': f'Failed to download PDF (status {response.status_code})',
                    'url': url,
                    'type': 'pdf'
                }

            if HAS_PYMUPDF:
                # Extract with PyMuPDF
                doc = fitz.open(stream=response.content, filetype='pdf')
                text_parts = []

                for page_num, page in enumerate(doc):
                    if page_num >= 20:  # Limit pages
                        text_parts.append(f"\n[... Truncated after {page_num} pages ...]")
                        break
                    text_parts.append(page.get_text())

                content = '\n'.join(text_parts)
                title = doc.metadata.get('title') or self._extract_filename(url)
                doc.close()
            else:
                # Fallback: Use Jina Reader
                jina_url = f"{CONFIG['jina_reader_url']}{url}"
                jina_resp = self.session.get(jina_url, timeout=CONFIG['request_timeout'])
                if jina_resp.status_code == 200:
                    content = jina_resp.text
                    title = self._extract_filename(url)
                else:
                    return {
                        'success': False,
                        'error': 'PDF extraction not available (PyMuPDF not installed)',
                        'url': url,
                        'type': 'pdf'
                    }

            if len(content) > CONFIG['max_content_length']:
                content = self._smart_truncate(content, CONFIG['max_content_length'])

            return {
                'success': True,
                'title': title,
                'content': f"[PDF Document]\n\n{content}",
                'url': url,
                'type': 'pdf'
            }

        except Exception as e:
            return {
                'success': False,
                'error': f'PDF extraction failed: {str(e)}',
                'url': url,
                'type': 'pdf'
            }

    def _fetch_github(self, url: str) -> Dict[str, Any]:
        """Fetch GitHub repository README or file content."""
        parsed = urlparse(url)
        path_parts = parsed.path.strip('/').split('/')

        if len(path_parts) < 2:
            return self._fetch_article(url)  # Not a repo URL

        owner, repo = path_parts[0], path_parts[1]

        # Check if it's a specific file
        if len(path_parts) > 3 and path_parts[2] == 'blob':
            # It's a file URL, fetch the raw content
            branch = path_parts[3]
            file_path = '/'.join(path_parts[4:])
            raw_url = f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{file_path}"

            try:
                response = self.session.get(raw_url, timeout=CONFIG['request_timeout'])
                if response.status_code == 200:
                    content = response.text
                    if len(content) > CONFIG['max_content_length']:
                        content = self._smart_truncate(content, CONFIG['max_content_length'])

                    return {
                        'success': True,
                        'title': f"{owner}/{repo} - {file_path}",
                        'content': f"[GitHub File: {file_path}]\n\n```\n{content}\n```",
                        'url': url,
                        'type': 'github'
                    }
            except:
                pass

        # Fetch README
        readme_urls = [
            f"https://raw.githubusercontent.com/{owner}/{repo}/main/README.md",
            f"https://raw.githubusercontent.com/{owner}/{repo}/master/README.md",
            f"https://raw.githubusercontent.com/{owner}/{repo}/main/readme.md",
        ]

        for readme_url in readme_urls:
            try:
                response = self.session.get(readme_url, timeout=CONFIG['request_timeout'])
                if response.status_code == 200:
                    content = response.text
                    if len(content) > CONFIG['max_content_length']:
                        content = self._smart_truncate(content, CONFIG['max_content_length'])

                    return {
                        'success': True,
                        'title': f"{owner}/{repo}",
                        'content': f"[GitHub Repository README]\n\n{content}",
                        'url': url,
                        'type': 'github'
                    }
            except:
                continue

        # Fallback to Jina Reader
        return self._fetch_article(url)

    def _fetch_arxiv(self, url: str) -> Dict[str, Any]:
        """Fetch arXiv paper abstract or PDF."""
        # Convert to abstract page if it's a PDF link
        if '/pdf/' in url:
            url = url.replace('/pdf/', '/abs/').replace('.pdf', '')

        # Use Jina Reader for best results
        return self._fetch_article(url)

    def _extract_youtube_id(self, url: str) -> Optional[str]:
        """Extract YouTube video ID from URL."""
        patterns = [
            r'(?:youtube\.com\/watch\?v=|youtu\.be\/|youtube\.com\/embed\/)([^&\n?#]+)',
            r'youtube\.com\/shorts\/([^&\n?#]+)',
        ]
        for pattern in patterns:
            match = re.search(pattern, url)
            if match:
                return match.group(1)
        return None

    def _extract_domain(self, url: str) -> str:
        """Extract domain from URL for fallback title."""
        parsed = urlparse(url)
        return parsed.netloc or 'Unknown'

    def _extract_filename(self, url: str) -> str:
        """Extract filename from URL."""
        parsed = urlparse(url)
        path = parsed.path
        if '/' in path:
            return path.split('/')[-1] or 'Document'
        return 'Document'

    def _smart_truncate(self, text: str, max_length: int) -> str:
        """Truncate text smartly, preserving complete sentences."""
        if len(text) <= max_length:
            return text

        # Find a good break point
        truncated = text[:max_length]

        # Try to break at paragraph
        last_para = truncated.rfind('\n\n')
        if last_para > max_length * 0.7:
            return truncated[:last_para] + '\n\n[... Content truncated ...]'

        # Try to break at sentence
        last_sentence = max(
            truncated.rfind('. '),
            truncated.rfind('.\n'),
            truncated.rfind('? '),
            truncated.rfind('! ')
        )
        if last_sentence > max_length * 0.7:
            return truncated[:last_sentence + 1] + '\n\n[... Content truncated ...]'

        # Hard truncate
        return truncated + '\n\n[... Content truncated ...]'

    def process_message_urls(self, message: str) -> Tuple[str, list]:
        """
        Process URLs in a message and return enhanced message with fetched content.
        Returns: (enhanced_message, list of fetch results)
        """
        urls = self.detect_urls(message)
        if not urls:
            return message, []

        results = []
        url_contents = []

        for url in urls:
            result = self.fetch_url(url)
            results.append(result)

            if result.get('success'):
                url_contents.append(
                    f"\n\n---\n"
                    f"**[Content from: {result.get('title', url)}]**\n"
                    f"Source: {url}\n"
                    f"Type: {result.get('type', 'article')}\n\n"
                    f"{result.get('content', '')}\n"
                    f"---"
                )
            else:
                url_contents.append(
                    f"\n\n---\n"
                    f"**[Could not fetch: {url}]**\n"
                    f"Error: {result.get('error', 'Unknown error')}\n"
                    f"---"
                )

        # Append fetched content to message
        enhanced_message = message + '\n'.join(url_contents)

        return enhanced_message, results


# Singleton instance
_reader_instance = None

def get_url_reader(firestore_db=None) -> URLReader:
    """Get or create URL reader instance."""
    global _reader_instance
    if _reader_instance is None:
        _reader_instance = URLReader(firestore_db)
    return _reader_instance
