"""
GNews API integration for home page news headlines.
Smart mode: extracts topics from user's recent chats and searches relevant news.
Falls back to top headlines if no history or keywords found.
Caches per-user for 1 hour.
"""

import logging
import re
import time
import requests
from typing import List, Dict, Optional

from .config import Config

logger = logging.getLogger(__name__)

# Per-user cache: { user_id: { 'headlines': [...], 'fetched_at': float, 'query': str } }
_user_cache: Dict[str, Dict] = {}
# Generic fallback cache (no user)
_generic_cache: Dict[str, any] = {'headlines': [], 'fetched_at': 0}
CACHE_TTL = 3600  # 1 hour

# Words to ignore when extracting topics from session names
STOP_WORDS = {
    'new', 'chat', 'help', 'me', 'with', 'the', 'a', 'an', 'and', 'or', 'for',
    'to', 'of', 'in', 'on', 'is', 'it', 'my', 'how', 'what', 'why', 'when',
    'where', 'can', 'you', 'i', 'do', 'this', 'that', 'about', 'from', 'more',
    'some', 'please', 'just', 'like', 'get', 'make', 'write', 'create', 'build',
    'explain', 'tell', 'give', 'show', 'find', 'need', 'want', 'thing', 'things',
    'test', 'testing', 'try', 'hello', 'hi', 'hey', 'thanks', 'thank', 'ok',
}


def extract_topics_from_sessions(db, user_id: str, max_sessions: int = 8) -> List[str]:
    """
    Extract topic keywords from user's recent session names.
    Returns up to 3 distinct topic phrases.
    """
    try:
        sessions_ref = db.collection('users').document(user_id).collection('chat_sessions')
        query = sessions_ref.where('status', '!=', 'archived').select(
            ['session_name', 'last_updated']
        ).order_by('last_updated', direction='DESCENDING').limit(max_sessions)

        try:
            docs = query.stream(timeout=3, retry=None)
        except TypeError:
            docs = query.stream()

        session_names = []
        for doc in docs:
            data = doc.to_dict()
            name = data.get('session_name', '')
            if name and name.lower() not in ('new chat', 'chat', ''):
                session_names.append(name)

        if not session_names:
            return []

        # Extract meaningful words/phrases
        all_words = []
        for name in session_names:
            # Clean and split
            words = re.findall(r'[a-zA-Z]+', name.lower())
            meaningful = [w for w in words if w not in STOP_WORDS and len(w) > 2]
            all_words.extend(meaningful)

        if not all_words:
            return []

        # Count frequency
        freq = {}
        for w in all_words:
            freq[w] = freq.get(w, 0) + 1

        # Get top keywords by frequency
        sorted_words = sorted(freq.items(), key=lambda x: -x[1])
        topics = [w for w, _ in sorted_words[:3]]

        logger.info(f'[News] Extracted topics for user: {topics} from {len(session_names)} sessions')
        return topics

    except Exception as e:
        logger.warning(f'[News] Failed to extract topics: {e}')
        return []


def _fetch_gnews(endpoint: str, params: dict) -> List[Dict]:
    """Fetch from GNews API and parse articles."""
    api_key = Config.GNEWS_API_KEY
    if not api_key:
        return []

    params['apikey'] = api_key
    try:
        resp = requests.get(
            f'https://gnews.io/api/v4/{endpoint}',
            params=params,
            timeout=5
        )
        resp.raise_for_status()
        data = resp.json()

        articles = data.get('articles', [])
        headlines = []
        for article in articles:
            title = article.get('title', '').split(' - ')[0].strip()
            if not title:
                continue
            headlines.append({
                'title': title,
                'description': article.get('description', ''),
                'source': article.get('source', {}).get('name', ''),
                'url': article.get('url', ''),
            })
        return headlines

    except Exception as e:
        logger.warning(f'[News] GNews fetch failed ({endpoint}): {e}')
        return []


def get_news_headlines(count: int = 2, lang: str = 'en', country: str = 'us',
                       db=None, user_id: str = None) -> List[Dict]:
    """
    Fetch news headlines — personalized if user context available, generic otherwise.
    """
    now = time.time()

    # Try personalized (per-user cache)
    if db and user_id:
        cached = _user_cache.get(user_id)
        if cached and (now - cached['fetched_at']) < CACHE_TTL:
            return cached['headlines'][:count]

        # Extract topics from recent chats
        topics = extract_topics_from_sessions(db, user_id)

        if topics:
            # Search multiple topic combinations for more results
            all_personalized = []
            seen = set()
            for topic in topics[:3]:
                batch = _fetch_gnews('search', {
                    'q': topic,
                    'lang': lang,
                    'max': 10,
                })
                for h in batch:
                    key = h['title'].lower()[:50]
                    if key not in seen:
                        seen.add(key)
                        all_personalized.append(h)
            headlines = all_personalized

            if headlines:
                _user_cache[user_id] = {
                    'headlines': headlines,
                    'fetched_at': now,
                    'query': query
                }
                logger.info(f'[News] Personalized news for user ({query}): {len(headlines)} results')
                return headlines[:count]

    # Fallback to generic top headlines (fetch across categories for volume)
    if _generic_cache['headlines'] and (now - _generic_cache['fetched_at']) < CACHE_TTL:
        return _generic_cache['headlines'][:count]

    categories = ['general', 'technology', 'business', 'science', 'entertainment']
    all_headlines = []
    seen_titles = set()

    for cat in categories:
        batch = _fetch_gnews('top-headlines', {
            'lang': lang,
            'country': country,
            'category': cat,
            'max': 10,
        })
        for h in batch:
            # Deduplicate by title
            key = h['title'].lower()[:50]
            if key not in seen_titles:
                seen_titles.add(key)
                all_headlines.append(h)

    if all_headlines:
        _generic_cache['headlines'] = all_headlines
        _generic_cache['fetched_at'] = now
        logger.info(f'[News] Fetched {len(all_headlines)} headlines across {len(categories)} categories')

    return all_headlines[:count]


def headlines_to_options(headlines: List[Dict]) -> List[Dict]:
    """Convert news headlines to option card format for the home page."""
    options = []
    for h in headlines:
        title = h['title']
        if len(title) > 70:
            title = title[:67] + '...'

        source = h.get('source', '')
        subtitle = source if source else 'News'

        safe_title = title.replace("'", "\\'").replace('"', '\\"')
        action = f"sendMessage('Tell me more about this: {safe_title}')"

        options.append({
            'title': title,
            'subtitle': subtitle,
            'action': action,
            'icon': 'news',
        })

    return options
