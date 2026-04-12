"""Process-wide Redis client singleton. Safe across gevent workers."""
import os
import redis

_client = None


def get_redis() -> redis.Redis:
    """Return a pooled Redis client for this process."""
    global _client
    if _client is None:
        url = os.getenv('REDIS_URL', 'redis://localhost:6379/0')
        _client = redis.from_url(url, decode_responses=True, socket_timeout=2, socket_connect_timeout=2)
    return _client
