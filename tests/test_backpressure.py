import pytest
from app.backpressure import LLM_SLOTS, acquire_or_503


def test_acquire_when_slot_available():
    with acquire_or_503(LLM_SLOTS, timeout=0.1) as ok:
        assert ok is True


def test_503_when_exhausted():
    held = []
    capacity = getattr(LLM_SLOTS, '_initial_value', 8)
    for _ in range(capacity):
        cm = acquire_or_503(LLM_SLOTS, timeout=0.01)
        cm.__enter__()
        held.append(cm)

    try:
        with acquire_or_503(LLM_SLOTS, timeout=0.05) as ok:
            assert ok is False
    finally:
        for cm in held:
            cm.__exit__(None, None, None)


def test_release_frees_slot():
    with acquire_or_503(LLM_SLOTS, timeout=0.1):
        pass
    with acquire_or_503(LLM_SLOTS, timeout=0.01) as ok:
        assert ok is True
