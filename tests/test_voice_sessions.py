import pytest
from app.voice_sessions import register, release, count


@pytest.fixture(autouse=True)
def _clean():
    """Clear voice keys before and after each test."""
    from app.redis_client import get_redis
    r = get_redis()
    for k in r.scan_iter('voice:*'):
        r.delete(k)
    yield
    for k in r.scan_iter('voice:*'):
        r.delete(k)


def test_register_increments_count():
    register('sess-a')
    assert count() == 1
    register('sess-b')
    assert count() == 2


def test_release_decrements_count():
    register('sess-a')
    register('sess-b')
    release('sess-a')
    assert count() == 1


def test_release_unknown_session_is_noop():
    release('never-registered')
    assert count() == 0


def test_register_is_idempotent():
    register('sess-a')
    register('sess-a')
    assert count() == 1
