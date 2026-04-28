"""Process-wide Redis client singleton. Safe across gevent workers."""
import json
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


def cache_get(key: str):
    """Get a JSON-serialized value from Redis. Returns None if missing
    or Redis is unreachable. Designed to replace in-memory dict caches
    that previously didn't survive Flask restarts and weren't shared
    across gunicorn workers."""
    try:
        val = get_redis().get(key)
        if val:
            return json.loads(val)
    except Exception:
        pass
    return None


def cache_set(key: str, value, ttl_seconds: int) -> None:
    """Set a JSON-serialized value in Redis with TTL. Silent on failure
    so a transient Redis outage degrades to no-cache rather than
    breaking the request path. Uses default=str to handle datetime/etc."""
    try:
        get_redis().setex(key, ttl_seconds, json.dumps(value, default=str))
    except Exception:
        pass
