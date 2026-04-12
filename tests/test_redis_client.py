import os
import pytest
from app.redis_client import get_redis


def test_get_redis_returns_same_instance():
    """Multiple calls return the same client (pooled)."""
    a = get_redis()
    b = get_redis()
    assert a is b


def test_redis_client_pings():
    """Client can reach live Redis (requires redis-server running)."""
    r = get_redis()
    assert r.ping() is True
